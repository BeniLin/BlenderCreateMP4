"""S2-2 M3b：layers pivot（修改 anchor 並補償 position，畫面不跳動；只用標準函式庫）。

合成 job（tests/test_animate.py 的 make_job）：ball-1 是 24×24 的圖（20×20 紅色 + 2 px 透明邊），
position (40, 64)、anchor 預設 [0.5, 0.5] → 圖片在畫布上是 x 28..52、y 52..76。
"""

from __future__ import annotations

import json
import math
import random

import pytest

from test_animate import make_job


@pytest.fixture
def cfg(make_config):
    return make_config()


@pytest.fixture
def job(cfg):
    return make_job(cfg.path("output_dir"))


def _spec(job):
    return json.loads((job / "spec.json").read_text(encoding="utf-8"))


def _write(job, spec):
    (job / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _backups(job):
    d = job / "history" / "edits"
    return sorted(d.iterdir()) if d.is_dir() else []


def _layer(spec, layer_id):
    return next(l for l in spec["layers"] if l["id"] == layer_id)


def _update_layer(job, layer_id, **fields):
    spec = _spec(job)
    _layer(spec, layer_id).update(fields)
    _write(job, spec)


def _add_anims(job, *anims):
    spec = _spec(job)
    spec["animations"].extend(anims)
    _write(job, spec)


def _cli(cfg, job, *args):
    from animator import cli

    return cli.main(["--config", str(cfg.config_file), "layers", "pivot", job.name, *args])


def _unchanged(job, original):
    return (job / "spec.json").read_bytes() == original and _backups(job) == []


SIZE = (24, 24)


# ---------------------------------------------------------------- 補償公式（純函數）

TRANSFORMS = [(1, 0), (0.6, 0), ([2, 0.5], 0), (1, 30), (1, -45), ([0.6, 1.4], 30), (0.6, -120), (1.5, 90)]


@pytest.mark.parametrize("scale, rotation", TRANSFORMS)
def test_compensation_keeps_every_image_pixel_in_place(scale, rotation):
    """anchor a → a'、position P → Q（= 新 pivot 的畫布座標）之後，圖片上每一點的畫布座標都不變。"""
    from animator.animate.edit import canvas_to_image, image_to_canvas

    rng = random.Random(1)
    p, a = (40.0, 64.0), (0.5, 0.5)
    for _ in range(5):
        q = image_to_canvas(rng.uniform(0, 24), rng.uniform(0, 24), p, a, SIZE, scale, rotation)
        u, v = canvas_to_image(*q, p, a, SIZE, scale, rotation)
        a2 = (u / SIZE[0], v / SIZE[1])
        for _ in range(20):
            pt = (rng.uniform(-5, 30), rng.uniform(-5, 30))
            before = image_to_canvas(*pt, p, a, SIZE, scale, rotation)
            after = image_to_canvas(*pt, q, a2, SIZE, scale, rotation)
            assert math.dist(before, after) < 1e-9


@pytest.mark.parametrize("scale, rotation", TRANSFORMS)
def test_canvas_to_image_is_the_inverse(scale, rotation):
    from animator.animate.edit import canvas_to_image, image_to_canvas

    p, a = (40.0, 64.0), (0.25, 0.75)
    for u, v in ((0, 0), (24, 24), (3.5, 20), (12, 1)):
        c = image_to_canvas(u, v, p, a, SIZE, scale, rotation)
        assert math.dist(canvas_to_image(*c, p, a, SIZE, scale, rotation), (u, v)) < 1e-9


def test_rotation_direction_matches_stage1():
    """順時針為正、y 往下：圖片右邊的點轉 90° 之後在 pivot 的正下方（docs/spec.md 2.3）。"""
    from animator.animate.edit import image_to_canvas

    x, y = image_to_canvas(24, 12, (40, 64), (0.5, 0.5), SIZE, 1, 90)
    assert math.isclose(x, 40, abs_tol=1e-9) and math.isclose(y, 76, abs_tol=1e-9)


# ---------------------------------------------------------------- 編輯結果

def test_bottom_centre_pivot_on_s2_1_style_layer(cfg, job, capsys):
    assert _cli(cfg, job, "ball-1", "--at", "40,74") == 0
    ball = _layer(_spec(job), "ball-1")
    assert ball["position"] == [40, 74]                               # 剛好等於 Q
    assert ball["anchor"] == [0.5, 22 / 24]                           # (74 − 52) / 24
    out = capsys.readouterr().out
    assert "[0.5, 0.5] → [0.5, 0.916667]" in out and "[40, 64] → [40, 74]" in out


def test_position_becomes_exactly_q_for_scaled_and_rotated_layer(cfg, job):
    from animator.animate.edit import canvas_to_image, image_to_canvas, set_layer_pivot

    _update_layer(job, "ball-1", scale=0.6, rotation=30)
    q = image_to_canvas(5, 19, (40, 64), (0.5, 0.5), SIZE, 0.6, 30)
    res = set_layer_pivot(job / "spec.json", "ball-1", list(q))
    ball = _layer(_spec(job), "ball-1")
    assert ball["position"] == [q[0], q[1]] == res.new_position
    u, v = canvas_to_image(*q, (40, 64), (0.5, 0.5), SIZE, 0.6, 30)
    assert math.isclose(ball["anchor"][0], u / 24) and math.isclose(ball["anchor"][1], v / 24)
    assert math.isclose(ball["anchor"][0], 5 / 24) and math.isclose(ball["anchor"][1], 19 / 24)
    assert ball["scale"] == 0.6 and ball["rotation"] == 30            # 初始 scale / rotation 不變


@pytest.mark.parametrize("at, anchor", [("28,52", [0, 0]), ("52,76", [1, 1]), ("28,76", [0, 1]), ("52,52", [1, 0])])
def test_anchor_boundaries_are_accepted(cfg, job, at, anchor):
    assert _cli(cfg, job, "ball-1", "--at", at) == 0
    assert _layer(_spec(job), "ball-1")["anchor"] == anchor


def test_boundary_within_float_error_is_accepted_for_rotated_layer(cfg, job):
    from animator.animate.edit import image_to_canvas, set_layer_pivot

    _update_layer(job, "ball-1", scale=0.5, rotation=90)
    corner = image_to_canvas(24, 0, (40, 64), (0.5, 0.5), SIZE, 0.5, 90)   # cos 90° 不是剛好 0
    res = set_layer_pivot(job / "spec.json", "ball-1", list(corner))
    assert res.new_anchor == [1, 0]


@pytest.mark.parametrize("at", ["27.99,52", "52.01,76", "40,51.9", "40,76.1", "100,100"])
def test_pivot_outside_the_image_is_rejected(cfg, job, capsys, at):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", "--at", at) == 1
    assert "不在圖層 'ball-1' 的圖片範圍內" in capsys.readouterr().err
    assert _unchanged(job, original)


# ---------------------------------------------------------------- move 動畫的補償

MOVES = [
    {"id": "m-to", "target": "ball-1", "effect": "move", "start": 0.0, "end": 0.25, "params": {"to": [100, 30]}},
    {"id": "m-by", "target": "ball-1", "effect": "move", "start": 0.25, "end": 0.5, "params": {"by": [10, -4]}},
    {"id": "m-off", "target": "ball-1", "effect": "move", "start": 0.5, "end": 0.75, "enabled": False,
     "params": {"from": [5, 5], "to": [20, 20]}},
    {"id": "m-from-by", "target": "ball-1", "effect": "move", "start": 0.75, "end": 1.0,
     "params": {"from": [50, 50], "by": [5, 0]}},
    {"id": "s1", "target": "ball-1", "effect": "scale", "start": 0.5, "end": 1.0, "params": {"to": 1.5}},
    {"id": "r1", "target": "ball-1", "effect": "rotate", "start": 0.2, "end": 0.6, "params": {"by": 45}},
]


def test_move_to_and_from_shift_and_by_is_unchanged(cfg, job, capsys):
    _add_anims(job, *json.loads(json.dumps(MOVES)))
    before = _spec(job)
    assert _cli(cfg, job, "ball-1", "--at", "44,70") == 0             # c = (4, 6)
    after = _spec(job)
    a = {x["id"]: x for x in after["animations"]}
    assert a["m-to"]["params"] == {"to": [104, 36]}
    assert a["m-by"]["params"] == {"by": [10, -4]}                    # 位移不變
    assert a["m-off"]["params"] == {"from": [9, 11], "to": [24, 26]}  # enabled: false 也改寫
    assert a["m-from-by"]["params"] == {"from": [54, 56], "by": [5, 0]}
    b = {x["id"]: x for x in before["animations"]}
    for keep in ("s1", "r1", "box-1-hand"):                           # scale / rotate / 其他圖層不變
        assert a[keep] == b[keep]
    for aid in ("m-to", "m-by", "m-off", "m-from-by"):                # 除了 params 以外都不變
        assert {**a[aid], "params": None} == {**b[aid], "params": None}
    assert "m-to, m-off, m-from-by" in capsys.readouterr().out        # 回報改寫了哪幾筆


def test_resolve_before_and_after_differ_by_exactly_the_offset(cfg, job):
    """用 Stage 1 真正的 resolve()：ball-1 的 position 和每一段 move 的 from / to 都剛好差 c，其他全部相同。"""
    from animator.animate.edit import set_layer_pivot
    from animator.spec import load_spec
    from animator.spec.resolve import resolve

    _update_layer(job, "ball-1", scale=0.6, rotation=30)
    _add_anims(job, *json.loads(json.dumps(MOVES)))
    spec_path = job / "spec.json"
    before = resolve(load_spec(spec_path), spec_path, cfg)
    res = set_layer_pivot(spec_path, "ball-1", [43.5, 69.25])
    after = resolve(load_spec(spec_path), spec_path, cfg)
    cx, cy = res.offset
    assert (cx, cy) == (3.5, 5.25)

    def close(p, q):
        return all(math.isclose(x, y + d, abs_tol=1e-9) for x, y, d in zip(p, q, (cx, cy)))

    lb = {l["id"]: l for l in before["layers"]}
    la = {l["id"]: l for l in after["layers"]}
    assert close(la["ball-1"]["position"], lb["ball-1"]["position"])
    for key in ("size", "z", "scale", "rotation", "opacity", "rank"):
        assert la["ball-1"][key] == lb["ball-1"][key]
    for lid in ("background", "box-1"):
        assert la[lid] == lb[lid]
    ab = {x["id"]: x for x in before["animations"]}
    for x in after["animations"]:
        if x["target"] == "ball-1" and x["channel"] == "position":
            assert close(x["from"], ab[x["id"]]["from"]) and close(x["to"], ab[x["id"]]["to"]), x["id"]
        else:
            assert x == ab[x["id"]], x["id"]


# ---------------------------------------------------------------- 第 1 格明確 from 的特殊情況

@pytest.mark.parametrize("anim", [
    {"effect": "scale", "start": 0.0, "end": 0.5, "params": {"from": 0.5, "to": 1}},
    {"effect": "scale", "start": 0.01, "end": 0.5, "params": {"from": [1, 2], "to": 1}},   # 仍然是第 1 格
    {"effect": "rotate", "start": 0.0, "end": 0.5, "params": {"from": 10, "to": 0}},
])
def test_explicit_from_at_frame_1_is_rejected(cfg, job, capsys, anim):
    _add_anims(job, {"id": "x", "target": "ball-1", **anim})
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", "--at", "40,74") == 1
    assert "第 1 格" in capsys.readouterr().err
    assert _unchanged(job, original)


@pytest.mark.parametrize("anim", [
    {"effect": "scale", "start": 0.0, "end": 0.5, "params": {"from": 1, "to": 2}},         # from = 初始值
    {"effect": "scale", "start": 0.0, "end": 0.5, "params": {"from": [1, 1], "to": 2}},
    {"effect": "rotate", "start": 0.0, "end": 0.5, "params": {"from": 0, "to": 90}},
    {"effect": "scale", "start": 0.5, "end": 0.8, "params": {"from": 0.5, "to": 2}},       # 不是第 1 格
    {"effect": "scale", "start": 0.0, "end": 0.5, "enabled": False, "params": {"from": 0.5, "to": 2}},
    {"effect": "scale", "start": 0.0, "end": 0.5, "params": {"to": 2}},                    # 沒有 from
    {"effect": "rotate", "start": 0.0, "end": 0.5, "params": {"by": 90}},
])
def test_scale_and_rotate_animations_are_not_rejected(cfg, job, anim):
    _add_anims(job, {"id": "x", "target": "ball-1", **anim})
    before = _spec(job)
    assert _cli(cfg, job, "ball-1", "--at", "40,74") == 0
    assert _spec(job)["animations"] == before["animations"]           # scale / rotate 動畫不改


# ---------------------------------------------------------------- 拒絕（結束碼 1），spec 不變、沒有備份

@pytest.mark.parametrize("args, match", [
    (["nope", "--at", "1,1"], "找不到圖層 'nope'"),
    (["background", "--at", "96,64"], "背景"),
    (["ball-1", "--at", "40"], "--at 必須是兩個數字"),
    (["ball-1", "--at", "a,b"], "--at 必須是兩個數字"),
    (["ball-1", "--at", "nan,64"], "--at 必須是兩個數字"),
    (["ball-1", "--at", "40,inf"], "--at 必須是兩個數字"),
    (["ball-1", "--at", "1,2,3"], "--at 必須是兩個數字"),
])
def test_rejected_requests_exit_1_and_leave_spec_untouched(cfg, job, capsys, args, match):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, *args) == 1
    err = capsys.readouterr().err
    assert match in err and "Traceback" not in err
    assert _unchanged(job, original)


