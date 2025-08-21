"""
Kafka bootstrap script to create the event_logs topic
"""
import os
import asyncio
import sys

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError, KafkaConnectionError

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_HOST_ADDR", "localhost:9092").split(',')
KAFKA_TOPIC = "event_logs"

async def main() -> None:
    """ Setup event_logs if it does not exist """
    print(f"Connecting to Kafka brokers: {KAFKA_BOOTSTRAP_SERVERS}")

    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

    try:
        await admin.start()
        print("Connected to Kafka successfully")

        # Check if topic already exists
        existing_topics: list[str] = await admin.list_topics()
        if KAFKA_TOPIC in existing_topics:
            print(f"Topic '{KAFKA_TOPIC}' already exists")
            return

        print(f"Creating topic '{KAFKA_TOPIC}'...")
        await admin.create_topics(
            [NewTopic(KAFKA_TOPIC, num_partitions=3, replication_factor=3)],
            timeout_ms=1000
        )
        print(f"Topic '{KAFKA_TOPIC}' created successfully")

    except TopicAlreadyExistsError:
        print(f"Topic '{KAFKA_TOPIC}' already exists")

    except KafkaConnectionError as e:
        print(f"Failed to connect to Kafka: {e}")
        sys.exit(1)

    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

    finally:
        await admin.close()
        print("Kafka admin client closed")

if __name__ == "__main__":
    asyncio.run(main())
