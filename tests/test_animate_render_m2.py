"""S2-2 M2 整合測試：同一個物件的 move + scale + rotate + opacity 時間重疊 → Stage 1 渲染。

合成 job（tests/test_animate.py 的 make_job），ball-1 換成「上方 6 列是綠色標記」的方塊，才看得出旋轉方向。
邊緣會有重新取樣 / 反鋸齒，所以只檢查內部像素、中心、尺寸，不要求邊緣逐像素相同。
"""

from __future__ import annotations

import json

import pytest

import make_sample_layers as L
from pngread import read_png
from test_animate import BG_H, BG_W, make_job

pytestmark = pytest.mark.blender

RED = (230, 20, 20)
GREEN = (40, 200, 40)
BLUE = (20, 40, 230)
SIDE, PAD, MARK = 20, 2, 6           # 方塊 20×20、透明邊 2 px、上方 6 列是綠色標記
DX, SCALE, ANGLE, OPACITY = 60, 1.5, 90, 0.5
FIRST, MID, LAST = 1, 13, 30         # t = 0、0.4（move 剛好一半）、0.967 s（全部結束）


def _ball_rows():
    rows = []
    for y in range(SIDE + 2 * PAD):
        row = b""
        for x in range(SIDE + 2 * PAD):
            if PAD <= x < SIDE + PAD and PAD <= y < SIDE + PAD:
                row += bytes((*(GREEN if y < PAD + MARK else RED), 255))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(row)
    return rows


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    from animator.animate.edit import add_animation
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.pipeline import render
    from animator.tools import find_blender, find_ffmpeg

    base = tmp_path_factory.mktemp("anim_render_m2")
    try:
        cfg = load_config(None, {"paths.output_dir": str(base / "output"), "paths.temp_dir": str(base / "tmp")})
        find_blender(cfg)
        find_ffmpeg(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")
    job = make_job(cfg.path("output_dir"))
    L.write_png(job / "layers" / "ball-1.png", SIDE + 2 * PAD, SIDE + 2 * PAD, _ball_rows())
    spec = job / "spec.json"
    edits = [  # 四個通道，時間互相重疊
        add_animation(spec, "ball-1", "move", {"by": [DX, 0]}, start=0.0, duration=0.8),
        add_animation(spec, "ball-1", "scale", {"to": SCALE}, start=0.1, duration=0.7),
        add_animation(spec, "ball-1", "rotate", {"by": ANGLE}, start=0.2, duration=0.6),
        add_animation(spec, "ball-1", "opacity", {"to": OPACITY}, start=0.3, duration=0.5),
    ]
    result = render(spec, cfg, keep_frames=True, argv=["pytest"])
    return {"job": job, "edits": edits, "result": result,
            "frame": lambda n: read_png(result.frames_dir / f"frame_{n:04d}.png"),
            "background": read_png(job / "layers" / "background.png")}


def _diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b))


def _close(a, b, tol):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _extent(img, bg, xs, ys, threshold=30):
    """和背景差異 > threshold 的像素範圍（只看 xs × ys）。"""
    pts = [(x, y) for y in ys for x in xs if _diff(img.pixel(x, y), bg.pixel(x, y)) > threshold]
    assert pts, "找不到物件"
    return min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)


def _linear_blend(fg, bg, alpha):
    """Blender 在 linear 空間混合，再用 sRGB（Standard view transform）輸出。"""
    def to_lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def to_srgb(v):
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return round(v * 255)

    return tuple(to_srgb(alpha * to_lin(f) + (1 - alpha) * to_lin(b)) for f, b in zip(fg, bg))


BALL_XS, BALL_YS = range(0, 132), range(28, 101)       # ball-1 的活動範圍（box-1 在 x ≥ 136）


def test_spec_entries_and_render_valid(rendered):
    anims = [e.animation for e in rendered["edits"]]
    assert [(a["id"], a["effect"], a["params"]) for a in anims] == [
        ("ball-1-move-1", "move", {"by": [DX, 0]}), ("ball-1-scale-1", "scale", {"to": SCALE}),
        ("ball-1-rotate-1", "rotate", {"by": ANGLE}), ("ball-1-opacity-1", "opacity", {"to": OPACITY})]
    assert all("from" not in a["params"] and "easing" not in a for a in anims)
    ver = rendered["result"].report["verification"]
    assert ver["ok"] and ver["decodable"], ver
    assert (ver["actual"]["width"], ver["actual"]["height"], ver["actual"]["frames"]) == (BG_W, BG_H, 30)