def test_missing_png_is_rejected(cfg, job, capsys):
    (job / "layers" / "ball-1.png").unlink()
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", "--at", "40,74") == 1
    assert "無法讀取圖層 'ball-1' 的圖片" in capsys.readouterr().err
    assert _unchanged(job, original)


def test_api_rejects_non_finite_coordinates(cfg, job):
    from animator.animate.edit import set_layer_pivot
    from animator.errors import AnimatorError

    original = (job / "spec.json").read_bytes()
    for at in ([float("nan"), 1], [1, float("inf")], [1], "1,2", [True, 1]):
        with pytest.raises(AnimatorError, match="--at"):
            set_layer_pivot(job / "spec.json", "ball-1", at)
    assert _unchanged(job, original)


def test_argparse_requires_at(cfg, job):
    with pytest.raises(SystemExit):
        _cli(cfg, job, "ball-1")
    with pytest.raises(SystemExit):                                   # 沒有 --anchor（P1）
        _cli(cfg, job, "ball-1", "--anchor", "0.5,1")


# ---------------------------------------------------------------- 沒有變更（P3）

def test_same_pivot_is_a_no_op(cfg, job, capsys):
    original = (job / "spec.json").read_bytes()
    mtime = (job / "spec.json").stat().st_mtime_ns
    assert _cli(cfg, job, "ball-1", "--at", "40,64") == 0             # 目前的 pivot（預設 anchor 的中心）
    assert "沒有變更" in capsys.readouterr().out
    assert _unchanged(job, original) and (job / "spec.json").stat().st_mtime_ns == mtime
    assert "anchor" not in _layer(_spec(job), "ball-1")               # 不會補上預設欄位


