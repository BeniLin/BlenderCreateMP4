from __future__ import annotations

import json
from datetime import datetime

from animator import jobs
from conftest import EXAMPLE


def test_job_id_and_sanitize(make_config):
    cfg = make_config()
    now = datetime(2026, 1, 2, 3, 4, 5)
    assert jobs.make_job_id(cfg, "My Demo!", now) == "20260102-030405_my-demo"
    assert jobs.sanitize_name("小鳥") == "job"


def test_create_job_from_external_spec(make_config, example_raw):
    cfg = make_config()
    job = jobs.create_job_from_spec(EXAMPLE / "spec.json", example_raw, cfg)
    assert job.dir.parent == cfg.path("output_dir")
    copied = json.loads(job.spec_path.read_text(encoding="utf-8"))
    assert [l["image"] for l in copied["layers"]][:2] == ["layers/background.png", "layers/cloud.png"]
    assert all((job.dir / l["image"]).is_file() for l in copied["layers"])
    # 範例目錄不會被修改
    assert json.loads((EXAMPLE / "spec.json").read_text(encoding="utf-8")) == example_raw

    assert jobs.existing_job_for(job.spec_path, cfg).id == job.id
    assert jobs.existing_job_for(EXAMPLE / "spec.json", cfg) is None


def test_create_job_from_layers(make_config):
    cfg = make_config()
    job = jobs.create_job_from_layers(EXAMPLE / "layers", cfg, "demo", duration=3)
    spec = json.loads(job.spec_path.read_text(encoding="utf-8"))
    assert spec["canvas"]["width"] == 1280 and spec["canvas"]["height"] == 720
    assert [l["id"] for l in spec["layers"]][0] == "background"   # 檔名排序
    assert spec["animations"] == []


def test_place_output_backs_up_previous(make_config, tmp_path):
    cfg = make_config()
    job = jobs.create_job_from_layers(EXAMPLE / "layers", cfg, "backup")

    first = tmp_path / "a.mp4"
    first.write_bytes(b"first")
    assert jobs.place_output(job, first, "result.mp4", "R1", overwrite=False) is None

    second = tmp_path / "b.mp4"
    second.write_bytes(b"second")
    moved = jobs.place_output(job, second, "result.mp4", "R2", overwrite=False)
    assert moved == job.history_dir / "R1" / "result.mp4"
    assert moved.read_bytes() == b"first"
    assert (job.dir / "result.mp4").read_bytes() == b"second"
    assert jobs.read_index(job)["current"]["result.mp4"] == "R2"

    third = tmp_path / "c.mp4"
    third.write_bytes(b"third")
    assert jobs.place_output(job, third, "result.mp4", "R3", overwrite=True) is None
    assert (job.dir / "result.mp4").read_bytes() == b"third"
