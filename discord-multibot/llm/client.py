"""OpenRouter call wrapper (spec section 7).

Wraps the OpenAI SDK pointed at OpenRouter. Handles 429 rate limits with
exponential backoff, a request timeout, and optional identifying headers.

Public surface:
    complete(model, system_prompt, user_message) -> str
    LLMError  -- raised on unrecoverable failure; carries a user-facing message.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from openai import APITimeoutError, OpenAI, RateLimitError
from openai import APIError

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
REQUEST_TIMEOUT = 30.0        # seconds
MAX_RETRIES = 3               # attempts on 429 before giving up
BACKOFF_BASE = 2.0           # seconds; sleep = BACKOFF_BASE * 2**attempt

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


def complete(model: str, system_prompt: str, user_message: str) -> str:
    """Call the model and return the reply text.

    Retries 429s with exponential backoff up to MAX_RETRIES. Raises LLMError
    on rate-limit exhaustion, timeout, empty response, or any API error.
    """
    client = _get_client()
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                logger.warning("Empty completion from model %s", model)
                raise LLMError()
            return text

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
