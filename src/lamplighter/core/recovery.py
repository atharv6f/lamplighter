"""Lists the work a kill cut off, for the start of the next run to deal with."""

from dataclasses import dataclass

from lamplighter.ports import Effect, EffectLedger, EventLog, Failure, SessionInfo


@dataclass(frozen=True, slots=True)
class UnfinishedSession:
    """A session whose run was cut off, and its effects that are not settled."""

    session: SessionInfo
    effects: tuple[Effect, ...]


@dataclass(frozen=True, slots=True)
class UnfinishedWork:
    """Everything a start must look at before it begins any new run.

    `other_effects` holds unsettled effects of sessions that are not
    unfinished. An effect in "unknown" can outlive its run, for example
    while it waits for you to say whether a message went out.
    """

    sessions: tuple[UnfinishedSession, ...]
    other_effects: tuple[Effect, ...]


async def unfinished_work(
    log: EventLog, ledger: EffectLedger
) -> UnfinishedWork | Failure:
    """Give every unfinished session with its unsettled effects, and the rest."""
    sessions = await log.sessions("unfinished")
    if isinstance(sessions, Failure):
        return sessions
    effects = await ledger.unresolved()
    if isinstance(effects, Failure):
        return effects
    open_ids = {info.session_id for info in sessions}
    return UnfinishedWork(
        sessions=tuple(
            UnfinishedSession(
                session=info,
                effects=tuple(
                    e for e in effects if e.action.session_id == info.session_id
                ),
            )
            for info in sessions
        ),
        other_effects=tuple(e for e in effects if e.action.session_id not in open_ids),
    )
