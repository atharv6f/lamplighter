"""The script that runs a command in a container, the line to start it, its answer."""

import base64
import binascii
import re
import shlex
from collections.abc import Mapping, Sequence

from lamplighter.ports import ExecResult

# Runs as root. Only the command runs as the command user, so the command
# cannot signal the script, reach its folder or write its answer line. Each
# detail is there because a check failed without it: the timer is a read on
# a pipe because `wait -n` lost wake-ups, `stdbuf -o0` keeps the bytes of a
# reader that is killed, the reader reads on past the cap so a flood ends by
# the kill and not by a closed pipe, and `9<&-` keeps the timer from the
# command.
SCRIPT = r"""exec 2>/dev/null
set -m
dir=$1; cap=$2; micros=$3; token=$4; shift 4
mkdir -m 700 "$dir" || exit 97
out=$dir/out; pipe=$dir/pipe; tick=$dir/tick
mkfifo "$pipe" "$tick" || exit 97
exec 9<>"$tick"
why=exited; status=
trap 'why=cancel' TERM
trap 'why=leave' USR1
: >"$out"
echo $$ >"$dir/supervisor"
{ stdbuf -o0 head -c "$((cap + 1))" >"$out"
  if [ "$(wc -c <"$out")" -gt "$cap" ]; then : >"$dir/full"; fi
  cat >/dev/null
} <"$pipe" 9<&- &
reader=$!
"$@" </dev/null >"$pipe" 2>&1 9<&- &
cmd=$!
end=$((${EPOCHREALTIME/./} + micros))
while :; do
  if [ "$why" != exited ]; then break; fi
  if ! kill -0 "$cmd"; then wait "$cmd"; status=$?; break; fi
  if [ -e "$dir/full" ]; then why=full; break; fi
  if [ "${EPOCHREALTIME/./}" -ge "$end" ]; then why=time; break; fi
  read -r -t 0.02 -u 9
done
kill -KILL -- "-$cmd"
if [ -z "$status" ]; then wait "$cmd"; status=$?; fi
tries=15
while kill -0 "$reader" && [ "$tries" -gt 0 ]; do
  read -r -t 0.02 -u 9
  tries=$((tries - 1))
done
kill -KILL -- "-$reader"
wait "$reader"
size=$(wc -c <"$out")
echo "$token $why $status $size"
head -c "$cap" "$out" | base64 -w0
echo
rm -rf "$dir"
"""

_ANSWER = re.compile(r"(exited|time|full|cancel|leave) (\d{1,3}) (\d+)")
_BASE64 = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_KILLED = 128 + 9


def run_folder(token: str) -> str:
    """Give the folder in the container's own `/tmp` that one command's script keeps."""
    return f"/tmp/.run-{token}"


def script_line(
    token: str,
    command_argv: Sequence[str],
    env: Mapping[str, str],
    front: Sequence[str],
    limits: tuple[int, int],
) -> str:
    """Give the bash line that runs the script on one command.

    `front` drops to the command user. `limits` is the output cap in bytes
    and the time limit in milliseconds. The `--` after `env -i` keeps a
    variable whose name starts with a dash from being read as an option.
    """
    cap, wall_ms = limits
    clean = ["/usr/bin/env", "-i", "--", *(f"{k}={v}" for k, v in env.items())]
    return shlex.join(
        [
            "bash",
            "-c",
            SCRIPT,
            "supervisor",
            run_folder(token),
            str(cap),
            str(wall_ms * 1000),
            token,
            *clean,
            *front,
            *command_argv,
        ]
    )


def read_answer(token: str, text: str, cap: int) -> ExecResult | None:
    """Give the result the script reported, or None when its answer is not whole.

    The answer is one line of the token, why the command ended, the status
    bash saw and the count of bytes kept, then one ended line of base64 that
    holds exactly the kept bytes up to the cap. Anything else, such as an
    answer cut short or a run folder the command removed, is not whole.
    """
    lines = text.split("\n")
    place = next(
        (i for i, line in enumerate(lines) if line.startswith(token + " ")), None
    )
    # The base64 line must have its own line end, or it may have been cut.
    if place is None or place + 2 >= len(lines):
        return None
    shaped = _ANSWER.fullmatch(lines[place].removeprefix(token + " "))
    output = _decoded(lines[place + 1])
    if shaped is None or output is None:
        return None
    why, status, size = shaped.group(1), int(shaped.group(2)), int(shaped.group(3))
    if len(output) != min(size, cap):
        return None
    by_signal = 128 < status <= 192
    # The flags follow the kill: a command that ended by itself in the
    # instant between a limit and the kill keeps its exit code.
    killed = status == _KILLED and why != "exited"
    return ExecResult(
        exit_code=None if by_signal else status,
        signal=status - 128 if by_signal else None,
        output=output,
        overflowed=size > cap,
        timed_out=killed and why == "time",
        cancelled=killed and why == "cancel",
    )


def _decoded(packed: str) -> bytes | None:
    if _BASE64.fullmatch(packed) is None:
        return None
    try:
        return base64.b64decode(packed, validate=True)
    except binascii.Error:
        return None
