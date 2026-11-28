#!/bin/bash
set -eu
cd /app
sed -i 's/^port = 80$/port = 8080/' settings.ini
