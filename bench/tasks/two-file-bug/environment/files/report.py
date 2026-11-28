"""Prints a settings file as a table."""

import sys
from pathlib import Path

from formatter import table
from parser import parse

if __name__ == "__main__":
    print(table(parse(Path(sys.argv[1]).read_text(encoding="utf-8"))), end="")
