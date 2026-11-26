"""The port for new ids, passed in so a test can choose them."""

from typing import Protocol


class IdSource(Protocol):
    """Gives ids that are new.

    Callers can rely on this: every id fits the id rule of the stores, and a
    source never gives the same id twice. Session ids, run ids and the ids
    a channel gives its messages come from here, so no code makes an id on
    its own and a test can know each one in advance.
    """

    def new_id(self) -> str:
        """Give an id this source has not given before."""
        ...
