"""Reads the settings' list of models and list of routes, and their rules."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from lamplighter.adapters import local_address
from lamplighter.core import NamedModel, Prices, Route, RouteWhen
from lamplighter.ports import ModelProfile, from_plain

type Adapter = Literal["anthropic", "openai_chat"]
type CountKind = Literal["llama_cpp", "vllm", "none"]
type ServerKind = Literal["llama.cpp", "vllm-metal", "mlx-lm"]

_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_LONGEST_NAME = 40
_LONGEST_MODEL = 200
_HASH = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[!-~]{1,100}")
_MODELS_FOLDER = "~/lamplighter-models/"
# The count endpoint each server has, so the adapter can count a request
# exactly before it sends it. MLX-LM has none.
_COUNT_OF: dict[ServerKind, CountKind] = {
    "llama.cpp": "llama_cpp",
    "vllm-metal": "vllm",
    "mlx-lm": "none",
}
# The one name `mlx_lm.server` maps to the model it loaded. Any other name
# makes it drop that model and try to download the name.
MLX_MODEL_NAME = "default_model"
_COMMON = frozenset(
    {
        "name",
        "adapter",
        "model",
        "window",
        "max_output",
        "tool_calls",
        "cache_hints",
        "count_error_permille",
        "concurrent_requests",
    }
)
_ONLY_FOR: dict[Adapter, frozenset[str]] = {
    "anthropic": frozenset({"prices"}),
    "openai_chat": frozenset(
        {"base_url", "weights_sha256", "server", "server_dir", "count", "call_markers"}
    ),
}
_MOST_HINTS: dict[Adapter, int] = {"anthropic": 4, "openai_chat": 0}
_ROUTE_FIELDS = frozenset({"id", "when", "models"})
_WHEN: tuple[RouteWhen, ...] = ("private", "always")


@dataclass(frozen=True, slots=True)
class LocalServer:
    """What a model entry for a server on this Mac says about that server.

    `base_url` passed the loopback rule and has no `/` at its end. `server`
    is the server's name, a space and its exact version, such as
    `llama.cpp b12826`. `server_dir` is the folder its program is installed
    in, as written, starting with `~/lamplighter-models/`. `count` names the
    server's endpoint for an exact count of a request, or `none`.
    `call_markers` are texts only a tool call in the model's own markup holds.
    """

    base_url: str
    weights_sha256: str
    server: str
    server_dir: str
    count: CountKind
    call_markers: tuple[str, ...]

    @property
    def kind(self) -> ServerKind:
        """Give the server's name without its version."""
        return cast(ServerKind, self.server.split(" ", 1)[0])


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """One `[[models]]` table of the settings.

    `prices` is set for an Anthropic model and None for a local one, whose
    replies are not priced. `local` is set only for a local one.
    """

    name: str
    profile: ModelProfile
    prices: Prices | None
    local: LocalServer | None

    def named(self) -> NamedModel:
        """Give the entry as the core reads it."""
        return NamedModel(self.name, self.profile, self.prices)


def read_models(tables: object) -> tuple[ModelEntry, ...]:
    """Read the `[[models]]` list, or raise ValueError that names the field."""
    if not isinstance(tables, list) or not tables:
        raise ValueError("the settings need at least one [[models]] table")
    entries = tuple(
        _entry(f"models[{place}]", table)
        for place, table in enumerate(cast(list[object], tables))
    )
    names = [entry.name for entry in entries]
    for name in names:
        if names.count(name) > 1:
            raise ValueError(f"two [[models]] tables have the name {name!r}")
    return entries


def read_routes(tables: object) -> tuple[Route, ...]:
    """Read the `[[routes]]` list, or raise ValueError that names the field."""
    if not isinstance(tables, list) or not tables:
        raise ValueError("the settings need at least one [[routes]] table")
    routes = tuple(
        _route(f"routes[{place}]", table)
        for place, table in enumerate(cast(list[object], tables))
    )
    ids = [route.id for route in routes]
    for route_id in ids:
        if ids.count(route_id) > 1:
            raise ValueError(f"two [[routes]] tables have the id {route_id!r}")
    return routes


