import re
from pathlib import Path

import pytest

from lamplighter.app.config import load_config
from lamplighter.app.model_settings import LocalServer
from lamplighter.core import Route
from tests.settings_text import GOOD, LOCAL, PRIVATE_ROUTE, with_local

BOTH = with_local()
ZEROS = "0" * 64


def write(folder: Path, text: str) -> Path:
    path = folder / "settings.toml"
    path.write_text(text)
    return path


def refused(folder: Path, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(write(folder, text))


def test_a_table_with_a_local_model_is_read_in_the_order_of_the_file(
    tmp_path: Path,
) -> None:
    config = load_config(write(tmp_path, BOTH))
    assert [entry.name for entry in config.models] == ["frontier", "local"]
    assert config.routes == (
        Route("private", "private", ("local",)),
        Route("default", "always", ("frontier",)),
    )
    local = config.models[1]
    assert local.prices is None
    assert local.local == LocalServer(
        base_url="http://127.0.0.1:9931",
        weights_sha256=ZEROS,
        server="llama.cpp b12826",
        server_dir="~/lamplighter-models/llama",
        count="llama_cpp",
        call_markers=("<tool_call>", "<function="),
    )
    assert local.local is not None and local.local.kind == "llama.cpp"
    assert config.default_model().name == "frontier"


def test_only_a_local_model_stays_on_this_mac(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, BOTH))
    stays = {entry.name: entry.profile.stays_on_mac for entry in config.models}
    assert stays == {"frontier": False, "local": True}


def test_stays_on_mac_cannot_be_set_in_the_settings(tmp_path: Path) -> None:
    # Not even to the value the loader would work out itself.
    for name, value in (("frontier", "true"), ("local", "true")):
        at = BOTH.index(f'name = "{name}"\n')
        text = BOTH[:at] + f"stays_on_mac = {value}\n" + BOTH[at:]
        refused(tmp_path, text, r"stays_on_mac is not a field")


def test_an_old_settings_file_is_refused_with_a_message_that_names_the_new_shape(
    tmp_path: Path,
) -> None:
    message = re.escape(
        "the settings now list models under [[models]] and routes under [[routes]]"
    )
    for table in ('[model]\nadapter = "anthropic"\n', "[prices]\noutput = 1\n"):
        refused(tmp_path, table + GOOD, message)


def test_settings_with_no_models_are_refused(tmp_path: Path) -> None:
    at, end = GOOD.index("[[models]]"), GOOD.index("[[routes]]")
    refused(tmp_path, GOOD[:at] + GOOD[end:], r"at least one \[\[models\]\]")


def test_settings_with_no_routes_are_refused(tmp_path: Path) -> None:
    at, end = GOOD.index("[[routes]]"), GOOD.index("[store]")
    refused(tmp_path, GOOD[:at] + GOOD[end:], r"at least one \[\[routes\]\]")


