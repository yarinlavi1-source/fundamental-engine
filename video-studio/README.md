# Video Studio — סביבת עבודה ליצירת סרטונים ועריכה

שני חלקים שעובדים יחד:

1. **עריכה מקומית** (`./vs`): כלי שורת פקודה מעל ffmpeg לחיתוך, הסרת שתיקות, מעבר ל-9:16,
   כתוביות בעברית, טקסט/CTA, מוזיקה עם הנמכה אוטומטית מתחת לדיבור, וייצוא לפי פלטפורמה.
2. **יצירה ב-AI** (Higgsfield): פרויקט בשם **Video Studio – Shorts, Edits & Ads**
   ליצירת קליפים, תמונות מוצר, קריינות ודיבוב. פרטים ב-[HIGGSFIELD.md](HIGGSFIELD.md).

הסביבה נפרדת לגמרי ממנוע המחקר `fundamental_engine` ולא משנה אותו.

## דרישות

`ffmpeg` / `ffprobe` ו-Python 3.10+. אין חבילות נוספות.

## מבנה

```
video-studio/
  vs                     # הפקודה הראשית
  tools/vs.py            # המימוש
  presets/platforms.json # רזולוציה, fps, ביטרייט ועוצמת קול לכל פלטפורמה
  projects/_template/    # raw/ assets/ music/ captions/ ai/ exports/
  projects/<שם>/         # הפרויקטים שלך (לא נשמרים ב-Git)
```

קבצי וידאו, אודיו ותמונות לא נכנסים ל-Git (ראו `.gitignore`).

## פקודות

| פקודה | מה היא עושה |
|---|---|
| `./vs new <שם> --type short\|edit\|ad` | פותחת תיקיית פרויקט חדשה מהתבנית |
| `./vs probe <קובץ>` | אורך, רזולוציה, fps, האם יש אודיו |
| `./vs trim in.mp4 out.mp4 --start 3 --end 18` | חיתוך קטע |
| `./vs silence-cut in.mp4 out.mp4` | מוריד שתיקות וגמגומים מצילומי דיבור (`--threshold -35dB`) |
| `./vs reframe in.mp4 out.mp4 --aspect 9:16 --fit blur` | אופקי לאנכי (`crop` / `blur` / `pad`) |
| `./vs concat out.mp4 a.mp4 b.mp4 ...` | חיבור קליפים, גם בגדלים שונים או בלי סאונד |
| `./vs subs in.mp4 captions.srt out.mp4 --style shorts` | צריבת כתוביות (עברית נתמכת, RTL) |
| `./vs text in.mp4 out.mp4 --text "50% הנחה" --box --end 3` | כותרת / קריאה לפעולה |
| `./vs music in.mp4 song.mp3 out.mp4 --volume 0.18` | מוזיקת רקע שמונמכת אוטומטית כשמדברים |
| `./vs export in.mp4 out.mp4 --preset tiktok` | ייצוא סופי + נרמול עוצמה ל-‎-14 LUFS |
| `./vs thumb in.mp4 cover.jpg --at 2.5` | תמונה ממוזערת |

כל פקודה מקבלת `--dry-run` (לפני שם הפקודה) כדי להציג את פקודת ה-ffmpeg בלי להריץ:
`./vs --dry-run export in.mp4 out.mp4 --preset reels`.

פריסטים לייצוא: `tiktok`, `reels`, `shorts` (‏1080×1920), `youtube` (‏1920×1080),
`ad-square` (‏1:1), `ad-feed` (‏4:5), `ad-story` (‏9:16).

## תהליכי עבודה מוכנים

**שורט/ריל מצילום שלך**
```bash
./vs new my-tip --type short
P=projects/my-tip
./vs silence-cut $P/raw/take1.mp4 $P/exports/1-cut.mp4
./vs reframe     $P/exports/1-cut.mp4 $P/exports/2-vertical.mp4 --aspect 9:16 --fit crop
./vs subs        $P/exports/2-vertical.mp4 $P/captions/captions.srt $P/exports/3-subs.mp4
./vs music       $P/exports/3-subs.mp4 $P/music/bed.mp3 $P/exports/4-music.mp4
./vs export      $P/exports/4-music.mp4 $P/exports/final-tiktok.mp4 --preset tiktok
```

**פרסומת מוצר**
1. צור ב-Higgsfield קליפי מוצר / תמונות (בקש ממני: "תייצר 3 שוטים של המוצר ל-9:16").
2. שמור אותם ב-`projects/<שם>/ai/`, ואז:
```bash
./vs concat $P/exports/1-joined.mp4 $P/ai/shot1.mp4 $P/ai/shot2.mp4 $P/ai/shot3.mp4 --aspect 9:16
./vs text   $P/exports/1-joined.mp4 $P/exports/2-hook.mp4 --text "הבעיה שכולם מכירים" --end 2.5
./vs text   $P/exports/2-hook.mp4   $P/exports/3-cta.mp4  --text "הזמינו עכשיו" --start 6 --position bottom --box
./vs export $P/exports/3-cta.mp4 $P/exports/ad-story.mp4 --preset ad-story
./vs export $P/exports/3-cta.mp4 $P/exports/ad-feed.mp4  --preset ad-feed --fit blur
```

## טיפים

- כתוביות: קובץ `.srt` רגיל ב-UTF-8. אפשר לבקש ממני לתמלל או לכתוב אותן.
- `--fit blur` שומר את כל הפריים עם רקע מטושטש; `crop` ממלא את המסך וחותך צדדים.
- מוזיקה: השתמש רק במוזיקה שיש לך זכות להשתמש בה (או במוזיקה מהספרייה של הפלטפורמה).

## בדיקות

```bash
python3 -m unittest discover -s tests -v
```
