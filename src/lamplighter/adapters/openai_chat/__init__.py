"""The model client for servers on this Mac that speak the chat completions format."""

from lamplighter.adapters.openai_chat.client import OpenAIChatModel
from lamplighter.adapters.openai_chat.port_owner import port_owner

__all__ = ["OpenAIChatModel", "port_owner"]
