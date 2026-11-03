"""rotate：以 anchor 為中心旋轉（度，順時針為正）。"""

from anim_bl.effects import register


@register("rotate", channel="rotation")
def apply(obj, anim, ctx):
    rot = ctx.units.rotation
    ctx.tracks.add(obj, "rotation", anim["frame_start"], anim["frame_end"],
                   [rot(anim["from"][0])], [rot(anim["to"][0])], anim["easing"])
