# AI Image-to-Animation

輸入一張靜態圖片和一段自然語言描述，自動產生 MP4 動畫。

```
image.png + 「小鳥從左邊飛進來，太陽慢慢放大，標題最後淡入」  →  result.mp4
```

工具會先把圖片拆成多個帶透明背景的**獨立圖層**，每個圖層各自加上移動、縮放、旋轉、透明度等動畫，再用 Blender 渲染、FFmpeg 合成影片。**不會**把整張圖當成一個物件平移。

| 文件 | 內容 |
|---|---|
| [docs/spec.md](docs/spec.md) | `spec.json` 完整格式規格（座標、時間、anchor、排序、透明度規則） |
| [docs/config.md](docs/config.md) | `config.yaml` 完整設定規格 |
| [docs/stage1.md](docs/stage1.md) | Stage 1 開發項目與驗收結果 |
| [CLAUDE.md](CLAUDE.md) | 開發規則 |

> **目前狀態**：**Stage 1 已完成**（人工 PNG 圖層 → spec.json → Blender → MP4）。**Stage 2 第一版（S2-1）等待審查**：`animator segment` 可以把一張圖片自動分成透明 PNG 圖層 + 補好的背景，輸出標準 spec.json 交給 Stage 1 渲染（只用 CPU、離線）。自然語言規劃（Stage 4 以後）**還沒有實作**。

---

## 1. 架構

```
┌──────────────── 系統 Python（.venv）────────────────┐      ┌────── Blender 內建 Python ──────┐
│                                                      │      │                                  │
│ 圖片+描述 → 圖層分離 → 動畫規劃 →  spec.json          │      │  blender/build_scene.py          │
│            (Stage 2-3) (Stage 4)   ▲ 可人工編輯       │      │   → 每個圖層一個平面 + 獨立材質  │
│ Stage 1：人工 PNG 圖層 ────────────┘     │            │      │   → effects 打關鍵影格           │
│                                    驗證 + resolve     │      │   → 渲染 PNG 影格                │
│                                          ▼            │      │                                  │
│                              spec.resolved.json ──────┼─────▶│                                  │
│                                                       │      └───────────────┬──────────────────┘
│  FFmpeg → MP4 → ffprobe 驗證  ◀──────────── frames/*.png ─────────────────────┘
└───────────────────────────────────────────────────────┘
```

| | 系統 Python（`src/animator/`） | Blender 內建 Python（`blender/`） |
|---|---|---|
| 職責 | 讀設定、驗證和解析 spec、管理工作和紀錄、呼叫 Blender/FFmpeg；未來的 AI 模組 | 讀 `spec.resolved.json`，建場景、打關鍵影格、渲染 |
| 可以用的套件 | `.venv` 裡的套件（目前只有 PyYAML） | **只能用** `bpy`、`bpy_extras`、`mathutils` 和標準函式庫 |
| 讀 config.yaml | ✅ | ❌ |

兩邊只透過檔案交換資料，不會互相 import（`tests/test_static.py` 會自動檢查這件事）。

---

## 2. 專案目錄結構

