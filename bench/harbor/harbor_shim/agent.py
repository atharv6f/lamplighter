"""The agent Harbor runs for this repo's harness: it seeds the box and calls the trial.

It holds no rule of the bench. Every decision is in the trial function or
in a pure function of the bench.
"""

# pyright checks this folder in standard mode, inside the bench's own
# project: Harbor ships no type marker, so strict mode cannot see its types.

import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from harbor_shim.environment import box_of
from lamplighter.app.bench_trial import TrialRequest
from lamplighter.app.main import bench_trial
from lamplighter.bench import TrialRecord

_SEED_SECONDS = 120


class TrialInfraError(Exception):
    """Harbor must copy nothing and run no check for this trial.

    The trial did not test the agent, or what the agent left is past a
    bound. The job file tells Harbor not to grade a trial that ends this
    way, and not to run it again.
    """


class HarnessAgent(BaseAgent):
    """This repo's harness as an agent of Harbor's.

    `trial` is the one seam: a test's own class sets it to a trial with a
    scripted model.
    """

    trial: ClassVar[Callable[[TrialRequest], Awaitable[TrialRecord]]] = staticmethod(
        bench_trial
    )

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        *,
        settings: str,
        shell_path: str,
        budget_micro_dollars: int,
        portable_config_id: str,
        code_revision: str,
        run_as: str,
        **kwargs: Any,
    ) -> None:
        """Keep the six values of the job file."""
        self._settings = Path(settings)
        self._shell_path = shell_path
        self._budget = budget_micro_dollars
        self._portable_id = portable_config_id
        self._revision = code_revision
        user, _, group = run_as.partition(":")
        self._user = (int(user), int(group))
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)

    @staticmethod
    def name() -> str:
        """Give the harness's name."""
        return "lamplighter"

    def version(self) -> str:
        """Give the planned portable config id's front and the planned code revision."""
        return f"{self._portable_id[:16]}+{self._revision}"

    async def setup(self, environment: BaseEnvironment) -> None:
        """Seed the box from /app, put a link in its place, and give the box away."""
        box = shlex.quote(str(box_of(environment.trial_paths.trial_dir)))
        user, group = self._user
        lines = [
            # Harbor starts a command in the image's work folder. The copy
            # runs only from /app: from / it would copy the container's /proc.
            f'home=$(pwd -P) && [ "$home" = /app ]'
            f' && cp -R -P --preserve=mode,timestamps "$home"/. {box}/work/'
            f' && cd / && rm -rf "$home" && ln -s {box}/work "$home"',
            # -h changes a link and never the file it points at.
            f"chown -R -h {user}:{group} {box}",
            f"getent passwd {user} >/dev/null"
            f' || echo "bench:x:{user}:{group}::/nonexistent:/bin/bash" >>/etc/passwd',
        ]
        for line in lines:
            try:
                seeded = await environment.exec(line, timeout_sec=_SEED_SECONDS, user=0)
            except RuntimeError as error:
                # Harbor's way of saying the call did not end in time.
                raise TrialInfraError(f"seeding: {error}") from None
            if seeded.return_code != 0:
                raise TrialInfraError(f"seeding ended with {seeded.return_code}")

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """Run the trial, fill Harbor's context from its record, and stop the check."""

        async def as_root(line: str, cwd: str, seconds: int) -> tuple[int, str] | None:
            try:
                done = await environment.exec(
                    line, cwd=cwd, timeout_sec=seconds, user=0
                )
            except RuntimeError:
                # Harbor's way of saying the call did not end in time.
                return None
            return done.return_code, (done.stdout or "") + (done.stderr or "")

        record = await type(self).trial(
            TrialRequest(
                instruction=instruction,
                as_root=as_root,
                user=self._user,
                trial_folder=environment.trial_paths.trial_dir.resolve(),
                settings=self._settings,
                shell_path=self._shell_path,
                budget_micro_dollars=self._budget,
                planned_portable_id=self._portable_id,
                planned_revision=self._revision,
            )
        )
        _fill(context, record)
        stop = record.infra_reason or record.left_out_of_bounds
        if stop is not None:
            await _empty_app(environment)
            raise TrialInfraError(stop)


async def _empty_app(environment: BaseEnvironment) -> None:
    # Harbor 0.27.0 copies /app out of the container after any error of the
    # agent, before it decides not to grade: its trial code calls the copy
    # from the handler of the error. In place of the link to the work
    # folder, an empty /app is all it then copies. The work folder itself
    # stays, for a person to read.
    try:
        await environment.exec(
            "rm -f /app && mkdir /app", timeout_sec=_SEED_SECONDS, user=0
        )
    except RuntimeError:
        # Harbor's way of saying the call did not end in time. The trial is
        # still not graded, and its outcome is still read from its record:
        # only the copy may then be large.
        return


def _fill(context: AgentContext, record: TrialRecord) -> None:
    tokens = record.tokens
    context.n_input_tokens = tokens.uncached + tokens.read + tokens.written
    context.n_cache_tokens = tokens.read
    context.n_output_tokens = tokens.output
    context.cost_usd = (
        None if record.micro_dollars is None else record.micro_dollars / 1_000_000
    )
    context.metadata = {
        "portable_config_id": record.portable_config_id,
        "code_revision": record.code_revision,
        "run_reason": record.run_reason,
        "gradable": record.gradable,
        "infra_reason": record.infra_reason,
    }
