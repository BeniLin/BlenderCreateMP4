"""Inpainter：MI-GAN 官方 ONNX pipeline（onnxruntime，CPU）。

S2-0.1 的結論（docs/stage2.md「S2-0.1 結果」）：
- 遮罩極性：255 = 保留、0 = 要補
- 輸入要用原圖（hole 內的原內容不會被使用，但不能塗成單一顏色，否則顏色會滲進補出來的區域）
- pipeline 會依遮罩的 bbox 裁切後縮到 512 → 呼叫端要把不相連的區域分開補
- 關掉 memory arena 可以省一半記憶體，warm 速度不變
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

WEIGHTS = "migan_pipeline_v2.onnx"


def keep_mask(hole: np.ndarray) -> np.ndarray:
    """MI-GAN 的遮罩：255 = 保留（已知區域）、0 = 要補。"""
    return np.where(hole, 0, 255).astype(np.uint8)


class MiganInpainter:
    def __init__(self, model_dir: Path, threads: int = 4, mem_arena: bool = False):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.enable_cpu_mem_arena = mem_arena
        self.session = ort.InferenceSession(str(Path(model_dir) / WEIGHTS), sess_options=opts,
                                            providers=["CPUExecutionProvider"])

    def inpaint(self, image: np.ndarray, hole: np.ndarray) -> np.ndarray:
        img = np.ascontiguousarray(image.transpose(2, 0, 1)[None])
        mask = keep_mask(hole)[None, None]
        out = self.session.run(None, {"image": img, "mask": mask})[0][0].transpose(1, 2, 0)
        return np.ascontiguousarray(out)
