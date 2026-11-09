"""'Pick one 1-N' vertical video: each product pops in on its own colour, then a grid end card."""
import json, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
SEG, END = 1.8, 2.8
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

cfg = json.load(open(sys.argv[1]))
out_dir = sys.argv[2]
items = cfg["items"]

def card(path, w, h):
    im = Image.open(path).convert("RGB")
    a = np.array(im).astype(int)
    ys, xs = np.where(np.abs(a - 255).max(2) > 18)
    box = [xs.min(), ys.min(), xs.max(), ys.max()]
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    bw = (box[2] - box[0]) * 1.12
    bh = max((box[3] - box[1]) * 1.25, bw * h / w)
    bw = bh * w / h
    pad = 1000
    padded = Image.new("RGB", (im.size[0] + 2 * pad, im.size[1] + 2 * pad), "white")
    padded.paste(im, (pad, pad))
    crop = padded.crop((int(cx - bw / 2) + pad, int(cy - bh / 2) + pad, int(cx + bw / 2) + pad, int(cy + bh / 2) + pad))
    crop = crop.resize((w, h), Image.LANCZOS)
    c = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    m = Image.new("L", (w, h), 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), 48, fill=255)
    c.paste(crop, (0, 0), m)
    return c

def shadow_mask(size, blur=28):
    w, h = size
    m = Image.new("L", (w + 4 * blur, h + 4 * blur), 0)
    ImageDraw.Draw(m).rounded_rectangle((2 * blur, 2 * blur + 22, 2 * blur + w, 2 * blur + h + 22), 48, fill=95)
    return m.filter(ImageFilter.GaussianBlur(blur))

def ease(t):
    return 1 - (1 - t) ** 3

big = [card(it["image"], 960, 640) for it in items]
small = [card(it["image"], 470, 330) for it in items]
num_font = ImageFont.truetype(FONT, 260)
small_num = ImageFont.truetype(FONT, 90)
frame_dir = f"{out_dir}/frames"
subprocess.run(["mkdir", "-p", frame_dir])

n = 0
for i, it in enumerate(items):
    for f in range(int(SEG * FPS)):
        t = f / FPS
        s = (1.14 - 0.14 * ease(min(1, t / 0.28))) * (1 + 0.035 * t / SEG)
        fr = Image.new("RGB", (W, H), it["color"])
        c = big[i].resize((int(960 * s), int(640 * s)), Image.BICUBIC)
        x, y = (W - c.size[0]) // 2, 1010 - c.size[1] // 2
        sm = shadow_mask(c.size); fr.paste((0, 0, 0), (x - 56, y - 56), sm)
        fr.paste(c, (x, y), c)
        d = ImageDraw.Draw(fr)
        d.text((W // 2 + 6, 560 + 8), str(i + 1), font=num_font, fill=(0, 0, 0), anchor="mm")
        d.text((W // 2, 560), str(i + 1), font=num_font, fill="white", anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0))
        fr.save(f"{frame_dir}/{n:05d}.png"); n += 1
end_bg = cfg.get("end_color", "#111111")
pos = [(60, 640), (550, 640), (60, 1060), (550, 1060)]
for f in range(int(END * FPS)):
    t = f / FPS
    fr = Image.new("RGB", (W, H), end_bg)
    d = ImageDraw.Draw(fr)
    for i, (x, y) in enumerate(pos[:len(items)]):
        k = min(1, max(0, (t - 0.08 * i) / 0.25))
        if k <= 0: continue
        s = 0.85 + 0.15 * ease(k)
        c = small[i].resize((int(470 * s), int(330 * s)), Image.BICUBIC)
        ox, oy = x + (470 - c.size[0]) // 2, y + (330 - c.size[1]) // 2
        tile = Image.new("RGB", (470, 330), items[i]["color"])
        tm = Image.new("L", (470, 330), 0); ImageDraw.Draw(tm).rounded_rectangle((0, 0, 469, 329), 40, fill=255)
        fr.paste(tile, (x, y), tm)
        c2 = small[i].resize((int(430 * s), int(290 * s)), Image.BICUBIC)
        fr.paste(c2, (x + (470 - c2.size[0]) // 2, y + (330 - c2.size[1]) // 2), c2)
        d.text((x + 50, y + 50), str(i + 1), font=small_num, fill="white", anchor="mm", stroke_width=5, stroke_fill=(0, 0, 0))
    fr.save(f"{frame_dir}/{n:05d}.png"); n += 1
print("frames", n, "duration", n / FPS)
