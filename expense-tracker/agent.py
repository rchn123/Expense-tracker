#!/usr/bin/env python3
"""
Expense Tracker Agent
Fetches transaction/receipt emails from Gmail, parses them with a local Ollama
model, stores in SQLite, and syncs to Google Sheets.
"""

import argparse
import base64
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
DB_FILE = "expenses.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"
SHEET_NAME = "Expenses"

# Add or remove queries to match your banks / payment apps
SEARCH_QUERIES = [

    "from:alerts@axis.bank.com",
]

CATEGORIES = [
    "Food & Dining",
    "Groceries",
    "Shopping",
    "Transport",
    "Utilities",
    "Entertainment",
    "Health",
    "Education",
    "Rent & Housing",
    "Subscriptions",
    "Travel",
    "Transfers",
    "Other",
]

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def get_credentials():
    """Load or refresh Google OAuth credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(
                    f"ERROR: {CREDENTIALS_FILE} not found.\n"
                    "Download it from Google Cloud Console (OAuth 2.0 Desktop client)."
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def init_db():
    """Create the expenses table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            amount      REAL NOT NULL,
            currency    TEXT DEFAULT 'INR',
            merchant    TEXT,
            category    TEXT,
            description TEXT,
            source      TEXT,
            gmail_id    TEXT UNIQUE,
            created_at  TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def expense_exists(conn, gmail_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM expenses WHERE gmail_id = ?", (gmail_id,)
    ).fetchone()
    return row is not None


def insert_expense(conn, expense: dict):
    conn.execute(
        """
        INSERT INTO expenses (date, amount, currency, merchant, category,
                              description, source, gmail_id)
        VALUES (:date, :amount, :currency, :merchant, :category,
                :description, :source, :gmail_id)
        """,
        expense,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


def build_gmail_query(month: Optional[str]) -> str:
    """Build a Gmail search query combining all SEARCH_QUERIES with date range."""
    combined = " OR ".join(f"({q})" for q in SEARCH_QUERIES)
    if month:
        year, mon = month.split("-")
        start = f"{year}/{mon}/01"
        # Compute end of month
        dt = datetime(int(year), int(mon), 1)
        if int(mon) == 12:
            end_dt = datetime(int(year) + 1, 1, 1)
        else:
            end_dt = datetime(int(year), int(mon) + 1, 1)
        end = end_dt.strftime("%Y/%m/%d")
        combined = f"({combined}) after:{start} before:{end}"
    return combined


def fetch_emails(service, query: str, max_results: int = 200):
    """Fetch email messages matching the query."""
    messages = []
    result = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    messages.extend(result.get("messages", []))

    while "nextPageToken" in result and len(messages) < max_results:
        result = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=max_results,
                pageToken=result["nextPageToken"],
            )
            .execute()
        )
        messages.extend(result.get("messages", []))

    return messages


def get_email_body(service, msg_id: str) -> dict:
    """Retrieve subject, date, sender, and plain-text body of an email."""
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    date_str = headers.get("date", "")

    # Parse date
    try:
        date_obj = parsedate_to_datetime(date_str)
        date_iso = date_obj.strftime("%Y-%m-%d")
    except Exception:
        date_iso = ""

    # Extract plain text body
    body_text = _extract_text(msg["payload"])
    return {
        "id": msg_id,
        "subject": subject,
        "sender": sender,
        "date": date_iso,
        "body": body_text[:3000],  # Truncate for LLM context
    }


def _extract_text(payload) -> str:
    """Recursively extract plain text from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    parts = payload.get("parts", [])
    for part in parts:
        text = _extract_text(part)
        if text:
            return text
    # Fallback: try HTML
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        raw = base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
        # Strip HTML tags for a rough plaintext
        return re.sub(r"<[^>]+>", " ", raw)
    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            raw = base64.urlsafe_b64decode(part["body"]["data"]).decode(
                "utf-8", errors="replace"
            )
            return re.sub(r"<[^>]+>", " ", raw)
    return ""


# ---------------------------------------------------------------------------
# Ollama parsing
# ---------------------------------------------------------------------------

PARSE_PROMPT = """\
You are an expense parser. Given the email below, extract the transaction details.
Reply ONLY with a valid JSON object (no markdown, no explanation). If the email is
NOT a financial transaction or receipt, reply exactly: {{"is_expense": false}}

Required JSON fields:
- is_expense: true
- amount: number (the amount debited/spent, without currency symbol)
- currency: string ("INR", "USD", etc.)
- merchant: string (who was paid)
- category: one of %s
- description: brief one-line summary of the purchase

