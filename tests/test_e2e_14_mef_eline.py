import time

import pytest
import requests

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api' % CONTROLLER

# Static paths between Ampath7 (00:20) and Ampath3 (00:17), as pairs of
# endpoints (interface IDs):
# - primary: Ampath7 - Ampath4 - Ampath1 - Ampath3
# - backup: Ampath7 - SoL2 - Ampath1 - Ampath3
PRIMARY_HOPS = [
    ("00:00:00:00:00:00:00:20:2", "00:00:00:00:00:00:00:18:2"),
    ("00:00:00:00:00:00:00:18:3", "00:00:00:00:00:00:00:11:3"),
    ("00:00:00:00:00:00:00:11:2", "00:00:00:00:00:00:00:17:2"),
]
BACKUP_HOPS = [
    ("00:00:00:00:00:00:00:20:1", "00:00:00:00:00:00:00:13:1"),
    ("00:00:00:00:00:00:00:13:4", "00:00:00:00:00:00:00:11:4"),
    ("00:00:00:00:00:00:00:11:2", "00:00:00:00:00:00:00:17:2"),
]
UNI_A = "00:00:00:00:00:00:00:20:16"
UNI_Z = "00:00:00:00:00:00:00:17:16"

# Link names (metadata.link_name) of the AmLight topology, indexed by link ID
LINK_NAMES = {
    # Ampath1 (00:11) - Ampath2 (00:12), port 1
    "b6dac3f36cb8e7dd72e3e49746b0ea19035fb9e7e57e117fe4bfbfb774851c4d": "MIA-Ampath1-Ampath2-01",
    # Ampath1 (00:11) - Ampath3 (00:17), port 2
    "48457fc028484b039a270942671a8edc3ee4f1878585c469fc2a609505330e4d": "MIA-Ampath1-Ampath3-01",
    # Ampath1 (00:11) - Ampath4 (00:18), port 3
    "73907de73db97005397e8d60739b2941e172d510827214c2f90152b3e97c0dee": "MIA-BCT-01",
    # Ampath1 (00:11) - SoL2 (00:13), port 4
    "f80f99546ad6e98baecc0f9811ae5eaa1801929bb7c07bcf315d417b25e8165a": "MIA-SAO-01",
    # Ampath1 (00:11) - SoL2 (00:13), port 5
    "324c05f85603619312bd9fc03e60ae98b35f1d0b9bb3884c49ad31df5f37dfd3": "MIA-SAO-02",
    # Ampath2 (00:12) - Ampath3 (00:17), port 3
    "a6369b70448345f1387a40c82d8767c9601cc26f7e88574875932c712349b7fc": "MIA-Ampath2-Ampath3-01",
    # Ampath2 (00:12) - Ampath5 (00:19), port 4
    "c528756e19b964d1355d0f9d4ae0ac46062f28b98b7c170a378f6996ba9765eb": "MIA-Ampath2-Ampath5-01",
    # Ampath2 (00:12) - AL2 (00:15), port 5
    "ed22424dfe2f40448fdedf899e38eb70883a535c3692e8f970c66a5d1790faee": "MIA-SCL-01",
    # Ampath2 (00:12) - SanJuan (00:14), port 6
    "a878bba10fc48bed370f1de720cdcb1d840f904614b87d3372f69956439fa7d0": "MIA-SJU-01",
    # Ampath4 (00:18) - Ampath5 (00:19), port 1
    "1f794712678b3795518c1e5eeacbcf35d99ed181135867a44a9f4950f058818f": "MIA-BCT-02",
    # Ampath4 (00:18) - Ampath7 (00:20), port 2
    "0f1e4e584f6df744797e8e44581a3378959cf4eed86cbe6f4dd16630358cfcd4": "MIA-BCT-03",
    # Ampath4 (00:18) - JAX1 (00:21), port 4
    "e59054b71c1f93b3ffb19eead5b5b3d040aa00d616071a39689ab2d1cf0936a4": "BCT-JAX-01",
    # Ampath5 (00:19) - JAX2 (00:22), port 5
    "b4e55d8b62413d5bfc7a98a65147ce6c0063e0d412f7cddf9d29a3cdbe2db9d0": "MIA-JAX-01",
    # Ampath7 (00:20) - SoL2 (00:13), port 1
    "49894a364d2dd2748939ad77bba497a3ecbf210372992bc4e3ae362068319025": "MIA-SAO-03",
    # SoL2 (00:13) - AL3 (00:16), port 2
    "988218dda2b6ff47d4a9d5f7b8cd1b27997e5173fadf75f95a736e76e2dc8262": "SAO-SCL-01",
    # AL3 (00:16) - AL2 (00:15), port 1
    "722ae748db21c07064d88efa0624d8b7fbaea92b52a9ea46e4b005e0dc9cbe15": "SCL-AndesLight2-AndesLight3-01",
    # AL2 (00:15) - SanJuan (00:14), port 3
    "ff64d5c598500606b83142d58003df121cf83736d050cc83d01e5de549a7b8a7": "SCL-SJU-01",
    # JAX1 (00:21) - JAX2 (00:22), port 1
    "a0a4108c8ec1c7c24f9dccc146fc09db0054e48164c75b4e16fd2b4ccf0aa3c4": "JAX-JAX1-JAX2-01",
}


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
        self.net.restart_kytos_clean()
        self.net.wait_kytos_links(status="UP")
        self.net.wait_kytos_buff_low_usage()
        time.sleep(5)
        # restart_kytos_clean() wipes the topology metadata, so the link
        # names have to be configured again before each test
        self.configure_link_names()

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name='amlight')
        cls.net.start(start_controller=False)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def wait_sdntrace_result(self, trace_id:int, timeout=30):
        """Wait until sdntrace finishes."""
        wait_count = 0
        while wait_count < timeout:
            try:
                api_url = KYTOS_API + '/amlight/sdntrace/v1/trace'
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
    
    def do_sdntrace(self, dpid:str, port:int, vlan:int) -> int:
        """Do a trace through sdntrace and return the trace_id"""
        payload = {
            "trace": {
                "switch": {"dpid": dpid, "in_port": port},
                "eth": {"dl_vlan": vlan}
            }
        }
        api_url = KYTOS_API + '/amlight/sdntrace/v1/trace'
        response = requests.put(api_url, json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        return data["result"]["trace_id"]

    def delete_evc(self, circuit_id) -> dict:
        """Delete an EVC."""
        api_url = f"{KYTOS_API}/kytos/mef_eline/v2/evc/{circuit_id}"
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        return data

    def create_evc(
        self,
        uni_a="00:00:00:00:00:00:00:01:1",
        uni_z="00:00:00:00:00:00:00:02:1",
        vlan_id=100,
        primary_path=None,
        backup_path=None,
        max_retries=5,
        wait_time=1,
        **kwargs,
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
        if kwargs:
            payload.update(kwargs)
        times = 0
        while times < max_retries:
            api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/'
            response = requests.post(api_url, json=payload)
            data = response.json()
            assert response.status_code == 201, response.text
            if data["deployed"] is True:
                break
            time.sleep(wait_time)
            self.delete_evc(data["circuit_id"])
            time.sleep(wait_time)
            times += 1
        else:
            msg = "Time out to create EVC which deploys"
            raise Exception(msg)

        return data['circuit_id']

    def get_evc_data(self, evc_id:str) -> dict:
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/' + evc_id
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        return response.json()
    
    def redeploy_evc(self, evc_id, try_avoid_same_s_vlan=True):
        str_avoid_vlan = "false"
        if try_avoid_same_s_vlan:
            str_avoid_vlan = "true"
        api_url = f"{KYTOS_API}/kytos/mef_eline/v2/evc/{evc_id}/redeploy?try_avoid_same_s_vlan={str_avoid_vlan}"
        response = requests.patch(api_url)
        assert response.status_code == 202, response.text
        time.sleep(10)

    def set_link_name(self, link_id: str, link_name: str) -> None:
        """Set metadata.link_name on a topology link."""
        api_url = f"{KYTOS_API}/kytos/topology/v3/links/{link_id}/metadata"
        response = requests.post(api_url, json={"link_name": link_name})
        assert response.status_code == 201, response.text

    def configure_link_names(self) -> dict:
        """Configure metadata.link_name on all topology links, according to
        LINK_NAMES.

        The result is saved on self.links, indexed by the pair of endpoints
        (interface IDs), so that tests can get the link ID / name from the
        same endpoints used in the regular endpoint_a/endpoint_b form.
        """
        api_url = KYTOS_API + '/kytos/topology/v3/links'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        links = response.json()["links"]
        assert set(links) == set(LINK_NAMES), str(set(links) ^ set(LINK_NAMES))
        self.links = {}
        for link_id, link in links.items():
            link_name = LINK_NAMES[link_id]
            self.set_link_name(link_id, link_name)
            key = frozenset(
                (link["endpoint_a"]["id"], link["endpoint_b"]["id"])
            )
            self.links[key] = {"id": link_id, "name": link_name}
        return self.links

    def rename_links(self, hops: list, prefix="NEW-") -> None:
        """Rename the links of the hops (pairs of endpoints) on topology."""
        for hop in hops:
            link = self.links[frozenset(hop)]
            link["name"] = prefix + link["name"]
            self.set_link_name(link["id"], link["name"])
        time.sleep(2)

    @staticmethod
    def path_by_endpoints(hops: list) -> list:
        """Path in the endpoint_a/endpoint_b form from pairs of endpoints."""
        return [
            {"endpoint_a": {"id": hop[0]}, "endpoint_b": {"id": hop[1]}}
            for hop in hops
        ]

    def path_by_id(self, hops: list) -> list:
        """Path as a list of link IDs from pairs of endpoints."""
        return [self.links[frozenset(hop)]["id"] for hop in hops]

    def path_by_name(self, hops: list) -> list:
        """Path as a list of link names from pairs of endpoints."""
        return [self.links[frozenset(hop)]["name"] for hop in hops]

    @staticmethod
    def assert_path(path: list, hops: list) -> None:
        """Assert that an EVC path (primary_path, backup_path, current_path)
        goes through the expected hops (pairs of endpoints), in order."""
        actual = [
            frozenset((link["endpoint_a"]["id"], link["endpoint_b"]["id"]))
            for link in path
        ]
        assert actual == [frozenset(hop) for hop in hops], str(path)

    def post_evc(self, vlan_id=100, **kwargs) -> requests.Response:
        """Request an EVC creation (static, Ampath7 <-> Ampath3), returning
        the raw response."""
        payload = {
            "name": "Vlan_%s" % vlan_id,
            "enabled": True,
            "dynamic_backup_path": False,
            "uni_a": {
                "interface_id": UNI_A,
                "tag": {"tag_type": "vlan", "value": vlan_id}
            },
            "uni_z": {
                "interface_id": UNI_Z,
                "tag": {"tag_type": "vlan", "value": vlan_id}
            },
        }
        payload.update(kwargs)
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/'
        return requests.post(api_url, json=payload)

    def patch_evc(self, evc_id: str, **kwargs) -> requests.Response:
        """Request an EVC update, returning the raw response."""
        api_url = f"{KYTOS_API}/kytos/mef_eline/v2/evc/{evc_id}"
        return requests.patch(api_url, json=kwargs)

    def check_static_evc(self, evc_id: str) -> None:
        """Check that a static EVC between Ampath7 and Ampath3 has the
        expected primary and backup paths (whichever form was used to request
        them), that it is deployed on the primary path, that it moves to the
        backup path when a primary link goes down, and that it goes back to
        the primary path when the link is restored.
        """
        evc_data = self.get_evc_data(evc_id)
        assert evc_data["enabled"]
        assert evc_data["active"]
        self.assert_path(evc_data["primary_path"], PRIMARY_HOPS)
        self.assert_path(evc_data["backup_path"], BACKUP_HOPS)
        self.assert_path(evc_data["current_path"], PRIMARY_HOPS)

        # Link down on primary path (Ampath7 - Ampath4): move to backup
        self.net.net.configLinkStatus('Ampath7', 'Ampath4', 'down')
        self.net.wait_kytos_links('Ampath7', 'Ampath4', status="DOWN")
        time.sleep(10)
        evc_data = self.get_evc_data(evc_id)
        assert evc_data["active"]
        self.assert_path(evc_data["current_path"], BACKUP_HOPS)

        # Link up again: back to the primary path
        self.net.net.configLinkStatus('Ampath7', 'Ampath4', 'up')
        self.net.wait_kytos_links('Ampath7', 'Ampath4', status="UP")
        time.sleep(10)
        evc_data = self.get_evc_data(evc_id)
        assert evc_data["active"]
        self.assert_path(evc_data["primary_path"], PRIMARY_HOPS)
        self.assert_path(evc_data["backup_path"], BACKUP_HOPS)
        self.assert_path(evc_data["current_path"], PRIMARY_HOPS)

    def get_link_vlan_dict_from_path(self, path: dict) -> dict[str, int]:
        link_vlan_dict = {}
        for link in path:
            link_vlan_dict[link["id"]] = link["metadata"]["s_vlan"]["value"]
        return link_vlan_dict

    def test_005_create_evc_on_nni(self):
        """Test to evaluate how mef_eline will behave when the uni is actually
        an NNI."""
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/'
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:16:2',
                               uni_z='00:00:00:00:00:00:00:11:1',
                               vlan_id=100,
                               max_paths=10)

        time.sleep(10)

        # It verifies EVC's data
        response = requests.get(api_url + evc1)
        data = response.json()
        assert data['enabled'] == True
        assert data['active'] == True

        trace_id = self.do_sdntrace('00:00:00:00:00:00:00:16', 2, 100)
        result = self.wait_sdntrace_result(trace_id)

        assert len(result) == 7, str(result)
        assert result[0]["dpid"] == "00:00:00:00:00:00:00:16"
        assert result[1]["dpid"] == "00:00:00:00:00:00:00:15"
        assert result[2]["dpid"] == "00:00:00:00:00:00:00:12"
        assert result[3]["dpid"] == "00:00:00:00:00:00:00:17"
        assert result[4]["dpid"] == "00:00:00:00:00:00:00:11"
        assert result[5]["dpid"] == "00:00:00:00:00:00:00:12"
        assert result[6]["type"] == "last"

    def test_010_redeploy_avoid_vlan(self):
        """Test if dynamic EVC takes different VLAN when redeploying."""
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:20:16',
                               uni_z='00:00:00:00:00:00:00:17:16',
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
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:2"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:18:2"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:18:3"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:3"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:11:2"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:17:2"}
            }
        ]
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:20:16',
                               uni_z='00:00:00:00:00:00:00:17:16',
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
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:2"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:18:2"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:18:3"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:3"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:11:2"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:17:2"}
            }
        ]
        backup_path = [
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:1"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:13:1"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:13:4"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:4"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:11:2"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:17:2"}
            }
        ]
        evc1 = self.create_evc(uni_a='00:00:00:00:00:00:00:20:16',
                               uni_z='00:00:00:00:00:00:00:17:16',
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
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:2"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:18:2"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:18:3"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:3"}
            },
        ]
        backup_path = [
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:20:1"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:13:1"}
            },
            {
                "endpoint_a": {"id": "00:00:00:00:00:00:00:13:4"},
                "endpoint_b": {"id": "00:00:00:00:00:00:00:11:4"}
            },
        ]
        # UNI_Z is an NNI link with Ampath3: 00:17
        evc = self.create_evc(uni_a='00:00:00:00:00:00:00:20:16',
                               uni_z='00:00:00:00:00:00:00:11:2',
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
        api_url = f"{KYTOS_API}/kytos/mef_eline/v2/evc/{evc}/redeploy"
        response = requests.patch(api_url)
        assert response.status_code == 409, response.text
        evc_content = self.get_evc_data(evc)
        assert not evc_content["current_path"]
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'down')
        Ampath1.vsctl(f"set-controller {Ampath1.name} tcp:127.0.0.1:6653")
        self.net.wait_switches_connect()
        self.net.wait_kytos_links('Ampath1', 'Ampath3', status="DOWN")
        time.sleep(5)
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'up')
        self.net.wait_kytos_links(status="UP")
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
        api_url = f"{KYTOS_API}/kytos/mef_eline/v2/evc/{evc}/redeploy"
        response = requests.patch(api_url)
        assert response.status_code == 409, response.text
        evc_content = self.get_evc_data(evc)
        assert not evc_content["current_path"]
        # Disable primary_path middle switch
        self.net.net.configLinkStatus('Ampath1', 'Ampath4', 'down')
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'down')
        Ampath1.vsctl(f"set-controller {Ampath1.name} tcp:127.0.0.1:6653")
        time.sleep(10)
        self.net.net.configLinkStatus('Ampath1', 'Ampath3', 'up')
        self.net.wait_kytos_links('Ampath1', 'Ampath3', status="UP")
        time.sleep(5)
        evc_content = self.get_evc_data(evc)
        current_path = evc_content['current_path' ]
        backup_path = evc_content['backup_path']
        assert current_path and backup_path
        assert len(current_path) == len(backup_path)
        for current, backup in zip(current_path, backup_path):
            assert current["endpoint_a"]["id"] == backup["endpoint_a"]["id"]
            assert current["endpoint_b"]["id"] == backup["endpoint_b"]["id"]
        self.net.reconnect_switches()

    def test_030_EVC_path_disjointness(self):
        """Testing disjointness by expecting a specific failover_path."""
        self.net.net.configLinkStatus('Ampath1', 'SoL2', 'down')
        self.net.wait_kytos_links('Ampath1', 'SoL2', status="DOWN")
        time.sleep(5)

        evc = self.create_evc(uni_a='00:00:00:00:00:00:00:15:16',
                       uni_z='00:00:00:00:00:00:00:11:16',
                       vlan_id=100)
        time.sleep(10)
        evc_content = self.get_evc_data(evc)

        failover_path = []
        for _path in evc_content["failover_path"]:
            failover_path.append({
                "endpoint_a": {"id": _path["endpoint_a"]["id"]},
                "endpoint_b": {"id": _path["endpoint_b"]["id"]}
            })

        expected_failover_path = [
            {"endpoint_a": {"id": "00:00:00:00:00:00:00:15:1"},
             "endpoint_b": {"id": "00:00:00:00:00:00:00:16:1"}},
            {"endpoint_a": {"id": "00:00:00:00:00:00:00:13:2"},
             "endpoint_b": {"id": "00:00:00:00:00:00:00:16:2"}},
            {"endpoint_a": {"id": "00:00:00:00:00:00:00:13:1"},
             "endpoint_b": {"id": "00:00:00:00:00:00:00:20:1"}},
            {"endpoint_a": {"id": "00:00:00:00:00:00:00:18:2"},
             "endpoint_b": {"id": "00:00:00:00:00:00:00:20:2"}},
            {"endpoint_a": {"id": "00:00:00:00:00:00:00:11:3"},
             "endpoint_b": {"id": "00:00:00:00:00:00:00:18:3"}},
        ]
        assert len(failover_path) == len(expected_failover_path)
        assert failover_path == expected_failover_path

    def test_035_static_path_by_link_name(self):
        """Test creating an EVC with primary_path and backup_path given by
        link name (metadata.link_name)."""
        primary_path = self.path_by_name(PRIMARY_HOPS)
        backup_path = self.path_by_name(BACKUP_HOPS)
        assert all(isinstance(name, str) for name in primary_path + backup_path)
        evc = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=100,
                              primary_path=primary_path,
                              backup_path=backup_path)
        time.sleep(10)
        self.check_static_evc(evc)

    def test_040_static_path_by_link_id(self):
        """Test creating an EVC with primary_path and backup_path given by
        link ID."""
        evc = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=100,
                              primary_path=self.path_by_id(PRIMARY_HOPS),
                              backup_path=self.path_by_id(BACKUP_HOPS))
        time.sleep(10)
        self.check_static_evc(evc)

    def test_045_static_path_mixed_forms(self):
        """Test creating EVCs mixing the three forms on the same path
        (endpoints, link ID and link name) and using different forms on
        primary_path and backup_path."""
        # the three forms mixed on the same path
        primary_path = [
            self.path_by_endpoints(PRIMARY_HOPS)[0],
            self.path_by_id(PRIMARY_HOPS)[1],
            self.path_by_name(PRIMARY_HOPS)[2],
        ]
        backup_path = [
            self.path_by_name(BACKUP_HOPS)[0],
            self.path_by_endpoints(BACKUP_HOPS)[1],
            self.path_by_id(BACKUP_HOPS)[2],
        ]
        evc1 = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=100,
                               primary_path=primary_path,
                               backup_path=backup_path)

        # one form for each path
        evc2 = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=101,
                               primary_path=self.path_by_name(PRIMARY_HOPS),
                               backup_path=self.path_by_id(BACKUP_HOPS))
        evc3 = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=102,
                               primary_path=self.path_by_endpoints(PRIMARY_HOPS),
                               backup_path=self.path_by_name(BACKUP_HOPS))
        time.sleep(10)

        # All of them have to be stored exactly the same way
        for evc in (evc2, evc3):
            evc_data = self.get_evc_data(evc)
            assert evc_data["enabled"]
            assert evc_data["active"]
            self.assert_path(evc_data["primary_path"], PRIMARY_HOPS)
            self.assert_path(evc_data["backup_path"], BACKUP_HOPS)
            self.assert_path(evc_data["current_path"], PRIMARY_HOPS)
        self.check_static_evc(evc1)
        for evc in (evc2, evc3):
            evc_data = self.get_evc_data(evc)
            assert evc_data["active"]
            self.assert_path(evc_data["current_path"], PRIMARY_HOPS)

    def test_050_update_static_path_by_link_name_and_id(self):
        """Test updating (PATCH) the primary_path and backup_path of an
        existing EVC using link names, link IDs and mixed forms."""
        # EVC created with the paths inverted, using the endpoints
        evc = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=100,
                              primary_path=self.path_by_endpoints(BACKUP_HOPS),
                              backup_path=self.path_by_endpoints(PRIMARY_HOPS))
        time.sleep(10)
        evc_data = self.get_evc_data(evc)
        self.assert_path(evc_data["current_path"], BACKUP_HOPS)

        # Update by name (primary) and by ID (backup)
        response = self.patch_evc(
            evc,
            primary_path=self.path_by_name(PRIMARY_HOPS),
            backup_path=self.path_by_id(BACKUP_HOPS),
        )
        assert response.status_code == 200, response.text
        time.sleep(10)
        self.check_static_evc(evc)

        # Update mixing forms: invert the paths again
        primary_path = [
            self.path_by_id(BACKUP_HOPS)[0],
            self.path_by_name(BACKUP_HOPS)[1],
            self.path_by_endpoints(BACKUP_HOPS)[2],
        ]
        response = self.patch_evc(
            evc,
            primary_path=primary_path,
            backup_path=self.path_by_name(PRIMARY_HOPS),
        )
        assert response.status_code == 200, response.text
        time.sleep(10)
        evc_data = self.get_evc_data(evc)
        assert evc_data["active"]
        self.assert_path(evc_data["primary_path"], BACKUP_HOPS)
        self.assert_path(evc_data["backup_path"], PRIMARY_HOPS)
        self.assert_path(evc_data["current_path"], BACKUP_HOPS)

    def test_055_rename_link_name(self):
        """Test renaming the links on topology: EVCs previously created by
        link name must continue working (no side effects, since the names
        are resolved only at creation/update), the old names are no longer
        accepted and new EVCs can be created using the new names."""
        old_primary_path = self.path_by_name(PRIMARY_HOPS)
        old_backup_path = self.path_by_name(BACKUP_HOPS)
        evc1 = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=100,
                               primary_path=old_primary_path,
                               backup_path=old_backup_path)
        time.sleep(10)
        evc_data = self.get_evc_data(evc1)
        self.assert_path(evc_data["current_path"], PRIMARY_HOPS)

        # Rename all links used by the EVC (one link is shared by both paths)
        self.rename_links(set(PRIMARY_HOPS + BACKUP_HOPS))
        new_primary_path = self.path_by_name(PRIMARY_HOPS)
        new_backup_path = self.path_by_name(BACKUP_HOPS)
        assert not set(old_primary_path + old_backup_path) & set(
            new_primary_path + new_backup_path
        )

        # The existing EVC is not affected: same paths, still active, it can
        # be redeployed and it still converges on link down / link up
        evc_data = self.get_evc_data(evc1)
        assert evc_data["enabled"]
        assert evc_data["active"]
        self.assert_path(evc_data["primary_path"], PRIMARY_HOPS)
        self.assert_path(evc_data["backup_path"], BACKUP_HOPS)
        self.assert_path(evc_data["current_path"], PRIMARY_HOPS)
        self.redeploy_evc(evc1)
        self.check_static_evc(evc1)

        # The old names are no longer accepted (create and update)
        response = self.post_evc(vlan_id=101,
                                 primary_path=old_primary_path,
                                 backup_path=old_backup_path)
        assert response.status_code == 400, response.text
        assert old_primary_path[0] in response.json()["description"]
        response = self.patch_evc(evc1, backup_path=old_backup_path)
        assert response.status_code == 400, response.text
        evc_data = self.get_evc_data(evc1)
        assert evc_data["active"]
        self.assert_path(evc_data["primary_path"], PRIMARY_HOPS)
        self.assert_path(evc_data["backup_path"], BACKUP_HOPS)

        # New EVC using the new names
        evc2 = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=101,
                               primary_path=new_primary_path,
                               backup_path=new_backup_path)
        time.sleep(10)
        self.check_static_evc(evc2)

        # The existing EVC can also be updated using the new names
        response = self.patch_evc(evc1,
                                  primary_path=new_backup_path,
                                  backup_path=new_primary_path)
        assert response.status_code == 200, response.text
        time.sleep(10)
        evc_data = self.get_evc_data(evc1)
        assert evc_data["active"]
        self.assert_path(evc_data["primary_path"], BACKUP_HOPS)
        self.assert_path(evc_data["backup_path"], PRIMARY_HOPS)
        self.assert_path(evc_data["current_path"], BACKUP_HOPS)

    def test_060_static_path_invalid_link_reference(self):
        """Test that unknown and ambiguous (duplicated) link names are
        rejected on EVC creation and update, while the link ID can still be
        used for links with a duplicated name."""
        evc1 = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=100,
                               primary_path=self.path_by_name(PRIMARY_HOPS),
                               backup_path=self.path_by_name(BACKUP_HOPS))
        time.sleep(10)

        # Unknown link name
        primary_path = self.path_by_name(PRIMARY_HOPS)
        primary_path[1] = "no-such-link"
        response = self.post_evc(vlan_id=101, primary_path=primary_path)
        assert response.status_code == 400, response.text
        assert "no-such-link" in response.json()["description"]
        response = self.patch_evc(evc1, primary_path=primary_path)
        assert response.status_code == 400, response.text

        # Ambiguous link name: same name on one link of each path
        dup_links = [
            self.links[frozenset(PRIMARY_HOPS[0])],
            self.links[frozenset(BACKUP_HOPS[0])],
        ]
        for link in dup_links:
            link["name"] = "DUP-LINK"
            self.set_link_name(link["id"], link["name"])
        time.sleep(2)
        response = self.post_evc(vlan_id=101,
                                 primary_path=self.path_by_name(PRIMARY_HOPS),
                                 backup_path=self.path_by_name(BACKUP_HOPS))
        assert response.status_code == 400, response.text
        description = response.json()["description"]
        assert "DUP-LINK" in description
        for link in dup_links:
            assert link["id"] in description
        response = self.patch_evc(evc1,
                                  backup_path=self.path_by_name(BACKUP_HOPS))
        assert response.status_code == 400, response.text

        # No EVC was created and the existing one remains unchanged
        api_url = KYTOS_API + '/kytos/mef_eline/v2/evc/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        assert list(response.json()) == [evc1]
        evc_data = self.get_evc_data(evc1)
        assert evc_data["active"]
        self.assert_path(evc_data["primary_path"], PRIMARY_HOPS)
        self.assert_path(evc_data["backup_path"], BACKUP_HOPS)
        self.assert_path(evc_data["current_path"], PRIMARY_HOPS)

        # The link ID is still accepted for the links with duplicated name
        primary_path = self.path_by_name(PRIMARY_HOPS)
        primary_path[0] = dup_links[0]["id"]
        backup_path = self.path_by_name(BACKUP_HOPS)
        backup_path[0] = dup_links[1]["id"]
        evc2 = self.create_evc(uni_a=UNI_A, uni_z=UNI_Z, vlan_id=101,
                               primary_path=primary_path,
                               backup_path=backup_path)
        time.sleep(10)
        self.check_static_evc(evc2)
