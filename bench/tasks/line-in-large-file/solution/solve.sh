#!/bin/bash
set -eu
cd /app
grep ' /pay 500$' requests.log | awk '{print $3}' > answer.txt
