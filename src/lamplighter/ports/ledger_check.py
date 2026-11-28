"""The rules a row keeps to enter the book of experiments, shared by every adapter.

A row is read back with the same check that lets it in, so no row that
was let in can make the book unreadable.
"""

import re
from collections.abc import Sequence

from lamplighter.ports.experiment_ledger import (
    BakeoffBody,
    BakeoffRow,
    CorrectionRow,
    DecisionRow,
    LedgerRow,
    PlanRow,
    RunSetRow,
    StartRow,
)
from lamplighter.ports.failure import Failure, FailureKind
from lamplighter.ports.json_text import canonical_json, read_saved_json
from lamplighter.ports.json_value import JsonObject
from lamplighter.ports.plain import from_plain
from lamplighter.ports.value_rules import is_hash

SOURCE = "experiment_ledger"
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MOST_NAME = 40
_ROWS: dict[str, type[LedgerRow]] = {
    "plan": PlanRow,
    "start": StartRow,
    "run_set": RunSetRow,
    "decision": DecisionRow,
    "correction": CorrectionRow,
    "bakeoff": BakeoffRow,
}
# How many config ids a row of each kind names. A run set of a baseline
# harness names none.
_CONFIG_IDS: dict[str, tuple[int, ...]] = {
    "plan": (2,),
    "start": (1,),
    "run_set": (0, 1),
    "decision": (2,),
    "correction": (0,),
    "bakeoff": (0,),
}


def read_row(text: str) -> LedgerRow:
    """Give the row a line holds. Raises ValueError unless it is a row of version 1."""
    plain = read_saved_json(text)
    kind = plain.get("kind") if isinstance(plain, dict) else None
    if not isinstance(kind, str) or kind not in _ROWS:
        raise ValueError(f"{kind!r} is not a kind of row")
    row = from_plain(_ROWS[kind], plain)
    fault = _shape_fault(row)
    if fault is not None:
        raise ValueError(fault)
    return row


def row_text(row: LedgerRow) -> str:
    """Give the one line a row is written as, without its newline."""
    return canonical_json(row)


def refused_row(rows: Sequence[LedgerRow], row: LedgerRow) -> Failure | None:
    """Give why the row may not be added after these rows, or None.

    The rules are tried in order and the first that applies answers: the
    row's shape, a row id already used, a second plan, start, run set or
    decision, a start or run set that does not agree with its plan, a
    decision without its plan and run sets, a decision with the wrong
    count of held-out uses, and a correction of a row that is not there.

    A bake-off row is refused when it is a second rule row that does not
    name the latest as the one it replaces and say why, or a first one
    that names one; a candidate or soak row whose rule hash or capture
    set hash is not the latest rule row's; or a chosen choice row whose
    winner has no passing soak row under the same rule.
    """
    try:
        read_row(row_text(row))
    except ValueError as error:
        return _refused("invalid_input", f"The row is not one of the book's: {error}")
    if any(r.row_id == row.row_id for r in rows):
        return _refused("conflict", f"The row id {row.row_id} is already used.")
    if _taken(rows, row):
        return _refused("conflict", f"The {row.kind} row of {_who(row)} is there.")
    disagrees = _disagrees(rows, row)
    if disagrees is not None:
        return _refused("invalid_input", disagrees)
    if isinstance(row, DecisionRow):
        uses = held_out_uses([*rows, row])
        if row.body.held_out_uses != uses:
            return _refused("invalid_input", f"The held-out uses are {uses}.")
    if isinstance(row, CorrectionRow) and not any(
        r.row_id == row.body.corrects and r.experiment == row.experiment for r in rows
    ):
        return _refused("invalid_input", "The row to correct is not in the book.")
    return None


def held_out_uses(rows: Sequence[LedgerRow]) -> int:
    """Count the experiments with a side started on the held-out set."""
    return len(
        {
            r.experiment
            for r in rows
            if isinstance(r, StartRow) and r.body.split == "held_out"
        }
    )


