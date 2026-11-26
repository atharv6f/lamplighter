"""Entry point: builds the adapters and starts the agent."""

import httpx

from lamplighter.adapters import SystemClock
from lamplighter.adapters.anthropic import AnthropicModel
from lamplighter.app.config import Config
from lamplighter.ports import ModelClient


def build_model(config: Config, api_key: str) -> ModelClient:
    """Build the client for the model the settings name."""
    return AnthropicModel(config.model, api_key, httpx.AsyncClient(), SystemClock())


def main() -> None:
    """Start Lamplighter. Nothing is wired in yet."""
    print("lamplighter: nothing to run yet")
