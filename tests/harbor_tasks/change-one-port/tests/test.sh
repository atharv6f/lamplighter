#!/bin/bash
# The reward is 1 only when the one line changed and nothing else did.
mkdir -p /logs/verifier
cd /app
if grep -qx "port = 8080" settings.ini && [ "$(grep -c . settings.ini)" = 3 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
