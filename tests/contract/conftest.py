"""Runs every contract test once for each kind of model client."""

import pytest

from tests.contract.model_clients import MAKERS, ClientMaker


@pytest.fixture(params=MAKERS.values(), ids=MAKERS.keys())
def make_client(request: pytest.FixtureRequest) -> ClientMaker:
    return request.param