def test_repeating_the_same_pivot_edit_is_a_no_op(cfg, job, capsys):
    assert _cli(cfg, job, "ball-1", "--at", "40,74") == 0
    after_first = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", "--at", "40,74") == 0
    assert "沒有變更" in capsys.readouterr().out
    assert (job / "spec.json").read_bytes() == after_first and len(_backups(job)) == 1


def test_skip_if_unchanged_is_opt_in(cfg, job):
    """edit_spec 預設仍然會寫入（M1 / M2 / M3a 不變）；只有 skip_if_unchanged=True 才會略過。"""
    from animator.animate.edit import edit_spec, set_layer_z

    value, backup, _ = edit_spec(job / "spec.json", lambda new: "same")
    assert value == "same" and backup is not None and len(_backups(job)) == 1
    value, backup, _ = edit_spec(job / "spec.json", lambda new: "same", skip_if_unchanged=True)
    assert backup is None and len(_backups(job)) == 1
    spec = _spec(job)
    _layer(spec, "ball-1")["meta"]["z_rule"] = "manual"
    _write(job, spec)
    assert set_layer_z(job / "spec.json", "ball-1", z=20).backup is not None   # M3a：內容沒變也照樣寫入
    assert len(_backups(job)) == 2


# ---------------------------------------------------------------- 保留其他欄位、失敗處理

