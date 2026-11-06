"""把各效果登記的「區段」寫成關鍵影格。

每個 (物件, 通道) 是一條軌道；區段之間的空檔保持前一段的結束值（CONSTANT），
第一段開始之前保持圖層的初始值（docs/spec.md 3.4 時間軸規則）。
"""

from collections import defaultdict

from anim_bl import compat
from anim_bl.easing import to_blender

# 通道 → 物件屬性的 (data_path, index) 分量
CHANNEL_PATHS = {
    "position": [("location", 0), ("location", 2)],
    "scale": [("scale", 0), ("scale", 2)],
    "rotation": [("rotation_euler", 1)],
    "opacity": [("color", 3)],
    "visibility": [("color", 0)],    # 1 = 顯示、0 = 隱藏；材質把 alpha 乘上 Object Info Color 的 R（layers.py）
}


class Tracks:
    def __init__(self, frame_start):
        self.frame_start = frame_start
        self._segments = defaultdict(list)   # (obj name, channel) -> [segment]
        self._objects = {}

    def add(self, obj, channel, frame_start, frame_end, value_from, value_to, easing):
        if channel not in CHANNEL_PATHS:
            raise ValueError(f"未知的通道：{channel}")
        if len(value_from) != len(CHANNEL_PATHS[channel]) or len(value_to) != len(value_from):
            raise ValueError(f"{channel} 的值維度錯誤：{value_from} → {value_to}")
        self._objects[obj.name] = obj
        self._segments[(obj.name, channel)].append(
            (frame_start, frame_end, tuple(value_from), tuple(value_to), easing))

    def build(self):
        count = 0
        for (name, channel), segments in self._segments.items():
            obj = self._objects[name]
            segments.sort(key=lambda s: s[0])
            for component, (path, index) in enumerate(CHANNEL_PATHS[channel]):
                initial = getattr(obj, path)[index]
                fcurve = compat.fcurve_for(obj, path, index)
                if segments[0][0] > self.frame_start:
                    key = fcurve.keyframe_points.insert(self.frame_start, initial)
                    key.interpolation = "CONSTANT"
                for f0, f1, v0, v1, easing in segments:
                    interpolation, mode = to_blender(easing)
                    key = fcurve.keyframe_points.insert(f0, v0[component])
                    key.interpolation = interpolation
                    key.easing = mode
                    key = fcurve.keyframe_points.insert(f1, v1[component])
                    key.interpolation = "CONSTANT"  # 下一段開始前保持結束值
                fcurve.update()
                count += len(fcurve.keyframe_points)
        return count
