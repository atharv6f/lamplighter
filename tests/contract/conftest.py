"""Runs every contract test once for each kind of model client."""

import pytest

from tests.contract.model_clients import KINDS, ClientKind


@pytest.fixture(params=list(KINDS.values()), ids=list(KINDS))
def kind(request: pytest.FixtureRequest) -> ClientKind:
    return request.param
