"""What each press of Ctrl-C does, with the three answers recorded."""

import asyncio

from lamplighter.app.ctrl_c import CtrlC
from lamplighter.core import RunningSessions


class Answers:
    """Records which answer each press got."""

    def __init__(self) -> None:
        self.running = RunningSessions()
        self.got: list[str] = []
        self.keys = CtrlC(
            "sess-a",
            self.running,
            self.stop,
            lambda: self.got.append("end input"),
            lambda: self.got.append("leave now"),
        )

    async def stop(self) -> None:
        self.got.append("stop")


def pressed(answers: Answers, times: int = 1) -> list[str]:
    async def scenario() -> None:
        for _ in range(times):
            answers.keys.pressed()
        # The stop is started as a task, so let it run.
        await asyncio.sleep(0)

    asyncio.run(scenario())
    return answers.got


def test_a_press_with_nothing_running_ends_the_input() -> None:
    assert pressed(Answers()) == ["end input"]


def test_a_press_while_another_session_runs_ends_the_input() -> None:
    answers = Answers()
    answers.running.begin("sess-b")
    assert pressed(answers) == ["end input"]


def test_the_first_press_during_a_run_is_a_stop() -> None:
    answers = Answers()
    answers.running.begin("sess-a")
    assert pressed(answers) == ["stop"]


def test_a_second_press_during_the_same_call_leaves_at_once() -> None:
    answers = Answers()
    answers.running.begin("sess-a")
    assert pressed(answers, 2) == ["leave now", "stop"]


def test_a_press_in_a_later_call_is_a_first_press_again() -> None:
    answers = Answers()
    answers.running.begin("sess-a")
    pressed(answers)
    answers.running.end("sess-a")
    answers.running.begin("sess-a")
    assert pressed(answers) == ["stop", "stop"]


def test_a_press_between_two_calls_ends_the_input_and_leaves_the_count() -> None:
    answers = Answers()
    answers.running.begin("sess-a")
    pressed(answers)
    answers.running.end("sess-a")
    assert pressed(answers) == ["stop", "end input"]
    answers.running.begin("sess-a")
    assert pressed(answers) == ["stop", "end input", "stop"]


def test_the_count_of_calls_begun_goes_up_only_on_a_begin_that_took() -> None:
    running = RunningSessions()
    assert running.begun("sess-a") == 0
    assert running.begin("sess-a")
    assert not running.begin("sess-a")
    assert running.begun("sess-a") == 1
    running.end("sess-a")
    running.begin("sess-a")
    assert (running.begun("sess-a"), running.begun("sess-b")) == (2, 0)
