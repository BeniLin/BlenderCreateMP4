"""S2-2 M3b 整合測試：layers pivot → 既有的 Stage 1 渲染器（不修改 Blender 端）。

合成 job（make_job），ball-1 換成「上方 6 列是綠色標記」的 20×20 方塊（24×24 圖，2 px 透明邊），
放在 (60, 64) → 方塊內容在畫布上是 x 50..70、y 54..74。
A. 初始 scale 0.6 + rotation 30°，含 move.to / move.by：修改 pivot 前後每一格都逐像素相同
B. pivot 在方塊底邊中央 + scale 1.5：底邊在整段縮放中都不動
C. pivot 在方塊左上角 + rotate 90°：方塊繞著這個角轉
D. move + scale + rotate：scale / rotate 開始前的影格相同；之後依新的 pivot 轉換
"""

from __future__ import annotations

import json

import pytest

import make_sample_layers as L
from pngread import read_png
from test_animate import BG_H, BG_W, make_job

pytestmark = pytest.mark.blender

RED, GREEN = (230, 20, 20), (40, 200, 40)
SIDE, PAD, MARK = 20, 2, 6
SIZE = (SIDE + 2 * PAD, SIDE + 2 * PAD)
P0 = (60.0, 64.0)


def _ball_rows():
    rows = []
    for y in range(SIZE[1]):
        row = b""
        for x in range(SIZE[0]):
            if PAD <= x < SIDE + PAD and PAD <= y < SIDE + PAD:
                row += bytes((*(GREEN if y < PAD + MARK else RED), 255))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(row)
    return rows


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.tools import find_blender, find_ffmpeg

    base = tmp_path_factory.mktemp("anim_render_m3b")
    try:
        cfg = load_config(None, {"paths.output_dir": str(base / "output"), "paths.temp_dir": str(base / "tmp")})
        find_blender(cfg)
        find_ffmpeg(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")
    return cfg


def _job(cfg, name, animations=(), **ball):
    job = make_job(cfg.path("output_dir"), job_id=f"20990101-000000_{name}")
    L.write_png(job / "layers" / "ball-1.png", SIZE[0], SIZE[1], _ball_rows())
    spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
    layer = next(l for l in spec["layers"] if l["id"] == "ball-1")
    layer.update({"position": list(P0), **ball})
    spec["animations"].extend(animations)
    (job / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return job / "spec.json"


def _render(cfg, spec_path, frames=(1, 8, 13, 16, 23, 30)):
    from animator.pipeline import render

    result = render(spec_path, cfg, keep_frames=True, argv=["pytest"])
    assert result.report["verification"]["ok"], result.report["verification"]
    return {n: read_png(result.frames_dir / f"frame_{n:04d}.png") for n in frames}


def _max_diff(a, b):
    return max(max(abs(x - y) for x, y in zip(a.pixel(px, py), b.pixel(px, py)))
               for py in range(BG_H) for px in range(BG_W))


def _close(a, b, tol=3):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _coverage(px, colour, bg):
    """這個像素有多少比例是 colour（linear 空間的 alpha 混合，最小平方估計）。"""
    d = [_lin(c) - _lin(b) for c, b in zip(colour, bg)]
    e = [_lin(p) - _lin(b) for p, b in zip(px, bg)]
    return sum(x * y for x, y in zip(d, e)) / sum(x * x for x in d)


def _edge(img, bg, x, ys, colour):
    """沿著第 x 欄，在 ys（依序）找 coverage 跨過 0.5 的位置（像素中心 = y + 0.5，線性內插）。"""
    cov = [(y + 0.5, _coverage(img.pixel(x, y), colour, bg.pixel(x, y))) for y in ys]
    for (y0, c0), (y1, c1) in zip(cov, cov[1:]):
        if (c0 - 0.5) * (c1 - 0.5) <= 0 and c0 != c1:
            return y0 + (0.5 - c0) / (c1 - c0) * (y1 - y0)
    raise AssertionError(f"找不到邊緣：{cov}")


@pytest.fixture(scope="module")
def background(env):
    spec = _job(env, "m3b-bg")
    return read_png(spec.parent / "layers" / "background.png")


# ---------------------------------------------------------------- A. 初始 scale + rotation、move.to + move.by

def test_a_pivot_edit_keeps_every_frame_pixel_identical(env):
    from animator.animate.edit import image_to_canvas, set_layer_pivot

    moves = [{"id": "a-to", "target": "ball-1", "effect": "move", "start": 0.0, "end": 0.5, "params": {"to": [120, 70]}},
             {"id": "a-by", "target": "ball-1", "effect": "move", "start": 0.5, "end": 0.9,
              "params": {"by": [-20, 10]}}]
    spec = _job(env, "m3b-a", moves, scale=0.6, rotation=30)
    before = _render(env, spec)
    q = image_to_canvas(4, 20, P0, (0.5, 0.5), SIZE, 0.6, 30)           # 圖片左下附近的一點
    res = set_layer_pivot(spec, "ball-1", list(q))
    assert res.rewritten == ["a-to"]                                    # move.by 不變
    after = _render(env, spec)
    diffs = {n: _max_diff(before[n], after[n]) for n in before}
    assert all(d <= 1 for d in diffs.values()), diffs


# ---------------------------------------------------------------- B. 底邊中央 pivot + scale

def test_b_bottom_edge_stays_fixed_while_scaling(env, background):
    from animator.animate.edit import add_animation, set_layer_pivot

    spec = _job(env, "m3b-b")
    set_layer_pivot(spec, "ball-1", [60, 74])                          # 方塊內容的底邊中央
    add_animation(spec, "ball-1", "scale", {"to": 1.5}, start=0.0, duration=0.8)
    frames = _render(env, spec)
    for n, img in frames.items():
        bottom = _edge(img, background, 60, range(66, 82), RED)
        assert abs(bottom - 74) <= 0.35, (n, bottom)                    # 底邊不動
    top = _edge(frames[30], background, 60, range(36, 52), GREEN)
    assert abs(top - (74 - SIDE * 1.5)) <= 0.35, top                    # 上邊往上：74 − 30 = 44
    assert _close(frames[30].pixel(60, 76), background.pixel(60, 76), 1)   # 預設 pivot 時這裡會是紅色（底邊在 79）


# ---------------------------------------------------------------- C. 左上角 pivot + rotate 90°

def test_c_rotation_around_corner_pivot(env, background):
    from animator.animate.edit import add_animation, set_layer_pivot

    spec = _job(env, "m3b-c")
    res = set_layer_pivot(spec, "ball-1", [50, 54])                    # 方塊內容的左上角
    assert res.new_anchor == [PAD / SIZE[0], PAD / SIZE[1]]
    add_animation(spec, "ball-1", "rotate", {"by": 90}, start=0.0, duration=0.8)
    frames = _render(env, spec)
    f1, mid, last = frames[1], frames[13], frames[30]

    def is_bg(img, x, y):
        return _close(img.pixel(x, y), background.pixel(x, y), 1)

    # 第 1 格：角落像素 (50, 54) 是方塊（綠色標記）；角落左上方是背景
    assert _close(f1.pixel(50, 54), GREEN) and is_bg(f1, 49, 54) and is_bg(f1, 50, 53)
    # 45°（第 13 格剛好轉一半）：方塊以 (50, 54) 為頂點；pivot 正下方在方塊內、正上方是背景
    assert not is_bg(mid, 50, 56) and is_bg(mid, 50, 51)
    # 90°：方塊在 x 30..50、y 54..74；原本上方的綠色標記轉到右邊；(50, 54) 仍然是角落
    assert _close(last.pixel(49, 54), GREEN) and is_bg(last, 50, 54) and is_bg(last, 49, 53)
    assert _close(last.pixel(33, 64), RED) and _close(last.pixel(47, 64), GREEN)
    assert is_bg(last, 60, 64)                                          # 預設 pivot（中心）時這裡會是方塊


# ---------------------------------------------------------------- D. move + scale + rotate

def test_d_combined_move_scale_rotate(env, background):
    from animator.animate.edit import image_to_canvas, set_layer_pivot

    anims = [{"id": "d-move", "target": "ball-1", "effect": "move", "start": 0.0, "end": 0.9, "params": {"by": [40, 0]}},
             {"id": "d-scale", "target": "ball-1", "effect": "scale", "start": 0.5, "end": 0.9, "params": {"to": 1.5}},
             {"id": "d-rot", "target": "ball-1", "effect": "rotate", "start": 0.5, "end": 0.9, "params": {"by": 30}}]
    spec = _job(env, "m3b-d", anims)
    before = _render(env, spec)
    res = set_layer_pivot(spec, "ball-1", [60, 74])
    after = _render(env, spec)
    # scale / rotate 從 0.5 s（第 16 格）才開始：之前的影格逐像素相同
    for n in (1, 8, 13, 16):
        assert _max_diff(before[n], after[n]) <= 1, n
    assert _max_diff(before[30], after[30]) > 50                        # 之後依新的 pivot 轉換（畫面不同）
    # 第 30 格：pivot 在 (100, 74)，scale 1.5、rotation 30°；圖片內部的取樣點都在預期的位置
    end = (P0[0] + 40, 74)
    samples = [((12, 14), RED), ((5, 17), RED), ((19, 17), RED), ((12, 20.5), RED), ((12, 4.5), GREEN),
               ((5, 4.5), GREEN), ((19, 5), GREEN)]
    for (u, v), colour in samples:
        x, y = image_to_canvas(u, v, end, res.new_anchor, SIZE, 1.5, 30)
        assert _close(after[30].pixel(int(x), int(y)), colour), ((u, v), (x, y), after[30].pixel(int(x), int(y)))
