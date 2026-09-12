"""Pure-Python zero-dependency QR Code generator for terminal display."""

from __future__ import annotations

import math

# GF(256) Galois Field math with primitive polynomial 0x11d for Reed-Solomon
EXP_TABLE = [0] * 512
LOG_TABLE = [0] * 256

x = 1
for i in range(255):
    EXP_TABLE[i] = x
    LOG_TABLE[x] = i
    x <<= 1
    if x & 0x100:
        x ^= 0x11D
for i in range(255, 512):
    EXP_TABLE[i] = EXP_TABLE[i - 255]


def _gf_mul(x: int, y: int) -> int:
    if x == 0 or y == 0:
        return 0
    return EXP_TABLE[LOG_TABLE[x] + LOG_TABLE[y]]


def _rs_generator_poly(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        poly = _poly_mul(poly, [1, EXP_TABLE[i]])
    return poly


def _poly_mul(p1: list[int], p2: list[int]) -> list[int]:
    result = [0] * (len(p1) + len(p2) - 1)
    for i, c1 in enumerate(p1):
        for j, c2 in enumerate(p2):
            result[i + j] ^= _gf_mul(c1, c2)
    return result


def _rs_encode(data: bytes, num_ecc: int) -> list[int]:
    gen = _rs_generator_poly(num_ecc)
    msg = list(data) + [0] * num_ecc
    for i in range(len(data)):
        coef = msg[i]
        if coef != 0:
            for j in range(len(gen)):
                msg[i + j] ^= _gf_mul(gen[j], coef)
    return msg[len(data):]


# Format information bits for masks 0..7 with Medium EC level
# Precomputed 15-bit BCH(15,5) format codes for Medium error correction (bits 11=10)
FORMAT_CODES_M = [
    0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4B9F, 0x4EC8
]


class QRCode:
    def __init__(self, data: str):
        self.data_str = data
        self.data_bytes = data.encode("utf-8")
        # Select Version based on length (Byte mode, Medium EC level)
        n = len(self.data_bytes)
        if n <= 14:
            self.version = 1
            self.size = 21
            self.num_data = 16
            self.num_ecc = 10
        elif n <= 26:
            self.version = 2
            self.size = 25
            self.num_data = 28
            self.num_ecc = 16
        elif n <= 42:
            self.version = 3
            self.size = 29
            self.num_data = 44
            self.num_ecc = 26
        elif n <= 62:
            self.version = 4
            self.size = 33
            self.num_data = 64
            self.num_ecc = 36
        else:
            self.version = 5
            self.size = 37
            self.num_data = 86
            self.num_ecc = 48

        self.matrix = [[False] * self.size for _ in range(self.size)]
        self.is_reserved = [[False] * self.size for _ in range(self.size)]
        self._build()

    def _reserve(self, r: int, c: int, val: bool):
        self.matrix[r][c] = val
        self.is_reserved[r][c] = True

    def _draw_finder(self, r_top: int, c_left: int):
        for r in range(7):
            for c in range(7):
                is_black = (r == 0 or r == 6 or c == 0 or c == 6 or (2 <= r <= 4 and 2 <= c <= 4))
                self._reserve(r_top + r, c_left + c, is_black)
        # Quiet separators around finder
        for r in range(-1, 8):
            for c in range(-1, 8):
                if r == -1 or r == 7 or c == -1 or c == 7:
                    rr, cc = r_top + r, c_left + c
                    if 0 <= rr < self.size and 0 <= cc < self.size:
                        self._reserve(rr, cc, False)

    def _draw_alignment(self, r_center: int, c_center: int):
        for r in range(-2, 3):
            for c in range(-2, 3):
                if 0 <= r_center + r < self.size and 0 <= c_center + c < self.size:
                    if not self.is_reserved[r_center + r][c_center + c]:
                        is_black = (abs(r) == 2 or abs(c) == 2 or (r == 0 and c == 0))
                        self._reserve(r_center + r, c_center + c, is_black)

    def _build(self):
        # 1. Finder patterns
        self._draw_finder(0, 0)
        self._draw_finder(0, self.size - 7)
        self._draw_finder(self.size - 7, 0)

        # 2. Alignment patterns (version >= 2)
        if self.version >= 2:
            align_coords = {
                2: [18],
                3: [22],
                4: [26],
                5: [30],
            }.get(self.version, [])
            for r in align_coords:
                for c in align_coords:
                    # Don't overlap finders
                    if not (r < 9 and c < 9) and not (r < 9 and c > self.size - 9) and not (r > self.size - 9 and c < 9):
                        self._draw_alignment(r, c)

        # 3. Timing patterns
        for i in range(self.size):
            if not self.is_reserved[6][i]:
                self._reserve(6, i, i % 2 == 0)
            if not self.is_reserved[i][6]:
                self._reserve(i, 6, i % 2 == 0)

        # 4. Dark module & format reservation
        self._reserve(self.size - 8, 8, True)
        for i in range(9):
            if not self.is_reserved[8][i]:
                self.is_reserved[8][i] = True
            if not self.is_reserved[i][8]:
                self.is_reserved[i][8] = True
        for i in range(self.size - 8, self.size):
            if not self.is_reserved[8][i]:
                self.is_reserved[8][i] = True
            if not self.is_reserved[i][8]:
                self.is_reserved[i][8] = True

        # 5. Encode data stream (Byte mode = 0100)
        bits = []
        # Mode (0100)
        bits.extend([0, 1, 0, 0])
        # Count (8 bits for V1-V9 byte mode)
        length = len(self.data_bytes)
        for b in range(7, -1, -1):
            bits.append((length >> b) & 1)
        # Data bytes
        for byte_val in self.data_bytes:
            for b in range(7, -1, -1):
                bits.append((byte_val >> b) & 1)

        # Terminator
        max_bits = self.num_data * 8
        bits.extend([0] * min(4, max_bits - len(bits)))
        while len(bits) % 8 != 0:
            bits.append(0)
        # Pad bytes (0xEC, 0x11)
        pad_bytes = [0xEC, 0x11]
        pad_idx = 0
        while len(bits) < max_bits:
            p_val = pad_bytes[pad_idx % 2]
            for b in range(7, -1, -1):
                bits.append((p_val >> b) & 1)
            pad_idx += 1

        # Convert bits to byte stream for RS encoding
        data_buf = bytearray()
        for i in range(0, len(bits), 8):
            val = 0
            for b in range(8):
                val = (val << 1) | bits[i + b]
            data_buf.append(val)

        ecc_buf = _rs_encode(data_buf, self.num_ecc)
        full_stream = list(data_buf) + ecc_buf

        # Convert full stream back to bit list
        all_bits = []
        for b_val in full_stream:
            for b in range(7, -1, -1):
                all_bits.append((b_val >> b) & 1)

        # 6. Place bits into matrix (zigzag up & down right to left)
        bit_idx = 0
        col = self.size - 1
        upward = True
        while col > 0:
            if col == 6:  # Skip vertical timing column
                col -= 1
            for row in range(self.size - 1, -1, -1) if upward else range(self.size):
                for c_offset in range(2):
                    c = col - c_offset
                    if not self.is_reserved[row][c]:
                        bit_val = all_bits[bit_idx] if bit_idx < len(all_bits) else 0
                        self.matrix[row][c] = bool(bit_val)
                        bit_idx += 1
            col -= 2
            upward = not upward

        # 7. Apply Mask (Mask 0: (row + col) % 2 == 0)
        mask_idx = 0
        for r in range(self.size):
            for c in range(self.size):
                if not self.is_reserved[r][c]:
                    if (r + c) % 2 == 0:
                        self.matrix[r][c] = not self.matrix[r][c]

        # 8. Write format information
        fmt_code = FORMAT_CODES_M[mask_idx]
        fmt_bits = [(fmt_code >> i) & 1 for i in range(14, -1, -1)]

        # Top-left & top-right / bottom-left format locations
        coords_1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                    (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
        coords_2 = [(self.size - 1, 8), (self.size - 2, 8), (self.size - 3, 8), (self.size - 4, 8),
                    (self.size - 5, 8), (self.size - 6, 8), (self.size - 7, 8),
                    (8, self.size - 8), (8, self.size - 7), (8, self.size - 6), (8, self.size - 5),
                    (8, self.size - 4), (8, self.size - 3), (8, self.size - 2), (8, self.size - 1)]

        for bit, (r, c) in zip(fmt_bits, coords_1):
            self.matrix[r][c] = bool(bit)
        for bit, (r, c) in zip(fmt_bits, coords_2):
            self.matrix[r][c] = bool(bit)

    def render_ascii(self) -> str:
        """Render QR code using half-block Unicode characters (2 vertical rows per text line)."""
        lines = []
        margin = 2
        size = self.size
        # Add top margin
        blank_row = " " * (size + margin * 2)
        lines.append(blank_row)

        for r in range(0, size, 2):
            line_chars = [" " * margin]
            for c in range(size):
                top_black = self.matrix[r][c]
                bot_black = self.matrix[r + 1][c] if r + 1 < size else False

                if top_black and bot_black:
                    line_chars.append("█")
                elif top_black and not bot_black:
                    line_chars.append("▀")
                elif not top_black and bot_black:
                    line_chars.append("▄")
                else:
                    line_chars.append(" ")
            line_chars.append(" " * margin)
            lines.append("".join(line_chars))

        lines.append(blank_row)
        return "\n".join(lines)


def render_qr(data: str) -> str:
    """Generate and return terminal ASCII string for a QR code."""
    try:
        return QRCode(data).render_ascii()
    except Exception:
        return f"[QR Generation Failed for {data}]"
