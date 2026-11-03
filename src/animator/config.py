"""讀取 config.yaml，並與內建預設值、環境變數、CLI 覆寫合併。

優先順序：CLI > 環境變數 ANIMATOR_* > config.yaml > 內建預設值
規格見 docs/config.md。內建預設值必須與 config/config.example.yaml 保持一致。
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from animator.errors import ConfigError
from animator.paths import project_root, resolve_executable, resolve_path

CONFIG_VERSION = 1

# 內建預設值：不能包含任何電腦專屬的值
DEFAULTS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "tools": {
        "blender": {
            "executable": "",
            "min_version": "4.2",
            "extra_args": ["--factory-startup"],
            "timeout_sec": 3600,
        },
        "ffmpeg": {"executable": "ffmpeg", "ffprobe": "", "timeout_sec": 600},
        "python": {"executable": ""},
    },
    "paths": {
        "input_dir": "assets",
        "output_dir": "output",
        "temp_dir": "tmp",
        "models_dir": "models",
    },
    "jobs": {
        "id_format": "%Y%m%d-%H%M%S_{name}",
        "keep_frames": False,
        "overwrite": False,
    },
    "defaults": {
        "canvas": {"fps": 30, "background_color": "#000000"},
        "render": {"engine": "eevee", "samples": 16, "view_transform": "Standard"},
        "encode": {"codec": "libx264", "crf": 18, "preset": "medium", "pix_fmt": "yuv420p"},
        "preview": {"resolution_percentage": 25, "samples": 4, "crf": 28},
    },
    "logging": {"level": "INFO"},
    "ai": {
        "device": "auto",
        "segmentation": {"backend": "manual"},
        "inpaint": {"backend": "none"},
        "planner": {"backend": "manual"},
    },
}

ENV_OVERRIDES = {
    "ANIMATOR_BLENDER": "tools.blender.executable",
    "ANIMATOR_FFMPEG": "tools.ffmpeg.executable",
    "ANIMATOR_PYTHON": "tools.python.executable",
}

PATH_KEYS = ("input_dir", "output_dir", "temp_dir", "models_dir")


@dataclass
class Config:
    data: dict[str, Any]
    sources: dict[str, str]
    root: Path
    config_file: Path | None
    warnings: list[str] = field(default_factory=list)

    def get(self, dotted: str) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(dotted)
            node = node[part]
        return node

    def source(self, dotted: str) -> str:
        return self.sources.get(dotted, "default")

    def path(self, name: str) -> Path:
        """paths.<name> 解析後的絕對路徑（以專案根目錄為基準）。"""
        if name not in PATH_KEYS:
            raise KeyError(name)
        return resolve_path(self.get(f"paths.{name}"), self.root)

    def executable(self, tool: str) -> Path | None:
        return resolve_executable(self.get(f"tools.{tool}.executable"), self.root)

    def effective(self) -> dict[str, Any]:
        """給 doctor 與渲染紀錄用：所有葉節點的值與來源。"""
        out: dict[str, Any] = {}
        for key, value in _flatten(self.data):
            out[key] = {"value": value, "source": self.source(key)}
        for name in PATH_KEYS:
            out[f"paths.{name}"]["resolved"] = str(self.path(name))
        return {
            "project_root": str(self.root),
            "config_file": str(self.config_file) if self.config_file else None,
            "values": out,
        }


def _flatten(node: dict[str, Any], prefix: str = ""):
    for key, value in node.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _flatten(value, dotted + ".")
        else:
            yield dotted, value


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    node = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _merge(base: dict, override: dict, defaults: dict, sources: dict, source: str,
           warnings: list[str], prefix: str = "") -> None:
    for key, value in override.items():
        dotted = f"{prefix}{key}"
        if key not in defaults:
            warnings.append(f"config：不認識的欄位 {dotted}（已忽略，請檢查是否打錯字）")
            continue
        default = defaults[key]
        if isinstance(default, dict):
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ConfigError(f"config：{dotted} 應該是一個區塊（mapping）")
            _merge(base[key], value, default, sources, source, warnings, dotted + ".")
            continue
        base[key] = _check_type(dotted, value, default)
        sources[dotted] = source


def _check_type(dotted: str, value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"config：{dotted} 應該是 true/false，目前是 {value!r}")
    elif isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"config：{dotted} 應該是整數，目前是 {value!r}")
    elif isinstance(default, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)  # 例如 min_version: 4.2
        if not isinstance(value, str):
            raise ConfigError(f"config：{dotted} 應該是字串，目前是 {value!r}")
    elif isinstance(default, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"config：{dotted} 應該是字串清單，目前是 {value!r}")
    return value


def find_config_file(explicit: str | os.PathLike | None, root: Path) -> Path:
    if explicit:
        path = resolve_path(explicit, Path.cwd())
    elif os.environ.get("ANIMATOR_CONFIG"):
        path = resolve_path(os.environ["ANIMATOR_CONFIG"], Path.cwd())
    else:
        path = root / "config" / "config.yaml"
    if not path.is_file():
        raise ConfigError(
            f"找不到設定檔：{path}\n"
            f"請先從範本複製：copy config\\config.example.yaml config\\config.yaml"
        )
    return path


def load_config(config_path: str | os.PathLike | None = None,
                cli_overrides: dict[str, Any] | None = None) -> Config:
    root = project_root()
    file = find_config_file(config_path, root)
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml 格式錯誤：{file}\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config.yaml 的最上層必須是 mapping：{file}")

    version = raw.get("config_version")
    if version != CONFIG_VERSION:
        raise ConfigError(f"config_version 必須是 {CONFIG_VERSION}，目前是 {version!r}（{file}）")

    data = copy.deepcopy(DEFAULTS)
    sources: dict[str, str] = {}
    warnings: list[str] = []
    _merge(data, raw, DEFAULTS, sources, "config", warnings)

    for env_name, dotted in ENV_OVERRIDES.items():
        if os.environ.get(env_name):
            _set_dotted(data, dotted, os.environ[env_name])
            sources[dotted] = "env"

    for dotted, value in (cli_overrides or {}).items():
        if value is None:
            continue
        _set_dotted(data, dotted, value)
        sources[dotted] = "cli"

    level = data["logging"]["level"]
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise ConfigError(f"config：logging.level 不支援 {level!r}")
    if data["defaults"]["render"]["engine"] not in ("eevee", "cycles"):
        raise ConfigError("config：defaults.render.engine 必須是 eevee 或 cycles")

    return Config(data=data, sources=sources, root=root, config_file=file, warnings=warnings)
