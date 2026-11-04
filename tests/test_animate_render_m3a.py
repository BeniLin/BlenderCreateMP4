"""S2-2 M3a 整合測試：layers z 改變前後順序 → 既有的 Stage 1 渲染器真的換了誰在前面。

合成 job：把 box-1 移到和 ball-1 重疊的位置（只改 spec 的初始位置，不改渲染器），
渲染第 1 格 → set_layer_z(box-1, above=ball-1) → 再渲染第 1 格，比較兩張圖。
"""

from __future__ import annotations

import json

import pytest

from pngread import read_png
from test_animate import BG_H, BG_W, make_job

pytestmark = pytest.mark.blender

RED, BLUE = (230, 20, 20), (20, 40, 230)
BALL_X, BOX_X, Y = 40, 48, 64        # 兩個 20×20 方塊：ball x 30..49、box x 38..57 → 重疊 x 38..49
OVERLAP = (38, 54, 50, 74)           # x0, y0, x1, y1（不含 x1 / y1）


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    from animator.animate.edit import set_layer_z
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.pipeline import render
    from animator.tools import find_blender

    base = tmp_path_factory.mktemp("anim_render_m3a")
    try:
        cfg = load_config(None, {"paths.output_dir": str(base / "output"), "paths.temp_dir": str(base / "tmp")})
        find_blender(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender：{exc}")
    job = make_job(cfg.path("output_dir"))
    spec_path = job / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    next(l for l in spec["layers"] if l["id"] == "box-1")["position"] = [float(BOX_X), float(Y)]
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    before = read_png(render(spec_path, cfg, frame=1, argv=["pytest"]).output)    # ball-1 z 20 在前
    edit = set_layer_z(spec_path, "box-1", above="ball-1")
    after = read_png(render(spec_path, cfg, frame=1, argv=["pytest"]).output)     # box-1 z 30 在前
    return {"before": before, "after": after, "edit": edit,
            "background": read_png(job / "layers" / "background.png")}


def _close(a, b, tol=2):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_edit_moved_box_in_front(rendered):
    e = rendered["edit"]
    assert (e.old_z, e.new_z) == (10, 30)
    assert e.order == ["background", "ball-1", "box-1"]


def test_overlap_shows_the_front_layer(rendered):
    before, after = rendered["before"], rendered["after"]
    x0, y0, x1, y1 = OVERLAP
    pts = [(x, y) for y in range(y0 + 1, y1 - 1) for x in range(x0 + 1, x1 - 1)]
    assert all(_close(before.pixel(x, y), RED) for x, y in pts)       # 之前：ball-1 在前
    assert all(_close(after.pixel(x, y), BLUE) for x, y in pts)       # 之後：box-1 在前


def test_non_overlapping_parts_are_unchanged(rendered):
    before, after = rendered["before"], rendered["after"]
    assert _close(before.pixel(33, Y), RED) and _close(after.pixel(33, Y), RED)       # 只有 ball-1
    assert _close(before.pixel(54, Y), BLUE) and _close(after.pixel(54, Y), BLUE)     # 只有 box-1


def test_only_the_overlap_region_changed(rendered):
    """前後兩張圖只有重疊區域不同；其他像素完全相同（渲染器沒有其他變化）。"""
    before, after = rendered["before"], rendered["after"]
    x0, y0, x1, y1 = OVERLAP
    changed = [(x, y) for y in range(BG_H) for x in range(BG_W) if before.pixel(x, y) != after.pixel(x, y)]
    assert changed
    assert all(x0 - 1 <= x < x1 + 1 and y0 - 1 <= y < y1 + 1 for x, y in changed), changed[:10]


def test_background_is_untouched(rendered):
    after, bg = rendered["after"], rendered["background"]
    worst = max(max(abs(a - b) for a, b in zip(after.pixel(x, y), bg.pixel(x, y)))
                for y in range(BG_H) for x in range(BG_W) if not (26 <= x < 62 and 50 <= y < 78))
    assert worst <= 1, worst
