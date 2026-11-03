"""opacity：圖層透明度（0~1）。fade_in / fade_out 已在系統端展開為 opacity。"""

from anim_bl.effects import register


@register("opacity", channel="opacity")
def apply(obj, anim, ctx):
    ctx.tracks.add(obj, "opacity", anim["frame_start"], anim["frame_end"],
                   anim["from"], anim["to"], anim["easing"])
