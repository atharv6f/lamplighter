"""Two small tools that only read, so the loop has something real to call."""

from lamplighter.ports import (
    Clock,
    Failure,
    JsonObject,
    ToolResult,
    ToolSpec,
    utc_text,
)

_SOURCE = "ToolProvider/stub"

# The same length as one model call may take. Long enough to try the run's
# time limit on, and short enough that a mistake cannot hang a run for long.
LONGEST_WAIT_S = 600

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


class StubTools:
    """The tools `current_time` and `wait`. Satisfies ToolProvider.

    Both only read, so neither needs the effect ledger. `wait` exists so a
    stop, a kill and the time limit can each be tried on a tool that is
    really running.
    """

    def __init__(self, clock: Clock) -> None:
        """Use `clock` for the time and for waiting."""
        self._clock = clock

    def list_tools(self) -> tuple[ToolSpec, ...]:
        """Give the two specs, sorted by name."""
        return (_CURRENT_TIME, _WAIT)

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        """Run one of the two tools."""
        if name == _CURRENT_TIME.name:
            if arguments:
                return ToolResult(
                    text="current_time takes no arguments.", is_error=True
                )
            return ToolResult(text=utc_text(self._clock.now()), is_error=False)
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
            )
        await self._clock.sleep(seconds)
        return ToolResult(text=f"Waited {seconds} seconds.", is_error=False)
