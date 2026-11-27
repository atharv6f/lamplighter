"""The edit_file tool: replace one exact piece of a text file in the work folder."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from lamplighter.adapters.builtin_tools.file_change import (
    Seen,
    file_plan,
    look_up,
    replace_file,
)
from lamplighter.adapters.builtin_tools.work_paths import Refused, WorkFolder
from lamplighter.ports import BlobStore, Failure, JsonObject, Plan, ToolResult, ToolSpec

MOST_EDIT_BYTES = 1_048_576

SPEC = ToolSpec(
    name="edit_file",
    description=(
        "Change a text file in the work folder by replacing old_text with"
        " new_text. old_text must match the file exactly, with its spaces and"
        " line breaks, and must occur exactly once: add text around it until"
        " it does. A path is relative to the work folder. Read the file"
        " first, and copy old_text from it."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "minLength": 1, "maxLength": 4_096},
            "old_text": {"type": "string", "minLength": 1},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    },
)


@dataclass(frozen=True, slots=True)
class _Edit:
    path: str
    old_text: str
    new_text: str

    def applied(self, old: bytes | None) -> bytes:
        if old is None:
            raise ValueError("an edit is planned only for a file that is there")
        return old.decode("utf-8").replace(self.old_text, self.new_text, 1).encode()


async def plan_edit(
    work: WorkFolder, blobs: BlobStore, arguments: JsonObject
) -> Plan | ToolResult | Failure:
    """Find old_text once in the file, saving its old bytes, or refuse the edit."""
    edit = _arguments(arguments)
    if isinstance(edit, ToolResult):
        return edit
    if edit.old_text == edit.new_text:
        return _error("old_text and new_text are the same. Nothing would change.")
    seen = await asyncio.to_thread(look_up, work, edit.path)
    if isinstance(seen, Refused):
        return _error(seen.text)
    refused = _refusal(edit, seen)
    if refused is not None:
        return refused
    assert seen.old is not None
    return await file_plan(work, blobs, seen.target, seen.old, edit.applied(seen.old))


def edit_job(
    work: WorkFolder, arguments: JsonObject, probe: JsonObject
) -> Callable[[], ToolResult | Failure] | ToolResult:
    """Give the blocking work that carries out a planned edit."""
    edit = _arguments(arguments)
    if isinstance(edit, ToolResult):
        return edit
    done = (
        f"Edited {edit.path}: replaced {len(edit.old_text)} characters"
        f" with {len(edit.new_text)}."
    )
    return lambda: replace_file(work, edit.path, probe, edit.applied, done)


def _refusal(edit: _Edit, seen: Seen) -> ToolResult | None:
    path = edit.path
    if seen.old is None:
        return _error(f"There is no file at {path}.")
    if seen.size > MOST_EDIT_BYTES or len(seen.old) > MOST_EDIT_BYTES:
        return _error(
            f"{path} is {max(seen.size, len(seen.old))} bytes. edit_file edits"
            f" files of up to {MOST_EDIT_BYTES} bytes."
        )
    try:
        text = seen.old.decode("utf-8")
    except UnicodeDecodeError:
        return _error(
            f"{path} is not valid UTF-8 text, and saving it back would damage it."
            " edit_file did not touch it."
        )
    found = _occurrences(text, edit.old_text)
    if found == 0:
        return _error(_not_found(text, edit))
    if found > 1:
        return _error(
            f"old_text occurs {found} times in {path}. Add more of the text"
            " around it, so that it occurs once."
        )
    return None


def _not_found(text: str, edit: _Edit) -> str:
    said = (
        f"old_text was not found in {edit.path}. It must match the file exactly,"
        " with its spaces and line breaks. Read the file again and copy the"
        " text from it."
    )
    crlf = edit.old_text.replace("\n", "\r\n")
    if "\r\n" in text and "\r\n" not in edit.old_text and crlf in text:
        said += (
            " The file's lines end in a carriage return and a line feed, and"
            " old_text's do not."
        )
    return said


def _occurrences(text: str, part: str) -> int:
    # Places that overlap each count: "aa" occurs twice in "aaa".
    count, at = 0, text.find(part)
    while at != -1:
        count += 1
        at = text.find(part, at + 1)
    return count


def _arguments(arguments: JsonObject) -> _Edit | ToolResult:
    path = arguments.get("path")
    old_text, new_text = arguments.get("old_text"), arguments.get("new_text")
    if not (
        isinstance(path, str)
        and isinstance(old_text, str)
        and isinstance(new_text, str)
        and old_text
    ):
        return _error(
            "edit_file needs a path, an old_text that is not empty and a new_text."
        )
    try:
        new_text.encode("utf-8")
    except UnicodeEncodeError:
        return _error("new_text holds a character that cannot be saved as UTF-8.")
    return _Edit(path, old_text, new_text)


def _error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")
