# S2-0 測試圖片來源

由 `scripts/s2_0/prepare_images.py` 產生（2026-11-03 下載）。

## 已納入 Git（CC0 已用 Wikimedia Commons API 確認）

| 檔案 | 尺寸 | 測試用途 | 來源 | 作者 | 授權 | 授權確認方式 |
|---|---|---|---|---|---|---|
| `photo_desk.jpg` | 1920×1280 | 照片：人物 + 多個物件（筆電、盆栽、滑鼠、筆記本、手機、瓶子） | [Taking notes at a coffee table (Unsplash).jpg](https://commons.wikimedia.org/wiki/File:Taking_notes_at_a_coffee_table_(Unsplash).jpg) | Tran Mau Tri Tam | CC0 1.0 | Wikimedia Commons API `LicenseShortName = CC0` |
| `occlusion_dogcat.jpg` | 1920×1440 | 物件互相遮擋：貓的身體被狗擋住一部分 | [Dog and cat, Arco, Madeira.jpg](https://commons.wikimedia.org/wiki/File:Dog_and_cat,_Arco,_Madeira.jpg) | Gerda Arendt | CC0 1.0 | Wikimedia Commons API `LicenseShortName = CC0` |

- 照片的長邊縮到 1920（Commons 的標準縮圖尺寸），以控制 repo 大小；測試時再另外縮成 512 / 1024。

## 尚未納入 Git（授權待人工確認）

下面兩張插畫在 S2-0 測試中用過，但**檔案沒有放進 Git**。它們的 CC0 授權只來自 Openverse 的索引資料；rawpixel 的頁面會擋程式存取，還沒有獨立確認頁面上的授權文字。確認之前，需要的話用 `scripts/s2_0/prepare_images.py` 在本機重新產生。

| 檔案 | 尺寸 | 測試用途 | 來源 | 作者 | 標示的授權 | 狀態 |
|---|---|---|---|---|---|---|
| `illustration_camp.png` | 1024×768 | 插畫：背景、太陽、雲、樹、帳篷、人物（女孩）、狗 | **合成**：[Camping site clipart（rawpixel 6448970）](https://www.rawpixel.com/image/6448970) + [Girl clipart, walk dog（rawpixel 6864999）](https://www.rawpixel.com/image/6864999) | rawpixel | CC0 1.0（Openverse） | 待確認 |
| `illustration_picnic.png` | 1024×731 | 插畫：多人互相重疊、樹、野餐墊 | [Hanami picnic clipart vector（rawpixel 6743953）](https://www.rawpixel.com/image/6743953) | rawpixel | CC0 1.0（Openverse） | 待確認 |

- `illustration_camp.png` 是兩張作品的合成：把女孩圖「和邊界相連的白色背景」去掉後，貼在營地場景的右下角。
