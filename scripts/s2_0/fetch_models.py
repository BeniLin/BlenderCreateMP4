"""下載 S2-0 測試用的官方模型到 models/<name>/（鎖定 revision，只下載 safetensors）。

產生 models/s2-0-manifest.json：每個檔案的大小與 SHA-256。
"""

import hashlib
import json
import time

from common import MODELS, MODEL_SPECS, model_dir, setup_env

setup_env(offline=False)

from huggingface_hub import snapshot_download  # noqa: E402


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


manifest = {}
for name, spec in MODEL_SPECS.items():
    target = model_dir(name)
    t0 = time.perf_counter()
    snapshot_download(spec["repo"], revision=spec["revision"], local_dir=target,
                      allow_patterns=spec["allow"])
    elapsed = time.perf_counter() - t0
    files = {}
    for p in sorted(target.rglob("*")):
        if p.is_file() and ".cache" not in p.parts:
            files[str(p.relative_to(target)).replace("\\", "/")] = {
                "bytes": p.stat().st_size, "sha256": sha256(p)}
    total = sum(f["bytes"] for f in files.values())
    manifest[name] = {**spec, "dir": str(target.relative_to(MODELS.parent)).replace("\\", "/"),
                      "total_bytes": total, "download_sec": round(elapsed, 1), "files": files}
    print(f"{name}: {total / 1e6:.1f} MB in {elapsed:.0f}s -> {target}")
    for f, meta in files.items():
        print(f"   {f:35s} {meta['bytes'] / 1e6:9.2f} MB  {meta['sha256'][:16]}…")

(MODELS / "s2-0-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
