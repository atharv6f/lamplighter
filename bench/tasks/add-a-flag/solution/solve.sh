#!/bin/bash
set -eu
cd /app
python3 - <<'EOF'
from pathlib import Path
p = Path('wordcount.py')
s = p.read_text()
s = s.replace('    parser.add_argument("file", type=Path)\n',
    '    parser.add_argument("file", type=Path)\n'
    '    parser.add_argument("--lines", action="store_true")\n')
s = s.replace('    print(f"words: {len(text.split())}")\n',
    '    print(f"words: {len(text.split())}")\n'
    '    if args.lines:\n'
    '        print(f"lines: {len(text.splitlines())}")\n')
p.write_text(s)
EOF
