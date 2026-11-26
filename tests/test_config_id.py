"""The config id: one hash over the model, prices, run settings and tools."""

import dataclasses

from lamplighter.core import Prices, RunSettings, config_id
from lamplighter.ports import ModelProfile, ToolSpec

MODEL = ModelProfile(
    adapter="anthropic",
    model="made-up-1",
    max_output=2_000,
    tool_calls=True,
    cache_hints=4,
    count_error_permille=50,
)
PRICES = Prices(
    input_uncached=4_000_000, cache_read=200_000, cache_write=5_000_000, output=1
)
RUN = RunSettings(
    system="You are a made-up helper.",
    max_steps=40,
    max_seconds=1_200,
    max_tokens=1_000_000,
    max_micro_dollars=1_000_000,
    model_max_output=1_000,
    model_deadline_ms=600_000,
    model_retries=2,
    cancel_bound_ms=800,
)
WAIT = ToolSpec(
    name="wait",
    description="Wait.",
    arguments_schema={
        "type": "object",
        "properties": {"seconds": {"type": "number", "maximum": 0.5}},
    },
)
# Worked out once from the saved JSON text of the four, checked by running
# sha256sum on that text, and pinned here, so a
# change to how the id is made shows up as a change to this value.
PINNED = "fd314cad16b1bf6079687bdfd43d81d545d783be4447158674a4e8e7c58f5787"


def test_the_id_of_known_settings_is_the_pinned_value() -> None:
    assert config_id(MODEL, PRICES, RUN, (WAIT,)) == PINNED


def test_a_change_to_any_of_the_four_gives_another_id() -> None:
    first = config_id(MODEL, PRICES, RUN, (WAIT,))
    changed = [
        config_id(dataclasses.replace(MODEL, max_output=2_001), PRICES, RUN, (WAIT,)),
        config_id(MODEL, dataclasses.replace(PRICES, output=2), RUN, (WAIT,)),
        config_id(MODEL, PRICES, dataclasses.replace(RUN, max_steps=41), (WAIT,)),
        config_id(MODEL, PRICES, dataclasses.replace(RUN, system="Hi."), (WAIT,)),
        config_id(MODEL, PRICES, RUN, ()),
        config_id(MODEL, PRICES, RUN, (dataclasses.replace(WAIT, name="pause"),)),
    ]
    assert first not in changed
    assert len(set(changed)) == len(changed)


def test_the_same_settings_always_give_the_same_id() -> None:
    again = RunSettings(**dataclasses.asdict(RUN))
    assert config_id(MODEL, PRICES, RUN, [WAIT]) == config_id(
        MODEL, PRICES, again, (WAIT,)
    )
