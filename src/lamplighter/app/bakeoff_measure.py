"""The bake-off's `run`: measure one candidate against its running server.

Every request goes only to the server on this Mac that the entry names.
A run killed part way writes nothing, so a candidate's row is always
whole: the requests are free, and the run is made again from the start.
"""

import asyncio
import dataclasses
import tomllib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from lamplighter.app.bakeoff import BakeoffParts, append_step, capture_path, empty_body
from lamplighter.app.bakeoff_wire import Wire
from lamplighter.app.bench_parts import ledger_rows, loaded
from lamplighter.app.capture_set import Captured, read_capture, set_hash
from lamplighter.app.config import Config
from lamplighter.app.memory_watch import MemoryWatch
from lamplighter.app.model_set import ModelSet
from lamplighter.app.model_settings import LocalServer, ModelEntry
from lamplighter.bench import (
    HARD,
    AdapterPerRequest,
    BakeoffRule,
    CandidateMeasures,
    FirstToken,
    PeakMemory,
    ReplyReason,
    Speeds,
    UsableReplies,
    read_bakeoff_rule,
)
from lamplighter.core import fold_reply, judge_reply
from lamplighter.ports import (
    BakeoffBody,
    BakeoffRow,
    BakeoffStage,
    Failure,
    LedgerRow,
    ModelClient,
    ModelEvent,
    ModelRequest,
    Verdict,
    from_plain,
    to_plain,
)

type Reader = Callable[[], int | None]


@dataclass(frozen=True)
class MeasureParts:
    """What `run` and `soak` are built from, beside the bake-off's parts.

    `http` gives a new client for the bake-off's own requests. `footprint`
    gives a reader of the footprint of the process that listens on
    127.0.0.1 at a port, or the Failure of finding none. `pressure` reads
    the Mac's memory pressure level. `keep_awake` keeps the Mac awake until
    the function it gives is called.
    """

    bakeoff: BakeoffParts
    http: Callable[[], httpx.AsyncClient]
    footprint: Callable[[int], Reader | Failure]
    pressure: Reader
    keep_awake: Callable[[], Callable[[], None]]


@dataclass(frozen=True, slots=True)
class Ready:
    """What a measure of one candidate starts from, once every check passed.

    `rule_row` is the latest rule row, and `captured` the capture set it
    names, read from its file.
    """

    rows: list[LedgerRow]
    rule_row: BakeoffBody
    config: Config
    entry: ModelEntry
    local: LocalServer
    captured: tuple[Captured, ...]


@dataclass(frozen=True, slots=True)
class CandidateNotes:
    """What you write down for a candidate that the bake-off cannot measure.

    The four hard requirements, each with its evidence, where the weights
    came from and their quant, the memory the footprint does not show,
    and whether the server takes an adapter per request, with how you know.
    """

    weights_source: str
    quant: str
    added_bytes: int
    adapter_per_request: AdapterPerRequest
    adapter_evidence: str
    hard: tuple[Verdict, ...]


async def run(parts: MeasureParts, settings: Path, model: str, notes: Path) -> int:
    """Measure the candidate the entry names, and write its row, or nothing."""
    bakeoff = parts.bakeoff
    found = await ready(bakeoff, settings, model)
    if isinstance(found, Failure):
        return bakeoff.refuse(found.message)
    written = read_notes(notes)
    if isinstance(written, Failure):
        return bakeoff.refuse(written.message)
    measures = await _measured(parts, found, written)
    if isinstance(measures, Failure):
        return bakeoff.refuse(measures.message)
    body = dataclasses.replace(
        candidate_body(found, "candidate"),
        weights_source=written.weights_source,
        quant=written.quant,
        hard=written.hard,
        criteria=to_plain(measures),
    )
    answer = await append_step(bakeoff, bakeoff.bench.ids.new_id(), body)
    if answer is not None:
        return bakeoff.refuse(answer.message)
    usable = measures.usable_replies
    bakeoff.say(
        f"The row of {body.server} {body.model} is in: {usable.usable} of"
        f" {usable.total} replies usable."
    )
    return 0


async def ready(parts: BakeoffParts, settings: Path, model: str) -> Ready | Failure:
    """Check the entry and the capture set against the latest rule row.

    The model must stay on this Mac, its `window` and `max_output` must be
    the rule's, and the capture file must have the rule row's hash.
    """
    rows = await ledger_rows(parts.bench)
    if isinstance(rows, Failure):
        return rows
    rules = [
        r.body for r in rows if isinstance(r, BakeoffRow) and r.body.stage == "rule"
    ]
    if not rules:
        return _refused("There is no bake-off rule row. Write it first.")
    rule = read_bakeoff_rule(rules[-1].rule)
    if isinstance(rule, Failure):
        return rule
    settings_file = loaded(parts.bench, settings)
    if isinstance(settings_file, str):
        return _refused(settings_file)
    config = settings_file.config
    entry = next((e for e in config.models if e.name == model), None)
    if entry is None:
        return _refused(f"{settings} has no model entry named {model}.")
    if entry.local is None or not entry.profile.stays_on_mac:
        return _refused(f"{model} does not stay on this Mac.")
    fault = _size_fault(entry, rule)
    if fault is not None:
        return _refused(fault)
    captured = _captured(parts, rules[-1].capture_set_hash)
    if isinstance(captured, Failure):
        return captured
    if not captured:
        return _refused("The capture set holds no request.")
    return Ready(rows, rules[-1], config, entry, entry.local, captured)


