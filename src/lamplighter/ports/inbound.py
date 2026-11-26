"""One message as it arrived on a channel, before it is routed to a session."""

from dataclasses import dataclass
from typing import Literal

from lamplighter.ports.blob_store import BlobHandle


@dataclass(frozen=True, slots=True)
class Sender:
    """Who sent a message, as the channel names them, and whether it is you."""

    id: str
    verified: bool


@dataclass(frozen=True, slots=True)
class Inbound:
    """One message from a channel.

    `channel` and `native_id` together are the key that stops one message
    being saved twice. `command` is set only when `kind` is "command", and
    `approval_id` and `answer` only when it is "approval_answer". Both times
    are in the saved time form. A channel with no clock of its own sets them
    equal. The bytes of each attachment are put in the blob store first.
    """

    channel: str
    native_id: str
    conversation: str
    sender: Sender
    kind: Literal["message", "command", "approval_answer"]
    text: str
    sent_at: str
    received_at: str
    attachments: tuple[BlobHandle, ...] = ()
    command: str | None = None
    approval_id: str | None = None
    answer: Literal["yes", "no"] | None = None
    v: int = 1
