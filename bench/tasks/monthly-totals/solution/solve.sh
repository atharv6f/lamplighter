#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
import csv
from collections import defaultdict
from decimal import Decimal
totals = defaultdict(Decimal)
for row in csv.DictReader(open('sales.csv')):
    totals[row['date'][:7]] += Decimal(row['amount'])
with open('totals.csv', 'w', newline='') as f:
    w = csv.writer(f, lineterminator='\n')
    w.writerow(['month', 'total'])
    for m in sorted(totals):
        w.writerow([m, f'{totals[m]:.2f}'])
EOF
