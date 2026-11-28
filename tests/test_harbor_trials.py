"""Whole trials of the harness in Harbor and Docker, each with a scripted model.

Each runs through the real start script, the shim's two classes and the
real trial function, and is read back through the adapter and the
outcome rule the gate uses.
"""

import asyncio
import dataclasses
import json
import os
from pathlib import Path

import pytest

from lamplighter.adapters.harbor_jobs import HarborJobs
from lamplighter.bench import BoundWord, TrialOutcome, harness_outcome, read_record
from lamplighter.ports import Failure, Job, TrialFacts
from tests.harbor_kit import harbor_paths, harness_job

pytestmark = pytest.mark.container
TASK = "change-one-port"
RUN_SET = "made-up--harness"


def trial(tmp_path: Path, agent: str, task: str = TASK) -> TrialFacts:
    return trial_of(tmp_path, agent, harness_job(tmp_path / "bench", RUN_SET, task))


def trial_of(tmp_path: Path, agent: str, job: Job) -> TrialFacts:
    jobs = HarborJobs(harbor_paths(tmp_path / "bench", f"scripted_agents:{agent}"))
    assert asyncio.run(jobs.run(job)) is None
    found = asyncio.run(jobs.trials(job.run_set))
    assert not isinstance(found, Failure), found
    (one,) = found
    return one


def outcome(facts: TrialFacts) -> TrialOutcome:
    record = None if facts.record is None else read_record(facts.record)
    return harness_outcome(record, facts.check)


def tool_texts(facts: TrialFacts) -> dict[str, str]:
    """Give the text of each tool result of the trial's exported session, by call."""
    events = Path(facts.folder) / "harness" / "export" / "events.jsonl"
    found: dict[str, str] = {}
    for line in events.read_text().splitlines():
        event = json.loads(line)
        if event["type"] == "tool_result":
            found[event["payload"]["call_id"]] = event["payload"]["text"]
    return found


def check_ran(facts: TrialFacts) -> bool:
    return (Path(facts.folder) / "verifier" / "reward.txt").exists()


def test_a_scripted_fix_is_a_whole_trial_that_passes(tmp_path: Path) -> None:
    facts = trial(tmp_path, "FixesThePort")
    assert outcome(facts) == TrialOutcome("pass", None)
    assert facts.check == "passed" and check_ran(facts)
    # The command ran as the command user, in the seeded work folder.
    lines = tool_texts(facts)["s"].splitlines()
    assert lines[0] == "[exit code 0]" and lines[1] == "3"
    assert int(lines[2]) != 0
    assert facts.tokens is not None and facts.tokens.input == 4000
    record = read_record(facts.record or "")
    assert record is not None
    assert facts.harness_version.endswith("+" + record.code_revision)


def test_a_model_that_does_nothing_fails(tmp_path: Path) -> None:
    facts = trial(tmp_path, "DoesNothing")
    assert outcome(facts) == TrialOutcome("fail", None)


def test_a_model_outage_is_not_graded_and_is_infra(tmp_path: Path) -> None:
    facts = trial(tmp_path, "ModelOutage")
    assert outcome(facts) == TrialOutcome("infra", "model_unavailable")
    assert (facts.check, facts.stopped_by) == ("none", "TrialInfraError")
    assert not check_ran(facts)


def test_a_process_left_to_write_the_reward_does_not_pass(tmp_path: Path) -> None:
    facts = trial(tmp_path, "LeavesAWriter")
    assert tool_texts(facts)["w"].endswith("1\n")
    assert outcome(facts) == TrialOutcome("fail", None)


def test_commands_that_attack_their_own_result_get_the_real_one(tmp_path: Path) -> None:
    facts = trial(tmp_path, "AttacksItsResult")
    texts = tool_texts(facts)
    assert texts["f"].startswith("[exit code 3]\n")
    assert texts["f"].endswith("Permission denied\nthe real output\n")
    assert "forged" not in texts["f"]
    assert outcome(facts) == TrialOutcome("pass", None)


@pytest.mark.parametrize(
    ("agent", "word"),
    [("LeavesDeepFolders", "too_deep"), ("LeavesALargeFile", "too_large")],
)
def test_what_is_left_past_a_bound_fails_and_is_neither_copied_nor_checked(
    tmp_path: Path, agent: str, word: BoundWord
) -> None:
    facts = trial(tmp_path, agent)
    record = read_record(facts.record or "")
    assert record is not None and record.left_out_of_bounds == word
    assert outcome(facts) == TrialOutcome("fail", word)
    assert facts.stopped_by == "TrialInfraError" and not check_ran(facts)
    # Harbor copies /app out after the shim's error, so the shim leaves an
    # empty one in its place.
    assert not any((Path(facts.folder) / "artifacts" / "app").iterdir())


def test_a_check_that_never_ends_is_a_fail(tmp_path: Path) -> None:
    facts = trial(tmp_path, "FixesThePort", "check-never-ends")
    assert facts.check == "timed_out"
    assert outcome(facts) == TrialOutcome("fail", "check_timed_out")


def test_the_agents_container_has_no_network(tmp_path: Path) -> None:
    facts = trial(tmp_path, "ListsTheNetwork")
    lines = tool_texts(facts)["n"].splitlines()
    assert lines[1] == "lo"
    assert lines[-1] != "connected=0" and lines[-1].startswith("connected=")


def test_the_file_tools_and_the_commands_change_each_others_files(
    tmp_path: Path,
) -> None:
    # In a trial the tools and the commands are one user, as the bench
    # refuses root. Under root the tests' stand-in user for the commands
    # could not write a file the tools made, so the job names the runner.
    job = harness_job(tmp_path / "bench", RUN_SET, TASK)
    agent = dataclasses.replace(job.agent, run_as=(os.getuid(), os.getgid()))
    facts = trial_of(
        tmp_path, "SharesFilesBothWays", dataclasses.replace(job, agent=agent)
    )
    texts = tool_texts(facts)
    assert texts["c"].splitlines() == [
        "[exit code 0]",
        "made by a command, edited by a tool",
        "made by a tool",
        "changed by a command",
    ]
    assert outcome(facts) == TrialOutcome("pass", None)


def test_the_key_of_harbors_process_is_in_no_container_and_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "made-up-key-marker-0c4f1e"
    monkeypatch.setenv("ANTHROPIC_API_KEY", marker)
    facts = trial(tmp_path, "ShowsItsEnvironment")
    shown = tool_texts(facts)["e"]
    assert "FIRST" in shown and "PATH=" in shown
    assert marker not in shown
    for found in (tmp_path / "bench").rglob("*"):
        if found.is_file() and not found.is_symlink():
            assert marker.encode() not in found.read_bytes(), found
    assert os.environ["ANTHROPIC_API_KEY"] == marker
