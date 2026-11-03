"""scale：以 anchor 為中心縮放（[sx, sy] 倍率）。"""

from anim_bl.effects import register


@register("scale", channel="scale")
def apply(obj, anim, ctx):
    ctx.tracks.add(obj, "scale", anim["frame_start"], anim["frame_end"],
                   anim["from"], anim["to"], anim["easing"])
