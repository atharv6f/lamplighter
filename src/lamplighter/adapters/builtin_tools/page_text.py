"""HTML turned into the text a person would read, with each link's address after it."""

import re
from html.parser import HTMLParser

_SPACES = re.compile(r"\s+")
# What is inside these is never text a reader sees.
_DROPPED = frozenset({"script", "style", "head", "noscript", "template"})
_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
        "pre", "section", "table", "td", "th", "tr", "ul",
    }
)  # fmt: skip


def page_text(html: str) -> str:
    """Give the text of a page: dropped parts left out, blocks on lines of their own.

    A link is its text and then its address in round brackets. Runs of
    blank lines become one. It can take seconds on a large hostile page, so
    it runs in a worker thread.
    """
    reader = _Reader()
    reader.feed(html)
    reader.close()
    lines = [" ".join(line.split()) for line in "".join(reader.pieces).split("\n")]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pieces: list[str] = []
        self._dropped = 0
        self._links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            # A browser ends the head here even when </head> is missing.
            self._dropped = 0
        elif tag in _DROPPED:
            self._dropped += 1
        elif tag in _BLOCKS:
            self.pieces.append("\n")
        elif tag == "a":
            self._links.append(dict(attrs).get("href"))

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROPPED:
            self._dropped = max(self._dropped - 1, 0)
        elif tag in _BLOCKS:
            self.pieces.append("\n")
        elif tag == "a" and self._links:
            href = self._links.pop()
            if href and not self._dropped:
                self.pieces.append(f" ({href})")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCKS:
            self.pieces.append("\n")

    def handle_data(self, data: str) -> None:
        # In HTML a line break in the text is a space. Lines come from blocks.
        if not self._dropped:
            self.pieces.append(_SPACES.sub(" ", data))
