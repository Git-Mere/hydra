"""Minimal tests for the pure helpers and config loading.

Run: python3 -m pytest   (or: python3 tests/test_bot.py)
Requires the project deps installed (discord.py, pyyaml, ...).
"""

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bot
import config


@dataclass
class _StubUser:
    id: int


def test_is_meaningful():
    assert bot.is_meaningful("안녕하세요")
    assert bot.is_meaningful("hello world")
    assert not bot.is_meaningful("   ")
    assert not bot.is_meaningful("")
    assert not bot.is_meaningful("😀😀")            # emoji only
    assert not bot.is_meaningful("<:custom:123>")   # custom emoji only
    assert not bot.is_meaningful("https://example.com")  # link only
    assert bot.is_meaningful("check this https://example.com")  # link + text


def test_strip_mentions():
    user = _StubUser(id=42)
    assert bot.strip_mentions("<@42> hi", user) == "hi"
    assert bot.strip_mentions("<@!42> what is 2+2?", user) == "what is 2+2?"
    assert bot.strip_mentions("no mention here", user) == "no mention here"


def test_split_message():
    assert bot.split_message("short") == ["short"]
    big = "a" * 4500
    chunks = bot.split_message(big)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == big
    # newline-preferring split
    text = ("x" * 1500) + "\n" + ("y" * 1000)
    chunks = bot.split_message(text)
    assert all(len(c) <= 2000 for c in chunks)


def test_config_lookup():
    # Uses the example channels.yaml shipped in the repo.
    translate = config.get_channel_config("111111111111111111")
    assert translate is not None
    assert translate.mode == "translate"
    assert translate.trigger == "auto"
    assert translate.enabled is True

    chat = config.get_channel_config("222222222222222222")
    assert chat is not None
    assert chat.mode == "chat"
    assert chat.trigger == "mention"

    assert config.get_channel_config("000000000000000000") is None  # unregistered


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
