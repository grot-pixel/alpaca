#!/usr/bin/env python3
"""
Intraday Scalp Bot - single-cycle (safe for scheduled CI runs)

Behavior:
- Uses 1-minute or 5-minute bars (configurable) for fast scalp signals
- SMA fast/slow + RSI signal
- Volatility & liquidity filter to reduce false signals
- Position sizing: qty = floor((equity * max_trade_pct) / price)
- Bracket orders with stop_loss_pct and take_profit_pct
- Single run and exit (safe for GitHub Actions)
"""
import os
import json
import csv
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
from alpaca_trade_api.rest import REST, TimeFrame, APIError

from utils import generate_signal, avg_volume_threshold

# Optional alerting
try:
    from email_alerts import send_alert
except Exception:
    def send_alert(subject, body):
        print("[alerts disabled]", subject)
        return False

# Load config
CONFIG_FILE = "config.json"
with open(CONFIG_FILE) as f:
    cfg = json.load(f)

TRADE_LOG = "trade_log.csv"

def log_trade(record: dict):
    header = [
        "timestamp_utc", "account", "symbol", "side", "qty",
        "intended_value", "avg_fill_price", "stop_price", "take_profit_price",
        "notes"
    ]
    exists = os.path.exists(TRADE_LOG)
    with open(TRADE_LOG, "a", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=header)
        if not exists:
            writer.writeheader()
        writer.writerow(record)

def is_regular_hours():
    now = datetime.now(timezone.utc)
    # 9:30-16:00 ET -> UTC 13:30-21:00; approximating by hour to avoid timezone libs:
    return now.weekday() < 5 and 14 <= now.hour < 21

def fetch_bars(api, symbol, timeframe: TimeFrame, limit: int):
    try:
        bars = api.get_bars(symbol, timeframe, limit=limit).df
        return bars if not bars.empty else None
    except Exception as e:
        print(f"[{symbol}] get_bars error: {e}")
        return None

def total_open_exposure(api):
    """Return current total market value (float) of all open positions."""
    try:
        positions = api.list_positions()
        total = 0.0
        for p in positions:
            total += float(p.qty) * float(p.current_price)
        return total
    except Exception:
