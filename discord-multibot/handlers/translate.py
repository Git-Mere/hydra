"""Translate mode handler (spec section 3)."""

from config import ChannelConfig, get_model_plan
from llm import client
from llm.prompts import get_translate_system


def handle(cfg: ChannelConfig, text: str) -> str:
    """Translate ``text`` using the default model.

    Uses the channel's configured tone (casual/polite); unknown tone falls back
    to casual inside ``get_translate_system``.

    Raises llm.client.LLMError on failure (caller posts a guidance message).
    """
    return client.complete(get_model_plan(), get_translate_system(cfg.tone), text)
