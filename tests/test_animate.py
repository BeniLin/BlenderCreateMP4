"""S2-2 M1：layers list / anim add move（只用標準函式庫，不需要 AI 套件）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import make_sample_layers as L

BG_W, BG_H = 192, 128


def _solid(w, h, rgba):
    return [bytes(rgba) * w for _ in range(h)]


def _square(size, pad, rgb):
    rows = []
    for y in range(size + 2 * pad):
        inside_y = pad <= y < size + pad
        rows.append(b"".join(bytes((*rgb, 255)) if inside_y and pad <= x < size + pad else b"\x00\x00\x00\x00"
                             for x in range(size + 2 * pad)))
    return rows


def make_job(out_dir: Path, job_id: str = "20990101-000000_synthetic") -> Path:
    """S2-1 風格的 job：背景（meta.role = background）+ 兩個物件圖層 + 一筆既有動畫 + 使用者自訂欄位。"""
    job = out_dir / job_id
    (job / "layers").mkdir(parents=True)
    bg = []
    for y in range(BG_H):
        bg.append(b"".join(bytes((x * 255 // (BG_W - 1), y * 255 // (BG_H - 1), 90, 255)) for x in range(BG_W)))
    L.write_png(job / "layers" / "background.png", BG_W, BG_H, bg)
    L.write_png(job / "layers" / "ball-1.png", 24, 24, _square(20, 2, (230, 20, 20)))
    L.write_png(job / "layers" / "box-1.png", 24, 24, _square(20, 2, (20, 40, 230)))
    spec = {
        "version": 1,
        "meta": {"name": "synthetic", "created_by": "animator segment", "note": "手動加的備註"},
        "canvas": {"width": BG_W, "height": BG_H, "fps": 30, "duration": 1.0},
        "layers": [
            {"id": "background", "image": "layers/background.png", "z": 0, "position": [96.0, 64.0],
             "meta": {"source": "ai", "role": "background", "status": "passed_checks"}},
            {"id": "ball-1", "image": "layers/ball-1.png", "z": 20, "position": [40.0, 64.0],
             "meta": {"source": "ai", "label": "ball", "crop_box": [28, 52, 52, 76], "custom": {"keep": True}}},
            {"id": "box-1", "image": "layers/box-1.png", "z": 10, "position": [150.0, 64.0],
             "meta": {"source": "manual", "label": "box"}},
        ],
        "animations": [
            {"id": "box-1-hand", "target": "box-1", "effect": "move", "start": 0.0, "end": 0.5,
             "easing": "linear", "params": {"by": [0, -20]}, "x_user_note": "hand written"},
        ],
    }
    (job / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return job


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


# ---------------------------------------------------------------- job resolution / layers list

def test_resolve_job_by_dir_spec_and_id(cfg, job):
    from animator.animate.edit import resolve_job_spec
    from animator.errors import AnimatorError

    spec = (job / "spec.json").resolve()
    assert resolve_job_spec(job, cfg) == spec
    assert resolve_job_spec(job / "spec.json", cfg) == spec
    assert resolve_job_spec(job.name, cfg) == spec                   # job_id → output_dir/<job_id>
    with pytest.raises(AnimatorError, match="找不到 job"):
        resolve_job_spec("no-such-job", cfg)


def test_layers_list(job):
    from animator.animate.edit import list_layers

    rows = {r.id: r for r in list_layers(_spec(job))}
    assert list(rows) == ["background", "ball-1", "box-1"]
    assert rows["background"].background and not rows["ball-1"].background
    assert (rows["ball-1"].label, rows["ball-1"].z, rows["ball-1"].position) == ("ball", 20, [40.0, 64.0])
    assert rows["box-1"].animations == 1 and rows["ball-1"].animations == 0
    assert rows["background"].label is None


def test_layers_list_cli(cfg, job, capsys):
    from animator import cli

    assert cli.main(["--config", str(cfg.config_file), "layers", "list", job.name]) == 0
    out = capsys.readouterr().out
    lines = {line.split()[0]: line for line in out.splitlines()[2:]}
    assert "yes" in lines["background"] and "ball" in lines["ball-1"] and "[40, 64]" in lines["ball-1"]


# ---------------------------------------------------------------- anim add move

def test_add_move_by(cfg, job):
    from animator.animate.edit import add_move
    from animator.spec.validate import validate

    before = _spec(job)
    res = add_move(job / "spec.json", "ball-1", by=[120, 0], start=0, duration=0.5)
    after = _spec(job)
    assert res.animation == {"id": "ball-1-move-1", "target": "ball-1", "effect": "move", "start": 0,
                             "end": 0.5, "params": {"by": [120, 0]}}
    assert after["animations"] == before["animations"] + [res.animation]
    assert validate(after, job).ok


def test_add_move_to(cfg, job):
    from animator.animate.edit import add_move

    res = add_move(job / "spec.json", "ball-1", to=[100, 30], start=0.25, duration=0.5)
    assert res.animation["params"] == {"to": [100, 30]}
    assert (res.animation["start"], res.animation["end"]) == (0.25, 0.75)


def test_unrelated_fields_layers_and_animations_are_preserved(cfg, job):
    from animator.animate.edit import add_move

    before = _spec(job)
    add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5)
    after = _spec(job)
    for key in ("version", "meta", "canvas", "layers"):
        assert after[key] == before[key], key                         # 初始位置、其他圖層、自訂欄位都不變
    assert after["animations"][0] == before["animations"][0]          # 既有動畫（含 x_user_note）不變
    assert list(after) == list(before)                                # 頂層欄位順序也不變


def test_animation_ids_are_unique(cfg, job):
    from animator.animate.edit import add_move

    a = add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.25).animation
    b = add_move(job / "spec.json", "ball-1", by=[10, 0], start=0.25, duration=0.25).animation
    assert (a["id"], b["id"]) == ("ball-1-move-1", "ball-1-move-2")


@pytest.mark.parametrize("kwargs, match", [
    ({"layer_id": "nope", "by": [1, 0]}, "找不到圖層"),
    ({"layer_id": "background", "by": [1, 0]}, "背景"),
    ({"layer_id": "ball-1"}, "--by 或 --to"),
    ({"layer_id": "ball-1", "by": [1, 0], "to": [1, 0]}, "--by 或 --to"),
    ({"layer_id": "ball-1", "by": [1, 0], "duration": 0}, "duration"),
    ({"layer_id": "ball-1", "by": [1, 0], "duration": -1}, "duration"),
    ({"layer_id": "ball-1", "by": [1, 0], "start": float("nan")}, "start"),
])
def test_invalid_edit_requests_are_rejected(cfg, job, kwargs, match):
    from animator.animate.edit import add_move
    from animator.errors import AnimatorError

    original = (job / "spec.json").read_bytes()
    args = {"start": 0, "duration": 0.5, **kwargs}
    with pytest.raises(AnimatorError, match=match):
        add_move(job / "spec.json", args.pop("layer_id"), **args)
    assert (job / "spec.json").read_bytes() == original and _backups(job) == []


@pytest.mark.parametrize("text", ["120", "1,2,3", "a,b", "nan,0", "inf,1", ""])
def test_invalid_coordinates_are_rejected(text):
    from animator.animate.edit import parse_pair
    from animator.errors import AnimatorError

    with pytest.raises(AnimatorError, match="兩個數字"):
        parse_pair(text, "--by")


def test_parse_pair_accepts_negative_and_decimal():
    from animator.animate.edit import parse_pair

    assert parse_pair("-12.5, 3", "--by") == [-12.5, 3]


@pytest.mark.parametrize("layer, start, duration, match", [
    ("ball-1", 0.8, 0.5, "canvas.duration"),          # 結束在 1.3 s > duration 1.0 s
    ("ball-1", -0.1, 0.5, "不可以小於 0"),
    ("box-1", 0.25, 0.5, "時間重疊"),                 # 和既有的 box-1-hand（0~0.5 s，position 通道）重疊
    ("ball-1", 0.5, 0.01, "不到 1 格"),               # 換算成影格後不到 1 格
])
def test_stage1_validation_rejects_and_leaves_spec_untouched(cfg, job, layer, start, duration, match):
    from animator.animate.edit import add_move
    from animator.errors import SpecError

    original = (job / "spec.json").read_bytes()
    with pytest.raises(SpecError, match=match):
        add_move(job / "spec.json", layer, by=[10, 0], start=start, duration=duration)
    assert (job / "spec.json").read_bytes() == original
    assert _backups(job) == []                                       # 失敗的編輯不建立備份


def test_back_to_back_segments_are_allowed(cfg, job):
    from animator.animate.edit import add_move

    add_move(job / "spec.json", "box-1", by=[10, 0], start=0.5, duration=0.25)   # 接在 box-1-hand 之後


# ---------------------------------------------------------------- 備份 / 原子寫入

def test_successful_edit_backs_up_exact_previous_bytes(cfg, job):
    from animator.animate.edit import add_move

    original = (job / "spec.json").read_bytes()
    res = add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5)
    assert res.backup.parent == job / "history" / "edits"
    assert res.backup.name.endswith("-spec.json")
    assert res.backup.read_bytes() == original


def test_backups_never_collide_or_overwrite(cfg, job, monkeypatch):
    from animator.animate import edit

    monkeypatch.setattr(edit, "_timestamp", lambda: "20990101-000000-000")   # 同一個時間戳記
    first = edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.25).backup
    second = edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0.25, duration=0.25).backup
    third = edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0.5, duration=0.25).backup
    assert [p.name for p in (first, second, third)] == [
        "20990101-000000-000-spec.json", "20990101-000000-000-2-spec.json", "20990101-000000-000-3-spec.json"]
    assert len(json.loads(first.read_text(encoding="utf-8"))["animations"]) == 1
    assert len(json.loads(second.read_text(encoding="utf-8"))["animations"]) == 2


def test_atomic_write_failure_keeps_original_and_leaves_no_temp_or_backup(cfg, job, monkeypatch):
    from animator.animate import edit

    original = (job / "spec.json").read_bytes()

    def broken_replace(src, dst):
        raise OSError("模擬：磁碟已滿")

    monkeypatch.setattr(edit.os, "replace", broken_replace)
    with pytest.raises(OSError, match="磁碟已滿"):
        edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5)
    assert (job / "spec.json").read_bytes() == original
    assert _backups(job) == []
    assert sorted(p.name for p in job.iterdir()) == ["history", "layers", "spec.json"]   # 沒有殘留暫存檔


def test_backup_write_failure_leaves_no_partial_backup(cfg, job, monkeypatch):
    """備份檔建立後寫入失敗：刪掉這次建立的半個備份；別人已經建立的同名備份不能動。"""
    from animator.animate import edit

    original = (job / "spec.json").read_bytes()
    stamp = "20990101-000000-000"
    monkeypatch.setattr(edit, "_timestamp", lambda: stamp)
    edits = job / "history" / "edits"
    edits.mkdir(parents=True)
    other = edits / f"{stamp}-spec.json"                     # 另一個行程剛好用了同一個檔名
    other.write_bytes(b"backup from another process")

    opened = []

    class BrokenWriter:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()

        def write(self, data):
            self.fh.write(data[:10])                           # 寫了一部分就失敗
            self.fh.flush()
            raise OSError("模擬：備份寫到一半磁碟已滿")

    def broken_open(path, mode="r", *a, **k):
        fh = open(path, mode, *a, **k)
        opened.append((Path(path), fh))
        return BrokenWriter(fh)

    monkeypatch.setattr(edit, "open", broken_open, raising=False)
    with pytest.raises(OSError, match="備份寫到一半"):
        edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5)
    assert [p.name for p, _ in opened] == [f"{stamp}-2-spec.json"]   # 撞名的檔案只跳過，沒有被開啟
    assert all(fh.closed for _, fh in opened)
    assert sorted(p.name for p in edits.iterdir()) == [other.name]   # 半個備份已刪除
    assert other.read_bytes() == b"backup from another process"     # 別人的備份不變
    assert (job / "spec.json").read_bytes() == original
    assert sorted(p.name for p in job.iterdir()) == ["history", "layers", "spec.json"]


def test_temp_file_open_failure_closes_fd_and_removes_temp(cfg, job, monkeypatch):
    """mkstemp 已經建立暫存檔，但 fdopen 失敗：fd 要關掉、暫存檔要刪掉、也不留備份。"""
    import os

    from animator.animate import edit

    original = (job / "spec.json").read_bytes()
    allocated = []
    real_mkstemp = edit.tempfile.mkstemp

    def spy_mkstemp(*a, **k):
        fd, tmp = real_mkstemp(*a, **k)
        allocated.append((fd, Path(tmp)))
        return fd, tmp

    def broken_fdopen(fd, *a, **k):
        raise OSError("模擬：無法開啟暫存檔")

    monkeypatch.setattr(edit.tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(edit.os, "fdopen", broken_fdopen)
    with pytest.raises(OSError, match="無法開啟暫存檔"):
        edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5)
    (fd, tmp), = allocated
    assert tmp.parent == job and not tmp.exists()                   # 暫存檔已刪除
    with pytest.raises(OSError):
        os.fstat(fd)                                                 # fd 已關閉，沒有洩漏
    assert (job / "spec.json").read_bytes() == original
    assert _backups(job) == []
    assert sorted(p.name for p in job.iterdir()) == ["history", "layers", "spec.json"]


def test_atomic_write_goes_through_same_directory_temp_file(cfg, job, monkeypatch):
    from animator.animate import edit

    seen = []
    real = edit.os.replace

    def spy(src, dst):
        seen.append((Path(src), Path(dst)))
        real(src, dst)

    monkeypatch.setattr(edit.os, "replace", spy)
    edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5)
    (src, dst), = seen
    assert dst == job / "spec.json" and src.parent == job and src.name.startswith(".spec.json.")
    assert not src.exists()


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
        edit.add_move(job / "spec.json", "ball-1", by=[10, 0], start=0, duration=0.5)
    assert _backups(job) == []


# ---------------------------------------------------------------- CLI

def test_anim_add_cli(cfg, job, capsys):
    from animator import cli

    code = cli.main(["--config", str(cfg.config_file), "anim", "add", str(job), "ball-1", "move",
                     "--by", "120,0", "--start", "0", "--duration", "0.5"])
    assert code == 0
    assert "ball-1-move-1" in capsys.readouterr().out
    assert _spec(job)["animations"][-1]["params"] == {"by": [120, 0]}


def test_anim_add_cli_errors(cfg, job, capsys):
    from animator import cli

    base = ["--config", str(cfg.config_file), "anim", "add", str(job)]
    assert cli.main(base + ["ball-1", "move", "--by", "1,x", "--start", "0", "--duration", "1"]) == 1
    assert cli.main(base + ["background", "move", "--by", "1,0", "--start", "0", "--duration", "1"]) == 1
    assert cli.main(base + ["ball-1", "move", "--by", "1,0", "--start", "0.9", "--duration", "1"]) == 2  # SpecError
    with pytest.raises(SystemExit):                                   # argparse：--by / --to 都沒給
        cli.main(base + ["ball-1", "move", "--start", "0", "--duration", "1"])
    with pytest.raises(SystemExit):                                   # argparse：不支援的效果
        cli.main(base + ["ball-1", "fade_in", "--to", "1", "--start", "0", "--duration", "1"])
    assert _backups(job) == []
