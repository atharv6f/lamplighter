"""The `models` command: the model entries of the settings, and one field of one."""

from collections.abc import Mapping
from typing import TextIO

from lamplighter.app.config import Config
from lamplighter.app.model_settings import ModelEntry
from lamplighter.core import ask_health
from lamplighter.ports import Failure, HealthCheck


async def list_models(
    config: Config, health: Mapping[str, HealthCheck], out: TextIO
) -> None:
    """Write one line for each model entry: what it is, where it runs, its health.

    Every check is asked at once. A model with no check, which is one on
    Anthropic's API, is not asked.
    """
    asked = await _ask_all(health)
    for entry in config.models:
        profile = entry.profile
        where = "this Mac" if profile.stays_on_mac else "off this Mac"
        answer = asked.get(entry.name, "not checked")
        out.write(
            f"{entry.name}  {profile.adapter}  {profile.model}"
            f"  window {profile.window}  {where}  {answer}\n"
        )


async def _ask_all(health: Mapping[str, HealthCheck]) -> dict[str, str]:
    answers = await ask_health(health, list(health))
    return {
        name: "up" if answer is None else f"{answer.kind}: {answer.message}"
        for name, answer in answers.items()
    }


def entry_field(
    entries: tuple[ModelEntry, ...], name: str, field: str
) -> str | Failure:
    """Give one field of the entry `name`, as the start script reads it.

    A text is given as it is, a whole number in digits, true or false in
    lowercase, and a list one item to a line.
    """
    entry = next((entry for entry in entries if entry.name == name), None)
    if entry is None:
        return _refused(f"There is no model entry named {name!r}.")
    fields = _fields(entry)
    if field not in fields:
        listed = ", ".join(sorted(fields))
        return _refused(f"The entry {name!r} has no field {field!r}. It has: {listed}.")
    value = fields[field]
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return "\n".join(value)
    return str(value)


def _fields(entry: ModelEntry) -> dict[str, str | int | bool | tuple[str, ...]]:
    profile = entry.profile
    fields: dict[str, str | int | bool | tuple[str, ...]] = {
        "name": entry.name,
        "adapter": profile.adapter,
        "model": profile.model,
        "window": profile.window,
        "max_output": profile.max_output,
        "tool_calls": profile.tool_calls,
        "cache_hints": profile.cache_hints,
        "count_error_permille": profile.count_error_permille,
        "concurrent_requests": profile.concurrent_requests,
    }
    server = entry.local
    if server is not None:
        fields |= {
            "base_url": server.base_url,
            "weights_sha256": server.weights_sha256,
            "server": server.server,
            "server_dir": server.server_dir,
            "count": server.count,
            "call_markers": server.call_markers,
        }
    return fields


def _refused(message: str) -> Failure:
    return Failure(
        kind="invalid_input", effect="none", message=message, source="models"
    )
