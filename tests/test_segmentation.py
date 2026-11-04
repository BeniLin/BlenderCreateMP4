"""Stage 2（S2-1）：animator segment 的測試。

- 沒有 AI 套件（numpy / OpenCV / Pillow）時整個檔案略過；Stage 1 的測試不受影響。
- @pytest.mark.ai：需要已下載並驗證過的模型（models/）；沒有時自動略過。
- @pytest.mark.blender：另外需要 Blender / FFmpeg。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
Image = pytest.importorskip("PIL.Image")

from animator.segmentation import compose as C  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "s2-0"


# ---------------------------------------------------------------- helpers

def scene(w=400, h=300):
    """有紋理的背景 + 紅色圓形 + 藍色方塊（物件互相重疊）。回傳 (rgb, circle_mask, square_mask)。"""
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:h, 0:w]
    bg = np.dstack([xx * 255 / w, yy * 255 / h, np.full((h, w), 120.0)])
    bg += rng.normal(0, 12, bg.shape)
    rgb = bg.clip(0, 255).astype(np.uint8)
    circle = (xx - 150) ** 2 + (yy - 150) ** 2 < 35 ** 2
    square = (xx > 175) & (xx < 245) & (yy > 110) & (yy < 180)
    rgb[square] = (30, 60, 220)
    rgb[circle] = (220, 30, 30)        # 圓形比較小，畫在前面（和「面積小的在前面」一致）
    return rgb, circle, square


def write_masks(d: Path, masks: dict[str, np.ndarray]) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for name, m in masks.items():
        Image.fromarray((m * 255).astype(np.uint8)).save(d / f"{name}.png")
    return d


def models_ready(names):
    from animator.config import load_config
    from animator.segmentation import models as M

    try:
        cfg = load_config(ROOT / "config" / "config.example.yaml")
    except Exception:  # noqa: BLE001
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return all(M.status(cfg, n).verified for n in names)


# ---------------------------------------------------------------- 界線

def test_stage1_cli_does_not_import_ai_packages():
    """`animator` CLI 本身不能載入 AI 套件（lazy import），Stage 1 才能在沒有 AI 依賴時運作。"""
    code = ("import sys; import animator.cli, animator.pipeline, animator.jobs; "
            "print([m for m in ('numpy','cv2','torch','transformers','onnxruntime','PIL','pymatting') "
            "if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})
    assert out.stdout.strip() == "[]"


def test_segmentation_never_imports_blender():
    import ast

    for p in (ROOT / "src" / "animator" / "segmentation").rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & {"bpy", "mathutils", "anim_bl"}, p


def test_config_float_fields_are_type_checked(make_config):
    from animator.errors import ConfigError

    cfg = make_config("ai:\n  inpaint:\n    review_area_ratio: 1\n")
    assert cfg.get("ai.inpaint.review_area_ratio") == 1.0
    with pytest.raises(ConfigError):
        make_config("ai:\n  inpaint:\n    review_area_ratio: 'big'\n")


# ---------------------------------------------------------------- compose

def test_load_image_pads_odd_size_and_applies_exif(tmp_path):
    p = tmp_path / "odd.png"
    Image.fromarray(np.zeros((31, 41, 3), np.uint8) + 7).save(p)
    src = C.load_image(p)
    assert src.rgb.shape == (32, 42, 3)
    assert src.original_size == (41, 31) and src.pad == (1, 1)

    exif = Image.Exif()
    exif[0x0112] = 6                                   # 需要順時針轉 90 度
    p2 = tmp_path / "rot.jpg"
    Image.fromarray(np.zeros((20, 40, 3), np.uint8)).save(p2, exif=exif.tobytes())
    assert C.load_image(p2).rgb.shape[:2] == (40, 20)


def test_clean_mask_removes_specks_and_fills_small_holes():
    m = np.zeros((100, 100), bool)
    m[20:80, 20:80] = True
    m[50, 50] = False                                  # 內部小洞
    m[2:4, 2:4] = True                                 # 碎塊
    out = C.clean_mask(m, min_area_px=50)
    assert out[50, 50] and not out[2:4, 2:4].any()


def test_filter_detections_normalizes_merged_labels_and_nms():
    dets = [
        {"label": "dog cat", "label_raw": "dog cat", "score": 0.9, "box": [0, 0, 10, 10]},
        {"label": "dog", "label_raw": "dog", "score": 0.8, "box": [0, 0, 10, 11]},    # 和第一個重疊 → NMS
        {"label": "cat", "label_raw": "cat", "score": 0.7, "box": [50, 50, 60, 60]},
        {"label": "cat", "label_raw": "cat", "score": 0.6, "box": [80, 80, 90, 90]},
    ]
    out = C.filter_detections(dets, ["dog", "cat"], max_per_label=1)
    assert [(d["label"], d["score"]) for d in out] == [("dog", 0.9), ("cat", 0.7)]
    assert out[0]["label_raw"] == "dog cat"


def test_assign_layers_orders_by_area_and_resolves_overlap():
    rgb, circle, square = scene()
    items = [C.ObjectItem("circle-1", "circle", 0.9, circle, "manual"),
             C.ObjectItem("square-1", "square", 0.9, square, "manual")]
    order = C.assign_layers(items, rgb.shape[0] * rgb.shape[1], review_ratio=0.15)
    small, big = sorted(order, key=lambda o: o.area)
    assert small.z > big.z                                        # 面積小的在前面
    assert not (order[0].visible & order[1].visible).any()        # 重疊的像素只屬於一個圖層
    assert (small.visible == small.mask).all()


def test_review_threshold_is_inclusive_at_15_percent():
    h, w = 100, 100
    at = np.zeros((h, w), bool)
    at[:15, :] = True                                             # 剛好 15%
    below = np.zeros((h, w), bool)
    below[:, :14] = True
    below[0, 14] = True                                           # 14.01%
    order = C.assign_layers([C.ObjectItem("a-1", "a", None, at, "manual"),
                             C.ObjectItem("b-1", "b", None, below, "manual")], h * w, 0.15)
    flagged = {o.id: bool(o.review) for o in order}
    assert flagged == {"a-1": True, "b-1": False}


def test_hole_regions_dilate_at_least_8px_and_split_components():
    m1 = np.zeros((200, 200), bool)
    m1[20:30, 20:30] = True
    m2 = np.zeros((200, 200), bool)
    m2[150:160, 150:160] = True
    hole, regions = C.hole_regions([m1, m2], (200, 200), dilate_px=2, review_ratio=0.15)
    assert len(regions) == 2
    assert hole[20, 12] and not hole[20, 11]                      # 外擴 8 px（不是 2 px）
    assert all(not r.reasons for r in regions)
    _, big = C.hole_regions([np.ones((200, 200), bool)], (200, 200), 12, 0.15)
    assert big[0].reasons and big[0].reasons[0].startswith("large_hole")


def test_migan_mask_polarity_is_255_keep_0_fill():
    from animator.segmentation.backends.migan_onnx import keep_mask

    hole = np.zeros((4, 4), bool)
    hole[1, 1] = True
    km = keep_mask(hole)
    assert km.dtype == np.uint8 and km[1, 1] == 0 and km[0, 0] == 255


def test_check_inpaint_detects_inverted_polarity():
    rgb, circle, _ = scene()
    hole = C.dilate(circle, 8)
    good = rgb.copy()
    good[hole] = 128
    assert C.check_inpaint(rgb, good, hole)["passed"]
    inverted = np.full_like(rgb, 128)                              # 洞外被改寫、洞內沒變
    inverted[hole] = rgb[hole]
    checks = C.check_inpaint(rgb, inverted, hole)
    assert not checks["passed"] and not checks["outside_unchanged"] and checks["target_changed_ratio"] == 0
    assert not C.check_inpaint(rgb, good[:-2], hole)["passed"]    # 尺寸不符


def test_composite_and_ring_restore_keep_outside_pixels():
    rgb, circle, _ = scene()
    hole = C.dilate(circle, 12)
    filled = np.full_like(rgb, 128)
    bg = C.composite_background(rgb, filled, hole)
    outside = ~C.dilate(hole, C.BLEND_BAND_PX)
    assert (bg[outside] == rgb[outside]).all()
    restored = C.restore_ring(rgb, bg, circle, hole, ring_px=12)
    assert (restored[~hole] == rgb[~hole]).all()                   # 洞外一律是原圖
    assert (restored[circle] == 128).all()                         # 物件本身的位置一定用修補結果
    edge = hole & ~C.dilate(circle, 11)                             # 外擴帶最外圈 → 接近原圖
    assert np.abs(restored[edge].astype(int) - rgb[edge].astype(int)).mean() < 15


def test_crop_layer_position_is_crop_center():
    rgb = np.zeros((100, 200, 3), np.uint8)
    alpha = np.zeros((100, 200), np.uint8)
    alpha[10:20, 50:90] = 255
    rgba, box, pos = C.crop_layer(rgb, alpha, pad=2)
    assert box == [48, 8, 92, 22] and rgba.shape == (14, 44, 4)
    assert pos == [70.0, 15.0]
    assert C.crop_layer(rgb, np.zeros_like(alpha)) is None


# ---------------------------------------------------------------- models

def test_model_registry_is_pinned_and_commercial():
    from animator.segmentation import models as M

    for spec in M.MODELS.values():
        assert len(spec.revision) == 40 and spec.revision != "main"
        assert len(spec.sha256) == 64 and spec.commercial


def test_ensure_ready_rejects_missing_and_noncommercial(make_config, tmp_path, monkeypatch):
    from animator.errors import AnimatorError
    from animator.segmentation import models as M

    cfg = make_config(f"paths:\n  models_dir: '{(tmp_path / 'm').as_posix()}'\n"
                      f"  output_dir: '{(tmp_path / 'o').as_posix()}'\n  temp_dir: '{(tmp_path / 't').as_posix()}'\n")
    with pytest.raises(AnimatorError, match="models download"):
        M.ensure_ready(cfg, "migan")
    nc = M.ModelSpec(**{**M.MODELS["migan"].__dict__, "commercial": False, "license": "CC-BY-NC"})
    monkeypatch.setitem(M.MODELS, "migan", nc)
    with pytest.raises(AnimatorError, match="非商用"):
        M.ensure_ready(cfg, "migan")


def test_wrong_hash_is_not_verified(make_config, tmp_path):
    from animator.segmentation import models as M

    cfg = make_config(f"paths:\n  models_dir: '{(tmp_path / 'm').as_posix()}'\n"
                      f"  output_dir: '{(tmp_path / 'o').as_posix()}'\n  temp_dir: '{(tmp_path / 't').as_posix()}'\n")
    d = tmp_path / "m" / "migan"
    d.mkdir(parents=True)
    (d / "migan_pipeline_v2.onnx").write_bytes(b"x" * M.MODELS["migan"].size_bytes)
    st = M.status(cfg, "migan")
    assert st.present and not st.verified and "SHA-256" in st.detail


# ---------------------------------------------------------------- pipeline（不需要模型）

def _segment_scene(make_config, tmp_path, inpaint="opencv", refine="none", **cfg_text):
    from animator.segmentation.pipeline import SegmentOptions, segment

    rgb, circle, square = scene()
    img = tmp_path / "scene.png"
    Image.fromarray(rgb).save(img)
    masks = write_masks(tmp_path / "masks", {"ball": circle, "box-1": square})
    cfg = make_config(cfg_text.get("text", ""))
    res = segment(img, cfg, SegmentOptions(masks_dir=masks, name="scene", inpaint=inpaint, refine=refine,
                                           duration=1.0))
    return res, rgb, circle, square


def test_segment_with_manual_masks_produces_valid_stage1_job(make_config, tmp_path):
    from animator.spec import load_spec
    from animator.spec.validate import validate

    res, rgb, circle, square = _segment_scene(make_config, tmp_path)
    job = res.job.dir
    spec = load_spec(res.spec_path)
    assert validate(spec, job).ok
    assert spec["version"] == 1 and spec["animations"] == []
    ids = [l["id"] for l in spec["layers"]]
    assert ids[0] == "background" and set(ids[1:]) == {"ball-1", "box-1"}
    for layer in spec["layers"]:
        assert (job / layer["image"]).is_file()
    for f in ("segmentation/source.png", "segmentation/overview.png", "segmentation/segmentation.json",
              "segmentation/masks/background_hole.png", "segmentation/masks/ball-1.png"):
        assert (job / f).is_file(), f

    # 背景：洞以外的像素和原圖逐像素相同；物件的位置已經被補掉（不再是純紅 / 純藍）
    bg = np.asarray(Image.open(job / "layers" / "background.png"))
    hole = np.asarray(Image.open(job / "segmentation/masks/background_hole.png")) > 127
    outside = ~C.dilate(hole, C.BLEND_BAND_PX)
    assert (bg[outside] == rgb[outside]).all()
    still_red = (bg[circle] == (220, 30, 30)).all(axis=1).mean()
    assert still_red < 0.5

    # 圖層疊回背景 ≈ 原圖：只有物件外 3 px 的保留帶和漸層帶（外擴帶內）會不同
    seg = json.loads((job / "segmentation/segmentation.json").read_text(encoding="utf-8"))
    assert seg["recomposite_vs_source"]["ratio_diff_gt8"] < 0.05
    # 圖層的顏色直接來自原圖
    ball = next(l for l in spec["layers"] if l["id"] == "ball-1")
    im = np.asarray(Image.open(job / ball["image"]))
    assert (im[im[..., 3] == 255][:, :3] == (220, 30, 30)).all()


def test_segment_flags_large_masks_and_opencv_fallback(make_config, tmp_path):
    """直接指定 opencv（不是備援）且面積 < 15% → passed_checks；面積 ≥ 15% 的遮罩一定被標記。"""
    res, *_ = _segment_scene(make_config, tmp_path)
    assert all(r["status"] == "passed_checks" for r in res.regions)
    assert not res.review_required

    from animator.segmentation.pipeline import SegmentOptions, segment
    rgb, _, _ = scene()
    big = np.zeros(rgb.shape[:2], bool)
    big[20:200, 20:300] = True
    img = tmp_path / "big.png"
    Image.fromarray(rgb).save(img)
    res2 = segment(img, make_config(), SegmentOptions(masks_dir=write_masks(tmp_path / "bigm", {"thing": big}),
                                                      inpaint="opencv", refine="none"))
    assert res2.review_required
    assert res2.regions[0]["status"] == "needs_review"
    assert any("large_mask" in r for r in res2.review) and any("large_hole" in r for r in res2.review)


def test_migan_failure_falls_back_to_opencv_and_requires_review(make_config, tmp_path):
    """MI-GAN 無法使用（模型不存在）→ 改用 OpenCV 備援，並且一律標記為需要人工檢查。"""
    text = (f"paths:\n  models_dir: '{(tmp_path / 'nomodels').as_posix()}'\n"
            f"  output_dir: '{(tmp_path / 'out').as_posix()}'\n  temp_dir: '{(tmp_path / 'tmp').as_posix()}'\n")
    res, rgb, *_ = _segment_scene(make_config, tmp_path, inpaint="migan", text=text)
    assert res.review_required
    assert all(r["backend"] == "opencv" and r["status"] == "needs_review" for r in res.regions)
    assert any("備援" in line for line in res.review)


def test_inpaint_none_keeps_original_background_and_requires_review(make_config, tmp_path):
    res, rgb, *_ = _segment_scene(make_config, tmp_path, inpaint="none")
    bg = np.asarray(Image.open(res.job.dir / "layers" / "background.png"))
    assert (bg == rgb).all()
    assert res.review_required and all(r["status"] == "not_inpainted" for r in res.regions)


def test_demo_animation_script_produces_valid_spec(make_config, tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from s2_1_demo_animate import add_demo_animation

    from animator.spec import load_spec
    from animator.spec.validate import validate

    res, *_ = _segment_scene(make_config, tmp_path)
    assert add_demo_animation(res.spec_path) == 4
    assert validate(load_spec(res.spec_path), res.job.dir).ok


# ---------------------------------------------------------------- 需要模型（@ai）

@pytest.mark.ai
def test_migan_repairs_object_hole_and_passes_checks(make_config, tmp_path):
    if not models_ready(["migan"]):
        pytest.skip("MI-GAN 模型尚未下載（animator models download migan）")
    from animator.segmentation.pipeline import SegmentOptions, segment

    img = ASSETS / "photo_desk.jpg"
    w, h = Image.open(img).size
    m = np.zeros((h, w), bool)
    cv2.circle(m.view(np.uint8), (1500, 300), 90, 1, -1)          # 小物件（< 15%）
    res = segment(img, make_config(), SegmentOptions(masks_dir=write_masks(tmp_path / "mk", {"thing": m}),
                                                     inpaint="migan", refine="none"))
    r = res.regions[0]
    assert r["backend"] == "migan" and r["status"] == "passed_checks", r
    assert r["checks"]["outside_unchanged"] and r["checks"]["target_changed_ratio"] > 0.9
    seg = json.loads((res.job.dir / "segmentation/segmentation.json").read_text(encoding="utf-8"))
    assert seg["models"]["migan"]["revision"] == "406830d0fa60666da0071c342ad2fbc8f30c5c64"


@pytest.mark.ai
def test_full_ai_pipeline_on_occlusion_photo(make_config):
    """端到端：Grounding DINO → SAM 2.1 → PyMatting → MI-GAN（CPU，離線）。"""
    if not models_ready(["grounding-dino-tiny", "sam2.1-hiera-tiny", "migan"]):
        pytest.skip("模型尚未下載（animator models download --all）")
    from animator.segmentation.pipeline import SegmentOptions, segment

    res = segment(ASSETS / "occlusion_dogcat.jpg", make_config(),
                  SegmentOptions(labels=["dog", "cat"], name="it-dogcat"))
    labels = sorted(l["meta"]["label"] for l in res.layers[1:])
    assert labels == ["cat", "dog"]
    for layer in res.layers[1:]:
        im = np.asarray(Image.open(res.job.dir / layer["image"]))
        a = im[..., 3]
        assert (a == 255).mean() > 0.2 and ((a > 0) & (a < 255)).any()   # 有實心區域，也有精修過的邊緣
    assert res.regions and all(r["backend"] == "migan" for r in res.regions)
    seg = json.loads((res.job.dir / "segmentation/segmentation.json").read_text(encoding="utf-8"))
    assert seg["recomposite_vs_source"]["ratio_diff_gt8"] < 0.02
    assert seg["timings_sec"]["total"] < 180


# ---------------------------------------------------------------- Stage 1 渲染（@blender）

@pytest.mark.blender
def test_segmented_job_renders_with_stage1(tmp_path):
    """segment 的輸出直接交給 Stage 1：不加動畫時，第 1 格和「原圖單一圖層」的渲染結果相同（洞外）。"""
    from animator.config import load_config
    from animator.errors import AnimatorError
    from animator.pipeline import render
    from animator.segmentation.pipeline import SegmentOptions, segment
    from animator.tools import find_blender, find_ffmpeg

    try:
        cfg = load_config(None, {"paths.output_dir": str(tmp_path / "output"),
                                 "paths.temp_dir": str(tmp_path / "tmp")})
        find_blender(cfg)
        find_ffmpeg(cfg)
    except AnimatorError as exc:
        pytest.skip(f"沒有可用的 Blender/FFmpeg：{exc}")

    rgb, circle, square = scene()
    img = tmp_path / "scene.png"
    Image.fromarray(rgb).save(img)
    res = segment(img, cfg, SegmentOptions(masks_dir=write_masks(tmp_path / "m", {"ball": circle, "box": square}),
                                           inpaint="opencv", refine="none", duration=1.0))
    layered = render(res.spec_path, cfg, frame=1, argv=["pytest"]).output

    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    Image.fromarray(rgb).save(ref_dir / "src.png")
    (ref_dir / "spec.json").write_text(json.dumps({
        "version": 1, "meta": {"name": "ref"}, "canvas": {"width": 400, "height": 300, "fps": 30, "duration": 1.0},
        "layers": [{"id": "src", "image": "src.png", "z": 0, "position": [200, 150]}], "animations": []}),
        encoding="utf-8")
    reference = render(ref_dir / "spec.json", cfg, frame=1, name="ref", argv=["pytest"]).output

    a = np.asarray(Image.open(layered).convert("RGB")).astype(int)
    b = np.asarray(Image.open(reference).convert("RGB")).astype(int)
    assert a.shape == b.shape == (300, 400, 3)
    d = np.abs(a - b).max(axis=2)
    hole = np.asarray(Image.open(res.job.dir / "segmentation/masks/background_hole.png")) > 127
    outside = ~C.dilate(hole, 2)                       # Blender 的取樣濾波約 1 px
    assert (d[outside] > 8).mean() < 0.001, f"洞外差異像素比例 {(d[outside] > 8).mean():.4f}"
    assert (d > 8).mean() < 0.05, f"整體差異像素比例 {(d > 8).mean():.4f}"   # 只有外擴帶內會不同

    # Stage 1 回歸測試 6：Blender 渲染的分層結果 = numpy 依 spec 疊圖（filter_size 0 之後應該幾乎相同）
    spec = json.loads(res.spec_path.read_text(encoding="utf-8"))
    bg = np.asarray(Image.open(res.job.dir / "layers" / "background.png").convert("RGB"))
    stack = [(np.asarray(Image.open(res.job.dir / l["image"]).convert("RGBA")), l["meta"]["crop_box"])
             for l in sorted(spec["layers"][1:], key=lambda l: l["z"])]
    recomp = C.recomposite(bg, stack).astype(int)
    diff = np.abs(a - recomp)
    assert diff.mean() < 0.05 and (diff.max(axis=2) > 2).mean() < 0.001, (diff.mean(), diff.max())
