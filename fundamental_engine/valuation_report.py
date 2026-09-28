"""Compact Hebrew valuation table with an accessible, complete calculation audit."""
from html import escape
import json


def render_valuation(r):
    def fmt(v): return 'לא חושב' if v is None else f'{v:,.2f}'
    def pct(v): return '—' if v is None else f'{v:+.1%}'
    rows = ''.join('<tr><td>' + escape(x['date']) + '</td>' + ''.join('<td>' + fmt(x[k]) + '</td>' for k in ('bear', 'base', 'bull')) + '<td>' + pct(x['base_gap_vs_quote']) + '</td></tr>' for x in r['annual_values'])
    warnings = list(r['warnings'])
    warnings.extend(x['message'] + ' [' + x['path'] + ']' for x in r.get('underwriting_audit', {}).get('issues', []))
    for s in r['scenarios']:
        warnings.extend(s['name'] + ': ' + i for i in s['issues'])
    notes = ''.join('<li>' + escape(x) + '</li>' for x in warnings)
    demo = '<p class="warning">דוגמה סינתטית בלבד — אינה ניתוח חברה או המלצת השקעה.</p>' if r['is_demo'] else ''
    if not r['is_demo'] and not r.get('underwriting_audit', {}).get('eligible_for_research_synthesis'):
        demo += '<p class="warning">טיוטת חישוב: בקרת הנתונים וההנחות טרם הושלמה. אין להסיק זול או יקר מהטבלה.</p>'
    audit = escape(json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False))
    return f'''<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>הערכת שווי {escape(r['ticker'])}</title><style>body{{font:17px system-ui;max-width:1080px;margin:40px auto;padding:20px;background:#f7f9fc;color:#17243a}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:14px;border-bottom:1px solid #dde4ee;text-align:right}}th{{background:#142c47;color:white}}.warning{{background:#fff0cc;padding:16px}}pre{{direction:ltr;text-align:left;overflow:auto;font-size:12px}}details{{margin-top:24px}}small{{color:#42536a}}</style>
<h1>{escape(r['ticker'])} — שווי הוגן לפי תרחישים</h1><p>תאריך המחקר: {escape(r['as_of'])} · מטבע: {escape(r['currency'])} · מחיר השוואה: {fmt(r['annual_values'][0]['current_quote'])}</p>{demo}
<p>השווי בכל שורה מתייחס לתאריך שלה. שווי עתידי אינו שווי להיום או תחזית מחיר בשוק. הפער מחושב מול מחיר ההשוואה הקבוע; הוא אינו תשואה שנתית.</p>
<table><thead><tr><th>מועד</th><th>שלילי</th><th>בסיסי</th><th>חיובי</th><th>פער בסיס מול המחיר עכשיו</th></tr></thead><tbody>{rows}</tbody></table>
<p>מצב החישוב: <b>{escape(r['status'])}</b>. טווח התרחישים אינו רווח סמך סטטיסטי או תקרת מחיר. הנחות התחזית דורשות בדיקה עסקית.</p>
<p>{escape(r.get('annual_path_semantics', ''))}</p><ul>{notes}</ul>
<details><summary>הנחות, מקורות, תחזית, מימון וכל החישובים</summary><pre>{audit}</pre></details></html>'''
