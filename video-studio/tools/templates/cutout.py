"""Cut a product off a light studio background (background = light pixels connected to the border)."""
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

def cutout(path, out, tol=34):
    im = Image.open(path).convert("RGB")
    a = np.array(im).astype(int)
    h, w, _ = a.shape
    border = np.concatenate([a[:8].reshape(-1, 3), a[-8:].reshape(-1, 3), a[:, :8].reshape(-1, 3), a[:, -8:].reshape(-1, 3)])
    bgcol = np.median(border, 0)
    near = (np.abs(a - bgcol).max(2) < tol).astype("uint8") * 255
    lab = Image.fromarray(near).copy()
    for x in range(0, w, 40):
        for y in (2, h - 3):
            if lab.getpixel((x, y)) == 255: ImageDraw.floodfill(lab, (x, y), 128, thresh=0)
    for y in range(0, h, 40):
        for x in (2, w - 3):
            if lab.getpixel((x, y)) == 255: ImageDraw.floodfill(lab, (x, y), 128, thresh=0)
    bg = np.array(lab) == 128
    alpha = Image.fromarray(np.where(bg, 0, 255).astype("uint8"))
    alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(7)).filter(ImageFilter.GaussianBlur(1.0))
    rgba = im.copy(); rgba.putalpha(alpha)
    rgba = rgba.crop(Image.fromarray((np.array(alpha) > 40).astype("uint8") * 255).getbbox())
    rgba.save(out)
    print(out, rgba.size, "bg", bgcol.astype(int))

for p in sys.argv[1:]:
    cutout(p, p.rsplit(".", 1)[0] + ".png")
