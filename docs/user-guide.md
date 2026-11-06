# AI-Animation 使用手冊

這份手冊說明怎麼把一張照片或插圖變成幾秒鐘的 MP4 小動畫。不需要懂程式，也不需要影片剪輯的經驗；只要能照著指令在 PowerShell 裡輸入就可以。

**寫法說明**：
- 灰色區塊裡的指令可以直接複製。用 `<尖括號>` 包起來的部分要換成你自己的內容，例如 `<job_id>` 要換成實際的資料夾名稱，**尖括號本身不要打**。
- 所有指令都在專案資料夾（例如 `C:\AI-Animation`）裡執行，開頭是 `.\animator.cmd`。
- 範例的輸出是 2026-11-06 實際執行的結果；你看到的日期時間、資料夾名稱會不一樣。

---

## A. 這個程式在做什麼

**用途**：輸入一張靜態圖片，程式會把裡面的物件（例如狗、貓、人）一個一個切出來，變成獨立的透明圖層，並把物件原本位置的背景用 AI 補起來。接著你可以讓每個物件各自移動、縮放、旋轉、淡出或暫時消失，最後輸出 MP4 影片。

**為什麼要先分圖層**：如果直接移動整張圖，背景會跟著一起動。把物件切成獨立的圖層、背景另外補好之後，才能做到「狗往右走、背景不動」這種效果。

**適合的圖片**：
- 物件輪廓清楚、和背景顏色有差異（例如草地上的狗、桌上的杯子）。
- 物件之間的重疊不要太多。
- 背景越單純（天空、牆壁、草地），補出來的背景越自然。
- JPG、PNG 都可以；有透明背景的圖片會先疊在白色上再處理。

**輸出**：
- 每個物件一張透明 PNG、補好的背景圖、一份描述動畫的 `spec.json`。
- 最後的影片 `result.mp4`（H.264，解析度和原圖相同，預設 30 fps）。

**做不到的事（請先有心理準備）**：
- **不會補出被擋住的部分**：如果貓的身體有一半被狗擋住，切出來的貓就只有看得到的那一半；狗移開後，貓身上會有缺口。
- **背景是 AI 補的，不一定完美**：物件移開後露出的地方可能有模糊、紋理不一致的情形，需要自己看過。
- **影子、倒影通常不會被切掉**：物件移走後，影子可能留在原地。
- **前後順序是猜的**：程式用「面積小的在前面」決定誰在前面，不一定正確，需要時要手動調整。
- **分割結果不保證完美**：物件可能漏掉、多切到一塊背景，或邊緣不夠乾淨。
- **不能用文字描述直接產生動畫**：動畫要用指令一個一個加上去（第 E 節）。
- 沒有圖形介面，也沒有批次處理多張圖片的功能。

---

## B. 安裝與第一次設定

### B-1. 需要的軟體

| 軟體 | 需要的版本 | 實際測試過的版本 | 取得方式 |
|---|---|---|---|
| Windows | 64 位元 | Windows 11 Pro（**只測試過 Windows 11**） | — |
| Python | 3.12 | 3.12.1 | https://www.python.org/downloads/windows/ |
| Blender | 4.2 LTS 以上 | 5.2.3 LTS（**4.x 沒有實際測試**） | https://www.blender.org/download/ |
| FFmpeg | 有 libx264 的版本 | 2024-11 的 BtbN GPL build | https://github.com/BtbN/FFmpeg-Builds/releases（`ffmpeg-master-latest-win64-gpl.zip`） |
| Git | 任何版本 | — | https://git-scm.com/ |

- **硬碟空間**：Python 環境（含 AI 套件）約 1.4 GB，AI 模型約 874 MB，每個作品（job）約幾十 MB。
- **電腦**：只用 CPU，不需要獨立顯示卡；測試用的是筆電等級的 CPU（i7-1165G7）。
- **網路**：只有安裝套件和下載模型時需要；之後分割、渲染都可以離線。
- **資料夾位置**：請把專案放在**路徑短的位置**，例如 `C:\AI-Animation`。Windows 的路徑長度有上限（260 個字元），放在很深的資料夾裡可能會出錯。

