"""The port for asking a service in a separate process whether it is up."""

from typing import Protocol

from lamplighter.ports.failure import Failure


class HealthCheck(Protocol):
    """A service in a separate process, asked whether it is up.

    Callers can rely on this:

    - `health` answers within two seconds, and changes nothing.
    - None means the service is up and ready for a call now.
    - A Failure means it is not. Its kind is `fatal` for a port held by
      another program or a service that answers as another one, and
      `unavailable` for every other answer: refused, still starting, too
      slow, or any status but the one expected.
    - An expected failure is never raised.
    """

    async def health(self) -> Failure | None:
        """Say whether the service is up."""
        ...
