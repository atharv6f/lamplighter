"""The search_files tool: lines that match a pattern, or files whose names do.

It runs the system's grep or find through the Executor, under `read_only`.
Each value is one item of the argument list, so no shell reads the pattern.
A command's two streams come as one, so a message from grep or find can
land among its records. A record from grep is told by its zero byte, and
a path from find by the `./` in front, so a message is left out. In the
rare case that one lands inside a record, or a file name holds a line
break, that one record is garbled.
"""

import asyncio

from lamplighter.adapters.builtin_tools.settings import ToolSettings, command_env
from lamplighter.adapters.builtin_tools.work_paths import Refused, path_parts
from lamplighter.ports import (
    Command,
    ExecResult,
    Executor,
    Failure,
    JsonObject,
    ToolResult,
    ToolSpec,
)

SEARCH_SECONDS = 30
CAPTURE_BYTES = 8_388_608
MOST_MATCH_CHARS = 500

SPEC = ToolSpec(
    name="search_files",
    description=(
        "Search the work folder. With target content, the default, gives each"
        " line that matches pattern, an extended regular expression, with its"
        " file and line number. With target names, gives each file whose name"
        " matches pattern, a shell pattern such as *.md. path narrows the"
        " search to a file or folder, and glob, such as *.py, narrows a content"
        " search to the files it matches. Skips .git and binary files."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1, "maxLength": 1_000},
            "target": {"type": "string", "enum": ["content", "names"]},
            "path": {"type": "string", "minLength": 1, "maxLength": 4_096},
            "glob": {"type": "string", "minLength": 1, "maxLength": 1_000},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
)

_TIMED_OUT = (
    f"The search took longer than {SEARCH_SECONDS} seconds and was stopped."
    " Narrow the path or the pattern."
)
_STOPPED = (
    f"[The search output passed {CAPTURE_BYTES} bytes and was stopped. Narrow"
    " the path or the pattern.]"
)
_UNREADABLE = "[Some files could not be read.]"


async def search_files(
    settings: ToolSettings, executor: Executor, arguments: JsonObject
) -> ToolResult | Failure:
    """Run the search, and give what it found.

    A search that is cancelled gives no value: the Executor kills the
    command first, and then the cancel goes on.
    """
    argv = _argv(settings, arguments)
    if isinstance(argv, ToolResult):
        return argv
    command = Command(
        argv=argv,
        cwd=settings.work_dir,
        env=command_env(settings),
        profile="read_only",
        wall_ms=SEARCH_SECONDS * 1000,
        capture_bytes=CAPTURE_BYTES,
    )
    ran = await executor.run(command)
    if isinstance(ran, Failure):
        return ran
    if ran.cancelled:
        raise asyncio.CancelledError
    if ran.timed_out:
        return ToolResult(_TIMED_OUT, True, "none")
    if arguments.get("target", "content") == "names":
        return _names(ran)
    return _content(ran)


def _argv(
    settings: ToolSettings, arguments: JsonObject
) -> tuple[str, ...] | ToolResult:
    pattern, target = arguments.get("pattern"), arguments.get("target", "content")
    path, glob = arguments.get("path", "."), arguments.get("glob")
    if not (
        isinstance(pattern, str)
        and pattern
        and target in ("content", "names")
        and isinstance(path, str)
        and (glob is None or isinstance(glob, str))
    ):
        return _error("search_files needs a pattern, and text for path and glob.")
    if glob is not None and target == "names":
        return _error(
            "glob is for the target content only. Put the name pattern in pattern."
        )
    parts = path_parts(path, settings.work_dir)
    if isinstance(parts, Refused):
        return _error(parts.text)
    where = "/".join((".", *parts))
    if target == "names":
        return (
            "/usr/bin/find", where, "-name", ".git", "-prune", "-o",
            "-type", "f", "-name", pattern, "-print",
        )  # fmt: skip
    include = () if glob is None else (f"--include={glob}",)
    return (
        "/usr/bin/grep", "-rnHIE", "--null", "--exclude-dir=.git", *include,
        "-e", pattern, "--", where,
    )  # fmt: skip


def _content(ran: ExecResult) -> ToolResult:
    matches, messages = _records(ran.output, whole_only=ran.overflowed)
    if not matches:
        if ran.exit_code == 1 and not ran.output:
            return ToolResult("No matches.", False, "none")
        if ran.exit_code != 0 or ran.signal is not None:
            return _failed(messages)
    matches.sort(key=lambda match: (match[0], match[1]))
    files = len({path for path, _, _ in matches})
    lines = [f"{len(matches)} matching lines in {files} files."]
    lines += [
        f"{path}:{number}: {text[:MOST_MATCH_CHARS]}" for path, number, text in matches
    ]
    return _with_last_line(lines, ran)


def _names(ran: ExecResult) -> ToolResult:
    text = ran.output.decode("utf-8", errors="replace")
    pieces = text.split("\n")
    if ran.overflowed:
        # The last piece may be half a path.
        pieces = pieces[:-1]
    paths = sorted(
        piece.removeprefix("./") for piece in pieces if piece.startswith("./")
    )
    messages = [piece for piece in pieces if piece and not piece.startswith("./")]
    if not paths:
        if ran.exit_code == 0 and not ran.output:
            return ToolResult("No files match.", False, "none")
        if ran.exit_code != 0 or ran.signal is not None:
            return _failed(messages)
    return _with_last_line([f"{len(paths)} files.", *paths], ran)


def _records(
    output: bytes, whole_only: bool
) -> tuple[list[tuple[str, int, str]], list[str]]:
    # A record is a path, a zero byte, a line number, a colon, the line and a
    # line break. The zero byte tells a file named a:b.txt from its number.
    # A line with no zero byte in it is a message. `whole_only` drops a
    # record that the capture limit cut.
    matches: list[tuple[str, int, str]] = []
    messages: list[str] = []
    at = 0
    while at < len(output):
        zero, newline = output.find(b"\0", at), output.find(b"\n", at)
        if newline != -1 and (zero == -1 or newline < zero):
            messages.append(_text(output[at:newline]))
            at = newline + 1
            continue
        end = len(output) if newline == -1 else newline
        if (newline == -1 and whole_only) or zero == -1:
            break
        number, colon, line = output[zero + 1 : end].partition(b":")
        if colon and number.isdigit():
            path = _text(output[at:zero]).removeprefix("./")
            matches.append((path, int(number), _text(line)))
        at = end + 1
    return matches, messages


def _with_last_line(lines: list[str], ran: ExecResult) -> ToolResult:
    if ran.overflowed:
        lines.append(_STOPPED)
    elif ran.exit_code != 0:
        lines.append(_UNREADABLE)
    return ToolResult("\n".join(lines), False, "none")


def _failed(messages: list[str]) -> ToolResult:
    said = " ".join(message.strip() for message in messages if message.strip())
    return _error(f"The search failed: {said[:MOST_MATCH_CHARS]}")


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")
