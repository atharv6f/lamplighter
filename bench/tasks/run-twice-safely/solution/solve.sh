#!/bin/bash
set -eu
cd /app
cat > install.sh <<'EOF'
#!/bin/bash
# Installs the made-up tool under PREFIX, which is ./prefix unless it is set.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
PREFIX="${PREFIX:-$here/prefix}"
mkdir -p "$PREFIX/bin" "$PREFIX/etc"
cp "$here/tool.sh" "$PREFIX/bin/tool"
ln -sfn tool "$PREFIX/bin/mt"
line="export PATH=\"$PREFIX/bin:\$PATH\""
touch "$PREFIX/etc/profile"
grep -qxF "$line" "$PREFIX/etc/profile" || echo "$line" >> "$PREFIX/etc/profile"
echo "installed under $PREFIX"
EOF
