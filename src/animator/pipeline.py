"""render 流程：validate → 建立/沿用 job → resolve → Blender → FFmpeg → 驗證 → 紀錄。"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from animator import jobs
from animator.config import Config
from animator.errors import RenderError, SpecError
from animator.render import ffmpeg as ff
from animator.render.blender_runner import run_blender
from animator.spec import dump_json, load_spec
from animator.spec.png import read_png_info
from animator.spec.resolve import resolve
from animator.spec.validate import validate
from animator.tools import find_blender, find_ffmpeg, find_ffprobe

log = logging.getLogger("animator")


@dataclass
class RenderResult:
    job: jobs.Job
    render_id: str
    output: Path
    history_dir: Path
    report: dict
    frames_dir: Path | None  # keep_frames 時保留的影格目錄


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def check_spec(spec_path: Path) -> tuple[dict, list]:
    raw = load_spec(spec_path)
    result = validate(raw, spec_path.resolve().parent)
    if not result.ok:
        raise SpecError(result.errors)
    return raw, result.warnings


def render(spec_path: Path, cfg: Config, *, preview: bool = False, frame: int | None = None,
           keep_frames: bool | None = None, name: str | None = None,
           argv: list[str] | None = None) -> RenderResult:
    spec_path = Path(spec_path).resolve()
    raw, warnings = check_spec(spec_path)
    for w in warnings:
        log.warning("spec 警告：%s", w)

    blender = find_blender(cfg)
    ffmpeg = find_ffmpeg(cfg) if frame is None else None
    ffprobe = find_ffprobe(cfg, ffmpeg.path if ffmpeg else None)

    job = jobs.existing_job_for(spec_path, cfg)
    if job is None:
        job = jobs.create_job_from_spec(spec_path, raw, cfg, name)
        log.info("建立新工作：%s", job.dir)
        raw = load_spec(job.spec_path)
    else:
        log.info("在原地重新渲染：%s", job.dir)

    render_id, hist = jobs.start_render(job)
    keep = cfg.get("jobs.keep_frames") if keep_frames is None else keep_frames
    tmp_dir = job.temp_dir / render_id
    frames_dir = tmp_dir / "frames"
    frames_dir.mkdir(parents=True)

    kind = "still" if frame is not None else ("preview" if preview else "final")
    report: dict = {
        "render_id": render_id,
        "job_id": job.id,
        "kind": kind,
        "status": "running",
        "started_at": _now(),
        "command": argv if argv is not None else sys.argv,
        "spec_sha256": sha256(job.spec_path),
        "tools": {"blender": {"path": str(blender.path), "version": blender.version}},
    }
    if ffmpeg:
        report["tools"]["ffmpeg"] = {"path": str(ffmpeg.path), "version": ffmpeg.version}
    t0 = time.perf_counter()

    def save_report() -> None:
        dump_json(report, hist / "render_report.json")

    try:
        shutil.copy2(job.spec_path, hist / "spec.json")
        (hist / "config.effective.yaml").write_text(
            yaml.safe_dump(cfg.effective(), allow_unicode=True, sort_keys=False), encoding="utf-8")

        resolved = resolve(raw, job.spec_path, cfg, preview=preview)
        frame_end = resolved["canvas"]["frame_end"]
        if frame is not None and not 1 <= frame <= frame_end:
            raise RenderError(f"--frame 必須介於 1 和 {frame_end} 之間")
        resolved_path = hist / "spec.resolved.json"
        dump_json(resolved, resolved_path)
        report["layers"] = {l["id"]: {"image": l["image"], "sha256": sha256(Path(l["image"]))}
                            for l in resolved["layers"]}
        save_report()

        still_tmp = tmp_dir / f"still_{frame}.png" if frame is not None else None
        log.info("Blender 渲染中（%s，%d 格）…", kind, 1 if frame else frame_end)
        bl = run_blender(
            blender.path, cfg.get("tools.blender.extra_args"), resolved_path, frames_dir,
            hist / "blender_report.json", hist / "blender.log",
            cfg.get("tools.blender.timeout_sec"), still_frame=frame, still_path=still_tmp)
        report["blender"] = bl

        if frame is not None:
            out_name = f"still_{frame}.png"
            produced = still_tmp
        else:
            out_name = resolved["output"]["filename"]
            produced = tmp_dir / out_name
            o = resolved["output"]
            log.info("FFmpeg 合成中…")
            ff.encode(ffmpeg.path, frames_dir / bl["frame_pattern"], resolved["canvas"]["fps"], produced,
                      o["codec"], o["crf"], o["preset"], o["pix_fmt"], hist / "ffmpeg.log",
                      cfg.get("tools.ffmpeg.timeout_sec"))
            report["verification"] = _verify(ffmpeg.path, ffprobe, produced, frames_dir, resolved)

        moved = jobs.place_output(job, produced, out_name, render_id, cfg.get("jobs.overwrite"))
        report["outputs"] = [out_name]
        if moved:
            report["previous_output_moved_to"] = str(moved.relative_to(job.dir))

        ver = report.get("verification")
        report["status"] = "ok" if not ver or ver["ok"] else "verify_failed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        report["finished_at"] = _now()
        report["elapsed_sec"] = round(time.perf_counter() - t0, 2)
        save_report()
        jobs.record_render(job, {
            "render_id": render_id, "kind": kind, "status": report["status"],
            "started_at": report["started_at"], "outputs": report.get("outputs", []),
        })
        if report["status"] == "ok" and not keep:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if report["status"] != "ok":
        raise RenderError(f"輸出驗證失敗：{report['verification']['problems']}（紀錄：{hist}）")
    return RenderResult(job=job, render_id=render_id, output=job.dir / out_name, history_dir=hist,
                        report=report, frames_dir=frames_dir if keep else None)


def _verify(ffmpeg: Path, ffprobe: Path | None, video: Path, frames_dir: Path, resolved: dict) -> dict:
    canvas = resolved["canvas"]
    first = sorted(frames_dir.glob("*.png"))[0]
    info = read_png_info(first)
    expected = {
        "width": info.width - info.width % 2,
        "height": info.height - info.height % 2,
        "fps": float(canvas["fps"]),
        "frames": canvas["frame_end"],
        "duration": canvas["frame_end"] / canvas["fps"],
    }
    problems: list[str] = []
    decode_errors = ff.decode_check(ffmpeg, video)
    if decode_errors:
        problems.append("解碼錯誤：" + "; ".join(decode_errors[:5]))

    actual = None
    if ffprobe is None:
        log.warning("找不到 ffprobe，略過影片規格驗證")
    else:
        actual = ff.probe(ffprobe, video)
        for key in ("width", "height", "frames"):
            if actual[key] != expected[key]:
                problems.append(f"{key}：預期 {expected[key]}，實際 {actual[key]}")
        if abs(actual["fps"] - expected["fps"]) > 1e-3:
            problems.append(f"fps：預期 {expected['fps']}，實際 {actual['fps']}")
        if abs(actual["duration"] - expected["duration"]) > 1.0 / canvas["fps"] + 1e-3:
            problems.append(f"duration：預期 {expected['duration']:.3f}，實際 {actual['duration']:.3f}")
    return {"ok": not problems, "expected": expected, "actual": actual,
            "decodable": not decode_errors, "problems": problems}
