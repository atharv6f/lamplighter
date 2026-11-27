"""Builds made-up logs by hand, for the tests of spans and of the projector.

Every event gets the next log position and the next place in its session.
Each event is stamped 100 ms after the one before, unless a test gives its
own time, so the times of every span are known in advance.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from lamplighter.core import Reply
from lamplighter.ports import (
    ErrorEvent,
    Event,
    Failure,
    JsonObject,
    Message,
    Part,
    StopEvent,
    StopReason,
    TextPart,
    ToolCallPart,
    Usage,
    event_id_for,
    inbound_event_id,
    to_plain,
    utc_text,
)

START = datetime(2026, 11, 26, 7, 35, 12, 100_000, tzinfo=UTC)
START_MS = 1_795_678_512_100
CONFIG = "c" * 64
USAGE = Usage(input_uncached=100, cache_read=40, cache_write=10, output=20)
STEP_MS = 100


@dataclass
class MadeUpLog:
    """The events of every session, in log order, as a log would give them."""

    events: list[Event] = field(default_factory=list[Event])
    _seqs: dict[str, int] = field(default_factory=dict[str, int])
    _ms: int = 0

    def run(self, session: str = "sess-a", run_id: str = "run-1") -> "MadeUpRun":
        """Give a builder of one run of a session, which writes into this log."""
        return MadeUpRun(self, session, run_id)

    def add(
        self,
        session: str,
        run_id: str | None,
        kind: str,
        payload: object,
        step: int | None = None,
        duration_ms: int | None = None,
        at_ms: int | None = None,
        event_id: str | None = None,
        cause: str | None = None,
    ) -> Event:
        """Save one event at the next position, 100 ms after the last one."""
        self._ms = self._ms + STEP_MS if at_ms is None else at_ms
        seq = self._seqs.get(session, 0) + 1
        self._seqs[session] = seq
        made = Event(
            session_id=session,
            seq=seq,
            pos=len(self.events) + 1,
            event_id=event_id or event_id_for(session, seq),
            type=kind,
            v=1,
            payload=cast(JsonObject, to_plain(payload)),
            at=utc_text(START + timedelta(milliseconds=self._ms)),
            run_id=run_id,
            step=step,
            duration_ms=duration_ms,
            cause=cause,
        )
        self.events.append(made)
        return made

    @property
    def now_ms(self) -> int:
        """The time of the last event, in ms after START."""
        return self._ms

    def has(self, session: str) -> bool:
        """Say whether the session has an event in this log yet."""
        return session in self._seqs


@dataclass
class MadeUpRun:
    """Adds the events of one run in the shapes the loop writes them."""

    log: MadeUpLog
    session: str
    run_id: str
    step: int = 0

    @property
    def events(self) -> list[Event]:
        """This run's events so far, in order."""
        return [
            event
            for event in self.log.events
            if event.session_id == self.session and event.run_id == self.run_id
        ]

    def add(self, kind: str, payload: object, **more: Any) -> Event:
        step = None if self.step == 0 else self.step
        return self.log.add(self.session, self.run_id, kind, payload, step, **more)

    def start(
        self, text: str = "Hello.", config_id: str | None = CONFIG, **more: Any
    ) -> Event:
        if not self.log.has(self.session):
            self.log.add(
                self.session,
                None,
                "session_started",
                {"channel": "terminal", "conversation": self.session},
            )
        payload: dict[str, Any] = {"trigger": "message", "wall": {"adapter": "x"}}
        if config_id is not None:
            payload["config_id"] = config_id
        started = self.add("run_started", payload, **more)
        self.add(
            "user_message",
            {"text": text},
            event_id=inbound_event_id("terminal", f"{self.run_id}-{text}"),
        )
        return started

    def request(
        self,
        model: str | None = "made-up-1",
        provider: str | None = "made-up",
        **more: Any,
    ) -> Event:
        self.step += 1
        payload: dict[str, Any] = {"deadline_ms": 600_000}
        if model is not None:
            payload["model"] = model
        if provider is not None:
            payload["provider"] = provider
        return self.add("model_request", payload, **more)

    def reply(
        self,
        *parts: Part,
        reason: StopReason = "end",
        usage: Usage | None = USAGE,
        cost: int | None = 1_234,
        duration_ms: int | None = 80,
        error: Failure | None = None,
        reported: str | None = "made-up-1-2026",
        **more: Any,
    ) -> Event:
        end: StopEvent | ErrorEvent = (
            StopEvent(
                reason=reason,
                provider_reason=reason,
                model_reported=reported,
                skipped=0,
            )
            if error is None
            else ErrorEvent(failure=error, model_reported=reported, skipped=0)
        )
        made = Reply(Message(role="assistant", parts=parts), usage, end)
        payload = cast(dict[str, Any], to_plain(made))
        if cost is not None:
            payload["cost_micro_dollars"] = cost
        # A reply names its request as its cause, as the loop writes it.
        asked = [e for e in self.events if e.type == "model_request"]
        more.setdefault("cause", asked[-1].event_id if asked else None)
        return self.add("model_response", payload, duration_ms=duration_ms, **more)

    def intent(
        self,
        call: ToolCallPart,
        action_class: str = "read",
        effect_id: int | None = None,
        target: str | None = None,
        **more: Any,
    ) -> Event:
        payload: dict[str, Any] = {
            "call_id": call.id,
            "name": call.name,
            "action_class": action_class,
        }
        if effect_id is not None:
            payload["effect_id"] = effect_id
        if target is not None:
            payload["target"] = target
        return self.add("tool_intent", payload, **more)

    def result(
        self,
        call: ToolCallPart,
        text: str = "Done it.",
        is_error: bool = False,
        harness: str | None = None,
        failure_kind: str | None = None,
        duration_ms: int | None = 30,
        tainted: bool = False,
        full_output: JsonObject | None = None,
        **more: Any,
    ) -> Event:
        payload: dict[str, Any] = {
            "call_id": call.id,
            "text": text,
            "is_error": is_error,
        }
        if harness is not None:
            payload["harness"] = harness
            payload["is_error"] = True
        if failure_kind is not None:
            payload["failure_kind"] = failure_kind
            payload["is_error"] = True
        if tainted:
            payload["tainted"] = True
        if full_output is not None:
            payload["full_output"] = full_output
        return self.add("tool_result", payload, duration_ms=duration_ms, **more)

    def ran(self, call: ToolCallPart, text: str = "Done it.") -> Event:
        self.intent(call)
        return self.result(call, text)

    def ready(
        self, text: str = "Done.", reason: str = "done", detail: str = ""
    ) -> Event:
        kind = "reply" if reason == "done" else "notice"
        return self.add(
            "reply_ready",
            {"text": text, "kind": kind, "run_reason": reason, "run_detail": detail},
        )

    def end(self, reason: str = "done", detail: str = "", **more: Any) -> Event:
        return self.add("run_end", {"reason": reason, "detail": detail}, **more)

    def answered(self, text: str = "All done.") -> None:
        """One step whose reply is the final text."""
        self.request()
        self.reply(TextPart(text=text))


def tool_call(call_id: str, name: str, **arguments: Any) -> ToolCallPart:
    """Give a tool call with these arguments."""
    return ToolCallPart(id=call_id, name=name, arguments=dict(arguments))


def plain_run(log: MadeUpLog | None = None) -> MadeUpRun:
    """The run of the plain table: one tool call in step 1, the answer in step 2."""
    run = (log or MadeUpLog()).run()
    run.start()
    note = tool_call("c1", "note", n=1)
    run.request()
    run.reply(note, reason="tool_calls")
    run.ran(note)
    run.request()
    run.reply(TextPart(text="All done."))
    run.ready()
    run.end()
    return run
