"""Translate mode handler (spec section 3)."""

from config import ChannelConfig, get_default_model
from llm import client
from llm.prompts import TRANSLATE_SYSTEM


def handle(cfg: ChannelConfig, text: str) -> str:
    """Translate ``text`` using the default model.

    Raises llm.client.LLMError on failure (caller posts a guidance message).
    """
    return client.complete(get_default_model(), TRANSLATE_SYSTEM, text)
