"""Stage 1 渲染畫質的回歸測試（Blender）：沒有變形的圖層要和原圖逐像素相同。

背景：Blender EEVEE 預設的 film filter（1.5 px）會在 linear 空間模糊畫面，照片類圖片平均差異 5~12、
紋理處偏亮（docs/stage1.md 第 4 節）。scene_setup.py 把 filter_size 設為 0 之後：
  1. 有紋理的圖層 → 逐像素相同
  2. 測試圖：1 px 棋盤對比、0~255 灰階、平塗色塊 → 完全保留
  3. 奇數尺寸、位置在 .5 px 的圖層 → 仍然逐像素相同
  4. 旋轉的圖層（有透明邊）→ 邊緣仍有反鋸齒（不是 Closest 那種階梯）
  5. 縮小 0.5 倍 → 接近理想的 2×2 平均（linear 空間）
（第 6 項「segment 的分層渲染 = numpy 疊圖」在 tests/test_segmentation.py，屬於 Stage 2。）

只用標準函式庫（不需要 numpy / Pillow）。執行：pytest -m blender
"""

from __future__ import annotations

import json
import random

import pytest

import make_sample_layers as L
from pngread import read_png

pytestmark = pytest.mark.blender

W, H = 640, 480


# ---------------------------------------------------------------- 測試圖（RGBA rows）

