"""產生 Stage 1 範例用的 RGBA PNG 圖層（只用標準函式庫，不需要 Pillow）。

用法：
  python scripts/make_sample_layers.py [輸出目錄]
預設輸出到 examples/basic/layers/（相對於專案根目錄）。

圖層的顏色與尺寸是固定的，tests/test_render_integration.py 會用這些值檢查渲染結果。
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "examples" / "basic" / "layers"

# 給整合測試參考的顏色
BG_TOP = (20, 40, 90)
BG_BOTTOM = (90, 150, 200)
SUN = (255, 196, 40)
STAR = (0, 220, 255)
BAR = (230, 60, 160)
TITLE = (240, 110, 40)
CLOUD = (255, 255, 255)
CLOUD_ALPHA = 0.85


# ---------------------------------------------------------------- PNG writer

def write_png(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    """rows：每列 width*4 bytes 的 RGBA（straight alpha）。"""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def render_coverage(width: int, height: int, inside, color, alpha_scale: float = 1.0, ss: int = 4) -> list[bytes]:
    """以 ss×ss 超取樣計算覆蓋率（抗鋸齒），回傳 RGBA 列。inside(x, y) 使用像素座標。"""
    r, g, b = color
    rows = []
    offsets = [(i + 0.5) / ss for i in range(ss)]
    total = ss * ss
    for y in range(height):
        row = bytearray()
        for x in range(width):
            hits = sum(1 for oy in offsets for ox in offsets if inside(x + ox, y + oy))
            a = round(255 * alpha_scale * hits / total)
            row += bytes((r, g, b, a)) if a else b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return rows


# ---------------------------------------------------------------- shapes

def background(w=1280, h=720):
    rows = []
    for y in range(h):
        t = y / (h - 1)
        c = bytes(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)) + b"\xff"
        rows.append(c * w)
    return w, h, rows


def sun(size=200):
    r = size / 2
    return size, size, render_coverage(size, size, lambda x, y: (x - r) ** 2 + (y - r) ** 2 <= r * r, SUN)


def star(size=160, outer=78, inner=32):
    c = size / 2
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = outer if i % 2 == 0 else inner
        pts.append((c + rad * math.cos(ang), c + rad * math.sin(ang)))

    def inside(x, y):
        hit = False
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
        return hit
    return size, size, render_coverage(size, size, inside, STAR)


def rounded_rect(w, h, radius):
    def inside(x, y):
        cx = min(max(x, radius), w - radius)
        cy = min(max(y, radius), h - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius
    return inside


def bar(w=280, h=40):
    return w, h, render_coverage(w, h, rounded_rect(w, h, 12), BAR)


# 5×7 點陣字（只需要 "STAGE 1"）
GLYPHS = {
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    " ": ["00000"] * 7,
}


def title(w=600, h=110, text="STAGE 1", cell=8):
    rows = render_coverage(w, h, rounded_rect(w, h, 20), TITLE, ss=2)
    text_w = len(text) * 6 * cell - cell
    x0, y0 = (w - text_w) // 2, (h - 7 * cell) // 2
    buf = [bytearray(r) for r in rows]
    for i, ch in enumerate(text):
        for gy, line in enumerate(GLYPHS[ch]):
            for gx, bit in enumerate(line):
                if bit != "1":
                    continue
                for py in range(cell):
                    for px in range(cell):
                        x = x0 + (i * 6 + gx) * cell + px
                        y = y0 + gy * cell + py
                        buf[y][x * 4:x * 4 + 4] = b"\xff\xff\xff\xff"
    return w, h, [bytes(r) for r in buf]


def cloud(w=300, h=150):
    def inside(x, y):
        return ((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2 <= 1
    return w, h, render_coverage(w, h, inside, CLOUD, alpha_scale=CLOUD_ALPHA, ss=2)


LAYERS = {
    "background": background,
    "cloud": cloud,
    "sun": sun,
    "bar": bar,
    "star": star,
    "title": title,
}


def main(argv: list[str]) -> int:
    out = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in LAYERS.items():
        w, h, rows = fn()
        write_png(out / f"{name}.png", w, h, rows)
        print(f"{out / (name + '.png')}  {w}x{h}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
