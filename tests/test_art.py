import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tune.art import _bmp_rows  # noqa: E402


def make_bmp(width, height, top_down=False, truncate=0):
    row = width * 3
    row_size = ((row + 3) // 4) * 4
    data = bytearray(54 + row_size * abs(height))
    data[0:2] = b"BM"
    data[10:14] = (54).to_bytes(4, "little")
    data[14:18] = (40).to_bytes(4, "little")
    data[18:22] = width.to_bytes(4, "little")
    data[22:26] = (height if not top_down else -height).to_bytes(4, "little", signed=True)
    data[28:30] = (24).to_bytes(2, "little")
    return bytes(data[: len(data) - truncate])


class TestBmpRows(unittest.TestCase):
    def test_parses_2x2(self):
        with tempfile.NamedTemporaryFile(suffix=".bmp") as f:
            f.write(make_bmp(2, 2))
            f.flush()
            rows = _bmp_rows(f.name)
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 2)

    def test_top_down(self):
        with tempfile.NamedTemporaryFile(suffix=".bmp") as f:
            f.write(make_bmp(1, 1, top_down=True))
            f.flush()
            rows = _bmp_rows(f.name)
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

    def test_truncated_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".bmp") as f:
            f.write(make_bmp(2, 2, truncate=10))
            f.flush()
            self.assertIsNone(_bmp_rows(f.name))

    def test_empty_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix=".bmp") as f:
            f.write(b"BM")
            f.flush()
            self.assertIsNone(_bmp_rows(f.name))

    def test_missing_file_returns_none(self):
        self.assertIsNone(_bmp_rows("/nonexistent/art.bmp"))


if __name__ == "__main__":
    unittest.main()
