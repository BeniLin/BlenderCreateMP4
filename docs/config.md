# config.yaml 設定規格（config_version 1）

範本：`config/config.example.yaml`（納入 Git）
本機：`config/config.yaml`（不納入 Git，從範本複製）

## 1. 檔案位置與載入順序

1. 命令列 `--config <path>`
2. 環境變數 `ANIMATOR_CONFIG`
3. `<專案根目錄>/config/config.yaml`
4. 以上都不存在 → 報錯，提示從 `config.example.yaml` 複製

**專案根目錄**的判定方式（不依賴目前工作目錄）：

1. 環境變數 `ANIMATOR_ROOT`（如果有設定）
2. 否則從 `src/animator/paths.py` 往上找，第一個同時含有 `pyproject.toml` 和 `config/` 的目錄

`config.yaml` 可以只寫部分欄位。沒寫的欄位會使用程式內建的預設值；這些預設值和範本相同，而且都不是任何電腦專屬的值。

## 2. 值的優先順序

```
命令列參數  >  環境變數 ANIMATOR_*  >  config.yaml  >  內建預設值
```

| 環境變數 | 覆寫的欄位 |
|---|---|
| `ANIMATOR_CONFIG` | 設定檔路徑 |
| `ANIMATOR_ROOT` | 專案根目錄 |
| `ANIMATOR_BLENDER` | `tools.blender.executable` |
| `ANIMATOR_FFMPEG` | `tools.ffmpeg.executable` |
| `ANIMATOR_PYTHON` | `tools.python.executable` |

## 3. 路徑解析規則

所有路徑欄位都依照下面的順序處理：

1. 展開 `${VAR}` 環境變數；如果變數不存在，直接報錯，不會變成空字串
2. 展開 `~`（使用目前使用者的家目錄，**不需要**在設定裡寫出使用者名稱）
3. 相對路徑 → 以**專案根目錄**為基準
4. 正規化成絕對路徑（`pathlib.Path.resolve()`）

**執行檔欄位**（`tools.*.executable`）多一條規則：如果值裡面沒有路徑分隔字元（例如 `ffmpeg`），就用 `shutil.which()` 從 PATH 尋找。

## 4. 欄位規格

### `config_version`（int，必填）
目前固定為 `1`。程式碰到不認識的版本會直接報錯。

### `tools.blender`

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `executable` | str | （必填） | Blender 執行檔 |
| `min_version` | str | `"4.2"` | 最低支援版本；比較 `blender --version` 第一行的版本號 |
| `extra_args` | list[str] | `["--factory-startup"]` | 加在 `-b` 之後的參數 |
| `timeout_sec` | int | `3600` | 單次渲染逾時 |

支援的版本：**4.2 LTS 以上**；目前實測過的版本：**5.2.3 LTS**。不同版本之間的 API 差異，集中在 `blender/compat.py` 處理（引擎名稱、動畫 action API、材質透明設定）。

### `tools.ffmpeg`

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `executable` | str | `"ffmpeg"` | 必須支援 `libx264`（`animator doctor` 會檢查） |
| `ffprobe` | str | `""` | 留空 = 在 ffmpeg 所在的目錄找 `ffprobe`；找不到也只會略過影片長度驗證，不會報錯 |
| `timeout_sec` | int | `600` | |

### `tools.python`

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `executable` | str | `""` | Stage 2 起，執行 AI worker 用的 Python。留空 = `sys.executable` |

Blender 使用的是自己內建的 Python，**不讀這個欄位**。

### `paths`

| 欄位 | 預設 | 用途 |
|---|---|---|
| `input_dir` | `assets` | 原始圖片、人工準備的圖層 |
| `output_dir` | `output` | `output/<job_id>/`：spec.json、layers/、result.mp4、renders.json、history/ |
| `temp_dir` | `tmp` | `tmp/<job_id>/<render_id>/frames/`：渲染用的 PNG 影格，成功之後會自動刪除 |
| `models_dir` | `models` | AI 模型權重（Stage 2 起使用） |

目錄如果不存在會自動建立；如果沒有寫入權限，就直接報錯。

### `jobs`

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `id_format` | str | `"%Y%m%d-%H%M%S_{name}"` | strftime 格式加上 `{name}`；`{name}` 只保留 `[a-z0-9_-]`，其他字元會被移除 |
| `keep_frames` | bool | `false` | 成功輸出 MP4 之後，是否保留影格 |
| `overwrite` | bool | `false` | 重新渲染時，是否直接刪除舊的輸出檔；`false` 時，舊檔會移到它所屬那次渲染的 `history/<render_id>/` 目錄。不論設定為何，每次渲染的 spec、設定和報告都會保留 |

