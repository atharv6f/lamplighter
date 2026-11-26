import pytest

from lamplighter.ports import Failure, from_plain, to_plain
from tests.model_samples import THROTTLED


def test_a_wait_time_is_only_allowed_when_throttled() -> None:
    with pytest.raises(ValueError, match="throttled"):
        Failure(
            kind="transient",
            effect="none",
            message="The connection dropped.",
            source="ModelClient/scripted",
            retry_after_ms=500,
        )


def test_a_failure_keeps_the_failure_that_caused_it_through_json() -> None:
    again = from_plain(Failure, to_plain(THROTTLED))
    assert again.cause is not None
    assert again.cause.kind == "transient"
    assert again.retry_after_ms == 2_000


def test_a_kind_outside_the_list_is_rejected() -> None:
    plain = to_plain(THROTTLED)
    assert isinstance(plain, dict)
    plain["kind"] = "grumpy"
    with pytest.raises(ValueError, match="grumpy"):
        from_plain(Failure, plain)


def test_a_new_failure_has_no_wait_no_detail_and_no_cause() -> None:
    failure = Failure(
        kind="fatal",
        effect="none",
        message="A promise broke.",
        source="ModelClient/scripted",
    )
    assert failure.retry_after_ms is None
    assert failure.detail == {}
    assert failure.cause is None
    assert failure.v == 1
