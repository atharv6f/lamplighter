"""A small HTTP server that answers as Phoenix does, on a loopback port, for tests.

It decodes each send with the same message classes the sink uses and keeps
the first copy of each span id, across projects, as Phoenix does. A test
scripts the answers: a status, how many questions pass before stored spans
show up, spans that never show up, a redirect, a body that is not JSON, or
no answer at all.
"""

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.trace.v1.trace_pb2 import Span as WireSpan


@dataclass
class Asked:
    """One request as it arrived: method, path, query and headers."""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]


@dataclass
class Script:
    """What the stand-in answers. Each list is used up from its front."""

    post_statuses: list[int] = field(default_factory=list[int])
    get_statuses: list[int] = field(default_factory=list[int])
    get_bodies: list[bytes] = field(default_factory=list[bytes])
    # Questions answered before what was stored shows up. Until then the
    # project is not there, and a question gets 404.
    hidden_for: int = 0
    never_shown: set[str] = field(default_factory=set[str])
    redirect_to: str | None = None
    hang_posts: bool = False
    hang_gets: bool = False


class StandIn:
    """The server's state. Every method may be called from any thread."""

    def __init__(self) -> None:
        self.script = Script()
        self.asked: list[Asked] = []
        self.bodies: list[bytes] = []
        self.requests: list[ExportTraceServiceRequest] = []
        self.spans: dict[str, tuple[str, WireSpan]] = {}
        self.questions = 0
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.url = ""

    def names(self, project: str | None = None) -> dict[str, str]:
        """Give the name of each kept span, by its hex id."""
        with self.lock:
            return {
                key: span.name
                for key, (where, span) in self.spans.items()
                if project is None or where == project
            }

    def posts(self) -> list[Asked]:
        return [asked for asked in self.asked if asked.method == "POST"]

    def gets(self) -> list[Asked]:
        return [asked for asked in self.asked if asked.method == "GET"]


def _project_of(request: ExportTraceServiceRequest) -> str:
    for kept in request.resource_spans[0].resource.attributes:
        if kept.key == "openinference.project.name":
            return kept.value.string_value
    return "default"


class _Handler(BaseHTTPRequestHandler):
    stand_in: StandIn

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        state = self._noted()
        with state.lock:
            state.bodies.append(body)
            status = (
                state.script.post_statuses.pop(0) if state.script.post_statuses else 200
            )
        if state.script.hang_posts:
            state.release.wait()
            return
        if self._redirected():
            return
        if status == 200:
            self._keep(body)
        self._answer(status, b"")

    def do_GET(self) -> None:
        state = self._noted()
        if state.script.hang_gets:
            state.release.wait()
            return
        if self._redirected():
            return
        with state.lock:
            state.questions += 1
            status = (
                state.script.get_statuses.pop(0) if state.script.get_statuses else 200
            )
            body = state.script.get_bodies.pop(0) if state.script.get_bodies else None
        if body is not None or status != 200:
            self._answer(status, body or b"{}")
            return
        self._answer_spans()

    def _answer_spans(self) -> None:
        state = self.stand_in
        parts = urlsplit(self.path)
        project = parts.path.split("/")[3]
        query = parse_qs(parts.query)
        asked = query.get("span_id", [])
        limit = int(query.get("limit", ["100"])[0])
        with state.lock:
            shown = state.questions > state.script.hidden_for
            stored = {
                key: span
                for key, (where, span) in state.spans.items()
                if where == project and key not in state.script.never_shown
            }
            # Phoenix makes a project as it stores its first span, so while
            # nothing shows the project is not there.
            known = shown and any(where == project for where, _ in state.spans.values())
        if not known:
            self._answer(404, b'{"detail":"not found"}')
            return
        rows = [
            {"context": {"span_id": key, "trace_id": stored[key].trace_id.hex()}}
            for key in (asked or list(stored))
            if shown and key in stored
        ]
        self._answer(
            200, json.dumps({"data": rows[:limit], "next_cursor": None}).encode()
        )

    def _noted(self) -> StandIn:
        state = self.stand_in
        parts = urlsplit(self.path)
        with state.lock:
            state.asked.append(
                Asked(
                    method=self.command,
                    path=parts.path,
                    query=parse_qs(parts.query),
                    headers={key.lower(): value for key, value in self.headers.items()},
                )
            )
        return state

    def _redirected(self) -> bool:
        target = self.stand_in.script.redirect_to
        if target is None:
            return False
        self.send_response(302)
        self.send_header("Location", target + self.path)
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _keep(self, body: bytes) -> None:
        state = self.stand_in
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        project = _project_of(request)
        with state.lock:
            state.requests.append(request)
            for scope in request.resource_spans[0].scope_spans:
                for span in scope.spans:
                    state.spans.setdefault(span.span_id.hex(), (project, span))

    def _answer(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def stand_in() -> Generator[StandIn]:
    """Run a stand-in on a free loopback port for the length of the block."""
    state = StandIn()
    handler = type("Handler", (_Handler,), {"stand_in": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
