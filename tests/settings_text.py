"""Made-up settings files as text, for tests of the settings loader."""

import dataclasses

from lamplighter.app.config import Config
from lamplighter.ports import ModelProfile

GOOD = """
[[models]]
name = "frontier"
adapter = "anthropic"
model = "made-up-1"
window = 200_000
max_output = 2_000
tool_calls = true
cache_hints = 4
count_error_permille = 25
concurrent_requests = 4
[models.prices]
input_uncached = 4_000_000
cache_read = 200_000
cache_write = 5_000_000
output = 20_000_000

[[routes]]
id = "default"
when = "always"
models = ["frontier"]

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

[tools]
work_dir = "~/made-up-work"
scratch_dir = "~/made-up-scratch"
shell_path = "/usr/bin:/bin"
shell_default_seconds = 120
shell_max_seconds = 600
output_cap_chars = 30_000
fetch_max_bytes = 5_242_880
fetch_seconds = 30

[traces]
phoenix_url = "http://127.0.0.1:6006"
project = "made-up-project"
"""

# A model on this Mac, with made-up weights, and a private route to it.
# Added to GOOD it makes a table that uses both kinds of model.
LOCAL = """
[[models]]
name = "local"
adapter = "openai_chat"
model = "local"
base_url = "http://127.0.0.1:9931"
window = 65_536
max_output = 16_000
tool_calls = true
cache_hints = 0
count_error_permille = 0
concurrent_requests = 2
weights_sha256 = "%s"
server = "llama.cpp b12826"
server_dir = "~/lamplighter-models/llama"
count = "llama_cpp"
call_markers = ["<tool_call>", "<function="]
""" % ("0" * 64)

PRIVATE_ROUTE = """
[[routes]]
id = "private"
when = "private"
models = ["local"]
"""


def with_local(base: str = GOOD) -> str:
    """Give the settings with the local model and a private route before the default."""
    at = base.index("[[routes]]")
    return base[:at] + LOCAL.lstrip() + "\n" + PRIVATE_ROUTE.lstrip() + "\n" + base[at:]


def with_profile(config: Config, profile: ModelProfile) -> Config:
    """Give the settings with the profile of the model an ordinary run starts on."""
    first = config.default_model()
    models = tuple(
        dataclasses.replace(entry, profile=profile) if entry == first else entry
        for entry in config.models
    )
    return dataclasses.replace(config, models=models)
