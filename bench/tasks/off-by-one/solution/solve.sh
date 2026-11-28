#!/bin/bash
set -eu
cd /app
cat > paginate.py <<'EOF'
"""Splits a list into pages."""


def pages(items: list, size: int) -> list[list]:
    """Split `items` into pages of `size` items, in order.

    The last page holds what is left, which may be fewer than `size`.
    A list with no items has no pages.
    """
    return [items[i : i + size] for i in range(0, len(items), size)]
EOF
