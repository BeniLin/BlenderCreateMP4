"""move：移動圖層的 anchor 點（畫布像素座標）。"""

from anim_bl.effects import register


@register("move", channel="position")
def apply(obj, anim, ctx):
    ctx.tracks.add(obj, "position", anim["frame_start"], anim["frame_end"],
                   ctx.units.to_xz(*anim["from"]), ctx.units.to_xz(*anim["to"]), anim["easing"])
