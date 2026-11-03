# Stage 1 開發項目：人工圖層 → spec.json → Blender → FFmpeg

**目標**：使用人工準備的透明 PNG 圖層，驗證整條流程：`spec.json` → 驗證 → `spec.resolved.json` → Blender 背景渲染 PNG 影格 → FFmpeg 輸出 MP4。

**範圍外**：任何 AI 模型（rembg、OpenCV、SAM 2…）、自然語言 planner、光暈、陰影、粒子、鏡頭效果。

## 0. 開始前需要確認的事項

| # | 項目 | 說明 |
|---|---|---|
| D1 | **PyYAML** | 讀取 `config.yaml` 需要它。系統 Python 和 Blender 內建 Python **目前都沒有安裝**。它是 MIT 授權的純 Python 套件，只裝在系統端的 `.venv` 裡，Blender 端不需要 |
| D2 | **pytest** | 開發時跑測試用。全域的 Python 3.12 已經有，但 `.venv` 裡還要另外安裝 |
| D3 | **`.venv`** | 用 `C:\Python312\python.exe -m venv .venv` 建立。這個環境和系統 Python 互相隔離 |
| D4 | **git init** | 目前這個目錄還不是 repo；`.gitignore` 已經準備好 |

Stage 1 **不需要** Pillow：PNG 尺寸直接用標準函式庫讀 IHDR 標頭取得；範例圖層也用標準函式庫（`zlib` + `struct`）產生。

## 1. 開發項目

| # | 項目 | 產出 | 執行環境 |
|---|---|---|---|
| S1-1 | 專案骨架 | `pyproject.toml`（入口 `animator`）、`src/animator/`、`blender/`、`tests/` | — |
| S1-2 | 路徑與設定 | `paths.py`：判定專案根目錄、展開 `~` 和 `${VAR}`。`config.py`：讀 YAML、和預設值合併、套用環境變數和 CLI 覆寫、記錄每個值的來源、對不認識的欄位顯示警告 | 系統 |
| S1-3 | 外部工具 | `tools.py`：找出 Blender、FFmpeg、ffprobe，解析 Blender 版本、檢查 libx264；`animator doctor` | 系統 |
| S1-4 | Spec | `spec/schema.py`（dataclass）、`spec/validate.py`（一次列出所有錯誤，並指出 JSON 路徑）、`spec/resolve.py`（補預設值、轉絕對路徑、讀 PNG 尺寸、秒換影格、展開別名、算好 `from`）；`animator validate` | 系統 |
| S1-5 | 工作目錄 | `jobs.py`：產生 job_id、建立 `output/<job_id>/` 和 `tmp/<job_id>/`、複製外部 spec 與圖層進來、備份舊的 MP4 | 系統 |
| S1-6 | Blender 場景 | `blender/build_scene.py`（入口：讀參數、檢查 spec 版本）、`compat.py`（引擎名稱、action API、材質透明設定）、`units.py`、`scene_setup.py`（解析度、fps、色彩管理、背景色、剛好對齊畫布的正交攝影機）、`layers.py`（依像素大小建立平面、依 anchor 偏移原點、每個圖層建立自己的材質：圖片 alpha × Object Info alpha） | **Blender** |
| S1-7 | 動畫效果 | `blender/easing.py`；`blender/effects/`：registry 加上 `move`、`scale`、`rotate`、`opacity`、`fade_in`、`fade_out` | **Blender** |
| S1-8 | 渲染與合成 | `render/blender_runner.py`（subprocess、逾時、log 寫到 `logs/blender.log`、讀取 `render_report.json`）；`render/ffmpeg.py`（編碼，並用 ffprobe 驗證影片長度、解析度、fps） | 系統 |
| S1-9 | CLI | `animator doctor`、`validate <spec>`、`new --layers <dir> [--name]`（用圖層資料夾產生起始 spec）、`render <spec> [--preview] [--frame N] [--keep-frames]` | 系統 |
| S1-10 | 範例 | `examples/basic/`：`spec.json` 和 4 張 RGBA 圖層（背景、圓形、星形、標題條）；`scripts/make_sample_layers.py` 用標準函式庫產生這些圖層 | 系統 |
| S1-11 | 測試 | 單元測試：config、路徑解析、spec 驗證和 resolve（時間換算、`from` 推算、重疊偵測）。整合測試（標記 `blender`）：用預覽模式渲染範例並用 ffprobe 驗證。靜態檢查：掃描 `blender/` 的 import，只允許 `bpy`、`bpy_extras`、`mathutils` 和標準函式庫；掃描 `src/`、`blender/`，不能有寫死的電腦專屬路徑 | 系統 |
| S1-12 | 文件 | 依實際做出來的結果更新 README、docs/spec.md、docs/config.md | — |

### `render` 的行為

- spec 本來就在 `output/<job_id>/` 裡面 → **直接在原地重新渲染**（人工修改後重新渲染就走這條路）。
- spec 在其他位置（例如 `examples/`）→ 先建立新的 job，把 spec 和它用到的圖層複製進去，再渲染，所以 `examples/` 不會被修改。
- `--preview`：套用 config 的預覽設定，輸出 `preview.mp4`，不會覆蓋 `result.mp4`。
- `--frame N`：只渲染一格，輸出 `still_N.png`，用來快速檢查。

## 2. 實作順序

```
S1-1 → S1-2 → S1-3（doctor 通過）
     → S1-4（validate 通過，產生 resolved spec）
     → S1-6 + S1-7（先用 blender -b 直接執行 resolved spec，用 --frame 檢查單格）
     → S1-5 + S1-8 + S1-9（全部串起來）
     → S1-10 + S1-11 → S1-12
```

## 3. 驗收標準

1. 這台電腦上 `animator doctor` 全部通過。
2. `animator render examples/basic/spec.json` 能產生 MP4：解析度和 fps 跟 spec 一致，長度 = `duration` ± 1 格。
3. 每個圖層的移動、縮放、旋轉、透明度都能獨立運作：背景保持不動，其他圖層各自依照時間軸變化（檢查指定時間點的影格）。
4. 手動修改 `output/<job_id>/spec.json`（改 `to`、改 `easing`、設 `enabled: false`、隱藏某個圖層）之後執行 `render`，結果會跟著改變，舊的 MP4 也有備份。
5. 用 `ANIMATOR_BLENDER` 或修改 `config.yaml` 就能切換 Blender 執行檔，不需要改程式碼。
6. 在 `src/`、`blender/` 裡搜尋電腦專屬路徑（磁碟代號、`Users\`、使用者名稱），結果為零。
7. `blender/` 只 import `bpy`、`bpy_extras`、`mathutils` 和標準函式庫。
8. `scripts/test_animation.py` 沒有被修改。
