"""The bake-off's rule: its values and hash, and the choice it makes from the rows."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from lamplighter.bench.bakeoff_measures import HARD, CandidateMeasures, SoakCounts
from lamplighter.ports import (
    BakeoffBody,
    BakeoffRow,
    Failure,
    JsonValue,
    LedgerRow,
    canonical_json,
    from_plain,
    from_stored,
)

SOURCE = "bakeoff_rule"


@dataclass(frozen=True, slots=True)
class BakeoffRule:
    """What the bake-off chooses by, fixed before any candidate runs.

    `tie_points` is in points out of 100. `pressure_limit` is the level of
    `kern.memorystatus_vm_pressure_level` that puts a candidate out.
    """

    memory_limit_bytes: int
    tie_points: int
    pressure_limit: int
    capture_window: int
    capture_max_tokens: int


@dataclass(frozen=True, slots=True)
class Candidate:
    """One server with one model's exact weights."""

    server: str
    model: str
    weights_sha256: str


@dataclass(frozen=True, slots=True)
class Left:
    """A candidate the rule put out, and the step and reason it did."""

    candidate: Candidate
    why: str


@dataclass(frozen=True, slots=True)
class BakeoffChoice:
    """What the rule makes of the rows under the latest rule row.

    `order` holds every candidate still in after step 2, in the rule's
    order. `to_soak` is the next to soak when the choice waits on a soak.
    Otherwise `chosen` is the winner, or None when no candidate is left
    or none passed its soak. `why` names the steps.
    """

    rule_hash: str
    order: tuple[Candidate, ...]
    out: tuple[Left, ...]
    to_soak: Candidate | None
    chosen: Candidate | None
    why: str


def read_bakeoff_rule(plain: JsonValue) -> BakeoffRule | Failure:
    """Read a rule, refusing any key it does not have and any value below 1."""
    try:
        rule = from_plain(BakeoffRule, plain)
    except ValueError as error:
        return _refused(f"The bake-off rule does not have the expected form: {error}.")
    if min(rule.memory_limit_bytes, rule.pressure_limit, rule.capture_window) < 1:
        return _refused("The bake-off rule's limits and window must be 1 or more.")
    if not 0 <= rule.tie_points <= 100:
        return _refused("The bake-off rule's tie_points must be 0 to 100.")
    if not 0 < rule.capture_max_tokens < rule.capture_window:
        return _refused("The bake-off rule's capture_max_tokens must fit its window.")
    return rule


def bakeoff_rule_hash(rule: BakeoffRule) -> str:
    """Give the SHA-256 of the rule's canonical JSON, the name it chooses under."""
    return hashlib.sha256(canonical_json(rule).encode("utf-8")).hexdigest()


def stays_up(counts: SoakCounts) -> bool:
    """Say whether a soak passed.

    It passed when it ran its whole time, no request failed, the footprint
    at the end is at most 110 % of the first hour's peak, and the median
    time of the last hour is at most 1.5 times the first hour's.
    """
    return (
        counts.whole
        and counts.requests > 0
        and counts.failed == 0
        and counts.end_bytes * 10 <= counts.first_hour_peak_bytes * 11
        and counts.last_hour_median_ms * 2 <= counts.first_hour_median_ms * 3
    )


def bakeoff_choice(rows: Sequence[LedgerRow]) -> BakeoffChoice | Failure:
    """Apply the latest rule row's rule to the latest row of each candidate under it.

    Step 1 puts out a candidate that failed or lacks a hard requirement.
    Step 2 puts out one whose peak is above the memory limit, or whose
    Mac reached the pressure limit. Step 3 finds the lead's share of
    usable replies and ties every candidate within `tie_points` of it.
    Step 4 orders the tied by generation speed at 32,000 tokens, then the
    others by share, each higher first, and equal ones by the smaller
    peak, then the server's name, then the model's. Step 5 walks the
    order: a candidate with no soak row is the next to soak, one whose
    soak failed is passed over, and the first whose soak passed is chosen.
    """
    steps = [r.body for r in rows if isinstance(r, BakeoffRow)]
    rules = [s for s in steps if s.stage == "rule"]
    if not rules:
        return _refused("There is no bake-off rule row.")
    latest = rules[-1]
    rule = read_bakeoff_rule(latest.rule)
    if isinstance(rule, Failure):
        return rule
    under = [s for s in steps if s.rule_hash == latest.rule_hash]
    measured: list[tuple[Candidate, CandidateMeasures]] = []
    out: list[Left] = []
    for candidate, body in _latest_of_each(under, "candidate").items():
        kept = _kept(rule, body)
        if isinstance(kept, str):
            out.append(Left(candidate, kept))
        else:
            measured.append((candidate, kept))
    order = _ordered(rule, measured)
    return _soaked(latest.rule_hash, order, tuple(out), _latest_of_each(under, "soak"))