def test_only_position_anchor_and_move_points_change(cfg, job):
    from animator.animate.edit import set_layer_pivot

    before = _spec(job)
    set_layer_pivot(job / "spec.json", "ball-1", [40, 74])
    after = _spec(job)
    for key in ("version", "meta", "canvas", "animations"):
        assert after[key] == before[key], key                         # 沒有 ball-1 的 move：動畫完全不變
    assert list(after) == list(before)
    b0, b1 = _layer(before, "ball-1"), _layer(after, "ball-1")
    assert list(b1) == list(b0) + ["anchor"]                          # position 原地更新，anchor 加在最後
    assert {k: v for k, v in b1.items() if k not in ("position", "anchor")} == \
        {k: v for k, v in b0.items() if k != "position"}
    for other in ("background", "box-1"):
        assert _layer(after, other) == _layer(before, other)


def test_write_failure_keeps_original_and_leaves_no_temp_or_backup(cfg, job, monkeypatch):
    from animator.animate import edit

    original = (job / "spec.json").read_bytes()

    def broken_replace(src, dst):
        raise OSError("模擬：磁碟已滿")

    monkeypatch.setattr(edit.os, "replace", broken_replace)
    with pytest.raises(OSError, match="磁碟已滿"):
        edit.set_layer_pivot(job / "spec.json", "ball-1", [40, 74])
    assert _unchanged(job, original)
    assert sorted(p.name for p in job.iterdir()) == ["history", "layers", "spec.json"]


