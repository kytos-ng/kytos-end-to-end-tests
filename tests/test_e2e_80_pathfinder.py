import json
import requests
from tests.helpers import NetworkTest
import tests.helpers
import time

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

    def test_005_undesired_links(self):
        Int_1 = "00:00:00:00:00:00:00:01:3"
        Int_2 = "00:00:00:00:00:00:00:06:3"
        api_url = KYTOS_API + '/pathfinder/v3/'
        post_body = {
    "source": "00:00:00:00:00:00:00:01:3",
    "destination": "00:00:00:00:00:00:00:06:3",
    "spf_attribute": "hop",
    "spf_max_path_cost": 1,
    "minimum_flexible_hits": 2,
    "parameter": "hop"
    }
        
        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data != [], "Response empty"
        assert len(data) == 1, f'More than 1 result: {data}'
        undesiredlink_post_body = {
    "source": "00:00:00:00:00:00:00:01:3",
    "destination": "00:00:00:00:00:00:00:06:3",
    "undesired_links": [
    "74bbc9527a0e309a86c95744042bcf9e3beb52955c942cac5fc735b1cf986f7f"
    ],
    "spf_attribute": "hop",
    "spf_max_path_cost": 1,
    "minimum_flexible_hits": 2,
    "parameter": "hop"
    }
        response = requests.post(api_url, json=undesiredlink_post_body)
        data = response.json()
        assert len(data) == 0, f'Link not removed: {data}'
    
    def test_010_spf_attribute(self):
        links_metadata = self.add_topology_metadata()
        api_url = KYTOS_API + '/pathfinder/v3/'
        post_body = {
    "source": "00:00:00:00:00:00:00:01:3",
    "destination": "00:00:00:00:00:00:00:06:3",
    "spf_attribute": "hop",
    "spf_max_path_cost": 1,
    "parameter": "hop"
    }
        
        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data != [], "Response empty"
        assert len(data) == 1, f'More than 1 result: {data}'
        assert data['paths'][0]['cost'] == 1, f'Path cost larger than 1: {data}'

        post_body = {
    "source": "00:00:00:00:00:00:00:01:3",
    "destination": "00:00:00:00:00:00:00:06:3",
    "spf_attribute": "delay",
    "spf_max_path_cost": 20,
    "parameter": "delay"
    }
        
        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['paths'][0]['cost'] == 20, f'Path cost larger than 20: {data}'

        post_body = {
    "source": "00:00:00:00:00:00:00:01:3",
    "destination": "00:00:00:00:00:00:00:06:3",
    "spf_attribute": "priority",
    "parameter": "priority"
    }
        
        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data == 10, f'Path cost larger than 10: {data}'


    def test_015_spf_max_path_cost(self):
        pass


    def test_020_mandatory_metrics(self):
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
        assert data != [], "Response empty"
        assert len(data) == 1, f'More than 1 result: {data}'
        assert data['paths'][0]['metrics']['ownership'] == "blue", f'Path cost larger than 1: {data}'

    
    def test_025_flexible_metrics_and_hits(self):
        links_metadata = self.add_topology_metadata()
        api_url = KYTOS_API + '/pathfinder/v3/'
        post_body = {
  "source": "00:00:00:00:00:00:00:01:3",
  "destination": "00:00:00:00:00:00:00:06:3",
  "spf_attribute": "hop",
  "flexible_metrics": {
  "delay": 10,
  "ownership": "blue"
},
  "minimum_flexible_hits": 2
}
        
        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data != 0, f'Path cost larger than 1: {data}'

        post_body = {
  "source": "00:00:00:00:00:00:00:01:3",
  "destination": "00:00:00:00:00:00:00:06:3",
  "spf_attribute": "hop",
  "flexible_metrics": {
  "delay": 10,
  "ownership": "red"
},
  "minimum_flexible_hits": 2
}
        
        response = requests.post(api_url, json=post_body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data == 0, f'Path cost larger than 1: {data}'

    def test_030_minimum_flexible_hits(self):
        pass