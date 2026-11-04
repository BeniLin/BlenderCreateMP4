"""S2-2 M1 整合測試：anim add move → Stage 1 渲染 → 只有目標物件移動，背景逐像素不變。

用標準函式庫產生的合成 job（tests/test_animate.py 的 make_job），不需要 AI 套件、不重跑分割。
"""

from __future__ import annotations

import pytest

from pngread import read_png
from test_animate import BG_H, BG_W, make_job

pytestmark = pytest.mark.blender

BALL = (230, 20, 20)
BOX = (20, 40, 230)
DX = 60                         # ball-1 的位移：中心 (40, 64) → (100, 64)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    from animator.animate.edit import add_move
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.pipeline import render
    from animator.tools import find_blender, find_ffmpeg

    base = tmp_path_factory.mktemp("anim_render")
    try:
        cfg = load_config(None, {"paths.output_dir": str(base / "output"), "paths.temp_dir": str(base / "tmp")})
        find_blender(cfg)
        find_ffmpeg(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")
    job = make_job(cfg.path("output_dir"))
    edit = add_move(job / "spec.json", "ball-1", by=[DX, 0], start=0, duration=0.5)
    result = render(job / "spec.json", cfg, keep_frames=True, argv=["pytest"])
    return {"job": job, "edit": edit, "result": result,
            "frame": lambda n: read_png(result.frames_dir / f"frame_{n:04d}.png"),
            "background": read_png(job / "layers" / "background.png")}


def _rect(cx, cy, half=12):
    return (cx - half, cy - half, cx + half, cy + half)


def _inside(x, y, rects, margin=1):
    return any(x0 - margin <= x < x1 + margin and y0 - margin <= y < y1 + margin for x0, y0, x1, y1 in rects)


def test_render_is_valid_and_rendered_in_place(rendered):
    res = rendered["result"]
    assert res.job.dir == rendered["job"]                              # 在原本的 job 裡渲染
    ver = res.report["verification"]
    assert ver["ok"] and ver["decodable"], ver
    a = ver["actual"]
    assert (a["codec"], a["width"], a["height"], a["frames"]) == ("h264", BG_W, BG_H, 30)
    assert (rendered["job"] / "result.mp4").is_file()
    assert (res.history_dir / "spec.json").is_file()                   # Stage 1 的渲染紀錄照常保留


def test_background_pixels_identical_across_frames(rendered):
    """兩個物件起點 / 終點以外的所有像素：第 1 格 = 最後一格 = background.png（逐像素）。"""
    first, last, bg = rendered["frame"](1), rendered["frame"](30), rendered["background"]
    moving = [_rect(40, 64), _rect(40 + DX, 64),                       # ball-1 起點、終點
              _rect(150, 64), _rect(150, 44)]                          # box-1 既有的 move by [0, -20]
    worst, checked = 0, 0
    for y in range(BG_H):
        for x in range(BG_W):
            if _inside(x, y, moving):
                continue
            ref = bg.pixel(x, y)
            for img in (first, last):
                worst = max(worst, max(abs(a - b) for a, b in zip(img.pixel(x, y), ref)))
            checked += 1
    assert checked > BG_W * BG_H * 0.8
    assert worst <= 1, worst


def test_target_object_moves_to_expected_location(rendered):
    first, last, bg = rendered["frame"](1), rendered["frame"](30), rendered["background"]

    def is_color(img, x, y, rgb, tol=2):
        return all(abs(a - b) <= tol for a, b in zip(img.pixel(x, y), rgb))

    interior = [(x, y) for y in range(56, 72) for x in range(-8, 8)]     # 20×20 方塊的內部（扣掉邊緣）
    assert all(is_color(first, 40 + dx, y, BALL) for dx, y in interior)          # 起點
    assert all(is_color(last, 40 + DX + dx, y, BALL) for dx, y in interior)      # 終點：剛好移動 DX px
    assert all(max(abs(a - b) for a, b in zip(last.pixel(40 + dx, y), bg.pixel(40 + dx, y))) <= 1
               for dx, y in interior)                                            # 原位置露出背景
    # 方塊的左右邊界剛好在預期的像素（沒有半像素偏移）
    assert is_color(last, 40 + DX - 10, 64, BALL) and not is_color(last, 40 + DX - 11, 64, BALL)
    assert is_color(last, 40 + DX + 9, 64, BALL) and not is_color(last, 40 + DX + 10, 64, BALL)


def test_motion_is_progressive_and_other_layer_unaffected(rendered):
    mid = rendered["frame"](8)                                          # t ≈ 0.23 s：還在移動中
    xs = [x for x in range(BG_W) if all(abs(a - b) <= 2 for a, b in zip(mid.pixel(x, 64), BALL))]
    assert xs and 40 < (min(xs) + max(xs)) / 2 < 40 + DX
    last = rendered["frame"](30)
    assert all(abs(a - b) <= 2 for a, b in zip(last.pixel(150, 44), BOX))   # box-1 照它自己的動畫移動
