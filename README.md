# Discord Multi-Bot

A single Discord bot that runs multiple modes per channel, configured at runtime with a slash command — no restarts, no file edits.

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)

## Features

- **Per-channel mode assignment** via `/setup` — switch between `translate` and `websearch` modes and trigger types without editing config files or restarting the process.
- **Bidirectional translation** — auto-detects Korean or English input and returns both a polite (존댓말) and casual (반말) version on labeled lines in a single reply.
- **Grounded web search** — answers questions strictly from Tavily search results (queried in both Korean and English for cross-validation), with cited URLs; refuses to answer from model memory when search is unavailable.
- **Transparent LLM fallback** — Gemini `flash-lite` is the primary model; any rate-limit or error falls through automatically to an OpenRouter free-model chain, logged per request.
- **Config isolation** — channel settings are stored in a JSON file keyed by `(guild_id, channel_id)` with atomic writes and loaded into memory at startup; the storage backend can be swapped without touching bot logic.

## Demo

No screenshots yet. Once a channel is configured, it behaves like this.

**Translate channel** — `/setup mode:translate trigger:auto`

> **You:** Could you review this by tomorrow?
>
> **Bot:**
> `공손:` 내일까지 검토해 주실 수 있을까요?
> `캐주얼:` 내일까지 검토해 줄 수 있어?

Direction is auto-detected from the input, and both tones are always returned on their own labeled line.

**Web search channel** — `/setup mode:websearch trigger:mention`

> **You:** @bot 오늘 서울 날씨?
>
> **Bot:** a Korean answer assembled only from Tavily search results, with the source URLs cited. If search returns nothing or is unavailable, it says so instead of answering from model memory.

## Built With

- **Python** — sole implementation language; all async I/O runs on the discord.py event loop.
- **discord.py** — chosen for its first-class `app_commands` slash-command support and per-guild sync, so `/setup` appears in Discord immediately after inviting the bot without a global propagation delay.
- **Google Gemini (`gemini-flash-lite-latest`)** — primary LLM via Google's OpenAI-compatible endpoint; the free tier handles the single-turn translation and bounded websearch workload at acceptable latency.
- **OpenRouter** — fallback LLM gateway that provides access to multiple free models; used transparently when Gemini is absent or rate-limited, so no single provider outage silences the bot.
- **Tavily MCP** — remote MCP server for web search; returns structured, cited results that the websearch handler surfaces directly, making the anti-hallucination constraint enforceable.
- **uv** — fast Python package manager that creates the virtual environment and installs dependencies reproducibly from `uv.lock` in a single command.

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) installed
- A Discord bot token with **MESSAGE CONTENT INTENT** enabled and both `bot` + `applications.commands` OAuth2 scopes granted
- At least one of `GEMINI_API_KEY` or `OPENROUTER_API_KEY`

### Installation

```bash
# Clone the repo
git clone https://github.com/Git-Mere/hydra.git
cd hydra

# Install dependencies into a managed virtual environment
uv sync

# Copy the example env file and fill in your tokens
cp .env.example .env
```

### Running

```bash
uv run python bot.py
```

### Verify without a Discord token

```bash
# Compile / import check
uv run python -m compileall bot.py config.py llm handlers

# Unit tests
uv run --with pytest python -m pytest -q
```

### Configuring channels

In any channel the bot can see, run one of:

```
/setup mode:translate trigger:auto
/setup mode:websearch trigger:mention
/setup-off
```

`trigger:auto` responds to every message; `trigger:mention` responds only when `@mentioned`. Both commands require the **Manage Channels** permission and reply with an ephemeral confirmation.

## What I Learned

**Centralising LLM routing so call-site code stays provider-agnostic.**
The bot needs to keep working when Gemini hits its free-tier rate limit mid-use. Rather than scattering `try/except` blocks across handlers, I routed every completion through a single `llm/client.py` wrapper. The wrapper detects 429 responses and falls through to an OpenRouter free-model chain, logging which model actually served each request. The concrete payoff: `handlers/translate.py` and `handlers/websearch.py` are completely unaware of which provider ran — they just receive a completion string.

**Bounding an agentic tool loop inside a single Discord interaction.**
The websearch handler runs a model↔tool loop (query Tavily → read results → optionally re-query in the other language) that must resolve before Discord's response window closes. I constrained the loop to a fixed maximum number of round-trips rather than letting the model decide when to stop. This prevented unbounded API spend and kept response latency predictable, while still allowing the cross-language validation that makes the search more reliable.

**Enforcing an anti-hallucination contract at the prompt boundary.**
Free-tier LLMs will confidently answer from training data when a tool returns nothing. The websearch system prompt explicitly tells the model to report that search found nothing rather than falling back to model memory — and the prompt is the only layer where this constraint can live reliably, since the handler has no post-hoc way to verify facts. Getting this right required understanding exactly what the model sees: if Tavily returns an empty result set, the prompt surfaces that emptiness explicitly rather than silently omitting it.

## License

No license specified.
