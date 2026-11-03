"""spec.json v1 的常數與共用資料結構（規格見 docs/spec.md）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

SPEC_VERSION = 1

# 效果名稱 → 控制的通道
EFFECT_CHANNELS: dict[str, str] = {
    "move": "position",
    "scale": "scale",
    "rotate": "rotation",
    "opacity": "opacity",
}

# 別名 → 實際效果（resolve 時展開）
EFFECT_ALIASES: dict[str, str] = {"fade_in": "opacity", "fade_out": "opacity"}

# 保留名稱：寫了會報「尚未支援」
RESERVED_EFFECTS = frozenset({
    "glow", "shadow", "tint", "light_sweep", "particles",
    "camera_move", "camera_zoom", "shake", "float", "wiggle",
})

EASING_CURVES = ("sine", "quad", "cubic", "quart", "quint", "expo", "circ", "back", "bounce", "elastic")
EASING_MODES = ("in", "out", "in_out")
EASINGS = frozenset(
    {"linear", "constant", "ease_in", "ease_out", "ease_in_out"}
    | {f"{c}_{m}" for c in EASING_CURVES for m in EASING_MODES}
)
DEFAULT_EASING = "ease_in_out"

ENGINES = ("eevee", "cycles")

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

TOP_KEYS = {"version", "meta", "canvas", "layers", "animations", "render", "output"}
CANVAS_KEYS = {"width", "height", "fps", "duration", "background_color"}
LAYER_KEYS = {"id", "image", "position", "anchor", "z", "scale", "rotation", "opacity", "visible", "meta"}
LAYER_RESERVED = {"parent", "blend_mode", "style"}
ANIM_KEYS = {"id", "target", "effect", "start", "end", "easing", "params", "enabled"}
RENDER_KEYS = {"engine", "samples", "resolution_percentage", "view_transform", "film_transparent"}
OUTPUT_KEYS = {"filename", "codec", "crf", "preset", "pix_fmt"}

PARAM_KEYS = {
    "move": {"to", "by", "from"},
    "scale": {"to", "from"},
    "rotate": {"to", "by", "from"},
    "opacity": {"to", "from"},
    "fade_in": {"to", "from"},
    "fade_out": {"to", "from"},
}

EPS = 1e-9


@dataclass(frozen=True)
class Issue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def effect_channel(effect: str) -> str | None:
    effect = EFFECT_ALIASES.get(effect, effect)
    return EFFECT_CHANNELS.get(effect)


def time_to_frame(t: float, fps: int, frame_end: int) -> int:
    """docs/spec.md 2.4：frame(t) = 1 + round(t × fps)，限制在 [1, frame_end]。"""
    return max(1, min(frame_end, 1 + round(t * fps)))


def frame_count(duration: float, fps: int) -> int:
    return max(1, round(duration * fps))
