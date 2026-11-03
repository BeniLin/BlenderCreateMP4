"""命令列入口：animator doctor / validate / new / render。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from animator import __version__
from animator.config import load_config
from animator.errors import AnimatorError, SpecError

log = logging.getLogger("animator")


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _load(args) -> "Config":  # noqa: F821
    overrides = {"tools.blender.executable": args.blender, "tools.ffmpeg.executable": args.ffmpeg}
    cfg = load_config(args.config, overrides)
    level = "DEBUG" if args.verbose else cfg.get("logging.level")
    logging.basicConfig(level=level, format="%(levelname)s %(message)s", force=True)
    for w in cfg.warnings:
        log.warning(w)
    return cfg


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    from animator.tools import ffmpeg_has_encoder, find_blender, find_ffmpeg, find_ffprobe

    cfg = _load(args)
    failed = False

    def line(status: str, label: str, detail: str = "") -> None:
        nonlocal failed
        failed |= status == "FAIL"
        print(f"[{status:<4}] {label}{'  ' + detail if detail else ''}")

    print(f"animator {__version__}")
    print(f"專案根目錄：{cfg.root}")
    print(f"設定檔：    {cfg.config_file}\n")

    try:
        b = find_blender(cfg)
        line("OK", "Blender", f"{b.version}  {b.path}  (來源：{cfg.source('tools.blender.executable')})")
    except AnimatorError as exc:
        line("FAIL", "Blender", str(exc))

    ffmpeg_path = None
    try:
        f = find_ffmpeg(cfg)
        ffmpeg_path = f.path
        line("OK", "FFmpeg", f"{f.version}  {f.path}  (來源：{cfg.source('tools.ffmpeg.executable')})")
        codec = cfg.get("defaults.encode.codec")
        if ffmpeg_has_encoder(f.path, codec):
            line("OK", f"FFmpeg 編碼器 {codec}")
        else:
            line("FAIL", f"FFmpeg 編碼器 {codec}", "不支援，請改用 GPL build 的 FFmpeg")
    except AnimatorError as exc:
        line("FAIL", "FFmpeg", str(exc))

    probe = find_ffprobe(cfg, ffmpeg_path)
    line("OK" if probe else "WARN", "ffprobe", str(probe) if probe else "找不到，將略過輸出影片驗證")

    py = cfg.executable("python") if cfg.get("tools.python.executable") else Path(sys.executable)
    line("OK" if py else "WARN", "Python（AI 模組用，Stage 2 起）", str(py) if py else "找不到設定的 Python")

    for name in ("input_dir", "output_dir", "temp_dir", "models_dir"):
        path = cfg.path(name)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe_file = path / ".write_test"
            probe_file.write_text("ok")
            probe_file.unlink()
            line("OK", f"paths.{name}", str(path))
        except OSError as exc:
            line("FAIL", f"paths.{name}", f"{path} 無法寫入：{exc}")

    if args.show_config:
        print("\n有效設定（值 / 來源）：")
        for key, item in cfg.effective()["values"].items():
            print(f"  {key} = {item['value']!r}  [{item['source']}]")

    print("\n結果：" + ("有項目失敗" if failed else "全部通過"))
    return 1 if failed else 0


# ---------------------------------------------------------------- validate

def cmd_validate(args) -> int:
    from animator.spec import load_spec
    from animator.spec.validate import validate

    _load(args)
    spec = Path(args.spec).resolve()
    result = validate(load_spec(spec), spec.parent)
    for w in result.warnings:
        print(f"[WARN] {w}")
    for e in result.errors:
        print(f"[FAIL] {e}")
    print(f"{spec}：{'驗證通過' if result.ok else f'{len(result.errors)} 個錯誤'}"
          f"（{len(result.warnings)} 個警告）")
    return 0 if result.ok else 2


# ---------------------------------------------------------------- new

def cmd_new(args) -> int:
    from animator.jobs import create_job_from_layers

    cfg = _load(args)
    layers = Path(args.layers)
    if not layers.is_absolute():
        layers = (Path.cwd() / layers)
    job = create_job_from_layers(layers, cfg, args.name, duration=args.duration, fps=args.fps)
    print(f"已建立工作：{job.dir}")
    print(f"請編輯 {job.spec_path} 加入動畫，然後執行：animator render \"{job.spec_path}\"")
    return 0


# ---------------------------------------------------------------- render

def cmd_render(args) -> int:
    from animator.pipeline import render

    cfg = _load(args)
    result = render(Path(args.spec), cfg, preview=args.preview, frame=args.frame,
                    keep_frames=True if args.keep_frames else None, name=args.name)
    print(f"完成：{result.output}")
    ver = result.report.get("verification")
    if ver and ver.get("actual"):
        a = ver["actual"]
        print(f"影片：{a['codec']} {a['width']}x{a['height']} {a['fps']:g}fps "
              f"{a['frames']} 格 {a['duration']:.2f}s，可解碼：{'是' if ver['decodable'] else '否'}")
    if result.report.get("previous_output_moved_to"):
        print(f"舊的輸出已備份到：{result.job.dir / result.report['previous_output_moved_to']}")
    print(f"渲染紀錄：{result.history_dir}")
    if result.frames_dir:
        print(f"影格保留於：{result.frames_dir}")
    return 0


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="animator", description="AI Image-to-Animation")
    p.add_argument("--version", action="version", version=f"animator {__version__}")
    p.add_argument("--config", help="設定檔路徑（預設 config/config.yaml，或 ANIMATOR_CONFIG）")
    p.add_argument("--blender", help="覆寫 tools.blender.executable")
    p.add_argument("--ffmpeg", help="覆寫 tools.ffmpeg.executable")
    p.add_argument("-v", "--verbose", action="store_true", help="顯示 DEBUG 訊息")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="檢查設定與外部工具")
    d.add_argument("--show-config", action="store_true", help="列出所有有效設定與來源")
    d.set_defaults(func=cmd_doctor)

    v = sub.add_parser("validate", help="驗證 spec.json")
    v.add_argument("spec")
    v.set_defaults(func=cmd_validate)

    n = sub.add_parser("new", help="用 PNG 圖層資料夾建立新工作與起始 spec")
    n.add_argument("--layers", required=True, help="PNG 圖層資料夾（檔名排序 = 前後順序）")
    n.add_argument("--name")
    n.add_argument("--duration", type=float, default=5.0)
    n.add_argument("--fps", type=int)
    n.set_defaults(func=cmd_new)

    r = sub.add_parser("render", help="渲染 spec.json 成 MP4")
    r.add_argument("spec")
    r.add_argument("--preview", action="store_true", help="低解析度預覽 → preview.mp4")
    r.add_argument("--frame", type=int, help="只渲染指定影格 → still_<N>.png")
    r.add_argument("--keep-frames", action="store_true", help="保留 tmp 中的 PNG 影格")
    r.add_argument("--name", help="外部 spec 建立新工作時使用的名稱")
    r.set_defaults(func=cmd_render)
    return p


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SpecError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    except AnimatorError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
