"""Refiner：PyMatting（closed-form alpha + 前景色估計）；以及不精修的 HardEdgeRefiner。

trimap：內側只往內縮 band/3（避免細長的部位整條變成半透明，S2-0 的觀察），外側外擴 band。
"""

from __future__ import annotations

import numpy as np


def trimap_for(mask: np.ndarray, band_px: int) -> tuple[np.ndarray, np.ndarray]:
    from animator.segmentation.compose import dilate, erode

    core = erode(mask, max(1, band_px // 3))
    outer = dilate(mask, band_px)
    tri = np.zeros(mask.shape, np.float64)
    tri[outer] = 0.5
    tri[core] = 1.0
    return tri, core


TILE_PX = 1024      # 每個 tile 的最大邊長：記憶體上限約 0.5~0.6 GB（working set），和圖片 / 物件大小無關
TILE_MARGIN = 32    # tile 之間重疊的上下文（closed-form matting 只看 3x3 的局部視窗）


def tiles(box: tuple[int, int, int, int], tile: int, margin: int, shape: tuple[int, int]):
    """把 box 切成 tile×tile 的內部區塊；回傳 (內部區塊, 外加 margin 的計算視窗)。"""
    x0, y0, x1, y1 = box
    h, w = shape
    for ty in range(y0, y1, tile):
        for tx in range(x0, x1, tile):
            inner = (tx, ty, min(tx + tile, x1), min(ty + tile, y1))
            outer = (max(0, inner[0] - margin), max(0, inner[1] - margin),
                     min(w, inner[2] + margin), min(h, inner[3] + margin))
            yield inner, outer


class PyMattingRefiner:
    def __init__(self, band_px: int = 8, tile_px: int = TILE_PX):
        from pymatting import estimate_alpha_cf, estimate_foreground_ml

        self.band_px, self.tile_px = band_px, tile_px
        self._alpha, self._fg = estimate_alpha_cf, estimate_foreground_ml

    def refine(self, image: np.ndarray, mask: np.ndarray):
        """回傳 (alpha uint8, 前景 RGB uint8, core bool)，都是原圖尺寸。

        只在含有「未知帶」的 tile 裡計算（每個 tile ≤ tile_px，加上 TILE_MARGIN 的重疊），
        所以記憶體用量有上限；大物件的內部（全部是確定前景）不需要計算。
        """
        from animator.errors import AnimatorError
        from animator.segmentation.compose import bbox

        h, w = mask.shape
        tri_full, core = trimap_for(mask, self.band_px)
        alpha = np.where(tri_full == 1.0, 255, 0).astype(np.uint8)
        fg = image.copy()
        b = bbox(tri_full == 0.5)
        if b is None:
            return alpha, fg, core
        for (ix0, iy0, ix1, iy1), (ox0, oy0, ox1, oy1) in tiles(tuple(b), self.tile_px, TILE_MARGIN, (h, w)):
            tri = tri_full[oy0:oy1, ox0:ox1]
            inner_tri = tri_full[iy0:iy1, ix0:ix1]
            if not (inner_tri == 0.5).any():
                continue
            rgb = image[oy0:oy1, ox0:ox1].astype(np.float64) / 255.0
            try:
                a = self._alpha(rgb, tri)
                f = self._fg(rgb, a)
            except MemoryError as exc:
                raise AnimatorError(
                    f"PyMatting 記憶體不足（tile {ox1 - ox0}x{oy1 - oy0}）：請關閉其他程式、"
                    f"或改用 --no-refine（refine.backend = none）") from exc
            sy, sx = slice(iy0 - oy0, iy1 - oy0), slice(ix0 - ox0, ix1 - ox0)
            band = inner_tri == 0.5
            alpha_tile = (a[sy, sx] * 255).round().clip(0, 255).astype(np.uint8)
            fg_tile = (f[sy, sx] * 255).round().clip(0, 255).astype(np.uint8)
            alpha[iy0:iy1, ix0:ix1][band] = alpha_tile[band]
            fg[iy0:iy1, ix0:ix1][band] = fg_tile[band]
        alpha[core] = 255
        return alpha, fg, core


class HardEdgeRefiner:
    """不精修：alpha = 遮罩本身（refine.backend = none）。"""

    def __init__(self, band_px: int = 0):
        self.band_px = band_px

    def refine(self, image: np.ndarray, mask: np.ndarray):
        return (mask.astype(np.uint8) * 255), image.copy(), mask.copy()
