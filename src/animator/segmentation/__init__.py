"""Stage 2：圖片自動分層（偵測 → 分割 → 邊緣精修 → 圖層 → 背景修補 → spec.json）。

設計見 docs/stage2.md。重點：
- 這個套件只在 `animator segment` / `animator models` 時才 import（lazy import），
  Stage 1 的指令不會載入 numpy、torch 等 AI 套件。
- 模型推論一律在獨立的子行程中執行（`worker.py`），結束後記憶體完全釋放。
- 輸出是標準的 spec v1 + RGBA PNG，交給 Stage 1 渲染；不會碰 blender/。
"""