```
AI-Animation/
├── README.md / CLAUDE.md
├── pyproject.toml                 # 專案中繼資料、pytest 設定
├── requirements.txt               # 執行依賴（PyYAML）
├── requirements-dev.txt           # 開發依賴（+ pytest）
├── animator.cmd                   # Windows 啟動器：用 .venv 執行 animator
├── .gitignore
│
├── config/
│   ├── config.example.yaml        # 設定範本（納入 Git）
│   └── config.yaml                # 本機設定（不納入 Git）
├── docs/                          # spec.md / config.md / stage1.md
│
├── src/animator/                  # ── 系統 Python ──
│   ├── cli.py, __main__.py        # doctor / validate / new / render
│   ├── paths.py                   # 專案根目錄、~ 與 ${VAR} 展開
│   ├── config.py                  # 預設值 + config.yaml + 環境變數 + CLI，記錄每個值的來源
│   ├── tools.py                   # 找 Blender / FFmpeg / ffprobe、檢查版本與 libx264
│   ├── jobs.py                    # output/<job_id>/、history/<render_id>/、輸出檔備份
│   ├── pipeline.py                # validate → resolve → Blender → FFmpeg → 驗證 → 紀錄
│   ├── errors.py
│   ├── spec/
│   │   ├── schema.py              # 常數、效果與通道、時間換影格規則
│   │   ├── validate.py            # 驗證，一次列出所有錯誤
│   │   ├── resolve.py             # 產生 spec.resolved.json
│   │   └── png.py                 # 讀 PNG 標頭（只用標準函式庫）
│   └── render/
│       ├── blender_runner.py      # subprocess 呼叫 Blender
│       └── ffmpeg.py              # 影格 → MP4、ffprobe、完整解碼檢查
│
├── blender/                       # ── Blender 內建 Python ──
│   ├── build_scene.py             # 入口
│   └── anim_bl/                   # 用套件包起來，避免和 Blender 內建模組撞名
│       ├── compat.py              # 處理不同 Blender 版本的差異（引擎、action API、透明設定）
│       ├── units.py               # 像素 ↔ Blender 單位、旋轉方向
│       ├── scene_setup.py         # 解析度、fps、色彩管理、背景、正交攝影機
│       ├── layers.py              # PNG → 平面 + 獨立材質（圖片 alpha × 物件 alpha）
│       ├── tracks.py              # 把各效果登記的區段寫成關鍵影格
│       ├── easing.py
│       └── effects/               # registry + move / scale / rotate / opacity
│
├── examples/basic/                # 範例 spec + 6 張測試圖層（由程式產生）
├── scripts/
│   ├── test_animation.py          # Stage 0 驗證腳本（保持不動）
│   └── make_sample_layers.py      # 產生範例 RGBA 圖層（只用標準函式庫）
├── tests/                         # pytest：64 個測試（其中 8 個需要 Blender）
│
├── assets/                        # paths.input_dir
├── models/                        # paths.models_dir（Stage 2 起使用，不納入 Git）
├── output/                        # paths.output_dir（不納入 Git）
│   └── <job_id>/
│       ├── spec.json              # ← 可以人工編輯，改完重新 render
│       ├── layers/*.png
│       ├── result.mp4 / preview.mp4 / still_<N>.png
│       ├── renders.json           # 所有渲染的索引，以及目前每個輸出檔屬於哪一次渲染
│       └── history/<render_id>/   # 每次渲染的完整紀錄（見第 6 節）
└── tmp/                           # paths.temp_dir（不納入 Git）
    └── <job_id>/<render_id>/frames/   # 成功後自動刪除（加上 --keep-frames 就會保留）
```

---

## 3. 可攜性原則

- **程式裡不寫任何電腦專屬路徑**：外部工具和各個目錄都從 `config/config.yaml` 讀取（`tests/test_static.py` 會自動掃描檢查）。
- **專案內的相對路徑以專案根目錄為基準**，不管從哪個目錄執行，結果都一樣。
- **spec 裡的圖層路徑以 spec 所在目錄為基準**，所以整個 `output/<job_id>/` 可以直接複製到另一台電腦重新渲染。
- **不依賴 Windows 使用者名稱**：設定裡可以用 `~` 和 `${ENV_VAR}`；工具在 PATH 上的話，只寫指令名稱就可以。
- **支援不同版本的 Blender**：最低 4.2 LTS，實測過的是 5.2.3 LTS；版本差異集中在 `blender/anim_bl/compat.py` 處理。
- 優先順序：命令列參數 > 環境變數 `ANIMATOR_*` > `config.yaml` > 內建預設值。

> 例外：`scripts/test_animation.py` 是 Stage 0 的驗證腳本，裡面寫死了路徑。依照要求保持不動，**不屬於正式程式碼**。

---

## 4. 在另一台 Windows 電腦部署

### 4.1 先安裝這些軟體

| 軟體 | 版本 | 取得方式 | 是否必須加進 PATH |
|---|---|---|---|
| Blender | ≥ 4.2 LTS（建議 5.2 LTS） | https://www.blender.org/download/ | 不必（寫在 config.yaml） |
| FFmpeg | 要有 libx264 的 GPL build | https://github.com/BtbN/FFmpeg-Builds/releases（`ffmpeg-master-latest-win64-gpl.zip`），解壓縮到任何位置 | 不必 |
| Python | ≥ 3.11（建議 3.12） | https://www.python.org/downloads/windows/ | 不必 |
| Git | 任何版本 | https://git-scm.com/ | — |

