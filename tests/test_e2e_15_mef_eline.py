import json
import time

import requests

from tests.helpers import NetworkTest

CONTROLLER = "127.0.0.1"
KYTOS_API = "http://%s:8181/api/kytos" % CONTROLLER


class TestE2EMefEline:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="ring")
        cls.net.start()
        cls.net.restart_kytos_clean()
        cls.net.wait_switches_connect()
        time.sleep(5)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def restart(self, _clean_config=False, _enable_all=True):
        self.net.start_controller(clean_config=_clean_config, enable_all=_enable_all)
        self.net.wait_switches_connect()
        # Wait a few seconds to kytos execute LLDP
        time.sleep(10)

    def add_topology_metadata(self):
        """Add topology metadata."""
        links_metadata = {
            "78282c4d5b579265f04ebadc4405ca1b49628eb1d684bb45e5d0607fa8b713d0": {
                "link_name": "s1-eth3-s2-eth2",
                "ownership": "red",
            },
            "c8b55359990f89a5849813dc348d30e9e1f991bad1dcb7f82112bd35429d9b07": {
                "link_name": "s1-eth4-s3-eth3",
                "ownership": "blue",
            },
            "4d42dc0852278accac7d9df15418f6d921db160b13d674029a87cef1b5f67f30": {
                "link_name": "s2-eth3-s3-eth2",
                "ownership": "red",
            },
        }

        for link_id, metadata in links_metadata.items():
            api_url = f"{KYTOS_API}/topology/v3/links/{link_id}/metadata"
            response = requests.post(
                api_url,
                data=json.dumps(metadata),
                headers={"Content-type": "application/json"},
            )
            assert response.status_code == 201, response.text
        return links_metadata

    def create_evc(
        self,
        uni_a="00:00:00:00:00:00:00:01:1",
        uni_z="00:00:00:00:00:00:00:03:1",
        vlan_id=100,
        primary_constraints=None,
        secondary_constraints=None,
    ):
        payload = {
            "name": "Vlan_%s" % vlan_id,
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {"interface_id": uni_a, "tag": {"tag_type": 1, "value": vlan_id}},
            "uni_z": {"interface_id": uni_z, "tag": {"tag_type": "vlan", "value": vlan_id}},
        }
        if primary_constraints:
            payload.update({"primary_constraints": primary_constraints})
        if secondary_constraints:
            payload.update({"secondary_constraints": secondary_constraints})
        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()
        return data["circuit_id"]

    def update_evc(self, circuit_id: str, **kwargs) -> dict:
        """Update an EVC."""
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/{circuit_id}"
        response = requests.patch(api_url, json=kwargs)
        assert response.status_code == 200, response.text
        data = response.json()
        return data

    def delete_evc(self, circuit_id) -> dict:
        """Delete an EVC."""
        api_url = f"{KYTOS_API}/mef_eline/v2/evc/{circuit_id}"
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        return data

    def test_002_update_uni(self):
        """Test when a uni is updated"""
        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        evc_id = self.create_evc(
            uni_a="00:00:00:00:00:00:00:01:1",
            uni_z="00:00:00:00:00:00:00:03:1",
            vlan_id=100,
        )
        time.sleep(10)
        response = requests.get(api_url + evc_id)
        data = response.json()
        assert data["enabled"]
        assert data["active"]

        # Update the EVC switching the uni_z to switch 2
        self.update_evc(
            evc_id,
            uni_z={
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {"tag_type": 1, "value": 100}
            },
        )
        time.sleep(10)
        response = requests.get(api_url + evc_id)
        data = response.json()
        assert data["uni_z"]["interface_id"] == "00:00:00:00:00:00:00:02:1"

        h11, h2 = self.net.net.get('h11', 'h2')
        h11.cmd('ip link add link %s name vlan100 type vlan id 100' % (h11.intfNames()[0]))
        h11.cmd('ip link set up vlan100')
        h11.cmd('ip addr add 100.0.0.11/24 dev vlan100')
        h2.cmd('ip link add link %s name vlan100 type vlan id 100' % (h2.intfNames()[0]))
        h2.cmd('ip link set up vlan100')
        h2.cmd('ip addr add 100.0.0.2/24 dev vlan100')
        result = h11.cmd('ping -c1 100.0.0.2')
        assert ', 0% packet loss,' in result

    def test_001_create_update_with_constraints(self):
        """Test to create -> update with constraints."""

        links_metadata = self.add_topology_metadata()
        blue_link_ids, red_link_ids = set(), set()
        for k, v in links_metadata.items():
            if "ownership" not in v:
                continue
            if v["ownership"] == "blue":
                blue_link_ids.add(k)
            if v["ownership"] == "red":
                red_link_ids.add(k)

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        evc_id = self.create_evc(
            uni_a="00:00:00:00:00:00:00:01:1",
            uni_z="00:00:00:00:00:00:00:03:1",
            vlan_id=100,
            primary_constraints={"mandatory_metrics": {"ownership": "red"}},
            secondary_constraints={
                "spf_attribute": "hop",
                "mandatory_metrics": {"ownership": "blue"}
            },
        )

        time.sleep(10)
        response = requests.get(api_url + evc_id)
        data = response.json()
        assert data["enabled"]
        assert data["active"]
        assert data["current_path"], data["current_path"]
        assert data["failover_path"], data["failover_path"]
        assert data["primary_constraints"] == {
            "mandatory_metrics": {"ownership": "red"}
        }
        assert data["secondary_constraints"] == {
            "mandatory_metrics": {"ownership": "blue"},
            "spf_attribute": "hop",
        }

        # assert current_path and failover_path expected paths
        current_path_ids = {link["id"] for link in data["current_path"]}
        failover_path_ids = {link["id"] for link in data["failover_path"]}

        assert current_path_ids == red_link_ids, current_path_ids
        assert failover_path_ids == blue_link_ids, failover_path_ids

        # update the EVC switching the primary and secondary constraints
        self.update_evc(
            evc_id,
            primary_constraints={
                "mandatory_metrics": {"ownership": "blue"}
            },
            secondary_constraints={
                "mandatory_metrics": {"ownership": "red"},
                "spf_attribute": "hop",
            },
        )
        time.sleep(10)
        response = requests.get(api_url + evc_id)
        data = response.json()
        assert data["enabled"]
        assert data["active"]
        assert data["current_path"], data["current_path"]
        assert data["failover_path"], data["failover_path"]
        assert data["primary_constraints"] == {
            "mandatory_metrics": {"ownership": "blue"}
        }
        assert data["secondary_constraints"] == {
            "mandatory_metrics": {"ownership": "red"},
            "spf_attribute": "hop",
        }

        # assert current_path and failover_path expected paths
        current_path_ids = {link["id"] for link in data["current_path"]}
        failover_path_ids = {link["id"] for link in data["failover_path"]}
        assert current_path_ids == blue_link_ids, current_path_ids
        assert failover_path_ids == red_link_ids, failover_path_ids

    def test_003_evc_vlan_allocation(self):
        """Test patch an evc with a duplicated tag value"""
        evc_1 = {
            "name": "EVC_1",
            "enabled": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:02:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:02:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=evc_1)
        assert response.status_code == 201, response.text
        assert 'circuit_id' in response.json()

        # Verify if EVC tag has been allocated
        topo_url = KYTOS_API + "/topology/v3/interfaces/tag_ranges"
        response = requests.get(topo_url)
        data = response.json()
        actual = data["00:00:00:00:00:00:00:02:1"]["available_tags"]["vlan"]
        actual_tr = data["00:00:00:00:00:00:00:02:1"]["tag_ranges"]["vlan"]
        expected = [[1, 99], [101, 3798], [3800, 4095]]
        expected_tr = [[1, 4095]]
        assert actual == expected
        assert actual_tr == expected_tr
        actual = data["00:00:00:00:00:00:00:02:2"]["available_tags"]["vlan"]
        actual_tr = data["00:00:00:00:00:00:00:02:1"]["tag_ranges"]["vlan"]
        assert actual == expected
        assert actual_tr == expected_tr

        evc_2 = {
            "name": "EVC_2",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": 200},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": 200},
                "interface_id": "00:00:00:00:00:00:00:02:2",
            }
        }
        response = requests.post(api_url, json=evc_2)
        assert response.status_code == 201, response.text
        data = response.json()
        assert 'circuit_id' in data
        evc_2_id = data["circuit_id"]

        # Verify if EVC tag has been allocated
        topo_url = KYTOS_API + "/topology/v3/interfaces/tag_ranges"
        response = requests.get(topo_url)
        data = response.json()
        actual = data["00:00:00:00:00:00:00:02:2"]["available_tags"]["vlan"]
        expected = [[1, 99], [101, 199], [201, 3798], [3800, 4095]]
        actual_tr = data["00:00:00:00:00:00:00:02:2"]["tag_ranges"]["vlan"]
        expected_tr = [[1, 4095]]
        assert actual == expected
        assert actual_tr == expected_tr

        actual = data["00:00:00:00:00:00:00:01:1"]["available_tags"]["vlan"]
        expected = [[1, 199], [201, 3798], [3800, 4095]]
        actual_tr = data["00:00:00:00:00:00:00:01:1"]["tag_ranges"]["vlan"]
        assert actual == expected
        assert actual_tr == expected_tr

        actual = data["00:00:00:00:00:00:00:02:1"]["available_tags"]["vlan"]
        expected = [[1, 99], [101, 3798], [3800, 4095]]
        actual_tr = data["00:00:00:00:00:00:00:02:1"]["tag_ranges"]["vlan"]
        assert actual == expected
        assert actual_tr == expected_tr

        # Patch EVC with used tag value
        payload = {
            "uni_a": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:02:1",
            },
        }
        response = requests.patch(api_url+evc_2_id, json=payload)
        assert response.status_code == 400, response.text

        # Verify that patch has not allocated a tag
        topo_url = KYTOS_API + "/topology/v3/interfaces/tag_ranges"
        response = requests.get(topo_url)
        data = response.json()
        actual = data["00:00:00:00:00:00:00:02:1"]["available_tags"]["vlan"]
        expected = [[1, 99], [101, 3798], [3800, 4095]]
        actual_tr = data["00:00:00:00:00:00:00:02:1"]["tag_ranges"]["vlan"]
        assert actual == expected
        assert actual_tr == expected_tr

        actual = data["00:00:00:00:00:00:00:02:2"]["available_tags"]["vlan"]
        expected = [[1, 99], [101, 199], [201, 3798], [3800, 4095]]
        actual_tr = data["00:00:00:00:00:00:00:02:2"]["tag_ranges"]["vlan"]
        assert actual == expected
        assert actual_tr == expected_tr

    def test_004_tag_restriction(self):
        """Test restrict tag range"""
        payload = {
            "name": "evc_1",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": 200},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": 200},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text

        intf_id = '00:00:00:00:00:00:00:01:1'
        api_url = KYTOS_API + f'/topology/v3/interfaces/{intf_id}/tag_ranges'
        response = requests.get(api_url)
        data = response.json()
        assert response.status_code == 200, response.text

        expected = [[1, 199], [201, 3798], [3800, 4095]]
        assert expected == data[intf_id]["available_tags"]["vlan"]

        # Ignoring EVC tag
        payload = {
            "tag_type": "vlan",
            "tag_ranges": [[1, 180], [300, 3500]]
        }
        api_url = KYTOS_API + f'/topology/v3/interfaces/{intf_id}/tag_ranges'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 400, response.text

        # Every used tag included
        payload = {
            "tag_type": "vlan",
            "tag_ranges": [[200, 4000]]
        }
        api_url = KYTOS_API + f'/topology/v3/interfaces/{intf_id}/tag_ranges'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 200, response.text

        api_url = KYTOS_API + f'/topology/v3/interfaces/{intf_id}/tag_ranges'
        response = requests.get(api_url)
        data = response.json()
        assert response.status_code == 200, response.text

        expected = [[201, 3798], [3800, 4000]]
        assert expected == data[intf_id]["available_tags"]["vlan"]

        # Trying EVC with not available tag
        payload = {
            "name": "evc_2",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": 100},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 400, response.text

        # EVC with available tag
        payload = {
            "name": "evc_2",
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "tag": {"tag_type": 1, "value": 300},
                "interface_id": "00:00:00:00:00:00:00:01:1",
            },
            "uni_z": {
                "tag": {"tag_type": 1, "value": 300},
                "interface_id": "00:00:00:00:00:00:00:01:2",
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text

        intf_id = '00:00:00:00:00:00:00:01:1'
        api_url = KYTOS_API + f'/topology/v3/interfaces/{intf_id}/tag_ranges'
        response = requests.get(api_url)
        data = response.json()
        assert response.status_code == 200, response.text

        expected = [[201, 299], [301, 3798], [3800, 4000]]
        assert expected == data[intf_id]["available_tags"]["vlan"]
