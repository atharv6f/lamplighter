"""A screen and a keyboard for the terminal channel, with no terminal.

The screen records what is printed and lets another thread wait for a
text to appear. The keyboard is a pipe, whose read end the channel reads.
"""

import io
import os
import threading


class Screen(io.StringIO):
    """What the channel printed. Safe to wait on from another thread."""

    def __init__(self) -> None:
        super().__init__()
        self._changed = threading.Condition()

    def write(self, s: str) -> int:
        with self._changed:
            written = super().write(s)
            self._changed.notify_all()
            return written

    def wait_for(self, text: str, seconds: float = 10.0) -> bool:
        with self._changed:
            return self._changed.wait_for(lambda: text in self.getvalue(), seconds)


class Keyboard:
    """A pipe to type into. `reader` is the end the channel reads."""

    def __init__(self) -> None:
        self.reader, self._writer = os.pipe()
        self._open = True

    def type(self, data: bytes) -> None:
        os.write(self._writer, data)

    def close(self) -> None:
        """End the input, as Ctrl-D would."""
        if self._open:
            self._open = False
            os.close(self._writer)

    def done(self) -> None:
        """End the input and free the read end too."""
        self.close()
        os.close(self.reader)


def typed(data: bytes) -> Keyboard:
    """Give a keyboard on which `data` was typed and the input then ended."""
    keyboard = Keyboard()
    keyboard.type(data)
    keyboard.close()
    return keyboard
