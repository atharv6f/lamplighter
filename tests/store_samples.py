"""Made-up records for the store tests: actions, results and inbound messages."""

import dataclasses

from lamplighter.ports import (
    Action,
    EffectResult,
    Outcome,
    effect_key,
)


def action(
    session_id: str = "sess-a",
    reply_seq: int = 41,
    call_index: int = 0,
    attempt: int = 0,
) -> Action:
    """A made-up write of a file, keyed by where its call sits."""
    return Action(
        session_id=session_id,
        reply_seq=reply_seq,
        call_index=call_index,
        attempt=attempt,
        tool="write_file",
        args={"path": "notes.txt", "text": "tea", "ratio": 0.5},
        action_class="reversible_write",
        target="file:notes.txt",
        effect_key=effect_key(session_id, reply_seq, call_index, attempt),
        action_hash="a" * 64,
        outward=False,
    )


def other_args(made: Action) -> Action:
    return dataclasses.replace(made, args={"path": "notes.txt", "text": "coffee"})


PROBE = {"kind": "file_hash", "v": 1, "path": "notes.txt", "sha256": "b" * 64}
UNDO_BEFORE = {"kind": "restore_file", "v": 1, "blob": "c" * 64}
UNDO_AFTER = {"kind": "restore_file", "v": 1, "blob": "d" * 64}

DONE = EffectResult(outcome="done", output={"bytes": 3})
FAILED = EffectResult(outcome="failed", output={"error": "the folder is read-only"})
TIMED_OUT = EffectResult(outcome="unknown", output={"reason": "timed out after 30 s"})
DONE_WITH_UNDO = EffectResult(
    outcome="done", output={"bytes": 3}, undo=UNDO_AFTER, external_id="file-7"
)

HAPPENED = Outcome(happened=True, settled_by="probe", evidence={"sha256": "e" * 64})
DID_NOT_HAPPEN = Outcome(happened=False, settled_by="user", evidence={"answer": "no"})
