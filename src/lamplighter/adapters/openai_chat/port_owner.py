"""Finds the command line of the program listening on a port of 127.0.0.1."""

import subprocess


def port_owner(port: int, seconds: float) -> str | None:
    """Give the command line of the program listening on 127.0.0.1 at `port`.

    It gives None when no program listens there, when one this user may not
    see does, or when `lsof` and `ps` together take longer than `seconds`.
    Each of the two gets half of them.
    """
    each = seconds / 2
    # Full paths, so that no folder on the PATH can put another program first.
    try:
        found = subprocess.run(
            ["/usr/sbin/lsof", "-nP", f"-iTCP@127.0.0.1:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=each,
            check=False,
        )
        ids = found.stdout.split()
        if found.returncode != 0 or not ids or not ids[0].isdigit():
            return None
        shown = subprocess.run(
            ["/bin/ps", "-o", "args=", "-p", ids[0]],
            capture_output=True,
            text=True,
            timeout=each,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    line = shown.stdout.strip()
    return line if shown.returncode == 0 and line else None
