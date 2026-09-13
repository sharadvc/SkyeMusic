"""Unit tests for terminal QR code generator."""

from __future__ import annotations

import unittest

from tune.qr import QRCode, render_qr


class TestQRCode(unittest.TestCase):
    def test_qr_matrix_generation(self):
        qr = QRCode("http://localhost:8765")
        self.assertGreater(qr.size, 20)
        self.assertEqual(len(qr.matrix), qr.size)
        self.assertEqual(len(qr.matrix[0]), qr.size)

    def test_render_ascii_output(self):
        output_blocks = render_qr("http://192.168.1.17:8765", ansi=True)
        self.assertIn("\033[47m", output_blocks)
        self.assertIn("\033[40m", output_blocks)
        self.assertTrue(len(output_blocks.splitlines()) > 10)

        output_compact = render_qr("http://192.168.1.17:8765", ansi=True, compact=True)
        self.assertIn("\033[47;30m", output_compact)
        self.assertTrue(len(output_compact.splitlines()) > 10)

        output_plain = render_qr("http://192.168.1.17:8765", ansi=False)
        self.assertIn("█", output_plain)
        self.assertIn("▀", output_plain)
        self.assertTrue(len(output_plain.splitlines()) > 10)


if __name__ == "__main__":
    unittest.main()
