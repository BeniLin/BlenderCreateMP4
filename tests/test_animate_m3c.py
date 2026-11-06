"""S2-2 M3c：anim add ... visibility --to visible|hidden（只用標準函式庫）。

合成 job（tests/test_animate.py 的 make_job）：30 fps、1 秒（30 格）；ball-1、box-1（有 box-1-hand move 0~0.5 s）、背景。
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


def _write(job, spec):
    (job / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _backups(job):
    d = job / "history" / "edits"
    return sorted(d.iterdir()) if d.is_dir() else []


def _layer(spec, layer_id):
    return next(l for l in spec["layers"] if l["id"] == layer_id)


def _hide_layer(job, layer_id):
    spec = _spec(job)
    _layer(spec, layer_id)["visible"] = False
    _write(job, spec)


def _cli(cfg, job, *args):
    from animator import cli

    return cli.main(["--config", str(cfg.config_file), "anim", "add", job.name, *args])


def _vis(cfg, job, layer, state, start="0.25", duration="0.5"):
    return _cli(cfg, job, layer, "visibility", "--to", state, "--start", start, "--duration", duration)


def _unchanged(job, original):
    return (job / "spec.json").read_bytes() == original and _backups(job) == []


# ---------------------------------------------------------------- 成功

def test_hide_writes_exact_stage1_entry(cfg, job, capsys):
    from animator.spec.validate import validate

    before = _spec(job)
    assert _vis(cfg, job, "ball-1", "hidden") == 0
    after = _spec(job)
    assert after["animations"] == before["animations"] + [
        {"id": "ball-1-visibility-1", "target": "ball-1", "effect": "visibility", "start": 0.25, "end": 0.75,
         "params": {"visible": False}}]                               # 沒有 from / to / easing
    for key in ("version", "meta", "canvas", "layers"):
        assert after[key] == before[key]
    assert validate(after, job).ok and len(_backups(job)) == 1
    assert 'ball-1-visibility-1：ball-1 visibility {"visible": false}' in capsys.readouterr().out


def test_show_on_hidden_layer_keeps_it_in_the_resolved_spec(cfg, job):
    from animator.spec import load_spec
    from animator.spec.resolve import resolve

    _hide_layer(job, "ball-1")
    assert _vis(cfg, job, "ball-1", "visible", "0.5", "0.5") == 0
    assert _spec(job)["animations"][-1]["params"] == {"visible": True}
    res = resolve(load_spec(job / "spec.json"), job / "spec.json", cfg)
    ball = next(l for l in res["layers"] if l["id"] == "ball-1")
    assert ball["visibility"] == 0.0
    seg = next(a for a in res["animations"] if a["channel"] == "visibility")
    assert (seg["frame_start"], seg["frame_end"], seg["from"], seg["to"]) == (16, 30, [1.0], [1.0])   # 到結尾


def test_state_is_case_insensitive(cfg, job):
    assert _vis(cfg, job, "ball-1", "Hidden") == 0
    assert _spec(job)["animations"][-1]["params"] == {"visible": False}


def test_back_to_back_windows_and_ids(cfg, job):
    assert _vis(cfg, job, "ball-1", "hidden", "0", "0.25") == 0
    assert _vis(cfg, job, "ball-1", "hidden", "0.25", "0.25") == 0  # 頭尾相接可以
    ids = [a["id"] for a in _spec(job)["animations"][1:]]
    assert ids == ["ball-1-visibility-1", "ball-1-visibility-2"]


def test_visibility_may_overlap_other_channels(cfg, job):
    """box-1 有 box-1-hand（move 0~0.5 s）；同時間加 visibility、opacity 都可以。"""
    assert _vis(cfg, job, "box-1", "hidden", "0", "0.5") == 0
    assert _cli(cfg, job, "box-1", "opacity", "--to", "0.5", "--start", "0", "--duration", "0.5") == 0
    assert [a["effect"] for a in _spec(job)["animations"]] == ["move", "visibility", "opacity"]


def test_api_uses_cli_option_names(cfg, job):
    from animator.animate.edit import add_animation
    from animator.errors import AnimatorError

    res = add_animation(job / "spec.json", "ball-1", "visibility", {"to": False}, start=0, duration=0.5)
    assert res.animation["params"] == {"visible": False}
    original = (job / "spec.json").read_bytes()
    for params, match in (({"visible": False}, "不支援 --visible"), ({"to": "no"}, "visible 或 hidden"),
                          ({}, "必須指定 --to"), ({"by": True}, "不支援 --by")):
        with pytest.raises(AnimatorError, match=match):
            add_animation(job / "spec.json", "ball-1", "visibility", params, start=0.5, duration=0.25)
    assert (job / "spec.json").read_bytes() == original and len(_backups(job)) == 1


# ---------------------------------------------------------------- 拒絕（結束碼 1）

@pytest.mark.parametrize("args, match", [
    (["ball-1", "visibility", "--to", "on"], "--to 必須是 visible 或 hidden"),
    (["ball-1", "visibility", "--to", "true"], "--to 必須是 visible 或 hidden"),
    (["ball-1", "visibility", "--to", ""], "--to 必須是 visible 或 hidden"),
    (["ball-1", "visibility", "--by", "hidden"], "visibility 不支援 --by"),
    (["nope", "visibility", "--to", "hidden"], "找不到圖層 'nope'"),
    (["background", "visibility", "--to", "hidden"], "背景"),
    (["ball-1", "visibility", "--to", "visible"], "本來就是 visible"),       # 沒有效果
])
def test_rejected_requests_exit_1(cfg, job, capsys, args, match):
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, job, *args, "--start", "0", "--duration", "0.5") == 1
    err = capsys.readouterr().err
    assert match in err and "Traceback" not in err
    assert _unchanged(job, original)


def test_hiding_an_already_hidden_layer_is_rejected(cfg, job, capsys):
    _hide_layer(job, "ball-1")
    original = (job / "spec.json").read_bytes()
    assert _vis(cfg, job, "ball-1", "hidden") == 1
    assert "本來就是 hidden" in capsys.readouterr().err
    assert _unchanged(job, original)


# ---------------------------------------------------------------- Stage 1 的時間驗證（結束碼 2）

@pytest.mark.parametrize("start, duration, match", [
    ("0.8", "0.5", "canvas.duration"),
    ("0.5", "0.01", "不到 1 格"),
    ("-0.1", "0.5", "不可以小於 0"),
])
def test_timing_errors_exit_2(cfg, job, capsys, start, duration, match):
    original = (job / "spec.json").read_bytes()
    assert _vis(cfg, job, "ball-1", "hidden", start, duration) == 2
    assert match in capsys.readouterr().err
    assert _unchanged(job, original)


def test_overlapping_windows_exit_2(cfg, job, capsys):
    assert _vis(cfg, job, "ball-1", "hidden", "0", "0.5") == 0
    before = (job / "spec.json").read_bytes()
    assert _vis(cfg, job, "ball-1", "hidden", "0.25", "0.5") == 2
    assert "visibility 通道上時間重疊" in capsys.readouterr().err
    assert (job / "spec.json").read_bytes() == before and len(_backups(job)) == 1


def test_argparse_requires_to(cfg, job):
    with pytest.raises(SystemExit):
        _cli(cfg, job, "ball-1", "visibility", "--start", "0", "--duration", "0.5")


# ---------------------------------------------------------------- 安全的寫入流程

def test_write_failure_keeps_original_and_leaves_no_temp_or_backup(cfg, job, monkeypatch):
    from animator.animate import edit

    original = (job / "spec.json").read_bytes()

    def broken_replace(src, dst):
        raise OSError("模擬：磁碟已滿")

    monkeypatch.setattr(edit.os, "replace", broken_replace)
    with pytest.raises(OSError, match="磁碟已滿"):
        edit.add_animation(job / "spec.json", "ball-1", "visibility", {"to": False}, start=0, duration=0.5)
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
        edit.add_animation(job / "spec.json", "ball-1", "visibility", {"to": False}, start=0, duration=0.5)
    assert _backups(job) == []


def test_backup_holds_exact_previous_bytes(cfg, job):
    from animator.animate.edit import add_animation

    original = (job / "spec.json").read_bytes()
    res = add_animation(job / "spec.json", "ball-1", "visibility", {"to": False}, start=0, duration=0.5)
    assert res.backup.read_bytes() == original
