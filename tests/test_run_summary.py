"""The sum of a session's newest run, which agrees with the day report."""

import re

from lamplighter.core import DayReport, TokenCounts, run_summary
from lamplighter.ports import Event, Failure, TextPart, Usage
from tests.span_kit import MadeUpLog, tool_call

TIMEOUT = Failure(kind="timeout", effect="unknown", message="Made up.", source="t")
DAY = "2026-11-26"


def three_runs() -> MadeUpLog:
    log = MadeUpLog()
    first = log.run("sess-a", "run-1")
    first.start()
    note, other = tool_call("c1", "note", n=1), tool_call("c2", "note", n=2)
    first.request()
    first.reply(note, other, reason="tool_calls", usage=Usage(300, 120, 30, 60))
    first.ran(note)
    first.ran(other)
    first.request()
    first.reply(TextPart(text="All done."), cost=1_000)
    first.end()
    second = log.run("sess-a", "run-2")
    second.start()
    second.request()
    second.reply(usage=None, cost=None, error=TIMEOUT, reported=None)
    second.end("failed", "model_timeout")
    log.run("sess-b", "run-3").start()
    return log


def report_line(events: list[Event], start: str) -> str:
    counts = DayReport(DAY)
    for event in events:
        counts.count(event)
    return next(line for line in counts.lines() if line.startswith(start))


def test_the_newest_run_of_a_session_is_summed() -> None:
    events = [event for event in three_runs().events if event.session_id == "sess-a"]
    summary = run_summary(events)
    assert summary is not None
    assert (summary.run_id, summary.reason, summary.detail) == (
        "run-2",
        "failed",
        "model_timeout",
    )
    assert (summary.steps, summary.tool_calls, summary.micro_dollars) == (1, 0, None)
    assert summary.tokens == TokenCounts(0, 0, 0, 0)


def test_the_counts_of_a_run_equal_those_of_the_day_report() -> None:
    log = three_runs()
    first = [event for event in log.events if event.run_id in (None, "run-1")]
    summary = run_summary(first)
    assert summary is not None and summary.reason == "done"
    tokens = summary.tokens
    assert report_line(first, "Steps: ") == f"Steps: {summary.steps}"
    assert report_line(first, "Tool calls: ") == f"Tool calls: {summary.tool_calls}"
    assert report_line(first, "Tokens in: ") == (
        f"Tokens in: {tokens.input} ({tokens.uncached} not cached, {tokens.read}"
        f" read from the cache, {tokens.written} written to the cache)"
    )
    assert report_line(first, "Tokens out: ") == f"Tokens out: {tokens.output}"
    cost = re.fullmatch(r"Cost: \$(\d+)\.(\d{6})", report_line(first, "Cost: "))
    assert cost is not None
    assert int(cost.group(1)) * 1_000_000 + int(cost.group(2)) == summary.micro_dollars
    assert (summary.steps, summary.tool_calls) == (2, 2)


def test_a_run_that_has_not_ended_has_no_reason() -> None:
    summary = run_summary(
        [event for event in three_runs().events if event.session_id == "sess-b"]
    )
    assert summary is not None and summary.run_id == "run-3"
    assert (summary.reason, summary.detail, summary.steps) == (None, None, 0)
    assert summary.micro_dollars == 0


def test_events_with_no_run_give_nothing() -> None:
    assert run_summary([]) is None
