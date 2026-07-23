"""End-to-end tests for telemetry_int Napp."""

import json
import os
import signal
import subprocess
import time

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
        
        for switch in self.net.net.switches:
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

        # delete vlan interfaces
        for host in self.net.net.hosts:
            result: str = host.cmd("ip --json link show")
            interfaces: list = json.loads(result)
            for interface in interfaces:
                # Check if link is a vlan interface
                if "link" in interface:
                    host.cmd(f"ip link del {interface['ifname']}")

        # Delete any openflow flows on the switches
        for switch in self.net.net.switches:
            switch.dpctl("del-flows")

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

    def test_002_local_forwarding_vlans(self):
        """
        Description: Test if local forwarding is working properly
        """

        s1, s2, s3 = self.net.net.get("s1", "s2", "s3")

        h11, h12, h2, h3 = self.net.net.get("h11", "h12", "h2", "h3")

        # Use local forwarding to config route from h11 to h2 over a vlan

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=1,vlan_vid=100,priority=100 output=3,set_vlan=100")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=100,priority=100 output=1,set_vlan=100")

        s2.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=100,priority=100 output=1,set_vlan=100")
        s2.cmd("p4ofagent set config p4 local_forwarding in_port=1,vlan_vid=100,priority=100 output=2,set_vlan=100")

        # Use local forwarding to config route from h12 to h3 through s2 on vlan 101

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=100,priority=100 output=3,set_vlan=101")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=101,priority=100 output=2,set_vlan=100") 

        s2.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=101,priority=100 output=3")
        s2.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=101,priority=100 output=2")

        s3.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=101,priority=100 output=1,set_vlan=100")
        s3.cmd("p4ofagent set config p4 local_forwarding in_port=1,vlan_vid=100,priority=100 output=2,set_vlan=101")

        # Create Vlan Interfaces on h11, h12, h2, and h3

        h11.cmd("ip link add link h11-eth0 name h11-eth0.100 type vlan id 100")
        h12.cmd("ip link add link h12-eth0 name h12-eth0.100 type vlan id 100")
        h2.cmd("ip link add link h2-eth0 name h2-eth0.100 type vlan id 100")
        h3.cmd("ip link add link h3-eth0 name h3-eth0.100 type vlan id 100")

        # Set all vlan interfaces up

        h11.cmd("ip link set h11-eth0.100 up")
        h12.cmd("ip link set h12-eth0.100 up")
        h2.cmd("ip link set h2-eth0.100 up")
        h3.cmd("ip link set h3-eth0.100 up")

        # Set IPs of hosts.

        h11.setIP("10.0.0.11", prefixLen=8)
        h12.setIP("10.0.0.12", prefixLen=8)
        h2.setIP("10.0.0.2", prefixLen=8)
        h3.setIP("10.0.0.3", prefixLen=8)

        # Set IPs of vlan interfaces

        h11.cmd("ip addr add 10.0.100.11/24 dev h11-eth0.100")
        h12.cmd("ip addr add 10.0.100.12/24 dev h12-eth0.100")
        h2.cmd("ip addr add 10.0.100.2/24 dev h2-eth0.100")
        h3.cmd("ip addr add 10.0.100.3/24 dev h3-eth0.100")

        # Test sending packets from h11 to h2 on vlan interfaces and vice versa

        result = h11.cmd("ping -c1 10.0.100.2")
        assert ', 0% packet loss,' in result

        result = h2.cmd("ping -c1 10.0.100.11")
        assert ', 0% packet loss,' in result

        # Test sending packets from h12 to h3 and vice versa

        result = h12.cmd("ping -c1 10.0.100.3")
        assert ', 0% packet loss,' in result

        result = h3.cmd("ping -c1 10.0.100.12")
        assert ', 0% packet loss,' in result

    def test_003_local_forwarding_vlans_mixed(self):
        """
        Description: Test if local forwarding with vlans is working properly
        """

        s1, s2, s3 = self.net.net.get("s1", "s2", "s3")

        h11, h2, h3 = self.net.net.get("h11", "h2", "h3")

        # Use local forwarding to config route from h11 to h2 on vlan 100

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=1,vlan_vid=100,priority=100 output=3,set_vlan=100")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=100,priority=100 output=1,set_vlan=100")

        s2.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=100,priority=100 output=1,set_vlan=100")
        s2.cmd("p4ofagent set config p4 local_forwarding in_port=1,vlan_vid=100,priority=100 output=2,set_vlan=100")

        # Use local forwarding to config route from h11 to h3 on vlan 101

        s1.cmd("p4ofagent set config p4 local_forwarding in_port=1,vlan_vid=101,priority=100 output=3,set_vlan=101")
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=101,priority=100 output=1,set_vlan=101") 

        s2.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=101,priority=100 output=3,set_vlan=101")
        s2.cmd("p4ofagent set config p4 local_forwarding in_port=3,vlan_vid=101,priority=100 output=2,set_vlan=101")

        s3.cmd("p4ofagent set config p4 local_forwarding in_port=2,vlan_vid=101,priority=100 output=1,set_vlan=101")
        s3.cmd("p4ofagent set config p4 local_forwarding in_port=1,vlan_vid=101,priority=100 output=2,set_vlan=101")

        # Create Vlan Interfaces on h11, h12, h2, and h3

        h11.cmd("ip link add link h11-eth0 name h11-eth0.100 type vlan id 100")
        h11.cmd("ip link add link h11-eth0 name h11-eth0.101 type vlan id 101")
        h2.cmd("ip link add link h2-eth0 name h2-eth0.100 type vlan id 100")
        h3.cmd("ip link add link h3-eth0 name h3-eth0.101 type vlan id 101")

        # Set all vlan interfaces up

        h11.cmd("ip link set h11-eth0.100 up")
        h11.cmd("ip link set h11-eth0.101 up")
        h2.cmd("ip link set h2-eth0.100 up")
        h3.cmd("ip link set h3-eth0.101 up")

        # Set IPs of hosts.

        h11.setIP("10.0.0.11", prefixLen=8)
        h2.setIP("10.0.0.2", prefixLen=8)
        h3.setIP("10.0.0.3", prefixLen=8)

        # Set IPs of vlan interfaces

        h11.cmd("ip addr add 10.0.100.11/24 dev h11-eth0.100")
        h11.cmd("ip addr add 10.0.101.11/24 dev h11-eth0.101")
        h2.cmd("ip addr add 10.0.100.2/24 dev h2-eth0.100")
        h3.cmd("ip addr add 10.0.101.3/24 dev h3-eth0.101")

        # Test sending packets from h11 to h2 on vlan interfaces and vice versa

        result = h11.cmd("ping -c1 10.0.100.2")
        assert ', 0% packet loss,' in result

        result = h2.cmd("ping -c1 10.0.100.11")
        assert ', 0% packet loss,' in result

        # Test sending packets from h12 to h3 and vice versa

        result = h11.cmd("ping -c1 10.0.101.3")
        assert ', 0% packet loss,' in result

        result = h3.cmd("ping -c1 10.0.101.11")
        assert ', 0% packet loss,' in result


        # TODO: Test these input rules
        # eth_dst, eth_type, in_port, priority, vlan_vid
        # TODO: Test these actions
        # copy_to_cpu, drop, no_action, output, send_packet_in, set_vlan

    def test_004_local_forwarding_send_packet_in(self):
        """
        Description: Test if send_packet_in is working correctly.
        """
        # Create a simple OpenFlow controller to receive packet_in messages
        controller_script = "tests/test_packet_in_controller.py"
        
        # Get switches and hosts
        h11, h12, s1 = self.net.net.get("h11", "h12", "s1")

        # Use dpctl on the switch to create connection between h11 and h12
        s1.dpctl("add-flow", "table=0,in_port=1,priority=100,actions=output:2")
        s1.dpctl("add-flow", "table=0,in_port=2,priority=100,actions=output:1")

        # Send a ping from h11 to h12 to test connection
        result = h11.cmd(f"ping -c1 {h12.IP()}")
        assert ', 0% packet loss,' in result

        # s1.dpctl("del-flows")

        # Reset the controller connection for all switches

        for switch in self.net.net.switches:
            switch.reset_controller()

        # Start the controller in background
        controller_process = subprocess.Popen(
            [
                "python3", 
                controller_script
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        
        # Give the controller time to start
        time.sleep(4)

        # Configure switch to send packet_in for specific traffic
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 send_packet_in")

        # Send a ping from h11 to h12 to trigger send_packet_in
        result = h11.cmd(f"ping -c1 {h12.IP()}")

        # All packets should be terminated at the send_packet_in rule
        assert ', 100% packet loss,' in result

        # Wait a bit for the controller to receive the message
        time.sleep(4)

        # Stop the controller
        controller_process.send_signal(signal.SIGINT)
        controller_process.wait()

        # Capture controller output

        output = controller_process.communicate()[0].decode('utf-8')

        # Check if controller was able to receive the packet_in
        
        assert "Packet-in received from switch" in output

    def test_005_local_forwarding_copy_to_cpu(self):
        """
        Description: Test if copy_to_cpu is working correctly.
        """
        # Get switches and hosts
        h11, h12, s1 = self.net.net.get("h11", "h12", "s1")

        # Use dpctl on the switch to create connection between h11 and h12
        s1.dpctl("add-flow", "table=0,in_port=1,priority=100,actions=output:2")
        s1.dpctl("add-flow", "table=0,in_port=2,priority=100,actions=output:1")

        # Send a ping from h11 to h12 to test connection
        result = h11.cmd(f"ping -c1 {h12.IP()}")
        assert ', 0% packet loss,' in result

        # Start listener on the cpu port

        cpu_listener = s1.popen(
            [
                "tcpdump",
                "-U",
                "-i", "veth320",
                # "-w", "/tmp/cpu_packets.pcap",
                # Listen for ping packets to verify copy_to_cpu functionality
                # f"icmp[0] == 8 and host {h12.IP()}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        
        # Give the listener time to start
        time.sleep(4)

        # Configure switch to send packet_in for specific traffic
        s1.cmd("p4ofagent set config p4 local_forwarding in_port=1,priority=100 copy_to_cpu")
        s1.cmd("p4ofagent set config switch copy_to_cpu 1")

        # Send a ping from h11 to h12 to check that the connection still works
        result = h11.cmd(f"ping -c1 {h12.IP()}")
        assert ', 0% packet loss,' in result

        # Wait a bit for the listener to receive the message
        time.sleep(4)

        # Stop the listener
        cpu_listener.send_signal(signal.SIGINT)
        cpu_listener.wait()

        # Capture listener output

        output = cpu_listener.communicate()[0].decode('utf-8')

        # Check if listener was able to receive the ping packets
        
        assert "Packet-in received from switch" in output
