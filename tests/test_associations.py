"""S2-2 M4：分割時記錄「物件 ↔ 補洞區域」「物件 ↔ 物件」的關聯（需要 numpy，不需要模型）。"""

from __future__ import annotations

import json

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from animator.segmentation import compose as C  # noqa: E402
from test_segmentation import _segment_scene  # noqa: E402

H = W = 200


def _rect(x0, y0, x1, y1):
    m = np.zeros((H, W), bool)
    m[y0:y1, x0:x1] = True
    return m


def _items(**masks):
    objects = [C.ObjectItem(name, name.split("-")[0], None, m, "manual") for name, m in masks.items()]
    return C.assign_layers(objects, H * W, 0.5)


def _assoc(**masks):
    items = _items(**masks)
    _, regions = C.hole_regions([o.mask for o in items], (H, W), dilate_px=2, review_ratio=0.5)
    return C.associations(items, regions), regions


def test_touching_masks_share_one_region():
    a, regions = _assoc(**{"a-1": _rect(20, 20, 60, 60), "b-1": _rect(66, 20, 100, 60)})   # 間隔 6 px < 外擴 8 px
    assert len(regions) == 1
    assert a == {"version": 1, "regions": {"0": ["a-1", "b-1"]}, "overlaps": []}


def test_separate_objects_get_separate_regions():
    a, regions = _assoc(**{"a-1": _rect(10, 10, 40, 40), "b-1": _rect(150, 150, 190, 190)})
    assert len(regions) == 2
    by_region = {k: v for k, v in a["regions"].items()}
    assert sorted(by_region.values()) == [["a-1"], ["b-1"]]
    for key, ids in by_region.items():                               # 區域的 box 確實包含它的物件
        x0, y0, x1, y1 = regions[int(key)].box
        assert (x0 <= 10 and x1 >= 40) if ids == ["a-1"] else (x0 <= 150 and x1 >= 190)
    assert a["overlaps"] == []


def test_overlapping_masks_record_exact_pixels_and_depth_order():
    big, small = _rect(20, 20, 120, 120), _rect(100, 100, 140, 140)  # 重疊 20×20 = 400 px
    a, _ = _assoc(**{"big-1": big, "small-1": small})
    assert a["regions"] == {"0": ["big-1", "small-1"]}
    assert a["overlaps"] == [{"behind": "big-1", "front": "small-1", "px": 400}]   # 面積大的在後面（z 小）


def test_multiple_objects_in_one_region_and_one_alone():
    a, regions = _assoc(**{"a-1": _rect(10, 10, 50, 50), "b-1": _rect(50, 10, 90, 50), "c-1": _rect(90, 10, 130, 50),
                           "d-1": _rect(150, 150, 180, 180)})
    assert len(regions) == 2
    groups = sorted(sorted(v) for v in a["regions"].values())
    assert groups == [["a-1", "b-1", "c-1"], ["d-1"]]


def test_disconnected_object_mask_can_belong_to_two_regions():
    two_parts = _rect(10, 10, 30, 30) | _rect(160, 160, 190, 190)
    a, regions = _assoc(**{"split-1": two_parts})
    assert len(regions) == 2 and a["regions"] == {"0": ["split-1"], "1": ["split-1"]}


def test_manual_mask_mode_writes_associations(make_config, tmp_path):
    res, rgb, circle, square = _segment_scene(make_config, tmp_path)
    seg = json.loads((res.job.dir / "segmentation" / "segmentation.json").read_text(encoding="utf-8"))
    assoc = seg["associations"]
    assert assoc["version"] == 1
    layer_ids = {l["id"] for l in json.loads(res.spec_path.read_text(encoding="utf-8"))["layers"]} - {"background"}
    assert {i for ids in assoc["regions"].values() for i in ids} == layer_ids == {"ball-1", "box-1"}
    assert set(assoc["regions"]) == {str(r["index"]) for r in seg["background"]["regions"]}
    assert assoc["overlaps"] == [{"behind": "box-1", "front": "ball-1", "px": int((circle & square).sum())}]
    assert "associations" not in json.loads(res.spec_path.read_text(encoding="utf-8"))   # 只在 segmentation.json
    for layer in json.loads(res.spec_path.read_text(encoding="utf-8"))["layers"]:
        assert "repair_regions" not in layer.get("meta", {})


@pytest.mark.blender
def test_incomplete_job_still_renders_unchanged(make_config, tmp_path):
    """M4 不擋渲染：背景沒有修補（incomplete）的 job 照樣可以渲染，結果和以前一樣是成功的。"""
    from animator.animate.warnings import analyze
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.pipeline import render
    from animator.tools import find_blender

    res, *_ = _segment_scene(make_config, tmp_path, inpaint="none")
    assert res.incomplete
    cfg = load_config(None, {"paths.output_dir": str(tmp_path / "out"), "paths.temp_dir": str(tmp_path / "tmp2")})
    try:
        find_blender(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender：{exc}")
    out = render(res.spec_path, cfg, frame=1, argv=["pytest"])
    assert out.report["status"] == "ok" and out.output.is_file()
    findings = analyze(res.spec_path, cfg).findings
    assert [f for f in findings if f.kind == "exposure"] == []        # 沒有動畫：沒有露出的提醒
    assert [f.kind for f in findings] == ["depth"]                    # ball / box 的遮罩本來就重疊
