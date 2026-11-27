"""The real wall on macOS: each way out that a command might try, and what must work.

These run under the `sandbox` marker, on a Mac and in the macOS job. The
folders sit in a folder of the test's own in the home folder, as a real
work folder would. Every file a command tries to read is a decoy the test
wrote. For a real private folder only an exit code is looked at, so no
private file's name or text can reach a test log.
"""

import asyncio
import os
import shutil
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from lamplighter.adapters import SystemClock
from lamplighter.adapters.builtin_tools import ToolSettings
from lamplighter.adapters.builtin_tools.settings import command_env
from lamplighter.adapters.seatbelt import SeatbeltExecutor
from lamplighter.adapters.seatbelt.profile import PROFILES
from lamplighter.ports import Command, ExecResult, Profile

pytestmark = pytest.mark.sandbox

DECOY = "made-up decoy text"
DATA_VOLUME = "/System/Volumes/Data"
HOME_DENY = """(deny file-read*
  (subpath "/Users")
  (subpath "/Volumes")
  (subpath "/Library/Keychains")
  (subpath "/private/var/root"))"""


@dataclass(frozen=True)
class Place:
    """The test's own folder in the home folder, and what is in it."""

    root: Path
    work: Path
    scratch: Path
    state: Path
    beside: Path

    def wall(self, state: Path | None = None) -> SeatbeltExecutor:
        return SeatbeltExecutor(
            str(self.work), str(self.scratch), str(state or self.state), SystemClock()
        )

    def env(self) -> dict[str, str]:
        settings = ToolSettings(
            work_dir=str(self.work),
            scratch_dir=str(self.scratch),
            shell_path="/usr/bin:/bin:/usr/sbin:/sbin",
            shell_default_seconds=120,
            shell_max_seconds=600,
            output_cap_chars=30_000,
            fetch_max_bytes=5_242_880,
            fetch_seconds=30,
        )
        return command_env(settings)

    def run(
        self,
        *argv: str,
        profile: Profile = "workspace_write",
        wall: SeatbeltExecutor | None = None,
    ) -> ExecResult:
        command = Command(
            argv=argv,
            cwd=str(self.work),
            env=self.env(),
            profile=profile,
            wall_ms=30_000,
            capture_bytes=1_000_000,
        )
        ended = asyncio.run((wall or self.wall()).run(command))
        assert isinstance(ended, ExecResult), ended
        return ended

    def shell(self, line: str, profile: Profile = "workspace_write") -> ExecResult:
        return self.run("/bin/sh", "-c", line, profile=profile)


@pytest.fixture
def place() -> Iterator[Place]:
    home = Path(os.path.realpath(Path.home()))
    root = home / f"lamplighter-wall-test-{os.getpid()}"
    made = Place(root, root / "work", root / "scratch", root / "state", root / "beside")
    for folder in (
        made.work,
        made.scratch / "home",
        made.scratch / "tmp",
        made.state,
        made.beside,
    ):
        folder.mkdir(parents=True)
    (made.beside / "secret.txt").write_text(DECOY)
    (made.state / "store.txt").write_text(DECOY)
    try:
        yield made
    finally:
        shutil.rmtree(root, ignore_errors=True)


def exit_code_only(place: Place, line: str) -> int:
    """Run a line with its output thrown away, and give only its exit code."""
    said = place.shell(f"{line} > /dev/null 2>&1; echo $?").output
    return int(said.decode().strip())


# The three ways out the layer is not done without.


def test_a_write_beside_the_work_folder_fails(place: Place) -> None:
    ended = place.run("/usr/bin/touch", str(place.beside / "escape"))
    assert ended.exit_code != 0
    assert sorted(p.name for p in place.beside.iterdir()) == ["secret.txt"]


def test_a_read_of_a_decoy_beside_the_work_folder_fails(place: Place) -> None:
    ended = place.run("/bin/cat", str(place.beside / "secret.txt"))
    assert ended.exit_code != 0
    assert DECOY.encode() not in ended.output


