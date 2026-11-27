"""The settings the built-in tools read, and the environment a command gets."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolSettings:
    """The `[tools]` section, with the two folders as resolved paths.

    `shell_path` is the PATH a command gets. A shell call runs for
    `shell_default_seconds` unless it asks for up to `shell_max_seconds`.
    `output_cap_chars` is the cap of every tool. A fetch keeps at most
    `fetch_max_bytes` of a body and takes at most `fetch_seconds`.
    """

    work_dir: str
    scratch_dir: str
    shell_path: str
    shell_default_seconds: int
    shell_max_seconds: int
    output_cap_chars: int
    fetch_max_bytes: int
    fetch_seconds: int


def command_env(settings: ToolSettings) -> dict[str, str]:
    """Give the seven variables a command gets, and nothing of the harness's own.

    The harness's environment holds the API key, so none of it is passed on.
    zsh makes its temp files under `TMPPREFIX`, not `TMPDIR`, and its own
    default is in /tmp, which the wall closes.
    """
    return {
        "PATH": settings.shell_path,
        "HOME": f"{settings.scratch_dir}/home",
        "TMPDIR": f"{settings.scratch_dir}/tmp",
        "TMPPREFIX": f"{settings.scratch_dir}/tmp/zsh",
        "LANG": "en_US.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
