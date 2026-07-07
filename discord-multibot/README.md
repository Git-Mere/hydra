# Discord Multi-Bot (Phase 1 MVP)

One Discord bot that behaves differently **per channel** — different model,
system prompt, and trigger — all routed through a single
[OpenRouter](https://openrouter.ai) key.

- `#translate` channel: auto-translates every message (Korean ↔ English).
- `#chat` channel: answers only when the bot is `@mentioned`.

Add a channel by adding one entry to `channels.yaml` — no code change.

## Directory layout

```
discord-multibot/
├── bot.py            # entrypoint: client + on_message flow
├── config.py         # channels.yaml loader + get_channel_config()
├── channels.yaml     # per-channel config
├── llm/
│   ├── client.py     # OpenRouter wrapper (429 backoff, timeout)
│   └── prompts.py    # TRANSLATE_SYSTEM / CHAT_SYSTEM
├── handlers/
│   ├── translate.py
│   └── chat.py
├── .env.example
└── requirements.txt
```

## 1. Create the Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** tab → **Add Bot**. Copy the **token** (goes in `.env` as `DISCORD_TOKEN`).
3. Under **Privileged Gateway Intents**, enable **MESSAGE CONTENT INTENT**.
   Without this the bot receives empty message content and nothing works.
4. **OAuth2 → URL Generator**: scope `bot`, permissions `Read Messages/View Channels`,
   `Send Messages`. Open the generated URL to invite the bot to your server.

## 2. Get an OpenRouter key

Sign up at [openrouter.ai](https://openrouter.ai), create an API key
(`sk-or-...`), and put it in `.env` as `OPENROUTER_API_KEY`.

Free models are rate-limited (roughly 20 req/min and 50 req/day under $10 of
credit; 1,000 req/day once you've topped up $10). The translate and chat
channels share the one key, so their limits add up.

## 3. Configure channels

Enable Developer Mode in Discord (User Settings → Advanced), right-click a
channel → **Copy ID**, and paste the IDs into `channels.yaml`:

```yaml
channels:
  "YOUR_TRANSLATE_CHANNEL_ID":
    mode: translate
    model: "qwen/qwen-3-8b:free"
    trigger: auto
    enabled: true
  "YOUR_CHAT_CHANNEL_ID":
    mode: chat
    model: "deepseek/deepseek-r1:free"
    trigger: mention
    enabled: true
    system_override: null
```

Fields: `mode` (`translate`|`chat`), `model` (OpenRouter id), `trigger`
(`auto`|`mention`), `enabled` (`false` disables the channel),
`system_override` (optional prompt that replaces the default).

## 4. Install and run

```bash
cd discord-multibot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then edit .env with your real tokens
python bot.py
```

`.env`:

```
DISCORD_TOKEN=your_discord_bot_token
OPENROUTER_API_KEY=sk-or-xxxxxxxx
```

Optional OpenRouter attribution headers: set `OPENROUTER_HTTP_REFERER` and
`OPENROUTER_X_TITLE` in `.env` to identify your app on OpenRouter.

## 5. Verify

Compile / import checks (no tokens needed):

```bash
python -m py_compile bot.py config.py llm/*.py handlers/*.py
python -c "import bot, config; from llm import client, prompts; from handlers import translate, chat"
```

Then in Discord:

- Type Korean in the translate channel → get English back (and vice versa).
- `@bot how are you?` in the chat channel → get an answer. A plain message
  (no mention) is ignored.
- The bot never replies to its own messages.
- If OpenRouter is rate-limited or errors, the channel gets a short "try again
  later" message instead of a crash.

## Notes

- Phase 1 is single-shot (no conversation history), config-file based.
- Config access is isolated in `config.py` so Phase 2 can swap YAML → DB and
  key by `(guild_id, channel_id)` without touching the rest of the bot.
