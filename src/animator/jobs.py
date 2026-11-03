"""工作目錄（output/<job_id>/）、渲染紀錄（history/<render_id>/）與輸出檔備份。"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from animator.config import Config
from animator.errors import AnimatorError
from animator.spec import dump_json
from animator.spec.png import read_png_info

SPEC_NAME = "spec.json"
INDEX_NAME = "renders.json"


@dataclass
class Job:
    id: str
    dir: Path
    temp_dir: Path

    @property
    def spec_path(self) -> Path:
        return self.dir / SPEC_NAME

    @property
    def history_dir(self) -> Path:
        return self.dir / "history"


def sanitize_name(name: str | None) -> str:
    text = re.sub(r"[^a-z0-9_-]+", "-", (name or "").lower()).strip("-_")
    return text or "job"


def make_job_id(cfg: Config, name: str | None, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime(cfg.get("jobs.id_format")).replace("{name}", sanitize_name(name))


def _job(cfg: Config, job_id: str) -> Job:
    return Job(id=job_id, dir=cfg.path("output_dir") / job_id, temp_dir=cfg.path("temp_dir") / job_id)


def _new_job_dir(cfg: Config, name: str | None) -> Job:
    base = make_job_id(cfg, name)
    job_id, n = base, 2
    while (cfg.path("output_dir") / job_id).exists():
        job_id, n = f"{base}-{n}", n + 1
    job = _job(cfg, job_id)
    job.dir.mkdir(parents=True)
    return job


def existing_job_for(spec_path: Path, cfg: Config) -> Job | None:
    """spec 位於 output/<job_id>/spec.json 時回傳該 job（在原地重新渲染）。"""
    spec_path = spec_path.resolve()
    if spec_path.name == SPEC_NAME and spec_path.parent.parent == cfg.path("output_dir"):
        return _job(cfg, spec_path.parent.name)
    return None


def _unique_name(name: str, used: set[str]) -> str:
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 2
    while candidate.lower() in used:
        candidate, n = f"{stem}-{n}{suffix}", n + 1
    used.add(candidate.lower())
    return candidate


def create_job_from_spec(spec_path: Path, raw: dict, cfg: Config, name: str | None = None) -> Job:
    """把外部 spec 與它引用的圖層複製到新的 job，圖層路徑改寫為 layers/<檔名>。"""
    spec_path = spec_path.resolve()
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    job = _new_job_dir(cfg, name or meta.get("name") or spec_path.parent.name)
    layers_dir = job.dir / "layers"
    layers_dir.mkdir()

    copied: dict[Path, str] = {}
    used: set[str] = set()
    new_raw = json.loads(json.dumps(raw))
    for layer in new_raw.get("layers", []):
        src = Path(layer["image"])
        src = (src if src.is_absolute() else spec_path.parent / src).resolve()
        if src not in copied:
            target = _unique_name(src.name, used)
            shutil.copy2(src, layers_dir / target)
            copied[src] = f"layers/{target}"
        layer["image"] = copied[src]
    new_raw.setdefault("meta", {}).setdefault("copied_from", str(spec_path))
    dump_json(new_raw, job.spec_path)
    return job


def create_job_from_layers(layers_dir: Path, cfg: Config, name: str | None,
                           duration: float = 5.0, fps: int | None = None) -> Job:
    """`animator new`：用一個 PNG 資料夾建立起始 spec（圖層置中、檔名排序決定前後、沒有動畫）。"""
    layers_dir = layers_dir.resolve()
    pngs = sorted(p for p in layers_dir.iterdir() if p.suffix.lower() == ".png")
    if not pngs:
        raise AnimatorError(f"資料夾裡沒有 PNG 圖層：{layers_dir}")

    job = _new_job_dir(cfg, name or layers_dir.name)
    target_dir = job.dir / "layers"
    target_dir.mkdir()

    layers, used, max_w, max_h = [], set(), 0, 0
    for index, png in enumerate(pngs):
        info = read_png_info(png)
        max_w, max_h = max(max_w, info.width), max(max_h, info.height)
        target = _unique_name(png.name, used)
        shutil.copy2(png, target_dir / target)
        layer_id = sanitize_name(png.stem)
        while any(l["id"] == layer_id for l in layers):
            layer_id += "-x"
        layers.append({"id": layer_id, "image": f"layers/{target}", "z": index})

    width, height = max(16, max_w + max_w % 2), max(16, max_h + max_h % 2)
    for layer in layers:
        layer["position"] = [width / 2, height / 2]
    spec = {
        "version": 1,
        "meta": {"name": sanitize_name(name or layers_dir.name), "created_by": "manual",
                 "created_at": datetime.now().isoformat(timespec="seconds")},
        "canvas": {"width": width, "height": height,
                   "fps": fps or cfg.get("defaults.canvas.fps"), "duration": duration},
        "layers": layers,
        "animations": [],
    }
    dump_json(spec, job.spec_path)
    return job


# ---------------------------------------------------------------- history

def new_render_id(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y%m%d-%H%M%S-") + f"{now.microsecond // 1000:03d}"


def start_render(job: Job) -> tuple[str, Path]:
    render_id = new_render_id()
    while (job.history_dir / render_id).exists():
        render_id = new_render_id()
    path = job.history_dir / render_id
    path.mkdir(parents=True)
    return render_id, path


def read_index(job: Job) -> dict:
    path = job.dir / INDEX_NAME
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"job_id": job.id, "renders": [], "current": {}}


def write_index(job: Job, index: dict) -> None:
    dump_json(index, job.dir / INDEX_NAME)


def place_output(job: Job, produced: Path, filename: str, render_id: str, overwrite: bool) -> Path | None:
    """把新的輸出檔放到 job 根目錄。舊檔會移到它所屬那次渲染的 history 目錄（或在 overwrite 時刪除）。

    回傳舊檔被移到的位置（刪除或沒有舊檔時回傳 None）。
    """
    index = read_index(job)
    target = job.dir / filename
    moved_to = None
    if target.exists():
        if overwrite:
            target.unlink()
        else:
            owner = index["current"].get(filename) or f"unknown-{new_render_id()}"
            backup_dir = job.history_dir / owner
            backup_dir.mkdir(parents=True, exist_ok=True)
            moved_to = backup_dir / filename
            if moved_to.exists():
                moved_to = backup_dir / f"{Path(filename).stem}.{new_render_id()}{Path(filename).suffix}"
            shutil.move(str(target), moved_to)
    shutil.move(str(produced), target)
    index["current"][filename] = render_id
    write_index(job, index)
    return moved_to


def record_render(job: Job, entry: dict) -> None:
    index = read_index(job)
    index["renders"].append(entry)
    write_index(job, index)
