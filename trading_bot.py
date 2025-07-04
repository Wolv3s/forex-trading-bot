import os
import csv
import requests
from flask import Flask, request, jsonify
import oandapyV20
import oandapyV20.endpoints.orders as orders
from datetime import datetime

# Load environment variables (used by Render or .env)
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

app = Flask(__name__)

# Check required env vars on startup
if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
    print("ERROR: Missing OANDA_API_KEY or OANDA_ACCOUNT_ID environment variables")
    exit(1)

# Trade log file
TRADE_LOG_FILE = "trade_log.csv"

# Ensure trade log exists with headers
if not os.path.exists(TRADE_LOG_FILE):
    with open(TRADE_LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "action", "units", "price", "risk_amount", "balance"])

# Get live account balance
def get_account_balance():
    url = f"https://api-fxpractice.oanda.com/v3/accounts/{OANDA_ACCOUNT_ID}"
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return float(r.json()["account"]["balance"])
    else:
        print(f"Failed to get balance: {r.text}")
        return 1000.0  # fallback balance

def place_trade(action, stop_loss_pips, entry_price, risk_reward_ratio):
    client = oandapyV20.API(access_token=OANDA_API_KEY)
    balance = get_account_balance()
    print(f"Account balance: {balance}")
    risk_percent = 0.02
    risk_amount = balance * risk_percent
    pip_value = 0.0001  # for GBPUSD
    pip_risk = stop_loss_pips * pip_value
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

    print(f"Stop loss price: {stop_loss_price}, Take profit price: {take_profit_price}")

    data = {
        "order": {
            "instrument": "GBP_USD",
            "units": str(units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "price": str(stop_loss_price)
            },
            "takeProfitOnFill": {
                "price": str(take_profit_price)
            }
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

def send_discord_alert(message):
    if DISCORD_WEBHOOK_URL:
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json={"content": message})
            print("Discord alert sent, status:", response.status_code)
        except Exception as e:
            print(f"Failed to send Discord alert: {e}")

def log_trade(action, units, price, risk_amount, balance):
    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([datetime.utcnow().isoformat(), action, units, price, risk_amount, balance])

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
        print("Error in webhook:", e)
        return jsonify({"status": "error", "message": str(e)})

# <-- Add the /test route here
@app.route('/test', methods=['POST'])
def test():
    data = request.get_json()
    print("Test endpoint received:", data)
    return jsonify({"status": "ok", "received": data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
