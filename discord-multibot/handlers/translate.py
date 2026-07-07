"""Translate mode handler (spec section 3)."""

from config import ChannelConfig
from llm import client
from llm.prompts import TRANSLATE_SYSTEM


def handle(cfg: ChannelConfig, text: str) -> str:
    """Translate ``text`` using the channel's model.

    Raises llm.client.LLMError on failure (caller posts a guidance message).
    """
    system_prompt = cfg.system_override or TRANSLATE_SYSTEM
    return client.complete(cfg.model, system_prompt, text)
