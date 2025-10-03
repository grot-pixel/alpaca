import os
import json
import alpaca_trade_api as tradeapi
import pandas as pd
from utils import generate_signals

# Load config
with open("config.json") as f:
    config = json.load(f)

# Alpaca auth
API_KEY = os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")

api = tradeapi.REST(API_KEY, API_SECRET, BASE_URL, api_version="v2")

def fetch_data(symbol, limit=100):
    barset = api.get_bars(symbol, "1Min", limit=limit)
    df = pd.DataFrame([{
        "time": bar.t,
        "open": bar.o,
        "high": bar.h,
        "low": bar.l,
        "close": bar.c,
        "volume": bar.v
    } for bar in barset])
    return df

def main():
    account = api.get_account()
    cash = float(account.cash)
    equity = float(account.equity)

    positions = {p.symbol: float(p.qty) for p in api.list_positions()}

    for symbol in config["symbols"]:
        try:
            df = fetch_data(symbol)
            signal = generate_signals(df, config)
            if not signal:
                continue

            position = positions.get(symbol, 0)
            max_alloc = equity * config["max_position_pct"]
            trade_size = equity * config["max_trade_pct"]

            if signal == "buy" and position == 0 and cash > trade_size:
                last_price = df.iloc[-1]["close"]
                qty = int(trade_size / last_price)
                if qty > 0:
                    api.submit_order(
                        symbol=symbol,
                        qty=qty,
                        side="buy",
                        type="market",
                        time_in_force="day"
                    )
                    print(f"BUY {qty} {symbol}")

            elif signal == "sell" and position > 0:
                api.submit_order(
                    symbol=symbol,
                    qty=position,
                    side="sell",
                    type="market",
                    time_in_force="day"
                )
                print(f"SELL {position} {symbol}")

        except Exception as e:
            print(f"Error trading {symbol}: {e}")

if __name__ == "__main__":
    main()
