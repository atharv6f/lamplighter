"""The plain values that may cross a port and be written as JSON."""

from collections.abc import Mapping, Sequence

type JsonValue = (
    None | bool | int | float | str | Sequence[JsonValue] | Mapping[str, JsonValue]
)
type JsonObject = Mapping[str, JsonValue]
