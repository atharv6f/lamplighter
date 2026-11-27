"""The encoder: a batch of spans in, one OTLP request out, field by field."""

import dataclasses

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as WireSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status

from lamplighter.adapters.phoenix.otlp import encoded
from lamplighter.ports import JsonValue, Span, SpanKind

TRACE = "0e9989490d36bd20896dd8df2ee81290"
START = 1_795_678_512_100


def span(
    kind: SpanKind, number: int, parent: str | None, error: str | None = None
) -> Span:
    return Span(
        trace_id=TRACE,
        span_id=f"{number:016x}",
        parent_id=parent,
        name=f"{kind} {number}",
        kind=kind,
        start_ms=START + number,
        duration_ms=250 * number,
        error=error,
        attributes={
            "lamp.step": number,
            "lamp.outside_text": True,
            "gen_ai.request.model": "made-up-1",
            "gen_ai.response.finish_reasons": ["end"],
        },
    )


RUN = span("run", 1, None, error="limit_steps")
STEP = span("step", 2, RUN.span_id)
MODEL = span("model", 3, STEP.span_id, error="timeout")
TOOL = span("tool", 4, STEP.span_id)


def decoded(
    spans: list[Span], project: str = "lamplighter"
) -> ExportTraceServiceRequest:
    request = ExportTraceServiceRequest()
    request.ParseFromString(encoded(spans, project))
    return request


def plain(value: AnyValue) -> object:
    which = value.WhichOneof("value")
    assert which is not None
    if which == "array_value":
        return [plain(item) for item in value.array_value.values]
    return (which, getattr(value, which))


def test_one_span_of_each_kind_arrives_field_by_field() -> None:
    request = decoded([RUN, STEP, MODEL, TOOL])
    [resource_spans] = request.resource_spans
    [scope] = resource_spans.scope_spans

    assert scope.scope.name == "lamplighter"
    assert scope.scope.version == ""
    for sent, wire in zip([RUN, STEP, MODEL, TOOL], scope.spans, strict=True):
        assert wire.trace_id == bytes.fromhex(TRACE)
        assert len(wire.trace_id) == 16
        assert wire.span_id == bytes.fromhex(sent.span_id)
        assert len(wire.span_id) == 8
        assert wire.parent_span_id == (
            b"" if sent.parent_id is None else bytes.fromhex(sent.parent_id)
        )
        assert wire.name == sent.name
        assert wire.start_time_unix_nano == sent.start_ms * 1_000_000
        assert wire.end_time_unix_nano == (sent.start_ms + sent.duration_ms) * 1_000_000
        assert {kept.key: plain(kept.value) for kept in wire.attributes} == {
            "lamp.step": ("int_value", sent.attributes["lamp.step"]),
            "lamp.outside_text": ("bool_value", True),
            "gen_ai.request.model": ("string_value", "made-up-1"),
            "gen_ai.response.finish_reasons": [("string_value", "end")],
        }
    kinds = [wire.kind for wire in scope.spans]
    assert kinds == [
        WireSpan.SpanKind.SPAN_KIND_INTERNAL,
        WireSpan.SpanKind.SPAN_KIND_INTERNAL,
        WireSpan.SpanKind.SPAN_KIND_CLIENT,
        WireSpan.SpanKind.SPAN_KIND_INTERNAL,
    ]
    codes = [wire.status.code for wire in scope.spans]
    assert codes == [
        Status.StatusCode.STATUS_CODE_ERROR,
        Status.StatusCode.STATUS_CODE_UNSET,
        Status.StatusCode.STATUS_CODE_ERROR,
        Status.StatusCode.STATUS_CODE_UNSET,
    ]
    assert all(wire.status.message == "" for wire in scope.spans)


def test_the_resource_names_the_service_and_the_project() -> None:
    [resource_spans] = decoded([STEP], project="made-up-project").resource_spans

    assert {
        kept.key: plain(kept.value) for kept in resource_spans.resource.attributes
    } == {
        "service.name": ("string_value", "lamplighter"),
        "openinference.project.name": ("string_value", "made-up-project"),
    }


@pytest.mark.parametrize("value", [1.5, None, {"a": 1}, [1, 2], ("a",)])
def test_a_value_a_span_never_holds_raises(value: JsonValue) -> None:
    odd = dataclasses.replace(STEP, attributes={"lamp.odd": value})

    with pytest.raises(ValueError, match="cannot hold"):
        encoded([odd], "lamplighter")
