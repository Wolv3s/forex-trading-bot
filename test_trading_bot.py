import unittest
from unittest.mock import patch, MagicMock
import json
from trading_bot import app

class TradingBotTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_test_endpoint(self):
        payload = {"foo": "bar"}
        response = self.app.post('/test', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['received'], payload)

    @patch('trading_bot.oandapyV20.API.request')
    @patch('trading_bot.get_account_balance', return_value=1000.0)
    def test_webhook_buy(self, mock_balance, mock_request):
        # Mock OANDA API response for placing order
        mock_request.return_value = {"orderCreateTransaction": {"id": "12345", "type": "ORDER_CREATE"}}
        payload = {
            "action": "buy",
            "stop_loss_pips": 20,
            "entry_price": 1.2345,
            "risk_reward": 2
        }
        response = self.app.post('/webhook', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'success')

    @patch('trading_bot.oandapyV20.API.request')
    @patch('trading_bot.get_account_balance', return_value=1000.0)
    def test_webhook_sell(self, mock_balance, mock_request):
        mock_request.return_value = {"orderCreateTransaction": {"id": "67890", "type": "ORDER_CREATE"}}
        payload = {
            "action": "sell",
            "stop_loss_pips": 15,
            "entry_price": 1.3456,
            "risk_reward": 2.5
        }
        response = self.app.post('/webhook', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['status'], 'success')

if __name__ == '__main__':
    unittest.main()