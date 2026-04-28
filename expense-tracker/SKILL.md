---
name: expense-tracker
description: Track personal expenses from Gmail receipts using local AI. Use this skill whenever the user mentions expenses, receipts, transaction tracking, spending analysis, or wants to parse bank/payment emails from Gmail. Also trigger when they mention syncing expenses to Google Sheets, building a spending dashboard, or ask about their spending history. Covers setup (OAuth, Ollama), running expense extraction, viewing dashboards, and customising email sources.
metadata:
  requires:
    os: ["darwin", "linux", "windows"]
    bins: ["python3", "ollama"]
---

# Expense Tracker Skill

Tracks personal expenses by fetching Gmail transaction/receipt emails, parsing them with a local Ollama model (llama3.1:8b), storing results in SQLite, and syncing to Google Sheets.

## File Layout

```
expense-tracker/
├── SKILL.md
├── agent.py          # Main fetch → parse → store → sync pipeline
├── dashboard.py      # Generates an HTML spending dashboard
└── requirements.txt  # Python dependencies
```

## Setup (first time only)

When the user asks to set up or install the expense tracker:

1. **Virtual environment** — Check if `.venv` exists. If not:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```

2. **Ollama model** — Check if `ollama` is running and `llama3.1:8b` is available:
   ```bash
   ollama list
   ```
   If not present: `ollama pull llama3.1:8b`

3. **Google credentials** — Check if `credentials.json` exists. If not, tell the user:
   > You need to download `credentials.json` from Google Cloud Console:
   > 1. Go to console.cloud.google.com
   > 2. Create a project → Enable **Gmail API**, **Google Sheets API**, **Google Drive API**
   > 3. APIs & Services → Credentials → Create OAuth 2.0 Client ID → Desktop app
   > 4. Download JSON → rename to `credentials.json` → place in this folder
   > 5. OAuth consent screen → Test users → add your Gmail address

4. **OAuth flow** — Once `credentials.json` is present:
   ```bash
   .venv/bin/python3 agent.py --setup
   ```
   This opens a browser. Tell the user to sign in and approve access.

## Running

When the user says "run expenses", "fetch my expenses", "track expenses", or similar:

```bash
.venv/bin/python3 agent.py --run
```

For a specific month (e.g. "run expenses for March 2025"):

```bash
.venv/bin/python3 agent.py --run --month 2025-03
```

## Dashboard

When the user asks to see their dashboard or spending report:

```bash
.venv/bin/python3 dashboard.py && open dashboard.html
```

For a specific month:

```bash
.venv/bin/python3 dashboard.py --month 2025-03 --out report.html && open report.html
```

## Customising email sources

When the user says their bank or a merchant is not being captured, instruct them to open `agent.py` and add the sender or subject to `SEARCH_QUERIES`. Examples:

- `"from:alerts@kotakbank.com"`
- `"subject:debited from your account"`
- `"from:support@phonepe.com"`

## Google Sheets sync

After every run, new expenses are automatically synced to a Google Sheet named **"Expenses"** in the user's Drive. The sheet URL is printed at the end of each run.

For a Looker Studio dashboard on top of it:
1. Go to lookerstudio.google.com
2. Create → Report → Add data → Google Sheets → pick "Expenses"
3. Build charts: doughnut by category, time series by date, full transaction table

## Status check

When the user asks "how many expenses do I have" or "what's saved":

```bash
.venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('expenses.db')
rows = conn.execute('SELECT count(*), sum(amount) FROM expenses').fetchone()
print(f'Total: {rows[0]} expenses, ₹{rows[1]:,.0f} spent')
conn.close()
"
```
