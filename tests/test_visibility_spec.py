"""S2-2 M3c：visibility 通道的驗證與 resolve（Stage 1 spec；只用標準函式庫）。

用 examples/basic（1280×720、30 fps、4 秒 → 120 格；frame(t) = 1 + round(t × fps)，限制在 1..120）。
"""

from __future__ import annotations

import copy
import json

import pytest

from animator.errors import SpecError
from animator.spec import schema as S
from animator.spec.resolve import resolve
from animator.spec.validate import validate
from conftest import EXAMPLE, ROOT, write_spec

VIS_EXAMPLE = ROOT / "examples" / "visibility"


def _vis(target, start, end, visible, **extra):
    return {"id": f"{target}-vis-{start}", "target": target, "effect": "visibility", "start": start, "end": end,
            "params": {"visible": visible}, **extra}


def _with(example_raw, *anims, **layer_fields):
    raw = copy.deepcopy(example_raw)
    raw["animations"].extend(anims)
    for layer_id, fields in layer_fields.items():
        next(l for l in raw["layers"] if l["id"] == layer_id).update(fields)
    return raw


def _errors(raw):
    return [str(e) for e in validate(raw, EXAMPLE).errors]


def _warnings(raw):
    return [str(w) for w in validate(raw, EXAMPLE).warnings]


def _resolve(raw, make_config, spec_dir):
    return resolve(raw, write_spec(spec_dir, raw), make_config())


def _vis_anims(res, target=None):
    return [a for a in res["animations"] if a["channel"] == "visibility" and (target is None or a["target"] == target)]


# ---------------------------------------------------------------- 舊的 spec

def test_existing_example_resolves_with_base_visibility_only(example_raw, make_config, spec_dir):
    res = _resolve(example_raw, make_config, spec_dir)
    assert [l["visibility"] for l in res["layers"]] == [1.0] * 6
    assert _vis_anims(res) == []
    assert {a["id"] for a in res["animations"]} == {a["id"] for a in example_raw["animations"]}
    assert {"visibility"} | {"id", "image", "size", "position", "anchor", "z", "scale", "rotation", "opacity",
                             "rank"} == set(res["layers"][0])       # 只多了 visibility


def test_visible_omitted_and_true_are_identical(example_raw, make_config, spec_dir):
    raw = copy.deepcopy(example_raw)
    for layer in raw["layers"]:
        layer["visible"] = True
    assert _resolve(raw, make_config, spec_dir) == _resolve(example_raw, make_config, spec_dir)


@pytest.mark.parametrize("extra", [[], [_vis("cloud", 1.0, 2.0, True, enabled=False)]])
def test_hidden_layer_without_enabled_visibility_is_still_dropped(example_raw, make_config, spec_dir, extra):
    raw = _with(example_raw, *extra, cloud={"visible": False})
    res = _resolve(raw, make_config, spec_dir)
    assert "cloud" not in [l["id"] for l in res["layers"]]
    assert all(a["target"] != "cloud" for a in res["animations"])   # 它的動畫也一起移除（和以前一樣）


# ---------------------------------------------------------------- 正常的 visibility

def test_hide_window_resolves_to_discrete_segment(example_raw, make_config, spec_dir):
    raw = _with(example_raw, _vis("bar", 1.0, 2.0, False))
    assert validate(raw, EXAMPLE).ok and _warnings(raw) == []
    seg, = _vis_anims(_resolve(raw, make_config, spec_dir))
    assert seg == {"id": "bar-vis-1.0", "target": "bar", "effect": "visibility", "source_effect": "visibility",
                   "channel": "visibility", "start": 1.0, "end": 2.0, "frame_start": 31, "frame_end": 61,
                   "easing": "constant", "from": [0.0], "to": [1.0]}   # 31..60 隱藏，61 恢復


def test_explicit_constant_easing_is_accepted(example_raw):
    assert validate(_with(example_raw, _vis("bar", 1.0, 2.0, False, easing="constant")), EXAMPLE).ok


