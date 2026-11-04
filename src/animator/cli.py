"""命令列入口：animator doctor / validate / new / render。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import contextmanager
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

    import importlib.util
    ai_mods = ("numpy", "cv2", "PIL", "torch", "transformers", "pymatting", "onnxruntime")
    missing = [m for m in ai_mods if importlib.util.find_spec(m) is None]
    line("OK" if not missing else "WARN", "AI 套件（Stage 2，只影響 segment）",
         "已安裝" if not missing else f"缺少：{', '.join(missing)}（見 requirements-ai.txt）")
    if not missing:
        from animator.segmentation import models as M
        for name in M.MODELS:
            st = M.status(cfg, name, full_hash=False)
            line("OK" if st.verified else "WARN", f"模型 {name}", st.detail)

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


# ---------------------------------------------------------------- segment / models（Stage 2）

# segment 在主行程用到的 AI 套件（頂層模組名稱）；模型推論的套件在子行程裡 import
AI_MODULES = frozenset({"numpy", "cv2", "PIL", "scipy", "torch", "torchvision", "transformers",
                        "huggingface_hub", "pymatting", "numba", "onnxruntime"})


@contextmanager
def ai_import_guard(command: str):
    """把「缺少 AI 套件」的 ModuleNotFoundError 轉成簡短、可操作的錯誤（不印 traceback）。

    只處理 AI_MODULES 裡的套件；其他模組找不到、或其他例外，一律原樣往外拋。
    原始例外保留在 __cause__，用 -v 執行時會寫進 DEBUG log。
    """
    try:
        yield
    except ModuleNotFoundError as exc:
        top = (exc.name or "").split(".")[0]
        if top not in AI_MODULES:
            raise
        log.debug("缺少 AI 套件的原始例外", exc_info=exc)
        raise AnimatorError(
            f"animator {command} 需要 Stage 2 的 AI 套件，但找不到 {top!r}（{exc}）。\n"
            f"請把 AI 依賴安裝到專案的 .venv（步驟寫在 requirements-ai.txt 的開頭）：\n"
            f"  1. torch / torchvision（PyTorch CPU index）\n"
            f"  2. .venv\\Scripts\\python.exe -m pip install --only-binary=:all: -r requirements-ai.txt\n"
            f"  3. .\\animator.cmd models download --all\n"
            f"Stage 1 的指令（doctor / validate / new / render）不需要這些套件。") from exc


def cmd_segment(args) -> int:
    cfg = _load(args)
    with ai_import_guard("segment"):
        from animator.segmentation.pipeline import SegmentOptions, segment

        labels = [s.strip() for s in args.labels.split(",") if s.strip()] if args.labels else None
        opts = SegmentOptions(labels=labels, masks_dir=Path(args.masks).resolve() if args.masks else None,
                              name=args.name, inpaint=args.inpaint, refine="none" if args.no_refine else None,
                              duration=args.duration, fps=args.fps, keep_temp=args.keep_temp)
        result = segment(Path(args.image), cfg, opts)
    print(f"已建立工作：{result.job.dir}")
    print(f"圖層：{len(result.layers) - 1} 個物件 + 背景")
    for layer in result.layers[1:]:
        flag = "  ⚠ 需要人工檢查" if layer["meta"]["review"] else ""
        print(f"  z={layer['z']:<4} {layer['id']:<20} {layer['meta']['label']}{flag}")
    for r in result.regions:
        done = "已修補" if r["repaired"] else "沒有修補（物件仍在背景上）"
        print(f"  背景區域 {r['index']}：{r['area_ratio']:.1%}  {r['backend'] or '-'}  {r['status']}  {done}")
    if result.review_required:
        print("\n需要人工檢查（沒有通過自動檢查，或超過 review 門檻）：")
        for line in result.review:
            print(f"  - {line}")
    print(f"\n檢查：{result.job.dir / 'segmentation' / 'overview.png'}")
    print(f"渲染：animator render \"{result.spec_path}\"")
    print(f"耗時：{result.timings.get('total')} 秒")
    if result.no_objects:
        print("[WARN] 沒有產生任何物件圖層（結束碼 3）")
        return 3
    if result.incomplete:
        print("[WARN] 背景沒有完整修補：有區域保留了原本的物件（結束碼 4）")
        return 4
    return 0


def cmd_models(args) -> int:
    from animator.segmentation import models as M

    cfg = _load(args)
    names = list(M.MODELS) if args.action == "list" or not args.names or args.all else args.names
    failed = False
    for name in names:
        if args.action == "download":
            st = M.download(cfg, name)
        else:
            st = M.status(cfg, name, full_hash=args.action == "verify")
        failed |= not st.verified
        s = st.spec
        print(f"[{'OK' if st.verified else 'FAIL' if st.present else '----'}] {name:<20} {s.role:<10} "
              f"{s.size_bytes / 1e6:7.1f} MB  {s.license:<11} {s.repo}@{s.revision[:7]}  {st.detail}")
    if args.action == "list":
        print(f"\n模型目錄：{cfg.path('models_dir')}（下載：animator models download [--all | <name>]）")
        return 0
    return 1 if failed else 0


# ---------------------------------------------------------------- layers / anim（S2-2）

def cmd_layers(args) -> int:
    from animator.animate.edit import list_layers, resolve_job_spec
    from animator.spec import load_spec

    cfg = _load(args)
    spec_path = resolve_job_spec(args.job, cfg)
    raw = load_spec(spec_path)
    if not isinstance(raw, dict):
        raise AnimatorError(f"{spec_path} 不是有效的 spec")
    rows = list_layers(raw)
    print(f"spec：{spec_path}")
    print(f"{'id':<22} {'label':<16} {'z':>6}  {'position':<20} {'background':<10} {'animations':>10}")
    for r in rows:
        pos = f"[{r.position[0]:g}, {r.position[1]:g}]" if len(r.position) == 2 else str(r.position)
        print(f"{r.id:<22} {r.label or '-':<16} {r.z:>6g}  {pos:<20} {'yes' if r.background else 'no':<10} "
              f"{r.animations:>10}")
    return 0


def cmd_anim(args) -> int:
    from animator.animate.edit import add_move, parse_pair, resolve_job_spec

    cfg = _load(args)
    spec_path = resolve_job_spec(args.job, cfg)
    by = parse_pair(args.by, "--by") if args.by is not None else None
    to = parse_pair(args.to, "--to") if args.to is not None else None
    res = add_move(spec_path, args.layer, by=by, to=to, start=args.start, duration=args.duration)
    a = res.animation
    print(f"已加入動畫 {a['id']}：{a['target']} move {json.dumps(a['params'], ensure_ascii=False)}  "
          f"{a['start']}s → {a['end']}s")
    print(f"spec：{res.spec_path}")
    print(f"原本的 spec 備份在：{res.backup}")
    for w in res.warnings:
        print(f"[WARN] {w}")
    print(f"預覽：animator render \"{res.spec_path}\" --preview")
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

    s = sub.add_parser("segment", help="Stage 2：把一張圖片自動分層 → spec.json + 透明 PNG 圖層")
    s.add_argument("--image", required=True, help="輸入圖片")
    s.add_argument("--labels", help='要分出來的元素（英文，逗號分隔），例如 "person, dog, sun"')
    s.add_argument("--masks", help="改用自己提供的遮罩資料夾（檔名 = 標籤，白色 = 物件），不跑偵測和分割")
    s.add_argument("--name")
    s.add_argument("--inpaint", choices=["migan", "opencv", "none"], help="覆寫 ai.inpaint.backend")
    s.add_argument("--no-refine", action="store_true", help="不做邊緣精修（refine.backend = none）")
    s.add_argument("--duration", type=float, default=5.0, help="產生的 spec 的長度（秒）")
    s.add_argument("--fps", type=int)
    s.add_argument("--keep-temp", action="store_true", help="成功時也保留 tmp/<job_id>/ 的暫存檔（失敗時一律保留）")
    s.set_defaults(func=cmd_segment)

    m = sub.add_parser("models", help="Stage 2：AI 模型的清單 / 下載 / 驗證")
    m.add_argument("action", choices=["list", "download", "verify"])
    m.add_argument("names", nargs="*", help="模型名稱（預設：全部）")
    m.add_argument("--all", action="store_true")
    m.set_defaults(func=cmd_models)

    ly = sub.add_parser("layers", help="S2-2：列出 job 的圖層")
    ly_sub = ly.add_subparsers(dest="layers_action", required=True)
    ll = ly_sub.add_parser("list", help="列出圖層（id、label、z、初始位置、是否為背景、動畫數）")
    ll.add_argument("job", help="job 資料夾、job 裡的 spec.json，或 output 底下的 job_id")
    ll.set_defaults(func=cmd_layers)

    an = sub.add_parser("anim", help="S2-2：編輯 job 的動畫（寫回 spec.json，先備份）")
    an_sub = an.add_subparsers(dest="anim_action", required=True)
    aa = an_sub.add_parser("add", help="加入一筆動畫（M1 只支援 move）")
    aa.add_argument("job", help="job 資料夾、job 裡的 spec.json，或 output 底下的 job_id")
    aa.add_argument("layer", help="圖層 id（不能是背景）")
    aa.add_argument("effect", choices=["move"], help="效果（M1 只支援 move）")
    target = aa.add_mutually_exclusive_group(required=True)
    target.add_argument("--by", metavar="DX,DY", help="相對位移（畫布像素），例如 120,0")
    target.add_argument("--to", metavar="X,Y", help="目標位置（圖層 anchor 點的畫布像素座標），例如 1000,720")
    aa.add_argument("--start", type=float, required=True, help="開始時間（秒）")
    aa.add_argument("--duration", type=float, required=True, help="持續時間（秒，> 0）")
    aa.set_defaults(func=cmd_anim)
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
