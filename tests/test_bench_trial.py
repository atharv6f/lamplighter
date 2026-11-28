"""One trial on fakes: a scripted model, a container played by a function, a store."""

import asyncio
import dataclasses
import io
import shlex
import socket
from collections.abc import Callable
from pathlib import Path

import pytest

from lamplighter.adapters import CountingIds, ScriptedBreak, ScriptedReply, SystemClock
from lamplighter.adapters.builtin_tools import BuiltinTools, ToolSettings, closed_web
from lamplighter.adapters.container import AsRoot, ContainerExecutor
from lamplighter.adapters.in_memory import (
    InMemoryBlobStore,
    InMemoryTraceCursor,
    InMemoryTraceSink,
)
from lamplighter.adapters.sqlite import SqliteEventLog, open_database
from lamplighter.app.bench_trial import (
    RECORD,
    SESSION,
    TrialParts,
    TrialRequest,
    TrialStore,
    run_trial,
    trial_config,
)
from lamplighter.app.chat import Tooling, chat, run_spec
from lamplighter.app.config import Config, load_config
from lamplighter.app.main import builtin_tooling, chat_parts, trial_parts, trial_store
from lamplighter.app.model_set import ModelSet
from lamplighter.app.work_bounds import MOST_DEPTH, MOST_ENTRIES, MOST_STATED_BYTES
from lamplighter.bench import Tokens, TrialRecord, read_record, record_text
from lamplighter.core import run_summary
from lamplighter.ports import (
    BlobStore,
    Event,
    Executor,
    Failure,
    TextPart,
    ToolCallPart,
    ToolProvider,
    ToolResultPart,
    Usage,
    canonical_json,
)
from tests.model_by_request import ModelByRequest, assistant_turns, model_set
from tests.terminal_kit import Screen, typed
from tests.tooling_kit import with_tool_folders

SETTINGS = Path(__file__).parents[1] / "lamplighter.toml"
REVISION = "5" * 40
USAGE = Usage(input_uncached=100, cache_read=10, cache_write=5, output=20)
OUTAGE = Failure(kind="unavailable", effect="none", message="Made up.", source="m")
WRONG = Failure(kind="invalid_input", effect="none", message="Made up.", source="m")
BUDGET = 30_000_000
DONE = ScriptedReply((TextPart(text="Done."),), "end", USAGE)
SHELL = ScriptedReply(
    (ToolCallPart(id="c1", name="shell", arguments={"command": "true"}),),
    "tool_calls",
    USAGE,
)
FETCH = ScriptedReply(
    (
        ToolCallPart(
            id="c1", name="web_fetch", arguments={"url": "https://made-up.invalid/"}
        ),
    ),
    "tool_calls",
    USAGE,
)
type Turn = ScriptedReply | ScriptedBreak
SPENT = TrialRecord(
    portable_config_id="p" * 64,
    config_id="c" * 64,
    code_revision=REVISION,
    model="made-up-1",
    provider="made-up",
    session_id=SESSION,
    run_id="run-1",
    run_reason="done",
    run_detail=None,
    gradable=True,
    infra_reason=None,
    left_out_of_bounds=None,
    steps=1,
    tool_calls=0,
    tokens=Tokens(1, 0, 0, 1),
    micro_dollars=0,
    ms=1,
    exported_events=1,
)


class FakeContainer:
    """Plays the container's side of every call, and notes each in `happened`.

    A command exits 0 with no output, or gets no answer when `lost` is set.
    """

    def __init__(self, happened: list[str], record: Path) -> None:
        self.happened = happened
        self.record = record
        self.listing: tuple[int, str] | None = (0, "40 1 7 40")
        self.passes = "swept 0"
        self.lost = False
        self.at_the_sweep: Callable[[], None] = lambda: None

    async def __call__(
        self, line: str, cwd: str, seconds: int
    ) -> tuple[int, str] | None:
        if line.startswith("printf"):
            self.happened.append("mark")
            return self.listing
        if "kill -KILL -1" in line or line.startswith("keep="):
            assert not self.record.exists()
            self.happened.append("sweep")
            self.at_the_sweep()
            return (0, self.passes)
        self.happened.append("command")
        if self.lost:
            return None
        token = shlex.split(line)[7]
        return (0, f"{token} exited 0 0\n\n")


def playing(*turns: Turn) -> ModelByRequest:
    """A model that plays the turns in order, one per reply it has given."""
    return ModelByRequest(lambda request: turns[assistant_turns(request)])


