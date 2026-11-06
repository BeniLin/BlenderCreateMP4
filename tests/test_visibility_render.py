"""S2-2 M3c 整合測試：visibility 通道 → Stage 1 渲染器（Blender 5.2.3、EEVEE）。

畫布 192×128、30 fps、1 秒（30 格）。圖層都是 24×24 的圖（20×20 實心 + 2 px 透明邊）：
- rear（藍，z 10）在 (60, 64)；front（紅，z 20）在 (68, 64) → 重疊 x 58..69
- front：visibility 隱藏 [0.2, 0.4)（第 7..12 格）、[0.7, 0.8)（第 22..24 格）；
         opacity 在 0.5 s 以 constant easing 變成 0（第 19 格起）
- mover（綠）：0 → 1 秒從 (30, 30) 等速移到 (150, 30)；隱藏 [0.3, 0.6)（第 10..18 格）
- tail（黃）：visible: false，visibility 顯示 [0.9, 1.0]（第 28..30 格，含最後一格）
比較解碼後的像素，不比較 PNG 檔案的 hash（PNG 的 metadata 每次都不同）。
"""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

import make_sample_layers as L
from pngread import read_png
from test_animate import _solid, _square

pytestmark = pytest.mark.blender

W, H = 192, 128
RED, BLUE, GREEN, YELLOW, GREY = (230, 20, 20), (20, 40, 230), (20, 200, 60), (230, 200, 20), (90, 90, 90)
OVERLAP, FRONT_ONLY, REAR_ONLY = (64, 64), (75, 64), (52, 64)


def _layer(layer_id, colour_name, z, position, **extra):
    return {"id": layer_id, "image": f"layers/{colour_name}.png", "z": z, "position": list(position), **extra}


SPEC = {
    "version": 1,
    "meta": {"name": "visibility-test"},
    "canvas": {"width": W, "height": H, "fps": 30, "duration": 1.0},
    "layers": [
        {"id": "background", "image": "layers/grey.png", "z": 0, "meta": {"role": "background"}},
        _layer("rear", "blue", 10, (60, 64)),
        _layer("front", "red", 20, (68, 64)),
        _layer("mover", "green", 30, (30, 30)),
        _layer("tail", "yellow", 40, (160, 100), visible=False),
    ],
    "animations": [
        {"id": "front-hide-1", "target": "front", "effect": "visibility", "start": 0.2, "end": 0.4,
         "params": {"visible": False}},
        {"id": "front-fade", "target": "front", "effect": "opacity", "start": 0.5, "end": 0.6, "easing": "constant",
         "params": {"to": 0}},
        {"id": "front-hide-2", "target": "front", "effect": "visibility", "start": 0.7, "end": 0.8,
         "params": {"visible": False}},
        {"id": "mover-move", "target": "mover", "effect": "move", "start": 0.0, "end": 1.0, "easing": "linear",
         "params": {"to": [150, 30]}},
        {"id": "mover-hide", "target": "mover", "effect": "visibility", "start": 0.3, "end": 0.6,
         "params": {"visible": False}},
        {"id": "tail-show", "target": "tail", "effect": "visibility", "start": 0.9, "end": 1.0,
         "params": {"visible": True}},
    ],
}


