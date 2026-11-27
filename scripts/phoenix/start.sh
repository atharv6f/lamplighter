#!/bin/sh
# Starts the pinned trace viewer on this machine only. Stop it with Ctrl-C.
# With no arguments it uses ~/.lamplighter-phoenix and the usual two ports.
# The tests pass a folder and two ports of their own.
set -eu
# A CDPATH from the caller would let `cd` below go to another folder.
unset CDPATH
folder="${1:-${HOME}/.lamplighter-phoenix}"
mkdir -p "$folder"
chmod 700 "$folder"
folder="$(cd "$folder" && pwd)"
cd "$(dirname "$0")"
# The viewer and the tool that starts it read many settings from the
# environment: where the database is, where to send data of their own, a file
# of more settings to load, and the keys of model providers. So nothing of the
# caller's environment is passed on but the two entries needed to find
# programs and the home folder. Every other setting is one of the lines below.
exec env -i \
  PATH="$PATH" \
  HOME="$HOME" \
  PHOENIX_WORKING_DIR="$folder" \
  PHOENIX_PORT="${2:-6006}" \
  PHOENIX_GRPC_PORT="${3:-4317}" \
  PHOENIX_HOST=127.0.0.1 \
  PHOENIX_TELEMETRY_ENABLED=false \
  PHOENIX_ALLOW_EXTERNAL_RESOURCES=false \
  PHOENIX_DISABLE_AGENT_ASSISTANT=true \
  PHOENIX_ENABLE_MCP_SERVER=false \
  uv run --frozen phoenix serve
