# 🎓 IT Insights Lab — Telegram Bot

A multi-user Telegram bot for a data analytics learning cohort: onboarding, task broadcasts, homework submissions routed to reviewers, and a full admin panel.

Built with **python-telegram-bot v21+** (async, ConversationHandler-based FSM) and **SQLite**.

---

## ✨ Features

| Area | What it does |
|---|---|
| **Onboarding** | `/start` walks through Name → City → Occupation → Photo → Bio, posts an intro card (photo + bio) to the cohort group, and DMs the user the group invite link |
| **Tasks** | Admin composes a task (title, description, optional attachment & deadline); every active student gets "📣 New Task Available!" with a **Get Task** button showing the full card |
| **Submissions** | `/submit` (or the button on a task) → pick task → file / link / text → saved to DB and pushed to the reviewers group with student name, @handle, task id/title and **Pending Review** |
| **Admin panel** | `/admin` — analytics (users, active, banned, submissions per task), user management (browse, ban/unban, remove), CSV export of roster & submission log |
| **Robustness** | HTML-escaped user input, global error handler, rate-limited broadcast, banned-user gating everywhere |

---

## 🗂 Project structure

```
bot/
├── config.py            # env loading + validation (fails fast)
├── db.py                # SQLite schema + data-access functions
├── keyboards.py         # all inline keyboards
├── utils.py             # escaping, task/intro text, CSV builders
├── main.py              # entry point (python -m bot.main)
└── handlers/
    ├── registration.py  # /start onboarding FSM
    ├── tasks.py         # /newtask FSM + [Get Task] viewer + /tasks
    ├── submit.py        # /submit FSM + review forwarding
    ├── admin.py         # /admin panel
    └── common.py        # /help, /myprofile, fallback, error handler
tests/test_db.py          # DB layer tests (pytest)
```

---

## 🚀 Quick start (local)

### 1. Prerequisites
- Python **3.10+** (`python --version`)
- A Telegram bot token from [@BotFather](https://t.me/BotFather) (`/newbot`)

### 2. Install dependencies
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Windows (PowerShell): Copy-Item .env.example .env
```
Edit `.env`:

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | token from @BotFather |
| `GROUP_CHAT_ID` | numeric id of the cohort group (negative, e.g. `-1001234567890`) |
| `REVIEW_CHAT_ID` | numeric id of the reviewers/admin group |
| `ADMIN_IDS` | comma-separated Telegram user ids, e.g. `123456789` |
| `COHORT_GROUP_INVITE_LINK` | public invite link (`https://t.me/+...`) sent to new members |
| `MANDATORY_CHANNEL` | mandatory channel username or link (`@itinsights_lab`) |
| `DATABASE_PATH` | SQLite file path (default `insights_lab.db`) |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

**Finding chat IDs:** add the bot to the group, send any message, then open
`https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` — the JSON contains `"chat":{"id":-100...}`.
For your own id, message [@userinfobot](https://t.me/userinfobot).

**Group settings:** the bot must be a **member of both groups** (admin recommended) so it can post intro cards and review messages.

### 4. Initialize the database
Nothing to do — the schema is created automatically on first start. To verify manually:
```bash
python -c "from bot import db; db.init_db('insights_lab.db'); print('db ready')"
```

### 5. Run the bot
```bash
python -m bot.main
```
You'll see `Bot authorised as @yourbot` and an online notification in your admin DM. Test `/start` in a private chat with the bot.

### 6. Run the tests
```bash
pip install -r requirements-dev.txt
pytest tests -q
```

---

## ☁️ Free 24/7 hosting — Oracle Cloud Always Free (recommended)

The only major free tier that **never sleeps** — a real VM, free forever, 24/7 uptime.

### 1. Create the VM
1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) (card needed for verification only — no charge on Always Free).
2. Console → **Compute → Instances → Create Instance**.
3. Shape: **Ampere A1** (ARM), **1 OCPU + 1 GB RAM** (Always Free), OS: **Ubuntu 22.04**.
4. Add your SSH key, create the instance, note its **public IP**.

### 2. Open the network path
Console → *Instance → Subnet → Security List* → add inbound rule: source `0.0.0.0/0`, TCP port `443` (optional; plain polling needs no inbound ports at all).

### 3. Install & run
```bash
ssh ubuntu@<VM_PUBLIC_IP>

sudo apt update && sudo apt install -y python3-venv git
git clone <YOUR_REPO_URL> iil-bot && cd iil-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
nano .env        # paste real values from your local .env
python -m bot.main   # test run, Ctrl+C to stop
```

### 4. Auto-start with systemd (survives crashes & reboots)
```bash
sudo tee /etc/systemd/system/iilbot.service > /dev/null <<'EOF'
[Unit]
Description=IT Insights Lab Telegram bot
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/iil-bot
ExecStart=/home/ubuntu/iil-bot/.venv/bin/python -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now iilbot
journalctl -u iilbot -f        # live logs; Ctrl+C to exit
```

The SQLite file lives on the VM's persistent disk, so data survives restarts. Back it up with:
```bash
scp ubuntu@<VM_PUBLIC_IP>:/home/ubuntu/iil-bot/insights_lab.db ./backup.db
```

**Deploying updates:** `git pull` on the VM, then `sudo systemctl restart iilbot`.

---

## ☁️ Alternative — Render (free web service, with caveats)

Works, but the free tier **sleeps after 15 min idle** (30–60 s wake delay on the next message) and the filesystem is **ephemeral** (DB resets on redeploy) — fine for demos, not for production data.

1. Push this repo to GitHub.
2. Render → **New → Web Service** → connect the repo.
3. Environment: **Python 3**, Build: `pip install -r requirements.txt`.
4. Start command:
   ```
   python -m bot.main
   ```
5. Add all `.env` values in **Environment**.

For 24/7 on Render you'd add a cron pinger; the Oracle VM above is simpler and truly free.

---

## 📖 Command reference

| Command | Who | Effect |
|---|---|---|
| `/start` | everyone | registration FSM (or welcome-back + invite link) |
| `/tasks` | everyone | list published tasks |
| `/submit [id]` | registered | submission FSM → reviewers group |
| `/myprofile` | everyone | show your saved profile |
| `/help` | everyone | command list |
| `/newtask` | admins | compose & broadcast a task |
| `/admin` | admins | panel: stats, users, export |

---

## 🧰 Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing required environment variable BOT_TOKEN` | `.env` missing/unfilled — copy from `.env.example` |
| Bot silent in groups | bot must be a member; make it admin in the cohort group |
| `Forbidden: bot was blocked by the user` in logs | a student blocked the bot — normal during broadcasts, counted as failed delivery |
| Intro card fails, students still registered | bot lacks post rights in `GROUP_CHAT_ID`, or chat id wrong; admins get a DM with the error |
| DB locked errors | only one bot instance may run at a time; stop duplicates (`systemctl stop iilbot` elsewhere) |
| Wrong chat ids | use `getUpdates` (see step 3) — group ids are negative `-100...` |
