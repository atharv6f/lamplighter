"""The effect ledger on the store's SQLite file."""

import sqlite3
from typing import Any

from lamplighter.adapters.sqlite.errors import damaged
from lamplighter.adapters.sqlite.transaction import read, write
from lamplighter.ports import (
    Action,
    Begun,
    Clock,
    Effect,
    EffectResult,
    Failure,
    JsonObject,
    Outcome,
    as_saved,
    check_begin,
    check_finish,
    check_resolve,
    from_stored,
    read_saved_json,
    saved_json,
    unknown_effect,
    utc_text,
)

_SOURCE = "EffectLedger/sqlite"

_COLUMNS = (
    "effect_id, effect_key, v, state, action, probe, undo, result, resolution,"
    " begun_at, finished_at, resolved_at"
)

# The version of the Effect record this code writes.
_EFFECT_V = 1

_INSERT = (
    "INSERT INTO effects (effect_key, v, session_id, state, action, probe, undo,"
    " begun_at) VALUES (?, ?, ?, 'intended', ?, ?, ?, ?)"
)


class SqliteEffectLedger:
    """Keeps effects in the store's effects table. Satisfies EffectLedger.

    It shares the connection that `open_database` gave, and runs each call as
    one transaction on the caller's thread, so calls never interleave.
    """

    def __init__(
        self, connection: sqlite3.Connection, clock: Clock, max_record_bytes: int
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._max_record_bytes = max_record_bytes

    async def begin(
        self, action: Action, probe: JsonObject | None, undo: JsonObject | None
    ) -> Begun | Failure:
        """Save the intent to run an action, or find the one already saved."""
        refused = check_begin(action, probe, undo, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        values = (
            action.effect_key,
            _EFFECT_V,
            action.session_id,
            saved_json(action),
            _saved_or_none(probe),
            _saved_or_none(undo),
            utc_text(self._clock.now()),
        )

        def body(connection: sqlite3.Connection) -> Begun | Failure:
            # Look first: an insert that meets its key still uses up an id
            # in SQLite, even with "do nothing", and the ids would have gaps.
            saved = _effect_with_key(connection, action.effect_key)
            if saved is None:
                connection.execute(_INSERT, values)
                made = _effect_with_key(connection, action.effect_key)
                if made is None:
                    raise ValueError("a row just inserted is there")
                return made if isinstance(made, Failure) else Begun(made, is_new=True)
            if isinstance(saved, Failure):
                return saved
            if saved.action != as_saved(Action, action):
                return _conflict("A different action is saved under this key.")
            return Begun(effect=saved, is_new=False)

        return write(self._connection, _SOURCE, body)

    async def finish(self, effect_id: int, result: EffectResult) -> None | Failure:
        """Save how an intended action ended."""
        refused = check_finish(result, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        saved_result = as_saved(EffectResult, result)
        finished_at = utc_text(self._clock.now())

        def body(connection: sqlite3.Connection) -> None | Failure:
            effect = _effect_with_id(connection, effect_id)
            if effect is None:
                return unknown_effect(effect_id, _SOURCE)
            if isinstance(effect, Failure):
                return effect
            if effect.state != "intended":
                if effect.result == saved_result:
                    return None
                return _conflict(f"The effect is {effect.state} with another result.")
            changed = connection.execute(
                "UPDATE effects SET state = ?, result = ?, undo = COALESCE(?, undo),"
                " finished_at = ? WHERE effect_id = ? AND state = 'intended'",
                (
                    saved_result.outcome,
                    saved_json(saved_result),
                    _saved_or_none(saved_result.undo),
                    finished_at,
                    effect_id,
                ),
            ).rowcount
            return _one_row_changed(changed)

        return write(self._connection, _SOURCE, body)

    async def resolve(self, effect_id: int, outcome: Outcome) -> None | Failure:
        """Save how an effect whose outcome was unknown was settled."""
        refused = check_resolve(outcome, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        saved_outcome = as_saved(Outcome, outcome)
        resolved_at = utc_text(self._clock.now())

        def body(connection: sqlite3.Connection) -> None | Failure:
            effect = _effect_with_id(connection, effect_id)
            if effect is None:
                return unknown_effect(effect_id, _SOURCE)
            if isinstance(effect, Failure):
                return effect
            if effect.state == "resolved" and effect.resolution == saved_outcome:
                return None
            if effect.state != "unknown":
                return _conflict(
                    f"Only an unknown effect is resolved, not {effect.state}."
                )
            changed = connection.execute(
                "UPDATE effects SET state = 'resolved', resolution = ?,"
                " resolved_at = ? WHERE effect_id = ? AND state = 'unknown'",
                (saved_json(saved_outcome), resolved_at, effect_id),
            ).rowcount
            return _one_row_changed(changed)

        return write(self._connection, _SOURCE, body)

    async def unresolved(self) -> tuple[Effect, ...] | Failure:
        """Give every effect that is intended or unknown, oldest first."""
        rows = read(
            self._connection,
            _SOURCE,
            lambda connection: connection.execute(
                f"SELECT {_COLUMNS} FROM effects"
                " WHERE state IN ('intended', 'unknown') ORDER BY effect_id"
            ).fetchall(),
        )
        if isinstance(rows, Failure):
            return rows
        effects: list[Effect] = []
        for row in rows:
            effect = _parsed(row)
            if isinstance(effect, Failure):
                return effect
            effects.append(effect)
        return tuple(effects)


def _effect_from_row(row: Any) -> Effect:
    # Raises ValueError when the row does not fit, which is damage.
    names = [name.strip() for name in _COLUMNS.split(",")]
    plain = dict(zip(names, row, strict=True))
    for column in ("action", "probe", "undo", "result", "resolution"):
        if plain[column] is not None:
            plain[column] = read_saved_json(plain[column])
    return from_stored(Effect, plain)


def _effect_with_id(
    connection: sqlite3.Connection, effect_id: int
) -> Effect | Failure | None:
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM effects WHERE effect_id = ?", (effect_id,)
    ).fetchone()
    return None if row is None else _parsed(row)


def _effect_with_key(
    connection: sqlite3.Connection, effect_key: str
) -> Effect | Failure | None:
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM effects WHERE effect_key = ?", (effect_key,)
    ).fetchone()
    return None if row is None else _parsed(row)


def _parsed(row: Any) -> Effect | Failure:
    try:
        return _effect_from_row(row)
    except ValueError:
        return damaged(f"The effect with id {row[0]} does not fit.", _SOURCE)


def _saved_or_none(value: JsonObject | None) -> str | None:
    return None if value is None else saved_json(value)


def _one_row_changed(changed: int) -> None:
    # The row was read in this same transaction under the write lock, so it
    # cannot have moved since. Anything else is a bug.
    if changed != 1:
        raise AssertionError(f"expected one row to change, not {changed}")


def _conflict(message: str) -> Failure:
    return Failure(kind="conflict", effect="none", message=message, source=_SOURCE)
