"""The bake-off's `soak`: one captured request a minute to the finalist, for a day.

As it goes, the soak adds a line a minute to a file in the bench folder.
A soak killed part way leaves that file behind, and the next soak writes
its row from it as not whole, which never passes. One soak runs at a
time, and the Mac is kept awake for exactly as long as it runs.
"""

import dataclasses
import os
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lamplighter.app.bakeoff import BakeoffParts, append_step
from lamplighter.app.bakeoff_measure import (
    MeasureParts,
    Reader,
    Ready,
    candidate_body,
    ready,
    streamed,
    watched_server,
)
from lamplighter.app.capture_set import Captured
from lamplighter.app.single_instance import lock_store
from lamplighter.bench import SoakCounts, SoakMeasures, candidate_of, stays_up
from lamplighter.ports import (
    BakeoffBody,
    BakeoffRow,
    ErrorEvent,
    Failure,
    ModelClient,
    ModelEvent,
    from_plain,
    read_saved_json,
    saved_json,
    to_plain,
)

SOAK_MINUTES = 24 * 60
SOAK_FOLDER = "soak"
_HOUR_MS = 3_600_000


@dataclass(frozen=True, slots=True)
class SoakSample:
    """One minute of a soak.

    `at_ms` is when its request was sent, from the soak's start, and
    `took_ms` how long the reply took to its end. `failed` says it ended
    with a failure of the server. `footprint_bytes` is the
    server's footprint after it, or None when it could not be read.
    """

    at_ms: int
    took_ms: int
    failed: bool
    footprint_bytes: int | None


@dataclass(frozen=True, slots=True)
class SoakStart:
    """The first line of a soak's file: the id and body of the row it will write."""

    row_id: str
    body: BakeoffBody


async def soak(parts: MeasureParts, settings: Path, model: str) -> int:
    """Soak the candidate the entry names for a day, and write its row."""
    held = lock_store(soak_folder(parts.bakeoff))
    if held is None:
        return parts.bakeoff.refuse("Another soak is running.")
    try:
        return await _soak_alone(parts, settings, model)
    finally:
        os.close(held)


async def _soak_alone(parts: MeasureParts, settings: Path, model: str) -> int:
    bakeoff = parts.bakeoff
    found = await ready(bakeoff, settings, model)
    if isinstance(found, Failure):
        return bakeoff.refuse(found.message)
    left = await _cut_short(bakeoff, found)
    if left is not None:
        return bakeoff.refuse(left.message)
    measured = _measured_row(found)
    if isinstance(measured, Failure):
        return bakeoff.refuse(measured.message)
    body = dataclasses.replace(
        candidate_body(found, "soak"),
        weights_source=measured.weights_source,
        quant=measured.quant,
    )
    start = SoakStart(bakeoff.bench.ids.new_id(), body)
    stop = parts.keep_awake()
    try:
        samples = await _soaked(parts, found, start)
    finally:
        stop()
    if isinstance(samples, Failure):
        return bakeoff.refuse(samples.message)
    counts = soak_counts(samples, whole=True)
    answer = await _write(bakeoff, start, counts)
    if answer is not None:
        return bakeoff.refuse(answer.message)
    progress_path(bakeoff).unlink()
    passed = "passed" if stays_up(counts) else "did not pass"
    bakeoff.say(f"The soak of {body.server} {body.model} {passed}: {to_plain(counts)}")
    return 0


def soak_counts(samples: Sequence[SoakSample], whole: bool) -> SoakCounts:
    """Give what the samples show.

    A minute failed when its request ended with a failure of the server,
    not of the model's output, or the server's footprint could not be
    read, which is what a server that stopped shows. The last hour is the
    hour up to the last sample.
    """
    if not samples:
        return SoakCounts(whole, 0, 0, 0, 0, 0, 0)
    end = samples[-1].at_ms
    first = [s for s in samples if s.at_ms < _HOUR_MS]
    last = [s for s in samples if s.at_ms > end - _HOUR_MS]
    read = [s.footprint_bytes for s in samples if s.footprint_bytes is not None]
    early = [s.footprint_bytes for s in first if s.footprint_bytes is not None]
    return SoakCounts(
        whole=whole,
        requests=len(samples),
        failed=sum(s.failed or s.footprint_bytes is None for s in samples),
        first_hour_peak_bytes=max(early, default=0),
        end_bytes=read[-1] if read else 0,
        first_hour_median_ms=statistics.median_low(s.took_ms for s in first),
        last_hour_median_ms=statistics.median_low(s.took_ms for s in last),
    )


def read_progress(text: str) -> tuple[SoakStart, tuple[SoakSample, ...]] | None:
    """Give what a soak's file holds, or None when not even its first line is whole.

    Each line ends with a line break, so a last piece with none is a line
    whose write was cut by the kill, and is left out. Raises ValueError
    for a whole line that a soak did not write.
    """
    lines = text.split("\n")[:-1]
    if not lines:
        return None
    start = from_plain(SoakStart, read_saved_json(lines[0]))
    samples = tuple(from_plain(SoakSample, read_saved_json(line)) for line in lines[1:])
    return start, samples


