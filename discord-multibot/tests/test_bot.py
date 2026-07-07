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
from llm import client as llm_client


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
    # mention in the middle should not leave a double space
    assert bot.strip_mentions("hi <@42> there", user) == "hi there"


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


class _FakeCompletions:
    """Stand-in for client.chat.completions that raises 429 a fixed number
    of times, then returns a canned reply."""

    def __init__(self, fail_times, reply="ok"):
        self.fail_times = fail_times
        self.reply = reply
        self.calls = 0

    def create(self, **kwargs):
        import httpx
        from openai import RateLimitError

        self.calls += 1
        if self.calls <= self.fail_times:
            resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
            raise RateLimitError("rate limited", response=resp, body=None)

        class _Msg:
            content = self.reply

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


class _FakeClient:
    def __init__(self, fake_completions):
        self.chat = type("Chat", (), {"completions": fake_completions})()


def _install_fake_client(monkey_completions):
    llm_client._client = _FakeClient(monkey_completions)


def test_429_retry_then_success():
    slept = []
    real_sleep = llm_client.time.sleep
    llm_client.time.sleep = lambda s: slept.append(s)
    try:
        fc = _FakeCompletions(fail_times=2, reply="translated")
        _install_fake_client(fc)
        out = llm_client.complete("m", "sys", "hi")
        assert out == "translated"
        assert fc.calls == 3          # 2 failures + 1 success
        assert len(slept) == 2        # slept before each retry
        assert slept == [2.0, 4.0]    # exponential backoff
    finally:
        llm_client.time.sleep = real_sleep
        llm_client._client = None


def test_429_exhaustion_raises_llmerror():
    real_sleep = llm_client.time.sleep
    llm_client.time.sleep = lambda s: None
    try:
        fc = _FakeCompletions(fail_times=99)
        _install_fake_client(fc)
        raised = False
        try:
            llm_client.complete("m", "sys", "hi")
        except llm_client.LLMError:
            raised = True
        assert raised
        assert fc.calls == llm_client.MAX_RETRIES  # gave up after MAX_RETRIES
    finally:
        llm_client.time.sleep = real_sleep
        llm_client._client = None


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
