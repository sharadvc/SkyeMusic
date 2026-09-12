"""Unit tests for macOS System Notifications & Album Art Banner Integration."""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from tune.notify import _download_artwork, notify_track
from tune.queue import Track


class TestNotify(unittest.TestCase):
    @patch("tune.notify._download_artwork")
    def test_notify_track(self, mock_art):
        mock_art.return_value = None
        track = Track(query="test", title="Arijit Singh - Pal", url="https://www.youtube.com/watch?v=fX41N940bMU", duration=250, channel="T-Series")
        # Ensure notify_track does not crash
        notify_track(track)

    def test_notify_track_empty(self):
        # Empty track should safely return without exception
        notify_track(None)


if __name__ == "__main__":
    unittest.main()
