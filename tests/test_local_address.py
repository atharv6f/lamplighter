"""The one form of Phoenix's address that stays on this Mac, row by row."""

import pytest

from lamplighter.adapters.phoenix import local_address

PASSES = {
    "http://127.0.0.1:6006": "http://127.0.0.1:6006",
    "http://127.0.0.1:6006/": "http://127.0.0.1:6006",
    "http://127.0.0.1:1": "http://127.0.0.1:1",
    "http://127.0.0.1:65535": "http://127.0.0.1:65535",
    "http://127.0.0.1:65535/": "http://127.0.0.1:65535",
}

REFUSED = [
    "http://127.0.0.1",
    "http://127.0.0.1:",
    "http://127.0.0.1:/",
    "http://127.0.0.1:0",
    "http://127.0.0.1:65536",
    "http://127.0.0.1:99999",
    "http://127.0.0.1:06006",
    "http://127.0.0.1:123456",
    "http://127.8.9.1:6006",
    "http://[::1]:6006",
    "http://localhost:6006",
    "http://0.0.0.0:6006",
    "https://127.0.0.1:6006",
    "HTTP://127.0.0.1:6006",
    " http://127.0.0.1:6006",
    "http://127.0.0.1:6006\n",
    "http://127.0.0.1:60\t06",
    "http://user@127.0.0.1:6006",
    "http://127.0.0.1:6006@example.com",
    "http://127.0.0.1.example.com:6006",
    "http://127.0.0.1:6006/prefix",
    "http://127.0.0.1:6006?x=1",
    "http://127.0.0.1:6006?",
    "http://127.0.0.1:6006#top",
    "http://127.0.0.1:6006//",
    "http://127.0.0.1:６００６",
    "http://127.0.0.1:+6006",
    "http://127.0.0.1:6006 ",
    "",
]


@pytest.mark.parametrize(("setting", "address"), PASSES.items())
def test_the_exact_form_passes_without_its_last_slash(
    setting: str, address: str
) -> None:
    assert local_address(setting) == address


@pytest.mark.parametrize("setting", REFUSED)
def test_any_other_text_does_not_pass(setting: str) -> None:
    assert local_address(setting) is None
