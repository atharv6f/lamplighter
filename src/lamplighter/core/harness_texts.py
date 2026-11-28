"""Every fixed sentence the harness saves, sends to the model, or sends to you.

A test pins each one, so a change to a sentence is a change someone meant.
"""

from collections.abc import Sequence
from typing import Literal

# Why the loop answered a tool call itself, saved as `harness` on the result.
type HarnessKind = Literal[
    "arguments_unparsed",
    "unknown_tool",
    "arguments_invalid",
    "cut_off",
    "interrupted",
    "stopped",
    "out_of_time",
    "not_run",
    "unconfirmed",
]

# Why a change's outcome was not known when its result was written.
type UnsettledReason = Literal["interrupted", "stopped", "out_of_time", "unconfirmed"]
UNSETTLED_REASONS: tuple[UnsettledReason, ...] = (
    "interrupted",
    "stopped",
    "out_of_time",
    "unconfirmed",
)

type LimitName = Literal["steps", "seconds", "tokens", "micro_dollars"]

# The kinds of model failure that a second try can fix.
RETRYABLE_KINDS = frozenset({"transient", "throttled", "timeout", "unavailable"})


def arguments_unparsed(name: str, parse_error: str) -> str:
    """Tell the model its arguments for a call were not valid JSON."""
    return (
        f"The arguments for {name} were not valid JSON: {parse_error}."
        " Send the call again with valid JSON."
    )


def unknown_tool(name: str, tool_names: Sequence[str]) -> str:
    """Tell the model a tool does not exist, and list the real ones."""
    names = ", ".join(sorted(tool_names))
    return f"There is no tool named {name}. The tools are: {names}."


def arguments_invalid(name: str, problems: Sequence[str]) -> str:
    """Tell the model its arguments for a call do not fit the tool's schema."""
    return (
        f"The arguments for {name} do not fit its schema: {'; '.join(problems)}."
        " Send the call again with arguments that fit."
    )


def cut_off(name: str) -> str:
    """Tell the model a call was cut by the output limit and was not run."""
    return (
        f"The reply reached its output limit while {name} was being written, so the"
        " call was not run. Send it again with shorter arguments, or do the work"
        " in smaller steps."
    )


def interrupted(name: str) -> str:
    """Tell the model a call was cut off before its result was saved.

    The run may have been cut off by a kill or by a store that failed, so
    the sentence names neither. It is the one text for a read and a change.
    """
    return unsettled("interrupted", name, None)


def stopped(name: str) -> str:
    """Tell the model a call was cut short by your stop."""
    return unsettled("stopped", name, None)


def out_of_time(name: str) -> str:
    """Tell the model a call was cut short by the run's time limit."""
    return unsettled("out_of_time", name, None)


TOOK_EFFECT = "A check afterwards shows that it took effect."
NO_EFFECT = (
    "A check afterwards shows that it did not take effect: its target is as it was."
)
_OUTCOME_UNKNOWN = "Its outcome is unknown."
_MAY_HAVE_RUN = (
    "Its outcome is unknown: it may have finished, partly run, or not run at all."
)


def unsettled(
    reason: UnsettledReason, name: str, found: bool | None, failure: str | None = None
) -> str:
    """Say why a call's outcome was not known, and what a check found after.

    `found` is the check's answer, or None when there was no check or it
    could not tell. `failure` is the error text of a tool that failed and
    could not say whether it changed anything.
    """
    match reason:
        case "interrupted":
            opening = (
                f"The run was cut off before the result of {name} was saved,"
                " and the call was not run again."
            )
        case "stopped":
            opening = f"{name} was cut short because the user stopped the run."
        case "out_of_time":
            opening = f"{name} was cut short because the run reached its time limit."
        case "unconfirmed":
            opening = f"{failure} It could not say whether anything was changed."
    if found is True:
        return f"{opening} {TOOK_EFFECT}"
    if found is False:
        return f"{opening} {NO_EFFECT}"
    if reason == "interrupted":
        return f"{opening} {_MAY_HAVE_RUN}"
    return f"{opening} {_OUTCOME_UNKNOWN}"


def not_run(name: str) -> str:
    """Tell the model a call was never started because the run ended."""
    return f"{name} was not run, because the run ended first."


def tool_failed(name: str, kind: str, message: str) -> str:
    """Give the result text of a tool that returned a failure."""
    return f"{name} failed ({kind}): {message}"


# Added after a space to `stopped` or `out_of_time` when the tool did not stop.
DID_NOT_STOP = "It did not stop when asked and may still be running."

