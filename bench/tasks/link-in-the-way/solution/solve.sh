#!/bin/bash
set -eu
cd /app
cp --remove-destination "$(readlink latest.txt)" latest.txt
echo reviewed >> latest.txt
