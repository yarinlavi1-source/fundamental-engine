# Fundamental Engine · 0.3.0

מנוע מחקר עסקי, פונדמנטלי ופוטנציאל מוקדם, עם חישובים ב־Python ודוח בעברית.
המטרה: להבחין בין עסק שנפגע זמנית לעסק שנחלש מבנית, ולחבר את הפוטנציאל
למחיר, לצורכי ההון ולחלק שיישאר למניה הקיימת.

**מעמד:** ליבת מחקר עובדת עם בדיקות ומקרי חברה אמיתיים. המנוע מחשב ומבקר
ראיות שהוזנו; הוא עדיין אינו חוקר אוטונומי שמאמת לבד כל טענה ומסמך.
אין הבטחת תשואה, ציון קנייה כולל או טענה ל־99% דיוק.

## המוח של Claude — התחל כאן

**[START_HERE.md](START_HERE.md)** הוא קובץ הכניסה ל־Claude.
חיבורי הדוחות, האינטרנט, המחירים והתיק נשארים אצלך ב־Claude; המאגר מספק
את תהליך החשיבה, כלי החישוב והזיכרון המקומי.

גרסה 0.3 מוסיפה מנהל מחקר עובד, 23 קובצי שיטה וחוזה קלט, שמונה מסלולי עסק,
ביקורת ראיות וטיעון נגדי, בדיקת הבטחות הנהלה, עצירת חיפושים חוזרים ושמירת
מחקר עם גרסאות. אין צורך לטעון את כל המאגר לכל שאלה.

```bash
python -m fundamental_engine research-plan examples/research_request.json
python -m fundamental_engine research-review examples/research_dossier_demo.json --out runs/review.json
python -m fundamental_engine research-checkpoint examples/research_dossier_demo.json
```

מסלול העבודה: תכנון → משיכת מידע בחיבורים הקיימים → ראיות וטענות → בדיקת
המחקר והפעלת החישובים → השאלה המהותית הבאה → שמירת גרסה → מסקנה בעברית.
תוצאת ready_for_conditional_synthesis מציינת השלמת תנאי תהליך; היא אינה
אימות עצמאי של הטענות ואינה מסקנת קנייה.

ראו [חוזה תיק המחקר](fundamental_engine/brain/protocols/dossier_contract.md)
ו[שיטת הערכת האיכות](benchmarks/README.md).

## התחלה

נדרש Python 3.11 ומעלה. אין תלות בחבילות Python חיצוניות בזמן הרצה.

```bash
python -m unittest discover -s tests -v
python -m fundamental_engine analyze examples/business_financing_demo.json
```

הפקודה מדפיסה נתיב לדוח HTML בעברית. לצדו נשמר JSON, וב־SQLite נשמרת תמונת
הקלט והתוצאה ללא דריסה. הדוגמה הזאת **סינתטית ומסומנת כך**.

```bash
python -m fundamental_engine analyze examples/real/meta_2022.json
python -m fundamental_engine analyze examples/real/boeing_2024.json
python -m fundamental_engine analyze examples/real/nike_2025.json
```

שלושת המקרים מבוססים על נתונים שתומללו ונבדקו מול הודעות התוצאות הרשמיות.
אלה שחזורים היסטוריים ברמת יום, לא נתונים חיים ולא בדיקת תשואה היסטורית.
אין בהם מחירי מניה או הנחות שווי שהומצאו כדי להפיק מסקנה.
ראו [מקרי הבדיקה וההתאמות המספריות](docs/REAL_CASES.md).

## יכולות החישוב והמחקר מ־v0.2

- אירועים: טענת הנהלה, ראיות תומכות ונגדיות, הישנות, השפעת מזומן, התאוששות ותנאי הפרכה.
- הבחנה בין זמני נתמך, מעורב, חשש מבני, התאוששות לא מוכחת ומידע חסר.
- גשר בין נתון מדווח למנורמל, כולל הסרה סימטרית של רווחים והוצאות חריגים.
  אין הוספת הכנסות שאולי היו מתקבלות ואין מחיקה אוטומטית של הוצאה חוזרת.
- מודל עסקי מובנה, עץ הכנסות, טענות על לקוחות/ספקים/יתרון/מגזרים ושלבי מסחור.
- מודל שווי לבעל המניה עם מזומן, השקעה בצמיחה, חוב, ריבית, גיוסי הון ודילול.
  מוצגת גם החלופה ללא גיוסים היפותטיים ורגישות למחיר ההנפקה.
- DCF ו־Reverse DCF עם השקעה חוזרת התלויה בצמיחה באמצעות sales-to-capital.
  מצב DCF הישן נשמר ומסומן כשמסלול המימון שלו נפרד.
