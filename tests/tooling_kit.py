"""Settings and tools for a chat in a test, with folders of its own and no wall."""

import dataclasses
from collections.abc import Callable
from pathlib import Path

from lamplighter.adapters import FakeExecutor
from lamplighter.app.chat import Tooling
from lamplighter.app.config import Config
from lamplighter.ports import BlobStore, Command, ExecResult, Failure, ToolProvider

ENDED_WELL = ExecResult(0, None, b"", False, False, False)

type Answer = Callable[[Command], ExecResult | Failure]


def with_tool_folders(config: Config, store: Path) -> Config:
    """The config with the store in `store`, and the tools' folders beside it."""
    tools = dataclasses.replace(
        config.tools,
        work_dir=str(store.parent / f"{store.name}-work"),
        scratch_dir=str(store.parent / f"{store.name}-scratch"),
    )
    return dataclasses.replace(
        config,
        store=dataclasses.replace(config.store, dir=str(store)),
        tools=tools,
    )


def tooling_for(
    config: Config,
    tools: ToolProvider,
    answer: Answer = lambda command: ENDED_WELL,
) -> Callable[[BlobStore], Tooling]:
    """Give `tools`, and a fake executor on the work folder that uses `answer`."""

    def build(blobs: BlobStore) -> Tooling:
        return Tooling(tools, FakeExecutor(Path(config.tools.work_dir), answer))

    return build
