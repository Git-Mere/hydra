"""Chat mode handler (spec section 3). Single-shot, no history (Phase 1)."""

from config import ChannelConfig
from llm import client
from llm.prompts import CHAT_SYSTEM


def handle(cfg: ChannelConfig, text: str) -> str:
    """Answer ``text`` using the channel's model.

    Raises llm.client.LLMError on failure (caller posts a guidance message).
    """
    system_prompt = cfg.system_override or CHAT_SYSTEM
    return client.complete(cfg.model, system_prompt, text)
