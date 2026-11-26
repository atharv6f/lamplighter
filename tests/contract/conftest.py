"""Runs every contract test once for each kind of model client and of store."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.contract.model_clients import KINDS, ClientKind
from tests.contract.stores import KINDS as STORE_KINDS
from tests.contract.stores import StoreKind, Stores
from tests.fake_clock import FakeClock


@pytest.fixture(params=list(KINDS.values()), ids=list(KINDS))
def kind(request: pytest.FixtureRequest) -> ClientKind:
    return request.param


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=list(STORE_KINDS.values()), ids=list(STORE_KINDS))
def stores(
    request: pytest.FixtureRequest, tmp_path: Path, clock: FakeClock
) -> Iterator[Stores]:
    store_kind: StoreKind = request.param
    made = store_kind.make(tmp_path, clock)
    yield made
    made.close()
