"""S2-0 模型可行性測試（CPU）。不是正式的 Stage 2 程式碼。

用法：
  python scripts/s2_0/bench.py run              # 依序在獨立 process 中執行所有 worker，彙整結果
  python scripts/s2_0/bench.py worker <name>    # 單獨執行一個 worker（gdino / sam2 / pymatting / opencv / sequence）

輸出：output/s2-0/<worker>/（視覺化結果）、output/s2-0/results.json
"""

from __future__ import annotations

import gc
import json
import platform
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

from common import IMAGES, OUT, model_dir, setup_env

setup_env(offline=True)   # 執行時完全離線

import memprobe  # noqa: E402

SIZES = (512, 1024)
IMAGE_FILES = {
    "illustration_camp": "illustration_camp.png",
    "illustration_picnic": "illustration_picnic.png",
    "photo_desk": "photo_desk.jpg",
    "occlusion_dogcat": "occlusion_dogcat.jpg",
}
LABELS = {
    "illustration_camp": ["sun", "cloud", "tree", "tent", "girl", "dog"],
    "illustration_picnic": ["person", "tree", "picnic blanket", "basket", "cup"],
    "photo_desk": ["person", "laptop", "potted plant", "computer mouse", "notebook", "phone", "bottle"],
    "occlusion_dogcat": ["dog", "cat"],
}
BOX_THRESHOLD, TEXT_THRESHOLD = 0.35, 0.25


# ---------------------------------------------------------------- helpers

def load_images():
    from PIL import Image

    out = {}
    for key, name in IMAGE_FILES.items():
        im = Image.open(IMAGES / name).convert("RGB")
        for size in SIZES:
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
    import os
    return {"python": sys.version.split()[0], "platform": platform.platform(), "cpu": cpu,
            "logical_cpus": os.cpu_count(), "ram": memprobe.system_ram()}


