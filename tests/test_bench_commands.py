"""`bench tasks`, `check`, `plan`, `ledger` and `show` on the two fakes."""

import asyncio
import json
import tomllib
from pathlib import Path

import pytest

from lamplighter.app.bench_commands import (
    check_tasks,
    correct_row,
    list_tasks,
    plan_experiment,
    show_ledger,
    show_trial,
)
from lamplighter.app.bench_parts import AS_ROOT, NO_CHECKOUT, NOT_CLEAN
from lamplighter.app.bench_run import run_experiment, run_loose
from lamplighter.app.bench_show import MOST_RESULT_CHARS
from lamplighter.app.main import main
from lamplighter.app.task_files import task_files
from lamplighter.bench import HARNESS, GateRule, rule_hash, task_set_hash
from lamplighter.ports import (
    CheckEnd,
    CorrectionRow,
    Job,
    JsonValue,
    PlanRow,
    ReferenceAgentSpec,
    RunnerAgentSpec,
    SplitName,
    row_text,
)
from tests.bench_kit import HARBOR_VERSION, RULE, USER, Bench, git
from tests.ledger_kit import bakeoff_candidate, bakeoff_rule
from tests.test_bench_trial import quick_settings

NAME = "made-up"


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


def _plan(
    bench: Bench,
    name: str = NAME,
    split: SplitName = "held_out",
    baseline: Path | None = None,
    candidate: Path | None = None,
) -> int:
    return asyncio.run(
        plan_experiment(
            bench.parts(),
            name,
            baseline or bench.baseline,
            candidate or bench.candidate,
            split,
            "A made-up note.",
        )
    )


def _commit(bench: Bench, path: str, text: str) -> None:
    (bench.root / path).write_text(text)
    git(bench.root, "add", "-A")
    git(bench.root, "commit", "-qm", "Made-up change")


# `bench tasks`.


def test_tasks_lists_each_task_with_its_group_and_side_and_the_hash(
    bench: Bench,
) -> None:
    assert list_tasks(bench.parts()) == 0
    lines = bench.out.getvalue().splitlines()
    side = "dev" if "code-one" in _dev(bench) else "held_out"
    assert lines[0] == f"code-one  code  {side}"
    assert len(lines) == 13
    files = task_files(bench.root)
    assert isinstance(files, list)
    assert lines[-1] == f"task set hash: {task_set_hash(files)}"
    assert bench.said() == ""


def _dev(bench: Bench) -> list[str]:
    split = tomllib.loads((bench.root / "bench" / "split.toml").read_text())
    return split["dev"]


def test_a_split_that_is_not_the_functions_is_a_fault(bench: Bench) -> None:
    path = bench.root / "bench" / "split.toml"
    split = tomllib.loads(path.read_text())
    dev, held_out = split["dev"], split["held_out"]
    dev[0], held_out[0] = held_out[0], dev[0]
    path.write_text(
        f'seed = "{split["seed"]}"\ndev = {json.dumps(dev)}\n'
        f"held_out = {json.dumps(held_out)}\n"
    )
    assert list_tasks(bench.parts()) == 1
    assert "bench/split.toml is not what the split function gives" in bench.said()


def test_a_task_that_breaks_a_rule_is_a_fault_named_by_its_task(bench: Bench) -> None:
    (bench.root / "bench" / "tasks" / "code-one" / "instruction.md").write_text(
        "Change /app/settings.ini.\n"
    )
    assert list_tasks(bench.parts()) == 1
    assert bench.said().startswith("code-one: instruction.md names /app")


def test_a_task_file_that_git_ignores_is_a_fault(bench: Bench) -> None:
    (bench.root / ".gitignore").write_text("*.db\n")
    (bench.root / "bench" / "tasks" / "code-one" / "tests" / "data.db").write_text("")
    assert list_tasks(bench.parts()) == 1
    assert (
        "bench/tasks/code-one/tests/data.db is left out by .gitignore" in bench.said()
    )


# `bench plan`.


