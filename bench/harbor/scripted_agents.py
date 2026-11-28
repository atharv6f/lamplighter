"""This repo's harness with a scripted model in place of the real one, for the tests.

Each class is the harness's agent class with one other trial: the real
trial function on the real parts, but with a model that plays a fixed
script and makes no network call. Harbor imports a class by module, and the
start script puts only this folder on the path, so the classes live here
and not under tests/.
"""

# pyright checks this file in standard mode, inside the bench's own
# project: Harbor ships no type marker, so strict mode cannot see its types.

import dataclasses
import random
from collections.abc import Awaitable, Callable

from harbor_shim.agent import HarnessAgent

from lamplighter.adapters import ScriptedBreak, ScriptedModel, ScriptedReply
from lamplighter.app.bench_trial import TrialModel, TrialRequest, run_trial
from lamplighter.app.code_revision import REPO_ROOT, code_revision
from lamplighter.app.config import Config
from lamplighter.app.main import trial_parts
from lamplighter.bench import TrialRecord
from lamplighter.ports import Failure, TextPart, ToolCallPart, Usage

type Turn = ScriptedReply | ScriptedBreak
type Trial = Callable[[TrialRequest], Awaitable[TrialRecord]]

_USAGE = Usage(input_uncached=1000, cache_read=0, cache_write=0, output=50)
_DONE = ScriptedReply((TextPart(text="Done."),), "end", _USAGE)
_FIX = ToolCallPart(
    id="fix",
    name="edit_file",
    arguments={
        "path": "settings.ini",
        "old_text": "port = 80",
        "new_text": "port = 8080",
    },
)


def _calls(*parts: ToolCallPart) -> ScriptedReply:
    return ScriptedReply(parts, "tool_calls", _USAGE)


def _shell(ident: str, command: str) -> ScriptedReply:
    return _calls(ToolCallPart(id=ident, name="shell", arguments={"command": command}))


def scripted(*turns: Turn) -> Trial:
    """Give a trial whose model plays the turns, on every other real part."""

    async def trial(request: TrialRequest) -> TrialRecord:
        parts = trial_parts("", code_revision(REPO_ROOT))

        def model(config: Config) -> TrialModel:
            return TrialModel(
                ScriptedModel(config.model, turns, random.Random(1)), _nothing
            )

        return await run_trial(dataclasses.replace(parts, model=model), request)

    return trial


async def _nothing() -> None:
    return None


class FixesThePort(HarnessAgent):
    """Reads the settings file, changes the one line, and runs a command."""

    trial = staticmethod(
        scripted(
            _calls(
                ToolCallPart(
                    id="r", name="read_file", arguments={"path": "settings.ini"}
                )
            ),
            _calls(_FIX),
            _shell("s", "grep -c . settings.ini && id -u"),
            _DONE,
        )
    )


class DoesNothing(HarnessAgent):
    """Says it is done and changes nothing."""

    trial = staticmethod(scripted(_DONE))


class ModelOutage(HarnessAgent):
    """The model service fails before the first reply ends."""

    trial = staticmethod(
        scripted(
            ScriptedBreak(
                (),
                Failure("unavailable", "none", "Made up: the service is down.", "m"),
            )
        )
    )


class LeavesAWriter(HarnessAgent):
    """Changes nothing, and leaves a process behind that writes a reward of 1."""

    trial = staticmethod(
        scripted(
            _shell(
                "w",
                "setsid nohup bash -c 'while :; do mkdir -p /logs/verifier;"
                " echo 1 > /logs/verifier/reward.txt; sleep 0.05; done'"
                " >/dev/null 2>&1 & sleep 0.3; cat /logs/verifier/reward.txt",
            ),
            _DONE,
        )
    )


class AttacksItsResult(HarnessAgent):
    """Forges an answer, leaves a process that signals everything, then fixes it."""

    trial = staticmethod(
        scripted(
            _shell(
                "f",
                "tok=$(tr '\\0' '\\n' </proc/$PPID/cmdline"
                " | grep -A3 '^/tmp/.run-' | sed -n 4p);"
                ' { echo "$tok exited 0 6"; printf forged | base64; }'
                " >/proc/$PPID/fd/1;"
                " echo the real output; exit 3",
            ),
            _shell(
                "h",
                "setsid bash -c 'while :; do for p in /proc/[0-9]*; do"
                " kill -TERM ${p#/proc/} 2>/dev/null; done; sleep 0.01; done'"
                " >/dev/null 2>&1 & echo started",
            ),
            _shell("k", "kill -9 -1; echo after"),
            _calls(_FIX),
            _DONE,
        )
    )


class LeavesDeepFolders(HarnessAgent):
    """Fixes the line and leaves 150 folders, each inside the one before."""

    trial = staticmethod(
        scripted(
            _calls(_FIX),
            _shell("d", "mkdir -p deep/$(printf 'd/%.0s' $(seq 150))"),
            _DONE,
        )
    )


class LeavesALargeFile(HarnessAgent):
    """Fixes the line and leaves a file that states 2 GiB and holds nothing."""

    trial = staticmethod(
        scripted(_calls(_FIX), _shell("l", "truncate -s 2G big"), _DONE)
    )


class ListsTheNetwork(HarnessAgent):
    """Lists the network interfaces, tries a connection out, then fixes the line."""

    trial = staticmethod(
        scripted(
            _shell(
                "n",
                "ls /sys/class/net; (exec 3<>/dev/tcp/192.0.2.1/80) 2>&1;"
                " echo connected=$?",
            ),
            _calls(_FIX),
            _DONE,
        )
    )


class SharesFilesBothWays(HarnessAgent):
    """Edits a file a command made, has a command change a tool's, then fixes it."""

    trial = staticmethod(
        scripted(
            _shell("m", "echo 'made by a command' > made.txt"),
            _calls(
                ToolCallPart(
                    id="e",
                    name="edit_file",
                    arguments={
                        "path": "made.txt",
                        "old_text": "by a command",
                        "new_text": "by a command, edited by a tool",
                    },
                ),
                ToolCallPart(
                    id="w",
                    name="write_file",
                    arguments={"path": "tool.txt", "content": "made by a tool\n"},
                ),
            ),
            _shell(
                "c", "echo 'changed by a command' >> tool.txt; cat made.txt tool.txt"
            ),
            _calls(_FIX),
            _DONE,
        )
    )


class ShowsItsEnvironment(HarnessAgent):
    """Prints its own environment and that of the container's first process."""

    trial = staticmethod(
        scripted(
            _shell("e", "env; echo FIRST; tr '\\0' '\\n' </proc/1/environ"),
            _calls(_FIX),
            _DONE,
        )
    )
