#!/bin/bash

set -xe

# Checks if there is an NAPPS_PATH specified
# If not it will then utilize the VIRTUAL_ENV path
# If a path is not found it will then use the Default Path for NApps
if ! [ -z "$NAPPS_PATH" ]; then
echo "NAPPS_PATH is specified."
elif ! [ -z "$VIRTUAL_ENV" ]; then
echo "NAPPS_PATH was not specified. The $VIRTUAL_ENV virtual environment path will be used."
NAPPS_PATH=$VIRTUAL_ENV
else
echo "There is no NAPPS_PATH specified. Default will be used."
NAPPS_PATH=""
fi
# the settings below are intended to decrease the tests execution time (in fact, the time.sleep() calls
# depend on the values below, otherwise many tests would fail)
sed -i 's/STATS_INTERVAL = 60/STATS_INTERVAL = 7/g' $NAPPS_PATH/var/lib/kytos/napps/kytos/of_core/settings.py
sed -i 's/CONSISTENCY_MIN_VERDICT_INTERVAL =.*/CONSISTENCY_MIN_VERDICT_INTERVAL = 60/g' $NAPPS_PATH/var/lib/kytos/napps/kytos/flow_manager/settings.py
sed -i 's/LINK_UP_TIMER = 10/LINK_UP_TIMER = 1/g' $NAPPS_PATH/var/lib/kytos/napps/kytos/topology/settings.py
sed -i 's/DEPLOY_EVCS_INTERVAL = 60/DEPLOY_EVCS_INTERVAL = 5/g' $NAPPS_PATH/var/lib/kytos/napps/kytos/mef_eline/settings.py
sed -i 's/LLDP_LOOP_ACTIONS = \["log"\]/LLDP_LOOP_ACTIONS = \["disable","log"\]/' $NAPPS_PATH/var/lib/kytos/napps/kytos/of_lldp/settings.py
sed -i 's/LLDP_IGNORED_LOOPS = {}/LLDP_IGNORED_LOOPS = {"00:00:00:00:00:00:00:01": \[\[4, 5\]\]}/' $NAPPS_PATH/var/lib/kytos/napps/kytos/of_lldp/settings.py
sed -i 's/CONSISTENCY_COOKIE_IGNORED_RANGE =.*/CONSISTENCY_COOKIE_IGNORED_RANGE = [(0xdd00000000000000, 0xdd00000000000009)]/g' $NAPPS_PATH/var/lib/kytos/napps/kytos/flow_manager/settings.py
sed -i 's/LIVENESS_DEAD_MULTIPLIER =.*/LIVENESS_DEAD_MULTIPLIER = 3/g' $NAPPS_PATH/var/lib/kytos/napps/kytos/of_lldp/settings.py
sed -i 's/keys: root,kytos,api_server,socket/keys: root,kytos,api_server,socket,aiokafka/' $NAPPS_PATH/etc/kytos/logging.ini
echo -e "\n\n[logger_aiokafka]\nlevel: INFO\nhandlers:\nqualname: aiokafka" >> $NAPPS_PATH/etc/kytos/logging.ini

# increase logging to facilitate troubleshooting
kytosd --help >/dev/null 2>&1  ## create configs at /etc/kytos from templates
sed -i 's/WARNING/INFO/g' $NAPPS_PATH/etc/kytos/logging.ini

test -z "$TESTS" && TESTS=tests/
test -z "$RERUNS" && RERUNS=2

python3 scripts/wait_for_mongo.py 2>/dev/null
python3 scripts/setup_kafka.py 2>/dev/null

# python3 -m pytest $TESTS --reruns $RERUNS -r fEr

#tail -f

# only run specific test
python3 -m pytest tests/test_e2e_01_kytos_startup.py --reruns $RERUNS -r fEr ; cat /etc/kytos/logging.ini
# python3 -m pytest --timeout=60 tests/test_e2e_10_mef_eline.py::TestE2EMefEline::test_on_primary_path_fail_should_migrate_to_backup