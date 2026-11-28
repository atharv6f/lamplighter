#!/bin/bash
set -eu
cd /app
awk '$2 == "ERROR" {print substr($1, 1, 13)}' app.log | sort | uniq -c | awk '{print $2, $1}' > errors.txt
