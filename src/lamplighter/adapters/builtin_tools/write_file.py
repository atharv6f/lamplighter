"""The write_file tool: put a whole text into a file in the work folder."""

import asyncio
from collections.abc import Callable

from lamplighter.adapters.builtin_tools.file_change import (
    file_plan,
    look_up,
    replace_file,
)
from lamplighter.adapters.builtin_tools.work_paths import (
    MOST_BYTES,
    Refused,
    WorkFolder,
)
from lamplighter.ports import BlobStore, Failure, JsonObject, Plan, ToolResult, ToolSpec

SPEC = ToolSpec(
    name="write_file",
    description=(
        "Write a text file in the work folder, with content as its whole new"
        " text. Makes the file, and any folder above it, if it is not there,"
        " and replaces it if it is. A path is relative to the work folder. To"
        " change part of a file, use edit_file."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 4_096},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)

_TOO_LARGE = f"write_file handles files of up to {MOST_BYTES} bytes."


async def plan_write(
    work: WorkFolder, blobs: BlobStore, arguments: JsonObject
) -> Plan | ToolResult | Failure:
    """Say which file the write replaces, saving its old bytes, or refuse it."""
    path, new = _arguments(arguments)
    if isinstance(new, ToolResult):
        return new
    seen = await asyncio.to_thread(look_up, work, path)
    if isinstance(seen, Refused):
        return _error(seen.text)
    if seen.old is not None and len(seen.old) > MOST_BYTES:
        return _error(_TOO_LARGE)
    if seen.old == new:
        text = f"{path} already holds exactly this content. Nothing was changed."
        return ToolResult(text, False, "none")
    return await file_plan(work, blobs, seen.target, seen.old, new)


def write_job(
    work: WorkFolder, arguments: JsonObject, probe: JsonObject
) -> Callable[[], ToolResult | Failure] | ToolResult:
    """Give the blocking work that carries out a planned write."""
    path, new = _arguments(arguments)
    if isinstance(new, ToolResult):
        return new
    if probe.get("before") is None:
        done = f"Created {path} with {len(new)} bytes."
    else:
        done = f"Wrote {len(new)} bytes to {path}."
    return lambda: replace_file(work, path, probe, lambda old: new, done)


def _arguments(arguments: JsonObject) -> tuple[str, bytes | ToolResult]:
    path, content = arguments.get("path"), arguments.get("content")
    if not isinstance(path, str) or not isinstance(content, str):
        return "", _error("write_file needs a path and a content.")
    try:
        new = content.encode("utf-8")
    except UnicodeEncodeError:
        return path, _error(
            "The content holds a character that cannot be saved as UTF-8."
        )
    if len(new) > MOST_BYTES:
        return path, _error(_TOO_LARGE)
    return path, new


def _error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")