REPEAT_WARNING = (
    "Note from the harness: this call and its result are the same as the two"
    " before it. Making the same call again will end the run."
)

_REPEAT_ENDING = "\n\n" + REPEAT_WARNING


def with_repeat_warning(text: str) -> str:
    """Add the repeat warning to the end of a result, after a blank line."""
    return text + _REPEAT_ENDING


def without_repeat_warning(text: str) -> str:
    """Give a result's text as the tool gave it, without the repeat warning."""
    return text.removesuffix(_REPEAT_ENDING)


EMPTY_REPLY_NOTE = (
    "Note from the harness, not from the user: your last reply was empty."
    " Carry on with the task, or say plainly that you are done."
)

_RUN_END_NOTES = {
    "cancelled": "Note from the harness, not from the user: the user stopped the run"
    " above before it finished.",
    "limit": "Note from the harness, not from the user: the run above was ended by a"
    " limit before it finished.",
    "failed": "Note from the harness, not from the user: the run above ended on an"
    " error before it finished.",
}


def run_end_note(reason: str) -> str | None:
    """Give the note the model reads after a run that ended this way, or None."""
    return _RUN_END_NOTES.get(reason)


STOPPED_NOTICE = "Stopped."

_GO_ON = "The work so far is saved. Send a message to go on."


def limit_notice(limit: LimitName, allowed: int) -> str:
    """Tell you which limit ended the run and what its value is."""
    if limit == "micro_dollars":
        return f"The run stopped at its limit of ${dollars(allowed)}. {_GO_ON}"
    return f"The run stopped at its limit of {allowed} {limit}. {_GO_ON}"


def dollars(micro_dollars: int) -> str:
    """Give an amount in dollars with two decimal places, half a cent going up."""
    cents = (micro_dollars + 5_000) // 10_000
    return f"{cents // 100}.{cents % 100:02d}"


def model_failed_notice(kind: str, message: str) -> str:
    """Tell you the model call failed, and whether trying again may help."""
    notice = f"The run ended because the model call failed: {message}"
    if kind in RETRYABLE_KINDS:
        return notice + " Send a message to try again."
    return notice


# The failure the loop saves for a model call it had to cut at its deadline.
MODEL_OUT_OF_TIME = "The model did not answer in the time it was given."

EMPTY_REPLY_NOTICE = (
    "The run ended because the model sent an empty reply, and asking again did not"
    " help."
)

REFUSED_NOTICE = "The model declined to answer that."


def repeated_call_notice(name: str) -> str:
    """Tell you the run ended because the model kept repeating one call."""
    return (
        f"The run ended because the model kept making the same call to {name} and"
        " getting the same result."
    )


def no_model_notice(names: Sequence[str]) -> str:
    """Tell you no model in the settings is answering, so nothing was called.

    `names` are the names on the run's ladder.
    """
    return (
        f"No model in the settings is answering: {_listed(names)}. Check that the"
        " model servers are running and send your message again."
    )


def private_no_model_notice(names: Sequence[str]) -> str:
    """Tell you no model on this Mac is answering a private run, so nothing was sent.

    `names` are the names on the run's ladder after the privacy veto.
    """
    return (
        "This session is private, so it only uses models on this Mac, and none"
        f" of them is answering: {_listed(names)}. Nothing was sent off this Mac."
        " Start the local model server and send your message again."
    )


def _listed(names: Sequence[str]) -> str:
    return ", ".join(names) or "none"


CONFIG_CHANGED_NOTICE = (
    "The settings changed while this run was open, so it was ended. Send your"
    " message again."
)

# Added after a blank line to a reply that stopped at the output limit.
CUT_REPLY_LINE = "[The reply was cut off before the model finished.]"


def cut_mark(dropped: int) -> str:
    """Give the line that stands where a cut text's middle was left out."""
    return f"[... {dropped} characters left out here ...]"


def saved_note(total: int, dropped: int, saved_as: str) -> str:
    """Tell the model a result was cut, and the id of the whole text."""
    return (
        f"[The output was {total} characters long and {dropped} were left out."
        f" The full output is saved as {saved_as}. Read more of it with read_output.]"
    )


def lost_note(total: int, dropped: int, kind: str) -> str:
    """Tell the model a result was cut, and its whole text could not be saved."""
    return (
        f"[The output was {total} characters long and {dropped} were left out."
        f" The full output could not be saved ({kind}), so it cannot be read later.]"
    )
