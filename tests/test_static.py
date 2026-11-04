"""靜態檢查：可攜性與兩個 Python 環境的界線。"""

from __future__ import annotations

import ast
import getpass
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLENDER_ALLOWED = {"bpy", "bpy_extras", "mathutils", "anim_bl"} | set(sys.stdlib_module_names)
MACHINE_PATH = re.compile(r"[A-Za-z]:[\\/]|[\\/]Users[\\/]|Program Files|AppData", re.IGNORECASE)

# Stage 0 驗證腳本：不得修改
TEST_ANIMATION_SHA256 = "5838ad13e637198833e3b224e517c0e954081654a7191632f87c5114db4d6d99"


def _py_files(*dirs: str):
    for d in dirs:
        yield from (ROOT / d).rglob("*.py")


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def test_blender_side_imports_only_bpy_and_stdlib():
    bad = {}
    for path in _py_files("blender"):
        extra = set(_imports(path)) - BLENDER_ALLOWED
        if extra:
            bad[str(path.relative_to(ROOT))] = sorted(extra)
    assert not bad, f"blender/ 只能 import bpy 與標準函式庫：{bad}"


def test_system_side_never_imports_bpy():
    offenders = [str(p.relative_to(ROOT)) for p in _py_files("src")
                 if {"bpy", "mathutils", "anim_bl"} & set(_imports(p))]
    assert not offenders


def test_no_machine_specific_paths_in_code():
    user = getpass.getuser().lower()
    hits = []
    for path in [*_py_files("src", "blender", "scripts"), ROOT / "animator.cmd"]:
        if path.name == "test_animation.py":
            continue  # Stage 0 例外（docs 已說明）
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if MACHINE_PATH.search(line) or (user and user in line.lower()):
                hits.append(f"{path.relative_to(ROOT)}:{no}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_example_config_has_no_user_name():
    text = (ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8").lower()
    assert getpass.getuser().lower() not in text


def test_stage0_script_unchanged():
    digest = hashlib.sha256((ROOT / "scripts" / "test_animation.py").read_bytes()).hexdigest()
    assert digest == TEST_ANIMATION_SHA256


def test_requirements_files_are_ascii():
    """pip 用系統字碼頁（繁中 Windows = cp950）讀 requirements 檔，非 ASCII 的註解會讓 pip install -r 失敗。"""
    for name in ("requirements.txt", "requirements-dev.txt"):
        data = (ROOT / name).read_bytes()
        assert all(b < 128 for b in data), name


def test_gitignore_covers_local_files():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in ("config/config.yaml", "output/", "tmp/", ".venv/", "models/"):
        assert entry in text
