#!/usr/bin/env python3
"""
Expense Dashboard Generator
Reads expenses from SQLite and generates a self-contained HTML dashboard.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_FILE = "expenses.db"
DEFAULT_OUTPUT = "dashboard.html"


def load_expenses(db_path: str, month: Optional[str] = None):
    """Load expenses from SQLite, optionally filtered by month."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM expenses"
    params = []
    if month:
        query += " WHERE date LIKE ?"
        params.append(f"{month}%")
    query += " ORDER BY date DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def compute_stats(expenses: list[dict]) -> dict:
    """Compute summary statistics."""
    if not expenses:
        return {
            "total": 0,
            "count": 0,
            "avg": 0,
            "max_expense": None,
            "by_category": {},
            "by_date": {},
            "top_merchants": [],
        }

    total = sum(e["amount"] for e in expenses)
    by_category = defaultdict(float)
    by_date = defaultdict(float)
    by_merchant = defaultdict(float)

    for e in expenses:
        by_category[e.get("category", "Other")] += e["amount"]
        by_date[e["date"]] += e["amount"]
        merchant = e.get("merchant", "Unknown")
        by_merchant[merchant] += e["amount"]

    max_expense = max(expenses, key=lambda e: e["amount"])
    top_merchants = sorted(by_merchant.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total": total,
        "count": len(expenses),
        "avg": total / len(expenses),
        "max_expense": max_expense,
        "by_category": dict(sorted(by_category.items(), key=lambda x: x[1], reverse=True)),
        "by_date": dict(sorted(by_date.items())),
        "top_merchants": top_merchants,
    }


def generate_html(expenses: list[dict], stats: dict, month: Optional[str]) -> str:
    """Generate a self-contained HTML dashboard."""
    title = f"Expenses — {month}" if month else "Expenses — All Time"
    cat_labels = json.dumps(list(stats["by_category"].keys()))
    cat_values = json.dumps(list(stats["by_category"].values()))
    date_labels = json.dumps(list(stats["by_date"].keys()))
    date_values = json.dumps(list(stats["by_date"].values()))

    # Build transaction rows
    tx_rows = ""
    for e in expenses[:200]:
        tx_rows += f"""
        <tr>
            <td>{e['date']}</td>
            <td class="amount">₹{e['amount']:,.0f}</td>
            <td>{e.get('merchant','—')}</td>
            <td><span class="badge">{e.get('category','Other')}</span></td>
            <td>{e.get('description','')}</td>
        </tr>"""

    # Top merchants rows
    merchant_rows = ""
    for name, amt in stats["top_merchants"]:
        merchant_rows += f"""
        <tr><td>{name}</td><td class="amount">₹{amt:,.0f}</td></tr>"""

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d37;
    --text: #e4e4e7;
    --muted: #9ca3af;
    --accent: #6366f1;
    --green: #22c55e;
    --red: #ef4444;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text);
    padding: 2rem; line-height: 1.6;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: 1.5rem; }}
  .grid {{ display: grid; gap: 1rem; margin-bottom: 1.5rem; }}
  .grid-4 {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
  .grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 1.25rem;
  }}
  .card h3 {{ font-size: 0.8rem; text-transform: uppercase; color: var(--muted);
              letter-spacing: 0.05em; margin-bottom: 0.5rem; }}
  .stat {{ font-size: 1.8rem; font-weight: 700; }}
  .chart-container {{ position: relative; height: 300px; }}
  table {{
    width: 100%; border-collapse: collapse; font-size: 0.85rem;
  }}
  th, td {{
    text-align: left; padding: 0.6rem 0.8rem;
    border-bottom: 1px solid var(--border);
  }}
  th {{ color: var(--muted); font-weight: 600; font-size: 0.75rem;
       text-transform: uppercase; }}
  .amount {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
  .badge {{
    background: rgba(99,102,241,0.15); color: var(--accent);
    padding: 0.15rem 0.5rem; border-radius: 6px; font-size: 0.75rem;
  }}
</style>
</head>
<body>
<h1>📊 {title}</h1>

<div class="grid grid-4">
  <div class="card">
    <h3>Total Spent</h3>
    <div class="stat">₹{stats['total']:,.0f}</div>
  </div>
  <div class="card">
    <h3>Transactions</h3>
    <div class="stat">{stats['count']}</div>
  </div>
  <div class="card">
    <h3>Average</h3>
    <div class="stat">₹{stats['avg']:,.0f}</div>
  </div>
  <div class="card">
    <h3>Biggest</h3>
    <div class="stat">₹{stats['max_expense']['amount'] if stats['max_expense'] else 0:,.0f}</div>
    <div style="color:var(--muted);font-size:0.8rem;">{stats['max_expense'].get('merchant','') if stats['max_expense'] else ''}</div>
  </div>
</div>

<div class="grid grid-2">
  <div class="card">
    <h3>By Category</h3>
    <div class="chart-container"><canvas id="catChart"></canvas></div>
  </div>
  <div class="card">
    <h3>Daily Spending</h3>
    <div class="chart-container"><canvas id="dateChart"></canvas></div>
  </div>
</div>

<div class="grid grid-2" style="margin-top:1rem;">
  <div class="card">
    <h3>Top Merchants</h3>
    <table>
      <thead><tr><th>Merchant</th><th>Total</th></tr></thead>
      <tbody>{merchant_rows}</tbody>
    </table>
  </div>
  <div class="card">
    <h3>Recent Transactions</h3>
    <div style="max-height:400px;overflow-y:auto;">
    <table>
      <thead><tr><th>Date</th><th>Amount</th><th>Merchant</th><th>Category</th><th>Description</th></tr></thead>
      <tbody>{tx_rows}</tbody>
    </table>
    </div>
  </div>
</div>

<script>
const palette = [
  '#6366f1','#8b5cf6','#a78bfa','#ec4899','#f43f5e',
  '#f97316','#eab308','#22c55e','#14b8a6','#06b6d4',
  '#3b82f6','#64748b','#d946ef'
];
Chart.defaults.color = '#9ca3af';
Chart.defaults.borderColor = '#2a2d37';

new Chart(document.getElementById('catChart'), {{
  type: 'doughnut',
  data: {{
    labels: {cat_labels},
    datasets: [{{ data: {cat_values}, backgroundColor: palette, borderWidth: 0 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'right', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }} }}
  }}
}});

new Chart(document.getElementById('dateChart'), {{
  type: 'bar',
  data: {{
    labels: {date_labels},
    datasets: [{{ data: {date_values}, backgroundColor: '#6366f1', borderRadius: 4 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ maxRotation: 45, font: {{ size: 10 }} }} }},
      y: {{ ticks: {{ callback: v => '₹' + v.toLocaleString() }} }}
    }}
  }}
}});
</script>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(description="Expense Dashboard Generator")
    parser.add_argument(
        "--month", type=str, default=None, help="Filter to month (YYYY-MM)"
    )
    parser.add_argument(
        "--out", type=str, default=DEFAULT_OUTPUT, help="Output HTML file"
    )
    args = parser.parse_args()

    if not Path(DB_FILE).exists():
        print(f"ERROR: {DB_FILE} not found. Run agent.py --run first.")
        sys.exit(1)

    expenses = load_expenses(DB_FILE, args.month)
    if not expenses:
        print("No expenses found for the given period.")
        sys.exit(0)

    stats = compute_stats(expenses)
    html = generate_html(expenses, stats, args.month)

    Path(args.out).write_text(html)
    print(f"✅ Dashboard written to {args.out} ({stats['count']} expenses)")


if __name__ == "__main__":
    main()
