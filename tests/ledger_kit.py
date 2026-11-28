"""Made-up rows of the book of experiments, and the work of a second process on it."""

import asyncio
import dataclasses
import itertools
import os
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Barrier
from pathlib import Path
from typing import Any

from lamplighter.ports import (
    BakeoffBody,
    BakeoffRow,
    BakeoffStage,
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
    Verdict,
)

RULE_HASH = "r" * 64
TASK_SET = "t" * 64
BASELINE_ID = "b" * 64
CANDIDATE_ID = "c" * 64
BAKEOFF_RULE = "1" * 64
CAPTURE_SET = "e" * 64
WEIGHTS = "w" * 64
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


def _bakeoff(stage: BakeoffStage, body: dict[str, Any]) -> BakeoffRow:
    """A bake-off row of the stage, with the fields of `body` set."""
    empty = BakeoffBody(
        stage=stage,
        rule={},
        rule_hash=BAKEOFF_RULE,
        replaces="",
        why="",
        capture_set_hash=CAPTURE_SET,
        capture_counts={},
        server="",
        server_version="",
        model="",
        weights_sha256="",
        weights_source="",
        quant="",
        hard=(),
        criteria={},
        chosen=False,
        note="",
    )
    return BakeoffRow(
        kind="bakeoff",
        row_id=new_id(),
        at="2026-11-28T14:00:00.000000Z",
        experiment="",
        config_ids=(),
        code_revision="unknown",
        body=dataclasses.replace(empty, **body),
    )


def bakeoff_rule(
    rule_hash: str = BAKEOFF_RULE,
    replaces: str = "",
    why: str = "",
    capture: str = CAPTURE_SET,
) -> BakeoffRow:
    return _bakeoff(
        "rule",
        {
            "rule": {"tie_points": 3},
            "rule_hash": rule_hash,
            "replaces": replaces,
            "why": why,
            "capture_set_hash": capture,
            "capture_counts": {"chat": 12, "trials": 188, "passed_over": 4},
        },
    )


def _candidate_fields(server: str, model: str) -> dict[str, Any]:
    return {
        "server": server,
        "server_version": "made-up-1",
        "model": model,
        "weights_sha256": WEIGHTS,
        "weights_source": "made-up/weights",
        "quant": "Q4_K_M",
    }


def bakeoff_candidate(
    server: str = "llama.cpp",
    model: str = "made-up-27b",
    rule_hash: str = BAKEOFF_RULE,
    capture: str = CAPTURE_SET,
) -> BakeoffRow:
    return _bakeoff(
        "candidate",
        {
            **_candidate_fields(server, model),
            "rule_hash": rule_hash,
            "capture_set_hash": capture,
            "hard": (Verdict("pinned", True, "One version, every hash."),),
            "criteria": {"usable_replies": {"usable": 190, "total": 200}},
        },
    )


def bakeoff_soak(
    passed: bool = True,
    server: str = "llama.cpp",
    model: str = "made-up-27b",
    rule_hash: str = BAKEOFF_RULE,
    capture: str = CAPTURE_SET,
) -> BakeoffRow:
    return _bakeoff(
        "soak",
        {
            **_candidate_fields(server, model),
            "rule_hash": rule_hash,
            "capture_set_hash": capture,
            "criteria": {"stays_up": {"passed": passed, "requests": 1440}},
        },
    )


def bakeoff_choice(
    server: str = "llama.cpp", model: str = "made-up-27b", rule_hash: str = BAKEOFF_RULE
) -> BakeoffRow:
    """A choice of the winner, or of none when the server is empty."""
    fields = _candidate_fields(server, model) if server else {}
    return _bakeoff(
        "choice",
        {**fields, "rule_hash": rule_hash, "chosen": bool(server), "why": "Step 5."},
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
