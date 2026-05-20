import json
import re
import time

import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

# BasicFlows
# Each should have at least 3 flows, considering topology 'ring':
# - 01 for LLDP
# - 02 for amlight/coloring (node degree - number of neighbors)
BASIC_FLOWS = 3


class TestE2EFlowManager:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_030_restart_kytos_should_preserve_flows(self):
        """Test if, after kytos restart, the flows are preserved on the switch
           flow table."""

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 999
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                }
            ]
        }

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flow to be installed
        time.sleep(10)

        # make sure flow was installed and get initial time duration
        s1 = self.net.net.get('s1')
        assert len(flows_s1.splitlines()) == BASIC_FLOWS + 1, flows_s1
        flows_s1 = s1.dpctl('dump-flows')
        initial_duration = 0
        for flow in flows_s1.splitlines():
            match = re.search("duration=([0-9.]+).*dl_vlan=999", flow)
            if match:
                initial_duration = float(match.group(1))
                break
        assert initial_duration > 0, flows_s1

        # restart controller keeping configuration
        t1 = time.time()
        self.net.start_controller()
        self.net.wait_switches_connect()
        delta = time.time() - t1

        # wait a few seconds to allow consistency check to run
        time.sleep(20)
        initial_duration += 20

        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.splitlines()) == BASIC_FLOWS + 1, flows_s1
        duration  = 0
        for flow in flows_s1.splitlines():
            match = re.search("duration=([0-9.]+).*dl_vlan=999", flow)
            if match:
                duration = float(match.group(1))
                break
        assert duration > initial_duration + delta, flows_s1

    def test_031_on_switch_restart_kytos_should_recreate_flows(self):
        """Test if, after kytos restart, the flows are preserved on the switch 
           flow table."""

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 999
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                }
            ]
        }

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flow to be installed
        time.sleep(10)

        # OVS does not have a way to actually restart the switch
        # so to simulate that, we just delete all flows
        s1 = self.net.net.get('s1')
        s1.dpctl('del-flows')
        # reconnect to trigger and speed up consistency check after the handshake
        self.net.reconnect_switches()

        # wait for the flow to be installed
        time.sleep(10)

        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.splitlines()) == BASIC_FLOWS + 1, flows_s1
        assert 'dl_vlan=999' in flows_s1

    def test_032_on_switch_reconnection_should_recreate_untagged_any_flows(self):
        """Test if, after kytos restart, deserialize properly"""

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": "4096/4096"
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                },
                {
                    "priority": 10,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 0
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                },
            ]
        }

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flow to be installed
        time.sleep(10)

        # OVS does not have a way to actually restart the switch
        # so to simulate that, we just delete all flows
        s1 = self.net.net.get('s1')
        s1.dpctl('del-flows')
        # reconnect to trigger and speed up consistency check after the handshake
        self.net.reconnect_switches()

        # wait for the flow to be installed
        time.sleep(10)

        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.splitlines()) == BASIC_FLOWS + 2, flows_s1
        # 4096/4096
        assert 'vlan_tci=0x1000/0x1000' in flows_s1
        # 0
        assert 'vlan_tci=0x0000/0x1fff' in flows_s1
