from trading_bot import log_trade

# Manually simulate a trade log
log_trade(
    action="buy",
    units=1500,
    price=1.2650,
    risk_amount=30.0,
    balance=1000.0
)