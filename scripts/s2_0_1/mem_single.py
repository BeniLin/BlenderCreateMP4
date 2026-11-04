"""單張圖片的記憶體量測：每個（尺寸, arena 開/關）在獨立 process 裡只跑 1 張圖 × 2 次推論。

    .venv\\Scripts\\python.exe scripts\\s2_0_1\\mem_single.py          # 跑全部組合，寫 output/s2-0.1/mem_single.json
"""

import json
import subprocess
import sys
import time

from common01 import MIGAN_PATH, OUT, setup_env

setup_env(offline=True)

import memprobe  # noqa: E402

CASES = [(size, arena) for size in ("512", "1024", "orig") for arena in (True, False)]


def child(size: str, arena: bool) -> dict:
    import numpy as np

    sys.argv = sys.argv[:1]
    from bench01 import load_images, make_masks  # 只用來產生同一組圖片 / 遮罩

    probe = memprobe.Probe()
    with probe.phase("import"):
        import onnxruntime as ort
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    opts.inter_op_num_threads = 1
    opts.enable_cpu_mem_arena = arena
    with probe.phase("load"):
        sess = ort.InferenceSession(str(MIGAN_PATH), sess_options=opts, providers=["CPUExecutionProvider"])
    s = size if size == "orig" else int(size)
    image = load_images()[("photo_desk", s)]
    hole = make_masks("photo_desk", image)["obj_d24"]
    img = np.ascontiguousarray(np.asarray(image).transpose(2, 0, 1)[None])
    mask = np.where(hole, 0, 255).astype(np.uint8)[None, None]
    times = []
    with probe.phase("infer_x2"):
        for _ in range(2):
            t0 = time.perf_counter()
            sess.run(None, {"image": img, "mask": mask})
            times.append(round(time.perf_counter() - t0, 3))
    probe.stop()
    return {"size": size, "image_wh": list(image.size), "arena": arena, "sec": times, "phases": probe.phases}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        print(json.dumps(child(sys.argv[2], sys.argv[3] == "1")))
    else:
        out = []
        for size, arena in CASES:
            r = subprocess.run([sys.executable, __file__, "child", size, "1" if arena else "0"],
                               capture_output=True, text=True, check=True)
            out.append(json.loads(r.stdout.strip().splitlines()[-1]))
        (OUT / "mem_single.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
        for r in out:
            p = r["phases"]["infer_x2"]
            print(r["size"], r["image_wh"], "arena" if r["arena"] else "no-arena", r["sec"],
                  "ws_peak", p["ws_peak_mb"], "private_peak", p["private_peak_mb"])
