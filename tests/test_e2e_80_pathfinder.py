import json
import requests
from tests.helpers import NetworkTest
import tests.helpers
import time
import pytest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2EPathfinder:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
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

    def restart(self, _clean_config=False, _enable_all=False):

        # Start the controller setting an environment in which the setting is
        # preserved (persistence) and avoid the default enabling of all elements
        self.net.start_controller(clean_config=_clean_config, enable_all=_enable_all)
        self.net.wait_switches_connect()

        # Wait a few seconds to kytos execute LLDP
        time.sleep(10)
    
    def add_topology_metadata(self):
        """Add topology metadata."""
        links_metadata = {
            "74bbc9527a0e309a86c95744042bcf9e3beb52955c942cac5fc735b1cf986f7f": {
                "link_name": "s1-eth3-s6-eth3",
                "ownership": "red",
                "bandwidth": "10",
                "delay" : 100,
                "priority": 120,
            },
            "cf0f4071be426b3f745027f5d22bc61f8312ae86293c9b28e7e66015607a9260": {
                "link_name": "s1-eth2-s2-eth2",
                "ownership": "blue",
                "bandwidth": "100",
                "delay" : 10,
                "priority": 5,                
            },
            "adda3859b963110d584bf6ec3ac85ddea80276001e37edc1c420463a34c80c9e": {
                "link_name": "s2-eth4-s6-eth4",
                "ownership": "blue",
                "bandwidth": "100",
                "delay" : 10,
                "priority": 5,                
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

    @pytest.mark.parametrize("undesired_link, expected", 
                            [([], 1),(["74bbc9527a0e309a86c95744042bcf9e3beb52955c942cac5fc735b1cf986f7f"], 8)])
    def test_10_undesired_link_and_max_path(self, undesired_link, expected):
        """Tests fastest path from switch 1 to 6 then blocks it"""
        api_url = KYTOS_API + '/pathfinder/v3/'
        post_body = {
"source": "00:00:00:00:00:00:00:01:3",
"destination": "00:00:00:00:00:00:00:06:3",
"undesired_links": undesired_link,
"spf_attribute": "hop",
"spf_max_paths": 1,
"parameter": "hop"
}

        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['paths'][0]['cost'] == expected, f'Shortest path expected {expected}: {data}'

    @pytest.mark.parametrize("attribute, expected_cost", 
                            [("hop", 1), ("delay", 20), ("priority", 15)])
    def test_20_spf_attribute_and_max_path_cost(self, attribute, expected_cost):
        """Tests hop, delay, and priority with path cost"""
        links_metadata = self.add_topology_metadata()
        api_url = KYTOS_API + '/pathfinder/v3/'
        post_body = {
"source": "00:00:00:00:00:00:00:01:3",
"destination": "00:00:00:00:00:00:00:06:3",
"spf_attribute": attribute,
"spf_max_path_cost": expected_cost,
"parameter": attribute
}

        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['paths'][0]['cost'] == expected_cost, f'Path cost larger than {expected_cost}: {data}'

    def test_30_mandatory_metrics(self):
        """The returned path should be of ownership blue"""
        links_metadata = self.add_topology_metadata()
        api_url = KYTOS_API + '/pathfinder/v3/'
        post_body = {
"source": "00:00:00:00:00:00:00:01:3",
"destination": "00:00:00:00:00:00:00:06:3",
"spf_attribute": "hop",
"mandatory_metrics": {
"ownership": "blue"
}
}

        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['paths'][0]['metrics']['ownership'] == "blue", f'Path ownership not blue: {data}'

    @pytest.mark.parametrize("ownership, expected_cost", 
                            [("blue", 2), ("red", 0)])
    def test_40_flexible_metrics_and_hits(self, ownership, expected_cost):
        """First test has all required metrics and will return paths, second test has no paths since no link meets the required flexible metrics"""
        links_metadata = self.add_topology_metadata()
        api_url = KYTOS_API + '/pathfinder/v3/'
        post_body = {
  "source": "00:00:00:00:00:00:00:01:3",
  "destination": "00:00:00:00:00:00:00:06:3",
  "spf_attribute": "hop",
  "flexible_metrics": {
  "delay": 10,
  "ownership": ownership
},
  "minimum_flexible_hits": 2
}

        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data['paths']) == expected_cost, f'Path cost larger than {expected_cost}: {data}'
