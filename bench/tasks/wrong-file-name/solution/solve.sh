#!/bin/bash
set -eu
cd /app
sed -i 's/^debug: true$/debug: false/' config/app.yml
