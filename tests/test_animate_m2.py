"""S2-2 M2：anim add scale / rotate / opacity，以及 --by / --to 的負數（只用標準函式庫）。

通用的備份、原子寫入、失敗清理測試在 tests/test_animate.py（add_move 和 add_animation 共用同一條路徑）。
"""

from __future__ import annotations

import json

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


def _backups(job):
    d = job / "history" / "edits"
    return sorted(d.iterdir()) if d.is_dir() else []


def _cli(cfg, job, *args):
    from animator import cli

    return cli.main(["--config", str(cfg.config_file), "anim", "add", job.name, *args])


def _last(job):
    return _spec(job)["animations"][-1]


# ---------------------------------------------------------------- 產生的 Stage 1 動畫

@pytest.mark.parametrize("effect, option, value, params", [
    ("scale", "--to", "1.5", {"to": 1.5}),                       # 一個數字 → 純量
    ("scale", "--to", "2", {"to": 2}),
    ("scale", "--to", "1.2,0.8", {"to": [1.2, 0.8]}),            # 兩個數字 → [sx, sy]
    ("rotate", "--by", "-45", {"by": -45}),
    ("rotate", "--to", "90", {"to": 90}),
    ("rotate", "--to", "-90.5", {"to": -90.5}),
    ("opacity", "--to", "0", {"to": 0}),
    ("opacity", "--to", "0.25", {"to": 0.25}),
    ("opacity", "--to", "1", {"to": 1}),
])
def test_cli_generates_exact_stage1_entry(cfg, job, capsys, effect, option, value, params):
    from animator.spec.validate import validate

    before = _spec(job)
    assert _cli(cfg, job, "ball-1", effect, option, value, "--start", "0.25", "--duration", "0.5") == 0
    after = _spec(job)
    assert after["animations"] == before["animations"] + [
        {"id": f"ball-1-{effect}-1", "target": "ball-1", "effect": effect, "start": 0.25, "end": 0.75,
         "params": params}]                                          # 沒有 from、沒有 easing
    for key in ("version", "meta", "canvas", "layers"):
        assert after[key] == before[key]
    assert list(after) == list(before)
    assert validate(after, job).ok
    assert len(_backups(job)) == 1
    assert f"ball-1-{effect}-1" in capsys.readouterr().out


def test_add_animation_api_and_add_move_wrapper(cfg, job):
    from animator.animate.edit import add_animation, add_move

    a = add_animation(job / "spec.json", "ball-1", "scale", {"to": [1.5, 1.5]}, start=0, duration=0.5).animation
    b = add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5).animation
    assert a == {"id": "ball-1-scale-1", "target": "ball-1", "effect": "scale", "start": 0, "end": 0.5,
                 "params": {"to": [1.5, 1.5]}}
    assert b["id"] == "ball-1-move-1" and b["params"] == {"by": [10, 0]}


def test_ids_are_numbered_per_effect(cfg, job):
    steps = [("scale", "--to", "1.2", "0"), ("scale", "--to", "1", "0.5"), ("rotate", "--by", "10", "0"),
             ("opacity", "--to", "0.5", "0"), ("move", "--by", "5,0", "0"), ("rotate", "--by", "10", "0.5")]
    for effect, opt, value, start in steps:
        assert _cli(cfg, job, "ball-1", effect, opt, value, "--start", start, "--duration", "0.5") == 0
    ids = [a["id"] for a in _spec(job)["animations"][1:]]
    assert ids == ["ball-1-scale-1", "ball-1-scale-2", "ball-1-rotate-1", "ball-1-opacity-1",
                   "ball-1-move-1", "ball-1-rotate-2"]


# ---------------------------------------------------------------- 拒絕（結束碼 1：參數 / 格式）

@pytest.mark.parametrize("effect, value", [("scale", "1.5"), ("opacity", "0.5")])
def test_by_is_rejected_for_scale_and_opacity(cfg, job, capsys, effect, value):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", effect, "--by", value, "--start", "0", "--duration", "0.5") == 1
    assert f"{effect} 不支援 --by" in capsys.readouterr().err
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []


