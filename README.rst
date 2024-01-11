*****
Kytos End-2-End-Tests
*****

Overview
########

The purpose of this repository is to eventually house all the End-to-End code necessary to test the entirety of the Kytos SDN Controller.
As of today, the E2E code available analyzes the mef_eline, topology, and maintenance Napps, as well as it ensures the proper start of Kytos without errors.
All tests are based on simple Mininet topologies (which are provided in the helpers.py file), and they are executed within a docker container that holds the 
code for installing all the basic requirements needed to set up an environment capable of executing the tests.

Getting Started
###############

Once you have cloned this project, you need to go into the project repository and run the following command::

  $ docker-compose up

This will create and start services as outlined in your docker-compose.yml file, which in this case are to kickstart the installation of the docker images 
for Kytos and Mininet.

After all installations finish, the docker-compose file will call the kytos-init.sh script which takes care of finishing installing Kytos and all of the required 
network applications in a quick and efficient way. This script is also responsible for executing all the tests within the projects repository via the commands::

  $ python3 -m pytest --timeout=60 tests/

Which runs all available tests, or run only a specific test::

  $ python3 -m pytest --timeout=60 \
        tests/test_e2e_10_mef_eline.py::TestE2EMefEline::test_on_primary_path_fail_should_migrate_to_backup

The above lines are entirely up to the user to modify, and will allow them to choose in which way they want to use the tests.

Running Tests Locally
#####################

You can start running tests locally by adding the mongoLT (Local Test) hosts with the add-etc-local-hosts.sh bash script as follows::

  $ ./local_setup/add-etc-local-hosts
  
Then you can add the required environment variables with the following add-persistent-env-variables.sh bash script::

  $ sudo ./local_setup/add-env-variables

Subsequently, the docker-compose.local.yml file can be used with the following command to run all of the required docker containers::

  $ docker-compose -f docker-compose.local.yml up -d

To make sure that the DB connectivity is functional run::

  $ python scripts/wait_for_mongo.py

Finally, switch to root user as mininet will only run as root and when sudo is used it doesn't have the required dependencies to run kytos and the tests::

  $ sudo su -

Make sure to utilize::

  $ ./kytos-init.sh

To have the correct settings in order for the tests to run properly!

Mininet Topologies
##################

.. image:: images/ Mininet-Topologies.png

you can run any of those topologies with the following command::

  # mn --custom tests/helpers.py --topo ring --controller=remote,ip=127.0.0.1

In the command above _ring_ is the name of the topology. To see all available topologies::

  $ grep "lambda.*Topo" tests/helpers.py

Requirements
############
* Python
* Mininet
* Docker
* docker-compose
* MongoDB (run via docker-compose)
* Kytos SDN Controller
* kytos/of_core 
* kytos/flow_manager 
* kytos/topology 
* kytos/of_lldp pathfinder 
* kytos/mef_eline 
* kytos/maintenance

