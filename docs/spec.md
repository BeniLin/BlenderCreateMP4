# spec.json 格式規格（version 1）

`spec.json` 描述一支動畫要用哪些圖層、每個圖層怎麼動、怎麼渲染。它是**給人編輯的檔案**：AI planner 產生的 spec 和人工撰寫的 spec 格式完全一樣，修改後執行 `animator render <spec.json>` 就能重新渲染。

## 1. 兩種 spec

| 檔案 | 誰寫 | 誰讀 | 說明 |
|---|---|---|---|
| `output/<job_id>/spec.json` | 人 / AI planner | 系統端 `animator` | 可以手動編輯；欄位可省略，用預設值 |
| `output/<job_id>/history/<render_id>/spec.resolved.json` | 系統端（自動產生） | Blender 端 | 已驗證、已補齊預設值、路徑轉為絕對路徑、時間換算成影格、`from` 也都算好了。**不要手動編輯** |

Blender 端**只讀 `spec.resolved.json`**，所以 Blender 端不需要做驗證、補預設值，也不需要任何第三方套件。

## 2. 核心規則：座標、單位、時間、anchor、排序、透明度

### 2.1 座標系統
- **原點**：畫布**左上角**是 `(0, 0)`；**x 往右、y 往下**為正；畫布右下角是 `(width, height)`。
- **單位**：所有位置、位移、尺寸都用**畫布像素（px）**，可以有小數。
- 畫布像素會一比一對應到輸出影片的像素（`resolution_percentage = 100` 時）。預覽或縮小解析度時，整個畫面等比例縮小，**spec 裡的數值不需要改**。
- 圖層可以超出畫布，甚至完全在畫布外（例如從畫面外飛進來）；超出畫布的部分不會顯示。

### 2.2 圖層尺寸與 anchor（pivot）
- 圖層的**原始尺寸** = PNG 的像素寬高（1 個圖片像素 = 1 個畫布像素）。
- **`anchor`** 是圖層上的一個點，用相對座標表示：`[0, 0]` 是圖片左上角、`[1, 1]` 是右下角、`[0.5, 0.5]` 是中心（預設值）。
- **`position`** 是 `anchor` 點放在畫布上的位置。例如 `anchor: [0, 0]` 搭配 `position: [0, 0]`，圖片左上角就會對齊畫布左上角。
- **`anchor` 同時是 pivot**：縮放和旋轉都以這個點為中心，而且這個點在縮放和旋轉時不會移動。
- 套用順序：縮放 → 旋轉（都以 anchor 為中心）→ 把 anchor 移到 `position`。

### 2.3 縮放與旋轉
- **縮放**：倍率，`1` = 原始尺寸；可以寫一個數字（等比例），或 `[sx, sy]`（分別縮放寬和高）。數值必須 > 0，v1 不支援用負值做鏡像。
- **旋轉**：單位是**度**，**順時針為正**（和 y 往下的螢幕座標一致）；可以超過 360°，例如 `720` = 轉兩圈。

### 2.4 時間
- **單位**：秒（float）。
- 動畫共 `frame_end = round(duration × fps)` 格，影格編號從 1 開始。
- 時間 `t` 對應到影格 `frame(t) = 1 + round(t × fps)`，並且限制在 `[1, frame_end]` 範圍內。所以 `t = 0` 是第 1 格，`t = duration` 是最後一格。
- 每筆動畫在 `frame(start)` 時是 `from` 的值，在 `frame(end)` 時是 `to` 的值，中間依 easing 插值。
- 動畫換算後至少要跨 1 格（`frame(end) > frame(start)`），否則驗證失敗。

### 2.5 圖層排序（前後順序）
- **`z` 越大越前面**（越靠近觀眾）。`z` 可以是任何數字，也可以是負數。
- `z` 相同時，**`layers` 陣列裡越後面的越前面**。沒有寫 `z` 時，預設值是它在陣列裡的索引（0, 1, 2…）。
- 排序在渲染時固定，**v1 不支援在動畫過程中改變前後順序**。
- 使用正交攝影機，前後距離不會影響圖層看起來的大小。

### 2.6 透明度與合成
- 圖片 PNG 建議用 **RGBA、straight（非 premultiplied）alpha、sRGB**；沒有 alpha 的 PNG 會被當成完全不透明（驗證時會出警告）。
- 圖層最終的不透明度 = **PNG 每個像素的 alpha × 圖層的 `opacity` × 圖層的 visibility（顯示 = 1、隱藏 = 0）**。`opacity` 的範圍是 `0`（完全透明）~ `1`（原圖的不透明度），不會讓 PNG 原本透明的地方變得不透明。visibility 見第 4 節的 `visibility` 效果；它和 `opacity` 互相獨立，不會改變顏色。
- 合成方式是一般的 alpha 混合（source-over），由後往前疊上去；最底下是 `canvas.background_color`。
- 顏色不會被燈光或色彩轉換改變：色彩管理固定用 `Standard`，PNG 原本的顏色會照原樣輸出。
- `opacity = 0` 的圖層仍然存在，之後還可以淡入；如果要整個移除圖層，請用 `visible: false`（如果這個圖層有 `visibility` 動畫，它會保留下來，只在動畫指定的時間顯示）。

