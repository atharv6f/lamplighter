"""web_fetch: the pinned request, the redirect rules, the limits and each error text.

The network is a mock transport and a made-up resolver, except for the
certificate tests, which run a real TLS server on a local socket.
"""

import asyncio
import gzip
import random
import shutil
import socket
import ssl
import tempfile
import tracemalloc
import zlib
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx
import pytest
import trustme

from lamplighter.adapters.builtin_tools import WebAccess, system_web
from lamplighter.adapters.builtin_tools.web_fetch import web_fetch
from lamplighter.ports import ToolResult
from tests.builtin_kit import settings_for, tools_on
from tests.waiting_clock import WaitingClock

PUBLIC = "93.184.216.34"
HTML = "<html><body><h1>Tea</h1><p>Green and <a href='/b'>black</a>.</p></body></html>"


@dataclass
class Net:
    """A made-up network: names and their addresses, and pages by URL."""

    names: dict[str, list[tuple[str, ...]]] = field(
        default_factory=lambda: {
            "example.test": [(PUBLIC,)],
            "www.example.test": [(PUBLIC,)],
        }
    )
    pages: dict[str, "Answer"] = field(default_factory=dict[str, "Answer"])
    asked: list[str] = field(default_factory=list[str])
    sent: list[httpx.Request] = field(default_factory=list[httpx.Request])

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.asked.append(host)
        answers = self.names.get(host)
        if not answers:
            raise socket.gaierror(socket.EAI_NONAME, "made-up: no such name")
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        host = request.headers["host"]
        url = f"{request.url.scheme}://{host}{request.url.raw_path.decode()}"
        if url not in self.pages:
            return page("none", 404, kind="text/plain")()
        return self.pages[url]()

    def web(self) -> WebAccess:
        return WebAccess(self.resolve, lambda: httpx.MockTransport(self.answer))


type Answer = Callable[[], httpx.Response]


def page(
    body: bytes | str = HTML,
    status: int = 200,
    kind: str = "text/html; charset=utf-8",
    packed: str | None = None,
) -> Answer:
    # A body that streams, as from a server: httpx reads, and unpacks, a body
    # given whole as soon as the response is made.
    kept = {"content-type": kind}
    if packed is not None:
        kept["content-encoding"] = packed
    content = body.encode() if isinstance(body, str) else body

    async def chunks() -> AsyncIterator[bytes]:
        for at in range(0, len(content), 65_536):
            yield content[at : at + 65_536]

    return lambda: httpx.Response(status, headers=kept, content=chunks())


def redirect(to: str, status: int = 302) -> Answer:
    return lambda: httpx.Response(status, headers={"location": to})


def fetch(work: Path, net: Net, url: str, **settings: int) -> ToolResult:
    chosen = replace(settings_for(work), **settings)
    return asyncio.run(web_fetch(chosen, net.web(), WaitingClock(), {"url": url}))


def error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return tmp_path


def test_a_page_comes_as_three_lines_and_its_text(work: Path) -> None:
    net = Net(pages={"https://example.test/a": page()})
    assert fetch(work, net, "https://example.test/a") == ToolResult(
        "URL: https://example.test/a\nStatus: 200\nType: text/html\n\n"
        "Tea\n\nGreen and black (/b).",
        False,
        "none",
    )


def test_the_request_goes_to_the_address_with_the_name_in_host_and_handshake(
    work: Path,
) -> None:
    net = Net(pages={"https://example.test:8443/a?q=1": page()})
    fetch(work, net, "https://example.test:8443/a?q=1")
    (sent,) = net.sent
    assert str(sent.url) == f"https://{PUBLIC}:8443/a?q=1"
    assert sent.headers["host"] == "example.test:8443"
    assert sent.extensions["sni_hostname"] == "example.test"
    assert sent.headers["user-agent"] == "Lamplighter"
    assert sent.headers["accept-encoding"] == "gzip, deflate"
    assert "cookie" not in sent.headers and "authorization" not in sent.headers


def test_the_name_is_looked_up_once_and_the_first_answer_is_used(work: Path) -> None:
    net = Net(pages={"http://example.test/": page()})
    net.names["example.test"] = [(PUBLIC, "8.8.8.8"), ("127.0.0.1",)]
    result = fetch(work, net, "http://example.test/")
    assert not result.is_error
    assert net.asked == ["example.test"]
    assert net.sent[0].url.host == PUBLIC


def test_a_status_of_four_hundred_or_more_is_an_error_with_its_text(work: Path) -> None:
    net = Net(
        pages={
            "https://example.test/bad": page("<p>Bad</p>", 400),
            "https://example.test/fine": page("<p>Fine</p>", 399),
        }
    )
    assert fetch(work, net, "https://example.test/bad") == error(
        "URL: https://example.test/bad\nStatus: 400\nType: text/html\n\nBad"
    )
    assert not fetch(work, net, "https://example.test/fine").is_error


