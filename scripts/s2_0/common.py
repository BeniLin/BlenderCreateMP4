"""S2-0 可行性測試共用設定（不是正式的 Stage 2 程式碼）。

- 所有模型與 Hugging Face 快取都放在專案的 models/ 底下，不使用使用者家目錄
- 模型鎖定 revision（commit hash），只下載 safetensors + 設定檔
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
HF_HOME = MODELS / ".hf_home"
OUT = ROOT / "output" / "s2-0"
IMAGES = ROOT / "assets" / "s2-0"


def setup_env(offline: bool = True) -> None:
    """在 import transformers / huggingface_hub 之前呼叫。"""
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TORCH_HOME"] = str(MODELS / ".torch")
    os.environ["NUMBA_CACHE_DIR"] = str(MODELS / ".numba_cache")
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


# 只下載這些檔案（避免同時下載 .bin / .pt 等重複權重）
MODEL_SPECS = {
    "grounding-dino-tiny": {
        "repo": "IDEA-Research/grounding-dino-tiny",
        "revision": "a2bb814dd30d776dcf7e30523b00659f4f141c71",   # 2024-05-12
        "allow": ["*.json", "vocab.txt", "README.md", "model.safetensors"],
        "license": "apache-2.0",   # 來源見 docs/stage2.md「S2-0 結果」的授權表
    },
    "sam2.1-hiera-tiny": {
        "repo": "facebook/sam2.1-hiera-tiny",
        "revision": "de431c4043854a71d8101e17995dfe596bf101a5",   # 2025-08-15
        "allow": ["*.json", "README.md", "model.safetensors"],
        "license": "apache-2.0",
    },
}


def model_dir(name: str) -> Path:
    return MODELS / name
