"""The bench's lock holds every package the harness runs on at the harness's version.

Without it, the bench could measure the harness on another HTTP library
than the service runs on.
"""

import tomllib
from pathlib import Path
from typing import cast

from lamplighter.app.code_revision import REPO_ROOT

HARNESS_LOCK = REPO_ROOT / "uv.lock"
BENCH_LOCK = REPO_ROOT / "bench" / "harbor" / "uv.lock"


def _packages(lock: Path) -> dict[str, dict[str, object]]:
    return {p["name"]: p for p in tomllib.loads(lock.read_text())["package"]}


def runs_on(packages: dict[str, dict[str, object]], name: str) -> set[str]:
    """Give the packages a package needs to run, itself left out, dev tools too."""
    found: set[str] = set()
    todo = [name]
    while todo:
        needs = cast(list[dict[str, str]], packages[todo.pop()].get("dependencies", []))
        for need in needs:
            needed = need["name"]
            if needed not in found:
                found.add(needed)
                todo.append(needed)
    return found


def test_every_package_the_harness_runs_on_has_one_version_in_both() -> None:
    harness = _packages(HARNESS_LOCK)
    bench = _packages(BENCH_LOCK)
    needed = runs_on(harness, "lamplighter")
    assert {"httpx", "httpcore"} <= needed
    for name in sorted(needed):
        assert name in bench, name
        assert bench[name]["version"] == harness[name]["version"], name


def test_the_bench_runs_the_harness_from_this_checkout() -> None:
    (lamplighter,) = (
        p
        for p in tomllib.loads(BENCH_LOCK.read_text())["package"]
        if p["name"] == "lamplighter"
    )
    assert lamplighter["source"] == {"editable": "../../"}
