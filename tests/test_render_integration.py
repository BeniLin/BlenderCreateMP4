"""整合測試：examples/basic → Blender → FFmpeg，並逐格檢查像素。

執行需要 config/config.yaml 中設定好的 Blender 與 FFmpeg；找不到時自動略過。
只跑這組：pytest -m blender；略過：pytest -m "not blender"
"""

from __future__ import annotations

import json

import pytest

import make_sample_layers as L
from conftest import EXAMPLE
from pngread import read_png

pytestmark = pytest.mark.blender

PCT = 50          # 測試用的預覽解析度
FPS = 30
TOL = 14


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.tools import find_blender, find_ffmpeg

    base = tmp_path_factory.mktemp("integration")
    try:
        cfg = load_config(None, {"paths.output_dir": str(base / "output"),
                                 "paths.temp_dir": str(base / "tmp")})
        find_blender(cfg)
        find_ffmpeg(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")
    cfg.data["defaults"]["preview"]["resolution_percentage"] = PCT
    return cfg


@pytest.fixture(scope="module")
def first_render(cfg):
    from animator.pipeline import render

    return render(EXAMPLE / "spec.json", cfg, preview=True, keep_frames=True, name="it", argv=["pytest"])


def frame(result, t):
    n = 1 + round(t * FPS)
    return read_png(result.frames_dir / f"frame_{min(n, 120):04d}.png")


def px(img, x, y):
    return img.pixel(x * PCT / 100, y * PCT / 100)


def close(a, b, tol=TOL):
    return all(abs(i - j) <= tol for i, j in zip(a, b))


def bg(img, y):
    """背景只有垂直漸層 → 同一列最左邊的像素就是背景色。"""
    return px(img, 4, y)


def test_video_output_and_report(first_render):
    rep = first_render.report
    assert rep["status"] == "ok"
    ver = rep["verification"]
    assert ver["ok"], ver["problems"]
    assert ver["decodable"]
    a = ver["actual"]
    assert (a["codec"], a["width"], a["height"], a["frames"]) == ("h264", 640, 360, 120)
    assert abs(a["duration"] - 4.0) < 0.05
    assert first_render.output.name == "preview.mp4" and first_render.output.is_file()


def test_trace_files_written(first_render):
    hist = first_render.history_dir
    for name in ("spec.json", "spec.resolved.json", "config.effective.yaml", "render_report.json",
                 "blender.log", "ffmpeg.log", "blender_report.json"):
        assert (hist / name).is_file(), name
    rep = json.loads((hist / "render_report.json").read_text(encoding="utf-8"))
    assert set(rep["layers"]) == {"background", "cloud", "sun", "bar", "star", "title"}
    assert all(len(v["sha256"]) == 64 for v in rep["layers"].values())
    assert rep["tools"]["blender"]["version"]


def test_colors_preserved(first_render):
    img = frame(first_render, 0)
    assert close(px(img, 150, 450), L.STAR)            # 星星中心
    assert close(px(img, 740, 250), L.BAR)             # 橫條右側
    assert close(px(img, 1050, 170), L.SUN)            # 太陽中心
    top = L.BG_TOP
    assert close(px(img, 4, 2), top, tol=20)           # 背景頂端


def test_move_independent(first_render):
    start, end = frame(first_render, 0), frame(first_render, 4)
    assert close(px(start, 150, 450), L.STAR)
    assert not close(px(start, 1130, 450), L.STAR)
    assert close(px(end, 1130, 450), L.STAR)
    assert close(px(end, 150, 450), bg(end, 450))      # 原位置露出背景


def test_scale_with_anchor_pivot(first_render):
    t0, t2, t4 = frame(first_render, 0), frame(first_render, 2), frame(first_render, 4)
    # 0.6 倍：半徑 60；1.2 倍：半徑 120；0.9 倍：半徑 90（中心不動）
    assert close(px(t0, 1050, 170), L.SUN)
    assert close(px(t0, 1140, 170), bg(t0, 170))
    assert close(px(t2, 1160, 170), L.SUN)
    assert close(px(t4, 1130, 170), L.SUN)
    assert close(px(t4, 1158, 170), bg(t4, 170))


def test_rotate_clockwise(first_render):
    t0, t4 = frame(first_render, 0), frame(first_render, 4)
    assert close(px(t0, 740, 250), L.BAR) and not close(px(t0, 640, 350), L.BAR)
    assert close(px(t4, 640, 350), L.BAR) and close(px(t4, 740, 250), bg(t4, 250))
    # back_out 會超過 90°：順時針時，下端往左
    mid = frame(first_render, 2)
    assert close(px(mid, 628, 360), L.BAR)


def test_fade_in_out(first_render):
    t0, t3, t4 = frame(first_render, 0), frame(first_render, 3), frame(first_render, 4)
    # 標題：0 → 一半 → 完全不透明
    assert close(px(t0, 390, 620), bg(t0, 620))
    mid_r, bg_r = px(t3, 390, 620)[0], bg(t3, 620)[0]
    assert bg_r + 40 < mid_r < L.TITLE[0] - 40
    assert close(px(t4, 390, 620), L.TITLE)
    # 雲：初始為半透明白色，2 秒後完全消失
    assert sum(px(t0, 300, 160)) > sum(bg(t0, 160)) + 200
    assert close(px(frame(first_render, 2.5), 220, 160), bg(t4, 160))


def test_rerender_in_place_after_manual_edit(cfg, first_render):
    """人工修改 job 裡的 spec → 原地重新渲染 → 結果改變，舊 MP4 與紀錄都保留。"""
    from animator.pipeline import render

    job = first_render.job
    spec = json.loads(job.spec_path.read_text(encoding="utf-8"))
    anims = {a["id"]: a for a in spec["animations"]}
    anims["title-in"]["enabled"] = False
    anims["star-fly"]["params"]["to"] = [900, 450]
    job.spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    second = render(job.spec_path, cfg, preview=True, keep_frames=True, argv=["pytest"])
    assert second.job.id == job.id                       # 沒有建立新 job
    assert second.render_id != first_render.render_id

    end = frame(second, 4)
    assert close(px(end, 900, 450), L.STAR)
    assert close(px(end, 390, 620), bg(end, 620))        # 標題不再出現

    backup = job.history_dir / first_render.render_id / "preview.mp4"
    assert backup.is_file()
    assert second.report["previous_output_moved_to"].replace("\\", "/") == \
        f"history/{first_render.render_id}/preview.mp4"
    old_spec = json.loads((first_render.history_dir / "spec.json").read_text(encoding="utf-8"))
    assert {a["id"]: a for a in old_spec["animations"]}["title-in"].get("enabled", True) is True

    index = json.loads((job.dir / "renders.json").read_text(encoding="utf-8"))
    assert [r["render_id"] for r in index["renders"]] == [first_render.render_id, second.render_id]
    assert index["current"]["preview.mp4"] == second.render_id
