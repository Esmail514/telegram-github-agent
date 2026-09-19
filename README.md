# 🤖 Telegram AI Coding Agent

A Telegram bot that acts as a remote controller for your GitHub repositories and an AI coding agent. Send a command from Telegram, pick a repository and issue, and the bot will clone the repository, run an AI agent to implement the issue, validate the changes, commit them to a branch, and open a Pull Request — all while streaming progress back to you.

---

## Architecture

```
Telegram Bot (python-telegram-bot v21)
        │
        ▼
Job Executor (asyncio)
        │
        ├── GitHub Client (PyGithub — GitHub App or PAT)
        ├── Workspace Manager (local clone directory)
        ├── Git Service (async git CLI wrapper)
        ├── Agent Manager ──► OpenCodeAgent (opencode CLI)
        │                    (Claude / Codex / Gemini — stubs)
        └── Database (aiosqlite — job state persistence)
```

**Security model:**
- Only your Telegram user ID is allowed to control the bot
- The agent never pushes to `main`/`master`
- Secrets are scanned before every git commit
- The bot token is never exposed in logs or messages

---

## Requirements

- Python 3.11+
- Git (installed and on PATH)
- [OpenCode](https://opencode.ai) (or another supported agent)
- A GitHub account
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))

---

## Setup

### 1. Clone and install

```bash
git clone <this-repo>
cd telegram-ai-agent
py -m pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` with your credentials (see sections below).

---

## Telegram Bot Setup

### Create the bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Follow the prompts to choose a name and username
4. Copy the **Bot Token** — add it to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=<your token here>
   ```

### Get your Telegram user ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It will reply with your numeric user ID
3. Add it to `.env`:
   ```
   TELEGRAM_ALLOWED_USER_ID=<your numeric ID>
   ```

---

## GitHub App Setup (Recommended)

A GitHub App is preferred over a Personal Access Token because it uses short-lived tokens and has precisely scoped permissions.

### Create the GitHub App

1. Go to **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**
2. Fill in:
   - **App name**: `ai-coding-agent` (or any name)
   - **Homepage URL**: `https://github.com` (placeholder is fine)
   - **Webhook**: Uncheck "Active" (webhooks not needed)
3. Set **Permissions** (Repository):
   | Permission | Level |
   |---|---|
   | Metadata | Read |
   | Contents | Read & Write |
   | Issues | Read & Write |
   | Pull requests | Read & Write |
4. Under **Where can this GitHub App be installed?** — select "Only on this account"
5. Click **Create GitHub App**
6. Note the **App ID** shown on the app page
7. Scroll to **Private keys** → **Generate a private key**
8. Save the downloaded `.pem` file (e.g. `github-app.pem`) in the project directory

### Install the App

1. On your GitHub App page → **Install App** → Install on your account
2. Choose "All repositories" or select specific ones
3. After install, look at the URL: `https://github.com/settings/installations/XXXXXXXX`
   — the number at the end is your **Installation ID**

### Configure `.env`

```env
GITHUB_APP_ID=<your App ID>
GITHUB_INSTALLATION_ID=<your Installation ID>
GITHUB_PRIVATE_KEY_PATH=./github-app.pem
```

---

## GitHub PAT Fallback (Simpler)

If you prefer a Personal Access Token:

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Create a token with:
   - Repository access: All repositories (or selected)
   - Permissions: Contents (R/W), Issues (R/W), Pull requests (R/W), Metadata (Read)
3. Add to `.env`:
   ```env
   GITHUB_TOKEN=<your token>
   ```

---

## OpenCode Setup

### Install OpenCode

```bash
npm install -g opencode-ai
# or follow https://opencode.ai/docs/installation
```

### Configure an AI provider

OpenCode needs an AI provider API key. Add the appropriate key to `.env`:

```env
# For Anthropic Claude:
ANTHROPIC_API_KEY=<your key>

# For OpenAI:
OPENAI_API_KEY=<your key>
```

The bot will forward these to the OpenCode subprocess automatically.

### Set the command

```env
OPENCODE_COMMAND=opencode
```

---

## Running the Bot

### Validate configuration first

```bash
py -m app.main --check-config
```

Expected output:
```
✅ Configuration is valid
  TELEGRAM_ALLOWED_USER_ID = 123456789
  DEFAULT_AGENT            = opencode
  ...
```

### Start the bot

```bash
py -m app.main
```

---

## Using the Bot

### Commands

