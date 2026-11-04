"""影像與遮罩處理（numpy + OpenCV + Pillow，不需要 AI 模型）。

- 讀圖：EXIF 方向校正 → RGB；奇數寬 / 高在右邊 / 下邊複製邊緣補 1 px（Stage 1 要求偶數畫布）
- 遮罩後處理、重疊處理、z 排序、補洞區域、裁切成 RGBA 圖層、背景合成與檢查
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

MIN_DILATE_PX = 8          # S2-0.1：物件遮罩至少外擴 8 px，物件邊緣才不會被讀進 MI-GAN 的混合帶
BLEND_BAND_PX = 3          # 背景合成時，hole 外側保留 MI-GAN 的混合帶寬度（實測 ≤ 1.4 px）


# ---------------------------------------------------------------- 讀寫圖片

@dataclass
class SourceImage:
    rgb: np.ndarray                 # uint8 (H, W, 3)，已補成偶數尺寸
    original_size: tuple[int, int]  # (w, h)，補邊之前
    pad: tuple[int, int]            # (右邊補的 px, 下邊補的 px)


def load_image(path: Path) -> SourceImage:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            # 有透明度的圖片：疊在白色上（Stage 2 的輸入應該是一般的不透明圖片）
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im)
        rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    pad = (w % 2, h % 2)
    if any(pad):
        rgb = np.pad(rgb, ((0, pad[1]), (0, pad[0]), (0, 0)), mode="edge")
    return SourceImage(rgb=np.ascontiguousarray(rgb), original_size=(w, h), pad=pad)


def save_png(arr: np.ndarray, path: Path) -> None:
    """uint8 (H, W) → L、(H, W, 3) → RGB、(H, W, 4) → RGBA。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(arr.astype(np.uint8))).save(path)


def mask_values(im: Image.Image) -> tuple[np.ndarray, str]:
    """遮罩影像 → uint8 (H, W)。回傳 (值, 使用的通道)。

    - 有 alpha 而且 alpha 不是整張都 255 → 用 alpha（透明 = 不是物件）
    - 其他情況（包括 GIMP / Photoshop 輸出的「不透明 RGBA、白底黑字」）→ 用亮度
    """
    if im.mode == "P":
        im = im.convert("RGBA")
    if im.mode in ("RGBA", "LA", "PA"):
        alpha = np.asarray(im.getchannel("A"))
        if alpha.min() < 255:
            return alpha, "alpha"
    return np.asarray(im.convert("L")), "luminance"


def load_mask(path: Path, original_size: tuple[int, int], pad: tuple[int, int] = (0, 0)) -> np.ndarray:
    """讀取使用者提供的遮罩（> 127 = 物件），尺寸必須和輸入圖片相同。

    original_size = 輸入圖片補邊之前的 (w, h)；pad = 補邊的 (右, 下)。
    遮罩也可以已經是補邊後的尺寸。其他尺寸一律拒絕（不做縮放，避免物件位置偏移）。
    """
    from animator.errors import AnimatorError

    with Image.open(path) as im:
        im.load()
        values, _ = mask_values(im)
    h, w = values.shape
    ow, oh = original_size
    if (w, h) == (ow, oh) and any(pad):
        values = np.pad(values, ((0, pad[1]), (0, pad[0])), mode="edge")
    elif (w, h) != (ow + pad[0], oh + pad[1]):
        raise AnimatorError(f"遮罩 {Path(path).name} 的尺寸 {w}x{h} 和輸入圖片 {ow}x{oh} 不同"
                            f"（遮罩必須和原圖一樣大，不會自動縮放）")
    return values > 127


def processing_scale(size: tuple[int, int], max_side: int) -> float:
    w, h = size
    return min(1.0, max_side / max(w, h))


def resize_rgb(rgb: np.ndarray, scale: float) -> np.ndarray:
    if scale >= 1.0:
        return rgb
    h, w = rgb.shape[:2]
    return np.asarray(Image.fromarray(rgb).resize((round(w * scale), round(h * scale)), Image.LANCZOS))