def test_a_plan_is_one_row_with_the_rule_tasks_settings_and_code(
    bench: Bench,
) -> None:
    assert _plan(bench) == 0
    (row,) = bench.rows()
    assert isinstance(row, PlanRow)
    body = row.body
    assert (row.experiment, row.code_revision) == (NAME, bench.revision())
    assert body.split == "held_out" and len(body.tasks) == 2
    assert not set(body.tasks) & set(_dev(bench))
    assert (body.trials_per_task, body.harbor_version, body.note) == (
        3,
        HARBOR_VERSION,
        "A made-up note.",
    )
    assert body.rule["alpha_permille"] == 50 and len(body.rule_hash) == 64
    assert body.baseline.path == str(bench.baseline.resolve())
    assert len(set(row.config_ids)) == 2
    files = task_files(bench.root)
    assert isinstance(files, list) and body.task_set_hash == task_set_hash(files)


def test_a_plan_on_the_dev_set_takes_the_dev_tasks(bench: Bench) -> None:
    assert _plan(bench, split="dev") == 0
    plan = bench.rows()[0]
    assert isinstance(plan, PlanRow)
    assert list(plan.body.tasks) == sorted(_dev(bench))


def test_a_plan_of_the_same_settings_twice_is_an_a_a_experiment(bench: Bench) -> None:
    assert _plan(bench, candidate=bench.baseline) == 0
    ids = bench.rows()[0].config_ids
    assert ids[0] == ids[1]
    assert "A/A experiment" in bench.out.getvalue()


def test_a_second_plan_of_one_name_is_refused(bench: Bench) -> None:
    assert _plan(bench) == 0
    assert _plan(bench) == 1
    assert bench.said() == f"The name {NAME} is taken.\n"
    assert len(bench.rows()) == 1


def test_a_plan_named_as_a_loose_run_set_is_refused(bench: Bench) -> None:
    parts = bench.parts()
    loose = run_loose(parts, NAME, HARNESS, (), bench.baseline, "")
    assert asyncio.run(loose) == 0
    assert _plan(bench) == 1
    assert bench.said() == f"The name {NAME} is taken.\n"


def test_a_plan_on_no_checkout_or_a_changed_one_is_refused(bench: Bench) -> None:
    bench.change()
    assert _plan(bench) == 1
    bench.root = bench.root / "src"
    assert _plan(bench) == 1
    assert bench.said() == f"{NOT_CLEAN}\n{NO_CHECKOUT}\n"
    assert bench.rows() == []


def test_settings_that_do_not_load_are_refused_with_their_path(
    bench: Bench, tmp_path: Path
) -> None:
    missing = tmp_path / "missing.toml"
    assert _plan(bench, baseline=missing) == 1
    assert bench.said().startswith(f"{missing}: ")
    broken = tmp_path / "broken.toml"
    broken.write_text(quick_settings(max_steps="0"))
    assert _plan(bench, candidate=broken) == 1
    assert f"{broken}: run.max_steps must be at least 1" in bench.said()
    assert bench.rows() == []


def test_a_rule_that_does_not_pass_is_refused_with_its_line(bench: Bench) -> None:
    _commit(bench, "bench/gate_rule.toml", RULE.replace("= 50\n", "= 500\n", 1))
    assert _plan(bench) == 1
    assert bench.said() == "The gate rule's alpha_permille must be 1 to 499.\n"


def test_a_broken_task_set_is_refused_with_its_first_fault(bench: Bench) -> None:
    path = "bench/tasks/code-one/environment/.dockerignore"
    _commit(bench, path, "*\n")
    assert _plan(bench) == 1
    assert bench.said() == (
        "code-one: environment/.dockerignore is not the one line .DS_Store.\n"
    )


# `bench ledger`.


def test_the_ledger_prints_one_line_per_row(bench: Bench) -> None:
    _plan(bench)
    asyncio.run(run_experiment(bench.parts(), NAME))
    bench.out.truncate(0)
    bench.out.seek(0)
    assert asyncio.run(show_ledger(bench.parts(), None)) == 0
    lines = bench.out.getvalue().splitlines()
    assert [line.split("  ")[2] for line in lines] == [
        "plan",
        "start",
        "run_set",
        "start",
        "run_set",
        "decision",
    ]
    assert lines[2].endswith("6 passed, 0 failed, 0 infra, of 6 trials")


def test_a_bake_off_row_is_printed_with_its_stage_and_candidate(bench: Bench) -> None:
    for row in (bakeoff_rule(), bakeoff_candidate()):
        assert asyncio.run(bench.ledger.append(row)) is None
    assert asyncio.run(show_ledger(bench.parts(), None)) == 0
    lines = bench.out.getvalue().splitlines()
    assert [line.split("  ", 4)[4] for line in lines] == [
        "bake-off rule",
        "bake-off candidate llama.cpp made-up-27b",
    ]


