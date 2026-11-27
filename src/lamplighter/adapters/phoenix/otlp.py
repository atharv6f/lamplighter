"""Turns a batch of spans into the bytes of one OTLP trace request. Pure.

The only file in this repo that imports OpenTelemetry's message classes.
"""

from collections.abc import Sequence

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import (
    AnyValue,
    ArrayValue,
    InstrumentationScope,
    KeyValue,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
    Status,
)
from opentelemetry.proto.trace.v1.trace_pb2 import (
    Span as WireSpan,
)

from lamplighter.ports import JsonValue, Span

_SERVICE = "lamplighter"
_NS_PER_MS = 1_000_000


def encoded(spans: Sequence[Span], project: str) -> bytes:
    """Give the request that files `spans` under `project` in Phoenix.

    Phoenix picks the project from the resource attribute
    `openinference.project.name`. Raises ValueError on an attribute value of
    a type a span never holds, which is a bug in whoever made the span.
    """
    resource = Resource(
        attributes=[
            _attribute("service.name", _SERVICE),
            _attribute("openinference.project.name", project),
        ]
    )
    scope = ScopeSpans(
        scope=InstrumentationScope(name=_SERVICE),
        spans=[_wire(span) for span in spans],
    )
    request = ExportTraceServiceRequest(
        resource_spans=[ResourceSpans(resource=resource, scope_spans=[scope])]
    )
    return request.SerializeToString()


def _wire(span: Span) -> WireSpan:
    made = WireSpan(
        trace_id=bytes.fromhex(span.trace_id),
        span_id=bytes.fromhex(span.span_id),
        parent_span_id=b"" if span.parent_id is None else bytes.fromhex(span.parent_id),
        name=span.name,
        kind=(
            WireSpan.SpanKind.SPAN_KIND_CLIENT
            if span.kind == "model"
            else WireSpan.SpanKind.SPAN_KIND_INTERNAL
        ),
        start_time_unix_nano=span.start_ms * _NS_PER_MS,
        end_time_unix_nano=(span.start_ms + span.duration_ms) * _NS_PER_MS,
        attributes=[_attribute(key, value) for key, value in span.attributes.items()],
    )
    # With no error the status is left as it is made: unset. OK is never sent.
    if span.error is not None:
        made.status.CopyFrom(Status(code=Status.StatusCode.STATUS_CODE_ERROR))
    return made


def _attribute(key: str, value: JsonValue) -> KeyValue:
    return KeyValue(key=key, value=_value(value))


def _value(value: JsonValue) -> AnyValue:
    # True is a whole number to Python, so it is looked at first.
    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, int):
        return AnyValue(int_value=value)
    if isinstance(value, str):
        return AnyValue(string_value=value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        texts = [AnyValue(string_value=str(item)) for item in value]
        return AnyValue(array_value=ArrayValue(values=texts))
    raise ValueError(f"a span attribute cannot hold {type(value).__name__}")
