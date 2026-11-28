"""The local model script checks the weights, then starts each server with its flags.

The servers and `uv` are made-up programs that print what they were given,
so the flags and the environment each server starts with can be read.
"""

import os
import subprocess
from pathlib import Path

from lamplighter.app.code_revision import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "local_model.sh"
WEIGHTS_HASH = "0" * 64
# What `models field` answers for the made-up entry, by field.
FIELDS = {
    "weights_sha256": WEIGHTS_HASH,
    "server": "llama.cpp b12826",
    "server_dir": "~/lamplighter-models/server",
    "base_url": "http://127.0.0.1:9931",
    "model": "local",
    "window": "65536",
    "concurrent_requests": "2",
}
_SAID = """#!/bin/sh
echo "ARGS: $*"
env | sort
"""


def made_up_uv(tools: Path, fields: dict[str, str], weights_hash: str) -> None:
    answers = "\n".join(
        f'  *" {name}") echo "{value}" ;;' for name, value in fields.items()
    )
    (tools / "uv").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *" weights-hash "*) echo "{weights_hash}" ;;\n'
        f"{answers}\n"
        '  *) echo "made-up uv was asked: $*" >&2; exit 9 ;;\n'
        "esac\n"
    )
    (tools / "uv").chmod(0o755)


def started(
    tmp_path: Path,
    weights: str = "Qwen3.6-35B-A3B-Q4_K_M.gguf",
    weights_hash: str = WEIGHTS_HASH,
    script: Path = SCRIPT,
    **changes: str,
) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    for program in (
        "server/bin/llama-server",
        "server/.venv/bin/vllm",
        "server/.venv/bin/mlx_lm.server",
    ):
        path = home / "lamplighter-models" / program
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_SAID)
        path.chmod(0o755)
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    made_up_uv(tools, {**FIELDS, **changes}, weights_hash)
    return subprocess.run(
        ["/bin/sh", str(script), "made-up.toml", "local", f"/made-up/{weights}"],
        env={
            "PATH": f"{tools}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(home),
            "ANTHROPIC_API_KEY": "made-up-key",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def args_and_env(found: subprocess.CompletedProcess[str]) -> tuple[str, list[str]]:
    assert found.returncode == 0, found.stderr
    first, *env = found.stdout.splitlines()
    return first, env


def test_llama_server_starts_on_loopback_with_a_window_for_each_slot(
    tmp_path: Path,
) -> None:
    args, env = args_and_env(started(tmp_path))
    weights = "/made-up/Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert args == (
        f"ARGS: --host 127.0.0.1 --port 9931 -m {weights} --alias local --jinja"
        " -np 2 -c 131072 --no-webui --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0"
    )
    home = tmp_path / "home"
    server_bin = home / "lamplighter-models" / "server" / "bin"
    # `env` prints PWD and SHLVL of its own; nothing else of the shell's.
    assert [line for line in env if not line.startswith(("PWD=", "SHLVL=", "_="))] == [
        "HF_HUB_OFFLINE=1",
        f"HOME={home}",
        f"PATH={server_bin}:/usr/bin:/bin",
        "TRANSFORMERS_OFFLINE=1",
    ]


def test_weights_that_do_not_match_the_entry_are_refused(tmp_path: Path) -> None:
    found = started(tmp_path, weights_hash="f" * 64)
    assert found.returncode == 1
    assert "the weights do not match the settings for local" in found.stderr
    assert "ARGS" not in found.stdout


def test_weights_of_a_model_with_no_known_sampling_are_refused(tmp_path: Path) -> None:
    found = started(tmp_path, weights="made-up-model.gguf")
    assert found.returncode == 2
    assert "no sampling is known for the weights made-up-model.gguf" in found.stderr


def test_each_model_s_kv_size_sets_mlx_lm_s_cache_cap(tmp_path: Path) -> None:
    for weights, kv_bytes in (
        ("Qwen3.6-35B-A3B-4bit", 20_480),
        ("Qwen3.8-27B-4bit", 65_536),
    ):
        args, env = args_and_env(
            started(tmp_path, weights, server="mlx-lm 0.32.2", model="default_model")
        )
        assert args == (
            f"ARGS: --host 127.0.0.1 --port 9931 --model /made-up/{weights}"
            " --decode-concurrency 2 --prompt-concurrency 2"
            f" --prompt-cache-bytes {kv_bytes * 65_536 * 2}"
            " --allowed-origins http://127.0.0.1:9931"
            " --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0"
        )
        assert "ANTHROPIC_API_KEY=made-up-key" not in env


def test_vllm_metal_is_not_started_before_its_memory_share_is_set(
    tmp_path: Path,
) -> None:
    found = started(tmp_path, server="vllm-metal 0.34.0")
    assert found.returncode == 2
    assert "set vllm_fraction" in found.stderr
    assert "ARGS" not in found.stdout


def test_vllm_metal_starts_with_its_parsers_limits_and_reports_off(
    tmp_path: Path,
) -> None:
    script = tmp_path / "scripts" / "local_model.sh"
    script.parent.mkdir()
    script.write_text(
        SCRIPT.read_text().replace('vllm_fraction=""', 'vllm_fraction="0.45"')
    )
    found = started(tmp_path, script=script, server="vllm-metal 0.34.0")
    args, env = args_and_env(found)
    assert args == (
        "ARGS: serve /made-up/Qwen3.6-35B-A3B-Q4_K_M.gguf --host 127.0.0.1"
        " --port 9931 --served-model-name local --max-model-len 65536"
        " --max-num-seqs 2 --gpu-memory-utilization 0.45"
        " --enable-auto-tool-choice --tool-call-parser qwen3_coder"
        " --reasoning-parser qwen3 --enable-prompt-tokens-details"
        ' --override-generation-config {"temperature": 0.6, "top_p": 0.95,'
        ' "top_k": 20, "min_p": 0}'
    )
    assert "VLLM_NO_USAGE_STATS=1" in env
    assert "DO_NOT_TRACK=1" in env


def test_an_unknown_server_is_refused(tmp_path: Path) -> None:
    found = started(tmp_path, server="made-up-server 1")
    assert found.returncode == 2
    assert "unknown server: made-up-server" in found.stderr


def test_the_script_takes_exactly_three_arguments() -> None:
    found = subprocess.run(
        ["/bin/sh", str(SCRIPT), "made-up.toml"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert found.returncode == 2
    assert "usage:" in found.stderr