def test_a_redirect_on_the_same_host_is_followed_with_www_and_up_to_https(
    work: Path,
) -> None:
    net = Net(
        pages={
            "http://example.test/a": redirect("https://www.example.test/b"),
            "https://www.example.test/b": redirect("/c", 301),
            "https://www.example.test/c": page(),
        }
    )
    result = fetch(work, net, "http://example.test/a")
    assert result.text.startswith("URL: https://www.example.test/c\nStatus: 200")
    assert net.asked == ["example.test", "www.example.test", "www.example.test"]


def test_five_redirects_are_followed_and_a_sixth_is_not(work: Path) -> None:
    pages = {f"https://example.test/{n}": redirect(f"/{n + 1}") for n in range(6)}
    net = Net(pages={**pages, "https://example.test/5": page()})
    assert not fetch(work, net, "https://example.test/0").is_error
    net = Net(pages={**pages, "https://example.test/6": page()})
    assert fetch(work, net, "https://example.test/0") == error(
        "The page redirected more than 5 times."
    )


@pytest.mark.parametrize(
    ("to", "where"),
    [
        ("https://other.test/x", "to another host"),
        ("http://10.0.0.1/", "to another host"),
        ("http://example.test/down", "from https to http"),
    ],
)
def test_a_redirect_elsewhere_or_down_to_http_is_given_and_not_followed(
    work: Path, to: str, where: str
) -> None:
    net = Net(pages={"https://example.test/a": redirect(to)})
    assert fetch(work, net, "https://example.test/a") == ToolResult(
        f"The page redirects {where}: {to}. Nothing was fetched from it."
        " Call web_fetch with that address to read it.",
        False,
        "none",
    )
    assert len(net.sent) == 1


@pytest.mark.parametrize(
    ("url", "text"),
    [
        ("ftp://example.test/x", "web_fetch reads http and https addresses only."),
        ("file:///etc/passwd", "web_fetch reads http and https addresses only."),
        (
            "https://me:secret@example.test/",
            "The address holds a user name or a password. web_fetch does not send"
            " those.",
        ),
        ("https:///no-host", "That is not a web address."),
        ("http://[::1", "That is not a web address."),
    ],
)
def test_an_address_that_is_refused_before_any_look_up(
    work: Path, url: str, text: str
) -> None:
    net = Net()
    assert fetch(work, net, url) == error(text)
    assert net.asked == []


def test_a_name_with_no_ipv4_address_and_one_that_leads_inside(work: Path) -> None:
    net = Net()
    net.names["inside.test"] = [(PUBLIC, "192.168.1.1")]
    assert fetch(work, net, "https://v6only.test/") == error(
        "The name v6only.test could not be found, or has no IPv4 address. web_fetch"
        " connects over IPv4 only."
    )
    assert fetch(work, net, "https://inside.test/") == error(
        "The name inside.test leads to 192.168.1.1, which is not a public address."
        " Nothing was fetched."
    )
    assert net.sent == []


def test_a_body_one_byte_over_the_limit_is_cut_and_says_so(work: Path) -> None:
    net = Net(
        pages={
            "https://example.test/a": page(body="x" * 101, kind="text/plain"),
            "https://example.test/b": page(body="x" * 100, kind="text/plain"),
        }
    )
    cut = fetch(work, net, "https://example.test/a", fetch_max_bytes=100)
    assert cut.text.endswith(
        "\n\n"
        + "x" * 100
        + "\n[The page was longer than 100 bytes. This is its start.]"
    )
    whole = fetch(work, net, "https://example.test/b", fetch_max_bytes=100)
    assert whole.text.endswith("\n\n" + "x" * 100)


def test_a_small_packed_body_stops_at_the_limit_when_unpacked(work: Path) -> None:
    bomb = gzip.compress(b"\0" * 52_428_800)
    assert len(bomb) < 60_000
    net = Net(
        pages={
            "https://example.test/a": page(body=bomb, kind="text/plain", packed="gzip")
        }
    )
    text = fetch(work, net, "https://example.test/a").text
    assert text.endswith("[The page was longer than 5242880 bytes. This is its start.]")
    assert len(text) < 5_242_880 + 200


def test_a_packed_body_stops_at_the_limit_read_from_the_network(work: Path) -> None:
    # Bytes after the end of the packed stream unpack to nothing, so only
    # the count of bytes read can stop them.
    body = gzip.compress(b"made-up") + b"\0" * 1_000
    net = Net(
        pages={
            "https://example.test/a": page(body=body, kind="text/plain", packed="gzip")
        }
    )
    cut = fetch(work, net, "https://example.test/a", fetch_max_bytes=100)
    assert cut.text.endswith(
        "\n\nmade-up\n[The page was longer than 100 bytes. This is its start.]"
    )


