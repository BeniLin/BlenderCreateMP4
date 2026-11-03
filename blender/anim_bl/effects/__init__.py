"""動畫效果 registry。

新增效果：在本目錄新增 <name>.py，用 @register("<name>", channel="<channel>") 註冊
apply(obj, anim, ctx)，並在下方 import。系統端也要在 src/animator/spec/ 補上驗證。

anim 是 spec.resolved.json 裡的一筆動畫（from/to 已推算好，時間已換算成影格）。
ctx.units 負責單位換算，ctx.tracks.add(...) 登記關鍵影格區段。
"""

from dataclasses import dataclass

_REGISTRY = {}


@dataclass
class EffectContext:
    units: object
    tracks: object


def register(name, channel):
    def decorator(fn):
        if name in _REGISTRY:
            raise RuntimeError(f"效果重複註冊：{name}")
        _REGISTRY[name] = (channel, fn)
        return fn
    return decorator


def get(name):
    if name not in _REGISTRY:
        raise KeyError(f"Blender 端沒有註冊效果 {name!r}（已註冊：{sorted(_REGISTRY)}）")
    return _REGISTRY[name]


def names():
    return sorted(_REGISTRY)


def apply(obj, anim, ctx):
    channel, fn = get(anim["effect"])
    if channel != anim["channel"]:
        raise ValueError(f"{anim['id']}：效果 {anim['effect']} 的通道是 {channel}，spec 寫的是 {anim['channel']}")
    fn(obj, anim, ctx)


# 匯入以完成註冊（放在最後，避免循環 import）
from anim_bl.effects import move, opacity, rotate, scale  # noqa: E402,F401
