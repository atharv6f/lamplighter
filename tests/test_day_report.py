"""The daily report: every line on a made-up day, and the edges of a day."""

from datetime import datetime, timedelta

from lamplighter.core import DayReport
from lamplighter.ports import Event, Failure, TextPart
from tests.span_kit import START, MadeUpLog, MadeUpRun, tool_call

DAY = "2026-11-26"
TIMEOUT = Failure(kind="timeout", effect="unknown", message="Made up.", source="t")


def made_up_day() -> MadeUpLog:
    """Three runs: one that went well, one whose call timed out, one cut short."""
    log = MadeUpLog()
    first = log.run("sess-a", "run-1")
    first.start()
    note, broken = tool_call("c1", "note", n=1), tool_call("c2", "note", n=2)
    first.request()
    first.reply(note, broken, reason="tool_calls", duration_ms=1_200)
    first.ran(note)
    first.intent(broken)
    first.result(broken, is_error=True)
    first.request()
    first.reply(TextPart(text="All done."), duration_ms=2_000)
    first.end()
    second = log.run("sess-a", "run-2")
    second.start()
    second.request()
    second.reply(
        usage=None, cost=None, error=TIMEOUT, reported=None, duration_ms=600_000
    )
    second.end("failed", "model_timeout")
    third = log.run("sess-b", "run-3")
    third.start()
    # A request saved before the model was saved on it.
    third.request(model=None, provider=None)
    third.reply(TextPart(text="Old."), cost=None, reported="made-up-0")
    third.request()
    third.end("limit", "seconds")
    return log


def report(events: list[Event], day: str = DAY) -> list[str]:
    counts = DayReport(day)
    for event in events:
        counts.count(event)
    return counts.lines()


def test_a_made_up_day_gives_every_line_of_the_report() -> None:
    assert report(made_up_day().events) == [
        "Day 2026-11-26, 00:00 to 24:00 UTC",
        "Runs ended: 3 (1 done, 1 failed, 1 limit)",
        "Steps: 5",
        "Tool calls: 2",
        "Tokens in: 450 (300 not cached, 120 read from the cache,"
        " 30 written to the cache)",
        "Tokens out: 60",
        "Cache hit rate: 26.7%",
        "Cost: $0.002468 (2 calls with no price)",
        "By model:",
        "  made-up-0: 1 call, 0.1 s, in 100 + 40 read + 10 written, out 20, no price",
        "  made-up-1: 3 calls, 603.2 s, in 200 + 80 read + 20 written, out 40,"
        " $0.002468 (1 call with no price)",
        "Model calls with no saved reply: 1. The provider may have billed them."
        " They are in no sum above. A call that is running right now is counted"
        " here too.",
        "Replies with a token count missing: 1. They are in no token sum above.",
        "Errors by kind:",
        "  model timeout: 1",
        "  run failed: model_timeout: 1",
        "  run limit: seconds: 1",
        "  tool tool_error: 1",
        "Runs:",
        "  07:35:13 done, 2 steps, 2 tool calls, cache 26.7%, $0.002468,"
        " session sess-a run run-1",
        "  07:35:13 failed: model_timeout, 1 step, 0 tool calls, cache none,"
        " no price, session sess-a run run-2",
        "  07:35:14 limit: seconds, 2 steps, 0 tool calls, cache 26.7%,"
        " no price, session sess-b run run-3",
    ]


def test_a_day_with_no_events_says_so_on_every_line() -> None:
    assert report(made_up_day().events, "2026-11-27") == [
        "Day 2026-11-27, 00:00 to 24:00 UTC",
        "Runs ended: 0",
        "Steps: 0",
        "Tool calls: 0",
        "Tokens in: 0 (0 not cached, 0 read from the cache, 0 written to the cache)",
        "Tokens out: 0",
        "Cache hit rate: none",
        "Cost: $0.000000",
        "By model: none",
        "Errors by kind: none",
        "Runs: none",
    ]


