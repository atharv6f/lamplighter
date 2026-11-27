"""One hash over everything that changes how a run behaves."""

import hashlib
from collections.abc import Sequence

from lamplighter.core.cost import Prices
from lamplighter.core.run_spec import RunSettings
from lamplighter.ports import JsonObject, ModelProfile, ToolEntry, saved_json


def config_id(
    model: ModelProfile,
    prices: Prices,
    run: RunSettings,
    tools: Sequence[ToolEntry],
    tool_settings: JsonObject,
    wall: JsonObject,
) -> str:
    """Give the SHA-256 of the saved JSON of the six, as 64 hex characters.

    `tools` are the entries, each with its spec, class, cap and taint flag.
    `tool_settings` is the settings file's section for the tools, and
    `wall` the executor's record of its wall, so a new profile, a new
    version of the system or a moved work folder each give a new id.

    A run saves it when it starts, so a run found open under other settings
    after a restart is ended and not carried on. The saved form is hashed,
    and not the canonical one, because a tool's schema may hold a number
    with a decimal point, which the canonical form refuses.
    """
    text = saved_json(
        {
            "model": model,
            "prices": prices,
            "run": run,
            "tools": tools,
            "tool_settings": tool_settings,
            "wall": wall,
        }
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
