"""S2-2 M3a：layers z（手動修改前後順序，只用標準函式庫）。

合成 job（tests/test_animate.py 的 make_job）：background z 0、box-1 z 10、ball-1 z 20（由後往前）。
"""

from __future__ import annotations

import json
import math

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


def _cli(cfg, job, *args):
    from animator import cli

    return cli.main(["--config", str(cfg.config_file), "layers", "z", job.name, *args])


def _add_layer(job, layer_id, z, image="layers/box-1.png", meta=None):
    spec = _spec(job)
    layer = {"id": layer_id, "image": image, "z": z, "position": [96.0, 30.0]}
    if meta is not None:
        layer["meta"] = meta
    spec["layers"].append(layer)
    _write(job, spec)


def _set_z(job, **zs):
    spec = _spec(job)
    for layer_id, z in zs.items():
        _layer(spec, layer_id.replace("_", "-"))["z"] = z
    _write(job, spec)


def _unchanged(job, original):
    return (job / "spec.json").read_bytes() == original and _backups(job) == []


# ---------------------------------------------------------------- 成功的編輯

def test_direct_z(cfg, job, capsys):
    assert _cli(cfg, job, "ball-1", "--z", "5") == 0
    ball = _layer(_spec(job), "ball-1")
    assert ball["z"] == 5 and ball["meta"]["z_rule"] == "manual"
    out = capsys.readouterr().out
    assert "從 20 改成 5" in out and "background → ball-1 → box-1" in out


def test_above_reference_at_top_uses_plus_10(cfg, job):
    from animator.animate.edit import set_layer_z

    res = set_layer_z(job / "spec.json", "box-1", above="ball-1")
    assert (res.old_z, res.new_z) == (10, 30)
    assert res.order == ["background", "ball-1", "box-1"]


def test_above_inserts_between_neighbours(cfg, job):
    from animator.animate.edit import set_layer_z

    _add_layer(job, "star-1", 30)                                     # bg 0, box 10, ball 20, star 30
    res = set_layer_z(job / "spec.json", "box-1", above="ball-1")
    assert res.new_z == 25 and res.order == ["background", "ball-1", "box-1", "star-1"]


def test_below_inserts_between_background_and_lowest(cfg, job):
    from animator.animate.edit import set_layer_z

    res = set_layer_z(job / "spec.json", "ball-1", below="box-1")
    assert res.new_z == 5 and res.order == ["background", "ball-1", "box-1"]


def test_above_background_puts_layer_just_in_front_of_it(cfg, job):
    from animator.animate.edit import set_layer_z

    res = set_layer_z(job / "spec.json", "ball-1", above="background")
    assert res.new_z == 5 and res.order == ["background", "ball-1", "box-1"]


def test_fractional_midpoint_is_kept(cfg, job):
    from animator.animate.edit import set_layer_z

    _set_z(job, box_1=10, ball_1=11)
    _add_layer(job, "star-1", 30)
    assert set_layer_z(job / "spec.json", "star-1", above="box-1").new_z == 10.5


def test_order_matches_stage1_resolve(cfg, job):
    """layers z 回報的順序和 Stage 1 resolve 實際的渲染順序（rank）一致，包含 z 相同時用陣列順序。"""
    from animator.animate.edit import set_layer_z
    from animator.spec import load_spec
    from animator.spec.resolve import resolve

    _add_layer(job, "star-1", 10)                                     # 和 box-1 同 z、陣列在後 → 比較前面
    res = set_layer_z(job / "spec.json", "ball-1", z=7)
    spec_path = job / "spec.json"
    resolved = resolve(load_spec(spec_path), spec_path, cfg)
    assert res.order == [l["id"] for l in sorted(resolved["layers"], key=lambda l: l["rank"])]
    assert res.order == ["background", "ball-1", "box-1", "star-1"]


