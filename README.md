# 🤖 Telegram AI Coding Agent

A Telegram controller for saved local projects, scheduled prompts, and GitHub
Issues, using Codex CLI or Antigravity CLI. Local prompts stay local; Issue jobs
can follow the controller's verification, approval, and Pull Request workflow.

```
Choose a project → Write a prompt or select an Issue → Choose time and agent → Confirm
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
DEFAULT_AGENT=antigravity  # or: codex
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

During development, start with automatic reload enabled:

```bash
py -m app.main --reload
```

The bot watches Python source and runtime configuration files, waits for file
writes to settle, then shuts down cleanly and restarts itself. If an AI job is
active, the reload is deferred until that job finishes so work is not interrupted.

---

## 📱 Usage

The persistent Telegram keyboard has six services: GitHub, المشاريع المحلية,
حالة المهمة, المهام المجدولة, الاستخدام الشخصي, and موارد الجهاز. Project folders
are selected through the drive/folder browser and saved only after confirmation.

You can also use these commands:

| Command | What it does |
|---|---|
| `/start` | Show main menu |
| `/run` | 🚀 Run AI Agent on an Issue |
| `/schedule` | 📅 Schedule an Issue or a local prompt |
| `/scheduled` | 📋 View, delete, start now, or change the time of pending jobs |
| `/issues` | Browse GitHub Issues |
| `/status` | Check current job status |
| `/stop` | Stop the running Agent |
| `/help` | Show all commands |

For a local prompt, open المهام المجدولة, create a task, enter the prompt, choose
the time and agent settings, and select a saved local project. Review the final
summary before confirming. Only one agent job runs at a time; pending work waits
until the previous job and its cleanup finish.

### Local prompt CSV

On confirmation, the bot creates `local_prompt_schedules.csv` beside the selected
project. Prompt bodies remain in CSV; SQLite stores job state, project locations,
and execution metadata. You can edit the CSV in a text editor or spreadsheet.
Preserve its header, use a unique task ID, keep the saved `project_id` and your
`chat_id`, and set a future `scheduled_at` with a timezone and `status=PENDING`.
The Telegram flow fills these fields for you.

A CSV that already exists must first be reviewed and explicitly approved using
**اعتماد CSV** in المهام المجدولة. Folder selection alone does not import tasks.
Approval covers that file location and later manual additions; review imported
repository files before approving them. New empty files created by the bot are
registered automatically. Invalid rows are reported without blocking valid work.

The automatic Git workflow excludes the root runtime CSV and its temporary file.
If either is already tracked or appears in outgoing history, publication stops
for local review; the bot does not delete the file or rewrite history.

Interrupted jobs are recovered at startup for explicit resume. Local resume
preserves the selected agent settings and requires the original unchanged prompt
in CSV. An old job lacking source metadata, or an edited/missing source prompt,
requires a new schedule. Reload waits for active work and completion cleanup.

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
| Agent not found | Make sure `agy`/`antigravity` CLI or `codex` CLI is installed and on PATH |
| Slow clone/push | Defaults are tuned for slow connections ✅ |

---

## 🔒 Security

- Bot responds **only** to your configured Telegram ID
- Agent **never** pushes directly to `main` or `master`
- Every commit is scanned for secrets before pushing
- Tokens are masked in all logs

## Development checks

```powershell
py -m pytest
py -m ruff check .
py -m mypy app
py -m app.main --check-config
py .agents/skills/skill-maintainer/scripts/validate_skills.py
```

Tests use temporary databases, CSVs, workspaces, and mocked external services.
See [the repair report](docs/repair-results-2026-10-26.md) for the review findings,
changed components, migration details, and verification limits.
