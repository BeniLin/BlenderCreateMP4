"""Blender 端：A/B 畫質實驗用的場景（不是 Stage 1 的程式，不會修改 blender/）。

開發用的量測工具（Stage 1 render 修正的證據），不是 Stage 1 的執行期依賴；需要 .venv 裡的 numpy / OpenCV / Pillow。

用 Stage 1 的 anim_bl 模組依 spec.resolved.json 建立「和 Stage 1 完全相同」的場景，
然後只改一個（或指定的幾個）設定，渲染一張 still，並把實際生效的設定寫成 JSON。

  blender --factory-startup -b --python-exit-code 1 -P scripts/s2_1_render_ab/ab_scene.py -- \
      --resolved <spec.resolved.json> --frame 1 --out <still.png> --report <report.json> \
      --variant '{"filter_size": 0.0}'

可用的 variant 欄位：
  filter_size (float px)、interpolation ("Linear"|"Closest"|"Cubic")、samples (int)、
  view_transform (str)、image_colorspace (str)、display_device (str)、dither (float)
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blender"))

import bpy  # noqa: E402

from anim_bl import compat, effects  # noqa: E402
from anim_bl.layers import build_layer  # noqa: E402
from anim_bl.scene_setup import clear_scene, setup_scene  # noqa: E402
from anim_bl.tracks import Tracks  # noqa: E402


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser()
    p.add_argument("--resolved", required=True, type=Path)
    p.add_argument("--frame", type=int, default=1)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--variant", default="{}")
    return p.parse_args(argv)


def settings(scene):
    vs, ds = scene.view_settings, scene.display_settings
    texs = [n for m in bpy.data.materials if m.node_tree for n in m.node_tree.nodes if n.type == "TEX_IMAGE"]
    return {
        "engine": scene.render.engine,
        "filter_size": round(scene.render.filter_size, 4),
        "taa_render_samples": scene.eevee.taa_render_samples,
        "view_transform": vs.view_transform, "look": vs.look, "exposure": vs.exposure, "gamma": vs.gamma,
        "display_device": ds.display_device,
        "sequencer_colorspace": scene.sequencer_colorspace_settings.name,
        "dither_intensity": scene.render.dither_intensity,
        "interpolations": sorted({t.interpolation for t in texs}),
        "image_colorspaces": sorted({i.colorspace_settings.name for i in bpy.data.images if i.source == "FILE"}),
        "image_alpha_modes": sorted({i.alpha_mode for i in bpy.data.images if i.source == "FILE"}),
        "output_color_management": scene.render.image_settings.color_management,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage],
    }


def main():
    args = parse_args()
    variant = json.loads(args.variant)
    resolved = json.loads(args.resolved.read_text(encoding="utf-8"))

    # ---- 和 blender/build_scene.py 的 build() 相同
    clear_scene()
    scene, engine, units = setup_scene(resolved, len(resolved["layers"]))
    objects = {layer["id"]: build_layer(scene, layer, units) for layer in resolved["layers"]}
    tracks = Tracks(resolved["canvas"]["frame_start"])
    ctx = effects.EffectContext(units=units, tracks=tracks)
    for anim in resolved["animations"]:
        effects.apply(objects[anim["target"]], anim, ctx)
    tracks.build()
    baseline = settings(scene)

    # ---- 實驗變因
    if "filter_size" in variant:
        scene.render.filter_size = variant["filter_size"]
    if "samples" in variant:
        compat.set_samples(scene, engine, variant["samples"])
    if "view_transform" in variant:
        scene.view_settings.view_transform = variant["view_transform"]
    if "display_device" in variant:
        scene.display_settings.display_device = variant["display_device"]
    if "dither" in variant:
        scene.render.dither_intensity = variant["dither"]
    for mat in bpy.data.materials:
        if not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and "interpolation" in variant:
                node.interpolation = variant["interpolation"]
    if "image_colorspace" in variant:
        for img in bpy.data.images:
            if img.source == "FILE":
                img.colorspace_settings.name = variant["image_colorspace"]

    # ---- 和 build_scene.py 的 render() 相同
    image = scene.render.image_settings
    image.file_format = "PNG"
    image.color_mode = "RGB"
    image.color_depth = "8"
    scene.frame_set(args.frame)
    scene.render.filepath = str(args.out)
    t0 = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    report = {"variant": variant, "baseline_settings": baseline, "effective_settings": settings(scene),
              "render_sec": round(time.perf_counter() - t0, 2), "blender": bpy.app.version_string}
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")


main()
