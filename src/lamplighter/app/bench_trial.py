"""One trial of the bench: one run of the agent on a task, its commands in a container.

It knows nothing of Harbor. It is given the instruction, the way into the
task's container, and folders, and it leaves a trial record. Each part that
goes wrong gives its failure as a value, and the trial goes straight to its
record, which then says the trial did not test the agent. Nothing is
caught: an exception is a bug, and leaves no record.
"""

import dataclasses
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters.builtin_tools import ToolSettings
from lamplighter.adapters.container import AsRoot, ContainerExecutor
from lamplighter.app.chat import Stores, Tooling, check_schemas, run_spec
from lamplighter.app.config import Config, load_config
from lamplighter.app.model_set import ModelSet
from lamplighter.app.work_bounds import out_of_bounds
from lamplighter.bench import (
    BoundWord,
    InfraReason,
    Tokens,
    TrialEnds,
    TrialRecord,
    infra_reason,
    read_record,
    record_text,
)
from lamplighter.core import (
    Deps,
    Observers,
    RunningSessions,
    RunSpec,
    RunSummary,
    TurnEnd,
    run_summary,
    run_turn,
)
from lamplighter.ports import (
    BlobStore,
    Clock,
    Executor,
    Failure,
    IdSource,
    Inbound,
    Sender,
    ToolProvider,
    utc_text,
)

SESSION = "bench-trial"
CHANNEL = "bench"
RECORD = "trial_record.json"


@dataclass(frozen=True)
class TrialRequest:
    """What one trial is given.

    `trial_folder` holds the box, which the container sees at the same path,
    and `harness/`, which no container sees. The job folder is the folder
    above it. The run set stops starting trials once the records in its job
    folder add up to `budget_micro_dollars`. The two planned values are
    what the run set was planned with, and a trial on anything else does
    not count.
    """

    instruction: str
    as_root: AsRoot
    user: tuple[int, int]
    trial_folder: Path
    settings: Path
    shell_path: str
    budget_micro_dollars: int
    planned_portable_id: str
    planned_revision: str


@dataclass(frozen=True)
class TrialStore:
    """The stores of one trial, how to export its session, and how to close them.

    `export` writes the session into a new folder and gives the number of
    events it wrote.
    """

    stores: Stores
    export: Callable[[str, Path], Awaitable[int | Failure]]


@dataclass(frozen=True)
class TrialParts:
    """What a trial is built from beside its request. A test gives its own.

    `code_revision` is the code this process loaded, worked out once.
    """

    clock: Clock
    ids: IdSource
    code_revision: str
    open_store: Callable[[Path, Config], TrialStore | Failure]
    executor: Callable[[AsRoot, tuple[int, int], Path], ContainerExecutor]
    tools: Callable[[ToolSettings, BlobStore, Executor], ToolProvider]
    models: Callable[[Config], ModelSet]


@dataclass(frozen=True)
class _Built:
    store: TrialStore
    executor: ContainerExecutor
    tooling: Tooling
    models: ModelSet


async def run_trial(parts: TrialParts, request: TrialRequest) -> TrialRecord:
    """Run one trial, write its record last, and give the record.

    The settings copy is read with its own checks, and then its work
    folder, scratch folder and command path are those of the trial.
    """
    began = parts.clock.monotonic()
    config = trial_config(request)
    harness = request.trial_folder / "harness"
    harness.mkdir()
    if spent(request.trial_folder.parent) >= request.budget_micro_dollars:
        return _written(harness, _stopped(parts, config, None, "budget_reached", began))
    store = parts.open_store(harness / "store", config)
    if isinstance(store, Failure):
        # The store's own words, so that a person can see why no run began.
        stopped = _stopped(parts, config, None, "harness_failure", began)
        return _written(harness, dataclasses.replace(stopped, run_detail=store.message))
    try:
        built = _build(parts, request, config, store)
        try:
            record = await _with_built(parts, request, config, built, began)
        finally:
            await built.models.close()
    finally:
        store.stores.close()
    return _written(harness, record)


def trial_config(request: TrialRequest) -> Config:
    """Read the settings copy, with the trial's folders and command path in it.

    Raises ValueError as `load_config` does.
    """
    config = load_config(request.settings)
    box = request.trial_folder / "box"
    tools = dataclasses.replace(
        config.tools,
        work_dir=os.path.realpath(box / "work"),
        scratch_dir=os.path.realpath(box / "scratch"),
        shell_path=request.shell_path,
    )
    return dataclasses.replace(config, tools=tools)


def spent(job_folder: Path) -> int:
    """Add up the cost of the trial records already in a job folder.

    A record that cannot be read, or whose cost is not known, adds nothing.
    """
    total = 0
    for found in sorted(job_folder.glob(f"*/harness/{RECORD}")):
        record = read_record(found.read_text(encoding="utf-8", errors="replace"))
        if record is not None and record.micro_dollars is not None:
            total += record.micro_dollars
    return total


