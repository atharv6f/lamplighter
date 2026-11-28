"""The tool list a settings file gives, without a trial, names the trial's config."""

import dataclasses
from pathlib import Path

from lamplighter.app.bench_trial import trial_config
from lamplighter.app.config import load_config
from lamplighter.app.main import tool_entries
from lamplighter.core import portable_config_id
from tests.test_bench_trial import Trial


def test_the_listed_tools_give_the_portable_id_a_trial_works_out(
    tmp_path: Path,
) -> None:
    trial = Trial(tmp_path)
    request = trial.request()
    config = load_config(trial.settings)
    listed = portable_config_id(
        config.model,
        config.prices,
        config.run,
        tool_entries(config),
        dataclasses.asdict(config.tools),
    )
    assert listed == trial.portable(request)
    assert trial_config(request).tools.work_dir != config.tools.work_dir


def test_the_list_holds_every_built_in_tool(tmp_path: Path) -> None:
    config = load_config(Trial(tmp_path).settings)
    assert [entry.spec.name for entry in tool_entries(config)] == [
        "edit_file",
        "read_file",
        "read_output",
        "search_files",
        "shell",
        "web_fetch",
        "write_file",
    ]
