import json
import re
import time
from datetime import datetime, timedelta

import pytest
from random import randrange
import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

TIME_FMT = "%Y-%m-%dT%H:%M:%S+0000"

RE_I=re.IGNORECASE

# BasicFlows
# Each should have at least 3 flows, considering topology 'ring':
# - 01 for LLDP
# - 02 for amlight/coloring (node degree - number of neighbors)
BASIC_FLOWS = 3

class TestE2EMefEline:
    net = None
    evcs = {}

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Since some tests may set a link to down state, we should reset
        # the link state to up (for all links)
        self.net.config_all_links_up()
        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.restart_kytos_clean()
        cls.net.wait_switches_connect()
        time.sleep(5)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def restart(self, _clean_config=False, _enable_all=True):
        # Start the controller setting an environment in which the setting is
        # preserved (persistence) and avoid the default enabling of all elements
        self.net.start_controller(clean_config=_clean_config, enable_all=_enable_all)
        self.net.wait_switches_connect()

        # Wait a few seconds to kytos execute LLDP
        time.sleep(10)

    def create_evc(self, vlan_id, store=False):
        payload = {
            "name": "Vlan_%s" % vlan_id,
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": vlan_id}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": "vlan", "value": vlan_id}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        if store:
            self.evcs[vlan_id] = data['circuit_id']
        return data['circuit_id']

    def test_010_list_evcs_should_be_empty(self):
        """Test if list circuits return 'no circuit stored.'."""
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        assert response.json() == {}

    def test_015_create_evc_intra_switch(self):
        """ create an intra-switch EVC e-line with VLAN tag
        (UNIs in the same switch) """
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 101
                }
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:01:2",
                "tag": {
                    "tag_type": "vlan",
                    "value": 101
                }
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        s1 = self.net.net.get('s1')
        flow_s1 = s1.dpctl('dump-flows')
        #Make sure that the flows have EVPL default values
        assert 'priority=20000' in flow_s1

        h11, h12 = self.net.net.get('h11', 'h12')
        h11.cmd('ip link add link %s name vlan101 type vlan id 101' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan101')
        h11.cmd('ip addr add 10.1.1.11/24 dev vlan101')
        h12.cmd('ip link add link %s name vlan101 type vlan id 101' % (h12.intfNames()[0]))
        h12.cmd('ip link set up vlan101')
        h12.cmd('ip addr add 10.1.1.12/24 dev vlan101')

        result = h11.cmd('ping -c1 10.1.1.12')
        assert ', 0% packet loss,' in result

        s1 = self.net.net.get('s1')
        flows_s1 = s1.dpctl('dump-flows')

        # Each switch must have BASIC_FLOWS + 02 for the EVC (ingress + egress)
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 2, flows_s1

        # TODO: make sure it should be dl_vlan instead of vlan_vid
        assert 'dl_vlan=101' in flows_s1

        # clean up
        h11.cmd('ip link del vlan101')
        h12.cmd('ip link del vlan101')
        self.net.restart_kytos_clean()

    def test_020_create_evc_inter_switch(self):
        payload = {
            "name": "my evc1",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 15
                }
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {
                    "tag_type": 1,
                    "value": 15
                }
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        # search for the cookie, should have three flows:
        #  - 2 for the current path
        #  - 1 for the failover path
        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')

        #Make sure that both flow have EVPL default values
        assert 'priority=20000' in flows_s1
        assert 'priority=20000' in flows_s2

        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 3, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 3, flows_s2

        # make sure it should be dl_vlan instead of vlan_vid
        assert 'dl_vlan=15' in flows_s1
        assert 'dl_vlan=15' in flows_s2

        # Make the final and most important test: connectivity
        # 1. create the vlans and setup the ip addresses
        # 2. try to ping each other
        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan15 type vlan id 15' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan15')
        h11.cmd('ip addr add 15.0.0.11/24 dev vlan15')
        h2.cmd('ip link add link %s name vlan15 type vlan id 15' % (h2.intfNames()[0]))
        h2.cmd('ip link set up vlan15')
        h2.cmd('ip addr add 15.0.0.2/24 dev vlan15')
        result = h11.cmd('ping -c1 15.0.0.2')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip link del vlan15')
        h2.cmd('ip link del vlan15')
        self.net.restart_kytos_clean()

    def test_025_create_evc_different_tags_each_side(self):
        payload = {
            "name": "Vlan102_103_Test",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": 1, "value": 102}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": "vlan", "value": 103}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(15)

        # Each switch must have BASIC_FLOWS + 03 for the EVC:
        #  - 2 for current path (ingress + egress)
        #  - 1 for failover path
        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 3, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 3, flows_s2

        #Make sure that both flow have EVPL default values
        assert 'priority=20000' in flows_s1
        assert 'priority=20000' in flows_s2

        # make sure it should be dl_vlan instead of vlan_vid
        assert 'in_port="s1-eth1",dl_vlan=102' in flows_s1
        assert 'in_port="s2-eth1",dl_vlan=103' in flows_s2

        # Make the final and most important test: connectivity
        # 1. create the vlans and setup the ip addresses
        # 2. try to ping each other
        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan102 type vlan id 102' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan102')
        h11.cmd('ip addr add 102.103.0.11/24 dev vlan102')
        h2.cmd('ip link add link %s name vlan103 type vlan id 103' % (h2.intfNames()[0]))
        h2.cmd('ip link set up vlan103')
        h2.cmd('ip addr add 102.103.0.2/24 dev vlan103')
        result = h11.cmd('ping -c1 102.103.0.2')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip link del vlan102')
        h2.cmd('ip link del vlan103')
        self.net.restart_kytos_clean()

    def test_030_create_evc_tag_notag(self):
        payload = {
            "name": "Vlan104_Test",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": 104}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1"
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        # Each switch must have BASIC_FLOWS + 03 for the EVC:
        #  - 2 for current path (ingress + egress)
        #  - 1 for failover path
        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 3, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 3, flows_s2

        #Make sure that both flow have EVPL and EPL default values
        assert 'priority=20000' in flows_s1
        assert 'priority=10000' in flows_s2

        # make sure it should be dl_vlan instead of vlan_vid
        assert 'in_port="s1-eth1",dl_vlan=104' in flows_s1, flows_s1
        assert 'in_port="s2-eth1",dl_vlan' not in flows_s2, flows_s2

        # Make the final and most important test: connectivity
        # 1. create the vlans and setup the ip addresses
        # 2. try to ping each other
        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan104 type vlan id 104' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan104')
        h11.cmd('ip addr add 104.0.0.11/24 dev vlan104')
        h2.cmd('ip addr add 104.0.0.2/24 dev %s' % (h2.intfNames()[0]))
        result = h11.cmd('ping -c1 104.0.0.2')

        # make sure it should be dl_vlan instead of vlan_vid
        assert 'dl_vlan=104' in flows_s1

        # clean up
        h11.cmd('ip link del vlan104')
        h2.cmd('ip addr del 104.0.0.2/24 dev %s' % (h2.intfNames()[0]))
        self.net.restart_kytos_clean()

    def test_035_create_evc_same_vid_different_uni(self):
        # Create circuit 1
        payload = {
            "name": "Vlan110_Test",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": 1, "value": 110}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": 1, "value": 110}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc1 = data['circuit_id']
        time.sleep(10)

        # Create circuit 2: same vlan id but in different UNIs
        payload = {
            "name": "Vlan110_Test2",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:2",
                "tag": {"tag_type": "vlan", "value": 110}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:03:1",
                "tag": {"tag_type": "vlan", "value": 110}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc2 = data['circuit_id']
        assert evc1 != evc2
        time.sleep(10)

        # Switch s1 should have BASIC_FLOWS + 3 for evc1 + 3 for evc2
        # Switch s2 should have BASIC_FLOWS + 3 for evc1 + 2 for evc2/failover
        # Switch s2 should have BASIC_FLOWS + 3 for evc2 + 2 for evc1/failover
        s1, s2, s3 = self.net.net.get('s1', 's2', 's3')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 6, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 5, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS + 5, flows_s3

        #Make sure that both flow have EVPL default values
        assert 'priority=20000' in flows_s1
        assert 'priority=20000' in flows_s2
        assert 'priority=20000' in flows_s3

        # make sure it should be dl_vlan instead of vlan_vid
        assert 'dl_vlan=110' in flows_s1
        assert 'dl_vlan=110' in flows_s2
        assert 'dl_vlan=110' in flows_s3

        # Make the final and most important test: connectivity
        # 1. create the vlans and setup the ip addresses
        # 2. try to ping each other
        # for evc 1:
        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan110 type vlan id 110' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan110')
        h11.cmd('ip addr add 110.0.0.11/24 dev vlan110')
        h2.cmd('ip link add link %s name vlan110 type vlan id 110' % (h2.intfNames()[0]))
        h2.cmd('ip link set up vlan110')
        h2.cmd('ip addr add 110.0.0.2/24 dev vlan110')
        result = h11.cmd('ping -c1 110.0.0.2')
        assert ', 0% packet loss,' in result

        # for evc 2:
        h12, h3 = self.net.net.get('h12', 'h3')
        h12.cmd('ip link add link %s name vlan110 type vlan id 110' % (h12.intfNames()[0]))
        h12.cmd('ip link set up vlan110')
        h12.cmd('ip addr add 110.0.0.12/24 dev vlan110')
        h3.cmd('ip link add link %s name vlan110 type vlan id 110' % (h3.intfNames()[0]))
        h3.cmd('ip link set up vlan110')
        h3.cmd('ip addr add 110.0.0.3/24 dev vlan110')
        result = h12.cmd('ping -c1 110.0.0.3')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip link del vlan110')
        h12.cmd('ip link del vlan110')
        h2.cmd('ip link del vlan110')
        h3.cmd('ip link del vlan110')
        self.net.restart_kytos_clean()

    def test_040_disable_circuit_should_remove_openflow_rules(self):
        # let's suppose that xyz is the circuit id previously created
        # curl -X PATCH -H "Content-Type: application/json" -d '{"enabled": false}' http://172.18.0.2:8181/api/kytos/mef_eline/v2/evc/xyz
        payload = {
            "name": "Vlan125_Test_evc1",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": 125}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": 1, "value": 125}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc1 = data['circuit_id']
        time.sleep(10)

        # It verifies EVC's status
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['enabled'] is True

        # It disables the circuit
        payload = {"enabled": False}
        response = requests.patch(api_url + evc1, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 200, response.text
        time.sleep(10)

        # It verifies EVC's status
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['enabled'] is False

        # Each switch must have BASIC_FLOWS
        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS, flows_s2

        # Nodes should not be able to ping each other
        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan125 type vlan id 125' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan125')
        h11.cmd('ip addr add 125.0.0.11/24 dev vlan125')
        h2.cmd('ip link add link %s name vlan125 type vlan id 125' % (h2.intfNames()[0]))
        h2.cmd('ip link set up vlan125')
        h2.cmd('ip addr add 125.0.0.2/24 dev vlan125')
        result = h11.cmd('ping -c1 125.0.0.2')
        assert ', 100% packet loss,' in result

        # Clean up
        h11.cmd('ip link del vlan125')
        h2.cmd('ip link del vlan125')

    def test_045_create_circuit_reusing_same_vlanid_from_previous_evc(self):
        payload = {
            "name": "Vlan125_Test_evc1",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": 125}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": "vlan", "value": 125}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc1 = data['circuit_id']
        time.sleep(10)

        # Delete the circuit
        api_url += evc1
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text
        time.sleep(10)

        # try to reuse the vlan id
        payload = {
            "name": "Vlan125_Test_evc2",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": 1, "value": 125}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": 1, "value": 125}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc2 = data['circuit_id']
        assert evc1 != evc2
        time.sleep(10)

        # Each switch must have BASIC_FLOWS + 03 for the EVC:
        #  - 2 for current path (ingress + egress)
        #  - 1 for failover path
        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 3, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 3, flows_s2

        # Nodes should be able to ping each other
        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan125 type vlan id 125' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan125')
        h11.cmd('ip addr add 125.0.0.11/24 dev vlan125')
        h2.cmd('ip link add link %s name vlan125 type vlan id 125' % (h2.intfNames()[0]))
        h2.cmd('ip link set up vlan125')
        h2.cmd('ip addr add 125.0.0.2/24 dev vlan125')
        result = h11.cmd('ping -c1 125.0.0.2')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip link del vlan125')
        h2.cmd('ip link del vlan125')
        self.net.restart_kytos_clean()

    def test_050_on_primary_path_fail_should_migrate_to_backup(self):
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 101
                }
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:03:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 101
                }
            },
            "primary_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"}},
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:02:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:03:2"}}
            ],
            "backup_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:4"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:03:3"}}
            ]
        }

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text

        time.sleep(10)

        # Each switch must have BASIC_FLOWS + 02 for the EVC:
        #  - 2 for current path (ingress + egress)
        #  - (there will be no failover path)
        s1, s2, s3 = self.net.net.get('s1', 's2', 's3')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')

        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 2, flows_s1
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS + 2, flows_s2
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS + 2, flows_s3

        # Command to up/down links to test if back-up path is taken
        self.net.net.configLinkStatus('s1', 's2', 'down')

        # Wait just a few seconds to give time to the controller receive and process the linkDown event
        time.sleep(10)

        # # Check on the virtual switches directly for flows
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        flows_s3 = s3.dpctl('dump-flows')

        # Nodes should be able to ping each other
        h11, h3 = self.net.net.get('h11', 'h3')
        h11.cmd('ip link add link %s name vlan101 type vlan id 101' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan101')
        h11.cmd('ip addr add 101.0.0.1/24 dev vlan101')
        h3.cmd('ip link add link %s name vlan101 type vlan id 101' % (h3.intfNames()[0]))
        h3.cmd('ip link set up vlan101')
        h3.cmd('ip addr add 101.0.0.3/24 dev vlan101')
        result = h11.cmd('ping -c1 101.0.0.3')

        # Clean up
        h11.cmd('ip link del vlan101')
        h3.cmd('ip link del vlan101')

        # Command to up/down links to test if back-up path is taken
        self.net.net.configLinkStatus('s1', 's2', 'up')

        assert ', 0% packet loss,' in result
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS + 2
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS
        assert len(flows_s3.split('\r\n ')) == BASIC_FLOWS + 2

    def test_055_delete_evc_after_restart_kytos_and_no_switch_reconnected(self):
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        # restart the controller and change the port on purpose to avoid switches to connect
        self.net.start_controller(clean_config=False, enable_all=True, port=9999)
        time.sleep(10)

        # Delete the circuit
        response = requests.delete(api_url + evc1)
        assert response.status_code == 200, response.text
        time.sleep(10)

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert evc1 not in data

        response = requests.get(api_url, params={'archived': True})
        assert response.status_code == 200, response.text
        data = response.json()
        assert evc1 in data
        assert data[evc1]['archived'] is True
        assert data[evc1]['active'] is False

    @pytest.mark.skip(reason="TODO")
    def test_patch_evc_by_changing_unis_from_interface_to_another(self):
        """To edit an EVC, a PATCH request must be used:

        PATCH /kytos/mef_eline/v2.0/evc/<id>

        Information necessary to modify the EVC:

        {UNI_A, UNI_Z, str bandwidth, datetime start_date, datetime end_date,
        [str primary_links], [str backup_links], bool dynamic_backup_path,
        tenant, service_level}"""

        assert True

    @pytest.mark.skip(reason="TODO")
    def test_create_evc_with_scheduled_times_for_provisioning_and_ending(self):
        assert True

    def test_080_create_and_remove_ten_circuits_ten_times(self):
        """ Tests the creation and removal of ten circuits many times. """
        for x in range(1, 10):
            evcs = {}
            for i in range(400, 410):
                payload = {
                    "name": "evc_%s" % i,
                    "enabled": True,
                    "dynamic_backup_path": True,
                    "uni_a": {
                        "interface_id": "00:00:00:00:00:00:00:01:1",
                        "tag": {"tag_type": 1, "value": i}
                    },
                    "uni_z": {
                        "interface_id": "00:00:00:00:00:00:00:02:1",
                        "tag": {"tag_type": "vlan", "value": i}
                    }
                }
                api_url = KYTOS_API + '/mef_eline/v2/evc/'
                response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
                assert response.status_code == 201, response.text
                data = response.json()
                assert 'circuit_id' in data
                evcs[i] = data['circuit_id']

            time.sleep(10)

            # make sure the evcs are active and the flows were created
            s1, s2 = self.net.net.get('s1', 's2')
            flows_s1 = s1.dpctl('dump-flows')
            flows_s2 = s2.dpctl('dump-flows')
            for vid in evcs:
                evc_id = evcs[vid]
                api_url = KYTOS_API + '/mef_eline/v2/evc/' + evc_id
                response = requests.get(api_url)
                assert response.status_code == 200, response.text
                evc = response.json()
                # should be active
                assert evc["active"] is True
                # search for the vlan id
                assert "dl_vlan=%s" % vid in flows_s1
                assert "dl_vlan=%s" % vid in flows_s2
                # search for the cookie, should have three flows:
                #  - 2 for the current path
                #  - 1 for the failover path
                assert len(re.findall(evc['id'], flows_s1, flags=RE_I)) == 3, \
                    f"round={x} - should have 3 flows but had: \n{flows_s1}"
                assert len(re.findall(evc['id'], flows_s2, flags=RE_I)) == 3, \
                    f"round={x} - should have 3 flows but had: \n{flows_s2}"

            # Delete the circuits
            for vid in evcs:
                evc_id = evcs[vid]
                api_url = KYTOS_API + '/mef_eline/v2/evc/' + evc_id
                response = requests.delete(api_url)
                assert response.status_code == 200, response.text

            time.sleep(10)

            # make sure the circuits were deleted
            api_url = KYTOS_API + '/mef_eline/v2/evc/'
            response = requests.get(api_url)
            assert response.status_code == 200, response.text
            assert response.json() == {}
            flows_s1 = s1.dpctl('dump-flows')
            flows_s2 = s2.dpctl('dump-flows')
            assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, \
                f"round={x} - should have {BASIC_FLOWS} flows but had: \n{flows_s1}"
            assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS, \
                f"round={x} - should have {BASIC_FLOWS} flows but had: \n{flows_s2}"

    def test_085_create_and_remove_ten_circuit_concurrently(self):
        """
        Tests the performance and race condition with
        the creation of multiple circuits using threading
        """
        import threading
        threads = list()
        for i in range(400, 410):
            t = threading.Thread(target=self.create_evc, args=(i, True))
            threads.append(t)
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # give some time so Kytos can create the flows and everything
        time.sleep(10)

        # make sure the evcs are active and the flows were created
        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        for vid in self.evcs:

            api_url = KYTOS_API + '/mef_eline/v2/evc/' + self.evcs[vid]
            response = requests.get(api_url)
            assert response.status_code == 200, response.text
            evc = response.json()
            # should be active
            assert evc["active"] is True

            # search for the vlan id
            assert "dl_vlan=%s" % vid in flows_s1
            assert "dl_vlan=%s" % vid in flows_s2
            # search for the cookie, should have three flows:
            #  - 2 for the current path
            #  - 1 for the failover path
            assert len(re.findall(evc['id'], flows_s1, flags=RE_I)) == 3, \
                "should have 3 flows but had: \n%s" % flows_s1
            assert len(re.findall(evc['id'], flows_s2, flags=RE_I)) == 3, \
                "should have 3 flows but had: \n%s" % flows_s2

        # Delete the circuits
        for vid in self.evcs:
            evc_id = self.evcs[vid]
            api_url = KYTOS_API + '/mef_eline/v2/evc/' + evc_id
            response = requests.delete(api_url)
            assert response.status_code == 200, response.text

        time.sleep(10)

        # make sure the circuits were deleted
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        assert response.json() == {}
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        assert len(flows_s1.split('\r\n ')) == BASIC_FLOWS, \
            f"should have only {BASIC_FLOWS} flow but had: \n{flows_s1}"
        assert len(flows_s2.split('\r\n ')) == BASIC_FLOWS, \
            f"should have only {BASIC_FLOWS} flow but had: \n{flows_s2}"

    def test_090_patch_evc_new_name(self):

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        # It verifies EVC's name
        response = requests.get(api_url + evc1)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['name'] == 'Vlan_100'

        payload = {"name": "My EVC_100"}

        # It sets a new name
        response = requests.patch(api_url + evc1, data=json.dumps(payload),
                                  headers={'Content-type': 'application/json'})
        assert response.status_code == 200, response.text

        time.sleep(10)

        # It verifies EVC's new name
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['name'] == 'My EVC_100'

    def test_095_patch_evc_new_unis(self):
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        payload = {
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:2"
            }
        }

        # It sets a new interface_id
        response = requests.patch(api_url + evc1, data=json.dumps(payload),
                                  headers={'Content-type': 'application/json'})
        assert response.status_code == 200, response.text

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['uni_a']['interface_id'] == "00:00:00:00:00:00:00:01:2"

    def test_100_patch_evc_new_unis(self):
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        payload = {
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:2"
            }
        }

        # It sets a new interface_id
        response = requests.patch(api_url + evc1, data=json.dumps(payload),
                                  headers={'Content-type': 'application/json'})
        assert response.status_code == 200, response.text

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['uni_z']['interface_id'] == "00:00:00:00:00:00:00:02:2"

    """Error - The circuit remains active despite 
    the modification on its end_date attribute"""
    @pytest.mark.xfail
    def test_105_patch_end_date(self):

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        end_delay = 1
        end_date = datetime.now() + timedelta(minutes=end_delay)

        payload = {
            "end_date": end_date.strftime(TIME_FMT)
        }

        # It sets a new circuit's end_date
        requests.patch(api_url + evc1, data=json.dumps(payload),
                       headers={'Content-type': 'application/json'})
        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['end_date'] == end_date.strftime(TIME_FMT)

        # waiting to reach the new end timing
        time.sleep(end_delay*60+5)

        # Verify if the circuit is active
        api_url = KYTOS_API + '/mef_eline/v2/evc/' + evc1
        response = requests.get(api_url)
        data = response.json()
        assert data["active"] is False

    def test_110_patch_bandwidth(self):

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        bandwidth = 40
        payload = {
            "bandwidth": bandwidth
        }

        # It sets a new circuit's bandwidth
        requests.patch(api_url + evc1, data=json.dumps(payload),
                       headers={'Content-type': 'application/json'})
        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['bandwidth'] == bandwidth

        requests.delete(api_url + evc1)

    def test_115_patch_priority(self):

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        sb_priority = 100
        payload = {
            "sb_priority": sb_priority
        }

        # It sets a new circuit's sb_priority
        response = requests.patch(api_url + evc1, data=json.dumps(payload),
                                  headers={'Content-type': 'application/json'})
        assert response.status_code == 200, response.text

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['sb_priority'] == sb_priority, data

        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')
        assert 'priority=100' in flows_s1
        assert 'priority=100' in flows_s2

    def test_120_patch_queue_id(self):

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        queue_id = 3
        payload = {
            "queue_id": queue_id
        }

        # It sets a new circuit's queue_id
        requests.patch(api_url + evc1, data=json.dumps(payload),
                       headers={'Content-type': 'application/json'})

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['queue_id'] == queue_id

        s1, s2 = self.net.net.get('s1', 's2')
        flows_s1 = s1.dpctl('dump-flows')
        flows_s2 = s2.dpctl('dump-flows')

        assert 'set_queue:3' in flows_s1
        assert 'set_queue:3' in flows_s2

    def test_125_patch_dynamic_backup_path(self):
        """Test patching an EVC to be non dynamic with primary_path."""

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)

        dynamic_backup_path = False
        payload = {
            "dynamic_backup_path": dynamic_backup_path,
            "primary_path": [
                {
                    "endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                    "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"},
                }
            ],
        }

        # It sets a new circuit's dynamic_backup_path
        response = requests.patch(api_url + evc1, data=json.dumps(payload),
                                  headers={'Content-type': 'application/json'})
        assert response.status_code == 200, response.text

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['dynamic_backup_path'] == dynamic_backup_path

    def test_126_patch_dynamic_backup_path_false_no_primary_path(self):
        """Test try to patch dynamic_backup_path as False with an EVC
        that doesn't have a static primary_path."""

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)
        payload = {
            "dynamic_backup_path": False,
        }
        response = requests.patch(api_url + evc1, data=json.dumps(payload),
                                  headers={'Content-type': 'application/json'})
        assert response.status_code == 400, response.text
        expected_err = "must have a primary path or allow dynamic paths"
        data = response.json()
        assert expected_err in data["description"]

    def test_130_patch_primary_path(self):
        """Patch EVC with primary_path and verify that it is applied to
         current_path."""

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        payload1 = {
            "name": "my evc1",
            "enabled": True,
            "dynamic_backup_path": False,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1"
            },
            "primary_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"}}
            ]
        }
        response = requests.post(api_url, data=json.dumps(payload1),
                                 headers={'Content-type': 'application/json'})
        data = response.json()
        evc1 = data['circuit_id']
        time.sleep(10)
        payload2 = {
            "primary_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:4"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:03:3"}},
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:02:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:03:2"}}
            ]
        }
        # It sets a new circuit's primary_path
        response = requests.patch(api_url + evc1, data=json.dumps(payload2),
                                  headers={'Content-type': 'application/json'})
        assert response.status_code == 200, response.text

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()

        paths = []
        for _path in data['current_path']:
            paths.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})

        assert paths == payload2["primary_path"]
        assert data['active'] is True

        response = requests.get(api_url + evc1)
        assert response.status_code == 200, response.text
        data = response.json()
        paths = []
        for _path in data['primary_path']:
            paths.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})
        assert paths == payload2["primary_path"]

    def test_135_patch_backup_path(self):

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {
                    "tag_type": 1,
                    "value": 101
                }
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:03:1",
                "tag": {
                    "tag_type": 1,
                    "value": 101
                }
            },
            "primary_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"}},
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:02:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:03:2"}}
            ],
            "backup_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:4"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:03:3"}}
            ]
        }
        response = requests.post(api_url, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        data = response.json()
        evc1 = data['circuit_id']

        time.sleep(10)

        payload2 = {
            "backup_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:4"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:03:3"}}
            ]
        }

        # It sets a new circuit's backup_path
        requests.patch(api_url + evc1, data=json.dumps(payload2),
                       headers={'Content-type': 'application/json'})

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()

        paths = []
        for _path in data['backup_path']:
            paths.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})

        assert paths == payload2["backup_path"]
        assert data['active'] is True

        requests.delete(api_url + evc1)

    def test_140_current_path_value_given_dynamic_backup_path_and_primary_path_conditions(self):
        payload = {
            "name": "my evc1",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": 100}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": "vlan", "value": 100}
            },
            "primary_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"}}
            ]
        }

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        data = response.json()
        evc1 = data['circuit_id']

        time.sleep(10)

        # Command to up/down links to test if back-up path is taken
        self.net.net.configLinkStatus('s1', 's2', 'down')

        # Wait just a few seconds to give time to the controller receive and process the linkDown event
        time.sleep(10)

        current_path = [{"endpoint_a": {"id": "00:00:00:00:00:00:00:01:4"},
                         "endpoint_b": {"id": "00:00:00:00:00:00:00:03:3"}},
                        {"endpoint_a": {"id": "00:00:00:00:00:00:00:02:3"},
                         "endpoint_b": {"id": "00:00:00:00:00:00:00:03:2"}}]

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()

        paths = []
        for _path in data['current_path']:
            paths.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})

        # Command to up/down links to test if back-up path is taken
        self.net.net.configLinkStatus('s1', 's2', 'up')

        assert paths == current_path

    def test_145_current_path_value_given_dynamic_backup_path_and_empty_primary_conditions(self):
        payload = {
            "name": "my evc1",
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

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        data = response.json()
        evc1 = data['circuit_id']

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()

        paths = []
        for _path in data['current_path']:
            paths.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})

        current_path = [{"endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                         "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"}}]

        assert paths == current_path

        # Command to up/down links to test if back-up path is taken
        self.net.net.configLinkStatus('s1', 's2', 'down')

        # Wait just a few seconds to give time to the controller receive and process the linkDown event
        time.sleep(10)

        current_path = [{"endpoint_a": {"id": "00:00:00:00:00:00:00:01:4"},
                         "endpoint_b": {"id": "00:00:00:00:00:00:00:03:3"}},
                        {"endpoint_a": {"id": "00:00:00:00:00:00:00:02:3"},
                         "endpoint_b": {"id": "00:00:00:00:00:00:00:03:2"}}]

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()

        paths = []
        for _path in data['current_path']:
            paths.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})
            
        assert paths == current_path

        # Command to up/down links to test if back-up path is taken
        # EVC should not redeploy
        self.net.net.configLinkStatus('s1', 's2', 'up')

        # Wait just a few seconds to give time to the controller receive and process the linkUp event
        time.sleep(10)

        response = requests.get(api_url + evc1)
        data = response.json()

        paths = []
        for _path in data['current_path']:
            paths.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})

        assert paths == current_path

    def test_150_current_path_value_given_dynamic_backup_path_and_empty_primary_conditions(self):
        payload = {
            "name": "my evc1",
            "enabled": True,
            "dynamic_backup_path": False,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": 100}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": 1, "value": 100}
            },
            "primary_path": [
                {"endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"}}
            ]
        }

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        data = response.json()
        evc1 = data['circuit_id']

        time.sleep(10)

        # Command to up/down links to test if back-up path is taken
        self.net.net.configLinkStatus('s1', 's2', 'down')

        # Wait just a few seconds to give time to the controller receive and process the linkDown event
        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()

        assert data['active'] is False
        assert data['enabled'] is True
        assert not data['current_path']

        current_path = []
        for _path in data['current_path']:
            current_path.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})
    
        expected_path = []

        assert current_path == expected_path

        # Command to up/down links to test if back-up path is taken
        self.net.net.configLinkStatus('s1', 's2', 'up')

        time.sleep(10)

        response = requests.get(api_url + evc1)
        data = response.json()

        assert data['active'] is True
        assert data['enabled'] is True
        assert data['current_path']

        expected_path = [
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:01:3"},
                 "endpoint_b": {"id": "00:00:00:00:00:00:00:02:2"}
            }
        ]

        current_path = []
        for _path in data['current_path']:
            current_path.append({"endpoint_a": {"id": _path['endpoint_a']['id']},
                          "endpoint_b": {"id": _path['endpoint_b']['id']}})

        assert current_path == expected_path

    def test_155_removing_evc_metadata_persistent(self):
        """
        Test /api/kytos/mef_eline/v2/evc/{evc_id}/metadata/{key} on DELETE
        supported by:
            /api/kytos/mef_eline/v2/evc/{evc_id}/metadata on POST
            and
            /api/kytos/mef_eline/v2/evc/{evc_id}/metadata on GET
        """
        evc1 = self.create_evc(100)
        api_url = KYTOS_API + '/mef_eline/v2/evc/%s/metadata' % evc1

        # Make sure the metadata is initially empty
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert 'metadata' in data
        assert data['metadata'] == {}

        # Insert evc metadata
        my_key = 'tmp_key'
        payload = {my_key: "tmp_value", "other": [1, 2, 3]}

        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text

        # Make sure the metadata was inserted
        response = requests.get(api_url)
        data = response.json()
        assert data['metadata'] == payload

        self.restart()

        # Make sure the metadata is still there
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['metadata'] == payload

        # Delete the evc metadata
        response = requests.delete(api_url + '/' + my_key)
        assert response.status_code == 200, response.text

        # Make sure the metadata was deleted
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert my_key not in data['metadata']
        assert len(data['metadata']) > 0

        self.restart()

        # Make sure the metadata is still not there
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert my_key not in data['metadata']
        assert len(data['metadata']) > 0

    @staticmethod
    def get_flow_by_vlan_match(data, dl_vlan):
        """Get flow from data matching with dl_vlan"""
        for flow in data:
            if flow["match"].get("dl_vlan", None) == dl_vlan:
                return flow
        return None

    def test_160_create_untagged_evc(self):
        """Test create an EVC with untagged in both uni"""
        payload = {
            "name": "evc1",
            "dynamic_backup_path": True,
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:01:1"
            },
            "uni_z": {
                "tag": {"tag_type": "vlan", "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:02:1"
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        untagged_flow = self.get_flow_by_vlan_match(data, 0)

        expected = {
            "match": {"in_port": 1, "dl_vlan": 0},
            "actions": [
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 3}
            ],
            "priority": 20000
        }
        assert untagged_flow["match"] == expected["match"]
        assert untagged_flow["priority"] == expected["priority"]
        assert untagged_flow["instructions"][0]["actions"] == expected["actions"]

        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip addr add 100.0.0.11/24 dev %s' % (h11.intfNames()[0]))
        h2.cmd('ip addr add 100.0.0.2/24 dev %s' % (h2.intfNames()[0]))
        result = h11.cmd('ping -c1 100.0.0.2')
        assert ', 0% packet loss,' in result

        # Clean up
        h11.cmd('ip addr del 100.0.0.11/24 dev %s' % (h11.intfNames()[0]))
        h2.cmd('ip addr del 100.0.0.2/24 dev %s' % (h2.intfNames()[0]))
        
    def test_165_create_any_evc(self):
        """Test create an EVC with any in both uni"""
        payload = {
            "name": "evc1",
            "dynamic_backup_path": True,
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": "vlan", "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:01:1"
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:02:1"
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        any_flow = self.get_flow_by_vlan_match(data, "4096/4096")

        expected = {
            "match": {"in_port": 1, "dl_vlan": "4096/4096"},
            "actions": [
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 3}
            ],
            "priority": 15000
        }
        assert any_flow["match"] == expected["match"]
        assert any_flow["priority"] == expected["priority"]
        assert any_flow["instructions"][0]["actions"] == expected["actions"]

        ra_vlan = randrange(1, 4096)
        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan_ra type vlan id %s' % (h11.intfNames()[0], ra_vlan))
        h11.cmd('ip link set up vlan_ra')
        h11.cmd('ip addr add 100.0.0.11/24 dev vlan_ra')
        h2.cmd('ip link add link %s name vlan_ra type vlan id %s' % (h2.intfNames()[0], ra_vlan))
        h2.cmd('ip link set up vlan_ra')
        h2.cmd('ip addr add 100.0.0.2/24 dev vlan_ra')
        result = h11.cmd('ping -c1 100.0.0.2')
        assert ', 0% packet loss,' in result

        # Clean up
        h11.cmd('ip link del vlan_ra')
        h2.cmd('ip link del vlan_ra')

    def test_170_create_any_100_evc(self):
        """Test create an EVC with any and 100 as uni.tag.value"""
        payload = {
            "name": "evc1",
            "dynamic_backup_path": True,
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:01:1"
            },
            "uni_z": {
                "tag": {"tag_type": "vlan", "value": 100},
                "interface_id": "00:00:00:00:00:00:00:02:1"
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = [
            {"match": {"in_port": 1, "dl_vlan": "4096/4096"},
            "actions": [
                {"action_type": "set_vlan", "vlan_id": 100},
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 3}
            ],
            "priority": 15000},
            {"match": {"in_port": 1, "dl_vlan": 100},
            "actions": [
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 2}
            ],
            "priority": 20000}
        ]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        any_flow = self.get_flow_by_vlan_match(data, "4096/4096")
        assert any_flow["match"] == expected[0]["match"]
        assert any_flow["priority"] == expected[0]["priority"]
        assert any_flow["instructions"][0]["actions"] == expected[0]["actions"]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:02'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:02"]["flows"]
        common_flow = self.get_flow_by_vlan_match(data, 100)
        assert common_flow["match"] == expected[1]["match"]
        assert common_flow["priority"] == expected[1]["priority"]
        assert common_flow["instructions"][0]["actions"] == expected[1]["actions"]

        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan100 type vlan id 100' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan100')
        h11.cmd('ip addr add 100.0.0.11/24 dev vlan100')
        h2.cmd('ip link add link %s name vlan100 type vlan id 100' % (h2.intfNames()[0]))
        h2.cmd('ip link set up vlan100')
        h2.cmd('ip addr add 100.0.0.2/24 dev vlan100')
        result = h11.cmd('ping -c1 100.0.0.2')
        assert ', 0% packet loss,' in result

        # Clean up
        h11.cmd('ip link del vlan100')
        h2.cmd('ip link del vlan100')

    def test_175_create_100_untagged_evc(self):
        """Test create an EVC with 100 and untagged as uni.tag.value"""
        payload = {
            "name": "evc1",
            "dynamic_backup_path": True,
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": "vlan", "value": 100},
                "interface_id": "00:00:00:00:00:00:00:01:1"
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:02:1"
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = [
            {"match": {"in_port": 1, "dl_vlan": 100},
            "actions": [
                {"action_type": "pop_vlan"},
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 3}
            ],
            "priority": 20000},
            {"match": {"in_port": 1, "dl_vlan": 0},
            "actions": [
                {"action_type": "push_vlan", "tag_type": "c"},
                {"action_type": "set_vlan", "vlan_id": 100},
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 2}
            ],
            "priority": 20000}
        ]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        common_flow = self.get_flow_by_vlan_match(data, 100)
        assert common_flow["match"] == expected[0]["match"]
        assert common_flow["priority"] == expected[0]["priority"]
        assert common_flow["instructions"][0]["actions"] == expected[0]["actions"]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:02'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:02"]["flows"]
        untagged_flow = self.get_flow_by_vlan_match(data, 0)
        assert untagged_flow["match"] == expected[1]["match"]
        assert untagged_flow["priority"] == expected[1]["priority"]
        assert untagged_flow["instructions"][0]["actions"] == expected[1]["actions"]

        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan100 type vlan id 100' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan100')
        h11.cmd('ip addr add 100.0.0.11/24 dev vlan100')
        h2.cmd('ip addr add 100.0.0.2/24 dev %s' % (h2.intfNames()[0]))
        result = h11.cmd('ping -c1 100.0.0.2')
        assert ', 0% packet loss,' in result

        # Clean up
        h11.cmd('ip link del vlan100')
        h2.cmd('ip addr del 100.0.0.2/24 dev %s' % (h2.intfNames()[0]))

    def test_180_create_any_untagged_evc(self):
        """Test create an EVC with any and untagged as uni.tag.value"""
        payload = {
            "name": "evc1",
            "dynamic_backup_path": True,
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:01:1"
            },
            "uni_z": {
                "tag": {"tag_type": "vlan", "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:02:1"
            }
        }

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = [
            {"match": {"in_port": 1, "dl_vlan": "4096/4096"},
            "actions": [
                {"action_type": "pop_vlan"},
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 3}
            ],
            "priority": 15000},
            {"match": {"in_port": 1, "dl_vlan": 0},
            "actions": [
                {"action_type": "push_vlan", "tag_type": "s"},
                {"action_type": "set_vlan", "vlan_id": 1},
                {"action_type": "output", "port": 2}
            ],
            "priority": 20000}
        ]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        any_flow = self.get_flow_by_vlan_match(data, "4096/4096")
        assert any_flow["match"] == expected[0]["match"]
        assert any_flow["priority"] == expected[0]["priority"]
        assert any_flow["instructions"][0]["actions"] == expected[0]["actions"]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:02'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:02"]["flows"]
        untagged_flow = self.get_flow_by_vlan_match(data, 0)
        assert untagged_flow["match"] == expected[1]["match"]
        assert untagged_flow["priority"] == expected[1]["priority"]
        assert untagged_flow["instructions"][0]["actions"] == expected[1]["actions"]

    def test_185_create_any_intra_evc(self):
        """Test create an intra-switch EVC with any as uni.tag.value"""
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": "vlan", "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = [
            {"match": {"in_port": 1, "dl_vlan": "4096/4096"},
            "actions": [{"action_type": "output", "port": 2}],
            "priority": 15000},
        ]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        any_flow = self.get_flow_by_vlan_match(data, "4096/4096")
        assert any_flow["match"] == expected[0]["match"]
        assert any_flow["priority"] == expected[0]["priority"]
        assert any_flow["instructions"][0]["actions"] == expected[0]["actions"]

        ra_vlan = randrange(1, 4096)
        h11, h12 = self.net.net.get('h11', 'h12')
        h11.cmd('ip link add link %s name ra_vlan type vlan id %s' % (h11.intfNames()[0], ra_vlan))
        h11.cmd('ip link set up ra_vlan')
        h11.cmd('ip addr add 10.1.1.11/24 dev ra_vlan')
        h12.cmd('ip link add link %s name ra_vlan type vlan id %s' % (h12.intfNames()[0], ra_vlan))
        h12.cmd('ip link set up ra_vlan')
        h12.cmd('ip addr add 10.1.1.12/24 dev ra_vlan')
        result = h11.cmd('ping -c1 10.1.1.12')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip link del ra_vlan')
        h12.cmd('ip link del ra_vlan')

    def test_190_create_untagged_intra_evc(self):
        """Test create an intra-switch EVC with untagged as uni.tag.value"""
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": "vlan", "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = {
            "match": {"in_port": 1, "dl_vlan": 0},
            "actions": [{"action_type": "output", "port": 2}],
            "priority": 20000
        }

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        untagged_flow = self.get_flow_by_vlan_match(data, 0)
        assert untagged_flow["match"] == expected["match"]
        assert untagged_flow["priority"] == expected["priority"]
        assert untagged_flow["instructions"][0]["actions"] == expected["actions"]

        h11, h12 = self.net.net.get('h11', 'h12')
        h11.cmd('ip addr add 100.1.1.11/24 dev %s' % (h11.intfNames()[0]))
        h12.cmd('ip addr add 100.1.1.12/24 dev %s' % (h12.intfNames()[0]))
        result = h11.cmd('ping -c1 100.1.1.12')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip addr del 100.1.1.11/24 dev %s' % (h11.intfNames()[0]))
        h12.cmd('ip addr del 100.1.1.12/24 dev %s' % (h12.intfNames()[0]))

    def test_195_create_any_100_intra_evc(self):
        """Test create an intra-switch EVC with any and 100 as uni.tag.value"""
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": "vlan", "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = [
            {"match": {"in_port": 1, "dl_vlan": "4096/4096"},
            "actions": [{"action_type": "set_vlan", "vlan_id": 100},
                        {"action_type": "output", "port": 2}],
            "priority": 15000},
            {"match": {"in_port": 2, "dl_vlan": 100},
            "actions": [{"action_type": "output", "port": 1}],
            "priority": 20000}
        ]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        any_flow = self.get_flow_by_vlan_match(data, "4096/4096")
        assert any_flow["match"] == expected[0]["match"]
        assert any_flow["priority"] == expected[0]["priority"]
        assert any_flow["instructions"][0]["actions"] == expected[0]["actions"]

        commom_flow = self.get_flow_by_vlan_match(data, 100)
        assert commom_flow["match"] == expected[1]["match"]
        assert commom_flow["priority"] == expected[1]["priority"]
        assert commom_flow["instructions"][0]["actions"] == expected[1]["actions"]

        h11, h12 = self.net.net.get('h11', 'h12')
        h11.cmd('ip link add link %s name vlan100 type vlan id 100' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan100')
        h11.cmd('ip addr add 10.1.1.11/24 dev vlan100')
        h12.cmd('ip link add link %s name vlan100 type vlan id 100' % (h12.intfNames()[0]))
        h12.cmd('ip link set up vlan100')
        h12.cmd('ip addr add 10.1.1.12/24 dev vlan100')
        result = h11.cmd('ping -c1 10.1.1.12')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip link del vlan100')
        h12.cmd('ip link del vlan100')

    def test_200_create_100_untagged_intra_evc(self):
        """Test create an intra-switch EVC with 100 and untagged as 
        uni.tag.value"""
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": "vlan", "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = [
            {"match": {"in_port": 1, "dl_vlan": 100},
            "actions": [
                {"action_type": "pop_vlan"},
                {"action_type": "output", "port": 2}
            ],
            "priority": 20000},
            {"match": {"in_port": 2, "dl_vlan": 0},
            "actions": [
                {"action_type": "push_vlan", "tag_type": 'c'},
                {"action_type": "set_vlan", "vlan_id": 100},
                {"action_type": "output", "port": 1}
            ],
            "priority": 20000}
        ]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        commom_flow = self.get_flow_by_vlan_match(data, 100)
        assert commom_flow["match"] == expected[0]["match"]
        assert commom_flow["priority"] == expected[0]["priority"]
        assert commom_flow["instructions"][0]["actions"] == expected[0]["actions"]

        untagged_flow = self.get_flow_by_vlan_match(data, 0)
        assert untagged_flow["match"] == expected[1]["match"]
        assert untagged_flow["priority"] == expected[1]["priority"]
        assert untagged_flow["instructions"][0]["actions"] == expected[1]["actions"]

        h11, h12 = self.net.net.get('h11', 'h12')
        h11.cmd('ip link add link %s name vlan100 type vlan id 100' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan100')
        h11.cmd('ip addr add 100.1.1.11/24 dev vlan100')
        h12.cmd('ip addr add 100.1.1.12/24 dev %s' % (h12.intfNames()[0]))
        result = h11.cmd('ping -c1 100.1.1.12')
        assert ', 0% packet loss,' in result

        # clean up
        h11.cmd('ip link del vlan100')
        h12.cmd('ip addr del 100.1.1.12/24 dev %s' % (h12.intfNames()[0]))

    def test_205_create_any_untagged_intra_evc(self):
        """Test create an intra-switch EVC with any and untagged as 
        uni.tag.value"""
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": "vlan", "value": "any"},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": "untagged"},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        time.sleep(10)

        expected = [
            {"match": {"in_port": 1, "dl_vlan": "4096/4096"},
            "actions": [{"action_type": "pop_vlan"},
                        {"action_type": "output", "port": 2}],
            "priority": 15000},
            {"match": {"in_port": 2, "dl_vlan": 0},
            "actions": [{"action_type": "output", "port": 1}],
            "priority": 20000}
        ]

        api_url = KYTOS_API + '/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.get(api_url)
        data = response.json()["00:00:00:00:00:00:00:01"]["flows"]
        any_flow = self.get_flow_by_vlan_match(data, "4096/4096")
        assert any_flow["match"] == expected[0]["match"]
        assert any_flow["priority"] == expected[0]["priority"]
        assert any_flow["instructions"][0]["actions"] == expected[0]["actions"]
        
        untagged_flow = self.get_flow_by_vlan_match(data, 0)
        assert untagged_flow["match"] == expected[1]["match"]
        assert untagged_flow["priority"] == expected[1]["priority"]
        assert untagged_flow["instructions"][0]["actions"] == expected[1]["actions"]

    def test_210_bulk_update_evc(self):
        """Test bulk updates to EVCs"""
        evc_1 = {
            "name": "EVC_1",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": "vlan", "value": 100},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": "vlan", "value": 200},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=evc_1)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc_1_id = data["circuit_id"]

        evc_2 = {
            "name": "EVC_2",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:02:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": 2200},
                "interface_id": "00:00:00:00:00:00:00:02:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=evc_2)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc_2_id = data["circuit_id"]

        time.sleep(10)
        
        payload = {
            "circuit_ids":[evc_1_id, evc_2_id],
            "test": "data"
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/metadata'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text

        api_url = KYTOS_API + '/mef_eline/v2/evc?metadata.test=data'
        response = requests.get(api_url, json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "test" in data[evc_1_id]["metadata"]
        assert "test" in data[evc_2_id]["metadata"]

    def test_300_inter_evc_dynamic_uni_status(self):
        """Test UNI status for a dynamic inter EVC."""
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(100)
        time.sleep(10)

        response = requests.get(api_url + evc1)
        data = response.json()
        assert data["active"]
        assert data["current_path"]

        # shutdown NNIs and UNI a
        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s3", "s1", "down")
        self.net.net.configLinkStatus("s1", "h11", "down")

        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert not data["current_path"]

        # bring up only UNI, EVC should still remain not active
        self.net.net.configLinkStatus("s1", "h11", "up")
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        # pathfinder won't find a new path since UNI is part of the graph
        assert not data["current_path"]

        # bring up the rest of NNIs, now it should be activated
        self.net.net.configLinkStatus("s1", "s2", "up")
        self.net.net.configLinkStatus("s3", "s1", "up")
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data["active"]
        assert data["current_path"]

        # shutdown UNI a, now it should deactivate again
        self.net.net.configLinkStatus("s1", "h11", "down")
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert data["current_path"]

        # bring up UNI a, now it should activate
        self.net.net.configLinkStatus("s1", "h11", "up")
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data["active"]
        assert data["current_path"]

        # shutdown NNIs and UNI a
        self.net.net.configLinkStatus("s1", "s2", "down")
        self.net.net.configLinkStatus("s3", "s1", "down")
        self.net.net.configLinkStatus("s1", "h11", "down")

        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert not data["current_path"]

        # bring up a NNI, it shouldn't activate yet since UNI is still down
        self.net.net.configLinkStatus("s1", "s2", "up")
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert not data["current_path"]

        # bring up UNI a, now it should activate, and result in new deployment
        self.net.net.configLinkStatus("s1", "h11", "up")
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data["active"]
        assert data["current_path"]

    def test_301_inter_evc_static_uni_status(self):
        """Test UNI status for a dynamic inter EVC."""
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        payload = {
            "name": "my evc1",
            "enabled": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:03:1"
            },
            "primary_path": [
                {
                    "endpoint_a": {
                        "id": "00:00:00:00:00:00:00:01:4"
                    },
                    "endpoint_b": {
                        "id": "00:00:00:00:00:00:00:03:3"
                    }
                }
            ]
        }

        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        r = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert r.status_code == 201, r.text
        data = r.json()
        evc1 = data["circuit_id"]

        time.sleep(10)

        response = requests.get(api_url + evc1)
        data = response.json()
        assert data["active"]
        assert data["current_path"]

        # shutdown UNI a, it should deactivate
        self.net.net.configLinkStatus('s1', 'h11', 'down')
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert data["current_path"]

        # shutdown NNI too, current_path should be gone too
        self.net.net.configLinkStatus('s3', 's1', 'down')
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert not data["current_path"]

        # bring up UNI a, it shouldn't activate yet, since NNI is down
        self.net.net.configLinkStatus('s1', 'h11', 'up')
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert not data["current_path"]

        # bring up NNI, it should activate
        self.net.net.configLinkStatus('s3', 's1', 'up')
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data["active"]
        assert data["current_path"]

        # shutdown again both
        self.net.net.configLinkStatus('s1', 'h11', 'down')
        self.net.net.configLinkStatus('s3', 's1', 'down')
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert not data["current_path"]

        # bring up NNI, it shouldn't activate since UNI is still down
        self.net.net.configLinkStatus('s3', 's1', 'up')
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert not data["active"]
        assert data["current_path"]

        # bring up UNI a, it should activate
        self.net.net.configLinkStatus('s1', 'h11', 'up')
        time.sleep(10)
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data["active"]
        assert data["current_path"]
