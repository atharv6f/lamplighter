"""Checks a request against a model's profile before any network call."""

from lamplighter.ports.failure import Failure
from lamplighter.ports.model import CacheHint, ModelProfile, ModelRequest


def check_request(profile: ModelProfile, request: ModelRequest) -> Failure | None:
    """Give the Failure for a request this profile cannot serve, or None if it can.

    Every model client runs this first, so a request that needs a missing
    feature fails loudly here and is never sent with a changed meaning.
    """
    problem = _problem(profile, request)
    if problem is None:
        return None
    return Failure(
        kind="invalid_input",
        effect="none",
        message=problem,
        source=f"ModelClient/{profile.adapter}",
    )


def _problem(profile: ModelProfile, request: ModelRequest) -> str | None:
    if request.tools and not profile.tool_calls:
        return "The request has tools and this model takes none."
    if not 1 <= request.max_output <= profile.max_output:
        return f"max_output must be from 1 to {profile.max_output}."
    if profile.cache_hints == 0:
        # Hints are advice. A model that honours none still gives a correct reply.
        return None
    if len(request.cache_hints) > profile.cache_hints:
        return f"The request has more than {profile.cache_hints} cache hints."
    if not all(_points_at_something(hint, request) for hint in request.cache_hints):
        return "A cache hint points at a system text or message that is not there."
    return None


def _points_at_something(hint: CacheHint, request: ModelRequest) -> bool:
    size = len(request.system) if hint.after == "system" else len(request.messages)
    return 0 <= hint.index < size