def soak_folder(parts: BakeoffParts) -> Path:
    """Give the folder of the soak's lock and progress, in the bench folder."""
    return Path(parts.bench.settings.dir) / SOAK_FOLDER


def progress_path(parts: BakeoffParts) -> Path:
    """Give the file a soak keeps its progress in."""
    return soak_folder(parts) / "progress.jsonl"


async def _cut_short(parts: BakeoffParts, found: Ready) -> Failure | None:
    # The file of a soak that did not finish: write its row as not whole,
    # unless its row went in before the file was removed, or it was under
    # an earlier rule row, where there is nothing to write.
    path = progress_path(parts)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        return _refused(f"{path} could not be read: {error.strerror}.")
    try:
        progress = read_progress(text)
    except ValueError as error:
        return _refused(f"{path} is not a soak's file: {error}.")
    if progress is not None:
        start, samples = progress
        if _still_to_write(found, start):
            answer = await _write(parts, start, soak_counts(samples, whole=False))
            if answer is not None:
                return answer
            parts.say(
                f"The soak of {start.body.server} {start.body.model} was cut short"
                f" after {len(samples)} minutes. Its row is in, and it does not pass."
            )
    path.unlink()
    return None


def _still_to_write(found: Ready, start: SoakStart) -> bool:
    written = any(row.row_id == start.row_id for row in found.rows)
    rule = found.rule_row
    under = (start.body.rule_hash, start.body.capture_set_hash) == (
        rule.rule_hash,
        rule.capture_set_hash,
    )
    return under and not written


def _measured_row(found: Ready) -> BakeoffBody | Failure:
    # The soak is of a candidate already measured under the rule row, and
    # its row names the weights the way that one did.
    named = candidate_of(candidate_body(found, "soak"))
    rows = [
        r.body
        for r in found.rows
        if isinstance(r, BakeoffRow)
        and r.body.stage == "candidate"
        and (r.body.rule_hash, r.body.capture_set_hash)
        == (found.rule_row.rule_hash, found.rule_row.capture_set_hash)
        and candidate_of(r.body) == named
    ]
    if not rows:
        return _refused(
            f"{named.server} {named.model} has no candidate row under the rule row."
            " Run it first."
        )
    return rows[-1]


async def _soaked(
    parts: MeasureParts, found: Ready, start: SoakStart
) -> list[SoakSample] | Failure:
    models = parts.bakeoff.models(found.config)
    try:
        footprint = await watched_server(parts, found, models)
        if isinstance(footprint, Failure):
            return footprint
        client = models.clients[found.entry.name]
        return await _minutes(parts.bakeoff, client, found.captured, footprint, start)
    finally:
        await models.close()


async def _minutes(
    parts: BakeoffParts,
    client: ModelClient,
    captured: Sequence[Captured],
    footprint: Reader,
    start: SoakStart,
) -> list[SoakSample] | Failure:
    clock = parts.bench.clock
    path = progress_path(parts)
    kept = _kept(path, to_plain(start))
    if kept is not None:
        return kept
    began = clock.monotonic()
    samples: list[SoakSample] = []
    for minute in range(SOAK_MINUTES):
        await clock.sleep(max(0.0, began + minute * 60 - clock.monotonic()))
        sent = clock.monotonic()
        events = await streamed(client, captured[minute % len(captured)].request)
        sample = SoakSample(
            at_ms=round((sent - began) * 1000),
            took_ms=round((clock.monotonic() - sent) * 1000),
            failed=_server_failed(events[-1]),
            footprint_bytes=footprint(),
        )
        samples.append(sample)
        kept = _kept(path, to_plain(sample))
        if kept is not None:
            return kept
    return samples


def _server_failed(end: ModelEvent) -> bool:
    # A reply the adapter's end checks refused, for call markup in its text
    # or calls it dropped, was answered: the model's output is judged by
    # `usable_replies`, and the soak asks whether the server stays up.
    return isinstance(end, ErrorEvent) and "model_output" not in end.failure.detail


def _kept(path: Path, line: object) -> Failure | None:
    # One whole line a write, on the disk before the next minute, so a kill
    # or a power cut loses at most the line being written.
    try:
        with path.open("a", encoding="utf-8") as file:
            file.write(saved_json(line) + "\n")
            file.flush()
            os.fsync(file.fileno())
    except OSError as error:
        return _refused(f"The soak's progress could not be kept: {error.strerror}.")
    return None


async def _write(
    parts: BakeoffParts, start: SoakStart, counts: SoakCounts
) -> Failure | None:
    measures = SoakMeasures(stays_up(counts), counts)
    body = dataclasses.replace(start.body, criteria={"stays_up": to_plain(measures)})
    return await append_step(parts, start.row_id, body)


def _refused(message: str) -> Failure:
    return Failure(
        kind="invalid_input", effect="none", message=message, source="bakeoff"
    )
