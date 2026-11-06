"""visibility：離散的顯示 / 隱藏（1 / 0），和 opacity 互相獨立。

系統端已經把區間換算好：from = 區間裡的狀態，to = 區間結束後的狀態，easing 一律 "constant"，
所以兩個關鍵影格都是 CONSTANT（tracks.py 明確設定，不依賴 Blender 的偏好設定）。
"""

from anim_bl.effects import register


@register("visibility", channel="visibility")
def apply(obj, anim, ctx):
    ctx.tracks.add(obj, "visibility", anim["frame_start"], anim["frame_end"],
                   anim["from"], anim["to"], "constant")