### `defaults`
這裡的值只會在**建立新 spec** 時寫進 spec，或在 spec 缺少這個欄位時拿來補上。**spec 裡已經有的值永遠優先**，所以同一份 spec 換到另一台電腦渲染，結果也會一樣。

| 欄位 | 預設 | 說明 |
|---|---|---|
| `canvas.fps` | `30` | |
| `canvas.background_color` | `"#000000"` | |
| `render.engine` | `"eevee"` | `eevee` / `cycles`，這是抽象名稱，由 `blender/anim_bl/compat.py` 轉成各版本實際的名稱 |
| `render.samples` | `16` | |
| `render.view_transform` | `"Standard"` | |
| `encode.codec` / `crf` / `preset` / `pix_fmt` | `libx264` / `18` / `medium` / `yuv420p` | |
| `preview.resolution_percentage` / `samples` / `crf` | `25` / `4` / `28` | 只在加上 `--preview` 時套用，**不會寫回 spec** |

### `logging.level`
`DEBUG` / `INFO` / `WARNING` / `ERROR`，預設是 `INFO`。

### `ai`（Stage 2：`animator segment` / `animator models`）
Stage 1 的指令（`validate` / `new` / `render`）不讀這一段，但會檢查格式是否正確。

| 欄位 | 預設 | 說明 |
|---|---|---|
| `device` | `"cpu"` | 目前只支援 `cpu`（沒有安裝 CUDA 版 PyTorch）；其他值會被拒絕 |
| `threads` | `4` | 每個 AI 子行程的 CPU 執行緒數 |
| `max_side` | `1024` | 偵測 / 分割用的處理圖長邊；輸出的圖層、背景都是原圖解析度 |
| `worker_timeout_sec` | `1800` | 每個 AI 子行程的逾時 |
| （推論一律離線） | — | AI 子行程一律設定 `HF_HUB_OFFLINE=1`，而且只讀本機檔案；只有 `animator models download` 會連網（沒有可以關掉的設定） |
| `models.allow_noncommercial` | `false` | 拒絕載入標記為非商用授權的模型 |
| `detection.backend` / `model` | `grounding_dino` / `grounding-dino-tiny` | |
| `detection.box_threshold` / `text_threshold` | `0.35` / `0.25` | Grounding DINO 的門檻 |
| `detection.max_per_label` | `5` | 每個標籤最多保留幾個物件 |
| `detection.default_labels` | `["person", "animal", "plant", "vehicle"]` | 沒有給 `--labels` 時使用（英文） |
| `segmentation.backend` / `model` | `sam2` / `sam2.1-hiera-tiny` | 用 `segment --masks <資料夾>` 時改用使用者提供的遮罩，不跑偵測和分割 |
| `segmentation.min_area_px` | `400` | 小於這個面積（原圖像素）的碎塊會被移除 |
| `refine.backend` | `pymatting` | `pymatting` / `none` |
| `refine.band_px` | `8` | 邊緣精修帶寬度（原圖像素） |
| `inpaint.backend` / `model` | `migan` / `migan` | `migan` / `opencv` / `none` |
| `inpaint.fallback` | `opencv` | MI-GAN 失敗時的備援（`opencv` / `none`）；備援結果一律標記為需要人工檢查 |
| `inpaint.dilate_px` | `12` | 物件遮罩外擴後才補背景；小於 8 時以 8 計算 |
| `inpaint.review_area_ratio` | `0.15` | 單一物件遮罩或補洞區域 ≥ 圖片面積的這個比例 → 標記為需要人工檢查 |
| `inpaint.mem_arena` | `false` | ONNX Runtime 的 CPU memory arena；關閉可以省一半記憶體，warm 速度不變（S2-0.1 實測） |
| `planner.backend` | `manual` | Stage 4 才使用 |

## 5. 驗證（`animator doctor`）

- 設定檔存在，`config_version` 正確，沒有不認識的欄位（打錯字會出現警告）
- Blender 可以執行，而且版本 ≥ `min_version`
- FFmpeg 可以執行，而且支援 libx264；ffprobe 找不到時只顯示警告
- Python（`tools.python`，AI 子行程用）、AI 套件是否安裝（只影響 `segment`），以及每個模型的快速檢查（檔案存在 + 大小；完整的 SHA-256 用 `animator models verify`）
- `output_dir`、`temp_dir` 可以寫入
- 列出最後實際採用的設定，並標示每個值的來源（CLI / env / config / default）

## 6. 依賴

讀取 YAML 需要 **PyYAML**（MIT 授權，純 Python）。它只在系統 Python 端使用。**Blender 端不讀 config.yaml**，需要的資訊都由系統端透過 `spec.resolved.json` 和命令列參數傳過去。
