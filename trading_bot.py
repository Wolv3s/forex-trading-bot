import os
import csv
import requests
from flask import Flask, request, jsonify
import oandapyV20
import oandapyV20.endpoints.orders as orders
from datetime import datetime

# Load environment variables (used by Render or .env)
OANDA_API_KEY = os.getenv("fe02e2de8aaf63ad2b70fe82b4f4c0b1-b79bbe3f369526f422a235a26e96e1f2")
OANDA_ACCOUNT_ID = os.getenv("101-001-35728007-001")
DISCORD_WEBHOOK_URL = os.getenv("https://discord.com/api/webhooks/1390395416130093198/olHo5LAWWW-vz0JZr25vmrjQ1ZTAdL7B_5yRHl0R0uI7TVAUNtd0ZdErt17a57vSZU30")

app = Flask(__name__)

# Trade log file
TRADE_LOG_FILE = "trade_log.csv"

# Ensure trade log exists with headers
if not os.path.exists(TRADE_LOG_FILE):
    with open(TRADE_LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "action", "units", "price", "risk_amount", "balance"])

# Get live account balance
def get_account_balance():
    client = oandapyV20.API(access_token=OANDA_API_KEY)
    url = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
    r = requests.get(url, headers={"Authorization": f"Bearer {OANDA_API_KEY}"})
    if r.status_code == 200:
        return float(r.json()["account"]["balance"])
    return 1000.0  # fallback

# Send Discord alert
def send_discord_alert(message):
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message})

# Create and submit trade
def place_trade(action, stop_loss_pips, entry_price):
    client = oandapyV20.API(access_token=OANDA_API_KEY)
    balance = get_account_balance()
    risk_percent = 0.02
    risk_amount = balance * risk_percent
    pip_value = 0.0001  # for GBPUSD
    units = int(risk_amount / (stop_loss_pips * pip_value))

    if action.startswith("SELL"):
        units = -abs(units)

    if action == "buy":
    stop_loss_price = round(entry_price - pip_risk, 5)
    take_profit_price = round(entry_price + (pip_risk * risk_reward_ratio), 5)
    else:  # sell
    stop_loss_price = round(entry_price + pip_risk, 5)
    take_profit_price = round(entry_price - (pip_risk * risk_reward_ratio), 5)

    data = {
        "order": {
            "instrument": "GBP_USD",
            "units": str(units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "distance": f"{stop_loss_pips * pip_value:.5f}"
            }
        }
    }

    try:
        r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=data)
        client.request(r)
        price = entry_price
        log_trade(action, units, price, risk_amount, balance)
        send_discord_alert(f"Executed {action} with {units} units at {price}, SL: {stop_loss_pips} pips")
        return True
    except Exception as e:
        send_discord_alert(f"Trade failed: {e}")
        return False

# Log trade to CSV
def log_trade(action, units, price, risk_amount, balance):
    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([datetime.utcnow().isoformat(), action, units, price, risk_amount, balance])

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("Received webhook:", data)

    try:
        action = data["action"]
        stop_loss_pips = data["stop_loss_pips"]
        entry_price = data["entry_price"]
        success = place_trade(action, stop_loss_pips, entry_price)
        return jsonify({"status": "success" if success else "fail"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
