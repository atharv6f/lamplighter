#!/bin/bash
set -eu
cd /app
sed -i 's/^timeout = 30$/timeout = 60/' config/service.conf