def draw_boxes(image, dets, path):
    from PIL import ImageDraw

    im = image.copy()
    d = ImageDraw.Draw(im)
    for det in dets:
        x0, y0, x1, y1 = det["box"]
        d.rectangle((x0, y0, x1, y1), outline=(255, 0, 0), width=3)
        d.text((x0 + 4, y0 + 2), f"{det['label']} {det['score']:.2f}", fill=(255, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


# ---------------------------------------------------------------- workers

def worker_gdino(probe):
    with probe.phase("import"):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    torch.manual_seed(0)
    images = load_images()
    path = model_dir("grounding-dino-tiny")
    with probe.phase("load"):
        processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(path, local_files_only=True).eval()

    def infer(image, labels):
        w, h = image.size
        text = ". ".join(labels) + "."
        size = {"shortest_edge": min(w, h), "longest_edge": max(w, h)}  # 以原尺寸推論（不放大到 800）
        inputs = processor(images=image, text=text, return_tensors="pt", size=size)
        with torch.inference_mode():
            outputs = model(**inputs)
        res = processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=BOX_THRESHOLD, text_threshold=TEXT_THRESHOLD,
            target_sizes=[(h, w)])[0]
        names = res.get("text_labels", res.get("labels"))
        dets = [{"label": str(n), "score": round(float(s), 3), "box": [round(float(v), 1) for v in b]}
                for n, s, b in zip(names, res["scores"], res["boxes"])]
        return dets, list(inputs["pixel_values"].shape)

    with probe.phase("warmup"):
        infer(images[("illustration_camp", 512)], LABELS["illustration_camp"])

    results = {}
    for (key, size), image in images.items():
        with probe.phase(f"infer/{key}/{size}"):
            dets, shape = infer(image, LABELS[key])
        results[f"{key}/{size}"] = {"input_tensor": shape, "detections": dets}
        draw_boxes(image, dets, OUT / "gdino" / f"{key}_{size}.png")
    save_json(OUT / "gdino" / "detections.json", results)

    with probe.phase("unload"):
        del model, processor
        gc.collect()
    return {"params_m": None, "results": {k: len(v["detections"]) for k, v in results.items()}}


def _best_boxes(dets, max_n=8):
    """每個標籤最多保留 2 個框，依分數排序。"""
    keep, per = [], {}
    for d in sorted(dets, key=lambda d: -d["score"]):
        if per.get(d["label"], 0) < 2:
            keep.append(d)
            per[d["label"]] = per.get(d["label"], 0) + 1
    return keep[:max_n]


def worker_sam2(probe):
    import numpy as np

    with probe.phase("import"):
        import torch
        from transformers import Sam2Model, Sam2Processor
    from PIL import Image

    torch.manual_seed(0)
    dets_all = json.loads((OUT / "gdino" / "detections.json").read_text(encoding="utf-8"))
    images = load_images()
    path = model_dir("sam2.1-hiera-tiny")
    with probe.phase("load"):
        processor = Sam2Processor.from_pretrained(path, local_files_only=True)
        model = Sam2Model.from_pretrained(path, local_files_only=True).eval()
    params = sum(p.numel() for p in model.parameters()) / 1e6

    timing: dict = {}

    def infer(image, boxes):
        t0 = time.perf_counter()
        inputs = processor(images=image, input_boxes=[[b["box"] for b in boxes]], return_tensors="pt")
        t1 = time.perf_counter()
        with torch.inference_mode():
            emb = model.get_image_embeddings(inputs["pixel_values"])          # image encoder（每張圖一次）
            t2 = time.perf_counter()
            outputs = model(input_boxes=inputs["input_boxes"], image_embeddings=emb, multimask_output=False)
        t3 = time.perf_counter()
        masks = processor.post_process_masks(outputs.pred_masks.cpu(), inputs["original_sizes"])[0]
        t4 = time.perf_counter()
        timing.clear()
        timing.update({"preprocess": round(t1 - t0, 3), "encoder": round(t2 - t1, 3),
                       "decoder_all_boxes": round(t3 - t2, 3), "postprocess": round(t4 - t3, 3)})
        scores = outputs.iou_scores.squeeze().reshape(-1).tolist()
        return masks.reshape(len(boxes), *masks.shape[-2:]).numpy().astype(bool), scores, list(inputs["pixel_values"].shape)

    first = ("illustration_camp", 512)
    with probe.phase("warmup"):
        infer(images[first], _best_boxes(dets_all["illustration_camp/512"]["detections"])[:1])

    results = {}
    palette = np.array([[230, 25, 75], [60, 180, 75], [255, 225, 25], [0, 130, 200], [245, 130, 48],
                        [145, 30, 180], [70, 240, 240], [240, 50, 230]], dtype=np.float32)
    for (key, size), image in images.items():
        boxes = _best_boxes(dets_all[f"{key}/{size}"]["detections"])
        if not boxes:
            results[f"{key}/{size}"] = {"boxes": 0}
            continue
        with probe.phase(f"infer/{key}/{size}"):
            masks, scores, shape = infer(image, boxes)
        rgb = np.asarray(image, dtype=np.float32)
        over = rgb.copy()
        out_dir = OUT / "sam2" / f"{key}_{size}"
        out_dir.mkdir(parents=True, exist_ok=True)
        info = []
        for i, (m, b) in enumerate(zip(masks, boxes)):
            over[m] = over[m] * 0.45 + palette[i % len(palette)] * 0.55
            Image.fromarray((m * 255).astype(np.uint8)).save(out_dir / f"mask_{i}_{b['label'].replace(' ', '_')}.png")
            info.append({"label": b["label"], "det_score": b["score"], "iou_pred": round(scores[i], 3),
                         "area_px": int(m.sum()), "box": b["box"]})
        Image.fromarray(over.clip(0, 255).astype(np.uint8)).save(OUT / "sam2" / f"{key}_{size}_overlay.png")
        results[f"{key}/{size}"] = {"input_tensor": shape, "boxes": len(boxes), "timing": dict(timing),
                                    "masks": info}
    save_json(OUT / "sam2" / "masks.json", results)

    with probe.phase("unload"):
        del model, processor
        gc.collect()
    return {"params_m": round(params, 1), "results": {k: len(v.get("masks", [])) for k, v in results.items()}}


def _primary_mask(key, size):
    """每張圖取一個主要物件的遮罩（人物 / 動物優先）給 matting 使用。"""
    info = json.loads((OUT / "sam2" / "masks.json").read_text(encoding="utf-8"))[f"{key}/{size}"]["masks"]
    order = {"girl": 0, "person": 0, "dog": 1, "cat": 2}
    best = min(range(len(info)), key=lambda i: (order.get(info[i]["label"], 9), -info[i]["area_px"]))
    d = OUT / "sam2" / f"{key}_{size}"
    return next(d.glob(f"mask_{best}_*.png")), info[best]


def worker_pymatting(probe):
    import numpy as np

    with probe.phase("import"):
        import cv2
        from pymatting import estimate_alpha_cf, estimate_foreground_ml
    from PIL import Image

    # numba 第一次執行要 JIT 編譯，單獨測量
    with probe.phase("jit_warmup"):
        tiny = np.random.default_rng(0).random((32, 32, 3))
        tri = np.full((32, 32), 0.5)
        tri[:8], tri[-8:] = 0, 1
        estimate_foreground_ml(tiny, estimate_alpha_cf(tiny, tri))

    images = load_images()
    results = {}
    for (key, size), image in images.items():
        mask_path, info = _primary_mask(key, size)
        mask = np.asarray(Image.open(mask_path)) > 127
        band = max(3, size // 128)                       # 512 → 4 px、1024 → 8 px
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
        fg = cv2.erode(mask.astype(np.uint8), kernel) > 0
        unknown_outer = cv2.dilate(mask.astype(np.uint8), kernel) > 0
        trimap = np.zeros(mask.shape, np.float64)
        trimap[unknown_outer] = 0.5
        trimap[fg] = 1.0
        ys, xs = np.where(unknown_outer)
        m = 2 * band
        y0, y1 = max(0, ys.min() - m), min(mask.shape[0], ys.max() + m + 1)
        x0, x1 = max(0, xs.min() - m), min(mask.shape[1], xs.max() + m + 1)
        rgb = np.asarray(image, dtype=np.float64)[y0:y1, x0:x1] / 255.0
        tri = trimap[y0:y1, x0:x1]
        with probe.phase(f"infer/{key}/{size}"):
            alpha = estimate_alpha_cf(rgb, tri)
            fg_rgb = estimate_foreground_ml(rgb, alpha)
        out = np.dstack([(fg_rgb * 255).clip(0, 255), (alpha * 255).clip(0, 255)]).astype(np.uint8)
        (OUT / "pymatting").mkdir(parents=True, exist_ok=True)
        Image.fromarray(out, "RGBA").save(OUT / "pymatting" / f"{key}_{size}_{info['label'].replace(' ', '_')}.png")
        results[f"{key}/{size}"] = {"label": info["label"], "crop": [x1 - x0, y1 - y0],
                                    "unknown_px": int((tri == 0.5).sum()), "band_px": band}
    return {"results": results}


def worker_opencv(probe):
    import numpy as np

    with probe.phase("import"):
        import cv2
    from PIL import Image

    images = load_images()
    results = {}
    for (key, size), image in images.items():
        d = OUT / "sam2" / f"{key}_{size}"
        masks = [np.asarray(Image.open(p)) > 127 for p in sorted(d.glob("mask_*.png"))]
        # 只移除「非背景」的物件：人物 / 動物 / 小物件（太陽與雲也算元素）
        hole = np.zeros(masks[0].shape, np.uint8)
        for m in masks:
            if m.mean() < 0.35:   # 排除太大的區域（例如整片野餐墊或樹叢）
                hole |= m.astype(np.uint8)
        k = max(3, size // 100)
        hole = cv2.dilate(hole, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
        bgr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        with probe.phase(f"infer/{key}/{size}"):
            out = cv2.inpaint(bgr, hole, 3, cv2.INPAINT_TELEA)
        (OUT / "opencv").mkdir(parents=True, exist_ok=True)
        side = np.concatenate([bgr, out], axis=1)
        cv2.imwrite(str(OUT / "opencv" / f"{key}_{size}.png"), side)
        results[f"{key}/{size}"] = {"hole_ratio": round(float(hole.mean()), 3)}
    return {"results": results}


def worker_sequence(probe):
    """同一個 process 依序：載入 DINO → 推論 → 釋放 → 載入 SAM 2 → 推論 → 釋放，確認記憶體有回收。"""
    with probe.phase("import"):
        import torch
        from transformers import (AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model,
                                  Sam2Processor)
    image = load_images()[("photo_desk", 1024)]
    w, h = image.size
    with probe.phase("dino_load_infer"):
        proc = AutoProcessor.from_pretrained(model_dir("grounding-dino-tiny"), local_files_only=True)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(model_dir("grounding-dino-tiny"),
                                                                    local_files_only=True).eval()
        inputs = proc(images=image, text="person. laptop.", return_tensors="pt",
                      size={"shortest_edge": min(w, h), "longest_edge": max(w, h)})
        with torch.inference_mode():
            out = model(**inputs)
        res = proc.post_process_grounded_object_detection(out, inputs.input_ids, threshold=BOX_THRESHOLD,
                                                          text_threshold=TEXT_THRESHOLD, target_sizes=[(h, w)])[0]
        boxes = [[float(v) for v in b] for b in res["boxes"]][:2]
    with probe.phase("dino_unload"):
        del model, proc, inputs, out, res
        gc.collect()
    with probe.phase("sam2_load_infer"):
        proc = Sam2Processor.from_pretrained(model_dir("sam2.1-hiera-tiny"), local_files_only=True)
        model = Sam2Model.from_pretrained(model_dir("sam2.1-hiera-tiny"), local_files_only=True).eval()
        inputs = proc(images=image, input_boxes=[boxes], return_tensors="pt")
        with torch.inference_mode():
            model(**inputs, multimask_output=False)
    with probe.phase("sam2_unload"):
        del model, proc, inputs
        gc.collect()
    return {"boxes": len(boxes)}


WORKERS = {"gdino": worker_gdino, "sam2": worker_sam2, "pymatting": worker_pymatting,
           "opencv": worker_opencv, "sequence": worker_sequence}


def run_worker(name: str) -> None:
    probe = memprobe.Probe()
    t0 = time.perf_counter()
    ws0, priv0 = memprobe.current()
    status, error, extra = "ok", None, {}
    try:
        extra = WORKERS[name](probe)
    except Exception as exc:  # 記錄失敗原因（Windows 相容性的一部分）
        import traceback
        status, error = "failed", traceback.format_exc()
    probe.stop()
    try:
        import torch
        threads = torch.get_num_threads()
    except Exception:
        threads = None
    save_json(OUT / f"worker_{name}.json", {
        "worker": name, "status": status, "error": error, "total_sec": round(time.perf_counter() - t0, 2),
        "start_ws_mb": memprobe.mb(ws0), "start_private_mb": memprobe.mb(priv0),
        "torch_threads": threads, "phases": probe.phases, **(extra or {})})
    print(f"[{status}] {name} {time.perf_counter() - t0:.1f}s")
    if error:
        print(error)


# ---------------------------------------------------------------- orchestrator

def gpu_used_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out.splitlines()[0])
    except Exception:
        return None


ALL = ("gdino", "sam2", "pymatting", "opencv", "sequence")


def run_all(only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    previous = OUT / "results.json"
    summary = (json.loads(previous.read_text(encoding="utf-8")) if previous.is_file() and only
               else {"workers": {}})
    summary.update({"env": env_info(), "gpu_before_mb": gpu_used_mb()})
    if only:   # 沿用先前已完成的 worker 結果
        for name in ALL:
            f = OUT / f"worker_{name}.json"
            if name not in only and name not in summary["workers"] and f.is_file():
                summary["workers"][name] = json.loads(f.read_text(encoding="utf-8"))
    for name in (only or ALL):
        samples, stop = [], threading.Event()

        def poll():
            while not stop.is_set():
                v = gpu_used_mb()
                if v is not None:
                    samples.append(v)
                stop.wait(1.0)

        th = threading.Thread(target=poll, daemon=True)
        th.start()
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, __file__, "worker", name])
        stop.set()
        th.join()
        data = json.loads((OUT / f"worker_{name}.json").read_text(encoding="utf-8"))
        data["wall_sec"] = round(time.perf_counter() - t0, 2)
        data["exit_code"] = proc.returncode
        data["gpu_used_mb"] = {"min": min(samples), "max": max(samples)} if samples else None
        summary["workers"][name] = data
    summary["gpu_after_mb"] = gpu_used_mb()
    save_json(OUT / "results.json", summary)
    print(f"results -> {OUT / 'results.json'}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["worker"]:
        run_worker(sys.argv[2])
    else:
        run_all(sys.argv[2:])   # bench.py run [worker ...]
