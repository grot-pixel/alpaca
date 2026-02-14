import os
import smtplib
from email.message import EmailMessage
from alpaca_trade_api.rest import REST

def send_email(subject, body):
    user, password = os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASS')
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'], msg['From'], msg['To'] = subject, user, user
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)

def get_report():
    report = "📊 DAILY BOT PERFORMANCE\n" + "="*25 + "\n\n"
    for i in [1, 2]:
        key, sec = os.getenv(f"APCA_API_KEY_{i}"), os.getenv(f"APCA_API_SECRET_{i}")
        if not key: continue
        api = REST(key, sec, "https://paper-api.alpaca.markets")
        acc = api.get_account()
        equity, prev = float(acc.equity), float(acc.last_equity)
        pnl, pnl_pct = equity - prev, ((equity - prev) / prev) * 100
        report += f"ACCOUNT {i}\nEquity: ${equity:,.2f}\nToday: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
        pos = api.list_positions()
        report += "Holding: " + (", ".join([p.symbol for p in pos]) if pos else "None") + "\n\n"
    return report

if __name__ == "__main__":
    try:
        send_email("📈 Your Daily Alpaca Report", get_report())
        print("Report sent!")
    except Exception as e: print(f"Error: {e}")