def test_one_experiments_rows_are_printed_in_full_with_the_noise_floor(
    bench: Bench,
) -> None:
    _plan(bench, candidate=bench.baseline)
    asyncio.run(run_experiment(bench.parts(), NAME))
    bench.out.truncate(0)
    bench.out.seek(0)
    assert asyncio.run(show_ledger(bench.parts(), NAME)) == 0
    lines = bench.out.getvalue().splitlines()
    assert lines[:6] == [row_text(row) for row in bench.rows()]
    assert lines[6].startswith("Noise floor, from the A/A experiment made-up:")
    assert asyncio.run(show_ledger(bench.parts(), "no-such")) == 1


def test_a_correction_is_a_row_about_another(bench: Bench) -> None:
    _plan(bench)
    planned = bench.rows()[0]
    assert asyncio.run(correct_row(bench.parts(), planned.row_id, "Made up.")) == 0
    correction = bench.rows()[1]
    assert isinstance(correction, CorrectionRow)
    assert (correction.experiment, correction.body.corrects) == (NAME, planned.row_id)
    assert asyncio.run(correct_row(bench.parts(), "no-such-row", "Made up.")) == 1
    assert bench.said() == "The ledger has no row no-such-row.\n"
    assert len(bench.rows()) == 2


# `bench check`.


def test_check_runs_the_reference_three_times_and_nothing_once_on_every_task(
    bench: Bench,
) -> None:
    assert asyncio.run(check_tasks(bench.parts())) == 0
    reference, nop = bench.check_jobs.jobs
    # The reference solution runs as this process's user, as commands do.
    assert reference.agent == ReferenceAgentSpec("reference", USER)
    assert nop.agent == RunnerAgentSpec("runner", "nop", None, {})
    assert (reference.attempts, nop.attempts) == (3, 1)
    assert len(reference.tasks) == 12 and not reference.network and not nop.network
    assert reference.run_set.endswith("-reference") and nop.run_set.endswith("-nop")
    assert bench.rows() == [] and bench.jobs.jobs == []
    assert bench.out.getvalue() == "12 of 12 tasks kept the rule.\n"


def test_check_names_each_task_whose_oracle_fails_or_whose_nothing_passes(
    bench: Bench,
) -> None:
    def checked(job: Job, task: str, attempt: int) -> CheckEnd:
        oracle = isinstance(job.agent, ReferenceAgentSpec)
        if task == "code-one" and oracle and attempt == 1:
            return "failed"
        if task == "data-two" and not oracle:
            return "passed"
        return "passed" if oracle else "failed"

    bench.checked = checked
    assert asyncio.run(check_tasks(bench.parts())) == 1
    assert bench.said().splitlines() == [
        "code-one: the reference solution passed 2 of 3 trials.",
        "data-two: doing nothing should fail once, and the check ended: passed.",
    ]
    assert bench.out.getvalue() == "10 of 12 tasks kept the rule.\n"


def test_check_as_root_is_refused_and_runs_nothing(bench: Bench) -> None:
    bench.user = (0, 0)
    assert asyncio.run(check_tasks(bench.parts())) == 1
    assert bench.said() == AS_ROOT + "\n"
    assert bench.check_jobs.jobs == []


def test_two_checks_never_share_a_job_folder(bench: Bench) -> None:
    asyncio.run(check_tasks(bench.parts()))
    asyncio.run(check_tasks(bench.parts()))
    names = [job.run_set for job in bench.check_jobs.jobs]
    assert len(set(names)) == 4


# `bench show`.


def _exported(folder: Path, events: list[JsonValue]) -> None:
    export = folder / "harness" / "export"
    export.mkdir(parents=True)
    lines = [json.dumps(event) for event in events]
    (export / "events.jsonl").write_text("\n".join(lines) + "\n")


