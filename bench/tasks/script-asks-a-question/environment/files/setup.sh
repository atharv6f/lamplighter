#!/bin/bash
# Sets up the made-up tool. Asks first, unless told --yes.
set -u
if [ "${1-}" = "--help" ]; then
  echo "usage: setup.sh [--yes]"
  echo "  --yes   answer yes to the question, for a run with no one to ask"
  exit 0
fi
if [ "${1-}" != "--yes" ]; then
  printf "Set up the made-up tool here? [y/N] "
  if ! read -r answer || [ "$answer" != "y" ]; then
    echo
    echo "setup.sh: no answer, so nothing was set up. See --help." >&2
    exit 1
  fi
fi
echo "made-up tool 1.0, set up" > installed.flag
echo "setup.sh: done"
