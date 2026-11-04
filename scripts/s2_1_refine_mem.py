"""S2-1 量測：PyMatting 精修在大圖 / 大物件時的記憶體與時間（每個案例一個子行程）。

    .venv\\Scripts\\python.exe scripts\\s2_1_refine_mem.py              # 全部案例
    .venv\\Scripts\\python.exe scripts\\s2_1_refine_mem.py child W H R  # 單一案例（內部使用）

案例：照片（photo_desk 放大）上的一個橢圓物件，佔畫面 R 比例；輸出 output/s2-1-refine-mem.json。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "s2_0"))

CASES = [(1024, 768, 0.3), (1920, 1440, 0.3), (1920, 1440, 0.6), (3840, 2160, 0.3), (3840, 2160, 0.6)]


def child(w: int, h: int, ratio: float) -> dict:
    import os

    os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / "models" / ".numba_cache"))
    import numpy as np
    from PIL import Image

    import memprobe

    probe = memprobe.Probe()
    with probe.phase("import"):
        from animator.segmentation.backends.pymatting_refine import PyMattingRefiner
        ref = PyMattingRefiner(band_px=8)
    img = np.asarray(Image.open(ROOT / "assets" / "s2-0" / "photo_desk.jpg").convert("RGB").resize((w, h)))
    yy, xx = np.mgrid[0:h, 0:w]
    a, b = (w / 2) * ratio ** 0.5 * 1.13, (h / 2) * ratio ** 0.5 * 1.13
    mask = ((xx - w / 2) / a) ** 2 + ((yy - h / 2) / b) ** 2 < 1
    t0 = time.perf_counter()
    with probe.phase("refine"):
        ref.refine(img, mask)
    probe.stop()
    p = probe.phases["refine"]
    return {"size": [w, h], "object_ratio": round(float(mask.mean()), 3), "sec": round(time.perf_counter() - t0, 2),
            "ws_peak_mb": p["ws_peak_mb"], "private_peak_mb": p["private_peak_mb"],
            "available_gb": memprobe.system_ram()["available_gb"]}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        print(json.dumps(child(int(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4]))))
    else:
        out = []
        for w, h, r in CASES:
            r_ = subprocess.run([sys.executable, __file__, "child", str(w), str(h), str(r)],
                                capture_output=True, text=True, timeout=1800)
            line = r_.stdout.strip().splitlines()[-1] if r_.returncode == 0 else None
            res = json.loads(line) if line else {"size": [w, h], "ratio": r, "error": r_.stderr[-600:]}
            out.append(res)
            print(res, flush=True)
        (ROOT / "output" / "s2-1-refine-mem.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
