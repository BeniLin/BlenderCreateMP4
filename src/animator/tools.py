"""找出外部工具（Blender / FFmpeg / ffprobe）並檢查版本與功能。"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from animator.config import Config
from animator.errors import ToolError
from animator.paths import resolve_executable

_BLENDER_VERSION = re.compile(r"Blender\s+(\d+)\.(\d+)(?:\.(\d+))?(.*)")


@dataclass
class ToolInfo:
    path: Path
    version: str  # 顯示用的完整版本字串


def parse_version(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text)
    if not parts:
        raise ValueError(f"無法解析版本號：{text!r}")
    return tuple(int(p) for p in parts[:3])


def _run(cmd: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"無法執行 {cmd[0]}：{exc}") from exc
    return proc.stdout.decode("utf-8", errors="replace") + proc.stderr.decode("utf-8", errors="replace")


def find_blender(cfg: Config) -> ToolInfo:
    value = cfg.get("tools.blender.executable")
    if not value:
        raise ToolError(
            "尚未設定 Blender：請在 config/config.yaml 填寫 tools.blender.executable，"
            "或設定環境變數 ANIMATOR_BLENDER。"
        )
    path = cfg.executable("blender")
    if path is None:
        raise ToolError(f"找不到 Blender 執行檔：{value!r}（來源：{cfg.source('tools.blender.executable')}）")

    output = _run([str(path), "--version"], timeout=120)
    match = _BLENDER_VERSION.search(output)
    if not match:
        raise ToolError(f"無法從 Blender 取得版本資訊：{path}\n{output[:500]}")
    version = tuple(int(g) for g in match.groups()[:3] if g is not None)
    label = f"{'.'.join(map(str, version))}{match.group(4).strip() and ' ' + match.group(4).strip()}"

    minimum = parse_version(cfg.get("tools.blender.min_version"))
    if version < minimum:
        raise ToolError(
            f"Blender 版本 {label} 低於最低需求 {cfg.get('tools.blender.min_version')}：{path}"
        )
    return ToolInfo(path=path, version=label)


def find_ffmpeg(cfg: Config) -> ToolInfo:
    value = cfg.get("tools.ffmpeg.executable")
    path = cfg.executable("ffmpeg")
    if path is None:
        raise ToolError(f"找不到 FFmpeg 執行檔：{value!r}（來源：{cfg.source('tools.ffmpeg.executable')}）")
    output = _run([str(path), "-hide_banner", "-version"])
    first = output.strip().splitlines()[0] if output.strip() else ""
    match = re.search(r"ffmpeg version (\S+)", first)
    return ToolInfo(path=path, version=match.group(1) if match else first)


def ffmpeg_has_encoder(ffmpeg: Path, codec: str) -> bool:
    output = _run([str(ffmpeg), "-hide_banner", "-encoders"])
    return re.search(rf"^\s*V\S*\s+{re.escape(codec)}\s", output, re.MULTILINE) is not None


def find_ffprobe(cfg: Config, ffmpeg: Path | None) -> Path | None:
    """ffprobe：config 指定 > 與 ffmpeg 同目錄 > PATH。找不到時回傳 None。"""
    value = cfg.get("tools.ffmpeg.ffprobe")
    if value:
        return resolve_executable(value, cfg.root)
    if ffmpeg is not None:
        sibling = ffmpeg.with_name("ffprobe" + ffmpeg.suffix)
        if sibling.is_file():
            return sibling
    found = shutil.which("ffprobe")
    return Path(found) if found else None
