"""No code in `src/` starts a tool's process but the runner, and only the wall calls it.

This is how "no path runs a command without the wall" stays true as the
code grows: a new file that starts a process, or calls the runner with no
wall in front, fails here. A few files start a fixed program of their own,
such as git, and each is listed with the programs it may start.
"""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src" / "lamplighter"
RUNNER = "lamplighter.adapters.command_run"
# The only file that may call the runner: the wall's adapter.
WALL = "adapters/seatbelt/executor.py"
STARTS = {"subprocess", "multiprocessing", "pty"}
# Files that start a fixed program of their own and never a tool's command.
# Each start there must name its program as a word in the code itself, so
# no command the model wrote can reach it.
FIXED_PROGRAMS = {
    "app/git_output.py": {"git"},
    "adapters/harbor_jobs/leftovers.py": {"docker"},
    "adapters/harbor_jobs/runner.py": {"/bin/sh"},
    "adapters/openai_chat/port_owner.py": {"/usr/sbin/lsof", "/bin/ps"},
    "app/memory_watch.py": {"/usr/sbin/lsof"},
    "app/keep_awake.py": {"/usr/bin/caffeinate"},
}
# The starts whose first word can be read: a list written in the call.
NAMED_STARTS = {"subprocess.run", "subprocess.Popen"}
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


def programs_started(tree: ast.AST) -> list[str | None]:
    """Give the program of each `subprocess.run` or `Popen`, by its first word.

    A first word that is not written in the code, such as a variable, is
    None, and so is a start of any other kind.
    """
    found: list[str | None] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        starts_os = name.removeprefix("os.") in OS_STARTS or name.startswith(
            ("os.exec", "os.spawn")
        )
        if name.startswith(("subprocess.", "asyncio.create_subprocess")) or starts_os:
            found.append(_first_word(node) if name in NAMED_STARTS else None)
    return found


def _first_word(call: ast.Call) -> str | None:
    if not call.args or not isinstance(call.args[0], ast.List | ast.Tuple):
        return None
    first = call.args[0].elts[0] if call.args[0].elts else None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def test_only_the_runner_and_the_files_of_fixed_programs_start_a_process() -> None:
    starters = {
        name
        for name, tree in sources().items()
        if {m.split(".")[0] for m in modules_imported(tree)} & STARTS or os_calls(tree)
    }
    assert starters == {"adapters/command_run.py", *FIXED_PROGRAMS}


def test_a_file_of_fixed_programs_starts_only_the_programs_it_names() -> None:
    trees = sources()
    for name, programs in FIXED_PROGRAMS.items():
        started = programs_started(trees[name])
        assert started, name
        assert set(started) <= programs, (name, started)


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
    starts = ast.parse(
        "subprocess.run(['git', 'status'])\n"
        "subprocess.run([program, 'x'])\n"
        "subprocess.Popen(['git'])\n"
        "subprocess.Popen([program])\n"
        "subprocess.call(['git'])\n"
        "os.system('git')\n"
        "os.path.realpath('git')\n"
    )
    assert programs_started(starts) == ["git", None, "git", None, None, None]
