"""Helpers that run a model stream to its end inside an ordinary test."""

import asyncio

from lamplighter.ports import Failure, ModelClient, ModelEvent, ModelRequest


def collect(client: ModelClient, request: ModelRequest) -> list[ModelEvent]:
    """Run one call and give back every event it yielded."""

    async def run() -> list[ModelEvent]:
        return [event async for event in client.stream(request)]

    return asyncio.run(run())


def collect_first(
    client: ModelClient, request: ModelRequest, count: int
) -> list[ModelEvent]:
    """Read `count` events of one call, then close the stream early."""

    async def run() -> list[ModelEvent]:
        events: list[ModelEvent] = []
        stream = client.stream(request)
        async for event in stream:
            events.append(event)
            if len(events) == count:
                break
        await stream.aclose()
        return events

    return asyncio.run(run())


def count(client: ModelClient, request: ModelRequest) -> int | Failure:
    """Ask the client for the prompt size of a request."""
    return asyncio.run(client.count_tokens(request))
