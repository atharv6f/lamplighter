import dataclasses
from pathlib import Path

import pytest

from lamplighter.app.config import StoreSettings, load_config, store_folder
from lamplighter.app.main import build_model
from lamplighter.core import Prices, RunSettings
from lamplighter.ports import ModelProfile

SETTINGS = Path(__file__).parents[1] / "lamplighter.toml"

GOOD = """
[model]
adapter = "anthropic"
model = "made-up-1"
max_output = 2_000
tool_calls = true
cache_hints = 4
count_error_permille = 25

[prices]
input_uncached = 4_000_000
cache_read = 200_000
cache_write = 5_000_000
output = 20_000_000

[store]
dir = "~/.lamplighter"
max_record_bytes = 4_194_304
max_blob_bytes = 67_108_864

[run]
system = "You are a made-up helper."
max_steps = 40
max_seconds = 1_200
max_tokens = 1_000_000
max_micro_dollars = 1_000_000
model_max_output = 1_000
model_deadline_ms = 600_000
model_retries = 2
cancel_bound_ms = 800
"""


def write(folder: Path, text: str) -> Path:
    path = folder / "settings.toml"
    path.write_text(text)
    return path


def test_a_settings_file_is_read_into_records(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, GOOD))
    assert config.model == ModelProfile(
        adapter="anthropic",
        model="made-up-1",
        max_output=2_000,
        tool_calls=True,
        cache_hints=4,
        count_error_permille=25,
    )
    assert config.prices == Prices(
        input_uncached=4_000_000,
        cache_read=200_000,
        cache_write=5_000_000,
        output=20_000_000,
    )


def test_the_store_folder_is_read_as_an_absolute_path_in_the_home_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = load_config(write(tmp_path, GOOD))
    assert config.store == StoreSettings(
        dir=str((tmp_path / ".lamplighter").resolve()),
        max_record_bytes=4_194_304,
        max_blob_bytes=67_108_864,
    )


@pytest.mark.parametrize(
    "setting",
    [
        "~/Documents/Lamplighter/state",
        "~/Documents",
        "~/Desktop/store",
        "~/Library/Mobile Documents/com~apple~CloudDocs/store",
        "~/Library/CloudStorage/Dropbox/store",
        "/Users/someone/Library/CloudStorage/Box/store",
        ".lamplighter",
        "store",
    ],
)
def test_a_store_folder_that_a_sync_tool_may_copy_or_that_is_relative_is_refused(
    tmp_path: Path, setting: str
) -> None:
    with pytest.raises(ValueError, match="store.dir"):
        store_folder(setting, tmp_path)


def test_a_store_folder_reached_through_a_link_into_documents_is_refused(
    tmp_path: Path,
) -> None:
    (tmp_path / "Documents").mkdir()
    (tmp_path / "shortcut").symlink_to(tmp_path / "Documents")
    with pytest.raises(ValueError, match="Documents"):
        store_folder("~/shortcut/store", tmp_path)


@pytest.mark.parametrize("setting", ["~/.lamplighter", "/var/lamplighter", "~"])
def test_a_store_folder_outside_synced_folders_is_accepted(
    tmp_path: Path, setting: str
) -> None:
    assert store_folder(setting, tmp_path).is_absolute()


def test_the_run_settings_are_read(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, GOOD))
    assert config.run == RunSettings(
        system="You are a made-up helper.",
        max_steps=40,
        max_seconds=1_200,
        max_tokens=1_000_000,
        max_micro_dollars=1_000_000,
        model_max_output=1_000,
        model_deadline_ms=600_000,
        model_retries=2,
        cancel_bound_ms=800,
    )


def test_a_model_max_output_equal_to_the_models_own_is_accepted(
    tmp_path: Path,
) -> None:
    text = GOOD.replace("model_max_output = 1_000\n", "model_max_output = 2_000\n")
    assert load_config(write(tmp_path, text)).run.model_max_output == 2_000


def test_no_retries_is_accepted(tmp_path: Path) -> None:
    text = GOOD.replace("model_retries = 2\n", "model_retries = 0\n")
    assert load_config(write(tmp_path, text)).run.model_retries == 0


def test_settings_cannot_be_changed_once_read(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, GOOD))
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.prices = config.prices  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize(
    ("good", "wrong"),
    [
        ("max_output = 2_000\n", ""),
        ("max_output = 2_000\n", 'max_output = "2_000"\n'),
        ("max_output = 2_000\n", "max_output = 2_000\ncolour = 3\n"),
        ("output = 20_000_000\n", "output = 20.5\n"),
        ("[prices]", "[costs]"),
        ("tool_calls = true\n", "tool_calls = 1\n"),
        ("max_blob_bytes = 67_108_864\n", ""),
        ('dir = "~/.lamplighter"', 'dir = "~/Documents/state"'),
        ("model_max_output = 1_000\n", "model_max_output = 2_001\n"),
        ("model_max_output = 1_000\n", "model_max_output = 0\n"),
        ("max_steps = 40\n", "max_steps = 0\n"),
        ("max_seconds = 1_200\n", "max_seconds = -1\n"),
        ("max_tokens = 1_000_000\n", "max_tokens = 0\n"),
        ("max_micro_dollars = 1_000_000\n", "max_micro_dollars = 0\n"),
        ("model_deadline_ms = 600_000\n", "model_deadline_ms = 0\n"),
        ("cancel_bound_ms = 800\n", "cancel_bound_ms = 0\n"),
        ("model_retries = 2\n", "model_retries = -1\n"),
        ('system = "You are a made-up helper."', 'system = "  "'),
        ("cancel_bound_ms = 800\n", ""),
    ],
)
def test_a_settings_file_that_does_not_fit_is_rejected(
    tmp_path: Path, good: str, wrong: str
) -> None:
    assert good in GOOD
    with pytest.raises(ValueError):
        load_config(write(tmp_path, GOOD.replace(good, wrong)))


def test_the_settings_file_in_the_repo_names_the_model_and_its_prices() -> None:
    config = load_config(SETTINGS)
    assert config.model.adapter == "anthropic"
    assert config.model.model == "claude-opus-5-5"
    assert config.model.max_output == 128_000
    assert config.model.cache_hints == 4
    assert config.prices == Prices(
        input_uncached=4_000_000,
        cache_read=200_000,
        cache_write=5_000_000,
        output=20_000_000,
    )


def test_the_settings_file_in_the_repo_keeps_the_store_out_of_the_checkout() -> None:
    config = load_config(SETTINGS)
    assert config.store.dir == str(Path("~/.lamplighter").expanduser().resolve())
    assert config.store.max_record_bytes == 4 * 1024 * 1024
    assert config.store.max_blob_bytes == 64 * 1024 * 1024


def test_the_settings_file_in_the_repo_holds_the_first_run_settings() -> None:
    run = load_config(SETTINGS).run
    assert run.system.startswith("You are Lamplighter, a personal assistant")
    assert run.system.endswith("If a tool fails, say what failed.")
    assert (run.max_steps, run.max_seconds) == (40, 1_200)
    assert (run.max_tokens, run.max_micro_dollars) == (1_000_000, 1_000_000)
    assert (run.model_max_output, run.model_deadline_ms) == (16_000, 600_000)
    assert (run.model_retries, run.cancel_bound_ms) == (2, 800)


def test_the_builder_gives_a_client_for_the_model_in_the_settings() -> None:
    config = load_config(SETTINGS)
    client = build_model(config, "made-up-key-0000")
    assert client.profile == config.model