EMAIL:
Subject: {subject}
From: {sender}
Date: {date}
Body:
{body}
"""


def parse_with_ollama(email_data: dict) -> Optional[dict]:
    """Send email text to Ollama and parse the JSON response."""
    prompt = PARSE_PROMPT % json.dumps(CATEGORIES)
    prompt = prompt.format(**email_data)

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "")
    except Exception as e:
        print(f"  ⚠ Ollama error: {e}")
        return None

    # Try to extract JSON from the response
    try:
        # Strip markdown fences if present
        cleaned = re.sub(r"```json\s*|```", "", raw).strip()
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON in the response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                print(f"  ⚠ Could not parse Ollama response as JSON")
                return None
        else:
            print(f"  ⚠ No JSON found in Ollama response")
            return None

    if not parsed.get("is_expense", False):
        return None

    return parsed


# ---------------------------------------------------------------------------
# Google Sheets sync
# ---------------------------------------------------------------------------


def sync_to_sheets(creds, conn):
    """Sync all expenses to a Google Sheet named 'Expenses'."""
    sheets_service = build("sheets", "v4", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    # Find or create the spreadsheet
    sheet_id = _find_sheet(drive_service, SHEET_NAME)
    if not sheet_id:
        sheet_id = _create_sheet(sheets_service, SHEET_NAME)
        print(f"  Created new Google Sheet: {SHEET_NAME}")

    # Fetch all expenses
    rows = conn.execute(
        "SELECT date, amount, currency, merchant, category, description, source "
        "FROM expenses ORDER BY date DESC"
    ).fetchall()

    headers = [
        ["Date", "Amount", "Currency", "Merchant", "Category", "Description", "Source"]
    ]
    data = headers + [list(r) for r in rows]

    # Clear and write
    sheets_service.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range="Sheet1"
    ).execute()

    sheets_service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Sheet1!A1",
        valueInputOption="USER_ENTERED",
        body={"values": data},
    ).execute()

    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    print(f"  ✅ Synced {len(rows)} expenses to Google Sheets: {url}")
    return url


def _find_sheet(drive_service, name: str) -> Optional[str]:
    results = (
        drive_service.files()
        .list(
            q=f"name='{name}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
            spaces="drive",
            fields="files(id)",
        )
        .execute()
    )
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _create_sheet(sheets_service, name: str) -> str:
    sheet = (
        sheets_service.spreadsheets()
        .create(body={"properties": {"title": name}})
        .execute()
    )
    return sheet["spreadsheetId"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_setup():
    """Run the OAuth setup flow."""
    print("🔑 Running OAuth setup...")
    creds = get_credentials()
    # Quick test
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    print(f"✅ Authenticated as: {profile['emailAddress']}")
    print("Setup complete! You can now run: python3 agent.py --run")


def run_pipeline(month: Optional[str]):
    """Main pipeline: fetch → parse → store → sync."""
    print("=" * 60)
    print(f"  Expense Tracker — {month or 'all time'}")
    print("=" * 60)

    creds = get_credentials()
    gmail = build("gmail", "v1", credentials=creds)
    conn = init_db()

    # 1. Fetch emails
    query = build_gmail_query(month)
    print(f"\n📧 Searching Gmail...")
    messages = fetch_emails(gmail, query)
    print(f"  Found {len(messages)} candidate emails")

    # 2. Process each email
    new_count = 0
    skip_count = 0
    fail_count = 0

    for i, msg in enumerate(messages, 1):
        msg_id = msg["id"]
        if expense_exists(conn, msg_id):
            skip_count += 1
            continue

        print(f"\n[{i}/{len(messages)}] Fetching email {msg_id}...")
        email_data = get_email_body(gmail, msg_id)

        if not email_data["body"].strip():
            print("  ⚠ Empty body, skipping")
            fail_count += 1
            continue

        print(f"  Subject: {email_data['subject'][:60]}")
        print(f"  Parsing with {OLLAMA_MODEL}...")

        parsed = parse_with_ollama(email_data)
        if not parsed:
            print("  ⏭ Not an expense or parse failed")
            fail_count += 1
            continue

        expense = {
            "date": email_data["date"] or datetime.now().strftime("%Y-%m-%d"),
            "amount": float(parsed.get("amount", 0)),
            "currency": parsed.get("currency", "INR"),
            "merchant": parsed.get("merchant", "Unknown"),
            "category": parsed.get("category", "Other"),
            "description": parsed.get("description", ""),
            "source": email_data["sender"][:100],
            "gmail_id": msg_id,
        }

        insert_expense(conn, expense)
        new_count += 1
        print(
            f"  ✅ ₹{expense['amount']:,.0f} at {expense['merchant']} "
            f"[{expense['category']}]"
        )

    # 3. Summary
    print(f"\n{'=' * 60}")
    print(f"  Done! New: {new_count} | Skipped (dup): {skip_count} | Failed: {fail_count}")

    total = conn.execute("SELECT count(*), sum(amount) FROM expenses").fetchone()
    print(f"  Total in DB: {total[0]} expenses, ₹{total[1] or 0:,.0f}")

    # 4. Sync to Google Sheets
    print(f"\n📊 Syncing to Google Sheets...")
    sync_to_sheets(creds, conn)

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Expense Tracker Agent")
    parser.add_argument("--setup", action="store_true", help="Run OAuth setup")
    parser.add_argument("--run", action="store_true", help="Run expense extraction")
    parser.add_argument(
        "--month",
        type=str,
        default=None,
        help="Filter to month (YYYY-MM format, e.g. 2025-03)",
    )
    args = parser.parse_args()

    if args.setup:
        run_setup()
    elif args.run:
        run_pipeline(args.month)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
