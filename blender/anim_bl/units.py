"""畫布像素座標 ↔ Blender 世界座標。

畫布：原點左上、x 往右、y 往下、單位 px（docs/spec.md 2.1）。
Blender：畫布中心 = 世界原點；圖層位於 XZ 平面（+X 往右、+Z 往上）；
         攝影機位於 -Y 往 +Y 看；圖層的前後順序用 Y 深度表示（越前面 Y 越小）。
"""

import math

PX_PER_UNIT = 100.0      # 100 px = 1 Blender 單位
LAYER_SPACING = 0.01     # 相鄰兩個圖層的深度間隔（BU）
CAMERA_DISTANCE = 50.0


class Units:
    def __init__(self, width, height):
        self.width = width
        self.height = height

    @property
    def ortho_scale(self):
        return max(self.width, self.height) / PX_PER_UNIT

    def length(self, px):
        return px / PX_PER_UNIT

    def to_xz(self, x, y):
        """畫布像素座標 → (X, Z)。"""
        return ((x - self.width / 2) / PX_PER_UNIT, (self.height / 2 - y) / PX_PER_UNIT)

    @staticmethod
    def depth(rank):
        return -rank * LAYER_SPACING

    @staticmethod
    def rotation(deg):
        """順時針為正（螢幕座標）→ 繞 +Y 軸的弧度（從 -Y 看過去也是順時針）。"""
        return math.radians(deg)
