# basic 範例（Stage 1）

示範 Stage 1 的所有基本效果，**不需要 AI 套件或模型**：6 個獨立的 PNG 圖層（`layers/`，由 `scripts/make_sample_layers.py` 產生），1280×720、30 fps、4 秒。安裝後第一次確認渲染時用這個範例。

```powershell
.\animator.cmd validate examples\basic\spec.json
.\animator.cmd render examples\basic\spec.json --frame 60     # 只渲染一格 → still_60.png（很快）
.\animator.cmd render examples\basic\spec.json                # → output\<job_id>_basic\result.mp4
```

render 會在 `output/` 建立新的 job，把 spec 和圖層複製進去；這個資料夾不會被修改。

| 動畫 | 效果 |
|---|---|
| `cloud-drift` / `cloud-out` | 半透明的雲往左飄（`move` by），同時淡出（`fade_out`） |
| `sun-grow` / `sun-settle` | 太陽放大到 1.2 倍再縮回 0.9 倍（同一個 `scale` 通道的兩段動畫頭尾相接） |
| `star-fly` / `star-spin` | 星星飛過畫面（`move` to）的同時旋轉 360°（不同通道同時進行） |
| `bar-turn` | 橫條轉成直的（`rotate` to 90，`back_out` easing 會稍微轉過頭） |
| `title-in` | 標題從 `opacity: 0` 淡入（`fade_in`） |

這個範例的 spec 是手寫的，用到 `fade_in` / `fade_out`、`easing` 這些只能直接寫在 spec 的功能；格式見 `docs/spec.md`。要重新產生圖層：`.venv\Scripts\python.exe scripts\make_sample_layers.py`。
