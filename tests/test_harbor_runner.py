"""The Harbor adapter's own steps, on stand-ins for Docker and the start script."""

import asyncio
import dataclasses
import json
import time
from pathlib import Path

import pytest

from lamplighter.adapters.harbor_jobs import HarborJobs, HarborPaths
from lamplighter.ports import Failure, Job, RunnerAgentSpec, canonical_json
from tests.harbor_results import passed, trial_folder
from tests.harbor_stand_ins import StandIns, stand_ins

JOB = Job(
    run_set="made-up--oracle",
    tasks=("change-one-port",),
    attempts=2,
    concurrent_trials=2,
    network=False,
    agent=RunnerAgentSpec(kind="runner", name="oracle", model=None, options={}),
)


@dataclasses.dataclass
class Bench:
    jobs: HarborJobs
    stand: StandIns
    folder: Path

    @property
    def own(self) -> Path:
        return self.folder / "run_sets" / JOB.run_set

    @property
    def job_folder(self) -> Path:
        return self.folder / "jobs" / JOB.run_set

    def run(self, job: Job = JOB) -> Failure | None:
        return asyncio.run(self.jobs.run(job))

    def started(self) -> list[str]:
        return [call for call in self.stand.calls() if call.startswith("start ")]


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bench:
    stand = stand_ins(tmp_path / "stand-ins", monkeypatch)
    folder = tmp_path / "bench"
    paths = HarborPaths(
        start_script=stand.start_script,
        bench=folder,
        tasks=tmp_path / "tasks",
        compose=tmp_path / "no_network.yaml",
        agent_class="made_up.agent:Agent",
        reference_class="made_up.reference:Reference",
        environment_class="made_up.environment:Environment",
    )
    return Bench(HarborJobs(paths), stand, folder)


def _kind(answer: Failure | None) -> str | None:
    return None if answer is None else answer.kind


def test_a_new_job_writes_its_file_and_starts_harbor_on_it(bench: Bench) -> None:
    assert bench.run() is None
    job_file = bench.own / "job.json"
    assert bench.started() == [f"start run -c {job_file}"]
    written = json.loads(job_file.read_text())
    assert written["job_name"] == JOB.run_set
    assert job_file.read_text() == canonical_json(written) + "\n"
    assert (bench.own / "harbor.log").read_text() == "made-up output of run\n"


def test_a_job_folder_with_harbors_copy_is_carried_on_naming_the_cancelled(
    bench: Bench,
) -> None:
    assert bench.run() is None
    bench.job_folder.mkdir(parents=True)
    (bench.job_folder / "config.json").write_text("{}")
    assert bench.run() is None
    assert bench.started()[1] == (
        f"start job resume -p {bench.job_folder} -f CancelledError"
    )
    log = (bench.own / "harbor.log").read_text()
    assert log == "made-up output of run\nmade-up output of job\n"


def test_a_job_folder_without_harbors_copy_is_started_as_new(bench: Bench) -> None:
    bench.job_folder.mkdir(parents=True)
    assert bench.run() is None
    assert bench.started() == [f"start run -c {bench.own / 'job.json'}"]


def test_an_exit_code_that_is_not_0_is_unavailable_with_the_code(bench: Bench) -> None:
    bench.stand.set("exit_code", "130")
    answer = bench.run()
    assert isinstance(answer, Failure)
    assert (answer.kind, answer.effect) == ("unavailable", "none")
    log = bench.own / "harbor.log"
    assert answer.message == f"Harbor ended with exit code 130. See {log}."
    bench.stand.clear("exit_code")
    assert bench.run() is None


def test_docker_that_does_not_answer_is_unavailable_and_starts_nothing(
    bench: Bench,
) -> None:
    bench.stand.set("down")
    answer = bench.run()
    assert isinstance(answer, Failure) and answer.kind == "unavailable"
    assert answer.message.startswith("Docker is not running. ")
    assert bench.started() == []


