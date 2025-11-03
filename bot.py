import os
import json
import time
from datetime import datetime, timezone, timedelta
import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame

# --- Load config ---
CONFIG_FILE = "config.json"
try:
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
except FileNotFoundError:
    print(f"Error: {CONFIG_FILE} not found. Ensure it's in the same directory.")
    exit()

print("Loaded config:", cfg)

# --- Signal logic (using your existing utils.py logic for reference) ---
# NOTE: The provided bot.py and utils.py have two different signal functions.
# This code assumes the logic from utils.py (SMA + RSI) is the intended one.

# Mock rsi and generate_signals functions for a self-contained bot.py
# If you use the separate `utils.py` file, you should import it: `from utils import generate_signals`
def rsi(series: pd.Series, period: int = 14):
    """Calculates the Relative Strength Index (RSI)."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def generate_signals(data, config):
    """Return 'buy' or 'sell' based on SMA + RSI strategy."""
    data["sma_fast"] = data["close"].rolling(config["sma_fast"]).mean()
    data["sma_slow"] = data["close"].rolling(config["sma_slow"]).mean()
    data["rsi"] = rsi(data["close"], config["rsi_period"])

    # Ensure we have enough data after rolling calculations
    if data.empty or data.iloc[-1].isnull().any():
        return None, "Not enough clean data for indicators"

    latest = data.iloc[-1]
    
    # Buy condition: Fast SMA > Slow SMA AND RSI is Oversold
    if (
        latest["sma_fast"] > latest["sma_slow"]
        and latest["rsi"] < config["rsi_oversold"]
    ):
        reason = f"SMA Cross: {latest['sma_fast']:.2f} > {latest['sma_slow']:.2f}, RSI Oversold: {latest['rsi']:.2f}"
        return "buy", reason
    # Sell condition: Fast SMA < Slow SMA AND RSI is Overbought
    elif (
        latest["sma_fast"] < latest["sma_slow"]
        and latest["rsi"] > config["rsi_overbought"]
    ):
        reason = f"SMA Cross: {latest['sma_fast']:.2f} < {latest['sma_slow']:.2f}, RSI Overbought: {latest['rsi']:.2f}"
        return "sell", reason
    else:
        return None, "No signal"


# --- Check if regular market is open ---
def is_regular_market_open(api: REST) -> bool:
    try:
        # Get the market clock
        clock = api.get_clock()
        # Check if is_open is True and current time is within regular hours
        is_open = clock.is_open and clock.timestamp.time() >= datetime.strptime('09:30', '%H:%M').time() and clock.timestamp.time() <= datetime.strptime('16:00', '%H:%M').time()
        return is_open
    except Exception as e:
        print(f"Error checking market clock: {e}")
        # Default to open if API call fails (be cautious with this in live trading)
        return True

# --- Main trading logic function ---
def trade_strategy(account_name: str, api: REST, symbols: list):
    print(f"\n--- Running strategy for {account_name} ---")

    # 1. Get current account information
    try:
        account = api.get_account()
        equity = float(account.equity)
        # cash = float(account.cash) # Cash not needed for percentage of equity sizing
        print(f"Account Equity: ${equity:,.2f} | Buying Power: ${float(account.buying_power):,.2f}")
    except Exception as e:
        print(f"Error fetching account info: {e}")
        return

    # 2. Get current positions
    positions_list = api.list_positions()
    positions = {p.symbol: float(p.qty) for p in positions_list}
    print(f"Current Positions: {positions}")

    # 3. Check for time validity
    market_open = is_regular_market_open(api)
    if not market_open:
        print("Regular market is closed. Only managing existing orders.")

    # 4. Check all symbols
    for sym in symbols:
        try:
            # Get latest market data
            # Adjust limit to include enough data points for all indicators
            required_limit = max(cfg["sma_slow"], cfg["rsi_period"]) + 2 
            
            # Fetch minute bars for the symbol
            # NOTE: For ETH, Alpaca uses Minute for crypto as well.
            bars = api.get_bars(sym, TimeFrame.Minute, limit=required_limit).df
            
            if bars.empty or len(bars) < required_limit:
                print(f"[{sym}] Skipping: Not enough data ({len(bars)}/{required_limit})")
                continue
            
            # Generate signals based on the loaded data and config
            # signal will be "buy", "sell", or None
            signal, reason = generate_signals(bars, cfg)
            
            # Get the latest closing price for sizing and stop/profit checks
            current_price = bars["close"].iloc[-1]
            
            # --- Position Sizing Calculation (The FIX for Scaling) ---
            # Max dollar amount for a single trade (20% of current equity)
            max_trade_dollar = equity * cfg["max_trade_pct"]
            
            # Max dollar amount for a total position (25% of current equity)
            max_position_dollar = equity * cfg["max_position_pct"]
            
            # Calculate the quantity of shares to trade
            # Use integer conversion (int()) for whole shares
            qty_to_buy = int(max_trade_dollar // current_price)
            
            # --- Trading Logic ---
            if market_open:
                if signal == "buy":
                    current_qty = positions.get(sym, 0)
                    current_dollar_value = current_qty * current_price
                    
                    # Calculate how many more shares can be bought to stay under the max position limit
                    remaining_buy_power = max_position_dollar - current_dollar_value
                    qty_to_add = int(remaining_buy_power // current_price)
                    
                    # The quantity to order is the minimum of:
                    # 1. The calculated percentage-based trade quantity (qty_to_buy)
                    # 2. The quantity needed to not exceed the max position limit (qty_to_add)
                    final_qty = min(qty_to_buy, qty_to_add)

                    if current_qty == 0 and final_qty > 0:
                        print(f"[{sym}] Signal: BUY ({reason}). Calculated quantity: {final_qty} shares @ ${current_price:.2f}")
                        # Place a market order for the calculated quantity
                        api.submit_order(sym, final_qty, 'buy', 'market', 'day')
                        print(f"[{sym}] Order submitted: BUY {final_qty} shares")
                    elif current_qty > 0 and final_qty > 0:
                        # Optional: Add pyramiding logic here if desired, currently it's for initial entry
                        print(f"[{sym}] Signal: BUY ({reason}). Already in position with {current_qty} shares.")
                    
                elif signal == "sell" and positions.get(sym, 0) > 0:
                    # Sell the entire position
                    qty_to_sell = positions[sym]
                    print(f"[{sym}] Signal: SELL ({reason}). Calculated quantity: {qty_to_sell} shares @ ${current_price:.2f}")
                    # Place a market order to close the position
                    api.submit_order(sym, qty_to_sell, 'sell', 'market', 'day')
                    print(f"[{sym}] Order submitted: SELL {qty_to_sell} shares")
                    
                else:
                    # No signal or no action needed
                    pass
            else:
                # --- FIX: Check if signal is None before calling .upper() ---
                if signal: 
                    print(f"[{sym}] Signal: {signal.upper()} ({reason}) [Regular Market Closed, NOT placing entry order]")
                else:
                    print(f"[{sym}] No Trading Signal ({reason}) [Regular Market Closed, NOT placing entry order]")
            
            # --- Stop-Loss/Take-Profit Monitoring (Simplified Example) ---
            if sym in positions:
                pos = api.get_position(sym)
                unrealized_plpc = float(pos.unrealized_plpc) # Unrealized P/L percentage
                
                # Check for Stop Loss
                if unrealized_plpc <= -cfg["stop_loss_pct"]:
                    qty_to_close = positions[sym]
                    print(f"[{sym}] ⚠️ STOP LOSS TRIGGERED! P/L: {unrealized_plpc*100:.2f}%. Closing {qty_to_close} shares.")
                    api.submit_order(sym, qty_to_close, 'sell', 'market', 'day')
                
                # Check for Take Profit
                elif unrealized_plpc >= cfg["take_profit_pct"]:
                    qty_to_close = positions[sym]
                    print(f"[{sym}] 💰 TAKE PROFIT TRIGGERED! P/L: {unrealized_plpc*100:.2f}%. Closing {qty_to_close} shares.")
                    api.submit_order(sym, qty_to_close, 'sell', 'market', 'day')

        except Exception as e:
            # This block now correctly catches any other errors that might occur
            print(f"[{sym}] ❌ Error processing symbol: {e}")


# --- Initialize accounts ---
accounts = []
for i in [1, 2]:
    key = os.getenv(f"APCA_API_KEY_{i}")
    secret = os.getenv(f"APCA_API_SECRET_{i}")
    base = os.getenv(f"APCA_BASE_URL_{i}") or "https://paper-api.alpaca.markets"

    if not key or not secret:
        print(f"API key/secret missing for account {i}, skipping.")
        continue

    try:
        api = REST(key, secret, base)
        api.get_account() # Test connection
        print(f"Account {i} connected successfully")
        accounts.append({
            "name": f"PaperAccount{i}",
            "api": api,
            "symbols": cfg["symbols"] # Use all symbols from config for now
        })
    except Exception as e:
        print(f"Failed to connect to Account {i}: {e}. Skipping.")


# --- Run the main logic for all connected accounts ---
if not accounts:
    print("No accounts connected. Exiting.")
else:
    for account_data in accounts:
        trade_strategy(account_data["name"], account_data["api"], account_data["symbols"])
    
    print("\nTrading bot run complete.")
