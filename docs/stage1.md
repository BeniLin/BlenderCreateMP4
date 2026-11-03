# Stage 1：人工圖層 → spec.json → Blender → FFmpeg

**狀態：✅ 完成（2026-11-03）**

**目標**：使用人工準備的透明 PNG 圖層，驗證整條流程：`spec.json` → 驗證 → `spec.resolved.json` → Blender 背景渲染 PNG 影格 → FFmpeg 輸出 MP4。

**範圍外**：任何 AI 模型（rembg、OpenCV、SAM 2…）、自然語言 planner、光暈、陰影、粒子、鏡頭效果。

## 1. 依賴（只裝在 `.venv`）

| 套件 | 版本 | 用途 |
|---|---|---|
| PyYAML | 6.0.3 | 讀 config.yaml（執行時需要） |
| pytest | 9.2.0 | 測試（開發時需要）；會連帶安裝 colorama 0.4.6、iniconfig 2.3.1、packaging 26.3、pluggy 1.7.0、Pygments 2.21.0 |

系統 Python 和 Blender 內建 Python **都沒有安裝**任何套件。Stage 1 不需要 Pillow：PNG 尺寸用標準函式庫讀取，範例圖層也用標準函式庫產生。

## 2. 開發項目

| # | 項目 | 結果 |
|---|---|---|
| S1-1 | 專案骨架 | `pyproject.toml`、`requirements*.txt`、`animator.cmd`、`src/animator/`、`blender/`、`tests/` |
| S1-2 | 路徑與設定 | `paths.py`、`config.py`（預設值 / config / 環境變數 / CLI 合併，記錄每個值的來源，對不認識的欄位顯示警告） |
| S1-3 | 外部工具 | `tools.py`、`animator doctor` |
| S1-4 | Spec | `spec/schema.py`、`validate.py`、`resolve.py`、`png.py`、`animator validate` |
| S1-5 | 工作目錄與紀錄 | `jobs.py`：`output/<job_id>/`、`history/<render_id>/`、`renders.json`、舊輸出檔備份 |
| S1-6 | Blender 場景 | `blender/build_scene.py`、`anim_bl/compat.py`、`units.py`、`scene_setup.py`、`layers.py` |
| S1-7 | 動畫效果 | `anim_bl/easing.py`、`tracks.py`、`effects/`（move、scale、rotate、opacity；fade_in/out 在系統端展開） |
| S1-8 | 渲染與合成 | `render/blender_runner.py`、`render/ffmpeg.py`（編碼 + ffprobe + 完整解碼檢查） |
| S1-9 | CLI | `doctor`、`validate`、`new`、`render [--preview] [--frame N] [--keep-frames]` |
| S1-10 | 範例 | `examples/basic/`（6 個圖層、8 筆動畫）；`scripts/make_sample_layers.py` |
| S1-11 | 測試 | 64 個測試：單元測試 56 個、Blender 整合測試 8 個（逐格檢查像素） |
| S1-12 | 文件 | README、docs/spec.md（補上核心規則和追溯）、docs/config.md、CLAUDE.md |

### 和原本計畫不同的地方（沒有改變架構）

- Blender 端的模組放在 `blender/anim_bl/` 套件裡，避免 `compat`、`units` 這類名稱和 Blender 內建模組撞名。
- 每次渲染的紀錄和 log 放在 `history/<render_id>/`，而不是 job 根目錄的 `logs/`，這樣每一次渲染都能追溯。
- 沒有用 `pip install -e .`：editable 安裝需要在建置時另外下載 setuptools，這不在核准的依賴清單裡。改用 `animator.cmd`（設定 `PYTHONPATH=src`，再用 `.venv` 的 Python 執行）。如果之後要用 `animator` 指令，再另外核准。

## 3. 驗收結果

| # | 標準 | 結果 |
|---|---|---|
| 1 | `animator doctor` 全部通過 | ✅ |
| 2 | 產生的 MP4 解析度、fps、長度和 spec 一致 | ✅ h264 1280×720、30 fps、120 格、4.00 s，完整解碼沒有錯誤 |
| 3 | 每個圖層的移動、縮放、旋轉、透明度都能獨立運作 | ✅ 整合測試在指定時間點檢查像素（星星位置、太陽半徑、橫條方向、標題/雲的透明度、背景不動） |
| 4 | 手動修改 spec 後重新渲染，結果會跟著改變，舊 MP4 和紀錄都有保留 | ✅ `test_rerender_in_place_after_manual_edit` |
| 5 | 用 config 或環境變數就能切換 Blender | ✅ `test_precedence_cli_over_env_over_config` |
| 6 | 程式裡沒有電腦專屬路徑 | ✅ `test_no_machine_specific_paths_in_code` |
| 7 | `blender/` 只 import bpy 和標準函式庫 | ✅ `test_blender_side_imports_only_bpy_and_stdlib` |
| 8 | `scripts/test_animation.py` 沒有被修改 | ✅ SHA-256 比對（`test_stage0_script_unchanged`） |
