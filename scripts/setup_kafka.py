import os
import asyncio

from aiokafka.admin import AIOKafkaAdminClient, NewTopic

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_HOST_ADDR", "localhost:9092").split(',')
KAFKA_TOPIC = "event_logs"

async def main() -> None:
    """ Setup event_logs if it does not exist """
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
    await admin.start()

    await admin.create_topics(
        [NewTopic(KAFKA_TOPIC, num_partitions=3, replication_factor=3)],
        timeout_ms=1000
    )

    await admin.close()

if __name__ == "__main__":
    asyncio.run(main())
