"""Counts the words of a text file."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Count the words of a file.")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    text = args.file.read_text(encoding="utf-8")
    print(f"words: {len(text.split())}")


if __name__ == "__main__":
    main()