### 4.2 部署步驟（PowerShell）

```powershell
# 1. 取得專案
git clone <repo-url> AI-Animation
cd AI-Animation

# 2. 建立 .venv（換成該電腦上 Python 的實際路徑；如果有 py launcher，也可以用 py -3.12）
& "C:\Path\To\Python312\python.exe" -m venv .venv

# 3. 只在 .venv 裡安裝依賴（不會影響系統 Python 或 Blender Python）
.venv\Scripts\python.exe -m pip install -r requirements.txt
#    開發或測試用：.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 4. 建立本機設定，並填入這台電腦的工具路徑
copy config\config.example.yaml config\config.yaml
notepad config\config.yaml
#    tools.blender.executable: "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
#    tools.ffmpeg.executable:  "ffmpeg"（如果在 PATH 上）或 "~/tools/ffmpeg/bin/ffmpeg.exe"

# 5. 檢查環境：每一項都應該顯示 [OK]
.\animator.cmd doctor

# 6. 渲染範例
.\animator.cmd render examples\basic\spec.json
```

- `animator.cmd` 會自動使用專案的 `.venv`，**不需要**先 activate，也不需要 `pip install` 本專案。
- 不想修改 config.yaml 的話，可以暫時用環境變數指定，例如 `$env:ANIMATOR_BLENDER = "D:/Apps/Blender 4.2/blender.exe"`，或用參數 `.\animator.cmd --blender "D:/Apps/Blender 4.2/blender.exe" doctor`。
- 要確認新電腦上一切正常：`.venv\Scripts\python.exe -m pytest`（大約 1 分鐘；只跑不需要 Blender 的測試：`-m "not blender"`）。

### 4.3 常見的 Blender 路徑

| OS | 路徑 |
|---|---|
| Windows | `C:/Program Files/Blender Foundation/Blender <版本>/blender.exe` |
| macOS | `/Applications/Blender.app/Contents/MacOS/Blender` |
| Linux | `blender`，或解壓縮目錄下的 `blender` |

（macOS / Linux 沒有 `animator.cmd`，請改用 `PYTHONPATH=src .venv/bin/python -m animator ...`。）

---

## 5. 使用方式

```powershell
.\animator.cmd doctor [--show-config]                 # 檢查設定和外部工具；--show-config 會列出每個值的來源
.\animator.cmd validate <spec.json>                   # 只做驗證（一次列出所有錯誤，結束碼 2 = 驗證失敗）
.\animator.cmd new --layers <PNG資料夾> --name demo    # 用圖層資料夾建立新工作和起始 spec（沒有動畫）
.\animator.cmd render <spec.json>                     # 渲染 → result.mp4
.\animator.cmd render <spec.json> --preview           # 低解析度快速預覽 → preview.mp4
.\animator.cmd render <spec.json> --frame 60          # 只渲染第 60 格 → still_60.png
.\animator.cmd render <spec.json> --keep-frames       # 保留 tmp 中的 PNG 影格
```

- spec 在 `output/<job_id>/` 裡面 → **在原地重新渲染**。
- spec 在其他位置（例如 `examples/`）→ 建立新的 job，把 spec 和圖層複製進去再渲染，原本的檔案不會被修改。

**Stage 2：圖片自動分層**（需要 `requirements-ai.txt` 的套件，安裝方式寫在檔案開頭；詳見 `docs/stage2.md`「S2-1」）：

```powershell
.\animator.cmd models download --all                 # 下載模型（唯一需要網路的步驟，鎖定 revision 並驗證 SHA-256）
.\animator.cmd segment --image <圖片> --labels "dog, cat" --name demo
.venv\Scripts\python.exe scripts\s2_1_demo_animate.py output\<job_id>\spec.json   # 加上示範動畫（可省略）
.\animator.cmd render output\<job_id>\spec.json
```

輸出：`output/<job_id>/spec.json`（沒有動畫）、`layers/background.png`（物件已移除、背景已修補）、`layers/<物件>.png`（透明 PNG），以及 `segmentation/overview.png`（檢查用）、`segmentation/segmentation.json`（偵測結果、模型 revision、需要人工檢查的項目）。

**Stage 2：圖層動畫編輯**（S2-2，只用標準函式庫，不需要 AI 套件）：`<job>` 可以是 job 資料夾、job 裡的 spec.json，或 `output/` 底下的 job_id。

