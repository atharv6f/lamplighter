#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
import csv
merged = {}
for row in csv.DictReader(open('b.csv')):
    e = row['email'].lower()
    merged[e] = [row['name'], e, row['phone']]
for row in csv.DictReader(open('a.csv')):
    e = row['email'].lower()
    old = merged.get(e, ['', e, ''])
    merged[e] = [row['name'], e, row['phone'] or old[2]]
with open('merged.csv', 'w', newline='') as f:
    w = csv.writer(f, lineterminator='\n')
    w.writerow(['name', 'email', 'phone'])
    for e in sorted(merged):
        w.writerow(merged[e])
EOF
