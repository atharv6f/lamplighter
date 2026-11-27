"""The rule for an address web_fetch may connect to, against Python's `is_global`."""

import ipaddress

import pytest

from lamplighter.adapters.builtin_tools.public_address import is_public

REFUSED = [
    "127.0.0.1",
    "10.0.0.5",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "192.0.2.1",
    "198.18.0.1",
    "240.0.0.1",
    "255.255.255.255",
]


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8"])
def test_a_public_ipv4_address_is_public(address: str) -> None:
    assert is_public(address)


@pytest.mark.parametrize("address", REFUSED)
def test_a_private_or_reserved_address_is_refused(address: str) -> None:
    assert not is_public(address)


def test_multicast_is_refused_though_python_calls_it_global() -> None:
    assert ipaddress.ip_address("224.0.0.1").is_global
    assert not is_public("224.0.0.1")


@pytest.mark.parametrize(
    "address", ["2606:4700:4700::1111", "::1", "::ffff:8.8.8.8", "64:ff9b::808:808"]
)
def test_every_ipv6_address_is_refused(address: str) -> None:
    assert not is_public(address)


@pytest.mark.parametrize("text", ["", "example.com", "127.1", "1.2.3.4.5"])
def test_text_that_is_not_an_address_is_refused(text: str) -> None:
    assert not is_public(text)
