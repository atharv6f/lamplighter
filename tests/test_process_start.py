"""No code in `src/` starts a process but the runner, and only the wall calls it.

This is how "no path runs a command without the wall" stays true as the
code grows: a new file that starts a process, or calls the runner with no
wall in front, fails here.
"""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src" / "lamplighter"
RUNNER = "lamplighter.adapters.command_run"
# The only file that may call the runner: the wall's adapter.
WALL = "adapters/seatbelt/executor.py"
STARTS = {"subprocess", "multiprocessing", "pty"}
OS_STARTS = {"fork", "forkpty", "posix_spawn", "posix_spawnp", "system", "popen"}


def modules_imported(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def os_calls(tree: ast.AST) -> set[str]:
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and (node.attr in OS_STARTS or node.attr.startswith(("exec", "spawn")))
    }


def sources() -> dict[str, ast.AST]:
    return {
        path.relative_to(SOURCE).as_posix(): ast.parse(path.read_text())
        for path in sorted(SOURCE.rglob("*.py"))
    }


def test_only_the_runner_starts_a_process() -> None:
    starters = {
        name
        for name, tree in sources().items()
        if {m.split(".")[0] for m in modules_imported(tree)} & STARTS or os_calls(tree)
    }
    assert starters == {"adapters/command_run.py"}


def test_only_the_wall_calls_the_runner() -> None:
    callers = {
        name for name, tree in sources().items() if RUNNER in modules_imported(tree)
    }
    assert callers <= {WALL}


def test_the_checks_see_an_import_and_a_call_written_each_way() -> None:
    # A check that cannot see what it looks for would pass for ever.
    seen = ast.parse(
        "import subprocess\n"
        "from lamplighter.adapters import command_run\n"
        "from lamplighter.adapters.command_run import run_command\n"
        "os.execv('a', [])\n"
        "os.posix_spawn('a', [], {})\n"
    )
    assert "subprocess" in modules_imported(seen)
    assert RUNNER in modules_imported(seen)
    assert os_calls(seen) == {"execv", "posix_spawn"}
