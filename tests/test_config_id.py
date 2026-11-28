"""The config ids: one hash over the six parts of a run, and one without its place."""

import dataclasses
from dataclasses import dataclass

from lamplighter.core import (
    NamedModel,
    Prices,
    Route,
    RunSettings,
    config_id,
    portable_config_id,
)
from lamplighter.ports import JsonObject, ModelProfile, ToolEntry, ToolSpec


@dataclass(frozen=True, slots=True)
class LocalEntry:
    """A made-up entry for a model on this Mac, with fields the core never reads."""

    name: str
    profile: ModelProfile
    weights_sha256: str
    server: str


MODEL = ModelProfile(
    adapter="anthropic",
    model="made-up-1",
    max_output=2_000,
    tool_calls=True,
    cache_hints=4,
    count_error_permille=50,
    window=200_000,
    concurrent_requests=4,
    stays_on_mac=False,
)
PRICES = Prices(
    input_uncached=4_000_000, cache_read=200_000, cache_write=5_000_000, output=1
)
LOCAL = LocalEntry(
    name="local",
    profile=dataclasses.replace(
        MODEL,
        adapter="openai_chat",
        model="local",
        cache_hints=0,
        window=65_536,
        stays_on_mac=True,
    ),
    weights_sha256="0" * 64,
    server="llama.cpp b12826",
)
MODELS: tuple[object, ...] = (NamedModel("frontier", MODEL, PRICES), LOCAL)
ROUTES = (
    Route("private", "private", ("local",)),
    Route("default", "always", ("local", "frontier")),
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
PINNED = "def116421111552db8fab2de176fc59109d635a7ddcef9ecd5848bff7b6d9f60"
# The portable id of the same five, without the wall and the three
# settings of where it runs, worked out and checked the same way.
PORTABLE_PINNED = "6f31ec738694a9ae481218f387e58ed5d1b4f916b57b38659a88c113e19fe973"


def made(
    models: tuple[object, ...] = MODELS,
    routes: tuple[Route, ...] = ROUTES,
    run: RunSettings = RUN,
    tools: tuple[ToolEntry, ...] = (ENTRY,),
    tool_settings: JsonObject = TOOL_SETTINGS,
    wall: JsonObject = WALL,
) -> str:
    return config_id(models, routes, run, tools, tool_settings, wall)


def models_changed() -> list[tuple[object, ...]]:
    """Give the made-up models with one thing changed in each."""
    frontier = NamedModel("frontier", MODEL, PRICES)
    return [
        (
            dataclasses.replace(
                frontier, profile=dataclasses.replace(MODEL, max_output=2_001)
            ),
            LOCAL,
        ),
        (
            dataclasses.replace(frontier, prices=dataclasses.replace(PRICES, output=2)),
            LOCAL,
        ),
        (frontier, dataclasses.replace(LOCAL, weights_sha256="1" * 64)),
        (frontier, dataclasses.replace(LOCAL, server="llama.cpp b12827")),
        (
            frontier,
            dataclasses.replace(
                LOCAL, profile=dataclasses.replace(LOCAL.profile, window=32_768)
            ),
        ),
        (
            frontier,
            dataclasses.replace(
                LOCAL, profile=dataclasses.replace(LOCAL.profile, stays_on_mac=False)
            ),
        ),
        (LOCAL, frontier),
    ]


def routes_changed() -> list[tuple[Route, ...]]:
    """Give the made-up routes with one thing changed in each."""
    private, default = ROUTES
    return [
        (private, dataclasses.replace(default, models=("frontier", "local"))),
        (private, dataclasses.replace(default, id="ordinary")),
        (dataclasses.replace(private, id="secret"), default),
        (default,),
    ]


def test_the_id_of_known_settings_is_the_pinned_value() -> None:
    assert made() == PINNED


def test_a_change_to_any_of_the_six_gives_another_id() -> None:
    changed = [
        *(made(models=models) for models in models_changed()),
        *(made(routes=routes) for routes in routes_changed()),
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


def portable(
    models: tuple[object, ...] = MODELS,
    routes: tuple[Route, ...] = ROUTES,
    run: RunSettings = RUN,
    tools: tuple[ToolEntry, ...] = (ENTRY,),
    tool_settings: JsonObject = TOOL_SETTINGS,
) -> str:
    return portable_config_id(models, routes, run, tools, tool_settings)


def test_the_portable_id_of_known_settings_is_the_pinned_value() -> None:
    assert portable() == PORTABLE_PINNED


def test_one_settings_file_on_two_folders_gives_one_portable_id_and_two_full_ids() -> (
    None
):
    elsewhere: JsonObject = {
        **TOOL_SETTINGS,
        "work_dir": "/made-up/trial-2/box/work",
        "scratch_dir": "/made-up/trial-2/box/scratch",
    }
    container: JsonObject = {"adapter": "container", "network": "none"}
    assert portable(tool_settings=elsewhere) == portable()
    assert made(tool_settings=elsewhere, wall=container) != made()
    assert len(portable()) == 64 and portable() != made()


def test_the_folders_the_path_and_the_wall_are_not_in_the_portable_id() -> None:
    for key, value in (
        ("work_dir", "/made-up/moved"),
        ("scratch_dir", "/made-up/elsewhere"),
        ("shell_path", "/usr/local/bin:/usr/bin:/bin"),
    ):
        assert portable(tool_settings={**TOOL_SETTINGS, key: value}) == portable()


def test_a_change_to_any_of_the_five_gives_another_portable_id() -> None:
    changed = [
        *(portable(models=models) for models in models_changed()),
        *(portable(routes=routes) for routes in routes_changed()),
        portable(run=dataclasses.replace(RUN, max_steps=1)),
        portable(run=dataclasses.replace(RUN, system="Hi.")),
        portable(tools=()),
        portable(tools=(dataclasses.replace(ENTRY, taints=True),)),
        portable(tool_settings={**TOOL_SETTINGS, "shell_default_seconds": 60}),
        portable(tool_settings={**TOOL_SETTINGS, "fetch_seconds": 31}),
    ]
    assert portable() not in changed
    assert len(set(changed)) == len(changed)
