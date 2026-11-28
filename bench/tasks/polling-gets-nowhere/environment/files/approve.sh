#!/bin/bash
# Approves the job.
set -eu
here=$(dirname "$0")
mkdir -p "$here/.job"
echo 1838 > "$here/.job/approved"
echo "approved"
