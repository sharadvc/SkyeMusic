"""Unit tests for Global Public Tunnel Engine."""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from tune.tunnel import get_active_tunnel_url, stop_tunnel


class TestTunnel(unittest.TestCase):
    def setUp(self):
        stop_tunnel()

    def test_tunnel_initial_state(self):
        self.assertIsNone(get_active_tunnel_url())

    def test_stop_tunnel_idempotent(self):
        stop_tunnel()
        self.assertIsNone(get_active_tunnel_url())


if __name__ == "__main__":
    unittest.main()
