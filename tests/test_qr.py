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
        output = render_qr("http://192.168.1.17:8765")
        self.assertIn("█", output)
        self.assertIn("▀", output)
        self.assertTrue(len(output.splitlines()) > 10)


if __name__ == "__main__":
    unittest.main()
