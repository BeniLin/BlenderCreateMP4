"""用 subprocess 啟動 Blender（背景模式）執行 blender/build_scene.py。

系統端與 Blender 端只透過檔案溝通：
  輸入：spec.resolved.json
  輸出：PNG 影格（或單張 still）+ blender_report.json
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from animator.errors import RenderError
from animator.paths import project_root


def build_scene_script() -> Path:
    return project_root() / "blender" / "build_scene.py"


def run_blender(blender: Path, extra_args: list[str], resolved: Path, frames_dir: Path,
                report: Path, log: Path, timeout: int, still_frame: int | None = None,
                still_path: Path | None = None) -> dict:
    cmd = [
        str(blender), *extra_args, "-b",
        "--python-exit-code", "1",
        "-P", str(build_scene_script()),
        "--",
        "--resolved", str(resolved),
        "--frames-dir", str(frames_dir),
        "--report", str(report),
    ]
    if still_frame is not None:
        cmd += ["--frame", str(still_frame), "--still", str(still_path)]

    with open(log, "wb") as fh:
        fh.write(("$ " + subprocess.list2cmdline(cmd) + "\n\n").encode("utf-8"))
        fh.flush()
        try:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RenderError(f"Blender 渲染逾時（{timeout} 秒），log：{log}") from exc
        except OSError as exc:
            raise RenderError(f"無法啟動 Blender：{exc}") from exc

    data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    if proc.returncode != 0 or not data or data.get("status") != "ok":
        detail = (data or {}).get("error") or _tail(log)
        raise RenderError(f"Blender 渲染失敗（exit {proc.returncode}）：{detail}\nlog：{log}")
    return data


def _tail(path: Path, lines: int = 15) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])
