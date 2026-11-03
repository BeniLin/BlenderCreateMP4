from __future__ import annotations

import copy

import pytest

from animator.errors import SpecError
from animator.spec import schema as S
from animator.spec.png import read_png_info
from animator.spec.resolve import resolve
from animator.spec.validate import parse_color, validate
from conftest import EXAMPLE, write_spec


def errors_of(raw, spec_dir):
    return [str(e) for e in validate(raw, spec_dir).errors]


def test_example_spec_is_valid(example_raw):
    result = validate(example_raw, EXAMPLE)
    assert result.ok, result.errors
    assert result.warnings == []


def test_time_to_frame_rule():
    assert S.frame_count(4.0, 30) == 120
    assert S.time_to_frame(0, 30, 120) == 1
    assert S.time_to_frame(1.0, 30, 120) == 31
    assert S.time_to_frame(4.0, 30, 120) == 120  # 限制在最後一格


def test_parse_color():
    assert parse_color("#ff0000") == (1.0, 0.0, 0.0, 1.0)
    assert parse_color([0, 0.5, 1]) == (0.0, 0.5, 1.0, 1.0)
    assert parse_color("red") is None


@pytest.mark.parametrize("mutate, expected", [
    (lambda r: r.update(version=2), "version"),
    (lambda r: r["canvas"].update(width=1281), "canvas.width"),
    (lambda r: r["canvas"].pop("duration"), "canvas.duration"),
    (lambda r: r["layers"][1].update(id="sun"), "layers[2].id"),
    (lambda r: r["layers"][1].update(id="Bad Id"), "layers[1].id"),
    (lambda r: r["layers"][1].update(image="layers/missing.png"), "layers[1].image"),
    (lambda r: r["layers"][1].update(anchor=[2, 0]), "layers[1].anchor"),
    (lambda r: r["layers"][1].update(opacity=1.5), "layers[1].opacity"),
    (lambda r: r["layers"][1].update(parent="sun"), "layers[1].parent"),
    (lambda r: r["animations"][0].update(target="nobody"), "animations[0].target"),
    (lambda r: r["animations"][0].update(target="@camera"), "animations[0].target"),
    (lambda r: r["animations"][0].update(effect="glow"), "animations[0].effect"),
    (lambda r: r["animations"][0].update(effect="explode"), "animations[0].effect"),
    (lambda r: r["animations"][0].update(end=99), "animations[0].end"),
    (lambda r: r["animations"][0].update(start=1.0, end=1.0), "animations[0].end"),
    (lambda r: r["animations"][0].update(start=1.0, end=1.01), "animations[0]"),
    (lambda r: r["animations"][0].update(easing="wobbly"), "animations[0].easing"),
    (lambda r: r["animations"][0]["params"].update(to=[1, 2]), "animations[0].params"),
    (lambda r: r["animations"][2]["params"].pop("to"), "animations[2].params.to"),
    (lambda r: r["animations"][3].update(start=1.5), "animations[3]"),  # sun scale 重疊
    (lambda r: r["output"].update(filename="../x.mp4"), "output.filename"),
    (lambda r: r["render"].update(engine="workbench"), "render.engine"),
])
def test_invalid_specs(example_raw, mutate, expected):
    raw = copy.deepcopy(example_raw)
    mutate(raw)
    errs = errors_of(raw, EXAMPLE)
    assert any(e.startswith(expected) for e in errs), errs


def test_all_errors_reported_at_once(example_raw):
    raw = copy.deepcopy(example_raw)
    raw["canvas"]["width"] = 7
    raw["animations"][0]["target"] = "nobody"
    raw["animations"][1]["easing"] = "nope"
    assert len(errors_of(raw, EXAMPLE)) >= 3


def test_overlap_only_on_same_channel(example_raw):
    # star-fly 與 star-spin 時間完全相同，但通道不同 → 合法
    assert validate(example_raw, EXAMPLE).ok


def test_disabled_animation_skips_overlap(example_raw):
    raw = copy.deepcopy(example_raw)
    raw["animations"][3]["start"] = 1.5
    raw["animations"][3]["enabled"] = False
    assert validate(raw, EXAMPLE).ok


def test_unknown_field_is_warning_only(example_raw):
    raw = copy.deepcopy(example_raw)
    raw["layers"][0]["opactiy"] = 0.5
    result = validate(raw, EXAMPLE)
    assert result.ok
    assert any("layers[0].opactiy" in str(w) for w in result.warnings)


