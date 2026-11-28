#!/bin/bash
set -eu
cd /app
set +e
./deploy.sh
code=$?
set -e
if [ "$code" = 0 ]; then echo ok > status.txt; else echo "failed $code" > status.txt; fi