def test_concurrent_modification_is_detected(cfg, job, monkeypatch):
    from animator.animate import edit
    from animator.errors import AnimatorError

    real_validate = edit.validate

    def validate_then_touch(raw, spec_dir):
        r = real_validate(raw, spec_dir)
        (job / "spec.json").write_text((job / "spec.json").read_text(encoding="utf-8") + " ", encoding="utf-8")
        return r

    monkeypatch.setattr(edit, "validate", validate_then_touch)
    with pytest.raises(AnimatorError, match="被其他程式修改"):
        edit.set_layer_pivot(job / "spec.json", "ball-1", [40, 74])
    assert _backups(job) == []


def test_backup_holds_exact_previous_bytes(cfg, job):
    from animator.animate.edit import set_layer_pivot

    original = (job / "spec.json").read_bytes()
    res = set_layer_pivot(job / "spec.json", "ball-1", [40, 74])
    assert res.backup.read_bytes() == original and len(_backups(job)) == 1


# ---------------------------------------------------------------- 負數、layers list

def test_negative_at_through_the_real_cli(cfg, job):
    _update_layer(job, "ball-1", position=[-5, 10])                   # 圖片在畫布上是 x -17..7、y -2..22
    assert _cli(cfg, job, "ball-1", "--at", "-10,-1") == 0
    ball = _layer(_spec(job), "ball-1")
    assert ball["position"] == [-10, -1] and ball["anchor"] == [7 / 24, 1 / 24]
    assert _cli(cfg, job, "ball-1", "--at=-12.5,-2") == 0
    assert _layer(_spec(job), "ball-1")["position"] == [-12.5, -2]


def test_negative_value_rewrite_scope():
    from animator.cli import join_anim_negative_values as join

    assert join(["layers", "pivot", "j", "l", "--at", "-1,2"])[-1] == "--at=-1,2"
    assert join(["layers", "z", "j", "l", "--z", "-5"])[-1] == "--z=-5"                    # M3a 不變
    assert join(["anim", "add", "j", "l", "move", "--by", "-1,0"])[-1] == "--by=-1,0"     # M2 不變
    for argv in (["layers", "list", "--at", "-1,2"], ["anim", "add", "j", "l", "move", "--at", "-1,2"],
                 ["layers", "pivot", "j", "l", "--at", "--x"]):
        assert join(list(argv)) == argv


def test_layers_list_shows_anchor_and_pivot_position(cfg, job, capsys):
    from animator import cli

    assert _cli(cfg, job, "ball-1", "--at", "40,74") == 0
    capsys.readouterr()
    assert cli.main(["--config", str(cfg.config_file), "layers", "list", job.name]) == 0
    out = capsys.readouterr().out
    header = out.splitlines()[1]
    assert "position(pivot px)" in header and "anchor" in header
    lines = {line.split()[0]: line for line in out.splitlines()[2:]}
    assert "[40, 74]" in lines["ball-1"] and "[0.5, 0.916667]" in lines["ball-1"]
    assert "[150, 64]" in lines["box-1"] and "[0.5, 0.5]" in lines["box-1"]   # 預設 anchor
    assert lines["ball-1"].split()[2] == "20"                         # 既有欄位（z）的位置不變
