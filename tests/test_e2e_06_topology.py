import requests
from tests.helpers import NetworkTest
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
        # which all elements are disabled in a clean setting
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_010_delete_interface_automatically(self):
        """Test interface removal after logical deletion"""
        intf_id = "00:00:00:00:00:00:00:02:1"
        api_url = KYTOS_API + f'/topology/v3/interfaces'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert intf_id in data["interfaces"]

        s2 = self.net.net.get('s2')
        s2.detach('s2-eth1')
        time.sleep(5)

        api_url = KYTOS_API + f'/topology/v3/interfaces'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert not intf_id in data["interfaces"]
