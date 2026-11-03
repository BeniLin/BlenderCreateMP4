"""Blender 端入口：讀 spec.resolved.json → 建場景 → 打關鍵影格 → 渲染 PNG。

由系統端呼叫：
  blender --factory-startup -b --python-exit-code 1 -P blender/build_scene.py -- \
      --resolved <spec.resolved.json> --frames-dir <dir> --report <blender_report.json> \
      [--frame N --still <still.png>]

只用 bpy 與標準函式庫；不讀 config.yaml。
"""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import bpy  # noqa: E402

from anim_bl import compat, effects  # noqa: E402
from anim_bl.layers import build_layer  # noqa: E402
from anim_bl.scene_setup import clear_scene, setup_scene  # noqa: E402
from anim_bl.tracks import Tracks  # noqa: E402

SUPPORTED_SPEC_VERSION = 1


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="build_scene.py")
    p.add_argument("--resolved", required=True, type=Path)
    p.add_argument("--frames-dir", required=True, type=Path)
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--frame", type=int)
    p.add_argument("--still", type=Path)
    return p.parse_args(argv)


def build(resolved):
    if resolved.get("version") != SUPPORTED_SPEC_VERSION or not resolved.get("resolved"):
        raise ValueError(f"不支援的 resolved spec（version={resolved.get('version')!r}）")

    clear_scene()
    scene, engine, units = setup_scene(resolved, len(resolved["layers"]))

    objects = {layer["id"]: build_layer(scene, layer, units) for layer in resolved["layers"]}

    tracks = Tracks(resolved["canvas"]["frame_start"])
    ctx = effects.EffectContext(units=units, tracks=tracks)
    for anim in resolved["animations"]:
        effects.apply(objects[anim["target"]], anim, ctx)
    keyframes = tracks.build()
    return scene, engine, objects, keyframes


def render(scene, args, frame_end):
    image = scene.render.image_settings
    image.file_format = "PNG"
    image.color_mode = "RGB"
    image.color_depth = "8"

    if args.frame is not None:
        scene.frame_set(args.frame)
        scene.render.filepath = str(args.still)
        bpy.ops.render.render(write_still=True)
        return None

    digits = max(4, len(str(frame_end)))
    args.frames_dir.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(args.frames_dir / ("frame_" + "#" * digits))
    scene.render.use_file_extension = True
    bpy.ops.render.render(animation=True)
    return f"frame_%0{digits}d.png"


def main():
    args = parse_args()
    report = {"status": "running", "blender_version": bpy.app.version_string}
    t0 = time.perf_counter()
    try:
        compat.check_version()
        resolved = json.loads(args.resolved.read_text(encoding="utf-8"))
        scene, engine, objects, keyframes = build(resolved)
        report.update({
            "engine": engine,
            "effects_registered": effects.names(),
            "layers": list(objects),
            "animations": len(resolved["animations"]),
            "keyframes": keyframes,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y,
                           scene.render.resolution_percentage],
            "frame_range": [scene.frame_start, scene.frame_end],
        })
        pattern = render(scene, args, scene.frame_end)
        if pattern:
            report["frame_pattern"] = pattern
            report["frames_rendered"] = len(list(args.frames_dir.glob("frame_*.png")))
        else:
            report["still"] = str(args.still)
        report["status"] = "ok"
    except Exception as exc:  # 寫入報告，讓系統端可以顯示原因
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        traceback.print_exc()
    report["elapsed_sec"] = round(time.perf_counter() - t0, 2)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] != "ok":
        sys.exit(1)


main()
