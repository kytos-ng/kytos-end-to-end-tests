#!/usr/bin/env python3
"""
INT Collector - Simply python3 application to parse and display In-band Network Telemetry
"""
import argparse
import struct

from scapy.all import BitField, Packet, XBitField, sniff
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Dot1Q, Ether

verbose = False


class TelemetryReportHeader(Packet):
    """INT Telemetrey Report Header."""

    name = "TelemetryReport "
    fields_desc = [
        BitField("f_version", 0, 4),
        BitField("f_length", 0, 4),
        BitField("f_next_proto", 0, 3),
        BitField("f_rep_md_bits", 0, 6),
        BitField("f_rsvd", 0, 6),
        BitField("f_drop", 0, 1),
        BitField("f_queue", 0, 1),
        BitField("f_flow", 0, 1),
        BitField("f_hw_id", 0, 6),
        XBitField("switch_id", 0, 32),
        XBitField("f_seq_num", 0, 32),
        XBitField("f_ingress_ts", 0, 32),
    ]


class IntShim(Packet):
    """INT Shim Header."""

    name = "INT Shim "
    fields_desc = [
        XBitField("shim_type", 0, 8),
        XBitField("shim_rsvd1", 0, 8),
        BitField("shim_len", 0, 8),
        XBitField("shim_rsvd2", 0, 8),
    ]


class IntMetadataHeader(Packet):
    """INT Metadata Header."""

    name = "INT Metadata Header "
    fields_desc = [
        BitField("int_version", 0, 4),
        BitField("int_replication", 0, 2),
        BitField("int_copy", 0, 1),
        BitField("int_hop_exceeded", 0, 1),
        BitField("int_mtu_exceeded", 0, 1),
        BitField("int_rsvd_1", 0, 10),
        BitField("int_hop_ml", 0, 5),
        BitField("int_remaining_hop_cnt", 0, 8),
        BitField("int_bit_0_switch_id", 0, 1),
        BitField("int_bit_1_ingress_egress_id", 0, 1),
        BitField("int_bit_2_hop_latency", 0, 1),
        BitField("int_bit_3_queue_id_occupancy", 0, 1),
        BitField("int_bit_4_ingress_ts", 0, 1),
        BitField("int_bit_5_egress_ts", 0, 1),
        BitField("int_bit_6_queue_id_congestion", 0, 1),
        BitField("int_bit_7_egress_tx_utilization", 0, 1),
        BitField("int_rsvd_instructions", 0, 8),
        BitField("int_rsvd_2", 0, 16),
    ]


def pkt_diam(pkt):
    """Process packet captured."""
    # raw = pkt.getlayer(Raw).load
    # print(raw)
    # pkt.show()
    # print("Len original pkt == %d" % len(pkt.getlayer(Raw).payload))
    # print("Len IP payload == %d" % len(pkt[IP].payload))
    # print("Len UDP payload == %d" % len(pkt[UDP].payload))
    try:
        telemetry_report_hdr = TelemetryReportHeader(pkt[UDP].load)
        # telemetry_report_hdr.show()
        # print("Len TelemetryReport payload = %d" % (len(telemetry_report_hdr.payload)))
        inner_ether = Ether(telemetry_report_hdr.load)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        if verbose:
            print(f"failed to parse packet: {exc}")
        return
    if (
        inner_ether.type == 0x9100
    ):  # this will help scapy to parse qinq (since we use 0x9100 instead of the standard 0x88A8)
        next_hdr = Dot1Q(inner_ether.load)
    else:
        next_hdr = inner_ether
    if verbose:
        next_hdr.show()
    ip = next_hdr.getlayer(IP)
    if ip.proto == 6:
        l4_size = 20
        l4_ports = f"tcp_sport={ip[TCP].sport} tcp_dport={ip[TCP].dport}"
    elif ip.proto == 17:
        l4_size = 8
        l4_ports = f"udp_sport={ip[UDP].sport} udp_dport={ip[UDP].dport}"
    else:
        print(f"error unknown ip_proto {ip.proto}")
        return
    # print("len l4 == %d" % len(l4.payload))

    int_shim = IntShim(bytes(ip)[20 + l4_size :])

    if verbose:
        int_shim.show()
    # print("len int_shim payload == %d" % len(int_shim.payload))
    int_metadata = IntMetadataHeader(int_shim.load)
    if verbose:
        int_metadata.show()
    # print("len int_metadata payload == %d" % len(int_metadata.payload))
    int_size_len = (int_shim.shim_len - 3) * 4
    data = int_metadata.load[:int_size_len]
    int_stack = show_int_md(data, int_metadata)
    print(
        f"pkt_len={len(inner_ether)} ip_src={ip.src} ip_dst={ip.dst} {l4_ports}",
        f" int_stack={';'.join(int_stack)}",
        flush=True,
    )
    if len(int_stack) == 0:
        print(bytes(inner_ether), flush=True)
        inner_ether.show()
        print("---", flush=True)
    if verbose:
        print("---")