### 2.7 顏色寫法
`"#RRGGBB"` / `"#RRGGBBAA"`，或 `[r, g, b]` / `[r, g, b, a]`（0~1，sRGB）。

## 3. 頂層結構

```jsonc
{
  "version": 1,          // 必填
  "meta":       { ... }, // 選填
  "canvas":     { ... }, // 必填
  "layers":     [ ... ], // 必填，至少 1 個
  "animations": [ ... ], // 選填，預設 []
  "render":     { ... }, // 選填
  "output":     { ... }  // 選填
}
```

### 3.1 `meta`（選填，不影響渲染）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `name` | str | 工作名稱，會用在 job_id 上 |
| `description` | str | |
| `source_image` | str | 原始圖片（相對 spec 的路徑） |
| `prompt` | str | 使用者的動畫描述 |
| `created_by` | str | `manual` / `planner:rule_based` / `planner:llm` … |
| `created_at` | str | ISO 8601 格式的時間 |

### 3.2 `canvas`（必填）

| 欄位 | 型別 | 必填 | 預設 | 限制 |
|---|---|---|---|---|
| `width` | int | ✅ | | 16~8192，**必須是偶數**（yuv420p 的要求） |
| `height` | int | ✅ | | 同上 |
| `fps` | int | | config `defaults.canvas.fps` | 1~120 |
| `duration` | float | ✅ | | > 0，單位為秒 |
| `background_color` | color | | config 預設值 | 沒有圖層蓋到的地方就顯示這個顏色 |

### 3.3 `layers[]`（必填）

| 欄位 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `id` | str | ✅ | | 符合 `^[a-z0-9][a-z0-9_-]*$`，不可重複；`@` 開頭的名稱保留給系統用（例如未來的 `@camera`） |
| `image` | str | ✅ | | PNG（建議 RGBA），路徑**相對於 spec.json 所在目錄**；也可以寫絕對路徑，但會出現「不可攜」的警告 |
| `position` | [x, y] | | 畫布中心 | 圖層 `anchor` 點在畫布上的位置（像素） |
| `anchor` | [ax, ay] | | `[0.5, 0.5]` | 在圖層上的相對位置，`[0,0]` 是左上角、`[1,1]` 是右下角；也是縮放和旋轉的中心 |
| `z` | number | | 陣列索引 | 前後順序 |
| `scale` | number \| [sx, sy] | | `1` | 初始縮放 |
| `rotation` | number | | `0` | 初始旋轉（度） |
| `opacity` | number | | `1` | 初始透明度 |
| `visible` | bool | | `true` | 圖層的**基本顯示狀態**。設為 `false`、而且沒有啟用的 `visibility` 動畫時，不會建立物件，它的動畫也會被忽略（方便人工暫時關掉一個圖層）；有 `visibility` 動畫時會保留圖層，平常隱藏，只在動畫的區間裡顯示 |
| `meta` | object | | | 自由欄位（例如 AI 分割時的 bbox、label、score），渲染時忽略 |

保留給未來的欄位（v1 寫了會報錯「尚未支援」）：`parent`（父子圖層）、`blend_mode`、`style`（靜態光暈或陰影）。

### 3.4 `animations[]`（選填）

| 欄位 | 型別 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `id` | str | | `anim_<索引>` | 方便人工對照和除錯 |
| `target` | str | ✅ | | 圖層 `id`（v1 只支援圖層；`@camera` 保留給未來） |
| `effect` | str | ✅ | | 效果名稱，見第 4 節 |
| `start` | float | ✅ | | 秒，`0 ≤ start` |
| `end` | float | ✅ | | 秒，`start < end ≤ canvas.duration` |
| `easing` | str | | `"ease_in_out"` | 見第 5 節 |
| `params` | object | | `{}` | 依效果而定 |
| `enabled` | bool | | `true` | 設為 `false` 時忽略這筆（人工關掉一筆動畫，不用刪除） |

**時間軸規則**