def _write_job(directory, spec):
    (directory / "layers").mkdir(parents=True)
    L.write_png(directory / "layers" / "grey.png", W, H, _solid(W, H, (*GREY, 255)))
    for name, rgb in (("red", RED), ("blue", BLUE), ("green", GREEN), ("yellow", YELLOW)):
        L.write_png(directory / "layers" / f"{name}.png", 24, 24, _square(20, 2, rgb))
    (directory / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return directory / "spec.json"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.tools import find_blender, find_ffmpeg

    base = tmp_path_factory.mktemp("visibility_render")
    try:
        cfg = load_config(None, {"paths.output_dir": str(base / "output"), "paths.temp_dir": str(base / "tmp")})
        find_blender(cfg)
        find_ffmpeg(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")
    return cfg, base


def _render_frames(cfg, spec_path):
    from animator.pipeline import render

    result = render(spec_path, cfg, keep_frames=True, argv=["pytest"])
    assert result.report["verification"]["ok"], result.report["verification"]
    return {n: read_png(result.frames_dir / f"frame_{n:04d}.png") for n in range(1, 31)}


@pytest.fixture(scope="module")
def frames(env):
    cfg, _ = env
    spec_path = _write_job(cfg.path("output_dir") / "20990101-000000_vis", SPEC)
    first = _render_frames(cfg, spec_path)
    second = _render_frames(cfg, spec_path)                           # 同一個 spec 再渲染一次
    return first, second


@pytest.fixture(scope="module")
def reference(env):
    """同樣的 job，但 front 用 visible: false 完全移除（沒有 visibility 動畫）→ 只看得到後面的圖層。"""
    cfg, _ = env
    spec = copy.deepcopy(SPEC)
    next(l for l in spec["layers"] if l["id"] == "front")["visible"] = False
    spec["animations"] = [a for a in spec["animations"] if a["target"] != "front"]
    return _render_frames(cfg, _write_job(cfg.path("output_dir") / "20990101-000000_vis-ref", spec))


def _close(a, b, tol=1):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _region(img, x0, y0, x1, y1):
    return [img.pixel(x, y) for y in range(y0, y1) for x in range(x0, x1)]


FRONT_BOX = (54, 50, 84, 78)          # front 圖層（含透明邊）+ 1 px


# ---------------------------------------------------------------- front：隱藏 / 顯示 / opacity

@pytest.mark.parametrize("frame, overlap, front_only", [
    (1, RED, RED), (6, RED, RED),                      # 顯示
    (7, BLUE, GREY), (12, BLUE, GREY),                 # 隱藏（opacity 1）→ 露出後面的 rear
    (13, RED, RED), (18, RED, RED),                    # 重新顯示
    (19, BLUE, GREY), (21, BLUE, GREY),                # 顯示但 opacity 0 → 透明
    (22, BLUE, GREY), (24, BLUE, GREY),                # 隱藏 + opacity 0
    (25, BLUE, GREY), (30, BLUE, GREY),                # 顯示，但 opacity 仍然是 0（visibility 不會改 opacity）
])
def test_front_layer_state_per_frame(frames, frame, overlap, front_only):
    img = frames[0][frame]
    assert _close(img.pixel(*OVERLAP), overlap), (frame, img.pixel(*OVERLAP))
    assert _close(img.pixel(*FRONT_ONLY), front_only), (frame, img.pixel(*FRONT_ONLY))
    assert _close(img.pixel(*REAR_ONLY), BLUE)                        # rear 永遠不受影響


def test_colours_are_unchanged(frames):
    """visibility 節點不改變顏色：顯示時的像素就是 PNG 的顏色。"""
    img = frames[0][1]
    assert img.pixel(*FRONT_ONLY) == RED and img.pixel(*REAR_ONLY) == BLUE and img.pixel(5, 120) == GREY


@pytest.mark.parametrize("frame", [7, 10, 12, 19, 22, 25, 30])
def test_hidden_front_reveals_exact_rear_pixels(frames, reference, frame):
    """front 隱藏（或 opacity 0）的影格：front 附近的像素和「完全沒有 front」的參考渲染逐像素相同。"""
    got, ref = _region(frames[0][frame], *FRONT_BOX), _region(reference[frame], *FRONT_BOX)
    assert max(max(abs(a - b) for a, b in zip(p, q)) for p, q in zip(got, ref)) <= 1


def test_showing_again_restores_the_same_appearance(frames):
    assert _region(frames[0][13], *FRONT_BOX) == _region(frames[0][6], *FRONT_BOX)


# ---------------------------------------------------------------- mover：隱藏期間繼續移動

def _green_center(img, y=30):
    xs = [x for x in range(W) if _close(img.pixel(x, y), GREEN, 2)]
    return (min(xs) + max(xs)) / 2 if xs else None


def test_hidden_mover_reappears_at_its_current_position(frames):
    def expected(frame):                                              # linear：第 1 格 30 → 第 30 格 150
        return 30 + 120 * (frame - 1) / 29

    for frame in (1, 9, 19, 30):
        c = _green_center(frames[0][frame])
        assert c is not None and abs(c - expected(frame)) <= 1, (frame, c, expected(frame))
    for frame in (10, 14, 18):                                        # 隱藏中：整列都沒有綠色
        assert _green_center(frames[0][frame]) is None, frame


# ---------------------------------------------------------------- tail：visible false + 顯示到最後一格

def test_hidden_layer_shown_through_the_last_frame(frames):
    assert _close(frames[0][27].pixel(160, 100), GREY)
    for frame in (28, 29, 30):                                        # end = duration：最後一格也顯示
        assert _close(frames[0][frame].pixel(160, 100), YELLOW), frame


# ---------------------------------------------------------------- 決定性

def test_repeated_renders_are_pixel_identical(frames):
    first, second = frames
    for n in range(1, 31):
        assert _region(first[n], 0, 0, W, H) == _region(second[n], 0, 0, W, H), n


# ---------------------------------------------------------------- 共用材質的兩個物件

BLENDER_SCRIPT = r'''
import json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, argv[0])
import bpy
from anim_bl.layers import build_layer
from anim_bl.scene_setup import clear_scene, setup_scene
resolved = json.load(open(argv[1], encoding="utf-8"))
clear_scene()
scene, engine, units = setup_scene(resolved, len(resolved["layers"]))
objs = {l["id"]: build_layer(scene, l, units) for l in resolved["layers"]}
left, right = objs["left"], objs["right"]
right.data.materials[0] = left.data.materials[0]       # 兩個物件共用同一個材質
left.color[0] = 0.0                                     # 只隱藏 left
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGB"
scene.frame_set(1)
scene.render.filepath = argv[2]
bpy.ops.render.render(write_still=True)
json.dump({"shared": left.active_material == right.active_material, "materials": len(bpy.data.materials)},
          open(argv[3], "w"))
'''


def test_objects_sharing_a_material_have_independent_visibility(env, tmp_path):
    from animator.spec.resolve import resolve
    from animator.tools import find_blender
    from conftest import ROOT

    cfg, _ = env
    spec = {"version": 1, "canvas": {"width": W, "height": H, "fps": 30, "duration": 1.0},
            "layers": [{"id": "background", "image": "layers/grey.png", "z": 0},
                       _layer("left", "red", 10, (50, 64)), _layer("right", "red", 20, (140, 64))]}
    spec_path = _write_job(tmp_path / "shared", spec)
    resolved = resolve(json.loads(spec_path.read_text(encoding="utf-8")), spec_path, cfg)
    (tmp_path / "resolved.json").write_text(json.dumps(resolved), encoding="utf-8")
    (tmp_path / "shared_material.py").write_text(BLENDER_SCRIPT, encoding="utf-8")
    out, info = tmp_path / "still.png", tmp_path / "info.json"
    subprocess.run([str(find_blender(cfg).path), "--factory-startup", "-b", "--python-exit-code", "1",
                    "-P", str(tmp_path / "shared_material.py"), "--", str(ROOT / "blender"),
                    str(tmp_path / "resolved.json"), str(out), str(info)],
                   check=True, capture_output=True, timeout=300)
    assert json.loads(info.read_text())["shared"] is True
    img = read_png(out)
    assert _close(img.pixel(50, 64), GREY)                            # left：隱藏
    assert _close(img.pixel(140, 64), RED)                            # right：同一個材質，仍然顯示


# ---------------------------------------------------------------- examples/visibility

def test_visibility_example_renders_as_documented(env):
    """examples/visibility：第 1 格橫條在太陽上；第 46 格（1.5 s）橫條隱藏露出太陽、星星隱藏；最後一格顯示標題。"""
    from animator.pipeline import render
    from conftest import ROOT

    cfg, _ = env
    spec = ROOT / "examples" / "visibility" / "spec.json"
    still = {n: read_png(render(spec, cfg, frame=n, name=f"vis-example-{n}", argv=["pytest"]).output)
             for n in (1, 46, 120)}

    def has(img, colour, x0, y0, x1, y1, step=4):
        return any(_close(img.pixel(x, y), colour, 3) for y in range(y0, y1, step) for x in range(x0, x1, step))

    assert _close(still[1].pixel(640, 300), L.BAR, 3)                 # 橫條蓋住太陽中央
    assert _close(still[46].pixel(640, 300), L.SUN, 3)                # 橫條隱藏 → 露出太陽
    assert has(still[1], L.STAR, 100, 440, 320, 600)                  # 第 1 格有星星
    assert not has(still[46], L.STAR, 0, 430, 1280, 610)              # 1.5 s：星星隱藏
    assert not has(still[1], L.TITLE, 320, 580, 960, 700)             # 標題一開始隱藏
    assert has(still[120], L.TITLE, 320, 580, 960, 700, step=2)       # 最後一格顯示（區間到影片結尾）
