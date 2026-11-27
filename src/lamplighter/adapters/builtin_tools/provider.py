"""The built-in tools as one provider: their entries, and a name handed to its tool."""

import asyncio
from collections.abc import Callable

from lamplighter.adapters.builtin_tools import (
    edit_file,
    read_file,
    read_output,
    search_files,
    shell,
    web_fetch,
    write_file,
)
from lamplighter.adapters.builtin_tools.file_change import file_check
from lamplighter.adapters.builtin_tools.settings import ToolSettings
from lamplighter.adapters.builtin_tools.work_paths import open_work_folder
from lamplighter.ports import (
    ActionClass,
    BlobStore,
    Clock,
    Executor,
    Failure,
    JsonObject,
    Plan,
    ToolEntry,
    ToolResult,
    ToolSpec,
    canonical_json,
)

_SOURCE = "ToolProvider/builtin"

type _Job = Callable[[], ToolResult | Failure]
type _JobMaker = Callable[..., _Job | ToolResult]


class BuiltinTools:
    """The tools that work in the work folder. Satisfies ToolProvider.

    Every file is reached from one handle on the work folder, opened when
    the provider is made, and its blocking work runs in a worker thread. A
    file change waits for its thread, even when it is cancelled, so it
    always answers with what the thread did. Searches and commands run
    through the Executor, behind its wall.
    """

    def __init__(
        self,
        settings: ToolSettings,
        blobs: BlobStore,
        executor: Executor,
        clock: Clock,
        web: web_fetch.WebAccess,
    ) -> None:
        """Work as the settings say. Keep old copies and read cut results in `blobs`."""
        self._settings = settings
        self._work = open_work_folder(settings.work_dir)
        self._blobs = blobs
        self._executor = executor
        self._clock = clock
        self._web = web
        self._entries = tuple(
            sorted(
                (
                    self._entry(read_file.SPEC, "read"),
                    self._entry(read_output.SPEC, "read", taints=True),
                    self._entry(search_files.SPEC, "read"),
                    self._entry(write_file.SPEC, "reversible_write"),
                    self._entry(edit_file.SPEC, "reversible_write"),
                    self._entry(shell.shell_spec(settings), "irreversible"),
                    self._entry(web_fetch.SPEC, "read", taints=True),
                ),
                key=lambda entry: entry.spec.name,
            )
        )
        # The probes of the changes whose thread has not ended.
        self._at_work: list[str] = []

    def list_tools(self) -> tuple[ToolEntry, ...]:
        """Give every entry, sorted by name."""
        return self._entries

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        """Run a read: a file in a worker thread, or a search behind the wall."""
        if name == "read_output":
            return await read_output.read_output(self._blobs, arguments)
        if name == "read_file":
            cap = self._settings.output_cap_chars
            return await asyncio.to_thread(
                read_file.read_file, self._work, arguments, cap
            )
        if name == "search_files":
            return await search_files.search_files(
                self._settings, self._executor, arguments
            )
        if name == "web_fetch":
            return await web_fetch.web_fetch(
                self._settings, self._web, self._clock, arguments
            )
        return _no_tool(name, "reads")

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        """Say what a change will touch, saving a file's old bytes first."""
        if name == "shell":
            return shell.SHELL_PLAN
        if name == "write_file":
            return await write_file.plan_write(self._work, self._blobs, arguments)
        if name == "edit_file":
            return await edit_file.plan_edit(self._work, self._blobs, arguments)
        return _no_tool(name, "changes something")

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        """Run a command, or carry out a file change in a worker thread."""
        if name == "shell":
            return await shell.run_shell(self._settings, self._executor, arguments)
        makers: dict[str, _JobMaker] = {
            "write_file": write_file.write_job,
            "edit_file": edit_file.edit_job,
        }
        if name not in makers or plan.probe is None:
            return _no_tool(name, "changes something")
        job = makers[name](self._work, arguments, plan.probe)
        if isinstance(job, ToolResult):
            return job
        return await self._past_any_cancel(job, canonical_json(plan.probe))

    async def check(self, probe: JsonObject) -> bool | None:
        """Say whether a file holds what the probe's change put there."""
        if canonical_json(probe) in self._at_work:
            return None
        return await asyncio.to_thread(file_check, self._work, probe)

    async def _past_any_cancel(self, job: _Job, key: str) -> ToolResult | Failure:
        self._at_work.append(key)
        thread = asyncio.get_running_loop().run_in_executor(None, job)
        thread.add_done_callback(lambda _: self._at_work.remove(key))
        while True:
            # Python cannot stop a thread. Letting the cancel through would
            # let a check run while the thread may still rename the file.
            try:
                return await asyncio.shield(thread)
            except asyncio.CancelledError:
                continue

    def _entry(
        self, spec: ToolSpec, action_class: ActionClass, taints: bool = False
    ) -> ToolEntry:
        cap = self._settings.output_cap_chars
        return ToolEntry(
            spec=spec, action_class=action_class, output_cap=cap, taints=taints
        )


def _no_tool(name: str, what: str) -> Failure:
    return Failure(
        kind="invalid_input",
        effect="none",
        message=f"There is no built-in tool named {name} that {what}.",
        source=_SOURCE,
    )
