"""OpenRouter call wrapper (spec section 7).

Wraps the OpenAI SDK pointed at OpenRouter. Handles 429 rate limits with
exponential backoff, a request timeout, and optional identifying headers.

Public surface:
    complete(model, system_prompt, user_message) -> str
    complete_with_tools(model, system_prompt, user_message, tools, tool_executor)
        -> str  (async; runs a bounded tool-call loop for chat mode)
    LLMError  -- raised on unrecoverable failure; carries a user-facing message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Awaitable, Callable, Optional

from openai import APITimeoutError, OpenAI, RateLimitError
from openai import APIError

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
REQUEST_TIMEOUT = 30.0        # seconds
MAX_RETRIES = 3               # attempts on 429 before giving up
BACKOFF_BASE = 2.0           # seconds; sleep = BACKOFF_BASE * 2**attempt
MAX_TOOL_ITERATIONS = 4       # cap on model<->tool round-trips in chat mode

# Short, user-facing guidance. Handlers/bot post this on failure.
USER_FACING_ERROR = "⚠️ 지금은 응답할 수 없어요. 잠시 후 다시 시도해 주세요."


class LLMError(Exception):
    """Raised when the model call cannot be completed.

    ``user_message`` is a short, channel-safe string the bot can post.
    """

    def __init__(self, user_message: str = USER_FACING_ERROR):
        super().__init__(user_message)
        self.user_message = user_message


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise LLMError(USER_FACING_ERROR)

        # Optional OpenRouter identifying headers (app attribution).
        default_headers = {}
        referer = os.environ.get("OPENROUTER_HTTP_REFERER")
        title = os.environ.get("OPENROUTER_X_TITLE")
        if referer:
            default_headers["HTTP-Referer"] = referer
        if title:
            default_headers["X-Title"] = title

        _client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            timeout=REQUEST_TIMEOUT,
            # We own the retry loop below, so disable the SDK's own retries.
            max_retries=0,
            default_headers=default_headers or None,
        )
    return _client


def _create_completion(model: str, messages: list[dict], tools: Optional[list] = None):
    """Make one chat-completion call and return the assistant message object.

    Retries 429s with exponential backoff up to MAX_RETRIES. Raises LLMError on
    rate-limit exhaustion, timeout, or any API error. This is the single place
    the 429/timeout policy lives; both complete() and the tool loop use it.
    """
    client = _get_client()
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_RETRIES):
        try:
            kwargs = {"model": model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message

        except RateLimitError as exc:
            last_exc = exc
            # No point sleeping after the final attempt.
            if attempt < MAX_RETRIES - 1:
                delay = BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "429 from OpenRouter (attempt %d/%d); retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, delay,
                )
                time.sleep(delay)
            continue

        except APITimeoutError as exc:
            logger.warning("OpenRouter request timed out: %s", exc)
            raise LLMError() from exc

        except APIError as exc:
            logger.error("OpenRouter API error: %s", exc)
            raise LLMError() from exc

    logger.error("Rate limit not cleared after %d attempts", MAX_RETRIES)
    raise LLMError() from last_exc


def complete(model: str, system_prompt: str, user_message: str) -> str:
    """Call the model and return the reply text (single-shot, no tools).

    Used by translate mode and as the search-free fallback for chat.
    """
    message = _create_completion(
        model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    text = (message.content or "").strip()
    if not text:
        logger.warning("Empty completion from model %s", model)
        raise LLMError()
    return text


def _assistant_message_dict(message) -> dict:
    """Serialise an assistant message carrying tool_calls back into request form."""
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in message.tool_calls
        ],
    }


async def complete_with_tools(
    model: str,
    system_prompt: str,
    user_message: str,
    tools: list,
    tool_executor: Callable[[str, dict], Awaitable[str]],
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> str:
    """Run a bounded agentic tool-call loop and return the model's final text.

    ``tools`` is a list of OpenAI/OpenRouter function schemas; ``tool_executor``
    is an async callable ``(name, arguments) -> str`` that runs the tool and
    returns its textual result. Each round: call the model with the tools; if it
    returns tool_calls, execute each and feed the results back; otherwise return
    the answer. After ``max_iterations`` rounds, make one final tool-free call so
    the model commits to an answer.

    A failing tool call does not abort the loop: the error is fed back as the
    tool result so the model can answer best-effort (spec requirement 2).
    The blocking OpenRouter calls run in a worker thread to keep the event loop
    responsive; 429/timeout handling is inherited from _create_completion.
    """
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for _ in range(max_iterations):
        message = await asyncio.to_thread(_create_completion, model, messages, tools)
        if not getattr(message, "tool_calls", None):
            text = (message.content or "").strip()
            if not text:
                logger.warning("Empty completion from model %s", model)
                raise LLMError()
            return text

        messages.append(_assistant_message_dict(message))
        for tc in message.tool_calls:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            try:
                result = await tool_executor(tc.function.name, arguments)
            except Exception as exc:  # noqa: BLE001 -- keep answering without the tool
                logger.warning("Tool %s failed: %s", tc.function.name, exc)
                result = f"Tool error: {exc}"
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    # Cap reached: force a final answer with no further tool calls.
    logger.info("Tool loop hit the %d-iteration cap; forcing a final answer", max_iterations)
    message = await asyncio.to_thread(_create_completion, model, messages, None)
    text = (message.content or "").strip()
    if not text:
        logger.warning("Empty final completion from model %s", model)
        raise LLMError()
    return text
