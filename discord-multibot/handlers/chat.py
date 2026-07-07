"""Chat mode handler (spec section 3). Single-shot, no history (Phase 1)."""

from config import ChannelConfig, get_default_model
from llm import client
from llm.prompts import CHAT_SYSTEM


def handle(cfg: ChannelConfig, text: str) -> str:
    """Answer ``text`` using the default model.

    Raises llm.client.LLMError on failure (caller posts a guidance message).
    """
    return client.complete(get_default_model(), CHAT_SYSTEM, text)
