"""Builds the saved events of a session by hand, for tests of the pure functions."""

from typing import Any, cast

from lamplighter.adapters import StubTools
from lamplighter.core import Prices, Reply
from lamplighter.core.run_spec import RunSpec
from lamplighter.ports import (
    ErrorEvent,
    Event,
    Failure,
    Inbound,
    InboxEntry,
    JsonObject,
    Message,
    Part,
    Sender,
    StopEvent,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolEntry,
    Usage,
    event_id_for,
    inbound_event_id,
    to_plain,
)
from tests.fake_clock import FakeClock

AT = "2026-11-26T07:35:12.345Z"
SESSION = "sess-a"
CONFIG = "c" * 64
PRICES = Prices(
    input_uncached=4_000_000,
    cache_read=200_000,
    cache_write=5_000_000,
    output=20_000_000,
)
SPEC = RunSpec(
    config_id=CONFIG,
    system="You are a made-up helper.",
    max_steps=40,
    max_seconds=1_200,
    max_tokens=1_000_000,
    max_micro_dollars=1_000_000,
    model_max_output=1_000,
    model_deadline_ms=600_000,
    model_retries=2,
    cancel_bound_ms=800,
    prices=PRICES,
)
TOOLS: dict[str, ToolEntry] = {
    entry.spec.name: entry for entry in StubTools(FakeClock()).list_tools()
}


def call(call_id: str = "call_a", name: str = "wait", **arguments: Any) -> ToolCallPart:
    return ToolCallPart(id=call_id, name=name, arguments=arguments or {"seconds": 1})


def unparsed(call_id: str = "call_a", name: str = "wait") -> ToolCallPart:
    return ToolCallPart(
        id=call_id,
        name=name,
        arguments=None,
        raw_arguments='{"seconds": ',
        parse_error="not JSON: Expecting value",
    )


def inbound(native_id: str, text: str = "Hello.", session: str = SESSION) -> Inbound:
    return Inbound(
        channel="terminal",
        native_id=native_id,
        conversation=session,
        sender=Sender(id="local", verified=True),
        kind="message",
        text=text,
        sent_at=AT,
        received_at=AT,
    )


def stop(native_id: str, session: str = SESSION) -> Inbound:
    return Inbound(
        channel="terminal",
        native_id=native_id,
        conversation=session,
        sender=Sender(id="local", verified=True),
        kind="command",
        text="stop",
        sent_at=AT,
        received_at=AT,
        command="stop",
    )


def row(inbox_id: int, made: Inbound) -> InboxEntry:
    return InboxEntry(inbox_id=inbox_id, inbound=made)


class History:
    """The events of one session, added one at a time in the loop's shapes."""

    def __init__(self, session: str = SESSION) -> None:
        self.session = session
        self.events: list[Event] = []
        self.run_id: str | None = None
        self.step: int | None = None

    def add(
        self,
        kind: str,
        payload: Any,
        duration_ms: int | None = None,
        event_id: str | None = None,
        cause: str | None = None,
    ) -> Event:
        seq = len(self.events) + 1
        made = Event(
            session_id=self.session,
            seq=seq,
            pos=seq,
            event_id=event_id or event_id_for(self.session, seq),
            type=kind,
            v=1,
            payload=cast(JsonObject, payload),
            at=AT,
            run_id=None if kind == "session_started" else self.run_id,
            step=self.step,
            duration_ms=duration_ms,
            cause=cause,
        )
        self.events.append(made)
        return made

    def start(
        self,
        text: str = "Hello.",
        native_id: str = "m1",
        run_id: str = "run-1",
        config_id: str = CONFIG,
    ) -> "History":
        if not self.events:
            self.add("session_started", {"channel": "terminal", "conversation": "s"})
        self.run_id, self.step = run_id, None
        self.add("run_started", {"config_id": config_id, "trigger": "message"})
        self.said(text, native_id)
        return self

    def said(self, text: str, native_id: str) -> Event:
        return self.add(
            "user_message",
            {"text": text},
            event_id=inbound_event_id("terminal", native_id),
        )

    def request(self) -> Event:
        self.step = (self.step or 0) + 1
        return self.add("model_request", {"deadline_ms": 600_000})

    def reply(
        self,
        *parts: Part,
        reason: StopReason = "end",
        usage: Usage | None = None,
        cost: int | None = None,
        duration_ms: int = 1_000,
        error: Failure | None = None,
    ) -> Event:
        end: StopEvent | ErrorEvent = (
            StopEvent(
                reason=reason, provider_reason=reason, model_reported=None, skipped=0
            )
            if error is None
            else ErrorEvent(failure=error, model_reported=None, skipped=0)
        )
        made = Reply(Message(role="assistant", parts=parts), usage, end)
        payload = cast(dict[str, Any], to_plain(made))
        if cost is not None:
            payload["cost_micro_dollars"] = cost
        return self.add("model_response", payload, duration_ms=duration_ms)

    def step_with(self, *parts: Part, reason: StopReason = "tool_calls") -> Event:
        self.request()
        return self.reply(*parts, reason=reason)

    def intent(self, made: ToolCallPart, action_class: str | None = None) -> Event:
        """Save an intent. With no class it is one that older code wrote."""
        payload: dict[str, Any] = {"call_id": made.id, "name": made.name}
        if action_class is not None:
            payload["action_class"] = action_class
        return self.add("tool_intent", payload)

    def result(
        self,
        made: ToolCallPart,
        text: str = "Waited 1 seconds.",
        duration_ms: int | None = 10,
        harness: str | None = None,
    ) -> Event:
        payload: dict[str, Any] = {"call_id": made.id, "text": text, "is_error": False}
        if harness is not None:
            payload["harness"] = harness
            payload["is_error"] = True
        return self.add("tool_result", payload, duration_ms=duration_ms)

    def ran(self, made: ToolCallPart, text: str = "Waited 1 seconds.") -> None:
        self.intent(made)
        self.result(made, text)

    def ready(
        self, text: str = "Done.", reason: str = "done", detail: str = ""
    ) -> Event:
        kind = "reply" if reason == "done" else "notice"
        return self.add(
            "reply_ready",
            {"text": text, "kind": kind, "run_reason": reason, "run_detail": detail},
        )

    def end(self, reason: str = "done", detail: str = "") -> Event:
        made = self.add("run_end", {"reason": reason, "detail": detail})
        self.run_id, self.step = None, None
        return made

    def answered(self, text: str = "Done.") -> None:
        """A whole run that ends with a final text."""
        self.request()
        self.reply(TextPart(text=text))
        self.ready(text)
        self.end()