@pytest.mark.parametrize("effect, option, value", [
    ("scale", "--to", "abc"), ("scale", "--to", "1,2,3"), ("scale", "--to", ""), ("scale", "--to", "nan"),
    ("scale", "--to", "1,x"), ("scale", "--to", "inf"),
    ("rotate", "--by", "1,2"), ("rotate", "--to", "x"), ("rotate", "--by", "inf"), ("rotate", "--to", "-nan"),
    ("opacity", "--to", "a"), ("opacity", "--to", "0.5,0.5"), ("opacity", "--to", "nan"),
    ("move", "--by", "-1"), ("move", "--to", "-x,1"),
])
def test_invalid_numeric_formats_exit_1(cfg, job, capsys, effect, option, value):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", effect, option, value, "--start", "0", "--duration", "0.5") == 1
    err = capsys.readouterr().err
    assert "[FAIL]" in err and "Traceback" not in err
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []


@pytest.mark.parametrize("effect, params, match", [
    ("scale", {"by": 2}, "不支援 --by"),
    ("opacity", {"by": 0.1}, "不支援 --by"),
    ("scale", {}, "必須指定 --to"),
    ("rotate", {}, "--by 或 --to"),
    ("rotate", {"by": 1, "to": 2}, "--by 或 --to"),
    ("rotate", {"from": 0, "to": 90}, "不支援 --from"),        # 這個版本不寫 from
    ("fade_in", {}, "不支援的效果"),
    ("fade_out", {}, "不支援的效果"),
])
def test_api_rejects_unsupported_requests(cfg, job, effect, params, match):
    from animator.animate.edit import add_animation
    from animator.errors import AnimatorError

    original = (job / "spec.json").read_bytes()
    with pytest.raises(AnimatorError, match=match):
        add_animation(job / "spec.json", "ball-1", effect, params, start=0, duration=0.5)
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []


@pytest.mark.parametrize("effect, option, value", [
    ("scale", "--to", "1.5"), ("rotate", "--by", "-45"), ("opacity", "--to", "0")])
def test_background_is_refused_for_every_effect(cfg, job, capsys, effect, option, value):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "background", effect, option, value, "--start", "0", "--duration", "0.5") == 1
    assert "背景" in capsys.readouterr().err
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []


def test_unsupported_effect_is_an_argparse_error(cfg, job):
    for effect in ("fade_in", "fade_out", "glow"):
        with pytest.raises(SystemExit):
            _cli(cfg, job, "ball-1", effect, "--to", "1", "--start", "0", "--duration", "0.5")


# ---------------------------------------------------------------- Stage 1 驗證（結束碼 2）

@pytest.mark.parametrize("effect, value, match", [
    ("scale", "0", "大於 0"), ("scale", "-1", "大於 0"), ("scale", "1,-0.5", "大於 0"), ("scale", "0,1", "大於 0"),
    ("opacity", "1.5", "0~1"), ("opacity", "-0.1", "0~1"),
])
def test_stage1_rejects_invalid_scale_and_opacity_values(cfg, job, capsys, effect, value, match):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", effect, "--to", value, "--start", "0", "--duration", "0.5") == 2
    assert match in capsys.readouterr().err
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []


def test_stage1_rejects_non_finite_rotation_through_api(cfg, job):
    from animator.animate.edit import add_animation
    from animator.errors import SpecError

    original = (job / "spec.json").read_bytes()
    with pytest.raises(SpecError, match="格式錯誤"):
        add_animation(job / "spec.json", "ball-1", "rotate", {"by": float("inf")}, start=0, duration=0.5)
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []


@pytest.mark.parametrize("effect, option, first, second, channel", [
    ("scale", "--to", "1.5", "1", "scale"),
    ("rotate", "--by", "30", "-30", "rotation"),
    ("opacity", "--to", "0.5", "1", "opacity"),
])
def test_same_channel_overlap_is_rejected(cfg, job, capsys, effect, option, first, second, channel):
    assert _cli(cfg, job, "ball-1", effect, option, first, "--start", "0", "--duration", "0.5") == 0
    before = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, "ball-1", effect, option, second, "--start", "0.25", "--duration", "0.5") == 2
    assert f"{channel} 通道上時間重疊" in capsys.readouterr().err
    assert (job / "spec.json").read_bytes() == before and len(_backups(job)) == 1
    # 頭尾相接可以
    assert _cli(cfg, job, "ball-1", effect, option, second, "--start", "0.5", "--duration", "0.25") == 0


