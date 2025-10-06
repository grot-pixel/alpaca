import json
import time
import datetime
import pandas as pd
import numpy as np
from robinhood_client import RobinhoodClient

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def get_signals(df, cfg):
    df["sma_fast"] = df["close"].rolling(cfg["sma_fast"]).mean()
    df["sma_slow"] = df["close"].rolling(cfg["sma_slow"]).mean()

    delta = df["close"].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(cfg["rsi_period"]).mean()
    avg_loss = pd.Series(loss).rolling(cfg["rsi_period"]).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    if len(df) < max(cfg["sma_slow"], cfg["rsi_period"]):
        return "NO_DATA"

    sma_fast = df["sma_fast"].iloc[-1]
    sma_slow = df["sma_slow"].iloc[-1]
    rsi = df["rsi"].iloc[-1]

    if sma_fast > sma_slow and rsi < cfg["rsi_overbought"]:
        return "BUY"
    elif sma_fast < sma_slow and rsi > cfg["rsi_oversold"]:
        return "SELL"
    else:
        return "HOLD"

def main():
    cfg = load_config()
    print(f"Loaded config: {cfg}")

    accounts = ["PaperAccount1", "PaperAccount2"]
    clients = [RobinhoodClient(acc) for acc in accounts]

    while True:
        print("\n=== New Scan", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "===")
        for client in clients:
            print(f"\n=== Trading for {client.account_name} ===")
            print(f"Account Cash: ${client.cash:.2f}, Equity: ${client.equity:.2f}")
            print(f"Current Positions: {client.positions}")

            for sym in cfg["symbols"]:
                try:
                    df = client.get_price_history(sym)
                    if df is None or len(df) < cfg["sma_slow"]:
                        print(f"[{sym}] Not enough data, skipping.")
                        continue

                    signal = get_signals(df, cfg)
                    if signal == "NO_DATA":
                        print(f"[{sym}] Insufficient data.")
                        continue

                    print(f"[{sym}] Signal → {signal}")
                except Exception as e:
                    print(f"[{sym}] Error: {e}")

        print("\nSleeping 5 minutes...\n")
        time.sleep(300)

if __name__ == "__main__":
    main()
