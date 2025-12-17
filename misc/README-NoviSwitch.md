# Running end-to-end tests with NoviSwitch

## Requirements

1. First of all, you will need to have access to virtual Noviflow switches, which are subject for purchase with Noviflow team (https://noviflow.com/noviswitch/). At AmLight we have a few virtual Noviflow switches that we can use to compose our test scenarios (for topologies that have more than what is available, we mix up with OpenVSwitch)

2. The execution environment can be just a Linux server with Virtualbox installed and all the tools needed for the end-to-end tests. However, we decided to implement our environment based on Kubernetes. Thus, the instructions below are based on the Kubernetes setup (but you can easily adapt for a standalone/simple Linux based solution). For the Kubernetes integration, you will need to install this Controller: https://github.com/italovalcy/vboxvms-k8s-ctrl (after installing the controller, we added the Virtual Noviflow switches as the templates according to the number of available licenses)

3. Be aware that when creating topologies, the maximum port number for Noviflow switches is 16 (default port mapping from Tofino-model)

## Environment variables

Before running the end-to-end tests, some environment variables need to be configured to enable tests to run correctly:

- ``NOVISWITCHES``: this variable is **mandatory** and should contain a comma-separated list of IP addresses for each virtual Noviflow switch that is available for the test. Example: ``export NOVISWITCHES=192.168.56.101,192.168.56.102``

- ``NOVIUSER``: this variable is **mandatory** and should contain the username used for SSH authentication with Noviflow switch. The username has to be for a privileged user (example: superuser).

- ``NOVIPASS``: this variable is **mandatory** and should contain the password used for SSH authentication with Noviflow switch.

- ``SWITCH_CLASS``: this variable is **very important** to actually run the tests with Noviflow backend. When using the NoviSwitch backend, Mininet will try to allocate virtual Noviflow switches as much as possible, meaning: if your topology has 10 switches, but you only provided 6 virtual switches (i.e., your ``NOVISWITCHES`` have 6 IP addresses) then your topology will be mixed with 6 virtual Noviflow switches plus 4 OpenVSwitch for backwards compatibility. Thus, if you want to have Noviflows switches as the backend for Mininet, you have set this variable with: ``export SWITCH_CLASS=NoviSwitch``.

- ``NOVISETTINGS``: this variable is **optional** and when set should be a JSON encoded string or file containing more information about the switches, which allows for instance to have different names for each switch. Example: ``export NOVISETTINGS='{"vNovi01": {"ip": "192.168.56.101"}, "vNovi02": {"ip": "192.168.56.102"}}'``

## Running end-to-end tests with NoviSwitch

Overall steps are described below:

```
KUBECONFIG=~/.kube/config

kubectl --kubeconfig $KUBECONFIG apply -f kubernetes-virtual-noviflow.yaml

kubectl --kubeconfig $KUBECONFIG apply -f kubernetes-kytos-mongo-kafka.yaml

kubectl --kubeconfig $KUBECONFIG rollout status deployment/kytos-regression-tests --timeout=120s

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container mongo1 -- mongosh --eval 'db.getSiblingDB("kytosdb").createUser({user: "kytosuser", pwd: "kytospass", roles: [ { role: "dbAdmin", db: "kytosdb" } ]})'

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- git clone --branch feat/adding-noviswitch-backend https://github.com/kytos-ng/kytos-end-to-end-tests

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c "service rsyslog start; service openvswitch-switch start; apt-get update; apt-get install -y python3-paramiko openssh-client"

kubectl --kubeconfig $KUBECONFIG wait --for=jsonpath='{.status.phase}'=Running vboxvms vnoviflow0{1,2,3,4,5,6} --timeout=10m

NOVISWITCHES=$(kubectl --kubeconfig $KUBECONFIG get vboxvms -o=jsonpath="{.items[*]['.ip']}")

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c "cd kytos-end-to-end-tests/; env NOVIPASS=noviflow NOVIUSER=superuser NOVISWITCHES='$NOVISWITCHES' python3 scripts/wait_for_novissh.py"

# temporary while fixing issues with Kafka events timeout
kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- bash -c 'sed -ri "s/LINGER_MS = .*/LINGER_MS = 0/g; s/KAFKA_TIMELIMIT = .*/KAFKA_TIMELIMIT = 10/g" $NAPPS_PATH/var/lib/kytos/napps/kytos/kafka_events/settings.py'

kubectl --kubeconfig $KUBECONFIG exec -it deployment/kytos-regression-tests --container kytos -- tmux new-session -d -s kytos-tests -e SWITCH_CLASS=NoviSwitch -e NOVIPASS=noviflow -e NOVIUSER=superuser -e NOVISWITCHES="$NOVISWITCHES" -e RERUNS=4 bash -c 'cd kytos-end-to-end-tests/; ./kytos-init.sh 2>&1 | tee /results-kytos-e2e.txt'
```