def noise_rows(w, h, seed=1):
    """照片類的紋理：平滑漸層 + 隨機雜訊（不透明）。"""
    rng = random.Random(seed)
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            base = (x * 255 // max(w - 1, 1), y * 255 // max(h - 1, 1), 128)
            row += bytes(max(0, min(255, c + rng.randint(-60, 60))) for c in base) + b"\xff"
        rows.append(bytes(row))
    return rows


PATCHES = [(0, 0, 0), (1, 1, 1), (16, 16, 16), (128, 128, 128), (250, 250, 250), (255, 255, 255),
           (255, 0, 0), (0, 255, 0), (0, 0, 255), (224, 172, 105), (30, 90, 160), (120, 40, 200)]


def chart_rows():
    """192×192：上 64 列 = 1 px 棋盤；中 64 列 = 0~255 灰階（每 0.75 px 一階 → 用 192 欄的 0~191 對應 0~254）；
    下 64 列 = 12 個 16×64 的平塗色塊。"""
    rows = []
    for y in range(192):
        row = bytearray()
        for x in range(192):
            if y < 64:
                v = 255 if (x + y) % 2 else 0
                row += bytes((v, v, v, 255))
            elif y < 128:
                v = round(x * 255 / 191)
                row += bytes((v, v, v, 255))
            else:
                row += bytes(PATCHES[x // 16]) + b"\xff"
        rows.append(bytes(row))
    return rows


def padded_square_rows(size=120, pad=2):
    rows = []
    for y in range(size + 2 * pad):
        inside_y = pad <= y < size + pad
        rows.append(b"".join(b"\xff\xff\xff\xff" if inside_y and pad <= x < size + pad else b"\x00\x00\x00\x00"
                             for x in range(size + 2 * pad)))
    return rows


# ---------------------------------------------------------------- 渲染

@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.tools import find_blender, find_ffmpeg

    base = tmp_path_factory.mktemp("fidelity")
    try:
        cfg = load_config(None, {"paths.output_dir": str(base / "output"), "paths.temp_dir": str(base / "tmp")})
        find_blender(cfg)
        find_ffmpeg(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")
    return cfg


def _render(cfg, d, name, layers, images):
    from animator.pipeline import render

    d.mkdir(parents=True, exist_ok=True)
    for fname, (w, h, rows) in images.items():
        L.write_png(d / fname, w, h, rows)
    spec = {"version": 1, "meta": {"name": name},
            "canvas": {"width": W, "height": H, "fps": 30, "duration": 1.0, "background_color": "#000000"},
            "layers": layers, "animations": []}
    (d / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    return read_png(render(d / "spec.json", cfg, frame=1, name=name, argv=["pytest"]).output)


@pytest.fixture(scope="module")
def sources():
    return {"noise": (200, 160, noise_rows(200, 160)), "chart": (192, 192, chart_rows()),
            "odd": (101, 77, noise_rows(101, 77, seed=2)), "square": (124, 124, padded_square_rows()),
            "half": (200, 160, noise_rows(200, 160, seed=3))}


@pytest.fixture(scope="module")
def identity(cfg, sources, tmp_path_factory):
    """沒有變形的圖層：noise 在 (110, 90)、chart 在 (440, 110)、odd（101×77）在 (110.5, 380.5)。"""
    layers = [{"id": "noise", "image": "noise.png", "z": 0, "position": [110, 90]},
              {"id": "chart", "image": "chart.png", "z": 1, "position": [440, 110]},
              {"id": "odd", "image": "odd.png", "z": 2, "position": [110.5, 380.5]}]
    imgs = {f"{k}.png": sources[k] for k in ("noise", "chart", "odd")}
    return _render(cfg, tmp_path_factory.mktemp("identity"), "fidelity-identity", layers, imgs)


@pytest.fixture(scope="module")
def transformed(cfg, sources, tmp_path_factory):
    """旋轉 20° 的白色方塊（2 px 透明邊）在 (160, 240)；縮小 0.5 倍的 noise 在 (480, 240)。"""
    layers = [{"id": "square", "image": "square.png", "z": 0, "position": [160, 240], "rotation": 20},
              {"id": "half", "image": "half.png", "z": 1, "position": [480, 240], "scale": 0.5}]
    imgs = {"square.png": sources["square"], "half.png": sources["half"]}
    return _render(cfg, tmp_path_factory.mktemp("transformed"), "fidelity-transform", layers, imgs)


def _layer_diff(img, src, x0, y0):
    """回傳 (最大差異, 平均差異)：src 的每個像素 vs 渲染結果的對應位置。"""
    w, h, rows = src
    worst, total = 0, 0
    for y in range(h):
        for x in range(w):
            sr = rows[y][x * 4:x * 4 + 3]
            rr = img.pixel(x0 + x, y0 + y)
            d = max(abs(a - b) for a, b in zip(sr, rr))
            worst, total = max(worst, d), total + d
    return worst, total / (w * h)


# ---------------------------------------------------------------- 1~3：沒有變形 → 逐像素相同

def test_textured_layer_is_pixel_exact(identity, sources):
    worst, mean = _layer_diff(identity, sources["noise"], 10, 10)
    assert worst <= 1 and mean < 0.05, (worst, mean)


def test_chart_checker_gradient_and_patches_preserved(identity, sources):
    worst, _ = _layer_diff(identity, sources["chart"], 344, 14)
    assert worst <= 1, worst
    # 1 px 棋盤的對比要保留（濾波模糊時會變成接近 128 的灰色）
    values = [identity.pixel(344 + x, 14 + y)[0] for y in range(8, 56) for x in range(8, 184)]
    assert min(values) <= 1 and max(values) >= 254
    assert sum(1 for v in values if 30 < v < 225) == 0


def test_odd_sized_layer_at_half_pixel_is_pixel_exact(identity, sources):
    # 101×77、中心 (110.5, 380.5) → 左上角 (60, 342)
    worst, mean = _layer_diff(identity, sources["odd"], 60, 342)
    assert worst <= 1 and mean < 0.05, (worst, mean)


# ---------------------------------------------------------------- 4~5：旋轉 / 縮放

def test_rotated_layer_edges_are_antialiased(transformed):
    """沿著旋轉方塊的邊緣，要有足夠的中間值像素（Closest 取樣時為 0，會呈現階梯）。"""
    edge, intermediate = 0, 0
    for y in range(150, 330):
        row = [transformed.pixel(x, y)[0] for x in range(60, 260)]
        for i in range(1, len(row)):
            if abs(row[i] - row[i - 1]) > 0:                    # 黑 ↔ 白的轉換附近
                edge += 1
                intermediate += 8 < row[i] < 247
    assert edge > 100
    assert intermediate / edge > 0.15, intermediate / edge


def test_half_scaled_layer_matches_linear_box_filter(transformed, sources):
    """縮小 0.5 倍：每個輸出像素 ≈ 2×2 原始像素在 linear 空間的平均（再轉回 sRGB）。"""
    w, h, rows = sources["half"]

    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    def srgb(v):
        v = v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return round(max(0.0, min(1.0, v)) * 255)

    x0, y0 = 480 - w // 4, 240 - h // 4
    total, n, worst = 0, 0, 0
    for oy in range(2, h // 2 - 2):
        for ox in range(2, w // 2 - 2):
            for c in range(3):
                avg = sum(lin(rows[2 * oy + dy][(2 * ox + dx) * 4 + c]) for dy in (0, 1) for dx in (0, 1)) / 4
                d = abs(transformed.pixel(x0 + ox, y0 + oy)[c] - srgb(avg))
                total, n, worst = total + d, n + 1, max(worst, d)
    assert total / n < 1.5, total / n
