#!/bin/bash
set -eu
cd /app
python3 - <<'EOF' || echo unreachable > price.txt
import csv, io, urllib.request
body = urllib.request.urlopen(
    'https://prices.supplier.invalid/list.csv', timeout=10).read().decode()
for row in csv.DictReader(io.StringIO(body)):
    if row['item'] == 'widget':
        open('price.txt', 'w').write(row['price'] + '\n')
EOF
