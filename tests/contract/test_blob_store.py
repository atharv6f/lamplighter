"""The promises of the blob store port, checked on every blob store."""

import asyncio
import hashlib
from collections.abc import Coroutine
from typing import Any

import pytest

from lamplighter.ports import BlobHandle, BlobStore, Failure
from tests.contract.stores import MAX_BLOB_BYTES, Stores

LIST = b"milk\neggs\ntea\n"


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def handle_of(result: BlobHandle | Failure) -> BlobHandle:
    assert isinstance(result, BlobHandle), result
    return result


def failure_of(result: object) -> Failure:
    assert isinstance(result, Failure), f"expected a failure, got {result!r}"
    return result


@pytest.fixture
def blobs(stores: Stores) -> BlobStore:
    return stores.blobs()


def test_put_gives_a_handle_named_by_the_hash_of_the_bytes(blobs: BlobStore) -> None:
    handle = handle_of(run(blobs.put(LIST, "text/plain")))
    assert handle == BlobHandle(
        sha256=hashlib.sha256(LIST).hexdigest(), size=len(LIST), media_type="text/plain"
    )


def test_get_gives_back_the_bytes_that_were_put(blobs: BlobStore) -> None:
    handle = handle_of(run(blobs.put(LIST, "text/plain")))
    assert run(blobs.get(handle)) == LIST


def test_the_same_bytes_twice_give_one_hash(blobs: BlobStore) -> None:
    first = handle_of(run(blobs.put(LIST, "text/plain")))
    second = handle_of(run(blobs.put(LIST, "text/markdown")))
    assert first.sha256 == second.sha256
    assert run(blobs.get(second)) == LIST


def test_empty_bytes_are_a_blob_too(blobs: BlobStore) -> None:
    handle = handle_of(run(blobs.put(b"", "text/plain")))
    assert handle.size == 0
    assert run(blobs.get(handle)) == b""


def test_exists_says_whether_a_blob_is_held(blobs: BlobStore) -> None:
    handle = BlobHandle(
        sha256=hashlib.sha256(LIST).hexdigest(), size=len(LIST), media_type="text/plain"
    )
    assert run(blobs.exists(handle)) is False
    run(blobs.put(LIST, "text/plain"))
    assert run(blobs.exists(handle)) is True


def test_bytes_over_the_limit_are_too_large(blobs: BlobStore) -> None:
    failure = failure_of(run(blobs.put(b"x" * (MAX_BLOB_BYTES + 1), "text/plain")))
    assert (failure.kind, failure.effect) == ("too_large", "none")
    assert handle_of(run(blobs.put(b"x" * MAX_BLOB_BYTES, "text/plain")))


def test_a_blob_that_was_never_put_is_missing(blobs: BlobStore) -> None:
    handle = BlobHandle(sha256="f" * 64, size=3, media_type="text/plain")
    failure = failure_of(run(blobs.get(handle)))
    assert failure.kind == "fatal"
    assert failure.detail["problem"] == "missing"


def test_a_handle_with_the_wrong_size_does_not_match(blobs: BlobStore) -> None:
    handle = handle_of(run(blobs.put(LIST, "text/plain")))
    wrong = BlobHandle(
        sha256=handle.sha256, size=handle.size + 1, media_type="text/plain"
    )
    failure = failure_of(run(blobs.get(wrong)))
    assert failure.kind == "fatal"
    assert failure.detail["problem"] == "size"


@pytest.mark.parametrize(
    "handle",
    [
        BlobHandle(sha256="F" * 64, size=1, media_type="text/plain"),
        BlobHandle(sha256="../state.db", size=1, media_type="text/plain"),
        BlobHandle(sha256="f" * 64, size=-1, media_type="text/plain"),
    ],
)
def test_a_handle_that_can_point_at_nothing_is_refused(
    blobs: BlobStore, handle: BlobHandle
) -> None:
    assert failure_of(run(blobs.get(handle))).kind == "invalid_input"
    assert failure_of(run(blobs.exists(handle))).kind == "invalid_input"
