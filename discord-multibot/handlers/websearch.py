"""Web searching mode handler (spec section 3).

Answers a single user turn with an agentic web-search tool loop backed by
Tavily's remote MCP server. No cross-message history: the tool loop lives
entirely within one turn.

Anti-hallucination: this mode answers ONLY from web-search results. If web
search is entirely unavailable (TAVILY_API_KEY unset or the MCP connection
fails), the bot does NOT fall back to a memory-based answer -- it tells the user
web search is unavailable, so it never fabricates facts. Only an LLMError
(OpenRouter failure) propagates, so the bot can post its user-facing guidance
message.
"""

from __future__ import annotations

import logging

from config import ChannelConfig, get_model_plan
from llm import client, tavily_search
from llm.client import LLMError
from llm.prompts import WEBSEARCH_SYSTEM

logger = logging.getLogger(__name__)

# Posted when web search cannot be used at all. We refuse to answer from model
# memory here so the bot never invents facts (anti-hallucination requirement).
WEBSEARCH_UNAVAILABLE_MESSAGE = (
    "⚠️ 지금은 웹 검색을 사용할 수 없어 답변을 드릴 수 없어요. 잠시 후 다시 시도해 주세요."
)


async def handle(cfg: ChannelConfig, text: str) -> str:
    """Answer ``text`` in Korean from web-search results.

    Returns a "web search unavailable" message (never a memory-based answer)
    when Tavily cannot be reached. Raises llm.client.LLMError on model failure
    (caller posts a guidance message).
    """
    plan = get_model_plan()
    # None until we get a search-backed answer. Kept outside the `with` so a
    # teardown error while closing the MCP session cannot discard a good answer.
    answer: str | None = None
    try:
        async with tavily_search.session() as (tools, executor):
            answer = await client.complete_with_tools(plan, WEBSEARCH_SYSTEM, text, tools, executor)
    except LLMError:
        # Model failure -- let the bot surface its user-facing message.
        raise
    except tavily_search.TavilyUnavailable:
        # No API key configured: web search is off, no noise in the log.
        logger.info("TAVILY_API_KEY not set; web search unavailable")
    except Exception:  # noqa: BLE001 -- MCP connection/protocol/teardown error
        logger.exception("Tavily MCP unavailable; web search unavailable")

    if answer is not None:
        return answer
    # Web search is unavailable: refuse to answer from parametric memory.
    return WEBSEARCH_UNAVAILABLE_MESSAGE
