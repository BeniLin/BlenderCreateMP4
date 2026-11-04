"""沒有安裝 AI 套件時的 CLI 行為（只用標準函式庫 + PyYAML，不需要 numpy）。

用 `python -S`（不載入 site-packages）+ 只放 PyYAML 的資料夾，真的模擬「沒有 AI 套件」的環境：
- segment 顯示簡短、可操作的錯誤（指向 requirements-ai.txt），沒有 traceback，結束碼非 0
- -v 時才把原始例外寫進 DEBUG log
- Stage 1 的指令（validate / new / doctor / render）照常可用
- ai_import_guard 只處理 AI 套件，不會吞掉其他例外
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "basic"


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> Path:
    """只含 PyYAML 的 site 資料夾（沒有 numpy / OpenCV / Pillow / torch …）。"""
    import yaml

    site = tmp_path_factory.mktemp("site_yaml_only")
    pkg = Path(yaml.__file__).parent
    shutil.copytree(pkg, site / pkg.name)
    ext = pkg.parent / "_yaml"
    if ext.is_dir():
        shutil.copytree(ext, site / "_yaml")
    return site


def _config(tmp_path: Path, with_tools: bool = False) -> Path:
    lines = ["config_version: 1", "paths:", f"  output_dir: '{(tmp_path / 'out').as_posix()}'",
             f"  temp_dir: '{(tmp_path / 'tmp').as_posix()}'"]
    if with_tools:
        from animator.config import load_config
        from animator.errors import AnimatorError
        from animator.tools import find_blender, find_ffmpeg

        try:
            cfg = load_config(None)
            blender, ffmpeg = find_blender(cfg).path, find_ffmpeg(cfg).path
        except AnimatorError as exc:
            pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")
        lines += ["tools:", "  blender:", f"    executable: '{Path(blender).as_posix()}'",
                  "  ffmpeg:", f"    executable: '{Path(ffmpeg).as_posix()}'"]
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_cli(sandbox: Path, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "ANIMATOR_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(sandbox), str(ROOT / "src")])
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-S", "-m", "animator", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def test_sandbox_really_has_no_ai_packages(sandbox):
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("PYTHON")},
           "PYTHONPATH": os.pathsep.join([str(sandbox), str(ROOT / "src")])}
    code = ("import importlib.util as u; import animator.cli, sys; "
            "print([m for m in ('numpy','cv2','PIL','torch','transformers','onnxruntime','pymatting') "
            "if u.find_spec(m)]); print([m for m in ('numpy','cv2','PIL','torch') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-S", "-c", code], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["[]", "[]"]                 # 找不到 AI 套件，而且 CLI 沒有預先 import


def test_segment_without_ai_packages_shows_actionable_error(sandbox, tmp_path):
    cfg = _config(tmp_path)
    res = run_cli(sandbox, "--config", str(cfg), "segment", "--image", str(ROOT / "assets/s2-0/photo_desk.jpg"),
                  "--labels", "person")
    assert res.returncode != 0
    err = res.stderr
    assert "Traceback" not in err and "Traceback" not in res.stdout
    assert "[FAIL]" in err and "requirements-ai.txt" in err and "'numpy'" in err
    assert "Stage 1" in err                                        # 告訴使用者 Stage 1 不受影響
    assert not (tmp_path / "out").exists() and not (tmp_path / "tmp").exists()   # 沒有留下任何檔案


def test_segment_verbose_keeps_original_exception_for_debugging(sandbox, tmp_path):
    cfg = _config(tmp_path)
    res = run_cli(sandbox, "-v", "--config", str(cfg), "segment", "--image",
                  str(ROOT / "assets/s2-0/photo_desk.jpg"), "--labels", "person")
    assert res.returncode != 0
    assert "Traceback" in res.stderr and "ModuleNotFoundError" in res.stderr
    assert "requirements-ai.txt" in res.stderr


def test_stage1_commands_work_without_ai_packages(sandbox, tmp_path):
    cfg = _config(tmp_path)
    res = run_cli(sandbox, "--config", str(cfg), "validate", str(EXAMPLE / "spec.json"))
    assert res.returncode == 0, res.stderr
    res = run_cli(sandbox, "--config", str(cfg), "new", "--layers", str(EXAMPLE / "layers"), "--name", "noai")
    assert res.returncode == 0, res.stderr
    assert list((tmp_path / "out").glob("*_noai/spec.json"))
    res = run_cli(sandbox, "--config", str(cfg), "doctor")
    assert "Traceback" not in res.stderr + res.stdout
    assert "[WARN] AI 套件" in res.stdout and "requirements-ai.txt" in res.stdout


def test_layer_animation_commands_work_without_ai_packages(sandbox, tmp_path):
    """S2-2 M1 的 layers list / anim add 只用標準函式庫。"""
    from test_animate import make_job

    cfg = _config(tmp_path)
    job = make_job(tmp_path / "out")
    res = run_cli(sandbox, "--config", str(cfg), "layers", "list", job.name)
    assert res.returncode == 0, res.stderr
    assert "ball-1" in res.stdout and "Traceback" not in res.stderr
    res = run_cli(sandbox, "--config", str(cfg), "anim", "add", job.name, "ball-1", "move",
                  "--by", "120,0", "--start", "0", "--duration", "0.5")
    assert res.returncode == 0, res.stderr
    assert "ball-1-move-1" in res.stdout
    res = run_cli(sandbox, "--config", str(cfg), "anim", "add", job.name, "ball-1", "rotate",
                  "--by", "-45", "--start", "0", "--duration", "0.5")         # S2-2 M2 + 負數
    assert res.returncode == 0, res.stderr
    assert "ball-1-rotate-1" in res.stdout
    assert len(list((job / "history" / "edits").iterdir())) == 2


@pytest.mark.blender
def test_stage1_render_works_without_ai_packages(sandbox, tmp_path):
    cfg = _config(tmp_path, with_tools=True)
    res = run_cli(sandbox, "--config", str(cfg), "render", str(EXAMPLE / "spec.json"), "--frame", "30",
                  "--name", "noai-render")
    assert res.returncode == 0, res.stderr[-2000:]
    assert list((tmp_path / "out").glob("*_noai-render/still_30.png"))


# ---------------------------------------------------------------- ai_import_guard（同一個行程）

def test_guard_converts_missing_ai_module():
    from animator.cli import ai_import_guard
    from animator.errors import AnimatorError

    with pytest.raises(AnimatorError, match="requirements-ai.txt") as info:
        with ai_import_guard("segment"):
            raise ModuleNotFoundError("No module named 'numpy.core'", name="numpy.core")
    assert isinstance(info.value.__cause__, ModuleNotFoundError)   # 原始例外保留


@pytest.mark.parametrize("exc", [
    ModuleNotFoundError("No module named 'yaml'", name="yaml"),          # 不是 AI 套件
    ModuleNotFoundError("No module named 'animator.segmentation.nope'", name="animator.segmentation.nope"),
    ImportError("cannot import name 'x' from 'numpy'", name="numpy"),     # 不是「找不到模組」
    ValueError("unrelated"),
    KeyboardInterrupt(),
])
def test_guard_does_not_swallow_unrelated_exceptions(exc):
    from animator.cli import ai_import_guard

    with pytest.raises(type(exc)):
        with ai_import_guard("segment"):
            raise exc
