# Discord Multi-Bot

One Discord bot that behaves differently **per channel**. Each channel is given
a **mode** (what the bot does) and a **trigger** (when it responds) at runtime
with the `/setup` slash command — no file editing, no restart.

- **translate** mode: translates messages between Korean and English. Direction
  is auto-detected from the input, and the bot always returns **both** a polite
  (공손/존댓말) and a casual (캐주얼/반말) version on two labeled lines.
- **web searching** mode: answers a question in Korean **strictly from web
  search** (via [Tavily](https://tavily.com)'s remote MCP server). It searches
  once in Korean and once in English to cross-check sources, and answers only
  from the results with cited URLs — if search finds nothing or is unavailable,
  it says so instead of inventing facts.

## LLM providers

All LLM calls go through one wrapper (`llm/client.py`) with automatic fallback:

- **Gemini (primary)** — when `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) is set, the
  bot uses `gemini-flash-lite-latest` through Google's OpenAI-compatible
  endpoint for both modes.
- **OpenRouter (fallback)** — if Gemini is unset or a call fails (rate limit,
  error), the request falls through to an OpenRouter free-model chain. With no
  Gemini key, everything runs on OpenRouter.

This is transparent per request: the served model is logged as
`... completion served by model <id>`.

## Directory layout

```
.
├── bot.py               # entrypoint: client + /setup, /setup-off + on_message flow
├── config.py            # JSON config store + model plans (Gemini-first, OpenRouter fallback)
├── channel_config.json  # runtime config (gitignored, created by /setup)
├── llm/
│   ├── client.py        # provider routing, 429 backoff, timeout, tool loop
│   ├── tavily_search.py # Tavily MCP client (web searching mode)
│   └── prompts.py       # translate (dual-tone, direction-fixed) + websearch prompts
├── handlers/
│   ├── translate.py     # single-shot, no tools
│   └── websearch.py     # agentic web-search tool loop (Tavily MCP)
├── tests/test_bot.py
├── initial-design.md    # original design spec
├── .env.example
└── pyproject.toml
```

## 1. Create the Discord bot

1. [Discord Developer Portal](https://discord.com/developers/applications) →
   **New Application**.
2. **Bot** tab → copy the **token** into `.env` as `DISCORD_TOKEN`.
3. Under **Privileged Gateway Intents**, enable **MESSAGE CONTENT INTENT**.
   Without it the bot receives empty message content and nothing works.
4. **OAuth2 → URL Generator**: select **both** scopes `bot` **and**
   `applications.commands` (the second is required for slash commands to
   appear). Permissions: `View Channels`, `Send Messages`. Open the generated
   URL to invite the bot.

   **If you invited the bot before slash commands existed, re-invite it** with
   the `applications.commands` scope, or `/setup` will not register.

## 2. Get API keys

- **Gemini (recommended primary)** — [Google AI Studio](https://aistudio.google.com/apikey).
  Put it in `.env` as `GEMINI_API_KEY`. Free tier is usable but rate-limited
  (roughly 15 requests/minute for `flash-lite`); bursts over the limit fall back
  to OpenRouter automatically.
- **OpenRouter (required fallback)** — [openrouter.ai/keys](https://openrouter.ai/keys),
  key looks like `sk-or-...`, goes in `.env` as `OPENROUTER_API_KEY`. Free
  models are shared across all channels and are rate-limited.
- **Tavily (optional, for web searching)** — [app.tavily.com](https://app.tavily.com),
  goes in `.env` as `TAVILY_API_KEY`.

## 3. Install and run

```bash
uv sync                   # creates .venv and installs dependencies

cp .env.example .env      # then edit .env with your real tokens/keys
uv run python bot.py
```

See `.env.example` for every supported variable, including optional model-chain
overrides (`MODEL_CHAIN`, `DEFAULT_MODEL`, `WEBSEARCH_MODEL_CHAIN`) and optional
OpenRouter attribution headers.

## 4. Configure channels with `/setup`

In the target channel, run:

- **`/setup mode:<translate|websearch> trigger:<auto|mention>`** — enables the
  bot in the current channel.
  - `trigger: auto` → responds to every (meaningful) message.
  - `trigger: mention` → responds only when `@mentioned`.
  - Translate mode always outputs both a polite and a casual version, so there
    is no tone to choose.
- **`/setup-off`** — disables the bot in the current channel.

Both commands require the **Manage Channels** permission (enforced server-side)
and reply with an **ephemeral** confirmation. Config is written to
`channel_config.json` (keyed by guild → channel) with an atomic write and loaded
into memory at startup; the file is gitignored runtime data. On startup the bot
syncs commands **per guild** so `/setup` is usable immediately after inviting.

Typical usage:

- **translate** channel: `mode:translate trigger:auto` — every message is
  translated (Korean → English / English → Korean), both tones shown.
- **web searching** channel: `mode:websearch trigger:mention` — `@bot 오늘 서울
  날씨?` returns a search-backed Korean answer with sources; a plain message is
  ignored.

## 5. Verify

```bash
# compile / import check (no tokens needed)
uv run python -m compileall bot.py config.py llm handlers

# tests
uv run --with pytest python -m pytest -q
```

Then in Discord: set up a translate channel and a websearch channel as above,
confirm translations show both tones, a mentioned websearch query returns a
cited Korean answer, `/setup-off` stops responses, and a member without Manage
Channels gets a permission error.

## Notes

- **No cross-message history.** Translate is single-shot; the web-search tool
  loop lives entirely within one message (bounded to a few model↔tool
  round-trips).
- **Anti-hallucination.** Web searching answers only from search results and
  refuses to answer from model memory when search is unavailable. Translate mode
  never uses tools.
- **Config isolation.** All config access is in `config.py` (JSON store keyed by
  `(guild_id, channel_id)`), so the storage backend can change without touching
  the rest of the bot.
