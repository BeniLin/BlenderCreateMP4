"""測試用的最小 PNG 解碼器（8-bit RGB/RGBA、非交錯），只用標準函式庫。"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


class Image:
    def __init__(self, width: int, height: int, channels: int, data: bytes):
        self.width, self.height, self.channels, self.data = width, height, channels, data

    def pixel(self, x: float, y: float) -> tuple[int, ...]:
        xi, yi = int(x), int(y)
        if not (0 <= xi < self.width and 0 <= yi < self.height):
            raise IndexError((x, y))
        i = (yi * self.width + xi) * self.channels
        return tuple(self.data[i:i + 3])


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path: Path) -> Image:
    blob = Path(path).read_bytes()
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    width = height = channels = 0
    while pos < len(blob):
        length, tag = struct.unpack(">I4s", blob[pos:pos + 8])
        body = blob[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and ctype in (2, 6) and interlace == 0, (depth, ctype, interlace)
            channels = 3 if ctype == 2 else 4
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    for y in range(height):
        ftype = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ftype == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ftype == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return Image(width, height, channels, bytes(out))
