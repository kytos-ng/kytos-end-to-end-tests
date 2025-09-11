"""
Kafka bootstrap script to create the event_logs topic
"""
import os
import asyncio

from typing import Any

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaConnectionError

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_HOST_ADDR", "localhost:29092")
KAFKA_TOPIC = "event_logs"

def bootstrap_servers_list(bootstrap_servers: str) -> list[str]:
    """Format the given bootstrap servers into ip:port"""
    return bootstrap_servers.split(",")

async def create_admin_client(bootstrap_servers: list[str]) -> AIOKafkaAdminClient:
    """Create and initialize an admin client, using a list of bootstrap servers"""
    admin = AIOKafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)

    try:
        await admin.start()
        return admin
    except KafkaConnectionError as exc:
        print(f"Unable to establish initial connection with Kafka servers at {bootstrap_servers}.")
        raise exc
    except Exception as exc:
        print("An unknown error has occurred.")
        raise exc

async def validate_cluster(admin: AIOKafkaAdminClient) -> None:
    """
    Using an admin client, validate that the cluster is healthy and all the nodes are operational.

    The connection is implicitly tested as the Kafka admin client must establish a stable
    connection with the cluster to get their metadata.
    """
    try:
        cluster_metadata: dict[str, Any] = await admin.describe_cluster()
        print(f"Cluster info: {cluster_metadata}")
    except KafkaConnectionError as exc:
        print("Unable to connect to cluster for validation.")
        raise exc
    except Exception as exc:
        print("An unknown issue occurred while validating.")
        raise exc

async def shutdown(admin: AIOKafkaAdminClient) -> None:
    """Attempt the shutdown sequence"""
    try:
        await admin.close()
    except KafkaConnectionError as exc:
        print("Unable to shutdown admin.")
        raise exc
    except Exception as exc:
        print("An unknown issue occurred while shutting down.")
        raise exc

async def create_topic(admin: AIOKafkaAdminClient) -> None:
    """Attempt to create 'event_logs'"""
    try:
        await admin.create_topics(
            [NewTopic(KAFKA_TOPIC, num_partitions=3, replication_factor=3)],
            timeout_ms=5000
        )
        await asyncio.sleep(2) # Let the topic propagate
    except KafkaConnectionError as exc:
        print("Unable to create topic.")
        raise exc
    except Exception as exc:
        print(f"An unknown issue occurred while creating {KAFKA_TOPIC}")
        raise exc


async def main() -> None:
    """ Setup event_logs if it does not exist """
    print("Starting setup_kafka.py...")

    bootstrap_servers: list[str] = bootstrap_servers_list(KAFKA_BOOTSTRAP_SERVERS)
    print(f"Attempting to create an admin client at {bootstrap_servers}...")

    admin = await create_admin_client(bootstrap_servers)
    print("Admin client was successful! Attempting to validate cluster...")

    await validate_cluster(admin)
    print(f"Cluster was successfully validated! Attempting to creat topic '{KAFKA_TOPIC}'...")

    await create_topic(admin)
    print(f"Topic '{KAFKA_TOPIC} was created! Attempting to close the admin client...")

    await shutdown(admin)
    print("Kafka admin client closed.")

if __name__ == "__main__":
    asyncio.run(main())
