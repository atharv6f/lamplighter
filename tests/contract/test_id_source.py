"""The promises of the id port, checked on the fake and the real source."""

from collections.abc import Callable

import pytest

from lamplighter.adapters import CountingIds, SystemIds
from lamplighter.ports import IdSource, is_id

SOURCES: dict[str, Callable[[], IdSource]] = {
    "counting": CountingIds,
    "system": SystemIds,
}


@pytest.fixture(params=list(SOURCES.values()), ids=list(SOURCES))
def source(request: pytest.FixtureRequest) -> IdSource:
    return request.param()


def test_a_thousand_ids_all_fit_the_id_rule_and_none_repeats(source: IdSource) -> None:
    ids = [source.new_id() for _ in range(1_000)]
    assert all(is_id(made) for made in ids)
    assert len(set(ids)) == len(ids)


def test_the_real_source_gives_sixteen_hex_characters() -> None:
    made = SystemIds().new_id()
    assert len(made) == 16
    assert set(made) <= set("0123456789abcdef")


def test_the_counting_source_counts_from_one() -> None:
    source = CountingIds("run")
    assert [source.new_id(), source.new_id()] == ["run-0001", "run-0002"]
    assert source.given == 2
