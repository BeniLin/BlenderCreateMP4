"""把已驗證的 spec.json 轉成 Blender 端讀取的 spec.resolved.json。

resolve 完成後：
  - 所有預設值都已補齊（spec 的值 > config defaults > 內建預設值）
  - 圖片路徑轉成絕對路徑，並附上 PNG 尺寸
  - 圖層依前後順序排好，並標上 rank（0 = 最後面）
  - visible=false 的圖層、enabled=false 的動畫已經移除
    （例外：visible=false 但有啟用的 visibility 動畫的圖層會保留，visibility = 0 是它的初始狀態）
  - 每個圖層都有 visibility（1.0 = 顯示、0.0 = 隱藏），Blender 端用它當 visibility 通道的初始值
  - visibility 動畫已換算成離散的區段：from = 區間裡的狀態，to = 區間結束後的狀態（easing 一律 constant）
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


def _visibility_segments(pending: list, by_id: dict, fps: int, frame_end: int) -> dict[int, dict]:
    """visibility 區間 → Blender 端的離散區段（docs/spec.md 4「visibility」）。

    每個區間 [start, end) 是一個區段：frame_start = frame(start)，值是區間的狀態；
    在 frame(end) 恢復成圖層的初始狀態——除非下一個區間剛好從同一格開始（那就直接用下一個區間的狀態，
    同一格不會有兩個不同的值），或 end 落在最後一格之後（區間持續到最後一格，不恢復）。
    狀態和初始值相同的區間沒有效果，不產生區段。回傳 {動畫索引: resolved 動畫}。
    """
    windows: dict[str, list] = {}
    for start, index, anim in pending:                       # pending 已經依 (start, 索引) 排好
        if anim["effect"] == "visibility":
            windows.setdefault(anim["target"], []).append((index, anim))
    out: dict[int, dict] = {}
    for target, items in windows.items():
        base = by_id[target]["visibility"]
        frames = [(S.time_to_frame(float(a["start"]), fps, frame_end),
                   S.window_end_frame(float(a["end"]), fps, frame_end),
                   1.0 if a["params"]["visible"] else 0.0) for _, a in items]
        for k, ((index, anim), (f0, f1, state)) in enumerate(zip(items, frames)):
            if state == base:
                continue
            if f1 > frame_end:                               # 持續到最後一格
                f1, after = frame_end, state
            else:
                nxt = frames[k + 1] if k + 1 < len(frames) else None
                after = nxt[2] if nxt and nxt[0] == f1 else base
            out[index] = {
                "id": anim.get("id", f"anim_{index}"),
                "target": target,
                "effect": "visibility",
                "source_effect": "visibility",
                "channel": "visibility",
                "start": float(anim["start"]),
                "end": float(anim["end"]),
                "frame_start": f0,
                "frame_end": f1,
                "easing": S.VISIBILITY_EASING,
                "from": [state],
                "to": [after],
            }
    return out


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
    enabled_anims = [a for a in raw.get("animations", []) if a.get("enabled", True) is not False]
    has_visibility = {a["target"] for a in enabled_anims if a["effect"] == "visibility"}
    layers: list[dict] = []
    for index, layer in enumerate(raw["layers"]):
        base_visible = layer.get("visible", True) is not False
        if not base_visible and layer["id"] not in has_visibility:
            continue                    # 和以前一樣：隱藏、而且沒有 visibility 動畫 → 整個圖層（含動畫）移除
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
            "visibility": 1.0 if base_visible else 0.0,
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

    visibility = _visibility_segments(pending, by_id, fps, frame_end)
    current: dict[tuple[str, str], list[float]] = {}
    animations: list[dict] = []
    for start, index, anim in pending:
        if anim["effect"] == "visibility":
            if index in visibility:                # 狀態和初始值相同的區間沒有效果，不產生關鍵影格
                animations.append(visibility[index])
            continue
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