def test_hidden_layer_with_visibility_is_retained_with_its_animations(example_raw, make_config, spec_dir):
    raw = _with(example_raw, _vis("cloud", 1.0, 2.0, True), cloud={"visible": False})
    res = _resolve(raw, make_config, spec_dir)
    cloud = next(l for l in res["layers"] if l["id"] == "cloud")
    assert cloud["visibility"] == 0.0
    assert {a["id"] for a in res["animations"] if a["target"] == "cloud"} == {"cloud-drift", "cloud-out", "cloud-vis-1.0"}
    seg, = _vis_anims(res, "cloud")
    assert (seg["from"], seg["to"]) == ([1.0], [0.0])
    assert [l["rank"] for l in res["layers"]] == list(range(6))


# ---------------------------------------------------------------- 拒絕

@pytest.mark.parametrize("params, match", [
    ({}, "params.visible: 必填"),
    ({"visible": "yes"}, "必須是 true 或 false"),
    ({"visible": 1}, "必須是 true 或 false"),
    ({"visible": False, "to": True}, "params.to: visibility 只接受 visible"),
    ({"visible": False, "from": True}, "params.from: visibility 只接受 visible"),
    ({"visible": False, "by": 1}, "params.by: visibility 只接受 visible"),
])
def test_invalid_params_are_errors(example_raw, params, match):
    anim = {**_vis("bar", 1.0, 2.0, False), "params": params}
    assert any(match in e for e in _errors(_with(example_raw, anim))), _errors(_with(example_raw, anim))


@pytest.mark.parametrize("easing", ["linear", "ease_in_out", "sine_in"])
def test_non_constant_easing_is_rejected(example_raw, easing):
    errs = _errors(_with(example_raw, _vis("bar", 1.0, 2.0, False, easing=easing)))
    assert any("easing 只能省略或寫 \"constant\"" in e for e in errs), errs


def test_overlapping_windows_are_rejected(example_raw):
    errs = _errors(_with(example_raw, _vis("bar", 1.0, 2.0, False), _vis("bar", 1.5, 2.5, False)))
    assert any("visibility 通道上時間重疊" in e for e in errs), errs


def test_window_shorter_than_one_frame_is_rejected(example_raw):
    errs = _errors(_with(example_raw, _vis("bar", 1.0, 1.01, False)))      # frame 31 → 31
    assert any("不到 1 格" in e for e in errs), errs


def test_resolve_raises_on_invalid_visibility(example_raw, make_config, spec_dir):
    with pytest.raises(SpecError):
        _resolve(_with(example_raw, _vis("bar", 1.0, 2.0, False, easing="linear")), make_config, spec_dir)


# ---------------------------------------------------------------- 邊界：[start, end) 與最後一格

def test_window_ending_before_the_last_frame_restores_at_frame_end(example_raw, make_config, spec_dir):
    seg, = _vis_anims(_resolve(_with(example_raw, _vis("bar", 3.0, 3.9, False)), make_config, spec_dir))
    assert (seg["frame_start"], seg["frame_end"], seg["to"]) == (91, 118, [1.0])   # 118 恢復（沒有差一格）


@pytest.mark.parametrize("end", [4.0, 3.99])
def test_window_reaching_the_end_stays_on_the_last_frame(example_raw, make_config, spec_dir, end):
    """end = duration（或落在最後一格之後）：最後一格（120）也維持隱藏，不插入恢復的關鍵影格。"""
    assert S.window_end_frame(end, 30, 120) == 121
    seg, = _vis_anims(_resolve(_with(example_raw, _vis("bar", 3.0, end, False)), make_config, spec_dir))
    assert (seg["frame_start"], seg["frame_end"], seg["from"], seg["to"]) == (91, 120, [0.0], [0.0])


def test_window_covering_only_the_last_frame(example_raw, make_config, spec_dir):
    raw = _with(example_raw, _vis("bar", 119 / 30, 4.0, False))
    assert validate(raw, EXAMPLE).ok                                  # 第 120 格：1 格
    seg, = _vis_anims(_resolve(raw, make_config, spec_dir))
    assert (seg["frame_start"], seg["frame_end"], seg["to"]) == (120, 120, [0.0])
    other = copy.deepcopy(raw)                                        # 其他效果同樣的時間仍然不到 1 格（規則不變）
    other["animations"][-1] = {"id": "x", "target": "bar", "effect": "opacity", "start": 119 / 30, "end": 4.0,
                               "params": {"to": 0.5}}
    assert any("不到 1 格" in e for e in _errors(other))