def test_different_channels_may_overlap_existing_move(cfg, job):
    """box-1 已經有 box-1-hand（move，0~0.5 s）；同一時間加 scale / rotate / opacity 都可以。"""
    from animator.spec.validate import validate

    for effect, opt, value in (("scale", "--to", "1.5"), ("rotate", "--by", "-90"), ("opacity", "--to", "0.5")):
        assert _cli(cfg, job, "box-1", effect, opt, value, "--start", "0", "--duration", "0.5") == 0
    spec = _spec(job)
    assert [a["effect"] for a in spec["animations"] if a["target"] == "box-1"] == ["move", "scale", "rotate", "opacity"]
    assert spec["animations"][0]["x_user_note"] == "hand written"     # 既有的 move 沒有被改
    assert validate(spec, job).ok
    # 同一個 position 通道仍然不能重疊
    assert _cli(cfg, job, "box-1", "move", "--by", "1,0", "--start", "0.25", "--duration", "0.5") == 2


# ---------------------------------------------------------------- 負數（M1 的 argparse 問題）

@pytest.mark.parametrize("effect, option, value, params", [
    ("move", "--by", "-120,0", {"by": [-120, 0]}),
    ("move", "--by", "-12.5,-3", {"by": [-12.5, -3]}),
    ("move", "--to", "-5.5,3", {"to": [-5.5, 3]}),
    ("move", "--by", "-.5,1", {"by": [-0.5, 1]}),
    ("rotate", "--by", "-45", {"by": -45}),
    ("rotate", "--to", "-90", {"to": -90}),
    ("rotate", "--by", "-0.5", {"by": -0.5}),
])
def test_negative_values_through_the_real_cli(cfg, job, effect, option, value, params):
    assert _cli(cfg, job, "ball-1", effect, option, value, "--start", "0", "--duration", "0.5") == 0
    assert _last(job)["params"] == params


def test_negative_values_with_equals_and_global_options(cfg, job):
    from animator import cli

    assert _cli(cfg, job, "ball-1", "move", "--by=-120,0", "--start", "0", "--duration", "0.25") == 0
    assert _last(job)["params"] == {"by": [-120, 0]}
    argv = ["-v", "--config", str(cfg.config_file), "anim", "add", job.name, "ball-1", "rotate",
            "--start", "0", "--duration", "0.5", "--by", "-30"]       # 全域選項在前、--by 放最後
    assert cli.main(argv) == 0
    assert _last(job)["params"] == {"by": -30}


def test_negative_value_rewrite_is_limited_to_anim_add():
    from animator.cli import join_anim_negative_values as join

    assert join(["anim", "add", "j", "l", "move", "--by", "-1,2"])[-1] == "--by=-1,2"
    assert join(["--config", "c.yaml", "anim", "add", "j", "l", "rotate", "--to", "-9"])[-1] == "--to=-9"
    for argv in (["render", "s.json", "--by", "-1,2"],                # 其他指令完全不變
                 ["segment", "--image", "x.png", "--labels", "-a"],
                 ["--config", "anim", "add", "--by", "-1"],           # "anim" 是 --config 的值，不是子指令
                 ["anim", "add", "j", "l", "move", "--by", "--start", "0"],   # 缺值：交給 argparse 報錯
                 ["anim", "add", "j", "l", "move", "--by", "-h"]):
        assert join(list(argv)) == argv


def test_missing_value_is_still_an_argparse_error(cfg, job):
    with pytest.raises(SystemExit):
        _cli(cfg, job, "ball-1", "move", "--by", "--start", "0", "--duration", "0.5")


def test_global_value_options_match_parser():
    from animator.cli import GLOBAL_VALUE_OPTIONS, build_parser

    p = build_parser()
    takes_value = {s for a in p._actions if a.option_strings and a.nargs != 0 and not a.option_strings[0] == "-h"
                   for s in a.option_strings if s.startswith("--") and a.dest not in ("help", "version")}
    assert set(GLOBAL_VALUE_OPTIONS) == takes_value
