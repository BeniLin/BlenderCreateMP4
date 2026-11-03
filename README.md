# AI Image-to-Animation

輸入一張靜態圖片和一段自然語言描述，自動產生 MP4 動畫。

```
image.png + 「小鳥從左邊飛進來，太陽慢慢放大，標題最後淡入」  →  result.mp4
```

工具會先把圖片拆成多個帶透明背景的**獨立圖層**，每個圖層各自加上移動、縮放、旋轉、透明度等動畫，再由 Blender 渲染、FFmpeg 合成影片。**不會**把整張圖當成一個物件平移。

| 文件 | 內容 |
|---|---|
| [docs/spec.md](docs/spec.md) | `spec.json` 完整格式規格 |
| [docs/config.md](docs/config.md) | `config.yaml` 完整設定規格 |
| [docs/stage1.md](docs/stage1.md) | Stage 1 開發項目與驗收標準 |
| [CLAUDE.md](CLAUDE.md) | 開發規則 |

> **目前狀態**：Stage 0（確認環境可用）已完成；Stage 1 的規格已確定，**還沒開始實作**。下面標示「Stage 1 完成後」的指令目前還不能用。

---

## 1. 架構

```
┌────────────────────── 系統 Python（.venv）──────────────────────┐   ┌──── Blender 內建 Python ────┐
│                                                                 │   │                             │
│  圖片 + 描述 ─▶ 圖層分離 ─▶ 動畫規劃 ─▶ spec.json ─▶ 驗證/解析   │──▶│ spec.resolved.json          │
│               (Stage 2-3)  (Stage 4)    ▲ 可人工編輯   │        │   │  → 建立場景、每圖層一平面   │
│                                         │              ▼        │   │  → effects 打關鍵影格       │
│  Stage 1：人工 PNG 圖層 ────────────────┘   spec.resolved.json  │   │  → 渲染 PNG 影格            │
│                                                                 │   └──────────────┬──────────────┘
│  FFmpeg 合成 MP4 ◀──────────────────────────── frames/*.png ────┼──────────────────┘
└─────────────────────────────────────────────────────────────────┘
```

**兩個 Python 完全分開**

| | 系統 Python（`src/animator/`） | Blender 內建 Python（`blender/`） |
|---|---|---|
| 職責 | 讀設定、驗證 spec、管理工作目錄、AI 模組、呼叫 Blender 和 FFmpeg | 讀取 `spec.resolved.json`，建立場景、打關鍵影格、渲染 |
| 可以用的套件 | `.venv` 裡安裝的套件（PyYAML、日後的 AI 套件） | **只能用** `bpy`、`bpy_extras`、`mathutils` 和標準函式庫 |
| 讀 config.yaml | ✅ | ❌（需要的值都由 resolved spec 和命令列參數傳過來） |

兩邊只透過**檔案**交換資料（`spec.resolved.json`、PNG 圖層、PNG 影格、`render_report.json`），不會互相 import。

---

## 2. 專案目錄結構