class Trial:
    """One trial's folders, its fakes, and what happened, in order."""

    def __init__(self, tmp_path: Path, settings_text: str | None = None) -> None:
        self.happened: list[str] = []
        self.job = tmp_path / "job"
        self.folder = self.job / "trial-a"
        self.container = FakeContainer(self.happened, self.folder / "harness" / RECORD)
        self.settings = tmp_path / "run_set" / "settings.toml"
        self.settings.parent.mkdir()
        self.settings.write_text(settings_text or quick_settings())
        for made in ("work", "scratch/home", "scratch/tmp"):
            (self.folder / "box" / made).mkdir(parents=True)
        self.export_fails = False
        self.closed: list[str] = []

    @property
    def work(self) -> Path:
        return self.folder / "box" / "work"

    def request(self, **changes: object) -> TrialRequest:
        made = TrialRequest(
            instruction="Fix the made-up file.",
            as_root=self.container,
            user=(1001, 1001),
            trial_folder=self.folder,
            settings=self.settings,
            shell_path="/usr/local/bin:/usr/bin:/bin",
            budget_micro_dollars=BUDGET,
            planned_portable_id="",
            planned_revision=REVISION,
        )
        made = dataclasses.replace(made, **changes)  # type: ignore[arg-type]
        if not made.planned_portable_id:
            made = dataclasses.replace(made, planned_portable_id=self.portable(made))
        return made

    def portable(self, request: TrialRequest) -> str:
        config = trial_config(request)
        parts = self.parts(playing())
        executor = parts.executor(request.as_root, request.user, self.work)
        tools = parts.tools(config.tools, InMemoryBlobStore(1 << 20), executor)
        spec = run_spec(config, Tooling(tools, executor), REVISION, private=False)
        return spec.portable_config_id

    def parts(self, model: ModelByRequest) -> TrialParts:
        clock = SystemClock()

        def open_store(folder: Path, config: Config) -> TrialStore | Failure:
            made = trial_store(folder, config, clock)
            if isinstance(made, Failure):
                return made
            return TrialStore(made.stores, self._noted(made))

        def executor(
            as_root: AsRoot, user: tuple[int, int], work: Path
        ) -> ContainerExecutor:
            return ContainerExecutor(as_root, user[0], user[1], work, CountingIds())

        def tools(
            settings: ToolSettings, blobs: BlobStore, wall: Executor
        ) -> ToolProvider:
            return BuiltinTools(settings, blobs, wall, clock, closed_web())

        async def close() -> None:
            self.closed.append("model")

        return TrialParts(
            clock=clock,
            ids=CountingIds(),
            code_revision=REVISION,
            open_store=open_store,
            executor=executor,
            tools=tools,
            models=lambda config: ModelSet(
                {entry.name: model for entry in config.models}, {}, close
            ),
        )

    def _noted(self, made: TrialStore):  # noqa: ANN202 - the export's own type
        async def export(session_id: str, into: Path) -> int | Failure:
            self.happened.append("export")
            assert not (self.folder / "harness" / RECORD).exists()
            if self.export_fails:
                return Failure("fatal", "none", "Made up.", "export")
            return await made.export(session_id, into)

        return export

    def run(self, model: ModelByRequest, **changes: object) -> TrialRecord:
        return asyncio.run(run_trial(self.parts(model), self.request(**changes)))

    def saved(self) -> TrialRecord | None:
        return read_record((self.folder / "harness" / RECORD).read_text())


def quick_settings(**values: str) -> str:
    """The repo's settings, fit to the scripted model's profile.

    No model retries, so that an outage ends at once.
    """
    text = SETTINGS.read_text()
    fit = {"model_retries": "0", "cache_hints": "0", "model_max_output": "1_000"}
    for name, value in {**fit, **values}.items():
        old = next(line for line in text.splitlines() if line.startswith(f"{name} ="))
        text = text.replace(old, f"{name} = {value}")
    return text


# The record.


