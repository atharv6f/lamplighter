#!/bin/bash
set -eu
cd /app
./dump.sh | grep '^serial number:' | sed 's/^serial number: //' > answer.txt
