# Trend templates

Each script renders PNG frames; encode them with ffmpeg (see examples). Product photos come
from the store's Shopify media (several angles per product); `cutout.py` removes the light
studio background.

| Script | Trend | Input |
|---|---|---|
| `lyric_grid.py` | zigzag product lineup + karaoke lyrics (word timings per block) | `examples/lyric_cfg.json` |
| `collage.py` | warm "aesthetic" 4-photo collages with hard cuts and grain | `examples/collage_cfg.json` |
| `pick.py` | "pick one 1–N" colour cards + grid end card | config with `items` |
| `cutout.py` | background removal for light studio photos | image paths |

Encode: `ffmpeg -framerate 30 -i frames/%05d.png -f lavfi -i anullsrc=r=48000:cl=stereo -c:v libx264 -crf 17 -pix_fmt yuv420p -c:a aac -shortest -movflags +faststart out.mp4`
(upload silent; add the trend sound in-app so its timing matches the lyric config).
