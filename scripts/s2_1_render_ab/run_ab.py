"""Stage 1 畫質 A/B 實驗（只做量測，不修改 Stage 1 的渲染設定）。

開發用的量測工具（Stage 1 render 修正的證據），不是 Stage 1 的執行期依賴；需要 .venv 裡的 numpy / OpenCV / Pillow。

    .venv\\Scripts\\python.exe scripts\\s2_1_render_ab\\run_ab.py [--segmented-job output\\<job_id>]

1. 準備測試圖（tmp/s2-1-ab/src）：合成測試圖（1 px 棋盤、細線、平塗色塊、0~255 灰階漸層）、照片裁切
2. 用 Stage 1 的 render(--frame 1) 渲染「原圖單一圖層」與「segment 產生的分層 job」→ 基準
3. 用 ab_scene.py 以 Stage 1 相同的場景，每次只改一個設定再渲染
4. 和原圖逐像素比較，輸出 output/s2-1-ab/results.json 與比較圖
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from animator.config import load_config  # noqa: E402
from animator.pipeline import render  # noqa: E402
from animator.tools import find_blender  # noqa: E402

OUT = ROOT / "output" / "s2-1-ab"
TMP = ROOT / "tmp" / "s2-1-ab"
AB_SCENE = Path(__file__).resolve().parent / "ab_scene.py"

VARIANTS = {
    "V0_stage1": {},                                   # 必須和 Stage 1 的輸出逐像素相同（證明實驗場景 = Stage 1）
    "F_filter_0": {"filter_size": 0.0},
    "F_filter_0.5": {"filter_size": 0.5},
    "F_filter_1.0": {"filter_size": 1.0},
    "I_closest": {"interpolation": "Closest"},
    "I_cubic": {"interpolation": "Cubic"},
    "S_samples_1": {"samples": 1},
    "S_samples_64": {"samples": 64},
    "C_raw_view": {"view_transform": "Raw"},
    "C_raw_view_noncolor": {"view_transform": "Raw", "image_colorspace": "Non-Color"},
    "X_filter0_closest": {"filter_size": 0.0, "interpolation": "Closest"},
    "X_filter0_samples1": {"filter_size": 0.0, "samples": 1},
    "X_filter0_noncolor_raw": {"filter_size": 0.0, "view_transform": "Raw", "image_colorspace": "Non-Color"},
}
SEG_VARIANTS = ["V0_stage1", "F_filter_0", "I_closest", "X_filter0_closest", "X_filter0_samples1"]


# ---------------------------------------------------------------- 測試圖

def make_chart(path: Path) -> None:
    h = w = 512
    img = np.zeros((h, w, 3), np.uint8)
    yy, xx = np.mgrid[0:256, 0:256]
    img[:256, :256] = (((xx + yy) % 2) * 255)[..., None]                       # 1 px 棋盤
    stripes = np.zeros((256, 256), np.uint8)
    stripes[:, ::2] = 255                                                      # 1 px 直線
    stripes[128:, :] = np.where((np.arange(256) // 2) % 2 == 0, 200, 40)       # 2 px 直線
    img[:256, 256:] = stripes[..., None]
    colors = [(0, 0, 0), (1, 1, 1), (5, 5, 5), (16, 16, 16), (64, 64, 64), (128, 128, 128),
              (192, 192, 192), (250, 250, 250), (255, 255, 255), (255, 0, 0), (0, 255, 0),
              (0, 0, 255), (224, 172, 105), (30, 90, 160), (255, 255, 0), (120, 40, 200)]
    for i, c in enumerate(colors):                                             # 16 個 64x64 平塗色塊
        y, x = 256 + (i // 4) * 64, (i % 4) * 64
        img[y:y + 64, x:x + 64] = c
    img[256:512, 256:512] = np.arange(256, dtype=np.uint8)[None, :, None]     # 0~255 灰階（每欄一個值）
    Image.fromarray(img).save(path)


PATCH_COLORS = 16


def prepare_sources() -> dict[str, Path]:
    src = TMP / "src"
    src.mkdir(parents=True, exist_ok=True)
    chart = src / "chart.png"
    make_chart(chart)
    photo = src / "photo.png"
    im = Image.open(ROOT / "assets" / "s2-0" / "photo_desk.jpg").convert("RGB")
    im.crop((480, 320, 1440, 960)).save(photo)                                 # 960x640，原尺寸不縮放
    return {"chart": chart, "photo": photo}


def single_layer_spec(image: Path, name: str) -> Path:
    w, h = Image.open(image).size
    d = TMP / "specs" / name
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image, d / "image.png")
    spec = {"version": 1, "meta": {"name": f"ab-{name}"},
            "canvas": {"width": w, "height": h, "fps": 30, "duration": 1.0},
            "layers": [{"id": "image", "image": "image.png", "z": 0, "position": [w / 2, h / 2]}],
            "animations": []}
    (d / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    return d / "spec.json"


# ---------------------------------------------------------------- 量測

def load_rgb(p: Path) -> np.ndarray:
    return np.asarray(Image.open(p).convert("RGB"))


def metrics(render_img: np.ndarray, truth: np.ndarray, kind: str) -> dict:
    a, t = render_img.astype(np.int16), truth.astype(np.int16)
    d = a - t
    ad = np.abs(d).max(axis=2)
    m = {"mean_abs": round(float(np.abs(d).mean()), 3), "mean_signed": round(float(d.mean()), 3),
         "p99": float(np.percentile(ad, 99)), "max": int(ad.max()),
         "ratio_gt2": round(float((ad > 2).mean()), 5), "ratio_gt8": round(float((ad > 8).mean()), 5)}
    lap = lambda x: float(cv2.Laplacian(cv2.cvtColor(x, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var())  # noqa: E731
    m["sharpness_ratio"] = round(lap(render_img) / max(lap(truth), 1e-9), 4)
    best = None
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            s = np.abs(np.roll(a, (dy, dx), axis=(0, 1))[4:-4, 4:-4] - t[4:-4, 4:-4]).mean()
            if best is None or s < best[0]:
                best = (float(s), dx, dy)
    m["best_shift"] = {"dx": best[1], "dy": best[2], "mean_abs": round(best[0], 3)}
    if kind == "chart":
        m.update(chart_metrics(render_img, truth))
    return m


def chart_metrics(r: np.ndarray, t: np.ndarray) -> dict:
    r16, t16 = r.astype(np.int16), t.astype(np.int16)
    patches = []
    for i in range(PATCH_COLORS):
        y, x = 256 + (i // 4) * 64, (i % 4) * 64
        inner = (slice(y + 8, y + 56), slice(x + 8, x + 56))                   # 避開色塊邊緣
        patches.append({"truth": t[y + 32, x + 32].tolist(),
                        "render_mean": [round(v, 2) for v in r16[inner].reshape(-1, 3).mean(0).tolist()],
                        "max_abs": int(np.abs(r16[inner] - t16[inner]).max())})
    band = (slice(300, 470), slice(256 + 4, 512 - 4))                          # 灰階漸層中段（避開左右邊）
    tone = np.abs(r16[band] - t16[band]).max()
    checker = (slice(16, 240), slice(16, 240))
    return {"patches_max_abs": max(p["max_abs"] for p in patches), "patches": patches,
            "gradient_max_abs": int(tone),
            "gradient_mean_signed": round(float((r16[band] - t16[band]).mean()), 3),
            "checker_contrast": round(float(r[checker].std() / t[checker].std()), 4),
            "flat_mean_signed": round(float(np.mean([np.mean(np.array(p["render_mean"]) - np.array(p["truth"]))
                                                     for p in patches])), 3)}


# ---------------------------------------------------------------- 渲染

def stage1_still(spec: Path, cfg, name: str):
    res = render(spec, cfg, frame=1, name=name, argv=["run_ab.py"])
    return res.output, res.history_dir / "spec.resolved.json"


def ab_render(blender: Path, resolved: Path, variant: dict, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    report = out.with_suffix(".json")
    cmd = [str(blender), "--factory-startup", "-b", "--python-exit-code", "1", "-P", str(AB_SCENE), "--",
           "--resolved", str(resolved), "--frame", "1", "--out", str(out), "--report", str(report),
           "--variant", json.dumps(variant)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"Blender 失敗：{variant}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return json.loads(report.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 比較圖

def zoom_grid(images: dict[str, np.ndarray], truth: np.ndarray, box, scale: int, path: Path,
              diff_gain: int = 4) -> None:
    """上排：放大的裁切（nearest）；下排：|差異| × diff_gain。"""
    x0, y0, x1, y1 = box
    tiles = []
    for name, img in images.items():
        crop = img[y0:y1, x0:x1]
        diff = (np.abs(crop.astype(np.int16) - truth[y0:y1, x0:x1].astype(np.int16)) * diff_gain).clip(0, 255)
        tiles.append((name, crop, diff.astype(np.uint8)))
    tw, th = (x1 - x0) * scale, (y1 - y0) * scale
    grid = Image.new("RGB", (tw * len(tiles), th * 2 + 36), "white")
    d = ImageDraw.Draw(grid)
    for i, (name, crop, diff) in enumerate(tiles):
        grid.paste(Image.fromarray(crop).resize((tw, th), Image.NEAREST), (i * tw, 18))
        grid.paste(Image.fromarray(diff).resize((tw, th), Image.NEAREST), (i * tw, th + 36))
        d.text((i * tw + 4, 3), name, fill="black")
        d.text((i * tw + 4, th + 21), f"|diff| x{diff_gain}", fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    grid.save(path)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segmented-job", type=Path, help="animator segment 產生的 job（output/<job_id>）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config(None, {"paths.output_dir": str(OUT / "jobs"), "paths.temp_dir": str(TMP / "tmp")})
    blender = find_blender(cfg).path
    results: dict = {"blender": str(find_blender(cfg).version), "variants": VARIANTS, "cases": {}}

    sources = prepare_sources()
    cases = {name: {"spec": single_layer_spec(p, name), "truth": load_rgb(p), "kind": name,
                    "variants": list(VARIANTS)} for name, p in sources.items()}
    if args.segmented_job:
        job = args.segmented_job.resolve()
        src_png = job / "segmentation" / "source.png"
        cases["seg_source_single"] = {"spec": single_layer_spec(src_png, "seg-source"), "truth": load_rgb(src_png),
                                      "kind": "photo", "variants": SEG_VARIANTS}
        cases["seg_layered"] = {"spec": job / "spec.json", "truth": load_rgb(src_png), "kind": "photo",
                                "variants": SEG_VARIANTS}

    renders: dict[str, dict[str, np.ndarray]] = {}
    for case, c in cases.items():
        still, resolved = stage1_still(c["spec"], cfg, f"ab-{case}")
        stage1 = load_rgb(still)
        renders[case] = {"truth": c["truth"], "stage1": stage1}
        r = {"stage1_vs_truth": metrics(stage1, c["truth"], c["kind"]), "variants": {}}
        for vname in c["variants"]:
            out = OUT / "renders" / case / f"{vname}.png"
            rep = ab_render(blender, resolved, VARIANTS[vname], out)
            img = load_rgb(out)
            renders[case][vname] = img
            r["variants"][vname] = {"vs_truth": metrics(img, c["truth"], c["kind"]),
                                    "identical_to_stage1": bool((img == stage1).all()),
                                    "effective": rep["effective_settings"], "render_sec": rep["render_sec"]}
            print(f"{case:18s} {vname:24s} mean_abs={r['variants'][vname]['vs_truth']['mean_abs']:7.3f} "
                  f"signed={r['variants'][vname]['vs_truth']['mean_signed']:7.3f} "
                  f"sharp={r['variants'][vname]['vs_truth']['sharpness_ratio']:.3f}", flush=True)
        results["cases"][case] = r

    # 分層 vs 單一圖層 vs numpy 重組
    if args.segmented_job:
        sys.path.insert(0, str(ROOT / "src"))
        from animator.segmentation.compose import fidelity, recomposite

        job = args.segmented_job.resolve()
        spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
        bg = load_rgb(job / "layers" / "background.png")
        stack = []
        for layer in sorted(spec["layers"][1:], key=lambda l: l["z"]):
            rgba = np.asarray(Image.open(job / layer["image"]).convert("RGBA"))
            x0, y0, x1, y1 = layer["meta"]["crop_box"]
            stack.append((rgba, [x0, y0, x1, y1]))
        recomp = recomposite(bg, stack)
        truth = renders["seg_layered"]["truth"]
        hole = np.asarray(Image.open(job / "segmentation" / "masks" / "background_hole.png")) > 127
        outside = ~cv2.dilate(hole.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        cmp = {}
        for vname in SEG_VARIANTS:
            lay, single = renders["seg_layered"][vname], renders["seg_source_single"][vname]
            d = np.abs(lay.astype(np.int16) - single.astype(np.int16)).max(axis=2)
            cmp[vname] = {"layered_vs_single": fidelity(lay, single),
                          "layered_vs_single_outside_hole_ratio_gt8": round(float((d[outside] > 8).mean()), 6),
                          "layered_vs_truth": fidelity(lay, truth), "single_vs_truth": fidelity(single, truth)}
        cmp["numpy_recomposite_vs_truth"] = fidelity(recomp, truth)
        results["segmented"] = cmp
        renders["seg_layered"]["numpy_recomposite"] = recomp

    # 比較圖
    grids = {"chart": [(232, 232, 280, 280), (300, 280, 348, 328), (40, 300, 88, 348)],
             "photo": [(380, 120, 460, 200), (600, 380, 680, 460)]}
    pick = ["truth", "stage1", "F_filter_0", "F_filter_0.5", "I_closest", "S_samples_1", "X_filter0_closest",
            "X_filter0_samples1", "C_raw_view_noncolor"]
    for case, boxes in grids.items():
        for i, box in enumerate(boxes):
            zoom_grid({k: renders[case][k] for k in pick if k in renders[case]}, renders[case]["truth"], box, 6,
                      OUT / "grids" / f"{case}_{i}.png")
    if args.segmented_job:
        hole = np.asarray(Image.open(args.segmented_job / "segmentation" / "masks" / "background_hole.png")) > 127
        ys, xs = np.where(hole)
        cy, cx = int(ys.mean()), int(xs.min()) + 10
        box = (cx - 60, cy - 60, cx + 60, cy + 60)
        sel = {"truth": renders["seg_layered"]["truth"], "numpy_recomposite": renders["seg_layered"]["numpy_recomposite"],
               "single_stage1": renders["seg_source_single"]["V0_stage1"], "layered_stage1": renders["seg_layered"]["V0_stage1"],
               "single_f0_closest": renders["seg_source_single"]["X_filter0_closest"],
               "layered_f0_closest": renders["seg_layered"]["X_filter0_closest"]}
        zoom_grid(sel, renders["seg_layered"]["truth"], box, 4, OUT / "grids" / "segmented_edge.png")
        results["segmented_grid_box"] = box

    (OUT / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("results:", OUT / "results.json")


if __name__ == "__main__":
    main()
