#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
import hashlib
from collections import defaultdict
from pathlib import Path
groups = defaultdict(list)
for p in Path('photos').rglob('*'):
    if p.is_file():
        groups[hashlib.sha256(p.read_bytes()).hexdigest()].append(str(p))
lines = sorted(' '.join(sorted(g)) for g in groups.values() if len(g) > 1)
Path('duplicates.txt').write_text(''.join(l + '\n' for l in lines))
EOF
