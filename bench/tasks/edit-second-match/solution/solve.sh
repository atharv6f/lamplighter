#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
from pathlib import Path
p = Path('notes.md')
head, _, tail = p.read_text().partition('## Release')
p.write_text(head + '## Release' + tail.replace('status: draft', 'status: final', 1))
EOF
