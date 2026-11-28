"""The upcasters that bring events saved by older code up to the versions read now."""

from lamplighter.core.event_types import MODEL_REQUEST
from lamplighter.ports import RUN_STARTED, JsonObject, Upcasters


def _run_started_from_1(payload: JsonObject) -> JsonObject:
    # Every run before private sessions existed was an ordinary one.
    return {**payload, "private": False}


def _model_request_from_1(payload: JsonObject) -> JsonObject:
    # Before routes, the settings held one model, and it had no name.
    return {**payload, "model_name": None}


UPCASTERS: Upcasters = {
    (RUN_STARTED, 1): _run_started_from_1,
    (MODEL_REQUEST, 1): _model_request_from_1,
}
