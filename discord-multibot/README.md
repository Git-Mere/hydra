# Discord Multi-Bot

One Discord bot that behaves differently **per channel** — different mode and
trigger — all routed through a single [OpenRouter](https://openrouter.ai) key.

- **translate** mode: translates messages (Korean ↔ English) into a single most
  natural sentence, in the channel's configured **tone** (casual/반말 or
  polite/존댓말), applied in both directions.
- **web searching** mode: answers questions in Korean **strictly from web
  search** via [Tavily](https://tavily.com)'s remote MCP server for
  current/verifiable facts (weather, prices, traffic rules, news, ...). It never
  answers from model memory: if search is unavailable, it says so.

Channels are configured **at runtime** with the `/setup` slash command — no
file editing, no restart. Config is stored in a local JSON file
(`channel_config.json`). Every channel uses one shared model (see
`DEFAULT_MODEL` below).

## Directory layout

```
discord-multibot/
├── bot.py              # entrypoint: client + slash commands + on_message flow
├── config.py           # JSON config store + get/set/disable_channel_config()
├── channel_config.json # runtime config (gitignored, created by /setup)
├── llm/
│   ├── client.py       # OpenRouter wrapper (429 backoff, timeout, tool loop)
│   ├── tavily_search.py# Tavily MCP client (web searching mode)
│   └── prompts.py      # TRANSLATE_SYSTEM / WEBSEARCH_SYSTEM
├── handlers/
│   ├── translate.py    # single-shot, no tools
│   └── websearch.py    # agentic web-search tool loop (Tavily MCP)
├── .env.example
└── pyproject.toml
```

## 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** tab → **Add Bot**. Copy the **token** (goes in `.env` as `DISCORD_TOKEN`).
3. Under **Privileged Gateway Intents**, enable **MESSAGE CONTENT INTENT**.
   Without this the bot receives empty message content and nothing works.
4. **OAuth2 → URL Generator**: select **both** scopes `bot` **and**
   `applications.commands` (the second is required for slash commands like
   `/setup` to appear). Permissions: `Read Messages/View Channels`,
   `Send Messages`. Open the generated URL to invite the bot.

   **If you invited the bot before this version, re-invite it** with the
   `applications.commands` scope added — otherwise the slash commands will not
   be registered for your server.

## 2. Get an OpenRouter key

Sign up at [openrouter.ai](https://openrouter.ai), create an API key
(`sk-or-...`), and put it in `.env` as `OPENROUTER_API_KEY`.

Free models are rate-limited (roughly 20 req/min and 50 req/day under $10 of
credit; 1,000 req/day once you've topped up $10). All channels share the one
key, so their limits add up.

## 3. Install and run

```bash
cd discord-multibot
uv sync                   # creates .venv and installs dependencies

cp .env.example .env      # then edit .env with your real tokens
uv run python bot.py
```

`.env`:

```
DISCORD_TOKEN=your_discord_bot_token
OPENROUTER_API_KEY=sk-or-xxxxxxxx
DEFAULT_MODEL=meta-llama/llama-3.3-70b-instruct:free   # optional, see below
TAVILY_API_KEY=                                        # optional, see "Web search"
```

Optional OpenRouter attribution headers: set `OPENROUTER_HTTP_REFERER` and
`OPENROUTER_X_TITLE` in `.env` to identify your app on OpenRouter.

### Web searching mode (`TAVILY_API_KEY`)

Web searching mode searches the web for current or verifiable facts (weather,
prices, traffic rules, news, ...) using [Tavily](https://tavily.com)'s remote
**MCP** server. Get a key at [tavily.com](https://tavily.com) and set it as
`TAVILY_API_KEY` in `.env`.

How it works: for one user turn the bot runs a bounded agentic loop (up to 4
model↔tool round-trips). The model calls the `web_search` tool, the bot executes
it against Tavily's MCP endpoint, feeds the results back, and the model answers
in Korean citing source URLs. It answers **only** from search results; if a
search finds nothing (or errors), it tells the user rather than inventing facts.
There is no cross-message history: the loop lives entirely within a single
message.

**When web search is unavailable:** `TAVILY_API_KEY` is optional, but if it is
unset (or the MCP connection fails at runtime), web searching mode does **not**
answer from model memory -- it replies that web search is currently unavailable.
This is deliberate: answering trivia from parametric memory is exactly the
hallucination this mode avoids. Translate mode never uses tools.

### `DEFAULT_MODEL`

There is no per-channel model. Every channel uses one model, read from the
`DEFAULT_MODEL` environment variable. If unset, it falls back to the
`DEFAULT_MODEL` constant in `config.py`.

## 4. Configure channels with `/setup`

In the channel you want the bot to act in, run:

- **`/setup mode:<translate|websearch> trigger:<auto|mention> [tone:<casual|polite>]`** —
  enables the bot in the current channel. Discord shows pickers for `mode`,
  `trigger`, and the optional `tone`.
  - `trigger: auto` → the bot responds to every (meaningful) message.
  - `trigger: mention` → the bot responds only when `@mentioned`.
  - `tone` (optional, translate mode only) → `casual` (반말) or `polite` (존댓말);
    applied in both directions. Defaults to `casual`. Ignored for websearch mode.
- **`/setup-off`** — disables the bot in the current channel.

Both commands reply with an **ephemeral** confirmation (only you see it) and
require the **Manage Channels** permission. Members without it get an ephemeral
"you need Manage Channels permission" reply. This is enforced server-side, not
just hidden in the UI.

Config is written to `channel_config.json` (keyed by guild → channel) with an
atomic write on every change, and loaded into memory at startup. The file is
gitignored — it is runtime data and must not be committed.

Command sync: on startup the bot syncs commands **per guild** for instant
propagation, so `/setup` is usable immediately after (re-)inviting the bot.

## 5. Verify

Compile / import checks (no tokens needed):

```bash
uv run python -m py_compile bot.py config.py llm/*.py handlers/*.py
uv run python -c "import bot, config; from llm import client, prompts, tavily_search; from handlers import translate, websearch"
```

Run the tests:

```bash
uv run python tests/test_bot.py
```

Then in Discord:

- `/setup mode:translate trigger:auto` in a channel → type Korean, get English
  back (and vice versa).
- `/setup mode:websearch trigger:mention` in another channel → `@bot 오늘 서울 날씨?`
  gets a search-backed answer; a plain message (no mention) is ignored.
- `/setup-off` → the bot stops responding in that channel.
- A member without **Manage Channels** running `/setup` gets a permission error.
- The bot never replies to its own messages.
- If OpenRouter is rate-limited or errors, the channel gets a short "try again
  later" message instead of a crash.

## Notes

- No cross-message conversation history. Web searching's tool loop is
  per-message; translate is single-shot.
- All config access is isolated in `config.py` (JSON store keyed by
  `(guild_id, channel_id)`), so the storage backend can change without touching
  the rest of the bot.
