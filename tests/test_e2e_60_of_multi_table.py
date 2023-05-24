import time

from tests.helpers import NetworkTest
import requests

CONTROLLER = '127.0.0.1'
KYTOS_API = f'http://{CONTROLLER}:8181/api/kytos'

BASIC_FLOWS = 3

class TestE2EOfMultiTable:

    def setup_method(self, method):
        """Called at the beginning of each class method"""
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def restart(self, _clean_config=False, _enable_all=True):
        self.net.start_controller(clean_config=_clean_config, enable_all=_enable_all)
        self.net.wait_switches_connect()

    def test_005_enable_pipeline(self):
        """Test if there is any error with enabling pipeline"""
        pipeline = {
            "multi_table": [
                {
                    "table_id": 0,
                    "description": "First table for miss flow entry",
                    "table_miss_flow": {
                        "priority": 0,
                        "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 1
                        }],
                        "match": {"dl_vlan": 101}
                    },
                },
                {
                    "table_id": 1,
                    "description": "Second table for coloring",
                    "napps_table_groups": {
                        "coloring": ["base"]
                    },
                },
                {
                    "table_id": 2,
                    "description": "Third table for of_lldp",
                    "napps_table_groups": {
                        "of_lldp": ["base"]
                    },
                },
                {
                    "table_id": 3,
                    "description": "Fourth table for mef_eline evpl",
                    "napps_table_groups": {
                        "mef_eline": ["evpl"],
                    },
                },
                {
                    "table_id": 4,
                    "description": "Fifth table for mef_eline epl",
                    "napps_table_groups": {
                        "mef_eline": ["epl"]
                    },
                },
            ]
        }
        evc = {
            "name": "evc01",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": 1, "value": 100}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:01:2"
            }
        }

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=evc)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        # Add pipeline
        api_url = KYTOS_API + '/of_multi_table/v1/pipeline'
        response = requests.post(api_url, json=pipeline)
        data = response.json()
        assert response.status_code == 201
        assert 'id' in data

        # Enabled pipeline
        api_url = KYTOS_API + '/of_multi_table/v1/pipeline/' + data['id'] + '/enable'
        response = requests.post(api_url)
        data = response.json()
        assert response.status_code == 200
        time.sleep(10)

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows').splitlines()
        for flow in flows_s1:
            if "table=0" in flow:
                assert 'actions=resubmit(,1)' in flow
            if "table=1" in flow:
                assert 'actions=CONTROLLER:65535' in flow
            if "table=2" in flow:
                assert 'dl_type=0x88cc' in flow
            if "table=3" in flow:
                assert 'dl_vlan=100 actions=output:"s1-eth2"' in flow
            if "table=4" in flow:
                assert 'actions=mod_vlan_vid:100,output:"s1-eth1"' in flow


    def test_010_delete_miss_flow(self):
        """Delete a miss flow so is recreated"""
        pipeline = {
            "multi_table": [
                {
                    "table_id": 0,
                    "description": "First table for miss flow entry",
                    "table_miss_flow": {
                        "priority": 0,
                        "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 1
                        }],
                        "match": {"dl_vlan": 101}
                    },
                },
                {
                    "table_id": 1,
                    "description": "Second table for coloring",
                    "napps_table_groups": {
                        "coloring": ["base"],
                        "of_lldp": ["base"],
                        "mef_eline": ["evpl", "epl"],
                    },
                },
            ]
        }

        # Add pipeline
        api_url = KYTOS_API + '/of_multi_table/v1/pipeline'
        response = requests.post(api_url, json=pipeline)
        data = response.json()
        assert response.status_code == 201
        assert 'id' in data

        # Enabled pipeline
        api_url = KYTOS_API + '/of_multi_table/v1/pipeline/' + data['id'] + '/enable'
        response = requests.post(api_url)
        data = response.json()
        assert response.status_code == 200
        time.sleep(10)

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')
        assert 'actions=resubmit(,1)' in flows_s1

        # Delete all flows from switch 1
        s1.dpctl('del-flows')
        self.net.reconnect_switches()

        time.sleep(10)

        flows_s1 = s1.dpctl('dump-flows')
        assert 'actions=resubmit(,1)' in flows_s1

