"""S2-1 量測：細毛 / 頭髮的 alpha 邊緣（只做量測與比較圖，不修改 segment 的行為）。

    .venv\\Scripts\\python.exe scripts\\s2_1_alpha_edge.py <job_dir> <layer_id> [<job_dir> <layer_id> ...]

對每個圖層：
- 重新跑 PyMatting（band 8 px），比較「alpha 截斷寬度」2 px（目前）/ 8 px（= band）/ 不截斷
  → 被截掉的 alpha 量（佔精修帶 alpha 的比例）
- 「外擴帶還原」keep 3 px（目前）/ 8 px：背景裡殘留的物件邊緣（物件移開後的殘影）
- 比較圖：物件往右移 15% 畫面寬，疊在修補後的背景上（放大的局部）
輸出：output/s2-1-alpha-edge/
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / "models" / ".numba_cache"))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from animator.segmentation import compose as C  # noqa: E402
from animator.segmentation.backends.pymatting_refine import PyMattingRefiner  # noqa: E402

OUT = ROOT / "output" / "s2-1-alpha-edge"
CLIPS = {"clip2_current": 2, "clip8_band": 8, "no_clip": None}
KEEPS = {"keep3_current": 3, "keep8": 8}


def layer_alpha(alpha, visible, clip):
    a = alpha.copy()
    if clip is not None:
        a[~C.dilate(visible, clip)] = 0
    return a


def composite(bg, rgb, alpha, dx):
    out = bg.astype(np.float32).copy()
    a = np.roll(alpha, dx, axis=1).astype(np.float32)[..., None] / 255
    c = np.roll(rgb, dx, axis=1).astype(np.float32)
    return (c * a + out * (1 - a)).round().clip(0, 255).astype(np.uint8)


def zoom(img, box, s=4):
    x0, y0, x1, y1 = box
    return Image.fromarray(img[y0:y1, x0:x1]).resize(((x1 - x0) * s, (y1 - y0) * s), Image.NEAREST)


def run(job: Path, layer_id: str) -> dict:
    src = np.asarray(Image.open(job / "segmentation" / "source.png").convert("RGB"))
    mask = np.asarray(Image.open(job / "segmentation" / "masks" / f"{layer_id}.png")) > 127
    hole = np.asarray(Image.open(job / "segmentation" / "masks" / "background_hole.png")) > 127
    bg_job = np.asarray(Image.open(job / "layers" / "background.png").convert("RGB"))
    h, w = mask.shape
    visible = mask                                       # 單一物件的情況：可見 = 遮罩
    alpha, fg, core = PyMattingRefiner(band_px=8).refine(src, mask)
    band = (alpha > 0) & (alpha < 255)
    rgb = src.copy()
    rgb[band & ~core] = fg[band & ~core]
    res = {"layer": layer_id, "band_alpha_sum": int(alpha[band].astype(np.int64).sum())}
    for name, clip in CLIPS.items():
        a = layer_alpha(alpha, visible, clip)
        res[name] = {"alpha_cut_ratio": round(1 - float(a.astype(np.int64).sum()) / max(1, float(alpha.astype(np.int64).sum())), 5),
                     "band_alpha_cut_ratio": round(float((alpha.astype(np.int64) - a)[band].sum()) / max(1, res["band_alpha_sum"]), 4)}
    # 背景殘影：還原外擴帶時，離物件 keep_px 以外用原圖 → 原圖裡物件外圍的毛髮會留在背景上
    # 用「精修 alpha 在外擴帶的量」估計殘影：alpha > 0 而且被還原成原圖（t > 0）的像素
    dist = __import__("cv2").distanceTransform((~mask).astype(np.uint8), __import__("cv2").DIST_L2, 5)
    for name, keep in KEEPS.items():
        restored = hole & (dist > keep)
        ghost = alpha.astype(np.float32) / 255 * restored
        res[name] = {"ghost_alpha_px": round(float(ghost.sum()), 1),
                     "ghost_ratio_of_band": round(float(ghost.sum()) / max(1.0, float((alpha[band] / 255).sum())), 4)}

    # 比較圖：往右移動後，邊緣的樣子
    dx = int(w * 0.15)
    ys, xs = np.where(band)
    i = np.argmin(ys)                                     # 最上面的邊緣（頭髮 / 耳朵 / 尾巴）
    cy, cx = int(ys[i]) + 20, int(xs[i])
    box_moved = (max(0, cx + dx - 50), max(0, cy - 50), min(w, cx + dx + 50), min(h, cy + 50))
    box_old = (max(0, cx - 50), max(0, cy - 50), min(w, cx + 50), min(h, cy + 50))
    tiles = [("source", zoom(src, box_old))]
    for name, clip in CLIPS.items():
        tiles.append((f"moved {name}", zoom(composite(bg_job, rgb, layer_alpha(alpha, visible, clip), dx), box_moved)))
    for name, keep in KEEPS.items():
        bg = C.restore_ring(src, bg_job, mask, hole, ring_px=12, keep_px=keep) if keep != 3 else bg_job
        tiles.append((f"bg at old pos {name}", zoom(bg, box_old)))
    tw, th = tiles[0][1].size
    grid = Image.new("RGB", (tw * len(tiles), th + 16), "white")
    d = ImageDraw.Draw(grid)
    for k, (name, im) in enumerate(tiles):
        grid.paste(im, (k * tw, 16))
        d.text((k * tw + 3, 2), name, fill="black")
    OUT.mkdir(parents=True, exist_ok=True)
    grid.save(OUT / f"{job.name}_{layer_id}.png")
    res["grid"] = str((OUT / f"{job.name}_{layer_id}.png").relative_to(ROOT))
    return res


if __name__ == "__main__":
    args = sys.argv[1:]
    results = [run(Path(args[i]), args[i + 1]) for i in range(0, len(args), 2)]
    (OUT / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=1, ensure_ascii=False))
