"""Readable HTML report; source text is always escaped."""
from html import escape
import json


def render(report):
    esc = lambda x: escape(str(x), quote=True)
    def table(rows):
        if not rows:
            return '<p class="muted">אין נתונים</p>'
        keys = list(rows[0])
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{esc(k)}</th>' for k in keys) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join(f'<td>{esc(json.dumps(r.get(k),ensure_ascii=False) if isinstance(r.get(k),(dict,list)) else r.get(k))}</td>' for k in keys)+'</tr>' for r in rows) + '</tbody></table></div>'
    company = report['company']
    demo = '<div class="warning">הדגמה בלבד — חברה, נתונים וראיות מומצאים</div>' if report['is_demo'] else ''
    sections = []
    def section(title, content):
        sections.append(f'<section><h2>{esc(title)}</h2>{content}</section>')
    section('ממצאים ופערי מידע', '<ul>'+''.join(f'<li>{esc(i)}</li>' for i in report['issues'])+'</ul>')
    section('נתונים פיננסיים · יחסים מוצגים כשבר עשרוני', table(report['financial_metrics']))
    section('פוטנציאל מוקדם', '<p>'+esc(report['potential']['status'])+'</p><p>תוויות התמיכה מבוססות על בדיקת האנליסט שסיפק את הנתונים; אינן אימות אוטומטי.</p>')
    for dimension, buckets in report['potential']['evidence'].items():
        rows = [{'מצב': bucket, 'טענה': s['claim'], 'מקורות': s['source_ids']} for bucket, signals in buckets.items() for s in signals]
        section(dimension, table(rows))
    for v in report['valuations']:
        body = f'<p>{esc(v["rationale"])}</p><p class="value">{v["value_per_share"]:,.2f} {esc(report["currency"])} למניה</p><p>שווי נוכחי מותנה בהנחות; אינו מחיר יעד לתאריך עתידי.</p>'
        body += table(v['sensitivity'])
        body += '<details><summary>תחזית וחישובים</summary>'+table(v['forecast'])+'<pre>'+esc(json.dumps({k:x for k,x in v.items() if k != 'forecast'},ensure_ascii=False,indent=2))+'</pre></details>'
        section('תרחיש · '+v['name'], body)
    if report['funding']:
        section('מסלול מימון', table(report['funding']['periods']))
    section('אבני דרך ותנאי הפרכה', table(report['milestones']))
    section('תזה שסופקה', '<pre>'+esc(json.dumps(report['thesis'], ensure_ascii=False, indent=2))+'</pre>')
    section('מקורות', table(report['sources']))
    section('מגבלות', '<ul>'+''.join(f'<li>{esc(i)}</li>' for i in report['limitations'])+'</ul>')
    return '<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(company['name'])+' · Fundamental Engine</title><style>body{font-family:system-ui,sans-serif;background:#101827;color:#e6edf6;margin:0;padding:24px;line-height:1.7}main{max-width:1100px;margin:auto}header{padding:30px 0}h1{font-size:2.3rem}section{background:#1a2638;padding:22px;margin:18px 0;border-radius:14px}h2{font-size:1.2rem;color:#70d6c2}table{border-collapse:collapse;width:100%;font-size:.88rem}td,th{padding:10px;border-bottom:1px solid #34445a;text-align:start;vertical-align:top}.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;direction:ltr;text-align:left}.warning{background:#6f4519;padding:18px;border-radius:12px}.muted{color:#9bacbf}.value{font-size:1.8rem}details{margin-top:16px}a{color:#70d6c2}</style><main>'+demo+'<header><p>FUNDAMENTAL ENGINE · '+esc(report['engine_version'])+'</p><h1>'+esc(company['name'])+' · '+esc(company['ticker'])+'</h1><p>'+esc(report['as_of'])+' | '+esc(report['status'])+'</p></header>'+''.join(sections)+'</main></html>'
