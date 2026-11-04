"""S2-0.1（MI-GAN 可行性測試）共用設定。不是正式的 Stage 2 程式碼。

只 import scripts/s2_0 的 common / memprobe，不修改它們。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s2_0"))

from common import IMAGES, MODELS, OUT as OUT_S2_0, ROOT, setup_env  # noqa: E402

OUT = ROOT / "output" / "s2-0.1"

MIGAN = {
    "repo": "andraniksargsyan/migan",   # 官方 README 連結的 ONNX pipeline（論文第一作者的帳號）
    "revision": "406830d0fa60666da0071c342ad2fbc8f30c5c64",   # 不用浮動的 main
    "filename": "migan_pipeline_v2.onnx",
    "sha256": "6f1f3530a1a2324b19752018ce756088b07973cda8d7d890034ace5c8a48c40b",
    "license": "mit",   # 官方 repo 的 LICENSE-WEIGHTS = MIT；HF repo 的 LICENSE 同為 MIT（PAIR）
}
MIGAN_DIR = MODELS / "migan"
MIGAN_PATH = MIGAN_DIR / MIGAN["filename"]

__all__ = ["IMAGES", "MODELS", "OUT", "OUT_S2_0", "ROOT", "setup_env", "MIGAN", "MIGAN_DIR", "MIGAN_PATH"]
