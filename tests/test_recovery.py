import asyncio

from lamplighter.adapters.in_memory import InMemoryEffectLedger, InMemoryEventLog
from lamplighter.core import UnfinishedWork, unfinished_work
from lamplighter.ports import Begun, NewEvent
from tests.fake_clock import FakeClock
from tests.store_samples import DONE, TIMED_OUT, action

AT = "2026-11-26T07:35:12.345Z"


def mark(kind: str) -> NewEvent:
    return NewEvent(type=kind, v=1, payload={}, at=AT, run_id="run-1")


async def listing() -> UnfinishedWork:
    log = InMemoryEventLog({}, 2_000)
    ledger = InMemoryEffectLedger(FakeClock(), 2_000)
    await log.append("cut-off", [mark("run_started")], 0)
    await log.append("finished", [mark("run_started"), mark("run_end")], 0)
    await log.append("quiet", [mark("run_started"), mark("run_end")], 0)
    began = [
        await ledger.begin(action("cut-off", call_index=0), None, None),
        await ledger.begin(action("cut-off", call_index=1), None, None),
        await ledger.begin(action("finished", call_index=0), None, None),
        await ledger.begin(action("quiet", call_index=0), None, None),
    ]
    ids = [b.effect.effect_id for b in began if isinstance(b, Begun)]
    await ledger.finish(ids[1], DONE)
    await ledger.finish(ids[2], TIMED_OUT)
    await ledger.finish(ids[3], DONE)
    work = await unfinished_work(log, ledger)
    assert isinstance(work, UnfinishedWork), work
    return work


def test_an_unfinished_session_is_listed_with_its_unsettled_effects() -> None:
    work = asyncio.run(listing())
    (session,) = work.sessions
    assert session.session.session_id == "cut-off"
    assert session.session.state == "unfinished"
    assert [(e.action.call_index, e.state) for e in session.effects] == [
        (0, "intended")
    ]


def test_an_unsettled_effect_of_a_finished_session_is_listed_on_its_own() -> None:
    work = asyncio.run(listing())
    assert [(e.action.session_id, e.state) for e in work.other_effects] == [
        ("finished", "unknown")
    ]


def test_nothing_is_listed_when_nothing_was_cut_off() -> None:
    async def empty() -> object:
        return await unfinished_work(
            InMemoryEventLog({}, 2_000), InMemoryEffectLedger(FakeClock(), 2_000)
        )

    assert asyncio.run(empty()) == UnfinishedWork(sessions=(), other_effects=())