def test_deflate_is_unpacked_and_other_packing_is_refused(work: Path) -> None:
    net = Net(
        pages={
            "https://example.test/d": page(
                body=zlib.compress(b"plain words"),
                kind="text/plain",
                packed="deflate",
            ),
            "https://example.test/b": page(body=b"??", kind="text/plain", packed="br"),
            "https://example.test/bad": page(
                body=b"not gzip at all",
                kind="text/plain",
                packed="gzip",
            ),
        }
    )
    assert fetch(work, net, "https://example.test/d").text.endswith("\n\nplain words")
    assert fetch(work, net, "https://example.test/b") == error(
        "The page came packed as br, which web_fetch does not unpack."
    )
    assert fetch(work, net, "https://example.test/bad") == error(
        "The page came packed as gzip, and it could not be unpacked."
    )


def test_the_types_that_are_read_and_one_that_is_not(work: Path) -> None:
    net = Net(
        pages={
            "https://example.test/j": page(body='{"a": 1}', kind="application/json"),
            "https://example.test/f": page(body="<feed/>", kind="application/atom+xml"),
            "https://example.test/i": page(body=b"\x89PNG", kind="image/png"),
        }
    )
    assert fetch(work, net, "https://example.test/j").text.endswith('\n\n{"a": 1}')
    assert fetch(work, net, "https://example.test/f").text.endswith("\n\n<feed/>")
    assert fetch(work, net, "https://example.test/i") == error(
        "The address gave image/png, which web_fetch does not read. It reads HTML and"
        " plain text."
    )


def test_a_character_set_python_knows_that_is_not_a_character_set_reads_as_utf8(
    work: Path,
) -> None:
    for charset in ("zlib_codec", "idna", "undefined", "rot13"):
        net = Net(
            pages={
                "https://example.test/": page(
                    body="café".encode(),
                    kind=f"text/plain; charset={charset}",
                )
            }
        )
        assert fetch(work, net, "https://example.test/").text.endswith("\n\ncafé")
    net = Net(
        pages={
            "https://example.test/": page(
                body=b"caf\xe9", kind='text/plain; charset="ISO-8859-1"'
            )
        }
    )
    assert fetch(work, net, "https://example.test/").text.endswith("\n\ncafé")


def test_each_of_the_sixteen_character_sets_reads_random_bytes() -> None:
    from lamplighter.adapters.builtin_tools.web_fetch import _CHARSETS  # pyright: ignore[reportPrivateUsage]

    data = random.Random(7).randbytes(200_000)
    assert len(_CHARSETS) == 16
    for charset in _CHARSETS:
        data.decode(charset, errors="replace")


def test_a_body_that_drips_is_cut_at_the_deadline(work: Path) -> None:
    async def drip() -> AsyncIterator[bytes]:
        # Long enough for the deadline to land first, short enough to end.
        for _ in range(500):
            await asyncio.sleep(0.002)
            yield b"x"

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, content=drip()
        )

    async def scene() -> ToolResult:
        clock = WaitingClock()
        resolve = Net().resolve
        web = WebAccess(resolve, lambda: httpx.MockTransport(answer))
        task = asyncio.create_task(
            web_fetch(settings_for(work), web, clock, {"url": "https://example.test/"})
        )
        await asyncio.sleep(0.05)
        assert not task.done()
        await clock.advance(30)
        return await task

    assert asyncio.run(scene()) == error(
        "The fetch took longer than 30 seconds and was stopped."
    )


def test_no_connection_and_a_cancelled_fetch(work: Path) -> None:
    assert fetch(work, Net(), "https://example.test/").is_error  # the 404 page
    tools = tools_on(work, web=Net().web())

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("made-up: refused", request=request)

    web = WebAccess(Net().resolve, lambda: httpx.MockTransport(refuse))
    assert asyncio.run(
        web_fetch(
            settings_for(work), web, WaitingClock(), {"url": "https://example.test/"}
        )
    ) == error("Could not connect to example.test.")

    async def scene() -> None:
        task = asyncio.create_task(
            tools.call("web_fetch", {"url": "https://example.test/"})
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scene())


# A real TLS server, on a local socket so that no address rule is bent.


@dataclass
class TlsServer:
    path: str
    names: list[str | None]
    hosts: list[str]


@pytest.fixture
def ca() -> trustme.CA:
    return trustme.CA()


@pytest.fixture
def socket_folder() -> Iterator[Callable[[], str]]:
    # A socket path must be short, so it is not under pytest's long temp path.
    folder = tempfile.mkdtemp(prefix="ll-", dir="/tmp")
    yield lambda: f"{folder}/s"
    shutil.rmtree(folder, ignore_errors=True)