```
AI-Animation/
├── README.md
├── CLAUDE.md
├── pyproject.toml                  # [S1] 套件定義；入口指令 animator
├── .gitignore
│
├── config/
│   ├── config.example.yaml         # 設定範本（納入 Git）
│   └── config.yaml                 # 本機設定（不納入 Git）
│
├── docs/
│   ├── spec.md                     # spec.json 格式規格
│   ├── config.md                   # config.yaml 設定規格
│   └── stage1.md                   # Stage 1 開發項目
│
├── src/animator/                   # ── 系統 Python ──
│   ├── __main__.py / cli.py        # [S1] doctor / validate / new / render
│   ├── paths.py                    # [S1] 判定專案根目錄、路徑展開
│   ├── config.py                   # [S1] 讀設定、合併預設值、套用環境變數和 CLI 覆寫
│   ├── tools.py                    # [S1] 找出 Blender/FFmpeg、檢查版本
│   ├── jobs.py                     # [S1] output/<job_id>/、tmp/<job_id>/
│   ├── pipeline.py                 # [S1] validate → resolve → blender → ffmpeg
│   ├── spec/
│   │   ├── schema.py               # [S1] dataclass
│   │   ├── validate.py             # [S1] 驗證，一次列出所有錯誤
│   │   └── resolve.py              # [S1] 產生 spec.resolved.json
│   ├── render/
│   │   ├── blender_runner.py       # [S1] subprocess 呼叫 Blender
│   │   └── ffmpeg.py               # [S1] 影格 → MP4，ffprobe 驗證
│   ├── segmentation/               # [S2-3] base.py / manual.py / rembg / sam2
│   ├── inpaint/                    # [S2]   base.py / opencv / lama
│   └── planner/                    # [S4]   base.py / rule_based / llm
│
├── blender/                        # ── Blender 內建 Python（只用 bpy + stdlib）──
│   ├── build_scene.py              # [S1] 入口
│   ├── compat.py                   # [S1] 處理不同 Blender 版本的 API 差異
│   ├── units.py                    # [S1] 像素↔Blender 單位、秒↔影格
│   ├── scene_setup.py              # [S1] 解析度、fps、色彩管理、正交攝影機
│   ├── layers.py                   # [S1] PNG → 平面 + 各自獨立的材質
│   ├── easing.py                   # [S1]
│   └── effects/
│       ├── __init__.py             # [S1] registry：@register("move")
│       ├── move.py  scale.py  rotate.py  opacity.py   # [S1]（opacity.py 也包含 fade_in/fade_out）
│       └── （未來）glow.py shadow.py particles.py camera_*.py
│
├── examples/basic/                 # [S1] spec.json + layers/*.png
├── scripts/
│   ├── test_animation.py           # ✅ 原始驗證腳本（保持不動）
│   └── make_sample_layers.py       # [S1] 產生範例 RGBA 圖層（只用標準函式庫）
├── tests/                          # [S1] pytest
│
├── assets/                         # 輸入素材          （paths.input_dir）
├── models/                         # AI 模型權重，不納入 Git（paths.models_dir）
├── output/                         # 不納入 Git        （paths.output_dir）
│   └── <job_id>/
│       ├── spec.json               # ← 可以人工編輯，改完重新 render
│       ├── layers/*.png
│       ├── result.mp4 / preview.mp4
│       ├── render_report.json
│       └── logs/blender.log, ffmpeg.log
└── tmp/                            # 不納入 Git        （paths.temp_dir）
    └── <job_id>/
        ├── spec.resolved.json
        └── frames/frame_0001.png …
```

`[S1]` 表示 Stage 1 要建立；`[S2-4]` 表示之後的階段才會建立。

---

## 3. 可攜性原則

- **程式裡不寫任何電腦專屬路徑。** 外部工具路徑和各個目錄都從 `config/config.yaml` 讀取（規格見 [docs/config.md](docs/config.md)）。
- **專案內的相對路徑以專案根目錄為基準**，所以不管從哪個目錄執行，結果都一樣。
- **spec 裡的圖層路徑以 spec 所在目錄為基準**，所以整個 `output/<job_id>/` 可以直接複製到另一台電腦重新渲染。
- **不依賴 Windows 使用者名稱**：設定裡可以用 `~` 和 `${ENV_VAR}`；工具在 PATH 上的話，只寫指令名稱就可以。
- **支援不同版本的 Blender**：最低 4.2 LTS，實測過的是 5.2.3 LTS。版本之間的 API 差異集中在 `blender/compat.py` 處理；`animator doctor` 會檢查版本。
- 優先順序：命令列參數 > 環境變數 `ANIMATOR_*` > `config.yaml` > 內建預設值。

> 例外：`scripts/test_animation.py` 是 Stage 0 的驗證腳本，裡面寫死了路徑。依照要求保持不動，**不屬於正式程式碼**。

---

## 4. 在新電腦部署

### 4.1 需要先安裝的軟體

| 軟體 | 版本 | 取得方式 |
|---|---|---|
| Blender | ≥ 4.2 LTS（建議 5.2 LTS） | https://www.blender.org/download/ |
| FFmpeg | 需要支援 libx264（GPL build） | Windows：https://github.com/BtbN/FFmpeg-Builds/releases ／ macOS：`brew install ffmpeg` ／ Linux：發行版套件 |
| Python | ≥ 3.11（建議 3.12） | https://www.python.org/ |
| Git | 任何版本 | https://git-scm.com/ |

Blender 和 FFmpeg **不一定要加進 PATH**，在 config.yaml 裡寫完整路徑即可。

### 4.2 部署步驟（Stage 1 完成後）

```powershell
# 1. 取得專案
git clone <repo-url> AI-Animation
cd AI-Animation

# 2. 建立虛擬環境（指定要用的 Python；不同電腦上的路徑可能不同）
py -3.12 -m venv .venv               # 或：python3 -m venv .venv
.venv\Scripts\activate               # macOS/Linux：source .venv/bin/activate
pip install -e .                     # 開發時加測試套件：pip install -e ".[dev]"

# 3. 建立本機設定
copy config\config.example.yaml config\config.yaml     # macOS/Linux：cp
#    編輯 config.yaml，至少要填 tools.blender.executable；
#    如果 ffmpeg 不在 PATH 上，也要填 tools.ffmpeg.executable

# 4. 檢查環境
animator doctor

# 5. 試著渲染範例
animator render examples\basic\spec.json --preview
```