### B-2. 取得專案、建立 Python 環境

開啟 PowerShell，依序執行（第 2 行的 Python 路徑請換成你電腦上 Python 3.12 的實際位置）：

```powershell
git clone <專案的網址> C:\AI-Animation
cd C:\AI-Animation
& "<Python 3.12 的路徑>\python.exe" -m venv .venv
```

`.venv` 是這個專案專用的 Python 環境，套件只會裝在這裡，不會影響電腦上其他的 Python 或 Blender。

接著安裝套件：

```powershell
# 渲染需要的套件
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 自動分割（AI）需要的套件（只用 CPU 的版本）
.venv\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0+cpu torchvision==0.29.2+cpu
.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-ai.txt
```

> **要不要「啟用」.venv？** 不需要。`.\animator.cmd` 會自動使用 `.venv`。如果你想直接打 `python`、`pip`，可以執行 `.venv\Scripts\Activate.ps1`（如果 PowerShell 說不允許執行指令碼，先執行一次 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`）。

### B-3. 設定 Blender 和 FFmpeg 的位置

```powershell
copy config\config.example.yaml config\config.yaml
notepad config\config.yaml
```

在記事本裡找到這兩行，填入你電腦上的路徑（**用正斜線 `/`**），存檔：

```yaml
tools:
  blender:
    executable: "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
  ffmpeg:
    executable: "ffmpeg"
```

- FFmpeg 如果已經加進 PATH，寫 `"ffmpeg"` 就可以；沒有的話寫完整路徑，例如 `"C:/tools/ffmpeg/bin/ffmpeg.exe"`。也可以用 `~` 代表使用者資料夾，例如 `"~/tools/ffmpeg/bin/ffmpeg.exe"`。
- `config\config.yaml` 是這台電腦自己的設定，不會上傳到 Git；其他欄位通常不用改（完整說明見 `docs/config.md`）。

### B-4. 下載並檢查 AI 模型

```powershell
.\animator.cmd models download --all
.\animator.cmd models verify
```

`models verify` 會計算每個檔案的 SHA-256（類似指紋），三個模型都要是 `[OK]`：

```
[OK] grounding-dino-tiny  detector     690.3 MB  Apache-2.0  ...  9 個檔案 SHA-256 正確
[OK] sam2.1-hiera-tiny    segmenter    155.9 MB  Apache-2.0  ...  6 個檔案 SHA-256 正確
[OK] migan                inpainter     28.1 MB  MIT         ...  1 個檔案 SHA-256 正確
```

`models list` 只看檔案在不在、大小對不對（很快），最後還是以 `models verify` 的結果為準。

### B-5. 開始前的總檢查

```powershell
.\animator.cmd doctor
```

每一項都應該是 `[OK]`，最後一行是「結果：全部通過」。如果有 `[FAIL]`，請看第 H 節。

想先確認「渲染」本身可以用（不需要 AI），可以渲染附的範例：

```powershell
.\animator.cmd render examples\basic\spec.json
```

完成後會顯示影片的位置（在 `output\` 裡，資料夾名稱是日期時間加上 `_basic`）。

---

## C. 第一個作品：從照片到 MP4

這一節用專案附的照片 `assets\s2-0\occlusion_dogcat.jpg`（一隻狗和一隻貓，1920×1440，CC0 授權）。

### 1. 選一張圖片

第一次建議直接用這張附的照片。之後換成自己的圖片時，把 `--image` 後面換成你的檔案路徑即可（路徑有空格時要用引號包起來）。**原本的圖片不會被修改。**

### 2. 分割（約 1 分鐘）

```powershell
.\animator.cmd segment --image assets\s2-0\occlusion_dogcat.jpg --labels "dog, cat" --name mydog --duration 4
```

- `--labels`：要切出來的東西，**用英文**，多個用逗號分隔。
- `--name`：作品名稱（會出現在資料夾名稱裡）。
- `--duration`：影片長度（秒），不寫的話是 5 秒。

成功時會看到：

```
已建立工作：...\output\20261106-173757_mydog
圖層：2 個物件 + 背景
  z=10   dog-1                dog
  z=20   cat-1                cat
  背景區域 0：7.2%  migan  passed_checks  已修補
