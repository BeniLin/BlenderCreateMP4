# AI Image-to-Animation

把一張靜態圖片拆成多個帶透明背景的**獨立圖層**（AI 自動分割 + 背景修補），再為每個圖層加上移動、縮放、旋轉、透明度、顯示 / 隱藏等動畫，用 Blender 渲染、FFmpeg 合成 MP4。**不會**把整張圖當成一個物件平移。

```
photo.jpg ─ segment ─▶ 透明 PNG 圖層 + 修補好的背景 + spec.json ─ anim add / layers … ─▶ render ─▶ result.mp4
```

> **目前狀態**：Stage 1（人工 PNG 圖層 → spec.json → MP4）、Stage 2 的自動分層（`segment`）、S2-2 的動畫編輯（`layers` / `anim`）都已完成。**自然語言描述 → 動畫（Stage 4）還沒有實作**：動畫要用指令或直接編輯 `spec.json` 加上。

> **第一次使用？請看 [使用手冊（docs/user-guide.md）](docs/user-guide.md)**：從安裝、第一個作品到疑難排解，一步一步說明（不需要懂程式）。

| 文件 | 內容 |
|---|---|
| [docs/user-guide.md](docs/user-guide.md) | **使用手冊**：給一般使用者的完整操作說明 |
| 這份 README | 專案概要、安裝摘要、快速教學、指令參考、常見問題 |
| [docs/spec.md](docs/spec.md) | `spec.json` 格式規格（座標、時間、anchor、排序、透明度、visibility） |
| [docs/config.md](docs/config.md) | `config.yaml` 設定規格 |
| [docs/stage1.md](docs/stage1.md) / [docs/stage2.md](docs/stage2.md) | 各階段的設計、實測與已知限制 |
| [examples/](examples/) | `basic`（Stage 1 的效果）、`visibility`（顯示 / 隱藏）；兩個都不需要 AI |
| [CLAUDE.md](CLAUDE.md) | 開發規則 |

---

## 1. 需要的環境

只有「實測」欄的組合真的測試過；其他版本只是程式允許，**沒有驗證過**。

