"""Brings a saved payload up to the newest form this code knows, as it is read."""

from lamplighter.ports.event_log import Upcasters
from lamplighter.ports.json_value import JsonObject


def upcast(
    event_type: str, v: int, payload: JsonObject, upcasters: Upcasters
) -> tuple[int, JsonObject]:
    """Pass a payload through every upcaster that matches, and give its new version.

    The saved row is never changed. Old data is upgraded each time it is read.
    """
    while (event_type, v) in upcasters:
        payload = upcasters[(event_type, v)](payload)
        v += 1
    return v, payload
