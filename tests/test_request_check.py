import dataclasses

from lamplighter.ports import CacheHint, check_request
from tests.model_samples import PROFILE, REQUEST

FOUR_HINTS = dataclasses.replace(PROFILE, cache_hints=4, adapter="made-up")


def test_a_request_the_profile_can_serve_passes() -> None:
    assert check_request(FOUR_HINTS, REQUEST) is None


def test_tools_fail_when_the_profile_takes_none() -> None:
    failure = check_request(dataclasses.replace(FOUR_HINTS, tool_calls=False), REQUEST)
    assert failure is not None
    assert (failure.kind, failure.effect) == ("invalid_input", "none")
    assert failure.source == "ModelClient/made-up"


def test_no_tools_pass_when_the_profile_takes_none() -> None:
    no_tools = dataclasses.replace(REQUEST, tools=())
    profile = dataclasses.replace(FOUR_HINTS, tool_calls=False)
    assert check_request(profile, no_tools) is None


def test_the_output_limit_must_be_at_least_one() -> None:
    assert (
        check_request(FOUR_HINTS, dataclasses.replace(REQUEST, max_output=0))
        is not None
    )
    assert check_request(FOUR_HINTS, dataclasses.replace(REQUEST, max_output=1)) is None


def test_the_output_limit_may_reach_the_profile_limit_and_no_further() -> None:
    limit = FOUR_HINTS.max_output
    assert (
        check_request(FOUR_HINTS, dataclasses.replace(REQUEST, max_output=limit))
        is None
    )
    over = dataclasses.replace(REQUEST, max_output=limit + 1)
    assert check_request(FOUR_HINTS, over) is not None


def test_one_cache_hint_too_many_fails() -> None:
    four = tuple(CacheHint("message", i % 3) for i in range(4))
    assert (
        check_request(FOUR_HINTS, dataclasses.replace(REQUEST, cache_hints=four))
        is None
    )
    five = (*four, CacheHint("system", 0))
    assert (
        check_request(FOUR_HINTS, dataclasses.replace(REQUEST, cache_hints=five))
        is not None
    )


def test_a_cache_hint_must_point_at_something() -> None:
    for hint in (
        CacheHint("system", 2),
        CacheHint("message", 3),
        CacheHint("message", -1),
    ):
        request = dataclasses.replace(REQUEST, cache_hints=(hint,))
        assert check_request(FOUR_HINTS, request) is not None, hint
    for hint in (CacheHint("system", 1), CacheHint("message", 2)):
        request = dataclasses.replace(REQUEST, cache_hints=(hint,))
        assert check_request(FOUR_HINTS, request) is None, hint


def test_cache_hints_are_not_checked_when_the_profile_honours_none() -> None:
    wild = tuple(CacheHint("message", 99) for _ in range(9))
    assert (
        check_request(PROFILE, dataclasses.replace(REQUEST, cache_hints=wild)) is None
    )