def test_a_whole_trial_writes_its_record_last_from_the_runs_own_events(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    record = trial.run(playing(SHELL, DONE))
    assert trial.happened == ["mark", "command", "sweep", "sweep", "export"]
    assert trial.saved() == record
    assert trial.closed == ["model"]
    assert (record.gradable, record.infra_reason, record.run_reason) == (
        True,
        None,
        "done",
    )
    assert (record.steps, record.tool_calls) == (2, 1)
    assert record.tokens == Tokens(200, 20, 10, 40)
    exported = trial.folder / "harness" / "export"
    assert record.exported_events is not None and record.exported_events > 0
    assert (exported / "manifest.json").is_file()
    assert record.session_id == SESSION and record.code_revision == REVISION
    assert record.left_out_of_bounds is None


def saved_events(trial: Trial) -> tuple[Event, ...]:
    connection = open_database(trial.folder / "harness" / "store" / "state.db", 5.0)
    assert not isinstance(connection, Failure)
    try:
        events = asyncio.run(SqliteEventLog(connection, {}, 1 << 22).read(SESSION, 0))
    finally:
        connection.close()
    assert not isinstance(events, Failure)
    return events


def test_the_records_numbers_are_the_runs_summary(tmp_path: Path) -> None:
    trial = Trial(tmp_path)
    record = trial.run(playing(SHELL, DONE))
    summary = run_summary(saved_events(trial))
    assert summary is not None
    assert (record.run_id, record.steps, record.tool_calls) == (
        summary.run_id,
        summary.steps,
        summary.tool_calls,
    )
    assert record.micro_dollars == summary.micro_dollars
    assert record.micro_dollars is not None and record.micro_dollars > 0


def test_the_one_message_comes_from_the_bench(tmp_path: Path) -> None:
    trial = Trial(tmp_path)
    trial.run(playing(DONE))
    started = saved_events(trial)[0]
    assert started.type == "session_started"
    assert started.payload == {"channel": "bench", "conversation": SESSION}


# Each ending of a run.


@pytest.mark.parametrize(
    ("turns", "settings", "run", "reason"),
    [
        ((DONE,), {}, ("done", ""), None),
        ((SHELL,), {"max_steps": "1"}, ("limit", "steps"), None),
        ((ScriptedBreak((), WRONG),), {}, ("failed", "model_invalid_input"), None),
        (
            (ScriptedBreak((), OUTAGE),),
            {},
            ("failed", "model_unavailable"),
            "model_unavailable",
        ),
    ],
)
def test_each_ending_of_the_run_gives_its_side(
    tmp_path: Path,
    turns: tuple[Turn, ...],
    settings: dict[str, str],
    run: tuple[str, str | None],
    reason: str | None,
) -> None:
    trial = Trial(tmp_path, quick_settings(**settings))
    record = trial.run(playing(*turns))
    assert (record.run_reason, record.run_detail) == run
    assert (record.gradable, record.infra_reason) == (reason is None, reason)
    assert trial.saved() == record


# The stops before the run, which call no model.


def test_a_run_set_at_its_budget_makes_no_model_call(tmp_path: Path) -> None:
    trial = Trial(tmp_path)
    other = trial.job / "trial-b" / "harness"
    other.mkdir(parents=True)
    spent = dataclasses.replace(SPENT, micro_dollars=BUDGET)
    (other / RECORD).write_text(record_text(spent))
    model = playing(DONE)
    record = trial.run(model)
    assert (record.gradable, record.infra_reason) == (False, "budget_reached")
    assert model.requests == [] and trial.happened == []
    assert record.portable_config_id is None and record.micro_dollars == 0


def test_a_store_that_does_not_open_is_infra_with_its_reason_kept(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    old = Failure("needs_user", "none", "SQLite 3.45.1 is older than 3.51.3.", "db")

    def open_store(folder: Path, config: Config) -> TrialStore | Failure:
        return old

    parts = dataclasses.replace(trial.parts(playing(DONE)), open_store=open_store)
    record = asyncio.run(run_trial(parts, trial.request()))
    assert (record.gradable, record.infra_reason) == (False, "harness_failure")
    assert record.run_detail == old.message
    assert trial.saved() == record


def test_a_settings_copy_that_is_not_the_planned_config_makes_no_model_call(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    model = playing(DONE)
    record = trial.run(model, planned_portable_id="0" * 64)
    assert (record.gradable, record.infra_reason) == (False, "config_mismatch")
    assert model.requests == []
    assert record.portable_config_id == trial.portable(trial.request())


def test_code_that_is_not_the_planned_code_makes_no_model_call(tmp_path: Path) -> None:
    trial = Trial(tmp_path)
    model = playing(DONE)
    record = trial.run(model, planned_revision="6" * 40)
    assert (record.gradable, record.infra_reason) == (False, "config_mismatch")
    assert model.requests == []


def test_a_container_that_does_not_answer_the_mark_makes_no_model_call(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    trial.container.listing = None
    model = playing(DONE)
    record = trial.run(model)
    assert (record.gradable, record.infra_reason) == (False, "executor_unavailable")
    assert model.requests == []


# What goes wrong after the run.


def test_a_command_that_lost_the_container_makes_the_trial_infra(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    trial.container.lost = True
    record = trial.run(playing(SHELL, DONE))
    assert (record.run_reason, record.gradable) == ("done", False)
    assert record.infra_reason == "executor_unavailable"


def test_a_sweep_that_finds_something_left_makes_the_trial_infra(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    trial.container.passes = "swept 1"
    nested_folders(trial.work, MOST_DEPTH + 1)
    record = trial.run(playing(DONE))
    assert (record.gradable, record.infra_reason) == (False, "sweep_failed")
    assert record.left_out_of_bounds is None
    assert trial.saved() == record


def test_a_failed_export_makes_the_trial_infra(tmp_path: Path) -> None:
    trial = Trial(tmp_path)
    trial.export_fails = True
    nested_folders(trial.work, MOST_DEPTH + 1)
    record = trial.run(playing(DONE))
    assert (record.gradable, record.infra_reason) == (False, "export_failed")
    assert (record.left_out_of_bounds, record.exported_events) == (None, None)


# What the agent left.


def nested_folders(folder: Path, depth: int) -> None:
    place = folder
    for _ in range(depth):
        place = place / "d"
    place.mkdir(parents=True, exist_ok=True)


def too_deep(folder: Path) -> None:
    nested_folders(folder, MOST_DEPTH + 1)


def stated_size(folder: Path) -> None:
    with (folder / "large").open("wb") as made:
        made.truncate(MOST_STATED_BYTES + 1)


def many_entries(folder: Path) -> None:
    for n in range(MOST_ENTRIES + 1):
        (folder / str(n)).touch()


@pytest.mark.parametrize(
    ("leave", "word"),
    [
        (stated_size, "too_large"),
        (many_entries, "too_many_entries"),
        (too_deep, "too_deep"),
    ],
)
def test_a_work_folder_past_a_bound_is_gradable_with_the_bounds_word(
    tmp_path: Path, leave: Callable[[Path], None], word: str
) -> None:
    trial = Trial(tmp_path)
    leave(trial.work)
    record = trial.run(playing(DONE))
    assert (record.gradable, record.left_out_of_bounds) == (True, word)


def test_what_the_agent_left_is_measured_after_the_sweep(tmp_path: Path) -> None:
    # A process left behind that writes until the sweep kills it.
    trial = Trial(tmp_path)
    trial.container.at_the_sweep = lambda: too_deep(trial.work)
    record = trial.run(playing(DONE))
    assert record.left_out_of_bounds == "too_deep"


# The tools of a trial.


def test_the_real_parts_of_a_trial_have_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_lookup(*args: object, **kwargs: object) -> None:
        raise AssertionError("a trial looked a name up")

    monkeypatch.setattr(socket, "getaddrinfo", no_lookup)
    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", no_lookup)
    trial = Trial(tmp_path)
    config = trial_config(trial.request())
    parts = trial_parts("made-up-key", REVISION)
    executor = parts.executor(trial.container, (1001, 1001), trial.work)
    tools = parts.tools(config.tools, InMemoryBlobStore(1 << 20), executor)
    fetched = asyncio.run(tools.call("web_fetch", {"url": "https://made-up.invalid/"}))
    assert not isinstance(fetched, Failure)
    assert "could not be found" in fetched.text and fetched.is_error


# The tools of a trial, as the model sees them.


def test_a_fetch_gets_the_result_for_a_name_that_cannot_be_found(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    model = playing(FETCH, DONE)
    trial.run(model)
    assert "web_fetch" in [tool.name for tool in model.requests[0].tools]
    results = [
        part
        for message in model.requests[1].messages
        for part in message.parts
        if isinstance(part, ToolResultPart)
    ]
    assert len(results) == 1
    assert "could not be found" in canonical_json(results[0])


def test_the_prompt_front_is_that_of_a_chat_on_the_same_settings_file(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    in_trial = playing(DONE)
    trial.run(in_trial)
    config = with_tool_folders(load_config(trial.settings), tmp_path / "chat")
    in_chat = playing(DONE)
    screen, errors = Screen(), io.StringIO()
    parts = chat_parts(
        config,
        model_set(config, in_chat),
        builtin_tooling(config, SystemClock()),
        InMemoryTraceSink(),
        InMemoryTraceCursor(),
        typed(b"Hello.\n").reader,
        screen,
        errors,
        REVISION,
    )
    assert chat(config, None, parts) == 0
    trial_front, chat_front = in_trial.requests[0], in_chat.requests[0]
    assert canonical_json(trial_front.system) == canonical_json(chat_front.system)
    assert canonical_json(trial_front.tools) == canonical_json(chat_front.tools)
