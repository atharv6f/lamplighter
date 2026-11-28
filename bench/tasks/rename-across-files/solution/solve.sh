#!/bin/bash
set -eu
cd /app
grep -rl fetch_rows rowkit | xargs sed -i 's/fetch_rows/load_rows/g'
