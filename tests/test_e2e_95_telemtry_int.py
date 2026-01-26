"""End-to-end tests for telemetry_int Napp."""

import os
import time
from pathlib import Path

import pytest
import requests

from .helpers import NetworkTest

CONTROLLER = "127.0.0.1"
KYTOS_API = f"http://{CONTROLLER}:8181/api"
SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")


def parse_int_collector(data):
    """Parse raw file content from int_collector.py output and return a dict."""
    int_pkts = {}
    for pkt in data.splitlines():
        # format:
        # pkt_len=96 ip_src=1.0.0.1 ip_dst=1.0.0.3 udp_sport=10 udp_dport=80 int_stack=sw_id=0x1,...
        fields = pkt.split()
        if len(fields) == 6 and len(fields[5]) >= 10:
            int_pkts[f"{fields[2]} {fields[4]}"] = fields[5][10:].split(";")
    return int_pkts


@pytest.mark.skipif(
    os.environ.get("SWITCH_CLASS") != "NoviSwitch"
    or os.environ.get("NOVIVERSION") != "NW570.6.1",
    reason="NoviSwitch does not support interface removal",
)
class TestE2ETelemtryINT:
    """End-to-end tests for telemetry_int."""

    net = None

    def setup_method(self, method):  # pylint: disable=unused-argument
        """
        It is called at the beginning of every class method execution
        """
        # Since some tests may set a link to down state, we should reset
        # the link state to up (for all links)
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(5)

    @classmethod
    def setup_class(cls):
        """Called once before all test methods within a class are run."""
        cls.net = NetworkTest(CONTROLLER, topo_name="amlight_intlab")
        cls.net.start()

    @classmethod
    def teardown_class(cls):
        """Called once after all tests in the class have finished for cleanup."""
        cls.net.stop()

    def create_evc(
        self,
        vlan_id,
        uni_a="00:00:00:00:00:00:00:01:1",
        uni_z="00:00:00:00:00:00:00:06:1",
        **kwargs,
    ):
        """Create an EVC to be used later, return EVC ID."""
        payload = {
            "name": f"Vlan_{vlan_id}",
            "dynamic_backup_path": True,
            "uni_a": {"interface_id": uni_a, "tag": {"tag_type": 1, "value": vlan_id}},
            "uni_z": {
                "interface_id": uni_z,
                "tag": {"tag_type": "vlan", "value": vlan_id},
            },
        }
        payload.update(kwargs)
        api_url = KYTOS_API + "/kytos/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload, timeout=5)
        assert response.status_code == 201, response.text
        data = response.json()
        return data["circuit_id"]

    def config_host_ip_vlan(self, host, ip, vlan):
        """Configure VLAN subinterface and VLAN into host."""
        host.cmd(
            f"ip link add link {host.name}-eth1 name vlan{vlan} type vlan id {vlan}"
        )
        host.cmd(f"ip link set up vlan{vlan}")
        host.cmd(f"ip addr add {ip}/24 dev vlan{vlan}")
        host.test_ip = ip
        host.test_intf = f"vlan{vlan}"

    def validate_evc_paths(self, evc_id, expected_current, expected_failover):
        """Validate EVC paths (current and failover) with the expected values."""
        # get evc data
        response = requests.get(
            f"{KYTOS_API}/kytos/mef_eline/v2/evc/{evc_id}", timeout=5
        )
        evc = response.json()
        current = []
        for link in evc["current_path"]:
            current.append([link["endpoint_a"]["id"], link["endpoint_b"]["id"]])
        assert current == expected_current, str(current)
        failover = []
        for link in evc["failover_path"]:
            failover.append([link["endpoint_a"]["id"], link["endpoint_b"]["id"]])
        assert failover == expected_failover, str(failover)

    def validate_switch_flows(self, evc_id, expected_counts):
        """
        Validates flows length multiple switches.
        expected_counts: list[tuples] (switch_obj, expected_int)
        """
        for switch, count in expected_counts:
            flows = switch.dpctl(
                "dump-flows", f"cookie=0xa0{evc_id}/0xf0ffffffffffffff"
            ).splitlines()
            assert len(flows) == count, f"Wrong flows length for {switch}: {flows}"

    def validate_traffic(self, src, dst):
        """Send traffic from src to dst and check packet received on dst."""
        result = src.cmd(f"ping -c1 {dst.test_ip}")
        assert ", 0% packet loss," in result, result

        dst.cmd(f"truncate -s0 /tmp/{dst.name}-capture.log")
        src.cmd(
            f"python3 {SCRIPTS_DIR}/sendp.py -i {src.test_intf} "
            f"-s 127.0.0.1 -d {dst.test_ip} -c1 -p 80"
        )
        src.cmd(
            f"python3 {SCRIPTS_DIR}/sendp.py -i {src.test_intf} "
            f"-s 127.0.0.1 -d {dst.test_ip} -c1 -p 80 -u"
        )
        time.sleep(2)
        dst.cmd(f"kill -USR2 {dst.capture_pid}")
        pkts = dst.cmd(f"cat /tmp/{dst.name}-capture.log").splitlines()
        assert sum(dst.test_ip in s for s in pkts) == 2, str(pkts)

    def test_001_dynamic_evc_without_proxy(self):
        # pylint: disable=too-many-locals, too-many-statements
        """Test enabling INT on a dynamic EVC without proxy port."""
        h1, h2, h3 = self.net.net.get("h1", "h2", "h3")
        s1, s2, s3, s4, s5, s6 = self.net.net.get("s1", "s2", "s3", "s4", "s5", "s6")

        evc_id = self.create_evc(198)

        time.sleep(10)

        # configure hosts h1 and h3 and ping
        self.config_host_ip_vlan(h1, "10.1.98.1", 198)
        self.config_host_ip_vlan(h3, "10.1.98.3", 198)

        # enable INT Collector on switches
        s1.novi_cmd(
            "set config int monitor portno 3 ethdst 00:00:00:00:02:01 ethsrc 00:00:00:aa:aa:01 "
            "ipv4src 10.255.255.1 ipv4dst 10.255.255.254 udpsrc 6000 udpdst 5900 maxlen 320"
        )
        s1.novi_cmd("set config int maxhopcount 10")
        s6.novi_cmd(
            "set config int monitor portno 3 ethdst 00:00:00:00:02:03 ethsrc 00:00:00:aa:aa:06 "
            "ipv4src 10.255.255.6 ipv4dst 10.255.255.254 udpsrc 6000 udpdst 5900 maxlen 320"
        )
        s6.novi_cmd("set config int maxhopcount 10")

        # start int collector
        h2.cmd(
            f"python3 {SCRIPTS_DIR}/int_collector.py -i h2-eth1 -i h2-eth3 "
            ">/tmp/int_collector.log 2>&1 &"
        )

        # start tcpdump
        h1.cmd("tcpdump -i vlan198 -n -l -e tcp or udp >/tmp/h1-capture.log 2>&1 &")
        h1.capture_pid = h1.lastPid
        h3.cmd("tcpdump -i vlan198 -n -l -e tcp or udp >/tmp/h3-capture.log 2>&1 &")
        h3.capture_pid = h3.lastPid

        expected_current = [
            ["00:00:00:00:00:00:00:01:7", "00:00:00:00:00:00:00:06:7"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:5", "00:00:00:00:00:00:00:05:5"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows before enabling INT to make sure it all works
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 0, output

        # check flows
        # s1:
        #  - mef_eline: 2 current_path + 1 failover_path
        # s5:
        #  - mef_eline: 2 failover_path
        # s6:
        #  - mef_eline: 2 current_path + 1 failover_path
        self.validate_switch_flows(
            evc_id, [(s1, 3), (s2, 0), (s3, 0), (s4, 0), (s5, 2), (s6, 3)]
        )

        # enable INT
        api_url = KYTOS_API + "/kytos/telemetry_int/v1/evc/enable"
        response = requests.post(api_url, json={"evc_ids": [evc_id]}, timeout=5)
        assert response.status_code == 201, response.text

        time.sleep(15)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)

        # check flows
        # s1:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 table0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        # s5:
        #  - mef_eline: 2 failover_path
        #  - telemetry_int: 4 failover_path
        # s6:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        self.validate_switch_flows(
            evc_id, [(s1, 12), (s2, 0), (s3, 0), (s4, 0), (s5, 6), (s6, 12)]
        )

        #####################################################
        ## simulate link down on current path: s1:7 -- s6:7
        #####################################################
        self.net.net.configLinkStatus("s1", "s6", "down")

        time.sleep(15)

        expected_current = [
            ["00:00:00:00:00:00:00:01:5", "00:00:00:00:00:00:00:05:5"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:5", "00:00:00:00:00:00:00:06:5"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=5,eg_port=8,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=5,eg_port=8,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x5,ig_port=8,eg_port=5,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x5,ig_port=8,eg_port=5,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)

        # check flows
        # s1:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        # s2:
        #  - mef_eline: 2 failover_path
        #  - telemetry_int: 4 failover_path
        # s5:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 4 current_path
        # s6:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        self.validate_switch_flows(
            evc_id, [(s1, 12), (s2, 6), (s3, 0), (s4, 0), (s5, 6), (s6, 12)]
        )

        #####################################################
        ## simulate link down on current path: s1:5 -- s5:5
        #####################################################
        self.net.net.configLinkStatus("s1", "s5", "down")

        time.sleep(15)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:5", "00:00:00:00:00:00:00:06:5"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x2,ig_port=6,eg_port=5,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x2,ig_port=6,eg_port=5,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=5,eg_port=6,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=5,eg_port=6,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=5,queue=0",
        ], str(int_pkts)

        # check flows
        # s1:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        # s2:
        #  - mef_eline: 2 current_path + 2 failover_path
        #  - telemetry_int: 4 current_path + 4 failover_path
        # s5:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 4 current_path
        # s6:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        self.validate_switch_flows(
            evc_id, [(s1, 12), (s2, 12), (s3, 0), (s4, 0), (s5, 6), (s6, 12)]
        )

        #####################################################
        ## simulate link down on current path: s2:5 -- s6:5
        #####################################################
        self.net.net.configLinkStatus("s2", "s6", "down")

        time.sleep(15)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:8", "00:00:00:00:00:00:00:06:8"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:4", "00:00:00:00:00:00:00:03:4"],
            ["00:00:00:00:00:00:00:03:6", "00:00:00:00:00:00:00:04:6"],
            ["00:00:00:00:00:00:00:04:7", "00:00:00:00:00:00:00:05:7"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=8,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=8,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=8,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=8,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=8,queue=0",
        ], str(int_pkts)

        # check flows
        # s1:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        # s2:
        #  - mef_eline: 2 current_path + 2 failover_path
        #  - telemetry_int: 4 current_path + 4 failover_path
        # s3:
        #  - mef_eline: 2 failover_path
        #  - telemetry_int: 4 failover_path
        # s4:
        #  - mef_eline: 2 failover_path
        #  - telemetry_int: 4 failover_path
        # s5:
        #  - mef_eline: 2 current_path + 2 failover
        #  - telemetry_int: 4 current_path + 4 failover
        # s6:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        self.validate_switch_flows(
            evc_id, [(s1, 12), (s2, 12), (s3, 6), (s4, 6), (s5, 12), (s6, 12)]
        )

        #####################################################
        ## simulate link down on current path: s5:8 -- s6:8
        #####################################################
        self.net.net.configLinkStatus("s5", "s6", "down", port1=8, port2=8)

        time.sleep(15)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:4", "00:00:00:00:00:00:00:03:4"],
            ["00:00:00:00:00:00:00:03:6", "00:00:00:00:00:00:00:04:6"],
            ["00:00:00:00:00:00:00:04:7", "00:00:00:00:00:00:00:05:7"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]
        expected_failover = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=7,eg_port=9,queue=0",
            "sw_id=0x4,ig_port=6,eg_port=7,queue=0",
            "sw_id=0x3,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=7,eg_port=9,queue=0",
            "sw_id=0x4,ig_port=6,eg_port=7,queue=0",
            "sw_id=0x3,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x3,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x4,ig_port=7,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=7,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=4,eg_port=6,queue=0",
            "sw_id=0x3,ig_port=6,eg_port=4,queue=0",
            "sw_id=0x4,ig_port=7,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=7,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)

        # check flows
        # s1:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        # s2:
        #  - mef_eline: 2 current_path + 2 failover_path
        #  - telemetry_int: 4 current_path + 4 failover_path
        # s3:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 4 current_path
        # s4:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 4 current_path
        # s5:
        #  - mef_eline: 2 current_path + 2 failover
        #  - telemetry_int: 4 current_path + 4 failover
        # s6:
        #  - mef_eline: 2 current_path + 1 failover_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2) + 3 failover_path (2 tab0 + 1 tab2)
        self.validate_switch_flows(
            evc_id, [(s1, 12), (s2, 12), (s3, 6), (s4, 6), (s5, 12), (s6, 12)]
        )

        #####################################################
        ## simulate link down on current path: s2:4 -- s3:4
        #####################################################
        self.net.net.configLinkStatus("s2", "s3", "down")

        time.sleep(15)

        expected_current = [
            ["00:00:00:00:00:00:00:01:6", "00:00:00:00:00:00:00:02:6"],
            ["00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:05:3"],
            ["00:00:00:00:00:00:00:05:9", "00:00:00:00:00:00:00:06:9"],
        ]
        expected_failover = []

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=9,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x5,ig_port=3,eg_port=9,queue=0",
            "sw_id=0x2,ig_port=6,eg_port=3,queue=0",
            "sw_id=0x1,ig_port=1,eg_port=6,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x2,ig_port=3,eg_port=6,queue=0",
            "sw_id=0x5,ig_port=9,eg_port=3,queue=0",
            "sw_id=0x6,ig_port=1,eg_port=9,queue=0",
        ], str(int_pkts)

        # check flows
        # s1:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2)
        # s2:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 4 current_path
        # s5:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 4 current_path
        # s6:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2)
        self.validate_switch_flows(
            evc_id, [(s1, 8), (s2, 6), (s3, 0), (s4, 0), (s5, 6), (s6, 8)]
        )

        #####################################################
        ## simulate link down on current path: s5:9 -- s6:9
        #####################################################
        self.net.net.configLinkStatus("s5", "s6", "down", port1=9, port2=9)

        time.sleep(15)

        self.validate_evc_paths(evc_id, [], [])

        # test ping and flows
        result = h1.cmd("ping -c1 10.1.98.3")
        assert ", 100% packet loss," in result

        # check flows
        self.validate_switch_flows(
            evc_id, [(s1, 0), (s2, 0), (s3, 0), (s4, 0), (s5, 0), (s6, 0)]
        )

        #####################################################
        ## simulate link UP on s1 -- s6 to check if the EVC will recover
        #####################################################
        self.net.net.configLinkStatus("s1", "s6", "up")

        time.sleep(15)

        expected_current = [
            ["00:00:00:00:00:00:00:01:7", "00:00:00:00:00:00:00:06:7"],
        ]
        expected_failover = []

        self.validate_evc_paths(evc_id, expected_current, expected_failover)

        # clear counters
        h2.cmd("truncate -s0 /tmp/int_collector.log")

        # test ping, tcp, udp and flows
        self.validate_traffic(h1, h3)
        self.validate_traffic(h3, h1)

        output = h2.cmd("cat /tmp/int_collector.log")
        int_pkts = parse_int_collector(output)
        assert len(int_pkts) == 4, output
        assert int_pkts.get("ip_dst=10.1.98.3 tcp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.3 udp_dport=80") == [
            "sw_id=0x1,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 tcp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)
        assert int_pkts.get("ip_dst=10.1.98.1 udp_dport=80") == [
            "sw_id=0x6,ig_port=1,eg_port=7,queue=0"
        ], str(int_pkts)

        # check flows
        # s1:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2)
        # s5:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 4 current_path
        # s6:
        #  - mef_eline: 2 current_path
        #  - telemetry_int: 6 current_path (4 tab0 2 tab2)
        self.validate_switch_flows(
            evc_id, [(s1, 8), (s2, 0), (s3, 0), (s4, 0), (s5, 0), (s6, 8)]
        )
