"""Runs every contract test once for each kind of model client."""

import pytest

from tests.contract.model_clients import MAKERS, ClientMaker


@pytest.fixture(params=list(MAKERS.values()), ids=list(MAKERS))
def make_client(request: pytest.FixtureRequest) -> ClientMaker:
    return request.param