def show_int_md(
    data, int_metadata
):  # pylint: disable=too-many-branches, too-many-statements
    """Show INT metadata stack."""
    int_stack = []
    int_stack_count = 0
    while len(data) >= int_metadata.int_hop_ml * 4:
        int_md = ""
        offset = 0
        int_stack_count += 1
        if verbose:
            print(f"### INT Stack {int_stack_count}")
        if int_metadata.int_bit_0_switch_id:
            sw_id = hex(struct.unpack("!L", data[:4])[0])
            if verbose:
                print(f"   - int_switch_id = {sw_id}")
            offset += 4
            int_md += f"sw_id={sw_id},"
        if int_metadata.int_bit_1_ingress_egress_id:
            ig_port = struct.unpack("!H", data[offset : offset + 2])[0]
            eg_port = struct.unpack("!H", data[offset + 2 : offset + 4])[0]
            if verbose:
                print(f"   - int_ingress_port = {ig_port}")
                print(f"   - int_egress_port  = {eg_port}")
            offset += 4
            int_md += f"ig_port={ig_port},eg_port={eg_port},"
        if int_metadata.int_bit_2_hop_latency:
            hop_latency = struct.unpack("!L", data[offset : offset + 4])[0]
            if verbose:
                print(f"   - int_hop_latency = {hop_latency}")
            offset += 4
        if int_metadata.int_bit_3_queue_id_occupancy:
            queue = struct.unpack("!B", data[offset : offset + 1])[0]
            queue_occ = int.from_bytes(data[offset + 1 : offset + 4], byteorder="big")
            if verbose:
                print(f"   - int_queue_id = {queue}")
                print(f"   - int_queue_occ = {queue_occ}")
            offset += 4
            int_md += f"queue={queue},"
        if int_metadata.int_bit_4_ingress_ts:
            ig_ts = int.from_bytes(data[offset : offset + 4], byteorder="big")
            if verbose:
                print(f"   - int_ingress_ts = {ig_ts}")
            offset += 4
        if int_metadata.int_bit_5_egress_ts:
            eg_ts = int.from_bytes(data[offset : offset + 4], byteorder="big")
            if verbose:
                print(f"   - int_egress_ts  = {eg_ts}")
            offset += 4
        if int_metadata.int_bit_6_queue_id_congestion:
            # -- Not Supported
            pass
        if int_metadata.int_bit_7_egress_tx_utilization:
            # -- Not Supported
            pass
        if offset == 0:
            # Sanity check: offset must be greater than zero
            break
        # print("INT Stack bytes = %d" % offset)
        int_stack.append(int_md.strip(","))
        data = data[offset:]
    return int_stack


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--interface", action="append", type=str)
    parser.add_argument("-f", "--filter", type=str, default="")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    verbose = args.verbose
    sniff(iface=args.interface, filter=args.filter, store=0, prn=pkt_diam)
    # sniff(offline=sys.argv[1], filter="", store=0, prn=pkt_diam)
