#!/bin/bash
# The reward is 1 only when the check of what the agent left in /app passes.
mkdir -p /logs/verifier
if python3 /tests/check.py; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
