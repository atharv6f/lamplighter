"""The rule for a model that makes the same call again and again.

Two calls are the same when the tool name, the arguments and the result
text are all equal. A tool asked the same thing that answers something new
is making progress and is never stopped. The third such call in a row gets
a warning at the end of its result. Once the model has seen that warning,
the next such call ends the run.
"""

from lamplighter.core.run_view import CallRecord, RunView


def deserves_warning(view: RunView, index: int, result: str) -> bool:
    """Say whether the result of the call at `index` should carry the warning.

    It should when the two calls before it, in the run's order and across
    replies, are the same call with the same result.
    """
    if index < 2:
        return False
    this = view.calls[index]
    before = view.calls[index - 2 : index]
    return all(_same(call, this, result) for call in before)


def ends_the_run(view: RunView, index: int) -> bool:
    """Say whether the call at `index` must not be run, and ends the run.

    It does when the three calls before it are the same call with the same
    result, and the newest of them, which carries the warning, belongs to
    an earlier reply. The calls of one reply are all written before any of
    their results exist, so they are never held against the model.
    """
    if index < 3:
        return False
    this = view.calls[index]
    before = view.calls[index - 3 : index]
    first_result = before[0].result
    if first_result is None:
        return False
    return (
        all(_same(call, this, first_result) for call in before)
        and before[-1].reply_seq < this.reply_seq
    )


def _same(call: CallRecord, this: CallRecord, result: str) -> bool:
    return call.key == this.key and call.result == result
