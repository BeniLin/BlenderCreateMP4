"""確認官方 Sam2Processor 能載入，以及官方權重載入 Sam2Model 時沒有缺漏的權重。"""

from common import model_dir, setup_env

setup_env(offline=True)

import json  # noqa: E402

from transformers import Sam2Model, Sam2Processor  # noqa: E402

path = model_dir("sam2.1-hiera-tiny")
cfg = json.loads((path / "config.json").read_text(encoding="utf-8"))
print("config model_type:", cfg.get("model_type"), "| architectures:", cfg.get("architectures"))

processor = Sam2Processor.from_pretrained(path, local_files_only=True)
print("processor:", type(processor).__name__, "| image_processor:", type(processor.image_processor).__name__)

model, info = Sam2Model.from_pretrained(path, local_files_only=True, output_loading_info=True)
for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"):
    items = list(info.get(key, []))
    print(f"{key}: {len(items)}", items[:8])
print("params (M):", round(sum(p.numel() for p in model.parameters()) / 1e6, 2))
