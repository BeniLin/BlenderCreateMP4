"""A/B 補充：圖層有旋轉 / 縮放時，各設定的反鋸齒與縮小取樣品質（只做量測）。

開發用的量測工具（Stage 1 render 修正的證據），不是 Stage 1 的執行期依賴；需要 .venv 裡的 numpy / OpenCV / Pillow。

    .venv\\Scripts\\python.exe scripts\\s2_1_render_ab\\run_ab_transform.py

(a) 旋轉 20° 的白色方塊（黑底）：邊緣的中間值像素比例（0 = 完全沒有反鋸齒、鋸齒狀）
(b) 照片縮小 0.5 倍（位置對齊像素格）：和 OpenCV INTER_AREA 縮圖比較（越小越接近理想的縮小取樣）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_ab import OUT, ROOT, TMP, VARIANTS, ab_render, load_rgb, stage1_still, zoom_grid  # noqa: E402

from animator.config import load_config  # noqa: E402
from animator.tools import find_blender  # noqa: E402

PICK = ["V0_stage1", "F_filter_0", "F_filter_0.5", "I_closest", "S_samples_1", "X_filter0_closest",
        "X_filter0_samples1"]


def spec(name, layers, w=512, h=512):
    d = TMP / "specs" / name
    d.mkdir(parents=True, exist_ok=True)
    for layer in layers:
        Image.fromarray(layer.pop("_img")).save(d / layer["image"])
    s = {"version": 1, "meta": {"name": name}, "canvas": {"width": w, "height": h, "fps": 30, "duration": 1.0},
         "layers": layers, "animations": []}
    (d / "spec.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    return d / "spec.json"


def main():
    cfg = load_config(None, {"paths.output_dir": str(OUT / "jobs"), "paths.temp_dir": str(TMP / "tmp")})
    blender = find_blender(cfg).path
    square = np.full((200, 200, 4), 255, np.uint8)
    padded = np.zeros((204, 204, 4), np.uint8)
    padded[2:-2, 2:-2] = 255                      # 2 px 透明邊（和 S2-1 的圖層裁切一樣）
    photo = np.asarray(Image.open(TMP / "src" / "photo.png").convert("RGB"))
    photo_rgba = np.dstack([photo, np.full(photo.shape[:2], 255, np.uint8)])
    cases = {
        "rotated_square": spec("ab-rot", [{"id": "sq", "image": "sq.png", "z": 0, "position": [256, 256],
                                           "rotation": 20, "_img": square}]),
        "rotated_square_padded": spec("ab-rot-pad", [{"id": "sq", "image": "sq.png", "z": 0,
                                                      "position": [256, 256], "rotation": 20, "_img": padded}]),
        "scaled_photo": spec("ab-scale", [{"id": "ph", "image": "ph.png", "z": 0, "position": [256, 256],
                                           "scale": 0.5, "_img": photo_rgba}]),
    }
    ref = np.zeros((512, 512, 3), np.uint8)
    ref[96:416, 16:496] = cv2.resize(photo, (480, 320), interpolation=cv2.INTER_AREA)
    out, imgs = {}, {}
    for case, sp in cases.items():
        _, resolved = stage1_still(sp, cfg, case)
        imgs[case] = {}
        out[case] = {}
        for v in PICK:
            p = OUT / "renders" / case / f"{v}.png"
            ab_render(blender, resolved, VARIANTS[v], p)
            img = load_rgb(p)
            imgs[case][v] = img
            if case.startswith("rotated_square"):
                g = img[..., 0].astype(int)
                edge = cv2.dilate((cv2.Canny(g.astype(np.uint8), 50, 150) > 0).astype(np.uint8), np.ones((3, 3))) > 0
                inter = ((g > 8) & (g < 247) & edge).sum() / max(edge.sum(), 1)
                levels = len(np.unique(g[edge]))
                out[case][v] = {"edge_intermediate_ratio": round(float(inter), 4), "edge_unique_levels": levels}
            else:
                inner = (slice(100, 412), slice(20, 492))
                d = np.abs(img[inner].astype(int) - ref[inner].astype(int))
                out[case][v] = {"vs_inter_area_mean_abs": round(float(d.mean()), 3),
                                "ratio_gt8": round(float((d.max(axis=2) > 8).mean()), 4)}
            print(case, v, out[case][v], flush=True)
    zoom_grid(imgs["rotated_square"], imgs["rotated_square"]["V0_stage1"], (300, 120, 360, 180), 6,
              OUT / "grids" / "rotated_square_edge.png", diff_gain=1)
    zoom_grid(imgs["rotated_square_padded"], imgs["rotated_square_padded"]["V0_stage1"], (300, 120, 360, 180), 6,
              OUT / "grids" / "rotated_square_padded_edge.png", diff_gain=1)
    zoom_grid({"inter_area_ref": ref, **imgs["scaled_photo"]}, ref, (180, 150, 250, 220), 5,
              OUT / "grids" / "scaled_photo.png")
    (OUT / "results_transform.json").write_text(json.dumps(out, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
