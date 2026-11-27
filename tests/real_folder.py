"""A real work folder for the kill tests: the built-in tools on it, and what changed.

The world of these tests is a temp folder. The tools are the built-in ones,
with commands on the direct executor. A file change is seen by comparing
the folder before and after each `apply`. A command's change is that it
ran, so it is seen when the executor started it, whatever it printed.
"""

import os
import shutil
import stat
import tempfile
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from lamplighter.adapters import SystemClock
from lamplighter.adapters.builtin_tools import BuiltinTools
from lamplighter.ports import (
    BlobStore,
    Command,
    Effect,
    ExecResult,
    Executor,
    Failure,
    JsonObject,
    Plan,
    ToolEntry,
    ToolResult,
    canonical_json,
)
from tests.builtin_kit import served_page, settings_for
from tests.direct_executor import DirectExecutor

# What the work folder holds before the first process starts.
SEED = {"pot.txt": "hot\n"}
DECOY = "made-up decoy text"
NEW_SUFFIX = ".lamplighter-new"
FILE_TOOLS = ("write_file", "edit_file")
# The file that the scenarios' commands write, and no file tool does.
COMMAND_FILE = "launches.txt"

type Key = tuple[str, str]
type Snapshot = dict[str, bytes]


@dataclass
class RealFolder:
    """A temp folder: the work folder, its scratch folder, and a folder beside them.

    `started` notes each call and apply as it starts, and `changed` each
    change as it happens, both as the tool's name and canonical arguments.
    Both outlive every process, as the folder does.
    """

    root: Path
    started: list[Key] = field(default_factory=list[Key])
    changed: list[Key] = field(default_factory=list[Key])

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def outside(self) -> Path:
        return self.root / "outside"

    def tools(self, blobs: BlobStore) -> "FolderTools":
        """Give a new process's tools, which note what they start and change."""
        return FolderTools(self, blobs)

    def release(self) -> None:
        """Close the handles on the work folder that a gone process left open.

        A real chat opens one for its life. Here hundreds of processes run
        in one test process, and a Mac allows 256 open files by default.
        """
        folder = os.stat(self.work)
        for name in os.listdir("/dev/fd"):
            try:
                found = os.fstat(int(name))
            except OSError:
                continue
            if stat.S_ISDIR(found.st_mode) and (found.st_dev, found.st_ino) == (
                folder.st_dev,
                folder.st_ino,
            ):
                os.close(int(name))

    def wrong(self, rows: Mapping[int, Effect]) -> list[str]:
        """Give each rule about the folders that the ledger and the drive break."""
        wrong = contents_wrong(snapshot(self.work), rows, SEED)
        if snapshot(self.outside) != {"decoy.txt": DECOY.encode()}:
            wrong.append("a file outside the work folder changed")
        left = [name for name in snapshot(self.work) if name.endswith(NEW_SUFFIX)]
        if left:
            wrong.append(f"unfinished files are left: {left}")
        return wrong


def new_folder() -> RealFolder:
    """Make a temp folder with the seed in its work folder. It goes with its record."""
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="lamplighter-kills-")))
    made = RealFolder(root)
    for folder in (
        made.work,
        root / "work-scratch" / "home",
        root / "work-scratch" / "tmp",
        made.outside,
    ):
        folder.mkdir(parents=True)
    (made.outside / "decoy.txt").write_text(DECOY)
    for name, text in SEED.items():
        (made.work / name).write_text(text)
    weakref.finalize(made, shutil.rmtree, root, True)
    return made


def snapshot(folder: Path) -> Snapshot:
    """Give every file below `folder`, by its path from there, with its bytes."""
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def contents_wrong(
    found: Snapshot, rows: Mapping[int, Effect], seed: Mapping[str, str]
) -> list[str]:
    """Give what is wrong with the files, which no command writes, of a folder.

    Each must hold the seed with each file change applied that the ledger
    says happened, in the order the changes were begun, and no other.
    """
    expected = {name: text.encode() for name, text in seed.items()}
    for effect_id in sorted(rows):
        row = rows[effect_id]
        if row.action.tool not in FILE_TOOLS or not _happened(row):
            continue
        args = row.action.args
        path = str(args["path"])
        if row.action.tool == "write_file":
            expected[path] = str(args["content"]).encode()
        else:
            old = expected.get(path, b"").decode()
            new = old.replace(str(args["old_text"]), str(args["new_text"]), 1)
            expected[path] = new.encode()
    held = {
        name: data
        for name, data in found.items()
        if name != COMMAND_FILE and not name.endswith(NEW_SUFFIX)
    }
    if held == expected:
        return []
    return [f"the folder holds {held}, and the ledger says {expected}"]


def _happened(row: Effect) -> bool:
    if row.state == "resolved" and row.resolution is not None:
        return row.resolution.happened
    return row.state == "done"


class FolderTools:
    """The built-in tools on the folder, noting each start and each change."""

    def __init__(self, folder: RealFolder, blobs: BlobStore) -> None:
        self._folder = folder
        self._applying: Key | None = None
        executor = _StartsNoted(DirectExecutor(folder.work, SystemClock()), self)
        self._inner = BuiltinTools(
            settings_for(folder.work),
            blobs,
            executor,
            SystemClock(),
            served_page(),
        )

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self._inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        self._folder.started.append((name, canonical_json(arguments)))
        return await self._inner.call(name, arguments)

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        return await self._inner.plan(name, arguments)

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        key = (name, canonical_json(arguments))
        self._folder.started.append(key)
        before = snapshot(self._folder.work)
        self._applying = key
        try:
            return await self._inner.apply(name, arguments, plan)
        finally:
            self._applying = None
            if name in FILE_TOOLS and snapshot(self._folder.work) != before:
                self._folder.changed.append(key)

    async def check(self, probe: JsonObject) -> bool | None:
        return await self._inner.check(probe)

    def command_started(self) -> None:
        """A command of the apply now running was started."""
        if self._applying is not None and self._applying[0] == "shell":
            self._folder.changed.append(self._applying)


class _StartsNoted:
    """The direct executor, telling the tools each time a command was started."""

    def __init__(self, inner: Executor, tools: FolderTools) -> None:
        self._inner = inner
        self._tools = tools

    async def run(self, command: Command) -> ExecResult | Failure:
        ran = await self._inner.run(command)
        if isinstance(ran, ExecResult):
            self._tools.command_started()
        return ran

    def wall(self) -> JsonObject:
        return self._inner.wall()

    def kill_all(self) -> None:
        self._inner.kill_all()
