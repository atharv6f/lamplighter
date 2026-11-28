#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
import csv
from decimal import Decimal
prices = {r['item']: Decimal(r['price']) for r in csv.DictReader(open('cache/prices.csv'))}
total = sum(prices[r['item']] * int(r['quantity']) for r in csv.DictReader(open('order.csv')))
open('total.txt', 'w').write(f'{total:.2f}\n')
EOF
