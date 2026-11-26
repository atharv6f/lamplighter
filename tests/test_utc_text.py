from datetime import UTC, datetime, timedelta, timezone

from lamplighter.ports import utc_text


def test_a_time_is_written_in_the_one_saved_form() -> None:
    moment = datetime(2026, 11, 26, 7, 35, 12, 345_000, tzinfo=UTC)
    assert utc_text(moment) == "2026-11-26T07:35:12.345Z"


def test_milliseconds_are_cut_and_never_rounded_up() -> None:
    moment = datetime(2026, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC)
    assert utc_text(moment) == "2026-12-31T23:59:59.999Z"


def test_a_time_in_another_zone_is_written_in_utc() -> None:
    pacific = timezone(timedelta(hours=-8))
    moment = datetime(2026, 11, 25, 23, 35, 12, 0, tzinfo=pacific)
    assert utc_text(moment) == "2026-11-26T07:35:12.000Z"


def test_every_time_has_the_same_length_so_text_order_is_time_order() -> None:
    early = datetime(2026, 1, 2, 3, 4, 5, 6_000, tzinfo=UTC)
    late = datetime(2026, 11, 26, 7, 35, 12, 345_000, tzinfo=UTC)
    assert len(utc_text(early)) == len(utc_text(late)) == 24
    assert utc_text(early) < utc_text(late)
