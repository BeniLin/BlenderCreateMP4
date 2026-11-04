"""S2-1 審查後的修正：遮罩解析、補洞檢查、備援、MI-GAN 上下文、零物件、遮罩驗證、
失敗原子性、暫存清除、設定預先驗證、source_image 路徑、標籤比對。

不需要模型（MI-GAN / Blender 都用替身或 OpenCV），約數秒。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")
Image = pytest.importorskip("PIL.Image")

from test_segmentation import scene, write_masks  # noqa: E402

from animator.errors import AnimatorError  # noqa: E402
from animator.segmentation import compose as C  # noqa: E402


def _cfg(make_config, tmp_path, extra=""):
    return make_config(f"paths:\n  output_dir: '{(tmp_path / 'out').as_posix()}'\n"
                       f"  temp_dir: '{(tmp_path / 'tmp').as_posix()}'\n" + extra)


def _scene_file(tmp_path, w=400, h=300):
    rgb, circle, square = scene(w, h)
    img = tmp_path / "scene.png"
    Image.fromarray(rgb).save(img)
    return img, rgb, circle, square


def _opts(**kw):
    from animator.segmentation.pipeline import SegmentOptions

    return SegmentOptions(**{"inpaint": "opencv", "refine": "none", "duration": 1.0, **kw})


# ---------------------------------------------------------------- 1. 遮罩解析

def test_opaque_rgba_mask_uses_luminance_not_alpha(tmp_path):
    a = np.zeros((40, 40, 4), np.uint8)
    a[..., 3] = 255                                   # GIMP / Photoshop：整張不透明、白色 = 物件
    a[10:20, 10:20, :3] = 255
    Image.fromarray(a).save(tmp_path / "m.png")
    m = C.load_mask(tmp_path / "m.png", (40, 40))
    assert m.sum() == 100 and m[15, 15] and not m[0, 0]


def test_transparent_rgba_mask_uses_alpha(tmp_path):
    a = np.zeros((40, 40, 4), np.uint8)
    a[..., :3] = 255                                  # 顏色全白，但只有方塊是不透明的
    a[10:20, 10:20, 3] = 255
    Image.fromarray(a).save(tmp_path / "m.png")
    assert C.load_mask(tmp_path / "m.png", (40, 40)).sum() == 100


def test_mask_of_wrong_size_is_rejected_and_odd_size_is_padded(tmp_path):
    Image.fromarray(np.zeros((30, 30), np.uint8)).save(tmp_path / "bad.png")
    with pytest.raises(AnimatorError, match="尺寸"):
        C.load_mask(tmp_path / "bad.png", (41, 31), (1, 1))
    m = np.zeros((31, 41), np.uint8)
    m[:, 40] = 255                                    # 最右邊一欄
    Image.fromarray(m).save(tmp_path / "odd.png")
    out = C.load_mask(tmp_path / "odd.png", (41, 31), (1, 1))
    assert out.shape == (32, 42)
    assert out[:, 40].all() and out[:, 41].all() and not out[:, 39].any()   # 補邊，不是拉伸


# ---------------------------------------------------------------- 2. 補洞檢查

def test_check_passes_for_perfect_fill_of_thin_object():
    img = np.full((100, 100, 3), 120, np.uint8)
    obj = np.zeros((100, 100), bool)
    obj[48:52, 10:90] = True
    img[obj] = (255, 0, 0)
    hole = C.dilate(obj, 12)
    out = img.copy()
    out[obj] = 120                                    # 完美的修補：外擴帶和原圖一樣
    checks = C.check_inpaint(img, out, hole, target=obj)
    assert checks["passed"] and checks["target_changed_ratio"] == 1.0


# ---------------------------------------------------------------- 3. 沒有通過檢查 → 備援

def test_region_failing_checks_goes_to_fallback(make_config, tmp_path, monkeypatch):
    from animator.segmentation import models as M
    from animator.segmentation import pipeline as P

    real = P.run_worker

    def fake(cfg, kind, task, work, logs, tag):
        if kind == "inpaint" and task["backend"] == "migan":     # 替身：回傳原圖 → 檢查一定失敗
            Image.open(task["image"]).save(Path(work) / "background_migan.png")
            return {"result": {"regions": [{"index": i, "checks": {"passed": False}} for i in task["region_indices"]]}}
        return real(cfg, kind, task, work, logs, tag)

    monkeypatch.setattr(P, "run_worker", fake)
    monkeypatch.setattr(M, "ensure_ready", lambda cfg, name: tmp_path)
    img, rgb, circle, _ = _scene_file(tmp_path)
    res = P.segment(img, _cfg(make_config, tmp_path),
                    _opts(masks_dir=write_masks(tmp_path / "m", {"ball": circle}), inpaint="migan"))
    r = res.regions[0]
    assert r["backend"] == "opencv" and r["status"] == "needs_review"
    assert any("沒有通過自動檢查" in x for x in r["reasons"]) and any("備援" in x for x in r["reasons"])


# ---------------------------------------------------------------- 4. MI-GAN 上下文

def test_inpaint_masks_all_unfilled_holes_in_context_window(tmp_path, monkeypatch):
    """補第一個區域時，旁邊還沒補的物件也要被當成「要補」，不能當成背景上下文。"""
    from animator.segmentation import base, worker

    rgb = np.full((200, 300, 3), 100, np.uint8)
    a = np.zeros((200, 300), bool)
    a[90:110, 60:80] = True
    b = np.zeros((200, 300), bool)
    b[90:110, 140:160] = True                         # 不相連，但在 a 的上下文視窗裡
    labels = np.zeros((200, 300), np.int32)
    labels[C.dilate(a, 8)] = 1
    labels[C.dilate(b, 8)] = 2
    np.save(tmp_path / "regions.npy", labels)
    np.save(tmp_path / "objects.npy", (a | b).astype(np.uint8))
    rgb[a] = (255, 0, 0)
    rgb[b] = (0, 0, 255)
    Image.fromarray(rgb).save(tmp_path / "src.png")
    seen = []

    class Recorder:
        def inpaint(self, image, hole):
            seen.append(hole.copy())
            out = image.copy()
            out[hole] = 100
            return out

    monkeypatch.setattr(base, "create", lambda *a, **k: Recorder())
    res = worker.run_inpaint({"work_dir": str(tmp_path), "image": str(tmp_path / "src.png"), "backend": "opencv",
                              "region_indices": [0, 1], "threads": 1, "mem_arena": False, "model_dir": ""})
    first = res["regions"][0]
    x0, y0, x1, y1 = first["window"]
    assert seen[0][100 - y0, 150 - x0], "第二個物件在第一次的遮罩裡（不會被當成背景）"
    assert all(r["checks"]["passed"] for r in res["regions"])
    bx0, by0 = res["regions"][1]["window"][:2]
    assert not seen[1][100 - by0, 70 - bx0], "第一個區域補好之後，第二次就把它當成背景"
    out = np.asarray(Image.open(tmp_path / "background_opencv.png"))
    assert (out[a] == 100).all() and (out[b] == 100).all()


# ---------------------------------------------------------------- 6. 零物件

def test_zero_objects_is_flagged_and_cli_returns_3(make_config, tmp_path):
    from animator import cli
    from animator.segmentation.pipeline import segment

    img, rgb, _, _ = _scene_file(tmp_path)
    tiny = np.zeros(rgb.shape[:2], bool)
    tiny[5:8, 5:8] = True                             # 9 px < min_area_px 400 → 會被清除
    masks = write_masks(tmp_path / "m", {"speck": tiny})
    cfg = _cfg(make_config, tmp_path)
    res = segment(img, cfg, _opts(masks_dir=masks))
    assert res.no_objects and res.review_required
    assert any(x.startswith("no_objects") for x in res.review)
    assert any("speck.png" in x for x in res.review)
    code = cli.main(["--config", str(cfg.config_file), "segment", "--image", str(img), "--masks", str(masks),
                     "--inpaint", "opencv", "--no-refine"])
    assert code == 3


def test_background_hole_png_in_mask_folder_is_ignored(make_config, tmp_path):
    from animator.segmentation.pipeline import segment

    img, rgb, circle, _ = _scene_file(tmp_path)
    masks = write_masks(tmp_path / "m", {"ball": circle, "background_hole": np.ones(rgb.shape[:2], bool)})
    res = segment(img, _cfg(make_config, tmp_path), _opts(masks_dir=masks))
    assert [layer["id"] for layer in res.layers] == ["background", "ball-1"]


# ---------------------------------------------------------------- 8. 失敗的原子性 + 5. 暫存清除

def test_failure_leaves_no_output_job_and_keeps_temp(make_config, tmp_path, monkeypatch):
    from animator.segmentation import pipeline as P

    def boom(*a, **k):
        raise RuntimeError("模擬的錯誤")

    monkeypatch.setattr(C, "restore_ring", boom)
    img, _, circle, _ = _scene_file(tmp_path)
    with pytest.raises(AnimatorError, match="沒有建立 output"):
        P.segment(img, _cfg(make_config, tmp_path), _opts(masks_dir=write_masks(tmp_path / "m", {"ball": circle})))
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())
    stages = list((tmp_path / "tmp").iterdir())
    assert len(stages) == 1
    failure = json.loads((stages[0] / "job" / "failure.json").read_text(encoding="utf-8"))
    assert "模擬的錯誤" in failure["error"]
    assert (stages[0] / "job" / "logs").is_dir()


def test_invalid_mask_fails_before_creating_anything(make_config, tmp_path):
    from animator.segmentation.pipeline import segment

    img, _, _, _ = _scene_file(tmp_path)
    Image.fromarray(np.zeros((10, 10), np.uint8)).save((tmp_path / "m").mkdir() or tmp_path / "m" / "x.png")
    with pytest.raises(AnimatorError, match="尺寸"):
        segment(img, _cfg(make_config, tmp_path), _opts(masks_dir=tmp_path / "m"))
    assert not (tmp_path / "out").exists() and not (tmp_path / "tmp").exists()


def test_success_removes_temp_unless_keep_temp(make_config, tmp_path):
    from animator.segmentation.pipeline import segment

    img, _, circle, _ = _scene_file(tmp_path)
    masks = write_masks(tmp_path / "m", {"ball": circle})
    cfg = _cfg(make_config, tmp_path)
    res = segment(img, cfg, _opts(masks_dir=masks))
    assert res.job.dir.parent == tmp_path / "out" and res.spec_path.is_file()
    assert not (tmp_path / "tmp" / res.job.id).exists()
    kept = segment(img, cfg, _opts(masks_dir=masks, keep_temp=True))
    assert (tmp_path / "tmp" / kept.job.id / "segment").is_dir()
    assert kept.job.id != res.job.id


# ---------------------------------------------------------------- 14. 設定預先驗證

@pytest.mark.parametrize("text, match", [
    ("ai:\n  segmentation:\n    backend: manual\n", "ai.segmentation.backend"),   # 舊版範本的值
    ("ai:\n  inpaint:\n    backend: lama\n", "ai.inpaint.backend"),
    ("ai:\n  inpaint:\n    fallback: telea\n", "ai.inpaint.fallback"),
    ("ai:\n  device: auto\n", "ai.device"),
    ("ai:\n  inpaint:\n    review_area_ratio: 1.5\n", "review_area_ratio"),
])
def test_invalid_settings_fail_before_any_work(make_config, tmp_path, text, match):
    from animator.segmentation.pipeline import SegmentOptions, segment

    img, _, _, _ = _scene_file(tmp_path)
    with pytest.raises(AnimatorError, match=match):
        segment(img, _cfg(make_config, tmp_path, text), SegmentOptions(labels=["dog"]))
    assert not (tmp_path / "out").exists() and not (tmp_path / "tmp").exists()


# ---------------------------------------------------------------- 15 / 16

def test_spec_source_image_points_to_existing_file(make_config, tmp_path):
    from animator.segmentation.pipeline import segment

    img, _, circle, _ = _scene_file(tmp_path)
    res = segment(img, _cfg(make_config, tmp_path), _opts(masks_dir=write_masks(tmp_path / "m", {"ball": circle})))
    spec = json.loads(res.spec_path.read_text(encoding="utf-8"))
    assert (res.job.dir / spec["meta"]["source_image"]).is_file()
    assert spec["meta"]["source_file"] == "scene.png"


@pytest.mark.parametrize("raw, labels, expected", [
    ("category", ["cat", "category"], "category"),
    ("cat", ["cat", "category"], "cat"),
    ("dog cat", ["dog", "cat"], "dog"),
    ("potted plant", ["plant", "potted plant"], "potted plant"),
    ("cats", ["cat"], "cats"),                            # 不是整個字 → 保留原文字
])
def test_normalize_label_prefers_whole_word_and_longest(raw, labels, expected):
    assert C.normalize_label(raw, labels) == expected


# ---------------------------------------------------------------- 11. 模型檔案驗證 / 12. 離線

def _fake_model(tmp_path, monkeypatch, make_config):
    import hashlib

    from animator.segmentation import models as M

    d = tmp_path / "models" / "fake"
    d.mkdir(parents=True)
    files = {"weights.bin": b"w" * 1000, "config.json": b'{"a": 1}'}
    for n, b in files.items():
        (d / n).write_bytes(b)
    spec = M.ModelSpec(name="fake", role="test", repo="x/fake", revision="0" * 40, weights="weights.bin",
                       license="MIT", commercial=True,
                       files=tuple(M.ModelFile(n, len(b), hashlib.sha256(b).hexdigest()) for n, b in files.items()))
    monkeypatch.setitem(M.MODELS, "fake", spec)
    cfg = make_config(f"paths:\n  models_dir: '{(tmp_path / 'models').as_posix()}'\n"
                      f"  output_dir: '{(tmp_path / 'out').as_posix()}'\n  temp_dir: '{(tmp_path / 'tmp').as_posix()}'\n")
    return M, cfg, d


def test_every_registered_file_is_hashed(make_config, tmp_path, monkeypatch):
    M, cfg, d = _fake_model(tmp_path, monkeypatch, make_config)
    assert M.status(cfg, "fake").verified
    (d / "config.json").write_bytes(b'{"a": 2}')          # 權重沒變，只改了 config（大小相同）
    st = M.status(cfg, "fake")
    assert not st.verified and "config.json" in st.detail
    with pytest.raises(AnimatorError):
        M.ensure_ready(cfg, "fake")


def test_tampering_that_keeps_size_and_mtime_is_detected(make_config, tmp_path, monkeypatch):
    import os

    M, cfg, d = _fake_model(tmp_path, monkeypatch, make_config)
    assert M.status(cfg, "fake").verified
    p = d / "weights.bin"
    st0 = p.stat()
    p.write_bytes(b"x" + b"w" * 999)
    os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns))     # 大小、修改時間都和原本一樣
    assert not M.status(cfg, "fake").verified
    assert not list(d.glob(".verified*"))                  # 不再使用驗證快取檔


def test_quick_status_does_not_claim_verified(make_config, tmp_path, monkeypatch):
    M, cfg, _ = _fake_model(tmp_path, monkeypatch, make_config)
    st = M.status(cfg, "fake", full_hash=False)
    assert st.present and not st.verified and "未計算雜湊" in st.detail


def test_workers_always_run_offline(make_config, tmp_path, monkeypatch):
    import subprocess

    from animator.config import DEFAULTS
    from animator.segmentation import pipeline as P

    assert "offline" not in DEFAULTS["ai"]["models"]        # 沒有效果的設定已移除
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    cfg = _cfg(make_config, tmp_path)
    (tmp_path / "w").mkdir()
    with pytest.raises(AnimatorError, match="逾時"):
        P.run_worker(cfg, "vision", {}, tmp_path / "w", tmp_path / "logs", "t")
    assert seen["HF_HUB_OFFLINE"] == "1" and seen["TRANSFORMERS_OFFLINE"] == "1"
    assert seen["HF_HOME"].startswith(str(cfg.path("models_dir")))


# ---------------------------------------------------------------- 9. alpha 邊緣 / 10. PyMatting 記憶體

def test_layer_alpha_keeps_soft_edge_up_to_band():
    visible = np.zeros((60, 60), bool)
    visible[20:40, 20:40] = True
    alpha = np.zeros((60, 60), np.uint8)
    alpha[12:48, 12:48] = 100                          # 精修出來的半透明邊：外擴 8 px
    alpha[visible] = 255
    _, a2 = C.layer_pixels(np.zeros((60, 60, 3), np.uint8), visible, alpha, None, None, clip_px=2)
    _, a8 = C.layer_pixels(np.zeros((60, 60, 3), np.uint8), visible, alpha, None, None, clip_px=8)
    assert a2[13, 30] == 0 and a8[13, 30] == 100       # 8 px 處的細毛：2 px 會被截掉，band 寬度會保留
    assert a8[5, 30] == 0                              # 超過精修帶的仍然截掉


def test_ring_keep_covers_refine_band_but_leaves_a_ramp():
    assert C.ring_keep_px(8, 12) == 8
    assert C.ring_keep_px(2, 12) == 3
    assert C.ring_keep_px(20, 12) == 10                # 一定比外擴寬度小（還有漸層）


def test_matting_tiles_cover_box_exactly():
    from animator.segmentation.backends.pymatting_refine import tiles

    box = (5, 7, 2600, 1300)
    seen = np.zeros((1400, 2700), np.int8)
    for (x0, y0, x1, y1), (ox0, oy0, ox1, oy1) in tiles(box, 1024, 32, (1400, 2700)):
        seen[y0:y1, x0:x1] += 1
        assert ox0 <= x0 and oy0 <= y0 and ox1 >= x1 and oy1 >= y1
        assert (x1 - x0) <= 1024 and (y1 - y0) <= 1024
    assert (seen[7:1300, 5:2600] == 1).all() and seen.sum() == (1300 - 7) * (2600 - 5)


@pytest.mark.ai
def test_tiled_matting_matches_untiled():
    import os

    os.environ.setdefault("NUMBA_CACHE_DIR", str(Path(__file__).resolve().parents[1] / "models" / ".numba_cache"))
    pytest.importorskip("pymatting")
    from animator.segmentation.backends.pymatting_refine import PyMattingRefiner

    rgb, circle, _ = scene(400, 300)
    a1, _, _ = PyMattingRefiner(8, tile_px=10000).refine(rgb, circle)
    a2, _, _ = PyMattingRefiner(8, tile_px=64).refine(rgb, circle)
    assert np.abs(a1.astype(int) - a2.astype(int)).max() <= 2


# ---------------------------------------------------------------- 13. 依賴檔

def test_stage2_requirements_files_are_ascii():
    """pip 用系統字碼頁（繁中 Windows = cp950）讀 requirements 檔；非 ASCII 的註解會讓 pip install -r 失敗。"""
    root = Path(__file__).resolve().parents[1]
    for name in ("requirements-ai.txt", "requirements-ai.lock.txt"):
        data = (root / name).read_bytes()
        assert all(b < 128 for b in data), name
    lock = (root / "requirements-ai.lock.txt").read_text(encoding="ascii")
    pins = [l for l in lock.splitlines() if l and not l.startswith("#")]
    assert pins and all("==" in l for l in pins)
    assert any(l.startswith("torch==") and l.endswith("+cpu") for l in pins)


# ---------------------------------------------------------------- D1：alpha = 255 保留原圖像素

def test_opaque_pixels_keep_source_colour_even_inside_matting_band():
    src = np.full((20, 20, 3), 100, np.uint8)
    fg = np.full((20, 20, 3), 200, np.uint8)             # 前景色估計和原圖不同
    visible = np.zeros((20, 20), bool)
    visible[5:15, 5:15] = True
    core = np.zeros((20, 20), bool)
    core[7:13, 7:13] = True
    alpha = np.zeros((20, 20), np.uint8)
    alpha[4:16, 4:16] = 128
    alpha[5:15, 5:15] = 255                              # 精修帶裡也有完全不透明的像素（core 以外）
    rgb, a = C.layer_pixels(src, visible, alpha, fg, core, clip_px=8)
    opaque = a == 255
    assert (opaque & ~core).any()
    assert (rgb[opaque] == 100).all()                    # alpha 255 → 原圖像素
    assert (rgb[(a > 0) & (a < 255)] == 200).all()       # 半透明 → 去色邊後的前景色


# ---------------------------------------------------------------- D3：沒有修補的區域

def _two_objects(tmp_path):
    rgb, _, _ = scene(400, 300)
    left = np.zeros(rgb.shape[:2], bool)
    left[120:170, 40:90] = True
    right = np.zeros(rgb.shape[:2], bool)
    right[120:170, 300:350] = True                       # 相距很遠 → 兩個補洞區域
    rgb[left] = (220, 30, 30)
    rgb[right] = (30, 60, 220)
    img = tmp_path / "two.png"
    Image.fromarray(rgb).save(img)
    return img, rgb, left, right, write_masks(tmp_path / "m2", {"left": left, "right": right})


def _run_with_outcomes(make_config, tmp_path, monkeypatch, img, masks, outcomes, fallback="opencv"):
    """outcomes[(backend, region_index)] = "pass" | "fail_checks" | "error"（"pass" 用真的 OpenCV 補）。"""
    from animator.segmentation import models as M
    from animator.segmentation import pipeline as P

    real = P.run_worker

    def fake(cfg, kind, task, work, logs, tag):
        b = task["backend"]
        passing = [i for i in task["region_indices"] if outcomes.get((b, i)) == "pass"]
        entries = {}
        out = Path(work) / f"background_{b}.png"
        if passing:
            res = real(cfg, kind, {**task, "backend": "opencv", "region_indices": passing}, work, logs, f"{tag}_real")
            entries = {e["index"]: e for e in res["result"]["regions"]}
            Image.open(Path(work) / "background_opencv.png").save(out)
        else:
            Image.open(task["image"]).save(out)
        regions = []
        for i in task["region_indices"]:
            o = outcomes.get((b, i), "error")
            if o == "pass":
                regions.append(entries[i])
            elif o == "fail_checks":
                regions.append({"index": i, "checks": {"passed": False, "outside_unchanged": True,
                                                        "target_changed_ratio": 0.0}})
            else:
                regions.append({"index": i, "error": "RuntimeError: 模擬的失敗"})
        return {"result": {"regions": regions}}

    monkeypatch.setattr(P, "run_worker", fake)
    monkeypatch.setattr(M, "ensure_ready", lambda cfg, name: tmp_path)
    cfg = _cfg(make_config, tmp_path, f"ai:\n  inpaint:\n    fallback: {fallback}\n")
    res = P.segment(img, cfg, _opts(masks_dir=masks, inpaint="migan"))
    seg = json.loads((res.job.dir / "segmentation" / "segmentation.json").read_text(encoding="utf-8"))
    spec = json.loads(res.spec_path.read_text(encoding="utf-8"))
    bg = np.asarray(Image.open(res.job.dir / "layers" / "background.png").convert("RGB"))
    return res, seg, spec, bg, cfg


def test_migan_failure_with_successful_fallback_is_repaired(make_config, tmp_path, monkeypatch):
    img, rgb, left, right, masks = _two_objects(tmp_path)
    res, seg, spec, bg, _ = _run_with_outcomes(make_config, tmp_path, monkeypatch, img, masks,
                                               {("opencv", 0): "pass", ("opencv", 1): "pass"})
    assert all(r["repaired"] and r["status"] == "needs_review" and r["backend"] == "opencv" for r in res.regions)
    assert [a["result"] for a in res.regions[0]["attempts"]] == ["error", "passed_checks"]
    assert seg["background"]["status"] == "needs_review" and not res.incomplete
    assert not any(x.startswith("unrepaired") for x in res.review)
    assert (bg[left] == (220, 30, 30)).all(axis=1).mean() < 0.5


def test_migan_and_fallback_failure_marks_region_unrepaired(make_config, tmp_path, monkeypatch):
    from animator import cli

    img, rgb, left, right, masks = _two_objects(tmp_path)
    for outcomes in ({("migan", 0): "fail_checks", ("migan", 1): "fail_checks",
                      ("opencv", 0): "fail_checks", ("opencv", 1): "fail_checks"},   # 兩個都沒通過檢查
                     {}):                                                           # 兩個都出錯
        res, seg, spec, bg, cfg = _run_with_outcomes(make_config, tmp_path, monkeypatch, img, masks, outcomes)
        assert all(not r["repaired"] for r in res.regions)
        assert all(r["backend"] == "opencv" and len(r["attempts"]) == 2 for r in res.regions)
        assert res.incomplete and seg["background"]["status"] == "incomplete"
        assert spec["layers"][0]["meta"]["status"] == "incomplete"
        assert seg["background"]["unrepaired_regions"] == [0, 1]
        msgs = [x for x in res.review if x.startswith("unrepaired")]
        assert len(msgs) == 2 and all("原本的物件仍留在背景上" in m for m in msgs)
        assert (bg[left] == (220, 30, 30)).all() and (bg[right] == (30, 60, 220)).all()   # 物件還在背景上
    code = cli.main(["--config", str(cfg.config_file), "segment", "--image", str(img), "--masks", str(masks),
                     "--inpaint", "migan", "--no-refine"])
    assert code == 4


def test_unrepaired_without_fallback_is_incomplete(make_config, tmp_path, monkeypatch):
    img, rgb, left, right, masks = _two_objects(tmp_path)
    res, seg, *_ = _run_with_outcomes(make_config, tmp_path, monkeypatch, img, masks,
                                      {("migan", 0): "fail_checks", ("migan", 1): "fail_checks"}, fallback="none")
    assert res.incomplete and all(not r["repaired"] and r["status"] == "failed_checks" for r in res.regions)
    assert any("沒有可用的備援" in x for x in res.regions[0]["reasons"])


def test_mixed_region_outcomes(make_config, tmp_path, monkeypatch):
    """區域 0：MI-GAN 成功；區域 1：MI-GAN 失敗、備援也失敗 → 整體 incomplete（優先於 needs_review）。"""
    img, rgb, left, right, masks = _two_objects(tmp_path)
    res, seg, spec, bg, _ = _run_with_outcomes(make_config, tmp_path, monkeypatch, img, masks,
                                               {("migan", 0): "pass", ("migan", 1): "error",
                                                ("opencv", 1): "fail_checks"})
    r0, r1 = res.regions
    assert r0["repaired"] and r0["status"] == "passed_checks" and r0["backend"] == "migan"
    assert not r1["repaired"] and r1["status"] == "failed_checks"
    assert [a["result"] for a in r1["attempts"]] == ["error", "failed_checks"]
    assert seg["background"]["status"] == "incomplete" and seg["background"]["unrepaired_regions"] == [1]
    unrepaired = [x for x in res.review if x.startswith("unrepaired")]
    assert len(unrepaired) == 1 and "背景區域 1" in unrepaired[0]
    assert (bg[left] == (220, 30, 30)).all(axis=1).mean() < 0.5        # 區域 0 已修補
    assert (bg[right] == (30, 60, 220)).all()                           # 區域 1 的物件還在


def test_inpaint_none_is_reported_as_incomplete(make_config, tmp_path):
    from animator.segmentation.pipeline import segment

    img, _, circle, _ = _scene_file(tmp_path)
    res = segment(img, _cfg(make_config, tmp_path),
                  _opts(masks_dir=write_masks(tmp_path / "m", {"ball": circle}), inpaint="none"))
    assert res.incomplete and not res.regions[0]["repaired"]