def test_only_target_z_and_z_rule_change(cfg, job):
    from animator.animate.edit import set_layer_z

    spec = _spec(job)
    _layer(spec, "ball-1")["meta"]["z_rule"] = "smaller_area_in_front"   # S2-1 寫的值
    _write(job, spec)
    before = _spec(job)
    set_layer_z(job / "spec.json", "ball-1", z=50)
    after = _spec(job)
    for key in ("version", "meta", "canvas", "animations"):
        assert after[key] == before[key], key                         # 動畫（含 x_user_note）不變
    assert list(after) == list(before)
    b0, b1 = _layer(before, "ball-1"), _layer(after, "ball-1")
    assert list(b1) == list(b0)                                       # 欄位順序不變（z 原地更新）
    assert list(b1["meta"]) == list(b0["meta"])                       # z_rule 原地更新
    assert b1["meta"] == {**b0["meta"], "z_rule": "manual"}           # 其他 meta（custom 等）保留
    assert {**b1, "z": b0["z"], "meta": b0["meta"]} == b0
    for other in ("background", "box-1"):
        assert _layer(after, other) == _layer(before, other)          # 其他圖層完全不變（不重新編號）


def test_meta_is_created_when_absent(cfg, job):
    from animator.animate.edit import set_layer_z
    from animator.spec.validate import validate

    _add_layer(job, "star-1", 30)                                     # 沒有 meta
    set_layer_z(job / "spec.json", "star-1", z=40)
    spec = _spec(job)
    assert _layer(spec, "star-1")["meta"] == {"z_rule": "manual"}
    assert validate(spec, job).ok


def test_backup_holds_exact_previous_bytes(cfg, job):
    from animator.animate.edit import set_layer_z

    original = (job / "spec.json").read_bytes()
    res = set_layer_z(job / "spec.json", "ball-1", z=5)
    assert res.backup.parent == job / "history" / "edits" and res.backup.read_bytes() == original
    assert len(_backups(job)) == 1


# ---------------------------------------------------------------- 拒絕（結束碼 1），spec 不變、沒有備份

@pytest.mark.parametrize("args, match", [
    (["nope", "--z", "5"], "找不到圖層 'nope'"),
    (["ball-1", "--above", "nope"], "找不到圖層 'nope'"),
    (["ball-1", "--below", "nope"], "找不到圖層 'nope'"),
    (["ball-1", "--above", "ball-1"], "不能是圖層自己"),
    (["ball-1", "--below", "ball-1"], "不能是圖層自己"),
    (["background", "--z", "50"], "背景"),
    (["background", "--above", "ball-1"], "背景"),
    (["ball-1", "--z", "0"], "不能小於或等於背景"),
    (["ball-1", "--z", "-5"], "不能小於或等於背景"),
    (["ball-1", "--z", "-1e3"], "不能小於或等於背景"),
    (["ball-1", "--z=-2.5"], "不能小於或等於背景"),
    (["ball-1", "--below", "background"], "不能小於或等於背景"),
    (["ball-1", "--z", "abc"], "--z 必須是一個數字"),
    (["ball-1", "--z", "nan"], "--z 必須是一個數字"),
    (["ball-1", "--z", "inf"], "--z 必須是一個數字"),
])
def test_rejected_edits_exit_1_and_leave_spec_untouched(cfg, job, capsys, args, match):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, *args) == 1
    err = capsys.readouterr().err
    assert match in err and "Traceback" not in err
    assert _unchanged(job, original)


def test_equal_z_neighbours_are_ambiguous(cfg, job, capsys):
    _set_z(job, box_1=10, ball_1=10)                                  # 同 z：陣列順序 ball-1 → box-1
    _add_layer(job, "star-1", 30)
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "star-1", "--above", "ball-1") == 1         # ball-1 前面緊接著同 z 的 box-1
    assert _cli(cfg, job, "star-1", "--below", "box-1") == 1          # box-1 後面緊接著同 z 的 ball-1
    err = capsys.readouterr().err
    assert err.count("z 值相同") == 2 and "請改用 --z" in err
    assert _unchanged(job, original)


def test_no_representable_gap_is_refused(cfg, job, capsys):
    _set_z(job, box_1=10.0, ball_1=math.nextafter(10.0, math.inf))   # 兩個相鄰的浮點數
    _add_layer(job, "star-1", 30)
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "star-1", "--above", "box-1") == 1
    assert "沒有可以表示的數值" in capsys.readouterr().err
    assert _unchanged(job, original)


def test_no_value_beyond_extreme_reference_is_refused(cfg, job, capsys):
    _set_z(job, ball_1=1e308)
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "box-1", "--above", "ball-1") == 1
    assert "找不到更前面的數值" in capsys.readouterr().err
    assert _unchanged(job, original)