@pytest.mark.parametrize(
    ("good", "wrong", "message"),
    [
        ('name = "frontier"\n', 'name = "Frontier"\n', r"models\[0\]\.name"),
        ('name = "frontier"\n', 'name = "front--ier"\n', r"models\[0\]\.name"),
        ('name = "frontier"\n', f'name = "{"a" * 41}"\n', r"models\[0\]\.name"),
        ('name = "frontier"\n', "name = 1\n", r"models\[0\]\.name"),
        ('adapter = "anthropic"\n', 'adapter = "openai"\n', r"models\[0\]\.adapter"),
        ('model = "made-up-1"\n', 'model = ""\n', r"models\[0\]\.model"),
        ('model = "made-up-1"\n', f'model = "{"m" * 201}"\n', r"models\[0\]\.model"),
        ('model = "made-up-1"\n', 'model = "made\\tup"\n', r"models\[0\]\.model"),
        ("window = 200_000\n", "window = 0\n", r"models\[0\]\.window"),
        ("window = 200_000\n", 'window = "200000"\n', r"models\[0\]\.window"),
        ("window = 200_000\n", "window = 2_000\n", r"max_output must be below"),
        ("max_output = 2_000\n", "max_output = 0\n", r"models\[0\]\.max_output"),
        ("tool_calls = true\n", 'tool_calls = "yes"\n', r"models\[0\]\.tool_calls"),
        ("cache_hints = 4\n", "cache_hints = 5\n", r"models\[0\]\.cache_hints"),
        ("cache_hints = 4\n", "cache_hints = -1\n", r"models\[0\]\.cache_hints"),
        (
            "count_error_permille = 25\n",
            "count_error_permille = 1001\n",
            r"models\[0\]\.count_error_permille",
        ),
        (
            "concurrent_requests = 4\n",
            "concurrent_requests = 0\n",
            r"models\[0\]\.concurrent_requests",
        ),
        (
            "concurrent_requests = 4\n",
            "concurrent_requests = true\n",
            r"models\[0\]\.concurrent_requests",
        ),
        ("concurrent_requests = 4\n", "", r"models\[0\]\.concurrent_requests is miss"),
        ("[models.prices]\n", "[models.costs]\n", r"models\[0\]\.costs is not"),
        ("output = 20_000_000\n", "", r"models\[0\]\.prices"),
        ("output = 20_000_000\n", "output = 20.5\n", r"models\[0\]\.prices"),
    ],
)
def test_a_field_of_an_anthropic_model_that_breaks_its_rule_is_refused(
    tmp_path: Path, good: str, wrong: str, message: str
) -> None:
    assert good in BOTH
    refused(tmp_path, BOTH.replace(good, wrong, 1), message)


def test_a_field_of_one_adapter_is_refused_on_the_other(tmp_path: Path) -> None:
    anthropic_at = BOTH.index('adapter = "anthropic"\n')
    for field in (
        'base_url = "http://127.0.0.1:9931"',
        f'weights_sha256 = "{ZEROS}"',
        'server = "llama.cpp b12826"',
        'server_dir = "~/lamplighter-models/llama"',
        'count = "llama_cpp"',
        "call_markers = []",
    ):
        text = BOTH[:anthropic_at] + field + "\n" + BOTH[anthropic_at:]
        refused(tmp_path, text, r"models\[0\]\.\w+ is not a field of an anthropic")
    prices = "[models.prices]\noutput = 1\n"
    at = BOTH.index("[[routes]]")
    refused(
        tmp_path,
        BOTH[:at] + prices + BOTH[at:],
        r"models\[1\]\.prices is not a field of an openai_chat",
    )


