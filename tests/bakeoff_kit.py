"""The bake-off's commands on a made-up checkout, made-up servers and fakes.

The capture set here is made-up requests, written where the bake-off
reads it. The model answers each by its last text, the server for the
bake-off's own requests counts and answers as `llama-server` does, and
the footprint and pressure are readings a test lists.
"""

import asyncio
import dataclasses
import json
import random
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import httpx

from lamplighter.adapters import ScriptedBreak, ScriptedModel, ScriptedReply
from lamplighter.app.bakeoff import BakeoffParts, capture_path, write_rule
from lamplighter.app.bakeoff_measure import MeasureParts
from lamplighter.app.capture_set import Captured, capture_line, set_hash
from lamplighter.app.config import Config
from lamplighter.app.model_set import ModelSet
from lamplighter.ports import (
    BakeoffBody,
    BakeoffRow,
    EventLog,
    Failure,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    TextPart,
    saved_json,
)
from tests.bench_kit import Bench, git
from tests.ladder_kit import SteeredHealth
from tests.model_samples import PROFILE
from tests.run_history import call
from tests.settings_text import GOOD, LOCAL, with_local
from tests.test_bakeoff_wire import Handler, body_of, counting
from tests.test_capture_set import REBUILD, made_up
from tests.test_memory_watch import Readings, YieldingClock

REPO = Path(__file__).resolve().parent.parent
# The made-up model's profile, with the rule's window and longest reply.
LOCAL_MODEL = dataclasses.replace(
    PROFILE, adapter="openai_chat", window=65_536, max_output=16_000
)
GB = 1_000_000_000
# How long the made-up model takes to answer, so the watch reads between.
ANSWER_SECONDS = 2.0
# The made-up requests of the capture set, each with what its recorded
# reply did, and the reason its made-up answer is judged by.
ASKED = (
    ("Answer in words.", False, "usable"),
    ("Wait one second.", True, "usable"),
    ("Wait a wrong amount.", True, "schema"),
    ("Answer with markup.", False, "call_markup"),
    ("Wait, but say it.", True, "no_call"),
)
NOTES = """\
weights_source = "https://example.invalid/made-up-weights"
quant = "Q4_K_M"
added_bytes = 1_000_000
adapter_per_request = "at_start"
adapter_evidence = "The made-up docs say adapters load at start."

[[hard]]
name = "pinned"
passed = true
evidence = "Made-up install with every hash."

[[hard]]
name = "broken_call_visible"
passed = true
evidence = "A made-up broken call came back as an error."

[[hard]]
name = "prompt_over_window_caught"
passed = true
evidence = "A made-up long prompt was refused."

[[hard]]
name = "loopback_only"
passed = true
evidence = "Made-up listen on 127.0.0.1 only."
"""
FITS = '{"street": "Made-up Lane", "lamps": 12}'


def only_local() -> str:
    """Give settings with only the local entry, and one route to it."""
    route = '[[routes]]\nid = "default"\nwhen = "always"\nmodels = ["local"]\n\n'
    return LOCAL.lstrip() + "\n" + route + GOOD[GOOD.index("[store]") :]


def answer(request: ModelRequest) -> ScriptedReply | ScriptedBreak:
    """The made-up model's answer to each made-up request, by its last text."""
    part = request.messages[-1].parts[0]
    assert isinstance(part, TextPart)
    text = part.text
    if text.startswith("Wait one"):
        return ScriptedReply((call("call_a", seconds=1),), "tool_calls")
    if text.startswith("Wait a wrong"):
        return ScriptedReply((call("call_a", seconds="one"),), "tool_calls")
    if text.startswith("Answer with markup"):
        failure = Failure(
            kind="transient",
            effect="none",
            message="Made-up markup in the text.",
            source="openai_chat",
            detail={"model_output": "call_markup"},
        )
        return ScriptedBreak((), failure)
    return ScriptedReply((TextPart(text="Words."),), "end")


class AnsweringModel:
    """Answers by `answer`, taking a while on the clock. Satisfies ModelClient.

    `fail_from` makes every answer from that request on end with a failure.
    `stop_at` raises KeyboardInterrupt at that request, as a kill does.
    """

    def __init__(self, clock: YieldingClock) -> None:
        self.clock = clock
        self.seconds = ANSWER_SECONDS
        self.requests: list[ModelRequest] = []
        self.counted: list[ModelRequest] = []
        self.count = 1_000
        self.fail_from: int | None = None
        self.stop_at: int | None = None

    @property
    def profile(self) -> ModelProfile:
        return LOCAL_MODEL

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        self.requests.append(request)
        if self.stop_at is not None and len(self.requests) > self.stop_at:
            raise KeyboardInterrupt
        await self.clock.sleep(self.seconds)
        played = answer(request)
        if self.fail_from is not None and len(self.requests) > self.fail_from:
            failure = Failure(
                kind="unavailable", effect="none", message="Made up.", source="test"
            )
            played = ScriptedBreak((), failure)
        model = ScriptedModel(LOCAL_MODEL, [played], random.Random(0))
        events: list[ModelEvent] = [event async for event in model.stream(request)]
        for event in events:
            yield event

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        self.counted.append(request)
        return self.count


