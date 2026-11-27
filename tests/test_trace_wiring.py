"""How the entry point builds the trace sink and its cursor, and refuses a far one."""

import asyncio
import dataclasses
import json
import os
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

from lamplighter.app.chat import OFF_THIS_MAC
from lamplighter.app.config import Config, TraceSettings, load_config
from lamplighter.app.main import NO_KEY, main, trace_cursor, trace_sink
from lamplighter.ports import CursorMark, canonical_json
from tests.fake_clock import FakeClock
from tests.phoenix_stand_in import stand_in
from tests.test_phoenix_sink import BATCH

SETTINGS = Path(__file__).parents[1] / "lamplighter.toml"
MARKER = "made-up-marker-in-the-setting"
PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy")


@pytest.fixture
def no_input(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Give the chat a standard input it can take the number of, which is empty."""
    with Path(os.devnull).open() as empty:
        monkeypatch.setattr(sys, "stdin", empty)
        yield


def settings_with(tmp_path: Path, phoenix_url: str) -> Path:
    path = tmp_path / "lamplighter.toml"
    text = SETTINGS.read_text()
    text = text.replace('dir = "~/.lamplighter"', f'dir = "{tmp_path / "store"}"')
    text = text.replace('"http://127.0.0.1:6006"', f'"{phoenix_url}"')
    path.write_text(text)
    return path


@pytest.mark.usefixtures("no_input")
@pytest.mark.parametrize(
    "setting",
    [
        f"http://localhost:6006/{MARKER}",
        f"http://127.0.0.1:6006/{MARKER}",
        f"http://127.0.0.1:6006/é{MARKER}",
        f"https://127.0.0.1:6006#{MARKER}",
    ],
)
def test_a_chat_with_a_trace_store_off_this_mac_is_refused_before_anything_is_made(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setting: str,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "made-up-key-0000")

    assert main(["--config", str(settings_with(tmp_path, setting)), "chat"]) == 1
    err = capsys.readouterr().err
    assert err == OFF_THIS_MAC + "\n"
    assert MARKER not in err
    assert not (tmp_path / "store").exists()


@pytest.mark.usefixtures("no_input")
def test_the_refusal_comes_after_the_key_and_the_session_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = str(settings_with(tmp_path, "http://localhost:6006"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["--config", settings, "chat"]) == 1
    assert capsys.readouterr().err == NO_KEY + "\n"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "made-up-key-0000")
    assert main(["--config", settings, "chat", "--session", "a b"]) == 1
    assert "breaks the id rule" in capsys.readouterr().err


def test_the_text_of_the_refusal_is_the_one_in_the_spec() -> None:
    assert OFF_THIS_MAC == (
        "Traces go only to a trace store on this Mac. traces.phoenix_url must be"
        " http://127.0.0.1: and a port, such as http://127.0.0.1:6006."
    )


def config_for(tmp_path: Path, phoenix_url: str, project: str = "made-up") -> Config:
    config = load_config(SETTINGS)
    return dataclasses.replace(
        config,
        store=dataclasses.replace(config.store, dir=str(tmp_path)),
        traces=TraceSettings(phoenix_url=phoenix_url, project=project),
    )


def test_the_settings_in_the_repo_give_a_sink_that_stays_on_this_mac() -> None:
    assert not trace_sink(load_config(SETTINGS), FakeClock()).leaves_this_mac


def test_the_sink_takes_no_proxy_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with stand_in() as proxy, stand_in() as server:
        for name in PROXIES:
            monkeypatch.setenv(name, proxy.url)
        sink = trace_sink(config_for(tmp_path, server.url), FakeClock())
        result = asyncio.run(sink.export(BATCH))
        reached = (len(proxy.asked), len(server.posts()))

    assert result is None
    assert reached == (0, 1)


def cursor_file_after_a_move(tmp_path: Path, phoenix_url: str) -> str:
    cursor = trace_cursor(config_for(tmp_path, phoenix_url))
    assert asyncio.run(cursor.move(CursorMark(0, 0), 5)) is True
    return (tmp_path / "trace_cursor.json").read_text()


def targets_in(written: str) -> list[str]:
    return list(json.loads(written)["cursors"])


@pytest.mark.parametrize("setting", ["http://127.0.0.1:6006", "http://127.0.0.1:6006/"])
def test_the_cursor_is_kept_for_the_address_the_rule_gives_back(
    tmp_path: Path, setting: str
) -> None:
    target = canonical_json(["http://127.0.0.1:6006", "made-up", 1])
    written = cursor_file_after_a_move(tmp_path, setting)

    assert written == canonical_json(
        {"cursors": {target: {"pos": 5, "resets": 0}}, "v": 1}
    )


def test_a_setting_that_fails_the_rule_never_goes_into_the_cursor(
    tmp_path: Path,
) -> None:
    target = canonical_json(["", "made-up", 1])
    written = cursor_file_after_a_move(tmp_path, f"http://é{MARKER}:6006")

    assert MARKER not in written
    assert targets_in(written) == [target]
