"""Inpainter：OpenCV Telea（備援；只適合小洞，S2-0 / S2-0.1 實測大洞會留下明顯的拉絲）。"""

from __future__ import annotations

import numpy as np


class OpenCVInpainter:
    def __init__(self, radius: int = 3, **_):
        self.radius = radius

    def inpaint(self, image: np.ndarray, hole: np.ndarray) -> np.ndarray:
        import cv2

        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        out = cv2.inpaint(bgr, hole.astype(np.uint8) * 255, self.radius, cv2.INPAINT_TELEA)
        return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
