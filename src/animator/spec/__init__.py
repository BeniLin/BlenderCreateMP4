"""spec.json 的讀取、驗證與解析。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from animator.errors import SpecError
from animator.spec.schema import Issue


def load_spec(path: Path) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SpecError([Issue("", f"無法讀取 spec：{path}（{exc}）")]) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError([Issue("", f"JSON 格式錯誤：第 {exc.lineno} 行第 {exc.colno} 欄：{exc.msg}")]) from exc


def dump_json(data: Any, path: Path) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
