"""One trial's transcript as lines, from the events of its exported session."""

from lamplighter.ports import JsonValue, canonical_json, read_saved_json

# A result longer than this is cut, so one large output does not hide the rest.
MOST_RESULT_CHARS = 2_000


def transcript_lines(events_text: str) -> list[str]:
    """Give the instruction, each reply and tool call, each result cut, and the end.

    `events_text` is the export's `events.jsonl`. A line that is not an
    event is named and passed over, so the rest can still be read.
    """
    lines: list[str] = []
    for number, text in enumerate(events_text.splitlines(), start=1):
        try:
            event = read_saved_json(text)
        except ValueError:
            lines.append(f"(line {number} of the export is not JSON)")
            continue
        lines.extend(_event_lines(event))
    return lines


def _event_lines(event: JsonValue) -> list[str]:
    if not isinstance(event, dict):
        return []
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return []
    match event.get("type"):
        case "user_message":
            return [f"Instruction: {payload.get('text')}"]
        case "model_response":
            return _reply_lines(payload.get("message"))
        case "tool_result":
            return [_result_line(payload)]
        case "run_end":
            detail = payload.get("detail")
            return [f"The run ended: {payload.get('reason')}. {detail or ''}".strip()]
        case _:
            return []


def _reply_lines(message: JsonValue) -> list[str]:
    parts = message.get("parts") if isinstance(message, dict) else None
    lines: list[str] = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("kind") == "text":
            lines.append(f"Reply: {part.get('text')}")
        elif part.get("kind") == "tool_call":
            arguments = canonical_json(part.get("arguments"))
            lines.append(f"Tool call {part.get('id')}: {part.get('name')} {arguments}")
    return lines


def _result_line(payload: dict[str, JsonValue]) -> str:
    text = payload.get("text")
    shown = text if isinstance(text, str) else ""
    if len(shown) > MOST_RESULT_CHARS:
        shown = f"{shown[:MOST_RESULT_CHARS]}\n(cut: {len(shown)} characters in all)"
    error = " (an error)" if payload.get("is_error") is True else ""
    return f"Result of {payload.get('call_id')}{error}: {shown}"
