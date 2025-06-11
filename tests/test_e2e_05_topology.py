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

    def test_005_list_topology(self):
        """
        Test /api/kytos/topology/v3/ on GET
        """
        api_url = KYTOS_API + '/topology/v3/'
        response = requests.get(api_url)
        data = response.json()

        topology = {
            '00:00:00:00:00:00:00:01':
                ['00:00:00:00:00:00:00:01:1', '00:00:00:00:00:00:00:01:2', '00:00:00:00:00:00:00:01:3',
                 '00:00:00:00:00:00:00:01:4', '00:00:00:00:00:00:00:01:4294967294'],
            '00:00:00:00:00:00:00:02':
                ['00:00:00:00:00:00:00:02:1', '00:00:00:00:00:00:00:02:2', '00:00:00:00:00:00:00:02:3',
                 '00:00:00:00:00:00:00:02:4294967294'],
            '00:00:00:00:00:00:00:03':
                ['00:00:00:00:00:00:00:03:1', '00:00:00:00:00:00:00:03:2', '00:00:00:00:00:00:00:03:3',
                 '00:00:00:00:00:00:00:03:4294967294'],
        }

        assert response.status_code == 200, response.text
        assert 'topology' in data
        assert 'switches' in data['topology']
        assert len(data['topology']['switches']) == 3

        for switch in data['topology']['switches']:
            # Switches validation
            assert switch in topology
            # Interfaces validation
            assert topology[switch].sort() == \
                   list(map(str, data['topology']['switches'][str(switch)]['interfaces'])).sort()
            # Links validation
            for link in data['topology']['switches'][str(switch)]['interfaces']:
                assert 'link' in data['topology']['switches'][str(switch)]['interfaces'][link]

    def test_010_list_switches(self):
        """
        Test /api/kytos/topology/v3/switches on GET
        """
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()

        assert response.status_code == 200, response.text
        assert 'switches' in data
        assert len(data['switches']) == 3
        assert '00:00:00:00:00:00:00:01' in data['switches']
        assert '00:00:00:00:00:00:00:02' in data['switches']
        assert '00:00:00:00:00:00:00:03' in data['switches']

    def test_020_enabling_switch_persistent(self):
        """
        Test /api/kytos/topology/v3/switches/{dpid}/enable on POST
        supported by
            /api/kytos/topology/v3/switches on GET
        """

        switch_id = '00:00:00:00:00:00:00:01'

        # Make sure the switches are disabled by default
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()
        assert data['switches'][switch_id]['enabled'] is False

        # Enable the switches
        api_url = KYTOS_API + '/topology/v3/switches/%s/enable' % switch_id
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        self.restart()

        # Check if the switch is enabled
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()
        assert data['switches'][switch_id]['enabled'] is True

        self.restart()

        # Check if the switches are still enabled and now with the links
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()
        assert data['switches'][switch_id]['enabled'] is True

    def test_030_disabling_switch_persistent(self):
        """
        Test /api/kytos/topology/v3/switches/{dpid}/disable on POST
        supported by
            /api/kytos/topology/v3/switches on GET
        """

        switch_id = "00:00:00:00:00:00:00:01"

        # Enable the switch
        api_url = KYTOS_API + '/topology/v3/switches/%s/enable' % switch_id
        requests.post(api_url)

        # Disable the switch
        api_url = KYTOS_API + '/topology/v3/switches/%s/disable' % switch_id
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        self.restart()

        # Check if the switch is disabled
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()
        assert data['switches'][switch_id]['enabled'] is False

    def test_040_removing_switch_metadata_persistent(self):
        """
        Test /api/kytos/topology/v3/switches/{dpid}/metadata/{key} on DELETED
        supported by:
            /api/kytos/topology/v3/switches/{dpid}/metadata on POST
            and
            /api/kytos/topology/v3/switches/{dpid}/metadata on GET
        """

        switch_id = "00:00:00:00:00:00:00:01"

        # Insert switch metadata
        payload = {"tmp_key": "tmp_value"}
        key = next(iter(payload))
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text

        self.restart()

        # Verify that the metadata is inserted
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.get(api_url)
        data = response.json()
        keys = data['metadata'].keys()
        assert key in keys

        # Delete the switch metadata
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata/%s' % (switch_id, key)
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Make sure the metadata is removed
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.get(api_url)
        data = response.json()
        keys = data['metadata'].keys()
        assert key not in keys

    def test_045_insert_switch_metadata_concurrently(self):
        """
        Test /api/kytos/topology/v3/switches/{dpid}/metadata/{key} on POST
        supported by:
            /api/kytos/topology/v3/switches/{dpid}/metadata on GET
        """
        switch_id = "00:00:00:00:00:00:00:01"

        n_keys = 100

        def insert_metadata(metadata):
            payload = metadata
            api_url = f"{KYTOS_API}/topology/v3/switches/{switch_id}/metadata"
            return requests.post(
                api_url,
                data=json.dumps(payload),
                headers={"Content-type": "application/json"},
            )

        metadatas = [{str(k): k for k in range(n_keys)}]
        with ThreadPoolExecutor(max_workers=n_keys) as executor:
            futures = [
                executor.submit(insert_metadata, metadata) for metadata in metadatas
            ]
            for future in as_completed(futures):
                response = future.result()
                assert response.status_code == 201, response.text

        # Verify that the metadata is inserted
        api_url = KYTOS_API + '/topology/v3/switches/%s/metadata' % switch_id
        response = requests.get(api_url)
        data = response.json()
        keys = list(data['metadata'].keys())
        expected_keys = [str(k) for k in range(n_keys)]
        diff = set(expected_keys) - set(keys)
        assert len(diff) == 0, f"Keys set difference: {diff}"

    def test_050_enabling_interface_persistent(self):
        """
        Test /api/kytos/topology/v3/interfaces/{interface_id}/enable on POST
        supported by
            /api/kytos/topology/v3/interfaces on GET
        """
        # Enable switch
        dpid = '00:00:00:00:00:00:00:01'
        api_url = f"{KYTOS_API}/topology/v3/switches/{dpid}/enable"
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        # Make sure the interfaces are disabled
        api_url = KYTOS_API + '/topology/v3/interfaces'
        response = requests.get(api_url)
        data = response.json()
        for interface in data['interfaces']:
            assert data['interfaces'][interface]['enabled'] is False

        interface_id = "00:00:00:00:00:00:00:01:4"

        # Enable the interface
        api_url = KYTOS_API + '/topology/v3/interfaces/%s/enable' % interface_id
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Check if the interface is enabled
        api_url = KYTOS_API + '/topology/v3/interfaces'
        response = requests.get(api_url)
        data = response.json()
        assert data['interfaces'][interface_id]['enabled'] is True

    def test_060_enabling_and_disabling_all_interfaces_on_a_switch_persistent(self):
        """
        Test /api/kytos/topology/v3/interfaces/switch/{dpid}/disable on POST
        supported by
            /api/kytos/topology/v3/switches on GET
        """
        # Enable switch
        switch_id = "00:00:00:00:00:00:00:01"
        api_url = f"{KYTOS_API}/topology/v3/switches/{switch_id}/enable"
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        # Make sure all the interfaces belonging to the target switch are disabled
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()

        for interface in data['switches'][switch_id]['interfaces']:
            assert data['switches'][switch_id]['interfaces'][interface]['enabled'] is False

        # Enabling all the interfaces
        api_url = KYTOS_API + '/topology/v3/interfaces/switch/%s/enable' % switch_id
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Make sure all the interfaces belonging to the target switch are enabled
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()

        for interface in data['switches'][switch_id]['interfaces']:
            assert data['switches'][switch_id]['interfaces'][interface]['enabled'] is True

        # Disabling all the interfaces
        api_url = KYTOS_API + '/topology/v3/interfaces/switch/%s/disable' % switch_id
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Make sure all the interfaces belonging to the target switch are disable
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()

        for interface in data['switches'][switch_id]['interfaces']:
            assert data['switches'][switch_id]['interfaces'][interface]['enabled'] is False

    def test_070_disabling_interface_persistent(self):
        """
        Test /api/kytos/topology/v3/interfaces/{interface_id}/disable on POST
        supported by:
            /api/kytos/topology/v3/interfaces/{interface_id}/enable on POST
            and
            /api/kytos/topology/v3/interfaces on GET
        """
        # Enable switch
        switch_id = "00:00:00:00:00:00:00:01"
        api_url = f"{KYTOS_API}/topology/v3/switches/{switch_id}/enable"
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        # Enable the interface
        interface_id = "00:00:00:00:00:00:00:01:4"
        api_url = KYTOS_API + '/topology/v3/interfaces/%s/enable' % interface_id
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Check if the interface is enabled
        api_url = KYTOS_API + '/topology/v3/interfaces'
        response = requests.get(api_url)
        data = response.json()
        assert data['interfaces'][interface_id]['enabled'] is True

        # Disable the interface and check if the interface is really disabled
        api_url = KYTOS_API + '/topology/v3/interfaces/%s/disable' % interface_id
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        api_url = KYTOS_API + '/topology/v3/interfaces'
        response = requests.get(api_url)
        data = response.json()
        assert data['interfaces'][interface_id]['enabled'] is False

    def test_080_disabling_all_interfaces_on_a_switch_persistent(self):
        """
        Test /api/kytos/topology/v3/interfaces/{interface_id}/disable on POST
        supported by:
            /api/kytos/topology/v3/interfaces/switch/{dpid}/enable on POST
            and
            /api/kytos/topology/v3/switches on GET
        """
        # Enable switch
        switch_id = "00:00:00:00:00:00:00:01"
        api_url = f"{KYTOS_API}/topology/v3/switches/{switch_id}/enable"
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        # Enabling all the interfaces
        api_url = KYTOS_API + '/topology/v3/interfaces/switch/%s/enable' % switch_id
        response = requests.post(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Make sure all the interfaces belonging to the target switch are enabled
        api_url = KYTOS_API + '/topology/v3/switches'
        response = requests.get(api_url)
        data = response.json()

        for interface in data['switches'][switch_id]['interfaces']:
            assert data['switches'][switch_id]['interfaces'][interface]['enabled'] is True

    def test_090_removing_interfaces_metadata_persistent(self):
        """
        Test /api/kytos/topology/v3/interfaces/{interface_id}/metadata/{key} on DELETE
        supported by:
            /api/kytos/topology/v3/interfaces/{interface_id}/metadata on POST
            and
            /api/kytos/topology/v3/interfaces/{interface_id}/metadata on GET
        """
        # It fails due to a bug, reported to Kytos team

        interface_id = "00:00:00:00:00:00:00:01:4"

        # Insert interface metadata
        payload = {"tmp_key": "tmp_value"}
        key = next(iter(payload))

        api_url = KYTOS_API + '/topology/v3/interfaces/%s/metadata' % interface_id
        response = requests.post(api_url, data=json.dumps(payload), headers={'Content-type': 'application/json'})
        assert response.status_code == 201, response.text

        self.restart()

        # Verify that the metadata is inserted
        api_url = KYTOS_API + '/topology/v3/interfaces/%s/metadata' % interface_id
        response = requests.get(api_url)
        data = response.json()
        keys = data['metadata'].keys()
        assert key in keys

        # Delete the interface metadata
        api_url = KYTOS_API + '/topology/v3/interfaces/%s/metadata/%s' % (interface_id, key)
        response = requests.delete(api_url)
        assert response.status_code == 200, response.text

        self.restart()

        # Make sure the metadata is removed
        api_url = KYTOS_API + '/topology/v3/interfaces/%s/metadata' % interface_id
        response = requests.get(api_url)
        data = response.json()
        keys = data['metadata'].keys()
        assert key not in keys

    def test_100_enabling_link_persistent(self):
        """
        Test /api/kytos/topology/v3/links/{link_id}/enable on POST
        supported by:
            /api/kytos/topology/v3/links on GET
        """

        endpoint_a = '00:00:00:00:00:00:00:01:3'
        endpoint_b = '00:00:00:00:00:00:00:02:2'

        # make sure the links are disabled by default
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()

        assert response.status_code == 200, response.text
        assert len(data['links']) == 0

        # Need to enable the switches and ports first
        for i in [1, 2, 3]:
            sw = "00:00:00:00:00:00:00:0%d" % i

            api_url = KYTOS_API + '/topology/v3/switches/%s/enable' % sw
            response = requests.post(api_url)
            assert response.status_code == 201, response.text

            api_url = KYTOS_API + '/topology/v3/interfaces/switch/%s/enable' % sw
            response = requests.post(api_url)
            assert response.status_code == 200, response.text

        self.restart()

        # now all the links should stay disabled
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert len(data['links']) == 3

        link_id1 = None
        for k, v in data['links'].items():
            link_a, link_b = v['endpoint_a']['id'], v['endpoint_b']['id']
            if {link_a, link_b} == {endpoint_a, endpoint_b}:
                link_id1 = k
        assert link_id1 is not None
        assert data['links'][link_id1]['enabled'] is False

        api_url = KYTOS_API + '/topology/v3/links/%s/enable' % link_id1
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        self.restart()

        # check if the links are now enabled
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert data['links'][link_id1]['enabled'] is True

    def test_110_disabling_link_persistent(self):
        """
        Test /api/kytos/topology/v3/links/{link_id}/disable on POST
        supported by:
            /api/kytos/topology/v3/links on GET
            and
            /api/kytos/topology/v3/links/{link_id}/enable on POST
        """

        endpoint_a = '00:00:00:00:00:00:00:01:3'
        endpoint_b = '00:00:00:00:00:00:00:02:2'

        # make sure the links are disabled by default
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()

        assert response.status_code == 200, response.text
        assert len(data['links']) == 0

        # enable the links (need to enable the switches and ports first)
        for i in [1, 2, 3]:
            sw = "00:00:00:00:00:00:00:0%d" % i

            api_url = KYTOS_API + '/topology/v3/switches/%s/enable' % sw
            response = requests.post(api_url)
            assert response.status_code == 201, response.text

            api_url = KYTOS_API + '/topology/v3/interfaces/switch/%s/enable' % sw
            response = requests.post(api_url)
            assert response.status_code == 200, response.text

        self.restart()

        # now all the links should stay disabled
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert len(data['links']) == 3

        link_id1 = None
        for k, v in data['links'].items():
            link_a, link_b = v['endpoint_a']['id'], v['endpoint_b']['id']
            if {link_a, link_b} == {endpoint_a, endpoint_b}:
                link_id1 = k
        assert link_id1 is not None
        assert data['links'][link_id1]['enabled'] is False

        api_url = KYTOS_API + '/topology/v3/links/%s/enable' % link_id1
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        # check if the links are now enabled
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert data['links'][link_id1]['enabled'] is True

        # restart kytos and check if the links are still enabled
        self.net.start_controller(clean_config=False)
        self.net.wait_switches_connect()

        # Wait a few seconds to kytos execute LLDP
        time.sleep(10)

        # check if the links are still enabled and now with the links
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert data['links'][link_id1]['enabled'] is True

        # disable the link
        api_url = KYTOS_API + '/topology/v3/links/%s/disable' % link_id1
        response = requests.post(api_url)
        assert response.status_code == 201, response.text

        # restart kytos and check if the links are still enabled
        self.net.start_controller(clean_config=False)
        self.net.wait_switches_connect()

        # Wait a few seconds to kytos execute LLDP
        time.sleep(10)

        # check if the links are still enabled and now with the links
        api_url = KYTOS_API + '/topology/v3/links'
        response = requests.get(api_url)
        data = response.json()
        assert data['links'][link_id1]['enabled'] is False

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