def check_table(
    models: Sequence[ModelEntry], routes: Sequence[Route], model_max_output: int
) -> None:
    """Raise ValueError for a rule table no run could follow as written.

    The built-in tools are always on, so every model a route names must take
    tool calls.
    """
    by_name = {entry.name: entry for entry in models}
    _check_ladders(routes, by_name)
    _check_default_last(routes)
    _check_private(routes, by_name)
    for route in routes:
        for name in route.models:
            profile = by_name[name].profile
            if not profile.tool_calls:
                raise ValueError(
                    f"routes {route.id!r} names {name!r}, which takes no tool"
                    " calls, and the tools are always on"
                )
            if model_max_output > profile.max_output:
                raise ValueError(
                    f"run.model_max_output is {model_max_output}, above the"
                    f" max_output of {profile.max_output} of {name!r}"
                )
    named = {name for route in routes for name in route.models}
    for entry in models:
        if entry.name not in named:
            raise ValueError(f"no route names the model {entry.name!r}")


def _check_ladders(routes: Sequence[Route], by_name: Mapping[str, ModelEntry]) -> None:
    for route in routes:
        for name in route.models:
            if name not in by_name:
                raise ValueError(
                    f"routes {route.id!r} names {name!r}, which no [[models]] table has"
                )


def _check_default_last(routes: Sequence[Route]) -> None:
    # The contract: a rule table with no default rule is rejected.
    if routes[-1].when != "always":
        raise ValueError('the last route must have when = "always"')
    for route in routes[:-1]:
        if route.when == "always":
            raise ValueError(
                f'only the last route may have when = "always", not {route.id!r}'
            )


def _check_private(routes: Sequence[Route], by_name: Mapping[str, ModelEntry]) -> None:
    private = [route for route in routes if route.when == "private"]
    if len(private) > 1:
        raise ValueError('only one route may have when = "private"')
    for route in private:
        if not any(by_name[name].profile.stays_on_mac for name in route.models):
            raise ValueError(
                f"routes {route.id!r} is for private runs and names no model"
                " that stays on this Mac"
            )


def _entry(where: str, table: object) -> ModelEntry:
    fields = _table(where, table)
    adapter = _adapter(where, fields)
    allowed = _COMMON | _ONLY_FOR[adapter]
    for name in fields:
        if name not in allowed:
            raise ValueError(f"{where}.{name} is not a field of an {adapter} model")
    for name in allowed:
        if name not in fields:
            raise ValueError(f"{where}.{name} is missing")
    name = _name(f"{where}.name", fields["name"])
    profile = _profile(where, adapter, fields)
    if adapter == "anthropic":
        return ModelEntry(name, profile, _prices(where, fields["prices"]), None)
    local = _local(where, fields)
    _check_local_model(where, name, profile.model, local)
    return ModelEntry(name, profile, None, local)


def _table(where: str, table: object) -> dict[str, Any]:
    if not isinstance(table, dict):
        raise ValueError(f"{where} is not a table")
    return cast(dict[str, Any], table)


def _adapter(where: str, fields: Mapping[str, Any]) -> Adapter:
    adapter = fields.get("adapter")
    if adapter == "anthropic" or adapter == "openai_chat":
        return adapter
    raise ValueError(f'{where}.adapter must be "anthropic" or "openai_chat"')


def _profile(where: str, adapter: Adapter, fields: Mapping[str, Any]) -> ModelProfile:
    model = _text(f"{where}.model", fields["model"])
    if not 1 <= len(model) <= _LONGEST_MODEL or not model.isprintable():
        raise ValueError(f"{where}.model must be 1 to 200 printable characters")
    window = _whole(f"{where}.window", fields["window"], 1, None)
    max_output = _whole(f"{where}.max_output", fields["max_output"], 1, None)
    if max_output >= window:
        raise ValueError(f"{where}.max_output must be below {where}.window")
    hints = _whole(
        f"{where}.cache_hints", fields["cache_hints"], 0, _MOST_HINTS[adapter]
    )
    return ModelProfile(
        adapter=adapter,
        model=model,
        max_output=max_output,
        tool_calls=_flag(f"{where}.tool_calls", fields["tool_calls"]),
        cache_hints=hints,
        count_error_permille=_whole(
            f"{where}.count_error_permille", fields["count_error_permille"], 0, 1000
        ),
        window=window,
        concurrent_requests=_whole(
            f"{where}.concurrent_requests", fields["concurrent_requests"], 1, None
        ),
        # Worked out, never read: only a local server's address is checked to
        # be this Mac's own.
        stays_on_mac=adapter == "openai_chat",
    )


