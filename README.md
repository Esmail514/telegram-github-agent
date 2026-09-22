# 🤖 Telegram AI Coding Agent

A Telegram bot that controls your GitHub repositories and runs an AI coding agent to solve issues automatically — then opens a Pull Request.

```
You pick an Issue in Telegram → Bot runs the AI Agent → Agent writes the code → PR opened 🎉
```

---

## ⚡ Quick Start

### 1. Install dependencies

```bash
git clone <repo-url>
cd telegram-ai-agent
py -m pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
copy .env.example .env
```

Then open `.env` and fill in:

```env
# ── Telegram ───────────────────────────────────────────
TELEGRAM_BOT_TOKEN=        # from @BotFather
TELEGRAM_ALLOWED_USER_ID=  # your ID from @userinfobot

# ── GitHub (pick one) ──────────────────────────────────
GITHUB_TOKEN=              # Personal Access Token (simplest)

# ── AI Agent ───────────────────────────────────────────
DEFAULT_AGENT=antigravity  # or: opencode / codex / claude
ANTHROPIC_API_KEY=         # Claude API key (optional)
OPENAI_API_KEY=            # OpenAI API key (optional)
GOOGLE_GENERATIVEAI_API_KEY= # Gemini API key (optional)
```

### 3. Validate your config

```bash
py -m app.main --check-config
```

You should see:
```
✅ Configuration is valid
```

### 4. Run the bot

```bash
py -m app.main
```

---

## 📱 Usage

Once the bot is running, message it on Telegram:

| Command | What it does |
|---|---|
| `/start` | Show main menu |
| `/run` | 🚀 Run AI Agent on an Issue |
| `/schedule` | 📅 Schedule an Issue to run later |
| `/scheduled` | 📋 View & cancel scheduled jobs |
| `/issues` | Browse GitHub Issues |
| `/status` | Check current job status |
| `/stop` | Stop the running Agent |
| `/help` | Show all commands |

**Quick example:**
1. Send `/run`
2. Pick a repo and an Issue
3. Watch live progress in Telegram:
```
🚀 Agent started...
🔍 Inspecting repository...
🛠 Implementing changes...
📦 Committing...
🎉 Done! PR #42 opened
```

---

## 🔑 GitHub Setup (one step)

**Easiest option:** Personal Access Token

1. GitHub → Settings → Developer settings → **Fine-grained tokens**
2. Create a token with: `Contents`, `Issues`, `Pull requests` (Read & Write)
3. Add to `.env`:
```env
GITHUB_TOKEN=ghp_xxxxxxxxxxxx
```

---

## 🤖 Telegram Setup (2 minutes)

1. Chat with [@BotFather](https://t.me/BotFather) → send `/newbot` → save the token
2. Chat with [@userinfobot](https://t.me/userinfobot) → save your numeric ID
3. Add both to `.env`

---

## ⚙️ Key Settings (in `.env`)

| Setting | Default | Description |
|---|---|---|
| `MAX_AGENT_RUNTIME_MINUTES` | `60` | Max time the Agent can run |
| `GIT_NETWORK_TIMEOUT_SECONDS` | `600` | Timeout for clone/push (good for slow internet) |
| `GIT_OPERATION_TIMEOUT_SECONDS` | `120` | Timeout for local git commands |
| `AUTO_MERGE_PR` | `false` | Auto-merge PR when Agent finishes |
| `LOG_LEVEL` | `INFO` | Log verbosity |

---

## 🛠 Troubleshooting

| Problem | Fix |
|---|---|
| Bot doesn't respond | Check `TELEGRAM_ALLOWED_USER_ID` is correct |
| GitHub auth error | Add `GITHUB_TOKEN` to `.env` |
| Agent not found | Make sure `agy` or `opencode` is installed and on PATH |
| Slow clone/push | Defaults are tuned for slow connections ✅ |

---

## 🔒 Security

- Bot responds **only** to your configured Telegram ID
- Agent **never** pushes directly to `main` or `master`
- Every commit is scanned for secrets before pushing
- Tokens are masked in all logs
