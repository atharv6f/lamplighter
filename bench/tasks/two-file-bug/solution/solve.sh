#!/bin/bash
set -eu
cd /app
sed -i 's/key, value = line.split("=")/key, value = line.split("=", 1)/' parser.py
python3 - <<'EOF'
from pathlib import Path
p = Path('formatter.py')
s = p.read_text().replace(
    '    return "".join(f"{key[:8]:<8} : {value}\\n" for key, value in settings)\n',
    '    width = max((len(key) for key, _ in settings), default=0)\n'
    '    return "".join(f"{key:<{width}} : {value}\\n" for key, value in settings)\n')
p.write_text(s)
EOF