def _prices(where: str, value: object) -> Prices:
    try:
        return from_plain(Prices, cast(Any, value))
    except ValueError as problem:
        raise ValueError(f"{where}.prices: {problem}") from problem


def _local(where: str, fields: Mapping[str, Any]) -> LocalServer:
    base_url = _text(f"{where}.base_url", fields["base_url"])
    address = local_address(base_url)
    if address is None:
        raise ValueError(
            f"{where}.base_url must be http://127.0.0.1: and a port, such as"
            " http://127.0.0.1:9931"
        )
    weights = _text(f"{where}.weights_sha256", fields["weights_sha256"])
    if _HASH.fullmatch(weights) is None:
        raise ValueError(f"{where}.weights_sha256 must be 64 lowercase hex characters")
    server = _server(f"{where}.server", fields["server"])
    count = _text(f"{where}.count", fields["count"])
    if count != _COUNT_OF[server[0]]:
        raise ValueError(
            f"{where}.count must be {_COUNT_OF[server[0]]!r} for {server[0]}"
        )
    return LocalServer(
        base_url=address,
        weights_sha256=weights,
        server=f"{server[0]} {server[1]}",
        server_dir=_server_dir(f"{where}.server_dir", fields["server_dir"]),
        count=_COUNT_OF[server[0]],
        call_markers=_markers(f"{where}.call_markers", fields["call_markers"]),
    )


def _server(where: str, value: object) -> tuple[ServerKind, str]:
    text = _text(where, value)
    kind, _, version = text.partition(" ")
    for known in _COUNT_OF:
        if kind == known and _VERSION.fullmatch(version):
            return known, version
    raise ValueError(
        f"{where} must be llama.cpp, vllm-metal or mlx-lm, a space, and the exact"
        " version, such as 'llama.cpp b12826'"
    )


def _server_dir(where: str, value: object) -> str:
    text = _text(where, value)
    rest = text.removeprefix(_MODELS_FOLDER)
    parts = rest.split("/")
    if (
        rest == text
        or not rest
        or any(part in ("", ".", "..") for part in parts)
        or not text.isprintable()
    ):
        raise ValueError(f"{where} must be a folder inside {_MODELS_FOLDER}")
    return text


def _markers(where: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list of texts")
    markers = cast(list[object], value)
    if not all(isinstance(marker, str) and marker for marker in markers):
        raise ValueError(f"{where} must be a list of texts that are not empty")
    return tuple(cast(list[str], markers))


def _check_local_model(where: str, name: str, model: str, local: LocalServer) -> None:
    # The start script gives the server the entry's name as its alias, and
    # MLX-LM maps only one name to the model it loaded.
    want = MLX_MODEL_NAME if local.kind == "mlx-lm" else name
    if model != want:
        raise ValueError(f"{where}.model must be {want!r} for {local.kind}")


def _route(where: str, table: object) -> Route:
    fields = _table(where, table)
    if fields.keys() != _ROUTE_FIELDS:
        raise ValueError(f"{where} needs exactly the fields id, when and models")
    route_id = _name(f"{where}.id", fields["id"])
    when = fields["when"]
    if when not in _WHEN:
        raise ValueError(f'{where}.when must be "private" or "always"')
    ladder = fields["models"]
    if not isinstance(ladder, list) or not ladder:
        raise ValueError(f"{where}.models must list at least one model name")
    names = cast(list[object], ladder)
    if not all(isinstance(name, str) for name in names):
        raise ValueError(f"{where}.models must list model names")
    if len(set(names)) != len(names):
        raise ValueError(f"{where}.models names one model twice")
    return Route(route_id, when, tuple(cast(list[str], names)))


def _name(where: str, value: object) -> str:
    text = _text(where, value)
    if len(text) > _LONGEST_NAME or _NAME.fullmatch(text) is None:
        raise ValueError(
            f"{where} must be 1 to 40 lowercase letters, digits and single hyphens"
        )
    return text


def _text(where: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a text")
    return value


def _flag(where: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{where} must be true or false")
    return value


def _whole(where: str, value: object, least: int, most: int | None) -> int:
    # bool is a kind of int in Python, and true is not a number here.
    if type(value) is not int:
        raise ValueError(f"{where} must be a whole number")
    if value < least or (most is not None and value > most):
        top = "" if most is None else f" to {most}"
        raise ValueError(f"{where} must be from {least}{top}, not {value}")
    return value
