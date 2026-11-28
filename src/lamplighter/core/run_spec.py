"""The settings one run keeps from its start to its end."""

from dataclasses import dataclass

from lamplighter.core.route_table import NamedModel, Route
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
    on `run_started` too, because the hash cannot be read back.

    `models` are the model entries of the settings, each with its profile
    and its prices, and `routes` the rule table, in the order of the file.
    `private` says the run may use only models whose calls stay on this
    Mac; it is in the full config id, so a run that was not private is
    never carried on as one.

    `portable_config_id` names the same settings on any machine, without
    the wall and the folders, and `code_revision` names the code that runs:
    a commit, a commit with a mark for uncommitted changes, or `unknown`.
    Both are saved on `run_started`, so a run can be traced to the config
    and the code that made it.
    """

    config_id: str
    portable_config_id: str
    code_revision: str
    models: tuple[NamedModel, ...]
    routes: tuple[Route, ...]
    private: bool
    system: str
    max_steps: int
    max_seconds: int
    max_tokens: int
    max_micro_dollars: int
    model_max_output: int
    model_deadline_ms: int
    model_retries: int
    cancel_bound_ms: int
    wall: JsonObject

    def model(self, name: str) -> NamedModel:
        """Give the model entry of this name. The settings name each one once."""
        return next(entry for entry in self.models if entry.name == name)