def _build(
    parts: TrialParts, request: TrialRequest, config: Config, store: TrialStore
) -> _Built:
    work = Path(config.tools.work_dir)
    executor = parts.executor(request.as_root, request.user, work)
    tools = parts.tools(config.tools, store.stores.blobs, executor)
    check_schemas(tools)
    return _Built(store, executor, Tooling(tools, executor), parts.models(config))


async def _with_built(
    parts: TrialParts,
    request: TrialRequest,
    config: Config,
    built: _Built,
    began: float,
) -> TrialRecord:
    spec = run_spec(config, built.tooling, parts.code_revision, private=False)
    if not await built.executor.mark():
        return _stopped(parts, config, spec, "executor_unavailable", began)
    planned = (request.planned_portable_id, request.planned_revision)
    if (spec.portable_config_id, parts.code_revision) != planned:
        return _stopped(parts, config, spec, "config_mismatch", began)
    ended = await _one_run(parts, request, spec, built)
    swept = await built.executor.sweep()
    events = await built.store.stores.log.read(SESSION, 0)
    summary = None if isinstance(events, Failure) else run_summary(events)
    ends = TrialEnds(
        run_reason=None if ended is None or summary is None else summary.reason,
        run_detail=None if summary is None else summary.detail,
        command_unavailable=built.executor.broken is not None,
        swept=swept,
        exported=True,
    )
    # Measured after the sweep, so that nothing is still writing, and only
    # while the trial still counts.
    left = (
        out_of_bounds(Path(config.tools.work_dir))
        if infra_reason(ends) is None
        else None
    )
    exported = await built.store.export(
        SESSION, request.trial_folder / "harness" / "export"
    )
    reason = infra_reason(
        dataclasses.replace(ends, exported=not isinstance(exported, Failure))
    )
    return _record(
        parts,
        config,
        spec,
        summary,
        reason,
        left if reason is None else None,
        None if isinstance(exported, Failure) else exported,
        began,
    )


async def _one_run(
    parts: TrialParts, request: TrialRequest, spec: RunSpec, built: _Built
) -> TurnEnd | None:
    stores = built.store.stores
    at = utc_text(parts.clock.now())
    accepted = await stores.inbox.accept(
        Inbound(
            channel=CHANNEL,
            native_id="instruction",
            conversation=SESSION,
            sender=Sender(id=CHANNEL, verified=True),
            kind="message",
            text=request.instruction,
            sent_at=at,
            received_at=at,
        )
    )
    if isinstance(accepted, Failure):
        return None
    deps = Deps(
        log=stores.log,
        inbox=stores.inbox,
        outbox=stores.outbox,
        models=built.models.clients,
        health=built.models.health,
        tools=built.tooling.tools,
        ledger=stores.ledger,
        blobs=stores.blobs,
        clock=parts.clock,
        ids=parts.ids,
        observers=Observers(),
        running=RunningSessions(),
    )
    ended = await run_turn(SESSION, spec, deps)
    return None if ended is None or isinstance(ended, Failure) else ended


def _stopped(
    parts: TrialParts,
    config: Config,
    spec: RunSpec | None,
    reason: InfraReason,
    began: float,
) -> TrialRecord:
    return _record(parts, config, spec, None, reason, None, None, began)


def _record(
    parts: TrialParts,
    config: Config,
    spec: RunSpec | None,
    summary: RunSummary | None,
    reason: InfraReason | None,
    left: BoundWord | None,
    exported_events: int | None,
    began: float,
) -> TrialRecord:
    tokens = Tokens(0, 0, 0, 0)
    if summary is not None:
        counts = summary.tokens
        tokens = Tokens(counts.uncached, counts.read, counts.written, counts.output)
    return TrialRecord(
        portable_config_id=None if spec is None else spec.portable_config_id,
        config_id=None if spec is None else spec.config_id,
        code_revision=parts.code_revision,
        model=config.default_model().profile.model,
        provider=config.default_model().profile.adapter,
        session_id=SESSION,
        run_id=None if summary is None else summary.run_id,
        run_reason=None if summary is None else summary.reason,
        run_detail=None if summary is None else summary.detail,
        gradable=reason is None,
        infra_reason=reason,
        left_out_of_bounds=left,
        steps=0 if summary is None else summary.steps,
        tool_calls=0 if summary is None else summary.tool_calls,
        tokens=tokens,
        micro_dollars=0 if summary is None else summary.micro_dollars,
        ms=int((parts.clock.monotonic() - began) * 1000),
        exported_events=exported_events,
    )


def _written(harness: Path, record: TrialRecord) -> TrialRecord:
    # Through a temporary file that is flushed and renamed, so that a reader
    # finds a whole record or none.
    temporary = harness / f"{RECORD}.new"
    with temporary.open("w", encoding="utf-8") as out:
        out.write(record_text(record))
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(harness / RECORD)
    folder = os.open(harness, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)
    return record
