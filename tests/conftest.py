from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "basic"
sys.path.insert(0, str(ROOT / "scripts"))   # make_sample_layers（只用標準函式庫）
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture
def make_config(tmp_path, monkeypatch):
    """在 tmp_path 建立 config.yaml 並載入；output/temp 指向 tmp_path。"""
    from animator.config import load_config

    for name in ("ANIMATOR_BLENDER", "ANIMATOR_FFMPEG", "ANIMATOR_PYTHON", "ANIMATOR_CONFIG"):
        monkeypatch.delenv(name, raising=False)

    def _make(text: str = "", overrides: dict | None = None):
        cfg_file = tmp_path / "config.yaml"
        body = "config_version: 1\n" + text
        if "paths:" not in text:
            body += (f"paths:\n  output_dir: '{(tmp_path / 'out').as_posix()}'\n"
                     f"  temp_dir: '{(tmp_path / 'tmp').as_posix()}'\n")
        cfg_file.write_text(body, encoding="utf-8")
        return load_config(cfg_file, overrides)

    return _make


@pytest.fixture
def example_raw() -> dict:
    return json.loads((EXAMPLE / "spec.json").read_text(encoding="utf-8"))


@pytest.fixture
def spec_dir(tmp_path) -> Path:
    """複製範例圖層到 tmp，回傳可放 spec 的目錄。"""
    import shutil

    target = tmp_path / "spec"
    shutil.copytree(EXAMPLE / "layers", target / "layers")
    return target


def write_spec(directory: Path, raw: dict) -> Path:
    path = directory / "spec.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
