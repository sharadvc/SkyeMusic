"""Unit tests for Multi-Room Party Session & Synchronization."""

from __future__ import annotations

import unittest
from tune.party import party_engine, PartySession


class TestPartySession(unittest.TestCase):
    def setUp(self):
        party_engine.stop()

    def test_party_lifecycle(self):
        self.assertFalse(party_engine.active)
        party_engine.start("123456")
        self.assertTrue(party_engine.active)
        self.assertEqual(party_engine.token, "123456")

        party_engine.register_listener("192.168.1.50")
        party_engine.register_listener("192.168.1.51")
        self.assertEqual(len(party_engine.listeners), 2)

        party_engine.unregister_listener("192.168.1.50")
        self.assertEqual(len(party_engine.listeners), 1)

        party_engine.stop()
        self.assertFalse(party_engine.active)
        self.assertEqual(len(party_engine.listeners), 0)


if __name__ == "__main__":
    unittest.main()
