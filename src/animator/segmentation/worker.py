"""AI 子行程的入口：python -m animator.segmentation.worker <task.json>

每種模型在自己的子行程裡載入、推論，結束後記憶體完全釋放（S2-0 實測 PyTorch 在同一個 process 裡
會殘留 0.25~0.6 GB）。主行程和子行程只透過 work 目錄裡的檔案交換資料：
    vision  ：Grounding DINO → SAM 2.1（同一個子行程，省一次 torch import）→ detections.json、masks.npy
    refine  ：PyMatting → alpha.npy、fg.npy、core.npy
    inpaint ：MI-GAN / OpenCV（每個補洞區域分開補）→ background.png、inpaint.json
"""

from __future__ import annotations

import gc
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _log(msg: str) -> None:
    print(msg, flush=True)


def run_vision(task: dict) -> dict:
    from PIL import Image

    from animator.segmentation import base
    from animator.segmentation.compose import filter_detections

    work = Path(task["work_dir"])
    image = np.asarray(Image.open(task["image"]).convert("RGB"))
    timing = {}

    t0 = time.perf_counter()
    det = base.create("detector", task["detector"]["backend"], model_dir=Path(task["detector"]["model_dir"]),
                      box_threshold=task["detector"]["box_threshold"],
                      text_threshold=task["detector"]["text_threshold"], threads=task["threads"])
    timing["detector_load"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    raw = det.detect(image, task["labels"])
    timing["detect"] = time.perf_counter() - t0
    det.close()
    del det
    gc.collect()
    kept = filter_detections(raw, task["labels"], task["detector"]["max_per_label"])
    _log(f"detect：{len(raw)} 個框，保留 {len(kept)} 個")

    masks = np.zeros((len(kept), *image.shape[:2]), np.uint8)
    scores: list[float] = []
    if kept:
        t0 = time.perf_counter()
        seg = base.create("segmenter", task["segmenter"]["backend"],
                          model_dir=Path(task["segmenter"]["model_dir"]), threads=task["threads"])
        timing["segmenter_load"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        for i, m in enumerate(seg.segment(image, [d["box"] for d in kept])):
            masks[i] = m
        timing["segment"] = time.perf_counter() - t0
        scores = list(getattr(seg, "last_scores", []))
        seg.close()
    np.save(work / "masks.npy", masks)
    _write(work / "detections.json", {"raw": raw, "kept": kept, "mask_scores": scores})
    return {k: round(v, 3) for k, v in timing.items()}


def run_refine(task: dict) -> dict:
    from PIL import Image

    from animator.segmentation import base

    work = Path(task["work_dir"])
    image = np.asarray(Image.open(task["image"]).convert("RGB"))
    masks = np.load(work / "visible.npy") > 0
    t0 = time.perf_counter()
    ref = base.create("refiner", task["backend"], band_px=task["band_px"])
    timing = {"load": time.perf_counter() - t0, "per_object": []}
    alphas = np.zeros(masks.shape, np.uint8)
    fgs = np.zeros((*masks.shape, 3), np.uint8)
    cores = np.zeros(masks.shape, bool)
    for i, m in enumerate(masks):
        t0 = time.perf_counter()
        alphas[i], fgs[i], cores[i] = ref.refine(image, m)
        timing["per_object"].append(round(time.perf_counter() - t0, 3))
    np.save(work / "alpha.npy", alphas)
    np.save(work / "fg.npy", fgs)
    np.save(work / "core.npy", cores)
    timing["load"] = round(timing["load"], 3)
    return timing


def run_inpaint(task: dict) -> dict:
    from PIL import Image

    from animator.segmentation import base
    from animator.segmentation.compose import bbox, check_inpaint, composite_background, context_window

    work = Path(task["work_dir"])
    current = np.asarray(Image.open(task["image"]).convert("RGB")).copy()
    labels = np.load(work / "regions.npy")
    objects = np.load(work / "objects.npy") > 0
    t0 = time.perf_counter()
    kwargs = {"model_dir": Path(task["model_dir"]), "threads": task["threads"],
              "mem_arena": task["mem_arena"]} if task["backend"] == "migan" else {}
    inp = base.create("inpainter", task["backend"], **kwargs)
    results = {"load_sec": round(time.perf_counter() - t0, 3), "regions": []}
    # 還沒補好的洞：這次要處理的所有區域（前一次已經補好的區域，現在就是背景）
    unfilled = np.isin(labels, [i + 1 for i in task["region_indices"]])
    for index in task["region_indices"]:
        hole = labels == index + 1
        entry = {"index": index}
        t0 = time.perf_counter()
        try:
            # 只在這個區域的上下文視窗裡補；視窗內「所有還沒補好的洞」都設為要補，
            # 其他物件的原像素才不會被當成背景（MI-GAN 會依遮罩 bbox 裁切後縮到 512）。
            x0, y0, x1, y1 = context_window(bbox(hole), hole.shape)
            win = (slice(y0, y1), slice(x0, x1))
            mask = unfilled[win]
            out = current.copy()
            out[win] = inp.inpaint(current[win], mask)  # 輸入是原圖（含 hole 內的原內容），不塗色
            entry["sec"] = round(time.perf_counter() - t0, 3)
            entry["window"] = [x0, y0, x1, y1]
            full_mask = np.zeros_like(unfilled)
            full_mask[win] = mask
            entry["checks"] = check_inpaint(current, out, full_mask, target=objects & hole)
            if entry["checks"]["passed"]:
                current = composite_background(current, out, hole)    # 只貼回這個區域
                unfilled &= ~hole
        except Exception as exc:  # noqa: BLE001 — 單一區域失敗時，交給主行程決定是否改用備援
            entry["error"] = f"{type(exc).__name__}: {exc}"
            _log(traceback.format_exc())
        results["regions"].append(entry)
        _log(f"inpaint[{task['backend']}] 區域 {index}：{entry.get('checks', entry.get('error'))}")
    Image.fromarray(current).save(work / f"background_{task['backend']}.png")
    return results


TASKS = {"vision": run_vision, "refine": run_refine, "inpaint": run_inpaint}


def main(argv: list[str]) -> int:
    task_path = Path(argv[0])
    task = json.loads(task_path.read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    result = TASKS[task["kind"]](task)
    _write(task_path.with_name(task_path.stem + ".result.json"),
           {"kind": task["kind"], "total_sec": round(time.perf_counter() - t0, 3), "result": result})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
