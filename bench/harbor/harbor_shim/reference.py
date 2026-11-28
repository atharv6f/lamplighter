"""The task's reference solution, run in the box as the user a trial's commands run as.

Harbor's own reference agent runs the solution as root, which can do what
the agent cannot: read a folder closed to others, or write a file root
owns. Run this way, a task that only root could do fails its check.
"""

# pyright checks this folder in standard mode, inside the bench's own
# project: Harbor ships no type marker, so strict mode cannot see its types.

from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from harbor_shim.agent import seed_box

_SOLUTION = "/solution"
_LOG = "reference.txt"


class ReferenceAsCommandUser(BaseAgent):
    """Seeds the box as the harness does, then runs `solve.sh` in it as `run_as`."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        run_as: str,
        **kwargs: Any,
    ) -> None:
        """Keep the user id and group id the solution runs as."""
        user, _, group = run_as.partition(":")
        self._user = (int(user), int(group))
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)

    @staticmethod
    def name() -> str:
        """Give the agent's name."""
        return "lamplighter-reference"

    def version(self) -> str:
        """Give the agent's version, which is this repo's."""
        return "reference"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Seed the box from /app, put a link in its place, and give the box away."""
        await seed_box(environment, self._user)

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """Copy the solution in, run it as the command user, and keep what it printed.

        Its exit code decides nothing: the check grades what it left.
        """
        solution = environment.environment_dir.parent / "solution"
        await environment.upload_dir(source_dir=solution, target_dir=_SOLUTION)
        await environment.exec(f"chmod -R a+rX {_SOLUTION}", user=0)
        user, group = self._user
        done = await environment.exec(
            f"bash {_SOLUTION}/solve.sh", user=f"{user}:{group}"
        )
        printed = (done.stdout or "") + (done.stderr or "")
        (self.logs_dir / _LOG).write_text(
            f"exit {done.return_code}\n{printed}", encoding="utf-8"
        )
