"""The script that starts Phoenix passes on nothing of the caller's but PATH and HOME.

A stand-in for `uv`, first on PATH, writes down the environment and the
arguments it was started with and ends, so no Phoenix is needed.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

START = Path(__file__).parents[1] / "scripts" / "phoenix" / "start.sh"
STAND_IN = """#!/bin/sh
/usr/bin/env > "{record}.env"
printf '%s\\n' "$@" > "{record}.args"
pwd > "{record}.pwd"
"""
# What the stand-in's own shell may add to what it was given.
SHELLS_OWN = {"PWD", "OLDPWD", "SHLVL", "_"}
HOSTILE = {
    "UV_ENV_FILE": "{tmp}/more.env",
    "ANTHROPIC_API_KEY": "made-up-key-0000",
    "OPENAI_API_KEY": "made-up-key-0001",
    "PHOENIX_SQL_DATABASE_URL": "sqlite:///{tmp}/elsewhere.db",
    "PHOENIX_HOST": "0.0.0.0",
    "PHOENIX_SERVER_INSTRUMENTATION_OTLP_TRACE_COLLECTOR_HTTP_ENDPOINT": "http://x",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
    "PYTHONPATH": "{tmp}",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "CDPATH": "{tmp}",
}


def started(
    tmp_path: Path, args: list[str], cwd: Path | None = None
) -> tuple[dict[str, str], list[str], str]:
    """Run the script with a hostile environment, and give what `uv` was given."""
    tools, record = tmp_path / "tools", tmp_path / "record"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text(STAND_IN.format(record=record))
    uv.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {name: value.format(tmp=tmp_path) for name, value in HOSTILE.items()}
    env |= {"PATH": f"{tools}:/usr/bin:/bin", "HOME": str(home)}
    subprocess.run([str(START), *args], env=env, cwd=cwd, check=True, timeout=30)
    pairs = Path(f"{record}.env").read_text().splitlines()
    given = dict(pair.split("=", 1) for pair in pairs)
    arguments = Path(f"{record}.args").read_text().splitlines()
    return given, arguments, Path(f"{record}.pwd").read_text().strip()


def test_uv_gets_path_home_and_the_nine_settings_and_nothing_else(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "phoenix"
    given, arguments, where = started(tmp_path, [str(folder), "16006", "14317"])

    passed = {key: value for key, value in given.items() if key not in SHELLS_OWN}
    assert passed == {
        "PATH": f"{tmp_path / 'tools'}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "PHOENIX_WORKING_DIR": str(folder.resolve()),
        "PHOENIX_PORT": "16006",
        "PHOENIX_GRPC_PORT": "14317",
        "PHOENIX_HOST": "127.0.0.1",
        "PHOENIX_TELEMETRY_ENABLED": "false",
        "PHOENIX_ALLOW_EXTERNAL_RESOURCES": "false",
        "PHOENIX_DISABLE_AGENT_ASSISTANT": "true",
        "PHOENIX_AGENTS_DISABLE_BASH": "true",
        "PHOENIX_ENABLE_MCP_SERVER": "false",
    }
    assert arguments == ["run", "--frozen", "phoenix", "serve"]
    assert Path(where).resolve() == START.parent.resolve()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_with_no_arguments_the_folder_is_in_home_and_the_ports_are_the_usual(
    tmp_path: Path,
) -> None:
    given, _, _ = started(tmp_path, [])

    folder = tmp_path / "home" / ".lamplighter-phoenix"
    assert given["PHOENIX_WORKING_DIR"] == str(folder.resolve())
    assert (given["PHOENIX_PORT"], given["PHOENIX_GRPC_PORT"]) == ("6006", "4317")
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_relative_folder_is_taken_from_where_the_caller_is(tmp_path: Path) -> None:
    caller = tmp_path / "caller"
    caller.mkdir()
    # A folder of the same name where the hostile CDPATH points.
    (tmp_path / "here").mkdir()
    given, _, _ = started(tmp_path, ["here"], cwd=caller)

    assert given["PHOENIX_WORKING_DIR"] == str((caller / "here").resolve())
    assert (caller / "here").is_dir()


@pytest.mark.parametrize(
    "name", ["pyproject.toml", "uv.lock", "start.sh"], ids=["project", "lock", "script"]
)
def test_the_phoenix_folder_holds_the_three_files(name: str) -> None:
    made = START.parent / name
    assert made.is_file()
    if name == "start.sh":
        assert os.access(made, os.X_OK)


def test_the_phoenix_project_pins_the_viewer_and_the_lock_holds_it() -> None:
    project = (START.parent / "pyproject.toml").read_text()
    lock = (START.parent / "uv.lock").read_text()

    assert 'dependencies = ["arize-phoenix==22.1.0"]' in project
    assert 'name = "arize-phoenix"\nversion = "22.1.0"' in lock
