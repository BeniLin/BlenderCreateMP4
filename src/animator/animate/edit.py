"""S2-2：列出圖層、加入 move / scale / rotate / opacity 動畫（M1 + M2）。

- 動畫格式完全沿用 Stage 1 的 spec v1（docs/spec.md 3.4 / 4），例如：
  {"id", "target", "effect": "move", "start", "end", "params": {"by": [dx, dy]} 或 {"to": [x, y]}}
  scale {"to": s | [sx, sy]}、rotate {"by": 度} 或 {"to": 度}、opacity {"to": 0~1}；一律不寫 from / easing。
- 修改後的整份 spec 一律用 Stage 1 的 validate() 檢查；沒有通過就不寫入、也不建立備份。
- 通過之後：先把「原本的 spec.json（逐位元組）」備份到 history/edits/<timestamp>-spec.json，
  再用「同目錄暫存檔 + os.replace」原子性地寫入新的 spec。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from animator.config import Config
from animator.errors import AnimatorError, SpecError
from animator.jobs import SPEC_NAME
from animator.spec import load_spec
from animator.spec.png import read_png_info
from animator.spec.schema import frame_count, time_to_frame
from animator.spec.validate import validate

T = TypeVar("T")
EDITS_DIR = Path("history") / "edits"
BACKGROUND_ID = "background"


# ---------------------------------------------------------------- job

def resolve_job_spec(job: str | os.PathLike, cfg: Config) -> Path:
    """<job> 可以是：job 資料夾、job 裡的 spec.json，或 output_dir 底下的 job_id。"""
    p = Path(job)
    candidates = []
    if p.suffix.lower() == ".json":
        candidates.append(p)
    else:
        candidates.append(p / SPEC_NAME)
        if not p.is_absolute() and len(p.parts) == 1:
            candidates.append(cfg.path("output_dir") / p / SPEC_NAME)
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise AnimatorError(f"找不到 job：{job}（找過：{', '.join(str(c) for c in candidates)}）")


# ---------------------------------------------------------------- layers

def is_background(layer: dict) -> bool:
    """S2-1 的背景圖層：meta.role = "background"；或 Stage 1 慣例的 id "background"。"""
    meta = layer.get("meta") if isinstance(layer.get("meta"), dict) else {}
    return meta.get("role") == "background" or layer.get("id") == BACKGROUND_ID


@dataclass
class LayerInfo:
    id: str
    label: str | None
    z: float
    position: list[float]
    background: bool
    animations: int
    anchor: list[float] = field(default_factory=lambda: [0.5, 0.5])   # pivot 在圖層圖片上的位置（0~1）


def list_layers(raw: dict) -> list[LayerInfo]:
    canvas = raw.get("canvas", {})
    center = [canvas.get("width", 0) / 2, canvas.get("height", 0) / 2]
    counts: dict[str, int] = {}
    for anim in raw.get("animations", []) or []:
        if isinstance(anim, dict) and isinstance(anim.get("target"), str):
            counts[anim["target"]] = counts.get(anim["target"], 0) + 1
    out = []
    for index, layer in enumerate(raw.get("layers", []) or []):
        if not isinstance(layer, dict):
            continue
        meta = layer.get("meta") if isinstance(layer.get("meta"), dict) else {}
        out.append(LayerInfo(id=str(layer.get("id")), label=meta.get("label"), z=layer.get("z", index),
                             position=list(layer.get("position", center)), background=is_background(layer),
                             animations=counts.get(layer.get("id"), 0),
                             anchor=list(layer.get("anchor", [0.5, 0.5]))))
    return out


# ---------------------------------------------------------------- 參數

def parse_pair(text: str, name: str) -> list[float]:
    """"120,0" → [120.0, 0.0]；只接受兩個有限的數字。"""
    parts = [s.strip() for s in str(text).split(",")]
    try:
        values = [float(s) for s in parts]
    except ValueError:
        values = []
    if len(values) != 2 or not all(math.isfinite(v) for v in values):
        raise AnimatorError(f"{name} 必須是兩個數字，例如 120,0（目前是 {text!r}）")
    return [_tidy(v) for v in values]


def _tidy(v: float) -> int | float:
    return int(v) if float(v).is_integer() else v


def parse_number(text: str, name: str) -> int | float:
    """"-45" → -45；只接受一個有限的數字。"""
    try:
        value = float(str(text).strip())
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise AnimatorError(f"{name} 必須是一個數字，例如 90 或 -45（目前是 {text!r}）")
    return _tidy(value)


def parse_scale(text: str, name: str) -> int | float | list[int | float]:
    """"1.5" → 1.5（等比例）；"1.2,0.8" → [1.2, 0.8]。範圍（> 0）由 Stage 1 驗證。"""
    if "," in str(text):
        return parse_pair(text, name)
    try:
        return parse_number(text, name)
    except AnimatorError:
        raise AnimatorError(f"{name} 必須是一個數字或兩個數字，例如 1.5 或 1.2,0.8（目前是 {text!r}）") from None


# 每個效果可以用的參數（--by / --to）與數值格式。範圍、時間、通道重疊一律交給 Stage 1 的 validate。
# 不提供 from：起點由 Stage 1 依「這個通道目前的值」推算（docs/spec.md 3.4）。
EFFECT_OPTIONS: dict[str, dict[str, Any]] = {
    "move": {"by": parse_pair, "to": parse_pair},
    "scale": {"to": parse_scale},
    "rotate": {"by": parse_number, "to": parse_number},
    "opacity": {"to": parse_number},
}
EFFECTS = tuple(EFFECT_OPTIONS)


def _check_effect(effect: str) -> dict[str, Any]:
    if effect not in EFFECT_OPTIONS:
        raise AnimatorError(f"不支援的效果 {effect!r}（可用：{', '.join(EFFECTS)}）")
    return EFFECT_OPTIONS[effect]


def parse_effect_value(effect: str, key: str, text: str) -> Any:
    """CLI 的 --by / --to 文字 → params 的值；效果不支援這個參數時直接拒絕。"""
    options = _check_effect(effect)
    if key not in options:
        allowed = " / ".join(f"--{k}" for k in options)
        raise AnimatorError(f"{effect} 不支援 --{key}（只能用 {allowed}）")
    return options[key](text, f"--{key}")


def _check_params(effect: str, params: dict) -> None:
    options = _check_effect(effect)
    unsupported = [k for k in params if k not in options]
    if unsupported:
        allowed = " / ".join(f"--{k}" for k in options)
        raise AnimatorError(f"{effect} 不支援 {', '.join('--' + k for k in unsupported)}（只能用 {allowed}）")
    if len(params) != 1:
        if len(options) == 1:
            raise AnimatorError(f"{effect} 必須指定 --{next(iter(options))}")
        raise AnimatorError(f"{effect} 必須剛好指定 --by 或 --to 其中一個")


def _unique_anim_id(raw: dict, target: str, effect: str) -> str:
    used = {a.get("id") for a in raw.get("animations", []) or [] if isinstance(a, dict)}
    used |= {f"anim_{i}" for i in range(len(raw.get("animations", []) or []))}   # Stage 1 的預設 id
    n = 1
    while f"{target}-{effect}-{n}" in used:
        n += 1
    return f"{target}-{effect}-{n}"


# ---------------------------------------------------------------- 編輯

@dataclass
class EditResult:
    spec_path: Path
    animation: dict
    backup: Path
    warnings: list[str]


def add_move(spec_path: Path, layer_id: str, *, by: list[float] | None = None, to: list[float] | None = None,
             start: float, duration: float) -> EditResult:
    """M1 的介面（保留相容）：加入一筆 move。"""
    params = {k: v for k, v in (("by", by), ("to", to)) if v is not None}
    return add_animation(spec_path, layer_id, "move", params, start=start, duration=duration)


def add_animation(spec_path: Path, layer_id: str, effect: str, params: dict, *,
                  start: float, duration: float) -> EditResult:
    """在 spec 的 animations[] 最後加入一筆 move / scale / rotate / opacity。其他欄位、圖層、動畫都不變。

    params 只能有這個效果支援的 by / to（不寫 from、easing，沿用 Stage 1 的預設）。
    """
    _check_params(effect, params)
    for name, v in (("--start", start), ("--duration", duration)):
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise AnimatorError(f"{name} 必須是有限的數字（目前是 {v!r}）")
    if duration <= 0:
        raise AnimatorError(f"--duration 必須大於 0（目前是 {duration}）")

    def change(new: dict) -> dict:
        layer = find_layer(new, layer_id)
        if is_background(layer):
            raise AnimatorError(f"圖層 {layer_id!r} 是背景，不允許對背景加動畫")
        if "animations" not in new:
            new["animations"] = []
        elif not isinstance(new["animations"], list):
            raise AnimatorError("spec 的 animations 不是陣列，無法加入動畫")
        anim = {"id": _unique_anim_id(new, layer_id, effect), "target": layer_id, "effect": effect,
                "start": _tidy(start), "end": _tidy(round(start + duration, 6)), "params": dict(params)}
        new["animations"].append(anim)
        return anim

    spec_path = Path(spec_path)
    anim, backup, warnings = edit_spec(spec_path, change)
    return EditResult(spec_path=spec_path, animation=anim, backup=backup, warnings=warnings)


def find_layer(spec: dict, layer_id: str) -> dict:
    layer = next((l for l in spec["layers"] if isinstance(l, dict) and l.get("id") == layer_id), None)
    if layer is None:
        ids = ", ".join(str(l.get("id")) for l in spec["layers"] if isinstance(l, dict))
        raise AnimatorError(f"找不到圖層 {layer_id!r}（可用：{ids}）")
    return layer


def edit_spec(spec_path: Path, change: Callable[[dict], T], *,
              skip_if_unchanged: bool = False) -> tuple[T, Path | None, list[str]]:
    """所有編輯共用的安全流程（M1 起）：

    讀取原本的 bytes → 解析 → 在深拷貝上呼叫 change(new)（可以 raise AnimatorError 拒絕）
    → 整份用 Stage 1 的 validate 檢查 → 確認檔案沒有被其他程式改過 → 備份 → 原子寫入。
    任何一步失敗都不會改到原本的 spec，也不會留下備份或暫存檔。回傳 (change 的回傳值, 備份路徑, 警告)。

    skip_if_unchanged=True（M3b 起，選用）：change 之後內容和原本完全相同時，不寫入、不建立備份，
    回傳的備份路徑是 None。預設 False，M1 / M2 / M3a 的行為不變。
    """
    spec_path = Path(spec_path)
    original_bytes = spec_path.read_bytes()
    raw = load_spec(spec_path)
    if not isinstance(raw, dict) or not isinstance(raw.get("layers"), list):
        raise AnimatorError(f"{spec_path} 不是有效的 spec（缺少 layers）")

    new = json.loads(json.dumps(raw))               # 深拷貝：原本的 raw 不會被修改
    value = change(new)
    if skip_if_unchanged and new == raw:
        return value, None, []

    result = validate(new, spec_path.parent)          # Stage 1 的驗證：時間、duration、通道重疊…
    if not result.ok:
        raise SpecError(result.errors)

    backup = write_spec_with_backup(spec_path, original_bytes, new)
    return value, backup, [str(w) for w in result.warnings]


# ---------------------------------------------------------------- 前後順序（M3a）

@dataclass
class ZEditResult:
    spec_path: Path
    layer_id: str
    old_z: float
    new_z: float
    order: list[str]              # 渲染順序：由後往前
    backup: Path
    warnings: list[str]


def _effective_z(layer: dict, index: int) -> float:
    """Stage 1 的規則（docs/spec.md 2.5）：沒有寫 z 時是它在 layers 陣列裡的索引。

    一律換成 float 再比較：Stage 1 的 resolve 用 float(z) 排序（resolve.py），
    大於 2**53 的整數可能變成同一個 float，比較結果必須和實際渲染一致。
    """
    z = layer.get("z", index)
    try:
        value = float(z) if isinstance(z, (int, float)) and not isinstance(z, bool) else math.nan
    except OverflowError:                       # 超過 float 範圍的整數
        value = math.inf
    if not math.isfinite(value):
        raise AnimatorError(f"圖層 {layer.get('id')!r} 的 z 不是有限的數字（{z!r}），無法計算前後順序")
    return value


def render_order(spec: dict) -> list[tuple[float, int, dict]]:
    """和 Stage 1 resolve 相同的排序：(z, 陣列索引)，由後往前。"""
    rows = [(_effective_z(l, i), i, l) for i, l in enumerate(spec["layers"]) if isinstance(l, dict)]
    return sorted(rows, key=lambda r: (r[0], r[1]))


def _between(low: float, high: float, what: str) -> int | float:
    if low == high:
        raise AnimatorError(f"{what} 的 z 值相同（{_tidy(low)}），無法插在中間；請改用 --z 指定數值")
    mid = (low + high) / 2
    if not low < mid < high:
        raise AnimatorError(f"{what} 的 z 值（{low!r}、{high!r}）之間沒有可以表示的數值；請改用 --z 指定數值")
    return _tidy(mid)


def set_layer_z(spec_path: Path, layer_id: str, *, z: float | None = None, above: str | None = None,
                below: str | None = None) -> ZEditResult:
    """修改圖層的 z（越大越前面），並把 meta.z_rule 設成 "manual"。不會重新編號其他圖層。

    --above / --below 會選一個嚴格介於參考圖層和相鄰圖層之間的 z；相鄰的 z 相同（或之間沒有數值）時拒絕。
    物件圖層的 z 必須大於背景的 z（背景是不透明的）。
    """
    if sum(v is not None for v in (z, above, below)) != 1:
        raise AnimatorError("必須剛好指定 --z、--above、--below 其中一個")
    if z is not None:
        try:
            finite = not isinstance(z, bool) and isinstance(z, (int, float)) and math.isfinite(float(z))
        except OverflowError:                   # 超過 float 範圍的整數
            finite = False
        if not finite:
            raise AnimatorError(f"--z 必須是有限的數字（目前是 {z!r}）")
    ref_id = above if above is not None else below
    if ref_id is not None and ref_id == layer_id:
        raise AnimatorError(f"--{'above' if above is not None else 'below'} 不能是圖層自己（{layer_id!r}）")

    def change(new: dict) -> tuple[float, float, list[str]]:
        layer = find_layer(new, layer_id)
        if is_background(layer):
            raise AnimatorError(f"圖層 {layer_id!r} 是背景，不允許修改背景的前後順序")
        order = render_order(new)
        old_z = next(r[0] for r in order if r[2] is layer)
        backgrounds = [r[0] for r in order if is_background(r[2])]
        others = [r for r in order if r[2] is not layer]         # 目標圖層以外的順序

        if z is not None:
            new_z = _tidy(z)
        else:
            ref = find_layer(new, ref_id)
            k = next(i for i, r in enumerate(others) if r[2] is ref)
            ref_z = others[k][0]
            if above is not None:
                if k + 1 < len(others):
                    nxt = others[k + 1]
                    new_z = _between(ref_z, nxt[0], f"{ref_id!r} 和它前面的 {nxt[2].get('id')!r}")
                else:
                    new_z = _tidy(ref_z + 10)                    # 參考圖層已經在最前面
                    if not new_z > ref_z:
                        raise AnimatorError(f"{ref_id!r} 的 z（{ref_z!r}）太大，找不到更前面的數值；請改用 --z")
            else:
                if k > 0:
                    prev = others[k - 1]
                    new_z = _between(prev[0], ref_z, f"{ref_id!r} 和它後面的 {prev[2].get('id')!r}")
                else:
                    new_z = _tidy(ref_z - 10)                    # 參考圖層已經在最後面
                    if not new_z < ref_z:
                        raise AnimatorError(f"{ref_id!r} 的 z（{ref_z!r}）太小，找不到更後面的數值；請改用 --z")
        if backgrounds and float(new_z) <= max(backgrounds):     # 和 Stage 1 一樣用 float 比較
            raise AnimatorError(f"z = {new_z} 不能小於或等於背景的 z（{_tidy(max(backgrounds))}）："
                                "背景是不透明的，物件會被擋住看不到")

        meta = layer.get("meta")
        if meta is None:
            layer["meta"] = meta = {}
        elif not isinstance(meta, dict):
            raise AnimatorError(f"圖層 {layer_id!r} 的 meta 不是物件，無法記錄 z_rule")
        layer["z"] = new_z
        meta["z_rule"] = "manual"
        return old_z, new_z, [str(r[2].get("id")) for r in render_order(new)]

    spec_path = Path(spec_path)
    (old_z, new_z, order), backup, warnings = edit_spec(spec_path, change)
    return ZEditResult(spec_path=spec_path, layer_id=layer_id, old_z=old_z, new_z=new_z, order=order,
                       backup=backup, warnings=warnings)


# ---------------------------------------------------------------- Pivot（M3b）
#
# Stage 1 的座標（layers.py / units.py，見 docs/spec.md 2.2）：圖層圖片的像素 (u, v)（v 往下）在畫布上的位置是
#     canvas = P + R(φ)·S·((u, v) − anchor ⊙ (W, H))
# P = position（anchor 點的畫布座標）、S = diag(sx, sy)、φ = rotation（度，順時針為正）、
# R(φ) = [[cos φ, −sin φ], [sin φ, cos φ]]（y 往下的座標）。Blender 的物件原點就是 anchor。

def _scale_pair_of(value: Any) -> tuple[float, float]:
    if isinstance(value, (list, tuple)):
        return float(value[0]), float(value[1])
    return float(value), float(value)


def image_to_canvas(u: float, v: float, position, anchor, size, scale, rotation: float) -> tuple[float, float]:
    """圖層圖片像素座標 (u, v) → 畫布座標（Stage 1 的 frame-1 / 初始姿勢）。"""
    sx, sy = _scale_pair_of(scale)
    du, dv = (u - anchor[0] * size[0]) * sx, (v - anchor[1] * size[1]) * sy
    c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    return position[0] + c * du - s * dv, position[1] + s * du + c * dv


def canvas_to_image(x: float, y: float, position, anchor, size, scale, rotation: float) -> tuple[float, float]:
    """image_to_canvas 的反函數：M⁻¹ = S⁻¹·R(−φ)。"""
    sx, sy = _scale_pair_of(scale)
    dx, dy = x - position[0], y - position[1]
    c, s = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    du, dv = c * dx + s * dy, -s * dx + c * dy
    return anchor[0] * size[0] + du / sx, anchor[1] * size[1] + dv / sy


ANCHOR_EPS = 1e-9                     # 浮點誤差：離 0 / 1 這麼近時視為剛好在邊上


@dataclass
class PivotEditResult:
    spec_path: Path
    layer_id: str
    changed: bool
    old_anchor: list[float]
    new_anchor: list[float]
    old_position: list[float]
    new_position: list[float]
    offset: list[float]               # 位移向量 c = P' − P（move.to / move.from 都加上它）
    rewritten: list[str]              # 改寫了 to / from 的 move 動畫 id
    backup: Path | None               # 沒有變更時是 None（沒有寫入、沒有備份）
    warnings: list[str]


def _finite_pair(value: Any, what: str) -> list[float]:
    ok = (isinstance(value, (list, tuple)) and len(value) == 2
          and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value))
    if not ok:
        raise AnimatorError(f"{what} 必須是兩個有限的數字（目前是 {value!r}）")
    return [float(value[0]), float(value[1])]


def _check_frame1_transform(spec: dict, layer_id: str, scale, rotation: float) -> None:
    """compensation 用的是圖層的初始 scale / rotation；如果第 1 格就被明確的 from 改掉，第 1 格會跳動 → 拒絕。"""
    canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    fps, duration = canvas.get("fps", 30), canvas.get("duration")
    if not (isinstance(fps, int) and fps > 0 and isinstance(duration, (int, float)) and duration > 0):
        return                                              # 畫布本身不合法：交給 Stage 1 的驗證報錯
    frame_end = frame_count(duration, fps)
    for i, anim in enumerate(spec.get("animations", []) or []):
        if not (isinstance(anim, dict) and anim.get("target") == layer_id
                and anim.get("effect") in ("scale", "rotate") and anim.get("enabled", True) is not False):
            continue
        params, start = anim.get("params"), anim.get("start")
        if not isinstance(params, dict) or "from" not in params or not isinstance(start, (int, float)):
            continue
        if time_to_frame(start, fps, frame_end) != 1:
            continue
        try:
            differs = (_scale_pair_of(params["from"]) != _scale_pair_of(scale) if anim["effect"] == "scale"
                       else float(params["from"]) != float(rotation))
        except (TypeError, ValueError, IndexError):
            continue                                        # 格式錯誤：交給 Stage 1 的驗證報錯
        if differs:
            raise AnimatorError(
                f"動畫 {anim.get('id', f'anim_{i}')!r}（{anim['effect']}）在第 1 格就用明確的 from 改變了圖層的"
                f"{'縮放' if anim['effect'] == 'scale' else '旋轉'}，第 1 格的姿勢和圖層的初始值不同，"
                "無法保證修改 pivot 後畫面不跳動；請先移除那筆動畫的 from，或讓它晚一點開始")


def set_layer_pivot(spec_path: Path, layer_id: str, at: list[float]) -> PivotEditResult:
    """把圖層的 pivot（= Stage 1 的 anchor）移到畫布座標 at（第 1 格的姿勢），畫面不跳動。

    - 新的 position 剛好等於 at；新的 anchor 用初始 scale / rotation 的反轉換算出來，必須在 [0, 1] 內。
    - 這個圖層所有 move 的 to / from（包括 enabled: false 的）都加上同一個位移 c = at − 原本的 position；
      move.by 不變。scale / rotate / opacity 的動畫、其他圖層都不變。
    - 結果和原本完全相同時不寫入、不建立備份（changed = False）。
    """
    q = _finite_pair(at, "--at")
    spec_path = Path(spec_path)

    def change(new: dict) -> PivotEditResult:
        layer = find_layer(new, layer_id)
        if is_background(layer):
            raise AnimatorError(f"圖層 {layer_id!r} 是背景，不允許修改背景的 pivot")
        image = layer.get("image")
        if not isinstance(image, str) or not image:
            raise AnimatorError(f"圖層 {layer_id!r} 沒有 image，無法計算 pivot")
        image_path = Path(image) if Path(image).is_absolute() else spec_path.parent / image
        try:
            info = read_png_info(image_path)
        except (OSError, ValueError) as exc:
            raise AnimatorError(f"無法讀取圖層 {layer_id!r} 的圖片 {image_path}：{exc}") from None
        size = (info.width, info.height)

        canvas = new.get("canvas") if isinstance(new.get("canvas"), dict) else {}
        try:                                                # Stage 1 的預設值（resolve.py）
            position = _finite_pair(layer.get("position", [canvas["width"] / 2, canvas["height"] / 2]),
                                    f"圖層 {layer_id!r} 的 position")
        except (KeyError, TypeError):
            raise AnimatorError("spec 的 canvas 缺少 width / height，無法計算 pivot") from None
        anchor = _finite_pair(layer.get("anchor", [0.5, 0.5]), f"圖層 {layer_id!r} 的 anchor")
        scale, rotation = layer.get("scale", 1), layer.get("rotation", 0)
        try:
            sx, sy = _scale_pair_of(scale)
            rotation = float(rotation)
        except (TypeError, ValueError, IndexError):
            raise AnimatorError(f"圖層 {layer_id!r} 的 scale / rotation 格式錯誤") from None
        if not (math.isfinite(sx) and math.isfinite(sy) and sx > 0 and sy > 0 and math.isfinite(rotation)):
            raise AnimatorError(f"圖層 {layer_id!r} 的 scale 必須大於 0、rotation 必須是有限的數字")
        _check_frame1_transform(new, layer_id, scale, rotation)

        if q == position:                                   # pivot 已經在這裡（避免浮點誤差改動 anchor）
            new_anchor = list(anchor)
        else:
            u, v = canvas_to_image(q[0], q[1], position, anchor, size, scale, rotation)
            new_anchor = []
            for value, length, axis in ((u, size[0], "x"), (v, size[1], "y")):
                a = value / length
                if -ANCHOR_EPS <= a < 0 or 1 < a <= 1 + ANCHOR_EPS:
                    a = min(1.0, max(0.0, a))              # 剛好在圖片邊上（浮點誤差）
                if not 0 <= a <= 1:
                    raise AnimatorError(
                        f"pivot ({_tidy(q[0])}, {_tidy(q[1])}) 不在圖層 {layer_id!r} 的圖片範圍內："
                        f"換算成圖片座標是 ({u:.3f}, {v:.3f})，圖片大小 {size[0]}×{size[1]}")
                new_anchor.append(_tidy(a))
        offset = [_tidy(q[0] - position[0]), _tidy(q[1] - position[1])]
        new_position = [_tidy(q[0]), _tidy(q[1])]
        old = PivotEditResult(spec_path, layer_id, False, [_tidy(a) for a in anchor], new_anchor,
                              [_tidy(p) for p in position], new_position, offset, [], None, [])
        if new_anchor == old.old_anchor and new_position == old.old_position:
            return old                                      # 沒有變更：不改任何欄位

        layer["position"] = new_position
        layer["anchor"] = new_anchor
        rewritten = []
        for i, anim in enumerate(new.get("animations", []) or []):
            if not (isinstance(anim, dict) and anim.get("target") == layer_id and anim.get("effect") == "move"
                    and isinstance(anim.get("params"), dict)):
                continue
            params, touched = anim["params"], False
            for key in ("to", "from"):                      # 絕對座標才要平移；by 是位移，不變
                value = params.get(key)
                if (isinstance(value, list) and len(value) == 2
                        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)):
                    params[key] = [_tidy(value[0] + offset[0]), _tidy(value[1] + offset[1])]
                    touched = True
            if touched:
                rewritten.append(str(anim.get("id", f"anim_{i}")))
        old.changed, old.rewritten = True, rewritten
        return old

    res, backup, warnings = edit_spec(spec_path, change, skip_if_unchanged=True)
    res.backup, res.warnings = backup, warnings
    return res


def _timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S-") + f"{now.microsecond // 1000:03d}"


def backup_spec(spec_path: Path, original_bytes: bytes) -> Path:
    """把原本的 spec（逐位元組）存到 history/edits/<timestamp>-spec.json；同一毫秒內的多次編輯加上 -2、-3…"""
    d = spec_path.parent / EDITS_DIR
    d.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp()
    n = 1
    while True:
        name = f"{stamp}-spec.json" if n == 1 else f"{stamp}-{n}-spec.json"
        path = d / name
        try:
            fh = open(path, "xb")                     # 只建立新檔，絕不覆蓋既有的備份
        except FileExistsError:
            n += 1
            continue
        try:                                          # 檔案是這次建立的：寫入失敗就刪掉，不留半個備份
            with fh:
                fh.write(original_bytes)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path


def atomic_write_json(path: Path, data: Any) -> None:
    """寫到同一個資料夾的暫存檔 → flush + fsync → os.replace（同一個磁碟上是原子操作）。"""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        try:
            fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        except BaseException:
            os.close(fd)                              # fdopen 失敗時 fd 還開著：先關掉，暫存檔才刪得掉
            raise
        with fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_spec_with_backup(spec_path: Path, original_bytes: bytes, new: dict) -> Path:
    """先備份、再原子寫入；寫入失敗時刪掉剛建立的備份（避免留下「好像編輯過」的備份），原檔不變。"""
    if spec_path.read_bytes() != original_bytes:
        raise AnimatorError(f"{spec_path} 在編輯期間被其他程式修改，已取消（請重新執行）")
    backup = backup_spec(spec_path, original_bytes)
    try:
        atomic_write_json(spec_path, new)
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup
