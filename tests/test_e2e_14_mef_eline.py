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
        assert response.status_code == 201, response.text
        return data['circuit_id']

    def get_evc_data(self, evc_id:str) -> dict:
        api_url = KYTOS_API + '/mef_eline/v2/evc/' + evc_id
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        return response.json()
    
    def redeploy_evc(self, evc_id, try_avoid_same_s_vlan=True):
        str_avoid_vlan = "false"
        if try_avoid_same_s_vlan:
            str_avoid_vlan = "true"
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/{evc_id}/redeploy?try_avoid_same_s_vlan={str_avoid_vlan}"
        response = requests.patch(api_url)
        assert response.status_code == 202, response.text
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

    def test_025_uni_link_up_static_path(self):
        """Test link_up in EVC UNI when the EVC is static and it does not have a
         current_path.
         - primary_path: Ampath7 - Ampath4 - Ampath1
         - backup_path: Ampath7 - SoL2 - Ampath1
         """
        primary_path = [
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:16"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:18:16"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:18:11"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:11"}
            },
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
        ]
        # UNI_Z is an NNI link with Ampath3: 00:17
        evc = self.create_evc(uni_a='00:00:00:00:00:00:00:20:59',
                               uni_z='00:00:00:00:00:00:00:11:9',
                               vlan_id=100,
                               primary_path=primary_path,
                               backup_path=backup_path)
        evc_content = self.get_evc_data(evc)
        assert evc_content["current_path"]
        assert evc_content["enabled"]
        assert evc_content["active"]

        Ampath1 = None
        switches:list = self.net.net.switches
        for switch in switches:
            if switch.name == "Ampath1":
                Ampath1 = switch
                break

        # Deployment to primary_path
        Ampath1.vsctl(f"set-controller {Ampath1.name} tcp:127.0.0.1:6654")
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/{evc}/redeploy"
        response = requests.patch(api_url)
        assert response.status_code == 409, response.text
        evc_content = self.get_evc_data(evc)
        assert not evc_content["current_path"]
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'down')
        Ampath1.vsctl(f"set-controller {Ampath1.name} tcp:127.0.0.1:6653")
        time.sleep(5)
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'up')
        time.sleep(5)
        evc_content = self.get_evc_data(evc)
        current_path = evc_content['current_path' ]
        primary_path = evc_content['primary_path']
        assert current_path and primary_path
        assert len(current_path) == len(primary_path)
        for current, primary in zip(current_path, primary_path):
            assert current["endpoint_a"]["id"] == primary["endpoint_a"]["id"]
            assert current["endpoint_b"]["id"] == primary["endpoint_b"]["id"]

        # Deployment to backup_path
        Ampath1.vsctl(f"set-controller {Ampath1.name} tcp:127.0.0.1:6654")
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/{evc}/redeploy"
        response = requests.patch(api_url)
        assert response.status_code == 409, response.text
        evc_content = self.get_evc_data(evc)
        assert not evc_content["current_path"]
        # Disable primary_path middle switch
        self.net.net.configLinkStatus('Ampath1', 'Ampath4', 'down')
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'down')
        Ampath1.vsctl(f"set-controller {Ampath1.name} tcp:127.0.0.1:6653")
        time.sleep(5)
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'up')
        time.sleep(5)
        evc_content = self.get_evc_data(evc)
        current_path = evc_content['current_path' ]
        backup_path = evc_content['backup_path']
        assert current_path and backup_path
        assert len(current_path) == len(backup_path)
        for current, backup in zip(current_path, backup_path):
            assert current["endpoint_a"]["id"] == backup["endpoint_a"]["id"]
            assert current["endpoint_b"]["id"] == backup["endpoint_b"]["id"]