不想修改 config.yaml 時，也可以暫時用環境變數指定：

```powershell
$env:ANIMATOR_BLENDER = "D:/Apps/Blender 4.2/blender.exe"
animator doctor
```

### 4.3 常見的 Blender 路徑

| OS | 路徑 |
|---|---|
| Windows | `C:/Program Files/Blender Foundation/Blender <版本>/blender.exe` |
| macOS | `/Applications/Blender.app/Contents/MacOS/Blender` |
| Linux | `blender`（套件管理器安裝）或解壓縮目錄下的 `blender` |

---

## 5. 使用方式（Stage 1 完成後）

```powershell
animator doctor                                   # 檢查設定和外部工具
animator new --layers assets\my_scene --name demo # 用一個圖層資料夾產生起始 spec → output/<job_id>/spec.json
animator validate output\<job_id>\spec.json       # 只做驗證
animator render   output\<job_id>\spec.json       # 渲染（在原地重新渲染，舊的 MP4 會先備份）
animator render   output\<job_id>\spec.json --preview    # 25% 解析度快速預覽 → preview.mp4
animator render   output\<job_id>\spec.json --frame 60   # 只渲染第 60 格 → still_60.png
```

人工修改的流程：編輯 `output/<job_id>/spec.json`（調整位置、時間、easing，或用 `enabled: false` 關掉某一筆動畫）→ 再執行一次 `animator render`。

---

## 6. 動畫系統

- 每個圖層都是一張**獨立的平面**，而且有**自己的材質**（不共用），所以每個圖層的變形和透明度都可以單獨控制。
- 每個效果是 `blender/effects/` 裡的一個模組，用 `@register("<name>")` 註冊；新增效果不需要改動其他程式碼。
- 每個效果只控制一個屬性通道（position / scale / rotation / opacity）。同一個通道的動畫時間不能重疊；不同通道可以同時進行。

| 效果 | Stage | 說明 |
|---|---|---|
| `move` / `scale` / `rotate` / `opacity` | 1 | 基本變形和透明度 |
| `fade_in` / `fade_out` | 1 | 這是 `opacity` 的別名 |
| `glow` / `shadow` / `tint` / `light_sweep` | 5 | 光影（名稱已保留） |
| `particles` | 5 | 粒子（名稱已保留） |
| `camera_move` / `camera_zoom` / `shake` | 5 | 鏡頭（`@camera` 這個 target 已保留） |

---

## 7. 開發階段

| 階段 | 內容 | 新的依賴 |
|---|---|---|
| **0** ✅ | 確認環境可用：Blender 背景渲染 + FFmpeg | — |
| **1** | 人工 PNG 圖層 → spec → Blender → MP4（見 [docs/stage1.md](docs/stage1.md)） | PyYAML；開發時另需 pytest |
| **2** | 自動去背 + 背景補洞 | rembg、opencv-python |
| **3** | 多元素分割（文字定位 + SAM 2） | torch、sam2 等 |
| **4** | 自然語言 → spec（規則式 → LLM） | 看選用的方案而定 |
| **5** | 光影、陰影、粒子、鏡頭、效果預設組合、UI | 看需求而定 |

### 預計使用的開源工具（目前都**還沒安裝**）

| 用途 | 工具 | 授權 |
|---|---|---|
| 去背 | rembg（u2net / isnet / BiRefNet） | MIT |
| 多元素分割 | SAM 2（tiny / small，以配合 2 GB VRAM） | Apache-2.0 |
| 用文字找出元素 | Florence-2 / GroundingDINO | MIT / Apache-2.0 |
| 背景補洞 | OpenCV inpaint → LaMa | Apache-2.0 |
| 渲染 / 合成 | Blender / FFmpeg | GPL |

---

## 8. 開發機環境紀錄（2026-11-03）

| 項目 | 值 |
|---|---|
| OS | Windows 11 Pro 10.0.26300 |
| Blender | 5.2.3 LTS（不在 PATH），內建 Python 3.13.13 |
| 系統 Python | 3.12.1（PATH 上還有 3.11）；**沒有安裝 PyYAML** |
| FFmpeg | N-117917（2024-11，GPL，有 libx264），已在 PATH |
| GPU | NVIDIA GeForce MX450，2 GB VRAM |
| Git | 已安裝；專案目錄還不是 repo |

原始驗證腳本（Stage 0）：

```powershell
& "<Blender 路徑>\blender.exe" -b -P scripts\test_animation.py
```
