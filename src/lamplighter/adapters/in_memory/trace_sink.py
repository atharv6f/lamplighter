"""A trace store kept in memory, for tests. It keeps the first copy of each span."""

from collections.abc import Sequence

from lamplighter.ports import Failure, Span


class InMemoryTraceSink:
    """Keeps each span id's first copy, in the order it came. Satisfies TraceSink.

    A test can make the next export fail with a failure of its choosing, and
    can read what is held and how many copies of each id arrived.
    """

    def __init__(self, leaves_this_mac: bool = False) -> None:
        self._leaves_this_mac = leaves_this_mac
        self._held: dict[str, Span] = {}
        self._copies: dict[str, int] = {}
        self._next_failure: Failure | None = None

    @property
    def leaves_this_mac(self) -> bool:
        """Whether anything this sink sends can reach another machine."""
        return self._leaves_this_mac

    @property
    def held(self) -> tuple[Span, ...]:
        """Every span kept, the first copy of each id, in the order it came."""
        return tuple(self._held.values())

    @property
    def copies(self) -> dict[str, int]:
        """How many times each span id arrived."""
        return dict(self._copies)

    def fail_next(self, failure: Failure) -> None:
        """Make the next export that has spans return `failure` and keep nothing."""
        self._next_failure = failure

    def empty(self) -> None:
        """Forget every span, as a trace store that was emptied by hand."""
        self._held.clear()
        self._copies.clear()

    async def export(self, spans: Sequence[Span]) -> Failure | None:
        """Keep each span whose id is new, or fail as the test asked."""
        if not spans:
            return None
        if self._next_failure is not None:
            failed, self._next_failure = self._next_failure, None
            return failed
        for span in spans:
            self._held.setdefault(span.span_id, span)
            self._copies[span.span_id] = self._copies.get(span.span_id, 0) + 1
        return None