- 每個效果只會控制一個**屬性通道**：`position`、`scale`、`rotation`、`opacity`、`visibility`。
- 同一個 `target` 的同一個通道，各筆動畫的時間**不可以重疊**（頭尾相接可以），否則驗證會失敗。不同通道可以同時進行，例如邊移動邊旋轉。
- 第一筆動畫開始之前，屬性維持圖層的初始值；最後一筆結束之後，維持在最後的 `to` 值。**例外：`visibility`** 只在自己的區間裡有效，結束後恢復圖層的 `visible`（見第 4 節）。
- 省略 `from` 時，代表從「這筆動畫開始那一刻的值」開始，也就是圖層初始值，或是同一通道上一筆動畫的結束值。這個值由系統端先算好，寫進 resolved spec。

## 4. 效果（v1）

每個效果在 `blender/effects/` 裡各有一個模組。下面 `from` 的欄位都可以省略。

### `move` — 通道 `position`
| params | 型別 | 說明 |
|---|---|---|
| `to` | [x, y] | 目標位置（像素）。`to` 和 `by` **只能選一個** |
| `by` | [dx, dy] | 相對位移（像素） |
| `from` | [x, y] | 起點 |

### `scale` — 通道 `scale`
| params | 型別 | 說明 |
|---|---|---|
| `to` | number \| [sx, sy] | 目標倍率（必填） |
| `from` | number \| [sx, sy] | |

### `rotate` — 通道 `rotation`
| params | 型別 | 說明 |
|---|---|---|
| `to` | number | 目標角度（度）。`to` 和 `by` 只能選一個 |
| `by` | number | 相對角度，可以超過 360（例如 `720` = 轉兩圈） |
| `from` | number | |

### `opacity` — 通道 `opacity`
| params | 型別 | 說明 |
|---|---|---|
| `to` | number | 0~1（必填） |
| `from` | number | |

### `visibility` — 通道 `visibility`（離散的顯示 / 隱藏）
| params | 型別 | 說明 |
|---|---|---|
| `visible` | bool | 區間裡的狀態（必填）：`false` = 隱藏、`true` = 顯示 |

```json
{ "id": "bar-hide", "target": "bar", "effect": "visibility", "start": 1.0, "end": 2.0, "params": { "visible": false } }
```

- **基本狀態**是圖層的 `visible`（預設 `true`）。`visibility` 動畫只在區間 **[start, end)** 裡改變狀態：從 `frame(start)` 這一格開始，到 `frame(end)` 的前一格為止；`frame(end)` 這一格恢復成基本狀態。
- **最後一格的例外**：`end` 落在最後一格之後（例如 `end = canvas.duration`）時，區間一直持續到最後一格，不會在最後一格恢復。判斷方式是不限制範圍的 `1 + round(end × fps)` 大於最後一格的編號。
- **離散，沒有內插**：省略 `easing` 時就是 `constant`，也只接受 `"constant"`；寫其他 easing 會驗證失敗。要淡入淡出請用 `opacity`。不接受 `from` / `to` / `by`。
- **和 `opacity` 互相獨立**：最終 alpha = PNG alpha × opacity × visibility。顯示但 `opacity = 0` → 看不到；隱藏時 `opacity = 1` 也看不到；顯示 / 隱藏不會改變 `opacity` 的值或動畫，淡入淡出在隱藏期間照樣進行。
- 隱藏期間其他通道（move / scale / rotate / opacity）照常進行，重新顯示時圖層出現在當下的位置、大小、角度。前後順序（`z`）不變。
- 同一個圖層的 `visibility` 區間**不可以重疊**（和其他通道一樣），**頭尾相接可以**：在共同的那一格直接切換成下一個區間的狀態，不會先恢復一格。區間換算後至少要有 1 格。
- 狀態和基本狀態相同的區間沒有效果（驗證時出**警告**）。
- `visible: false` 的圖層只要有一筆啟用的 `visibility` 動畫就會保留（平常隱藏，它的其他動畫也保留）；沒有的話和以前一樣整個移除。

### 便利別名（會展開成 `opacity`）
| effect | 等同於 |
|---|---|
| `fade_in` | `opacity`，`from: 0`、`to: 1`（params 可以覆寫） |
| `fade_out` | `opacity`，`to: 0` |

### 保留給未來的效果名稱（v1 寫了會報錯「尚未支援」）
`glow`、`shadow`、`tint`、`light_sweep`、`particles`、`camera_move`、`camera_zoom`、`shake`、`float`、`wiggle`。新增效果時，在這份文件補上 params 表格；如果是不影響舊 spec 的新增，`version` 不需要改。

## 5. Easing

| 值 | 說明 |
|---|---|
| `linear` | 等速 |
| `constant` | 停在起始值，到 `end` 那一格直接跳到結束值 |
| `ease_in` / `ease_out` / `ease_in_out` | 平滑加速 / 減速（等同於 `cubic_in` / `cubic_out` / `cubic_in_out`） |
| `<curve>_in` / `<curve>_out` / `<curve>_in_out` | `curve` ∈ `sine`、`quad`、`cubic`、`quart`、`quint`、`expo`、`circ`、`back`、`bounce`、`elastic`（對應到 Blender 內建的插值方式） |

