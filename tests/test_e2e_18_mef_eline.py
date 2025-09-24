import requests
import time


from .helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api' % CONTROLLER

class TestE2EMefEline:
    net = None

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

    def setup_method(self, method):
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

    def teardown_method(self, method):
        for link in self.net.net.links:
            self.net.net.configLinkStatus(
                link.intf1.node.name,
                link.intf2.node.name,
                "up"
            )

    def is_tag_used_by_interface(self, interface_id, tag_type, value):
        api_url = KYTOS_API + "/kytos/topology/v3/interfaces/tag_ranges"
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()

        interface_tags = data[interface_id]
        available_tags = interface_tags["available_tags"][tag_type]
        # tag_ranges = interface_tags["tag_ranges"][tag_type]

        for range_start, range_end in available_tags:
            if range_start <= value <= range_end:
                break
        else:
            return True
        return False
    
    def assert_tag_used_by_interface(self, interface_id, tag_type, value):
        api_url = KYTOS_API + "/kytos/topology/v3/interfaces/tag_ranges"
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()

        interface_tags = data[interface_id]
        available_tags = interface_tags["available_tags"][tag_type]
        # tag_ranges = interface_tags["tag_ranges"][tag_type]

        for range_start, range_end in available_tags:
            if range_start <= value <= range_end:
                break
        else:
            assert True, available_tags
        assert False, available_tags

    def assert_tag_not_used_by_interface(self, interface_id, tag_type, value):
        api_url = KYTOS_API + "/kytos/topology/v3/interfaces/tag_ranges"
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()

        interface_tags = data[interface_id]
        available_tags = interface_tags["available_tags"][tag_type]
        # tag_ranges = interface_tags["tag_ranges"][tag_type]

        for range_start, range_end in available_tags:
            if range_start <= value <= range_end:
                break
        else:
            assert False, available_tags
        assert True, available_tags

    def test_010_patch_inter_evc(self):
        payload = {
            "name": "Test EVC",
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 100,
                },
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 100,
                },
            },
            "enabled": True,
            "dynamic_backup_path": True,
        }
        api_url = KYTOS_API + "/kytos/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()

        evc_id = data["circuit_id"]

        # Have uni_a go down
        self.net.net.configLinkStatus('h11', 's1', 'down')

        # Config disable new uni_a
        api_url = KYTOS_API + "/kytos/topology/v3/interfaces/00:00:00:00:00:00:00:01:2/disable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        # Try to patch to new uni_a
        payload = {
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:2",
                "tag": {
                    "tag_type": "vlan",
                    "value": 100,
                }
            }
        }
        api_url = KYTOS_API + f"/kytos/mef_eline/v2/evc/{evc_id}"
        response = requests.patch(api_url, json=payload)
        
        assert response.status_code == 200, response.text

        # Check old uni_a
        self.assert_tag_not_used_by_interface(
            "00:00:00:00:00:00:00:01:1",
            "vlan",
            100
        )

        self.assert_tag_used_by_interface(
            "00:00:00:00:00:00:00:01:2",
            "vlan",
            100
        )

        api_url = KYTOS_API + f"/kytos/mef_eline/v2/evc/{evc_id}"
        response = requests.get(api_url)

        assert response.status_code == 200, response.text

        data = response.json()
        assert data["uni_a"]["interface_id"] != "00:00:00:00:00:00:00:01:1"

    def test_020_patch_intra_evc(self):
        payload = {
            "name": "Test EVC",
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 100,
                },
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:02:1",
                "tag": {
                    "tag_type": "vlan",
                    "value": 100,
                },
            },
            "enabled": True,
            "dynamic_backup_path": True,
        }
        api_url = KYTOS_API + "/kytos/mef_eline/v2/evc/"
        response = requests.post(api_url, json=payload)
        assert response.status_code == 201, response.text
        data = response.json()

        evc_id = data["circuit_id"]

        # Have uni_z go down
        self.net.net.configLinkStatus('h2', 's2', 'down')

        # Config disable new uni_z
        api_url = KYTOS_API + "/kytos/topology/v3/interfaces/00:00:00:00:00:00:00:01:2/disable"
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        # Try to patch to new uni_z
        payload = {
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:01:2",
                "tag": {
                    "tag_type": "vlan",
                    "value": 100,
                }
            }
        }
        api_url = KYTOS_API + f"/kytos/mef_eline/v2/evc/{evc_id}"
        response = requests.patch(api_url, json=payload)
        
        assert response.status_code == 200, response.text

        # Check old uni_z
        self.assert_tag_not_used_by_interface(
            "00:00:00:00:00:00:00:02:1",
            "vlan",
            100
        )

        self.assert_tag_used_by_interface(
            "00:00:00:00:00:00:00:01:2",
            "vlan",
            100
        )

        api_url = KYTOS_API + f"/kytos/mef_eline/v2/evc/{evc_id}"
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()
        assert data["uni_z"]["interface_id"] != "00:00:00:00:00:00:00:02:1"
