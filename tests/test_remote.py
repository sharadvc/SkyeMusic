import unittest
from pathlib import Path
from unittest.mock import MagicMock
from tune.remote import Handler, _EXTRA_TYPES

class TestRemoteHandler(unittest.TestCase):
    def test_extra_types(self):
        self.assertEqual(_EXTRA_TYPES[".js"], "application/javascript")
        self.assertEqual(_EXTRA_TYPES[".css"], "text/css")
        self.assertEqual(_EXTRA_TYPES[".html"], "text/html")

    def test_authorized_pin(self):
        handler = Handler.__new__(Handler)
        mock_daemon = MagicMock()
        mock_daemon.cfg.get.side_effect = lambda k: "123456" if k == "remote_pin" else ""
        Handler.daemon = mock_daemon

        self.assertTrue(handler._authorized("pin=123456"))
        self.assertTrue(handler._authorized("token=123456"))
        self.assertFalse(handler._authorized("pin=999999"))

if __name__ == "__main__":
    unittest.main()