def _shape_fault(row: LedgerRow) -> str | None:
    if row.v != 1:
        return f"version {row.v}"
    if len(row.config_ids) not in _CONFIG_IDS[row.kind]:
        return f"a {row.kind} row with {len(row.config_ids)} config ids"
    if isinstance(row, BakeoffRow):
        return _bakeoff_fault(row)
    loose = isinstance(row, RunSetRow) and not row.experiment
    # A correction of a loose run set has the run set's empty experiment.
    unnamed = loose or (isinstance(row, CorrectionRow) and not row.experiment)
    if not unnamed and not _named(row.experiment):
        return f"the experiment name {row.experiment!r}"
    if isinstance(row, StartRow) and row.body.run_set != _side_name(row):
        return f"a start of the run set {row.body.run_set!r}"
    if isinstance(row, RunSetRow):
        return _run_set_fault(row, loose)
    if isinstance(row, DecisionRow) and not isinstance(
        row.body.decision.get("rule_hash"), str
    ):
        return "a decision with no rule hash"
    return None


def _run_set_fault(row: RunSetRow, loose: bool) -> str | None:
    found = row.body.run_set
    name = found.get("run_set")
    if not all(isinstance(found.get(k), str) for k in ("split", "task_set_hash")):
        return "a run set with no split or task set hash"
    if type(found.get("trials_per_task")) is not int:
        return "a run set with no trials per task"
    if loose != (row.body.side == ""):
        return "a run set whose side does not fit its experiment"
    if loose and not (isinstance(name, str) and _named(name)):
        return f"a loose run set named {name!r}"
    if not loose and name != f"{row.experiment}--{row.body.side}":
        return f"a run set named {name!r} for its side"
    return None


def _bakeoff_fault(row: BakeoffRow) -> str | None:
    body = row.body
    if row.experiment:
        return f"a bakeoff row of the experiment {row.experiment!r}"
    if not is_hash(body.rule_hash):
        return "a bakeoff row with no rule hash"
    if body.stage == "soak" and _soak_passed(body) is None:
        return "a soak row with no stays_up result"
    named = all((body.server, body.model, body.weights_sha256))
    unnamed = not any((body.server, body.model, body.weights_sha256))
    if body.stage == "choice" and not (named if body.chosen else unnamed):
        return "a choice row whose winner does not fit whether one is chosen"
    return None


def _soak_passed(body: BakeoffBody) -> bool | None:
    found = body.criteria.get("stays_up")
    passed = found.get("passed") if isinstance(found, dict) else None
    return passed if isinstance(passed, bool) else None


def _taken(rows: Sequence[LedgerRow], row: LedgerRow) -> bool:
    # One plan, one start and one run set for each side, and one decision.
    if isinstance(row, RunSetRow) and not row.experiment:
        return False
    if isinstance(row, CorrectionRow | BakeoffRow):
        return False
    return any(
        r.kind == row.kind and r.experiment == row.experiment and _side(r) == _side(row)
        for r in rows
    )


def _disagrees(rows: Sequence[LedgerRow], row: LedgerRow) -> str | None:
    if isinstance(row, BakeoffRow):
        return _bakeoff_disagrees(rows, row.body)
    if isinstance(row, RunSetRow) and not row.experiment:
        split = row.body.run_set.get("split")
        return (
            "A loose run set may not use the held-out set."
            if split == "held_out"
            else None
        )
    plan = next(
        (r for r in rows if isinstance(r, PlanRow) and r.experiment == row.experiment),
        None,
    )
    if isinstance(row, StartRow):
        return _start_disagrees(plan, row)
    if isinstance(row, RunSetRow):
        return _run_set_disagrees(plan, rows, row)
    if isinstance(row, DecisionRow):
        return _decision_disagrees(plan, rows, row)
    return None