def test_center_follows_move(rendered):
    bg = rendered["background"]
    for n, cx in ((FIRST, 40), (MID, 40 + DX / 2), (LAST, 40 + DX)):   # ease_in_out 在一半的時間剛好走一半
        x0, x1, y0, y1 = _extent(rendered["frame"](n), bg, BALL_XS, BALL_YS)
        assert abs((x0 + x1) / 2 - cx) <= 1.0, (n, x0, x1)
        assert abs((y0 + y1) / 2 - 64) <= 1.0, (n, y0, y1)              # pivot（anchor = 圖層中心）沒有偏移


def test_final_size_is_about_1_5x(rendered):
    bg = rendered["background"]
    x0, x1, y0, y1 = _extent(rendered["frame"](FIRST), bg, BALL_XS, BALL_YS)
    w0, h0 = x1 - x0 + 1, y1 - y0 + 1
    x0, x1, y0, y1 = _extent(rendered["frame"](LAST), bg, BALL_XS, BALL_YS)
    w1, h1 = x1 - x0 + 1, y1 - y0 + 1
    assert abs(w0 - SIDE) <= 1 and abs(h0 - SIDE) <= 1, (w0, h0)
    assert abs(w1 / w0 - SCALE) <= 0.1 and abs(h1 / h0 - SCALE) <= 0.1, (w0, h0, w1, h1)


def test_rotation_is_clockwise(rendered):
    first, last = rendered["frame"](FIRST), rendered["frame"](LAST)
    # 第 1 格：綠色標記在上方
    assert first.pixel(40, 64 - 8)[1] > first.pixel(40, 64 - 8)[0]
    assert first.pixel(40, 64 + 5)[0] > first.pixel(40, 64 + 5)[1]
    # 順時針 90°：上方的標記轉到右邊（逆時針會在左邊）
    cx = 40 + DX
    right, left = last.pixel(cx + 11, 64), last.pixel(cx - 11, 64)
    assert right[1] > right[0] + 40, right
    assert left[0] > left[1] + 40, left
    top, bottom = last.pixel(cx, 64 - 11), last.pixel(cx, 64 + 11)  # 上下都是紅色
    assert top[0] > top[1] and bottom[0] > bottom[1], (top, bottom)


def test_opaque_interior_is_50_percent_blend(rendered):
    last, bg = rendered["frame"](LAST), rendered["background"]
    for x, y in ((40 + DX - 7, 64), (40 + DX - 3, 60), (40 + DX - 3, 68)):   # 紅色區域內部（遠離邊緣）
        want = _linear_blend(RED, bg.pixel(x, y), OPACITY)
        got = last.pixel(x, y)
        srgb_space = tuple(round(OPACITY * f + (1 - OPACITY) * b) for f, b in zip(RED, bg.pixel(x, y)))
        assert _close(got, want, 3), (x, y, got, want, srgb_space)


def test_background_outside_animation_area_is_stable(rendered):
    bg = rendered["background"]
    frames = [rendered["frame"](n) for n in (FIRST, MID, LAST)]
    excluded = [(40 - 26, 64 - 26, 40 + DX + 26, 64 + 26),           # ball-1：移動 + 放大 + 旋轉掃過的範圍
                (150 - 14, 44 - 14, 150 + 14, 64 + 14)]               # box-1：既有的 move by [0, -20]
    worst, checked = 0, 0
    for y in range(BG_H):
        for x in range(BG_W):
            if any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in excluded):
                continue
            ref = bg.pixel(x, y)
            for img in frames:
                worst = max(worst, max(abs(a - b) for a, b in zip(img.pixel(x, y), ref)))
            checked += 1
    assert checked > BG_W * BG_H * 0.6, checked
    assert worst <= 1, worst


def test_box_is_unaffected(rendered):
    first, last = rendered["frame"](FIRST), rendered["frame"](LAST)
    inner = [(dx, dy) for dy in range(-8, 9) for dx in range(-8, 9)]
    assert all(_close(first.pixel(150 + dx, 64 + dy), BLUE, 2) for dx, dy in inner)
    assert all(_close(last.pixel(150 + dx, 44 + dy), BLUE, 2) for dx, dy in inner)   # 照它自己的動畫，完全不透明
    spec = json.loads((rendered["job"] / "spec.json").read_text(encoding="utf-8"))
    assert [a["id"] for a in spec["animations"] if a["target"] == "box-1"] == ["box-1-hand"]
