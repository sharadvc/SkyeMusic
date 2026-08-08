import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests import _env  # noqa: E402
from tune.config import DEFAULTS, Config  # noqa: E402


class TestConfig(unittest.TestCase):
    def setUp(self):
        _env.clean()  # don't leak the config file into other test modules

    def test_defaults_present(self):
        c = Config()
        self.assertEqual(c.get("volume"), 80)
        self.assertEqual(c.get("repeat"), "off")
        self.assertEqual(c.get("theme"), "default")

    def test_set_get_roundtrip(self):
        c = Config()
        c.set("volume", 60)
        c2 = Config()
        self.assertEqual(c2.get("volume"), 60)

    def test_new_keys_defaults(self):
        # the feature keys added over time must exist with safe defaults
        self.assertIn("media_keys", DEFAULTS)
        self.assertIn("smart_queue", DEFAULTS)
        self.assertIn("resume", DEFAULTS)
        self.assertIn("intro_skip", DEFAULTS)
        self.assertIn("listenbrainz_token", DEFAULTS)
        self.assertIn("remote_pin", DEFAULTS)
        self.assertFalse(DEFAULTS["smart_queue"])
        self.assertFalse(DEFAULTS["resume"])

    def test_set_stores_raw(self):
        # Config.set stores what it's given; the daemon's _h_config coerces
        # types based on the existing value (covered in test_daemon).
        c = Config()
        c.set("autoplay", "on")
        self.assertEqual(c.get("autoplay"), "on")


if __name__ == "__main__":
    unittest.main()
