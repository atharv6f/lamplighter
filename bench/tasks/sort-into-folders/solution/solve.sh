#!/bin/bash
set -eu
cd /app
for f in inbox/*; do
  n=$(basename "$f")
  mkdir -p "archive/${n:0:4}/${n:5:2}"
  mv "$f" "archive/${n:0:4}/${n:5:2}/$n"
done
