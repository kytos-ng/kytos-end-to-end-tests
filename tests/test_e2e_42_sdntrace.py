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
        cls.net = NetworkTest(CONTROLLER, topo_name='amlight_looped')
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
        self.net.wait_switches_connect()
        time.sleep(10)
        #circuit_id = self.create_evc(400)
        #time.sleep(10)
        #self.circuit = self.wait_until_evc_is_active(circuit_id)


    @staticmethod
    def create_evc(vlan_id, interface_a="00:00:00:00:00:00:00:01:15", interface_z="00:00:00:00:00:00:00:06:22"):
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

    def test_trace_goto_table_intra(cls):
        """Test trace rest call."""
        circuit_id = cls.create_evc(201, interface_a="00:00:00:00:00:00:00:01:15", interface_z="00:00:00:00:00:00:00:01:16")
        time.sleep(10)

        # Add flows in S1
        payload_stored_flow = {
            "flows": [
                {
                    "match": {
                        "in_port": 15,
                        "dl_vlan": 201
                        },
                    "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20100,
                },
                {
                    "match": {
                        "in_port": 16,
                        "dl_vlan": 200
                        },
                    "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20100,
                },
                {
                    "match": {
                        "in_port": 15,
                        "dl_vlan": 201
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "output",
                            "port": 19
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 16,
                        "dl_vlan": 200
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "output",
                            "port": 17
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 20,
                        "dl_vlan": 201
                        },
                    "instructions": [
                        {
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 20,
                        "dl_vlan": 201
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "set_vlan",
                            "vlan_id": 200
                        }, {
                            "action_type": "output",
                            "port": 16
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 18,
                        "dl_vlan": 200
                        },
                    "instructions": [
                        {
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 18,
                        "dl_vlan": 200
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "set_vlan",
                            "vlan_id": 201
                        }, {
                            "action_type": "output",
                            "port": 15
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                }
            ]
        }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        payload = [
            {"trace": {
                "switch": {
                    "dpid": "00:00:00:00:00:00:00:01",
                    "in_port": 15
                    },
                "eth": {"dl_vlan": 201}
                }},
            {"trace": {
                "switch": {
                    "dpid": "00:00:00:00:00:00:00:01",
                    "in_port": 16
                    },
                "eth": {"dl_vlan": 200}
                }}
        ]
                
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"]

        result = list_results[0]

        assert result[0]['dpid'] == '00:00:00:00:00:00:00:01'
        assert result[0]['port'] == 15
        assert result[0]['vlan'] == 201

        assert result[1]['port'] == 20
        assert result[0]['vlan'] == 201
        assert result[1]['out']['port'] == 16
        assert result[1]['out']['vlan'] == 200

        result = list_results[1]

        assert result[0]['dpid'] == '00:00:00:00:00:00:00:01'
        assert result[0]['port'] == 16
        assert result[0]['vlan'] == 200

        assert result[1]['port'] == 18
        assert result[0]['vlan'] == 200
        assert result[1]['out']['port'] == 15
        assert result[1]['out']['vlan'] == 201

    def test_trace_goto_table_inter(cls):
        """Test trace rest call."""
        circuit_id = cls.create_evc(100)
        time.sleep(10)

        # Add flows in S1
        payload_stored_flow = {
            "flows": [
                {
                    "match": {
                        "in_port": 15,
                        "dl_vlan": 100
                        },
                    "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20100,
                },
                {
                    "match": {
                        "in_port": 15,
                        "dl_vlan": 100
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "set_vlan",
                            "vlan_id": 100
                        }, {
                            "action_type": "push_vlan",
                            "tag_type": "s"
                        }, {
                            "action_type": "set_vlan",
                            "vlan_id": 1
                        }, {
                            "action_type": "output",
                            "port": 11
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 11,
                        "dl_vlan": 1
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "output",
                            "port": 17
                        }]}
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20100,
                },
                {
                    "match": {
                        "in_port": 18,
                        "dl_vlan": 1
                        },
                    "instructions": [
                        {
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 18,
                        "dl_vlan": 1
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "pop_vlan"
                        }, {
                            "action_type": "output",
                            "port": 15
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                }
            ]
        }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        # Add flows in S6
        payload_stored_flow = {
            "flows": [
                {
                    "match": {
                        "in_port": 22,
                        "dl_vlan": 100
                        },
                    "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20100,
                },
                {
                    "match": {
                        "in_port": 11,
                        "dl_vlan": 1
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "output",
                            "port": 25
                        }]}
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20100,
                },
                {
                    "match": {
                        "in_port": 22,
                        "dl_vlan": 100
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "set_vlan",
                            "vlan_id": 100
                        }, {
                            "action_type": "push_vlan",
                            "tag_type": "s"
                        }, {
                            "action_type": "set_vlan",
                            "vlan_id": 1
                        }, {
                            "action_type": "output",
                            "port": 11
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 26,
                        "dl_vlan": 1
                        },
                    "instructions": [
                        {
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }
                    ],
                    "table_id": 0,
                    "table_group": "evpl",
                    "priority": 20000,
                },
                {
                    "match": {
                        "in_port": 26,
                        "dl_vlan": 1
                        },
                    "instructions": [{
                        "instruction_type": "apply_actions",
                        "actions": [{
                            "action_type": "pop_vlan"
                        }, {
                            "action_type": "output",
                            "port": 22
                        }]}
                    ],
                    "table_id": 2,
                    "table_group": "base",
                    "priority": 20000,
                }
            ]
        }
        api_url = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:06'
        response = requests.post(api_url, json = payload_stored_flow)
        assert response.status_code == 202, response.text
        time.sleep(10)

        payload = [
            {"trace": {
                "switch": {
                    "dpid": "00:00:00:00:00:00:00:01",
                    "in_port": 15
                    },
                "eth": {"dl_vlan": 100}
                }},
            {"trace": {
                "switch": {
                    "dpid": "00:00:00:00:00:00:00:06",
                    "in_port": 22
                    },
                "eth": {"dl_vlan": 100}
                }}
        ]
                
        api_url = KYTOS_API + '/amlight/sdntrace_cp/v1/traces'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        list_results = data["result"]

        result = list_results[0]

        assert result[0]['dpid'] == '00:00:00:00:00:00:00:01'
        assert result[0]['port'] == 15
        assert result[0]['vlan'] == 100

        assert result[1]['dpid'] == '00:00:00:00:00:00:00:06'
        assert result[1]['port'] == 11
        assert result[1]['vlan'] == 1

        assert result[2]['dpid'] == '00:00:00:00:00:00:00:06'
        assert result[2]['port'] == 26
        assert result[2]['vlan'] == 1
        assert result[2]['out']['port'] == 22
        assert result[2]['out']['vlan'] == 100

        result = list_results[1]

        assert result[0]['dpid'] == '00:00:00:00:00:00:00:06'
        assert result[0]['port'] == 22
        assert result[0]['vlan'] == 100

        assert result[1]['dpid'] == '00:00:00:00:00:00:00:01'
        assert result[1]['port'] == 11
        assert result[1]['vlan'] == 1

        assert result[2]['dpid'] == '00:00:00:00:00:00:00:01'
        assert result[2]['port'] == 18
        assert result[2]['vlan'] == 1
        assert result[2]['out']['port'] == 15
        assert result[2]['out']['vlan'] == 100