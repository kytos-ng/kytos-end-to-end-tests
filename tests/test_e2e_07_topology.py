import requests
from .helpers import NetworkTest, LinkID
import time

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

class TestE2ETopology:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Start the controller setting an environment in
        # which all elements are enabled in a clean setting
        self.net.config_all_links_up()
        self.net.restart_kytos_clean()
        time.sleep(20)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="multi")
        cls.net.start()
        cls.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_010_set_tag_ranges_persistence(self):
        """Test persistence of interface tag ranges"""

        intf_id = "00:00:00:00:00:00:00:01:1"
        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}/tag_ranges'

        # Check default vlans

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[intf_id]['tag_ranges']['vlan'] == [[1, 4094]], data
        assert data[intf_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data

        # Test removing all the vlans

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[3799, 3799]]}
        response = requests.post(api_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[intf_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[intf_id]['available_tags']['vlan'] == [], data

        # Check persistence of removed vlans

        self.net.start_controller()

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[intf_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[intf_id]['available_tags']['vlan'] == [], data

        # Test returning all the vlans

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 4094]]}
        response = requests.post(api_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[intf_id]['tag_ranges']['vlan'] == [[1, 4094]], data
        assert data[intf_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data

        # Check persistence of returned vlans

        self.net.start_controller()

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[intf_id]['tag_ranges']['vlan'] == [[1, 4094]], data
        assert data[intf_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data


    def test_020_set_tag_ranges_persistence(self):
        """Test persistence of link tag ranges"""

        link_id = LinkID(
            "00:00:00:00:00:00:00:01:2",
            "00:00:00:00:00:00:00:02:2"
        )
        api_url = f'{KYTOS_API}/topology/v3/links/{link_id}/tag_ranges'

        # Check default vlans

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data

        # Test removing all the vlans

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": []}
        response = requests.post(api_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [], data
        assert data[link_id]['available_tags']['vlan'] == [], data

        # Check persistence of removed vlans

        self.net.start_controller()

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [], data
        assert data[link_id]['available_tags']['vlan'] == [], data

        # Test returning all the vlans

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 3798], [3800, 4094]]}
        response = requests.post(api_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data

        # Check persistence of returned vlans

        self.net.start_controller()

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data


    def test_030_create_mixed_nni_uni(self):
        """Test creating a mixed uni/nni"""

        interface1_id = "00:00:00:00:00:00:00:01:2"
        interface2_id = "00:00:00:00:00:00:00:02:2"

        link_id = LinkID(
            interface1_id,
            interface2_id
        )

        interface1_url = f'{KYTOS_API}/topology/v3/interfaces/{interface1_id}/tag_ranges'
        interface2_url = f'{KYTOS_API}/topology/v3/interfaces/{interface2_id}/tag_ranges'
        link_url = f'{KYTOS_API}/topology/v3/links/{link_id}/tag_ranges'

        # Check the default state of the tags

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data

        response = requests.get(interface1_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface1_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface1_id]['available_tags']['vlan'] == [], data

        response = requests.get(interface2_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface2_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface2_id]['available_tags']['vlan'] == [], data

        # Clear away some of the link tags

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 2048]]}
        response = requests.post(link_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data

        # Acquire the tags on one of the interfaces

        response = requests.get(interface1_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface1_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface1_id]['available_tags']['vlan'] == [], data

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[2049, 4094]]}
        response = requests.post(interface1_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(interface1_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface1_id]['tag_ranges']['vlan'] == [[2049, 4094]], data
        assert data[interface1_id]['available_tags']['vlan'] == [[2049, 3798], [3800, 4094]], data

        # Try (and fail) to acquire the tags on the link

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 4094]]}
        response = requests.post(link_url, json=new_tag_ranges)
        assert response.status_code != 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data

        # Acquire the tags on the  second interface

        response = requests.get(interface2_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface2_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface2_id]['available_tags']['vlan'] == [], data

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[2049, 4094]]}
        response = requests.post(interface2_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(interface2_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface2_id]['tag_ranges']['vlan'] == [[2049, 4094]], data
        assert data[interface2_id]['available_tags']['vlan'] == [[2049, 3798], [3800, 4094]], data

    def test_040_reset_mixed_nni_uni(self):
        """Test trying to reset tags on a mixed uni/nni"""

        interface1_id = "00:00:00:00:00:00:00:01:2"
        interface2_id = "00:00:00:00:00:00:00:02:2"

        link_id = LinkID(
            interface1_id,
            interface2_id
        )

        interface1_url = f'{KYTOS_API}/topology/v3/interfaces/{interface1_id}/tag_ranges'
        interface2_url = f'{KYTOS_API}/topology/v3/interfaces/{interface2_id}/tag_ranges'
        link_url = f'{KYTOS_API}/topology/v3/links/{link_id}/tag_ranges'

        # Check the default state of the tags

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data

        response = requests.get(interface1_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface1_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface1_id]['available_tags']['vlan'] == [], data
        assert data[interface1_id]['default_tag_ranges']['vlan'] == [[3799, 3799]], data

        response = requests.get(interface2_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface2_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface2_id]['available_tags']['vlan'] == [], data
        assert data[interface2_id]['default_tag_ranges']['vlan'] == [[3799, 3799]], data

        # Clear away some of the link tags

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 2048]]}
        response = requests.post(link_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data

        # Reset the link tags

        response = requests.delete(link_url)
        assert response.status_code == 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 3798], [3800, 4094]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data

        # Clear away some of the link tags

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 2048]]}
        response = requests.post(link_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 3798], [3800, 4094]], data


        # Acquire the tags on one of the interfaces

        response = requests.get(interface1_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface1_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface1_id]['available_tags']['vlan'] == [], data
        assert data[interface1_id]['default_tag_ranges']['vlan'] == [[3799, 3799]], data

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[2049, 4094]]}
        response = requests.post(interface1_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(interface1_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface1_id]['tag_ranges']['vlan'] == [[2049, 4094]], data
        assert data[interface1_id]['available_tags']['vlan'] == [[2049, 3798], [3800, 4094]], data
        assert data[interface1_id]['default_tag_ranges']['vlan'] == [[2049, 4094]], data

        # Reset the link tags

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 2048]], data

        response = requests.delete(link_url)
        assert response.status_code == 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 2048]], data

        # Try (and fail) to acquire the tags on the link

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 4094]]}
        response = requests.post(link_url, json=new_tag_ranges)
        assert response.status_code != 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data

        # Clear a few tags.

        new_tag_ranges = {"tag_type": "vlan", "tag_ranges": [[1, 1024]]}
        response = requests.post(link_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 1024]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 1024]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 2048]], data

        # Reset the link tags

        response = requests.delete(link_url)
        assert response.status_code == 200, response.text

        response = requests.get(link_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[link_id]['tag_ranges']['vlan'] == [[1, 2048]], data
        assert data[link_id]['available_tags']['vlan'] == [[1, 2048]], data
        assert data[link_id]['default_tag_ranges']['vlan'] == [[1, 2048]], data

        # Reset the tags on the  second interface

        response = requests.get(interface2_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface2_id]['tag_ranges']['vlan'] == [[3799, 3799]], data
        assert data[interface2_id]['available_tags']['vlan'] == [], data
        assert data[interface2_id]['default_tag_ranges']['vlan'] == [[2049, 4094]], data

        response = requests.delete(interface2_url, json=new_tag_ranges)
        assert response.status_code == 200, response.text

        response = requests.get(interface2_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data[interface2_id]['tag_ranges']['vlan'] == [[2049, 4094]], data
        assert data[interface2_id]['available_tags']['vlan'] == [[2049, 3798], [3800, 4094]], data
        assert data[interface2_id]['default_tag_ranges']['vlan'] == [[2049, 4094]], data
