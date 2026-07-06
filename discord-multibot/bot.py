"""Discord multi-bot entrypoint (spec section 3).

One client, many channels. Each channel's behaviour (mode, model, trigger,
prompt) comes from channels.yaml via config.get_channel_config.

Flow per incoming message:
    1. ignore the bot's own messages (infinite-loop guard)
    2. look up channel config by channel_id; no config -> ignore
    3. respect enabled: false
    4. trigger check: auto -> all messages, mention -> only when @mentioned
    5. skip empty / whitespace / emoji-only / link-only messages
    6. dispatch to the mode handler
    7. reply to the channel (splitting to Discord's 2000-char limit)
"""

from __future__ import annotations

import logging
import os
import re

import discord
from dotenv import load_dotenv

from config import ChannelConfig, get_channel_config
from handlers import chat as chat_handler
from handlers import translate as translate_handler
from llm.client import USER_FACING_ERROR, LLMError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bot")

DISCORD_MAX_LEN = 2000

# Handlers keyed by mode. Adding a mode = one entry here + a handler module.
_HANDLERS = {
    "translate": translate_handler.handle,
    "chat": chat_handler.handle,
}

# Custom Discord emoji: <:name:id> or animated <a:name:id>.
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
# URLs.
_URL = re.compile(r"https?://\S+")
# User/role/channel mentions: <@123>, <@!123>, <@&123>, <#123>.
_MENTION = re.compile(r"<[@#][!&]?\d+>")
# Broad unicode emoji / pictograph ranges (good enough to detect emoji-only).
_UNICODE_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U0000FE00-\U0000FE0F\U00002190-\U000021FF\U00002B00-\U00002BFF‍⃣]+"
)


def is_meaningful(text: str) -> bool:
    """True if ``text`` has content worth sending to the model.

    Filters out whitespace-only, emoji-only, and link-only messages so the
    bot does not burn free-tier quota on them (spec section 8.3).
    """
    stripped = text
    stripped = _CUSTOM_EMOJI.sub("", stripped)
    stripped = _URL.sub("", stripped)
    stripped = _MENTION.sub("", stripped)
    stripped = _UNICODE_EMOJI.sub("", stripped)
    return bool(stripped.strip())


def strip_mentions(text: str, bot_user: discord.abc.User) -> str:
    """Remove the bot's own mention from ``text``, leaving the pure question.

    Handles both <@id> and <@!id> forms (spec section 8.6).
    """
    pattern = re.compile(rf"<@!?{bot_user.id}>")
    without = pattern.sub("", text)
    # Collapse the whitespace left where the mention token was removed.
    return re.sub(r"\s+", " ", without).strip()


def split_message(text: str, limit: int = DISCORD_MAX_LEN) -> list[str]:
    """Split ``text`` into chunks that each fit Discord's char limit.

    Prefers to break on newlines, then whitespace, then hard-cuts if a single
    token exceeds the limit (spec section 8.4).
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut == -1:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit  # no boundary; hard cut
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n ")
    if remaining:
        chunks.append(remaining)
    return chunks


def _should_process(cfg: ChannelConfig, message: discord.Message, bot_user: discord.abc.User) -> bool:
    """Apply the trigger rule for the channel."""
    if cfg.trigger == "auto":
        return True
    if cfg.trigger == "mention":
        return bot_user in message.mentions
    logger.warning("Unknown trigger %r for channel %s", cfg.trigger, message.channel.id)
    return False


intents = discord.Intents.default()
intents.message_content = True  # required to read message text (spec section 8.2)

client = discord.Client(intents=intents)


@client.event
async def on_ready() -> None:
    logger.info("Logged in as %s (id=%s)", client.user, client.user.id if client.user else "?")


@client.event
async def on_message(message: discord.Message) -> None:
    # 1. Never react to our own (or any bot's) messages -- infinite-loop guard.
    if message.author.bot:
        return

    # 2. Channel config lookup. No config -> not a bot channel, ignore.
    cfg = get_channel_config(message.channel.id)
    if cfg is None:
        return

    # 3. Disabled channel.
    if not cfg.enabled:
        return

    # 4. Trigger check.
    if not _should_process(cfg, message, client.user):
        return

    # 6a. For mention triggers, strip the mention to get the pure question.
    text = message.content
    if cfg.trigger == "mention":
        text = strip_mentions(text, client.user)

    # 5. Skip empty / emoji-only / link-only / whitespace-only messages.
    if not is_meaningful(text):
        return

    handler = _HANDLERS.get(cfg.mode)
    if handler is None:
        logger.warning("Unknown mode %r for channel %s", cfg.mode, message.channel.id)
        return

    # 6b + 7. Dispatch and reply, splitting long output. Never crash silently.
    try:
        async with message.channel.typing():
            reply = await client.loop.run_in_executor(None, handler, cfg, text)
    except LLMError as exc:
        await message.channel.send(exc.user_message)
        return
    except Exception:  # noqa: BLE001 -- last-resort guard so the bot stays up
        logger.exception("Unexpected handler error in channel %s", message.channel.id)
        await message.channel.send(USER_FACING_ERROR)
        return

    for chunk in split_message(reply):
        await message.channel.send(chunk)


def main() -> None:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set (copy .env.example to .env and fill it in).")
    client.run(token)


if __name__ == "__main__":
    main()
