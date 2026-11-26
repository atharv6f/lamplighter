"""The rows of the frozen version 1 store.

The text file tests/stores/version_1.sql was made once, by writing these
rows through the SQLite adapters and dumping the file as SQL. Neither the
text file nor this one is edited after that. The test that reads the text
file compares it with what the in-memory stores give for the same writes,
so a change that reads old data differently fails it.
"""

from typing import cast

from lamplighter.core import Reply
from lamplighter.ports import (
    EffectLedger,
    EventLog,
    Inbox,
    JsonObject,
    Message,
    NewEvent,
    StopEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
    inbound_event_id,
    to_plain,
)
from tests.model_samples import REASONING
from tests.store_samples import DONE_WITH_UNDO, UNDO_BEFORE, action, inbound

AT = "2026-11-26T07:35:12.345Z"
CALL = ToolCallPart(id="call_a", name="read_file", arguments={"path": "list.txt"})
REPLY = Reply(
    message=Message(
        role="assistant", parts=(REASONING, TextPart(text="I will look."), CALL)
    ),
    usage=Usage(input_uncached=120, cache_read=0, cache_write=0, output=40),
    end=StopEvent(
        reason="tool_calls",
        provider_reason="tool_use",
        model_reported="made-up-1",
        skipped=0,
    ),
)
RESULT = ToolResultPart(call_id="call_a", text="milk\neggs\ntea", is_error=False)

SESSION_A = [
    NewEvent(
        type="user_message",
        v=1,
        payload={"text": "What is on my list?"},
        at=AT,
        event_id=inbound_event_id("telegram", "8812"),
    ),
    NewEvent(type="run_started", v=1, payload={}, at=AT, run_id="run-1"),
    NewEvent(
        type="model_response",
        v=1,
        payload=cast(JsonObject, to_plain(REPLY)),
        at=AT,
        run_id="run-1",
        step=1,
        duration_ms=2_150,
    ),
    NewEvent(
        type="tool_result",
        v=1,
        payload={"call_id": "call_a", "text": "milk\neggs\ntea", "is_error": False},
        at=AT,
        run_id="run-1",
        step=1,
    ),
    NewEvent(type="run_end", v=1, payload={"reason": "done"}, at=AT, run_id="run-1"),
]
SESSION_B = [NewEvent(type="run_started", v=1, payload={}, at=AT, run_id="run-2")]
DONE_ACTION = action("sess-a", reply_seq=3)
INTENDED_ACTION = action("sess-b", reply_seq=1)


async def write_samples(log: EventLog, ledger: EffectLedger, inbox: Inbox) -> None:
    """Write the frozen rows. Used once, to make the text file."""
    await inbox.accept(inbound("8812"))
    await inbox.accept(inbound("8813"))
    await log.append("sess-a", SESSION_A, 0)
    await log.append("sess-b", SESSION_B, 0)
    await inbox.mark_routed(1, "sess-a", 1)
    await ledger.begin(DONE_ACTION, None, UNDO_BEFORE)
    await ledger.begin(INTENDED_ACTION, None, None)
    await ledger.finish(1, DONE_WITH_UNDO)
