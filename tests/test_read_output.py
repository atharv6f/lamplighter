"""read_output: pages of a saved text, and ids it will not read."""

import asyncio
import re
from pathlib import Path

import pytest

from lamplighter.adapters.builtin_tools.read_output import MOST_PAGE_CHARS, read_output
from lamplighter.adapters.in_memory import InMemoryBlobStore
from lamplighter.ports import (
    BlobHandle,
    Failure,
    FailureKind,
    JsonObject,
    ToolResult,
    output_id,
)
from tests.builtin_kit import tools_on

# Made-up output: long enough for three pages, with characters of two and
# three bytes so a page boundary in bytes would show.
WHOLE = "".join(f"line {n}: é€ made-up output\n" for n in range(2_500))
_HEADER = re.compile(r"\[Characters (\d+) to (\d+) of (\d+)\. (.*)\]")
_BARE_HASH = re.compile(r"[0-9a-f]{64}")


def _saved(text: str) -> tuple[InMemoryBlobStore, str]:
    store = InMemoryBlobStore(16_000_000)
    handle = asyncio.run(store.put(text.encode("utf-8"), "text/plain"))
    assert isinstance(handle, BlobHandle)
    return store, output_id(handle)


def _read(store: InMemoryBlobStore, **arguments: str | int) -> ToolResult:
    given: JsonObject = dict(arguments)
    return asyncio.run(read_output(store, given))


def _page(result: ToolResult) -> tuple[int, int, int, str, str]:
    first, _, page = result.text.partition("\n")
    match = _HEADER.fullmatch(first)
    assert match is not None, first
    start, end, total = (int(match.group(n)) for n in (1, 2, 3))
    return start, end, total, match.group(4), page


def test_the_pages_joined_are_the_whole_text() -> None:
    store, made = _saved(WHOLE)
    pages: list[str] = []
    offset = 0
    while True:
        result = _read(store, id=made, offset=offset)
        assert not result.is_error
        start, end, total, after, page = _page(result)
        assert (start, total, len(page)) == (offset, len(WHOLE), end - start)
        pages.append(page)
        if after == "That is the end.":
            break
        assert after == f"Use offset={end} to continue."
        offset = end
    assert len(pages) == 3
    assert "".join(pages) == WHOLE


def test_a_page_stops_at_the_limit_and_a_whole_page_fits_the_smallest_cap() -> None:
    store, made = _saved(WHOLE)
    assert _page(_read(store, id=made, offset=10, limit=7))[:4] == (
        10,
        17,
        len(WHOLE),
        "Use offset=17 to continue.",
    )
    full = _read(store, id=made)
    assert len(_page(full)[4]) == MOST_PAGE_CHARS
    assert len(full.text) < 26_000


def test_the_last_characters_are_the_end() -> None:
    store, made = _saved(WHOLE)
    start, end, _, after, page = _page(_read(store, id=made, offset=len(WHOLE) - 1))
    assert (start, end, after, page) == (
        len(WHOLE) - 1,
        len(WHOLE),
        "That is the end.",
        "\n",
    )


@pytest.mark.parametrize("offset", [len(WHOLE), len(WHOLE) + 1])
def test_an_offset_at_or_past_the_end_is_refused(offset: int) -> None:
    store, made = _saved(WHOLE)
    result = _read(store, id=made, offset=offset)
    assert result.is_error
    assert result.text == (
        f"The output has {len(WHOLE)} characters. offset={offset} is past the end."
    )


@pytest.mark.parametrize(
    "text",
    [
        "a" * 32 + "-" + "b" * 32,
        "a" * 64 + "-5",
        "A" * 32 + "-" + "b" * 32 + "-5",
        "a" * 32 + "-" + "b" * 32 + "-05",
        "a" * 32 + "-" + "b" * 32 + "-123456789",
        "a" * 31 + "-" + "b" * 33 + "-5",
    ],
)
def test_text_of_another_form_is_not_an_id(text: str) -> None:
    result = _read(InMemoryBlobStore(16), id=text)
    assert result.is_error
    assert result.text == (
        'That is not an output id. An id is the text after "saved as" in a result'
        " that was cut."
    )


def test_an_id_with_no_blob_is_said_to_have_none() -> None:
    store, made = _saved(WHOLE)
    result = _read(store, id="0" + made[1:])
    assert result.is_error
    assert result.text == "There is no saved output with that id."


def test_a_size_with_a_digit_left_off_gives_the_kind_and_no_bare_hash() -> None:
    store, made = _saved(WHOLE)
    short = made[:-1]
    result = _read(store, id=short)
    assert result.is_error
    assert result.text == (
        f"The saved output {short} could not be read (fatal). Copy the id again"
        " from the note of the result that was cut."
    )
    assert _BARE_HASH.search(result.text) is None


class _FailingStore(InMemoryBlobStore):
    """A store whose `exists` or `get` fails, with the hash in its message."""

    def __init__(self, failing: str, kind: FailureKind) -> None:
        super().__init__(16_000_000)
        self._failing = failing
        self._kind: FailureKind = kind

    def _failure(self, handle: BlobHandle) -> Failure:
        return Failure(self._kind, "none", f"made-up {handle.sha256}", "test")

    async def exists(self, handle: BlobHandle) -> bool | Failure:
        if self._failing == "exists":
            return self._failure(handle)
        return await super().exists(handle)

    async def get(self, handle: BlobHandle) -> bytes | Failure:
        if self._failing == "get":
            return self._failure(handle)
        return await super().get(handle)


@pytest.mark.parametrize(
    ("failing", "kind"), [("exists", "unavailable"), ("get", "transient")]
)
def test_a_store_that_fails_gives_only_its_kind(
    failing: str, kind: FailureKind
) -> None:
    store = _FailingStore(failing, kind)
    handle = asyncio.run(store.put(b"made-up", "text/plain"))
    assert isinstance(handle, BlobHandle)
    result = _read(store, id=output_id(handle))
    assert result.is_error
    assert f"could not be read ({kind})." in result.text
    assert "made-up" not in result.text
    assert _BARE_HASH.search(result.text) is None


def test_bytes_that_are_not_utf8_read_with_replacement() -> None:
    store = InMemoryBlobStore(16)
    handle = asyncio.run(store.put(b"a\xffb", "application/octet-stream"))
    assert isinstance(handle, BlobHandle)
    assert _page(_read(store, id=output_id(handle)))[4] == "a�b"


@pytest.mark.parametrize(
    "arguments",
    [{"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 25_001}, {"limit": "5"}],
)
def test_an_offset_or_limit_out_of_range_is_refused(arguments: JsonObject) -> None:
    store, made = _saved(WHOLE)
    result = asyncio.run(read_output(store, {"id": made, **arguments}))
    assert result.is_error
    assert result.text.startswith("read_output needs an id, an offset from 0")


def test_the_provider_reads_from_its_own_store(tmp_path: Path) -> None:
    store, made = _saved(WHOLE)
    tools = tools_on(tmp_path, blobs=store)
    result = asyncio.run(tools.call("read_output", {"id": made, "limit": 5}))
    assert isinstance(result, ToolResult)
    assert _page(result)[4] == WHOLE[:5]
    entry = next(e for e in tools.list_tools() if e.spec.name == "read_output")
    assert (entry.action_class, entry.taints) == ("read", True)
