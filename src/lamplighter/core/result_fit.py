"""The one cut of a tool's text that is too long: its start and its end are kept."""

from dataclasses import dataclass

from lamplighter.core.harness_texts import cut_mark


@dataclass(frozen=True, slots=True)
class Cut:
    """A text that was cut: what is shown, how long it was, and how much is gone."""

    shown: str
    total: int
    dropped: int


def fit(text: str, cap: int) -> Cut | None:
    """Give the cut of a text longer than `cap` characters, or None when it fits.

    The first half of the cap and the last half are kept, with one line
    between them that says how many characters were left out. For an odd
    cap the last part is one longer. The same text and cap always give the
    same cut, so a replay builds the same prompt.
    """
    total = len(text)
    if total <= cap:
        return None
    head = cap // 2
    tail = cap - head
    dropped = total - cap
    shown = f"{text[:head]}\n\n{cut_mark(dropped)}\n\n{text[total - tail :]}"
    return Cut(shown=shown, total=total, dropped=dropped)
