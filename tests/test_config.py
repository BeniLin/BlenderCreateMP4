from __future__ import annotations

from pathlib import Path

import pytest

from animator.config import DEFAULTS, load_config
from animator.errors import ConfigError
from animator.paths import expand_env, project_root, resolve_executable, resolve_path

ROOT = Path(__file__).resolve().parents[1]


def test_project_root_detected():
    assert project_root() == ROOT


def test_example_config_matches_builtin_defaults(monkeypatch):
    """config.example.yaml 必須與內建預設值一致（避免文件與程式脫節）。"""
    import yaml

    for name in ("ANIMATOR_BLENDER", "ANIMATOR_FFMPEG", "ANIMATOR_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    example = yaml.safe_load((ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    assert example == DEFAULTS
    cfg = load_config(ROOT / "config" / "config.example.yaml")
    assert cfg.warnings == []


def test_partial_config_uses_defaults(make_config):
    cfg = make_config("tools:\n  blender:\n    executable: blender\n")
    assert cfg.get("tools.blender.executable") == "blender"
    assert cfg.source("tools.blender.executable") == "config"
    assert cfg.get("defaults.encode.crf") == 18
    assert cfg.source("defaults.encode.crf") == "default"


def test_precedence_cli_over_env_over_config(make_config, monkeypatch):
    text = "tools:\n  blender:\n    executable: from-config\n"
    assert make_config(text).get("tools.blender.executable") == "from-config"

    monkeypatch.setenv("ANIMATOR_BLENDER", "from-env")
    cfg = make_config(text)
    assert (cfg.get("tools.blender.executable"), cfg.source("tools.blender.executable")) == ("from-env", "env")

    cfg = make_config(text, {"tools.blender.executable": "from-cli"})
    assert (cfg.get("tools.blender.executable"), cfg.source("tools.blender.executable")) == ("from-cli", "cli")


def test_unknown_key_warns(make_config):
    cfg = make_config("jobs:\n  keep_frame: true\n")
    assert any("jobs.keep_frame" in w for w in cfg.warnings)


def test_type_error_rejected(make_config):
    with pytest.raises(ConfigError):
        make_config("jobs:\n  keep_frames: 'yes'\n")


def test_wrong_version_rejected(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("config_version: 2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(f)


def test_missing_config_file_message(tmp_path):
    with pytest.raises(ConfigError, match="config.example.yaml"):
        load_config(tmp_path / "nope.yaml")


def test_relative_paths_resolve_against_project_root(make_config, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 工作目錄不影響結果
    cfg = make_config("paths:\n  output_dir: output\n  temp_dir: tmp\n")
    assert cfg.path("output_dir") == ROOT / "output"


def test_env_and_home_expansion(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIM_TEST_DIR", str(tmp_path))
    assert resolve_path("${ANIM_TEST_DIR}/x", ROOT) == (tmp_path / "x").resolve()
    assert resolve_path("~/x", ROOT) == (Path.home() / "x").resolve()
    with pytest.raises(ConfigError):
        expand_env("${ANIM_DOES_NOT_EXIST_123}/x")


def test_resolve_executable(tmp_path):
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"")
    assert resolve_executable(str(exe), ROOT) == exe.resolve()
    assert resolve_executable(str(tmp_path / "missing.exe"), ROOT) is None
    assert resolve_executable("", ROOT) is None
    assert resolve_executable("definitely-not-a-command-xyz", ROOT) is None