| 項目 | 要求 | 實測 | 說明 |
|---|---|---|---|
| Windows | 64 位元 Windows | Windows 11 Pro 10.0.26300 | **只在 Windows 11 上測試過**（Windows 10 沒有驗證）；`animator.cmd` 只支援 Windows |
| Python | 3.12（建立專案的 `.venv`） | 3.12.1 | AI 套件的鎖定版本（`requirements-ai.lock.txt`）是用 3.12 安裝的；Stage 1 只需要 PyYAML |
| Blender | ≥ 4.2 LTS（`tools.blender.min_version`） | 5.2.3 LTS（EEVEE） | 4.x 的相容分支寫在 `blender/anim_bl/compat.py`，**沒有實測**；不需要加進 PATH |
| FFmpeg | 有 libx264 的 build（例如 [BtbN 的 win64 GPL build](https://github.com/BtbN/FFmpeg-Builds/releases)） | N-117917（2024-11，GPL） | 加進 PATH，或把完整路徑寫在 config；ffprobe 預設在 ffmpeg 同一個資料夾找 |
| 硬碟 | `.venv` 約 1.4 GB（含 AI 套件；只用 Stage 1 時很小）＋ 模型 874 MB（`models/`） | | 一個 1920×1440、4 秒的 job 約 23 MB；渲染中的 PNG 影格放在 `tmp/`，成功後自動刪除 |
| CPU / GPU | 只用 CPU（`ai.device: cpu`，其他值會被拒絕） | i7-1165G7 | GPU 沒有使用；不需要 CUDA |
| 網路 | 只有安裝套件和 `models download` 需要 | | 分割、渲染一律離線 |

**路徑長度**：Windows 預設的路徑上限是 260 個字元。把專案放在短的路徑（例如 `C:\AI-Animation`），不要放在很深的資料夾裡；太深時 AI 套件的快取（numba）可能寫入失敗（見第 5 節）。

---

## 2. 第一次安裝（PowerShell）

```powershell
# 1. 取得專案
git clone <repo-url> C:\AI-Animation
cd C:\AI-Animation

# 2. 建立 .venv（換成這台電腦上 Python 3.12 的路徑；有 py launcher 也可以用 py -3.12）
& "C:\Path\To\Python312\python.exe" -m venv .venv

# 3. Stage 1（渲染）的套件：只裝在 .venv（不會影響系統 Python，Blender 不需要任何套件）
.venv\Scripts\python.exe -m pip install -r requirements.txt
#    要跑測試的話改用：.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 4. Stage 2（segment）的 AI 套件（CPU 版；指令也寫在 requirements-ai.txt 的開頭）
.venv\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0+cpu torchvision==0.29.2+cpu
.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-ai.txt
#    要完全重現實測環境（所有間接依賴都鎖定版本）：改用 requirements-ai.lock.txt（安裝指令寫在該檔開頭）

# 5. 本機設定（config\config.yaml 不納入 Git）
copy config\config.example.yaml config\config.yaml
notepad config\config.yaml
#    tools.blender.executable: "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
#    tools.ffmpeg.executable:  "ffmpeg"（在 PATH 上時）或 "~/tools/ffmpeg/bin/ffmpeg.exe"
#    （路徑用正斜線；可以用 ~ 和 ${環境變數}；相對路徑以專案根目錄為基準）

# 6. 下載 AI 模型（唯一需要網路的步驟：鎖定 revision，下載後驗證 SHA-256）
.\animator.cmd models download --all
.\animator.cmd models verify          # 計算每個檔案的 SHA-256；全部 [OK] 才可以分割

# 7. 檢查環境：每一項都應該是 [OK]（只用 Stage 1 時，AI 套件 / 模型的 [WARN] 可以忽略）
.\animator.cmd doctor
```

- `animator.cmd` 會直接使用專案的 `.venv`，**不需要** activate，也不需要 `pip install` 這個專案。
- 不想改 config 時可以暫時用環境變數或參數：`$env:ANIMATOR_BLENDER = "D:/Apps/Blender 5.2/blender.exe"`，或 `.\animator.cmd --blender "D:/Apps/Blender 5.2/blender.exe" doctor`。優先順序：命令列參數 > 環境變數 `ANIMATOR_*` > `config.yaml` > 內建預設值（`doctor --show-config` 會列出每個值的來源）。
- 只想先確認渲染：`.\animator.cmd render examples\basic\spec.json`（不需要 AI 套件和模型；會建立新的 job，不會修改 `examples/`）。
- 想確認整個安裝：`.venv\Scripts\python.exe -m pytest`（需要 `requirements-dev.txt`；全部大約 15 分鐘，`-m "not blender"` 只跑不需要 Blender 的測試）。

---

## 3. 第一支動畫：從照片到 MP4

用專案附的 CC0 照片 `assets/s2-0/occlusion_dogcat.jpg`（一隻狗和一隻貓，1920×1440；來源見 `assets/s2-0/SOURCES.md`）。下面的指令都實際執行過，輸出節錄自 2026-11-06 的實際執行（當時用的是 `--name e2e`）；job_id 的日期時間和名稱每次都不同，請換成你自己的。

**1. 準備圖片**：任何 JPG / PNG 都可以；物件之間的背景越單純，修補的效果越好。這個範例直接用附的照片。

**2. 分割**（CPU 約 1 分鐘）：

```powershell
.\animator.cmd segment --image assets\s2-0\occlusion_dogcat.jpg --labels "dog, cat" --name tutorial --duration 4
```
```
已建立工作：...\output\20261106-173757_tutorial
圖層：2 個物件 + 背景
  z=10   dog-1                dog
  z=20   cat-1                cat
  背景區域 0：7.2%  migan  passed_checks  已修補
```

**3. 檢查分割結果**：打開 `output\<job_id>\segmentation\overview.png`（物件的範圍和補洞區域）和 `layers\background.png`（物件已移除、背景已修補）。`passed_checks` 只代表通過自動檢查，畫面還是要自己看過；需要人工檢查的項目會列在 `segment` 的輸出和 `segmentation\segmentation.json` 的 `review` 裡。

**4. 列出圖層**：

```powershell
.\animator.cmd layers list 20261106-173757_tutorial
```
```
id                     label                 z  position(pivot px)   anchor             background animations
background             -                     0  [960, 720]           [0.5, 0.5]         yes                 0
dog-1                  dog                  10  [902, 576]           [0.5, 0.5]         no                  0
cat-1                  cat                  20  [1095, 740]          [0.5, 0.5]         no                  0
```

**5. 檢查提醒**：

```powershell
.\animator.cmd anim check 20261106-173757_tutorial
```
```
[NOTICE] dog-1（後）和 cat-1（前）在第 1 格的範圍重疊，前後順序是自動決定的（面積小的在前面）；分割時兩個遮罩重疊 18 px，不一定正確；需要時用 animator layers z 調整
```

**6. 加上動畫**（每個指令都會先備份 spec，並列出和這個圖層有關的提醒）：

```powershell
.\animator.cmd layers pivot 20261106-173757_tutorial dog-1 --at 902,939                      # pivot 移到狗的腳（縮小時腳不會離開地面）
.\animator.cmd anim add 20261106-173757_tutorial dog-1 move  --by 300,0 --start 0.5 --duration 1.5
.\animator.cmd anim add 20261106-173757_tutorial dog-1 scale --to 0.8   --start 0.5 --duration 1.5
.\animator.cmd anim add 20261106-173757_tutorial cat-1 visibility --to hidden --start 2.5 --duration 1
.\animator.cmd anim check 20261106-173757_tutorial
```
```
[WARNING] dog-1 可能露出修補過的背景 區域 0（已修補，只通過自動檢查（沒有人工確認））：move（0.5 秒 / 第 16 格起）、scale < 1（縮小）（0.5 秒 / 第 16 格起）
[WARNING] cat-1 可能露出修補過的背景 區域 0（已修補，只通過自動檢查（沒有人工確認））：visibility 隱藏（2.5 秒 / 第 76 格起）
[NOTICE] dog-1（後）和 cat-1（前）...
共 2 個 warning、1 個 notice（只是提醒，不影響編輯或渲染）
```
狗移開、貓隱藏時會露出 AI 修補過的背景，所以會有提醒；這是預期的，渲染完看一下那個區域是否可以接受。

**7. 渲染**（1920×1440、120 格約 2 分鐘；先用 `--preview` 看低解析度版本會快很多）：

```powershell
.\animator.cmd render output\20261106-173757_tutorial\spec.json --preview
.\animator.cmd render output\20261106-173757_tutorial\spec.json
```
```
完成：...\output\20261106-173757_tutorial\result.mp4
影片：h264 1920x1440 30fps 120 格 4.00s，可解碼：是
```

**8. 成品**：`output\<job_id>\result.mp4`（預覽是 `preview.mp4`）。每次渲染的完整紀錄在 `output\<job_id>\history\<render_id>\`；原本的照片不會被修改。

只想試渲染、不想裝 AI：把第 2 步換成 `.\animator.cmd new --layers <PNG 圖層資料夾> --name demo`（或直接渲染 `examples\basic\spec.json`），其他步驟相同（`anim check` 只會檢查前後順序）。

---

## 4. 指令參考

`<job>` 可以是 job 資料夾、job 裡的 `spec.json`，或 `output\` 底下的 job_id。

### 慣例
- **座標**：畫布像素，原點在左上角，x 往右、y 往下；`position` 是圖層 anchor（= pivot）點在畫布上的位置；`anchor` 是 pivot 在圖層圖片上的位置（0~1）。
- **時間**：秒；影格從 1 開始，`frame(t) = 1 + round(t × fps)`（限制在 1 ~ 最後一格）。
- **角度**：度，順時針為正。**前後順序**：`z` 越大越前面，`z` 相同時 `layers` 陣列裡越後面的越前面。
- **全域選項**（放在指令名稱前面）：`--config <檔案>`（預設 `config/config.yaml`，或 `ANIMATOR_CONFIG`）、`--blender <路徑>`、`--ffmpeg <路徑>`、`-v`（DEBUG 訊息）、`--version`。

### 結束碼

| 結束碼 | 意思 |
|---|---|
| 0 | 成功（`anim check`、編輯後列出的提醒不會改變結束碼） |
| 1 | 程式檢查到的錯誤：找不到 job / 圖層 / 工具、數值格式錯誤、不允許的操作（例如背景圖層）、渲染或 Blender / FFmpeg 失敗、`models verify` 有模型沒通過、`doctor` 有 `[FAIL]` 的項目（`[WARN]` 不算） |
| 2 | spec 沒有通過 Stage 1 的驗證（數值範圍、超過 duration、同一個通道時間重疊…），或 argparse 的用法錯誤（缺少必填選項、不支援的選項值） |
| 3 | `segment`：沒有產生任何物件圖層 |
| 4 | `segment`：背景沒有完整修補（有區域保留了原本的物件）；job 仍然建立，也可以渲染 |
| 130 | 被 Ctrl+C 中斷 |

### 環境與模型

| 指令 | 說明 |
|---|---|
| `doctor [--show-config]` | 檢查 config、Blender、FFmpeg / libx264、ffprobe、AI 套件、模型（快速檢查：檔案存在 + 大小）、各個目錄；`--show-config` 列出每個設定值和來源 |
| `models list` | 列出模型：角色、大小、授權、鎖定的 revision、快速檢查結果（結束碼一律 0） |
| `models download [--all \| <name>…]` | 從鎖定的 revision 下載登記的檔案，下載後驗證 SHA-256，不符就刪除 |
| `models verify [<name>…]` | 計算每個檔案的 SHA-256；有不符的結束碼 1 |

模型：`grounding-dino-tiny`（偵測，690 MB）、`sam2.1-hiera-tiny`（分割，156 MB）、`migan`（背景修補，28 MB），都是商用可用的授權（Apache-2.0 / MIT），放在 `paths.models_dir`（預設 `models/`）。

### 建立 job

| 指令 | 說明 |
|---|---|
| `segment --image <圖片> [--labels "dog, cat"] [--masks <資料夾>] [--name N] [--inpaint migan\|opencv\|none] [--no-refine] [--duration 5] [--fps N] [--keep-temp]` | AI 分割 → `output/<job_id>/`：`spec.json`（沒有動畫）、`layers/*.png`、`segmentation/`（`overview.png`、`segmentation.json`、遮罩）、`logs/`。`--labels` 是英文（預設 `ai.detection.default_labels`）；`--masks` 用自己的遮罩（檔名 = 標籤，白色 = 物件），不跑偵測和分割；`--duration` 預設 5 秒，`--fps` 預設 `defaults.canvas.fps`（30）。先檢查所有設定和輸入，失敗時不會在 `output/` 留下半成品 |
| `new --layers <PNG 資料夾> [--name N] [--duration 5] [--fps N]` | 用現成的 PNG 圖層建立 job（檔名排序 = 前後順序），不需要 AI |
| `validate <spec.json>` | 只做驗證，一次列出所有錯誤 |

### 編輯（只用標準函式庫；不需要 AI 套件）

每次編輯都會：讀取 → 修改副本 → 用 Stage 1 的驗證檢查整份 spec → 確認檔案沒有被其他程式改過 → 把原本的 spec **逐位元組備份**到 `history/edits/<時間>-spec.json` → 原子性地寫入。任何一步失敗，spec 都不會被修改，也不會留下備份或暫存檔。背景圖層不能編輯。

| 指令 | 說明 |
|---|---|
| `layers list <job>` | id、label、z、`position(pivot px)`、`anchor`、是否為背景、動畫數 |
| `layers z <job> <layer> (--z N \| --above <id> \| --below <id>)` | 修改前後順序（整段動畫固定）。只改這個圖層的 `z`，並把 `meta.z_rule` 設成 `manual`；`--above` / `--below` 取相鄰圖層之間的值，相鄰圖層 z 相同時拒絕（請改用 `--z`）；物件的 z 必須大於背景 |
| `layers pivot <job> <layer> --at X,Y` | 把 pivot（縮放 / 旋轉的中心）移到畫布座標 X,Y（第 1 格的姿勢），必須在圖層圖片的範圍內。畫面不會跳動：第 1 格、以及 scale / rotation 和第 1 格相同的影格都和原本一樣；這個圖層 `move` 的 `to` / `from` 會一起平移，`move.by` 不變；pivot 已經在這裡時不寫入 |
| `anim add <job> <layer> <effect> (--by V \| --to V) --start S --duration D` | 在 `animations` 最後加一筆標準的 Stage 1 動畫（不寫 `from`，從這個通道目前的值開始；easing 是預設的 `ease_in_out`）。動畫 id 是 `<layer>-<effect>-<n>` |
| `anim check <job>` | 只讀取：列出可能露出修補過的背景、或前後順序可能不確定的地方（說明見下面） |

`anim add` 的效果：

| effect | `--by` | `--to` | 說明 |
|---|---|---|---|
| `move` | `DX,DY`（像素） | `X,Y`（pivot 的畫布座標） | 負數可以直接寫，例如 `--by -120,0` |
| `scale` | — | `S` 或 `SX,SY`（> 0） | 以 pivot 為中心 |
| `rotate` | 度 | 度 | 順時針為正，以 pivot 為中心 |
| `opacity` | — | 0~1 | 淡入淡出 |
| `visibility` | — | `visible` / `hidden` | 離散的顯示 / 隱藏，只在 `[start, start + duration)` 有效，之後恢復圖層的 `visible`；區間到影片結尾時最後一格也維持。和 opacity 互相獨立 |

- 同一個圖層的同一個通道（move / scale / rotate / opacity / visibility 各自一個通道）時間不可以重疊（結束碼 2），頭尾相接可以；不同通道可以同時進行。
- 和圖層原本狀態相同的 visibility 區間沒有效果，會被拒絕。`visible: false` 的圖層加上 `--to visible` 之後，平常隱藏，只在區間裡顯示。
- 沒有提供：`fade_in` / `fade_out`、`--from`、`--easing`（需要的話直接編輯 `spec.json`，規格見 `docs/spec.md`）。

`anim check` 的提醒（只是資訊，不影響編輯或渲染；`anim add`、`layers z`、`layers pivot` 成功之後也會列出和那個圖層有關的提醒）：

| 標籤 | 情況 |
|---|---|
| `[WARNING]` | move、rotate、scale < 1、visibility 隱藏、`visible: false`、初始姿勢和分割時不同 → 可能露出原本被物件蓋住、由 AI 修補的背景；列出區域編號和修補狀態（已修補 / 需要人工檢查 / 沒有修補：原本的物件仍在背景上） |
| `[NOTICE]` | opacity < 1、scale > 1；兩個自動排序的圖層在第 1 格或動畫中可能重疊（`layers z` 之後不再提醒；手動不代表一定正確） |
| `[NOTICE] [關聯不明]` | 舊的 job（2026-11-06 以前分割的）沒有「物件 ↔ 補洞區域」的關聯：每個 job 最多一則，不會猜測 |

範圍用保守的矩形估計（旋轉時用外接圓），所以可能出現實際上沒有碰到的提醒。不是 `segment` 產生的 job 不檢查修補區域。

### 渲染

| 指令 | 輸出 |
|---|---|
| `render <spec.json>` | `result.mp4`（h264、yuv420p；完成後用 ffprobe 檢查尺寸、fps、格數、長度，並完整解碼一次） |
| `render <spec.json> --preview` | `preview.mp4`（25% 解析度、較少取樣，比較快） |
| `render <spec.json> --frame N` | `still_<N>.png`（只渲染一格；不需要 FFmpeg） |
| `render <spec.json> --keep-frames` | 另外保留 `tmp/<job_id>/<render_id>/frames/` 的 PNG 影格 |
| `render <spec.json> --name N` | spec 不在 `output/` 裡時，新 job 使用的名稱 |

- spec 在 `output/<job_id>/` 裡 → **在原地重新渲染**；在其他地方（例如 `examples/`）→ 建立新的 job，把 spec 和圖層複製進去，原本的檔案不會被修改。
- 背景是 `incomplete` 的 job 也可以渲染（不會擋）；修補不完整的地方要自己確認。
- 重新渲染時，同名的舊輸出檔會移到 `history/<舊的 render_id>/`（`jobs.overwrite: true` 時直接刪除）。

**人工修改**：可以直接編輯 `output/<job_id>/spec.json`（easing、`enabled: false` 關掉一筆動畫、`visible: false` 隱藏圖層…），再執行一次 `render`。規格見 `docs/spec.md`。

---

## 5. 常見問題

| 狀況 | 原因與處理 |
|---|---|
| `segment` 顯示缺少套件（指向 `requirements-ai.txt`） | 沒有安裝 AI 套件：照第 2 節的第 4 步安裝到 `.venv`。Stage 1 的指令（`render`、`layers`、`anim`）不受影響 |
| `segment` 顯示模型無法使用 / `models verify` 是 `[FAIL]` | 模型沒有下載或檔案損壞：`.\animator.cmd models download <name>`（或 `--all`），再 `models verify`。`models list` / `doctor` 只看檔案大小，最後以 `models verify` 為準 |
| 找不到 Blender | 在 `config\config.yaml` 填 `tools.blender.executable` 的完整路徑（正斜線），或用 `--blender` / `ANIMATOR_BLENDER`；`doctor` 會顯示用了哪個值 |
| 找不到 FFmpeg / 沒有 libx264 | 把 FFmpeg 加進 PATH，或填 `tools.ffmpeg.executable`；要用有 libx264 的 GPL build。ffprobe 找不到時可以填 `tools.ffmpeg.ffprobe` |
| `segment` 失敗或結束碼 3（沒有物件） | `--labels` 要用英文，換成更一般的詞（例如 `animal`）；看 `output\<job_id>\logs\` 或 `tmp\<job_id>\`（失敗時保留）的 log；也可以用 `--masks` 提供自己的遮罩 |
| 背景標記 `needs_review`、結束碼 4（`incomplete`） | 大的補洞區域（≥ 15%）一律需要人工檢查；`incomplete` 代表有區域沒有修補（原本的物件還在背景上）。看 `segmentation\overview.png` 和 `layers\background.png`；可以改用 `--inpaint opencv` 再分割一次。job 仍然可以渲染 |
| 前後順序不對（例如狗應該在貓前面） | 自動排序只用「面積小的在前面」。用 `.\animator.cmd layers z <job> dog-1 --above cat-1` |
| 動畫露出不自然的背景（`anim check` 的 `[WARNING]`） | 物件移開、縮小、旋轉、隱藏時會看到 AI 修補的背景（影子不會被移除）。可以縮短移動距離、用 `layers pivot` 讓物件留在原地附近，或接受並確認畫面 |
| 縮小時物件飄起來 | pivot 預設是圖層圖片的中心；用 `layers pivot --at X,Y` 把 pivot 移到腳或底邊 |
| 渲染失敗 | 看 `output\<job_id>\history\<render_id>\blender.log`、`ffmpeg.log`、`render_report.json`；先用 `--frame 1` 試一格 |
| 錯誤訊息裡有很長的路徑、`FileNotFoundError` / `No such file` | Windows 的 260 字元路徑上限：把專案移到短的路徑（例如 `C:\AI-Animation`），或在 Windows 設定裡啟用長路徑（LongPathsEnabled，需要系統管理員）|
| 中文訊息變成亂碼 | 主控台的字碼頁不是 UTF-8；在 PowerShell 用 `chcp 65001`，或改用 Windows Terminal |

---

## 6. 在另一台 Windows 電腦上確認（手動檢查清單）

這份 README 的流程只在一台電腦（第 11 節）上實際驗證過。換到新電腦時，請逐項確認：

- [ ] `.venv` 用 Python 3.12 建立；`.\animator.cmd --version` 顯示 `animator 0.1.0`
- [ ] `config\config.yaml` 是從 `config.example.yaml` 複製的，只填了這台電腦的路徑（不要修改任何程式碼）
- [ ] `.\animator.cmd doctor`：Blender、FFmpeg、libx264、ffprobe 都是 `[OK]`
- [ ] `.\animator.cmd render examples\basic\spec.json` 產生 `result.mp4`（1280×720、30 fps、4 秒）
- [ ] `.\animator.cmd models download --all` 之後 `.\animator.cmd models verify` 三個都是 `[OK]`
- [ ] 第 3 節的教學整個做一次：`segment` 結束碼 0、`anim check` 有列出提醒、`result.mp4` 是 1920×1440、30 fps、4 秒
- [ ] 專案放在不同的路徑（例如含空格的資料夾、另一個磁碟）時上面的步驟仍然可以完成
- [ ] Blender 版本不是 5.2.3 時：記錄版本，並看 `.venv\Scripts\python.exe -m pytest -m blender` 的結果（4.x 沒有實測過）
- [ ] （開發用）`.venv\Scripts\python.exe -m pip install -r requirements-dev.txt` 之後 `.venv\Scripts\python.exe -m pytest` 全部通過

---

## 7. 架構

```
┌──────────────── 系統 Python（.venv）────────────────┐      ┌────── Blender 內建 Python ──────┐
│                                                      │      │                                  │
│ 圖片 → segment（AI 子行程）→ spec.json ◀─ layers / anim│      │  blender/build_scene.py          │
│ Stage 1：人工 PNG 圖層 ──────▲    （可人工編輯）       │      │   → 每個圖層一個平面 + 獨立材質  │
│                              │         │              │      │   → effects 打關鍵影格           │
│                              │    驗證 + resolve       │      │   → 渲染 PNG 影格                │
│                                        ▼              │      │                                  │
│                              spec.resolved.json ──────┼─────▶│                                  │
│                                                       │      └───────────────┬──────────────────┘
│  FFmpeg → MP4 → ffprobe 驗證  ◀──────────── frames/*.png ─────────────────────┘
└───────────────────────────────────────────────────────┘
```

| | 系統 Python（`src/animator/`） | Blender 內建 Python（`blender/`） |
|---|---|---|
| 職責 | 讀設定、驗證和解析 spec、分割（AI 在子行程執行）、動畫編輯與提醒、管理 job 和紀錄、呼叫 Blender / FFmpeg | 讀 `spec.resolved.json`，建場景、打關鍵影格、渲染 |
| 可以用的套件 | Stage 1 / 編輯：PyYAML；`segment`：`requirements-ai.txt` | **只能用** `bpy`、`bpy_extras`、`mathutils` 和標準函式庫 |
| 讀 config.yaml | ✅ | ❌ |

兩邊只透過檔案交換資料，不會互相 import（`tests/test_static.py` 會自動檢查）。Stage 1 的指令和編輯指令不會載入任何 AI 套件。

```
AI-Animation/
├── animator.cmd                   # Windows 啟動器：用 .venv 執行 animator
├── requirements.txt / requirements-dev.txt / requirements-ai.txt / requirements-ai.lock.txt
├── config/config.example.yaml     # 設定範本（config.yaml 是本機設定，不納入 Git）
├── docs/                          # spec.md / config.md / stage1.md / stage2.md
├── src/animator/
│   ├── cli.py, config.py, paths.py, tools.py, jobs.py, pipeline.py, errors.py
│   ├── spec/                      # schema / validate / resolve / png（只用標準函式庫）
│   ├── render/                    # blender_runner / ffmpeg
│   ├── segmentation/              # segment（AI；只在 segment / models 指令時 import）
│   └── animate/                   # layers / anim 的編輯（edit.py）與提醒（warnings.py）
├── blender/build_scene.py + anim_bl/   # Blender 端：compat / units / scene_setup / layers / tracks / effects
├── examples/basic/ examples/visibility/
├── assets/s2-0/                   # 測試照片（CC0，來源見 SOURCES.md）
├── scripts/                       # make_sample_layers.py、s2_0* / s2_1*（S2 的實驗與 A/B 腳本）、test_animation.py（Stage 0，保持不動）
├── tests/                         # pytest（標記：blender = 需要 Blender、ai = 需要模型）
├── models/                        # paths.models_dir（不納入 Git）
├── output/<job_id>/               # paths.output_dir（不納入 Git）
│   ├── spec.json、layers/、segmentation/、logs/
│   ├── result.mp4 / preview.mp4 / still_<N>.png、renders.json
│   └── history/<render_id>/、history/edits/
└── tmp/                           # paths.temp_dir（不納入 Git）
```

**可攜性**：程式裡沒有電腦專屬的路徑（`tests/test_static.py` 會掃描檢查；唯一的例外是 Stage 0 的 `scripts/test_animation.py`，保持不動）。外部工具和目錄一律從 config 讀取；專案內的相對路徑以專案根目錄為基準；spec 裡的圖層路徑以 spec 所在的資料夾為基準，所以整個 `output/<job_id>/` 可以複製到另一台電腦重新渲染。

---

## 8. 重新渲染與追溯

每次渲染都會建立 `output/<job_id>/history/<render_id>/`：

| 檔案 | 內容 |
|---|---|
| `spec.json` | 這次渲染用的 spec 快照 |
| `spec.resolved.json` | 傳給 Blender 的完整解析結果 |
| `config.effective.yaml` | 實際採用的設定值，以及每個值的來源（cli / env / config / default） |
| `render_report.json` | 指令、時間、狀態、Blender/FFmpeg 路徑與版本、spec 和每張圖層的 SHA-256、ffprobe 驗證結果 |
| `blender.log` / `ffmpeg.log` / `blender_report.json` | 外部工具的輸出 |
| `result.mp4`（被取代之後） | 下一次渲染產生同名檔案時，舊檔會移到這裡 |

`renders.json` 記錄所有渲染的索引，以及目前根目錄裡每個輸出檔屬於哪一次渲染。編輯指令的備份在 `history/edits/`。

---

## 9. 動畫系統

- 每個圖層是一張**獨立的平面**，而且有**自己的材質**；最終 alpha = PNG alpha × opacity × visibility（顯示 1 / 隱藏 0）。
- 每個效果是 `blender/anim_bl/effects/` 裡的一個模組，用 `@register("<name>", channel=...)` 註冊；每個效果只控制一個通道（position / scale / rotation / opacity / visibility）。

| 效果 | 狀態 |
|---|---|
| `move`（`to` / `by`）、`scale`、`rotate`（`to` / `by`）、`opacity` | ✅ Stage 1 |
| `fade_in` / `fade_out`（`opacity` 的別名） | ✅ Stage 1（只能直接寫在 spec） |
| `visibility`（離散的顯示 / 隱藏，區間 `[start, end)`） | ✅ S2-2（範例：`examples/visibility/`） |
| easing：`linear`、`constant`、`ease_*`，以及 `sine`、`quad`、`cubic`、`quart`、`quint`、`expo`、`circ`、`back`、`bounce`、`elastic` 的 `_in` / `_out` / `_in_out` | ✅ Stage 1（只能直接寫在 spec） |
| `glow` / `shadow` / `tint` / `light_sweep` / `particles` / `camera_*` / `shake` / `float` / `wiggle` | 名稱已保留（未實作） |

---

## 10. 開發階段

| 階段 | 內容 | 狀態 |
|---|---|---|
| 0 | 確認環境：Blender 背景渲染 + FFmpeg | ✅ |
| 1 | 人工 PNG 圖層 → spec → Blender → MP4，並保留渲染紀錄 | ✅ |
| 2（S2-0 / S2-1） | 圖片自動分層：Grounding DINO → SAM 2.1 → PyMatting → MI-GAN 背景修補 → spec.json | ✅ |
| S2-2 | 動畫編輯：move / scale / rotate / opacity、前後順序、pivot、visibility、修補區域與前後順序的提醒 | ✅ |
| 3 | 分層品質：影子、遮擋排序、修改遮罩後重建圖層 | 未開始 |
| 4 | 自然語言 → spec | 未開始 |
| 5 | 光影、粒子、鏡頭、效果預設組合、UI | 未開始 |

---

## 11. 實測環境

| 項目 | 值 |
|---|---|
| OS | Windows 11 Pro 10.0.26300 |
| CPU / GPU | Intel i7-1165G7 / NVIDIA GeForce MX450 2 GB（沒有使用） |
| Blender | 5.2.3 LTS（EEVEE），內建 Python 3.13.13 |
| 系統 Python | 3.12.1 → `.venv`：PyYAML 6.0.3、pytest 9.2.0，以及 `requirements-ai.lock.txt` 的 AI 套件（CPU 版） |
| FFmpeg | N-117917（2024-11，GPL，有 libx264） |
| 時間（2026-11-06） | `segment`（1920×1440，dog + cat，CPU）約 62 秒；渲染 1920×1440、120 格約 112 秒；`examples/basic`（1280×720、120 格）約 40 秒 |
