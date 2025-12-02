import hashlib
import json
import time
import random
from collections import defaultdict

import requests

from .helpers import NetworkTest

CONTROLLER = "127.0.0.1"
KYTOS_API = "http://%s:8181/api/kytos" % CONTROLLER

class LinkID(str):
    """Link Identifier"""

    def __new__(cls, interface_a, interface_b):
        raw_str = ":".join(sorted((interface_a, interface_b)))
        digest = hashlib.sha256(raw_str.encode('utf-8')).hexdigest()
        return super().__new__(cls, digest)

    def __init__(self, interface_a, interface_b):
        self.interfaces = tuple(sorted((interface_a, interface_b)))
        super().__init__()

    def __getnewargs__(self):
        """To make sure it's pickleable"""
        return self.interfaces


class TestE2EMefEline:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Since some tests may set a link to down state, we should reset
        # the link state to up (for all links)
        self.net.config_all_links_up()
        # Start the controller with all elements enabled and clean database
        self.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="multi")
        cls.net.start()
        cls.net.restart_kytos_clean()
        cls.net.wait_switches_connect()
        time.sleep(5)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_001_link_down(self):
        """Test link down behaviour."""

        self.net.net.configLinkStatus("s1", "s6", "down")
        self.net.net.configLinkStatus("s5", "s6", "down")

        time.sleep(5)

        payload = {
            "name": "Link Down Test 001",
            "uni_a": {"interface_id": "00:00:00:00:00:00:00:01:1", "tag": {"tag_type": "vlan", "value": 100}},
            "uni_z": {"interface_id": "00:00:00:00:00:00:00:05:1", "tag": {"tag_type": "vlan", "value": 100}},
            "dynamic_backup_path": True,
        }
        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)

        assert response.status_code == 201, response.text

        data = response.json()
        evc_id =  data["circuit_id"]

        time.sleep(10)

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["current_path"]
        assert data["failover_path"]

        # Collect service vlans

        vlan_allocations = defaultdict[str, list[int]](list)

        for link in data["current_path"]:
            s_vlan = link["metadata"]["s_vlan"]
            for endpoint in (link["endpoint_a"], link["endpoint_b"]):
                vlan_allocations[endpoint["id"]].append(s_vlan)

        for link in data["failover_path"]:
            s_vlan = link["metadata"]["s_vlan"]
            for endpoint in (link["endpoint_a"], link["endpoint_b"]):
                vlan_allocations[endpoint["id"]].append(s_vlan)


        # Close a link that both the current and failover path depend on

        self.net.net.configLinkStatus("s1", "s2", "down")

        time.sleep(10)

        # EVC should be enabled but not active

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["enabled"]
        assert not data["active"]

        assert not data["current_path"]
        assert not data["failover_path"]

        # Check that all the s_vlans have been freed

        api_url = f"{KYTOS_API}/topology/v3/interfaces/tag_ranges"

        response = requests.get(api_url)

        assert response.ok, response.text

        data = response.json()

        for interface, reserved_tags in vlan_allocations.items():
            available_tags = data[interface]["available_tags"]
            for reserved_tag in reserved_tags:
                assert any(
                    reserved_tag["value"] >= range_start and reserved_tag["value"] <= range_end
                    for (range_start, range_end) in available_tags[reserved_tag["tag_type"]]
                ), f"Vlan tag {reserved_tag} on interface {interface}, not released. Available tags: {available_tags}"


    def test_002_link_down(self):
        """Test link down behaviour on current_path."""

        self.net.net.configLinkStatus("s1", "s6", "down")
        self.net.net.configLinkStatus("s3", "s6", "down")
        self.net.net.configLinkStatus("s5", "s6", "down")

        time.sleep(5)

        payload = {
            "name": "Link Down Test 002",
            "uni_a": {"interface_id": "00:00:00:00:00:00:00:01:1", "tag": {"tag_type": "vlan", "value": 100}},
            "uni_z": {"interface_id": "00:00:00:00:00:00:00:05:1", "tag": {"tag_type": "vlan", "value": 100}},
            "dynamic_backup_path": True,
        }
        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)

        assert response.status_code == 201, response.text

        data = response.json()
        evc_id =  data["circuit_id"]

        time.sleep(10)

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["current_path"]
        assert data["failover_path"]

        # Collect service vlans

        vlan_allocations = defaultdict[str, list[int]](list)

        for link in data["current_path"]:
            s_vlan = link["metadata"]["s_vlan"]
            for endpoint in (link["endpoint_a"], link["endpoint_b"]):
                vlan_allocations[endpoint["id"]].append(s_vlan)

        # Close a link that the current path depends on

        link = data["current_path"][1]
        if link["id"] == LinkID("00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:03:2"):
            self.net.net.configLinkStatus("s2", "s3", "down")
        else:
            self.net.net.configLinkStatus("s2", "s6", "down")
        
        time.sleep(10)

        # EVC should be enabled but not active

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["enabled"]
        assert data["active"]

        assert data["current_path"]
        assert not data["failover_path"]

        # Check that all the s_vlans have been freed

        api_url = f"{KYTOS_API}/topology/v3/interfaces/tag_ranges"

        response = requests.get(api_url)

        assert response.ok, response.text

        data = response.json()

        for interface, reserved_tags in vlan_allocations.items():
            available_tags = data[interface]["available_tags"]
            for reserved_tag in reserved_tags:
                assert any(
                    reserved_tag["value"] >= range_start and reserved_tag["value"] <= range_end
                    for (range_start, range_end) in available_tags[reserved_tag["tag_type"]]
                ), f"Vlan tag {reserved_tag} on interface {interface}, not released. Available tags: {available_tags}"

    def test_003_link_down(self):
        """Test link down behaviour on failover_path."""

        self.net.net.configLinkStatus("s1", "s6", "down")
        self.net.net.configLinkStatus("s3", "s6", "down")
        self.net.net.configLinkStatus("s5", "s6", "down")

        time.sleep(5)

        payload = {
            "name": "Link Down Test 003",
            "uni_a": {"interface_id": "00:00:00:00:00:00:00:01:1", "tag": {"tag_type": "vlan", "value": 100}},
            "uni_z": {"interface_id": "00:00:00:00:00:00:00:05:1", "tag": {"tag_type": "vlan", "value": 100}},
            "dynamic_backup_path": True,
        }
        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)

        assert response.status_code == 201, response.text

        data = response.json()
        evc_id =  data["circuit_id"]

        time.sleep(10)

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["current_path"]
        assert data["failover_path"]

        # Collect service vlans

        vlan_allocations = defaultdict[str, list[int]](list)

        for link in data["failover_path"]:
            s_vlan = link["metadata"]["s_vlan"]
            for endpoint in (link["endpoint_a"], link["endpoint_b"]):
                vlan_allocations[endpoint["id"]].append(s_vlan)

        # Close a link that the failover path depends on

        link = data["failover_path"][1]
        if link["id"] == LinkID("00:00:00:00:00:00:00:02:3", "00:00:00:00:00:00:00:03:2"):
            self.net.net.configLinkStatus("s2", "s3", "down")
        else:
            self.net.net.configLinkStatus("s2", "s6", "down")
        
        time.sleep(10)

        # EVC should be enabled but not active

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["enabled"]
        assert data["active"]

        assert data["current_path"]
        assert not data["failover_path"]

        # Check that all the s_vlans have been freed

        api_url = f"{KYTOS_API}/topology/v3/interfaces/tag_ranges"

        response = requests.get(api_url)

        assert response.ok, response.text

        data = response.json()

        for interface, reserved_tags in vlan_allocations.items():
            available_tags = data[interface]["available_tags"]
            for reserved_tag in reserved_tags:
                assert any(
                    reserved_tag["value"] >= range_start and reserved_tag["value"] <= range_end
                    for (range_start, range_end) in available_tags[reserved_tag["tag_type"]]
                ), f"Vlan tag {reserved_tag} on interface {interface}, not released. Available tags: {available_tags}"

    def test_004_link_down(self):
        """Test multiple simultaneous link down behaviour."""

        self.net.net.configLinkStatus("s1", "s6", "down")
        self.net.net.configLinkStatus("s3", "s6", "down")
        self.net.net.configLinkStatus("s5", "s6", "down")

        time.sleep(5)

        payload = {
            "name": "Link Down Test 004",
            "uni_a": {"interface_id": "00:00:00:00:00:00:00:01:1", "tag": {"tag_type": "vlan", "value": 100}},
            "uni_z": {"interface_id": "00:00:00:00:00:00:00:05:1", "tag": {"tag_type": "vlan", "value": 100}},
            "dynamic_backup_path": True,
        }
        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)

        assert response.status_code == 201, response.text

        data = response.json()
        evc_id =  data["circuit_id"]

        time.sleep(10)

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["current_path"]
        assert data["failover_path"]

        # Collect service vlans

        vlan_allocations = defaultdict[str, list[int]](list)

        for link in data["current_path"]:
            s_vlan = link["metadata"]["s_vlan"]
            for endpoint in (link["endpoint_a"], link["endpoint_b"]):
                vlan_allocations[endpoint["id"]].append(s_vlan)

        for link in data["failover_path"]:
            s_vlan = link["metadata"]["s_vlan"]
            for endpoint in (link["endpoint_a"], link["endpoint_b"]):
                vlan_allocations[endpoint["id"]].append(s_vlan)


        # Close a link that both the current and failover path depend on

        self.net.net.configLinkStatus("s2", "s3", "down")
        self.net.net.configLinkStatus("s2", "s6", "down")

        time.sleep(10)

        # EVC should be enabled but not active

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["enabled"]
        assert not data["active"]

        assert not data["current_path"]
        assert not data["failover_path"]

        # Check that all the s_vlans have been freed

        api_url = f"{KYTOS_API}/topology/v3/interfaces/tag_ranges"

        response = requests.get(api_url)

        assert response.ok, response.text

        data = response.json()

        for interface, reserved_tags in vlan_allocations.items():
            available_tags = data[interface]["available_tags"]
            for reserved_tag in reserved_tags:
                assert any(
                    reserved_tag["value"] >= range_start and reserved_tag["value"] <= range_end
                    for (range_start, range_end) in available_tags[reserved_tag["tag_type"]]
                ), f"Vlan tag {reserved_tag} on interface {interface}, not released. Available tags: {available_tags}"


    def test_005_link_down_current_path_failover_path(self):
        """Test a link_down affecting both current_path and failover_path."""

        # Initially, set down links to force that EVC paths
        # will end up with shared links
        self.net.net.configLinkStatus("s3", "s6", "down")
        self.net.net.configLinkStatus("s4", "s6", "down")
        self.net.net.configLinkStatus("s5", "s6", "down")

        time.sleep(5)

        payload = {
            "name": "Link Down Test",
            "uni_a": {"interface_id": "00:00:00:00:00:00:00:05:1", "tag": {"tag_type": "vlan", "value": 100}},
            "uni_z": {"interface_id": "00:00:00:00:00:00:00:06:1", "tag": {"tag_type": "vlan", "value": 100}},
            "enabled": True,
            "dynamic_backup_path": True,
        }
        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)

        assert response.status_code == 201, response.text

        data = response.json()
        evc_id =  data["circuit_id"]

        time.sleep(10)

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["current_path"]
        assert data["failover_path"]

        # s4-eth3 and s5-eth2
        shared_link = "0b814adbd3b05669482ca479280787e55be5c155780f8a780423fa9e67e3a848"

        # assert that indeed the shared_link is being used by both current and failover paths
        link_ids = {link["id"] for link in data["current_path"]}
        assert shared_link in link_ids
        link_ids = {link["id"] for link in data["failover_path"]}
        assert shared_link in link_ids

        cookie = int(f"0xaa{evc_id}", 16)

        stored_flows = f'{KYTOS_API}/flow_manager/v2/stored_flows/?cookie_range={cookie}&cookie_range={cookie}&state=installed'
        response = requests.get(stored_flows)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data

        # Shut down a shared link that is expected to cause an EVC undeploy
        self.net.net.configLinkStatus("s4", "s5", "down")

        time.sleep(10)

        # EVC should be enabled but not active

        api_url = KYTOS_API + "/mef_eline/v2/evc/"
        response = requests.get(api_url + evc_id)
        data = response.json()

        assert data["enabled"]
        assert not data["active"]
        assert not data["current_path"]
        assert not data["failover_path"]

        # Check that all related flows have been removed

        stored_flows = f'{KYTOS_API}/flow_manager/v2/stored_flows/?cookie_range={cookie}&cookie_range={cookie}&state=installed'
        response = requests.get(stored_flows)
        assert response.status_code == 200, response.text
        data = response.json()
        assert not data
