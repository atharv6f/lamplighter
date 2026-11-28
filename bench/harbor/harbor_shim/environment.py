"""Harbor's Docker environment, with the trial's box shared at the same path inside."""

# pyright checks this folder in standard mode, inside the bench's own
# project: Harbor ships no type marker, so strict mode cannot see its types.

from pathlib import Path
from typing import Any

from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.trial.paths import TrialPaths

# Harbor names the agent's environment "<trial>__env", and a grading one
# "<trial>__verifier__<key>". Only the agent's gets the box.
_AGENTS = "__env"


def box_of(trial_dir: Path) -> Path:
    """Give the box of a trial: the folder the agent's container shares."""
    return trial_dir.resolve() / "box"


class SharedFolderDocker(DockerEnvironment):
    """Mounts the box into the agent's container, and nothing into a grading one."""

    def __init__(
        self,
        *args: Any,
        trial_paths: TrialPaths,
        session_id: str,
        mounts: Any = None,
        **kwargs: Any,
    ) -> None:
        """Make the box's folders, and add the one mount, before Harbor's own set-up."""
        extra = list(mounts or [])
        if session_id.endswith(_AGENTS):
            box = box_of(trial_paths.trial_dir)
            for name in ("work", "scratch/home", "scratch/tmp"):
                (box / name).mkdir(parents=True, exist_ok=True)
            extra.append({"type": "bind", "source": str(box), "target": str(box)})
        super().__init__(
            *args,
            trial_paths=trial_paths,
            session_id=session_id,
            mounts=extra,
            **kwargs,
        )