- חילוץ SEC בתגית מפורשת ונרמול תקופות לשנתי, רבעוני, YTD ו־TTM עם תיעוד מקורות.
- מאגר מקורות מקומי, חיפוש לפי תאריך ואיחוד עותקים ומקורות בעלי מוצא משותף.
- שרת MCP מקומי להפעלת המחקר מתוך Claude או לקוח תואם.
- דוח עברי ב־14 סעיפים, שאלות מחקר מתועדפות, גרסאות והשוואת ריצות.

## חיבור ל־Claude

[מדריך חיבור MCP](docs/MCP.md) כולל הגדרה ודרך בדיקה.

```bash
python -m fundamental_engine mcp --corpus runs/sources.sqlite
```

השרת חושף את כלי המחקר `research_plan`, `research_packet`, `research_review`,
`research_checkpoint`, `research_load`, `research_history`, `research_compare`, לצד
`analyze_company`, `normalize_sec_concept`, `ingest_source`, `search_sources`. זהו תהליך stdio שלקוח MCP מפעיל; אין ממשק HTTP ציבורי.
השרת לא מפעיל API בתשלום ולא שולח קבצים לשירות מודל. השימוש ב־Claude עצמו
כפוף לחבילה ולעלויות של הלקוח שבו עובדים.

## מקורות ונתוני SEC

```bash
python -m fundamental_engine source-ingest examples/source_record.json
python -m fundamental_engine source-search "revenue" --as-of 2026-09-26
python -m fundamental_engine source-import metadata.json transcript.txt
```

ייבוא תומך ב־TXT/Markdown וב־PDF בר חיפוש אם `pdftotext` מותקן.
אין OCR מובנה, הורדת תוכן מאחורי תשלום או מעקב אוטומטי אחרי קישורים.
אפשר לייבא תוכן יוצרים שיש לך גישה אליו; דעה נשארת דעה ולא הופכת לנתון חברה.

למשיכת SEC יש להגדיר `SEC_USER_AGENT` עם שם יישום וכתובת קשר אמיתית:

```bash
python -m fundamental_engine sec-fetch 1326801
python -m fundamental_engine sec-periods runs/sec/CIK0001326801.json --tag RevenueFromContractWithCustomerExcludingAssessedTax --unit USD --as-of 2023-02-01
```

התגית היא בחירה מפורשת של החוקר: אם אינה קיימת, מתקבלת שגיאה ולא תחליף שקט.
יש לבחור מושג, יחידה, תקופה ובסיס חשבונאי מתאימים לחברה. חילוץ מושג אינו בניית
דוח כספי מלא. הגדרת SEC חסרה בסביבת הבנייה, ולכן לא נטען שנבדקה משיכה חיה.

## גבולות המימוש

- ה־LLM/האנליסט מחלץ ובוחן טענות; Python אחראי לחישובים ולכללי הבקרה.
  תווית `supported` היא תוצאת סקירה שסופקה, לא אימות עצמאי של אמיתות המקור.
- המחקר המרכזי עדיין מקבל שורות שנתיות. פקודת SEC מנרמלת רבעונים ו־TTM בנפרד;
  אין עדיין בנייה אוטומטית של קלט חברה שלם מרבעונים, ביאורים ומגזרים.
- אין ספק מחירים או מתזמן בתוך המאגר: Claude משתמש בחיבורים הקיימים שלו.
  אין טענה שהחיבורים הפרטיים שלך נבדקו מתוך סביבת הבנייה.
- מודל הבעלות בודק נזילות שנתית; אינו מזהה פער בתוך השנה. אין NOL מלא,
  אופציות עובדים מלאות, ADR, פיצולים אוטומטיים, המרת מטבע או מיסוי פרטני.
- אין מודלים ייעודיים לבנקים, ביטוח, REIT, ביוטק מוקדם, NAV או SOTP.
- אין חיזוי סיבתי, הסתברויות הצלחה מכוילות, בדיקת alpha או המלצה מותאמת אישית.
- שרת MCP נבדק מול לקוח בדיקה מקומי; חיבור לחשבון Claude של המשתמש טרם נבדק.

## מסמכי הפרויקט

- [חוזה הקלט המקורי](docs/INPUT_CONTRACT.md) ו[הרחבות v0.2](docs/INPUT_V02.md)
- [הנחות מודל השווי והמימון](docs/MODELS.md)
- [סקירת הפערים לפני הפיתוח](docs/AUDIT_V02.md)
- [מצב הבנייה והבדיקות](docs/BUILD_STATUS.md)
- [בסיס המחקר הפונדמנטלי בעברית](docs/RESEARCH_THEORY_HE.md)

```bash
python -m fundamental_engine history demo-emerging
python -m fundamental_engine compare FIRST_RUN_ID SECOND_RUN_ID
```
