"""A job of the bench as Harbor's job file, exactly, and when two are one job."""

import dataclasses
import json
from pathlib import Path

from lamplighter.adapters.harbor_jobs.job_file import (
    HarborPaths,
    job_file,
    without_pace,
)
from lamplighter.ports import HarnessAgentSpec, Job, JsonValue, RunnerAgentSpec

PATHS = HarborPaths(
    start_script=Path("/made-up/repo/bench/harbor/start.sh"),
    bench=Path("/made-up/bench"),
    tasks=Path("/made-up/repo/bench/tasks"),
    compose=Path("/made-up/repo/bench/harbor/no_network.yaml"),
    agent_class="made_up_shim.agent:Agent",
    environment_class="made_up_shim.environment:Environment",
)
HARNESS = HarnessAgentSpec(
    kind="harness",
    settings="/made-up/bench/run_sets/made-up--baseline/settings.toml",
    shell_path="/usr/bin:/bin",
    budget_micro_dollars=30_000_000,
    portable_config_id="d" * 64,
    code_revision="5" * 40,
    run_as=(501, 20),
)
JOB = Job(
    run_set="made-up--baseline",
    tasks=("change-one-port", "count-the-lines"),
    attempts=3,
    concurrent_trials=4,
    network=False,
    agent=HARNESS,
)
POLICY = {
    "TrialInfraError": {
        "should_retry": False,
        "should_grade": False,
        "has_valid_reward": False,
    }
}


def _common(job: Job) -> dict[str, JsonValue]:
    return {
        "job_name": job.run_set,
        "jobs_dir": "/made-up/bench/jobs",
        "n_attempts": job.attempts,
        "n_concurrent_trials": job.concurrent_trials,
        "retry": {"max_retries": 0},
        "override_error_policy": POLICY,
        "datasets": [
            {
                "path": "/made-up/repo/bench/tasks",
                "task_names": ["change-one-port", "count-the-lines"],
            }
        ],
    }


def _as_json(file: object) -> object:
    return json.loads(json.dumps(file))


def test_the_harness_gets_the_shared_folder_the_closed_network_and_six_values() -> None:
    expected = {
        **_common(JOB),
        "environment": {
            "delete": True,
            "import_path": "made_up_shim.environment:Environment",
            "extra_docker_compose": ["/made-up/repo/bench/harbor/no_network.yaml"],
        },
        "agents": [
            {
                "import_path": "made_up_shim.agent:Agent",
                "kwargs": {
                    "settings": HARNESS.settings,
                    "shell_path": "/usr/bin:/bin",
                    "budget_micro_dollars": 30_000_000,
                    "portable_config_id": "d" * 64,
                    "code_revision": "5" * 40,
                    "run_as": "501:20",
                },
            }
        ],
    }
    assert _as_json(job_file(JOB, PATHS)) == expected


def test_a_runner_agent_with_no_model_gets_only_the_closed_network() -> None:
    oracle = dataclasses.replace(
        JOB, agent=RunnerAgentSpec(kind="runner", name="oracle", model=None, options={})
    )
    expected = {
        **_common(oracle),
        "environment": {
            "delete": True,
            "extra_docker_compose": ["/made-up/repo/bench/harbor/no_network.yaml"],
        },
        "agents": [{"name": "oracle", "kwargs": dict[str, JsonValue]()}],
    }
    assert _as_json(job_file(oracle, PATHS)) == expected


def test_a_baseline_with_a_network_gets_its_model_and_options_and_no_compose() -> None:
    agent = RunnerAgentSpec(
        kind="runner", name="made-up-cli", model="made-up-model", options={"turns": 9}
    )
    baseline = dataclasses.replace(JOB, network=True, agent=agent)
    expected = {
        **_common(baseline),
        "environment": {"delete": True},
        "agents": [
            {
                "name": "made-up-cli",
                "model_name": "anthropic/made-up-model",
                "kwargs": {"turns": 9},
            }
        ],
    }
    assert _as_json(job_file(baseline, PATHS)) == expected


def _plain(job: Job) -> JsonValue:
    return json.loads(json.dumps(job_file(job, PATHS)))


def test_two_job_files_that_differ_only_in_pace_and_budget_are_one_job() -> None:
    faster = dataclasses.replace(
        JOB,
        concurrent_trials=1,
        agent=dataclasses.replace(HARNESS, budget_micro_dollars=1),
    )
    assert without_pace(_plain(faster)) == without_pace(_plain(JOB))
    assert _plain(faster) != _plain(JOB)


def test_every_other_change_is_another_job() -> None:
    changes = [
        dataclasses.replace(JOB, attempts=2),
        dataclasses.replace(JOB, tasks=("change-one-port",)),
        dataclasses.replace(JOB, network=True),
        dataclasses.replace(JOB, run_set="made-up--candidate"),
        *(
            dataclasses.replace(
                JOB, agent=dataclasses.replace(HARNESS, **{name: value})
            )
            for name, value in (
                ("settings", "/made-up/other.toml"),
                ("shell_path", "/bin"),
                ("portable_config_id", "e" * 64),
                ("code_revision", "6" * 40),
                ("run_as", (502, 20)),
            )
        ),
    ]
    for other in changes:
        assert without_pace(_plain(other)) != without_pace(_plain(JOB)), other


def test_a_file_of_another_shape_is_no_job() -> None:
    shapes: list[JsonValue] = [None, [], "text", {"agents": "none"}, {"agents": [1]}]
    for broken in shapes:
        assert without_pace(broken) != without_pace(_plain(JOB))
        assert without_pace(broken) == broken
