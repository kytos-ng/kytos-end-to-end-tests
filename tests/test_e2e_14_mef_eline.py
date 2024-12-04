import time

import pytest
import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

class TestE2EMefEline:
    net = None

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
        cls.net = NetworkTest(CONTROLLER, topo_name='amlight')
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

    def create_evc(
        self,
        uni_a="00:00:00:00:00:00:00:01:1",
        uni_z="00:00:00:00:00:00:00:02:1",
        vlan_id=100,
        primary_path=None,
        backup_path=None,    
    ):
        payload = {
            "name": "Vlan_%s" % vlan_id,
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": uni_a,
                "tag": {"tag_type": "vlan", "value": vlan_id}
            },
            "uni_z": {
                "interface_id": uni_z,
                "tag": {"tag_type": 1, "value": vlan_id}
            }
        }
        if primary_path:
            payload["primary_path"] = primary_path
            payload["dynamic_backup_path"] = False
        if backup_path:
            payload["backup_path"] = backup_path
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        data = response.json()
        return data['circuit_id']

    def get_evc_data(self, evc_id:str) -> dict:
        api_url = KYTOS_API + '/mef_eline/v2/evc/' + evc_id
        response = requests.get(api_url)
        return response.json()
    
    def redeploy_evc(self, evc_id, try_avoid_same_s_vlan=True):
        str_avoid_vlan = "false"
        if try_avoid_same_s_vlan:
            str_avoid_vlan = "true"
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/{evc_id}/redeploy?try_avoid_same_s_vlan={str_avoid_vlan}"
        requests.patch(api_url)
        time.sleep(10)

    def get_link_vlan_dict_from_path(self, path: dict) -> dict[str, int]:
        link_vlan_dict = {}
        for link in path:
            link_vlan_dict[link["id"]] = link["metadata"]["s_vlan"]["value"]
        return link_vlan_dict

    #
    # Issue: https://github.com/kytos-ng/mef_eline/issues/72
    #
    @pytest.mark.xfail
    def test_005_create_evc_on_nni(self):
        """Test to evaluate how mef_eline will behave when the uni is actually
        an NNI."""
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:16:5',
                               uni_z='00:00:00:00:00:00:00:11:1',
                               vlan_id=100)

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['enabled'] == True
        assert data['active'] == True

        # Verify connectivity
        h6, h1 = self.net.net.get('h6', 'h1')
        h6.cmd('ip link add link %s name vlan100 type vlan id 100' % (h6.intfNames()[0]))
        h6.cmd('ip link set up vlan100')
        h6.cmd('ip addr add 10.1.0.6/24 dev vlan100')
        h1.cmd('ip link add link %s name vlan100 type vlan id 100' % (h1.intfNames()[0]))
        h1.cmd('ip link set up vlan100')
        h1.cmd('ip addr add 10.1.0.1/24 dev vlan100')

        result = h6.cmd('ping -c1 10.1.0.1')
        assert ', 0% packet loss,' in result

        # clean up
        h6.cmd('ip link del vlan100')
        h1.cmd('ip link del vlan100')

    def test_010_redeploy_avoid_vlan(self):
        """Test if dynamic EVC takes different VLAN when redeploying."""
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:20:59',
                               uni_z='00:00:00:00:00:00:00:17:56',
                               vlan_id=100)
        
        time.sleep(10)

        evc_data = self.get_evc_data(evc1)
        old_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])

        self.redeploy_evc(evc1, False)

        evc_data = self.get_evc_data(evc1)
        new_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])
        assert new_path_dict == old_path_dict

        self.redeploy_evc(evc1, True)

        evc_data = self.get_evc_data(evc1)
        new_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])
        assert new_path_dict != old_path_dict

    def test_015_redeploy_avoid_primary_path(self):
        """Test redeploying avoiding VLAN with primary_path"""
        primary_path = [
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:16"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:18:16"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:18:11"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:11"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:11:9"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:17:9"}
            }
        ]
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:20:59',
                               uni_z='00:00:00:00:00:00:00:17:56',
                               vlan_id=100,
                               primary_path=primary_path)
        
        time.sleep(10)

        evc_data = self.get_evc_data(evc1)
        old_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])

        self.redeploy_evc(evc1, False)

        evc_data = self.get_evc_data(evc1)
        new_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])
        assert new_path_dict == old_path_dict

        self.redeploy_evc(evc1, True)

        evc_data = self.get_evc_data(evc1)
        new_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])
        assert new_path_dict != old_path_dict

    def test_020_redeploy_avoid_vlan_static_path(self):
        """Test avoiding VLAN with static EVC"""
        primary_path = [
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:16"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:18:16"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:18:11"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:11"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:11:9"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:17:9"}
            }
        ]
        backup_path = [
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:17"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:13:17"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:13:2"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:2"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:11:9"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:17:9"}
            }
        ]
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:20:59',
                               uni_z='00:00:00:00:00:00:00:17:56',
                               vlan_id=100,
                               primary_path=primary_path,
                               backup_path=backup_path)
        
        time.sleep(10)

        evc_data = self.get_evc_data(evc1)
        old_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])

        self.redeploy_evc(evc1, False)

        evc_data = self.get_evc_data(evc1)
        new_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])
        assert new_path_dict == old_path_dict

        self.redeploy_evc(evc1, True)

        evc_data = self.get_evc_data(evc1)
        new_path_dict = self.get_link_vlan_dict_from_path(evc_data["current_path"])
        assert new_path_dict != old_path_dict