| Command | Description |
|---|---|
| `/start` | Show main menu |
| `/repos` | Browse your GitHub repositories |
| `/issues` | Browse issues in a repository |
| `/newissue` | Create a new GitHub issue |
| `/run` | Start the AI agent on an issue |
| `/status` | Check current agent job status |
| `/stop` | Stop the running agent |
| `/help` | Show help |

### Typical workflow

1. Send `/run`
2. Select a repository from the inline keyboard
3. Select an open issue
4. Confirm with **🚀 Start Agent**
5. Watch progress updates arrive in Telegram:
   ```
   🚀 Agent started
   🔍 Inspecting repository...
   🛠 Implementing changes...
   🧪 Running tests...
   ✅ All tests passed
   📦 Creating commit...
   ⬆️ Pushing branch...
   🔀 Creating Pull Request...
   🎉 Completed! PR #57
   ```

---

## Configuration Reference

All settings are set via `.env`:

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_ID` | ✅ | — | Your Telegram numeric user ID |
| `GITHUB_APP_ID` | ✅* | — | GitHub App ID |
| `GITHUB_INSTALLATION_ID` | ✅* | — | GitHub App Installation ID |
| `GITHUB_PRIVATE_KEY_PATH` | ✅* | — | Path to .pem file |
| `GITHUB_TOKEN` | ✅* | — | PAT fallback (if not using App) |
| `WORKSPACE_DIR` | — | `./workspaces` | Local clone directory |
| `DEFAULT_AGENT` | — | `opencode` | AI agent to use |
| `OPENCODE_COMMAND` | — | `opencode` | opencode executable |
| `ANTHROPIC_API_KEY` | — | — | Forwarded to agent |
| `OPENAI_API_KEY` | — | — | Forwarded to agent |
| `MAX_AGENT_RUNTIME_MINUTES` | — | `60` | Hard timeout for agent |
| `MAX_FIX_ITERATIONS` | — | `5` | Max fix attempts |
| `LOG_LEVEL` | — | `INFO` | DEBUG/INFO/WARNING/ERROR |

*Either GitHub App credentials or `GITHUB_TOKEN` must be set.

---

## Project Structure

```
telegram-ai-agent/
├── app/
│   ├── main.py                  # Entry point
│   ├── config/settings.py       # Pydantic-settings config
│   ├── telegram/
│   │   ├── bot.py               # Application assembler
│   │   ├── auth.py              # Allow-list middleware
│   │   ├── keyboards/           # Inline keyboard builders
│   │   └── handlers/            # Command & callback handlers
│   ├── github/                  # GitHub API wrappers
│   ├── agents/                  # Agent abstraction + OpenCode
│   ├── runner/                  # Executor, git, workspace
│   ├── database/                # aiosqlite job persistence
│   └── utils/                   # Logging, secrets, security
├── tests/
├── workspaces/                  # Cloned repos (gitignored)
├── .env.example
└── requirements.txt
```

---

## Troubleshooting

**`opencode: command not found`**
→ Install opencode: `npm install -g opencode-ai`
→ Or set `OPENCODE_COMMAND=/full/path/to/opencode` in `.env`

**`GitHub authentication not configured`**
→ Set either GitHub App vars or `GITHUB_TOKEN` in `.env`

**Bot doesn't respond**
→ Check `TELEGRAM_ALLOWED_USER_ID` matches your actual user ID (get it from @userinfobot)

**`git clone failed`**
→ For GitHub App auth, the clone URL uses HTTPS. Make sure the App has Contents (Read) permission.
→ If using PAT: set `GITHUB_TOKEN` and the clone will use HTTPS with token auth.

**Agent times out**
→ Increase `MAX_AGENT_RUNTIME_MINUTES` in `.env`

---

## Security Considerations

- **Telegram is not a shell**: The bot only executes predefined operations (repos, issues, run, stop). Arbitrary shell commands via Telegram are not possible.
- **Allow-list enforcement**: All requests from non-configured user IDs are silently dropped.
- **Secret scanning**: Before every git commit, the diff is scanned for common secret patterns. The commit is blocked if secrets are detected.
- **Protected branches**: The git service refuses to push to `main` or `master`.
- **Token masking**: The bot token is stored as `pydantic.SecretStr` and never appears in logs.
- **`.env` gitignored**: The `.gitignore` excludes `.env`, `*.pem`, `workspaces/`, and database files.

---

## Adding Another Agent

1. Create `app/agents/myagent.py` implementing `BaseAgent`
2. Register it in `app/agents/manager.py`: `"myagent": MyAgent`
3. Set `DEFAULT_AGENT=myagent` in `.env`

No changes to Telegram or GitHub code needed.
