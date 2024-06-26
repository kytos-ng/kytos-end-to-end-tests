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
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER, topo_name="amlight")
        cls.net.start()
        cls.net.wait_switches_connect()
        time.sleep(20)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_010_delete_interface_automatically(self):
        """Test interface removal after logical deletion.
        Deleted:
            - Interface: JAX2-eth61
        """
        intf_id = "00:00:00:00:00:00:00:22:61"
        api_url = f'{KYTOS_API}/topology/v3/interfaces'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert intf_id in data["interfaces"]
        api_url = f'{KYTOS_API}/topology/v3/interfaces/{intf_id}/disable'
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        JAX2 = self.net.net.get('JAX2')
        JAX2.detach('JAX2-eth61')
        time.sleep(5)

        api_url = f'{KYTOS_API}/topology/v3/interfaces'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert not intf_id in data["interfaces"]

    def test_015_delete_link(self):
        """Test api/kytos/topology/v3/links/{link_id} on DELETE.
        Deleted:
            - Link: JAX1 - JAX2
        """
        switch_1 = "00:00:00:00:00:00:00:22"
        switch_2 = "00:00:00:00:00:00:00:21"

        # Get the link_id
        api_url = f'{KYTOS_API}/topology/v3/links'
        response = requests.get(api_url)
        assert response.status_code == 200
        data = response.json()
        link_id = None
        for key, value in data['links'].items():
            endpoint_a = value["endpoint_a"]["switch"]
            endpoint_b = value["endpoint_b"]["switch"]
            if ((endpoint_a == switch_1 and endpoint_b == switch_2) or 
                (endpoint_a == switch_2 and endpoint_b == switch_1)):
                link_id = key
                break
        assert link_id

        # Not disabled
        api_url = f'{KYTOS_API}/topology/v3/links/{link_id}'
        response = requests.delete(api_url)
        assert response.status_code == 409, response.text
        
        # Disabling link
        self.net.net.configLinkStatus('JAX1', 'JAX2', 'down')
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
            - Links: Ampath3 - Ampath2; Ampath3 - Ampath1
            - Switch: Ampath2
        """
        # Switch is not disabled, 409
        switch = "00:00:00:00:00:00:00:17"
        api_url = f'{KYTOS_API}/topology/v3/switches/{switch}'
        response = requests.delete(api_url)
        assert response.status_code == 409

        # Switch have links, 409
        api_url = f'{KYTOS_API}/topology/v3/switches/{switch}/disable'
        response = requests.post(api_url)
        assert response.status_code == 201

        api_url = f'{KYTOS_API}/topology/v3/switches/{switch}'
        response = requests.delete(api_url)
        assert response.status_code == 409

        # Get the link_id
        api_url = f'{KYTOS_API}/topology/v3/links'
        response = requests.get(api_url)
        assert response.status_code == 200
        data = response.json()
        links_id = list()
        for key, value in data['links'].items():
            if (value["endpoint_a"]["switch"] == switch or 
                value["endpoint_b"]["switch"] == switch):
                links_id.append(key)
        assert links_id

        self.net.net.configLinkStatus('Ampath3', 'Ampath2', 'down')
        self.net.net.configLinkStatus('Ampath3', 'Ampath1', 'down')
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
        api_url = f'{KYTOS_API}/topology/v3/switches/{switch}'
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

    def test_025_delete_interface(self):
        """Test api/kytos/topology/v3/interfaces/{interface_id} on DELETE
        Deleted:
            - Link: JAX2 - Ampath5
        """
        switch_jax = "00:00:00:00:00:00:00:22"
        switch_a5 = "00:00:00:00:00:00:00:19"
        intf_id = "00:00:00:00:00:00:00:22:15"

        payload = {"flows": [
            {
                "priority": 10,
                "table_id": 1,
                "instructions": [{
                    "instruction_type": "apply_actions",
                    "actions": [{"action_type": "output", "port": 15}]
                }]
            }
        ]}
        api_url = f'{KYTOS_API}/flow_manager/v2/flows/{switch_jax}'
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
        JAX2 = self.net.net.get('JAX2')
        JAX2.detach('JAX2-eth15')

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
            if ((endpoint_a == switch_jax and endpoint_b == switch_a5) or
                (endpoint_b == switch_jax and endpoint_a == switch_a5)):
                link_id = key
                break
        assert link_id
        self.net.net.configLinkStatus('JAX2', 'Ampath5', 'down')
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
        api_url = f'{KYTOS_API}/flow_manager/v2/flows/{switch_jax}'
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
