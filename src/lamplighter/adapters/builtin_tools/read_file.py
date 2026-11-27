"""The read_file tool: numbered lines of a text file in the work folder."""

import os

from lamplighter.adapters.builtin_tools.work_paths import (
    MOST_BYTES,
    Refused,
    WorkFolder,
    open_existing,
    path_parts,
    read_up_to,
)
from lamplighter.ports import JsonObject, ToolResult, ToolSpec

MOST_LINES = 2_000
MOST_LINE_CHARS = 2_000
# Room left under the cap for the last line, so the result is never cut.
_ROOM = 500
# Where a zero byte says a file is not text.
_TEXT_TEST_BYTES = 8_192

SPEC = ToolSpec(
    name="read_file",
    description=(
        "Read a text file in the work folder. Gives its lines with their"
        " numbers, and a last line that says which lines these are and how"
        " to read on. A path is relative to the work folder. Use offset to"
        f" start at a later line and limit to give fewer lines. Gives at most"
        f" {MOST_LINES} lines, and at most {MOST_LINE_CHARS} characters of a line."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 4_096},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": MOST_LINES},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)


def read_file(work: WorkFolder, arguments: JsonObject, cap: int) -> ToolResult:
    """Give the lines the arguments ask for, or an error result.

    `cap` is the tool's output cap. It blocks, so it runs in a worker thread.
    """
    path, offset, limit = (
        arguments.get("path"),
        arguments.get("offset", 1),
        arguments.get("limit", MOST_LINES),
    )
    if not (
        isinstance(path, str)
        and type(offset) is int
        and type(limit) is int
        and offset >= 1
        and 1 <= limit <= MOST_LINES
    ):
        return _error(
            "read_file needs a path, an offset from 1 and a limit from 1"
            f" to {MOST_LINES}."
        )
    parts = path_parts(path, work.root)
    if isinstance(parts, Refused):
        return _error(parts.text)
    found = open_existing(work, parts, path)
    if isinstance(found, Refused):
        return _error(found.text)
    if found is None:
        return _error(f"There is no file at {path}.")
    size = os.fstat(found).st_size
    data = read_up_to(found, MOST_BYTES)
    if len(data) > MOST_BYTES:
        return _error(
            f"{path} is {max(size, len(data))} bytes. read_file reads files of up to"
            f" {MOST_BYTES} bytes. Search it with search_files, or read part of it"
            " with a command."
        )
    if b"\0" in data[:_TEXT_TEST_BYTES]:
        return _error(f"{path} is not a text file. read_file reads text only.")
    if not data:
        return ToolResult(f"{path} is empty.", False, "none")
    return _lines(path, data, offset, limit, cap)


def _lines(path: str, data: bytes, offset: int, limit: int, cap: int) -> ToolResult:
    lines = data.decode("utf-8", errors="replace").split("\n")
    if data.endswith(b"\n"):
        lines.pop()
    total = len(lines)
    if offset > total:
        return _error(f"{path} has {total} lines. offset={offset} is past the end.")
    shown: list[str] = []
    used = 0
    for number in range(offset, min(offset + limit, total + 1)):
        line = _numbered(number, lines[number - 1])
        added = len(line) + (1 if shown else 0)
        if shown and used + added > cap - _ROOM:
            break
        shown.append(line)
        used += added
    last = offset + len(shown) - 1
    where = (
        "That is the end of the file."
        if last == total
        else f"Use offset={last + 1} to continue."
    )
    shown.append(f"[Lines {offset} to {last} of {total}. {where}]")
    return ToolResult("\n".join(shown), False, "none")


def _numbered(number: int, line: str) -> str:
    if len(line) > MOST_LINE_CHARS:
        more = len(line) - MOST_LINE_CHARS
        line = f"{line[:MOST_LINE_CHARS]} [... {more} more characters on this line]"
    return f"{number:>6}\t{line}"


def _error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")
