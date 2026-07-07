"""Chat mode handler (spec section 3).

Answers a single user turn with an agentic web-search tool loop backed by
Tavily's remote MCP server. No cross-message history: the tool loop lives
entirely within one turn.

Graceful degradation (spec requirement 2): if TAVILY_API_KEY is unset or the
MCP connection fails, chat still answers -- just without web search -- via the
single-shot path. Only an LLMError (OpenRouter failure) propagates, so the bot
can post its user-facing guidance message.
"""

from __future__ import annotations

import asyncio
import logging

from config import ChannelConfig, get_default_model
from llm import client, tavily_search
from llm.client import LLMError
from llm.prompts import CHAT_SYSTEM

logger = logging.getLogger(__name__)


async def handle(cfg: ChannelConfig, text: str) -> str:
    """Answer ``text`` in Korean, using web search when it is available.

    Raises llm.client.LLMError on model failure (caller posts a guidance message).
    """
    model = get_default_model()
    # None until we get a search-backed answer. Kept outside the `with` so a
    # teardown error while closing the MCP session cannot discard a good answer.
    answer: str | None = None
    try:
        async with tavily_search.session() as (tools, executor):
            answer = await client.complete_with_tools(model, CHAT_SYSTEM, text, tools, executor)
    except LLMError:
        # Model failure -- let the bot surface its user-facing message.
        raise
    except tavily_search.TavilyUnavailable:
        # No API key configured: answer without search, no noise in the log.
        logger.info("TAVILY_API_KEY not set; chat answering without web search")
    except Exception:  # noqa: BLE001 -- MCP connection/protocol/teardown error
        logger.exception("Tavily MCP unavailable; chat answering without web search")

    if answer is not None:
        return answer
    return await asyncio.to_thread(client.complete, model, CHAT_SYSTEM, text)
