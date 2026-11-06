"""`animator segment`：一張圖片 → output/<job_id>/（spec.json v1 + layers/*.png + segmentation/）。

流程（docs/stage2.md 第 4 節）：
 1. 讀圖（EXIF 方向、奇數尺寸補 1 px）
 2. 偵測 + 分割（子行程：Grounding DINO → SAM 2.1，在 ai.max_side 的處理圖上）；或用 --masks 提供的遮罩
 3. 遮罩後處理、z 排序（面積小的在前面）、重疊像素歸給前面的圖層
 4. 邊緣精修（子行程：PyMatting）→ RGBA 圖層（RGB 直接取原圖，只有邊緣帶換成去色邊後的顏色）
 5. 背景修補（子行程：MI-GAN；失敗時用 OpenCV 備援）→ 自動檢查 → 標記需要人工檢查的區域
 6. 寫出 spec.json（沒有動畫），並用 Stage 1 的 validate 檢查
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from animator.config import Config
from animator.errors import AnimatorError
from animator.jobs import Job, sanitize_name
from animator.paths import project_root
from animator.segmentation import compose as C
from animator.segmentation import models as M
from animator.spec import dump_json

REVIEW_OK = "passed_checks"
BG_INCOMPLETE = "incomplete"     # 至少一個區域沒有修補（物件仍在背景上），優先於 needs_review


@dataclass
class SegmentOptions:
    labels: list[str] | None = None
    masks_dir: Path | None = None
    name: str | None = None
    inpaint: str | None = None        # 覆寫 ai.inpaint.backend
    refine: str | None = None         # 覆寫 ai.refine.backend
    duration: float = 5.0
    fps: int | None = None
    keep_temp: bool = False           # 成功時也保留 temp_dir/<job_id>/（失敗時一律保留）


@dataclass
class SegmentResult:
    job: Job
    spec_path: Path
    layers: list[dict]
    regions: list[dict]
    review: list[str]
    timings: dict = field(default_factory=dict)
    no_objects: bool = False
    background_status: str = ""

    @property
    def incomplete(self) -> bool:
        return self.background_status == BG_INCOMPLETE

    @property
    def review_required(self) -> bool:
        return bool(self.review)


# ---------------------------------------------------------------- 子行程

def _python(cfg: Config) -> Path:
    if cfg.get("tools.python.executable"):
        py = cfg.executable("python")
        if py is None:
            raise AnimatorError(f"找不到 tools.python.executable：{cfg.get('tools.python.executable')}")
        return py
    return Path(sys.executable)


def run_worker(cfg: Config, kind: str, task: dict, work: Path, logs: Path, tag: str) -> dict:
    """在獨立的子行程裡執行一個 AI 步驟；失敗時把 log 的最後幾行放進錯誤訊息。"""
    task = {"kind": kind, "work_dir": str(work), **task}
    task_path = work / f"{tag}.task.json"
    task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    env = os.environ.copy()
    env.update(M.hf_env(cfg, offline=True))          # 推論一律離線
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(project_root() / "src"), env.get("PYTHONPATH")]))
    env["PYTHONIOENCODING"] = "utf-8"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"segment_{tag}.log"
    cmd = [str(_python(cfg)), "-m", "animator.segmentation.worker", str(task_path)]
    try:
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env, cwd=project_root(),
                                  timeout=cfg.get("ai.worker_timeout_sec"))
    except subprocess.TimeoutExpired as exc:
        raise AnimatorError(f"AI 子行程 {tag} 逾時（{exc.timeout} 秒），log：{log}") from exc
    if proc.returncode != 0:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
        raise AnimatorError(f"AI 子行程 {tag} 失敗（exit {proc.returncode}），log：{log}\n{tail}")
    return json.loads(task_path.with_name(task_path.stem + ".result.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 物件來源

def _label_from_filename(stem: str) -> str:
    parts = stem.rsplit("-", 1)
    base = parts[0] if len(parts) == 2 and parts[1].isdigit() else stem
    return base.replace("_", " ").strip() or "object"


SKIP_MASK_FILES = {"background_hole.png"}   # segment 自己輸出的補洞遮罩，不是物件


def mask_files(masks_dir: Path) -> list[Path]:
    if not masks_dir.is_dir():
        raise AnimatorError(f"找不到遮罩資料夾：{masks_dir}")
    pngs = sorted(p for p in masks_dir.iterdir()
                  if p.suffix.lower() == ".png" and p.name.lower() not in SKIP_MASK_FILES)
    if not pngs:
        raise AnimatorError(f"遮罩資料夾裡沒有物件遮罩（PNG）：{masks_dir}")
    return pngs


def objects_from_masks(masks_dir: Path, src: C.SourceImage, min_area_px: int) -> tuple[list[dict], list[str]]:
    """--masks：使用者提供的遮罩（檔名 = 標籤，例如 person-1.png、dog.png；白色或不透明 = 物件）。

    遮罩必須和輸入圖片一樣大（不縮放）。回傳 (物件, 被捨棄的遮罩說明)。
    """
    out, dropped = [], []
    for p in mask_files(masks_dir):
        m = C.clean_mask(C.load_mask(p, src.original_size, src.pad), min_area_px)
        if m.any():
            out.append({"label": _label_from_filename(p.stem), "score": None, "mask": m,
                        "box": None, "source": "manual", "file": p.name})
        else:
            dropped.append(f"遮罩 {p.name}：清除小於 {min_area_px} px 的碎塊後是空的，已略過")
    return out, dropped


def objects_from_ai(cfg: Config, src: C.SourceImage, labels: list[str], work: Path, logs: Path,
                    timings: dict, used_models: dict) -> tuple[list[dict], dict]:
    det_model = cfg.get("ai.detection.model")
    seg_model = cfg.get("ai.segmentation.model")
    det_dir, seg_dir = M.ensure_ready(cfg, det_model), M.ensure_ready(cfg, seg_model)
    used_models.update({det_model: M.get_spec(det_model), seg_model: M.get_spec(seg_model)})
    h, w = src.rgb.shape[:2]
    scale = C.processing_scale((w, h), cfg.get("ai.max_side"))
    proc = C.resize_rgb(src.rgb, scale)
    C.save_png(proc, work / "proc.png")
    t0 = time.perf_counter()
    res = run_worker(cfg, "vision", {
        "image": str(work / "proc.png"), "labels": labels, "threads": cfg.get("ai.threads"),
        "detector": {"backend": cfg.get("ai.detection.backend"), "model_dir": str(det_dir),
                     "box_threshold": cfg.get("ai.detection.box_threshold"),
                     "text_threshold": cfg.get("ai.detection.text_threshold"),
                     "max_per_label": cfg.get("ai.detection.max_per_label")},
        "segmenter": {"backend": cfg.get("ai.segmentation.backend"), "model_dir": str(seg_dir)},
    }, work, logs, "vision")
    timings["vision_total"] = round(time.perf_counter() - t0, 2)
    timings["vision"] = res["result"]
    dets = json.loads((work / "detections.json").read_text(encoding="utf-8"))
    masks = np.load(work / "masks.npy")
    min_area = cfg.get("ai.segmentation.min_area_px")
    out = []
    dropped = []
    for i, d in enumerate(dets["kept"]):
        m = C.clean_mask(C.upscale_mask(masks[i] > 0, (w, h)), min_area)
        if not m.any():
            dropped.append(f"偵測到 {d['label']}（{d['score']:.2f}），但分割遮罩清除碎塊後是空的，已略過")
            continue
        box = [round(v / scale, 1) for v in d["box"]]
        score = dets["mask_scores"][i] if i < len(dets["mask_scores"]) else None
        out.append({"label": d["label"], "label_raw": d["label_raw"], "score": d["score"],
                    "mask_iou_pred": score, "mask": m, "box": box, "source": "ai"})
    info = {"processing_size": [proc.shape[1], proc.shape[0]], "scale": round(scale, 5),
            "detections_raw": [{**d, "box_full": [round(v / scale, 1) for v in d["box"]]} for d in dets["raw"]],
            "detections_kept": len(dets["kept"]), "dropped": dropped}
    return out, info


# ---------------------------------------------------------------- 背景

def repair_background(cfg: Config, src: C.SourceImage, regions: list[C.HoleRegion], objects: np.ndarray,
                      backend: str, work: Path, logs: Path, timings: dict, used_models: dict) -> np.ndarray:
    """依序修補每個區域，並設定每個區域的 status / reasons / checks。回傳修補後的背景。

    - 主要 backend 出錯、或自動檢查沒有通過的區域 → 交給備援（ai.inpaint.fallback）
    - 備援的結果一律是 needs_review；備援也失敗 → failed / failed_checks（該區域保留原圖）
    """
    if not regions:
        return src.rgb.copy()
    if backend == "none":
        for r in regions:
            r.status, r.backend, r.repaired = "not_inpainted", "none", False
            r.attempts.append({"backend": "none", "result": "skipped"})
            r.reasons.append("inpaint.backend = none：物件仍留在背景上")
        return src.rgb.copy()

    labels = np.zeros(src.rgb.shape[:2], np.int32)
    for r in regions:
        labels[r.mask] = r.index + 1
    np.save(work / "regions.npy", labels)
    np.save(work / "objects.npy", objects.astype(np.uint8))
    C.save_png(src.rgb, work / "source.png")

    def run(name: str, image: Path, indices: list[int]) -> dict:
        task = {"image": str(image), "backend": name, "region_indices": indices,
                "threads": cfg.get("ai.threads"), "mem_arena": cfg.get("ai.inpaint.mem_arena"),
                "model_dir": ""}
        if name == "migan":
            model = cfg.get("ai.inpaint.model")
            task["model_dir"] = str(M.ensure_ready(cfg, model))
            used_models[model] = M.get_spec(model)
        t0 = time.perf_counter()
        res = run_worker(cfg, "inpaint", task, work, logs, f"inpaint_{name}")
        timings[f"inpaint_{name}_total"] = round(time.perf_counter() - t0, 2)
        return res["result"]

    by_index = {r.index: r for r in regions}
    current = work / "source.png"
    retry: list[int] = []
    try:
        res = run(backend, current, [r.index for r in regions])
        current = work / f"background_{backend}.png"
        for entry in res["regions"]:
            r = by_index[entry["index"]]
            r.backend, r.checks = backend, entry.get("checks", {})
            if "error" in entry:
                r.reasons.append(f"{backend} 失敗：{entry['error']}")
                r.status = "failed"
                r.attempts.append({"backend": backend, "result": "error"})
                retry.append(r.index)
            elif not r.checks.get("passed"):
                r.reasons.append(f"{backend} 的結果沒有通過自動檢查")
                r.status = "failed_checks"
                r.attempts.append({"backend": backend, "result": "failed_checks"})
                retry.append(r.index)
            else:
                r.status = REVIEW_OK if not r.reasons else "needs_review"
                r.repaired = True
                r.attempts.append({"backend": backend, "result": "passed_checks"})
    except AnimatorError as exc:
        retry = [r.index for r in regions]
        for r in regions:
            r.status, r.backend = "failed", backend
            r.attempts.append({"backend": backend, "result": "error"})
            r.reasons.append(f"{backend} 無法執行：{str(exc).splitlines()[0]}")

    fallback = cfg.get("ai.inpaint.fallback")
    if retry and fallback != "none" and fallback != backend:
        try:
            res = run(fallback, current, retry)
            current = work / f"background_{fallback}.png"
            for entry in res["regions"]:
                r = by_index[entry["index"]]
                r.backend = fallback
                if "error" in entry:
                    r.status = "failed"
                    r.attempts.append({"backend": fallback, "result": "error"})
                    r.reasons.append(f"備援 {fallback} 也失敗：{entry['error']}")
                    continue
                r.checks = entry.get("checks", {})
                if r.checks.get("passed"):
                    r.status, r.repaired = "needs_review", True
                    r.attempts.append({"backend": fallback, "result": "passed_checks"})
                    r.reasons.append(f"使用備援 {fallback}（只適合小洞）")
                else:
                    r.status = "failed_checks"
                    r.attempts.append({"backend": fallback, "result": "failed_checks"})
                    r.reasons.append(f"備援 {fallback} 的結果也沒有通過自動檢查")
        except AnimatorError as exc:
            for i in retry:
                by_index[i].backend = fallback
                by_index[i].status = "failed"
                by_index[i].attempts.append({"backend": fallback, "result": "error"})
                by_index[i].reasons.append(f"備援 {fallback} 無法執行：{str(exc).splitlines()[0]}")
    elif retry:
        for i in retry:
            by_index[i].reasons.append(f"沒有可用的備援（ai.inpaint.fallback = {fallback}）")

    from PIL import Image
    return np.asarray(Image.open(current).convert("RGB")).copy()


# ---------------------------------------------------------------- 主流程

INPAINT_BACKENDS = ("migan", "opencv", "none")


def preflight(cfg: Config, opts: SegmentOptions, image_path: Path) -> dict:
    """在建立任何檔案之前檢查設定、輸入與模型；有問題就直接報錯（不留下半成品）。"""
    from animator.segmentation.base import backend_names

    errors = []
    if cfg.get("ai.device") != "cpu":
        errors.append(f"ai.device = {cfg.get('ai.device')!r}：目前只支援 cpu（沒有安裝 CUDA 版 PyTorch）")
    inpaint = opts.inpaint or cfg.get("ai.inpaint.backend")
    refine = opts.refine or cfg.get("ai.refine.backend")
    fallback = cfg.get("ai.inpaint.fallback")
    checks = [("ai.inpaint.backend", inpaint, INPAINT_BACKENDS),
              ("ai.inpaint.fallback", fallback, INPAINT_BACKENDS),
              ("ai.refine.backend", refine, backend_names("refiner"))]
    if not opts.masks_dir:
        checks += [("ai.detection.backend", cfg.get("ai.detection.backend"), backend_names("detector")),
                   ("ai.segmentation.backend", cfg.get("ai.segmentation.backend"), backend_names("segmenter"))]
    for key, value, allowed in checks:
        if value not in allowed:
            errors.append(f"{key} = {value!r} 不支援（可用：{', '.join(allowed)}）")
    numeric = [("ai.threads", 1, 64), ("ai.max_side", 64, 8192), ("ai.worker_timeout_sec", 1, 86400),
               ("ai.refine.band_px", 0, 64), ("ai.inpaint.dilate_px", 0, 256),
               ("ai.segmentation.min_area_px", 0, 10 ** 8), ("ai.detection.max_per_label", 1, 100)]
    for key, lo, hi in numeric:
        if not lo <= cfg.get(key) <= hi:
            errors.append(f"{key} = {cfg.get(key)} 超出範圍 {lo}~{hi}")
    ratio = cfg.get("ai.inpaint.review_area_ratio")
    if not 0 < ratio <= 1:
        errors.append(f"ai.inpaint.review_area_ratio = {ratio} 必須介於 0~1")
    for key in ("ai.detection.box_threshold", "ai.detection.text_threshold"):
        if not 0 < cfg.get(key) < 1:
            errors.append(f"{key} = {cfg.get(key)} 必須介於 0~1")
    if not image_path.is_file():
        errors.append(f"找不到圖片：{image_path}")
    labels = opts.labels or list(cfg.get("ai.detection.default_labels"))
    if not opts.masks_dir and not labels:
        errors.append("沒有標籤：請用 --labels 指定，或設定 ai.detection.default_labels")
    if errors:
        raise AnimatorError("segment 的設定 / 輸入有問題：\n  - " + "\n  - ".join(errors))

    if opts.masks_dir:
        mask_files(Path(opts.masks_dir))            # 資料夾存在、而且有遮罩
    else:                                           # 偵測 / 分割模型一定要有；MI-GAN 缺少時會改用備援
        M.ensure_ready(cfg, cfg.get("ai.detection.model"))
        M.ensure_ready(cfg, cfg.get("ai.segmentation.model"))
    return {"inpaint": inpaint, "refine": refine, "labels": labels}


def _reserve_job(cfg: Config, name: str) -> tuple[str, Path]:
    """決定 job_id，並在 temp_dir 建立暫存資料夾（output/ 底下要到成功時才會出現）。"""
    from animator.jobs import make_job_id

    base = make_job_id(cfg, name)
    job_id, n = base, 2
    while (cfg.path("output_dir") / job_id).exists() or (cfg.path("temp_dir") / job_id).exists():
        job_id, n = f"{base}-{n}", n + 1
    stage = cfg.path("temp_dir") / job_id
    stage.mkdir(parents=True)
    return job_id, stage


def segment(image_path: Path, cfg: Config, opts: SegmentOptions) -> SegmentResult:
    """建立一個分層 job。

    原子性：所有檔案先寫到 temp_dir/<job_id>/job/，全部成功、spec 通過驗證之後才搬到 output/<job_id>/。
    失敗時 output/ 底下不會留下任何東西；暫存檔（含 log 與 failure.json）保留在 temp_dir/<job_id>/。
    成功時刪除暫存檔（opts.keep_temp = True 時保留）。
    """
    image_path = Path(image_path).resolve()
    settings = preflight(cfg, opts, image_path)
    src = C.load_image(image_path)
    h, w = src.rgb.shape[:2]
    if not (16 <= w <= 8192 and 16 <= h <= 8192):
        raise AnimatorError(f"圖片尺寸 {w}x{h} 超出 Stage 1 支援的範圍（16~8192）")
    min_area = cfg.get("ai.segmentation.min_area_px")
    manual = None
    if opts.masks_dir:                              # 遮罩在建立 job 之前就先讀取、驗證尺寸
        manual = objects_from_masks(Path(opts.masks_dir), src, min_area)

    name = opts.name or image_path.stem
    job_id, stage = _reserve_job(cfg, name)
    job_tmp = stage / "job"
    try:
        result = _segment(cfg, opts, settings, image_path, src, manual, job_id, job_tmp, stage / "segment")
    except Exception as exc:
        import traceback

        job_tmp.mkdir(parents=True, exist_ok=True)
        (job_tmp / "failure.json").write_text(json.dumps(
            {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()},
            ensure_ascii=False, indent=2), encoding="utf-8")
        msg = str(exc) if isinstance(exc, AnimatorError) else f"{type(exc).__name__}: {exc}"
        raise AnimatorError(f"{msg}\n（segment 失敗，沒有建立 output/{job_id}；暫存檔與 log 保留在 {stage}）") from exc

    final = cfg.path("output_dir") / job_id
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(job_tmp), str(final))
    if not opts.keep_temp:
        shutil.rmtree(stage, ignore_errors=True)
    job = Job(id=job_id, dir=final, temp_dir=stage)
    return SegmentResult(job=job, spec_path=job.spec_path, layers=result["layers"], regions=result["regions"],
                         review=result["review"], timings=result["timings"], no_objects=result["no_objects"],
                         background_status=result["background_status"])


def _segment(cfg: Config, opts: SegmentOptions, settings: dict, image_path: Path, src: C.SourceImage,
             manual, job_id: str, job_dir: Path, work: Path) -> dict:
    inpaint_backend, refine_backend, labels = settings["inpaint"], settings["refine"], settings["labels"]
    review_ratio = float(cfg.get("ai.inpaint.review_area_ratio"))
    h, w = src.rgb.shape[:2]
    t_start = time.perf_counter()
    timings: dict = {}
    used_models: dict[str, M.ModelSpec] = {}
    seg_dir, layers_dir, logs = job_dir / "segmentation", job_dir / "layers", job_dir / "logs"
    for d in (seg_dir / "masks", layers_dir, logs, work):
        d.mkdir(parents=True, exist_ok=True)
    C.save_png(src.rgb, seg_dir / "source.png")

    # 2. 物件
    ai_info: dict = {}
    if manual is not None:
        raw_objects, dropped = manual
        mode = "manual_masks"
    else:
        raw_objects, ai_info = objects_from_ai(cfg, src, labels, work, logs, timings, used_models)
        dropped = ai_info.get("dropped", [])
        mode = "ai"

    # 3. 排序、重疊
    ids = C.unique_ids([o["label"] for o in raw_objects], sanitize_name)
    items = [C.ObjectItem(id=i, label=o["label"], score=o["score"], mask=o["mask"], source=o["source"],
                          det_box=o["box"]) for i, o in zip(ids, raw_objects)]
    extra = {i: o for i, o in zip(ids, raw_objects)}
    items = C.assign_layers(items, w * h, review_ratio) if items else []

    # 4. 邊緣精修 → 圖層
    band_px = 2 if refine_backend == "none" else cfg.get("ai.refine.band_px")
    layer_entries: list[dict] = []
    stack: list = []                  # (rgba, box)，依 z 由後往前，用來檢查疊回去是否等於原圖
    if items:
        visible = np.stack([o.visible for o in items]).astype(np.uint8)
        np.save(work / "visible.npy", visible)
        C.save_png(src.rgb, work / "source.png")
        if refine_backend == "none":
            alphas, fgs, cores = visible * 255, None, None
        else:
            t0 = time.perf_counter()
            res = run_worker(cfg, "refine", {"image": str(work / "source.png"), "backend": refine_backend,
                                             "band_px": cfg.get("ai.refine.band_px")}, work, logs, "refine")
            timings["refine_total"] = round(time.perf_counter() - t0, 2)
            timings["refine"] = res["result"]
            alphas, fgs, cores = np.load(work / "alpha.npy"), np.load(work / "fg.npy"), np.load(work / "core.npy")
        for i, o in enumerate(items):
            rgb, alpha = C.layer_pixels(src.rgb, o.visible, alphas[i],
                                        None if fgs is None else fgs[i], None if cores is None else cores[i],
                                        clip_px=band_px)
            C.save_png((o.mask * 255).astype(np.uint8), seg_dir / "masks" / f"{o.id}.png")
            crop = C.crop_layer(rgb, alpha)
            if crop is None:
                o.review.append("empty_layer：精修後沒有可見的像素，沒有輸出圖層")
                continue
            rgba, box, pos = crop
            C.save_png(rgba, layers_dir / f"{o.id}.png")
            stack.append((rgba, box))
            meta = {"source": o.source, "label": o.label, "z_rule": "smaller_area_in_front",
                    "mask": f"segmentation/masks/{o.id}.png", "crop_box": box,
                    "area_ratio": round(o.area / (w * h), 4), "review": o.review}
            if o.score is not None:
                meta["score"] = o.score
            if o.det_box is not None:
                meta["bbox"] = o.det_box
            if "file" in extra[o.id]:
                meta["mask_file"] = extra[o.id]["file"]
            layer_entries.append({"id": o.id, "image": f"layers/{o.id}.png", "z": o.z,
                                  "position": pos, "meta": meta})

    # 5. 背景
    hole, regions = C.hole_regions([o.mask for o in items], (h, w), cfg.get("ai.inpaint.dilate_px"),
                                   review_ratio)
    C.save_png((hole * 255).astype(np.uint8), seg_dir / "masks" / "background_hole.png")
    # 物件 ↔ 補洞區域 / 物件 ↔ 物件：只有這裡同時有原始遮罩和區域遮罩（S2-2 M4 的警告會用到）
    associations = C.associations(items, regions)
    union = np.zeros((h, w), bool)
    for o in items:
        union |= o.mask
    background = repair_background(cfg, src, regions, union, inpaint_backend, work, logs, timings, used_models)
    dilate_px = max(cfg.get("ai.inpaint.dilate_px"), C.MIN_DILATE_PX)
    if items:
        background = C.restore_ring(src.rgb, background, union, hole, dilate_px,
                                    keep_px=C.ring_keep_px(band_px, dilate_px))
    C.save_png(background, layers_dir / "background.png")
    C.save_png(C.overview(src.rgb, items, regions), seg_dir / "overview.png")
    # 不加動畫時，圖層疊回去應該和原圖幾乎相同（差異只會出現在物件邊緣的精修帶）
    recomp = C.fidelity(C.recomposite(background, stack), src.rgb)

    no_objects = not layer_entries
    review: list[str] = []
    if no_objects:
        review.append("no_objects：沒有產生任何物件圖層（沒有偵測到物件，或遮罩都被清除）；spec 只有原圖背景")
    review += dropped
    for o in items:
        review += [f"圖層 {o.id}：{r}" for r in o.review]
    for r in regions:
        if not r.repaired:
            review.append(f"unrepaired：背景區域 {r.index}（{r.area_ratio:.1%}，bbox {r.box}）沒有修補，"
                          f"原本的物件仍留在背景上（{r.status}）：" + "；".join(r.reasons))
        elif r.status != REVIEW_OK:
            review.append(f"背景區域 {r.index}（{r.status}，已修補）：" + "；".join(r.reasons or ["需要人工檢查"]))
    if not regions:
        bg_status = "unchanged"
    elif any(not r.repaired for r in regions):
        bg_status = BG_INCOMPLETE                 # 優先於 needs_review
    elif all(r.status == REVIEW_OK for r in regions):
        bg_status = REVIEW_OK
    else:
        bg_status = "needs_review"

    # 6. segmentation.json → spec.json（spec 最後寫，而且整個 job 成功後才會搬到 output/）
    created_at = datetime.now().isoformat(timespec="seconds")
    spec = {
        "version": 1,
        "meta": {"name": sanitize_name(opts.name or image_path.stem), "created_by": "animator segment",
                 "created_at": created_at, "source_image": "segmentation/source.png",
                 "source_file": image_path.name, "segmentation": "segmentation/segmentation.json",
                 "review_required": bool(review)},
        "canvas": {"width": w, "height": h, "fps": opts.fps or cfg.get("defaults.canvas.fps"),
                   "duration": opts.duration},
        "layers": [{"id": "background", "image": "layers/background.png", "z": 0, "position": [w / 2, h / 2],
                    "meta": {"source": "ai", "role": "background", "status": bg_status,
                             "hole_mask": "segmentation/masks/background_hole.png"}}] + layer_entries,
        "animations": [],
    }
    from animator.spec.validate import validate
    result = validate(spec, job_dir)
    if not result.ok:
        raise AnimatorError("產生的 spec 沒有通過 Stage 1 驗證：\n" + "\n".join(map(str, result.errors)))

    timings["total"] = round(time.perf_counter() - t_start, 2)
    seg_json = {
        "version": 1, "job_id": job_id, "mode": mode, "created_at": created_at,
        "source": {"file": image_path.name, "sha256": M.sha256_file(image_path),
                   "original_size": list(src.original_size), "padded_right_bottom": list(src.pad),
                   "canvas_size": [w, h]},
        "labels": labels if mode == "ai" else None, **ai_info,
        "objects": [{"id": o.id, "label": o.label, "score": o.score, "source": o.source, "z": o.z,
                     "bbox": o.det_box, "area_px": o.area, "visible_px": int(o.visible.sum()),
                     "mask": f"segmentation/masks/{o.id}.png", "review": o.review,
                     **({"label_raw": extra[o.id]["label_raw"], "mask_iou_pred": extra[o.id]["mask_iou_pred"]}
                        if o.source == "ai" else {})} for o in items],
        "background": {"backend": inpaint_backend, "fallback": cfg.get("ai.inpaint.fallback"),
                       "status": bg_status, "unrepaired_regions": [r.index for r in regions if not r.repaired],
                       "dilate_px": dilate_px, "ring_restore_keep_px": C.ring_keep_px(band_px, dilate_px),
                       "layer_alpha_clip_px": band_px,
                       "review_area_ratio": review_ratio, "regions": [r.to_json() for r in regions]},
        "associations": associations,
        "refine": {"backend": refine_backend, "band_px": cfg.get("ai.refine.band_px")},
        "models": {n: {"repo": s.repo, "revision": s.revision, "weights": s.weights, "sha256": s.sha256,
                       "license": s.license} for n, s in used_models.items()},
        "recomposite_vs_source": recomp,
        "no_objects": no_objects, "review_required": bool(review), "review": review, "timings_sec": timings,
        "note": "status = passed_checks 只代表通過自動檢查（尺寸、遮罩極性、洞外像素不變），不代表視覺品質已經人工確認",
    }
    dump_json(seg_json, seg_dir / "segmentation.json")
    dump_json(spec, job_dir / "spec.json")
    return {"layers": spec["layers"], "regions": [r.to_json() for r in regions], "review": review,
            "timings": timings, "no_objects": no_objects, "background_status": bg_status}
