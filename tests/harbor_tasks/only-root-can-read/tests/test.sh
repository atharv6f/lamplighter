#!/bin/bash
# The reward is 1 only when the report was copied.
mkdir -p /logs/verifier
if [ "$(cat /app/report.txt 2>/dev/null)" = "A made-up report." ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
