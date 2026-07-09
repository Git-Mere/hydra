"""OpenRouter call wrapper (spec section 7).

Wraps the OpenAI SDK pointed at OpenRouter. Handles 429 rate limits with
OpenRouter server-side model fallback, bounded client-side batch retries, a
request timeout, and optional identifying headers.

Public surface:
    complete(plan, system_prompt, user_message) -> str
    complete_with_tools(plan, system_prompt, user_message, tools, tool_executor)
        -> str  (async; runs a bounded tool-call loop for web searching mode)
    LLMError  -- raised on unrecoverable failure; carries a user-facing message.

``plan`` is the batched model plan from config.get_model_plan(): an ordered
list of ``{"models": [...], "reasoning": {..}|None}`` batches, each already
sliced to OpenRouter's fallback cap and reasoning-consistent.
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
MAX_RETRIES = 3               # full-chain passes on 429 before giving up
BACKOFF_BASE = 2.0           # seconds; sleep = BACKOFF_BASE * 2**attempt
MAX_TOOL_ITERATIONS = 4       # cap on model<->tool round-trips in web searching mode
MAX_BACKOFF_SECONDS = 8.0

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


def _retry_after_seconds(exc: RateLimitError, attempt: int) -> float:
    """Return bounded 429 backoff, preferring server Retry-After hints."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after is not None:
        try:
            # OpenRouter sends numeric seconds; HTTP-date Retry-After values are ignored.
            return min(float(retry_after), MAX_BACKOFF_SECONDS)
        except (TypeError, ValueError):
            pass

    body = getattr(exc, "body", None)
    metadata = None
    if isinstance(body, dict):
        metadata = body.get("metadata")
        if metadata is None and isinstance(body.get("error"), dict):
            metadata = body["error"].get("metadata")
    if isinstance(metadata, dict):
        retry_after = metadata.get("retry_after_seconds")
        if retry_after is not None:
            try:
                return min(float(retry_after), MAX_BACKOFF_SECONDS)
            except (TypeError, ValueError):
                pass

    return min(BACKOFF_BASE * (2 ** attempt), MAX_BACKOFF_SECONDS)


def _create_completion(plan: list[dict], messages: list[dict], tools: Optional[list] = None):
    """Make one chat-completion call and return the assistant message object.

    Walks the batched model ``plan`` (each batch already sliced to OpenRouter's
    fallback cap), using OpenRouter's server-side fallback within each batch and
    applying the batch's shared reasoning param. If every batch 429s, retries
    the full plan with bounded backoff up to MAX_RETRIES passes. Raises LLMError
    on rate-limit exhaustion, timeout, or any API error. This is the single
    place the 429/timeout policy lives; both complete() and the tool loop use it.
    """
    client = _get_client()
    if not plan:
        raise LLMError()
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_RETRIES):
        for batch in plan:
            models = batch["models"]
            reasoning = batch.get("reasoning")
            try:
                extra_body = {"models": models}
                if reasoning:
                    extra_body["reasoning"] = reasoning
                kwargs = {
                    "model": models[0],
                    "messages": messages,
                    "extra_body": extra_body,
                }
                if tools:
                    kwargs["tools"] = tools
                resp = client.chat.completions.create(**kwargs)
                if resp is None or not getattr(resp, "choices", None):
                    # OpenRouter sometimes returns HTTP 200 whose body is an
                    # error object (no completion). resp.choices is None/empty.
                    # Surface the real cause, then treat it as a soft, retryable
                    # failure so the remaining fallback batches still get a try.
                    error = getattr(resp, "error", None)
                    if error is None:
                        extra = getattr(resp, "model_extra", None)
                        if isinstance(extra, dict):
                            error = extra.get("error")
                    logger.warning(
                        "OpenRouter returned no choices for batch %s: %r",
                        models,
                        error,
                    )
                    last_exc = LLMError(f"OpenRouter returned no choices: {error!r}")
                    continue

                logger.info("OpenRouter completion served by model %s", resp.model)
                return resp.choices[0].message

            except RateLimitError as exc:
                last_exc = exc
                logger.warning("429 from OpenRouter for model batch %s", models)
                continue

            except APITimeoutError as exc:
                logger.warning("OpenRouter request timed out: %s", exc)
                raise LLMError() from exc

            except APIError as exc:
                logger.error("OpenRouter API error: %s", exc)
                raise LLMError() from exc

        # No point sleeping after the final full-chain pass.
        if attempt < MAX_RETRIES - 1:
            delay = _retry_after_seconds(last_exc, attempt)
            logger.warning(
                "All OpenRouter model batches rate-limited (pass %d/%d); retrying in %.1fs",
                attempt + 1,
                MAX_RETRIES,
                delay,
            )
            time.sleep(delay)

    logger.error("Rate limit not cleared after %d full-chain passes", MAX_RETRIES)
    raise LLMError() from last_exc


