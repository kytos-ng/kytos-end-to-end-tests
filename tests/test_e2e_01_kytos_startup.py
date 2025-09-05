import time
import shutil
import requests
from tests.helpers import NetworkTest
import re
import os
import pytest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER

# TODO: check all the logs on the end
# TODO: persist the logs of syslog
# TODO: multiple instances or single instance for checking memory leak /
#  usage (benchmark - how many flows are supported? how many switches are supported?)


class TestE2EKytosServer:
    net = None
    syslog_found = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(5)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.wait_switches_connect()
        # rotate logfile (copy/truncate strategy)
        try:
            cls.logfile = '/var/log/syslog'
            if not os.path.exists(cls.logfile):
                raise Exception(FileNotFoundError)
            shutil.copy(cls.logfile, cls.logfile + '-' + time.strftime("%Y%m%d%H%M%S"))
            open(cls.logfile, 'w').close()
        except FileNotFoundError:
            pass

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_start_kytos_api_core(self):

        # Check server status if it is UP and running
        api_url = KYTOS_API+'/core/status/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()
        assert data['response'] == 'running'

        # check the list of enabled napps
        expected_napps = [
                ("kytos", "pathfinder"),
                ("kytos", "mef_eline"),
                ("kytos", "maintenance"),
                ("kytos", "flow_manager"),
                ("kytos", "of_core"),
                ("kytos", "topology"),
                ("kytos", "of_lldp"),
                ("kytos", "of_multi_table"),
                ("kytos", "kafka_events"),
                ('amlight', 'sdntrace'),
                ('amlight', 'coloring'),
                ('amlight', 'sdntrace_cp'),
                ('amlight', 'kytos_stats'),
            ]
        api_url = KYTOS_API+'/core/napps_enabled/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()
        assert set([tuple(lst) for lst in data['napps']]) == set(expected_napps)

        # Check disable a napp
        api_url = KYTOS_API+'/core/napps/kytos/mef_eline/disable'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        api_url = KYTOS_API+'/core/napps_enabled/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()
        assert set([tuple(lst) for lst in data['napps']]) == set(expected_napps) - set([("kytos", "mef_eline")])

        # Restart kytos and check if the switches are still enabled
        self.net.start_controller(clean_config=False)
        self.net.wait_switches_connect()

        api_url = KYTOS_API+'/core/napps_enabled/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()
        assert set([tuple(lst) for lst in data['napps']]) == set(expected_napps) - set([("kytos", "mef_eline")])

        # check enable a napp
        api_url = KYTOS_API+'/core/napps/kytos/mef_eline/enable'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        api_url = KYTOS_API+'/core/napps_enabled/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text

        data = response.json()
        assert set([tuple(lst) for lst in data['napps']]) == set(expected_napps)

    # test auth api
    # TODO

    @pytest.mark.skipif(not os.path.exists('/var/log/syslog'), reason="/var/log/syslog does not exist")
    def test_start_kytos_without_errors(self):
        with open(self.logfile, "r") as f:
            assert re.findall(r'kytos.*?(error|exception)(.*)?', f.read(), re.I) == []
