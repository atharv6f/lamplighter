"""Made-up job folders in the shape Harbor 0.27.0 writes, for the reader's tests."""

import json
from pathlib import Path

from lamplighter.ports import JsonObject, JsonValue

STARTED = "2026-11-28T10:00:00.000000Z"
FINISHED = "2026-11-28T10:00:17.250500Z"
CHECKSUM = "a" * 64


def result(
    trial: str = "change-one-port__AbCdEfG",
    *,
    rewards: JsonValue = None,
    reward_valid: bool = False,
    stopped_by: str | None = None,
    tokens: tuple[int | None, int | None, int | None] = (None, None, None),
    cost: JsonValue = None,
    started: JsonValue = STARTED,
    finished: JsonValue = FINISHED,
) -> dict[str, JsonValue]:
    """One trial's result file, with the fields the reader takes and some it leaves."""
    given, cached, output = tokens
    return {
        "schema_version": 1,
        "id": "00000000-0000-4000-8000-000000000000",
        "task_name": "lamplighter-bench/" + trial.split("__")[0],
        "trial_name": trial,
        "trial_uri": "file:///made-up/jobs/made-up/" + trial,
        "task_id": {"path": "/made-up/tasks/" + trial.split("__")[0]},
        "source": "tasks",
        "task_checksum": CHECKSUM,
        "agent_info": {"name": "lamplighter", "version": "d" * 16 + "+" + "5" * 40},
        "agent_result": {
            "n_input_tokens": given,
            "n_cache_tokens": cached,
            "n_output_tokens": output,
            "cost_usd": cost,
            "metadata": None,
        },
        "verifier_result": None if rewards is None else {"rewards": rewards},
        "exception_info": None
        if stopped_by is None
        else {
            "exception_type": stopped_by,
            "exception_message": "Made up.",
            "exception_traceback": "Made up.",
            "occurred_at": FINISHED,
        },
        "started_at": started,
        "finished_at": finished,
        "step_results": None,
        "reward_valid": reward_valid,
    }


def passed(trial: str = "change-one-port__AbCdEfG") -> dict[str, JsonValue]:
    """A trial whose check gave a reward of 1."""
    return result(trial, rewards={"reward": 1.0}, reward_valid=True)


def trial_folder(
    job: Path, name: str, written: JsonObject | None, record: str | None = None
) -> Path:
    """Make a trial folder with its result file, and the trial record if given."""
    folder = job / name
    (folder / "agent").mkdir(parents=True)
    if written is not None:
        (folder / "result.json").write_text(json.dumps(written), encoding="utf-8")
    if record is not None:
        (folder / "harness").mkdir()
        (folder / "harness" / "trial_record.json").write_text(record, encoding="utf-8")
    return folder
