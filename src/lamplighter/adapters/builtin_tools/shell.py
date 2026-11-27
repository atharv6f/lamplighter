"""The shell tool: one bash command line, run in the work folder behind the wall."""

from lamplighter.adapters.builtin_tools.settings import ToolSettings, command_env
from lamplighter.ports import (
    Command,
    ExecResult,
    Executor,
    Failure,
    JsonObject,
    Plan,
    ToolResult,
    ToolSpec,
)

CAPTURE_BYTES = 8_388_608
MOST_COMMAND_CHARS = 100_000


def shell_spec(settings: ToolSettings) -> ToolSpec:
    """Give the spec, whose longest time limit comes from the settings."""
    return ToolSpec(
        name="shell",
        description=(
            "Run one bash command line in the work folder, in a sandbox: it can"
            " write only in the work folder, and has no network. Gives a status"
            " line and the output of both streams. Nothing is kept between"
            " calls: no folder change, no variable and no background process."
            f" timeout_s is the time limit, {settings.shell_default_seconds}"
            " seconds when left out."
        ),
        arguments_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MOST_COMMAND_CHARS,
                },
                "timeout_s": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": settings.shell_max_seconds,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    )


# Nothing can check what a command did, and nothing can undo it.
SHELL_PLAN = Plan(target=".", probe=None, undo=None)


async def run_shell(
    settings: ToolSettings, executor: Executor, arguments: JsonObject
) -> ToolResult | Failure:
    """Run the command line, and give its status line and output.

    A cancel reaches the Executor, which kills the command and gives what it
    printed so far, so a stop still answers with a result.
    """
    line = arguments.get("command")
    seconds = arguments.get("timeout_s", settings.shell_default_seconds)
    if not (
        isinstance(line, str)
        and line
        and type(seconds) is int
        and 1 <= seconds <= settings.shell_max_seconds
    ):
        return ToolResult(
            "shell needs a command, and a timeout_s from 1 to"
            f" {settings.shell_max_seconds}.",
            True,
            "none",
        )
    ran = await executor.run(
        Command(
            argv=("/bin/bash", "-c", line),
            cwd=settings.work_dir,
            env=command_env(settings),
            profile="workspace_write",
            wall_ms=seconds * 1000,
            capture_bytes=CAPTURE_BYTES,
        )
    )
    if isinstance(ran, Failure):
        # Nothing was started.
        return ran
    status, is_error = _status(ran, seconds)
    output = ran.output.decode("utf-8", errors="replace") or "(no output)"
    # A command may have changed something, whatever its ending.
    return ToolResult(f"{status}\n{output}", is_error, "done")


def _status(ran: ExecResult, seconds: int) -> tuple[str, bool]:
    # The first row that fits is the one used.
    if ran.cancelled:
        return "[stopped before it ended, and was killed]", True
    if ran.timed_out:
        return f"[timed out after {seconds} seconds and was killed]", True
    if ran.overflowed and ran.signal is not None:
        return f"[stopped at {CAPTURE_BYTES} bytes of output and was killed]", True
    if ran.overflowed:
        return (
            f"[exit code {ran.exit_code}, and the output after {CAPTURE_BYTES}"
            " bytes was left out]",
            ran.exit_code != 0,
        )
    if ran.signal is not None:
        return f"[killed by signal {ran.signal}]", True
    return f"[exit code {ran.exit_code}]", ran.exit_code != 0