## 6. `render`（選填）

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `engine` | str | config 預設值 | `eevee` / `cycles` |
| `samples` | int | config 預設值 | |
| `resolution_percentage` | int | `100` | 1~100；加上 `--preview` 時會暫時改用 config 的預覽值 |
| `view_transform` | str | `"Standard"` | |
| `film_transparent` | bool | `false` | 保留給日後輸出透明影片使用；v1 設成 `true` 時只會出現警告，並且當成 `false` 處理 |

## 7. `output`（選填）

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `filename` | str | `"result.mp4"` | 輸出到 spec 所在的目錄 |
| `codec` / `crf` / `preset` / `pix_fmt` | | config `defaults.encode` | |

## 8. 驗證錯誤

驗證失敗時，錯誤訊息會指出 JSON 路徑，例如 `animations[2].params.to`，並且一次列出**所有**錯誤，不會只顯示第一個：

- `version` 不支援；缺少必填欄位；型別錯誤；數值超出範圍
- `layers[].id` 重複或格式不合；`image` 檔案不存在或不是 PNG
- `animations[].target` 找不到對應的圖層；`effect` 沒有註冊或尚未支援
- `start`/`end` 不合法，或超過 `duration`
- 同一個 target 的同一個通道，時間重疊（`visibility` 也一樣）
- `visibility` 的 `params.visible` 不是 true / false，或 `easing` 不是 `constant`
- `move`/`rotate` 的 `to` 和 `by` 同時出現，或兩個都沒有
- 出現不認識的欄位 → 只顯示**警告**，不會中止（避免手動編輯時打錯字卻沒發現）

## 9. 完整範例

```json
{
  "version": 1,
  "meta": {
    "name": "bird-demo",
    "prompt": "小鳥從左邊飛進來並微微旋轉，太陽放大，標題最後淡入",
    "created_by": "manual"
  },
  "canvas": { "width": 1920, "height": 1080, "fps": 30, "duration": 5.0, "background_color": "#0b1230" },
  "layers": [
    { "id": "background", "image": "layers/background.png", "z": 0 },
    { "id": "sun",   "image": "layers/sun.png",   "z": 5,  "position": [1500, 260], "scale": 0.8 },
    { "id": "bird",  "image": "layers/bird.png",  "z": 10, "position": [-150, 420] },
    { "id": "title", "image": "layers/title.png", "z": 20, "position": [960, 900], "opacity": 0 }
  ],
  "animations": [
    { "id": "bird-fly",  "target": "bird",  "effect": "move",   "start": 0.0, "end": 2.5,
      "easing": "ease_out", "params": { "to": [900, 380] } },
    { "id": "bird-tilt", "target": "bird",  "effect": "rotate", "start": 0.0, "end": 2.5,
      "easing": "sine_in_out", "params": { "to": -12 } },
    { "id": "sun-grow",  "target": "sun",   "effect": "scale",  "start": 1.0, "end": 4.0,
      "params": { "to": 1.1 } },
    { "id": "title-in",  "target": "title", "effect": "fade_in", "start": 3.5, "end": 4.5 }
  ],
  "render": { "engine": "eevee", "samples": 16 },
  "output": { "filename": "result.mp4", "crf": 18 }
}
```

## 10. 渲染紀錄與追溯

每次執行 `animator render`，都會建立 `output/<job_id>/history/<render_id>/`，保存這次渲染的完整依據：

| 檔案 | 內容 |
|---|---|
| `spec.json` | 這次渲染用的 spec 快照（之後再修改 spec，也不會影響這份） |
| `spec.resolved.json` | 傳給 Blender 的完整解析結果 |
| `config.effective.yaml` | 這次實際採用的設定值，以及每個值的來源（cli / env / config / default） |
| `render_report.json` | render_id、執行的指令、開始和結束時間、狀態、Blender/FFmpeg 版本、spec 與每張圖層的 SHA-256、輸出檔、ffprobe 驗證結果 |
| `blender.log` / `ffmpeg.log` | 外部工具的完整輸出 |
| `result.mp4` 等 | 被下一次渲染取代時，舊的輸出檔會**移到它自己的 history 目錄**（`jobs.overwrite: true` 時改為直接刪除，但其他紀錄仍然保留） |

`output/<job_id>/renders.json` 記錄所有渲染的索引，以及目前根目錄裡每個輸出檔屬於哪一次渲染。

## 11. 版本演進

- 不影響舊 spec 的變更（新增選填欄位、新增效果）：`version` 不變。
- 會影響舊 spec 的變更：`version` + 1，並在系統端提供 `animator migrate-spec`。
- Blender 端碰到不支援的 `version` 會直接報錯並停止。
