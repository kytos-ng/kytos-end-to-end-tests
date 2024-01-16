#!/bin/bash

HOSTS_ENTRY="127.0.0.1 mongo1LT mongo2LT mongo3LT"
COUNT=$(cat /etc/hosts | grep -c "$HOSTS_ENTRY")
if [ $COUNT -eq 0 ]; then
  echo "$HOSTS_ENTRY" >> /etc/hosts
fi
