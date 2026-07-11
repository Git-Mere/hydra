"""Translate mode handler (spec section 3)."""

from config import ChannelConfig, get_model_plan
from llm import client
from llm.prompts import get_translate_system


def handle(cfg: ChannelConfig, text: str) -> str:
    """Translate ``text`` using the default model.

    Emits both a polite and a casual translation. Direction is auto-detected
    from the input language (Korean -> English; otherwise -> Korean).

    Raises llm.client.LLMError on failure (caller posts a guidance message).
    """
    return client.complete(get_model_plan(), get_translate_system(text), text)
