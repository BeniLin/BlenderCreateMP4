"""把 output/s2-0/worker_*.json 整理成表格（Markdown），輸出到 stdout。"""

import json
import sys

from common import OUT

names = sys.argv[1:] or ["gdino", "sam2", "pymatting", "opencv", "sequence"]
for name in names:
    f = OUT / f"worker_{name}.json"
    if not f.is_file():
        print(f"## {name}: （沒有結果）\n")
        continue
    d = json.loads(f.read_text(encoding="utf-8"))
    print(f"## {name}  status={d['status']}  total={d['total_sec']}s  threads={d.get('torch_threads')}"
          f"  gpu={d.get('gpu_used_mb')}")
    print("| phase | sec | WS before | WS peak | WS after | private peak |")
    print("|---|---:|---:|---:|---:|---:|")
    for ph, v in d["phases"].items():
        print(f"| {ph} | {v['sec']} | {v['ws_before_mb']} | {v['ws_peak_mb']} | {v['ws_after_mb']} | {v['private_peak_mb']} |")
    print()