@pytest.mark.parametrize(
    ("good", "wrong", "message"),
    [
        ('model = "local"\n', 'model = "other"\n', r"models\[1\]\.model must be"),
        ('model = "local"\n', 'model = "default_model"\n', r"models\[1\]\.model"),
        ("cache_hints = 0\n", "cache_hints = 1\n", r"models\[1\]\.cache_hints"),
        (
            'base_url = "http://127.0.0.1:9931"',
            'base_url = "http://localhost:9931"',
            r"models\[1\]\.base_url",
        ),
        (
            'base_url = "http://127.0.0.1:9931"',
            'base_url = "http://0.0.0.0:9931"',
            r"models\[1\]\.base_url",
        ),
        (
            'base_url = "http://127.0.0.1:9931"',
            'base_url = "https://127.0.0.1:9931"',
            r"models\[1\]\.base_url",
        ),
        (
            'base_url = "http://127.0.0.1:9931"',
            'base_url = "http://127.0.0.1:09931"',
            r"models\[1\]\.base_url",
        ),
        (
            'base_url = "http://127.0.0.1:9931"',
            'base_url = "http://127.0.0.1:65536"',
            r"models\[1\]\.base_url",
        ),
        (
            'base_url = "http://127.0.0.1:9931"',
            'base_url = "http://127.0.0.1:9931/v1"',
            r"models\[1\]\.base_url",
        ),
        (
            f'weights_sha256 = "{ZEROS}"',
            f'weights_sha256 = "{"A" * 64}"',
            r"models\[1\]\.weights_sha256",
        ),
        (
            f'weights_sha256 = "{ZEROS}"',
            f'weights_sha256 = "{"0" * 63}"',
            r"models\[1\]\.weights_sha256",
        ),
        ('server = "llama.cpp b12826"', 'server = "llama.cpp"', r"models\[1\]\.server"),
        (
            'server = "llama.cpp b12826"',
            'server = "ollama 0.9"',
            r"models\[1\]\.server",
        ),
        (
            'server = "llama.cpp b12826"',
            'server = "llama.cpp b1 2"',
            r"models\[1\]\.server",
        ),
        (
            'server_dir = "~/lamplighter-models/llama"',
            'server_dir = "/opt/llama"',
            r"models\[1\]\.server_dir",
        ),
        (
            'server_dir = "~/lamplighter-models/llama"',
            'server_dir = "~/lamplighter-models/"',
            r"models\[1\]\.server_dir",
        ),
        (
            'server_dir = "~/lamplighter-models/llama"',
            'server_dir = "~/lamplighter-models/../bin"',
            r"models\[1\]\.server_dir",
        ),
        ('count = "llama_cpp"', 'count = "vllm"', r"models\[1\]\.count must be"),
        ('count = "llama_cpp"', 'count = "none"', r"models\[1\]\.count must be"),
        (
            'call_markers = ["<tool_call>", "<function="]',
            'call_markers = "<tool_call>"',
            r"models\[1\]\.call_markers",
        ),
        (
            'call_markers = ["<tool_call>", "<function="]',
            'call_markers = ["<tool_call>", ""]',
            r"models\[1\]\.call_markers",
        ),
        (
            'call_markers = ["<tool_call>", "<function="]',
            "call_markers = [1]",
            r"models\[1\]\.call_markers",
        ),
    ],
)
def test_a_field_of_a_local_model_that_breaks_its_rule_is_refused(
    tmp_path: Path, good: str, wrong: str, message: str
) -> None:
    assert good in BOTH
    refused(tmp_path, BOTH.replace(good, wrong, 1), message)


def test_a_local_model_with_no_call_markers_is_accepted(tmp_path: Path) -> None:
    text = BOTH.replace(
        'call_markers = ["<tool_call>", "<function="]', "call_markers = []"
    )
    local = load_config(write(tmp_path, text)).models[1].local
    assert local is not None and local.call_markers == ()


def test_each_server_takes_its_own_count_and_mlx_lm_its_one_model_name(
    tmp_path: Path,
) -> None:
    for server, count, model in (
        ("vllm-metal v0.34.0", "vllm", "local"),
        ("mlx-lm 0.32.2", "none", "default_model"),
    ):
        text = (
            BOTH.replace('server = "llama.cpp b12826"', f'server = "{server}"')
            .replace('count = "llama_cpp"', f'count = "{count}"')
            .replace('model = "local"\n', f'model = "{model}"\n')
        )
        local = load_config(write(tmp_path, text)).models[1]
        assert local.local is not None
        assert (local.local.server, local.local.count) == (server, count)
        assert local.profile.model == model


def test_a_local_address_is_kept_without_its_last_slash(tmp_path: Path) -> None:
    text = BOTH.replace(
        'base_url = "http://127.0.0.1:9931"', 'base_url = "http://127.0.0.1:9931/"'
    )
    local = load_config(write(tmp_path, text)).models[1].local
    assert local is not None and local.base_url == "http://127.0.0.1:9931"


def test_two_models_with_one_name_are_refused(tmp_path: Path) -> None:
    text = BOTH.replace('name = "local"', 'name = "frontier"').replace(
        'model = "local"\n', 'model = "frontier"\n'
    )
    refused(tmp_path, text, r"two \[\[models\]\] tables have the name 'frontier'")


def test_two_routes_with_one_id_are_refused(tmp_path: Path) -> None:
    text = BOTH.replace('id = "private"', 'id = "default"')
    refused(tmp_path, text, r"two \[\[routes\]\] tables have the id 'default'")


