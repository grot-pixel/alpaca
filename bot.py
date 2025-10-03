import os
import json
import alpaca_trade_api as tradeapi
import pandas as pd
from utils import generate_signals

# Config file for symbols, max allocation, etc.
with open("config.json") as f:
    config = json.load(f)

# Define accounts using secret environment variables
accounts_list = [
    {
        "name": "PaperAccount1",
        "api_key": os.getenv("APCA_API_KEY_1"),
        "api_secret": os.getenv("APCA_API_SECRET_1"),
        "base_url": os.getenv("APCA_BASE_URL_1", "https://paper-api.alpaca.markets")
    },
    {
        "name": "PaperAccount2",
        "api_key": os.getenv("APCA_API_KEY_2"),
        "api_secret": os.getenv("APCA_API_SECRET_2"),
        "base_url": os.getenv("APCA_BASE_URL_2", "https://paper-api.alpaca.markets")
    }
    # Add more accounts here as needed
]

# Fetch price data
def fetch_data(api, symbol, limit=100):
    bars = api.get_bars(symbol, "1Min", limit=limit)
    df = pd.DataFrame([{
        "time": bar.t,
        "open": bar.o,
        "high": bar.h,
        "low": bar.l,
        "close": bar.c,
        "volume": bar.v
    } for bar in bars])
    return df

# Trade logic
def trade_account(account_info):
    print(f"\n=== Trading for {account_info['name']} ===")
    api = tradeapi.REST(
        account_info["api_key"],
        account_info["api_secret"],
        account_info.get("base_url", "https://paper-api.alpaca.markets"),
        api_version="v2"
    )

    account = api.get_account()
    cash = float(account.cash)
    equity = float(account.equity)
    positions = {p.symbol: float(p.qty) for p in api.list_positions()}

    print(f"Account Cash: ${cash:.2f}, Equity: ${equity:.2f}")
    print(f"Current Positions: {positions}")

    for symbol in config["symbols"]:
        try:
            df = fetch_data(api, symbol)
            signal = generate_signals(df, config)

            if signal:
                print(f"Signal for {symbol}: {signal.upper()}")
            else:
                print(f"No signal for {symbol}, skipping.")
                continue

            position = positions.get(symbol, 0)
            max_alloc = equity * config["max_position_pct"]
            trade_size = equity * config["max_trade_pct"]
            last_price = df.iloc[-1]["close"]
            qty = int(trade_size / last_price) if trade_size > 0 else 0

            if signal == "buy":
                if position > 0:
                    print(f"Skipped BUY {symbol}: already holding {position} shares")
                elif cash < trade_size:
                    print(f"Skipped BUY {symbol}: insufficient cash (need ${trade_size:.2f}, have ${cash:.2f})")
                elif qty == 0:
                    print(f"Skipped BUY {symbol}: calculated qty is 0 at last price ${last_price:.2f}")
                else:
                    take_profit_price = round(last_price * (1 + config["take_profit_pct"]), 2)
                    stop_loss_price = round(last_price * (1 - config["stop_loss_pct"]), 2)

                    api.submit_order(
                        symbol=symbol,
                        qty=qty,
                        side="buy",
                        type="market",
                        time_in_force="day",
                        order_class="bracket",
                        take_profit={"limit_price": take_profit_price},
                        stop_loss={"stop_price": stop_loss_price},
                        extended_hours=True
                    )
                    print(f"Placed BUY {qty} {symbol} @ {last_price:.2f}, TP {take_profit_price}, SL {stop_loss_price}")

            elif signal == "sell":
                if position == 0:
                    print(f"Skipped SELL {symbol}: no position to sell")
                else:
                    api.submit_order(
                        symbol=symbol,
                        qty=position,
                        side="sell",
                        type="market",
                        time_in_force="day",
                        extended_hours=True
                    )
                    print(f"Placed SELL {position} {symbol} @ {last_price:.2f}")

        except Exception as e:
            print(f"Error processing {symbol} for {account_info['name']}: {e}")

if __name__ == "__main__":
    for account_info in accounts_list:
        trade_account(account_info)
