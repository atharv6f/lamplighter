"""The config id: one hash over the model, prices, run, tools, their settings, wall."""

import dataclasses

from lamplighter.core import Prices, RunSettings, config_id
from lamplighter.ports import JsonObject, ModelProfile, ToolEntry, ToolSpec

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
ENTRY = ToolEntry(spec=WAIT, action_class="read", output_cap=30_000, taints=False)
TOOL_SETTINGS: JsonObject = {
    "work_dir": "/made-up/work",
    "scratch_dir": "/made-up/scratch",
    "shell_path": "/usr/bin:/bin",
    "shell_default_seconds": 120,
    "shell_max_seconds": 600,
    "output_cap_chars": 30_000,
    "fetch_max_bytes": 5_242_880,
    "fetch_seconds": 30,
}
WALL: JsonObject = {"adapter": "seatbelt", "profile_sha256": "a" * 64}
# Worked out once from the saved JSON text of the six, checked by running
# sha256sum on that text, and pinned here, so a change to how the id is
# made shows up as a change to this value.
PINNED = "4f06cb3ccacebcf2374bf501d738ce6a1c2ad95b9e5126b499b8df1c29a3d839"


def made(
    model: ModelProfile = MODEL,
    prices: Prices = PRICES,
    run: RunSettings = RUN,
    tools: tuple[ToolEntry, ...] = (ENTRY,),
    tool_settings: JsonObject = TOOL_SETTINGS,
    wall: JsonObject = WALL,
) -> str:
    return config_id(model, prices, run, tools, tool_settings, wall)


def test_the_id_of_known_settings_is_the_pinned_value() -> None:
    assert made() == PINNED


def test_a_change_to_any_of_the_six_gives_another_id() -> None:
    changed = [
        made(model=dataclasses.replace(MODEL, max_output=2_001)),
        made(prices=dataclasses.replace(PRICES, output=2)),
        made(run=dataclasses.replace(RUN, max_steps=41)),
        made(run=dataclasses.replace(RUN, system="Hi.")),
        made(tools=()),
        made(
            tools=(
                dataclasses.replace(
                    ENTRY, spec=dataclasses.replace(WAIT, name="pause")
                ),
            )
        ),
        made(tools=(dataclasses.replace(ENTRY, action_class="irreversible"),)),
        made(tools=(dataclasses.replace(ENTRY, output_cap=26_000),)),
        made(tools=(dataclasses.replace(ENTRY, taints=True),)),
        made(tool_settings={**TOOL_SETTINGS, "work_dir": "/made-up/moved"}),
        made(wall={**WALL, "os_version": "26.5.1"}),
        made(wall={**WALL, "profile_sha256": "b" * 64}),
    ]
    assert made() not in changed
    assert len(set(changed)) == len(changed)


def test_the_same_settings_always_give_the_same_id() -> None:
    again = RunSettings(**dataclasses.asdict(RUN))
    assert made(run=again) == made()
