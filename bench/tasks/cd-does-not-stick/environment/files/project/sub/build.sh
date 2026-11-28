#!/bin/bash
# Reads parts.txt and writes the sorted parts with their count.
set -eu
if [ ! -f parts.txt ]; then
  echo "build.sh: no parts.txt here. Run me from project/sub." >&2
  exit 1
fi
mkdir -p out
{ sort parts.txt; echo "parts: $(grep -c . parts.txt)"; } > out/result.txt
echo "build.sh: wrote out/result.txt"
