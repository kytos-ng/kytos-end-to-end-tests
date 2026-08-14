"""End-to-end tests for telemetry_int Napp."""

import json
import os
import signal
import subprocess
import time

import pytest

from pyof.v0x04.common.header import Type
from pyof.v0x04.common.port import PortState
from pyof.v0x04.asynchronous.port_status import PortStatus

from .helpers import NetworkTest
from .simple_of_controller import OpenFlowController

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
class TestP4PortStatusQuarantine:
    net = None
    of_controller = None

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
            # Clear local_forwarding config
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

            # Clear copy_to_cpu
            result: str = switch.cmd("p4ofagent show config switch copy_to_cpu --command")
            result = result.splitlines()
            result = result[1:]
            for command in result:
                command = command.replace("set", "del")
                switch.cmd(command)

            # Clear port quarantine
            result: str = switch.cmd("p4ofagent show config port portno all --command")
            result = result.splitlines()
            result = result[1:]
            for command in result:
                if "quarantine" not in command:
                    continue
                command = command.replace("set", "del")
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

        # # Undo IP assignments
        # for host in self.net.net.hosts:
        #     host.cmd(f"ip addr flush dev {host.defaultIntf().name}")
        #     host.defaultIntf().updateIP()

        # Delete any openflow flows on the switches
        for switch in self.net.net.switches:
            switch.dpctl("del-flows")

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="ring")
        cls.net.start(start_controller=False)
        cls.of_controller = OpenFlowController()
        cls.of_controller.start()

    @classmethod
    def teardown_class(cls):
        cls.net.stop()
        cls.of_controller.stop()

    def test_001_port_status_quarantine(self):
        """
        Description: Test if port quarantine is working correctly.
        """
        # Get switches and hosts
        h11, h12, s1 = self.net.net.get("h11", "h12", "s1")

        # Configure port quarantine on switch 1
        s1.cmd("p4ofagent set config quarantine 3 5")

        s1_connection = self.of_controller.get_switch_by_dpid("00:00:00:00:00:00:00:01")
        assert s1_connection

        port_down_time = None
        port_up_time = None

        def watcher_func(
            message: PortStatus
        ) -> None:
            nonlocal port_down_time, port_up_time
            if message.desc.state & PortState.OFPPS_LINK_DOWN:
                port_down_time = time.time()
            else:
                port_up_time = time.time()

        with s1_connection.watch(
            Type.OFPT_PORT_STATUS,
            watcher_func
        ):
            # Config link down
            self.net.net.configLinkStatus("s1", "s2", "down")

            time.sleep(1)

            # Config link up
            self.net.net.configLinkStatus("s1", "s2", "up")

            time.sleep(10)
        
        assert port_down_time is not None
        assert port_up_time is not None

        total_down_time = port_up_time - port_down_time

        assert total_down_time > 4.5

    def test_002_port_status_no_quarantine(self):
        """
        Description: Sanity check timing when quarantine isn't set.
        """
        # Get switches and hosts
        h11, h12, s1 = self.net.net.get("h11", "h12", "s1")

        s1_connection = self.of_controller.get_switch_by_dpid("00:00:00:00:00:00:00:01")
        assert s1_connection

        port_down_time = None
        port_up_time = None

        def watcher_func(
            message: PortStatus
        ) -> None:
            nonlocal port_down_time, port_up_time
            if message.desc.state & PortState.OFPPS_LINK_DOWN:
                port_down_time = time.time()
            else:
                port_up_time = time.time()

        with s1_connection.watch(
            Type.OFPT_PORT_STATUS,
            watcher_func
        ):
            # Config link down
            self.net.net.configLinkStatus("s1", "s2", "down")

            time.sleep(1)

            # Config link up
            self.net.net.configLinkStatus("s1", "s2", "up")

            time.sleep(10)
        
        assert port_down_time is not None
        assert port_up_time is not None

        total_down_time = port_up_time - port_down_time

        assert total_down_time < 4.5