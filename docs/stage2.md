# Stage 2：AI Image Segmentation（圖片自動分層）— 技術評估與設計

**狀態：✅ S2-0、S2-0.1 已審查通過（MI-GAN 為預設的背景修補）；🔧 S2-1（第一個可執行的端到端版本）已依審查意見修正，等待最後審查。** 實測結果見「S2-0 結果」和「S2-0.1 結果」；第 1~11 節是原本的設計草案，實作以「S2-1」這一節為準。

## S2-1：第一個可執行版本（2026-11-04）

> 注意：使用者核准的「S2-1」是**完整的端到端流程**，涵蓋第 10 節原本規劃的 S2-1 ~ S2-8 的大部分；第 10 節的編號只是原始草案。

### 可以執行的部分

```powershell
.\animator.cmd models list                       # 模型、授權、revision、SHA-256 狀態
.\animator.cmd models download --all             # 唯一需要網路的步驟（鎖定 revision，下載後驗證 SHA-256）
.\animator.cmd models verify                     # 重新計算 SHA-256
.\animator.cmd segment --image assets\s2-0\occlusion_dogcat.jpg --labels "dog, cat" --name demo
.\animator.cmd segment --image x.jpg --masks <遮罩資料夾>      # 不跑 AI 偵測 / 分割，只用自己的遮罩
.venv\Scripts\python.exe scripts\s2_1_demo_animate.py output\<job_id>\spec.json   # 加上示範動畫
.\animator.cmd render output\<job_id>\spec.json  # Stage 1，沒有修改
```

| 步驟 | 實作 | 執行位置 |
|---|---|---|
| 讀圖 | EXIF 方向校正、RGBA → 白底、奇數尺寸右 / 下補 1 px | 主行程 |
| 偵測 | Grounding DINO Tiny，處理圖長邊 `ai.max_side`；標籤正規化（"dog cat" → "dog"；整個字比對、同位置取最長，"category" 不會變成 "cat"）、同標籤 NMS、每個標籤最多 `max_per_label` 個 | 子行程 `vision` |
| 分割 | SAM 2.1 Tiny（框提示）→ 放大回原圖 → 去碎塊、補小洞 | 同一個 `vision` 子行程（DINO 釋放後才載入） |
| 排序 / 重疊 | z：背景 0、物件依面積由大到小 10, 20, …（面積小的在前面）；重疊像素歸給前面的圖層 | 主行程 |
| 邊緣精修 | PyMatting closed-form + 前景色估計；trimap 內縮 band/3、外擴 band；只在含有未知帶的 tile（≤ 1024 px，重疊 32 px）裡計算 → 記憶體有上限；記憶體不足時明確報錯 | 子行程 `refine` |
| 圖層 | RGB 直接取原圖，只有邊緣帶用去色邊的前景色；alpha 保留到「可見範圍外擴 band_px」；依 alpha 裁切，`position` = 裁切中心 | 主行程 |
| 背景 | 遮罩聯集外擴 max(dilate_px, 8) → 每個連通區域在自己的上下文視窗裡用 MI-GAN 補（輸入原圖、255 = 保留；視窗內**所有還沒補好的洞**都設為要補，其他物件不會被當成背景）→ 自動檢查 → 只貼回這個區域 → 外擴帶漸層還原成原圖（離物件 max(3, band_px) 內用修補結果） | 子行程 `inpaint` |
| 備援 | MI-GAN 失敗（例如模型不存在）**或結果沒有通過自動檢查** → OpenCV Telea，**一律標記需要人工檢查**；備援也失敗時該區域保留原圖（`failed` / `failed_checks`） | 子行程 `inpaint` |
| 人工檢查標記 | 物件遮罩或補洞區域 ≥ 畫面 15% → `needs_review`（已修補）；檢查沒過 → `failed_checks`（不貼回）；沒有任何物件 → `no_objects`（CLI 結束碼 3）；被清除的遮罩 / 偵測也會列出 | 主行程 |
| 修補結果的狀態 | 每個區域有 `repaired`（修補結果通過自動檢查、已經貼回背景）和 `attempts`（每個 backend 的結果）。只要有一個區域 `repaired: false`，背景狀態就是 **`incomplete`**（優先於 `needs_review`），review 會列出 `unrepaired：背景區域 N … 原本的物件仍留在背景上`，CLI 結束碼 **4**。背景狀態：`passed_checks`（全部通過、沒有需要檢查的項目）→ `needs_review`（全部已修補，但有大洞或使用了備援）→ `incomplete`（有區域沒有修補）；沒有物件時是 `unchanged` | 主行程 |
| 輸出 | 標準 spec v1（沒有動畫，`meta.source_image` = `segmentation/source.png`）＋ `layers/*.png` ＋ `segmentation/`（source、masks、overview、segmentation.json）；寫出前用 Stage 1 的 `validate` 檢查 | 主行程 |
| 使用者遮罩（`--masks`） | 有 alpha 而且不是整張不透明 → 用 alpha，否則用亮度（GIMP / Photoshop 的不透明白底黑字遮罩也可以）；尺寸必須和原圖相同（奇數尺寸會補邊，不縮放），否則報錯；`background_hole.png` 自動略過 | 主行程 |

- 每個 AI 步驟都在獨立的子行程（`python -m animator.segmentation.worker`），而且一律離線（`HF_HUB_OFFLINE=1` + 只讀本機檔案）；`ai.models.offline` 這個沒有作用的設定已移除。
- **預先驗證**：所有 backend 名稱、數值範圍、`ai.device`、圖片、遮罩（尺寸）、偵測 / 分割模型，都在建立任何檔案之前檢查。
- **原子性**：job 先寫在 `tmp/<job_id>/job/`，全部成功、spec 通過驗證之後才搬到 `output/<job_id>/`（segmentation.json 先寫、spec.json 最後寫）。失敗時 `output/` 不會出現任何東西；`tmp/<job_id>/` 保留 log、暫存檔和 `failure.json`。成功時刪除 `tmp/<job_id>/`（`--keep-temp` 可以保留）。
- 模型 registry（`src/animator/segmentation/models.py`）鎖定 revision，並登記**每個檔案**（權重、config、tokenizer…）的大小和 SHA-256；每次推論前完整重算雜湊（不使用快取，三個模型合計約 1 秒）；`ai.models.allow_noncommercial = false` 時拒絕非商用模型。`doctor` / `models list` 只檢查存在與大小（會標示「未計算雜湊」），`models verify` 完整驗證。
- 依賴：`requirements-ai.txt`（直接依賴）＋ `requirements-ai.lock.txt`（`pip freeze` 的完整版本，41 個套件）。兩個檔案都必須是 ASCII：pip 用系統字碼頁（cp950）讀 requirements 檔。
- `status = passed_checks` 只代表通過自動檢查（輸出尺寸、洞外像素不變、洞內有被改寫 = 遮罩極性正確），**不代表視覺品質已經人工確認**。

### 實測（CPU，離線）

| 項目 | 結果 |
|---|---|
| `occlusion_dogcat.jpg`（1920×1440，dog + cat） | 第一次 61 s（其中 torch / transformers import 約 30 s）；第二次 40 s。dog、cat 都正確分出，背景區域 7.2%，`passed_checks` |
| 圖層疊回背景 vs 原圖 | 差異 > 8 的像素 1.2%（都在物件邊緣的精修帶 / 外擴帶裡） |
| Stage 1 渲染（不加動畫） | 洞外和「原圖單一圖層」的渲染相同；Stage 1 的 filter 修正之後，分層渲染和 numpy 疊圖幾乎相同（測試門檻：平均差異 < 0.05） |
| 審查修正後（同一張圖，35 s） | `passed_checks`（`target_changed_ratio` 1.0）；疊回背景 vs 原圖：差異 > 8 的像素 1.3% |
| 示範動畫 → MP4 | h264 1920×1440、30 fps、5 秒，可解碼；物件移開後背景沒有破洞（狗的影子還在，見限制） |

### 測試

