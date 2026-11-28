"""The names of the event types the loop writes, and the version it reads of each."""

from lamplighter.ports import RUN_END, RUN_STARTED, USER_MESSAGE

SESSION_STARTED = "session_started"
MODEL_REQUEST = "model_request"
MODEL_RESPONSE = "model_response"
TOOL_INTENT = "tool_intent"
TOOL_RESULT = "tool_result"
LIMIT_HIT = "limit_hit"
CANCELLED = "cancelled"
REPLY_READY = "reply_ready"
ROUTE_DECIDED = "route_decided"

# The payload version of each type this code reads. A newer version was
# written by newer code, and old code never guesses at its meaning.
READ_VERSION = {
    SESSION_STARTED: 1,
    RUN_STARTED: 2,
    USER_MESSAGE: 1,
    MODEL_REQUEST: 2,
    MODEL_RESPONSE: 1,
    TOOL_INTENT: 1,
    TOOL_RESULT: 1,
    LIMIT_HIT: 1,
    CANCELLED: 1,
    REPLY_READY: 1,
    RUN_END: 1,
    ROUTE_DECIDED: 1,
}
