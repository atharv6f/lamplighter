"""Ids that count up, for tests that must know every id in advance."""


class CountingIds:
    """Gives id-0001, id-0002 and so on. Satisfies IdSource.

    A test that builds a new process on the same stores passes the same
    source on, because a real source never gives an id twice, and a count
    that began again would give the second process the first one's ids.
    """

    def __init__(self, prefix: str = "id") -> None:
        """Start the count at 1, with ids that begin with `prefix`."""
        self._prefix = prefix
        self.given = 0

    def new_id(self) -> str:
        """Give the next id in the count."""
        self.given += 1
        return f"{self._prefix}-{self.given:04d}"
