import os
import csv
import time
import json
import base64
import requests
import schedule
import pandas as pd
from datetime import datetime
from flask import Flask, request, jsonify

import oandapyV20
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === Env vars ===
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GOOGLE_SERVICE_ACCOUNT_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "ForexTradeLog")

TRADE_LOG_FILE = "trade_log.csv"

# === Flask app ===
app = Flask(__name__)

# === OANDA client ===
client = oandapyV20.API(access_token=OANDA_API_KEY)

# === Google Sheets auth ===
def get_sheet():
    if not GOOGLE_SERVICE_ACCOUNT_B64:
        raise Exception("Missing GOOGLE_SERVICE_ACCOUNT_B64 environment variable")
    decoded = base64.b64decode(GOOGLE_SERVICE_ACCOUNT_B64).decode("utf-8")
    service_account_info = json.loads(decoded)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    client_gs = gspread.authorize(creds)
    sheet = client_gs.open(GOOGLE_SHEET_NAME).sheet1
    return sheet

# === Logging ===
def log_trade(action, units, price, risk_amount, balance, instrument):
    timestamp = datetime.utcnow().isoformat()
    row = [timestamp, instrument, action, units, price, risk_amount, balance]

    # Google Sheets
    try:
        sheet = get_sheet()
        sheet.append_row(row)
        print("Logged to Google Sheets")
    except Exception as e:
        print(f"Failed to log to Google Sheets: {e}")

    # CSV fallback + header check
    try:
        if not os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "instrument", "action", "units", "entry_price", "risk_amount", "balance"])

        with open(TRADE_LOG_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        print("Logged to CSV")
    except Exception as e:
        print(f"Failed to write to CSV: {e}")

# === Discord alert ===
last_alert_time = 0
def send_discord_alert(message):
    global last_alert_time
    now = time.time()
    if now - last_alert_time < 2:
        print("Skipping Discord alert to avoid rate limit.")
        return
    last_alert_time = now
    if not DISCORD_WEBHOOK_URL:
        print("No Discord webhook URL set.")
        return
    payload = {
        "embeds": [{
            "title": "📈 Forex Trade Bot Alert",
            "description": message,
            "color": 5814783
        }]
    }
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print(f"Discord alert sent, status: {response.status_code}")
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

# === Get account balance ===
def get_account_balance():
    url = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return float(r.json()["account"]["balance"])
    else:
        print(f"Failed to get balance: {r.text}")
        return 1000.0  # fallback balance

# === Place trade ===
def place_trade(action, stop_loss_pips, entry_price, risk_reward_ratio, instrument):
    balance = get_account_balance()
    print(f"Account balance: {balance}")
    risk_percent = 0.02
    risk_amount = balance * risk_percent
    pip_value = 0.0001  # Approx pip value for most pairs
    if instrument in ["USD_JPY", "EUR_JPY", "GBP_JPY"]:
        pip_value = 0.01  # Adjust pip value for JPY pairs
    pip_risk = stop_loss_pips * pip_value
    if pip_risk == 0:
        print("Invalid stop loss pips (0), skipping trade")
        return False
    units = int(risk_amount / pip_risk)
    print(f"Calculated units: {units}")

    if action.lower() == "sell":
        units = -abs(units)
        stop_loss_price = round(entry_price + pip_risk, 5)
        take_profit_price = round(entry_price - (pip_risk * risk_reward_ratio), 5)
    else:
        units = abs(units)
        stop_loss_price = round(entry_price - pip_risk, 5)
        take_profit_price = round(entry_price + (pip_risk * risk_reward_ratio), 5)

    print(f"Placing {action.upper()} order for {instrument}: units={units}, SL={stop_loss_price}, TP={take_profit_price}")

    data = {
        "order": {
            "instrument": instrument,
            "units": str(units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": str(stop_loss_price)},
            "takeProfitOnFill": {"price": str(take_profit_price)}
        }
    }

    try:
        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=data)
        response = client.request(r)
        print("Order response:", response)
        log_trade(action, units, entry_price, risk_amount, balance, instrument)
        send_discord_alert(f"Executed {action.upper()} {units} units on {instrument} at {entry_price}, SL: {stop_loss_price}, TP: {take_profit_price}")
        return True
    except Exception as e:
        print("OANDA request failed:", e)
        send_discord_alert(f"Trade failed: {e}")
        return False

# === Fetch candles ===
def fetch_candles(instrument, granularity="M5", count=50):
    url = f"https://api-fxpractice.oanda.com/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    params = {"granularity": granularity, "count": count, "price": "M"}
    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200:
        print(f"Failed to fetch candles for {instrument}: {r.text}")
        return None
    data = r.json()
    candles = data["candles"]
    prices = [float(c["mid"]["c"]) for c in candles if c["complete"]]
    times = [c["time"] for c in candles if c["complete"]]
    df = pd.DataFrame({"time": pd.to_datetime(times), "close": prices})
    return df

# === Technical indicators & signals ===
def moving_average_crossover_signal(df, short=5, long=20):
    if df is None or len(df) < long:
        return None
    df["short_ma"] = df["close"].rolling(short).mean()
    df["long_ma"] = df["close"].rolling(long).mean()
    if df["short_ma"].iloc[-2] < df["long_ma"].iloc[-2] and df["short_ma"].iloc[-1] > df["long_ma"].iloc[-1]:
        return "buy"
    elif df["short_ma"].iloc[-2] > df["long_ma"].iloc[-2] and df["short_ma"].iloc[-1] < df["long_ma"].iloc[-1]:
        return "sell"
    return None

def rsi_signal(df, period=14, oversold=30, overbought=70):
    if df is None or len(df) < period + 1:
        return None
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    if rsi.iloc[-2] < oversold and rsi.iloc[-1] > oversold:
        return "buy"
    elif rsi.iloc[-2] > overbought and rsi.iloc[-1] < overbought:
        return "sell"
    return None

def combined_signal(df):
    ma_signal = moving_average_crossover_signal(df)
    rsi_sig = rsi_signal(df)
    # Prioritize MA crossover; fallback to RSI if no MA signal
    return ma_signal if ma_signal else rsi_sig

# === Get open trades ===
def get_open_trades():
    r = trades.OpenTrades(OANDA_ACCOUNT_ID)
    response = client.request(r)
    return response.get("trades", [])

# === Modify trailing stop ===
def modify_trailing_stop(trade_id, new_stop_price):
    data = {"stopLoss": {"price": str(new_stop_price)}}
    r = trades.TradeCRCDO(OANDA_ACCOUNT_ID, trade_id, data=data)
    response = client.request(r)
    return response

# === Strategy loop ===
def strategy_loop():
    print(f"[{datetime.utcnow()}] Running strategy check...")

    pairs = ["USD_CAD", "EUR_USD"]
    stop_loss_pips = 25
    risk_reward_ratio = 2

    for instrument in pairs:
        df = fetch_candles(instrument)
        if df is None:
            print(f"No data for {instrument}, skipping...")
            continue

        signal = combined_signal(df)
        current_price = df["close"].iloc[-1]

        if signal:
            print(f"Signal for {instrument}: {signal} at price {current_price}")
            place_trade(signal, stop_loss_pips, current_price, risk_reward_ratio, instrument)

    # Manage trailing stops for all open trades
    open_trades = get_open_trades()
    for trade in open_trades:
        units = int(trade["currentUnits"])
        trade_id = trade["id"]
        instrument = trade["instrument"]

        current_stop = None
        if "stopLossOrder" in trade and trade["stopLossOrder"]:
            current_stop = float(trade["stopLossOrder"]["price"])

        # Fetch current price for trade instrument
        df = fetch_candles(instrument, count=1)
        current_price = float(df["close"].iloc[-1]) if df is not None else float(trade["price"])

        trail_pips = 0.0010  # 10 pips trailing, adjust if needed

        if units > 0:
            new_stop = max(current_stop if current_stop else 0, current_price - trail_pips)
        else:
            new_stop = min(current_stop if current_stop else 1e10, current_price + trail_pips)

        if current_stop is None or (units > 0 and new_stop > current_stop) or (units < 0 and new_stop < current_stop):
            try:
                print(f"Updating trailing stop for trade {trade_id} to {new_stop}")
                modify_trailing_stop(trade_id, new_stop)
            except Exception as e:
                print(f"Failed to update trailing stop for trade {trade_id}: {e}")

# === Flask routes ===
@app.route('/', methods=['GET'])
def index():
    return "Trading bot is live!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    print("Webhook triggered")
    data = request.get_json()
    print("Payload:", data)
    try:
        action = data["action"]
        stop_loss_pips = float(data["stop_loss_pips"])
        entry_price = float(data["entry_price"])
        risk_reward_ratio = float(data.get("risk_reward", 2))
        instrument = data.get("instrument", "EUR_USD")  # default EUR_USD if not specified
        success = place_trade(action, stop_loss_pips, entry_price, risk_reward_ratio, instrument)
        return jsonify({"status": "success" if success else "fail"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("Error in webhook:", e)
        return jsonify({"status": "error", "message": str(e)})

@app.route('/test', methods=['POST'])
def test():
    data = request.get_json()
    print("Test endpoint received:", data)
    return jsonify({"status": "ok", "received": data})

# === Scheduler setup ===
def run_scheduler():
    schedule.every(5).minutes.do(strategy_loop)
    print("Scheduler started, running strategy every 5 minutes...")
    while True:
        schedule.run_pending()
        time.sleep(1)

# === Run ===
if __name__ == "__main__":
    import threading

    port = int(os.getenv("PORT", 10000))  # Default to 10000 if PORT not set

    # Start scheduler thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

    # Run Flask app
    app.run(host="0.0.0.0", port=port)
