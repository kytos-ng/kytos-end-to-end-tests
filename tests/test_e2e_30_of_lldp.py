import requests
from .helpers import NetworkTest
import time

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER


class TestE2EOfLLDP:
    net = None

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.restart_kytos_clean()
        cls.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def capture_lldp_packets(self, node_names: list[str], sleep_time=10):

        nodes = [
            self.net.net.get(name)
            for name in node_names
        ]

        for node, name in zip(nodes, node_names):
            pcap_file = f'/tmp/lldp-{node.intfNames()[0]}.pcap'
            node.cmd(f'rm {pcap_file}')

        lldp_packet_monitors = [
            node.popen(
                [
                    'tcpdump',
                    '-U',
                    '-i', node.intfNames()[0],
                    '-w', f'/tmp/lldp-{node.intfNames()[0]}.pcap',
                    'ether proto 0x88cc',
                ]
            )
            for node in nodes
        ]

        time.sleep(sleep_time)

        for monitor in lldp_packet_monitors:
            monitor.terminate()
            monitor.wait()

        results = {}

        for name, node in zip(node_names, nodes):
            pcap_file = f'/tmp/lldp-{node.intfNames()[0]}.pcap'
            count = int(node.cmd(f'tcpdump -r {pcap_file} 2> /dev/null | wc -l'))
            results[name] = count

        return results

    def enable_all_interfaces(self):
        api_url = KYTOS_API + '/topology/v3/switches/'
        response = requests.get(api_url)
        data = response.json()
        switches = data.get("switches", {})
        for sw in switches.keys():
            response = requests.post(KYTOS_API + '/topology/v3/switches/%s/enable' % sw)
            response = requests.post(KYTOS_API + '/topology/v3/interfaces/switch/%s/enable' % sw)

    @staticmethod
    def disable_all_of_lldp():
        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        all_interfaces = data.get("interfaces", [])
        response = requests.post(api_url+'disable/', json={"interfaces": all_interfaces})
        assert response.status_code == 200, response.text

    def test_001_list_interfaces_with_lldp(self):
        """ List interfaces with OF LLDP. """
        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "interfaces" in data
        # the number of interfaces should match the topology + the OFP_LOCAL port, for the RingTopology it means:
        # mininet> net
        # ...
        # s1 lo:  s1-eth1:h11-eth0 s1-eth2:h12-eth0 s1-eth3:s2-eth2 s1-eth4:s3-eth3
        # s2 lo:  s2-eth1:h2-eth0 s2-eth2:s1-eth3 s2-eth3:s3-eth2
        # s3 lo:  s3-eth1:h3-eth0 s3-eth2:s2-eth3 s3-eth3:s1-eth4
        expected_interfaces = [
                "00:00:00:00:00:00:00:01:1", "00:00:00:00:00:00:00:01:2", "00:00:00:00:00:00:00:01:3",
                "00:00:00:00:00:00:00:01:4", "00:00:00:00:00:00:00:01:4294967294",
                "00:00:00:00:00:00:00:02:1", "00:00:00:00:00:00:00:02:2", "00:00:00:00:00:00:00:02:3",
                "00:00:00:00:00:00:00:02:4294967294",
                "00:00:00:00:00:00:00:03:1", "00:00:00:00:00:00:00:03:2", "00:00:00:00:00:00:00:03:3",
                "00:00:00:00:00:00:00:03:4294967294"
        ]
        assert set(data["interfaces"]) == set(expected_interfaces)

        # make sure the interfaces are actually receiving LLDP
        results = self.capture_lldp_packets(['h11', 'h12', 'h2', 'h3'], sleep_time=10)

        for packet_count in results.values():
            assert packet_count > 0

    def test_010_disable_of_lldp(self):
        """ Test if the disabling OF LLDP in an interface worked properly. """
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(5)
        self.enable_all_interfaces()

        # disabling all the UNI interfaces
        payload = {
            "interfaces": [
                "00:00:00:00:00:00:00:01:1", "00:00:00:00:00:00:00:01:2", "00:00:00:00:00:00:00:01:4294967294",
                "00:00:00:00:00:00:00:02:1", "00:00:00:00:00:00:00:02:4294967294",
                "00:00:00:00:00:00:00:03:1", "00:00:00:00:00:00:00:03:4294967294"
            ]
        }
        expected_interfaces = [
                "00:00:00:00:00:00:00:01:3", "00:00:00:00:00:00:00:01:4",
                "00:00:00:00:00:00:00:02:2", "00:00:00:00:00:00:00:02:3",
                "00:00:00:00:00:00:00:03:2", "00:00:00:00:00:00:00:03:3"
        ]

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/disable/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 200, response.text

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

        results = self.capture_lldp_packets(['h11', 'h12', 'h2', 'h3'], sleep_time=10)

        for packet_count in results.values():
            assert packet_count == 0

        # restart kytos and check if lldp remains disabled
        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

    def test_020_enable_of_lldp(self):
        """ Test if enabling OF LLDP in an interface works properly. """
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(5)
        self.enable_all_interfaces()
        TestE2EOfLLDP.disable_all_of_lldp()

        payload = {
            "interfaces": [
                "00:00:00:00:00:00:00:01:1"
            ]
        }
        expected_interfaces = [
                "00:00:00:00:00:00:00:01:1"
        ]

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/enable/'
        response = requests.post(api_url, json=payload)
        assert response.status_code == 200, response.text

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

        results = self.capture_lldp_packets(['h11', 'h12', 'h2', 'h3'], sleep_time=10)

        enabled_nodes = ['h11']

        disabled_nodes = ['h12', 'h2', 'h3']

        for node in enabled_nodes:
            assert results[node] > 0

        for node in disabled_nodes:
            assert results[node] == 0

        # restart kytos and check if lldp remains disabled
        self.net.start_controller(clean_config=False, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(10)

        api_url = KYTOS_API + '/of_lldp/v1/interfaces/'
        response = requests.get(api_url)
        data = response.json()
        assert set(data["interfaces"]) == set(expected_interfaces)

    def test_030_change_polling_interval(self):
        """ Test if changing the polling interval works works properly. """
        self.net.restart_kytos_clean()
        time.sleep(5)

        default_polling_time = 3
        api_url = KYTOS_API + '/of_lldp/v1/polling_time'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "polling_time" in data
        assert data["polling_time"] == default_polling_time

        lldp_wait = 31
        delta_pps = self.capture_lldp_packets(['h11'], sleep_time=lldp_wait)['h11']

        api_url = KYTOS_API + '/of_lldp/v1/polling_time'
        response = requests.post(api_url, json={"polling_time": 1})
        assert response.status_code == 200, response.text

        response = requests.get(api_url)
        data = response.json()
        assert data["polling_time"] == 1

        # wait a few seconds to let the last polling time schedule finish
        time.sleep(default_polling_time)

        delta_pps_2 = self.capture_lldp_packets(['h11'], sleep_time=lldp_wait)['h11']

        # the delta pps now should be around 30, because the interval is every 1s
        assert delta_pps_2 > delta_pps + 15
