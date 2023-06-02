import time

from tests.helpers import NetworkTest
import requests

CONTROLLER = '127.0.0.1'
KYTOS_API = f'http://{CONTROLLER}:8181/api/kytos'
OF_MULTI_TABLE_API = '/of_multi_table/v1/pipeline'

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
        """Test if there is any error with enabling and disabling pipeline"""
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
                        }]
                    },
                },
                {
                    "table_id": 1,
                    "description": "Second table for coloring",
                    "napps_table_groups": {
                        "coloring": ["base"]
                    },
                    "table_miss_flow": {
                        "priority": 0,
                        "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 2
                        }]
                    },
                },
                {
                    "table_id": 2,
                    "description": "Third table for of_lldp",
                    "napps_table_groups": {
                        "of_lldp": ["base"]
                    },
                    "table_miss_flow": {
                        "priority": 0,
                        "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 3
                        }]
                    },
                },
                {
                    "table_id": 3,
                    "description": "Fourth table for mef_eline evpl",
                    "napps_table_groups": {
                        "mef_eline": ["evpl"],
                    },
                    "table_miss_flow": {
                        "priority": 0,
                        "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 4
                        }]
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

        # Add circuit
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/"
        response = requests.post(api_url, json=evc)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        # Add pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline)
        data = response.json()
        assert response.status_code == 201, response.text
        assert 'id' in data

        # Enabled pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data['id']}/enable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text
        time.sleep(10)

        # Get pipeline dictionary
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data['id']}"
        response = requests.get(api_url)
        pipeline_data = response.json()
        assert response.status_code == 200, response.text
        assert pipeline_data["status"] == "enabled"

        # Assert installed flows
        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows').splitlines()
        assert len(flows_s1) == 9, flows_s1
        assert "table=0" in flows_s1[0]
        assert 'priority=0 actions=resubmit(,1)' in flows_s1[0] or \
               'priority=0 actions=goto_table:1' in flows_s1[0]
        assert "table=1" in flows_s1[1]
        assert 'actions=CONTROLLER:65535' in flows_s1[1]
        assert "table=1" in flows_s1[2]
        assert 'actions=CONTROLLER:65535' in flows_s1[2]
        assert "table=1" in flows_s1[3]
        assert 'priority=0 actions=resubmit(,2)' in flows_s1[3] or \
               'priority=0 actions=goto_table:2' in flows_s1[3]
        assert "table=2" in flows_s1[4]
        assert 'dl_type=0x88cc actions=CONTROLLER:65535' in flows_s1[4]
        assert "table=2" in flows_s1[5]
        assert 'priority=0 actions=resubmit(,3)' in flows_s1[5] or \
               'priority=0 actions=goto_table:3' in flows_s1[5]
        assert "table=3" in flows_s1[6]
        assert 'dl_vlan=100 actions=output:"s1-eth2"' in flows_s1[6]
        assert "table=3" in flows_s1[7]
        assert 'priority=0 actions=resubmit(,4)' in flows_s1[7] or \
               'priority=0 actions=goto_table:4' in flows_s1[7]
        assert "table=4" in flows_s1[8]
        assert 'actions=mod_vlan_vid:100,output:"s1-eth1"' in flows_s1[8]

        self.net.start_controller(clean_config=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        # Assert installed flows
        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows').splitlines()
        assert len(flows_s1) == 9, flows_s1
        assert "table=0" in flows_s1[0]
        assert 'priority=0 actions=resubmit(,1)' in flows_s1[0] or \
               'priority=0 actions=goto_table:1' in flows_s1[0]
        assert "table=1" in flows_s1[1]
        assert 'actions=CONTROLLER:65535' in flows_s1[1]
        assert "table=1" in flows_s1[2]
        assert 'actions=CONTROLLER:65535' in flows_s1[2]
        assert "table=1" in flows_s1[3]
        assert 'priority=0 actions=resubmit(,2)' in flows_s1[3] or \
               'priority=0 actions=goto_table:2' in flows_s1[3]
        assert "table=2" in flows_s1[4]
        assert 'dl_type=0x88cc actions=CONTROLLER:65535' in flows_s1[4]
        assert "table=2" in flows_s1[5]
        assert 'priority=0 actions=resubmit(,3)' in flows_s1[5] or \
               'priority=0 actions=goto_table:3' in flows_s1[5]
        assert "table=3" in flows_s1[6]
        assert 'dl_vlan=100 actions=output:"s1-eth2"' in flows_s1[6]
        assert "table=3" in flows_s1[7]
        assert 'priority=0 actions=resubmit(,4)' in flows_s1[7] or \
               'priority=0 actions=goto_table:4' in flows_s1[7]
        assert "table=4" in flows_s1[8]
        assert 'actions=mod_vlan_vid:100,output:"s1-eth1"' in flows_s1[8]

        # Return to default pipeline
        # Disabled pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data['id']}/disable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text
        time.sleep(10)

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows').splitlines()
        assert len(flows_s1) == 5, flows_s1
        for flow in flows_s1:
            assert 'table=0' in flow
        assert 'actions=CONTROLLER:65535' in flows_s1[0]
        assert 'actions=CONTROLLER:65535' in flows_s1[1]
        assert 'dl_vlan=100 actions=output:"s1-eth2"' in flows_s1[2]
        assert 'actions=mod_vlan_vid:100,output:"s1-eth1"' in flows_s1[3]
        assert 'dl_vlan=3799,dl_type=0x88cc actions=CONTROLLER:65535' in flows_s1[4]

        # Delete disabled pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data['id']}"
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

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
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline)
        data = response.json()
        assert response.status_code == 201, response.text
        assert 'id' in data

        # Enabled pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data['id']}/enable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text
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

    def test_015_mef_eline_pipelined(self):
        "Test if mef_eline flows can communicate through tables"
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
                        "match": {}
                    },
                },
                {
                    "table_id": 1,
                    "description": "Second table for coloring",
                    "napps_table_groups": {
                        "mef_eline": ["epl", "evpl"]
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
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": 1, "value": 100}
            }
        }

        # Add circuit
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/"
        response = requests.post(api_url, json=evc)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd(f'ip link add link {h11.intfNames()[0]} name vlan_ra type vlan id 100')
        h11.cmd('ip link set up vlan_ra')
        h11.cmd('ip addr add 100.0.0.11/24 dev vlan_ra')
        h2.cmd(f'ip link add link {h2.intfNames()[0]} name vlan_ra type vlan id 100')
        h2.cmd('ip link set up vlan_ra')
        h2.cmd('ip addr add 100.0.0.2/24 dev vlan_ra')
        result = h11.cmd('ping -c1 100.0.0.2')
        assert ', 0% packet loss,' in result

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows').splitlines()
        assert len(flows_s1) == 6, flows_s1
        for flow in flows_s1:   
            assert 'table=0' in flow

        # Add pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline)
        data = response.json()
        assert response.status_code == 201, response.text
        assert 'id' in data

        # Enabled pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data['id']}/enable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text
        time.sleep(10)

        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd(f'ip link add link {h11.intfNames()[0]} name vlan_ra type vlan id 100')
        h11.cmd('ip link set up vlan_ra')
        h11.cmd('ip addr add 100.0.0.11/24 dev vlan_ra')
        h2.cmd(f'ip link add link {h2.intfNames()[0]} name vlan_ra type vlan id 100')
        h2.cmd('ip link set up vlan_ra')
        h2.cmd('ip addr add 100.0.0.2/24 dev vlan_ra')
        result = h11.cmd('ping -c1 100.0.0.2')
        assert ', 0% packet loss,' in result

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows').splitlines()
        assert len(flows_s1) == 7, flows_s1
        assert 'table=1' in flows_s1[4]
        assert 'in_port="s1-eth1",dl_vlan=100' in flows_s1[4]
        assert 'table=1' in flows_s1[5]
        assert 'in_port="s1-eth3",dl_vlan=1 ' in flows_s1[5]
        assert 'table=1' in flows_s1[6]
        assert 'in_port="s1-eth4",dl_vlan=1 ' in flows_s1[6]

    def test_020_install_multiple_pipelines(test):
        """Test changing pipeline status"""
        pipeline1 = {
            "multi_table": [
                {
                    "table_id": 1,
                    "description": "Second table for coloring",
                    "napps_table_groups": {
                        "mef_eline": ["epl", "evpl"]
                    },
                },
            ]
        }
        pipeline2 = {
            "multi_table": [
                {
                    "table_id": 0,
                    "description": "Second table for coloring",
                    "napps_table_groups": {
                        "coloring": ["base"]
                    },
                },
            ]
        }

        pipeline3 = {
            "multi_table": [
                {
                    "table_id": 1,
                    "description": "Second table for coloring",
                    "napps_table_groups": {
                        "of_lldp": ["base"]
                    },
                },
            ]
        }
                
        # Add pipelines
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline1)
        data1 = response.json()
        assert response.status_code == 201, response.text
        assert 'id' in response.json()

        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline2)
        data2 = response.json()
        assert response.status_code == 201, response.text
        assert 'id' in response.json()

        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline3)
        data3 = response.json()
        assert response.status_code == 201, response.text
        assert 'id' in response.json()

        # Enabled pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data1['id']}/enable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text
        time.sleep(10)

        # Try to enable another pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data2['id']}/enable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text
        time.sleep(10)

        # Try to delete an enabled pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data2['id']}"
        response = requests.delete(api_url)
        assert response.status_code == 409, response.text

        # Try to disable an already disable pipeline
        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}/{data3['id']}/disable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.get(api_url)

        for pipeline in response.json()['pipelines']:
            if pipeline['id'] == data2['id']:
                assert pipeline['status'] == 'enabled'
                continue
            assert pipeline['status'] == 'disabled'

    def test_020_invalid_pipelines(test):
        """Test invalid pipelines"""
        # Invalid: Duplicated table id
        pipeline1 = {
            "multi_table": [
                {"table_id": 1},
                {"table_id": 1},
            ]
        }

        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline1)
        assert response.status_code == 400, response.text

        # Invalid: Duplicated table group from napp
        pipeline2 = {
            "multi_table": [
                {
                    "table_id": 1,
                    "napps_table_groups": {
                        "mef_eline": ["epl"]
                    },
                },
                {
                    "table_id": 0,
                    "napps_table_groups": {
                        "mef_eline": ["epl", "evpl"]
                    },
                },
            ]
        }

        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline2)
        assert response.status_code == 400, response.text

        # Invalid: goto_table value is lower than table_id
        pipeline3 = {
            "multi_table": [
                {
                    "table_id": 3,
                    "table_miss_flow": {
                        "priority": 0,
                        "instructions": [{
                            "instruction_type": "goto_table",
                            "table_id": 1
                        }]
                    },
                },
            ]
        }


        api_url = f"{KYTOS_API}{OF_MULTI_TABLE_API}"
        response = requests.post(api_url, json=pipeline3)
        assert response.status_code == 400, response.text
