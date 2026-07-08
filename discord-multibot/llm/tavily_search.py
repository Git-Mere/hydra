"""Tavily web-search via its remote MCP server (web searching mode only).

Connects to Tavily's hosted MCP endpoint over streamable HTTP, lists the
server's tools, and exposes them as OpenAI/OpenRouter function schemas plus an
async executor the tool-call loop can drive.

The API key comes from the ``TAVILY_API_KEY`` env var and is NEVER hardcoded.
When it is unset, ``session`` raises ``TavilyUnavailable`` so the caller can
tell the user web search is unavailable instead of answering from memory.

Usage::

    async with tavily_search.session() as (tools, executor):
        answer = await client.complete_with_tools(model, system, text, tools, executor)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

_TAVILY_MCP_URL = "https://mcp.tavily.com/mcp/?tavilyApiKey={key}"

# An async callable: (tool_name, arguments) -> textual result.
ToolExecutor = Callable[[str, dict], Awaitable[str]]


class TavilyUnavailable(Exception):
    """Raised when web search cannot be used (no key). Caller degrades gracefully."""


def _tavily_url() -> str:
    """Build the Tavily MCP URL from TAVILY_API_KEY, or raise if it is unset."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise TavilyUnavailable("TAVILY_API_KEY is not set")
    return _TAVILY_MCP_URL.format(key=api_key)


def mcp_tools_to_openai(tools: list) -> list[dict]:
    """Map a list of MCP ``Tool`` objects to OpenAI/OpenRouter function schemas."""
    schemas: list[dict] = []
    for tool in tools:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema or {"type": "object", "properties": {}},
                },
            }
        )
    return schemas


def _result_to_text(result: Any) -> str:
    """Flatten a CallToolResult's content blocks into a single text string."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


@asynccontextmanager
async def session():
    """Open a Tavily MCP session, yielding ``(tool_schemas, executor)``.

    Raises ``TavilyUnavailable`` if no API key is configured. Any connection or
    protocol error propagates to the caller (web searching mode then reports
    that search is unavailable). The session and its transport are torn down on
    exit.
    """
    url = _tavily_url()
    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()
            listed = await mcp_session.list_tools()
            schemas = mcp_tools_to_openai(listed.tools)
            logger.info("Tavily MCP exposes %d tool(s)", len(schemas))

            async def executor(name: str, arguments: dict) -> str:
                result = await mcp_session.call_tool(name, arguments)
                text = _result_to_text(result)
                if getattr(result, "isError", False):
                    logger.warning("Tavily tool %s reported an error", name)
                return text or "(no result)"

            yield schemas, executor