```

`20261106-173757_mydog` 就是這個作品的 **job_id**（日期-時間_名稱），後面的指令都會用到。下面的範例請換成你自己的 job_id。

### 3. 作品資料夾裡有什麼

```
output\<job_id>\
├── spec.json            ← 動畫的設定（用指令修改）
├── layers\              ← 切出來的圖層：background.png、dog-1.png、cat-1.png
├── segmentation\        ← 分割結果（overview.png、segmentation.json、masks\）
└── logs\                ← 分割過程的紀錄（出錯時看這裡）
```

詳細說明見第 D 節。

### 4. 檢查分割結果

用圖片檢視器打開：
- `output\<job_id>\segmentation\overview.png`：每個物件切到的範圍，以及要補的背景區域。
- `output\<job_id>\layers\background.png`：物件拿掉、補好的背景。
- `output\<job_id>\layers\dog-1.png` 等：切出來的物件。

如果物件切錯或漏掉，可以換一個 `--labels`（例如 `animal`）重新分割；每次分割都會建立新的資料夾，不會覆蓋舊的。

### 5. 驗證 spec

```powershell
.\animator.cmd validate output\<job_id>\spec.json
```

顯示「驗證通過（1 個警告）」就可以了。這個警告是「背景 PNG 沒有 alpha 通道」，對背景來說是正常的，可以忽略。

### 6. 列出圖層

```powershell
.\animator.cmd layers list <job_id>
```

```
id                     label                 z  position(pivot px)   anchor             background animations
background             -                     0  [960, 720]           [0.5, 0.5]         yes                 0
dog-1                  dog                  10  [902, 576]           [0.5, 0.5]         no                  0
cat-1                  cat                  20  [1095, 740]          [0.5, 0.5]         no                  0
```

### 7. 圖層 ID 是什麼

- 第一欄的 `dog-1`、`cat-1` 就是**圖層 ID**，加動畫時用它指定要動哪一個物件。規則是「標籤-編號」，同一種物件有兩個時會是 `dog-1`、`dog-2`。
- `background` 是背景，**不能加動畫**。
- `z` 是前後順序（越大越前面）；`position` 是這個圖層的中心點（pivot）在畫面上的位置。

### 8. 動畫之前先檢查

```powershell
.\animator.cmd anim check <job_id>
```

```
[NOTICE] dog-1（後）和 cat-1（前）在第 1 格的範圍重疊，前後順序是自動決定的（面積小的在前面）；分割時兩個遮罩重疊 18 px，不一定正確；需要時用 animator layers z 調整
```

這是提醒，不是錯誤：狗和貓有重疊，程式猜貓在前面。看 `overview.png` 確認一下；如果猜錯了，用第 10 步調整。

### 9. 加上動畫

```powershell
.\animator.cmd anim add <job_id> dog-1 move  --by 300,0 --start 0.5 --duration 1.5
.\animator.cmd anim add <job_id> dog-1 scale --to 0.8   --start 0.5 --duration 1.5
.\animator.cmd anim add <job_id> cat-1 visibility --to hidden --start 2.5 --duration 1
```

意思是：狗從 0.5 秒開始、花 1.5 秒往右移 300 像素，同時縮小到 0.8 倍；貓在 2.5 秒到 3.5 秒之間消失。每個指令都會：
- 顯示加上的動畫，例如 `已加入動畫 dog-1-move-1：dog-1 move {"by": [300, 0]}  0.5s → 2s`；
- 把修改前的 spec 備份到 `history\edits\`；
- 列出和這個圖層有關的提醒（第 F 節）。

### 10. 需要時調整前後順序或 pivot

- **前後順序**：如果狗應該在貓前面：
  ```powershell
  .\animator.cmd layers z <job_id> dog-1 --above cat-1
  ```
- **pivot（縮放、旋轉的中心）**：預設是圖層圖片的中心，縮小時狗會往中心縮，腳就離開地面。把 pivot 移到腳下：
  ```powershell
  .\animator.cmd layers pivot <job_id> dog-1 --at 902,939
  ```
  `902,939` 是狗腳底在畫面上的座標（怎麼找座標見第 E 節）。pivot 最好在加 scale / rotate 動畫**之前**設定。

### 11. 渲染

先渲染低解析度的預覽（快很多），確認動作沒問題再渲染正式版：

```powershell
.\animator.cmd render output\<job_id>\spec.json --preview
.\animator.cmd render output\<job_id>\spec.json
```

正式版（1920×1440、120 格）在測試的筆電上約 2 分鐘。完成時會看到：

```
完成：...\output\<job_id>\result.mp4
影片：h264 1920x1440 30fps 120 格 4.00s，可解碼：是
```

### 12. 找到並播放影片

- 正式版：`output\<job_id>\result.mp4`
- 預覽：`output\<job_id>\preview.mp4`

用任何播放器（例如「媒體播放器」）打開即可。

---

## D. 看懂分割結果

| 檔案 / 資料夾 | 內容 | 可以改嗎 |
|---|---|---|
| `spec.json` | 畫布大小、長度、每個圖層的位置與前後順序、所有動畫 | 用指令修改最安全；也可以手動編輯（見第 E-8 節） |
| `layers\background.png` | 物件拿掉、補好的背景 | 不要改 |
| `layers\<圖層ID>.png` | 每個物件的透明 PNG（只裁切到物件的範圍） | 不要改 |
| `segmentation\overview.png` | 每個物件的範圍和補洞區域（檢查用） | 只看 |
| `segmentation\source.png` | 分割時使用的原圖 | 只看 |
| `segmentation\masks\` | 每個物件的遮罩（白色 = 物件）和 `background_hole.png`（要補的區域） | 只看 |
| `segmentation\segmentation.json` | 偵測分數、背景修補的結果、需要人工檢查的項目、物件和補洞區域的關係 | 不要改 |
| `logs\` | 分割過程的紀錄 | 出錯時看 |
| `history\` | 每次編輯的備份（`history\edits\`）和每次渲染的紀錄 | 不要改 |

**背景修補的狀態**（`segment` 的輸出、`segmentation.json` 的 `background.status`）：

| 狀態 | 意思 | 要做什麼 |
|---|---|---|
| `passed_checks` | 已修補，通過自動檢查 | 自動檢查只確認格式和範圍正確，**畫面還是要自己看** |
| `needs_review` | 已修補，但區域很大（超過畫面的 15%）或用了備援方法 | 一定要看 `background.png` |
| `incomplete` | 有區域沒有修補，原本的物件還留在背景上（`segment` 的結束碼是 4） | 物件移開時會看到「兩個」物件；可以加 `--inpaint opencv` 重新分割 |
| `unchanged` | 沒有切出任何物件，背景就是原圖 | 換 `--labels` 再試 |

`segment` 結束時如果列出「需要人工檢查」，原因也會記在 `segmentation.json` 的 `review` 裡。

---

## E. 編輯動畫

### E-1. 基本規則

- **座標**：單位是像素，原點在畫面**左上角**，x 往右變大、y **往下**變大。畫面大小就是原圖大小（寬或高是奇數時會補成偶數）。
- **時間**：單位是秒。`--start` 是開始時間，`--duration` 是持續多久；`start + duration` 不能超過影片長度。
- **影格**：預設每秒 30 格，第 1 格就是 0 秒。
- **找座標的方法**：用「小畫家」打開 `segmentation\source.png`，把滑鼠移到想要的位置，左下角會顯示像素座標。圖層在畫面上的範圍也記在 `spec.json` 裡那個圖層的 `meta.crop_box`（左、上、右、下）。
- **同一種動作不能重疊**：同一個圖層的兩段 move 不能在時間上重疊（頭尾相接可以）；但 move 和 scale 這種不同的動作可以同時進行。
- **`--by` 和 `--to`**：`--by` 是「變化多少」（相對），`--to` 是「變成多少」（絕對）。每個指令只能用其中一個。

| 動作 | 指令範例 | `--by` | `--to` |
|---|---|---|---|
| 移動 | `anim add <job_id> dog-1 move --by -200,0 --start 0 --duration 2` | 移動多少像素（DX,DY） | 移到哪個座標（pivot 的位置） |
| 縮放 | `anim add <job_id> dog-1 scale --to 1.5 --start 0 --duration 2` | 不支援 | 倍數；`1.2,0.8` 是寬、高分別縮放 |
| 旋轉 | `anim add <job_id> dog-1 rotate --by -45 --start 0 --duration 2` | 轉多少度 | 轉到幾度 |
| 透明度 | `anim add <job_id> dog-1 opacity --to 0 --start 3 --duration 1` | 不支援 | 0（完全透明）~ 1（不透明） |
| 顯示 / 隱藏 | `anim add <job_id> cat-1 visibility --to hidden --start 1 --duration 2` | 不支援 | `hidden` 或 `visible` |

（每個指令前面都要加 `.\animator.cmd `。）

### E-2. 移動（move）

```powershell
.\animator.cmd anim add <job_id> dog-1 move --by 300,0 --start 0.5 --duration 1.5
.\animator.cmd anim add <job_id> dog-1 move --to 1200,600 --start 2 --duration 1
```

- `--by 300,0`：往右 300、往下 0。往左或往上用負數，例如 `--by -120,0`。
- `--to 1200,600`：把圖層的 pivot 移到畫面座標 (1200, 600)。
- 動作預設是「慢慢開始、慢慢停下」（ease in-out）。動畫結束後會停在最後的位置。

### E-3. 縮放（scale）

```powershell
.\animator.cmd anim add <job_id> dog-1 scale --to 0.8 --start 0.5 --duration 1.5
```

以 pivot 為中心縮放。`1` 是原本的大小，必須大於 0。

### E-4. 旋轉（rotate）

```powershell
.\animator.cmd anim add <job_id> dog-1 rotate --by 15 --start 1 --duration 0.5
```

單位是度，**順時針為正**，負數就是逆時針。以 pivot 為中心旋轉。

### E-5. 透明度（opacity）

```powershell
.\animator.cmd anim add <job_id> dog-1 opacity --to 0 --start 3 --duration 1
```

在 3 秒到 4 秒之間慢慢淡出。之後要再淡入，就再加一筆 `--to 1`（時間不能和前一筆重疊）。

### E-6. 顯示 / 隱藏（visibility）

```powershell
.\animator.cmd anim add <job_id> cat-1 visibility --to hidden --start 2.5 --duration 1
```

- 這是**瞬間**消失 / 出現，不是淡出；要慢慢消失請用 opacity。
- 只在 `[開始, 開始 + 持續時間)` 這段時間有效，之後自動恢復原本的狀態；如果一直持續到影片結尾，最後一格也維持這個狀態。
- 隱藏期間其他動畫照常進行，重新出現時會在當下的位置。
- 對本來就看得到的圖層加 `--to visible`，指令會拒絕（因為沒有效果）。

### E-7. pivot（縮放、旋轉的中心）

```powershell
.\animator.cmd layers pivot <job_id> dog-1 --at 902,939
```

- 預設的 pivot 是圖層圖片的中心。改成腳底、手腕等位置，縮放、旋轉看起來才自然。
- `--at` 是**第 1 格畫面上**的座標，而且必須在這個圖層的圖片範圍內（`meta.crop_box` 之內）。
- 修改 pivot **不會讓畫面跳動**：第 1 格看起來完全一樣。改變的是之後縮放、旋轉的中心。
- 因為 `move --to` 指定的是「pivot 要到哪裡」，改了 pivot 之後，這個圖層既有的 `move --to`（以及 spec 裡的 `from`）會自動平移同樣的距離，讓物件仍然到達原本的位置；`move --by` 是相對移動，不會改。指令會列出改了哪幾筆。
- pivot 已經在那個位置時，指令會顯示「沒有變更」，不會寫入。

### E-8. 前後順序（z）

```powershell
.\animator.cmd layers z <job_id> dog-1 --above cat-1    # 放在 cat-1 的正前方
.\animator.cmd layers z <job_id> dog-1 --below cat-1    # 放在 cat-1 的正後方
.\animator.cmd layers z <job_id> dog-1 --z 30           # 直接指定數字（越大越前面）
```

- 前後順序在整支影片裡固定，不能中途改變。
- 只會改這個圖層，不會重新編號其他圖層；物件不能放到背景後面。
- 兩個相鄰圖層的 z 相同時 `--above` / `--below` 會拒絕，請改用 `--z` 指定數字。

### E-9. 指令做不到的事：手動編輯 spec.json

下面這些功能只能直接編輯 `spec.json`（用記事本或 VS Code）：

- **刪除或暫時關掉一筆動畫**：沒有刪除的指令。在那一筆動畫裡加上 `"enabled": false` 就會被忽略，或直接刪掉那一筆。
- **改變動作的節奏（easing）**：在那一筆動畫裡加上例如 `"easing": "linear"`（等速）。可以用的值：`linear`、`constant`、`ease_in`、`ease_out`、`ease_in_out`，以及 `sine` / `quad` / `cubic` / `quart` / `quint` / `expo` / `circ` / `back` / `bounce` / `elastic` 加上 `_in`、`_out` 或 `_in_out`（例如 `back_out` 會稍微超過再回來）。
- **淡入 / 淡出的簡寫**（`fade_in` / `fade_out`）、**從指定的值開始**（`from`）、**修改影片長度**（`canvas.duration`）。

手動編輯的步驟：
1. 先複製一份：`copy output\<job_id>\spec.json output\<job_id>\spec-backup.json`
2. 編輯、存檔（編碼用 UTF-8）。
3. 檢查：`.\animator.cmd validate output\<job_id>\spec.json`。有錯誤時會列出位置，例如 `animations[2].easing`。
4. 想回到某一次指令修改前的狀態，可以把 `history\edits\` 裡對應時間的檔案複製回 `spec.json`。

格式的完整說明見 `docs/spec.md`。

---

## F. 看懂提醒（warning）

**錯誤和提醒的差別**：
- **錯誤**（`[FAIL]`）：指令沒有完成，spec 沒有被修改，結束碼不是 0。例如時間超過影片長度、兩段 move 重疊、對背景加動畫。
- **提醒**（`[WARNING]` / `[NOTICE]` / `[WARN]`）：指令已經完成，只是提醒你檢查畫面。**提醒不會阻止渲染。**

隨時可以用下面的指令列出所有提醒（只讀取，不會修改任何檔案）：

```powershell
.\animator.cmd anim check <job_id>
```

`anim add`、`layers z`、`layers pivot` 成功之後，也會列出和那個圖層有關的提醒。

| 提醒 | 意思 | 要怎麼處理 |
|---|---|---|
| `[WARNING] … 可能露出修補過的背景 區域 0（已修補，只通過自動檢查…）：move（0.5 秒 / 第 16 格起）` | 物件移開、縮小、旋轉或隱藏後，原本被它擋住、由 AI 補的背景會露出來 | 渲染（或預覽）後看那個位置是否可以接受；不能接受就縮小動作幅度 |
| `[WARNING] … 區域 N（沒有修補：原本的物件仍留在背景上…）` | 背景沒補好，物件移開後會看到兩個一樣的物件 | **一定要處理**：用 `--inpaint opencv` 重新分割，或不要移動這個物件 |
| `[NOTICE] … opacity < 1` / `scale > 1` | 半透明或放大時，可能稍微看到補過的背景 | 通常可以接受，看一下就好 |
| `[NOTICE] A（後）和 B（前）… 前後順序是自動決定的` | 兩個物件重疊（或動畫中可能重疊），誰在前面是程式猜的 | 看 `overview.png` 或預覽；猜錯就用 `layers z` 調整。調整過的圖層不會再出現這個提醒 |
| `[NOTICE] [關聯不明] …` | 這個作品是舊版程式分割的，無法判斷動畫會露出哪個區域 | 渲染後自己檢查；或用新版重新分割 |
| `[WARN] layers[0].image: PNG 沒有 alpha 通道` | 背景圖沒有透明度 | 背景本來就不透明，**可以忽略** |

提醒的範圍是保守估計（用矩形估算），有時候物件實際上沒有碰到也會提醒。

---

## G. 渲染與輸出

| 指令 | 輸出檔（在 `output\<job_id>\`） | 用途 |
|---|---|---|
| `.\animator.cmd render output\<job_id>\spec.json --preview` | `preview.mp4` | 四分之一解析度、比較快，用來確認動作 |
| `.\animator.cmd render output\<job_id>\spec.json --frame 60` | `still_60.png` | 只看第 60 格的畫面（很快） |
| `.\animator.cmd render output\<job_id>\spec.json` | `result.mp4` | 正式版 |

- **注意**：`render` 要給 `spec.json` 的路徑（不是 job_id）。
- 影片的規格：H.264（yuv420p，大部分播放器都能播）、解析度和畫布相同、每秒 30 格（預設）、長度就是分割時的 `--duration`。完成時程式會自動檢查解析度、格數、長度，並把影片完整解碼一次。
- **會不會覆蓋舊的影片？** 同一個作品重新渲染時，舊的 `result.mp4` 會被移到 `history\<上一次的渲染編號>\`，不會直接刪除（除非你在 config 設定 `jobs.overwrite: true`）。每次渲染的設定和紀錄也都保存在 `history\` 裡。
- 想同時保留兩個版本，可以把 `result.mp4` 另外複製一份並改名。
- `--keep-frames` 會另外保留每一格的 PNG（在 `tmp\` 裡），平常不需要。
- 背景狀態是 `incomplete` 的作品也可以渲染，只是要自己確認畫面。

---

## H. 疑難排解

| 狀況 | 怎麼做 |
|---|---|
| 分割時顯示缺少套件、提到 `requirements-ai.txt` | AI 套件沒有安裝：照 B-2 安裝。渲染、編輯指令不需要 AI 套件 |
| 分割時顯示模型無法使用 | 執行 `.\animator.cmd models download --all`，再 `.\animator.cmd models verify` |
| `models verify` 顯示 `[FAIL]` | 檔案損壞或不完整：`.\animator.cmd models download <模型名稱>` 重新下載（模型名稱見 `models list`） |
| `doctor` 顯示 Blender `[FAIL]` | `config\config.yaml` 的 `tools.blender.executable` 路徑不對；確認檔案存在、用正斜線。也可以臨時用 `.\animator.cmd --blender "<blender.exe 的路徑>" doctor` 測試 |
| `doctor` 顯示 FFmpeg `[FAIL]` 或沒有 libx264 | 確認 `tools.ffmpeg.executable`；請下載有 libx264 的 GPL 版本 |
| 分割失敗、或說沒有產生物件（結束碼 3） | 標籤要用英文；換成比較一般的詞（`animal`、`person`）；看 `output\<job_id>\logs\` 的紀錄。失敗時暫存檔會留在 `tmp\` 裡 |
| 背景需要人工檢查（`needs_review`）、或結束碼 4（`incomplete`） | 打開 `layers\background.png` 檢查；不滿意可以加 `--inpaint opencv` 重新分割（會建立新的作品） |
| 物件前後順序不對 | `.\animator.cmd layers z <job_id> <前面的圖層> --above <後面的圖層>` |
| 動畫露出奇怪的背景 | 見第 F 節：縮小移動距離，或用 pivot 讓物件留在原地附近 |
| 縮小時物件飄起來 | 用 `layers pivot` 把 pivot 移到物件底部（E-7） |
| 指令說時間重疊（結束碼 2） | 同一個圖層的同一種動作時間重疊了；改 `--start`，或讓前一段結束後再開始 |
| 渲染失敗 | 看 `output\<job_id>\history\<渲染編號>\` 裡的 `blender.log`、`ffmpeg.log`；先用 `--frame 1` 試渲染一格 |
| 錯誤訊息有很長的路徑、`FileNotFoundError` | Windows 路徑太長：把專案移到 `C:\AI-Animation` 這種短路徑，或請系統管理員啟用長路徑支援（LongPathsEnabled） |
| 中文變成亂碼 | 在 PowerShell 先執行 `chcp 65001`，或改用 Windows Terminal |

指令的結束碼：`0` 成功、`1` 一般錯誤（找不到檔案、參數錯誤、渲染失敗…）、`2` spec 驗證失敗或指令格式錯誤、`3` 分割沒有產生物件、`4` 背景沒有完整修補。

---

## I. 建議的做法

1. **先用簡單的圖片**：一、兩個輪廓清楚的物件，背景單純。
2. **分割完先檢查**：看 `overview.png` 和 `background.png`，不滿意就重新分割，不要急著加動畫。
3. **先決定前後順序和 pivot**，再加動畫（尤其是 scale、rotate）。
4. **動作從小開始**：移動距離越大，露出的補過的背景越多。
5. **每次修改後跑一次 `anim check`**，看看有沒有新的 `[WARNING]`。
6. **先 `--preview` 再正式渲染**，或用 `--frame` 只看一格。
7. **原圖不要放在 `output\` 裡**，也不需要另外備份；程式不會修改原圖。
8. **手動編輯 spec.json 前先複製一份**（指令修改時會自動備份到 `history\edits\`）。

---

## J. 指令一覽

所有指令都是 `.\animator.cmd <指令>`；在任何指令後面加 `-h` 可以看說明。`<job>` 可以是作品資料夾、裡面的 `spec.json`，或 job_id。

| 指令 | 說明 |
|---|---|
| `doctor [--show-config]` | 檢查設定、Blender、FFmpeg、AI 套件和模型 |
| `models list` | 列出模型和快速檢查結果 |
| `models download --all`（或 `<模型名稱>`） | 下載模型 |
| `models verify` | 完整檢查模型檔案（SHA-256） |
| `segment --image <圖片> [--labels "<英文標籤>"] [--name <名稱>] [--duration <秒>] [--fps <數字>] [--inpaint migan\|opencv\|none] [--no-refine] [--masks <遮罩資料夾>] [--keep-temp]` | 分割圖片，建立新作品 |
| `new --layers <PNG 資料夾> [--name <名稱>] [--duration <秒>] [--fps <數字>]` | 用自己準備的 PNG 圖層建立作品（不需要 AI） |
| `validate <spec.json>` | 檢查 spec 是否正確 |
| `layers list <job>` | 列出圖層 |
| `layers z <job> <圖層ID> (--z <數字> \| --above <圖層ID> \| --below <圖層ID>)` | 調整前後順序 |
| `layers pivot <job> <圖層ID> --at <X>,<Y>` | 移動 pivot |
| `anim add <job> <圖層ID> move\|scale\|rotate\|opacity\|visibility (--by <值> \| --to <值>) --start <秒> --duration <秒>` | 加上動畫 |
| `anim check <job>` | 列出提醒（不修改檔案） |
| `render <spec.json> [--preview] [--frame <N>] [--keep-frames] [--name <名稱>]` | 渲染（`--preview` 低解析度預覽；`--frame` 只渲染一格） |

放在指令名稱前面的共用選項：`--config <設定檔>`、`--blender <路徑>`、`--ffmpeg <路徑>`、`-v`（顯示詳細訊息）、`--version`。

更多細節：`README.md`（指令參考、結束碼）、`docs/spec.md`（spec.json 格式）、`docs/config.md`（設定）。