@pytest.mark.parametrize("kwargs, match", [
    ({}, "剛好指定"),
    ({"z": 5, "above": "box-1"}, "剛好指定"),
    ({"z": float("nan")}, "有限的數字"),
    ({"z": True}, "有限的數字"),
])
def test_api_rejects_bad_requests(cfg, job, kwargs, match):
    from animator.animate.edit import set_layer_z
    from animator.errors import AnimatorError

    original = (job / "spec.json").read_bytes()
    with pytest.raises(AnimatorError, match=match):
        set_layer_z(job / "spec.json", "ball-1", **kwargs)
    assert _unchanged(job, original)


def test_argparse_requires_exactly_one_option(cfg, job):
    for args in ([], ["--z", "5", "--above", "box-1"], ["--above", "box-1", "--below", "box-1"]):
        with pytest.raises(SystemExit):
            _cli(cfg, job, "ball-1", *args)


def test_invalid_spec_is_reported_by_stage1_exit_2(cfg, job, capsys):
    spec = _spec(job)
    spec["animations"][0]["end"] = 5.0                                # 超過 canvas.duration（1 秒）
    _write(job, spec)
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", "--z", "5") == 2
    assert "canvas.duration" in capsys.readouterr().err
    assert _unchanged(job, original)


# ---------------------------------------------------------------- 負數

def test_negative_z_is_accepted_when_background_is_lower(cfg, job):
    _set_z(job, background=-100)
    for value, want in (("-50", -50), ("-1e1", -10), ("-.5", -0.5)):
        assert _cli(cfg, job, "ball-1", "--z", value) == 0
        assert _layer(_spec(job), "ball-1")["z"] == want


def test_negative_value_rewrite_scope():
    from animator.cli import join_anim_negative_values as join

    assert join(["layers", "z", "j", "l", "--z", "-1e3"])[-1] == "--z=-1e3"
    assert join(["-v", "--config", "c", "layers", "z", "j", "l", "--z", "-5"])[-1] == "--z=-5"
    assert join(["anim", "add", "j", "l", "move", "--by", "-1,0"])[-1] == "--by=-1,0"     # M2 不變
    for argv in (["layers", "list", "--z", "-1"],                     # 其他子指令完全不變
                 ["layers", "z", "j", "l", "--above", "-x"],          # 只改寫 --z
                 ["anim", "add", "j", "l", "move", "--z", "-1"],      # --z 不是 anim add 的選項
                 ["layers", "z", "j", "l", "--z", "--above"],         # 缺值：交給 argparse
                 ["render", "s.json", "--z", "-1"]):
        assert join(list(argv)) == argv


# ---------------------------------------------------------------- 失敗時不改 spec（共用的安全流程）

def test_write_failure_keeps_original_and_leaves_no_temp_or_backup(cfg, job, monkeypatch):
    from animator.animate import edit

    original = (job / "spec.json").read_bytes()

    def broken_replace(src, dst):
        raise OSError("模擬：磁碟已滿")

    monkeypatch.setattr(edit.os, "replace", broken_replace)
    with pytest.raises(OSError, match="磁碟已滿"):
        edit.set_layer_z(job / "spec.json", "ball-1", z=5)
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []
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
        edit.set_layer_z(job / "spec.json", "ball-1", z=5)
    assert _backups(job) == []


def test_layers_list_shows_new_z(cfg, job, capsys):
    from animator import cli

    assert _cli(cfg, job, "box-1", "--above", "ball-1") == 0
    capsys.readouterr()
    assert cli.main(["--config", str(cfg.config_file), "layers", "list", job.name]) == 0
    lines = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()[2:]}
    assert lines["box-1"].split()[2] == "30"


# ---------------------------------------------------------------- 大於 2**53 的 z：和 Stage 1 的 float 排序一致

BIG = 2 ** 60                         # float 在這附近的間隔是 256


def _stage1_order(cfg, job):
    """Stage 1 實際的渲染順序：resolve 的 (float(z), 陣列索引) → rank。"""
    from animator.spec import load_spec
    from animator.spec.resolve import resolve

    spec_path = job / "spec.json"
    resolved = resolve(load_spec(spec_path), spec_path, cfg)
    return [l["id"] for l in sorted(resolved["layers"], key=lambda l: l["rank"])]


