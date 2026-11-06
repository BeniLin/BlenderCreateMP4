"""spec.json 驗證：一次列出所有錯誤與警告，並標示 JSON 路徑（docs/spec.md 第 8 節）。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from animator.spec import schema as S
from animator.spec.png import read_png_info

_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


@dataclass
class ValidationResult:
    errors: list[S.Issue] = field(default_factory=list)
    warnings: list[S.Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------- helpers

def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_pair(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and all(is_num(x) for x in v)


def parse_color(v: Any) -> tuple[float, float, float, float] | None:
    """回傳 sRGB 0~1 的 RGBA；格式不合時回傳 None。"""
    if isinstance(v, str) and _HEX_COLOR.match(v):
        h = v[1:]
        vals = [int(h[i:i + 2], 16) / 255 for i in range(0, len(h), 2)]
        if len(vals) == 3:
            vals.append(1.0)
        return tuple(vals)  # type: ignore[return-value]
    if isinstance(v, list) and len(v) in (3, 4) and all(is_num(x) and 0 <= x <= 1 for x in v):
        vals = [float(x) for x in v] + ([1.0] if len(v) == 3 else [])
        return tuple(vals)  # type: ignore[return-value]
    return None


class _Ctx:
    def __init__(self) -> None:
        self.result = ValidationResult()

    def err(self, path: str, msg: str) -> None:
        self.result.errors.append(S.Issue(path, msg))

    def warn(self, path: str, msg: str) -> None:
        self.result.warnings.append(S.Issue(path, msg))

    def unknown_keys(self, obj: dict, known: set[str], path: str) -> None:
        for key in obj:
            if key not in known:
                self.warn(f"{path}.{key}" if path else key, "不認識的欄位（已忽略，請檢查是否打錯字）")


def _opt_bool(c: _Ctx, obj: dict, key: str, path: str) -> None:
    if key in obj and not isinstance(obj[key], bool):
        c.err(f"{path}.{key}", "必須是 true 或 false")


def _scale_ok(v: Any) -> bool:
    return (is_num(v) and v > 0) or (is_pair(v) and all(x > 0 for x in v))


# ---------------------------------------------------------------- sections

def _canvas(c: _Ctx, raw: dict) -> dict | None:
    canvas = raw.get("canvas")
    if not isinstance(canvas, dict):
        c.err("canvas", "必填，且必須是物件")
        return None
    c.unknown_keys(canvas, S.CANVAS_KEYS, "canvas")
    for key in ("width", "height"):
        v = canvas.get(key)
        if v is None:
            c.err(f"canvas.{key}", "必填")
        elif not is_int(v) or not 16 <= v <= 8192:
            c.err(f"canvas.{key}", "必須是 16~8192 的整數")
        elif v % 2:
            c.err(f"canvas.{key}", "必須是偶數（yuv420p 的要求）")
    if "fps" in canvas and (not is_int(canvas["fps"]) or not 1 <= canvas["fps"] <= 120):
        c.err("canvas.fps", "必須是 1~120 的整數")
    d = canvas.get("duration")
    if d is None:
        c.err("canvas.duration", "必填")
    elif not is_num(d) or d <= 0:
        c.err("canvas.duration", "必須是大於 0 的秒數")
    if "background_color" in canvas and parse_color(canvas["background_color"]) is None:
        c.err("canvas.background_color", "顏色格式應為 #RRGGBB、#RRGGBBAA 或 [r,g,b(,a)]（0~1）")
    return canvas


def _layers(c: _Ctx, raw: dict, spec_dir: Path) -> dict[str, dict]:
    layers = raw.get("layers")
    found: dict[str, dict] = {}
    if not isinstance(layers, list) or not layers:
        c.err("layers", "必填，且至少要有 1 個圖層")
        return found
    for i, layer in enumerate(layers):
        p = f"layers[{i}]"
        if not isinstance(layer, dict):
            c.err(p, "必須是物件")
            continue
        c.unknown_keys(layer, S.LAYER_KEYS | S.LAYER_RESERVED, p)
        for key in S.LAYER_RESERVED & layer.keys():
            c.err(f"{p}.{key}", "v1 尚未支援此欄位")

        lid = layer.get("id")
        if not isinstance(lid, str) or not S.ID_PATTERN.match(lid):
            c.err(f"{p}.id", "必填，格式須符合 ^[a-z0-9][a-z0-9_-]*$")
        elif lid in found:
            c.err(f"{p}.id", f"圖層 id 重複：{lid!r}")
        else:
            found[lid] = layer

        image = layer.get("image")
        if not isinstance(image, str) or not image:
            c.err(f"{p}.image", "必填")
        else:
            img_path = Path(image)
            if img_path.is_absolute():
                c.warn(f"{p}.image", "使用絕對路徑，這份 spec 換到其他電腦時會無法使用")
            else:
                img_path = spec_dir / img_path
            if not img_path.is_file():
                c.err(f"{p}.image", f"找不到圖片：{img_path}")
            else:
                try:
                    info = read_png_info(img_path)
                    if not info.has_alpha:
                        c.warn(f"{p}.image", "PNG 沒有 alpha 通道，整張圖會被視為不透明")
                except (OSError, ValueError) as exc:
                    c.err(f"{p}.image", f"無法讀取 PNG：{exc}")

        if "position" in layer and not is_pair(layer["position"]):
            c.err(f"{p}.position", "必須是 [x, y]（像素）")
        if "anchor" in layer and not (is_pair(layer["anchor"]) and all(0 <= v <= 1 for v in layer["anchor"])):
            c.err(f"{p}.anchor", "必須是 [ax, ay]，數值 0~1")
        if "z" in layer and not is_num(layer["z"]):
            c.err(f"{p}.z", "必須是數字")
        if "scale" in layer and not _scale_ok(layer["scale"]):
            c.err(f"{p}.scale", "必須是大於 0 的數字，或 [sx, sy]")
        if "rotation" in layer and not is_num(layer["rotation"]):
            c.err(f"{p}.rotation", "必須是數字（度）")
        if "opacity" in layer and not (is_num(layer["opacity"]) and 0 <= layer["opacity"] <= 1):
            c.err(f"{p}.opacity", "必須是 0~1")
        _opt_bool(c, layer, "visible", p)
        if "meta" in layer and not isinstance(layer["meta"], dict):
            c.err(f"{p}.meta", "必須是物件")
    return found


def _params(c: _Ctx, effect: str, params: dict, p: str) -> None:
    if effect == "visibility":                 # 只接受 visible；from / to / by 等其他欄位一律是錯誤（不是警告）
        for key in sorted(set(params) - S.PARAM_KEYS["visibility"]):
            c.err(f"{p}.{key}", "visibility 只接受 visible（true / false），不支援這個欄位")
        if "visible" not in params:
            c.err(f"{p}.visible", "必填（true = 顯示、false = 隱藏）")
        elif not isinstance(params["visible"], bool):
            c.err(f"{p}.visible", "必須是 true 或 false")
        return
    c.unknown_keys(params, S.PARAM_KEYS[effect], p)

    def need_one_of_to_by(check) -> None:
        has_to, has_by = "to" in params, "by" in params
        if has_to == has_by:
            c.err(p, "`to` 和 `by` 必須剛好擇一")
        for key in ("to", "by", "from"):
            if key in params and not check(params[key]):
                c.err(f"{p}.{key}", "格式錯誤")

    if effect == "move":
        need_one_of_to_by(is_pair)
    elif effect == "rotate":
        need_one_of_to_by(is_num)
    elif effect == "scale":
        if "to" not in params:
            c.err(f"{p}.to", "必填")
        for key in ("to", "from"):
            if key in params and not _scale_ok(params[key]):
                c.err(f"{p}.{key}", "必須是大於 0 的數字，或 [sx, sy]")
    else:  # opacity / fade_in / fade_out
        if effect == "opacity" and "to" not in params:
            c.err(f"{p}.to", "必填")
        for key in ("to", "from"):
            if key in params and not (is_num(params[key]) and 0 <= params[key] <= 1):
                c.err(f"{p}.{key}", "必須是 0~1")


def _animations(c: _Ctx, raw: dict, canvas: dict | None, layers: dict[str, dict]) -> None:
    anims = raw.get("animations", [])
    if not isinstance(anims, list):
        c.err("animations", "必須是陣列")
        return

    duration = canvas.get("duration") if canvas else None
    fps = canvas.get("fps", 30) if canvas else 30
    timing_ok = is_num(duration) and duration > 0 and is_int(fps) and fps > 0
    frame_end = S.frame_count(duration, fps) if timing_ok else 0

    ids: set[str] = set()
    timeline: dict[tuple[str, str], list[tuple[float, float, int, int, str]]] = {}

    for i, anim in enumerate(anims):
        p = f"animations[{i}]"
        if not isinstance(anim, dict):
            c.err(p, "必須是物件")
            continue
        c.unknown_keys(anim, S.ANIM_KEYS, p)

        aid = anim.get("id")
        if aid is not None:
            if not isinstance(aid, str) or not aid:
                c.err(f"{p}.id", "必須是非空字串")
            elif aid in ids:
                c.err(f"{p}.id", f"動畫 id 重複：{aid!r}")
            else:
                ids.add(aid)

        target = anim.get("target")
        if not isinstance(target, str):
            c.err(f"{p}.target", "必填")
            target = None
        elif target.startswith("@"):
            c.err(f"{p}.target", f"v1 尚未支援系統 target {target!r}")
            target = None
        elif target not in layers:
            c.err(f"{p}.target", f"找不到圖層 {target!r}")
            target = None

        effect = anim.get("effect")
        if not isinstance(effect, str):
            c.err(f"{p}.effect", "必填")
            effect = None
        elif effect in S.RESERVED_EFFECTS:
            c.err(f"{p}.effect", f"效果 {effect!r} 是保留名稱，v1 尚未支援")
            effect = None
        elif effect not in S.PARAM_KEYS:
            c.err(f"{p}.effect", f"未知的效果 {effect!r}")
            effect = None

        start, end = anim.get("start"), anim.get("end")
        times_ok = True
        if not is_num(start):
            c.err(f"{p}.start", "必填，必須是秒數")
            times_ok = False
        if not is_num(end):
            c.err(f"{p}.end", "必填，必須是秒數")
            times_ok = False
        if times_ok:
            if start < 0:
                c.err(f"{p}.start", "不可以小於 0")
                times_ok = False
            if end <= start:
                c.err(f"{p}.end", "必須大於 start")
                times_ok = False
            if timing_ok and end > duration + S.EPS:
                c.err(f"{p}.end", f"超過 canvas.duration（{duration} 秒）")
                times_ok = False

        if "easing" in anim and anim["easing"] not in S.EASINGS:
            c.err(f"{p}.easing", f"不支援的 easing {anim['easing']!r}")
        elif effect == "visibility" and anim.get("easing", S.VISIBILITY_EASING) != S.VISIBILITY_EASING:
            c.err(f"{p}.easing", "visibility 是離散的（沒有淡入淡出），easing 只能省略或寫 \"constant\"；"
                                 "要淡入淡出請用 opacity")
        _opt_bool(c, anim, "enabled", p)

        params = anim.get("params", {})
        if not isinstance(params, dict):
            c.err(f"{p}.params", "必須是物件")
        elif effect:
            _params(c, effect, params, f"{p}.params")
            if (effect == "visibility" and target and isinstance(params.get("visible"), bool)
                    and params["visible"] == (layers[target].get("visible", True) is not False)):
                c.warn(p, f"圖層 {target!r} 本來就是{'顯示' if params['visible'] else '隱藏'}的，這筆 visibility 沒有效果")

        if times_ok and timing_ok:
            f0 = S.time_to_frame(start, fps, frame_end)
            if effect == "visibility":                       # [start, end)：end 可以落在最後一格之後
                f1 = S.window_end_frame(end, fps, frame_end)
            else:
                f1 = S.time_to_frame(end, fps, frame_end)
            if f1 <= f0:
                c.err(p, f"換算成影格後不到 1 格（frame {f0} → {f1}），請拉長時間")
            elif target and effect and anim.get("enabled", True) is not False:
                key = (target, S.effect_channel(effect))
                timeline.setdefault(key, []).append((start, end, f0, f1, p))

    for (target, channel), items in timeline.items():
        items.sort()
        for prev, cur in zip(items, items[1:]):
            if cur[0] < prev[1] - S.EPS or cur[2] < prev[3]:
                c.err(cur[4], f"與 {prev[4]} 在圖層 {target!r} 的 {channel} 通道上時間重疊")


def _render_output(c: _Ctx, raw: dict) -> None:
    render = raw.get("render", {})
    if not isinstance(render, dict):
        c.err("render", "必須是物件")
    else:
        c.unknown_keys(render, S.RENDER_KEYS, "render")
        if "engine" in render and render["engine"] not in S.ENGINES:
            c.err("render.engine", f"必須是 {' / '.join(S.ENGINES)}")
        if "samples" in render and (not is_int(render["samples"]) or not 1 <= render["samples"] <= 4096):
            c.err("render.samples", "必須是 1~4096 的整數")
        rp = render.get("resolution_percentage")
        if rp is not None and (not is_int(rp) or not 1 <= rp <= 100):
            c.err("render.resolution_percentage", "必須是 1~100 的整數")
        if "view_transform" in render and not isinstance(render["view_transform"], str):
            c.err("render.view_transform", "必須是字串")
        _opt_bool(c, render, "film_transparent", "render")
        if render.get("film_transparent") is True:
            c.warn("render.film_transparent", "v1 輸出 MP4 不支援透明背景，會當成 false 處理")

    output = raw.get("output", {})
    if not isinstance(output, dict):
        c.err("output", "必須是物件")
        return
    c.unknown_keys(output, S.OUTPUT_KEYS, "output")
    name = output.get("filename")
    if name is not None and (not isinstance(name, str) or not re.match(r"^[\w.-]+\.mp4$", name)):
        c.err("output.filename", "必須是 .mp4 檔名，不可以包含路徑")
    crf = output.get("crf")
    if crf is not None and (not is_int(crf) or not 0 <= crf <= 51):
        c.err("output.crf", "必須是 0~51 的整數")
    for key in ("codec", "preset", "pix_fmt"):
        if key in output and not isinstance(output[key], str):
            c.err(f"output.{key}", "必須是字串")


# ---------------------------------------------------------------- entry

def validate(raw: Any, spec_dir: Path) -> ValidationResult:
    c = _Ctx()
    if not isinstance(raw, dict):
        c.err("", "spec 最上層必須是 JSON 物件")
        return c.result
    if raw.get("version") != S.SPEC_VERSION:
        c.err("version", f"必須是 {S.SPEC_VERSION}，目前是 {raw.get('version')!r}")
    c.unknown_keys(raw, S.TOP_KEYS, "")
    if "meta" in raw and not isinstance(raw["meta"], dict):
        c.err("meta", "必須是物件")
    canvas = _canvas(c, raw)
    layers = _layers(c, raw, spec_dir)
    _animations(c, raw, canvas, layers)
    _render_output(c, raw)
    return c.result
