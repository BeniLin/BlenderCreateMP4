"""S2-1 demo：在 `animator segment` 產生的 spec.json 加上簡單的動畫（只用標準函式庫）。

每個物件圖層：先往左右移開再回到原位（move），背景不動 → 可以看出背景已經補好、圖層可以獨立移動。

    .venv\\Scripts\\python.exe scripts\\s2_1_demo_animate.py output\\<job_id>\\spec.json
    .\\animator.cmd render output\\<job_id>\\spec.json --preview
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def add_demo_animation(spec_path: Path) -> int:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    width = spec["canvas"]["width"]
    duration = spec["canvas"]["duration"]
    objects = [layer for layer in spec["layers"] if layer["id"] != "background"]
    anims = []
    mid, end = duration * 0.5, duration * 0.9
    for i, layer in enumerate(objects):
        dx = round(width * 0.12) * (1 if i % 2 == 0 else -1)
        start = min(duration * (0.05 + 0.05 * i), duration * 0.3)   # 依序錯開開始時間
        anims.append({"id": f"demo-out-{layer['id']}", "target": layer["id"], "effect": "move",
                      "start": start, "end": mid, "easing": "ease_in_out", "params": {"by": [dx, 0]}})
        anims.append({"id": f"demo-back-{layer['id']}", "target": layer["id"], "effect": "move",
                      "start": mid, "end": end, "easing": "ease_in_out", "params": {"by": [-dx, 0]}})
    spec["animations"] = anims
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(anims)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    n = add_demo_animation(Path(sys.argv[1]))
    print(f"已加入 {n} 段動畫：{sys.argv[1]}")