```powershell
.\animator.cmd layers list <job>                                                  # id、label、z、初始位置、是否為背景、動畫數
.\animator.cmd layers z <job> <layer_id> --z 30                                   # 直接指定 z（越大越前面）
.\animator.cmd layers z <job> <layer_id> --above <other_id>                       # 放在 other 的正前方
.\animator.cmd layers z <job> <layer_id> --below <other_id>                       # 放在 other 的正後方
.\animator.cmd layers pivot <job> <layer_id> --at 902,939                         # 把 pivot 移到這個畫布座標（第 1 格的姿勢）
.\animator.cmd anim add <job> <layer_id> move    --by -120,0  --start 0 --duration 2   # 相對位移（像素）
.\animator.cmd anim add <job> <layer_id> move    --to 1000,720 --start 0 --duration 2  # anchor 點的目標位置
.\animator.cmd anim add <job> <layer_id> scale   --to 1.5     --start 0 --duration 2   # 或 --to 1.2,0.8（> 0）
.\animator.cmd anim add <job> <layer_id> rotate  --by -45     --start 0 --duration 2   # 或 --to 90（度，順時針為正）
.\animator.cmd anim add <job> <layer_id> opacity --to 0       --start 3 --duration 1   # 0~1
.\animator.cmd render output\<job_id>\spec.json
```

- 每次都在 `spec.json` 的 `animations` 最後加一筆標準的 Stage 1 動畫（不寫 `from`，從這個通道目前的值開始；easing 是預設的 `ease_in_out`），用 Stage 1 的驗證檢查通過才寫入；原本的 spec 會先備份到 `history/edits/`。
- 結束碼：`1` = 程式檢查到的參數或格式錯誤（數值格式錯誤、scale / opacity 用了 `--by`、背景圖層）；`2` = argparse 的用法錯誤（沒有給 `--by` / `--to`、不支援的效果名稱、選項後面沒有值），或 Stage 1 驗證失敗（數值範圍、超過 duration、同一個通道時間重疊）。不同通道（例如 move 和 rotate）可以同時進行。
- scale 和 rotate 以圖層目前的 `anchor` 為 pivot。S2-1 的物件圖層 anchor 預設是裁切範圍的中心，不一定是物件的視覺中心（例如縮小後腳會離開地面），可以用 `layers pivot` 修改。
- `layers pivot --at X,Y`：X,Y 是第 1 格姿勢下的畫布像素座標（原點在左上角、y 往下），必須在圖層圖片的範圍內（剛好在邊上可以）。會把圖層的 `anchor`（pivot 在圖片上的位置，0~1）改成這一點，並把 `position`（pivot 的畫布座標）設成剛好 X,Y，用初始的 scale / rotation 換算，所以畫面不會跳動：
  - 第 1 格，以及 scale / rotation 和第 1 格相同的影格，圖片的位置和原本完全一樣。
  - scale / rotation 改變的影格，會以新的 pivot 為中心縮放、旋轉（這正是修改 pivot 的目的）。
  - 這個圖層所有 `move` 的絕對座標 `to` / `from`（包括 `enabled: false` 的）會一起平移同樣的距離，輸出會列出改寫了哪幾筆；`move.by` 是位移，不會改變。scale / rotate / opacity 的動畫和其他圖層都不變。
  - 如果有 scale / rotate 動畫在第 1 格就用明確的 `from` 改變了初始值，會拒絕（第 1 格的姿勢和換算用的姿勢不同）。pivot 已經在這一點時不會寫入，也不會建立備份。`layers list` 會顯示每個圖層的 `position(pivot px)` 和 `anchor`。
- 縮小、旋轉、移動或淡出物件時，會露出原本被物件擋住、由 AI 修補的背景，修補結果可能不完美（質感不一致、殘留的影子）。
- `layers z` 只修改目標圖層的 `z`（前後順序在整段動畫中固定），並把它的 `meta.z_rule` 設成 `manual`，不會重新編號其他圖層。`--above` / `--below` 會選一個介於參考圖層和相鄰圖層之間的 z（參考圖層已經在最前 / 最後面時是 ±10）；相鄰圖層的 z 相同、或兩者之間沒有數值可以用時會拒絕，請改用 `--z`。物件的 z 必須大於背景的 z（背景不透明，物件會被擋住），背景本身的 z 不能修改。
- 不支援：背景圖層的動畫、`fade_in` / `fade_out`、`--from`、`--easing`。

