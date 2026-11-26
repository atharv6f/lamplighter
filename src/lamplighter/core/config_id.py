"""One hash over everything that changes how a run behaves."""

import hashlib
from collections.abc import Sequence

from lamplighter.core.cost import Prices
from lamplighter.core.run_spec import RunSettings
from lamplighter.ports import ModelProfile, ToolSpec, saved_json


def config_id(
    model: ModelProfile, prices: Prices, run: RunSettings, tools: Sequence[ToolSpec]
) -> str:
    """Give the SHA-256 of the saved JSON of the four, as 64 hex characters.

    A run saves it when it starts, so a run found open under other settings
    after a restart is ended and not carried on. The saved form is hashed,
    and not the canonical one, because a tool's schema may hold a number
    with a decimal point, which the canonical form refuses.
    """
    text = saved_json({"model": model, "prices": prices, "run": run, "tools": tools})
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
