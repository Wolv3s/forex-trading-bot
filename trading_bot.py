import os
import csv
import time
import json
import base64
import requests
import pandas as pd
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

import oandapyV20
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.trades as trades

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === Load environment variables ===
load_dotenv()
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
GOOGLE_SERVICE_ACCOUNT_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "ForexTradeLog")
INSTRUMENT = "GBP_USD"
TRADE_LOG_FILE = "trade_log.csv"

# === Flask app ===
app = Flask(__name__)
client = oandapyV20.API(access_token=OANDA_API_KEY)

# === Google Sheets Auth ===
def get_sheet():
    if not GOOGLE_SERVICE_ACCOUNT_B64:
        raise Exception("Missing GOOGLE_SERVICE_ACCOUNT_B64")
    decoded = base64.b64decode(GOOGLE_SERVICE_ACCOUNT_B64).decode("utf-8")
    service_account_info = json.loads(decoded)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    gclient = gspread.authorize(creds)
    return gclient.open(GOOGLE_SHEET_NAME).sheet1

# === Logging ===
def log_trade(action, units, price, risk_amount, balance):
    timestamp = datetime.utcnow().isoformat()
    row = [timestamp, action, units, price, risk_amount, balance]
    try:
        sheet = get_sheet()
        sheet.append_row(row)
        print("Logged to Google Sheets")
    except Exception as e:
        print(f"Google Sheets logging failed: {e}")

    try:
        if not os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, "w", newline="") as f:
                csv.writer(f).writerow(["timestamp", "action", "units", "entry_price", "risk_amount", "balance"])
        with open(TRADE_LOG_FILE, "a", newline="") as f:
            csv.writer(f).writerow(row)
        print("Logged to CSV")
    except Exception as e:
        print(f"CSV logging failed: {e}")

# === Discord Alert ===
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
            "title": "📈 Forex Trade Alert",
            "description": message,
            "color": 5814783
        }]
    }
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print(f"Discord alert status: {r.status_code}")
    except Exception as e:
        print(f"Discord alert failed: {e}")

# === OANDA Balance ===
def get_account_balance():
    try:
        r = requests.get(f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}",
                         headers={"Authorization": f"Bearer {OANDA_API_KEY}"})
        return float(r.json()["account"]["balance"])
    except Exception as e:
        print(f"Failed to get balance: {e}")
        return 1000.0

# === Place Trade ===
def place_trade(action, stop_loss_pips, entry_price, risk_reward_ratio, instrument=INSTRUMENT):
    print(f"Placing trade: action={action}, stop_loss_pips={stop_loss_pips}, entry_price={entry_price}, risk_reward_ratio={risk_reward_ratio}, instrument={instrument}")
    balance = get_account_balance()
    risk_percent = 0.02
    risk_amount = balance * risk_percent
    pip_value = 0.0001
    pip_risk = stop_loss_pips * pip_value
    if pip_risk == 0:
        print("Invalid stop_loss_pips (0), skipping trade.")
        return False
    units = int(risk_amount / pip_risk)

    if action.lower() == "sell":
        units = -abs(units)
        sl_price = round(entry_price + pip_risk, 5)
        tp_price = round(entry_price - pip_risk * risk_reward_ratio, 5)
    else:
        units = abs(units)
        sl_price = round(entry_price - pip_risk, 5)
        tp_price = round(entry_price + pip_risk * risk_reward_ratio, 5)

    data = {
        "order": {
            "instrument": instrument,
            "units": str(units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": str(sl_price)},
            "takeProfitOnFill": {"price": str(tp_price)}
        }
    }

    try:
        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=data)
        response = client.request(r)
        print("Order placed:", response)
        log_trade(action, units, entry_price, risk_amount, balance)
        send_discord_alert(f"{action.upper()} {units} units @ {entry_price} | SL: {sl_price}, TP: {tp_price}")
        return True
    except Exception as e:
        print("Trade execution failed:", e)
        send_discord_alert(f"Trade failed: {e}")
        return False

# === Webhook ===
@app.route('/webhook', methods=['POST'])
def webhook():
    print("Webhook triggered")
    data = request.get_json()
    print("Payload:", data)
    try:
        action = data["action"]
        stop_loss_pips = float(data["stop_loss_pips"])
        entry_price = float(data["entry_price"])
        risk_reward = float(data.get("risk_reward", 2))
        instrument = data.get("instrument", INSTRUMENT)
        success = place_trade(action, stop_loss_pips, entry_price, risk_reward, instrument)
        return jsonify({"status": "success" if success else "fail"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})

# === Test ===
@app.route('/test', methods=['POST'])
def test():
    data = request.get_json()
    print("Test received:", data)
    return jsonify({"status": "ok", "received": data})

# === Default route ===
@app.route('/', methods=['GET'])
def index():
    return "Trading bot is live", 200

# === Run Flask ===
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print("OANDA_ACCOUNT_ID:", repr(OANDA_ACCOUNT_ID))
    app.run(host="0.0.0.0", port=port)
