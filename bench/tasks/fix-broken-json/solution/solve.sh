#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
from pathlib import Path
p = Path('data/config.json')
t = p.read_text().replace('Okafor,', 'Okafor",').replace('50,}', '50}')
p.write_text(t)
EOF
