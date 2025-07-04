import os
import csv
import requests
import base64
import json
import time
from flask import Flask, request, jsonify
import oandapyV20
import oandapyV20.endpoints.orders as orders
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Load environment variables
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRADE_LOG_FILE = "trade_log.csv"

app = Flask(__name__)

# Validate API credentials
if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    print("ERROR: Missing OANDA_API_KEY or OANDA_ACCOUNT_ID environment variables")
    exit(1)

# Get Google Sheet object
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    b64_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")
    if not b64_json:
        raise Exception("Missing GOOGLE_SERVICE_ACCOUNT_B64 environment variable")

    try:
        decoded = base64.b64decode(b64_json).decode("utf-8")
        service_account_info = json.loads(decoded)
    except Exception as e:
        raise Exception(f"Failed to decode service account JSON: {e}")

    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    client = gspread.authorize(creds)
    sheet = client.open(os.getenv("GOOGLE_SHEET_NAME", "ForexTradeLog")).sheet1
    return sheet

# Log trade to Google Sheets + CSV fallback
def log_trade(action, units, price, risk_amount, balance):
    timestamp = datetime.utcnow().isoformat()
    row = [timestamp, action, units, price, risk_amount, balance]

    # Google Sheets
    try:
        sheet = get_sheet()
        sheet.append_row(row)
        print("Logged to Google Sheets")
    except Exception as e:
        print(f"Failed to log to Google Sheets: {e}")

    # CSV header on first run
    if not os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "action", "units", "entry_price", "risk_amount", "balance"])

    # CSV fallback
    try:
        with open(TRADE_LOG_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        print("Logged to CSV")
    except Exception as e:
        print(f"Failed to write to CSV: {e}")

# Get current account balance
def get_account_balance():
    url = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return float(r.json()["account"]["balance"])
    else:
        print(f"Failed to get balance: {r.text}")
        return 1000.0  # fallback

# Trade execution logic
def place_trade(action, stop_loss_pips, entry_price, risk_reward_ratio):
    client = oandapyV20.API(access_token=OANDA_API_KEY)
    balance = get_account_balance()
    risk_percent = 0.02
    risk_amount = balance * risk_percent
    pip_value = 0.0001
    pip_risk = stop_loss_pips * pip_value
    units = int(risk_amount / pip_risk)

    if action.lower() == "sell":
        units = -abs(units)
        stop_loss_price = round(entry_price + pip_risk, 5)
        take_profit_price = round(entry_price - (pip_risk * risk_reward_ratio), 5)
    else:
        units = abs(units)
        stop_loss_price = round(entry_price - pip_risk, 5)
        take_profit_price = round(entry_price + (pip_risk * risk_reward_ratio), 5)

    data = {
        "order": {
            "instrument": "GBP_USD",
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
        log_trade(action, units, entry_price, risk_amount, balance)
        send_discord_alert(f"Executed {action.upper()} {units} units at {entry_price}, SL: {stop_loss_price}, TP: {take_profit_price}")
        return True
    except Exception as e:
        print("OANDA request failed:", e)
        send_discord_alert(f"Trade failed: {e}")
        return False

# Discord alert
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
            "title": "📈 Forex Trade Executed",
            "description": message,
            "color": 5814783
        }]
    }

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print("Discord alert sent, status:", response.status_code)
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

# Health check
@app.route('/', methods=['GET'])
def index():
    return "Trading bot is live!", 200

# Webhook entry point
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
        success = place_trade(action, stop_loss_pips, entry_price, risk_reward_ratio)
        return jsonify({"status": "success" if success else "fail"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})

# Test endpoint
@app.route('/test', methods=['POST'])
def test():
    data = request.get_json()
    print("Test endpoint received:", data)
    return jsonify({"status": "ok", "received": data})

# Run server
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