@pytest.mark.parametrize(
    ("good", "wrong", "message"),
    [
        ('id = "default"', 'id = "Default"', r"routes\[1\]\.id"),
        ('id = "default"', "id = 3", r"routes\[1\]\.id"),
        ('when = "always"', 'when = "sometimes"', r"routes\[1\]\.when"),
        ('models = ["frontier"]', "models = []", r"routes\[1\]\.models"),
        ('models = ["frontier"]', 'models = "frontier"', r"routes\[1\]\.models"),
        ('models = ["frontier"]', "models = [1]", r"routes\[1\]\.models"),
        (
            'models = ["frontier"]',
            'models = ["frontier", "frontier"]',
            r"routes\[1\]\.models names one model twice",
        ),
        ('when = "always"', 'when = "always"\nweight = 1', r"routes\[1\] needs"),
    ],
)
def test_a_route_field_that_breaks_its_rule_is_refused(
    tmp_path: Path, good: str, wrong: str, message: str
) -> None:
    assert good in BOTH
    refused(tmp_path, BOTH.replace(good, wrong, 1), message)


def test_a_route_that_names_a_model_not_listed_is_refused(tmp_path: Path) -> None:
    text = BOTH.replace('models = ["frontier"]', 'models = ["frontier", "other"]')
    refused(tmp_path, text, r"routes 'default' names 'other', which no")


def test_a_table_whose_last_route_is_not_the_default_is_refused(
    tmp_path: Path,
) -> None:
    # The contract: a rule table with no default rule is rejected.
    text = BOTH.replace('when = "always"', 'when = "private"')
    refused(tmp_path, text, r'the last route must have when = "always"')


def test_a_default_route_that_is_not_last_is_refused(tmp_path: Path) -> None:
    text = BOTH + '\n[[routes]]\nid = "later"\nwhen = "always"\nmodels = ["local"]\n'
    refused(tmp_path, text, r'only the last route may have when = "always"')


def test_two_private_routes_are_refused(tmp_path: Path) -> None:
    second = PRIVATE_ROUTE.replace('id = "private"', 'id = "private-two"')
    at = BOTH.index('[[routes]]\nid = "default"')
    text = BOTH[:at] + second.lstrip() + "\n" + BOTH[at:]
    refused(tmp_path, text, r'only one route may have when = "private"')


def test_a_private_route_with_no_model_on_this_mac_is_refused(tmp_path: Path) -> None:
    text = BOTH.replace(
        'when = "private"\nmodels = ["local"]',
        'when = "private"\nmodels = ["frontier"]',
    ).replace('models = ["frontier"]\n\n[store]', 'models = ["local"]\n\n[store]')
    refused(tmp_path, text, r"routes 'private' is for private runs and names no model")


def test_a_private_route_that_also_names_the_frontier_model_is_accepted(
    tmp_path: Path,
) -> None:
    # The veto, not the loader, keeps a private run off the frontier model.
    text = BOTH.replace('models = ["local"]', 'models = ["frontier", "local"]')
    routes = load_config(write(tmp_path, text)).routes
    assert routes[0].models == ("frontier", "local")


def test_a_table_with_no_private_route_is_accepted(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, GOOD))
    assert [route.when for route in config.routes] == ["always"]


def test_a_model_that_takes_no_tool_calls_is_refused_in_any_ladder(
    tmp_path: Path,
) -> None:
    text = BOTH.replace(
        "tool_calls = true\ncache_hints = 0", "tool_calls = false\ncache_hints = 0"
    )
    refused(tmp_path, text, r"names 'local', which takes no tool calls")


def test_an_output_limit_above_any_ladder_models_own_is_refused(
    tmp_path: Path,
) -> None:
    text = BOTH.replace("max_output = 16_000", "max_output = 900").replace(
        "window = 65_536", "window = 65_536"
    )
    refused(
        tmp_path, text, r"run.model_max_output is 1000, above the max_output of 900"
    )


def test_a_model_no_route_names_is_refused(tmp_path: Path) -> None:
    at = GOOD.index("[[routes]]")
    text = GOOD[:at] + LOCAL.lstrip() + "\n" + GOOD[at:]
    refused(tmp_path, text, r"no route names the model 'local'")
