import json
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
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
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

    def test_005_install_flow(self):
        """Tests if, after kytos restart, a flow installed
        to a switch will still be installed."""

        cookie = 1024
        payload = {
            "flows": [
                {
                    "cookie": cookie,
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
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

        # restart controller keeping configuration
        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()

        time.sleep(10)

        # Make sure that the flow that was sent is on /v2/stored_flows
        dpid = "00:00:00:00:00:00:00:01"
        response = requests.get(
            f"{KYTOS_API}/flow_manager/v2/stored_flows?state=installed&dpid={dpid}"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert dpid in data
        assert len(data[dpid]) == BASIC_FLOWS + 1
        expected_flow = payload["flows"][0]
        for stored_flow in data[dpid]:
            if stored_flow["flow"]["cookie"] != cookie:
                continue
            assert stored_flow["state"] == "installed"
            for key, value in expected_flow.items():
                assert stored_flow["flow"][key] == value, stored_flow

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert 'actions=output:"s1-eth2"' in flows_s1

    def test_010_install_flow_and_retrieve_it_back(self):
        """Tests the flow status through the
        API's call after its installation."""

        switch_id = '00:00:00:00:00:00:00:01'

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
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

        # It installs the flow
        api_url = KYTOS_API + '/flow_manager/v2/flows/' + switch_id
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        # restart controller keeping configuration
        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()

        time.sleep(10)

        sw_name = "s1"
        sw = self.net.net.get(sw_name)
        flows_sw = sw.dpctl("dump-flows")
        assert len(flows_sw.split('\r\n ')) == BASIC_FLOWS + 1, flows_sw
        assert 'actions=output:"%s-eth2"' % sw_name in flows_sw

        stored_flows = f'{KYTOS_API}/flow_manager/v2/stored_flows/?dpids={switch_id}'
        response = requests.get(stored_flows)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data[switch_id]) == BASIC_FLOWS + 1
        assert data[switch_id][-1]["flow"]["actions"] == payload["flows"][0]["actions"]
        assert data[switch_id][-1]["flow"]["match"] == payload["flows"][0]["match"]
        assert data[switch_id][-1]["flow"]["priority"] == payload["flows"][0]["priority"]
        assert data[switch_id][-1]["flow"]["idle_timeout"] == payload["flows"][0]["idle_timeout"]
        assert data[switch_id][-1]["flow"]["hard_timeout"] == payload["flows"][0]["hard_timeout"]

    def test_011_install_flows_same_match_diff_cookie(self):
        """Test install flows with same match but different cookie
        it's expected that cookie will be overwritten."""

        switch_id = '00:00:00:00:00:00:00:01'
        cookie1, cookie2 = 100, 101
        payload = {
            "flows": [
                {
                    "priority": 10,
                    "cookie": cookie1,
                    "match": {
                        "in_port": 1
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
                    "cookie": cookie2,
                    "match": {
                        "in_port": 1
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

        # It installs the flow
        api_url = KYTOS_API + '/flow_manager/v2/flows/' + switch_id
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        sw_name = "s1"
        sw = self.net.net.get(sw_name)
        flows_sw = sw.dpctl("dump-flows")
        assert len(flows_sw.split('\r\n ')) == BASIC_FLOWS + 1, flows_sw
        assert 'actions=output:"%s-eth2"' % sw_name in flows_sw
        assert 'cookie=0x65' in flows_sw
        assert 'cookie=0x64' not in flows_sw

        stored_flows = f'{KYTOS_API}/flow_manager/v2/stored_flows/?dpids={switch_id}&cookie_range={cookie1}&cookie_range={cookie2}'
        response = requests.get(stored_flows)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data[switch_id]) == 1
        assert data[switch_id][0]["flow"]["actions"] == payload["flows"][1]["actions"]
        assert data[switch_id][0]["flow"]["match"] == payload["flows"][1]["match"]
        assert data[switch_id][0]["flow"]["priority"] == payload["flows"][1]["priority"]
        assert data[switch_id][0]["flow"]["cookie"] == payload["flows"][1]["cookie"]

    def test_015_install_flows(self):
        """Tests if, after kytos restart, a flow installed
        to all switches will still be installed."""

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
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

        api_url = KYTOS_API + '/flow_manager/v2/flows'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flow to be installed
        time.sleep(10)

        # restart controller keeping configuration
        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()

        time.sleep(10)

        for sw_name in ['s1', 's2', 's3']:
            sw = self.net.net.get(sw_name)
            flows_sw = sw.dpctl('dump-flows')
            assert len(flows_sw.split('\r\n ')) == BASIC_FLOWS + 1, flows_sw
            assert 'actions=output:"%s-eth2"' % sw_name in flows_sw

    def test_016_install_invalid_flow_cookie_overflowed(self):
        """Test try to install an overflowed cookie value."""
        payload = {
          "flows": [
            {
              "priority": 101,
              "cookie": 27115650311270694912,
              "match": {
                "in_port": 1
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
        api_url = KYTOS_API + '/flow_manager/v2/flows'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 400, response.text
        data = response.json()
        assert "FlowMod.cookie" in data["description"]

    def test_017_install_flows_and_retrieve_sorted(self):
        """Test that flows are returned in order according to priority."""

        switch_id = '00:00:00:00:00:00:00:01'

        payload = {
            "flows": [
                {
                    "cookie": 1,
                    "priority": 1,
                    "match": {
                        "in_port": 1
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 3
                        }
                    ]
                },
                {
                    "cookie": 2,
                    "priority": 10,
                    "match": {
                        "in_port": 1
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                },
                {
                    "cookie": 3,
                    "priority": 20,
                    "match": {
                        "in_port": 2
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

        # It installs the flow
        api_url = KYTOS_API + '/flow_manager/v2/flows/' + switch_id
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        # restart controller keeping configuration
        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()

        time.sleep(10)

        stored_flows = f'{KYTOS_API}/flow_manager/v2/stored_flows/?dpids={switch_id}&cookie_range=1&cookie_range=3'
        response = requests.get(stored_flows)
        assert response.status_code == 200, response.text
        data = response.json()
        data = data[switch_id]
        assert data[0]["flow"]["priority"] > data[1]["flow"]["priority"] 
        assert data[1]["flow"]["priority"] > data[2]["flow"]["priority"]

    def test_020_delete_flow(self):
        """Tests if, after kytos restart, a flow deleted
        from a switch will still be deleted."""

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
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
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        # delete the flow
        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.delete(api_url, data=json.dumps(payload),
                                   headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flow to be deleted
        time.sleep(10)

        # restart controller keeping configuration
        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()

        time.sleep(10)

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, flows_s1
        assert 'actions=output:"s1-eth2"' not in flows_s1

    def test_025_delete_flows(self):
        """Tests if, after kytos restart, a flow deleted
        from all switches will still be deleted."""

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
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

        api_url = KYTOS_API + '/flow_manager/v2/flows'
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        # delete the flow
        api_url = KYTOS_API + '/flow_manager/v2/flows'
        response = requests.delete(api_url, data=json.dumps(payload),
                                   headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flow to be deleted
        time.sleep(10)

        # restart controller keeping configuration
        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()

        time.sleep(10)

        # Make sure that flows are soft deleted on /v2/stored_flows
        response = requests.get(
            f"{KYTOS_API}/flow_manager/v2/stored_flows?state=deleted"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        for i in range(1, 4):
            dpid = f"00:00:00:00:00:00:00:0{i}"
            assert dpid in data
            assert len(data[dpid]) == len(payload["flows"])
            expected_flow = payload["flows"][0]
            flow_entry = data[dpid][0]["flow"]
            for key, value in expected_flow.items():
                assert flow_entry[key] == value, flow_entry

        # Make sure that flows are deleted on /v2/flows
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        for i in range(1, 4):
            dpid = f"00:00:00:00:00:00:00:0{i}"
            assert dpid in data
            assert len(data[dpid]["flows"]) == BASIC_FLOWS, data[dpid]

        for sw_name in ['s1', 's2', 's3']:
            sw = self.net.net.get(sw_name)
            flows_sw = sw.dpctl('dump-flows')
            assert len(flows_sw.split('\r\n ')) == BASIC_FLOWS, flows_sw
            assert 'actions=output:"%s-eth2"' % sw_name not in flows_sw

    def test_026_delete_flows_cookie_mask_range(self):
        """Test deleting flows with cookie range mask and persistence."""""

        payload = {
            "flows": [
                {
                    "cookie": 0xaa00000000000001,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 100
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                },
                {
                    "cookie": 0xaa00000000000002,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 101
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

        dpid = "00:00:00:00:00:00:00:01"
        api_url = f"{KYTOS_API}/flow_manager/v2/flows/{dpid}"
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        # wait for the flows to be installed
        time.sleep(10)

        # it's expected to match all 0xaa cookie prefix
        delete_payload = {
          "flows": [
            {
              "cookie": 0xaa00000000000000,
              "cookie_mask": 0xff00000000000000
            }
          ]
        }

        # delete the flows
        api_url = f"{KYTOS_API}/flow_manager/v2/flows/{dpid}"
        response = requests.delete(api_url, data=json.dumps(delete_payload),
                                   headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flows to be deleted
        time.sleep(10)

        # restart controller keeping configuration
        self.net.start_controller(enable_all=True, del_flows=True)
        self.net.wait_switches_connect()

        time.sleep(10)

        # Make sure that flows are soft deleted on /v2/stored_flows
        response = requests.get(
            f"{KYTOS_API}/flow_manager/v2/stored_flows?state=deleted"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert dpid in data
        assert len(data[dpid]) == len(payload["flows"])

        expected_flows = payload["flows"]
        flow_entry = data[dpid]
        for i, flow in enumerate(expected_flows):
            for key, value in flow.items():
                assert flow_entry[i]["flow"][key] == value, flow_entry[i]

        # Make sure that flows are deleted on /v2/flows
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert dpid in data
        assert len(data[dpid]["flows"]) == BASIC_FLOWS, data[dpid]

        sw = self.net.net.get("s1")
        flows_sw = sw.dpctl("dump-flows")
        assert len(flows_sw.split('\r\n ')) == BASIC_FLOWS, flows_sw

    def test_027_delete_flows_cookie_mask_range_any(self):
        """Test deleting flows with cookie range mask any."""""

        payload = {
            "flows": [
                {
                    "cookie": 0xaa00000000000001,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 100
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                },
                {
                    "cookie": 0xaa00000000000002,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 101
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                },
                {
                    "cookie": 0xbb00000000000001,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 102
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

        dpid = "00:00:00:00:00:00:00:01"
        api_url = f"{KYTOS_API}/flow_manager/v2/flows/{dpid}"
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        # wait for the flows to be installed
        time.sleep(10)

        # cookie mask all 0's means match any
        delete_payload = {
          "flows": [
            {
              "cookie": 0x0000000000000000,
              "cookie_mask": 0x0000000000000000
            }
          ]
        }

        # delete the flows
        api_url = f"{KYTOS_API}/flow_manager/v2/flows/{dpid}"
        response = requests.delete(api_url, data=json.dumps(delete_payload),
                                   headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']

        # wait for the flows to be deleted
        time.sleep(10)

        # Make sure that flows are soft deleted on /v2/stored_flows
        response = requests.get(
            f"{KYTOS_API}/flow_manager/v2/stored_flows?state=deleted"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert dpid in data
        assert len(data[dpid]) == len(payload["flows"]) + BASIC_FLOWS

        # Make sure that flows are deleted on /v2/flows
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert dpid in data
        assert len(data[dpid]["flows"]) == 0, data[dpid]

        sw = self.net.net.get("s1")
        flows_sw = sw.dpctl("dump-flows")
        assert flows_sw.split('\r\n ') == [''], flows_sw

    def test_028_delete_flows_cookie_mask_range_partial(self):
        """Test deleting flows with cookie range mask partial match."""""

        payload = {
            "flows": [
                {
                    "cookie": 0xaa00000000000001,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 100
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2
                        }
                    ]
                },
                {
                    "cookie": 0xaa00000000000002,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 101
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

        dpid = "00:00:00:00:00:00:00:01"
        api_url = f"{KYTOS_API}/flow_manager/v2/flows/{dpid}"
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        # wait for the flows to be installed
        time.sleep(10)

        # it's expected to match [0xaa00000000000000, 0xaa00000000000001]
        delete_payload = {
          "flows": [
            {
              "cookie": 0xaa00000000000000,
              "cookie_mask": 0xfffffffffffffffe
            }
          ]
        }

        # delete the flow
        api_url = f"{KYTOS_API}/flow_manager/v2/flows/{dpid}"
        response = requests.delete(api_url, data=json.dumps(delete_payload),
                                   headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data = response.json()
        assert 'FlowMod Messages Sent' in data['response']
        # wait for the flow to be deleted
        time.sleep(10)

        # Make sure that only one flow got soft deleted on /v2/stored_flows
        response = requests.get(
            f"{KYTOS_API}/flow_manager/v2/stored_flows?state=deleted"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert dpid in data
        assert len(data[dpid]) == 1

        expected_flows = payload["flows"]
        flow_entry = data[dpid]
        for key, value in expected_flows[0].items():
            assert flow_entry[0]["flow"][key] == value, flow_entry[0]

        # Make sure that only one flow got deleted on /v2/flows
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert dpid in data
        assert len(data[dpid]["flows"]) == BASIC_FLOWS + 1, data[dpid]

        # Make sure that only one flow got deleted
        sw = self.net.net.get("s1")
        flows_sw = sw.dpctl("dump-flows")
        assert len(flows_sw.split('\r\n ')) == BASIC_FLOWS + 1, flows_sw
        assert 'dl_vlan=101' in flows_sw

    def modify_match(self, restart_kytos=False):
        """Tests if after a match is modified outside
        kytos, the original flow is restored."""

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
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
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        s1 = self.net.net.get('s1')
        s1.dpctl('del-flows', 'in_port=1')
        s1.dpctl('add-flow', 'idle_timeout=360,hard_timeout=1200,priority=10,'
                             'dl_vlan=324,actions=output:1')
        if restart_kytos:
            # restart controller keeping configuration
            self.net.start_controller(enable_all=True)
            self.net.wait_switches_connect()
        else:
            self.net.reconnect_switches()

        time.sleep(10)

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert 'in_port="s1-eth1' in flows_s1

    def test_030_modify_match(self):
        self.modify_match()

    def test_035_modify_match_restarting(self):
        self.modify_match(restart_kytos=True)

    def replace_action_flow(self, restart_kytos=False):

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
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
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        # Verify the flow
        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert 'in_port="s1-eth1' in flows_s1

        # Modify the actions and verify its modification
        s1.dpctl('mod-flows', 'actions=output:3')
        flows_s1 = s1.dpctl('dump-flows')
        assert 'actions=output:"s1-eth2"' not in flows_s1
        assert 'actions=output:"s1-eth3"' in flows_s1

        if restart_kytos:
            # restart controller keeping configuration
            self.net.start_controller(enable_all=True)
            self.net.wait_switches_connect()
        else:
            self.net.reconnect_switches()

        time.sleep(10)

        # Check that the flow keeps the original setting
        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert 'actions=output:"s1-eth3"' not in flows_s1
        assert 'in_port="s1-eth1' in flows_s1

    def test_040_replace_action_flow(self):
        self.replace_action_flow()

    def test_045_replace_action_flow_restarting(self):
        self.replace_action_flow(restart_kytos=True)

    def add_action_flow(self, restart_kytos=False):

        payload = {
            "flows": [
                {
                    "priority": 10,
                    "idle_timeout": 360,
                    "hard_timeout": 1200,
                    "match": {
                        "in_port": 1
                    },
                    "actions": [
                        {"action_type": "output", "port": 2}
                    ]
                }
            ]
        }

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})

        # wait for the flow to be installed
        time.sleep(10)

        # Verify the flow
        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert 'in_port="s1-eth1' in flows_s1

        s1.dpctl('add-flow', 'in_port=1,idle_timeout=360,hard_timeout=1200,priority=10,actions=strip_vlan,output:2')

        if restart_kytos:
            # restart controller keeping configuration
            self.net.start_controller(enable_all=True)
            self.net.wait_switches_connect()
        else:
            self.net.reconnect_switches()

        time.sleep(10)

        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert 'actions=strip_vlan,' not in flows_s1
        assert 'actions=output:"s1-eth2' in flows_s1

    def test_050_add_action_flow(self):
        self.add_action_flow()

    def test_055_add_action_flow_restarting(self):
        self.add_action_flow(restart_kytos=True)

    def flow_another_table(self, restart_kytos=False):
        """Tests if, after adding a flow in another
        table outside kytos, the flow is removed."""

        s1 = self.net.net.get('s1')
        s1.dpctl('add-flow', 'table=2,in_port=1,actions=output:2')
        if restart_kytos:
            # restart controller keeping configuration
            self.net.start_controller(enable_all=True)
            self.net.wait_switches_connect()
        else:
            self.net.reconnect_switches()

        time.sleep(10)

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, flows_s1

    def test_060_flow_another_table(self):
        self.flow_another_table()

    def test_065_flow_another_table_restarting(self):
        self.flow_another_table(restart_kytos=True)

    def flow_table_0(self, restart_kytos=False):
        """Tests if, after adding a flow in another
        table outside kytos, the flow is removed."""

        s1 = self.net.net.get('s1')
        s1.dpctl('add-flow', 'table=0,in_port=1,actions=output:2')

        if restart_kytos:
            # restart controller keeping configuration
            self.net.start_controller(enable_all=True)
            self.net.wait_switches_connect()
        else:
            self.net.reconnect_switches()

        time.sleep(10)

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, flows_s1

    def test_070_flow_table_0(self):
        self.flow_table_0()

    def test_075_flow_table_0_restarting(self):
        self.flow_table_0(restart_kytos=True)

    def test_080_retrieve_flows(self):
        api_url = KYTOS_API + '/flow_manager/v2/stored_flows'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 3
        assert "00:00:00:00:00:00:00:01" in data.keys()
        assert "00:00:00:00:00:00:00:02" in data.keys()
        assert "00:00:00:00:00:00:00:03" in data.keys()
        assert len(data["00:00:00:00:00:00:00:01"]) == BASIC_FLOWS
        assert len(data["00:00:00:00:00:00:00:02"]) == BASIC_FLOWS
        assert len(data["00:00:00:00:00:00:00:03"]) == BASIC_FLOWS

    def test_085_install_flows_by_switch_but_404(self):
        """Install flows_by_switch but one of the switches
         does not exist."""
        payload = {
            "00:00:00:00:00:00:00:01": {
                "flows": [{"cookie": 12297829382473034410, "priority": 20000}]},
            "00:00:00:00:00:00:00:11": {
                "flows": [{"cookie": 12297829382473034410, "priority": 20000},]},
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows_by_switch'
        response = requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 404, response.text

    def test_090_install_flows_in_switch_list_but_404(self):
        """Through /v2/flows install flows in a list of switches
         but one of them does not exist."""
        payload = {
            "switches": ["00:00:00:00:00:00:00:01", "00:00:00:00:00:00:00:11"],
            "flows": [{"cookie": 12297829382473034410, "priority": 20000}],
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows'
        response = requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 404, response.text
 
    def test_095_install_delete_flows_by_switch(self):
        """Install and delete via flows_by_switch API request."""
        payload = {
            "00:00:00:00:00:00:00:01": {
                "flows": [{"cookie": 12297829382473034410, "priority": 20000}]},
            "00:00:00:00:00:00:00:02": {
                "flows": [
                    {"cookie": 12297829382473034410, "priority": 20000},
                    {"cookie": 12297829382473034410, "priority": 2000}
                ]},
            "00:00:00:00:00:00:00:03": {
                "flows": [
                    {"cookie": 12297829382473034410, "priority": 20000},
                    {"cookie": 12297829382473034410, "priority": 2000},
                    {"cookie": 12297829382473034410, "priority": 200}
                ]},
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows_by_switch'
        response = requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        stored_url = KYTOS_API + '/flow_manager/v2/stored_flows'
        response = requests.get(stored_url)
        data_flows = response.json()
        sw1_flows = data_flows["00:00:00:00:00:00:00:01"]
        sw2_flows = data_flows["00:00:00:00:00:00:00:02"]
        sw3_flows = data_flows["00:00:00:00:00:00:00:03"]
        
        assert len(sw1_flows) == BASIC_FLOWS + 1, sw1_flows
        assert len(sw2_flows) == BASIC_FLOWS + 2, sw2_flows
        assert len(sw3_flows) == BASIC_FLOWS + 3, sw3_flows
        test_list = [(sw1_flows, 1), (sw2_flows, 2), (sw3_flows, 3)]
        for sw_flows, length in test_list:
            for flow in sw_flows[BASIC_FLOWS: BASIC_FLOWS + length]:
                assert flow["state"] in {"installed", "pending"}

        s1, s2, s3 = self.net.net.get('s1', 's2', 's3')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 2, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS + 3, flows_s3

        payload = {
            "00:00:00:00:00:00:00:03": {
                "flows": [{"cookie": 12297829382473034410, "cookie_mask": 18446744073709551615}]
            },
            "00:00:00:00:00:00:00:02": {
                "flows": [{"cookie": 12297829382473034410, "cookie_mask": 18446744073709551615}]
            },
            "00:00:00:00:00:00:00:01": {
                "flows": [{"cookie": 12297829382473034410, "cookie_mask": 18446744073709551615}]
            }
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows_by_switch'
        response = requests.delete(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        stored_url = KYTOS_API + '/flow_manager/v2/stored_flows'
        response = requests.get(stored_url)
        data_flows = response.json()
        sw1_flows = data_flows["00:00:00:00:00:00:00:01"]
        sw2_flows = data_flows["00:00:00:00:00:00:00:02"]
        sw3_flows = data_flows["00:00:00:00:00:00:00:03"]
        
        assert len(sw1_flows) == BASIC_FLOWS + 1, sw1_flows
        assert len(sw2_flows) == BASIC_FLOWS + 2, sw2_flows
        assert len(sw3_flows) == BASIC_FLOWS + 3, sw3_flows
        test_list = [(sw1_flows, 1), (sw2_flows, 2), (sw3_flows, 3)]
        for sw_flows, length in test_list:
            for flow in sw_flows[BASIC_FLOWS: BASIC_FLOWS + length]:
                assert flow["state"] == "deleted"

        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS, flows_s3

    def test_100_install_delete_flows_in_switch_list(self):
        """Install and delete through /v2/flows and a list of
         switches."""
        payload = {
            "switches": ["00:00:00:00:00:00:00:01", "00:00:00:00:00:00:00:02"],
            "flows": [
                    {"cookie": 12297829382473034410, "priority": 20000},
                    {"cookie": 12297829382473034410, "priority": 2000}
            ]
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows'
        response = requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        s1, s2, s3 = self.net.net.get('s1', 's2', 's3')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 2, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 2, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS, flows_s3

        payload = {
            "switches": ["00:00:00:00:00:00:00:01", "00:00:00:00:00:00:00:02"],
            "flows": [{"cookie": 12297829382473034410, "cookie_mask": 18446744073709551615}]
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows'
        response = requests.delete(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS, flows_s3

    def test_105_mismatch_miss_flow(self):
        """Install miss flow and try to delete it with a mismatched
        flow."""
        payload = {"00:00:00:00:00:00:00:01": {
            "flows": [{
                "priority": 0,
                "instructions": [{
                    "instruction_type": "goto_table",
                    "table_id": 2
                }]
            }]
        }}
        api_url = KYTOS_API + '/flow_manager/v2/flows_by_switch/?force=true'
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1

        payload = {"00:00:00:00:00:00:00:01": {
            "flows": [{
                "table_id": 0,
                "match": {"dl_src": "ee:ee:ee:ee:ee:01"}
            }],
        }}
        response = requests.delete(api_url, data=json.dumps(payload),
                                   headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        # Restart kytos
        self.net.start_controller(enable_all=True)
        self.net.wait_switches_connect()

        # Previously installed flow should not be deleted
        flows_s1 = s1.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1

    def test_110_install_delete_flows_by_switch_diff_cookie(self):
        """Install and delete via flows_by_switch API request.
         But with different cookies."""
        payload = {
            "00:00:00:00:00:00:00:01": {
                "flows": [{"cookie": 11111111111111111, "priority": 20000}]},
                                     
            "00:00:00:00:00:00:00:02": {
                "flows": [
                    {"cookie": 22222222222222222, "priority": 20000},
                    {"cookie": 22222222222222222, "priority": 2000}
                ]},
            "00:00:00:00:00:00:00:03": {
                "flows": [
                    {"cookie": 33333333333333333, "priority": 20000},
                    {"cookie": 33333333333333333, "priority": 2000},
                    {"cookie": 33333333333333333, "priority": 200}
                ]},
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows_by_switch'
        response = requests.post(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        stored_url = KYTOS_API + '/flow_manager/v2/stored_flows'
        response = requests.get(stored_url)
        data_flows = response.json()
        sw1_flows = data_flows["00:00:00:00:00:00:00:01"]
        sw2_flows = data_flows["00:00:00:00:00:00:00:02"]
        sw3_flows = data_flows["00:00:00:00:00:00:00:03"]
        
        assert len(sw1_flows) == BASIC_FLOWS + 1, sw1_flows
        assert len(sw2_flows) == BASIC_FLOWS + 2, sw2_flows
        assert len(sw3_flows) == BASIC_FLOWS + 3, sw3_flows
        test_list = [(sw1_flows, 1), (sw2_flows, 2), (sw3_flows, 3)]
        for sw_flows, length in test_list:
            for flow in sw_flows[BASIC_FLOWS: BASIC_FLOWS + length]:
                assert flow["state"] in {"installed", "pending"}

        s1, s2, s3 = self.net.net.get('s1', 's2', 's3')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 1, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 2, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS + 3, flows_s3

        payload = {
            "00:00:00:00:00:00:00:03": {
                "flows": [{"cookie": 33333333333333333, "cookie_mask": 18446744073709551615}]
            },
            "00:00:00:00:00:00:00:02": {
                "flows": [{"cookie": 22222222222222222, "cookie_mask": 18446744073709551615}]
            },
            "00:00:00:00:00:00:00:01": {
                "flows": [{"cookie": 11111111111111111, "cookie_mask": 18446744073709551615}]
            }
        }
        api_url = KYTOS_API + '/flow_manager/v2/flows_by_switch'
        response = requests.delete(api_url, data=json.dumps(payload),
                      headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        stored_url = KYTOS_API + '/flow_manager/v2/stored_flows'
        response = requests.get(stored_url)
        data_flows = response.json()
        sw1_flows = data_flows["00:00:00:00:00:00:00:01"]
        sw2_flows = data_flows["00:00:00:00:00:00:00:02"]
        sw3_flows = data_flows["00:00:00:00:00:00:00:03"]
        
        assert len(sw1_flows) == BASIC_FLOWS + 1, sw1_flows
        assert len(sw2_flows) == BASIC_FLOWS + 2, sw2_flows
        assert len(sw3_flows) == BASIC_FLOWS + 3, sw3_flows
        test_list = [(sw1_flows, 1), (sw2_flows, 2), (sw3_flows, 3)]
        for sw_flows, length in test_list:
            for flow in sw_flows[BASIC_FLOWS: BASIC_FLOWS + length]:
                assert flow["state"] == "deleted", flow

        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS, flows_s3
