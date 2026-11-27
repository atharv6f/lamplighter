"""The settings one run keeps from its start to its end."""

from dataclasses import dataclass

from lamplighter.core.cost import Prices
from lamplighter.ports import JsonObject


@dataclass(frozen=True, slots=True)
class RunSettings:
    """The nine run settings from the settings file.

    The four `max_` values are the run's limits: model calls, seconds of
    model and tool calls, tokens, and millionths of a dollar.
    """

    system: str
    max_steps: int
    max_seconds: int
    max_tokens: int
    max_micro_dollars: int
    model_max_output: int
    model_deadline_ms: int
    model_retries: int
    cancel_bound_ms: int


@dataclass(frozen=True, slots=True)
class RunSpec:
    """Everything that shapes how a run behaves, built once at start.

    `config_id` is one hash over all of it, saved on `run_started`, so a run
    found open under another config after a restart is ended and not
    carried on under new settings. The four `max_` values are the run's
    limits: model calls, seconds of model and tool calls, tokens, and
    millionths of a dollar. `model_deadline_ms` bounds one model call with
    its retries, and `cancel_bound_ms` is how long a call that is being cut
    short gets to stop. `wall` is the executor's record of its wall, saved
    on `run_started` too, because the hash cannot be read back. `model` and
    `provider` are the names from the model settings, saved on each model
    request, so a call that gets no reply still says which model was asked.
    """

    config_id: str
    model: str
    provider: str
    system: str
    max_steps: int
    max_seconds: int
    max_tokens: int
    max_micro_dollars: int
    model_max_output: int
    model_deadline_ms: int
    model_retries: int
    cancel_bound_ms: int
    prices: Prices
    wall: JsonObject
