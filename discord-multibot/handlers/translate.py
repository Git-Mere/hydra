"""Translate mode handler (spec section 3)."""

from config import ChannelConfig, get_model_chain
from llm import client
from llm.prompts import TRANSLATE_SYSTEM


def handle(cfg: ChannelConfig, text: str) -> str:
    """Translate ``text`` using the default model.

    Raises llm.client.LLMError on failure (caller posts a guidance message).
    """
    return client.complete(get_model_chain(), TRANSLATE_SYSTEM, text)
