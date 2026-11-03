"""只用標準函式庫讀取 PNG 標頭（尺寸與色彩型態），不需要 Pillow。"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class PngInfo:
    width: int
    height: int
    bit_depth: int
    color_type: int  # 0 灰階, 2 RGB, 3 索引色, 4 灰階+alpha, 6 RGBA
    has_trns: bool

    @property
    def has_alpha(self) -> bool:
        return self.color_type in (4, 6) or self.has_trns


def read_png_info(path: Path) -> PngInfo:
    with open(path, "rb") as fh:
        if fh.read(8) != PNG_SIGNATURE:
            raise ValueError("不是 PNG 檔案")
        length, ctype = struct.unpack(">I4s", fh.read(8))
        if ctype != b"IHDR" or length != 13:
            raise ValueError("PNG 標頭損壞（缺少 IHDR）")
        width, height, bit_depth, color_type = struct.unpack(">IIBB", fh.read(10))
        fh.read(3 + 4)  # compression, filter, interlace, CRC
        has_trns = False
        while True:
            head = fh.read(8)
            if len(head) < 8:
                break
            length, ctype = struct.unpack(">I4s", head)
            if ctype == b"tRNS":
                has_trns = True
                break
            if ctype in (b"IDAT", b"IEND"):
                break
            fh.seek(length + 4, 1)
    if width == 0 or height == 0:
        raise ValueError("PNG 尺寸為 0")
    return PngInfo(width, height, bit_depth, color_type, has_trns)