**人工修改的流程**：編輯 `output/<job_id>/spec.json`（調整位置、時間、easing；用 `enabled: false` 關掉某一筆動畫；用 `visible: false` 隱藏某個圖層）→ 再執行一次 `.\animator.cmd render output\<job_id>\spec.json`。

---

## 6. 重新渲染與追溯

每次渲染都會建立 `output/<job_id>/history/<render_id>/`：

| 檔案 | 內容 |
|---|---|
| `spec.json` | 這次渲染用的 spec 快照 |
| `spec.resolved.json` | 傳給 Blender 的完整解析結果 |
| `config.effective.yaml` | 實際採用的設定值，以及每個值的來源（cli / env / config / default） |
| `render_report.json` | 指令、時間、狀態、Blender/FFmpeg 路徑與版本、spec 和每張圖層的 SHA-256、ffprobe 驗證結果 |
| `blender.log` / `ffmpeg.log` / `blender_report.json` | 外部工具的輸出 |
| `result.mp4`（被取代之後） | 下一次渲染產生同名檔案時，舊檔會移到這裡 |

`renders.json` 記錄所有渲染的索引，以及目前根目錄裡每個輸出檔屬於哪一次渲染。設定 `jobs.overwrite: true` 時，舊的 MP4 會直接刪除，但其他紀錄仍然保留。

---

## 7. 動畫系統

- 每個圖層是一張**獨立的平面**，而且有**自己的材質**；最終 alpha = PNG alpha × 圖層 opacity。
- 每個效果是 `blender/anim_bl/effects/` 裡的一個模組，用 `@register("<name>", channel=...)` 註冊。
- 每個效果只控制一個通道（position / scale / rotation / opacity）。同一個通道的動畫時間不能重疊；不同通道可以同時進行。

| 效果 | 狀態 |
|---|---|
| `move`（`to` / `by`）、`scale`、`rotate`（`to` / `by`）、`opacity` | ✅ Stage 1 |
| `fade_in` / `fade_out`（`opacity` 的別名） | ✅ Stage 1 |
| easing：`linear`、`constant`、`ease_*`，以及 `sine`、`quad`、`cubic`、`quart`、`quint`、`expo`、`circ`、`back`、`bounce`、`elastic` 的 `_in` / `_out` / `_in_out` | ✅ Stage 1 |
| `glow` / `shadow` / `tint` / `light_sweep` / `particles` / `camera_*` / `shake` / `float` / `wiggle` | 名稱已保留（Stage 5） |

---

## 8. 開發階段

| 階段 | 內容 | 依賴 |
|---|---|---|
| **0** ✅ | 確認環境可用：Blender 背景渲染 + FFmpeg | — |
| **1** ✅ | 人工 PNG 圖層 → spec → Blender → MP4，並保留渲染紀錄 | PyYAML（另外開發用 pytest） |
| **2** 🔧 | 圖片自動分層：Grounding DINO → SAM 2.1 → PyMatting → MI-GAN 背景修補 → spec.json（S2-1 等待審查） | `requirements-ai.txt`（CPU 版 torch、transformers、pymatting、onnxruntime…） |
| 3 | 分層品質：影子、遮擋排序、`layers rebuild` 等 | 看需求而定 |
| 4 | 自然語言 → spec（規則式 → LLM） | 看選用的方案而定 |
| 5 | 光影、陰影、粒子、鏡頭、效果預設組合、UI | 看需求而定 |

---

## 9. 實測環境（2026-11-03）

| 項目 | 值 |
|---|---|
| OS | Windows 11 Pro 10.0.26300 |
| Blender | 5.2.3 LTS（EEVEE），內建 Python 3.13.13 |
| 系統 Python | 3.12.1 → `.venv`：PyYAML 6.0.3、pytest 9.2.0 |
| FFmpeg | N-117917（2024-11，GPL，有 libx264） |
| GPU | NVIDIA GeForce MX450，2 GB |
| 範例渲染時間 | 1280×720、120 格：約 40 秒（含 Blender 啟動和 shader 編譯）；單格 `--frame` 約 45 秒 |
