"""準備 S2-0 測試圖片（CC0），輸出到 assets/s2-0/。

用法：python scripts/s2_0/prepare_images.py <下載暫存目錄>
暫存目錄需要有：camp.jpg、girl_dog.jpg、picnic.jpg（rawpixel）、desk.jpg、dogcat.jpg（Wikimedia Commons）

illustration_camp.png 是由兩張 CC0 插畫合成：營地場景（太陽、雲、樹、帳篷）+ 遛狗的女孩。
女孩圖只把「和邊界相連的近白色背景」設為透明（內部的白色區域保留），再直接貼上。
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from common import IMAGES


def cut_outer_background(im: Image.Image, thresh: int = 235) -> Image.Image:
    rgb = np.asarray(im.convert("RGB"))
    light = (rgb >= thresh).all(axis=2)
    labels, _ = ndimage.label(light)
    border = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))) - {0}
    outside = np.isin(labels, list(border))
    alpha = np.where(outside, 0, 255).astype(np.uint8)
    alpha = np.asarray(Image.fromarray(alpha).filter(__import__("PIL.ImageFilter").ImageFilter.GaussianBlur(0.7)))
    return Image.fromarray(np.dstack([rgb, alpha]), "RGBA")

src = Path(sys.argv[1])
IMAGES.mkdir(parents=True, exist_ok=True)

# 1. 插畫：營地場景 + 女孩與狗
camp = Image.open(src / "camp.jpg").convert("RGB")            # 1024x1024，場景置中
w, h = camp.size
scene = camp.crop((0, int(h * 0.12), w, int(h * 0.12) + 768))  # 取 1024x768 的橫幅
girl = cut_outer_background(Image.open(src / "girl_dog.jpg"))
girl = girl.resize((int(girl.width * 0.42), int(girl.height * 0.42)), Image.LANCZOS)
scene.paste(girl, (scene.width - girl.width - 10, scene.height - girl.height - 40), girl)
scene.save(IMAGES / "illustration_camp.png")

# 2. 插畫：賞花野餐（多人重疊）
Image.open(src / "picnic.jpg").convert("RGB").save(IMAGES / "illustration_picnic.png")

# 3. 照片：人物 + 多個物件；4. 照片：狗與貓互相遮擋（長邊縮到 2048，避免 repo 過大）
for name, out in (("desk.jpg", "photo_desk.jpg"), ("dogcat.jpg", "occlusion_dogcat.jpg")):
    im = Image.open(src / name).convert("RGB")
    im.thumbnail((2048, 2048), Image.LANCZOS)
    im.save(IMAGES / out, quality=92)

for p in sorted(IMAGES.iterdir()):
    if p.suffix in (".png", ".jpg"):
        with Image.open(p) as im:
            print(f"{p.name:28s} {im.width}x{im.height}  {p.stat().st_size / 1e3:.0f} KB")