`tests/test_segmentation.py`（24 個）＋ `tests/test_segmentation_robustness.py`（審查修正，37 個）：
- 不需要模型（約 4 秒）：lazy import 界線、config 型別、EXIF / 補邊、遮罩處理、標籤正規化、z 排序與重疊、15% 門檻（剛好 15% 要標記）、最少外擴 8 px、MI-GAN 遮罩極性、極性反轉偵測、合成後洞外不變、裁切與 position、模型 registry（鎖定 revision、SHA-256 錯誤、非商用防呆）、手動遮罩端到端、備援一定標記人工檢查、`inpaint none`、示範動畫產生的 spec 通過驗證
- `@pytest.mark.ai`（約 45 秒，沒有模型時自動略過）：MI-GAN 補洞通過檢查；DINO → SAM 2.1 → PyMatting → MI-GAN 端到端
- `@pytest.mark.blender`：segment 的輸出交給 Stage 1 渲染，洞外和原圖單一圖層的渲染相同；分層渲染 = numpy 疊圖
- D1 / D3 的測試：alpha = 255 的像素保留原圖顏色；MI-GAN 失敗但備援成功（已修補）；MI-GAN 和備援都失敗（沒通過檢查 / 出錯兩種，`incomplete`、物件仍在背景上、結束碼 4）；沒有備援（`fallback: none`）；多個區域成功與失敗混合；`inpaint none` 也是 `incomplete`
- 審查修正的測試：不透明 RGBA 遮罩、透明 alpha 遮罩、遮罩尺寸錯誤 / 奇數補邊、細長物件的完美修補通過檢查、檢查失敗會改用備援、MI-GAN 上下文遮住其他未補的洞、零物件（結束碼 3）、`background_hole.png` 略過、失敗不留下 output、遮罩錯誤在建立任何檔案前就報錯、成功清除暫存 / `--keep-temp`、5 種錯誤設定預先報錯、`source_image` 指向存在的檔案、標籤比對、alpha 保留到 band、外擴帶保留寬度、matting tile 覆蓋、tile 與不分 tile 結果相同（@ai）、每個登記檔案都驗證雜湊、大小和修改時間相同的竄改也偵測得到、快速狀態不宣稱已驗證、子行程一律離線、requirements 檔是 ASCII

### 審查修正的量測（2026-11-04）

| 項目 | 修正前 | 修正後 |
|---|---|---|
| PyMatting 記憶體（3840×2160、物件 60%） | working set 2.6 GB | 0.8 GB（tile；1024×768 ~ 4K 都在 0.26~0.8 GB；alpha 和不分 tile 的結果最多差 1） |
| 毛髮 / 細毛的 alpha（狗、貓、人物頭髮） | 精修帶的 alpha 被截掉 25~32%（頭髮邊緣變成硬邊、狗尾巴的毛尖不見） | 0%（alpha 保留到 band_px = 8） |
| 背景殘留的物件邊緣（物件移開後的殘影） | 精修帶 alpha 的 16~24% 留在背景上 | < 0.1%（外擴帶保留寬度 = max(3, band_px)） |

量測腳本：`scripts/s2_1_refine_mem.py`、`scripts/s2_1_alpha_edge.py`（比較圖在 `output/s2-1-alpha-edge/`）。

### 還沒有完成 / 已知限制

| 項目 | 狀態 |
|---|---|
| `animator layers rebuild`（修改遮罩後重建，不重跑 AI） | ❌ 還沒做；暫時的做法：把修改後的物件遮罩（不含 `background_hole.png`）放到另一個資料夾，用 `segment --masks <資料夾>` 建立新的 job |
| 影子、倒影 | ❌ 不會被切掉，物件移開後影子留在背景上（可以在標籤加 `shadow`，未驗證） |
| 遮擋排序 | ⚠ 只用「面積小的在前面」，沒有深度資訊；例如狗擋住貓時，z 可能相反（重疊像素很少時影響不大） |
| 被遮住的部分 | ❌ 不會補回（沒有 amodal 補全）；後面的物件移開時會看到缺口 |
| 大洞（≥ 15%） | ⚠ **會修補**（MI-GAN，通過自動檢查時 `repaired: true`），但一律標記 `needs_review`，需要人工檢查畫面是否可用（S2-0.1 的大矩形實測常會補出不存在的物件） |
| 大圖的大遮罩 | ⚠ MI-GAN 會把裁切區縮到 512，補出來的區域比周圍模糊 |
| Stage 1 渲染的取樣濾波 | ✅ 已修正（Stage 1 獨立的修改：`filter_size = 0`，見 docs/stage1.md 第 4 節）；**不使用 Closest**（A/B 實驗證明會造成鋸齒） |
| 大物件的 hole check | ⚠ `target_changed_ratio` 只看物件本身的像素；如果修補結果剛好和物件原本的顏色相同（例如物件和背景同色），檢查會失敗而改用備援 |
| 已提交的 requirements 檔 | ⚠ `requirements.txt`（Stage 1）和 `scripts/s2_0/requirements-s2-0.txt`（S2-0）有中文註解，在繁中 Windows 上 `pip install -r` 會失敗（cp950 解碼錯誤），要設定 `PYTHONUTF8=1` 才能用；沒有修改（需要你同意） |
| GPU | ⚪ 沒有使用（只支援 CPU） |
| `doctor` | 會顯示 AI 套件和模型狀態 |

### S2-2 M4：物件 ↔ 補洞區域的關聯（`segmentation.json` 的 `associations`）

`animator segment`（AI 模式和 `--masks` 模式相同）在原始遮罩和補洞區域遮罩都還在的時候，記錄它們的關係；只寫在 `segmentation/segmentation.json`，`spec.json` 不變（`spec.meta.segmentation` 指向這個檔案）。

```json
"associations": {
  "version": 1,
  "regions":  { "0": ["dog-1", "cat-1"] },
  "overlaps": [ { "behind": "dog-1", "front": "cat-1", "px": 18 } ]
}
```

- `regions`：補洞區域的 `index`（對應 `background.regions[].index`）→ 原始遮罩和這個區域有像素重疊的物件。一個區域可以屬於多個物件（外擴後連在一起的遮罩會合成同一個區域）；不連續的遮罩可能屬於多個區域。
- `overlaps`：兩個物件的原始遮罩實際重疊的像素數；`behind` / `front` 是自動排序的結果（面積大的在後面）。
- 座標系統和其他分割資料相同（畫布像素，原點左上）。用實際的遮罩像素計算，不是用 bounding box。
- **比較舊的 job 沒有 `associations`**：`animator anim check` 只會說「關聯不明」，不會從遮罩或透明 PNG 推測，也沒有轉換指令。舊 job 的區域也可能沒有 `repaired` 欄位，這時不假設已經修補。

用途：`animator anim check <job>`（以及 `anim add`、`layers z`、`layers pivot` 成功之後）會提醒哪些動畫可能露出修補過的背景、哪些自動排序的圖層可能前後順序不確定；只是提醒，不會擋住編輯或渲染（`incomplete` 的 job 照樣可以渲染，和以前一樣）。

## 0. 審查決定（2026-11-03）與 S2-0 範圍

| 項目 | 決定 |
|---|---|
| Grounding DINO Tiny | 使用 IDEA-Research 官方的 Hugging Face 模型 `IDEA-Research/grounding-dino-tiny` |
| SAM 2.1 Tiny | 優先使用官方的 `facebook/sam2.1-hiera-tiny`，**不使用未驗證的社群權重** |
| PyMatting | 保留，用在有 trimap 的 alpha matting |
| LaMa | 授權確認之前，**不作為正式預設**；S2-0 **不測試**（避免額外安裝 onnxruntime） |
| OpenCV | 保留，作為小範圍修補的 fallback；S2-0 會一起測試 |
| 推論裝置 | **只用 CPU**；**不安裝 CUDA PyTorch**。所以 S2-0 **不會**得到 MX450 GPU 的實測數據，也不會因為權重檔小於 2 GB 就推定能在 GPU 上跑 |
| 測量項目 | 每個模型的 RAM 峰值、VRAM（確認沒有使用）、import 時間、載入時間、512 px / 1024 px 推論時間、釋放後的記憶體 |
| 載入方式 | 每個模型在**獨立的 process** 裡測試；另外做一次「依序載入 → 釋放 → 再載入下一個」的測試 |
| 平台 | Windows 原生 Python 3.12（`.venv`），不使用 WSL |
| 安裝位置 | 套件只裝在 `.venv`；模型只下載到專案的 `models/`（`HF_HOME` 也指向 `models/`），**只下載 safetensors**，不下載重複的 `.bin` |
| 測試素材 | CC0 公共素材：插畫（背景、太陽、雲、人物）、照片（人物 + 多個物件）、物件互相遮擋的圖片 |
| 不做的事 | 完整的分層流程、S2-1 以後的項目、修改 Stage 1 |

### S2-0 最小依賴（安裝前用 `pip install --dry-run` 解析出的版本）

| 直接安裝 | 版本 | 授權 | 用途 |
|---|---|---|---|
| torch（CPU 版，從 `download.pytorch.org/whl/cpu` 安裝） | 2.15.0+cpu | BSD-3 | 執行 DINO / SAM 2 |
| transformers | 5.22.1 | Apache-2.0 | DINO / SAM 2 的實作；會連帶安裝 huggingface_hub 2.4.0 |
| pillow | 13.0.0 | MIT-CMU | 讀寫圖片 |
| pymatting | 1.1.16 | MIT | alpha matting；會連帶安裝 numba 0.68.1、llvmlite 0.50.0、scipy 1.18.1 |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | trimap 形態學處理、Telea inpainting |

