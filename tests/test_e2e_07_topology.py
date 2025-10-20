import asyncio
import datetime
import httpx
import time
from datetime import datetime, timedelta, UTC, timezone


from .helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api' % CONTROLLER

TIME_FMT = "%Y-%m-%dT%H:%M:%S+0000"

class TestE2EMefEline:
    net = None

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start_controller(clean_config=True, enable_all=False)
        time.sleep(5)

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    def setup_method(self, method):
        self.net.start_controller(clean_config=True, enable_all=False)
        time.sleep(5)

    def teardown_method(self, method):
        pass

    async def test_010_topology_race_condition(self):

        self.net.stop_kytosd()

        link_count = 1000

        # fake db entries
        switch_entry = {
            '_id': '00:00:00:00:00:00:00:01',
            'connection': '127.0.0.1:50074',
            'data_path': 's1',
            'enabled': False,
            'hardware': 'Open vSwitch',
            'id': '00:00:00:00:00:00:00:01',
            'inserted_at': datetime(
                2025,
                10,
                9,
                18,
                26,
                33,
                144000
            ),
            'interfaces': [
                {
                    'id': '00:00:00:00:00:00:00:01:4294967294',
                    'enabled': False,
                    'mac': '6e:3c:76:aa:05:40',
                    'speed': 0.0,
                    'port_number': 4294967294,
                    'name': 's1',
                    'nni': False,
                    'lldp': False,
                    'switch': '00:00:00:00:00:00:00:01',
                    'link': '',
                    'link_side': None,
                    'metadata': {},
                    'updated_at': None
                },
                *[
                    {
                        'id': f'00:00:00:00:00:00:00:01:{i}',
                        'enabled': False,
                        'mac': 'be:2a:12:c4:c4:52',
                        'speed': 1250000000.0,
                        'port_number': i,
                        'name': f's1-eth{i}',
                        'nni': False,
                        'lldp': False,
                        'switch': '00:00:00:00:00:00:00:01',
                        'link': '',
                        'link_side': None,
                        'metadata': {},
                        'updated_at': None
                    }
                    for i in range(1, link_count + 1)
                ]
            ],
            'manufacturer': 'Nicira, Inc.',
            'metadata': {},
            'ofp_version': '0x04',
            'serial': 'None',
            'software': '3.3.0',
            'updated_at': datetime(
                2025,
                10,
                9,
                18,
                26,
                33,
                172000
            )
        }
        self.net.db["switches"].insert_one(switch_entry)

        self.net.start_controller()

        time.sleep(5)

        api_url = KYTOS_API + "/kytos/topology/v3/"

        response = httpx.get(api_url)

        assert response.status_code == 200, response.text

        data = response.json()

        assert "00:00:00:00:00:00:00:01" in data["topology"]["switches"]

        async def update_interface(client: httpx.AsyncClient, interface_id):
            api_url = KYTOS_API + f"/kytos/topology/v3/interfaces/{interface_id}/disable"
            response = await client.post(api_url)
            assert response.status_code < 500, response.text

        async def delete_switch(client: httpx.AsyncClient, switch_id):
            api_url = KYTOS_API + f"/kytos/topology/v3/switches/{switch_id}"
            response = await client.delete(api_url)
            assert response.status_code == 200, response.text

        async with httpx.AsyncClient(timeout=120) as client:
            await asyncio.gather(
                update_interface(client, '00:00:00:00:00:00:00:01:4294967294'),
                *[
                    update_interface(client, f'00:00:00:00:00:00:00:01:{i}')
                    for i in range(1, link_count // 2)
                ],
                delete_switch(client, '00:00:00:00:00:00:00:01'),
                *[
                    update_interface(client, f'00:00:00:00:00:00:00:01:{i}')
                    for i in range(link_count // 2, link_count + 1)
                ],
            )

        self.net.stop_kytosd()

        remaining_switches = list(self.net.db["switches"].find({}))

        assert not remaining_switches

    async def test_020_topology_race_condition(self):

        self.net.stop_kytosd()

        link_count = 1000
        attempt_count = 100

        # fake db entries
        switch_entry = {
            '_id': '00:00:00:00:00:00:00:01',
            'connection': '127.0.0.1:50074',
            'data_path': 's1',
            'enabled': False,
            'hardware': 'Open vSwitch',
            'id': '00:00:00:00:00:00:00:01',
            'inserted_at': datetime(
                2025,
                10,
                9,
                18,
                26,
                33,
                144000
            ),
            'interfaces': [
                {
                    'id': '00:00:00:00:00:00:00:01:4294967294',
                    'enabled': False,
                    'mac': '6e:3c:76:aa:05:40',
                    'speed': 0.0,
                    'port_number': 4294967294,
                    'name': 's1',
                    'nni': False,
                    'lldp': False,
                    'switch': '00:00:00:00:00:00:00:01',
                    'link': '',
                    'link_side': None,
                    'metadata': {},
                    'updated_at': None
                },
                *[
                    {
                        'id': f'00:00:00:00:00:00:00:01:{i}',
                        'enabled': False,
                        'mac': 'be:2a:12:c4:c4:52',
                        'speed': 1250000000.0,
                        'port_number': i,
                        'name': f's1-eth{i}',
                        'nni': False,
                        'lldp': False,
                        'switch': '00:00:00:00:00:00:00:01',
                        'link': '',
                        'link_side': None,
                        'metadata': {},
                        'updated_at': None
                    }
                    for i in range(1, link_count + 1)
                ]
            ],
            'manufacturer': 'Nicira, Inc.',
            'metadata': {},
            'ofp_version': '0x04',
            'serial': 'None',
            'software': '3.3.0',
            'updated_at': datetime(
                2025,
                10,
                9,
                18,
                26,
                33,
                172000
            )
        }
        self.net.db["switches"].insert_one(switch_entry)

        self.net.start_controller()

        time.sleep(5)

        api_url = KYTOS_API + "/kytos/topology/v3/"

        response = httpx.get(api_url)

        assert response.status_code == 200, response.text

        data = response.json()

        assert "00:00:00:00:00:00:00:01" in data["topology"]["switches"]

        async def update_interfaces(client: httpx.AsyncClient, switch_id):
            api_url = KYTOS_API + f"/kytos/topology/v3/interfaces/switch/{switch_id}/disable"
            response = await client.post(api_url)
            assert response.status_code < 500, response.text

        async def delete_switch(client: httpx.AsyncClient, switch_id):
            api_url = KYTOS_API + f"/kytos/topology/v3/switches/{switch_id}"
            response = await client.delete(api_url)
            assert response.status_code == 200, response.text

        async with httpx.AsyncClient(timeout=120) as client:
            await asyncio.gather(
                *[
                    update_interfaces(client, '00:00:00:00:00:00:00:01')
                    for i in range(1, attempt_count // 2)
                ],
                delete_switch(client, '00:00:00:00:00:00:00:01'),
                *[
                    update_interfaces(client, '00:00:00:00:00:00:00:01:')
                    for i in range(attempt_count // 2, attempt_count + 1)
                ],
            )

            api_url = KYTOS_API + f"/kytos/topology/v3/switches"

            response = await client.get(api_url)

            assert response.status_code == 200, response.text

            data = response.json()

            assert not data["switches"]

        self.net.stop_kytosd()

        remaining_switches = list(self.net.db["switches"].find({}))

        assert not remaining_switches