def test_a_command_has_no_network(place: Place) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.5)
    port = listener.getsockname()[1]
    try:
        public = place.run(
            "/usr/bin/curl", "-sS", "--max-time", "5", "https://example.com"
        )
        local = place.run(
            "/usr/bin/curl", "-sS", "--max-time", "5", f"http://127.0.0.1:{port}/"
        )
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        listener.close()
    assert public.exit_code != 0
    assert local.exit_code != 0


# More ways out.


def test_a_real_folder_in_the_home_folder_cannot_be_listed(place: Place) -> None:
    # Every Mac has this folder, so the refusal cannot come from its being
    # missing, as it could for a folder such as ~/.ssh.
    library = Path(os.path.realpath(Path.home())) / "Library"
    assert library.is_dir()
    assert exit_code_only(place, f'/bin/ls "{library}"') != 0


def test_a_grandchild_cannot_write_outside(place: Place) -> None:
    target = place.beside / "grandchild"
    place.shell(f'(/bin/sh -c "/usr/bin/touch {target}") & wait')
    assert not target.exists()


def test_a_link_out_of_the_work_folder_reads_and_writes_nothing(place: Place) -> None:
    secret = place.beside / "secret.txt"
    made = place.run("/bin/ln", "-s", str(secret), "link")
    read = place.run("/bin/cat", "link")
    place.shell("echo changed > link")
    assert made.exit_code == 0
    assert read.exit_code != 0 and DECOY.encode() not in read.output
    assert secret.read_text() == DECOY


@pytest.mark.parametrize("folder", ["work", "scratch"])
def test_a_root_folder_cannot_be_removed_or_moved(place: Place, folder: str) -> None:
    root: Path = getattr(place, folder)
    # The wall's self-test writes in the scratch folder's tmp, so it runs
    # before the folder is emptied, and the same wall runs the rest.
    wall = place.wall()
    place.run("/usr/bin/true", wall=wall)
    for name in list(root.iterdir()):
        shutil.rmtree(name)
    place.run("/bin/rmdir", str(root), wall=wall)
    place.run("/bin/mv", str(root), str(place.work / "moved"), wall=wall)
    place.run("/bin/mv", str(root), str(place.scratch / "moved"), wall=wall)
    assert root.is_dir() and not root.is_symlink()


def test_a_signal_to_a_process_outside_fails(place: Place) -> None:
    # SIGCONT does nothing to a running process, so a wall that let it
    # through would harm nothing.
    ended = place.run("/bin/kill", "-CONT", str(os.getpid()))
    assert ended.exit_code != 0


def test_no_app_can_be_started_or_talked_to(place: Place) -> None:
    before = _running("Calculator")
    opened = place.run("/usr/bin/open", "-a", "Calculator")
    # An application's name is answered from its bundle, with no event
    # sent, so the script asks for something only the running Finder knows.
    told = place.run(
        "/usr/bin/osascript", "-e", 'tell application "Finder" to count windows'
    )
    started = _running("Calculator") - before
    for pid in started:
        os.kill(pid, 9)
    assert opened.exit_code != 0
    assert told.exit_code != 0
    assert started == set()


@pytest.mark.parametrize("profile", ["read_only", "workspace_write"])
def test_the_store_cannot_be_read_written_or_listed(
    place: Place, profile: Profile
) -> None:
    read = place.run("/bin/cat", str(place.state / "store.txt"), profile=profile)
    place.run("/usr/bin/touch", str(place.state / "new"), profile=profile)
    listed = place.run("/bin/ls", str(place.state), profile=profile)
    assert read.exit_code != 0 and DECOY.encode() not in read.output
    assert listed.exit_code != 0 and b"store.txt" not in listed.output
    assert sorted(p.name for p in place.state.iterdir()) == ["store.txt"]


