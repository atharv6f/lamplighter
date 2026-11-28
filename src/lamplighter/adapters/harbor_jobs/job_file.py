"""A job of the bench as the JSON file that Harbor reads."""

from dataclasses import dataclass
from pathlib import Path

from lamplighter.ports import (
    AgentSpec,
    HarnessAgentSpec,
    Job,
    JsonObject,
    JsonValue,
)

# Harbor runs no trial a second time on its own, so that a run set has
# exactly the attempts it was planned with.
_NO_RETRY: JsonObject = {"max_retries": 0}
# The shim raises this for a trial that did not test the agent. Harbor then
# copies nothing out and runs no check, so a model outage is never graded.
_INFRA: JsonObject = {
    "TrialInfraError": {
        "should_retry": False,
        "should_grade": False,
        "has_valid_reward": False,
    }
}


@dataclass(frozen=True, slots=True)
class HarborPaths:
    """Where the Harbor adapter finds what it is given, all as full paths.

    `bench` holds a folder per run set under `run_sets/` and Harbor's job
    folders under `jobs/`. The two classes are import paths, as Harbor
    takes them, of the shim's agent and environment.
    """

    start_script: Path
    bench: Path
    tasks: Path
    compose: Path
    agent_class: str
    environment_class: str


def job_file(job: Job, paths: HarborPaths) -> JsonObject:
    """Give the job as Harbor's job file.

    A task container with no network gets the compose file that takes it
    away. Only this repo's harness gets the shared-folder environment.
    """
    environment: dict[str, JsonValue] = {"delete": True}
    if isinstance(job.agent, HarnessAgentSpec):
        environment["import_path"] = paths.environment_class
    if not job.network:
        environment["extra_docker_compose"] = [str(paths.compose)]
    return {
        "job_name": job.run_set,
        "jobs_dir": str(paths.bench / "jobs"),
        "n_attempts": job.attempts,
        "n_concurrent_trials": job.concurrent_trials,
        "retry": _NO_RETRY,
        "override_error_policy": _INFRA,
        "environment": environment,
        "agents": [_agent(job.agent, paths)],
        "datasets": [{"path": str(paths.tasks), "task_names": list(job.tasks)}],
    }


def _agent(agent: AgentSpec, paths: HarborPaths) -> JsonObject:
    if isinstance(agent, HarnessAgentSpec):
        user, group = agent.run_as
        return {
            "import_path": paths.agent_class,
            "kwargs": {
                "settings": agent.settings,
                "shell_path": agent.shell_path,
                "budget_micro_dollars": agent.budget_micro_dollars,
                "portable_config_id": agent.portable_config_id,
                "code_revision": agent.code_revision,
                "run_as": f"{user}:{group}",
            },
        }
    if agent.model is None:
        return {"name": agent.name, "kwargs": agent.options}
    return {
        "name": agent.name,
        "model_name": f"anthropic/{agent.model}",
        "kwargs": agent.options,
    }


def without_pace(file: JsonValue) -> JsonValue:
    """Give a job file without the trials at a time and the budget.

    Two job files that are equal this way are one job, run at another pace.
    A file of any other shape is given back as it is, and so equals no job
    file this adapter writes.
    """
    if not isinstance(file, dict):
        return file
    kept = {key: value for key, value in file.items() if key != "n_concurrent_trials"}
    agents = kept.get("agents")
    if isinstance(agents, list):
        kept["agents"] = [_without_budget(agent) for agent in agents]
    return kept


def _without_budget(agent: JsonValue) -> JsonValue:
    kwargs = agent.get("kwargs") if isinstance(agent, dict) else None
    if not isinstance(agent, dict) or not isinstance(kwargs, dict):
        return agent
    kept = {
        key: value for key, value in kwargs.items() if key != "budget_micro_dollars"
    }
    return {**agent, "kwargs": kept}
