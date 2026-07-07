"""Tests for the pure helpers, the JSON config store, and the slash commands.

Run: python3 -m pytest   (or: python3 tests/test_bot.py)
Requires the project deps installed (discord.py, openai, ...).
"""

import json
import os
import sys
import tempfile
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from discord.app_commands import MissingPermissions

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


# --- JSON config store -------------------------------------------------------

def _tmp_store_path():
    d = tempfile.mkdtemp(prefix="mbcfg_")
    return os.path.join(d, "channel_config.json")


def test_store_set_get_and_persist():
    path = _tmp_store_path()
    store = config.JsonStore(path)
    assert store.get(1, 2) is None                       # unregistered -> None

    cfg = store.set(1, 2, "chat", "mention")
    assert cfg == config.ChannelConfig("chat", "mention", True)
    assert store.get(1, 2) == cfg
    assert store.get("1", "2") == cfg                     # int/str keys equivalent

    # Reloading from disk yields the same config (persistence works).
    reloaded = config.JsonStore(path)
    assert reloaded.get(1, 2) == config.ChannelConfig("chat", "mention", True)


def test_store_guild_and_channel_keying():
    store = config.JsonStore(_tmp_store_path())
    store.set(100, 200, "translate", "auto")
    assert store.get(100, 200).mode == "translate"
    assert store.get(100, 999) is None    # same guild, different channel
    assert store.get(999, 200) is None    # different guild, same channel


def test_store_set_replaces_existing():
    store = config.JsonStore(_tmp_store_path())
    store.set(1, 2, "chat", "mention")
    store.set(1, 2, "translate", "auto")
    assert store.get(1, 2) == config.ChannelConfig("translate", "auto", True)


def test_store_disable():
    path = _tmp_store_path()
    store = config.JsonStore(path)
    store.set(1, 2, "chat", "auto")

    assert store.disable(1, 2) is True
    assert store.get(1, 2).enabled is False
    # persisted disabled state survives a reload
    assert config.JsonStore(path).get(1, 2).enabled is False

    assert store.disable(1, 9999) is False   # nothing to disable


def test_store_atomic_write_leaves_no_temp_file():
    path = _tmp_store_path()
    store = config.JsonStore(path)
    store.set(1, 2, "chat", "auto")

    directory = os.path.dirname(path)
    leftovers = [n for n in os.listdir(directory) if n.startswith(".channel_config.")]
    assert leftovers == []
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == {"1": {"2": {"mode": "chat", "trigger": "auto", "enabled": True}}}


def test_store_missing_file_starts_empty():
    store = config.JsonStore(os.path.join(tempfile.mkdtemp(), "does_not_exist.json"))
    assert store.get(1, 2) is None


def test_store_corrupt_file_starts_empty():
    path = _tmp_store_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ]]")
    store = config.JsonStore(path)          # must not raise
    assert store.get(1, 2) is None
    # store is usable afterwards (overwrites the corrupt file)
    store.set(1, 2, "chat", "auto")
    assert config.JsonStore(path).get(1, 2) is not None


def test_module_wrappers_use_singleton_store():
    """set_channel_config / disable_channel / get_channel_config round-trip."""
    prev = config._store
    config._store = config.JsonStore(_tmp_store_path())
    try:
        assert config.get_channel_config(5, 6) is None
        config.set_channel_config(5, 6, "translate", "auto")
        got = config.get_channel_config(5, 6)
        assert got == config.ChannelConfig("translate", "auto", True)
        assert config.disable_channel(5, 6) is True
        assert config.get_channel_config(5, 6).enabled is False
    finally:
        config._store = prev


def test_default_model_env_override():
    prev = os.environ.get("DEFAULT_MODEL")
    try:
        os.environ.pop("DEFAULT_MODEL", None)
        assert config.get_default_model() == config.DEFAULT_MODEL
        os.environ["DEFAULT_MODEL"] = "vendor/custom-model"
        assert config.get_default_model() == "vendor/custom-model"
    finally:
        if prev is None:
            os.environ.pop("DEFAULT_MODEL", None)
        else:
            os.environ["DEFAULT_MODEL"] = prev


# --- Slash commands / app_commands tree --------------------------------------

def test_command_tree_builds():
    assert bot.tree.get_command("setup") is not None
    assert bot.tree.get_command("setup-off") is not None

    setup = bot.tree.get_command("setup")
    param_names = {p.name for p in setup.parameters}
    assert {"mode", "trigger"} <= param_names
    # Pickers: choices are exposed to Discord.
    mode_param = next(p for p in setup.parameters if p.name == "mode")
    assert {c.value for c in mode_param.choices} == {"translate", "chat"}


class _FakeInteraction:
    def __init__(self, permissions):
        self.permissions = permissions


def _run_checks(command, interaction):
    """Run every check on a command synchronously (they are sync predicates)."""
    for chk in command.checks:
        result = chk(interaction)
        assert result is True


def test_setup_requires_manage_channels():
    allowed = _FakeInteraction(discord.Permissions(manage_channels=True))
    denied = _FakeInteraction(discord.Permissions.none())

    for command in (bot.tree.get_command("setup"), bot.tree.get_command("setup-off")):
        assert command.checks, "command must carry a permission check"
        _run_checks(command, allowed)      # passes for a privileged member

        raised = False
        try:
            _run_checks(command, denied)   # blocked server-side for others
        except MissingPermissions:
            raised = True
        assert raised


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
