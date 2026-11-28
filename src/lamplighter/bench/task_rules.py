"""The rules a task keeps that its files can show, as a check on plain values.

Some rules cannot be seen in the files: that the check reads only the end
state under `/app`, never which tools were called, and that the end state
holds no link. Those are for the task's author and for the run of the
reference solution and of doing nothing.
"""

import re
import tomllib
from collections.abc import Iterable, Mapping

from lamplighter.bench.task_set import TaskFile

PINNED_IMAGE = (
    "python:3.13-slim-bookworm"
    "@sha256:f0fa218d5b06f9b9235024fa061cde2cb141f726de52134471053d2d9b5324a3"
)
GROUPS = frozenset({"files", "shell", "harness", "data", "code", "honesty"})
REQUIRED = (
    "instruction.md",
    "task.toml",
    "environment/Dockerfile",
    "environment/.dockerignore",
    "solution/solve.sh",
    "tests/test.sh",
)
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_INSTALLER = re.compile(r"\b(?:pip|apt-get|apt|curl|wget|uv)\b")
_HOST = re.compile(rb"https?://([^/\s:'\"<>()\[\]]+)")
# A path under the check's own folder, but not one under /app/tests.
_CHECK_COPY = re.compile(r"(?<![\w.-])/tests/([\w./-]+)")


def task_faults(name: str, files: Iterable[TaskFile]) -> tuple[str, ...]:
    """Give each rule the task's files break, as a line a person can read.

    `name` is the task's folder, and each path is from inside it.
    """
    by_path = {f.path: f for f in files}
    faults: list[str] = []
    if not _NAME.fullmatch(name):
        faults.append("The folder's name is not lowercase letters, digits and hyphens.")
    faults.extend(f"There is no {path}." for path in REQUIRED if path not in by_path)
    if faults:
        return tuple(faults)
    text = {
        path: f.data.decode("utf-8", errors="replace") for path, f in by_path.items()
    }
    faults.extend(_task_toml_faults(name, text["task.toml"]))
    faults.extend(_dockerfile_faults(text["environment/Dockerfile"], by_path))
    faults.extend(_check_faults(text["tests/test.sh"], by_path))
    faults.extend(_installer_faults(text))
    faults.extend(_host_faults(by_path.values()))
    if "/app" in text["instruction.md"]:
        faults.append(
            "instruction.md names /app: it names paths inside the work folder."
        )
    if text["environment/.dockerignore"].strip() != ".DS_Store":
        faults.append("environment/.dockerignore is not the one line .DS_Store.")
    return tuple(faults)


def _task_toml_faults(name: str, text: str) -> list[str]:
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ["task.toml is not TOML."]
    task = parsed.get("task", {})
    metadata = parsed.get("metadata", {})
    free = (task.get("description"), metadata.get("drawn_from"))
    if not all(isinstance(value, str) and value for value in free):
        return ["task.toml needs a description and a drawn_from line."]
    group = metadata.get("group")
    if group not in GROUPS:
        return [f"task.toml's group {group!r} is not one of the six."]
    expected = {
        "schema_version": "1.4",
        "artifacts": ["/app"],
        "task": {"name": f"lamplighter-bench/{name}", "description": free[0]},
        "metadata": {"group": group, "drawn_from": free[1]},
        "agent": {"timeout_sec": 1500.0},
        "verifier": {"timeout_sec": 300.0, "environment_mode": "separate"},
        "environment": {"build_timeout_sec": 600.0, "cpus": 1, "memory_mb": 1024},
    }
    differ = sorted(
        key
        for key in expected.keys() | parsed.keys()
        if parsed.get(key) != expected.get(key)
    )
    if differ:
        return [f"task.toml differs from the fixed form in {', '.join(differ)}."]
    return []


def _dockerfile_faults(text: str, by_path: Mapping[str, TaskFile]) -> list[str]:
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines or lines[0] != f"FROM {PINNED_IMAGE}":
        return ["The Dockerfile does not start FROM the pinned image, by digest."]
    faults: list[str] = []
    installed: set[str] = set()
    for line in lines[1:]:
        word, _, rest = line.partition(" ")
        if line == "WORKDIR /app":
            continue
        if word == "COPY":
            wrong = _copy_faults(rest.split())
            faults.extend(wrong)
            if not wrong:
                installed |= _installed(rest.split(), by_path)
        elif word != "RUN" or rest not in installed:
            # A RUN may only run, by its path, one file a COPY put there.
            faults.append(f"The Dockerfile has a line it may not have: {line}")
    return faults


def _copy_faults(words: list[str]) -> list[str]:
    flags = [w for w in words if w.startswith("--")]
    places = [w for w in words if not w.startswith("--")]
    if len(places) < 2 or any(flag != "--chmod=644" for flag in flags):
        return [f"The Dockerfile has a COPY it may not have: {' '.join(words)}"]
    faults: list[str] = []
    for source in places[:-1]:
        if not source.startswith(("files/", "outside/")):
            faults.append(
                f"A COPY takes {source}, which is not under files/ or outside/."
            )
        if source.startswith("outside/") and "--chmod=644" not in flags:
            faults.append(f"A COPY of {source} needs --chmod=644.")
    return faults


def _installed(words: list[str], by_path: Mapping[str, TaskFile]) -> set[str]:
    # The paths in the image of the task's files that one COPY puts there.
    places = [w for w in words if not w.startswith("--")]
    target = places[-1]
    paths: set[str] = set()
    for source in places[:-1]:
        for path in by_path:
            inside = path.removeprefix(f"environment/{source}")
            if source.endswith("/") and path.startswith(f"environment/{source}"):
                paths.add(target + inside)
            elif path == f"environment/{source}":
                name = source.rsplit("/", 1)[-1]
                paths.add(target + name if target.endswith("/") else target)
    return paths


def _check_faults(test_sh: str, by_path: Mapping[str, TaskFile]) -> list[str]:
    faults: list[str] = []
    for refused in ("/app/tests", "/solution"):
        if refused in test_sh:
            faults.append(f"tests/test.sh names {refused}, which the agent can reach.")
    for named in _CHECK_COPY.findall(test_sh):
        place = f"tests/{named}".rstrip("/")
        if place not in by_path and not any(p.startswith(place + "/") for p in by_path):
            faults.append(
                f"tests/test.sh reads /tests/{named}, which is not in tests/."
            )
    return faults


def _installer_faults(text: Mapping[str, str]) -> list[str]:
    checked = [
        path
        for path in text
        if path == "environment/Dockerfile"
        or (path.startswith("tests/") and path.endswith(".sh"))
    ]
    return [
        f"{path} installs something: it names {found.group(0)}."
        for path in sorted(checked)
        if (found := _INSTALLER.search(text[path])) is not None
    ]


def _host_faults(files: Iterable[TaskFile]) -> list[str]:
    return [
        f"{f.path} names the host {host.decode(errors='replace')}, not under .invalid."
        for f in sorted(files, key=lambda f: f.path)
        for host in _HOST.findall(f.data)
        if not host.lower().rstrip(b".").endswith(b".invalid")
    ]