會連帶安裝的套件（沒辦法避免）：numpy、safetensors、tokenizers、regex、tqdm、hf-xet、httpx2 / httpcore2（huggingface_hub 2.x 的 HTTP client，來源是 pydantic/httpx2）、typer / rich / click（huggingface_hub CLI 用）、sympy、networkx、jinja2、fsspec、filelock 等。
**不安裝**：psutil（記憶體改用 Windows API 測量）、onnxruntime、rembg、CUDA 版的 torch。

> **追加（經核准）**：transformers 5.22.1 的 `Sam2ImageProcessor` 必須搭配 **torchvision**，所以另外安裝了 `torchvision==0.29.2+cpu`。安裝前先 dry-run，確認只會新增這一個套件，不會升級或降級任何已安裝的套件。

---

## S2-0 結果（2026-11-03 實測）

測試程式：`scripts/s2_0/`（`fetch_models.py`、`prepare_images.py`、`bench.py`、`memprobe.py`、`summarize.py`、`check_sam2_load.py`）。測試圖片和授權見 `assets/s2-0/SOURCES.md`。原始數據和視覺化結果在 `output/s2-0/`（不納入 Git）。

### R1. 測試環境

| 項目 | 值 |
|---|---|
| CPU | Intel Core i7-1165G7（4 核 8 緒），PyTorch 使用 4 個執行緒 |
| RAM | 總共 15.7 GB；**測試期間只剩 2.6~3.2 GB 可用**（其他程式佔用），這可能是時間波動的原因之一 |
| GPU | MX450 **沒有使用**（CPU 版 PyTorch）：每個 worker 執行期間 nvidia-smi 都顯示 **0 MB** |
| Python | Windows 原生 3.12.1（`.venv`），沒有使用 WSL |
| 執行方式 | 每個模型在**獨立的 process** 中執行，並設定 `HF_HUB_OFFLINE=1`（完全離線） |

### R2. 實際安裝的套件（`.venv`，site-packages 共 1,244 MB）

| 套件 | 版本 | 安裝後大小 |
|---|---|---|
| torch | 2.15.0+cpu | 514 MB |
| torchvision | 0.29.2+cpu | 7 MB |
| transformers | 5.22.1 | 104 MB |
| huggingface_hub / tokenizers / safetensors | 2.4.0 / 0.23.3 / 0.8.0 | 約 23 MB |
| opencv-python-headless | 5.0.0.93 | 112 MB |
| pymatting / numba / llvmlite | 1.1.16 / 0.68.1 / 0.50.0 | 約 145 MB |
| scipy / numpy / pillow | 1.18.1 / 2.5.4 / 13.0.0 | 約 155 MB |

版本鎖定在 `scripts/s2_0/requirements-s2-0.txt`。系統 Python 和 Blender Python 都沒有安裝任何套件。

### R3. 模型下載（`models/`，鎖定 revision，只下載 safetensors）

| 模型 | Repo @ revision | 下載大小 | 排除的重複權重 |
|---|---|---|---|
| Grounding DINO Tiny | `IDEA-Research/grounding-dino-tiny` @ `a2bb814` | **690.3 MB** | `pytorch_model.bin`（691.9 MB） |
| SAM 2.1 Hiera-Tiny | `facebook/sam2.1-hiera-tiny` @ `de431c4`（官方 repo，2025-08 起提供 Transformers 格式） | **155.9 MB** | `sam2.1_hiera_tiny.pt`（156.0 MB） |
| 合計 | | **846 MB** | 每個檔案的 SHA-256 記錄在 `models/s2-0-manifest.json` |

- 官方的 SAM 2.1 權重載入 `Sam2Model` 時：missing / unexpected / mismatched keys 都是 **0**，參數 31.44M（只使用影像部分）。
- 使用者家目錄的 `~/.cache/huggingface`、`~/.cache/torch` **都不存在**：所有快取都在 `models/` 底下。

### R4. CPU 推論結果（每個模型在獨立 process 中；時間單位為秒）

| 模型 | import | 載入 | 512 px | 1024 px | RAM 峰值（WorkingSet / commit） | 釋放後 |
|---|---|---|---|---|---|---|
| Grounding DINO Tiny | 11.5~12.7 | 2.1~2.8 | **3.5~6.1**（一次離群值 13.8） | **14.9~24.4**（一次離群值 44.5） | 512：1.40 / 2.0 GB；1024：**2.2 / 3.0 GB** | WS 從 1.1 GB 降到 0.45 GB |
| SAM 2.1 Tiny | 20~40 | 1.1~1.6 | **10.8~12.6** | **11.0~13.0** | **1.0 / 1.7 GB** | WS 從 0.68 GB 降到 0.57 GB |
| PyMatting（每個物件） | **第一次 292**（numba 編譯）；有快取後 2.7 | — | 0.07~0.25 | 0.44~1.29 | 0.58 GB / commit 4.4~4.8 GB ⚠ | — |
| OpenCV Telea | 0.05 | — | 0.02~0.10 | 0.10~0.39 | < 0.07 GB | — |

- **SAM 2 的 image encoder 固定約 10 秒**，因為它在內部一律縮放成 1024×1024，所以**輸入 512 或 1024 的成本相同**；decoder 每個框約 0.3~0.45 秒（2~8 個框共 0.8~2.4 秒）；放大回原尺寸 ≤ 0.32 秒。
- **DINO 的成本隨輸入尺寸增加**（512 → 1024 大約 3~4 倍），而且同一台電腦上前後兩次執行的波動很大（受系統負載影響）。
- **PyMatting**：第一次 import 時 numba 要編譯約 **5 分鐘**（只有第一次；快取 2.7 MB 存在 `models/.numba_cache`）。commit 峰值在 4.4~4.8 GB，但實際佔用的 WorkingSet 只有 0.58 GB，推測是 numba runtime 預先保留的空間；分頁檔很小的電腦要注意。
- **依序載入測試**（同一個 process）：DINO 載入加推論，WS 峰值 2.0 GB → 釋放後降到 0.43 GB；接著 SAM 2，峰值 0.96 GB → 釋放後 0.61 GB。**記憶體會回收**，但 PyTorch allocator 會保留約 0.25~0.6 GB → 正式實作建議**每個模型用獨立的 subprocess**，才能完全釋放。
- **每張 1024 px 圖片的合計時間（CPU，模型已經載入）**：DINO 15~24 + SAM 2 11~13 + matting 約 1 × 物件數 + OpenCV 0.4 ≈ **30~40 秒**；第一次還要加上 import 和載入 30~60 秒。512 px 約 17~20 秒（SAM 2 的成本是固定的）。**比原本估計的 10~30 秒慢。**
- **VRAM**：全程 0 MB（只用 CPU）。MX450 上的 GPU 可行性**沒有實測**（依照指示沒有安裝 CUDA PyTorch），所以**不能**推論它在 2 GB VRAM 上能不能跑。

### R5. 品質觀察（視覺化結果在 `output/s2-0/gdino`、`sam2`、`pymatting`、`opencv`）

| 項目 | 結果 |
|---|---|
| DINO（照片） | person 0.90、laptop 0.88、computer mouse 0.94、potted plant 0.79、notebook 0.73、phone 0.66 → **良好** |
| DINO（遮擋） | 1024 px：dog 0.85、cat 0.84 → 良好；**512 px 時把兩個標籤合併成 "dog cat"** |
| DINO（插畫） | sun、tent、tree、girl 都能偵測到；**cloud 在 512 px 有偵測到（0.44），1024 px 卻低於門檻**；dog 在 512 px 漏掉；picnic 的 basket 兩種尺寸都漏掉 |
| DINO 的問題 | 多個標籤放在同一段 prompt 裡時會**合併標籤**（"dog cat"、"notebook phone"）；門檻要依圖片風格調整 → 正式實作建議**每個標籤分開查詢**，或加上後處理 |
| SAM 2（照片 / 遮擋） | 人物（含頭髮）、筆電、盆栽、手機、滑鼠、筆記本的遮罩都很乾淨；**貓被狗擋住的部分正確地被排除**（不會補回被遮住的部分，和預期相同） |
| SAM 2（插畫） | 太陽、樹、女孩、狗都切得出來；細線（狗繩）和女孩的一部分腳**不完整** |
| PyMatting | 頭髮、狗毛的邊緣自然；但**比 trimap 邊緣帶還細的結構（例如手臂）會整條變成半透明** → 邊緣帶的寬度要依結構粗細調整（例如用 distance transform 決定） |
| OpenCV Telea | 小洞可以補；**大洞（人物、狗、帳篷）會糊成條紋和色塊，不能用** → 只適合當作小範圍的 fallback |
| 測試程式的限制 | 為了控制時間，`bench.py` 每個標籤最多保留 2 個遮罩，所以 picnic 的 4 個人只切了 2 個（這是測試程式的設定，不是模型的限制） |

