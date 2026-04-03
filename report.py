"""
report.py — Daily performance report via email
===============================================
Runs once per day (typically at market close via GitHub Actions).
Reads all accounts and sends a formatted summary email.
"""

import os
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus


# ─── EMAIL ───────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str):
    user     = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")

    if not user or not password:
        print("⚠️  EMAIL_USER / EMAIL_PASS not set. Skipping email send.")
        print(body)
        return

    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = user

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


# ─── REPORT BUILDER ──────────────────────────────────────────────────────────

def build_account_report(name: str, api_key: str, api_secret: str, base_url: str) -> str:
    is_paper = "paper" in base_url
    client   = TradingClient(api_key, api_secret, paper=is_paper)
    mode     = "Paper" if is_paper else "⚠️  LIVE"

    lines = [f"  {'─'*30}", f"  {name} ({mode})", f"  {'─'*30}"]

    # ── Account snapshot ─────────────────────────────────────────────────────
    try:
        acct       = client.get_account()
        equity     = float(acct.equity)
        prev       = float(acct.last_equity)
        pnl        = equity - prev
        pnl_pct    = (pnl / prev * 100) if prev else 0
        cash       = float(acct.cash)
        buying_pow = float(acct.buying_power)

        pnl_icon = "📈" if pnl >= 0 else "📉"
        lines += [
            f"  Equity:       ${equity:>12,.2f}",
            f"  Prior Close:  ${prev:>12,.2f}",
            f"  Day P&L:      ${pnl:>+12.2f}  ({pnl_pct:+.2f}%)  {pnl_icon}",
            f"  Cash:         ${cash:>12,.2f}",
            f"  Buying Power: ${buying_pow:>12,.2f}",
        ]
    except Exception as e:
        lines.append(f"  ❌ Account error: {e}")
        return "\n".join(lines)

    # ── Open positions ───────────────────────────────────────────────────────
    lines.append("")
    try:
        positions = client.get_all_positions()
        if positions:
            lines.append(f"  Open Positions ({len(positions)}):")
            for p in positions:
                sym        = p.symbol
                qty        = int(float(p.qty))
                avg        = float(p.avg_entry_price)
                cur        = float(p.current_price)
                unreal     = float(p.unrealized_pl)
                unreal_pct = float(p.unrealized_plpc) * 100
                icon       = "🟢" if unreal >= 0 else "🔴"
                lines.append(
                    f"  {icon} {sym:<6}  {qty:>4} shares  "
                    f"avg ${avg:.2f} → ${cur:.2f}  "
                    f"P&L: ${unreal:+.2f} ({unreal_pct:+.2f}%)"
                )
        else:
            lines.append("  Open Positions: none")
    except Exception as e:
        lines.append(f"  Positions error: {e}")

    # ── Today's filled orders ────────────────────────────────────────────────
    lines.append("")
    try:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, after=today_str, limit=50)
        orders = client.get_orders(req)
        filled = [o for o in orders if o.status.value == "filled"]

        if filled:
            lines.append(f"  Today's Fills ({len(filled)}):")
            for o in filled:
                side  = o.side.value.upper()
                sym   = o.symbol
                qty   = int(float(o.filled_qty)) if o.filled_qty else 0
                price = float(o.filled_avg_price) if o.filled_avg_price else 0
                ts    = o.filled_at.strftime("%H:%M") if o.filled_at else "?"
                icon  = "↑" if side == "BUY" else "↓"
                lines.append(f"  {icon} {ts}  {side:<4} {qty:>4}x {sym:<6} @ ${price:.2f}")
        else:
            lines.append("  Today's Fills: none")
    except Exception as e:
        lines.append(f"  Orders error: {e}")

    return "\n".join(lines)


def get_report() -> str:
    now     = datetime.now(timezone.utc)
    header  = [
        "=" * 45,
        "  📊 ALPACA BOT — DAILY PERFORMANCE REPORT",
        f"  {now.strftime('%A, %B %d %Y  %I:%M %p UTC')}",
        "=" * 45,
        "",
    ]
    sections = []

    for i in [1, 2]:
        key    = os.getenv(f"APCA_API_KEY_{i}")
        secret = os.getenv(f"APCA_API_SECRET_{i}")
        url    = os.getenv(f"APCA_BASE_URL_{i}", "https://paper-api.alpaca.markets")

        if not key or not secret:
            continue

        section = build_account_report(f"Account {i}", key, secret, url)
        sections.append(section)

    footer = [
        "",
        "─" * 45,
        "  ⚠️  Not financial advice. Paper trade first.",
        "=" * 45,
    ]

    return "\n".join(header + sections + footer)


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        content = get_report()
        print(content)
        send_email("📈 Daily Alpaca Bot Report", content)
        print("\n✅ Report sent successfully!")
    except Exception as e:
        print(f"❌ Report failed: {e}")
        raise
