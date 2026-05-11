## Running Kytos end-to-end tests with P4OfSwitch

You can run the end-to-end tests with Kytos based on the P4OfSwitch available on Docker image `amlight/p4ofswitch:latest` (Github repo to be published soon) and Mininet with Docker support (Docker-in-Docker exec methodology) based on this forked version (https://github.com/italovalcy/mininet/releases/tag/2.3.2).

Follow the steps below to run the tests:
```
docker run -d --name mongo2 mongo:7.0

docker exec -it mongo2 mongosh --eval 'db.getSiblingDB("k2").createUser({user: "k2", pwd: "k2", roles: [ { role: "dbAdmin", db: "k2" } ]})'

docker run -d --name kafka2 --hostname kafka2 -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@127.0.0.1:9093 -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka2:9092  apache/kafka:4.0.0

docker run -d --name k2 --privileged -v /home/italo/docker:/var/lib/docker -v /lib/modules:/lib/modules --link mongo2 --link kafka2 -e SWITCH_CLASS=P4OfSwitch -e MONGO_DBNAME=k2 -e MONGO_USERNAME=k2 -e MONGO_PASSWORD=k2 -e MONGO_HOST_SEEDS=mongo2:27017  -e KAFKA_HOST_ADDR=kafka2:9092 --pull always --init amlight/kytos:latest /usr/bin/tail -f /dev/null

docker exec -it k2 bash

# all steps below are executed inside the k2 docker container:

apt-get update

apt-get install -y --no-install-recommends --no-install-suggests gnupg

curl -LO https://github.com/italovalcy/mininet/releases/download/2.3.2/mininet_2.3.2-1--debian12_amd64.deb

apt-get install -y ./mininet_2.3.2-1--debian12_amd64.deb

install -m 0755 -d /etc/apt/keyrings  && curl -fsSL https://download.docker.com/linux/debian/gpg     | gpg --dearmor -o /etc/apt/keyrings/docker.gpg  && chmod a+r /etc/apt/keyrings/docker.gpg  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable"     | tee /etc/apt/sources.list.d/docker.list

apt-get update && apt-get install --no-install-recommends --no-install-suggests -y     docker-ce     docker-ce-cli     containerd.io     docker-buildx-plugin     docker-compose-plugin

setsid dockerd > /var/log/dockerd.log 2>&1 &

until docker info > /dev/null 2>&1; do sleep 1; done

docker pull amlight/p4ofswitch:latest

git clone https://github.com/kytos-ng/kytos-end-to-end-tests

cd kytos-end-to-end-tests/

git checkout feat/p4ofswitch_refactored

tmux new-sess -d -s e2e-tests bash -c 'env TESTS="tests/" RERUNS=2 ./kytos-init.sh 2>&1 | tee e2e-results-1.log; bash'
```
