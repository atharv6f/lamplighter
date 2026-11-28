#!/bin/bash
set -eu
cd /app
./approve.sh
./status.sh | sed 's/^ready, code: //' > code.txt
