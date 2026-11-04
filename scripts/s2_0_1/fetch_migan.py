"""下載 MI-GAN ONNX pipeline（鎖定 revision），並驗證 SHA-256。雜湊不符就刪掉檔案並以非 0 結束。"""

import hashlib
import json
import sys

from common01 import MIGAN, MIGAN_DIR, MIGAN_PATH, MODELS, setup_env

setup_env(offline=False)
from huggingface_hub import hf_hub_download  # noqa: E402


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


MIGAN_DIR.mkdir(parents=True, exist_ok=True)
path = hf_hub_download(repo_id=MIGAN["repo"], filename=MIGAN["filename"], revision=MIGAN["revision"],
                       local_dir=MIGAN_DIR)
got = sha256(path)
ok = got == MIGAN["sha256"]
print("path", path)
print("size", MIGAN_PATH.stat().st_size)
print("sha256", got)
print("expected", MIGAN["sha256"])
print("MATCH" if ok else "MISMATCH")
if not ok:
    MIGAN_PATH.unlink()
    sys.exit(2)
(MODELS / "s2-0.1-manifest.json").write_text(json.dumps(
    {**MIGAN, "size_bytes": MIGAN_PATH.stat().st_size, "sha256_verified": got}, indent=2), encoding="utf-8")
