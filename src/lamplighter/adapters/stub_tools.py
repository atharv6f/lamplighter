"""Two small tools that only read, so the loop has something real to call."""

from lamplighter.ports import (
    Clock,
    Failure,
    JsonObject,
    Plan,
    ToolEntry,
    ToolResult,
    ToolSpec,
    utc_text,
)

_SOURCE = "ToolProvider/stub"

# The same length as one model call may take. Long enough to try the run's
# time limit on, and short enough that a mistake cannot hang a run for long.
LONGEST_WAIT_S = 600
# Each stub's text is short, so its cap is never reached.
_CAP = 30_000

_CURRENT_TIME = ToolSpec(
    name="current_time",
    description="Give the current date and time in UTC.",
    arguments_schema={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)

_WAIT = ToolSpec(
    name="wait",
    description=f"Wait for a whole number of seconds, from 0 to {LONGEST_WAIT_S},"
    " then say so.",
    arguments_schema={
        "type": "object",
        "properties": {
            "seconds": {"type": "integer", "minimum": 0, "maximum": LONGEST_WAIT_S}
        },
        "required": ["seconds"],
        "additionalProperties": False,
    },
)


_SPECS = (_CURRENT_TIME, _WAIT)


class StubTools:
    """The tools `current_time` and `wait`. Satisfies ToolProvider.

    Both only read, so neither needs the effect ledger. `wait` exists so a
    stop, a kill and the time limit can each be tried on a tool that is
    really running.
    """

    def __init__(self, clock: Clock) -> None:
        """Use `clock` for the time and for waiting."""
        self._clock = clock

    def list_tools(self) -> tuple[ToolEntry, ...]:
        """Give the two entries, sorted by name. Both are reads."""
        return tuple(ToolEntry(spec, "read", _CAP, False) for spec in _SPECS)

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        """Run one of the two tools."""
        if name == _CURRENT_TIME.name:
            if arguments:
                return ToolResult(
                    text="current_time takes no arguments.",
                    is_error=True,
                    effect="none",
                )
            return ToolResult(
                text=utc_text(self._clock.now()), is_error=False, effect="none"
            )
        if name == _WAIT.name:
            return await self._wait(arguments)
        return Failure(
            kind="invalid_input",
            effect="none",
            message=f"There is no tool named {name}.",
            source=_SOURCE,
        )

    async def _wait(self, arguments: JsonObject) -> ToolResult:
        seconds = arguments.get("seconds")
        # bool is a kind of int in Python, and True is not a number of seconds.
        if (
            set(arguments) != {"seconds"}
            or type(seconds) is not int
            or not 0 <= seconds <= LONGEST_WAIT_S
        ):
            return ToolResult(
                text="wait takes one argument, seconds, a whole number"
                f" from 0 to {LONGEST_WAIT_S}.",
                is_error=True,
                effect="none",
            )
        await self._clock.sleep(seconds)
        return ToolResult(
            text=f"Waited {seconds} seconds.", is_error=False, effect="none"
        )

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        """Refuse: both stubs are reads."""
        del arguments
        return _not_a_change(name)

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        """Refuse: both stubs are reads."""
        del arguments, plan
        return _not_a_change(name)

    async def check(self, probe: JsonObject) -> bool | None:
        """Give None: neither stub makes a probe."""
        del probe
        return None


def _not_a_change(name: str) -> Failure:
    return Failure(
        kind="invalid_input",
        effect="none",
        message=f"{name} is a read, so it has no plan.",
        source=_SOURCE,
    )
