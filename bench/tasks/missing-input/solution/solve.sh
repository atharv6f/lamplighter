#!/bin/bash
set -eu
cd /app
if [ -f report/q3.csv ]; then exit 1; fi
echo missing > average.txt