### R6. Windows 相容性

| 項目 | 結果 |
|---|---|
| 安裝 | ✅ 全部都是 cp312 win_amd64 或純 Python 的 wheel，**不需要編譯器、不需要 WSL** |
| 模型執行 | ✅ HF Transformers 的 Grounding DINO / SAM 2 在 Windows CPU 上可以正常推論 |
| 離線 | ✅ `HF_HUB_OFFLINE=1` + `local_files_only=True` 執行成功 |
| 路徑 | ✅ 用 `local_dir` 下載，沒有 symlink 問題；家目錄沒有快取 |
| 注意 1 | SAM 2 的 processor 需要 **torchvision**（已核准並安裝） |
| 注意 2 | 第一次 import numba 要編譯約 5 分鐘 |
| 注意 3 | transformers 會警告 `sam2_video` → `sam2`（已確認沒有缺漏的權重，可以忽略）；DINO 有 `labels`→`text_labels` 的 FutureWarning |
| 注意 4 | 系統 Python 3.12.1 的 `urllib` 下載 Wikimedia 時出現 SSL 憑證過期錯誤（只影響下載素材，和模型執行無關；改用 Windows curl）。正式下載模型使用 huggingface_hub，沒有這個問題 |
| 注意 5 | Stage 1 的 `test_no_machine_specific_paths_in_code` 會把 `https://` 誤判成磁碟代號（`s:/`）→ S2-0 的程式避開了網址字串，**沒有修改 Stage 1**；之後要修正這個測試的 regex |

### R7. 授權來源

