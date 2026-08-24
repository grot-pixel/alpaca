"""
Daily Alpaca performance report.

Supports up to two accounts and sends the report through Gmail
when EMAIL_USER and EMAIL_PASS are configured.
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest


EASTERN = ZoneInfo("America/New_York")


# ============================================================
# EMAIL
# ============================================================

def send_email(
    subject: str,
    body: str,
) -> None:
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")

    if not user or not password:
        print(
            "⚠️ EMAIL_USER / EMAIL_PASS not set. "
            "Skipping email."
        )
        print(body)
        return

    message = EmailMessage()
    message.set_content(body)
    message["Subject"] = subject
    message["From"] = user
    message["To"] = user

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
    ) as smtp:
        smtp.login(
            user,
            password,
        )
        smtp.send_message(message)


# ============================================================
# FORMATTING
# ============================================================

def format_qty(qty: float) -> str:
    if abs(qty - round(qty)) < 1e-8:
        return f"{int(round(qty))}"

    return f"{qty:.6f}".rstrip("0").rstrip(".")


def format_local_time(
    timestamp,
) -> str:
    if timestamp is None:
        return "?"

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(
            tzinfo=timezone.utc
        )

    return timestamp.astimezone(
        EASTERN
    ).strftime("%I:%M:%S %p ET")


# ============================================================
# ACCOUNT REPORT
# ============================================================

def build_account_report(
    name: str,
    api_key: str,
    api_secret: str,
    base_url: str,
) -> str:
    is_paper = (
        "paper"
        in base_url.lower()
    )

    client = TradingClient(
        api_key,
        api_secret,
        paper=is_paper,
    )

    mode = (
        "PAPER"
        if is_paper
        else "⚠️ LIVE"
    )

    lines = [
        "─" * 52,
        f"{name} ({mode})",
        "─" * 52,
    ]

    # --------------------------------------------------------
    # ACCOUNT
    # --------------------------------------------------------

    try:
        account = client.get_account()

        equity = float(account.equity)
        previous = float(
            account.last_equity
        )

        pnl = equity - previous

        pnl_pct = (
            pnl / previous * 100
            if previous
            else 0.0
        )

        cash = float(account.cash)
        buying_power = float(
            account.buying_power
        )

        icon = (
            "📈"
            if pnl >= 0
            else "📉"
        )

        lines.extend(
            [
                f"Equity:       ${equity:,.2f}",
                f"Prior close:  ${previous:,.2f}",
                (
                    f"Day P&L:      "
                    f"${pnl:+,.2f} "
                    f"({pnl_pct:+.2f}%) {icon}"
                ),
                f"Cash:         ${cash:,.2f}",
                (
                    f"Buying power: "
                    f"${buying_power:,.2f}"
                ),
            ]
        )

    except Exception as exc:
        lines.append(
            f"❌ Account error: {exc}"
        )
        return "\n".join(lines)

    # --------------------------------------------------------
    # POSITIONS
    # --------------------------------------------------------

    lines.append("")

    try:
        positions = (
            client.get_all_positions()
        )

        if positions:
            lines.append(
                f"Open positions "
                f"({len(positions)}):"
            )

            for position in positions:
                symbol = position.symbol
                qty = float(position.qty)

                avg = float(
                    position.avg_entry_price
                )

                current = float(
                    position.current_price
                )

                unrealized = float(
                    position.unrealized_pl
                )

                unrealized_pct = (
                    float(
                        position.unrealized_plpc
                    )
                    * 100
                )

                icon = (
                    "🟢"
                    if unrealized >= 0
                    else "🔴"
                )

                lines.append(
                    (
                        f"{icon} {symbol:<6} "
                        f"{format_qty(qty):>10} "
                        f"shares | "
                        f"${avg:.2f} → "
                        f"${current:.2f} | "
                        f"P&L ${unrealized:+.2f} "
                        f"({unrealized_pct:+.2f}%)"
                    )
                )

        else:
            lines.append(
                "Open positions: none"
            )

    except Exception as exc:
        lines.append(
            f"Positions error: {exc}"
        )

    # --------------------------------------------------------
    # FILLED ORDERS
    # --------------------------------------------------------

    lines.append("")

    try:
        now_utc = datetime.now(
            timezone.utc
        )

        start_of_day = (
            now_utc
            .replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        )

        request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=start_of_day,
            limit=100,
        )

        orders = client.get_orders(
            filter=request
        )

        filled = [
            order
            for order in orders
            if order.status.value
            == "filled"
        ]

        if filled:
            lines.append(
                f"Today's fills "
                f"({len(filled)}):"
            )

            for order in sorted(
                filled,
                key=lambda item: (
                    item.filled_at
                    or datetime.min.replace(
                        tzinfo=timezone.utc
                    )
                ),
            ):
                side = (
                    order.side.value.upper()
                )

                symbol = order.symbol

                qty = (
                    float(order.filled_qty)
                    if order.filled_qty
                    else 0.0
                )

                price = (
                    float(
                        order.filled_avg_price
                    )
                    if order.filled_avg_price
                    else 0.0
                )

                timestamp = (
                    format_local_time(
                        order.filled_at
                    )
                )

                icon = (
                    "↑"
                    if side == "BUY"
                    else "↓"
                )

                lines.append(
                    (
                        f"{icon} "
                        f"{timestamp} "
                        f"{side:<4} "
                        f"{format_qty(qty):>10}x "
                        f"{symbol:<6} "
                        f"@ ${price:.2f}"
                    )
                )

        else:
            lines.append(
                "Today's fills: none"
            )

    except Exception as exc:
        lines.append(
            f"Orders error: {exc}"
        )

    return "\n".join(lines)


# ============================================================
# FULL REPORT
# ============================================================

def get_report() -> str:
    now = datetime.now(
        timezone.utc
    ).astimezone(EASTERN)

    header = [
        "=" * 52,
        "📊 ALPACA BOT — DAILY PERFORMANCE REPORT",
        now.strftime(
            "%A, %B %d %Y  %I:%M:%S %p ET"
        ),
        "=" * 52,
        "",
    ]

    sections = []

    for index in (1, 2):
        key = os.getenv(
            f"APCA_API_KEY_{index}"
        )

        secret = os.getenv(
            f"APCA_API_SECRET_{index}"
        )

        url = os.getenv(
            f"APCA_BASE_URL_{index}",
            "https://paper-api.alpaca.markets",
        )

        if not key or not secret:
            continue

        sections.append(
            build_account_report(
                f"Account {index}",
                key,
                secret,
                url,
            )
        )

    footer = [
        "",
        "─" * 52,
        "⚠️ Automated trading software. "
        "Paper trade and validate before live use.",
        "=" * 52,
    ]

    return "\n".join(
        header
        + sections
        + footer
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    try:
        report = get_report()

        print(report)

        send_email(
            "📈 Daily Alpaca Bot Report",
            report,
        )

        print(
            "\n✅ Report completed."
        )

    except Exception as exc:
        print(
            f"❌ Report failed: "
            f"{type(exc).__name__}: {exc}"
        )
        raise