def test_big_int_z_beyond_top_reference_is_refused(cfg, job, capsys):
    """2**60 + 10 和 2**60 是同一個 float：放不到更前面，必須拒絕（修正前會回報錯誤的順序）。"""
    _set_z(job, ball_1=BIG)
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "box-1", "--above", "ball-1") == 1
    assert "找不到更前面的數值" in capsys.readouterr().err
    assert _unchanged(job, original)


def test_distinct_json_ints_with_the_same_float_are_a_tie(cfg, job, capsys):
    _set_z(job, ball_1=BIG, box_1=BIG + 1)                            # 兩個不同的整數 → 同一個 float
    _add_layer(job, "star-1", 30)
    spec = _spec(job)
    assert _layer(spec, "ball-1")["z"] != _layer(spec, "box-1")["z"]
    assert float(_layer(spec, "ball-1")["z"]) == float(_layer(spec, "box-1")["z"])
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "star-1", "--above", "ball-1") == 1         # Stage 1 視為同 z：ball-1 → box-1
    assert _cli(cfg, job, "star-1", "--below", "box-1") == 1
    err = capsys.readouterr().err
    assert err.count("z 值相同") == 2
    assert _unchanged(job, original)


@pytest.mark.parametrize("args", [["--above", "ball-1"], ["--below", "box-1"]])
def test_adjacent_big_floats_have_no_representable_gap(cfg, job, capsys, args):
    _set_z(job, ball_1=BIG, box_1=BIG + 256)                          # 相鄰的兩個 float，中間沒有 float
    _add_layer(job, "star-1", 30)
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "star-1", *args) == 1
    assert "沒有可以表示的數值" in capsys.readouterr().err
    assert _unchanged(job, original)


def test_big_z_insertion_matches_stage1_order(cfg, job):
    from animator.animate.edit import set_layer_z

    _set_z(job, ball_1=BIG, box_1=2 * BIG)
    _add_layer(job, "star-1", 30)
    res = set_layer_z(job / "spec.json", "star-1", above="ball-1")
    z = _layer(_spec(job), "star-1")["z"]
    assert float(BIG) < float(z) < float(2 * BIG)                     # 用 Stage 1 的 float 比較也是嚴格介於中間
    assert res.order == ["background", "ball-1", "star-1", "box-1"] == _stage1_order(cfg, job)


def test_reported_order_matches_stage1_for_float_ties(cfg, job):
    """不同整數、同一個 float：Stage 1 用陣列順序排；回報的順序也要一樣。"""
    from animator.animate.edit import set_layer_z

    _set_z(job, ball_1=BIG + 1, box_1=BIG)                            # 整數比較：box < ball；float 比較：同 z
    res = set_layer_z(job / "spec.json", "ball-1", z=40)              # 改 ball-1 之外，box-1 仍在 2**60
    assert res.order == _stage1_order(cfg, job) == ["background", "ball-1", "box-1"]
    _set_z(job, ball_1=BIG + 1)
    _add_layer(job, "star-1", 5)
    res = set_layer_z(job / "spec.json", "star-1", z=6)
    assert res.order == _stage1_order(cfg, job) == ["background", "star-1", "ball-1", "box-1"]


def test_big_int_z_equal_to_background_as_float_is_refused(cfg, job, capsys):
    from animator.animate.edit import set_layer_z
    from animator.errors import AnimatorError

    _set_z(job, background=BIG)
    original = (job / "spec.json").read_bytes()
    with pytest.raises(AnimatorError, match="不能小於或等於背景"):
        set_layer_z(job / "spec.json", "ball-1", z=BIG + 1)          # 整數比背景大，但 float 相同
    assert _unchanged(job, original)


def test_z_beyond_float_range_never_enters_the_spec(cfg, job):
    from animator.animate.edit import set_layer_z
    from animator.errors import AnimatorError

    original = (job / "spec.json").read_bytes()
    with pytest.raises(AnimatorError, match="有限的數字"):
        set_layer_z(job / "spec.json", "ball-1", z=10 ** 400)        # float(10**400) 會 OverflowError
    assert _unchanged(job, original)
    _set_z(job, box_1=10 ** 400)                                      # spec 裡已經有超過 float 範圍的整數
    original = (job / "spec.json").read_bytes()
    with pytest.raises(AnimatorError, match="不是有限的數字"):
        set_layer_z(job / "spec.json", "ball-1", z=5)
    assert _unchanged(job, original)
