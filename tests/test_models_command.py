"""The `models` command: one line for each model entry, and one field of one."""

import asyncio
import io
from pathlib import Path

import pytest

from lamplighter.app.config import Config, load_config
from lamplighter.app.main import main
from lamplighter.app.models_command import entry_field, list_models
from lamplighter.ports import Failure
from tests.ladder_kit import HELD, SteeredHealth
from tests.settings_text import GOOD, with_local


def settings(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text)
    return path


def both(tmp_path: Path) -> Config:
    return load_config(settings(tmp_path, with_local()))


def listed(config: Config, health: dict[str, SteeredHealth]) -> list[str]:
    out = io.StringIO()
    asyncio.run(list_models(config, health, out))
    return out.getvalue().splitlines()


def test_each_entry_is_listed_with_where_it_runs_and_its_health(
    tmp_path: Path,
) -> None:
    config = both(tmp_path)
    frontier = next(e for e in config.models if e.name == "frontier").profile
    assert listed(config, {"local": SteeredHealth()}) == [
        f"frontier  anthropic  {frontier.model}  window {frontier.window}"
        "  off this Mac  not checked",
        "local  openai_chat  local  window 65536  this Mac  up",
    ]


def test_a_model_that_is_down_is_listed_with_why(tmp_path: Path) -> None:
    lines = listed(both(tmp_path), {"local": SteeredHealth(HELD)})
    assert lines[1].endswith("this Mac  fatal: Another program.")


def test_a_check_that_does_not_answer_is_listed_as_unavailable(
    tmp_path: Path,
) -> None:
    hung = SteeredHealth(hang=True)
    lines = listed(both(tmp_path), {"local": hung})
    assert lines[1].endswith(
        "this Mac  unavailable: The health check did not answer within two seconds."
    )
    assert hung.cancelled == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "local"),
        ("adapter", "openai_chat"),
        ("model", "local"),
        ("base_url", "http://127.0.0.1:9931"),
        ("window", "65536"),
        ("concurrent_requests", "2"),
        ("tool_calls", "true"),
        ("weights_sha256", "0" * 64),
        ("server", "llama.cpp b12826"),
        ("server_dir", "~/lamplighter-models/llama"),
        ("count", "llama_cpp"),
        ("call_markers", "<tool_call>\n<function="),
    ],
)
def test_one_field_of_a_local_entry_is_given_as_the_script_reads_it(
    tmp_path: Path, field: str, value: str
) -> None:
    assert entry_field(both(tmp_path).models, "local", field) == value


def test_a_field_only_a_local_entry_has_is_not_on_another(tmp_path: Path) -> None:
    answer = entry_field(both(tmp_path).models, "frontier", "base_url")
    assert isinstance(answer, Failure)
    assert "has no field 'base_url'" in answer.message
    assert "window" in answer.message


def test_an_entry_that_is_not_there_is_refused(tmp_path: Path) -> None:
    answer = entry_field(both(tmp_path).models, "elsewhere", "model")
    assert isinstance(answer, Failure)
    assert answer.message == "There is no model entry named 'elsewhere'."


def test_the_field_command_reads_the_settings_it_is_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = settings(tmp_path, with_local())
    command = ["models", "field", "--settings", str(path), "local", "base_url"]
    assert main(command) == 0
    assert capsys.readouterr().out == "http://127.0.0.1:9931\n"
    assert main(["models", "field", "--settings", str(path), "local", "nope"]) == 1
    assert "has no field 'nope'" in capsys.readouterr().err


def test_the_list_command_needs_no_key_and_asks_no_server(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = settings(tmp_path, GOOD)
    assert main(["models", "--settings", str(path)]) == 0
    assert capsys.readouterr().out == (
        "frontier  anthropic  made-up-1  window 200000  off this Mac  not checked\n"
    )