def made_up_server(schema_reply: str = FITS) -> Handler:
    """The server's answers to the bake-off's own requests."""
    count = counting()

    async def handle(request: httpx.Request) -> httpx.Response:
        if "response_format" in body_of(request):
            message = {"role": "assistant", "content": schema_reply}
            content = {"choices": [{"index": 0, "message": message}]}
            return httpx.Response(200, content=json.dumps(content).encode())
        return await count(request)

    return handle


class Bakeoff:
    """The bake-off's parts on the made-up bench, model, server and readings."""

    def __init__(self, tmp_path: Path) -> None:
        self.bench = Bench(tmp_path)
        self.clock = YieldingClock()
        self.bench.clock = self.clock
        rule = (REPO / "bench" / "bakeoff_rule.toml").read_text()
        (self.bench.root / "bench" / "bakeoff_rule.toml").write_text(rule)
        git(self.bench.root, "add", "-A")
        git(self.bench.root, "commit", "-q", "-m", "Add the rule")
        self.settings = tmp_path / "local.toml"
        self.settings.write_text(with_local())
        self.notes = tmp_path / "notes.toml"
        self.notes.write_text(NOTES)
        self.model = AnsweringModel(self.clock)
        self.health = SteeredHealth()
        self.closed = 0
        self.footprint: Callable[[], int | None] = Readings(20 * GB, 22 * GB, 21 * GB)
        self.pressure = Readings(1, 2, 1)
        self.listening: Failure | None = None
        self.ports: list[int] = []
        self.server: Handler = made_up_server()
        self.awake: list[str] = []
        self.log: EventLog | None = None
        self.log_closed = 0
        self.chat_ids: frozenset[str] = frozenset()

    def parts(self) -> BakeoffParts:
        return BakeoffParts(
            bench=self.bench.parts(),
            models=self.models,
            chat_ids=lambda config: self.chat_ids,
            rebuild=lambda config: REBUILD,
            open_log=self.open_log,
        )

    def measure(self) -> MeasureParts:
        return MeasureParts(
            bakeoff=self.parts(),
            http=self.http,
            footprint=self.footprint_of,
            pressure=self.pressure,
            keep_awake=self.keep_awake,
        )

    def models(self, config: Config) -> ModelSet:
        async def close() -> None:
            self.closed += 1

        clients = {entry.name: self.model for entry in config.models}
        return ModelSet(clients, {"local": self.health}, close)

    def open_log(self, config: Config) -> tuple[EventLog, Callable[[], None]] | Failure:
        assert self.log is not None

        def close() -> None:
            self.log_closed += 1

        return self.log, close

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.server))

    def footprint_of(self, port: int) -> Callable[[], int | None] | Failure:
        self.ports.append(port)
        return self.listening if self.listening is not None else self.footprint

    def keep_awake(self) -> Callable[[], None]:
        self.awake.append("start")
        return lambda: self.awake.append("stop")

    def captures(self) -> Path:
        return self.parts().captures()

    def write_capture(self, asked: tuple[tuple[str, bool, str], ...] = ASKED) -> str:
        """Write the made-up capture set and its summary, and give its hash."""
        requests = [
            Captured(made_up(text).request, recorded, "chat")
            for text, recorded, _ in asked
        ]
        text = "".join(capture_line(c) + "\n" for c in requests)
        digest = set_hash(text)
        folder = self.captures()
        folder.mkdir(parents=True, exist_ok=True)
        capture_path(folder, digest).write_text(text)
        summary = {"capture_set_hash": digest, "requests": len(requests)}
        capture_path(folder, digest).with_suffix(".json").write_text(
            saved_json(summary)
        )
        return digest

    def start(self) -> str:
        """Write the capture set and the rule row, as the build does first."""
        digest = self.write_capture()
        assert asyncio.run(write_rule(self.parts(), "", "")) == 0, self.said()
        return digest

    def steps(self) -> list[BakeoffBody]:
        return [r.body for r in self.bench.rows() if isinstance(r, BakeoffRow)]

    def said(self) -> str:
        return self.bench.said()

    def out(self) -> str:
        return self.bench.out.getvalue()
