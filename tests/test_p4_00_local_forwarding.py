"""End-to-end tests for telemetry_int Napp."""

import os

import pytest

from .helpers import NetworkTest

CONTROLLER = "127.0.0.1"
KYTOS_API = "http://%s:8181/api/kytos" % CONTROLLER

@pytest.mark.skipif(
    os.environ.get("SWITCH_CLASS") not in ("NoviSwitch", "P4OfSwitch")
    or (
        os.environ.get("SWITCH_CLASS") == "NoviSwitch"
        and os.environ.get("NOVIVERSION") != "NW570.6.1"
    ),
    reason="NoviSwitch does not support interface removal",
)
class TestP4LocalForwarding:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Since some tests may set a link to down state, we should reset
        # the link state to up (for all links)
        self.net.config_all_links_up()
        # Start the controller with all elements enabled and clean database
        # self.net.restart_kytos_clean()
        # time.sleep(10)
        
        for switch in self.net.net.get("s1", "s2", "s3"):
            result: str = switch.cmd("p4ofagent show config p4 local_forwarding --command")
            result = result.splitlines()
            # Remove first line that contains comment
            result = result[1:]
            for command in result:
                # Replace set with del in command
                command = command.replace("set", "del")
                # Remove last argument of the command
                command = " ".join(command.split(" ")[:-1])
                switch.cmd(command)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="ring")
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_001_local_forwarding_ports(self):
        """
        Description: Test if local forwarding is working properly
        """

        s1, s2, s3 = self.net.net.get("s1", "s2", "s3")

        h11, h12, h2, h3 = self.net.net.get("h11", "h12", "h2", "h3")

        # Use local forwarding to config route from h11 to h2

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 output=3")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=3,priority=100 output=1")

        s2.cmd("p4ofagent set config p4 local_forwarding in_port=2,priority=100 output=1")
        s2.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 output=2")

        # Use local forwarding to config route from h12 to h3

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=2,priority=100 output=4")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=4,priority=100 output=2")

        s3.cmd("p4ofagent set config p4 local_forwarding in_port=3,priority=100 output=1")
        s3.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 output=3")

        # Set IPs of hosts.

        h11.setIP("10.0.0.11", prefixLen=8)
        h12.setIP("10.0.0.12", prefixLen=8)
        h2.setIP("10.0.0.2", prefixLen=8)
        h3.setIP("10.0.0.3", prefixLen=8)

        # Test sending packets from h11 to h2 and vice versa

        result = h11.cmd(f"ping -c1 {h2.IP()}")
        assert ', 0% packet loss,' in result

        result = h2.cmd(f"ping -c1 {h11.IP()}")
        assert ', 0% packet loss,' in result

        # Test sending packets from h12 to h3 and vice versa

        result = h12.cmd(f"ping -c1 {h3.IP()}")
        assert ', 0% packet loss,' in result

        result = h3.cmd(f"ping -c1 {h12.IP()}")
        assert ', 0% packet loss,' in result


        # TODO: Test these input rules
        # eth_dst, eth_type, in_port, priority, vlan_vid
        # TODO: Test these actions
        # copy_to_cpu, drop, no_action, output, send_packet_in, set_vlan

    def test_002_local_forwarding_vlans(self):
        """
        Description: Test if local forwarding is working properly
        """

        s1, s2, s3 = self.net.net.get("s1", "s2", "s3")

        h11, h12, h2, h3 = self.net.net.get("h11", "h12", "h2", "h3")

        # Use local forwarding to config route from h11 to h2 over a vlan

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 output=3,set_vlan=100")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=100,priority=100 output=1,set_vlan=0")

        s2.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=100,priority=100 output=1,set_vlan=0")
        s2.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 output=2,set_vlan=100")

        # Use local forwarding to config route from h12 to h3 through s2 on vlan 101

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=2,priority=100 output=3,set_vlan=101")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=101,priority=100 output=2,set_vlan=0") 

        s2.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=101,priority=100 output=3")
        s2.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=101,priority=100 output=2")

        s3.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=101,priority=100 output=1,set_vlan=0")
        s3.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 output=2,set_vlan=101")

        # Set IPs of hosts.

        h11.setIP("10.0.0.11", prefixLen=8)
        h12.setIP("10.0.0.12", prefixLen=8)
        h2.setIP("10.0.0.2", prefixLen=8)
        h3.setIP("10.0.0.3", prefixLen=8)

        # Test sending packets from h11 to h2 and vice versa

        result = h11.cmd(f"ping -c1 {h2.IP()}")
        assert ', 0% packet loss,' in result

        result = h2.cmd(f"ping -c1 {h11.IP()}")
        assert ', 0% packet loss,' in result

        # Test sending packets from h12 to h3 and vice versa

        result = h12.cmd(f"ping -c1 {h3.IP()}")
        assert ', 0% packet loss,' in result

        result = h3.cmd(f"ping -c1 {h12.IP()}")
        assert ', 0% packet loss,' in result