def complete(plan: list[dict], system_prompt: str, user_message: str) -> str:
    """Call the model and return the reply text (single-shot, no tools).

    Used by translate mode.
    """
    message = _create_completion(
        plan,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    text = (message.content or "").strip()
    if not text:
        logger.warning("Empty completion from model plan %s", plan)
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
    plan: list[dict],
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

    # [websearch-diag] TEMPORARY instrumentation to see why the tool loop hits
    # the cap and answers "couldn't find results". Remove once root cause found.
    tool_calls_made = 0

    for iteration in range(1, max_iterations + 1):
        logger.info("[websearch-diag] iteration %d/%d", iteration, max_iterations)
        message = await asyncio.to_thread(_create_completion, plan, messages, tools)
        if not getattr(message, "tool_calls", None):
            text = (message.content or "").strip()
            if not text:
                logger.warning("Empty completion from model plan %s", plan)
                raise LLMError()
            logger.info(
                "[websearch-diag] final answer len=%d snippet=%r", len(text), text[:300]
            )
            return text

        messages.append(_assistant_message_dict(message))
        for tc in message.tool_calls:
            raw_args = tc.function.arguments or "{}"
            try:
                arguments = json.loads(raw_args)
                valid_args = isinstance(arguments, dict)
            except (json.JSONDecodeError, TypeError):
                valid_args = False
            if not valid_args:
                # Bad tool args: don't call the tool with {} (Tavily would reject
                # the empty query). Feed the error back so the model retries.
                logger.info(
                    "[websearch-diag] tool_call name=%s args=%r",
                    tc.function.name,
                    tc.function.arguments,
                )
                logger.warning(
                    "Tool %s called with invalid JSON arguments: %r",
                    tc.function.name,
                    tc.function.arguments,
                )
                result = f"Tool error: arguments were not valid JSON: {raw_args}"
            else:
                logger.info(
                    "[websearch-diag] tool_call name=%s args=%r",
                    tc.function.name,
                    arguments,
                )
                try:
                    result = await tool_executor(tc.function.name, arguments)
                except Exception as exc:  # noqa: BLE001 -- keep answering without the tool
                    logger.warning("Tool %s failed: %s", tc.function.name, exc)
                    result = f"Tool error: {exc}"
            tool_calls_made += 1
            logger.info(
                "[websearch-diag] tool_result name=%s len=%d snippet=%r",
                tc.function.name,
                len(result),
                result[:300],
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )

    # Cap reached: force a final answer with no further tool calls.
    logger.info("Tool loop hit the %d-iteration cap; forcing a final answer", max_iterations)
    logger.info(
        "[websearch-diag] cap reached: %d tool call(s) made across %d iteration(s); "
        "model never returned a content answer",
        tool_calls_made,
        max_iterations,
    )
    message = await asyncio.to_thread(_create_completion, plan, messages, None)
    text = (message.content or "").strip()
    if not text:
        logger.warning("Empty final completion from model plan %s", plan)
        raise LLMError()
    logger.info(
        "[websearch-diag] final answer len=%d snippet=%r", len(text), text[:300]
    )
    return text