def test_show_prints_a_trials_transcript_and_outcome(bench: Bench) -> None:
    long = "x" * (MOST_RESULT_CHARS + 10)
    reply: JsonValue = {
        "role": "assistant",
        "parts": [
            {"kind": "text", "text": "Looking."},
            {"kind": "tool_call", "id": "c1", "name": "shell", "arguments": {"a": 1}},
        ],
    }
    result: JsonValue = {"call_id": "c1", "text": long, "is_error": False}
    events: list[JsonValue] = [
        {"type": "run_started", "payload": {}},
        {"type": "user_message", "payload": {"text": "Fix the made-up file."}},
        {"type": "model_response", "payload": {"message": reply}},
        {"type": "tool_result", "payload": result},
        {"type": "run_end", "payload": {"reason": "done", "detail": ""}},
    ]
    _plan(bench, split="dev")
    asyncio.run(run_experiment(bench.parts(), NAME))
    run_set = f"{NAME}--baseline"
    task = _dev(bench)[0]
    # The second attempt is the second to start.
    _exported(bench.trial_folders / run_set / f"{task}__1", events)
    bench.out.truncate(0)
    bench.out.seek(0)
    assert asyncio.run(show_trial(bench.parts(), run_set, task, 2)) == 0
    assert bench.out.getvalue().splitlines() == [
        "Instruction: Fix the made-up file.",
        "Reply: Looking.",
        'Tool call c1: shell {"a":1}',
        f"Result of c1: {'x' * MOST_RESULT_CHARS}",
        f"(cut: {MOST_RESULT_CHARS + 10} characters in all)",
        "The run ended: done.",
        "Outcome: pass.",
    ]


def test_show_of_a_trial_with_no_export_says_where_the_agents_log_is(
    bench: Bench,
) -> None:
    bench.record = lambda job, task, n: None
    parts = bench.parts()
    asyncio.run(run_loose(parts, "made-up-loose", "terminus-2", (), bench.baseline, ""))
    trials = asyncio.run(bench.jobs.trials("made-up-loose"))
    assert isinstance(trials, tuple)
    task = trials[0].task
    bench.out.truncate(0)
    bench.out.seek(0)
    assert asyncio.run(show_trial(bench.parts(), "made-up-loose", task, 1)) == 0
    folder = trials[0].folder
    assert bench.out.getvalue().splitlines() == [
        f"{folder} has no export of this harness. Harbor keeps an agent's own log"
        f" in {folder}/agent.",
        "No trial record. The check ended: passed.",
    ]


def test_show_of_a_trial_that_is_not_there_is_refused(bench: Bench) -> None:
    assert asyncio.run(show_trial(bench.parts(), "no-such", "code-one", 1)) == 1
    assert bench.said() == "no-such has no trial 1 of code-one.\n"


# Every part.


def test_no_bench_command_opens_the_services_store(
    bench: Bench, tmp_path: Path
) -> None:
    store = tmp_path / "service-store"
    for settings in (bench.baseline, bench.candidate):
        settings.write_text(
            settings.read_text().replace('dir = "~/.lamplighter"', f'dir = "{store}"')
        )
    assert f'dir = "{store}"' in bench.baseline.read_text()
    parts = bench.parts()
    assert list_tasks(parts) == 0
    assert asyncio.run(check_tasks(parts)) == 0
    assert _plan(bench) == 0
    assert asyncio.run(run_experiment(parts, NAME)) == 0
    assert (
        asyncio.run(run_loose(parts, "made-up-loose", HARNESS, (), bench.baseline, ""))
        == 0
    )
    assert asyncio.run(show_ledger(parts, NAME)) == 0
    task = _dev(bench)[0]
    assert asyncio.run(show_trial(parts, "made-up-loose", task, 1)) == 0
    assert not store.exists()


def test_bench_with_no_part_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(["bench"])
    assert stopped.value.code == 2
    assert "required: action" in capsys.readouterr().err


def test_the_rule_hash_in_a_plan_is_the_checked_in_rules(bench: Bench) -> None:
    _plan(bench)
    plan = bench.rows()[0]
    rule = bench.parts().rule()
    assert isinstance(rule, GateRule) and isinstance(plan, PlanRow)
    assert plan.body.rule_hash == rule_hash(rule)


def test_the_bench_command_loads_no_settings_file_of_the_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    missing = tmp_path / "missing.toml"
    assert main(["--config", str(missing), "bench", "ledger"]) == 0
    assert capsys.readouterr() == ("", "")
    assert (tmp_path / "lamplighter-bench").is_dir()
