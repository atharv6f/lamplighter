#!/bin/bash
# Installs the made-up tool under PREFIX, which is ./prefix unless it is set.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
PREFIX="${PREFIX:-$here/prefix}"
mkdir -p "$PREFIX"
mkdir "$PREFIX/bin" "$PREFIX/etc"
cp "$here/tool.sh" "$PREFIX/bin/tool"
ln -s tool "$PREFIX/bin/mt"
echo "export PATH=\"$PREFIX/bin:\$PATH\"" >> "$PREFIX/etc/profile"
echo "installed under $PREFIX"
