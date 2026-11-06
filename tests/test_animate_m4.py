"""S2-2 M4：修補區域 / 前後順序的提醒（animate/warnings.py、anim check；只用標準函式庫）。

合成 job（tests/test_animate.py 的 make_job，30 fps、1 秒）+ 手寫的 segmentation.json（不需要真的分割）：
ball-1（24×24，crop_box 28..52 × 52..76，z 20）、box-1（24×24，crop_box 138..162 × 52..76，z 10；
有 box-1-hand move 0~0.5 s）。兩個物件都用自動排序（z_rule = smaller_area_in_front）。
"""

from __future__ import annotations

import json

import pytest

from test_animate import make_job

AUTO = "smaller_area_in_front"


@pytest.fixture
def cfg(make_config):
    return make_config()


def _region(index, status="passed_checks", repaired=True, **extra):
    r = {"index": index, "area_px": 1000, "area_ratio": 0.05, "box": [20, 44, 170, 84], "status": status,
         "backend": "migan", "reasons": [], "checks": {}, **extra}
    if repaired is not None:
        r["repaired"] = repaired
    return r


def _job(cfg, *, regions=None, associations="default", bg_status="passed_checks", anims=None, layer_meta=None,
         seg=True):
    job = make_job(cfg.path("output_dir"))
    spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
    if anims is not None:
        spec["animations"] = anims
    if seg:
        spec["meta"]["segmentation"] = "segmentation/segmentation.json"
        for layer in spec["layers"][1:]:
            layer["meta"]["z_rule"] = AUTO
        next(l for l in spec["layers"] if l["id"] == "box-1")["meta"]["crop_box"] = [138, 52, 162, 76]
    for layer_id, meta in (layer_meta or {}).items():
        next(l for l in spec["layers"] if l["id"] == layer_id)["meta"].update(meta)
    (job / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if seg:
        data = {"version": 1, "background": {"status": bg_status,
                                             "regions": regions if regions is not None else [_region(0), _region(1)]}}
        if associations == "default":
            associations = {"version": 1, "regions": {"0": ["ball-1"], "1": ["box-1"]}, "overlaps": []}
        if associations is not None:
            data["associations"] = associations
        (job / "segmentation").mkdir()
        (job / "segmentation" / "segmentation.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return job


def _anim(target, effect, start, end, **params):
    return {"id": f"{target}-{effect}-{start}", "target": target, "effect": effect, "start": start, "end": end,
            "params": params}


HAND = _anim("box-1", "move", 0.0, 0.5, by=[0, -20])


def _analyze(cfg, job):
    from animator.animate.warnings import analyze

    return analyze(job / "spec.json", cfg)


def _only(report, kind, layer=None):
    return [f for f in report.findings if f.kind == kind and (layer is None or layer in f.layers)]


# ---------------------------------------------------------------- 沒有提醒

def test_static_layers_produce_no_findings(cfg):
    report = _analyze(cfg, _job(cfg, anims=[]))
    assert report.findings == [] and report.associations_known and report.background_status == "passed_checks"


def test_pivot_edit_alone_produces_no_findings(cfg):
    from animator.animate.edit import set_layer_pivot

    job = _job(cfg, anims=[])
    set_layer_pivot(job / "spec.json", "ball-1", [40, 74])
    assert _analyze(cfg, job).findings == []                          # pivot 本身不改變畫面


def test_jobs_without_segmentation_metadata_have_no_repair_findings(cfg):
    job = _job(cfg, seg=False, anims=[HAND, _anim("ball-1", "visibility", 0.0, 0.5, visible=False)])
    report = _analyze(cfg, job)
    assert report.segmentation is None and report.findings == []     # 純 Stage 1 job（也沒有 z_rule）


def test_missing_segmentation_file_is_treated_as_no_metadata(cfg):
    job = _job(cfg, anims=[HAND])
    (job / "segmentation" / "segmentation.json").unlink()
    assert _only(_analyze(cfg, job), "exposure") == [] and _analyze(cfg, job).segmentation is None


def test_unchanged_background_produces_no_exposure(cfg):
    job = _job(cfg, bg_status="unchanged", regions=[], associations={"version": 1, "regions": {}, "overlaps": []},
               anims=[HAND])
    assert _only(_analyze(cfg, job), "exposure") == [] and _only(_analyze(cfg, job), "unknown_association") == []


# ---------------------------------------------------------------- exposure 規則

@pytest.mark.parametrize("anim, severity, text", [
    (_anim("ball-1", "move", 0.5, 0.8, by=[30, 0]), "warning", "move"),
    (_anim("ball-1", "rotate", 0.5, 0.8, by=45), "warning", "rotate"),
    (_anim("ball-1", "scale", 0.5, 0.8, to=0.5), "warning", "scale < 1"),
    (_anim("ball-1", "scale", 0.5, 0.8, to=[1, 0.8]), "warning", "scale < 1"),
    (_anim("ball-1", "visibility", 0.5, 0.8, visible=False), "warning", "visibility 隱藏"),
    (_anim("ball-1", "opacity", 0.5, 0.8, to=0.5), "notice", "opacity < 1"),
    (_anim("ball-1", "scale", 0.5, 0.8, to=1.5), "notice", "scale > 1"),
])
def test_each_exposure_rule(cfg, anim, severity, text):
    report = _analyze(cfg, _job(cfg, anims=[anim]))
    f, = _only(report, "exposure", "ball-1")
    assert (f.severity, f.layers, f.regions, f.time, f.frame) == (severity, ["ball-1"], [0], 0.5, 16)
    assert text in f.message and "區域 0（已修補，只通過自動檢查（沒有人工確認））" in f.message


def test_hidden_base_layer_is_an_exposure_from_frame_1(cfg):
    for anims in ([], [_anim("ball-1", "visibility", 0.5, 0.8, visible=True)]):    # 被 resolve 移除 / 保留
        job = _job(cfg, anims=anims)
        spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
        next(l for l in spec["layers"] if l["id"] == "ball-1")["visible"] = False
        (job / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        f, = _only(_analyze(cfg, job), "exposure", "ball-1")
        assert (f.severity, f.frame) == ("warning", 1) and "visible 是 false" in f.message
        import shutil
        shutil.rmtree(job)


def test_initial_pose_differing_from_segmentation_is_detected(cfg):
    job = _job(cfg, anims=[])
    spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
    next(l for l in spec["layers"] if l["id"] == "ball-1")["position"] = [45, 64]   # 手動改了初始位置
    (job / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    f, = _only(_analyze(cfg, job), "exposure", "ball-1")
    assert (f.severity, f.frame) == ("warning", 1) and "初始位置" in f.message


def test_pose_is_not_guessed_without_crop_box(cfg):
    job = _job(cfg, anims=[], layer_meta={"ball-1": {"crop_box": None}})
    spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
    next(l for l in spec["layers"] if l["id"] == "ball-1")["position"] = [45, 64]
    (job / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    assert _only(_analyze(cfg, job), "exposure", "ball-1") == []      # 不知道原本的姿勢：不猜


def test_findings_are_deduplicated_per_layer_with_earliest_time(cfg):
    anims = [_anim("ball-1", "move", 0.6, 0.7, by=[5, 0]), _anim("ball-1", "move", 0.7, 0.8, by=[5, 0]),
             _anim("ball-1", "rotate", 0.2, 0.4, by=10), _anim("ball-1", "opacity", 0.1, 0.2, to=0.9)]
    f, = _only(_analyze(cfg, _job(cfg, anims=anims)), "exposure", "ball-1")
    assert f.severity == "warning" and (f.time, f.frame) == (0.1, 4)  # 最早的是 opacity（0.1 s）
    assert f.message.count("move") == 1 and "rotate" in f.message and "opacity" in f.message


def test_box_hand_move_is_reported_once_for_box(cfg):
    report = _analyze(cfg, _job(cfg, anims=[HAND]))
    f, = _only(report, "exposure", "box-1")
    assert f.regions == [1] and (f.time, f.frame) == (0.0, 1)


# ---------------------------------------------------------------- 區域的狀態

@pytest.mark.parametrize("region, text", [
    (_region(0, "passed_checks", True), "已修補，只通過自動檢查"),
    (_region(0, "needs_review", True), "已修補，需要人工檢查"),
    (_region(0, "failed_checks", False), "沒有修補：原本的物件仍留在背景上"),
    (_region(0, "passed_checks", None), "修補結果沒有記錄（passed_checks）"),   # 缺少 repaired：不假設已修補
])
def test_region_state_is_described_without_assumptions(cfg, region, text):
    job = _job(cfg, regions=[region, _region(1)], bg_status="incomplete" if region.get("repaired") is False else
               "passed_checks", anims=[_anim("ball-1", "move", 0.5, 0.8, by=[30, 0])])
    f, = _only(_analyze(cfg, job), "exposure", "ball-1")
    assert text in f.message


def test_region_shared_by_two_objects(cfg):
    assoc = {"version": 1, "regions": {"0": ["ball-1", "box-1"]}, "overlaps": []}
    report = _analyze(cfg, _job(cfg, regions=[_region(0)], associations=assoc, anims=[HAND]))
    f, = _only(report, "exposure", "box-1")
    assert f.regions == [0] and _only(report, "exposure", "ball-1") == []   # ball-1 沒動


def test_layer_without_associated_region_is_skipped(cfg):
    assoc = {"version": 1, "regions": {"0": ["ball-1"]}, "overlaps": []}  # box-1 不在任何區域
    assert _only(_analyze(cfg, _job(cfg, associations=assoc, anims=[HAND])), "exposure", "box-1") == []


# ---------------------------------------------------------------- 關聯不明（舊的 job）

def test_unknown_association_gives_one_notice_per_job(cfg):
    anims = [HAND, _anim("ball-1", "rotate", 0.5, 0.8, by=30)]
    report = _analyze(cfg, _job(cfg, associations=None, anims=anims))
    f, = report.findings[:1] if len(report.findings) == 1 else [x for x in report.findings
                                                                if x.kind == "unknown_association"]
    assert f.severity == "notice" and f.regions is None and sorted(f.layers) == ["ball-1", "box-1"]
    assert "沒有「物件 ↔ 補洞區域」的關聯" in f.message and (f.time, f.frame) == (0.0, 1)
    assert _only(report, "exposure") == [] and len(_only(report, "unknown_association")) == 1


def test_unknown_association_without_exposure_is_silent(cfg):
    report = _analyze(cfg, _job(cfg, associations=None, anims=[]))
    assert _only(report, "unknown_association") == [] and not report.associations_known


def test_legacy_metadata_from_a_real_old_job_is_accepted(cfg):
    """2026-11-04 的舊 job 格式：區域沒有 repaired / attempts，background 沒有 unrepaired_regions，沒有 associations。"""
    old_region = {"index": 0, "area_px": 198736, "area_ratio": 0.0719, "box": [740, 209, 1217, 1083],
                  "status": "passed_checks", "backend": "migan", "reasons": [], "checks": {"passed": True}}
    report = _analyze(cfg, _job(cfg, regions=[old_region], associations=None, anims=[HAND]))
    assert [f.kind for f in report.findings] == ["unknown_association"]


# ---------------------------------------------------------------- 前後順序

def _move_layer(job, layer_id, position, crop_box):
    spec = json.loads((job / "spec.json").read_text(encoding="utf-8"))
    layer = next(l for l in spec["layers"] if l["id"] == layer_id)
    layer["position"], layer["meta"]["crop_box"] = position, crop_box
    (job / "spec.json").write_text(json.dumps(spec), encoding="utf-8")


def test_frame_1_overlap_of_automatic_layers(cfg):
    assoc = {"version": 1, "regions": {"0": ["ball-1", "box-1"]},
             "overlaps": [{"behind": "box-1", "front": "ball-1", "px": 18}]}
    job = _job(cfg, regions=[_region(0)], associations=assoc, anims=[])
    _move_layer(job, "box-1", [50, 64], [38, 52, 62, 76])             # 和 ball-1 重疊
    f, = _only(_analyze(cfg, job), "depth")
    assert (f.severity, f.layers, f.frame) == ("notice", ["box-1", "ball-1"], 1)
    assert "自動決定" in f.message and "重疊 18 px" in f.message and "layers z" in f.message


def test_overlap_introduced_by_animation(cfg):
    anims = [_anim("ball-1", "move", 0.5, 0.9, to=[150, 64])]          # 飛到 box-1 的位置
    f, = _only(_analyze(cfg, _job(cfg, anims=anims)), "depth")
    assert f.layers == ["box-1", "ball-1"] and (f.time, f.frame) == (0.5, 16)  # ball-1 從 0.5 s 開始移動
    assert "動畫中可能重疊" in f.message


def test_rotation_uses_a_conservative_bounding_circle(cfg):
    job = _job(cfg, anims=[_anim("ball-1", "rotate", 0.5, 0.9, by=45)])
    _move_layer(job, "box-1", [68, 64], [56, 52, 80, 76])             # 軸對齊時不重疊（ball 到 52、box 從 56）
    f, = _only(_analyze(cfg, job), "depth")
    assert "動畫中可能重疊" in f.message                               # 外接圓半徑 16.97 → 到 56.97
    _move_layer(job, "box-1", [72, 64], [60, 52, 84, 76])             # 外接圓也碰不到
    assert _only(_analyze(cfg, job), "depth") == []


def test_manual_z_order_suppresses_depth_notices(cfg):
    from animator.animate.edit import set_layer_z

    job = _job(cfg, anims=[_anim("ball-1", "move", 0.5, 0.9, to=[150, 64])])
    set_layer_z(job / "spec.json", "ball-1", z=40)
    assert _only(_analyze(cfg, job), "depth") == []


def test_depth_notices_are_deduplicated_per_pair(cfg):
    anims = [_anim("ball-1", "move", 0.5, 0.7, to=[150, 64]), _anim("ball-1", "move", 0.7, 0.9, to=[140, 60]),
             _anim("ball-1", "rotate", 0.5, 0.9, by=90)]
    assert len(_only(_analyze(cfg, _job(cfg, anims=anims)), "depth")) == 1


# ---------------------------------------------------------------- 唯讀

def test_analysis_never_writes(cfg):
    job = _job(cfg, anims=[HAND, _anim("ball-1", "visibility", 0.5, 0.8, visible=False)])
    files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in job.rglob("*") if p.is_file()}
    _analyze(cfg, job)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in job.rglob("*") if p.is_file()} == files
    assert not (job / "history").exists()


# ---------------------------------------------------------------- CLI

def _cli(cfg, *args):
    from animator import cli

    return cli.main(["--config", str(cfg.config_file), *args])


def test_anim_check_prints_findings_and_exits_0(cfg, capsys):
    job = _job(cfg, anims=[HAND, _anim("ball-1", "opacity", 0.5, 0.8, to=0.5)])
    before = {p: p.read_bytes() for p in job.rglob("*") if p.is_file()}
    assert _cli(cfg, "anim", "check", job.name) == 0
    out = capsys.readouterr().out
    assert "[WARNING] box-1 可能露出修補過的背景 區域 1" in out
    assert "[NOTICE] ball-1 可能露出修補過的背景 區域 0" in out
    assert "共 1 個 warning、1 個 notice" in out and "有物件 ↔ 補洞區域的關聯" in out
    assert {p: p.read_bytes() for p in job.rglob("*") if p.is_file()} == before


def test_anim_check_unknown_and_stage1_jobs(cfg, capsys):
    job = _job(cfg, associations=None, anims=[HAND])
    assert _cli(cfg, "anim", "check", job.name) == 0
    out = capsys.readouterr().out
    assert "[NOTICE] [關聯不明]" in out and "比較舊的 job" in out
    import shutil
    shutil.rmtree(job)
    job = _job(cfg, seg=False, anims=[HAND])
    assert _cli(cfg, "anim", "check", job.name) == 0
    out = capsys.readouterr().out
    assert "分割資料：沒有" in out and "共 0 個 warning、0 個 notice" in out


def test_anim_check_errors_keep_existing_exit_codes(cfg, capsys):
    assert _cli(cfg, "anim", "check", "no-such-job") == 1
    job = _job(cfg, anims=[_anim("ball-1", "move", 0.8, 1.5, by=[1, 0])])   # 超過 duration
    assert _cli(cfg, "anim", "check", job.name) == 2


def test_successful_edits_print_relevant_findings_only(cfg, capsys):
    job = _job(cfg, anims=[HAND])
    assert _cli(cfg, "anim", "add", job.name, "ball-1", "move", "--by", "30,0", "--start", "0.5",
                "--duration", "0.3") == 0
    out = capsys.readouterr().out
    assert "[WARNING] ball-1 可能露出修補過的背景" in out and "box-1 可能露出" not in out   # 只列這個圖層
    assert "anim check" in out
    spec_after_edit = (job / "spec.json").read_bytes()
    assert _cli(cfg, "layers", "z", job.name, "ball-1", "--z", "40") == 0   # 手動順序：不再有 depth 提醒
    out = capsys.readouterr().out
    assert "自動決定" not in out and "[WARNING] ball-1" in out
    assert _cli(cfg, "layers", "pivot", job.name, "ball-1", "--at", "40,74") == 0
    assert "[WARNING] ball-1" in capsys.readouterr().out             # move 仍然會露出（pivot 本身不算）
    assert spec_after_edit != (job / "spec.json").read_bytes()       # （編輯本身照常寫入）


def test_failed_edits_print_no_findings(cfg, capsys):
    job = _job(cfg, anims=[HAND])
    original = (job / "spec.json").read_bytes()
    assert _cli(cfg, "anim", "add", job.name, "ball-1", "move", "--by", "30,0", "--start", "0.9",
                "--duration", "0.5") == 2
    captured = capsys.readouterr()
    assert "[WARNING]" not in captured.out + captured.err and "[NOTICE]" not in captured.out + captured.err
    assert (job / "spec.json").read_bytes() == original


def test_warnings_never_change_spec_output_or_exit_code(cfg, monkeypatch, capsys):
    """分析壞掉時，編輯照樣成功：spec 的內容和沒有 M4 時相同、結束碼 0。"""
    from animator.animate import warnings as W

    job_a = _job(cfg, anims=[HAND])
    assert _cli(cfg, "anim", "add", job_a.name, "ball-1", "move", "--by", "30,0", "--start", "0.5",
                "--duration", "0.3") == 0
    spec_with_analysis = json.loads((job_a / "spec.json").read_text(encoding="utf-8"))
    import shutil
    shutil.rmtree(job_a)

    def broken(*a, **k):
        raise RuntimeError("模擬：分析失敗")

    monkeypatch.setattr(W, "analyze", broken)
    job_b = _job(cfg, anims=[HAND])
    capsys.readouterr()
    assert _cli(cfg, "anim", "add", job_b.name, "ball-1", "move", "--by", "30,0", "--start", "0.5",
                "--duration", "0.3") == 0
    assert "[NOTE] 無法分析" in capsys.readouterr().out
    assert json.loads((job_b / "spec.json").read_text(encoding="utf-8")) == spec_with_analysis