def test_back_to_back_windows_switch_without_restoring(example_raw, make_config, spec_dir):
    raw = _with(example_raw, _vis("bar", 1.0, 2.0, False), _vis("bar", 2.0, 3.0, False))
    assert validate(raw, EXAMPLE).ok                                  # 頭尾相接可以
    first, second = _vis_anims(_resolve(raw, make_config, spec_dir))
    assert (first["frame_end"], first["to"]) == (61, [0.0])           # 61 直接是下一段的狀態，不先恢復
    assert (second["frame_start"], second["from"], second["to"]) == (61, [0.0], [1.0])


def test_back_to_back_hide_then_show_on_hidden_layer(example_raw, make_config, spec_dir):
    raw = _with(example_raw, _vis("cloud", 1.0, 2.0, True), _vis("cloud", 2.0, 3.0, False),
                _vis("cloud", 3.0, 4.0, True), cloud={"visible": False})
    res = _resolve(raw, make_config, spec_dir)
    segs = _vis_anims(res, "cloud")                                  # 中間那段（= 基本狀態）沒有效果
    assert [(s["frame_start"], s["frame_end"], s["from"], s["to"]) for s in segs] == \
        [(31, 61, [1.0], [0.0]), (91, 120, [1.0], [1.0])]
    assert any("沒有效果" in w for w in _warnings(raw))


# ---------------------------------------------------------------- 沒有效果、和其他通道一起使用

@pytest.mark.parametrize("layer_fields, visible", [({}, True), ({"cloud": {"visible": False}}, False)])
def test_no_op_window_warns_and_produces_no_segment(example_raw, make_config, spec_dir, layer_fields, visible):
    anims = [_vis("cloud", 1.0, 2.0, visible)]
    if not visible:
        anims.append(_vis("cloud", 3.0, 3.5, True))                   # 讓隱藏的圖層保留下來
    raw = _with(example_raw, *anims, **layer_fields)
    assert validate(raw, EXAMPLE).ok
    assert any("cloud" in w and "沒有效果" in w for w in _warnings(raw))
    assert all(s["start"] != 1.0 for s in _vis_anims(_resolve(raw, make_config, spec_dir), "cloud"))


def test_visibility_with_opacity_move_scale_rotate(example_raw, make_config, spec_dir):
    """visibility 和其他通道可以同時進行，其他通道 resolve 的結果完全不變。"""
    base = _resolve(example_raw, make_config, spec_dir)
    raw = _with(example_raw, _vis("cloud", 0.5, 1.5, False), _vis("star", 1.0, 3.0, False),
                _vis("sun", 0.0, 4.0, False))                         # 和 fade_out / move+rotate / scale 重疊
    assert validate(raw, EXAMPLE).ok
    res = _resolve(raw, make_config, spec_dir)
    assert [a for a in res["animations"] if a["channel"] != "visibility"] == base["animations"]
    assert len(_vis_anims(res)) == 3


def test_layer_named_background_is_allowed_by_stage1(example_raw, make_config, spec_dir):
    raw = _with(example_raw, _vis("background", 1.0, 2.0, False))
    assert validate(raw, EXAMPLE).ok                                  # CLI 會拒絕背景；Stage 1 不限制
    seg, = _vis_anims(_resolve(raw, make_config, spec_dir), "background")
    assert seg["from"] == [0.0]


# ---------------------------------------------------------------- examples/visibility

def test_visibility_example_is_valid_and_resolves(make_config):
    raw = json.loads((VIS_EXAMPLE / "spec.json").read_text(encoding="utf-8"))
    result = validate(raw, VIS_EXAMPLE)
    assert result.ok and result.warnings == [], (result.errors, result.warnings)
    res = resolve(raw, VIS_EXAMPLE / "spec.json", make_config())
    layers = {l["id"]: l for l in res["layers"]}
    assert layers["title"]["visibility"] == 0.0 and layers["bar"]["visibility"] == 1.0
    segs = {s["id"]: (s["frame_start"], s["frame_end"], s["from"], s["to"]) for s in _vis_anims(res)}
    assert segs == {"bar-hide": (31, 61, [0.0], [1.0]), "star-hide": (46, 76, [0.0], [1.0]),
                    "title-show": (91, 120, [1.0], [1.0])}