def candidate_of(body: BakeoffBody) -> Candidate:
    """Give the candidate a bake-off row names."""
    return Candidate(body.server, body.model, body.weights_sha256)


@dataclass(frozen=True, slots=True)
class _SoakPassed:
    # The part of a soak row's `stays_up` that the choice reads, which the
    # book's check lets in only when it is there.
    passed: bool


def _latest_of_each(
    steps: Sequence[BakeoffBody], stage: str
) -> dict[Candidate, BakeoffBody]:
    # A candidate measured again keeps only its latest row.
    return {candidate_of(s): s for s in steps if s.stage == stage}


def _kept(rule: BakeoffRule, body: BakeoffBody) -> CandidateMeasures | str:
    # The measures of a candidate still in after step 2, or why it is out.
    passed = {v.name for v in body.hard if v.passed}
    missing = [name for name in HARD if name not in passed]
    if missing:
        return f"step 1: {', '.join(missing)} did not pass"
    try:
        measures = from_plain(CandidateMeasures, body.criteria)
    except ValueError as error:
        return f"step 2: its measures cannot be read: {error}"
    memory = measures.peak_memory
    peak = memory.footprint_bytes + memory.added_bytes
    if peak > rule.memory_limit_bytes:
        return f"step 2: its peak of {peak:,} bytes is above the limit"
    if memory.pressure_level >= rule.pressure_limit:
        return f"step 2: the Mac reached pressure level {memory.pressure_level}"
    if measures.usable_replies.total < 1:
        return "step 2: it was measured on no request"
    return measures


def _ordered(
    rule: BakeoffRule, measured: Sequence[tuple[Candidate, CandidateMeasures]]
) -> tuple[Candidate, ...]:
    if not measured:
        return ()
    lead = max(_share(m) for _, m in measured)
    floor = lead - Fraction(rule.tie_points, 100)
    tied = [(c, m) for c, m in measured if _share(m) >= floor]
    rest = [(c, m) for c, m in measured if _share(m) < floor]
    tied.sort(key=lambda p: (-p[1].tokens_per_second.generation_32k_milli, *_last(p)))
    rest.sort(key=lambda p: (-_share(p[1]), *_last(p)))
    return tuple(c for c, _ in (*tied, *rest))


def _share(measures: CandidateMeasures) -> Fraction:
    usable = measures.usable_replies
    return Fraction(usable.usable, usable.total)


def _last(pair: tuple[Candidate, CandidateMeasures]) -> tuple[int, str, str]:
    candidate, measures = pair
    memory = measures.peak_memory
    peak = memory.footprint_bytes + memory.added_bytes
    return peak, candidate.server, candidate.model


def _soaked(
    rule_hash: str,
    order: tuple[Candidate, ...],
    out: tuple[Left, ...],
    soaks: dict[Candidate, BakeoffBody],
) -> BakeoffChoice:
    failed: list[str] = []
    for place, candidate in enumerate(order, start=1):
        soak = soaks.get(candidate)
        if soak is None:
            why = f"step 5: {_name(candidate)} is next to soak"
            return BakeoffChoice(rule_hash, order, out, candidate, None, why)
        if from_stored(_SoakPassed, soak.criteria["stays_up"]).passed:
            why = (
                f"steps 3 and 4: it is number {place} in the order; step 5: its soak"
                " passed"
            )
            if failed:
                why += f", after {', '.join(failed)} failed theirs"
            return BakeoffChoice(rule_hash, order, out, None, candidate, why)
        failed.append(_name(candidate))
    why = (
        "step 5: no candidate passed its soak"
        if order
        else "step 2: no candidate is left"
    )
    return BakeoffChoice(rule_hash, order, out, None, None, why)


def _name(candidate: Candidate) -> str:
    return f"{candidate.server} {candidate.model}"


def _refused(message: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=SOURCE)
