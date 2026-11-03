# CLAUDE.md

AI Image-to-Animation：輸入圖片 + 自然語言描述，輸出 MP4。
架構與部署見 `README.md`；格式規格見 `docs/spec.md`、`docs/config.md`；目前要做的工作見 `docs/stage1.md`。

## 絕對規則

- **不要修改 `scripts/test_animation.py`**（它是 Stage 0 的基準腳本，裡面寫死了路徑；這是唯一允許的例外）。
- **沒有使用者同意，不要安裝任何套件**（pip / Blender 內建 Python / winget 都一樣）。要用新套件時，先說明用途和授權。
- **Stage 1 不要碰 AI**：不要安裝或 import rembg、OpenCV、SAM 2、torch 等套件。
- **程式裡不能寫死電腦專屬路徑**（磁碟代號、`Users\<名稱>`、Program Files…）。外部工具和各個目錄一律從 config 取得；專案內的路徑以專案根目錄為基準（`animator.paths.project_root()`）。
- `config/config.yaml` 是本機設定，不納入 Git。新增設定欄位時，要同步更新 `config.example.yaml`、`docs/config.md`，以及 `config.py` 裡的內建預設值。
- 每個圖層都必須是獨立的平面，而且有自己的材質；不要把整張圖片當成一個物件做動畫。
- 不要覆蓋 `output/` 根目錄裡的舊結果；新的輸出一律放到 `output/<job_id>/`。

## 兩個 Python 的界線

| | `src/animator/` | `blender/` |
|---|---|---|
| 執行環境 | 系統 Python（`.venv`） | Blender 內建 Python |
| 可以 import | 標準函式庫 + `.venv` 裡的套件 | **只能用** `bpy`、`bpy_extras`、`mathutils`、標準函式庫 |
| 禁止 | `import bpy` | import `animator`、PyYAML、任何 AI 套件，以及讀 config.yaml |

- 兩邊只透過檔案交換資料：系統端寫 `tmp/<job_id>/spec.resolved.json` → Blender 讀取並寫出 `frames/*.png` 和 `render_report.json`。
- 驗證、補預設值、路徑解析、秒換影格、展開別名、推算 `from`，**全部在系統端完成**。Blender 端只負責「照著 resolved spec 建場景」。
- 呼叫方式：`<blender> -b --factory-startup -P blender/build_scene.py -- --resolved <path> --frames-dir <path> [--frame N]`；參數用 `sys.argv[sys.argv.index("--")+1:]` 讀取。
- `blender/` 裡的模組要互相 import 時，入口先把 `blender/` 加進 `sys.path`。

## Spec 規則（詳見 docs/spec.md）

- 座標用畫布像素座標，原點在左上角，y 往下；旋轉角度順時針為正；時間用秒。單位換算只在 `blender/units.py` 裡做。
- 每個效果只控制一個通道；同一個 target 的同一個通道，時間不可以重疊。
- 改 spec 格式時：會影響舊 spec 的話，要把 `version` + 1，並同步更新 `docs/spec.md`、`schema.py`、`validate.py`、`resolve.py`、`examples/`。

## 新增動畫效果

1. `blender/effects/<name>.py`：`@register("<name>", channel="<channel>")`，加上 `apply(obj, anim, ctx)`。
2. 系統端：在 `validate.py` 加上這個效果的 params 驗證；如果有 `from` 推算或別名，要寫在 `resolve.py`。
3. 更新 `docs/spec.md` 第 4 節的效果表格，並在 `examples/` 加一個範例。
4. 效果只能改動自己的 target 和自己的通道。

## Blender 版本相容（集中寫在 `blender/compat.py`）

支援 4.2 LTS 以上，實測過 5.2.3。以下幾點**都要在 5.2 上實際確認**之後才能依賴：

- 引擎名稱：5.x 是 `'BLENDER_EEVEE'`（`test_animation.py` 已確認可用）；4.2~4.x 是 `'BLENDER_EEVEE_NEXT'`。spec 裡只寫抽象名稱 `eevee`。
- 動畫資料：4.4 以後改成 slotted action，5.x 已經移除舊的 `action.fcurves`。要修改 fcurve 時，透過 `bpy_extras.anim_utils.action_get_channelbag_for_slot()` 取得 channelbag。
- 半透明材質：EEVEE 用 `material.surface_render_method`（`'BLENDED'` / `'DITHERED'`）。
- 色彩管理的 view transform 要設成 `'Standard'`，才能保留原圖顏色。
- 一律用 `--factory-startup` 執行，不依賴 `test.blend`（它目前不在專案裡）。

## 環境（本機的值寫在 config.yaml，這裡只是參考）

- Windows 11；Blender 5.2.3（不在 PATH）；系統 Python 3.12.1（PATH 上還有 3.11）；FFmpeg 已在 PATH
- PyYAML 目前**沒有安裝**，Stage 1 開始前要先取得使用者同意
- GPU：MX450 2 GB，未來的 AI 模型要選小版本，並且要能退回 CPU 執行

## 測試與驗證

- 單元測試用 pytest；需要 Blender 的整合測試加上 `@pytest.mark.blender` 標記。
- 驗證渲染時，先用 `--frame N` 或 `--preview` 這種低成本的方式，確認沒問題再跑完整渲染。
- 完成一個效果之後，要實際渲染並檢查影格，不能只看程式碼就說完成。
- 如果 Blender 或 FFmpeg 失敗，看 `output/<job_id>/logs/` 裡的 log，回報時引用實際的錯誤內容。

## 程式風格

- 程式碼的識別字用英文；註解和給使用者看的訊息可以用繁體中文。
- 型別提示 + dataclass；用 `pathlib` 處理路徑，記得路徑裡可能有空格。
- 呼叫 subprocess 時，參數一律用 list 傳，不要用 `shell=True`。
- 每個後端（segmentation / inpaint / planner）都要實作 `base.py` 裡的介面，並且可以透過 config 切換。
