# visibility 範例（S2-2 M3c）

示範離散的 `visibility` 動畫（顯示 / 隱藏），以及它和 `opacity` 互相獨立。規格見 `docs/spec.md` 第 4 節的 `visibility`。

```powershell
.\animator.cmd validate examples\visibility\spec.json
.\animator.cmd render examples\visibility\spec.json            # 會建立新的 job，不會修改這個資料夾
```

圖層直接使用 `examples/basic/layers/` 的 PNG（`"image": "../basic/layers/..."`），渲染時會複製到 job 裡。

| 時間 | 畫面 |
|---|---|
| 0 ~ 1 秒 | 橫條（bar）蓋在太陽（sun）中間；星星（star）從左往右飛 |
| 1 ~ 2 秒 | `bar-hide`：橫條隱藏，露出後面的太陽；2 秒那一格恢復顯示 |
| 1.5 ~ 2.5 秒 | `star-hide`：星星隱藏，但仍然在移動；2.5 秒重新出現在當下的位置 |
| 2 ~ 3 秒 | `sun-dim`：太陽的 opacity 從 1 降到 0.4（`opacity` 動畫，和 visibility 無關） |
| 3 ~ 4 秒 | `title-show`：標題的 `visible` 是 `false`，這段時間顯示；區間一直到影片結尾，所以最後一格也是顯示的 |

重點：

- visibility 沒有淡入淡出，切換是瞬間的；要淡入淡出請用 `opacity`。
- 區間是 `[start, end)`：`end` 那一格恢復成圖層的 `visible`；`end` 落在最後一格之後（`end = duration`）時，最後一格維持區間的狀態。
- `visible: false` 的圖層只要有 visibility 動畫就會保留；沒有的話和以前一樣整個移除。
