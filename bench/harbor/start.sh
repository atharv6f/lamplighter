#!/bin/sh
# Starts the pinned Harbor with nothing of the caller's environment but what is named here.
set -eu
: "${HOME:?start.sh: HOME is not set.}"
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
cd "$here"
# Started through a link, the folder above would be the link's and not the project's.
if [ ! -f pyproject.toml ]; then
  echo "start.sh: start it by its own path, not through a link." >&2
  exit 1
fi
# Harbor loads a file of this name from the folder it starts in, without being asked.
if [ -e .env.local ]; then
  echo "start.sh: remove $here/.env.local first." >&2
  exit 1
fi
awake=""
if [ "$(uname -s)" = Darwin ]; then awake="caffeinate -i"; fi
key=""
if [ -n "${ANTHROPIC_API_KEY-}" ]; then key="ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"; fi
docker_host=""
if [ -n "${DOCKER_HOST-}" ]; then docker_host="DOCKER_HOST=$DOCKER_HOST"; fi
docker_context=""
if [ -n "${DOCKER_CONTEXT-}" ]; then docker_context="DOCKER_CONTEXT=$DOCKER_CONTEXT"; fi
python=""
if [ -n "${UV_PYTHON-}" ]; then python="UV_PYTHON=$UV_PYTHON"; fi
# Where uv keeps the Pythons it installed. Without it, uv may build Harbor's
# environment on another Python on PATH, whose SQLite the harness cannot use.
pythons=""
if [ -n "${UV_PYTHON_INSTALL_DIR-}" ]; then pythons="UV_PYTHON_INSTALL_DIR=$UV_PYTHON_INSTALL_DIR"; fi
exec $awake env -i \
  PATH="$PATH" HOME="$HOME" \
  ${key:+"$key"} ${docker_host:+"$docker_host"} ${docker_context:+"$docker_context"} ${python:+"$python"} ${pythons:+"$pythons"} \
  HARBOR_TELEMETRY=0 \
  LITELLM_LOCAL_MODEL_COST_MAP=True \
  PYTHONPATH="$here" \
  uv run --frozen harbor "$@"
