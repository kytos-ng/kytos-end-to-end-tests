#!/bin/bash

env_variables=("export MONGO_HOST_SEEDS='mongo1LT:27037,mongo2LT:27038,mongo3LT:27039'"
"export MONGO_INITDB_ROOT_USERNAME=root_user" 
"export MONGO_INITDB_ROOT_PASSWORD=root_pw" 
"export MONGO_USERNAME=napp_user" 
"export MONGO_PASSWORD=napp_pw" 
"export MONGO_DBNAME=napps")

for variable in "${env_variables[@]}"; do
  COUNT=$(cat /etc/environment | grep -c "$variable")
  if [ $COUNT -eq 0 ]; then
    echo $variable >> /etc/environment
  fi

done
# Remember to do "" 
# if you just added the env variables
echo "exported env varibles"

