"""Harbor's result files read into the facts of each finished trial, and no more."""

import json
from pathlib import Path

import pytest

from lamplighter.adapters.harbor_jobs.job_folder import finished_trials
from lamplighter.ports import Failure, JsonValue, RecordedTokens, TrialFacts
from tests.harbor_results import CHECKSUM, passed, result, trial_folder


def _read(job: Path) -> list[TrialFacts]:
    found = finished_trials(job)
    assert not isinstance(found, Failure), found
    return found


def _one(
    job: Path, written: dict[str, JsonValue], record: str | None = None
) -> TrialFacts:
    trial_folder(job, str(written["trial_name"]), written, record)
    (found,) = _read(job)
    return found


def test_a_passed_trial_gives_every_fact(tmp_path: Path) -> None:
    written = result(
        "change-one-port__AbCdEfG",
        rewards={"reward": 1},
        reward_valid=True,
        tokens=(1200, 200, 50),
        cost=0.0123456,
    )
    found = _one(tmp_path, written, record='{"made": "up"}')
    assert found == TrialFacts(
        task="change-one-port",
        runner_name="change-one-port__AbCdEfG",
        started_at="2026-11-28T10:00:00+00:00",
        ms=17250,
        check="passed",
        stopped_by="",
        task_folder_hash=CHECKSUM,
        harness_version="d" * 16 + "+" + "5" * 40,
        tokens=RecordedTokens(input=1200, cache_read=200, output=50),
        micro_dollars=12346,
        record='{"made": "up"}',
        folder=str(tmp_path / "change-one-port__AbCdEfG"),
    )


@pytest.mark.parametrize(
    ("written", "check", "stopped_by"),
    [
        (result(rewards={"reward": 1.0}, reward_valid=True), "passed", ""),
        (result(rewards={"reward": 0.0}, reward_valid=True), "failed", ""),
        (result(rewards={"reward": 0.5}, reward_valid=True), "failed", ""),
        (result(rewards={"a": 1.0, "b": 1.0}, reward_valid=True), "failed", ""),
        (result(rewards={"reward": True}, reward_valid=True), "failed", ""),
        (result(stopped_by="TrialInfraError"), "none", "TrialInfraError"),
        (
            result(stopped_by="VerifierTimeoutError"),
            "timed_out",
            "VerifierTimeoutError",
        ),
        *(
            (result(stopped_by=name), "unreadable_reward", name)
            for name in (
                "RewardFileNotFoundError",
                "RewardFileEmptyError",
                "VerifierOutputParseError",
            )
        ),
        # Harbor's own word decides first: a reward it does not count as
        # valid is no check that ended, whatever the number.
        (
            result(rewards={"reward": 1.0}, stopped_by="AgentTimeoutError"),
            "none",
            "AgentTimeoutError",
        ),
        (result(), "none", ""),
    ],
)
def test_how_the_check_ended(
    tmp_path: Path, written: dict[str, JsonValue], check: str, stopped_by: str
) -> None:
    found = _one(tmp_path, written)
    assert (found.check, found.stopped_by) == (check, stopped_by)


def test_a_trial_with_no_counts_and_no_cost_gives_none_of_them(tmp_path: Path) -> None:
    found = _one(tmp_path, passed())
    assert (found.tokens, found.micro_dollars, found.record) == (None, None, None)


def test_a_cancelled_trial_and_a_cut_off_one_are_not_given(tmp_path: Path) -> None:
    trial_folder(
        tmp_path, "a__Cancel1", result("a__Cancel1", stopped_by="CancelledError")
    )
    trial_folder(tmp_path, "b__CutOff1", None, record='{"made": "up"}')
    trial_folder(tmp_path, "c__Passed1", passed("c__Passed1"))
    (tmp_path / "result.json").write_text("{}")
    (tmp_path / "config.json").write_text("{}")
    assert [t.runner_name for t in _read(tmp_path)] == ["c__Passed1"]


def test_trials_come_in_the_order_of_their_folders(tmp_path: Path) -> None:
    for name in ("b__Second1", "a__First11", "c__Third11"):
        trial_folder(tmp_path, name, passed(name))
    assert [t.runner_name for t in _read(tmp_path)] == [
        "a__First11",
        "b__Second1",
        "c__Third11",
    ]


def test_a_job_folder_that_is_not_there_holds_no_trial(tmp_path: Path) -> None:
    assert _read(tmp_path / "not-there") == []


def test_a_time_in_another_zone_keeps_its_offset_and_its_length(tmp_path: Path) -> None:
    written = result(
        rewards={"reward": 1.0},
        reward_valid=True,
        started="2026-11-28T12:00:00.000000+02:00",
        finished="2026-11-28T10:00:01Z",
    )
    found = _one(tmp_path, written)
    assert (found.started_at, found.ms) == ("2026-11-28T12:00:00+02:00", 1000)


@pytest.mark.parametrize(
    "broken",
    [
        {**passed(), "trial_name": None},
        {**passed(), "task_checksum": 7},
        {**passed(), "reward_valid": "yes"},
        {**passed(), "agent_info": {"name": "x"}},
        {**passed(), "started_at": "yesterday"},
        {**passed(), "started_at": "2026-11-28T10:00:00"},
        {**passed(), "finished_at": "2026-11-28T09:00:00Z"},
        result(reward_valid=True),
        result(rewards={}, reward_valid=True),
        result(rewards={"reward": 1.0}, reward_valid=True, tokens=(1, None, None)),
        result(rewards={"reward": 1.0}, reward_valid=True, tokens=(5, 10, 1)),
        result(rewards={"reward": 1.0}, reward_valid=True, tokens=(-1, 0, 0)),
        result(rewards={"reward": 1.0}, reward_valid=True, cost="0.1"),
        result(rewards={"reward": 1.0}, reward_valid=True, cost=-0.5),
        {**passed(), "exception_info": {"exception_type": 3}},
        [1, 2],
    ],
)
def test_a_result_of_another_shape_makes_the_whole_read_unavailable(
    tmp_path: Path, broken: JsonValue
) -> None:
    trial_folder(tmp_path, "a__Fine1111", passed("a__Fine1111"))
    folder = tmp_path / "b__Broken11"
    folder.mkdir()
    (folder / "result.json").write_text(json.dumps(broken))
    found = finished_trials(tmp_path)
    assert isinstance(found, Failure)
    assert (found.kind, found.effect) == ("unavailable", "none")
    assert "b__Broken11" in found.message


def test_a_result_that_is_not_json_is_unavailable(tmp_path: Path) -> None:
    folder = tmp_path / "a__Torn11111"
    folder.mkdir()
    (folder / "result.json").write_bytes(b'{"trial_name": "a__T')
    found = finished_trials(tmp_path)
    assert isinstance(found, Failure) and "a__Torn11111" in found.message


def test_a_folder_that_cannot_be_read_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Root reads a file with no permissions, so the refusal is made up.
    trial_folder(tmp_path, "a__Fine1111", passed("a__Fine1111"))

    def refused(path: Path) -> bytes:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "read_bytes", refused)
    found = finished_trials(tmp_path)
    assert isinstance(found, Failure) and found.kind == "unavailable"
    assert "Permission denied" in found.message


def test_reading_changes_no_file(tmp_path: Path) -> None:
    trial_folder(tmp_path, "a__Fine1111", passed("a__Fine1111"), record="{}")
    trial_folder(tmp_path, "b__CutOff11", None)
    before = sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    _read(tmp_path)
    assert sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*")) == before
