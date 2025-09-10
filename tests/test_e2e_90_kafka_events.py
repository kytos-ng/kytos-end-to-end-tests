import os
import time
import json
import asyncio

import pytest
import requests
from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from tests.helpers import NetworkTest

CONTROLLER = '127.0.0.1'
KYTOS_API = 'http://%s:8181/api/kytos' % CONTROLLER
KAFKA_TOPIC = "event_logs"
TIMEOUT = 1000
KAFKA_ADDRESSES = os.environ.get("KAFKA_HOST_ADDR").split(',')

class TestE2EKafkaEvents:
    net = None
    admin = None

    def setup_method(self, method):
        """
        It is called at the beginning of every class method execution
        """
        # Start the controller setting an environment in
        # which all elements are disabled in a clean setting
        self.net.config_all_links_up()
        self.net.start_controller(clean_config=True, enable_all=False)
        self.net.wait_switches_connect()
        time.sleep(5)

    @classmethod
    def setup_class(cls):
        cls.net = NetworkTest(CONTROLLER)
        cls.net.start()
        cls.net.wait_switches_connect()

    @classmethod
    def teardown_class(cls):
        cls.net.stop()

    @pytest.fixture(autouse=True)
    async def setup_kafka(self):
        """
        Pytest fixture that runs every time
        """
        # Create & setup the admin client
        self.admin = AIOKafkaAdminClient(
            bootstrap_servers=KAFKA_ADDRESSES
        )
        await self.admin.start()

        # Create the kafka topic
        await self.create_kafka_topic()

        # Run the test
        yield

        # Tear down
        await self.teardown_kafka_topic()

        # Close the admin client
        await self.admin.close()

    async def teardown_kafka_topic(self):
        """
        Tears down the Kafka topic to be rebuilt
        """
        await self.admin.delete_topics([KAFKA_TOPIC], TIMEOUT)
        # Let the topic deletion propagate
        await asyncio.sleep(1)

    async def create_kafka_topic(self):
        """
        Creates the Kafka topic
        """
        await self.admin.create_topics(
            [NewTopic(KAFKA_TOPIC, num_partitions=3, replication_factor=3)],
            timeout_ms=5000
        )
        # Let the topic creation propagate
        await asyncio.sleep(2)

    async def test_01_napp_sends_data_correctly(self):
        """
        Test that kafka_events correctly runs the 'setup' method. This would require
        that the AIOKafkaProducer has been properly initialized
        """
        # Create a consumer before the message is propagated to reduce the amount of messages

        consumer = AIOKafkaConsumer(
            (KAFKA_TOPIC),
            bootstrap_servers=KAFKA_ADDRESSES,
        )

        await consumer.start()

        # Create an EVC-creation event to send to Kafka

        evc_name = "Vlan_%s" % 902

        payload = {
            "name": evc_name,
            "enabled": True,
            "dynamic_backup_path": True,
            "uni_a": {
                "interface_id": "00:00:00:00:00:00:00:01:1",
                "tag": {"tag_type": "vlan", "value": 902}
            },
            "uni_z": {
                "interface_id": "00:00:00:00:00:00:00:03:1",
                "tag": {"tag_type": "vlan", "value": 902}
            }
        }
        api_url = KYTOS_API + '/mef_eline/v2/evc/'
        response = requests.post(
            api_url,
            data=json.dumps(payload),
            headers={'Content-type': 'application/json'},
            timeout=5
        )

        assert response.status_code == 201, response.text

        # Wait for the message to propagate

        await asyncio.sleep(32)

        # Collect the message from Kafka

        try:
            # Wait up to 1 second for messages
            results = await consumer.getmany(timeout_ms=1000)

            # Ensure values exist
            assert results.values()

            found = False

            for messages in results.values():
                for msg in messages:
                    event = json.loads(msg.value.decode())
                    if event["event"] == 'kytos/mef_eline.created':
                        found = True
                        break

            assert found

        finally:
            await consumer.stop()
