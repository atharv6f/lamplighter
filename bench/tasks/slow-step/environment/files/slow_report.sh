#!/bin/bash
# Works out a made-up report, slowly, then writes it at the end.
set -eu
for step in 1 2 3 4 5; do
  sleep 30
  echo "slow_report.sh: step $step of 5 done"
done
rows=$(( 6 * 7 ))
echo "rows counted: $rows" > report.txt
echo "slow_report.sh: wrote report.txt"
