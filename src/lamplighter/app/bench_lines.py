"""The lines the bench command prints for a decision, a row of the ledger, a run set."""

from collections.abc import Sequence

from lamplighter.bench import GateDecision, RunSet, SideSummary
from lamplighter.ports import (
    BakeoffRow,
    CorrectionRow,
    DecisionRow,
    LedgerRow,
    PlanRow,
    RunSetRow,
    StartRow,
    from_plain,
)


def decision_lines(decision: GateDecision) -> list[str]:
    """Give the gate's answer, then every number it was decided by."""
    lines = [f"The gate's answer: {decision.outcome}, because {decision.why}."]
    if decision.difference_bp is not None:
        lines.append(
            f"Tasks: {decision.tasks}, {decision.tasks_up} up, {decision.tasks_down}"
            f" down, {decision.tasks_same} the same."
        )
        lines.append(f"Difference: {_points(decision.difference_bp)} points.")
    else:
        lines.append(f"Tasks: {decision.tasks}. The test was not run.")
    if decision.interval_low_bp is not None and decision.interval_high_bp is not None:
        lines.append(
            f"90% interval, a reading aid: {_points(decision.interval_low_bp)}"
            f" to {_points(decision.interval_high_bp)} points."
        )
    if decision.p_better_ppm is not None and decision.p_worse_ppm is not None:
        lines.append(
            f"p better: {_millionths(decision.p_better_ppm)},"
            f" p worse: {_millionths(decision.p_worse_ppm)}."
        )
    lines.append(_side("Baseline", decision.baseline))
    lines.append(_side("Candidate", decision.candidate))
    lines.extend(
        f"Guard {g.name}: {'passed' if g.passed else 'failed'}."
        for g in decision.guards
    )
    lines.extend(
        f"Changed: {c.task}, {c.baseline_passes} of {c.baseline_valid} passed,"
        f" now {c.candidate_passes} of {c.candidate_valid}."
        for c in decision.changed
    )
    if decision.why == "budget_reached":
        lines.append("A run set reached its money stop, so the answer is no answer.")
    return lines


def noise_floor_line(rows: Sequence[LedgerRow]) -> str:
    """Give the gap and the cost ratio of the newest A/A decision in the ledger.

    A decision is A/A when its two config ids are the same. The gap is
    noise, and is shown so that a later difference is read beside it.
    """
    newest = next(
        (
            r
            for r in reversed(rows)
            if isinstance(r, DecisionRow) and r.body.same_config
        ),
        None,
    )
    if newest is None:
        return "Noise floor: no A/A experiment has a decision yet."
    decision = from_plain(GateDecision, newest.body.decision)
    gap = (
        f"a difference of {_points(decision.difference_bp)} points"
        if decision.difference_bp is not None
        else f"no difference measured, because {decision.why}"
    )
    base = decision.baseline.micro_dollars_per_valid_trial
    cand = decision.candidate.micro_dollars_per_valid_trial
    ratio = (
        f"a cost ratio of {_thousandths(cand * 1000 // base)}"
        if base and cand is not None
        else "no cost ratio"
    )
    return f"Noise floor, from the A/A experiment {newest.experiment}: {gap}, {ratio}."


def ledger_line(row: LedgerRow) -> str:
    """Give one line that names a row and says what it holds."""
    head = f"{row.at}  {row.row_id}  {row.kind}  {row.experiment or '-'}"
    match row:
        case PlanRow():
            body = row.body
            what = (
                f"{len(body.tasks)} tasks on {body.split}, {body.trials_per_task} each"
            )
        case StartRow():
            what = f"{row.body.side} starts on {row.body.split}"
        case RunSetRow():
            what = run_set_line(from_plain(RunSet, row.body.run_set))
        case DecisionRow():
            decision = from_plain(GateDecision, row.body.decision)
            what = f"{decision.outcome}, because {decision.why}"
        case CorrectionRow():
            what = f"corrects {row.body.corrects}: {row.body.note}"
        case BakeoffRow():
            step = row.body
            what = f"bake-off {step.stage} {step.server} {step.model}".rstrip()
    return f"{head}  {what}"


def run_set_line(found: RunSet) -> str:
    """Give a run set's name and how its trials came out."""
    counts = {"pass": 0, "fail": 0, "infra": 0}
    for trial in found.trials:
        counts[trial.outcome] += 1
    return (
        f"{found.run_set}: {counts['pass']} passed, {counts['fail']} failed,"
        f" {counts['infra']} infra, of {len(found.trials)} trials"
    )


def _side(name: str, side: SideSummary) -> str:
    mean = "" if side.mean_pass_bp is None else f", mean {_points(side.mean_pass_bp)}"
    cost = side.micro_dollars_per_valid_trial
    return (
        f"{name}: {side.passes} passed, {side.fails} failed, {side.infra} infra"
        f" of {side.planned}{mean}; {side.tasks_all_passed} tasks passed every"
        f" time, {side.tasks_any_passed} at least once;"
        f" {'unknown' if cost is None else cost} micro-dollars a valid trial."
    )


def _points(bp: int) -> str:
    sign = "-" if bp < 0 else ""
    return f"{sign}{abs(bp) // 100}.{abs(bp) % 100:02d}"


def _millionths(ppm: int) -> str:
    return f"{ppm // 1_000_000}.{ppm % 1_000_000:06d}"


def _thousandths(permille: int) -> str:
    return f"{permille // 1000}.{permille % 1000:03d}"
