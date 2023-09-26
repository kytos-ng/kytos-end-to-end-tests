import time
import json

from tests.helpers import NetworkTest
import requests

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api' % CONTROLLER
KYTOS_STATS = KYTOS_API + '/amlight/kytos_stats/v1'

class TestE2EKytosStats:
    
    def setup_method(self, method):
        """Called at the beginning of each class method"""
        self.net.start_controller(clean_config=True, enable_all=True)
        self.net.wait_switches_connect()
        time.sleep(10)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.restart_kytos_clean()
        time.sleep(10)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def test_005_flow_stats(self):
        """Test flow_stats"""
 
        api_url = KYTOS_STATS + '/flow/stats?dpid=00:00:00:00:00:00:00:01'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 1
        assert '00:00:00:00:00:00:00:01' in data, str(data)

        api_url = KYTOS_STATS + '/flow/stats?dpid=00:00:00:00:00:00:00:01&dpid=00:00:00:00:00:00:00:02'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 2
        assert '00:00:00:00:00:00:00:01' in data, str(data)
        assert '00:00:00:00:00:00:00:02' in data, str(data)

        api_url = KYTOS_STATS + '/flow/stats'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()

        api_url = KYTOS_API + '/kytos/topology/v3/switches'
        response = requests.get(api_url)
        data_topp = response.json()
        assert len(data) == len(data_topp['switches'])

    def test_010_table_stats(self):
        """Test table_stats""" 

        api_url = KYTOS_STATS + '/table/stats?dpid=00:00:00:00:00:00:00:01' 
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 1
        assert '00:00:00:00:00:00:00:01' in data, str(data)

        api_url = KYTOS_STATS + '/table/stats?dpid=00:00:00:00:00:00:00:01&dpid=00:00:00:00:00:00:00:02' 
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 2
        assert '00:00:00:00:00:00:00:01' in data, str(data)
        assert '00:00:00:00:00:00:00:02' in data, str(data)

        api_url = KYTOS_API + '/kytos/topology/v3/switches'
        response = requests.get(api_url)
        data_topp = response.json()
        topo_switches = data_topp['switches']

        api_url = KYTOS_STATS + '/table/stats?table=0'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == len(topo_switches)
        for sw in topo_switches:
            assert len(data[sw]) == 1
            assert '0' in data[sw], str(data)

        api_url = KYTOS_STATS + '/table/stats?table=0&table=1'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == len(topo_switches)
        for sw in topo_switches:
            assert len(data[sw]) == 2
            assert '0' in data[sw], str(data)
            assert '1' in data[sw], str(data)

        api_url = KYTOS_STATS + '/table/stats?dpid=00:00:00:00:00:00:00:01&table=0'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == 1
        assert len(data['00:00:00:00:00:00:00:01']) == 1
        assert '0' in data['00:00:00:00:00:00:00:01'], str(data)

        api_url = KYTOS_STATS + '/table/stats'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data) == len(topo_switches)

    def test_015_packet_count(self):
        """Test packet_count""" 
        sw = "00:00:00:00:00:00:00:01"

        # install a flow
        cookie = 5
        payload = {
            "flows": [{
                "cookie": cookie,
                "match": {"in_port": 1, 'dl_dst': '33:33:00:00:00:02', 'dl_type': 0x86dd},
                'actions': [{'action_type': 'output', 'port': 2}]
            }]
        }

        api_url_flow_manager = KYTOS_API + f'/kytos/flow_manager/v2/flows/{sw}'
        response = requests.post(api_url_flow_manager, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data_flow = response.json()
        assert 'FlowMod Messages Sent' in data_flow['response']

        # wait the flow to be installed
        time.sleep(5)

        # send N packets, each one containing 1500 bytes
        # (14 ether hdr + 40 ipv6 + 8 icmp + 1438 payload)
        h11 = self.net.net.get('h11')
        n = 20
        h11.cmd(f"ping -6 -b -c {n} -s 1438 FF02::2%h11-eth0 -Mdo -i 0.01 -W 2")

        # give enough time for stats gathering (of_core.STATS_INTERVAL)
        time.sleep(10)

        api_url = KYTOS_STATS + f'/flow/stats?dpid={sw}'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data_flow = response.json()[sw]
        for flow_id, flow in data_flow.items():
            if flow['cookie'] == cookie:
                packet_counter = flow['packet_count']
                assert packet_counter >= n, str(flow)
                packet_per_second = packet_counter/flow['duration_sec']
                break
        
        api_url = KYTOS_STATS + f'/packet_count/{flow_id}' 
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['flow_id'] == flow_id
        assert data['packet_counter'] == packet_counter
        assert data['packet_per_second'] == packet_per_second

    def test_020_bytes_count(self):
        """Test bytes_count""" 
        sw = "00:00:00:00:00:00:00:01"

        # install a flow
        cookie = 5
        payload = {
            "flows": [{
                "cookie": cookie,
                "match": {"in_port": 1, 'dl_dst': '33:33:00:00:00:02', 'dl_type': 0x86dd},
                'actions': [{'action_type': 'output', 'port': 2}]
            }]
        }

        api_url_flow_manager = KYTOS_API + f'/kytos/flow_manager/v2/flows/{sw}'
        response = requests.post(api_url_flow_manager, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data_flow = response.json()
        assert 'FlowMod Messages Sent' in data_flow['response']

        # wait the flow to be installed
        time.sleep(5)

        # send N packets, each one containing 1500 bytes
        # (14 ether hdr + 40 ipv6 + 8 icmp + 1438 payload)
        h11 = self.net.net.get('h11')
        n = 20
        h11.cmd(f"ping -6 -b -c {n} -s 1438 FF02::2%h11-eth0 -Mdo -i 0.01 -W 2")

        # waiting to give enough time for stats gathering (of_core.STATS_INTERVAL)
        time.sleep(10)

        api_url = KYTOS_STATS + f'/flow/stats?dpid={sw}'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data_flow = response.json()[sw]
        for flow_id, flow in data_flow.items():
            if flow['cookie'] == cookie:
                bytes_counter = flow['byte_count']
                assert bytes_counter >= n*1500, str(flow)
                bits_per_second = 8*bytes_counter/flow['duration_sec']
                break
        
        api_url = KYTOS_STATS + f'/bytes_count/{flow_id}' 
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['flow_id'] == flow_id
        assert data['bytes_counter'] == bytes_counter
        assert data['bits_per_second'] == bits_per_second

    def test_025_packet_count_per_flow(self):
        """Test packet_count_per_flow""" 
        api_url = KYTOS_STATS + '/flow/stats?dpid=00:00:00:00:00:00:00:01'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data_flow = response.json()['00:00:00:00:00:00:00:01']

        api_url = KYTOS_STATS + '/packet_count/per_flow/00:00:00:00:00:00:00:01' 
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        for info_flow in data:
            flow_id = info_flow['flow_id']
            count = data_flow[flow_id]['packet_count']
            assert info_flow['packet_counter'] == count
            assert info_flow['packet_per_second'] == count/data_flow[flow_id]['duration_sec']


    def test_030_bytes_count_per_flow(self):
        """Test bytes_count_per_flow""" 

        api_url = KYTOS_STATS + '/flow/stats?dpid=00:00:00:00:00:00:00:01'  
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data_flow = response.json()['00:00:00:00:00:00:00:01']

        api_url = KYTOS_STATS + '/bytes_count/per_flow/00:00:00:00:00:00:00:01' 
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()
        for info_flow in data:
            flow_id = info_flow['flow_id']
            count = data_flow[flow_id]['byte_count']
            assert info_flow['bytes_counter'] == count
            assert info_flow['bits_per_second'] == 8 * count/data_flow[flow_id]['duration_sec']

    def test_035_table_fields_update(self):
        """Test fields are updating on table 0.
        active_count increments only when new flows are added
        matched_count and lookup_count keep incrementing
        """ 

        api_url = KYTOS_STATS + '/table/stats?table=0'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()

        # install a flow
        payload = {"flows": [{"match": {"in_port": 1}}]}

        api_url_flow_manager = KYTOS_API + '/kytos/flow_manager/v2/flows/00:00:00:00:00:00:00:01'
        response = requests.post(api_url_flow_manager, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data_flow = response.json()
        assert 'FlowMod Messages Sent' in data_flow['response']

        time.sleep(10)

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data_1 = response.json()
        increase_active_count = False
        increase_lookup_count = False
        increase_matched_count = False

        for sw in data_1:
            if data[sw]['0']['active_count'] < data_1[sw]['0']['active_count']:
                increase_active_count = True
            if data[sw]['0']['lookup_count'] < data_1[sw]['0']['lookup_count']:
                increase_lookup_count = True
            if data[sw]['0']['matched_count'] < data_1[sw]['0']['matched_count']:
                increase_matched_count = True
        assert increase_active_count
        assert increase_lookup_count
        assert increase_matched_count

    def test_036_table_1_active_count_update(self):
        """Test active count are increasing on table 1.
        """ 

        api_url = KYTOS_STATS + '/table/stats?table=1'
        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data = response.json()

        # install a flow
        payload = {"flows": [{"table_id": 1, "match": {"in_port": 1}}]}
        sw = "00:00:00:00:00:00:00:01"
        api_url_flow_manager = KYTOS_API + f'/kytos/flow_manager/v2/flows/{sw}'
        response = requests.post(api_url_flow_manager, data=json.dumps(payload),
                                 headers={'Content-type': 'application/json'})
        assert response.status_code == 202, response.text
        data_flow = response.json()
        assert 'FlowMod Messages Sent' in data_flow['response']

        time.sleep(10)

        response = requests.get(api_url)
        assert response.status_code == 200, response.text
        data_1 = response.json()

        assert data[sw]['1']['active_count'] < data_1[sw]['1']['active_count']

