import dataclasses
import json

import pytest
from model_samples import RECORDS

from lamplighter.ports import from_plain, to_plain


def _name(record: object) -> str:
    return type(record).__name__


@pytest.mark.parametrize("record", RECORDS, ids=_name)
def test_a_record_cannot_be_changed(record: object) -> None:
    assert dataclasses.is_dataclass(record)
    first_field = dataclasses.fields(record)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(record, first_field, "changed")


@pytest.mark.parametrize("record", RECORDS, ids=_name)
def test_a_record_survives_a_trip_through_json(record: object) -> None:
    text = json.dumps(to_plain(record))
    assert from_plain(type(record), json.loads(text)) == record
