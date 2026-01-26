#!/usr/bin/python3
"""
Send packets using scapy.
"""
import argparse

from scapy.all import RandShort, sendp
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether

parser = argparse.ArgumentParser()
parser.add_argument("-i", "--interface", type=str, required=True)
parser.add_argument("-c", "--count", type=int, default=1)
parser.add_argument("-s", "--source", type=str, default="127.0.0.1")
parser.add_argument("-d", "--destination", type=str, default="127.0.0.1")
parser.add_argument("-p", "--port", type=int, default=80)
parser.add_argument("-u", "--udp", action="store_true")
args = parser.parse_args()

sport = RandShort()

p = (
    Ether()
    / IP(src=args.source, dst=args.destination)
    / (
        UDP(sport=sport, dport=args.port)
        if args.udp
        else TCP(sport=sport, dport=args.port)
    )
    / "aaaaa"
)

sendp(p * args.count, iface=args.interface)
