import os
import smtplib
from email.message import EmailMessage
from alpaca_trade_api.rest import REST

def send_email(subject, body):
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_USER') # Sends to yourself

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_PASS'))
            smtp.send_message(msg)
        print("Email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

def get_report():
    report_text = "--- Daily Alpaca Performance Report ---\n\n"
    
    for i in [1, 2]:
        key = os.getenv(f"APCA_API_KEY_{i}")
        sec = os.getenv(f"APCA_API_SECRET_{i}")
        url = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"
        
        if key and sec:
            api = REST(key, sec, url)
            acc = api.get_account()
            
            # Calculate daily change
            equity = float(acc.equity)
            last_equity = float(acc.last_equity)
            daily_pnl = equity - last_equity
            pnl_pct = (daily_pnl / last_equity) * 100
            
            report_text += f"Account {i}:\n"
            report_text += f"Total Equity: ${equity:,.2f}\n"
            report_text += f"Daily P/L: ${daily_pnl:,.2f} ({pnl_pct:+.2f}%)\n"
            report_text += f"Buying Power: ${float(acc.buying_power):,.2f}\n"
            report_text += "---------------------------------------\n"
    
    return report_text

if __name__ == "__main__":
    content = get_report()
    send_email("📊 Alpaca Daily Summary", content)
