"""PNG 影格序列 → MP4，以及用 ffprobe / ffmpeg 驗證輸出。"""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from animator.errors import RenderError


def encode(ffmpeg: Path, pattern: Path, fps: int, out: Path, codec: str, crf: int,
           preset: str, pix_fmt: str, log: Path, timeout: int) -> None:
    cmd = [
        str(ffmpeg), "-hide_banner", "-y",
        "-framerate", str(fps), "-start_number", "1", "-i", str(pattern),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",   # 保證寬高為偶數（yuv420p）
        "-c:v", codec, "-preset", preset, "-crf", str(crf),
        "-pix_fmt", pix_fmt, "-r", str(fps),
        "-movflags", "+faststart",
        str(out),
    ]
    with open(log, "wb") as fh:
        fh.write(("$ " + subprocess.list2cmdline(cmd) + "\n\n").encode("utf-8"))
        fh.flush()
        try:
            proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RenderError(f"FFmpeg 逾時（{timeout} 秒），log：{log}") from exc
    if proc.returncode != 0 or not out.is_file():
        raise RenderError(f"FFmpeg 合成失敗（exit {proc.returncode}），log：{log}")


def probe(ffprobe: Path, video: Path) -> dict:
    cmd = [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-count_frames",
           "-show_entries", "stream=codec_name,width,height,r_frame_rate,nb_read_frames,pix_fmt:format=duration",
           "-of", "json", str(video)]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RenderError(f"ffprobe 失敗：{proc.stderr.decode('utf-8', 'replace')}")
    data = json.loads(proc.stdout.decode("utf-8"))
    stream = data["streams"][0]
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(Fraction(stream["r_frame_rate"])),
        "frames": int(stream.get("nb_read_frames", 0)),
        "pix_fmt": stream.get("pix_fmt"),
        "duration": float(data["format"]["duration"]),
    }


def decode_check(ffmpeg: Path, video: Path) -> list[str]:
    """完整解碼一次，回傳解碼錯誤訊息（空清單 = 影片可以正常播放）。"""
    proc = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(video), "-f", "null", "-"],
                          capture_output=True, timeout=600)
    text = proc.stderr.decode("utf-8", "replace").strip()
    errors = text.splitlines() if text else []
    if proc.returncode != 0 and not errors:
        errors = [f"ffmpeg exit {proc.returncode}"]
    return errors
