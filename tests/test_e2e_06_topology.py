import requests
from tests.helpers import NetworkTest
import time
import json

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

class TestE2ETopology:
    net = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
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

    def test_010_delete_interface_automatically(self):
        """Test interface removal after logical deletion.
        Deleted:
            - Interface: S1-eth1
        """
        intf_id = "00:00:00:00:00:00:00:01:1"
        api_url = f'{KYTOS_API}/topology/v3/interfaces'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert intf_id in data["interfaces"]
        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}/disable'
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        S1 = self.net.net.get('s1')
        S1.detach('s1-eth1')
        time.sleep(5)

        api_url = f'{KYTOS_API}/topology/v3/interfaces'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert not intf_id in data["interfaces"]

    def test_015_delete_link(self):
        """Test api/kytos/topology/v3/links/{link_id} on DELETE.
        Deleted:
            - Link: s1 - s6
        """
        switch_1 = "00:00:00:00:00:00:00:01"
        switch_6 = "00:00:00:00:00:00:00:06"

        # Get the link_id
        api_url = f'{KYTOS_API}/topology/v3/links'
        response = requests.get(api_url)
        assert response.status_code == 200
        data = response.json()
        link_id = None
        for key, value in data['links'].items():
            endpoint_a = value["endpoint_a"]["switch"]
            endpoint_b = value["endpoint_b"]["switch"]
            if ((endpoint_a == switch_1 and endpoint_b == switch_6) or 
                (endpoint_a == switch_6 and endpoint_b == switch_1)):
                link_id = key
                break
        assert link_id

        # Not disabled
        api_url = f'{KYTOS_API}/topology/v3/links/{link_id}'
        response = requests.delete(api_url)
        assert response.status_code == 409, response.text
        
        # Disabling link
        self.net.net.configLinkStatus('s1', 's6', 'down')
        api_url = f'{KYTOS_API}/topology/v3/links/{link_id}/disable'
        response = requests.post(api_url)
        assert response.status_code == 201, response.text
    
        # Deleting link
        api_url = f'{KYTOS_API}/topology/v3/links/{link_id}'
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

        # Verify absence of link
        api_url = f'{KYTOS_API}/topology/v3/links'
        response = requests.get(api_url)
        assert response.status_code == 200
        data = response.json()
        assert link_id not in data["links"]

    def test_020_delete_switch(self):
        """Test api/kytos/topology/v3/switches/{switch_id} on DELETE
        Deleted:
            - Links: s1 - s2
            - Switch: s1
        """
        # Switch is not disabled, 409
        switch_1 = "00:00:00:00:00:00:00:01"
        api_url = f'{KYTOS_API}/topology/v3/switches/{switch_1}'
        response = requests.delete(api_url)
        assert response.status_code == 409

        # Switch have links, 409
        api_url = f'{KYTOS_API}/topology/v3/switches/{switch_1}/disable'
        response = requests.post(api_url)
        assert response.status_code == 201

        api_url = f'{KYTOS_API}/topology/v3/switches/{switch_1}'
        response = requests.delete(api_url)
        assert response.status_code == 409

        # Get the link_id
        api_url = f'{KYTOS_API}/topology/v3/links'
        response = requests.get(api_url)
        assert response.status_code == 200
        data = response.json()
        links_id = list()
        for key, value in data['links'].items():
            if (value["endpoint_a"]["switch"] == switch_1 or 
                value["endpoint_b"]["switch"] == switch_1):
                links_id.append(key)
        assert links_id

        self.net.net.configLinkStatus('s1', 's2', 'down')
        for link in links_id:
            # Disabling links
            api_url = f'{KYTOS_API}/topology/v3/links/{link}/disable'
            response = requests.post(api_url)
            assert response.status_code == 201, response.text
    
            # Deleting links
            api_url = f'{KYTOS_API}/topology/v3/links/{link}'
            response = requests.delete(api_url)
            assert response.status_code == 200, response.text

        # Delete switch, success
        time.sleep(10)
        api_url = f'{KYTOS_API}/topology/v3/switches/{switch_1}'
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

    def test_025_delete_interface(self):
        """Test api/kytos/topology/v3/interfaces/{interface_id} on DELETE
        Deleted:
            - Link: s2 - s6
        """
        #switch_jax = "00:00:00:00:00:00:00:22"
        switch_2 = "00:00:00:00:00:00:00:02"
        #switch_a5 = "00:00:00:00:00:00:00:19"
        switch_6 = "00:00:00:00:00:00:00:06"
        intf_id = "00:00:00:00:00:00:00:02:4"

        payload = {"flows": [
            {
                "priority": 10,
                "table_id": 1,
                "instructions": [{
                    "instruction_type": "apply_actions",
                    "actions": [{"action_type": "output", "port": 4}]
                }]
            }
        ]}
        api_url = f'{KYTOS_API}/flow_manager/v2/flows/{switch_2}'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        # Interface is enabled
        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}'
        response = requests.delete(api_url)
        assert response.status_code == 409, response.text

        # Interface is active
        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}/disable/'
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}'
        response = requests.delete(api_url)
        assert response.status_code == 409, response.text

        # Interface has a link
        S2 = self.net.net.get('s2')
        S2.detach('s2-eth4')

        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}'
        response = requests.delete(api_url)
        assert response.status_code == 409, response.text

        # Installed flows related to the interface
        api_url = f'{KYTOS_API}/topology/v3/links'
        response = requests.get(api_url)
        assert response.status_code == 200
        data = response.json()
        link_id = None
        for key, value in data['links'].items():
            endpoint_a = value["endpoint_a"]["switch"]
            endpoint_b = value["endpoint_b"]["switch"]
            if ((endpoint_a == switch_2 and endpoint_b == switch_6) or
                (endpoint_b == switch_2 and endpoint_a == switch_6)):
                link_id = key
                break
        assert link_id
        self.net.net.configLinkStatus('s2', 's6', 'down')
        api_url = f'{KYTOS_API}/topology/v3/links/{link_id}/disable'
        response = requests.post(api_url)
        assert response.status_code == 201, response.text
        api_url = f'{KYTOS_API}/topology/v3/links/{link_id}'
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}'
        response = requests.delete(api_url)
        assert response.status_code == 409, response.text

        # Interface succesfully deleted
        payload = {
            "force": True,
            "flows": [{"priority": 10, "table_id": 1}]
        }
        api_url = f'{KYTOS_API}/flow_manager/v2/flows/{switch_2}'
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text

        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}'
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text
        api_url = f'{KYTOS_API}/topology/v3/interfaces'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert not intf_id in data["interfaces"]
