"""Counts the spans a log promises, without the function that makes them.

The count comes from the rules of the four spans alone, so a test that
compares it with what the span function gave finds a span that is missing
or one that was made up.
"""

from collections import defaultdict
from collections.abc import Sequence

from lamplighter.ports import Event


def promised_spans(events: Sequence[Event]) -> int:
    """Give the number of spans that are whole in `events`, a log in order.

    A run is told apart by its session id and its run id together, because
    two sessions may use one run id. Each ended run counts one, each tool
    result of a step that has its request one, each request that is not its
    run's last event one, and each request that a later request or the run's
    end follows one more.
    """
    runs: defaultdict[tuple[str, str], list[Event]] = defaultdict(list[Event])
    for event in events:
        if event.run_id is not None:
            runs[(event.session_id, event.run_id)].append(event)
    return sum(_run_count(run) for run in runs.values())


def _run_count(run: Sequence[Event]) -> int:
    if not any(event.type == "run_started" for event in run):
        return 0
    asked = {event.step for event in run if event.type == "model_request"}
    count = sum(1 for event in run if event.type == "run_end")
    count += sum(
        1 for event in run if event.type == "tool_result" and event.step in asked
    )
    for index, event in enumerate(run):
        if event.type != "model_request":
            continue
        later = [after.type for after in run[index + 1 :]]
        count += bool(later)
        count += "model_request" in later or "run_end" in later
    return count
