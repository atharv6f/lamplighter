#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
import csv
from pathlib import Path
for row in csv.DictReader(open('names.csv')):
    p = Path(f"cards/card{row['card']}.txt")
    p.write_text(p.read_text().replace('name: ?', f"name: {row['name']}"))
EOF
