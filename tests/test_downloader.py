"""Unit tests for offline audio downloader."""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from tune.downloader import (
    get_downloads_index,
    is_downloaded,
    get_local_path,
    save_download_meta,
    remove_download,
    download_track,
)
from tune.queue import Track


class TestDownloader(unittest.TestCase):
    @patch("tune.downloader.get_downloads_index")
    def test_is_downloaded(self, mock_idx):
        mock_idx.return_value = {
            "http://example.com/song": {"file_path": __file__}
        }
        self.assertTrue(is_downloaded("http://example.com/song"))
        self.assertFalse(is_downloaded("http://example.com/other"))
        self.assertIsNone(get_local_path("http://example.com/other"))
        self.assertEqual(get_local_path("http://example.com/song"), __file__)

    @patch("tune.downloader.is_downloaded")
    def test_download_track_already_downloaded(self, mock_is_dl):
        mock_is_dl.return_value = True
        track = Track(query="test", title="Test", url="http://example.com/song", duration=180, channel="Artist")
        cb = MagicMock()
        with patch("tune.downloader.get_local_path", return_value="/path/to/file.mp3"):
            download_track(track, on_complete=cb)
            cb.assert_called_once_with(True, "/path/to/file.mp3")


if __name__ == "__main__":
    unittest.main()
