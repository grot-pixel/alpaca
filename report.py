import os
import smtplib
from email.message import EmailMessage
from alpaca_trade_api.rest import REST

def send_email(subject, body):
    user = os.getenv('EMAIL_USER')
    password = os.getenv('EMAIL_PASS')
    
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = user
    msg['To'] = user # Sends the report to your own inbox

    # Connect to Gmail's free SMTP server
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)

def get_report():
    report = "📊 DAILY BOT PERFORMANCE\n" + "="*25 + "\n\n"
    
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        sec = os.getenv(f"APCA_API_SECRET_{i}")
        if not key: continue
        
        api = REST(key, sec, "https://paper-api.alpaca.markets")
        acc = api.get_account()
        
        equity = float(acc.equity)
        prev_equity = float(acc.last_equity)
        pnl = equity - prev_equity
        pnl_pct = (pnl / prev_equity) * 100
        
        report += f"ACCOUNT {i}\n"
        report += f"Current Equity: ${equity:,.2f}\n"
        report += f"Today's Change: ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
        
        # Add a quick list of what you're holding
        positions = api.list_positions()
        if positions:
            report += "Active Positions: " + ", ".join([p.symbol for p in positions]) + "\n"
        else:
            report += "Active Positions: None\n"
        report += "-"*25 + "\n"
        
    return report

if __name__ == "__main__":
    try:
        content = get_report()
        send_email("📈 Your Daily Alpaca Report", content)
        print("Report sent!")
    except Exception as e:
        print(f"Error: {e}")
