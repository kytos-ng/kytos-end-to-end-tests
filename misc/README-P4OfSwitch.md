## Running Kytos end-to-end tests with P4OfSwitch

You can run the end-to-end tests with Kytos based on the P4OfSwitch available on Docker image `amlight/p4ofswitch:latest` (Github repo to be published soon) and Mininet with Docker support (Docker-in-Docker exec methodology) based on this forked version (https://github.com/italovalcy/mininet/releases/tag/2.3.2).

The following environment variables can be used to customize the behavior of the end-to-end tests with P4OfSwitch:

- `SWITCH_CLASS`: of cource, this should be set to "P4OfSwitch" to activate the P4OfSwitch backend
- `P4OFSWITCH_ARCH`: the Tofino Model architecture that will be simulated, it can be `tf1` (Tofino 1 switch, with 64 x 100G ports) or `tf2` (Tofino 2 switch, 32 x 400G ports)
- `P4OFSWITCH_IMAGE`: the docker image to be used to run the switches, by default it points to `amlight/p4ofswitch:latest` but you can customize to target a testing or pre-release tag, etc.
- `MYLOCALIP`: if you dont specify, it will be dynamically be obtained using linux `iproute` package (testing connectivity to `8.8.8.8` by default). This parameter is important for configuring the OpenFlow controller on the docker containers running the P4OfAgent switch (keep in mind that we cannot use `127.0.0.1` there, which would point to the container itself, instead of the host running Kytos/mininet)

**Pre-requirements:**

1. Mininet with Docker hosts support (https://github.com/italovalcy/mininet/releases/tag/2.3.2)

2. Docker daemon

3. Memory Huge Pages

To run Tofino Model and `bf_switchd` (open-studio), you are required to provide Kernel Huge Pages memory feature. On our tests, each P4OfSwitch/TofinoModel container will consume around 81 Huge Pages (standard 2MB page size is enough). Since our current biggest topology is AmLightTopo (tests/helpers.py), 1024 huge pages should be enough. Make sure you apply the following setting to your running host:

```
sysctl vm.nr_hugepages=1024
mount -t hugetlbfs none /dev/hugepages
```

## Running with Docker

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

## Running with Kubernetes

```
export KUBECONFIG=~/.kube/config
export E2E_BRANCH=master

kubectl --kubeconfig $KUBECONFIG apply -f misc/kubernetes-kytos-mongo-kafka.yaml

kubectl --kubeconfig $KUBECONFIG rollout status deployment/kytos-regression-tests --timeout=120s

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container mongo1 -- mongosh --eval 'db.getSiblingDB("kytosdb").createUser({user: "kytosuser", pwd: "kytospass", roles: [ { role: "dbAdmin", db: "kytosdb" } ]})'

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c "sysctl vm.nr_hugepages=1024; service rsyslog start;"

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- git clone --branch $E2E_BRANCH https://github.com/kytos-ng/kytos-end-to-end-tests

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c "cd /tmp; curl -LO https://github.com/italovalcy/mininet/releases/download/2.3.2/mininet_2.3.2-1--debian12_amd64.deb; apt-get update; apt-get install ./mininet_2.3.2-1--debian12_amd64.deb; apt-get install --no-install-recommends -y gpg"

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c 'install -m 0755 -d /etc/apt/keyrings  && curl -fsSL https://download.docker.com/linux/debian/gpg     | gpg --dearmor -o /etc/apt/keyrings/docker.gpg  && chmod a+r /etc/apt/keyrings/docker.gpg  && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable"     | tee /etc/apt/sources.list.d/docker.list'

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c "apt-get update && apt-get install --no-install-recommends --no-install-suggests -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c "setsid dockerd > /var/log/dockerd.log 2>&1 & true; until docker info >/dev/null 2>&1; do sleep 1; done"

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- tmux new-session -d -s kytos-tests -e SWITCH_CLASS=P4OfSwitch -e RERUNS=2 bash -c 'cd kytos-end-to-end-tests/; ./kytos-init.sh 2>&1 | tee /results-kytos-e2e.txt; bash'
```
