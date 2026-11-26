"""What the harness sends to you, and the receipt a channel gives for it."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Outbound:
    """One message to you.

    `out_id` is worked out from the session and the seq of the event the
    message came from, so a step that runs again finds the same row. A
    `notice` is a short line from the harness and a `reply` is the model's
    own text. `maybe_duplicate` is set when this copy may not be the first.
    """

    out_id: str
    session_id: str
    kind: Literal["reply", "notice"]
    text: str
    maybe_duplicate: bool = False
    v: int = 1


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """When a channel showed a message, in the saved time form."""

    at: str