def candidate_body(found: Ready, stage: BakeoffStage) -> BakeoffBody:
    """Give a body of the stage under the rule row, naming the candidate."""
    return dataclasses.replace(
        empty_body(stage, found.rule_row.rule_hash),
        capture_set_hash=found.rule_row.capture_set_hash,
        server=found.local.kind,
        server_version=found.local.server,
        model=found.entry.profile.model,
        weights_sha256=found.local.weights_sha256,
    )


async def watched_server(
    parts: MeasureParts, found: Ready, models: ModelSet
) -> Reader | Failure:
    """Check the candidate's server is up, and give a reader of its footprint."""
    down = await models.health[found.entry.name].health()
    if down is not None:
        return _refused(f"The server of {found.entry.name} is not up: {down.message}")
    port = int(found.local.base_url.rsplit(":", 1)[1])
    return parts.footprint(port)


async def usable_replies(
    client: ModelClient, captured: Sequence[Captured]
) -> UsableReplies:
    """Send each captured request once, in order, and judge each reply."""
    each: list[ReplyReason] = []
    calls = good = 0
    for one in captured:
        verdict = judge_reply(
            fold_reply(await streamed(client, one.request)),
            one.request.tools,
            one.recorded_call,
        )
        each.append(verdict.reason)
        calls += verdict.calls
        good += verdict.good_calls
    return UsableReplies(each.count("usable"), len(captured), tuple(each), calls, good)


async def streamed(client: ModelClient, request: ModelRequest) -> list[ModelEvent]:
    """Give every event of one call, to its last."""
    return [event async for event in client.stream(request)]


def read_notes(path: Path) -> CandidateNotes | Failure:
    """Read what you wrote down for the candidate, with each hard requirement once."""
    try:
        notes = from_plain(
            CandidateNotes, tomllib.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as error:
        return _refused(f"{path} could not be read: {error}.")
    names = sorted(verdict.name for verdict in notes.hard)
    if names != sorted(HARD):
        return _refused(f"{path} must give each of {', '.join(HARD)} once.")
    if notes.added_bytes < 0:
        return _refused(f"{path}: added_bytes cannot be below 0.")
    ordered = sorted(notes.hard, key=lambda verdict: HARD.index(verdict.name))
    return dataclasses.replace(notes, hard=tuple(ordered))


async def _measured(
    parts: MeasureParts, found: Ready, notes: CandidateNotes
) -> CandidateMeasures | Failure:
    models = parts.bakeoff.models(found.config)
    try:
        footprint = await watched_server(parts, found, models)
        if isinstance(footprint, Failure):
            return footprint
        clock = parts.bakeoff.bench.clock
        watch = MemoryWatch(footprint, parts.pressure, clock)
        async with parts.http() as http:
            wire = Wire(http, found.local.base_url, found.entry.profile.model, clock)
            client = models.clients[found.entry.name]
            asked = await _beside(watch, _asked(client, found.captured, wire))
            if isinstance(asked, Failure):
                return asked
            forced = await wire.forced_schema()
    finally:
        await models.close()
    if isinstance(forced, Failure):
        return forced
    peaks = watch.peaks()
    if peaks.reads == 0 or peaks.unread:
        return _refused(
            f"The server's memory could not be read {peaks.unread} times of"
            f" {peaks.reads}, so it may have stopped."
        )
    usable, first, speeds = asked
    peak = PeakMemory(
        footprint_bytes=peaks.footprint_bytes,
        added_bytes=notes.added_bytes,
        pressure_level=peaks.pressure_level,
        reads=peaks.reads,
        longest_gap_ms=peaks.longest_gap_ms,
    )
    return CandidateMeasures(
        usable_replies=usable,
        first_token_ms=first,
        tokens_per_second=speeds,
        peak_memory=peak,
        adapter_per_request=notes.adapter_per_request,
        adapter_evidence=notes.adapter_evidence,
        forced_schema=forced,
    )


async def _asked(
    client: ModelClient, captured: Sequence[Captured], wire: Wire
) -> tuple[UsableReplies, FirstToken, Speeds] | Failure:
    usable = await usable_replies(client, captured)
    first = await wire.first_token()
    if isinstance(first, Failure):
        return first
    speeds = await wire.speeds()
    if isinstance(speeds, Failure):
        return speeds
    return usable, first, speeds


async def _beside[T](watch: MemoryWatch, work: Awaitable[T]) -> T:
    # The watch reads for exactly as long as the work runs. Waiting on the
    # cancelled task, not awaiting it, keeps a cancel of this one raised,
    # and a watch that stopped on an error of its own still raises it.
    task = asyncio.create_task(watch.watch())
    try:
        return await work
    finally:
        task.cancel()
        await asyncio.wait([task])
        if not task.cancelled():
            task.result()


def _size_fault(entry: ModelEntry, rule: BakeoffRule) -> str | None:
    sizes = (entry.profile.window, entry.profile.max_output)
    if sizes == (rule.capture_window, rule.capture_max_tokens):
        return None
    return (
        f"{entry.name} must have the rule's window, {rule.capture_window}, and its"
        f" max_output, {rule.capture_max_tokens}."
    )


def _captured(parts: BakeoffParts, digest: str) -> tuple[Captured, ...] | Failure:
    path = capture_path(parts.captures(), digest)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return _refused(f"{path} could not be read: {error.strerror}.")
    if set_hash(text) != digest:
        return _refused(f"{path} is not the capture set the rule row names.")
    try:
        return read_capture(text)
    except ValueError as error:
        return _refused(f"{path} could not be read: {error}.")


def _refused(message: str) -> Failure:
    return Failure(
        kind="invalid_input", effect="none", message=message, source="bakeoff"
    )
