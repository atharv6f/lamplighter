"""A transcript is read from the export's events, and a broken line does not stop it."""

import json

from lamplighter.app.bench_show import transcript_lines
from lamplighter.ports import JsonValue


def test_a_line_that_is_not_json_is_named_and_the_rest_is_read() -> None:
    good = json.dumps({"type": "user_message", "payload": {"text": "Made up."}})
    assert transcript_lines(f"{{not json\n{good}\n") == [
        "(line 1 of the export is not JSON)",
        "Instruction: Made up.",
    ]


def test_an_error_result_and_a_run_end_with_a_detail_say_so() -> None:
    events: list[JsonValue] = [
        {
            "type": "tool_result",
            "payload": {"call_id": "c1", "text": "No.", "is_error": True},
        },
        {"type": "run_end", "payload": {"reason": "limit", "detail": "steps"}},
        {"type": "model_request", "payload": {}},
    ]
    text = "\n".join(json.dumps(e) for e in events)
    assert transcript_lines(text) == [
        "Result of c1 (an error): No.",
        "The run ended: limit. steps",
    ]