@pytest.mark.parametrize("profile", ["read_only", "workspace_write"])
def test_the_stores_own_rule_holds_outside_the_home_folders(
    place: Place, tmp_path: Path, profile: Profile
) -> None:
    # The temp folder is readable to a command, so only the store's rule
    # can refuse here. The file beside the store shows that.
    state = Path(os.path.realpath(tmp_path)) / "state"
    state.mkdir()
    (state / "store.txt").write_text(DECOY)
    (tmp_path / "open.txt").write_text("readable")
    wall = place.wall(state)
    read = place.run("/bin/cat", str(state / "store.txt"), profile=profile, wall=wall)
    place.run("/usr/bin/touch", str(state / "new"), profile=profile, wall=wall)
    listed = place.run("/bin/ls", str(state), profile=profile, wall=wall)
    beside = place.run("/bin/cat", str(tmp_path / "open.txt"), wall=wall)
    assert beside.output == b"readable"
    assert read.exit_code != 0 and DECOY.encode() not in read.output
    assert listed.exit_code != 0
    assert sorted(p.name for p in state.iterdir()) == ["store.txt"]


def test_read_only_refuses_every_write(place: Place) -> None:
    for target in (place.work / "new", place.scratch / "tmp" / "new"):
        place.run("/usr/bin/touch", str(target), profile="read_only")
        assert not target.exists()
    to_null = place.shell("echo made-up > /dev/null", profile="read_only")
    assert to_null.exit_code == 0


def test_the_second_path_to_the_home_folders_is_closed(place: Place) -> None:
    secret = f"{DATA_VOLUME}{place.beside}/secret.txt"
    read = place.run("/bin/cat", secret)
    home = os.path.realpath(Path.home())
    assert read.exit_code != 0 and DECOY.encode() not in read.output
    assert exit_code_only(place, f'/bin/ls "{DATA_VOLUME}{home}"') != 0


# What must work behind the wall.


def test_git_python_mktemp_and_here_documents_work(place: Place) -> None:
    lines = [
        "git init -q repo && cd repo && echo made-up > a.txt && git add a.txt"
        " && git -c user.name=test -c user.email=test@example.com commit -qm x",
        'python3 -c "import tempfile; tempfile.NamedTemporaryFile().close()"',
        "mktemp",
        "cat <<EOF\nmade-up\nEOF",
        "bash -c 'cat <<EOF\nmade-up\nEOF'",
        "zsh -c 'cat <<EOF\nmade-up\nEOF'",
    ]
    for line in lines:
        ended = place.shell(line)
        assert ended.exit_code == 0, (line, ended.output[-500:])


def test_a_cancel_during_the_self_test_leaves_the_wall_up(place: Place) -> None:
    wall = place.wall()

    def command(*argv: str) -> Command:
        return Command(argv, str(place.work), {}, "read_only", 30_000, 100)

    async def scene() -> tuple[object, object]:
        call = asyncio.ensure_future(wall.run(command("/bin/sleep", "30")))
        # The self-test starts sandbox-exec three times, which takes longer.
        await asyncio.sleep(0.01)
        call.cancel()
        stopped = await call
        after = await wall.run(command("/bin/echo", "after"))
        return stopped, after

    stopped, after = asyncio.run(scene())
    assert isinstance(stopped, ExecResult) and stopped.cancelled
    assert isinstance(after, ExecResult) and after.output == b"after\n"


def test_with_the_home_deny_removed_the_decoy_read_gets_through(
    place: Place, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The planted break for the wall: it shows the read test can fail.
    profile = PROFILES["workspace_write"]
    assert HOME_DENY in profile
    monkeypatch.setitem(PROFILES, "workspace_write", profile.replace(HOME_DENY, ""))
    ended = place.run("/bin/cat", str(place.beside / "secret.txt"))
    assert ended.output == DECOY.encode()


def _running(name: str) -> set[int]:
    found = subprocess.run(
        ["/usr/bin/pgrep", "-x", name], capture_output=True, text=True, check=False
    )
    return {int(pid) for pid in found.stdout.split()}
