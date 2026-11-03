"""把已驗證的 spec.json 轉成 Blender 端讀取的 spec.resolved.json。

resolve 完成後：
  - 所有預設值都已補齊（spec 的值 > config defaults > 內建預設值）
  - 圖片路徑轉成絕對路徑，並附上 PNG 尺寸
  - 圖層依前後順序排好，並標上 rank（0 = 最後面）
  - visible=false 的圖層、enabled=false 的動畫已經移除
  - fade_in/fade_out 已展開為 opacity；by 已換算成 to；from 已推算好
  - 時間已換算成影格
  - 每個通道的值一律寫成 list：position [x,y]、scale [sx,sy]、rotation [deg]、opacity [a]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from animator.config import Config
from animator.errors import SpecError
from animator.spec import schema as S
from animator.spec.png import read_png_info
from animator.spec.validate import parse_color, validate


def _scale_pair(v: Any) -> list[float]:
    return [float(v), float(v)] if not isinstance(v, list) else [float(v[0]), float(v[1])]


def _initial_values(layer: dict) -> dict[str, list[float]]:
    return {
        "position": [float(x) for x in layer["position"]],
        "scale": list(layer["scale"]),
        "rotation": [float(layer["rotation"])],
        "opacity": [float(layer["opacity"])],
    }


def _channel_value(channel: str, v: Any) -> list[float]:
    if channel == "position":
        return [float(v[0]), float(v[1])]
    if channel == "scale":
        return _scale_pair(v)
    return [float(v)]


def resolve(raw: dict, spec_path: Path, cfg: Config, preview: bool = False) -> dict:
    spec_path = spec_path.resolve()
    spec_dir = spec_path.parent
    result = validate(raw, spec_dir)
    if not result.ok:
        raise SpecError(result.errors)

    d = cfg.get("defaults")
    rc = raw["canvas"]
    fps = rc.get("fps", d["canvas"]["fps"])
    duration = float(rc["duration"])
    frame_end = S.frame_count(duration, fps)
    width, height = rc["width"], rc["height"]
    canvas = {
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "frame_start": 1,
        "frame_end": frame_end,
        "background_color": list(parse_color(rc.get("background_color", d["canvas"]["background_color"]))),
    }

    # ---- layers
    layers: list[dict] = []
    for index, layer in enumerate(raw["layers"]):
        if layer.get("visible", True) is False:
            continue
        image = Path(layer["image"])
        image = (image if image.is_absolute() else spec_dir / image).resolve()
        info = read_png_info(image)
        layers.append({
            "id": layer["id"],
            "image": str(image),
            "size": [info.width, info.height],
            "position": [float(x) for x in layer.get("position", [width / 2, height / 2])],
            "anchor": [float(x) for x in layer.get("anchor", [0.5, 0.5])],
            "z": float(layer.get("z", index)),
            "scale": _scale_pair(layer.get("scale", 1)),
            "rotation": float(layer.get("rotation", 0)),
            "opacity": float(layer.get("opacity", 1)),
            "_index": index,
        })
    layers.sort(key=lambda l: (l["z"], l["_index"]))
    for rank, layer in enumerate(layers):
        layer["rank"] = rank
        del layer["_index"]
    by_id = {l["id"]: l for l in layers}

    # ---- animations
    pending = []
    for index, anim in enumerate(raw.get("animations", [])):
        if anim.get("enabled", True) is False or anim["target"] not in by_id:
            continue
        pending.append((float(anim["start"]), index, anim))
    pending.sort(key=lambda t: (t[0], t[1]))

    current: dict[tuple[str, str], list[float]] = {}
    animations: list[dict] = []
    for start, index, anim in pending:
        source_effect = anim["effect"]
        effect = S.EFFECT_ALIASES.get(source_effect, source_effect)
        channel = S.EFFECT_CHANNELS[effect]
        target = anim["target"]
        params = dict(anim.get("params", {}))
        if source_effect == "fade_in":
            params.setdefault("from", 0.0)
            params.setdefault("to", 1.0)
        elif source_effect == "fade_out":
            params.setdefault("to", 0.0)

        key = (target, channel)
        if key not in current:
            current[key] = _initial_values(by_id[target])[channel]
        start_value = _channel_value(channel, params["from"]) if "from" in params else list(current[key])
        if "to" in params:
            end_value = _channel_value(channel, params["to"])
        else:  # by
            delta = _channel_value(channel, params["by"])
            end_value = [a + b for a, b in zip(start_value, delta)]
        current[key] = end_value

        end = float(anim["end"])
        animations.append({
            "id": anim.get("id", f"anim_{index}"),
            "target": target,
            "effect": effect,
            "source_effect": source_effect,
            "channel": channel,
            "start": start,
            "end": end,
            "frame_start": S.time_to_frame(start, fps, frame_end),
            "frame_end": S.time_to_frame(end, fps, frame_end),
            "easing": anim.get("easing", S.DEFAULT_EASING),
            "from": start_value,
            "to": end_value,
        })

    # ---- render / output
    rr = raw.get("render", {})
    ro = raw.get("output", {})
    render = {
        "engine": rr.get("engine", d["render"]["engine"]),
        "samples": rr.get("samples", d["render"]["samples"]),
        "resolution_percentage": rr.get("resolution_percentage", 100),
        "view_transform": rr.get("view_transform", d["render"]["view_transform"]),
    }
    output = {
        "filename": ro.get("filename", "result.mp4"),
        "codec": ro.get("codec", d["encode"]["codec"]),
        "crf": ro.get("crf", d["encode"]["crf"]),
        "preset": ro.get("preset", d["encode"]["preset"]),
        "pix_fmt": ro.get("pix_fmt", d["encode"]["pix_fmt"]),
    }
    if preview:
        pv = d["preview"]
        render["resolution_percentage"] = pv["resolution_percentage"]
        render["samples"] = pv["samples"]
        output["crf"] = pv["crf"]
        output["filename"] = "preview.mp4"

    return {
        "version": S.SPEC_VERSION,
        "resolved": True,
        "source_spec": str(spec_path),
        "preview": preview,
        "meta": raw.get("meta", {}),
        "canvas": canvas,
        "layers": layers,
        "animations": animations,
        "render": render,
        "output": output,
        "warnings": [str(w) for w in result.warnings],
    }
