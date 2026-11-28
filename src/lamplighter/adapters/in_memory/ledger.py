"""A book of experiments kept in memory, for tests."""

from collections.abc import Sequence

from lamplighter.ports import ExperimentLedger, Failure, LedgerRow, refused_row


class InMemoryLedger(ExperimentLedger):
    """Keeps the rows in a list, and lets in only what the book's rules allow."""

    def __init__(self) -> None:
        """Start with no rows."""
        self._rows: list[LedgerRow] = []

    async def append(self, row: LedgerRow) -> Failure | None:
        """Add the row unless a rule refuses it."""
        refused = refused_row(self._rows, row)
        if refused is None:
            self._rows.append(row)
        return refused

    async def rows(self) -> Sequence[LedgerRow] | Failure:
        """Give every row, in the order added."""
        return tuple(self._rows)
