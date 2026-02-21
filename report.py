import os
import smtplib
from email.message import EmailMessage
from alpaca_trade_api.rest import REST
from datetime import datetime, timezone


def send_email(subject, body):
    user = os.getenv('EMAIL_USER')
    password = os.getenv('EMAIL_PASS')

    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = user
    msg['To'] = user

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


def get_report():
    report = "📊 DAILY BOT PERFORMANCE\n" + "=" * 35 + "\n"
    report += f"Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}\n\n"

    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        sec = os.getenv(f"APCA_API_SECRET_{i}")
        url = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

        if not key:
            continue

        api = REST(key, sec, url)

        try:
            acc = api.get_account()
        except Exception as e:
            report += f"ACCOUNT {i}: Error fetching account — {e}\n\n"
            continue

        equity = float(acc.equity)
        prev_equity = float(acc.last_equity)
        pnl = equity - prev_equity
        pnl_pct = (pnl / prev_equity) * 100
        buying_power = float(acc.buying_power)

        report += f"ACCOUNT {i}  ({'Paper' if 'paper' in url else 'Live'})\n"
        report += f"  Equity:       ${equity:>10,.2f}\n"
        report += f"  Prev Close:   ${prev_equity:>10,.2f}\n"
        report += f"  Day P&L:      ${pnl:>+10.2f}  ({pnl_pct:+.2f}%)\n"
        report += f"  Buying Power: ${buying_power:>10,.2f}\n\n"

        # --- Open Positions ---
        try:
            positions = api.list_positions()
            if positions:
                report += "  Open Positions:\n"
                for p in positions:
                    sym = p.symbol
                    qty = int(float(p.qty))
                    avg = float(p.avg_entry_price)
                    cur = float(p.current_price)
                    unreal = float(p.unrealized_pl)
                    unreal_pct = float(p.unrealized_plpc) * 100
                    report += (
                        f"    {sym:<6} {qty:>4} shares | "
                        f"Avg ${avg:.2f} → ${cur:.2f} | "
                        f"P&L: ${unreal:+.2f} ({unreal_pct:+.2f}%)\n"
                    )
            else:
                report += "  Open Positions: None\n"
        except Exception as e:
            report += f"  Positions error: {e}\n"

        report += "\n"

        # --- Today's Trades ---
        try:
            today = datetime.now(timezone.utc).strftime('%Y-%m-%dT00:00:00Z')
            orders = api.list_orders(status='filled', after=today, limit=50)
            if orders:
                report += f"  Today's Filled Orders ({len(orders)}):\n"
                for o in orders:
                    side = o.side.upper()
                    sym = o.symbol
                    qty = int(float(o.filled_qty))
                    price = float(o.filled_avg_price)
                    filled_at = o.filled_at[:16].replace('T', ' ') if o.filled_at else '?'
                    report += f"    {filled_at}  {side:<4} {qty:>4}x {sym:<6} @ ${price:.2f}\n"
            else:
                report += "  Today's Filled Orders: None\n"
        except Exception as e:
            report += f"  Orders error: {e}\n"

        report += "-" * 35 + "\n\n"

    return report


if __name__ == "__main__":
    try:
        content = get_report()
        print(content)  # Always print so GitHub Actions logs show it
        send_email("📈 Daily Alpaca Report", content)
        print("✅ Report sent successfully!")
    except Exception as e:
        print(f"❌ Error sending report: {e}")
        raise
