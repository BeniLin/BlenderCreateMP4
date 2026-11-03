"""專案根目錄判定與路徑解析。

規則（見 docs/config.md 第 3 節）：
  1. 展開 ${VAR}（變數不存在時報錯）
  2. 展開 ~
  3. 相對路徑以 base（通常是專案根目錄）為基準
  4. 正規化為絕對路徑
"""

from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

from animator.errors import ConfigError

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _is_project_root(path: Path) -> bool:
    return (path / "pyproject.toml").is_file() and (path / "config").is_dir()


@lru_cache(maxsize=1)
def project_root() -> Path:
    """回傳專案根目錄：ANIMATOR_ROOT > 從本檔往上找 > 從目前工作目錄往上找。"""
    env = os.environ.get("ANIMATOR_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if not root.is_dir():
            raise ConfigError(f"ANIMATOR_ROOT 指向的目錄不存在：{root}")
        return root

    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for candidate in (start, *start.parents):
            if _is_project_root(candidate):
                return candidate

    raise ConfigError(
        "找不到專案根目錄（需要同時含有 pyproject.toml 與 config/ 的目錄）。"
        "請在專案目錄中執行，或設定環境變數 ANIMATOR_ROOT。"
    )


def expand_env(value: str) -> str:
    """展開 ${VAR}；變數不存在時報錯，而不是變成空字串。"""

    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ConfigError(f"設定值引用了不存在的環境變數 ${{{name}}}：{value!r}")
        return os.environ[name]

    return _ENV_PATTERN.sub(repl, value)


def resolve_path(value: str | os.PathLike, base: Path) -> Path:
    """依照專案路徑規則，把設定值轉成絕對路徑。"""
    text = expand_env(str(value))
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _looks_like_command(value: str) -> bool:
    return "/" not in value and "\\" not in value and not Path(value).is_absolute()


def resolve_executable(value: str, base: Path) -> Path | None:
    """解析執行檔：只有指令名稱時從 PATH 尋找；否則視為路徑。找不到時回傳 None。"""
    if not value or not str(value).strip():
        return None
    text = expand_env(str(value).strip())
    if _looks_like_command(text):
        found = shutil.which(text)
        return Path(found).resolve() if found else None
    path = resolve_path(text, base)
    return path if path.is_file() else None


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
