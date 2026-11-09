"""Zigzag product grid with karaoke lyrics (trend: 'shoe lineup + song lyrics')."""
import json, os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
cfg = json.load(open(sys.argv[1]))
out = sys.argv[2]
os.makedirs(out, exist_ok=True)
FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
SIZES = {"S": 30, "M": 50, "L": 60, "XL": 82}
GRAY, BLACK = (165, 165, 165), (17, 17, 17)

# zigzag slots (centre x, centre y, max width) mirroring the reference layout
SLOTS = [(285, 270, 500), (770, 455, 470), (270, 690, 480), (790, 790, 460),
         (265, 1085, 480), (285, 1395, 490), (800, 1520, 460), (300, 1745, 490)]

bg = Image.new("RGB", (W, H), "white")
for (cx, cy, mw), path in zip(SLOTS, cfg["images"]):
    im = Image.open(path).convert("RGBA")
    im.thumbnail((mw, 330), Image.LANCZOS)
    a = im.split()[3]
    sh = Image.new("L", (im.size[0] + 80, im.size[1] + 80), 0)
    sh.paste(a.point(lambda v: v * 0.35), (40, 52))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    x, y = cx - im.size[0] // 2, cy - im.size[1] // 2
    bg.paste((60, 50, 40), (x - 40, y - 40), sh)
    bg.paste(im, (x, y), im)

fonts = {k: ImageFont.truetype(FONT, v) for k, v in SIZES.items()}
TX, TY = cfg.get("text_x", 585), cfg.get("text_y", 985)
blocks = cfg["blocks"]
dur = cfg["duration"]
words = [(w["t"]) for b in blocks for line in b["lines"] for w in line["words"]]
words.sort()

def next_time(t):
    for x in words:
        if x > t + 1e-6:
            return x
    return dur

n = int(round(dur * FPS))
for f in range(n):
    t = f / FPS
    fr = bg.copy()
    d = ImageDraw.Draw(fr)
    blk = None
    for b in blocks:
        if b["start"] <= t < b["end"]:
            blk = b
    if blk:
        y = TY
        for line in blk["lines"]:
            font = fonts[line["size"]]
            asc, desc = font.getmetrics()
            x = TX
            shown = False
            for w in line["words"]:
                if t < w["t"]:
                    break
                shown = True
                col = GRAY if t < min(w["t"] + 0.28, next_time(w["t"])) else BLACK
                d.text((x, y), w["w"], font=font, fill=col)
                x += d.textlength(w["w"] + " ", font=font)
            y += int((asc + desc) * 0.92)
    fr.save(f"{out}/{f:05d}.png")
print("frames", n)
