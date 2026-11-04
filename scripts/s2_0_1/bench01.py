"""S2-0.1：MI-GAN（官方 ONNX pipeline）大面積背景修補的可行性測試。不是正式的 Stage 2 程式碼。

用法（每個 worker 在獨立的 process 裡跑，記憶體量測才不會互相干擾）：
    .venv\\Scripts\\python.exe scripts\\s2_0_1\\bench01.py run            # 全部
    .venv\\Scripts\\python.exe scripts\\s2_0_1\\bench01.py run migan       # 只跑其中一個
    .venv\\Scripts\\python.exe scripts\\s2_0_1\\bench01.py grid           # 產生比較圖

測試設計：
- 圖片：assets/s2-0 的 4 張圖（插畫兩張只在本機使用），長邊 512 / 1024，照片另外跑原始 1920。
- 遮罩（hole = True 表示要補的區域）：
  rect_05/15/30/50：置中矩形，面積占比 5/15/30/50%
  free_s/m/l：固定 seed 的不規則筆刷，覆蓋率約 10/25/40%
  obj_d8 / obj_d24：S2-0 的 SAM 2.1 物件遮罩（1024 版）縮放後外擴 8 / 24 px
- 輸入圖片的 hole 區域一律先塗成洋紅色 (255,0,255)：輸出裡如果出現洋紅色 = 遮罩極性或合成有問題。
- MI-GAN ONNX 的遮罩慣例：255 = 已知區域、0 = 要補的區域（官方 README）。
- 比較對象：OpenCV Telea / Navier-Stokes（半徑 3）。
- 只讀取 output/s2-0 的遮罩，不修改任何 S2-0 的檔案。
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
import winreg
from pathlib import Path

from common01 import IMAGES, MIGAN, MIGAN_PATH, OUT, OUT_S2_0, setup_env

setup_env(offline=True)   # 執行時完全離線

import memprobe  # noqa: E402  （scripts/s2_0/memprobe.py，只 import 不修改）

THREADS = 4
SIZES = (512, 1024, "orig")
IMAGE_FILES = {
    "illustration_camp": "illustration_camp.png",
    "illustration_picnic": "illustration_picnic.png",
    "photo_desk": "photo_desk.jpg",
    "occlusion_dogcat": "occlusion_dogcat.jpg",
}
OBJ_LABELS = {   # 要「移除」的物件（用 S2-0 SAM 2.1 1024 版的遮罩）
    "illustration_camp": {"girl", "dog"},
    "illustration_picnic": {"person"},
    "photo_desk": {"person"},
    "occlusion_dogcat": {"dog"},
}
RECTS = {"rect_05": 0.05, "rect_15": 0.15, "rect_30": 0.30, "rect_50": 0.50}
FREES = {"free_s": 0.10, "free_m": 0.25, "free_l": 0.40}
OBJS = {"obj_d8": 8, "obj_d24": 24}
MASK_NAMES = [*RECTS, *FREES, *OBJS]
MAGENTA = (255, 0, 255)
RUNS = 3


# ---------------------------------------------------------------- helpers

def load_images():
    from PIL import Image

    out = {}
    for key, name in IMAGE_FILES.items():
        im = Image.open(IMAGES / name).convert("RGB")
        for size in SIZES:
            if size == "orig":
                if max(im.size) <= 1024:      # 插畫原圖就是 1024，不重複測
                    continue
                out[(key, size)] = im
                continue
            scale = size / max(im.size)
            out[(key, size)] = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return out


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2,
                               default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                    encoding="utf-8")


def env_info() -> dict:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            cpu = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        cpu = platform.processor()
    return {"python": sys.version.split()[0], "platform": platform.platform(), "cpu": cpu,
            "logical_cpus": os.cpu_count(), "ram": memprobe.system_ram()}


def gpu_used_mb() -> int | None:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def tag(key, size):
    return f"{key}_{size}"


# ---------------------------------------------------------------- masks

def make_masks(key, image):
    """回傳 {mask_name: bool ndarray(H, W)}，True = 要補的區域。"""
    import cv2
    import numpy as np
    from PIL import Image

    w, h = image.size
    masks = {}
    for name, frac in RECTS.items():
        m = np.zeros((h, w), bool)
        rw, rh = round(w * frac ** 0.5), round(h * frac ** 0.5)
        x0, y0 = (w - rw) // 2, (h - rh) // 2
        m[y0:y0 + rh, x0:x0 + rw] = True
        masks[name] = m

    for i, (name, target) in enumerate(FREES.items()):
        rng = np.random.default_rng(1000 + i)
        canvas = np.zeros((h, w), np.uint8)
        side = max(w, h)
        while canvas.mean() / 255 < target:
            pts = [rng.uniform((0.1 * w, 0.1 * h), (0.9 * w, 0.9 * h))]
            for _ in range(int(rng.integers(3, 7))):
                ang = rng.uniform(0, 2 * np.pi)
                step = rng.uniform(0.08, 0.2) * side
                nxt = np.clip(pts[-1] + step * np.array([np.cos(ang), np.sin(ang)]), 0, (w - 1, h - 1))
                pts.append(nxt)
            thick = int(rng.uniform(side / 40, side / 14))
            cv2.polylines(canvas, [np.array(pts, np.int32)], False, 255, thick, lineType=cv2.LINE_8)
        masks[name] = canvas > 0

    d = OUT_S2_0 / "sam2" / f"{key}_1024"
    obj = np.zeros((h, w), bool)
    for p in sorted(d.glob("mask_*.png")):
        label = p.stem.split("_", 2)[2]
        if label in OBJ_LABELS[key]:
            m = Image.open(p).convert("L").resize((w, h), Image.NEAREST)
            obj |= np.asarray(m) > 127
    for name, px in OBJS.items():
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
        masks[name] = cv2.dilate(obj.astype(np.uint8), k) > 0
    return masks


def official_crop(hole):
    """依官方 create_onnx_pipeline.py 的 get_masked_bbox 邏輯推算 pipeline 實際裁切的區域（numpy 版）。"""
    import numpy as np

    h, w = hole.shape
    xs, ys = np.where(hole.any(axis=0))[0], np.where(hole.any(axis=1))[0]
    x_min, x_max = (xs.min(), xs.max()) if len(xs) else (w, 0)
    y_min, y_max = (ys.min(), ys.max()) if len(ys) else (h, 0)
    x_min, x_max = min(x_min, x_max), max(x_min, x_max)
    y_min, y_max = min(y_min, y_max), max(y_min, y_max)
    cx, cy = (x_min + x_max) // 2, (y_min + y_max) // 2
    crop = max(max(x_max - x_min, y_max - y_min) + 2 * 128, 512)
    off = crop // 2
    x0, x1 = max(cx - off, 0), min(cx + off, w)
    y0, y1 = max(cy - off, 0), min(cy + off, h)
    ex, ey = max(crop - (x1 - x0), 0), max(crop - (y1 - y0), 0)
    x0, x1 = max(x0 - ex, 0), min(x1 + ex, w)
    y0, y1 = max(y0 - ey, 0), min(y1 + ey, h)
    return {"box": [int(x0), int(y0), int(x1), int(y1)], "w": int(x1 - x0), "h": int(y1 - y0),
            "model_scale": round(512 / max(x1 - x0, y1 - y0), 3)}


def magenta_input(rgb, hole):
    x = rgb.copy()
    x[hole] = MAGENTA
    return x


def evaluate(orig, inp, out, hole, synthetic):
    """品質與合成檢查。orig/inp/out: uint8 (H, W, 3)；hole: bool (H, W)。"""
    import cv2
    import numpy as np

    res = {"size_ok": out.shape == orig.shape}
    o = out.astype(np.int16)
    # 1) 洋紅色殘留：hole 裡面「看起來像洋紅色」的像素（原圖同位置的數量當作基準）
    def is_mag(a):
        a = a.astype(np.int16)
        return (a[..., 0] - a[..., 1] > 120) & (a[..., 2] - a[..., 1] > 120)
    mag_out = is_mag(out) & hole
    res["magenta_in_hole_px"] = int(mag_out.sum())
    res["magenta_in_hole_baseline_px"] = int((is_mag(orig) & hole).sum())
    # 洋紅色出現在離 hole 邊界多遠的地方（hole 內部到已知區域的距離）
    dist_in = cv2.distanceTransform(hole.astype(np.uint8), cv2.DIST_L2, 3)
    res["magenta_max_depth_px"] = round(float(dist_in[mag_out].max()), 1) if mag_out.any() else 0.0
    # 2) 已知區域是否保持不變：依「離 hole 的距離」找出有變動的最遠距離（= 實際的混合帶寬度）
    known = ~hole
    diff = np.abs(o - inp.astype(np.int16)).max(axis=2)
    dist_out = cv2.distanceTransform(known.astype(np.uint8), cv2.DIST_L2, 3)
    changed = known & (diff > 0)
    res["known_changed_px"] = int(changed.sum())
    res["known_changed_max_dist_px"] = round(float(dist_out[changed].max()), 1) if changed.any() else 0.0
    far = known & (dist_out > 8)
    res["known_far_max_diff"] = int(diff[far].max()) if far.any() else 0   # 距離 > 8 px 必須是 0
    # 3) 有原圖可對照時（合成遮罩），hole 內的 PSNR / MAE
    if synthetic and hole.any():
        e = (o[hole] - orig.astype(np.int16)[hole]).astype(np.float64)
        mse = float((e ** 2).mean())
        res["psnr_hole"] = round(10 * np.log10(255 ** 2 / mse), 2) if mse > 0 else 99.0
        res["mae_hole"] = round(float(np.abs(e).mean()), 2)
    return res


def save_case(method, key, size, mask_name, arr):
    from PIL import Image

    d = OUT / "cases" / method
    d.mkdir(parents=True, exist_ok=True)
    im = Image.fromarray(arr)
    if size == "orig":
        im.save(d / f"{tag(key, size)}_{mask_name}.jpg", quality=95)
    else:
        im.save(d / f"{tag(key, size)}_{mask_name}.png")


def save_input(key, size, mask_name, arr):
    save_case("input", key, size, mask_name, arr)


# ---------------------------------------------------------------- workers

def worker_migan(probe):
    import numpy as np

    with probe.phase("import"):
        import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = THREADS
    opts.inter_op_num_threads = 1
    with probe.phase("load"):
        sess = ort.InferenceSession(str(MIGAN_PATH), sess_options=opts, providers=["CPUExecutionProvider"])
    io = {"inputs": [(i.name, i.shape, i.type) for i in sess.get_inputs()],
          "outputs": [(o.name, o.shape, o.type) for o in sess.get_outputs()],
          "providers": sess.get_providers()}

    def infer(rgb, hole):
        img = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])
        mask = np.where(hole, 0, 255).astype(np.uint8)[None, None]   # 255 = 已知、0 = 要補
        return sess.run(None, {"image": img, "mask": mask})[0][0].transpose(1, 2, 0)

    images = load_images()
    results = {}
    first = True
    for size in SIZES:
        with probe.phase(f"infer/{size}"):
            for (key, s), image in images.items():
                if s != size:
                    continue
                rgb = np.asarray(image)
                for mask_name, hole in make_masks(key, image).items():
                    inp = magenta_input(rgb, hole)
                    times = []
                    for _ in range(RUNS + (1 if first else 0)):
                        t0 = time.perf_counter()
                        out = infer(inp, hole)
                        times.append(time.perf_counter() - t0)
                    cold = None
                    if first:
                        cold, times, first = times[0], times[1:], False
                    save_input(key, size, mask_name, inp)
                    save_case("migan", key, size, mask_name, out)
                    r = {"image_wh": list(image.size), "hole_ratio": round(float(hole.mean()), 3),
                         "crop": official_crop(hole), "sec_median": round(statistics.median(times), 3),
                         "sec_runs": [round(t, 3) for t in times],
                         **evaluate(rgb, inp, out, hole, synthetic=not mask_name.startswith("obj"))}
                    if cold is not None:
                        r["sec_first_call"] = round(cold, 3)
                    results[f"{tag(key, size)}/{mask_name}"] = r

    # 遮罩極性對照：故意把遮罩反過來（hole 當成已知區域），確認錯誤可以被偵測到
    polarity = {}
    for (key, size), image in images.items():
        if size != 1024:
            continue
        rgb = np.asarray(image)
        hole = make_masks(key, image)["rect_15"]
        inp = magenta_input(rgb, hole)
        img = np.ascontiguousarray(inp.transpose(2, 0, 1)[None])
        wrong = np.where(hole, 255, 0).astype(np.uint8)[None, None]
        out = sess.run(None, {"image": img, "mask": wrong})[0][0].transpose(1, 2, 0)
        save_case("migan_inverted", key, size, "rect_15", out)
        diff_known = np.abs(out.astype(np.int16) - rgb.astype(np.int16)).max(axis=2)[~hole]
        mag = (out[..., 0].astype(int) - out[..., 1] > 120) & (out[..., 2].astype(int) - out[..., 1] > 120)
        polarity[tag(key, size)] = {
            "magenta_kept_in_hole_ratio": round(float(mag[hole].mean()), 3),
            "known_changed_ratio": round(float((diff_known > 0).mean()), 3),
        }
    return {"io": io, "model": {**MIGAN, "size_bytes": MIGAN_PATH.stat().st_size},
            "ort_version": ort.__version__, "results": results, "polarity_inverted": polarity}


def worker_opencv(probe):
    import numpy as np

    with probe.phase("import"):
        import cv2
    images = load_images()
    results = {}
    for size in SIZES:
        with probe.phase(f"infer/{size}"):
            for (key, s), image in images.items():
                if s != size:
                    continue
                rgb = np.asarray(image)
                for mask_name, hole in make_masks(key, image).items():
                    inp = magenta_input(rgb, hole)
                    bgr = cv2.cvtColor(inp, cv2.COLOR_RGB2BGR)
                    m8 = hole.astype(np.uint8) * 255
                    for method, flag in (("telea", cv2.INPAINT_TELEA), ("ns", cv2.INPAINT_NS)):
                        t0 = time.perf_counter()
                        out = cv2.cvtColor(cv2.inpaint(bgr, m8, 3, flag), cv2.COLOR_BGR2RGB)
                        sec = time.perf_counter() - t0
                        save_case(method, key, size, mask_name, out)
                        results[f"{method}/{tag(key, size)}/{mask_name}"] = {
                            "hole_ratio": round(float(hole.mean()), 3), "sec": round(sec, 3),
                            **evaluate(rgb, inp, out, hole, synthetic=not mask_name.startswith("obj"))}
    return {"cv2_version": cv2.__version__, "results": results}


def worker_migan_variants(probe):
    """輸入變體（每個 case 跑 1 次）：
    - orig：hole 內保留原本內容（實際用途：要移除的物件還在圖上）
    - prefill：hole 先用 OpenCV Telea 補一次，再交給 MI-GAN（避免 hole 內容滲入混合帶 / 縮圖）
    對照 worker_migan 的 magenta 輸入（hole 塗滿洋紅色）。
    """
    import cv2
    import numpy as np

    with probe.phase("import"):
        import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = THREADS
    opts.inter_op_num_threads = 1
    with probe.phase("load"):
        sess = ort.InferenceSession(str(MIGAN_PATH), sess_options=opts, providers=["CPUExecutionProvider"])

    def infer(rgb, hole):
        img = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])
        mask = np.where(hole, 0, 255).astype(np.uint8)[None, None]
        return sess.run(None, {"image": img, "mask": mask})[0][0].transpose(1, 2, 0)

    images = load_images()
    results = {}
    for size in SIZES:
        with probe.phase(f"infer/{size}"):
            for (key, s), image in images.items():
                if s != size:
                    continue
                rgb = np.asarray(image)
                for mask_name, hole in make_masks(key, image).items():
                    synthetic = not mask_name.startswith("obj")
                    m8 = hole.astype(np.uint8) * 255
                    pre = cv2.cvtColor(cv2.inpaint(cv2.cvtColor(magenta_input(rgb, hole), cv2.COLOR_RGB2BGR),
                                                   m8, 3, cv2.INPAINT_TELEA), cv2.COLOR_BGR2RGB)
                    for variant, inp in (("orig", rgb), ("prefill", pre)):
                        t0 = time.perf_counter()
                        out = infer(inp, hole)
                        sec = time.perf_counter() - t0
                        save_case(f"migan_{variant}", key, size, mask_name, out)
                        r = {"hole_ratio": round(float(hole.mean()), 3), "sec": round(sec, 3),
                             **evaluate(rgb, inp, out, hole, synthetic=synthetic)}
                        if variant == "orig" and synthetic:
                            r["note"] = "hole 內原圖內容會經過 1~2 px 混合帶滲入，PSNR 偏高、不可和其他變體直接比"
                        results[f"{variant}/{tag(key, size)}/{mask_name}"] = r
    return {"results": results}


WORKERS = {"migan": worker_migan, "opencv": worker_opencv, "migan_variants": worker_migan_variants}


def run_worker(name: str) -> None:
    probe = memprobe.Probe()
    t0 = time.perf_counter()
    gpu0 = gpu_used_mb()
    status, err, extra = "ok", None, {}
    try:
        extra = WORKERS[name](probe)
    except Exception as e:  # noqa: BLE001 — 失敗也要寫出報告
        import traceback
        status, err = "error", traceback.format_exc()
    probe.stop()
    save_json(OUT / f"worker_{name}.json", {
        "worker": name, "status": status, "error": err, "total_sec": round(time.perf_counter() - t0, 1),
        "threads": THREADS, "gpu_used_mb_before": gpu0, "gpu_used_mb_after": gpu_used_mb(),
        "phases": probe.phases, **extra})
    print(f"{name}: {status}")


def run_all(only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in only or list(WORKERS):
        subprocess.run([sys.executable, __file__, "worker", name], check=False)
    merged = {"env": env_info()}
    for name in WORKERS:
        f = OUT / f"worker_{name}.json"
        if f.is_file():
            merged[name] = json.loads(f.read_text(encoding="utf-8"))
    save_json(OUT / "results.json", merged)


# ---------------------------------------------------------------- grids

def make_grids() -> None:
    """每個（圖片, 尺寸）一張比較圖：列 = 遮罩，欄 = 輸入（洋紅色 = hole）| MI-GAN（洋紅輸入 / 原內容 /
    Telea 預填）| Telea | NS。"""
    from PIL import Image, ImageDraw

    cols = ["input", "migan", "migan_orig", "migan_prefill", "telea", "ns"]
    thumb_w = 320
    images = load_images()
    (OUT / "grids").mkdir(parents=True, exist_ok=True)
    for (key, size), image in images.items():
        th = round(image.height * thumb_w / image.width)
        grid = Image.new("RGB", (thumb_w * len(cols), (th + 18) * len(MASK_NAMES) + 18), "white")
        d = ImageDraw.Draw(grid)
        for c, col in enumerate(cols):
            d.text((c * thumb_w + 4, 2), col, fill="black")
        for r, mask_name in enumerate(MASK_NAMES):
            y = 18 + r * (th + 18)
            d.text((4, y), mask_name, fill="black")
            for c, col in enumerate(cols):
                ext = "jpg" if size == "orig" else "png"
                p = OUT / "cases" / col / f"{tag(key, size)}_{mask_name}.{ext}"
                if p.is_file():
                    grid.paste(Image.open(p).resize((thumb_w, th), Image.LANCZOS), (c * thumb_w, y + 16))
        grid.save(OUT / "grids" / f"{tag(key, size)}.jpg", quality=90)
    print("grids:", OUT / "grids")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "worker":
        run_worker(sys.argv[2])
    elif cmd == "grid":
        make_grids()
    else:
        run_all(sys.argv[2:])