| 項目 | 授權 | 來源 |
|---|---|---|
| Grounding DINO Tiny（權重） | Apache-2.0 | [HF 模型卡 metadata](https://huggingface.co/IDEA-Research/grounding-dino-tiny)（revision `a2bb814`）；程式碼 [IDEA-Research/GroundingDINO](https://github.com/IDEA-Research/GroundingDINO) |
| SAM 2.1 Hiera-Tiny（權重） | Apache-2.0 | [HF 模型卡 metadata](https://huggingface.co/facebook/sam2.1-hiera-tiny)（revision `de431c4`）；程式碼 [facebookresearch/sam2](https://github.com/facebookresearch/sam2) |
| PyMatting | MIT | [pymatting/pymatting](https://github.com/pymatting/pymatting) |
| OpenCV（opencv-python-headless） | Apache-2.0 | [opencv/opencv-python](https://github.com/opencv/opencv-python) |
| PyTorch / torchvision | BSD-3-Clause | [pytorch/pytorch](https://github.com/pytorch/pytorch)、[pytorch/vision](https://github.com/pytorch/vision) |
| Transformers | Apache-2.0 | [huggingface/transformers](https://github.com/huggingface/transformers) |
| 測試圖片 | 照片 2 張：CC0 1.0（Commons API 確認，已納入 Git）；插畫 2 張：Openverse 標示 CC0，待人工確認，**未納入 Git** | `assets/s2-0/SOURCES.md` |

模型的授權是從 HF API 的 `card_data.license` 讀取的，不是法律審查的結果。

### R8. 結論：是否繼續採用這套架構

**建議：繼續採用 Grounding DINO + SAM 2.1 + PyMatting 作為分層的核心，但開始 S2-1 之前要先解決「大面積背景修補」的問題。**

| 面向 | 判定 |
|---|---|
| CPU 可行性 | ✅ 每張 1024 px 圖片 30~40 秒，RAM 峰值 2.2 GB（WS）/ 3.0 GB（commit），依序載入可以控制在這個範圍。適合離線批次處理，不適合即時互動 |
| 分層品質 | ✅ 照片和遮擋的情況良好；插畫需要調整門檻，並且每個標籤分開查詢 |
| 邊緣品質 | ✅ PyMatting 很快（每個物件 ≤ 1.3 秒）；trimap 要改成依結構粗細調整 |
| **背景修補** | ❌ **只靠 OpenCV 不能接受**：物件稍微大一點就會留下明顯的色塊。LaMa 的授權還沒確認 → 需要決定：(a) 確認 LaMa 的授權、(b) 評估 MI-GAN（MIT）、(c) 先接受「大洞用人工修補」 |
| GPU | ⚪ 沒有實測。CPU 版已經能用；如果需要加速，要另外核准安裝 CUDA PyTorch（約 2.5 GB）再測試 |
| 架構調整建議 | 每個模型用獨立的 subprocess（可以完全釋放記憶體）；DINO 每個標籤分開查詢；SAM 2 的 encoder 成本固定，所以縮小圖片不會變快，但同一張圖的 embedding 可以重複使用；至少要有 4 GB 的可用 RAM |

---

## S2-0.1 結果：MI-GAN 背景修補（2026-11-04 實測，等待審查）

範圍：只測 MI-GAN 官方 ONNX pipeline 的大面積背景修補；沒有實作 Stage 2 流程、沒有修改 Stage 1、沒有修改 S2-0 的結果（`output/s2-0/` 89 個檔案的 SHA-256 前後一致）。
測試程式：`scripts/s2_0_1/`（`fetch_migan.py`、`bench01.py`、`mem_single.py`）。原始數據和比較圖在 `output/s2-0.1/`（`results.json`、`mem_single.json`、`grids/*.jpg`，不納入 Git）。

### M1. 套件與模型

| 項目 | 內容 |
|---|---|
| 新增套件（`.venv`，`--only-binary=:all: --no-cache-dir`） | onnxruntime 1.31.0（MIT，CPU 版，48 MB）、protobuf 7.36.2（BSD-3，2.2 MB）、flatbuffers 25.12.19（Apache-2.0，0.3 MB）。安裝前後 `pip freeze` 比對：只多了這 3 個，既有套件沒有變動 |
| 執行環境 | `CPUExecutionProvider`（onnxruntime 內建的 AzureExecutionProvider 沒有使用）；4 個 intra-op threads；`HF_HUB_OFFLINE=1`；GPU 使用量 0 MB |
| 模型 | `andraniksargsyan/migan` 的 `migan_pipeline_v2.onnx`，鎖定 revision `406830d0fa60666da0071c342ad2fbc8f30c5c64`；28,079,181 bytes（26.8 MB）；SHA-256 `6f1f3530…8c40b` **完全相符** |
| 授權 | 官方 repo `Picsart-AI-Research/MI-GAN`（commit `2b793c5`）的 `LICENSE-WEIGHTS` = MIT（和程式碼的 `LICENSE` 逐字相同，Copyright (c) 2024 Picsart AI Research (PAIR)）；HF repo 的 LICENSE 同為 MIT / PAIR。ONNX 檔是官方 README 連結的版本（論文第一作者的帳號）。不是法律審查 |
| 模型介面 | 輸入 `image` uint8 (1,3,H,W)、`mask` uint8 (1,1,H,W)，**255 = 已知、0 = 要補**；輸出 uint8 (1,3,H,W)。任意尺寸，內部會依遮罩裁切後縮放到 512×512 |

### M2. 測試內容

- 圖片：4 張（兩張插畫只在本機使用，不納入 Git），長邊 512 / 1024，兩張照片另外跑原始 1920 px。
- 遮罩：置中矩形 5 / 15 / 30 / 50%；不規則筆刷約 10 / 25 / 40%（固定 seed）；S2-0 的 SAM 2.1 物件遮罩外擴 8 / 24 px（人物、狗、女孩 + 狗）。共 90 組。
- 輸入的三種變體：(a) hole 塗滿洋紅色（極性 / 滲漏檢查用）、(b) hole 保留原圖內容（實際用途：要移除的物件還在圖上）、(c) hole 先用 OpenCV Telea 預填。
- 比較對象：OpenCV Telea / Navier-Stokes（半徑 3）。

### M3. 速度與記憶體（CPU，i7-1165G7）

| 項目 | MI-GAN | OpenCV Telea |
|---|---|---|
| import / 建立 session | 0.2 s / 0.5 s | — |
| 每次推論（warm，中位數） | 512：約 0.95 s；1024：1.1~1.3 s；1920：1.2~1.4 s | 512：0.01~0.11 s；1024：0.05~0.53 s；1920：0.2~1.8 s |
| 第一次推論 | 0.9~1.0 s（arena 開）/ 1.4~2.0 s（arena 關） | — |
| RAM，單張圖獨立 process（arena 開） | WS 857 / 906 / 1,112 MB；commit 1.38 / 1.41 / 1.43 GB（512 / 1024 / 1920） | < 0.5 GB |
| RAM，單張圖獨立 process（arena 關） | WS 497 / 526 / 609 MB；commit 0.69 / 0.73 / 0.81 GB | — |
| RAM，同一個 process 連跑 90 種尺寸 | commit 最高 2.6 GB（arena 依輸入尺寸累積，不會自動釋放） | — |

- 推論時間幾乎不受圖片尺寸影響，因為模型固定在 512×512 上運算。
- 關掉 `enable_cpu_mem_arena` 之後記憶體減半，warm 速度不變，只有第一次推論慢約 0.5~0.9 s。

### M4. 遮罩極性與合成（全部 270 次 MI-GAN 推論）

| 檢查 | 結果 |
|---|---|
| 極性 | ✅ 255 = 已知、0 = 要補，和官方 README 一致。故意反轉遮罩時：hole 內 100% 保留洋紅色、已知區域 100% 被改寫 → 很容易偵測，正式流程應該加上這個檢查 |
| 輸出尺寸 | ✅ 全部和輸入相同 |
| 已知區域 | ✅ 離 hole 超過 1.4 px 的像素完全沒有變（最大差異 0）；pipeline 會在 hole 外側做 1~2 px 的混合帶 |
| hole 內容滲漏 | ⚠ pipeline 會讀取 hole 最外圈 1~2 px 的輸入內容，而且裁切區縮小時（1920 px 大遮罩縮到 0.27 倍）會再擴散。**hole 塗成純色（洋紅）時，整個補出來的區域都被染色**，最深到離邊界 57 px。改用原圖內容或 Telea 預填時，洋紅色像素只剩 15~17 個（原圖本身同位置的基準是 1,534 個）→ 沒有滲漏；兩者的結果幾乎一樣 |

結論：**輸入要用原圖（或 Telea 預填），不能先把 hole 塗成單一顏色**；物件遮罩要外擴（8 px 以上），讓物件邊緣不會被讀進混合帶。

### M5. 品質（比較圖：`output/s2-0.1/grids/`，欄位：輸入 | MI-GAN 洋紅輸入 | MI-GAN 原圖輸入 | MI-GAN 預填 | Telea | NS）

hole 內和原圖的 PSNR（dB，越高越接近原圖；1024 px，4 張圖平均；MI-GAN 用預填輸入）：

| 遮罩 | rect_05 | rect_15 | rect_30 | rect_50 | free_s | free_m | free_l |
|---|---|---|---|---|---|---|---|
| MI-GAN | 14.2 | 11.7 | 10.9 | 10.8 | **18.1** | **15.7** | **16.5** |
| Telea | 13.4 | 11.9 | 11.8 | 12.1 | 15.9 | 15.1 | 15.8 |

PSNR 對「模糊的平均色」比較有利，大洞時不能代表視覺品質，所以以比較圖為主：

| 情況 | MI-GAN | OpenCV |
|---|---|---|
| 移除物件（obj_d8 / d24） | ✅ 狗移除後地面幾乎看不出痕跡；插畫的女孩和狗移除後草地 / 背景自然；照片移除人物後補出合理的椅子 / 書架（細看有些變形） | ❌ 明顯的放射狀色塊 |
| 不規則筆刷（10~40%） | ✅ 照片和插畫都很自然，明顯比 OpenCV 好 | ❌ 塊狀拉絲 |
| 小矩形（5%） | ✅ 可以接受 | ⚠ 模糊 |
| 大矩形（15~50%） | ❌ 會「發明」不存在的物件（人臉、彩色色塊），插畫上更明顯 | ❌ 放射狀模糊 |
| 解析度 | ⚠ 大遮罩在 1920 px 時裁切區縮到 0.27~0.33 倍再放大，補出來的區域比周圍模糊 | — |

### M6. 結論：MI-GAN 是否可行

**建議：採用 MI-GAN（MIT）作為預設的背景修補，取代 OpenCV；LaMa 目前不需要。** 但要搭配下面的限制：

| 面向 | 判定 |
|---|---|
| 授權 | ✅ 程式碼與權重都是 MIT（官方 `LICENSE-WEIGHTS`） |
| CPU / Windows / 離線 | ✅ 原生 Windows、純 wheel、只用 CPU；每張約 1~1.5 秒、RAM 0.5~1.1 GB，比 S2-0 的分割模型輕很多 |
| 移除物件後補背景 | ✅ 這是 Stage 2 的主要用途，品質明顯比 OpenCV 好 |
| 很大的洞（物件占畫面 15% 以上、或接近矩形的大區塊） | ❌ 兩種方法都不行 → 應該標記為「需要人工修補」，或是保留原圖背景不移除 |
| 正式流程需要的規則 | 輸入用原圖、不要塗純色；遮罩外擴 ≥ 8 px；檢查遮罩極性（255 = 已知）；每次推論用獨立 process，或關掉 memory arena；大圖的大遮罩要注意解析度損失（可以考慮分區處理，留到 S2-1 設計） |

**目標**：輸入一張普通圖片 →（1）辨識背景、人物、物件 →（2）每個元素輸出成一張透明 PNG →（3）盡量保留原圖的外觀、顏色與邊緣 →（4）移除物件後，用 inpainting 補好背景 →（5）輸出的結果可以直接交給 Stage 1 的 `spec.json` → Blender 流程渲染。

**硬體限制**：NVIDIA MX450（2 GB VRAM）、Windows 11、Python 3.12、Blender 5.2.3。

---

## 1. 結論（建議方案）

> **Grounding DINO（tiny）用文字找出元素 → SAM 2.1（tiny）切出精準遮罩 → 邊緣精修與去色邊 → LaMa（ONNX）補背景**
> 預設用 **CPU**，可以選擇改用 CUDA；全部**離線**執行，所有模型都採 **Apache-2.0 / MIT** 授權。

| 步驟 | 選用 | 授權 | 替代 / 備援 |
|---|---|---|---|
| 找出元素（帶標籤） | Grounding DINO tiny（`IDEA-Research/grounding-dino-tiny`，透過 HF Transformers） | Apache-2.0 | OWLv2（Apache-2.0）、Florence-2（MIT） |
| 切出遮罩 | SAM 2.1 Hiera-Tiny（透過 HF Transformers；可升級為 Small） | Apache-2.0 | rembg（isnet / BiRefNet），適用於只有單一主體的情況 |
| 邊緣精修 | trimap + pymatting（alpha 精修 + 前景色估計，去除色邊） | MIT | 不精修（直接用 SAM 遮罩加羽化） |
| 背景修補 | LaMa（big-lama，ONNX 512×512，ONNX Runtime CPU） | Apache-2.0 | OpenCV Telea（不需要模型，只適合小洞）、MI-GAN（MIT，待評估） |

**為什麼選這個組合**
1. **可以辨識多個元素，而且有語意標籤**：DINO 負責「找出什麼東西、在哪裡」，SAM 2 負責「切出精確的輪廓」。這是目前開源界最成熟的組合（Grounded-SAM-2）。標籤可以直接寫進 `layers[].meta.label`，Stage 4 的自然語言規劃（例如「小鳥」→ `bird`）也能直接對應。
2. **小模型就夠用**：三個模型加起來大約 1 GB。CPU 能跑，2 GB VRAM 也放得下（一次只載入一個）。
3. **授權乾淨**：全部都是 Apache-2.0 / MIT，可以商業使用（但要注意第 5 節提到的訓練資料問題）。
4. **在 Windows 上好安裝**：用 HF Transformers 的實作，**不需要**編譯 CUDA extension（原版 Grounding DINO 和 SAM 2 的 repo 在 Windows 上很難編譯）。LaMa 用 ONNX Runtime 執行，不需要額外安裝 LaMa 原始專案的依賴。
5. **每個步驟都可以替換**：detector、segmenter、refiner、inpainter 是四個獨立的介面（見第 3 節）。

---

## 2. 技術評估

### 2.1 各方案的能力比較

| 方案 | 能做到什麼 | 多元素 + 標籤 | 邊緣品質 | 背景修補 | 主要問題 | 判定 |
|---|---|---|---|---|---|---|
| **rembg** | 去背：只抓出「最顯著的那一個主體」 | ❌ 只能輸出一個前景遮罩 | ◎（isnet / BiRefNet 對頭髮的表現很好） | ❌ | 沒辦法把多個物件分開；而且會連帶安裝 scikit-image、pooch 等一大串依賴 | **備援 backend**：只有單一主體時使用（Stage 2 先不做，介面會預留） |
| **SAM / SAM 2** | 給一個點或框，就能切出精準遮罩；也有「全部切」模式 | ⚠ 能切，但**不知道切出來的是什麼** | ○（遮罩是二值的，頭髮一般） | ❌ | 「全部切」模式會切得太碎；在 CPU 上用 32×32 格點提示要好幾分鐘 | **採用**（使用框提示） |
| **Grounding DINO** | 用文字找出物件的框（open-vocabulary） | ✅ 標籤 + 框 | —（只輸出框） | ❌ | 只能輸出框；文字提示要用英文；原版需要編譯 CUDA ops（HF 版不需要） | **採用** |
| **OpenCV Inpainting**（Telea / NS） | 從洞的邊緣往內擴散補色 | — | — | △ 只適合細線和小洞，大洞會糊成一片 | 補不出紋理 | **備援**（沒有模型時，或洞很小時） |
| **LaMa** | 用快速傅立葉卷積補大面積的洞，可以延續原本的紋理 | — | — | ◎ 大洞也能補出合理的紋理 | 固定 512 輸入，大圖要裁切處理；沒辦法「想像」出複雜的結構（例如被遮住的臉） | **採用** |
| SAM 3 | 用文字提示做概念分割，一步完成 | ✅ | ◎ | ❌ | 約 840M 參數（fp32 約 3.4 GB）：**放不進 2 GB VRAM**，CPU 很慢。授權是 Meta 自訂的 SAM License（可以商用，但有用途限制，而且 Meta 可以修改條款） | **暫不採用**（硬體升級後可以列為替代 backend） |
| Florence-2 | 偵測、caption、grounding 都能做 | ✅（自動列出物件，不需要提示） | — | ❌ | 部分版本需要 `trust_remote_code`（執行下載下來的程式碼，有安全疑慮）；open-vocab grounding 比 DINO 弱 | **未來的「全自動模式」候選** |
| OWLv2 | open-vocab 偵測 | ✅ | — | ❌ | 實測上 grounding 品質一般比 DINO 差 | 替代 detector |
| YOLO-World / Ultralytics YOLO-seg | 快速偵測 + 分割 | ✅ | ○ | ❌ | **AGPL-3.0**：商業產品要開放原始碼或購買授權 | ❌ 不採用 |
| BRIA RMBG 1.4 / 2.0 | 高品質去背 | ❌ | ◎ | ❌ | **不能商用**（CC BY-NC / 要另外簽商業合約） | ❌ 不採用 |
| Stable Diffusion inpainting | 能補出有語意的內容 | — | — | ◎（可以「生成」被遮住的內容） | 2 GB VRAM 或 CPU 要跑好幾分鐘；OpenRAIL-M 授權有用途限制；結果不穩定 | ❌ 不採用（Stage 5 以後再評估） |
| MI-GAN | 輕量 inpainting（約 30 MB） | — | — | ○ 速度快，品質稍遜於 LaMa | 只支援 512 | 列入 S2-0 的評估名單 |
| Depth Anything V2 **Small** | 單張圖片估計深度 | — | — | — | 只有 Small 是 Apache-2.0（Base / Large 是 CC-BY-NC） | 未來用來**自動決定 z 順序**（選用） |

### 2.2 硬體、速度、大小（⚠ 都是估計值，會在 S2-0 實測後更新）

以 1024 px 長邊的圖片、筆電 4 核 CPU 估計：

| 模型 | 參數 / 檔案大小 | CPU 執行時間 | RAM 峰值 | 2 GB VRAM 放得下？ |
|---|---|---|---|---|
| Grounding DINO tiny | 約 172M / 約 690 MB（fp32） | 3~8 秒 | 約 1.5 GB | ✅ 用 fp16 約 0.5 GB 權重 + 推論過程的暫存 |
| SAM 2.1 Hiera-Tiny | 約 39M / 約 156 MB | 1~3 秒（encoder）+ 每個物件約 0.1 秒 | 約 0.8 GB | ✅ |
| SAM 2.1 Hiera-Small | 約 46M / 約 185 MB | 2~4 秒 | 約 1 GB | ✅ |
| SAM 2.1 Large | 約 224M / 約 900 MB | 8~15 秒 | 約 3 GB | ⚠ 很勉強 |
| LaMa big-lama（ONNX） | 約 51M / 約 208 MB | 每個 512 區塊 1~3 秒 | 約 1 GB | ✅（Stage 2 先用 CPU 跑） |
| pymatting（只處理邊緣帶） | 沒有模型 | 1~5 秒（第一次執行時 numba 要先編譯，大約 20~40 秒） | 約 1 GB | 不使用 GPU |
| SAM 3 | 約 840M / 約 3.4 GB | 估計超過 15 秒 | 超過 5 GB | ❌ |
| BiRefNet（1024，fp32） | 約 220M / 約 900 MB | 10 秒以上 | 約 4 GB | ❌（lite 版勉強可以） |

- **每張圖片全程（CPU）約 10~30 秒**，加上第一次載入模型的 5~15 秒。
- **模型依序載入、用完就釋放**：RAM 峰值大約 2~3 GB；VRAM 峰值低於 1.5 GB。
- **只用 CPU 可以嗎？可以。** 所有模型都有 CPU 執行路徑，這也是**預設值**：每台電腦都能跑，部署最簡單。
- **GPU（MX450）**：需要 CUDA 版的 PyTorch（下載約 2.5 GB，CPU 版大約 200 MB），速度估計是 CPU 的 2~4 倍。設定 `ai.device: auto` 時，如果 CUDA 可用就改用 fp16；記憶體不夠（OOM）時，會自動退回 CPU 重跑該步驟。

### 2.3 網路與離線

- **執行時完全離線**：模型都存在 `paths.models_dir`，執行時會設定 `HF_HUB_OFFLINE=1`，保證**不會連網**，也**不使用任何雲端 API**。
- **只有第一次需要網路**：用 `animator models download` 從 Hugging Face 下載，並且鎖定 **revision（commit hash）+ SHA-256**，確保每台電腦拿到的模型完全相同。
- **完全不能連網的電腦**：把整個 `models/` 目錄複製過去，再執行 `animator models verify` 檢查 SHA-256。
- **一律不使用 `trust_remote_code`**：不執行任何從網路下載下來的程式碼。

---

## 3. 架構

### 3.1 位置與界線

```
                       系統 Python（.venv）                                 Blender（不需要修改）
┌───────────────────────────────────────────────────────────────────┐
│ animator segment --image x.png --labels "person, dog, sun"         │
│   └─ src/animator/segmentation/   ← Stage 2 新增，lazy import       │
│        detector → segmenter → refiner → compose → inpainter → export│
│                                                    │                │
│   輸出：output/<job_id>/  spec.json（v1，沒有動畫）+ layers/*.png  ──┼──▶  Stage 1：animator render
└───────────────────────────────────────────────────────────────────┘          （完全沒有修改）
```

- **和 Blender 完全分開**：AI 模組只在系統端執行，**不會碰 `blender/`**。Blender 端看到的就只是 PNG 圖層。
- **不改變 Stage 1 的介面**：輸出是標準的 **spec v1 + RGBA PNG**，`version` 不變，不新增必填欄位。AI 的資訊寫在已經存在的自由欄位 `layers[].meta` 和 `meta` 裡。`validate`、`render`、`new` 的行為都不變。
- **AI 依賴是選用的**：AI 套件只在呼叫 `animator segment` / `models` 時才 import（lazy import）。沒有安裝 AI 套件時，Stage 1 的所有指令照常可以用，會有測試檢查這一點。
- **可以替換模型**：四個介面各自用 registry 註冊，透過 `config.yaml` 的 `ai.*.backend` 或 CLI 參數切換。

### 3.2 目錄（新增的部分）

```
src/animator/segmentation/
├── __init__.py
├── types.py              # Detection、MaskResult、LayerCandidate、SegmentationResult（dataclass）
├── base.py               # Detector / Segmenter / Refiner / Inpainter 介面 + registry
├── pipeline.py           # 串接整個流程、決定處理解析度、依序載入和釋放模型
├── compose.py            # 遮罩後處理、重疊處理、z 排序、裁切成 RGBA、背景合成
├── export.py             # 寫出 job：layers/、segmentation/、spec.json
├── rebuild.py            # 人工修改遮罩之後重建圖層（不重跑 AI）
├── models.py             # 模型 registry：repo、revision、SHA-256、授權、下載/驗證
├── device.py             # 選擇 CPU/CUDA、fp16、OOM 時退回 CPU
└── backends/
    ├── manual_masks.py       # 使用者自己提供遮罩（不需要 AI）
    ├── grounding_dino.py     # detector
    ├── sam2.py               # segmenter
    ├── pymatting_refine.py   # refiner
    ├── lama_onnx.py          # inpainter
    └── opencv_inpaint.py     # inpainter（備援）
```

### 3.3 介面（草案）

```python
class Detector(Protocol):        # image: RGB uint8 (H,W,3)
    def detect(self, image, labels: list[str]) -> list[Detection]: ...      # label, score, box(xyxy, px)

class Segmenter(Protocol):
    def segment(self, image, boxes: list[Box]) -> list[np.ndarray]: ...     # 每個框一張 float32 (H,W) 0~1

class Refiner(Protocol):
    def refine(self, image, mask) -> tuple[np.ndarray, np.ndarray]: ...     # alpha (H,W)、去色邊後的前景 RGB

class Inpainter(Protocol):
    def inpaint(self, image, hole) -> np.ndarray: ...                       # 只重繪 hole 範圍內的像素
```

新增一種模型 = 新增一個 backend 檔案 + 在 `models.py` 登記它的權重 + 在 config 裡切換。

### 3.4 輸出格式（job 目錄）

```
output/<job_id>/
├── spec.json                  # 標準 v1：背景 z=0 + 每個元素一個圖層，animations: []
├── layers/
│   ├── background.png         # 原圖大小、不透明、已經補好洞
│   ├── person-1.png           # 依 bbox 裁切（外加 padding），RGBA
│   └── dog-1.png
└── segmentation/              # AI 的中間結果（人工修正和追溯用）
    ├── source.png             # 原圖（已處理 EXIF 方向）
    ├── masks/person-1.png     # 8-bit 灰階 alpha，原圖大小 ← 可以用 Krita/GIMP 修改
    ├── masks/background_hole.png
    ├── overview.png           # 用彩色遮罩 + 標籤疊在原圖上，方便快速檢查
    └── segmentation.json      # 偵測結果、分數、bbox、模型 id/revision、參數、執行時間
```

spec 裡的圖層長這樣：

```json
{ "id": "person-1", "image": "layers/person-1.png", "z": 20, "position": [812.5, 430.0],
  "meta": { "label": "person", "score": 0.83, "bbox": [640, 180, 985, 680],
            "source": "ai", "mask": "segmentation/masks/person-1.png" } }
```

- 裁切後的圖層 `anchor` 用預設的 `[0.5, 0.5]`，`position` = 裁切區域中心在原圖上的座標。所以**不加任何動畫時，渲染出來的畫面就是原圖**，旋轉和縮放也會以物件中心為 pivot。
- 原圖的長或寬是奇數時（Stage 1 要求偶數），在右邊或下邊補 1 px（複製邊緣像素），並記錄在 `segmentation.json`。

---

## 4. 資料流程

```
 1. 讀取圖片     EXIF 方向校正 → sRGB RGB；奇數尺寸補 1 px
 2. 縮小處理圖   長邊縮到 ai.max_side（預設 1024）只給模型用；原圖保留不動
 3. 找出元素     Grounding DINO(labels) → 框；門檻過濾、NMS、限制每個標籤的最大數量
 4. 切出遮罩     SAM 2.1（以框為提示）→ 把 logits 放大回原圖尺寸再取門檻 → 去掉小碎塊、補小洞
 5. 重疊與排序   重疊的像素歸給前面的圖層；z：背景 0，物件預設「面積小的在前面」
 6. 邊緣精修     依遮罩建立 trimap（邊緣帶 ai.refine.band_px）→ pymatting 算出 alpha 和前景色
 7. 輸出圖層     RGB 直接取原圖像素（只有邊緣帶換成去色邊後的顏色）；alpha 用精修結果 → 依 bbox 裁切
 8. 補背景       hole = 所有遮罩聯集再擴張 k px → 依每個洞裁出含上下文的區塊 → 縮放到 512 → LaMa →
                縮放回原尺寸 → **只把洞的範圍**（加羽化）貼回原圖
 9. 寫出 job     layers/、segmentation/、spec.json（沒有動畫）
10. 人工檢查     看 overview.png；需要的話修改 masks/*.png → animator layers rebuild
11. Stage 1      人工或之後的 Stage 4 加上動畫 → animator render（完全沒有改動）
```

**保留原圖外觀的原則**
- 圖層的顏色**直接來自原圖像素**，不經過模型重新產生，也不會壓縮或重新取樣（只有裁切）；只有 alpha 是模型算出來的。
- 背景在洞以外的地方和原圖**逐像素相同**，只有洞的範圍會被重繪。
- 前景色估計用來去掉邊緣上殘留的舊背景顏色（色邊），這樣圖層移到新位置時不會出現一圈殘影。

---

## 5. 授權總表（商業使用）

| 項目 | 授權 | 可以商用？ | 注意事項 |
|---|---|---|---|
| Grounding DINO（原版 / tiny 權重） | Apache-2.0 | ✅ | Grounding DINO 1.5 / 1.6 只提供付費 API，沒有開源 → 不使用 |
| SAM 2 / 2.1（程式碼與權重） | Apache-2.0 | ✅ | |
| LaMa（程式碼、big-lama 權重、Carve ONNX 版本） | Apache-2.0 | ✅ ⚠ | 訓練資料 Places2 的條款是「非商業研究／教育用途」。權重本身是 Apache-2.0，**正式商用前建議請法務確認** |
| OpenCV | Apache-2.0 | ✅ | opencv-python wheel 內附的 FFmpeg 是 LGPL |
| pymatting / numba / llvmlite | MIT / BSD | ✅ | |
| PyTorch / Transformers / ONNX Runtime | BSD-3 / Apache-2.0 / MIT | ✅ | |
| rembg（函式庫） | MIT | ✅ | 個別模型的授權要另外確認：BiRefNet 據報是 MIT（待查證）；**bria-rmbg 不能商用** |
| SAM 3 | Meta SAM License（自訂） | ⚠ 可以，但有用途限制 | 禁止軍事、武器等用途；Meta 可以修改條款 |
| YOLO-World / Ultralytics | AGPL-3.0 | ⚠ 實務上不行 | 要開放原始碼或購買商業授權 |
| BRIA RMBG 1.4 / 2.0 | CC BY-NC / 商業合約 | ❌ | |
| Depth Anything V2 Base / Large | CC-BY-NC-4.0 | ❌ | 只有 Small 是 Apache-2.0 |

**防呆設計**：`models.py` 的每個模型都會標註授權。config 的 `ai.models.allow_noncommercial` 預設為 `false`，碰到標記為非商用的模型會直接拒絕載入。

> 以上是技術評估，不是法律意見。正式商用前，請以各模型卡和 LICENSE 的原文為準。

---

## 6. 品質預期與限制

| 情況 | 預期 | 對策 |
|---|---|---|
| 主體清楚的物件、人物 | 好 | — |
| 頭髮、毛皮、半透明（玻璃、煙霧） | 普通；SAM 的遮罩比較硬 | pymatting 邊緣精修；之後可以加 rembg/BiRefNet 或 ViTMatte backend |
| 物件的**影子和倒影** | 會殘留在背景上 | 標籤加上 `shadow` 一起切掉；或人工修改 hole mask |
| 物件互相遮擋 | 後面那個物件被遮住的部分**不會補回來**（沒有 amodal 補全） | 前面的物件移開時，可以看到缺口 → 人工修正，或之後用生成式補圖 |
| 大面積、結構複雜的背景洞 | LaMa 會補出「合理的紋理」，但補不出具體的結構 | 縮小 hole 擴張量；用人工修圖取代 |
| 文字、標題 | 偵測不穩定 | 標籤用 `text` / `title`；之後可以加 OCR backend |
| 沒有給標籤（全自動） | 品質明顯比較差 | 用 `ai.detection.default_labels` 這份通用清單；建議由使用者或 Stage 4 提供標籤 |
| 中文標籤 | Grounding DINO 只懂英文 | Stage 2 只接受英文；Stage 4 的 planner 負責翻譯 |

---

## 7. 人工修正流程

1. 看 `segmentation/overview.png`，確認切得對不對。
2. 修改遮罩：用任何影像軟體編輯 `segmentation/masks/<id>.png`（白色 = 屬於這個物件）；`background_hole.png` 控制背景要補的範圍。
3. `animator layers rebuild output\<job_id>`：**不重跑 AI**，只依照遮罩重新產生 `layers/*.png` 和背景。
   - 已經存在的 `animations` 和人工修改過的欄位都會保留，只更新 AI 圖層的 `image` 和 `position`，並列出更新了哪些項目。
4. 也可以直接用自己的 PNG 取代 `layers/` 裡的圖層；這本來就是 Stage 1 支援的做法。
5. `backends/manual_masks.py` 讓使用者**完全不用 AI**，只提供原圖 + 一組遮罩，就能產生圖層和背景。

---

## 8. 依賴套件（待核准，只安裝在 `.venv`，另外列在 `requirements-ai.txt`）

| 套件 | 授權 | 用途 | 大約大小 |
|---|---|---|---|
| torch（**CPU 版**，從 download.pytorch.org/whl/cpu 安裝） | BSD-3 | 執行 DINO / SAM 2 | 下載約 200 MB、安裝後約 1 GB（CUDA 版下載約 2.5 GB） |
| transformers（連帶安裝 tokenizers、safetensors、huggingface_hub 等） | Apache-2.0 | DINO / SAM 2 的實作、模型下載 | 約 50 MB |
| onnxruntime（CPU） | MIT | LaMa | 約 15 MB |
| opencv-python-headless | Apache-2.0 | 形態學處理、Telea、縮放 | 約 40 MB |
| pillow | MIT-CMU | 讀寫圖片、EXIF、overview 標籤文字 | 約 3 MB |
| numpy | BSD | （會連帶安裝） | — |
| pymatting（連帶安裝 numba、llvmlite、scipy） | MIT / BSD | 邊緣精修、前景色估計 | 約 80 MB |

- **模型**：DINO tiny 約 690 MB + SAM 2.1 tiny 約 156 MB + LaMa ONNX 約 208 MB ≈ **1.05 GB**，存在 `models/`（不納入 Git）。
- **不會安裝**：rembg、SAM 3、ultralytics，以及任何雲端 SDK。
- Blender Python **一律不安裝**任何套件。
- Python 3.12 的相容性：以上所有套件都有 cp312 的 Windows wheel。

### config 新增的欄位（沿用 `ai` 區塊，`config_version` 仍然是 1）

```yaml
ai:
  device: "auto"                 # auto | cpu | cuda
  max_side: 1024                 # 模型處理圖的長邊（輸出仍然是原圖解析度）
  models:
    offline: true                # 執行時設定 HF_HUB_OFFLINE=1
    allow_noncommercial: false
  detection:
    backend: "grounding_dino"
    model: "grounding-dino-tiny"
    box_threshold: 0.35
    text_threshold: 0.25
    max_per_label: 5
    default_labels: ["person", "animal", "vehicle", "plant", "text"]
  segmentation:
    backend: "sam2"              # sam2 | manual_masks（之後可以加 rembg）
    model: "sam2.1-hiera-tiny"
    min_area_px: 400
  refine:
    backend: "pymatting"         # pymatting | none
    band_px: 8
  inpaint:
    backend: "lama"              # lama | opencv | none
    dilate_px: 12
```

---

## 9. 新增的 CLI（Stage 1 的指令完全不變）

```powershell
.\animator.cmd models list                      # 列出模型：授權、大小、是否已下載、SHA-256 是否正確
.\animator.cmd models download [--all | <name>]  # 唯一需要網路的步驟
.\animator.cmd models verify
.\animator.cmd segment --image assets\scene.jpg --labels "bird, sun, title" [--name demo]
                       [--device cpu|cuda] [--inpaint lama|opencv|none] [--no-refine]
.\animator.cmd layers rebuild output\<job_id>
.\animator.cmd render output\<job_id>\spec.json  # Stage 1，沒有變
```

`doctor` 會多顯示：AI 套件是否安裝、可不可以用 CUDA、模型是否已經下載。

---

## 10. 實作順序

| # | 項目 | 需要 AI 套件嗎 | 產出 / 驗收 |
|---|---|---|---|
| S2-0 | **安裝依賴 + 效能實測**（需要你核准） | ✅ | `scripts/bench_ai.py`：在 3~5 張測試圖上測量各模型的時間、RAM、VRAM，比較 CPU 和 CUDA → 決定要不要用 CUDA 版的 torch，並更新第 2.2 節 |
| S2-1 | 型別、介面、registry、`models.py`、config 擴充、`models` CLI | 部分 | 離線下載和驗證；授權防呆；單元測試 |
| S2-2 | `compose.py` + `export.py` + `manual_masks` backend | ❌ | 用**合成的遮罩**產生 job，再用 Stage 1 渲染，畫面要和原圖一致（不使用任何 AI 就能驗證整條流程） |
| S2-3 | `opencv_inpaint` | ❌（opencv） | 背景補洞；洞以外的像素和原圖相同 |
| S2-4 | `grounding_dino` detector | ✅ | 測試圖上找到預期的標籤和框 |
| S2-5 | `sam2` segmenter | ✅ | 遮罩和參考遮罩的 IoU 達到門檻 |
| S2-6 | `lama_onnx` inpainter（裁切區塊、縮放、貼回） | ✅ | 大洞補出來的結果明顯比 OpenCV 好（人工檢查 + 數值檢查） |
| S2-7 | `pymatting` 精修 + 去色邊 | ✅ | 邊緣色邊減少；圖層移到新背景上沒有殘影 |
| S2-8 | 重疊處理、z 排序、`overview.png` | ❌ | |
| S2-9 | `layers rebuild` | ❌ | 修改遮罩後重建；保留 animations |
| S2-10 | 整合測試（`@pytest.mark.ai`，沒有模型時自動略過）+ 文件 | ✅ | 端到端：`segment` → 加一段簡單動畫 → `render` → MP4 |

**Stage 2 的驗收標準（草案）**
1. 只靠 CPU、在離線狀態下，一張 1024 px 的圖片 60 秒內完成分層。
2. 不加動畫時，渲染出的第 1 格和原圖的差異在門檻內（證明分層加合成不會改變原圖外觀）。
3. 把物件移開之後，背景沒有明顯的洞或物件殘影（人工檢查）。
4. 修改遮罩後執行 `layers rebuild`，不需要重跑 AI。
5. 沒有安裝 AI 套件時，Stage 1 的所有測試仍然通過；`blender/` 沒有任何修改。
6. 換用另一個 backend（例如 `inpaint: opencv`）只需要修改 config。

---

## 11. 開始實作前需要你決定的事

1. **依賴清單**（第 8 節）是否核准？**torch 先用 CPU 版**（建議），等 S2-0 實測後再決定要不要改裝 CUDA 版（約 2.5 GB）？
2. **測試圖片**：建議你提供 3~5 張**代表實際用途**的圖片（人物、動物、插畫、有文字的海報…），並確認可以存進 repo；或者我改用 CC0 公有領域的圖片。
3. **標籤**：Stage 2 是否接受「需要提供英文標籤」（沒有提供時使用通用清單、品質較差）？
4. **LaMa 的訓練資料條款**（第 5 節）是否需要先請法務確認？在確認之前，可以先用 OpenCV 當預設的 inpainter。

---

## 參考來源（2026-11-03 查詢）

- SAM 2.1 tiny 的 Transformers 相容權重（約 156 MB）：[danelcsb/sam2.1_hiera_tiny](https://huggingface.co/danelcsb/sam2.1_hiera_tiny/tree/main)、[CollectionStudio/sam2.1-hiera-tiny](https://huggingface.co/CollectionStudio/sam2.1-hiera-tiny/commits/main)
- Grounding DINO tiny（Transformers 用法、Apache-2.0、約 172M 參數）：[Transformers docs](https://huggingface.co/docs/transformers/v4.41.0/model_doc/grounding-dino)、[frovolts/grounding-dino-tiny](https://huggingface.co/frovolts/grounding-dino-tiny)、[PromptLayer](https://www.promptlayer.com/models/grounding-dino-tiny)
- LaMa ONNX（512 固定輸入、Apache-2.0、Places2 條款）：[Carve/LaMa-ONNX](https://huggingface.co/Carve/LaMa-ONNX/blob/f5aae8835f5517a2d2ab92c14510e5e1b6212ba5/README.md)、[imagingforgood/lama-512-onnx](https://huggingface.co/imagingforgood/lama-512-onnx)
- rembg 支援的模型與 BRIA 授權：[rembg on PyPI](https://pypi.org/project/rembg/2.0.71/)、[briaai/RMBG-2.0](https://huggingface.co/briaai/RMBG-2.0/blob/refs%2Fpr%2F6/README.md)
- SAM 3（約 840M 參數、SAM License）：[Roboflow：SAM 3 licensing](https://roboflow.com/model-licenses/segment-anything-3)、[Roboflow：What is SAM 3](https://roboflow.com/blog/what-is-sam3)、[InfoQ](https://www.infoq.com/news/2025/11/meta-sam3)、[Voxel51](https://voxel51.com/blog/how-to-use-segment-anything-model-sam3)

第 2.2 節的執行時間和記憶體都是**估計值**，S2-0 實測後會用實際數字取代。
