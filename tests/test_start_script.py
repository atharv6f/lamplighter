"""The start script passes Harbor only what it names, and refuses three starts."""

import os
import shutil
import subprocess
from pathlib import Path

from lamplighter.app.code_revision import REPO_ROOT

SCRIPT = REPO_ROOT / "bench" / "harbor" / "start.sh"
# Spaces, both quotes, a dollar sign and a star, to arrive as one value.
KEY = "made-up key 'one' \"two\" $HOME *"
_UV = """#!/bin/sh
echo "ARGS: $*"
env
"""


def _started(
    tmp_path: Path, env: dict[str, str], script: Path = SCRIPT
) -> subprocess.CompletedProcess[str]:
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    uv = tools / "uv"
    uv.write_text(_UV)
    uv.chmod(0o755)
    path = f"{tools}{os.pathsep}/usr/bin{os.pathsep}/bin"
    return subprocess.run(
        ["/bin/sh", str(script), "run", "-c", "/made-up/job.json"],
        env={"PATH": path, **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _environment(out: str) -> dict[str, str]:
    lines = out.splitlines()
    assert lines[0] == "ARGS: run --frozen harbor run -c /made-up/job.json"
    return dict(line.split("=", 1) for line in lines[1:] if "=" in line)


def test_harbor_gets_the_named_variables_and_no_others(tmp_path: Path) -> None:
    done = _started(
        tmp_path,
        {
            "HOME": "/made-up/home",
            "ANTHROPIC_API_KEY": KEY,
            "DOCKER_HOST": "unix:///made-up/docker.sock",
            "MADE_UP_SECRET": "not-for-harbor",
            "UV_INDEX_URL": "https://made-up.invalid/simple",
            "UV_PYTHON": "3.13",
            "UV_PYTHON_INSTALL_DIR": "/made-up/pythons",
        },
    )
    assert done.returncode == 0, done.stderr
    env = _environment(done.stdout)
    assert env["ANTHROPIC_API_KEY"] == KEY
    assert env["HOME"] == "/made-up/home"
    assert env["DOCKER_HOST"] == "unix:///made-up/docker.sock"
    assert env["HARBOR_TELEMETRY"] == "0"
    assert env["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert env["PYTHONPATH"] == os.path.realpath(SCRIPT.parent)
    assert env["UV_PYTHON"] == "3.13"
    assert env["UV_PYTHON_INSTALL_DIR"] == "/made-up/pythons"
    assert "MADE_UP_SECRET" not in env
    assert "UV_INDEX_URL" not in env
    assert "DOCKER_CONTEXT" not in env


def test_an_empty_key_is_not_passed(tmp_path: Path) -> None:
    done = _started(tmp_path, {"HOME": "/made-up/home", "ANTHROPIC_API_KEY": ""})
    assert done.returncode == 0, done.stderr
    assert "ANTHROPIC_API_KEY" not in _environment(done.stdout)


def test_a_start_with_no_home_is_refused(tmp_path: Path) -> None:
    done = _started(tmp_path, {})
    assert done.returncode != 0
    assert "HOME is not set" in done.stderr
    assert "ARGS" not in done.stdout


def test_a_start_through_a_link_from_another_folder_is_refused(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "start.sh").symlink_to(SCRIPT)
    done = _started(tmp_path, {"HOME": "/made-up/home"}, elsewhere / "start.sh")
    assert done.returncode == 1
    assert "start it by its own path" in done.stderr
    assert "ARGS" not in done.stdout


def test_a_start_beside_a_file_harbor_would_load_is_refused(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(SCRIPT, project / "start.sh")
    (project / "pyproject.toml").write_text("")
    (project / ".env.local").write_text("")
    done = _started(tmp_path, {"HOME": "/made-up/home"}, project / "start.sh")
    assert done.returncode == 1
    assert ".env.local" in done.stderr
    assert "ARGS" not in done.stdout