async def serve(
    ca: trustme.CA, name: str, path: str
) -> tuple[asyncio.Server, TlsServer]:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert(name).configure_cert(context)  # pyright: ignore[reportUnknownMemberType]
    seen = TlsServer(path, [], [])
    context.sni_callback = lambda sock, sent, ctx: seen.names.append(sent)  # type: ignore[assignment]

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        for line in head.decode().split("\r\n"):
            if line.lower().startswith("host:"):
                seen.hosts.append(line.split(":", 1)[1].strip())
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n"
            b"Connection: close\r\n\r\nok"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(handle, path, ssl=context)
    return server, seen


def tls_fetch(
    ca: trustme.CA, served_name: str, path: str, work: Path
) -> tuple[ToolResult, TlsServer]:
    client = ssl.create_default_context()
    ca.configure_trust(client)  # pyright: ignore[reportUnknownMemberType]

    async def resolve(host: str, port: int) -> tuple[str, ...]:
        return (PUBLIC,)

    web = WebAccess(resolve, lambda: httpx.AsyncHTTPTransport(verify=client, uds=path))

    async def scene() -> tuple[ToolResult, TlsServer]:
        server, seen = await serve(ca, served_name, path)
        async with server:
            result = await web_fetch(
                settings_for(work),
                web,
                WaitingClock(),
                {"url": "https://example.test/"},
            )
        return result, seen

    return asyncio.run(scene())


def test_the_certificate_is_checked_against_the_name_and_not_the_address(
    ca: trustme.CA, socket_folder: Callable[[], str], work: Path
) -> None:
    result, seen = tls_fetch(ca, "example.test", socket_folder(), work)
    assert result == ToolResult(
        "URL: https://example.test/\nStatus: 200\nType: text/plain\n\nok", False, "none"
    )
    assert seen.names == ["example.test"]
    assert seen.hosts == ["example.test"]


def test_a_certificate_for_another_name_is_not_accepted(
    ca: trustme.CA, socket_folder: Callable[[], str], work: Path
) -> None:
    result, _ = tls_fetch(ca, "other.test", socket_folder(), work)
    assert result == error(
        "The secure connection to example.test failed: its certificate was not"
        " accepted."
    )


def test_a_type_it_does_not_read_is_refused_before_the_body_is_read(work: Path) -> None:
    read: list[bytes] = []

    async def body() -> AsyncIterator[bytes]:
        read.append(b"x")
        yield b"\x89PNG"

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "image/png"}, content=body()
        )

    web = WebAccess(Net().resolve, lambda: httpx.MockTransport(answer))
    result = asyncio.run(
        web_fetch(
            settings_for(work), web, WaitingClock(), {"url": "https://example.test/"}
        )
    )
    assert result.is_error
    assert read == []


def test_a_cookie_from_one_answer_is_not_sent_on_the_redirect(work: Path) -> None:
    net = Net(
        pages={
            "https://example.test/a": lambda: httpx.Response(
                302, headers={"location": "/b", "set-cookie": "session=made-up"}
            ),
            "https://example.test/b": page(),
        }
    )
    assert not fetch(work, net, "https://example.test/a").is_error
    assert [request.headers.get("cookie") for request in net.sent] == [None, None]


def test_unpacking_keeps_at_most_the_limit_in_memory(work: Path) -> None:
    bomb = gzip.compress(b"\0" * 52_428_800)
    net = Net(
        pages={"https://example.test/a": page(bomb, kind="text/plain", packed="gzip")}
    )
    tracemalloc.start()
    try:
        fetch(work, net, "https://example.test/a")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 40_000_000


def test_proxy_settings_in_the_environment_are_ignored(
    ca: trustme.CA,
    socket_folder: Callable[[], str],
    work: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    result, seen = tls_fetch(ca, "example.test", socket_folder(), work)
    assert not result.is_error, result
    assert seen.names == ["example.test"]


def test_the_real_look_up_gives_ipv4_addresses_only() -> None:
    resolve = system_web().resolve

    async def look_up(host: str) -> tuple[str, ...]:
        return await resolve(host, 80)

    assert asyncio.run(look_up("127.0.0.1")) == ("127.0.0.1",)
    assert asyncio.run(look_up("127.1")) == ("127.0.0.1",)
    with pytest.raises(socket.gaierror):
        asyncio.run(look_up("::1"))


@pytest.mark.parametrize("host", ["a..test", ".test", "a" * 64 + ".test"])
def test_a_name_dns_cannot_spell_is_not_found_and_raises_nothing(
    host: str, work: Path
) -> None:
    # The real look-up spells the name before it asks, so no question is
    # sent for these.
    tools = tools_on(work, web=system_web())
    result = asyncio.run(tools.call("web_fetch", {"url": f"http://{host}/"}))
    assert result == ToolResult(
        f"The name {host} could not be found, or has no IPv4 address. web_fetch"
        " connects over IPv4 only.",
        True,
        "none",
    )
