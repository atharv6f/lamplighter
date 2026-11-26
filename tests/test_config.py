import dataclasses
from pathlib import Path

import pytest

from lamplighter.app.config import load_config
from lamplighter.app.main import build_model
from lamplighter.core import Prices
from lamplighter.ports import ModelProfile

SETTINGS = Path(__file__).parents[1] / "lamplighter.toml"

GOOD = """
[model]
adapter = "anthropic"
model = "made-up-1"
window = 10_000
max_output = 2_000
tool_calls = true
multiple_tool_calls = false
cache_hints = 4
count_error_permille = 25

[prices]
input_uncached = 4_000_000
cache_read = 200_000
cache_write = 5_000_000
output = 20_000_000
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
        window=10_000,
        max_output=2_000,
        tool_calls=True,
        multiple_tool_calls=False,
        cache_hints=4,
        count_error_permille=25,
    )
    assert config.prices == Prices(
        input_uncached=4_000_000,
        cache_read=200_000,
        cache_write=5_000_000,
        output=20_000_000,
    )


def test_settings_cannot_be_changed_once_read(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, GOOD))
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.prices = config.prices  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize(
    ("good", "wrong"),
    [
        ("window = 10_000\n", ""),
        ("window = 10_000\n", 'window = "10_000"\n'),
        ("window = 10_000\n", "window = 10_000\ncolour = 3\n"),
        ("output = 20_000_000\n", "output = 20.5\n"),
        ("[prices]", "[costs]"),
        ("tool_calls = true\n", "tool_calls = 1\n"),
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
    assert (config.model.window, config.model.max_output) == (1_000_000, 128_000)
    assert config.model.cache_hints == 4
    assert config.prices == Prices(
        input_uncached=4_000_000,
        cache_read=200_000,
        cache_write=5_000_000,
        output=20_000_000,
    )


def test_the_builder_gives_a_client_for_the_model_in_the_settings() -> None:
    config = load_config(SETTINGS)
    client = build_model(config, "made-up-key-0000")
    assert client.profile == config.model
