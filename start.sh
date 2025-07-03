#!/bin/bash
export FLASK_APP=trading_bot.py
export FLASK_ENV=production
flask run --host=0.0.0.0 --port=8000
