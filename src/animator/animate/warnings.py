"""S2-2 M4：修補區域與前後順序的提醒（只讀取、只用標準函式庫）。

分析 job 的 spec.json 和 segmentation/segmentation.json：
- 不執行 AI、不讀圖片像素（只用 Stage 1 resolve 讀 PNG 標頭的尺寸）、不重建分割、不寫任何檔案。
- 提醒只是資訊：不會擋住編輯或渲染，也不會改變任何指令的結束碼。

規則（依圖層去重，不是每一段動畫各一個）：
- exposure（露出修補過的背景）：圖層的 footprint 改變或變透明，原本被它蓋住的補洞區域就可能露出來。
  warning：move、rotate、scale < 1、visibility 隱藏、初始姿勢和分割時不同；
  notice：opacity < 1（半透明）、scale > 1（凹的邊緣附近可能露出）。
- unknown：舊的 job（segmentation.json 沒有 associations）→ 每個 job 最多一個 notice，不猜。
- depth：兩個「自動排序」（meta.z_rule = smaller_area_in_front）的圖層 footprint 在第 1 格重疊，
  或動畫可能讓它們重疊 → notice，建議用 animator layers z 手動調整。z_rule = manual 的圖層不提醒。
footprint 是保守的軸對齊矩形（旋轉時用外接圓），重疊不代表前後順序一定錯。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from animator.animate.edit import image_to_canvas, is_background
from animator.config import Config
from animator.spec import load_spec
from animator.spec.resolve import resolve

AUTO_Z_RULE = "smaller_area_in_front"
EPS = 1e-6

WARNING, NOTICE = "warning", "notice"


@dataclass
class Finding:
    severity: str                      # "warning" | "notice"
    kind: str                          # "exposure" | "unknown_association" | "depth"
    layers: list[str]                  # 相關的圖層（depth 是一對）
    message: str
    regions: list[int] | None = None   # 相關的補洞區域（不知道時是 None）
    time: float | None = None          # 風險最早可能出現的時間（秒）
    frame: int | None = None


@dataclass
class Report:
    spec_path: Path
    segmentation: Path | None          # None：不是分割產生的 job（或找不到 segmentation.json）
    associations_known: bool
    background_status: str | None
    findings: list[Finding] = field(default_factory=list)

    def for_layer(self, layer_id: str) -> list[Finding]:
        return [f for f in self.findings if layer_id in f.layers]


# ---------------------------------------------------------------- 讀取 metadata

def _load_segmentation(raw: dict, spec_path: Path) -> tuple[Path | None, dict | None]:
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    rel = meta.get("segmentation")
    if not isinstance(rel, str) or not rel:
        return None, None
    path = (spec_path.parent / rel) if not Path(rel).is_absolute() else Path(rel)
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path, None
    return path, data if isinstance(data, dict) else None


def _associations(seg: dict | None) -> dict | None:
    a = seg.get("associations") if seg else None
    if not (isinstance(a, dict) and isinstance(a.get("regions"), dict)):
        return None
    return a


def _region_text(region: dict | None, index: int) -> str:
    if region is None:
        return f"區域 {index}（沒有這個區域的資料）"
    status = region.get("status")
    repaired = region.get("repaired")                        # 舊的 job 沒有這個欄位：不假設已修補
    if repaired is False:
        state = "沒有修補：原本的物件仍留在背景上，露出時會看到重複的物件"
    elif repaired is True and status == "passed_checks":
        state = "已修補，只通過自動檢查（沒有人工確認）"
    elif repaired is True and status == "needs_review":
        state = "已修補，需要人工檢查"
    elif repaired is True:
        state = f"已修補（{status}）"
    else:
        state = f"修補結果沒有記錄（{status or '狀態不明'}）"
    return f"區域 {index}（{state}）"


# ---------------------------------------------------------------- 幾何

def _corners(layer: dict, position, scale, rotation) -> list[tuple[float, float]]:
    w, h = layer["size"]
    return [image_to_canvas(u, v, position, layer["anchor"], (w, h), scale, rotation)
            for u, v in ((0, 0), (w, 0), (w, h), (0, h))]


def _box(points) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b) -> bool:
    return a[0] < b[2] - EPS and b[0] < a[2] - EPS and a[1] < b[3] - EPS and b[1] < a[3] - EPS


def _channel_values(layer: dict, anims: list[dict], channel: str) -> list[list[float]]:
    initial = {"position": layer["position"], "scale": layer["scale"], "rotation": [layer["rotation"]],
               "opacity": [layer["opacity"]], "visibility": [layer.get("visibility", 1.0)]}[channel]
    values = [list(initial)]
    for a in anims:
        if a["channel"] == channel:
            values += [list(a["from"]), list(a["to"])]
    return values


def _sweep_box(layer: dict, anims: list[dict]) -> tuple[float, float, float, float]:
    """整段動畫可能佔用的範圍（保守）：位置的範圍 + 圖片的大小；scale / rotation 會變時用外接圓。"""
    positions = _channel_values(layer, anims, "position")
    scales = _channel_values(layer, anims, "scale")
    rotations = _channel_values(layer, anims, "rotation")
    xs, ys = [p[0] for p in positions], [p[1] for p in positions]
    if all(s == scales[0] for s in scales) and all(r == rotations[0] for r in rotations):
        p0 = layer["position"]
        offs = [(x - p0[0], y - p0[1]) for x, y in _corners(layer, p0, scales[0], rotations[0][0])]
        return (min(xs) + min(o[0] for o in offs), min(ys) + min(o[1] for o in offs),
                max(xs) + max(o[0] for o in offs), max(ys) + max(o[1] for o in offs))
    w, h = layer["size"]
    ax, ay = layer["anchor"][0] * w, layer["anchor"][1] * h
    radius = max(math.hypot(u - ax, v - ay) for u, v in ((0, 0), (w, 0), (w, h), (0, h)))
    radius *= max(max(abs(c) for c in s) for s in scales)
    return min(xs) - radius, min(ys) - radius, max(xs) + radius, max(ys) + radius


def _changes_footprint(a: dict, layer: dict) -> bool:
    if a["channel"] not in ("position", "scale", "rotation"):
        return False
    initial = {"position": layer["position"], "scale": layer["scale"], "rotation": [layer["rotation"]]}[a["channel"]]
    return a["from"] != a["to"] or list(a["from"]) != list(initial)


# ---------------------------------------------------------------- exposure

def _when(frame: int, fps: int) -> tuple[float, int]:
    return round((frame - 1) / fps, 6), frame


def _exposures(layer: dict, raw_layer: dict, anims: list[dict], fps: int) -> list[tuple[str, str, int]]:
    """這個圖層會露出背景的原因：[(severity, 說明, 最早的影格)]。"""
    out: list[tuple[str, str, int]] = []

    # 初始姿勢和分割時不同（只在 meta.crop_box 和圖片尺寸一致、可以可靠比較時判斷）
    meta = raw_layer.get("meta") if isinstance(raw_layer.get("meta"), dict) else {}
    crop = meta.get("crop_box")
    if (isinstance(crop, list) and len(crop) == 4 and all(isinstance(v, (int, float)) for v in crop)
            and [crop[2] - crop[0], crop[3] - crop[1]] == list(layer["size"])):
        now = _corners(layer, layer["position"], layer["scale"], layer["rotation"])
        orig = [(crop[0], crop[1]), (crop[2], crop[1]), (crop[2], crop[3]), (crop[0], crop[3])]
        if any(math.dist(p, q) > EPS for p, q in zip(now, orig)):
            out.append((WARNING, "初始位置 / 大小 / 角度和分割時不同", 1))

    def first(channel, test) -> int | None:
        frames = [a["frame_start"] for a in anims if a["channel"] == channel and test(a)]
        return min(frames) if frames else None

    f = first("position", lambda a: _changes_footprint(a, layer))
    if f:
        out.append((WARNING, "move", f))
    f = first("rotation", lambda a: _changes_footprint(a, layer))
    if f:
        out.append((WARNING, "rotate", f))
    shrink = first("scale", lambda a: any(v < 1 - EPS for v in a["from"] + a["to"]) and _changes_footprint(a, layer))
    if shrink or any(v < 1 - EPS for v in layer["scale"]):
        out.append((WARNING, "scale < 1（縮小）", shrink if shrink and min(layer["scale"]) >= 1 - EPS else 1))
    grow = first("scale", lambda a: any(v > 1 + EPS for v in a["from"] + a["to"]) and _changes_footprint(a, layer))
    if grow or any(v > 1 + EPS for v in layer["scale"]):
        out.append((NOTICE, "scale > 1（放大；凹的邊緣附近可能露出）", grow if grow and max(layer["scale"]) <= 1 + EPS else 1))
    if layer.get("visibility", 1.0) == 0:
        out.append((WARNING, "圖層的 visible 是 false（隱藏）", 1))
    else:
        f = first("visibility", lambda a: a["from"] == [0.0])
        if f:
            out.append((WARNING, "visibility 隱藏", f))
    if layer["opacity"] < 1 - EPS:
        out.append((NOTICE, "opacity < 1（半透明）", 1))
    else:
        f = first("opacity", lambda a: min(a["from"] + a["to"]) < 1 - EPS)
        if f:
            out.append((NOTICE, "opacity < 1（半透明）", f))
    return out


# ---------------------------------------------------------------- 主程式

def analyze(spec_path: Path, cfg: Config) -> Report:
    """讀取 job，回傳目前所有的提醒。只讀取，不寫任何檔案。spec 不合法時 raise SpecError（和 render 一樣）。"""
    spec_path = Path(spec_path)
    raw = load_spec(spec_path)
    resolved = resolve(raw, spec_path, cfg)
    fps = resolved["canvas"]["fps"]
    seg_path, seg = _load_segmentation(raw, spec_path)
    assoc = _associations(seg)
    background = seg.get("background") if isinstance(seg, dict) and isinstance(seg.get("background"), dict) else {}
    bg_status = background.get("status") if seg else None
    regions = {r.get("index"): r for r in background.get("regions", []) or [] if isinstance(r, dict)}
    report = Report(spec_path, seg_path if seg is not None else None, assoc is not None, bg_status)

    raw_layers = {l["id"]: l for l in raw["layers"] if isinstance(l, dict)}
    layers = [l for l in resolved["layers"] if not is_background(raw_layers[l["id"]])]
    anims_of = {l["id"]: [a for a in resolved["animations"] if a["target"] == l["id"]] for l in layers}

    # ---- 露出修補過的背景
    if seg is not None and bg_status != "unchanged":
        region_of: dict[str, list[int]] = {}
        if assoc is not None:
            for key, ids in assoc["regions"].items():
                for oid in ids or []:
                    region_of.setdefault(oid, []).append(int(key))
        unknown: list[str] = []
        unknown_first: int | None = None
        entries = [(l, _exposures(l, raw_layers[l["id"]], anims_of[l["id"]], fps)) for l in layers]
        resolved_ids = {l["id"] for l in resolved["layers"]}
        entries += [({"id": lid}, [(WARNING, "圖層的 visible 是 false（整段影片都不顯示）", 1)])
                    for lid, rl in raw_layers.items()                # resolve 已經移除的隱藏圖層
                    if lid not in resolved_ids and not is_background(rl) and rl.get("visible", True) is False]
        for layer, reasons in entries:
            if not reasons:
                continue
            first_frame = min(r[2] for r in reasons)
            what = "、".join(f"{text}（{_fmt_when(*_when(frame, fps))}起）" for _, text, frame in
                            sorted(reasons, key=lambda r: r[2]))
            if assoc is None:
                unknown.append(f"{layer['id']}：{what}")
                unknown_first = first_frame if unknown_first is None else min(unknown_first, first_frame)
                continue
            ids = sorted(region_of.get(layer["id"], []))
            if not ids:
                continue                                    # 不是分割產生的圖層：後面沒有補洞區域
            severity = WARNING if any(r[0] == WARNING for r in reasons) else NOTICE
            t, frame = _when(first_frame, fps)
            report.findings.append(Finding(
                severity, "exposure", [layer["id"]],
                f"{layer['id']} 可能露出修補過的背景 " + "、".join(_region_text(regions.get(i), i) for i in ids)
                + f"：{what}", regions=ids, time=t, frame=frame))
        if unknown:
            t, frame = _when(unknown_first, fps)
            report.findings.append(Finding(
                NOTICE, "unknown_association", [u.split("：")[0] for u in unknown],
                "這個 job 的分割資料沒有「物件 ↔ 補洞區域」的關聯（比較舊的 job），無法判斷下面的動畫會露出哪個區域"
                f"（背景狀態：{bg_status or '不明'}）：" + "；".join(unknown), regions=None, time=t, frame=frame))

    # ---- 自動前後順序可能不確定
    auto = [l for l in layers if (raw_layers[l["id"]].get("meta") or {}).get("z_rule") == AUTO_Z_RULE]
    overlap_px = {}
    for o in (assoc or {}).get("overlaps", []) or []:
        if isinstance(o, dict):
            overlap_px[frozenset((o.get("behind"), o.get("front")))] = o.get("px")
    rank = {l["id"]: l["rank"] for l in resolved["layers"]}
    for i, a in enumerate(auto):
        for b in auto[i + 1:]:
            behind, front = sorted((a, b), key=lambda l: rank[l["id"]])
            pair = [behind["id"], front["id"]]
            box_a = _box(_corners(a, a["position"], a["scale"], a["rotation"]))
            box_b = _box(_corners(b, b["position"], b["scale"], b["rotation"]))
            px = overlap_px.get(frozenset(pair))
            evidence = f"；分割時兩個遮罩重疊 {px} px" if px else ""
            if _overlap(box_a, box_b):
                report.findings.append(Finding(
                    NOTICE, "depth", pair,
                    f"{behind['id']}（後）和 {front['id']}（前）在第 1 格的範圍重疊，前後順序是自動決定的"
                    f"（面積小的在前面）{evidence}，不一定正確；需要時用 animator layers z 調整",
                    time=0.0, frame=1))
            elif _overlap(_sweep_box(a, anims_of[a["id"]]), _sweep_box(b, anims_of[b["id"]])):
                frames = [x["frame_start"] for x in anims_of[a["id"]] + anims_of[b["id"]]
                          if _changes_footprint(x, a if x["target"] == a["id"] else b)]
                t, frame = _when(min(frames), fps) if frames else (None, None)
                report.findings.append(Finding(
                    NOTICE, "depth", pair,
                    f"{behind['id']}（後）和 {front['id']}（前）在動畫中可能重疊"
                    f"{f'（{_fmt_when(t, frame)}起）' if frame else ''}，前後順序是自動決定的（面積小的在前面），"
                    "不一定正確；需要時用 animator layers z 調整", time=t, frame=frame))

    report.findings.sort(key=lambda f: (f.severity != WARNING, f.frame or 0, f.layers))
    return report


def _fmt_when(t: float | None, frame: int | None) -> str:
    return f"{t:g} 秒 / 第 {frame} 格" if frame else ""


def format_findings(findings: list[Finding]) -> list[str]:
    labels = {WARNING: "[WARNING]", NOTICE: "[NOTICE]"}
    out = []
    for f in findings:
        tag = labels[f.severity] + (" [關聯不明]" if f.kind == "unknown_association" else "")
        out.append(f"{tag} {f.message}")
    return out


def summarize(report: Report) -> str:
    w = sum(f.severity == WARNING for f in report.findings)
    n = sum(f.severity == NOTICE for f in report.findings)
    return f"共 {w} 個 warning、{n} 個 notice（只是提醒，不影響編輯或渲染）"


def source_line(report: Report) -> str:
    if report.segmentation is None:
        return "分割資料：沒有（不是 animator segment 產生的 job，不檢查修補區域）"
    state = "有物件 ↔ 補洞區域的關聯" if report.associations_known else "沒有物件 ↔ 補洞區域的關聯（比較舊的 job）"
    return f"分割資料：{report.segmentation}（{state}；背景狀態：{report.background_status or '不明'}）"


def as_dict(report: Report) -> dict[str, Any]:
    """測試與除錯用。"""
    return {"segmentation": str(report.segmentation) if report.segmentation else None,
            "associations_known": report.associations_known, "background_status": report.background_status,
            "findings": [vars(f) for f in report.findings]}
