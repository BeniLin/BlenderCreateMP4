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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from animator.config import Config
from animator.errors import AnimatorError, SpecError
from animator.jobs import SPEC_NAME
from animator.spec import load_spec
from animator.spec.validate import validate

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
                             animations=counts.get(layer.get("id"), 0)))
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

    spec_path = Path(spec_path)
    original_bytes = spec_path.read_bytes()
    raw = load_spec(spec_path)
    if not isinstance(raw, dict) or not isinstance(raw.get("layers"), list):
        raise AnimatorError(f"{spec_path} 不是有效的 spec（缺少 layers）")
    layer = next((l for l in raw["layers"] if isinstance(l, dict) and l.get("id") == layer_id), None)
    if layer is None:
        ids = ", ".join(str(l.get("id")) for l in raw["layers"] if isinstance(l, dict))
        raise AnimatorError(f"找不到圖層 {layer_id!r}（可用：{ids}）")
    if is_background(layer):
        raise AnimatorError(f"圖層 {layer_id!r} 是背景，不允許對背景加動畫")

    new = json.loads(json.dumps(raw))               # 深拷貝：原本的 raw 不會被修改
    if "animations" not in new:
        new["animations"] = []
    elif not isinstance(new["animations"], list):
        raise AnimatorError("spec 的 animations 不是陣列，無法加入動畫")
    anim = {"id": _unique_anim_id(new, layer_id, effect), "target": layer_id, "effect": effect,
            "start": _tidy(start), "end": _tidy(round(start + duration, 6)), "params": dict(params)}
    new["animations"].append(anim)

    result = validate(new, spec_path.parent)          # Stage 1 的驗證：時間、duration、通道重疊…
    if not result.ok:
        raise SpecError(result.errors)

    backup = write_spec_with_backup(spec_path, original_bytes, new)
    return EditResult(spec_path=spec_path, animation=anim, backup=backup,
                      warnings=[str(w) for w in result.warnings])


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