def test_another_job_on_the_run_set_is_a_conflict_and_changes_nothing(
    bench: Bench,
) -> None:
    assert bench.run() is None
    first = (bench.own / "job.json").read_bytes()
    for other in (
        dataclasses.replace(JOB, attempts=3),
        dataclasses.replace(JOB, tasks=("count-the-lines",)),
        dataclasses.replace(JOB, network=True),
        dataclasses.replace(
            JOB,
            agent=RunnerAgentSpec(kind="runner", name="nop", model=None, options={}),
        ),
    ):
        answer = bench.run(other)
        assert isinstance(answer, Failure), other
        assert (answer.kind, answer.effect) == ("conflict", "none")
    assert len(bench.started()) == 1
    assert (bench.own / "job.json").read_bytes() == first


def test_another_pace_is_the_same_job_and_keeps_the_first(bench: Bench) -> None:
    assert bench.run() is None
    first = (bench.own / "job.json").read_bytes()
    assert bench.run(dataclasses.replace(JOB, concurrent_trials=1)) is None
    assert len(bench.started()) == 2
    assert (bench.own / "job.json").read_bytes() == first


def test_a_first_job_file_that_is_not_json_is_a_conflict(bench: Bench) -> None:
    bench.own.mkdir(parents=True)
    (bench.own / "job.json").write_text('{"job_na')
    assert _kind(bench.run()) == "conflict"
    assert bench.started() == []


def test_a_run_while_harbor_still_holds_the_lock_is_a_conflict(bench: Bench) -> None:
    # The stand-in leaves a process behind that holds the lock it was
    # given, as Harbor does when only the parent is killed.
    bench.stand.set("hold", "2")
    assert bench.run() is None
    bench.stand.clear("hold")
    answer = bench.run()
    assert isinstance(answer, Failure)
    assert (answer.kind, answer.message) == (
        "conflict",
        "This run set is still running.",
    )
    deadline = time.monotonic() + 10
    while bench.run() is not None:
        assert time.monotonic() < deadline
        time.sleep(0.1)


def test_two_runs_at_once_let_one_in(bench: Bench) -> None:
    bench.stand.set("hold", "1")

    async def both() -> list[Failure | None]:
        return list(await asyncio.gather(bench.jobs.run(JOB), bench.jobs.run(JOB)))

    kinds = sorted(str(_kind(answer)) for answer in asyncio.run(both()))
    assert kinds in (["None", "conflict"], ["conflict", "None"])


def test_what_cut_off_trials_left_is_removed_first_and_nothing_else(
    bench: Bench,
) -> None:
    trial_folder(bench.job_folder, "Change-One-Port__AbCdEfG", None)
    trial_folder(bench.job_folder, "change-one-port__Done111", passed())
    bench.stand.set(
        "containers",
        "c1 change-one-port__abcdefg__env\n"
        "c2 change-one-port__abcdefg__verifier__test\n"
        "c3 change-one-port__othr111__env\n"
        "c4 change-one-port__abcdefgx__env\n"
        "c5 change-one-port__done111__env\n"
        "c6 \n"
        "c7 change-one-port__abcdefg__envx\n",
    )
    bench.stand.set(
        "networks",
        "n1 change-one-port__abcdefg__verifier__test\n"
        "n2 change-one-port__othr111__env\n",
    )
    assert bench.run() is None
    calls = bench.stand.calls()
    removed = [call for call in calls if " rm " in call]
    assert removed == [
        "docker container rm --force c1 c2",
        "docker network rm n1",
    ]
    assert calls[-1].startswith("start ")


def test_a_removal_that_docker_refuses_is_unavailable_and_starts_nothing(
    bench: Bench,
) -> None:
    trial_folder(bench.job_folder, "change-one-port__AbCdEfG", None)
    bench.stand.set("containers", "c1 change-one-port__abcdefg__env\n")
    bench.stand.set("refuse")
    assert _kind(bench.run()) == "unavailable"
    assert bench.started() == []


def test_a_bench_folder_that_cannot_be_made_is_unavailable(bench: Bench) -> None:
    bench.folder.write_text("a file where the folder should be")
    assert _kind(bench.run()) == "unavailable"


def test_trials_reads_harbors_job_folder(bench: Bench) -> None:
    trial_folder(bench.job_folder, "change-one-port__AbCdEfG", passed())
    found = asyncio.run(bench.jobs.trials(JOB.run_set))
    assert not isinstance(found, Failure)
    assert [t.check for t in found] == ["passed"]
