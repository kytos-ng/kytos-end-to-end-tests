import requests
from tests.helpers import NetworkTest
import time

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api' % CONTROLLER


class TestE2ESDNTrace:
    net = None
    circuit = None

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name='linear10')
        cls.net.start()
        cls.net.restart_kytos_clean()
        cls.net.wait_switches_connect()

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def setup_method(self, method):
        """
        It is called at the beginning of each method execution
        """
        self.net.start_controller(clean_config=True, enable_all=True)
        time.sleep(10)
        circuit_id = self.create_evc(400)
        time.sleep(10)
        self.circuit = self.wait_until_evc_is_active(circuit_id)


    @staticmethod
    def create_evc(vlan_id, interface_a="00:00:00:00:00:00:00:01:1", interface_z="00:00:00:00:00:00:00:0a:1"):
        payload = {
            "name": "Vlan_%s" % vlan_id,
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": interface_a,
                "tag": {"tag_type": 1, "value": vlan_id}
            },
            "uni_z": {
                "interface_id": interface_z,
                "tag": {"tag_type": 1, "value": vlan_id}
            }
        }
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        return data['circuit_id']

    @staticmethod
    def get_evc(circuit_id):
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/'
        response = requests.get(api_url+circuit_id)
        assert response.status_code == 200, response.text
        data = response.json()
        return data

    @classmethod
    def wait_until_evc_is_active(
        cls, evc_id: str, wait_secs=6, i=0, max_i=20
    ) -> dict:
        """Wait until evc is active."""
        evc = cls.get_evc(evc_id)
        if evc["active"]:
            return evc
        time.sleep(wait_secs)
        if i < max_i:
            return cls.wait_until_evc_is_active(evc_id, wait_secs, i + 1, max_i)
        else:
            raise ValueError(f"TimeoutError: {evc_id} didn't get active. {evc}")

    def test_001_run_sdntrace_cp(self):
        """Run SDNTrace-CP (Control Plane)."""
        # Trace from UNI_A
        payload = {
            "trace": {
                "switch": {"dpid": "00:00:00:00:00:00:00:01", "in_port": 1},
                "eth": {"dl_type": 33024, "dl_vlan": 400}
            }
        }
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/trace'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "result" in data, data
        assert len(data["result"]) == 10, data

        expected = [
            (
                l['endpoint_b']['switch'],
                l['endpoint_b']['port_number'],
                l['metadata']['s_vlan']['value']
            )
            for l in self.circuit['current_path']
        ]
        expected.insert(0, ('00:00:00:00:00:00:00:01', 1, 400))

        actual = [
            (step['dpid'], step['port'], step['vlan'])
            for step in data["result"]
        ]

        assert expected == actual, f"Expected {expected}. Actual: {actual}"

        # Trace from UNI_Z
        payload = {
            "trace": {
                "switch": {"dpid": "00:00:00:00:00:00:00:0a", "in_port": 1},
                "eth": {"dl_type": 33024, "dl_vlan": 400}
            }
        }
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/trace'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "result" in data
        assert len(data["result"]) == 10, data

        expected = [
            (
                l['endpoint_a']['switch'],
                l['endpoint_a']['port_number'],
                l['metadata']['s_vlan']['value']
            )
            for l in reversed(self.circuit['current_path'])
        ]
        expected.insert(0, ('00:00:00:00:00:00:00:0a', 1, 400))

        actual = [
            (step['dpid'], step['port'], step['vlan'])
            for step in data["result"]
        ]

        assert expected == actual, f"Expected {expected}. Actual: {actual}"

    def wait_sdntrace_result(self, trace_id, timeout=10):
        """Wait until sdntrace finishes."""
        wait_count = 0
        while wait_count < timeout:
            try:
                api_url = KYTOS_API + '/amlight/sdntrace/trace'
                response = requests.get(f"{api_url}/{trace_id}")
                data = response.json()
                assert data["result"][-1]["reason"] == "done"
                break
            except:
                time.sleep(1)
                wait_count += 1
        else:
            msg = 'Timeout while waiting from sdntrace result.'
            raise Exception(msg)
        return data["result"]

    def test_010_run_sdntrace(self):
        """Run SDNTrace (Data Plane trace)."""
        # Trace from UNI_A
        payload = {
            "trace": {
                "switch": {
                    "dpid": "00:00:00:00:00:00:00:01",
                    "in_port": 1
                },
                "eth": {
                    "dl_vlan": 400,
                    "dl_vlan_pcp": 4,
                    "dl_type": 2048
                },
                "ip": {
                    "nw_src": "0.0.0.1",
                    "nw_dst": "0.0.0.2",
                    "nw_tos": 5,
                    "nw_proto": 17
                },
                "tp": {
                    "tp_src": 33948,
                    "tp_dst": 53
                }
            }
        }

        api_url = KYTOS_API + '/amlight/sdntrace/trace'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "result" in data, data
        assert "trace_id" in data["result"], data
        result = self.wait_sdntrace_result(data["result"]["trace_id"])

        expected = [
            (
                l['endpoint_b']['switch'],
                l['endpoint_b']['port_number'],
            )
            for l in self.circuit['current_path']
        ]

        actual = [
            (step['dpid'], step['port']) for step in result[1:-1]
        ]

        assert expected == actual, f"Expected {expected}. Actual: {actual}"

        # Trace from UNI_Z
        payload = {
            "trace": {
                "switch": {
                    "dpid": "00:00:00:00:00:00:00:0a",
                    "in_port": 1
                },
                "eth": {
                    "dl_vlan": 400,
                    "dl_vlan_pcp": 4,
                    "dl_type": 2048
                },
                "ip": {
                    "nw_src": "0.0.0.1",
                    "nw_dst": "0.0.0.2",
                    "nw_tos": 5,
                    "nw_proto": 17
                },
                "tp": {
                    "tp_src": 33948,
                    "tp_dst": 53
                }
            }
        }

        api_url = KYTOS_API + '/amlight/sdntrace/trace'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "result" in data, data
        assert "trace_id" in data["result"], data
        result = self.wait_sdntrace_result(data["result"]["trace_id"])

        expected = [
            (
                l['endpoint_a']['switch'],
                l['endpoint_a']['port_number'],
            )
            for l in reversed(self.circuit['current_path'])
        ]

        actual = [
            (step['dpid'], step['port']) for step in result[1:-1]
        ]

        assert expected == actual, f"Expected {expected}. Actual: {actual}"

    def test_020_run_sdntrace_fail_missing_flow(self):
        """Run SDNTrace-CP with a failure due to missing flows:
        - delete flow from intermediate switch
        - make sure sdntrace_cp detects the failure
        - make sure sdntrace detects the failure
        - redeploy evc and make sure sdntrace / sdntrace_cp works
        """
        # 1. delete flow
        delete_flow = {
            "flows": [
                {
                    'cookie': int("0xaa%s" % self.circuit['id'], 16),
                    'cookie_mask': 0xffffffffffffffff,
                }
            ]
        }

        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:05'
        response = requests.delete(api_url, json=delete_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        # 2. sdntrace control plane - Trace from UNI_A
        payload_1 = {
            "trace": {
                "switch": {"dpid": "00:00:00:00:00:00:00:01", "in_port": 1},
                "eth": {"dl_type": 33024, "dl_vlan": 400}
            }
        }
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/trace'
        response = requests.put(api_url, json=payload_1)
        data = response.json()
        # only 4 steps are expected: starting, 1->2, 2->3, 3->4
        assert len(data["result"]) == 4, str(data)

        full_path = [
            (
                l['endpoint_b']['switch'],
                l['endpoint_b']['port_number'],
                l['metadata']['s_vlan']['value']
            )
            for l in self.circuit['current_path']
        ]

        actual = [
            (step['dpid'], step['port'], step['vlan'])
            for step in data["result"][1:]
        ]

        assert full_path != actual, f"Full path {full_path}. Actual: {actual}"
        assert full_path[:3] == actual, f"Expected {full_path[:3]}. Actual: {actual}"

        # 3. sdntrace data plane - Trace from UNI_A
        payload_2 = {
            "trace": {
                "switch": {
                    "dpid": "00:00:00:00:00:00:00:01",
                    "in_port": 1
                },
                "eth": {
                    "dl_vlan": 400,
                    "dl_vlan_pcp": 4,
                    "dl_type": 2048
                },
                "ip": {
                    "nw_src": "0.0.0.1",
                    "nw_dst": "0.0.0.2",
                    "nw_tos": 5,
                    "nw_proto": 17
                },
                "tp": {
                    "tp_src": 33948,
                    "tp_dst": 53
                }
            }
        }

        api_url = KYTOS_API + '/amlight/sdntrace/trace'
        response = requests.put(api_url, json=payload_2)
        assert response.status_code == 200, response.text
        data = response.json()
        result = self.wait_sdntrace_result(data["result"]["trace_id"])

        full_path = [
            (
                l['endpoint_b']['switch'],
                l['endpoint_b']['port_number'],
            )
            for l in self.circuit['current_path']
        ]

        actual = [
            (step['dpid'], step['port']) for step in result[1:-1]
        ]

        assert full_path != actual, f"Full path {full_path}. Actual: {actual}"
        assert full_path[:4] == actual, f"Expected {full_path[:4]}. Actual: {actual}"

        # 4. redeploy evc and check again
        circuit_id = self.circuit['id']
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc'
        response = requests.patch(f"{api_url}/{circuit_id}/redeploy")
        assert response.status_code == 202, response.text
        time.sleep(10)
        self.circuit = self.wait_until_evc_is_active(circuit_id)

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/trace'
        response = requests.put(api_url, json=payload_1)
        data = response.json()
        assert len(data["result"]) == 10, data
        expected = [
            (
                l['endpoint_b']['switch'],
                l['endpoint_b']['port_number'],
                l['metadata']['s_vlan']['value']
            )
            for l in self.circuit['current_path']
        ]
        actual = [
            (step['dpid'], step['port'], step['vlan'])
            for step in data["result"][1:]
        ]
        assert expected == actual, f"Expected {expected}. Actual: {actual}"

        api_url = KYTOS_API + '/amlight/sdntrace/trace'
        response = requests.put(api_url, json=payload_2)
        assert response.status_code == 200, response.text
        data = response.json()
        result = self.wait_sdntrace_result(data["result"]["trace_id"])
        expected = [
            (
                l['endpoint_b']['switch'],
                l['endpoint_b']['port_number'],
            )
            for l in self.circuit['current_path']
        ]
        actual = [
            (step['dpid'], step['port']) for step in result[1:-1]
        ]
        assert expected == actual, f"Expected {expected}. Actual: {actual}"

    def test_030_run_sdntrace_for_stored_flows(cls):
        """Run SDNTrace to get traces from flow_manager stored_flow"""
        cls.create_evc(100, "00:00:00:00:00:00:00:01:1", "00:00:00:00:00:00:00:0a:1")
        cls.create_evc(101, "00:00:00:00:00:00:00:03:2", "00:00:00:00:00:00:00:0a:1")
        cls.create_evc(102, "00:00:00:00:00:00:00:01:1", "00:00:00:00:00:00:00:0a:1")
        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:02",
                                "in_port": 1
                            },
                            "eth": {
                                "dl_vlan": 100
                            }
                        }
                    },
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:01",
                                "in_port": 1
                            },
                            "eth": {
                                "dl_vlan": 100
                            }
                        }
                    },
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:0a",
                                "in_port": 1
                            }
                        }
                    },
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:03",
                                "in_port": 2
                            },
                            "eth": {
                                "dl_vlan": 101
                            }
                        }
                    }
                ]
                
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"] 
        assert len(list_results) == 4
        assert len(list_results[0]) == 0

        assert len(list_results[1]) == 10
        assert list_results[1][0]["dpid"] == "00:00:00:00:00:00:00:01"
        assert list_results[1][0]["port"] == 1
        assert list_results[1][-1]["type"] == "last"
        assert list_results[1][-1]["out"] == {'port': 1, 'vlan': 100}

        assert len(list_results[2]) == 0

        assert len(list_results[3]) == 8
        assert list_results[3][0]["dpid"] == "00:00:00:00:00:00:00:03"
        assert list_results[3][0]["port"] == 2
        assert list_results[3][-1]["type"] == "last"
        assert list_results[3][-1]["out"] == {'port': 1, 'vlan': 101}

    def test_040_run_sdntrace_no_action(cls):
        """Run SDNTrace to get traces from flow_manager stored_flow"""
        # Topo: linear(10): s1-eth2:s2-eth2,s2-eth3:s3-eth2,s3-eth3:s4-eth2,...
        # Add a flow in S1: in_port = 2, out_port = 2
        payload_stored_flow = {
            "flows": [
                {
                    "match": {
                        "in_port": 2,
                        "dl_vlan": 100
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
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)
       # Add a flow in S2: in_port = 2, out_port = 3
        payload_stored_flow = {
            "flows": [
                {
                    "match": {
                        "in_port": 2,
                        "dl_vlan": 100
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 3
                        }
                    ]
                }
            ]
        }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:02'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)
        # Add a flow in S3: in_port = 2, "no action"
        payload_stored_flow = {
                "flows": [
                {
                    "match": {
                        "in_port": 2,
                        "dl_vlan": 100
                    }
                }
                ]
            }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:03'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:01",
                                "in_port": 2,
                            },
                            "eth": {
                                "dl_vlan": 100
                            }
                        }
                    }
                ]
                
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"] 
        assert len(list_results[0]) == 2

    def test_050_run_sdntrace_loop(cls):
        """Run SDNTrace to verify loop type"""
        # Topo: linear(10): s1-eth2:s2-eth2,s2-eth3:s3-eth2,s3-eth3:s4-eth2,...
        # Add a flow in S1: in_port = 2, out_port = 2
        payload_stored_flow = {
            "flows": [
                {
                    "match": {
                        "in_port": 2,
                        "dl_vlan": 100
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
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)
        # Add a flow in S2: 
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:02'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:01",
                                "in_port": 2,
                            },
                            "eth": {
                                "dl_vlan": 100
                            }
                        }
                    }
                ]
                
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"] 
        assert len(list_results[0]) == 2
        assert list_results[0][-1]['type'] == 'loop'

    def test_055_run_sdntrace_loop(cls):
        """Run SDNTrace to verify loop type"""
        cls.create_evc(5, "00:00:00:00:00:00:00:01:1", "00:00:00:00:00:00:00:03:1")

        # len(trace_result) -> 1
        payload_stored_flow = {
            "flows": [
                {
                    "priority": 55000,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 10
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 1,
                        }, 
                    ]
                }
            ]
        }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)
        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:01",
                                "in_port": 1,
                            },
                            "eth": {
                                "dl_vlan": 10
                            }
                        }
                    }
                ]

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"] 
        assert len(list_results[0]) == 1
        assert list_results[0][0]['type'] == 'loop'

        # len(trace_result) > 1

        payload_stored_flow = {
            "flows": [
                {
                    "priority": 55000,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 10
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 2,
                        }, {"action_type": "set_vlan", "vlan_id": 5 }
                    ]
                },
                {
                    "priority": 55000,
                    "match": {
                        "in_port": 2,
                        "dl_vlan": 5
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 1,
                        }
                    ]
                }
            ]
        }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        # Add a flow in S2: 
        payload_stored_flow = {
            "flows": [
                {
                    "priority": 55000,
                    "match": {
                        "in_port": 2,
                        "dl_vlan": 5
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 3
                        }
                    ]
                },
                {
                    "priority": 55000,
                    "match": {
                        "in_port": 2,
                        "dl_vlan": 5
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
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:02'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"] 
        assert len(list_results[0]) > 1
        assert list_results[0][-1]['type'] == 'loop'

    def test_060_run_sdntrace_order(cls):
        """Run SDNTrace to verify the order in the matching algorithm"""
        payload_stored_flow = {
            "flows": [
                {
                    "table_id": 0,
                    "cookie": 100,
                    "priority": 101,
                    "match": {
                        "in_port": 1,
                        "dl_vlan": 105
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 1
                        }
                    ]
                },
                {   
                    "table_id": 0,
                    "cookie": 101,
                    "priority": 100,
                    "match": {
                        "in_port": 1
                    },
                    "actions": [
                        {
                            "action_type": "output",
                            "port": 3
                        }
                    ]
                }
            ]
        }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:01",
                                "in_port": 1,
                            },
                            "eth": {
                                "dl_vlan": 105
                            }
                        }
                    }
                ]
                
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"]
        assert list_results[0][0]["out"]["port"] == 1

    def test_070_run_sdntrace_untagged_vlan(cls):
        """Run sdntrace_cp and sdntrace when vlan is untagged in evc"""

        cls.create_evc("untagged", interface_a="00:00:00:00:00:00:00:02:1", interface_z="00:00:00:00:00:00:00:03:1")        
        time.sleep(10)

        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:02",
                                "in_port": 1
                            }
                        }
                    }
        ]

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"]

        assert len(list_results[0]) == 2
        assert list_results[0][0]["dpid"] == "00:00:00:00:00:00:00:02"
        assert list_results[0][0]["port"] == 1
        assert list_results[0][-1]["type"] == "last"

        api_url = KYTOS_API + '/amlight/sdntrace/trace'
        response = requests.put(api_url, json=payload[0])
        assert response.status_code == 200, response.text
        data = response.json()
        assert "result" in data, data
        assert "trace_id" in data["result"], data
        result = cls.wait_sdntrace_result(data["result"]["trace_id"])

        assert len(result) == 3, result
        assert result[0]["dpid"] == "00:00:00:00:00:00:00:02"
        assert result[0]["port"] == 1
        assert result[0]["type"] == "starting"
        assert result[1]["dpid"] == "00:00:00:00:00:00:00:03"

    def test_075_run_sdntrace_any_vlan(cls):
        """Run sdntrace_cp and sdntrace when vlan is any in evc"""

        cls.create_evc("any", interface_a="00:00:00:00:00:00:00:02:1", interface_z="00:00:00:00:00:00:00:03:1")
        time.sleep(10)

        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:02",
                                "in_port": 1
                            },
                            "eth": {
                                "dl_vlan": 1
                            }
                        }
                    }
                ]

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"]

        assert list_results[0][0]["dpid"] == "00:00:00:00:00:00:00:02"
        assert list_results[0][0]["port"] == 1
        assert list_results[0][-1]["type"] == "last"

        api_url = KYTOS_API + '/amlight/sdntrace/trace'
        response = requests.put(api_url, json=payload[0])
        assert response.status_code == 200, response.text
        data = response.json()
        assert "result" in data, data
        assert "trace_id" in data["result"], data
        result = cls.wait_sdntrace_result(data["result"]["trace_id"])

        assert len(result) == 3, result
        assert result[0]["dpid"] == "00:00:00:00:00:00:00:02"
        assert result[0]["port"] == 1
        assert result[0]["type"] == "starting"
        assert result[1]["dpid"] == "00:00:00:00:00:00:00:03"

    def test_080_validate_attribute_on_payload(self):
        "Validate parameters"

        # Mandatory parameter missing (in_port):
        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:01"
                            },
                            "eth": {
                                "dl_vlan": 10
                            }
                        }
                    }               ]

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 400, response.text

        # Wrong data type (dpid should be string):
        payload[0]['trace']['switch']['in_port'] = 3
        payload[0]['trace']['switch']['dpid'] = 1

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 400, response.text

        # Wrong dl_vlan (should be integer):
        payload[0]['trace']['switch']['dpid'] = "00:00:00:00:00:00:00:01"
        payload[0]['trace']['eth']['dl_vlan'] = "10"

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 400, response.text

        # dl_vlan out of range (should be in [1, 4095]):
        payload[0]['trace']['eth']['dl_vlan'] = 4096

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 400, response.text
 
        # Wrong dl_type (should be integer):
        payload[0]['trace']['eth']['dl_vlan'] = 10
        payload[0]['trace']['eth']['dl_type'] = "1"

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 400, response.text
 
        # Valid request:
        payload[0]['trace']['eth']['dl_type'] = 1

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200

    def test_085_test_evcs_terminating_on_nnis(cls):
        "Test EVCs terminating on NNIs"

        cid1 = cls.create_evc(999, "00:00:00:00:00:00:00:02:1", "00:00:00:00:00:00:00:04:1")
        
        payload = {
                "name": "pw_s3",
                "dynamic_backup_path": True,
                "uni_a": {
                    "interface_id": "00:00:00:00:00:00:00:03:2"
                },
                "uni_z": {
                    "interface_id": "00:00:00:00:00:00:00:03:3"
                }
            }
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        cid2 = response.json()['circuit_id']
        
        payload = [
                    {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:03",
                                "in_port": 2
                            }
                        }
                    }, {
                        "trace": {
                            "switch": {
                                "dpid": "00:00:00:00:00:00:00:02",
                                "in_port": 1
                            },
                            "eth": {"dl_vlan": 999}
                        }
                    }               
                ]

        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        results = response.json()['result']
        data = results[0][0]
        assert data['type'] == 'last'
        assert data['dpid'] == '00:00:00:00:00:00:00:03'
        assert data['port'] == 2
        assert data['out']['port'] == 3

        data = results[1]
        assert data[0]['dpid'] == '00:00:00:00:00:00:00:02'
        assert data[0]['port'] == 1
        assert data[0]['type'] == 'starting'
        assert data[0]['vlan'] == 999
        assert data[-1]['dpid'] == '00:00:00:00:00:00:00:04'
        assert data[-1]['out']['port'] == 1
        assert data[-1]['type'] == 'last'
        assert data[-1]['out']['vlan'] == 999

