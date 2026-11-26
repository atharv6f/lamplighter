"""The one cut of a long text, and the id of the saved whole, as pure functions."""

import re

from lamplighter.core.result_fit import Cut, fit
from lamplighter.ports import BlobHandle, output_id, read_output_id

# The SHA-256 of the four bytes "one" and a line break.
ONE_HASH = "2c8b08da5ce60398e1f19af0e5dccc744df274b826abe585eaba68c525434806"


def test_a_text_at_the_cap_is_kept_as_it_is() -> None:
    assert fit("x" * 10, 10) is None


def test_a_text_one_over_the_cap_keeps_its_two_halves() -> None:
    text = "abcde" + "!" + "vwxyz"
    assert fit(text, 10) == Cut(
        shown="abcde\n\n[... 1 characters left out here ...]\n\nvwxyz",
        total=11,
        dropped=1,
    )


def test_five_million_characters_keep_the_first_and_last_fifteen_thousand() -> None:
    text = "".join(chr(ord("a") + n % 26) for n in range(5_000_000))
    cut = fit(text, 30_000)
    assert cut is not None
    head, mark, tail = cut.shown.split("\n\n")
    assert head == text[:15_000]
    assert tail == text[-15_000:]
    assert mark == "[... 4970000 characters left out here ...]"
    assert (cut.total, cut.dropped) == (5_000_000, 4_970_000)


def test_an_odd_cap_gives_the_end_one_more_character() -> None:
    cut = fit("0123456789", 5)
    assert cut is not None
    assert cut.shown == "01\n\n[... 5 characters left out here ...]\n\n789"


def test_the_same_text_and_cap_always_give_the_same_cut() -> None:
    text = "é" * 40_000
    assert fit(text, 30_000) == fit(text, 30_000)


def test_the_characters_counted_are_letters_and_not_bytes() -> None:
    cut = fit("é" * 12, 10)
    assert cut is not None
    assert cut.dropped == 2


def test_an_output_id_holds_the_hash_in_two_halves_and_the_size() -> None:
    handle = BlobHandle(sha256=ONE_HASH, size=4, media_type="text/plain")
    made = output_id(handle)
    assert made == (
        "2c8b08da5ce60398e1f19af0e5dccc74-4df274b826abe585eaba68c525434806-4"
    )
    assert re.search("[0-9a-f]{64}", made) is None
    assert read_output_id(made) == (ONE_HASH, 4)


def test_text_of_another_form_is_not_an_output_id() -> None:
    for text in (
        ONE_HASH,
        ONE_HASH[:32] + "-" + ONE_HASH[32:],
        ONE_HASH[:32] + "-" + ONE_HASH[32:] + "-04",
        ONE_HASH[:32] + "-" + ONE_HASH[32:] + "-123456789",
        ONE_HASH[:32].upper() + "-" + ONE_HASH[32:] + "-4",
        " " + output_id(BlobHandle(ONE_HASH, 4, "text/plain")),
    ):
        assert read_output_id(text) is None, text
