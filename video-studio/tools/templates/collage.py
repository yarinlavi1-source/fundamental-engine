"""Warm 'aesthetic' collage slideshow: 4 stacked tiles per collage, hard cuts, film grain."""
import json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
cfg = json.load(open(sys.argv[1])); out = sys.argv[2]; os.makedirs(out, exist_ok=True)
TW, TH = W, H // 4
rng = np.random.default_rng(7)

def tile(path, tint):
    im = Image.open(path).convert("RGB")
    a = np.array(im).astype(int)
    ys, xs = np.where(np.abs(a - np.array([243, 242, 238])).max(2) > 22)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    bw, bh = (x1 - x0) * 1.18, (y1 - y0) * 1.25
    s = min(TW / bw, TH / bh)
    cw, ch = TW / s, TH / s
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    pad = 2000
    big = Image.new("RGB", (im.size[0] + 2 * pad, im.size[1] + 2 * pad), (243, 242, 238))
    big.paste(im, (pad, pad))
    t = big.crop((int(cx - cw / 2) + pad, int(cy - ch / 2) + pad, int(cx + cw / 2) + pad, int(cy + ch / 2) + pad)).resize((TW, TH), Image.LANCZOS)
    f = np.array(t).astype(float) / 255
    f = 0.08 + 0.84 * f                                   # lift blacks, soften whites
    f = f * np.array(tint)                                  # warm tint
    yy, xx = np.mgrid[0:TH, 0:TW]
    v = 1 - 0.28 * (((xx - TW / 2) / (TW / 2)) ** 2 + ((yy - TH / 2) / (TH / 2)) ** 2) / 2
    f = f * v[..., None]
    return np.clip(f, 0, 1)

collages = []
for c in cfg["collages"]:
    tiles = [tile(p, c["tint"]) for p in c["images"]]
    collages.append(np.concatenate(tiles, axis=0))

seg = cfg["duration"] / len(collages)
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 44) if cfg.get("text") else None
n = int(round(cfg["duration"] * FPS))
for i in range(n):
    t = i / FPS
    k = min(len(collages) - 1, int(t // seg))
    lt = t - k * seg
    z = 1 + 0.035 * lt / seg
    base = collages[k]
    img = Image.fromarray((base * 255).astype("uint8"))
    zw, zh = int(W / z), int(H / z)
    img = img.crop(((W - zw) // 2, (H - zh) // 2, (W + zw) // 2, (H + zh) // 2)).resize((W, H), Image.BILINEAR)
    f = np.array(img).astype(float)
    f += rng.normal(0, 7, f.shape[:2])[..., None]           # film grain
    img = Image.fromarray(np.clip(f, 0, 255).astype("uint8"))
    d = ImageDraw.Draw(img)
    for yline in (TH, 2 * TH, 3 * TH):                      # thin seams between photos
        d.line([(0, yline), (W, yline)], fill=(250, 246, 238), width=4)
    if font:
        d.text((W // 2, H // 2), cfg["text"], font=font, fill=(255, 250, 240), anchor="mm", stroke_width=2, stroke_fill=(60, 40, 25))
    img.save(f"{out}/{i:05d}.png")
print("frames", n)
