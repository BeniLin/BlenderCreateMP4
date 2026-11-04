"""四種 backend 的介面與 registry。

影像一律是 numpy uint8 (H, W, 3) RGB；遮罩是 bool (H, W)，True = 屬於物件 / 要補的區域。
新增 backend：在 backends/ 新增模組並實作下面其中一個介面，再把「名稱 → 模組:類別」加進 BACKENDS。
"""

from __future__ import annotations

import importlib
from typing import Any, Protocol

from animator.errors import AnimatorError


class Detector(Protocol):
    def detect(self, image, labels: list[str]) -> list[dict]:
        """回傳 [{"label", "label_raw", "score", "box": [x0, y0, x1, y1]}]（處理圖的像素座標）。"""


class Segmenter(Protocol):
    def segment(self, image, boxes: list[list[float]]) -> list[Any]:
        """每個框回傳一張 bool (H, W) 遮罩，尺寸和 image 相同。"""


class Refiner(Protocol):
    def refine(self, image, mask) -> tuple[Any, Any, Any]:
        """回傳 (alpha uint8 (H, W), 去色邊後的前景 RGB uint8 (H, W, 3), core bool (H, W))。

        core = 確定是前景的內部區域；圖層在 core 內直接使用原圖像素，只有邊緣帶使用前景色。
        """


class Inpainter(Protocol):
    def inpaint(self, image, hole) -> Any:
        """回傳修補後的 RGB uint8 (H, W, 3)；只應該改動 hole 範圍（含 1~2 px 混合帶）內的像素。"""


BACKENDS: dict[str, dict[str, str]] = {
    "detector": {"grounding_dino": "animator.segmentation.backends.grounding_dino:GroundingDinoDetector"},
    "segmenter": {"sam2": "animator.segmentation.backends.sam2:Sam2Segmenter"},
    "refiner": {
        "pymatting": "animator.segmentation.backends.pymatting_refine:PyMattingRefiner",
        "none": "animator.segmentation.backends.pymatting_refine:HardEdgeRefiner",
    },
    "inpainter": {
        "migan": "animator.segmentation.backends.migan_onnx:MiganInpainter",
        "opencv": "animator.segmentation.backends.opencv_inpaint:OpenCVInpainter",
    },
}


def backend_names(kind: str) -> list[str]:
    return sorted(BACKENDS[kind])


def create(kind: str, name: str, **kwargs):
    """依名稱建立 backend（lazy import，只有真的用到才載入該 backend 的套件）。"""
    try:
        target = BACKENDS[kind][name]
    except KeyError:
        raise AnimatorError(f"不支援的 {kind} backend：{name!r}（可用：{', '.join(backend_names(kind))}）") from None
    module_name, cls_name = target.split(":")
    return getattr(importlib.import_module(module_name), cls_name)(**kwargs)
