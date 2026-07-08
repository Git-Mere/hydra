"""Tests for the pure helpers, the JSON config store, and the slash commands.

Run: python3 -m pytest   (or: python3 tests/test_bot.py)
Requires the project deps installed (discord.py, openai, ...).
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord
from discord.app_commands import MissingPermissions

import bot
import config
from handlers import chat as chat_handler
from llm import client as llm_client
from llm import tavily_search


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

    def __init__(self, fail_times, reply="ok", served_model="served-model"):
        self.fail_times = fail_times
        self.reply = reply
        self.served_model = served_model
        self.calls = 0
        self.kwargs = []

    def create(self, *, model, messages, tools=None, extra_body=None):
        import httpx
        from openai import RateLimitError

        self.calls += 1
        kwargs = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "extra_body": extra_body,
        }
        self.kwargs.append(kwargs)
        if self.calls <= self.fail_times:
            resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
            raise RateLimitError("rate limited", response=resp, body=None)

        class _Msg:
            content = self.reply

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        response = _Resp()
        response.model = self.served_model
        return response


def _rate_limit_error(headers=None, body=None):
    import httpx
    from openai import RateLimitError

    resp = httpx.Response(
        429,
        headers=headers or {},
        request=httpx.Request("POST", "http://x"),
    )
    return RateLimitError("rate limited", response=resp, body=body)


class _BatchFakeCompletions:
    def __init__(self, fail_batches, reply="ok", served_model="served-model"):
        self.fail_batches = [tuple(batch) for batch in fail_batches]
        self.reply = reply
        self.served_model = served_model
        self.calls = []

    def create(self, *, model, messages, tools=None, extra_body=None):
        kwargs = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "extra_body": extra_body,
        }
        self.calls.append(kwargs)
        batch = tuple(extra_body["models"])
        if batch in self.fail_batches:
            raise _rate_limit_error()

        class _Msg:
            content = self.reply

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        response = _Resp()
        response.model = self.served_model
        return response


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
        out = llm_client.complete(["m"], "sys", "hi")
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
            llm_client.complete(["m"], "sys", "hi")
        except llm_client.LLMError:
            raised = True
        assert raised
        assert fc.calls == llm_client.MAX_RETRIES  # gave up after MAX_RETRIES
    finally:
        llm_client.time.sleep = real_sleep
        llm_client._client = None


def test_429_batch_falls_through_to_next_batch():
    first_batch = ["m1", "m2", "m3"]
    second_batch = ["m4", "m5", "m6"]
    fc = _BatchFakeCompletions(fail_batches=[first_batch], reply="from fallback")
    _install_fake_client(fc)
    try:
        out = llm_client.complete(first_batch + second_batch, "sys", "hi")
        assert out == "from fallback"
        assert len(fc.calls) == 2
        assert fc.calls[0]["model"] == "m1"
        assert fc.calls[0]["extra_body"]["models"] == first_batch
        assert "models" not in fc.calls[0]
        assert fc.calls[1]["model"] == "m4"
        assert fc.calls[1]["extra_body"]["models"] == second_batch
        assert "models" not in fc.calls[1]
    finally:
        llm_client._client = None


def test_served_model_is_logged():
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Handler()
    llm_client.logger.addHandler(handler)
    previous_level = llm_client.logger.level
    llm_client.logger.setLevel(logging.INFO)
    fc = _FakeCompletions(fail_times=0, reply="ok", served_model="actual/model:free")
    _install_fake_client(fc)
    try:
        assert llm_client.complete(["requested/model:free"], "sys", "hi") == "ok"
        assert any("actual/model:free" in record.getMessage() for record in records)
    finally:
        llm_client.logger.removeHandler(handler)
        llm_client.logger.setLevel(previous_level)
        llm_client._client = None


def test_full_chain_exhaustion_uses_capped_retry_after_backoff():
    class _AlwaysRateLimited:
        def __init__(self):
            self.calls = []

        def create(self, *, model, messages, tools=None, extra_body=None):
            kwargs = {
                "model": model,
                "messages": messages,
                "tools": tools,
                "extra_body": extra_body,
            }
            self.calls.append(kwargs)
            raise _rate_limit_error(headers={"Retry-After": "60"})

    slept = []
    real_sleep = llm_client.time.sleep
    llm_client.time.sleep = lambda s: slept.append(s)
    fc = _AlwaysRateLimited()
    _install_fake_client(fc)
    try:
        raised = False
        try:
            llm_client.complete(["m1", "m2", "m3", "m4", "m5", "m6"], "sys", "hi")
        except llm_client.LLMError:
            raised = True
        assert raised
        assert len(fc.calls) == 2 * llm_client.MAX_RETRIES
        assert fc.calls[0]["extra_body"]["models"] == ["m1", "m2", "m3"]
        assert "models" not in fc.calls[0]
        assert slept == [8.0, 8.0]
    finally:
        llm_client.time.sleep = real_sleep
        llm_client._client = None


# --- Tool-call loop (chat web search) ----------------------------------------
#
# These exercise the plumbing with a MOCKED model (_create_completion) and a
# MOCKED tool executor. No real network / MCP calls are made.

class _FakeFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FakeFn(name, arguments)


class _FakeMsg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def _snapshot(messages):
    return [m.copy() if isinstance(m, dict) else m for m in messages]


_DUMMY_TOOLS = [{"type": "function", "function": {"name": "web_search"}}]


def test_mcp_tools_to_openai_maps_schemas():
    @dataclass
    class _T:
        name: str
        description: object
        inputSchema: object

    tools = [_T("web_search", "search the web",
                {"type": "object", "properties": {"query": {"type": "string"}}})]
    schemas = tavily_search.mcp_tools_to_openai(tools)
    assert len(schemas) == 1
    fn = schemas[0]
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "web_search"
    assert fn["function"]["description"] == "search the web"
    assert fn["function"]["parameters"]["properties"]["query"]["type"] == "string"

    # A tool with no description / schema still yields a valid object schema.
    fallback = tavily_search.mcp_tools_to_openai([_T("t", None, None)])
    assert fallback[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_tool_loop_executes_and_feeds_back():
    responses = [
        _FakeMsg(tool_calls=[_FakeToolCall("c1", "web_search", '{"query": "seoul weather"}')]),
        _FakeMsg(content="서울은 맑음"),
    ]
    seen = []

    def fake_create(models, messages, tools=None):
        seen.append({"tools": tools, "messages": _snapshot(messages)})
        return responses[len(seen) - 1]

    executor_calls = []

    async def executor(name, arguments):
        executor_calls.append((name, arguments))
        return "sunny 25C"

    prev = llm_client._create_completion
    llm_client._create_completion = fake_create
    try:
        out = asyncio.run(
            llm_client.complete_with_tools(["m"], "sys", "weather?", _DUMMY_TOOLS, executor)
        )
    finally:
        llm_client._create_completion = prev

    assert out == "서울은 맑음"
    # The MCP tool was invoked with the JSON arguments parsed into a dict.
    assert executor_calls == [("web_search", {"query": "seoul weather"})]
    # First call offered the tool schemas to the model.
    assert seen[0]["tools"] == _DUMMY_TOOLS
    # Second call fed the tool result (and the assistant tool_calls) back.
    second = seen[1]["messages"]
    assert any(m.get("role") == "tool" and m.get("content") == "sunny 25C" for m in second)
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in second)


def test_tool_loop_respects_iteration_cap():
    calls = {"n": 0}

    def fake_create(models, messages, tools=None):
        calls["n"] += 1
        if tools:  # still offering tools -> model keeps requesting one
            return _FakeMsg(tool_calls=[_FakeToolCall("c", "web_search", "{}")])
        return _FakeMsg(content="final answer")  # forced tool-free call

    async def executor(name, arguments):
        return "result"

    prev = llm_client._create_completion
    llm_client._create_completion = fake_create
    try:
        out = asyncio.run(
            llm_client.complete_with_tools(
                ["m"], "sys", "q", _DUMMY_TOOLS, executor, max_iterations=4
            )
        )
    finally:
        llm_client._create_completion = prev

    assert out == "final answer"
    # 4 tool-offering rounds + 1 forced tool-free call = the loop terminates.
    assert calls["n"] == 5


def test_tool_loop_tool_error_is_fed_back():
    responses = [
        _FakeMsg(tool_calls=[_FakeToolCall("c1", "web_search", "{}")]),
        _FakeMsg(content="best effort answer"),
    ]
    seen = []

    def fake_create(models, messages, tools=None):
        seen.append(_snapshot(messages))
        return responses[len(seen) - 1]

    async def executor(name, arguments):
        raise RuntimeError("mcp down")

    prev = llm_client._create_completion
    llm_client._create_completion = fake_create
    try:
        out = asyncio.run(
            llm_client.complete_with_tools(["m"], "sys", "q", _DUMMY_TOOLS, executor)
        )
    finally:
        llm_client._create_completion = prev

    assert out == "best effort answer"
    tool_msgs = [m for m in seen[1] if m.get("role") == "tool"]
    assert tool_msgs and "Tool error" in tool_msgs[0]["content"]


def test_chat_without_tavily_key_answers_without_tools():
    prev_key = os.environ.pop("TAVILY_API_KEY", None)
    prev_complete = llm_client.complete
    llm_client.complete = lambda model, system, text: "답변"
    try:
        cfg = config.ChannelConfig("chat", "auto", True)
        out = asyncio.run(chat_handler.handle(cfg, "hi"))
        assert out == "답변"
    finally:
        llm_client.complete = prev_complete
        if prev_key is not None:
            os.environ["TAVILY_API_KEY"] = prev_key


def _set_tavily_key():
    """Set a dummy key; return the previous value for restoration."""
    prev = os.environ.get("TAVILY_API_KEY")
    os.environ["TAVILY_API_KEY"] = "dummy"
    return prev


def _restore_tavily_key(prev):
    if prev is None:
        os.environ.pop("TAVILY_API_KEY", None)
    else:
        os.environ["TAVILY_API_KEY"] = prev


def test_chat_mcp_connection_failure_falls_back_to_no_tools():
    prev_key = _set_tavily_key()
    prev_session = tavily_search.session
    prev_complete = llm_client.complete

    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    tavily_search.session = boom
    llm_client.complete = lambda model, system, text: "fallback"
    try:
        cfg = config.ChannelConfig("chat", "auto", True)
        out = asyncio.run(chat_handler.handle(cfg, "hi"))
        assert out == "fallback"
    finally:
        tavily_search.session = prev_session
        llm_client.complete = prev_complete
        _restore_tavily_key(prev_key)


def test_chat_keeps_answer_despite_session_teardown_error():
    """A good, search-backed answer must survive an MCP teardown failure."""
    from contextlib import asynccontextmanager

    prev_key = _set_tavily_key()
    prev_session = tavily_search.session
    prev_cwt = llm_client.complete_with_tools
    prev_complete = llm_client.complete

    @asynccontextmanager
    async def flaky_session():
        async def executor(name, arguments):
            return ""
        try:
            yield ([], executor)
        finally:
            raise RuntimeError("teardown boom")

    async def fake_complete_with_tools(model, system, text, tools, executor):
        return "search-backed answer"

    tavily_search.session = flaky_session
    llm_client.complete_with_tools = fake_complete_with_tools
    llm_client.complete = lambda *a, **k: (_ for _ in ()).throw(AssertionError("fallback used"))
    try:
        cfg = config.ChannelConfig("chat", "auto", True)
        out = asyncio.run(chat_handler.handle(cfg, "hi"))
        assert out == "search-backed answer"
    finally:
        tavily_search.session = prev_session
        llm_client.complete_with_tools = prev_cwt
        llm_client.complete = prev_complete
        _restore_tavily_key(prev_key)


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


def _restore_model_env(prev_default, prev_chain):
    if prev_default is None:
        os.environ.pop("DEFAULT_MODEL", None)
    else:
        os.environ["DEFAULT_MODEL"] = prev_default
    if prev_chain is None:
        os.environ.pop("MODEL_CHAIN", None)
    else:
        os.environ["MODEL_CHAIN"] = prev_chain


def test_model_chain_default():
    prev_default = os.environ.get("DEFAULT_MODEL")
    prev_chain = os.environ.get("MODEL_CHAIN")
    try:
        os.environ.pop("DEFAULT_MODEL", None)
        os.environ.pop("MODEL_CHAIN", None)
        assert config.get_model_chain() == config.DEFAULT_MODEL_CHAIN
        assert config.get_default_model() == config.get_model_chain()[0]
    finally:
        _restore_model_env(prev_default, prev_chain)


def test_model_chain_model_chain_env_override():
    prev_default = os.environ.get("DEFAULT_MODEL")
    prev_chain = os.environ.get("MODEL_CHAIN")
    try:
        os.environ["DEFAULT_MODEL"] = "ignored/default"
        os.environ["MODEL_CHAIN"] = " vendor/a , ,vendor/b, vendor/c "
        assert config.get_model_chain() == ["vendor/a", "vendor/b", "vendor/c"]
        assert config.get_default_model() == "vendor/a"
    finally:
        _restore_model_env(prev_default, prev_chain)


def test_model_chain_default_model_primary_with_fallbacks_deduped():
    prev_default = os.environ.get("DEFAULT_MODEL")
    prev_chain = os.environ.get("MODEL_CHAIN")
    try:
        os.environ.pop("MODEL_CHAIN", None)
        chosen = config.DEFAULT_MODEL_CHAIN[3]
        os.environ["DEFAULT_MODEL"] = chosen
        chain = config.get_model_chain()
        assert chain[0] == chosen
        assert chain.count(chosen) == 1
        assert chain[1:] == [model for model in config.DEFAULT_MODEL_CHAIN if model != chosen]
        assert config.get_default_model() == chain[0]
    finally:
        _restore_model_env(prev_default, prev_chain)


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
