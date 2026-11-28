#!/bin/bash
# Says whether the job is ready, and gives its code once it is.
set -eu
here=$(dirname "$0")
if [ ! -f "$here/.job/approved" ]; then
  echo "pending"
  exit 0
fi
echo "ready, code: $(( $(cat "$here/.job/approved") * 3 + 7 ))"