def at(text: str) -> int:
    """Give a time of day in UTC as ms after the kit's start."""
    moment = datetime.fromisoformat(text + "+00:00")
    return (moment - START) // timedelta(milliseconds=1)


def across_midnight() -> MadeUpLog:
    """A run from before midnight to after it, with a call that spans it."""
    log = MadeUpLog()
    run = log.run()
    run.start(at_ms=at("2026-11-25T23:59:58"))
    run.request(at_ms=at("2026-11-25T23:59:58.500"))
    run.reply(TextPart(text="Before."), at_ms=at("2026-11-25T23:59:59"))
    run.request(at_ms=at("2026-11-25T23:59:59.500"))
    run.reply(TextPart(text="After."), cost=5_000, at_ms=at("2026-11-26T00:00:01"))
    run.end(at_ms=at("2026-11-26T00:00:02"))
    return log


def test_a_run_line_counts_the_whole_run_and_the_day_only_its_own_events() -> None:
    lines = report(across_midnight().events)

    assert "Steps: 0" in lines
    assert "Cost: $0.005000" in lines
    assert lines[-2:] == [
        "Runs:",
        "  00:00:02 done, 2 steps, 0 tool calls, cache 26.7%, $0.006234,"
        " session sess-a run run-1",
    ]


def test_a_call_across_midnight_is_a_step_of_one_day_and_a_reply_of_the_next() -> None:
    first = report(across_midnight().events, "2026-11-25")
    second = report(across_midnight().events, "2026-11-26")

    assert "Steps: 2" in first
    assert [line for line in first if line.startswith("  made-up-1")] == [
        "  made-up-1: 1 call, 0.1 s, in 100 + 40 read + 10 written, out 20, $0.001234"
    ]
    # Its reply came, so it is no call with no saved reply on either day.
    assert not any(line.startswith("Model calls with no") for line in first + second)
    assert [line for line in second if line.startswith("  made-up-1")] == [
        "  made-up-1: 1 call, 0.1 s, in 100 + 40 read + 10 written, out 20, $0.005000"
    ]
    assert "Runs: none" in first


def one_run(log: MadeUpLog) -> MadeUpRun:
    run = log.run()
    run.start()
    return run


def test_a_day_whose_replies_all_lack_a_cost_says_no_price() -> None:
    log = MadeUpLog()
    run = one_run(log)
    for _ in range(2):
        run.request()
        run.reply(TextPart(text="Free."), cost=None)
    run.end()

    lines = report(log.events)
    assert "Cost: no price" in lines
    row = "  made-up-1: 2 calls, 0.2 s, in 200 + 80 read + 20 written, out 40, no price"
    assert row in lines


def test_a_day_whose_replies_all_have_a_cost_gives_the_dollars_alone() -> None:
    log = MadeUpLog()
    run = one_run(log)
    run.request()
    run.reply(TextPart(text="Paid."), cost=2_500_000)
    run.end()

    assert "Cost: $2.500000" in report(log.events)


def test_a_reported_name_that_fails_the_check_is_shown_as_unknown() -> None:
    marker = "made up; rm -rf"
    log = MadeUpLog()
    run = one_run(log)
    run.request(model=None, provider=None)
    run.reply(TextPart(text="Old."), reported=marker)
    run.end()

    lines = report(log.events)
    assert any(line.startswith("  unknown: 1 call,") for line in lines)
    assert not any(marker in line for line in lines)


def test_a_failed_reply_that_reports_no_model_is_in_its_requested_models_row() -> None:
    log = MadeUpLog()
    run = one_run(log)
    run.request()
    run.reply(TextPart(text="Fine."))
    run.request()
    run.reply(error=TIMEOUT, reported=None, usage=None, cost=None)
    run.end("failed", "model_timeout")

    rows = [line for line in report(log.events) if line.startswith("  made-up-1")]
    assert rows == [
        "  made-up-1: 2 calls, 0.2 s, in 100 + 40 read + 10 written, out 20,"
        " $0.001234 (1 call with no price)"
    ]
