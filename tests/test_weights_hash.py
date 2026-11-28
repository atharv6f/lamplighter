"""The hash of a model's weights, for a file and for a folder of files.

The pinned values were worked out once with sha256sum in a shell, from the
same made-up files, so a change to how the hash is made shows up here.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from lamplighter.app.main import main
from lamplighter.app.weights_hash import weights_hash
from lamplighter.ports import Failure

FILE_HASH = "099010bd0f2d7193cb52be7703b8023eda50db137b7320aeec51aaa57015e52a"
# Of the four lines "<hash>  B.txt", "<hash>  a.json", "<hash>  model.bin"
# and "<hash>  sub/c.bin", each ended by a newline, in that order: by bytes,
# so the capital letter comes first.
FOLDER_HASH = "e6b095d81c3988e83da1c2daef3afca1ca49abbbb77e36ab097bc41f948916b2"


def made_up_weights(folder: Path) -> Path:
    weights = folder / "weights"
    (weights / "sub").mkdir(parents=True)
    (weights / "model.bin").write_bytes(b"made-up weights")
    (weights / "a.json").write_bytes(b'{"made": "up"}')
    (weights / "B.txt").write_bytes(b"TEMPLATE")
    (weights / "sub" / "c.bin").write_bytes(b"deep")
    return weights


def refused(path: Path) -> Failure:
    answer = weights_hash(path)
    assert isinstance(answer, Failure), answer
    assert answer.kind == "invalid_input"
    return answer


def test_a_file_s_hash_is_its_own_sha256(tmp_path: Path) -> None:
    weights = made_up_weights(tmp_path)
    assert weights_hash(weights / "model.bin") == FILE_HASH


def test_a_folder_s_hash_is_of_its_files_listed_by_path_as_bytes(
    tmp_path: Path,
) -> None:
    assert weights_hash(made_up_weights(tmp_path)) == FOLDER_HASH


def test_a_changed_template_gives_another_hash(tmp_path: Path) -> None:
    weights = made_up_weights(tmp_path)
    (weights / "B.txt").write_bytes(b"TEMPLATE2")
    answer = weights_hash(weights)
    assert isinstance(answer, str) and answer != FOLDER_HASH


def test_a_moved_file_gives_another_hash(tmp_path: Path) -> None:
    weights = made_up_weights(tmp_path)
    (weights / "sub" / "c.bin").rename(weights / "c.bin")
    answer = weights_hash(weights)
    assert isinstance(answer, str) and answer != FOLDER_HASH


def test_an_empty_folder_hashes_an_empty_list(tmp_path: Path) -> None:
    # The SHA-256 of no bytes at all.
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert weights_hash(tmp_path) == empty


@pytest.mark.parametrize("target", ["model.bin", "sub"])
def test_a_link_inside_the_folder_is_refused(tmp_path: Path, target: str) -> None:
    weights = made_up_weights(tmp_path)
    (weights / "linked").symlink_to(weights / target)
    assert "link" in refused(weights).message


def test_something_that_is_not_a_file_is_refused(tmp_path: Path) -> None:
    weights = made_up_weights(tmp_path)
    os.mkfifo(weights / "pipe")
    assert "not a file" in refused(weights).message


def test_a_path_with_nothing_there_is_refused(tmp_path: Path) -> None:
    assert "no file or folder" in refused(tmp_path / "missing").message


def test_the_command_prints_the_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    weights = made_up_weights(tmp_path)
    assert main(["models", "weights-hash", str(weights)]) == 0
    assert capsys.readouterr().out == FOLDER_HASH + "\n"
    assert main(["models", "weights-hash", str(tmp_path / "missing")]) == 1
    assert "no file or folder" in capsys.readouterr().err


def test_a_folder_the_walk_cannot_open_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As a folder with no read permission does: the walk reports it and
    # goes on without its files.
    weights = made_up_weights(tmp_path)
    walk = os.walk

    def walk_that_cannot_open_sub(
        top: Path, onerror: Callable[[OSError], object]
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        for root, folders, files in walk(top):
            if Path(root).name == "sub":
                onerror(PermissionError(13, "Permission denied", root))
                continue
            yield root, folders, files

    monkeypatch.setattr(os, "walk", walk_that_cannot_open_sub)
    assert "could not be read" in refused(weights).message