def _start_disagrees(plan: PlanRow | None, row: StartRow) -> str | None:
    if plan is None:
        return f"The experiment {row.experiment} has no plan."
    if row.body.split != plan.body.split:
        return "The start's split is not the plan's."
    if row.config_ids != (_planned_id(plan, row.body.side),):
        return "The start's config id is not the plan's for its side."
    return None


def _run_set_disagrees(
    plan: PlanRow | None, rows: Sequence[LedgerRow], row: RunSetRow
) -> str | None:
    side = row.body.side
    started = any(isinstance(r, StartRow) and _same_side(r, row) for r in rows)
    if plan is None or side == "" or not started:
        return f"The {side} side of {row.experiment} has not started."
    found: JsonObject = row.body.run_set
    planned = (plan.body.split, plan.body.task_set_hash, plan.body.trials_per_task)
    if (found["split"], found["task_set_hash"], found["trials_per_task"]) != planned:
        return "The run set's split, task set or trials per task are not the plan's."
    if row.config_ids != (_planned_id(plan, side),):
        return "The run set's config id is not the plan's for its side."
    return None


def _decision_disagrees(
    plan: PlanRow | None, rows: Sequence[LedgerRow], row: DecisionRow
) -> str | None:
    if plan is None or plan.body.rule_hash != row.body.decision["rule_hash"]:
        return f"The experiment {row.experiment} has no plan with this rule."
    sides = {
        r.body.side
        for r in rows
        if isinstance(r, RunSetRow) and r.experiment == row.experiment
    }
    if sides != {"baseline", "candidate"}:
        return f"The experiment {row.experiment} lacks a run set for a side."
    return None


def _bakeoff_disagrees(rows: Sequence[LedgerRow], body: BakeoffBody) -> str | None:
    rules = [
        r.body for r in rows if isinstance(r, BakeoffRow) and r.body.stage == "rule"
    ]
    latest = rules[-1] if rules else None
    if body.stage == "rule":
        return _rule_disagrees(latest, body)
    if body.stage in ("candidate", "soak"):
        under = latest is not None and (body.rule_hash, body.capture_set_hash) == (
            latest.rule_hash,
            latest.capture_set_hash,
        )
        return None if under else f"The {body.stage} row is not under the latest rule."
    if body.chosen and not any(_passing_soak(r, body) for r in rows):
        return f"The winner {body.server} {body.model} has no passing soak row."
    return None


def _rule_disagrees(latest: BakeoffBody | None, body: BakeoffBody) -> str | None:
    if latest is None:
        return "There is no rule row to replace." if body.replaces else None
    if body.replaces != latest.rule_hash or not body.why:
        return (
            "A rule row is there: a new one names the latest as the one it"
            " replaces, and says why."
        )
    return None


def _passing_soak(row: LedgerRow, choice: BakeoffBody) -> bool:
    if not isinstance(row, BakeoffRow) or row.body.stage != "soak":
        return False
    soak = row.body
    same = (soak.rule_hash, soak.server, soak.model, soak.weights_sha256) == (
        choice.rule_hash,
        choice.server,
        choice.model,
        choice.weights_sha256,
    )
    return same and _soak_passed(soak) is True


def _planned_id(plan: PlanRow, side: str) -> str:
    return plan.config_ids[0 if side == "baseline" else 1]


def _side(row: LedgerRow) -> str:
    if isinstance(row, StartRow | RunSetRow):
        return row.body.side
    return ""


def _same_side(start: StartRow, row: RunSetRow) -> bool:
    return start.experiment == row.experiment and start.body.side == row.body.side


def _side_name(row: StartRow) -> str:
    return f"{row.experiment}--{row.body.side}"


def _named(name: str) -> bool:
    return len(name) <= MOST_NAME and _NAME.fullmatch(name) is not None


def _who(row: LedgerRow) -> str:
    side = _side(row)
    return f"{row.experiment} {side}".strip()


def _refused(kind: FailureKind, message: str) -> Failure:
    return Failure(kind=kind, effect="none", message=message, source=SOURCE)
