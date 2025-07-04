import unittest
from unittest.mock import patch, MagicMock, mock_open
from datetime import datetime

# Import your log_trade function and constants
from trading_bot import log_trade, TRADE_LOG_FILE

class TestLogging(unittest.TestCase):

    @patch("trading_bot.get_sheet")
    def test_google_sheet_logging(self, mock_get_sheet):
        mock_sheet = MagicMock()
        mock_get_sheet.return_value = mock_sheet

        log_trade("buy", 1000, 1.2345, 20.0, 1000.0)
        self.assertTrue(mock_sheet.append_row.called)
        args = mock_sheet.append_row.call_args[0][0]
        self.assertEqual(args[1:], ["buy", 1000, 1.2345, 20.0, 1000.0])

    @patch("builtins.open", new_callable=mock_open)
    @patch("os.path.exists", return_value=True)
    def test_csv_logging(self, mock_exists, mock_file):
        log_trade("sell", -1000, 1.3000, 20.0, 980.0)
        mock_file.assert_called_with(TRADE_LOG_FILE, "a", newline="")
        handle = mock_file()
        writerow_call = handle.write.call_args_list
        self.assertTrue(len(writerow_call) > 0)

if __name__ == "__main__":
    unittest.main()