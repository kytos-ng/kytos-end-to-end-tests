import os
import time
import pytest
import requests
from tests.helpers import NetworkTest

CONTROLLER = "127.0.0.1"
KYTOS_API = f"http://{CONTROLLER}:8181/api/kytos"


class TestE2ETopologyDupDpid:
    net = None

    def setup_method(self, method):
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="ring")
        cls.net.start()
        cls.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    @pytest.mark.skipif(
        os.environ.get("SWITCH_CLASS") == "NoviSwitch",
        reason="No need to repeat for NoviFlow",
    )
    def test_dup_dpid_does_not_overwrite_sw1_interfaces(self):
        """Test that a switch with a duplicate DPID connecting later does not
        overwrite the original sw1 interfaces in the topology.

        Scenario:
        1. Ring topology is running with sw1 (dpid 00:00:00:00:00:00:00:01),
           which has 5 interfaces: ports 1-4 and the local port 4294967294.
        2. The original sw1 interfaces are recorded via GET /v3/topology.
        3. A new switch with the same DPID as sw1 is added and started via the
           Mininet API. It only has the single local port (4294967294), so if
           Kytos were to accept its port list it would clobber sw1's 5 interfaces.
        4. Assert that sw1 still has its original set of interfaces and that the
           total number of switches in the topology is unchanged.
        """
        sw1_dpid = "00:00:00:00:00:00:00:01"

        api_url = f"{KYTOS_API}/topology/v3/"
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()

        switches = data["topology"]["switches"]
        assert sw1_dpid in switches, f"sw1 ({sw1_dpid}) not found in topology"
        assert switches[sw1_dpid]["enabled"]
        assert switches[sw1_dpid]["active"]

        original_interfaces = set(switches[sw1_dpid]["interfaces"].keys())
        expected_interfaces = {
            "00:00:00:00:00:00:00:01:1",
            "00:00:00:00:00:00:00:01:2",
            "00:00:00:00:00:00:00:01:3",
            "00:00:00:00:00:00:00:01:4",
            "00:00:00:00:00:00:00:01:4294967294",
        }
        assert original_interfaces == expected_interfaces, (
            f"Unexpected initial interfaces for sw1: {original_interfaces}"
        )
        original_switch_count = len(switches)

        mn = self.net.net
        dup_sw = mn.addSwitch("dup_s1", dpid="0000000000000001")
        try:
            dup_sw.start(mn.controllers)

            time.sleep(10)

            response = requests.get(api_url)
            assert response.status_code == 200, response.text
            data = response.json()

            switches = data["topology"]["switches"]
            assert sw1_dpid in switches, (
                f"sw1 ({sw1_dpid}) disappeared from topology after "
                "duplicate DPID switch connected"
            )

            current_interfaces = set(switches[sw1_dpid]["interfaces"].keys())
            assert original_interfaces == current_interfaces, (
                "sw1's interfaces were overwritten by the duplicate DPID switch. "
                f"Original: {original_interfaces}, "
                f"Current: {current_interfaces}"
            )

            assert len(switches) == original_switch_count, (
                f"Switch count changed after duplicate DPID connected. "
                f"Expected {original_switch_count}, got {len(switches)}"
            )
        finally:
            dup_sw.stop()
            mn.switches.remove(dup_sw)
            del mn.nameToNode[dup_sw.name]
