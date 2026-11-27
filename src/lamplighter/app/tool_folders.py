"""Makes the work folder and the scratch folder, and refuses one reached by a link."""

import os
import stat
from pathlib import Path

from lamplighter.adapters.builtin_tools import ToolSettings

THROUGH_A_LINK = (
    "{setting} is {path}, which is reached through a symbolic link to {real}."
    " Write the resolved path in the settings file."
)
_PRIVATE = 0o700


def make_tool_folders(tools: ToolSettings) -> str | None:
    """Make the folders the tools need, or give the line that stops the start.

    The wall names real paths, so a folder reached through a link is
    refused: the link could be pointed somewhere else later. A command may
    write in the scratch folder, so it could have left a link or a file
    where `home` or `tmp` should be. Such a thing is removed and the folder
    made again.
    """
    for setting, folder in (
        ("tools.work_dir", tools.work_dir),
        ("tools.scratch_dir", tools.scratch_dir),
    ):
        Path(folder).mkdir(mode=_PRIVATE, parents=True, exist_ok=True)
        real = os.path.realpath(folder)
        if real != folder:
            return THROUGH_A_LINK.format(setting=setting, path=folder, real=real)
    for name in ("home", "tmp"):
        _real_folder(Path(tools.scratch_dir, name))
    return None


def _real_folder(folder: Path) -> None:
    try:
        found = folder.lstat()
    except FileNotFoundError:
        folder.mkdir(mode=_PRIVATE)
        return
    if not stat.S_ISDIR(found.st_mode):
        folder.unlink()
        folder.mkdir(mode=_PRIVATE)
