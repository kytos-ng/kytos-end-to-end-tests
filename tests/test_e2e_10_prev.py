import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    def restart(self, _clean_config=False, _enable_all=False):

        # Start the controller setting an environment in which the setting is
        # preserved (persistence) and avoid the default enabling of all elements
        self.net.start_controller(clean_config=_clean_config, enable_all=_enable_all)
        self.net.wait_switches_connect()

        # Wait a few seconds to kytos execute LLDP
        time.sleep(10)

    def test_120_removing_link_metadata_persistent(self):
        """
        Test /api/kytos/topology/v3/links/{link_id}/metadata/{key} on DELETE
        supported by:
            /api/kytos/topology/v3/links/{link_id}/metadata on POST
            and
            /api/kytos/topology/v3/links/{link_id}/metadata on GET
        """

        endpoint_a = '00:00:00:00:00:00:00:01:3'
        endpoint_b = '00:00:00:00:00:00:00:02:2'

        # Enable the switches and ports first
        for i in [1, 2, 3]:
            sw = "00:00:00:00:00:00:00:0%d" % i

            api_url = KYTOS_API + '/topology/v3/switches/%s/enable' % sw
            response = requests.post(api_url)
            assert response.status_code == 201, response.text

            api_url = KYTOS_API + '/topology/v3/interfaces/switch/%s/enable' % sw
            response = requests.post(api_url)
            assert response.status_code == 200, response.text

        self.restart()

        # Get the link_id
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()

        link_id1 = None
        for k, v in data['links'].items():
            link_a, link_b = v['endpoint_a']['id'], v['endpoint_b']['id']
            if {link_a, link_b} == {endpoint_a, endpoint_b}:
                link_id1 = k

        # Enable the link_id
        api_url = KYTOS_API + '/topology/v3/links/%s/enable' % link_id1
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        self.restart()

        # Insert link metadata
        payload = {"tmp_key": "tmp_value"}
        key = next(iter(payload))

        api_url = KYTOS_API + '/topology/v3/links/%s/metadata' % link_id1
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text

        self.restart()

        # Verify that the metadata is inserted
        api_url = KYTOS_API + '/topology/v3/links/%s/metadata' % link_id1
        response = requests.get(api_url)
        data = response.json()
        keys = data['metadata'].keys()
        assert key in keys

        # Delete the link metadata
        api_url = KYTOS_API + '/topology/v3/links/%s/metadata/%s' % (link_id1, key)
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Make sure the metadata is removed
        api_url = KYTOS_API + '/topology/v3/links/%s/metadata' % link_id1
        response = requests.get(api_url)
        data = response.json()

        keys = data['metadata'].keys()
        assert key not in keys

    def test_130_mismatched_links(self):
        """Test mismatched links.
         The link ("01:3" - "02:2") will be mismatched by
         another link ("01:3" - "03:4") and then the link
         ("01:3" - "02:2") will be recovered.
        """
        self.net.config_all_links_up()
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

        s1, s2, s3 = self.net.net.get('s1', 's2', 's3')
        endpoint_1 = '00:00:00:00:00:00:00:01:3'
        endpoint_2 = '00:00:00:00:00:00:00:02:2'
        endpoint_3 = '00:00:00:00:00:00:00:03:4'
        s1_eht3, s2_eth2 = None, None
        link_1_2, link_1_3 = None, None

        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert len(data['links']) == 3

        for k, v in data['links'].items():
            link_a, link_b = v['endpoint_a']['id'], v['endpoint_b']['id']
            if {link_a, link_b} == {endpoint_1, endpoint_2}:
                link_1_2 = k
            assert v['enabled'] is True
            assert v['active'] is True
            assert v['status'] == 'UP'
            assert not v['status_reason']
        
        # Stop kytos so it does not get the link_down event
        self.net.stop_kytosd()
        time.sleep(5)

        # Add new link ("01:3" - "03:4") through Mininet
        for intf in s1.intfList():
            if intf.name == "s1-eth3":
                s1_eht3 = intf
                break
        assert s1_eht3 is not None
        s1_eht3.delete()
        self.net.net.addLink(s1, s3, port1=3, port2=4)
        s1.attach('s1-eth3')
        s3.attach('s3-eth4')

        # Start Kytos with the new link
        self.net.start_controller(clean_config=False, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert len(data['links']) == 4

        for k, v in data['links'].items():
            link_a, link_b = v['endpoint_a']['id'], v['endpoint_b']['id']
            # Old link ("01:3" - "02:2")
            if {link_a, link_b} == {endpoint_1, endpoint_2}:
                assert v['status'] == 'DOWN'
                assert "mismatched_link" in v['status_reason']
            else:
                assert v['status'] == 'UP'
                assert not v['status_reason']

        # Stop kytos so it does not get the link_down event
        self.net.stop_kytosd()
        time.sleep(5)

        # Add link ("01:3" - "02:2") again so new link ("01:3" - "03:4") 
        # is mismatched
        for intf in s1.intfList():
            if intf.name == "s1-eth3":
                s1_eht3 = intf
                break
        assert s1_eht3 is not None
        for intf in s2.intfList():
            if intf.name == "s2-eth2":
                s2_eth2 = intf
                break
        assert s2_eth2 is not None
        s1_eht3.delete()
        s2_eth2.delete()
        self.net.net.addLink(s1, s2, port1=3, port2=2)
        s1.attach('s1-eth3')
        s2.attach('s2-eth2')

        # Start Kytos with the new link
        self.net.start_controller(clean_config=False, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert len(data['links']) == 4

        for k, v in data['links'].items():
            link_a, link_b = v['endpoint_a']['id'], v['endpoint_b']['id']
            # New link ("01:3" - "03:4")
            if {link_a, link_b} == {endpoint_1, endpoint_3}:
                assert v['status'] == 'DOWN'
                assert "mismatched_link" in v['status_reason']
            else:
                assert v['status'] == 'UP'
                assert not v['status_reason']

    def test_200_switch_disabled_on_clean_start(self):

        switch_id = "00:00:00:00:00:00:00:01"

        # Make sure the switch is disabled
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()

        assert response.status_code == 200, response.text
        assert data['switches'][switch_id]['enabled'] is False

    def test_300_interfaces_disabled_on_clean_start(self):

        # Make sure the interfaces are disabled
        api_url = KYTOS_API + '/topology/v3/interfaces'
        response = requests.get(api_url)
        data = response.json()

        assert response.status_code == 200, response.text
        for interface in data['interfaces']:
            assert data['interfaces'][interface]['enabled'] is False

    def test_400_switch_enabled_on_clean_start(self):

        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(5)

        # Make sure the switch is disabled
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)

        assert response.status_code == 200, response.text
        data = response.json()
        for switch in data['switches']:
            assert data['switches'][switch]['enabled'] is True

    def test_500_interfaces_enabled_on_clean_start(self):

        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(5)

        # Make sure the interfaces are disabled
        api_url = KYTOS_API + '/topology/v3/interfaces'
        response = requests.get(api_url)

        assert response.status_code == 200, response.text
        data = response.json()
        for interface in data['interfaces']:
            assert data['interfaces'][interface]['enabled'] is True