"""Made-up rows of the book of experiments, and the work of a second process on it."""

import asyncio
import itertools
import os
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path

from lamplighter.ports import (
    CorrectionBody,
    CorrectionRow,
    DecisionBody,
    DecisionRow,
    LedgerRow,
    PlanBody,
    PlanRow,
    RunSetBody,
    RunSetRow,
    SettingsFile,
    Side,
    SplitName,
    StartBody,
    StartRow,
)

RULE_HASH = "r" * 64
TASK_SET = "t" * 64
BASELINE_ID = "b" * 64
CANDIDATE_ID = "c" * 64
_IDS = itertools.count(1)


def new_id() -> str:
    return f"row-{os.getpid()}-{next(_IDS)}"


def plan(experiment: str = "made-up", split: SplitName = "dev") -> PlanRow:
    return PlanRow(
        kind="plan",
        row_id=new_id(),
        at="2026-11-28T10:00:00.000000Z",
        experiment=experiment,
        config_ids=(BASELINE_ID, CANDIDATE_ID),
        code_revision="unknown",
        body=PlanBody(
            rule={"alpha_permille": 50},
            rule_hash=RULE_HASH,
            split=split,
            task_set_hash=TASK_SET,
            tasks=("task-a", "task-b"),
            trials_per_task=3,
            baseline=SettingsFile("bench/configs/made-up.toml", "s" * 64),
            candidate=SettingsFile("bench/configs/other.toml", "o" * 64),
            harbor_version="0.27.0",
            note="A made-up plan.",
        ),
    )


def _planned(side: Side) -> str:
    return BASELINE_ID if side == "baseline" else CANDIDATE_ID


def start(
    experiment: str = "made-up",
    side: Side = "baseline",
    split: SplitName = "dev",
    config_id: str | None = None,
) -> StartRow:
    return StartRow(
        kind="start",
        row_id=new_id(),
        at="2026-11-28T10:01:00.000000Z",
        experiment=experiment,
        config_ids=(config_id or _planned(side),),
        code_revision="unknown",
        body=StartBody(side=side, run_set=f"{experiment}--{side}", split=split),
    )


def run_set(
    experiment: str = "made-up",
    side: Side = "baseline",
    split: SplitName = "dev",
    task_set_hash: str = TASK_SET,
    config_id: str | None = None,
) -> RunSetRow:
    return RunSetRow(
        kind="run_set",
        row_id=new_id(),
        at="2026-11-28T11:00:00.000000Z",
        experiment=experiment,
        config_ids=(config_id or _planned(side),),
        code_revision="unknown",
        body=RunSetBody(
            run_set={
                "run_set": f"{experiment}--{side}",
                "split": split,
                "task_set_hash": task_set_hash,
                "trials_per_task": 3,
            },
            side=side,
            totals={"passes": 4},
            started_at="2026-11-28T10:01:00.000000Z",
            ended_at="2026-11-28T11:00:00.000000Z",
            concurrent_trials=4,
            note="",
        ),
    )


def loose(name: str = "made-up-loose", split: SplitName = "dev") -> RunSetRow:
    row = run_set()
    body = RunSetBody(
        run_set={**row.body.run_set, "run_set": name, "split": split},
        side="",
        totals=row.body.totals,
        started_at=row.body.started_at,
        ended_at=row.body.ended_at,
        concurrent_trials=4,
        note="",
    )
    return RunSetRow(
        kind="run_set",
        row_id=new_id(),
        at=row.at,
        experiment="",
        config_ids=(BASELINE_ID,),
        code_revision="unknown",
        body=body,
    )


def decision(
    experiment: str = "made-up", uses: int = 0, rule_hash: str = RULE_HASH
) -> DecisionRow:
    return DecisionRow(
        kind="decision",
        row_id=new_id(),
        at="2026-11-28T12:00:00.000000Z",
        experiment=experiment,
        config_ids=(BASELINE_ID, CANDIDATE_ID),
        code_revision="unknown",
        body=DecisionBody(
            decision={"outcome": "inconclusive", "rule_hash": rule_hash},
            held_out_uses=uses,
            same_config=False,
        ),
    )


def correction(corrects: str, experiment: str = "made-up") -> CorrectionRow:
    return CorrectionRow(
        kind="correction",
        row_id=new_id(),
        at="2026-11-28T13:00:00.000000Z",
        experiment=experiment,
        config_ids=(),
        code_revision="unknown",
        body=CorrectionBody(corrects=corrects, note="A made-up note."),
    )


def whole_experiment(
    experiment: str = "made-up", split: SplitName = "dev"
) -> list[LedgerRow]:
    """Every row of one experiment run to its end, in order."""
    return [
        plan(experiment, split),
        start(experiment, "baseline", split),
        run_set(experiment, "baseline", split),
        start(experiment, "candidate", split),
        run_set(experiment, "candidate", split),
    ]


def plan_each_round(
    path: str, rounds: int, barrier: Barrier, answers: "Queue[list[bool]]"
) -> None:
    """In a second process: plan the same experiment as the others, each round.

    Puts, for each round, whether this process's plan was let in.
    """
    from lamplighter.adapters import FileLedger

    ledger = FileLedger(Path(path))
    taken: list[bool] = []
    for number in range(rounds):
        barrier.wait()
        answer = asyncio.run(ledger.append(plan(f"round-{number}")))
        taken.append(answer is None)
    answers.put(taken)


def add_loose_rows(
    path: str, count: int, barrier: Barrier, answers: "Queue[int]"
) -> None:
    """In a second process: add `count` loose run sets, at once with the others."""
    from lamplighter.adapters import FileLedger

    ledger = FileLedger(Path(path))
    barrier.wait()
    added = 0
    for number in range(count):
        answer = asyncio.run(ledger.append(loose(f"p{os.getpid()}-{number}")))
        added += answer is None
    answers.put(added)


def append_forever(path: str) -> None:
    """In a second process: add loose run sets until it is killed."""
    from lamplighter.adapters import FileLedger

    ledger = FileLedger(Path(path))
    for number in itertools.count():
        asyncio.run(ledger.append(loose(f"k{os.getpid()}-{number}")))