def upscale_mask(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """處理圖的遮罩放大回原圖（雙線性內插後取 0.5 門檻，邊緣比 nearest 平滑）。"""
    w, h = size
    if mask.shape == (h, w):
        return mask.astype(bool)
    up = cv2.resize(mask.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    return up >= 0.5


# ---------------------------------------------------------------- 偵測結果

def box_iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def normalize_label(raw: str, labels: list[str]) -> str:
    """Grounding DINO 有時會把多個標籤合併成一個（例如 "dog cat"）→ 取最先出現的那個要求的標籤。"""
    raw_l = " ".join(raw.lower().split())
    if raw_l in (label.lower().strip() for label in labels):
        return next(label for label in labels if label.lower().strip() == raw_l)
    # 以「整個字」比對（"cat" 不會配到 "category"）；同一個位置有多個候選時取最長的
    hits = []
    for label in labels:
        key = " ".join(label.lower().split())
        m = re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", raw_l)
        if m:
            hits.append((m.start(), -len(key), label))
    if hits:
        return min(hits)[2]
    return raw_l or "object"


def filter_detections(dets: list[dict], labels: list[str], max_per_label: int,
                      nms_iou: float = 0.7) -> list[dict]:
    """標籤正規化 → 同標籤 NMS → 每個標籤最多 max_per_label 個（依分數）。"""
    out: list[dict] = []
    per: dict[str, int] = {}
    for d in sorted(dets, key=lambda d: -d["score"]):
        d = {**d, "label": normalize_label(d.get("label_raw", d["label"]), labels)}
        if per.get(d["label"], 0) >= max_per_label:
            continue
        if any(o["label"] == d["label"] and box_iou(o["box"], d["box"]) > nms_iou for o in out):
            continue
        out.append(d)
        per[d["label"]] = per.get(d["label"], 0) + 1
    return out


# ---------------------------------------------------------------- 遮罩

def clean_mask(mask: np.ndarray, min_area_px: int) -> np.ndarray:
    """移除小碎塊、補上小於 min_area_px 的內部小洞。"""
    m = mask.astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area_px
    m = keep[lab]
    inv = (~m).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    h, w = m.shape
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        touches_border = x == 0 or y == 0 or x + bw == w or y + bh == h
        if not touches_border and area < min_area_px:
            m[lab == i] = True
    return m


def dilate(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    return cv2.dilate(mask.astype(np.uint8), k) > 0


def erode(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    return cv2.erode(mask.astype(np.uint8), k) > 0


def bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


@dataclass
class ObjectItem:
    id: str
    label: str
    score: float | None
    mask: np.ndarray                 # 原圖尺寸的 bool 遮罩（重疊處理前）
    source: str                      # "ai" | "manual"
    det_box: list[float] | None = None
    visible: np.ndarray | None = None  # 重疊處理後實際可見的部分
    z: int = 0
    review: list[str] = field(default_factory=list)

    @property
    def area(self) -> int:
        return int(self.mask.sum())


def assign_layers(objects: list[ObjectItem], total_px: int, review_ratio: float) -> list[ObjectItem]:
    """z 排序（面積小的在前面）+ 重疊的像素歸給前面的圖層 + 大遮罩標記人工檢查。

    回傳依 z 由後往前排序的清單；z = 10, 20, 30…（背景是 0）。
    """
    order = sorted(objects, key=lambda o: (-o.area, o.id))   # 面積大的在後面
    taken = np.zeros_like(order[0].mask) if order else None
    for o in reversed(order):                                  # 從最前面的開始分配像素
        o.visible = o.mask & ~taken
        taken |= o.visible
    for i, o in enumerate(order):
        o.z = 10 * (i + 1)
        ratio = o.area / total_px
        if ratio >= review_ratio:
            o.review.append(f"large_mask：遮罩占畫面 {ratio:.1%}（≥ {review_ratio:.0%}）")
        if not o.visible.any():
            o.review.append("fully_occluded：完全被前面的圖層遮住")
    return order


def unique_ids(labels: list[str], sanitize) -> list[str]:
    ids, count = [], {}
    for label in labels:
        base = sanitize(label)
        if base == "background":
            base = "background-object"
        count[base] = count.get(base, 0) + 1
        ids.append(f"{base}-{count[base]}")
    return ids


# ---------------------------------------------------------------- 圖層

def crop_layer(rgb: np.ndarray, alpha: np.ndarray, pad: int = 2):
    """依 alpha > 0 的範圍裁切（外加 pad）。回傳 (rgba, box[x0,y0,x1,y1], position[cx, cy]) 或 None。"""
    b = bbox(alpha > 0)
    if b is None:
        return None
    h, w = alpha.shape
    x0, y0 = max(0, b[0] - pad), max(0, b[1] - pad)
    x1, y1 = min(w, b[2] + pad), min(h, b[3] + pad)
    rgba = np.dstack([rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1]]).astype(np.uint8)
    return rgba, [x0, y0, x1, y1], [(x0 + x1) / 2, (y0 + y1) / 2]


def layer_pixels(source: np.ndarray, visible: np.ndarray, alpha: np.ndarray, fg: np.ndarray | None,
                 core: np.ndarray | None, clip_px: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """圖層的 RGB 直接取原圖像素；只有「半透明」的像素（0 < alpha < 255）換成去色邊後的前景色。
    alpha = 255 的像素（包括精修帶裡被判定為完全不透明的像素）一律保留原圖像素。

    alpha 只保留在「可見範圍外擴 clip_px」之內。clip_px 要等於精修帶寬度（band_px）：
    S2-1 實測 2 px 會截掉毛髮 / 細毛 25~32% 的半透明邊（docs/stage2.md「S2-1 審查修正」）。
    """
    rgb = source.copy()
    if fg is not None and core is not None:
        partial = (alpha > 0) & (alpha < 255) & ~core
        rgb[partial] = fg[partial]
    a = alpha.copy()
    a[~dilate(visible, max(clip_px, 1))] = 0
    return rgb, a


# ---------------------------------------------------------------- 背景

@dataclass
class HoleRegion:
    index: int
    mask: np.ndarray
    area_px: int
    area_ratio: float
    box: list[int]
    status: str = "pending"
    backend: str = ""              # 最後使用（或最後嘗試）的 backend
    repaired: bool = False         # True = 修補結果通過自動檢查而且已經貼回背景
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    attempts: list[dict] = field(default_factory=list)   # 每次嘗試：backend、結果

    def to_json(self) -> dict:
        return {"index": self.index, "area_px": self.area_px, "area_ratio": round(self.area_ratio, 4),
                "box": self.box, "status": self.status, "repaired": self.repaired, "backend": self.backend,
                "attempts": self.attempts, "reasons": self.reasons, "checks": self.checks}


def hole_regions(masks: list[np.ndarray], shape: tuple[int, int], dilate_px: int,
                 review_ratio: float) -> tuple[np.ndarray, list[HoleRegion]]:
    """所有物件遮罩的聯集外擴 max(dilate_px, 8) px → 依連通區域切成多個補洞區域。"""
    union = np.zeros(shape, bool)
    for m in masks:
        union |= m
    hole = dilate(union, max(dilate_px, MIN_DILATE_PX))
    n, lab = cv2.connectedComponents(hole.astype(np.uint8), connectivity=8)
    total = shape[0] * shape[1]
    regions = []
    for i in range(1, n):
        m = lab == i
        area = int(m.sum())
        r = HoleRegion(index=i - 1, mask=m, area_px=area, area_ratio=area / total, box=bbox(m))
        if r.area_ratio >= review_ratio:
            r.reasons.append(f"large_hole：補洞區域占畫面 {r.area_ratio:.1%}（≥ {review_ratio:.0%}）")
        regions.append(r)
    return hole, regions


MIN_CHANGED_RATIO = 0.5   # 物件位置上至少一半的像素要被改寫


def check_inpaint(original: np.ndarray, output: np.ndarray, hole: np.ndarray,
                  target: np.ndarray | None = None) -> dict:
    """自動檢查一次修補的結果（不代表視覺品質已確認）。

    - shape_ok：輸出尺寸與輸入相同
    - outside_unchanged：hole 外擴 BLEND_BAND_PX 以外的像素和原圖完全相同（遮罩極性錯誤時會失敗）
    - target_changed_ratio：「物件本身」的像素被改寫的比例（極性反了會是 0）。
      只看物件（target），不看外擴帶：外擴帶本來就是背景，補得好時可能和原圖幾乎一樣。
    """
    checks: dict = {"shape_ok": output.shape == original.shape}
    if not checks["shape_ok"]:
        checks["passed"] = False
        return checks
    target = hole if target is None or not (target & hole).any() else (target & hole)
    diff = np.abs(output.astype(np.int16) - original.astype(np.int16)).max(axis=2)
    outside = ~dilate(hole, BLEND_BAND_PX)
    checks["outside_max_diff"] = int(diff[outside].max()) if outside.any() else 0
    checks["outside_unchanged"] = checks["outside_max_diff"] == 0
    checks["target_changed_ratio"] = round(float((diff[target] > 0).mean()), 4) if target.any() else 0.0
    checks["passed"] = checks["outside_unchanged"] and checks["target_changed_ratio"] >= MIN_CHANGED_RATIO
    return checks


def context_window(box: list[int], shape: tuple[int, int], min_margin: int = 128) -> tuple[int, int, int, int]:
    """補洞區域的 bbox 外加上下文（至少 min_margin px，或區域長邊的一半）。"""
    x0, y0, x1, y1 = box
    m = max(min_margin, max(x1 - x0, y1 - y0) // 2)
    h, w = shape
    return max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)


def composite_background(original: np.ndarray, inpainted: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """只把 hole（含混合帶）範圍貼回原圖；其他像素保證和原圖逐像素相同。"""
    region = dilate(hole, BLEND_BAND_PX)
    out = original.copy()
    out[region] = inpainted[region]
    return out


def recomposite(background: np.ndarray, layers: list[tuple[np.ndarray, list[int]]]) -> np.ndarray:
    """把圖層（依 z 由後往前）疊回背景：不加動畫時 Stage 1 渲染出來的畫面（不含 Blender 的取樣濾波）。"""
    out = background.astype(np.float32)
    for rgba, (x0, y0, x1, y1) in layers:
        a = rgba[..., 3:4].astype(np.float32) / 255
        out[y0:y1, x0:x1] = rgba[..., :3] * a + out[y0:y1, x0:x1] * (1 - a)
    return out.round().clip(0, 255).astype(np.uint8)


def fidelity(a: np.ndarray, b: np.ndarray) -> dict:
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return {"mean_abs_diff": round(float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()), 3),
            "ratio_diff_gt8": round(float((d > 8).mean()), 5), "max_diff": int(d.max())}


RING_KEEP_PX = 3           # 最小值：離物件這個距離以內一律使用修補結果（實際用 max(3, band_px)）


def ring_keep_px(band_px: int, dilate_px: int) -> int:
    """外擴帶還原時「保留修補結果」的寬度：至少涵蓋精修帶（毛髮的半透明邊才不會殘留在背景上），
    但要比外擴寬度小，才有漸層過渡到原圖。"""
    return max(RING_KEEP_PX, min(band_px, dilate_px - 2))


def restore_ring(original: np.ndarray, background: np.ndarray, objects: np.ndarray, hole: np.ndarray,
                 ring_px: int, keep_px: int = RING_KEEP_PX) -> np.ndarray:
    """把「外擴帶」裡原本就是背景的像素還原成原圖（漸層混合）。

    補洞時物件遮罩要外擴（避免物件邊緣滲進 MI-GAN），但外擴帶本身是真正的背景：
    離物件 ≤ keep_px 用修補結果，到外擴帶外緣線性過渡成原圖，和洞外的原圖無縫接上。
    """
    if not hole.any():
        return background
    dist = cv2.distanceTransform((~objects).astype(np.uint8), cv2.DIST_L2, 5)
    span = max(1.0, ring_px - keep_px)
    t = np.clip((dist - keep_px) / span, 0.0, 1.0)[..., None]
    t[~hole] = 1.0
    out = background.astype(np.float32) * (1 - t) + original.astype(np.float32) * t
    return out.round().clip(0, 255).astype(np.uint8)


def overview(rgb: np.ndarray, items: list[ObjectItem], regions: list[HoleRegion]) -> np.ndarray:
    """彩色遮罩 + 標籤疊在原圖上；需要人工檢查的補洞區域畫紅框。"""
    from PIL import ImageDraw

    palette = np.array([[230, 25, 75], [60, 180, 75], [255, 225, 25], [0, 130, 200], [245, 130, 48],
                        [145, 30, 180], [70, 240, 240], [240, 50, 230]], np.float32)
    over = rgb.astype(np.float32)
    for i, o in enumerate(items):
        v = o.visible if o.visible is not None else o.mask
        over[v] = over[v] * 0.45 + palette[i % len(palette)] * 0.55
    im = Image.fromarray(over.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for i, o in enumerate(items):
        b = bbox(o.mask)
        if b:
            color = tuple(int(c) for c in palette[i % len(palette)])
            d.rectangle(b, outline=color, width=2)
            text = f"{o.id} z={o.z}" + (" !" if o.review else "")
            d.text((b[0] + 3, b[1] + 2), text, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    for r in regions:
        if r.status != "passed_checks":
            label = f"hole {r.index}: {r.status}" + ("" if r.repaired else " (NOT repaired)")
            d.rectangle(r.box, outline=(255, 0, 0), width=3)
            d.text((r.box[0] + 3, r.box[3] - 14), label, fill=(255, 0, 0),
                   stroke_width=2, stroke_fill=(255, 255, 255))
    return np.asarray(im)
