"""The one reading of whether a session is private."""

from typing import Any

import pytest

from lamplighter.core import session_is_private
from tests.run_history import History


def test_a_session_with_no_events_is_not_private() -> None:
    assert not session_is_private(())


def test_a_private_run_makes_its_session_private_for_good() -> None:
    history = History().start(private=True)
    assert session_is_private(history.events)
    history.answered()
    for place in range(2, 5):
        history.start(native_id=f"m{place}", run_id=f"run-{place}", private=False)
        history.answered()
    assert session_is_private(history.events)


@pytest.mark.parametrize("value", [False, "true", 1, None, "missing"])
def test_only_a_json_true_on_run_started_makes_a_run_private(value: Any) -> None:
    history = History()
    payload: dict[str, Any] = {"config_id": "c" * 64, "trigger": "message"}
    if value != "missing":
        payload["private"] = value
    history.add("run_started", payload)
    assert not session_is_private(history.events)


def test_a_private_flag_on_another_event_does_not_count() -> None:
    history = History().start()
    history.add("user_message", {"text": "Hello.", "private": True})
    history.chose()
    assert not session_is_private(history.events)
