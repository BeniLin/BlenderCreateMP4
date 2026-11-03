"""spec 的 easing 名稱 → Blender 關鍵影格的 (interpolation, easing)。"""

_MODES = {"in": "EASE_IN", "out": "EASE_OUT", "in_out": "EASE_IN_OUT"}
_CURVES = {"sine", "quad", "cubic", "quart", "quint", "expo", "circ", "back", "bounce", "elastic"}


def to_blender(name):
    if name == "linear":
        return "LINEAR", "AUTO"
    if name == "constant":
        return "CONSTANT", "AUTO"
    if name in ("ease_in", "ease_out", "ease_in_out"):
        return "CUBIC", _MODES[name[len("ease_"):]]
    curve, _, mode = name.partition("_")
    if curve not in _CURVES or mode not in _MODES:
        raise ValueError(f"不支援的 easing：{name!r}")
    return curve.upper(), _MODES[mode]
