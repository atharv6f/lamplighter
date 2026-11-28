"""The bench's task set as the commands see it: the tasks, their split, their faults.

It is worked out from the files' plain values, and reads nothing itself.
"""

import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import cast

from lamplighter.app.task_files import SPLIT, TASKS
from lamplighter.bench import Split, TaskFile, split, task_faults, task_set_hash
from lamplighter.ports import Failure


@dataclass(frozen=True, slots=True)
class TaskSetView:
    """The tasks by name with their group, the split, the hash and every fault.

    A task whose group cannot be read has the group "". `split` is what
    `split.toml` holds, and is None when the file cannot be read. With no
    fault, it is also what the split function gives.
    """

    groups: dict[str, str]
    split: Split | None
    hash: str
    faults: tuple[str, ...]

    def side(self, task: str) -> str:
        """Give the side of the split a task is on, or "" when it is on none."""
        if self.split is None:
            return ""
        if task in self.split.dev:
            return "dev"
        return "held_out" if task in self.split.held_out else ""


def task_set_view(files: Iterable[TaskFile], ignored: Sequence[str]) -> TaskSetView:
    """Check the task set's files and give what they hold.

    `ignored` names the task files git leaves out, each of which is a
    fault: it would drop out of the hash without a word.
    """
    files = list(files)
    by_task: dict[str, list[TaskFile]] = {}
    faults = [
        f"{path} is left out by .gitignore, so it would not count in the hash."
        for path in ignored
    ]
    split_text: bytes | None = None
    for file in files:
        if file.path == SPLIT:
            split_text = file.data
            continue
        name, _, inner = file.path.removeprefix(f"{TASKS}/").partition("/")
        if not inner:
            faults.append(f"{file.path} is not inside a task's folder.")
            continue
        by_task.setdefault(name, []).append(
            TaskFile(inner, file.data, file.executable, file.link)
        )
    groups: dict[str, str] = {}
    for name, inner_files in sorted(by_task.items()):
        faults.extend(f"{name}: {fault}" for fault in task_faults(name, inner_files))
        groups[name] = _group(inner_files)
    found, split_faults = _split(split_text, groups)
    return TaskSetView(groups, found, task_set_hash(files), (*faults, *split_faults))


def _group(files: list[TaskFile]) -> str:
    toml = next((f for f in files if f.path == "task.toml"), None)
    try:
        parsed = tomllib.loads(toml.data.decode("utf-8")) if toml else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return ""
    group = parsed.get("metadata", {}).get("group")
    return group if isinstance(group, str) else ""


def _split(
    text: bytes | None, groups: dict[str, str]
) -> tuple[Split | None, list[str]]:
    read = _split_file(text)
    if isinstance(read, str):
        return None, [read]
    seed, found = read
    if "" in groups.values():
        return found, ["The split cannot be worked out: a task has no group."]
    if not groups:
        return found, ["The split cannot be worked out: there is no task."]
    by_group: dict[str, list[str]] = {}
    for name, group in groups.items():
        by_group.setdefault(group, []).append(name)
    worked_out = split(seed, by_group)
    if isinstance(worked_out, Failure):
        return found, [f"The split cannot be worked out: {worked_out.message}"]
    if (sorted(found.dev), sorted(found.held_out)) != (
        list(worked_out.dev),
        list(worked_out.held_out),
    ):
        return found, [
            f"{SPLIT} is not what the split function gives:"
            f" dev is {', '.join(worked_out.dev)}."
        ]
    return worked_out, []


def _split_file(text: bytes | None) -> tuple[str, Split] | str:
    if text is None:
        return f"There is no {SPLIT}."
    try:
        parsed = tomllib.loads(text.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return f"{SPLIT} is not TOML."
    seed = parsed.get("seed")
    dev, held_out = _names(parsed.get("dev")), _names(parsed.get("held_out"))
    if (
        set(parsed) != {"seed", "dev", "held_out"}
        or not isinstance(seed, str)
        or dev is None
        or held_out is None
    ):
        return f"{SPLIT} needs a seed, a dev list and a held_out list, only."
    return seed, Split(dev, held_out)


def _names(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    names = [v for v in cast(list[object], value) if isinstance(v, str)]
    return tuple(names) if len(names) == len(cast(list[object], value)) else None