# ---------------------------------------------------------------- resolve

def test_resolve_example(example_raw, make_config, spec_dir):
    cfg = make_config()
    path = write_spec(spec_dir, example_raw)
    res = resolve(example_raw, path, cfg)

    assert res["canvas"]["frame_end"] == 120
    assert [l["id"] for l in res["layers"]] == ["background", "cloud", "sun", "bar", "star", "title"]
    assert [l["rank"] for l in res["layers"]] == list(range(6))
    bg = res["layers"][0]
    assert bg["size"] == [1280, 720] and bg["position"] == [640.0, 360.0]
    assert res["layers"][2]["scale"] == [0.6, 0.6]

    anims = {a["id"]: a for a in res["animations"]}
    # by → to，from 由初始值推算
    assert anims["cloud-drift"]["from"] == [300.0, 160.0] and anims["cloud-drift"]["to"] == [220.0, 160.0]
    # 別名展開
    assert anims["cloud-out"]["effect"] == "opacity" and anims["cloud-out"]["source_effect"] == "fade_out"
    assert anims["cloud-out"]["from"] == [1.0] and anims["cloud-out"]["to"] == [0.0]
    assert anims["title-in"]["from"] == [0.0] and anims["title-in"]["to"] == [1.0]
    # 同通道串接：sun-settle 從 sun-grow 的結束值開始
    assert anims["sun-grow"]["from"] == [0.6, 0.6]
    assert anims["sun-settle"]["from"] == [1.2, 1.2] and anims["sun-settle"]["to"] == [0.9, 0.9]
    assert anims["star-spin"]["to"] == [360.0]
    assert anims["star-fly"]["frame_start"] == 16 and anims["star-fly"]["frame_end"] == 106
    assert anims["title-in"]["easing"] == S.DEFAULT_EASING


def test_resolve_preview_and_defaults(example_raw, make_config, spec_dir):
    cfg = make_config()
    raw = copy.deepcopy(example_raw)
    del raw["render"], raw["output"]
    res = resolve(raw, write_spec(spec_dir, raw), cfg, preview=True)
    assert res["render"]["resolution_percentage"] == 25
    assert res["output"]["filename"] == "preview.mp4"
    assert res["output"]["crf"] == 28
    assert res["render"]["engine"] == "eevee"


def test_resolve_z_ties_and_visibility(example_raw, make_config, spec_dir):
    raw = copy.deepcopy(example_raw)
    for layer in raw["layers"]:
        layer["z"] = 0          # 全部同 z → 依陣列順序
    raw["layers"][1]["visible"] = False   # cloud 隱藏 → 它的動畫也被移除
    res = resolve(raw, write_spec(spec_dir, raw), make_config())
    assert [l["id"] for l in res["layers"]] == ["background", "sun", "bar", "star", "title"]
    assert all(a["target"] != "cloud" for a in res["animations"])


def test_resolve_explicit_from_and_negative_z(example_raw, make_config, spec_dir):
    raw = copy.deepcopy(example_raw)
    raw["layers"][5]["z"] = -1   # title 移到最後面
    raw["animations"][4]["params"]["from"] = [0, 0]
    res = resolve(raw, write_spec(spec_dir, raw), make_config())
    assert res["layers"][0]["id"] == "title"
    assert {a["id"]: a for a in res["animations"]}["star-fly"]["from"] == [0.0, 0.0]


def test_resolve_rejects_invalid(example_raw, make_config, spec_dir):
    raw = copy.deepcopy(example_raw)
    raw["animations"][0]["target"] = "nobody"
    with pytest.raises(SpecError):
        resolve(raw, write_spec(spec_dir, raw), make_config())


def test_png_info_and_generator(tmp_path):
    import make_sample_layers as gen

    gen.main(["x", str(tmp_path)])
    info = read_png_info(tmp_path / "star.png")
    assert (info.width, info.height, info.color_type, info.has_alpha) == (160, 160, 6, True)
    for name in gen.LAYERS:
        assert (tmp_path / f"{name}.png").read_bytes() == (EXAMPLE / "layers" / f"{name}.png").read_bytes(), \
            f"examples/basic/layers/{name}.png 與產生器不一致，請重新執行 scripts/make_sample_layers.py"
