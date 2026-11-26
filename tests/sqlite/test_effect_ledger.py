"""What the SQLite effect ledger does beyond the port's contract tests."""

import sqlite3
from pathlib import Path

import pytest

from lamplighter.adapters.sqlite import SqliteEffectLedger
from lamplighter.ports import Begun, Failure
from tests.fake_clock import FakeClock
from tests.sqlite.helpers import failure_of, opened, run
from tests.store_samples import DONE, action


def ledger_on(connection: sqlite3.Connection) -> SqliteEffectLedger:
    return SqliteEffectLedger(connection, FakeClock(), 2_000)


def begun(result: Begun | Failure) -> Begun:
    assert isinstance(result, Begun), result
    return result


def test_a_saved_action_missing_a_field_with_a_default_is_still_the_same(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")
    ledger = ledger_on(connection)
    run(ledger.begin(action(), None, None))
    # As older code would have saved it, before the field existed.
    connection.execute("UPDATE effects SET action = json_remove(action, '$.v')")
    again = begun(run(ledger.begin(action(), None, None)))
    assert not again.is_new
    assert again.effect.action == action()


def test_a_saved_action_with_a_field_this_code_does_not_know_is_still_the_same(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")
    ledger = ledger_on(connection)
    run(ledger.begin(action(), None, None))
    connection.execute(
        "UPDATE effects SET action = json_set(action, '$.added_later', 1)"
    )
    assert not begun(run(ledger.begin(action(), None, None))).is_new


@pytest.mark.parametrize(
    "planted",
    [
        "UPDATE effects SET state = 'unknown',"
        """ result = '{"outcome": "maybe", "output": {}}'""",
        "UPDATE effects SET action = '[1]'",
        "UPDATE effects SET action = json_remove(action, '$.tool')",
        "UPDATE effects SET probe = 'not json'",
        "UPDATE effects SET undo = '[1]'",
    ],
)
def test_a_row_that_does_not_fit_is_reported_as_damage(
    tmp_path: Path, planted: str
) -> None:
    connection = opened(tmp_path / "state.db")
    ledger = ledger_on(connection)
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    connection.execute(planted)
    for answer in (
        run(ledger.unresolved()),
        run(ledger.finish(effect_id, DONE)),
        run(ledger.begin(action(), None, None)),
    ):
        failure = failure_of(answer)
        assert (failure.kind, failure.effect) == ("fatal", "none")


def test_an_effect_id_is_never_used_again_after_the_newest_row_is_deleted(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")
    ledger = ledger_on(connection)
    first = begun(run(ledger.begin(action(call_index=0), None, None))).effect
    connection.execute("DELETE FROM effects")
    second = begun(run(ledger.begin(action(call_index=1), None, None))).effect
    assert second.effect_id > first.effect_id


def test_the_session_of_an_effect_is_saved_beside_it(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    run(ledger_on(connection).begin(action(session_id="sess-z"), None, None))
    assert connection.execute("SELECT session_id FROM effects").fetchall() == [
        ("sess-z",)
    ]


def test_a_state_this_code_does_not_know_is_reported_as_damage(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")
    ledger = ledger_on(connection)
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    connection.execute("UPDATE effects SET state = 'paused'")
    assert failure_of(run(ledger.finish(effect_id, DONE))).kind == "fatal"
    assert failure_of(run(ledger.begin(action(), None, None))).kind == "fatal"
