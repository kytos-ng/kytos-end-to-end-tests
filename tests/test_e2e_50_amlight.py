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
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

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
            links_id.append(key)
        assert links_id
        print("LINKS -> ", len(links_id))

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

        status_code = 400
        sleeping = 0
        while status_code > 300:
            sleeping += 2
            time.sleep(sleeping)
            api_url = f'{KYTOS_API}/topology/v3/switches/{switch}'
            response = requests.delete(api_url)
            #assert response.status_code == 200, response.text
            status_code = response.status_code
        print("TIME SLEEPING -> ", sleeping)
        assert 1 == 2
    
