"""What the tests of the built-in tools share: a provider on a temp folder."""

import asyncio
import socket
from pathlib import Path
from typing import Any

import httpx

from lamplighter.adapters import SystemClock
from lamplighter.adapters.builtin_tools import BuiltinTools, ToolSettings, WebAccess
from lamplighter.adapters.in_memory import InMemoryBlobStore
from lamplighter.ports import (
    Clock,
    Executor,
    Failure,
    JsonObject,
    Plan,
    ToolResult,
)
from tests.direct_executor import DirectExecutor

CAP = 30_000


def settings_for(work: Path, cap: int = CAP) -> ToolSettings:
    """Settings with `work` as the work folder, and a scratch folder beside it."""
    scratch = work.parent / f"{work.name}-scratch"
    return ToolSettings(
        work_dir=str(work),
        scratch_dir=str(scratch),
        shell_path="/usr/bin:/bin",
        shell_default_seconds=120,
        shell_max_seconds=600,
        output_cap_chars=cap,
        fetch_max_bytes=5_242_880,
        fetch_seconds=30,
    )


async def _no_name(host: str, port: int) -> tuple[str, ...]:
    raise socket.gaierror(socket.EAI_NONAME, "made-up: no network in tests")


def _no_answer(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("made-up: no network in tests", request=request)


# A network that finds no name, so no test reaches the real one by mistake.
NO_WEB = WebAccess(resolve=_no_name, transport=lambda: httpx.MockTransport(_no_answer))


def tools_on(
    work: Path,
    cap: int = CAP,
    executor: Executor | None = None,
    clock: Clock | None = None,
    web: WebAccess = NO_WEB,
) -> BuiltinTools:
    """The built-in tools on `work`, with old copies kept in memory.

    Commands run on the direct executor and time is the system's, unless
    others are given.
    """
    return BuiltinTools(
        settings_for(work, cap),
        InMemoryBlobStore(16_000_000),
        executor or DirectExecutor(work, SystemClock()),
        clock or SystemClock(),
        web,
    )


def planned(tools: BuiltinTools, name: str, **arguments: Any) -> Plan:
    """The plan of a change, which must be a plan."""
    given: JsonObject = dict(arguments)
    plan = asyncio.run(tools.plan(name, given))
    assert isinstance(plan, Plan), plan
    return plan


def refused(tools: BuiltinTools, name: str, **arguments: Any) -> ToolResult:
    """What `plan` gives for a change it will not make."""
    given: JsonObject = dict(arguments)
    result = asyncio.run(tools.plan(name, given))
    assert isinstance(result, ToolResult), result
    assert result.effect == "none"
    return result


def changed(
    tools: BuiltinTools, name: str, plan: Plan, **arguments: Any
) -> ToolResult | Failure:
    """What `apply` gives for a plan."""
    given: JsonObject = dict(arguments)
    return asyncio.run(tools.apply(name, given, plan))


def made(tools: BuiltinTools, name: str, **arguments: Any) -> ToolResult | Failure:
    """Plan a change and apply it."""
    return changed(tools, name, planned(tools, name, **arguments), **arguments)
