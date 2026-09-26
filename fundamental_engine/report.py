"""Standalone Hebrew research report. Escape all source/user data; no scripts."""
from html import escape
import json

LABELS = {
 'conditional_valuation':'שווי מותנה בהנחות', 'conditional_ownership_valuation':'שווי למניה עם תרחישי מימון',
 'research_incomplete':'המחקר חסר', 'financing_required':'נדרש פתרון מימון',
 'temporary_supported':'יש ראיות לחולשה זמנית', 'mixed':'תמונה מעורבת',
 'structural_concern':'חשש לפגיעה מבנית', 'recovery_unproven':'התאוששות טרם הוכחה',
 'insufficient_evidence':'אין מספיק ראיות', 'early_evidence':'ראיות מוקדמות',
 'hypothesis_only':'השערה ללא תמיכה מספקת', 'mixed_evidence':'ראיות מעורבות',
 'broad_evidence_requires_valuation':'ראיות במספר תחומים; נדרשת בחינת תמחור',
 'funded':'מסלול המימון מכסה את התרחיש', 'financing_gap':'פער מימון',
 'terminal_unproven':'רווחיות לטווח הארוך טרם הוכחה',
 'revenue':'הכנסות','ebit':'רווח תפעולי','net_income':'רווח נקי','cfo':'תזרים תפעולי',
 'market':'שוק','adoption':'אימוץ','advantage':'יתרון','execution':'ביצוע','unit_economics':'כלכלת יחידה',
 'supported':'נתמך לפי בדיקת האנליסט','unverified':'לא אומת','contradicted':'נסתר',
 'supporting':'ראיות תומכות','opposing':'ראיות נגדיות','stale':'ראיות ישנות',
 'paying_customers':'לקוחות משלמים','repeat_orders':'הזמנות חוזרות','scaling':'התרחבות',
 'proven_unit_economics':'כלכלת יחידה מוכחת','idea':'רעיון','prototype':'אב־טיפוס','pilot':'פיילוט',
 'stage_supported':'שלב נתמך בראיות','stage_unproven':'השלב הנטען טרם הוכח',
}


def render(report):
    def esc(value):
        return escape(str(value),quote=True)
    def label(value):
        return LABELS.get(str(value),str(value))
    def cell(value):
        if value is None:
            return 'לא זמין'
        if isinstance(value,bool):
            return 'כן' if value else 'לא'
        if isinstance(value,(int,float)):
            return f'{value:,.4f}'.rstrip('0').rstrip('.') if isinstance(value,float) else f'{value:,}'
        if isinstance(value,(list,dict)):
            return esc(json.dumps(value,ensure_ascii=False))
        return esc(label(value))
    def table(rows):
        if not rows:
            return '<p class="muted">אין נתונים זמינים בסעיף זה.</p>'
        keys = list(dict.fromkeys(k for r in rows for k in r))
        return '<div class="scroll"><table><thead><tr>'+''.join(f'<th>{esc(k)}</th>' for k in keys)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{cell(r.get(k))}</td>' for k in keys)+'</tr>' for r in rows)+'</tbody></table></div>'
    def bullets(values):
        return '<ul>'+''.join('<li>'+cell(v)+'</li>' for v in values)+'</ul>' if values else '<p class="muted">לא סופק מידע.</p>'
    def details(title,value):
        return '<details><summary>'+esc(title)+'</summary><pre dir="ltr">'+esc(json.dumps(value,ensure_ascii=False,indent=2))+'</pre></details>'
    sections=[]
    def section(title,body):
        sections.append('<section><h2>'+esc(title)+'</h2>'+body+'</section>')
    company=report['company']; business=report.get('business_analysis',{}); events=report.get('event_analysis',{})
    section('1 · מסקנה ומידת הוודאות', '<p class="value">'+esc(label(report['status']))+'</p><p>אין כאן מסקנת קנייה אוטומטית. איכות העסק, הפוטנציאל והשווי נבחנים בנפרד. תגובת יתר של השוק לא הוכחה.</p>'+bullets(report['issues'])+table(report.get('investigation_priorities',[])))
    section('2 · כיצד העסק פועל', '<p>'+esc(business.get('description','טרם נבנה תיאור עסק מבוסס מקורות.'))+'</p>'+details('עץ מניעי ההכנסות',business.get('drivers'))+table([{'תחום':c['kind'],'טענה':c['claim'],'מצב':c['status'],'מקורות':c['source_ids']} for c in business.get('claims',[]) if c['kind']!='moat']))
    section('3 · יתרון תחרותי',table([{'טענה':c['claim'],'מצב':c['status'],'מקורות':c['source_ids']} for c in business.get('claims',[]) if c['kind']=='moat'])+'<p>היעדר ראיה ליתרון אינו הוכחה שאין יתרון.</p>')
    funding = details('מסלול מימון נפרד — ללא דילול',report['funding']) if report['funding'] else '<p>לא סופק מסלול מימון נפרד.</p>'
    for v in report.get('owner_valuations',[]):
        funding += '<h3>'+esc(v['name'])+'</h3>'+table([{'מצב':v['status'],'חלק הבעלים המקוריים':v.get('original_ownership'),'שווי למניה מקורית':v.get('value_per_initial_share')}])+details('מה קורה ללא הגיוסים ההיפותטיים?',v['without_hypothetical_financing'])+details('ציר מימון, מזומן ומניות',v['forecast'])
    section('4 · נזילות, מימון ודילול',funding)
    potential='<p>'+esc(label(report['potential']['status']))+'</p><p>תוויות התמיכה מבוססות על בדיקת האנליסט; המערכת אינה מאמתת את המקור באופן עצמאי.</p>'
    for dimension,buckets in report['potential']['evidence'].items():
        potential+='<h3>'+esc(label(dimension))+'</h3>'+table([{'מצב':bucket,'טענה':s['claim'],'מקורות':s['source_ids']} for bucket,signals in buckets.items() for s in signals])
    potential+=table([{'הזדמנות':o['name'],'שלב נטען':o['claimed_stage'],'שלב עם ראיות':o['highest_evidenced_stage'],'מצב':o['status'],'סיכונים למניה':o['shareholder_risks']} for o in business.get('opportunities',[])])
    section('5 · פוטנציאל ושלב מסחרי',potential)
    body=''
    for e in events.get('events',[]):
        body+='<h3>'+esc(e['description'])+'</h3><p>'+esc(label(e['status']))+'</p>'+table([{'טענת ההנהלה':e['management_claim'],'השפעה על מזומן':e['cash_effect'],'הישנות':e['recurrence'],'מקורות':e['source_ids']}])+table([{'טענה':r['claim'],'תפקיד':r['role'],'מצב':r['status'],'מקורות':r['source_ids']} for r in e['evidence']])+details('כימות ההשפעה והחלטות הנרמול',e['impacts'])
    section('6 · חולשה זמנית או פגיעה מבנית?',body or '<p>לא הוזנו אירועים לבדיקה.</p>')
    section('7 · מדווח לעומת מנורמל',table([{'סוף תקופה':r['period_end'],'מדד':r['metric'],'מדווח':r['reported'],'התאמות':r['adjustments'],'מנורמל':r['normalized']} for r in events.get('normalization',[])])+'<p>הנרמול אינו מחליף את הדיווח המקורי ואינו מוזן אוטומטית לתחזית.</p>'+table([{'סוף תקופה':m['end'],'צמיחת הכנסות':m['revenue_growth'],'שיעור רווח תפעולי':m['operating_margin'],'תזרים חופשי מדווח':m['reported_fcf'],'SBC להכנסות':m['sbc_to_revenue'],'מקורות':m['source_ids']} for m in report['financial_metrics']])+'<p class="muted">יחסים מוצגים כשבר עשרוני: 0.10 = 10%. סכומים ביחידות מטבע מלאות.</p>')
    body=''
    for v in report['valuations']:
        body+='<h3>'+esc(v['name'])+'</h3><p>'+esc(v['rationale'])+'</p><p class="value">'+cell(v['value_per_share'])+' '+esc(report['currency'])+' למניה</p>'+table(v['sensitivity'])+details('תחזית וגשר לשווי ההון',v)
    for v in report.get('owner_valuations',[]):
        body+='<h3>'+esc(v['name'])+'</h3><p>'+esc(v['rationale'])+'</p>'+table(v['issue_price_sensitivity'])
    section('8 · שווי, תרחישים ורגישות',(body or '<p>אין מספיק הנחות להערכת שווי. לא הופק מחיר יעד.</p>')+'<p>המודלים מותנים בהנחות; איכות העסק לבדה אינה מעידה על מחיר אטרקטיבי.</p>')
    reverse=[{'תרחיש':v['name'],'תוצאה':v.get('reverse_dcf')} for v in report['valuations'] if v.get('reverse_dcf')]
    section('9 · מה המחיר דורש?',table(reverse)+details('בדיקת חלון תגובת השוק',report.get('market_context'))+'<p>Reverse DCF הוא פתרון תחת הנחות, לא זיהוי ודאי של קונצנזוס השוק. בלי מחיר מתוארך אין מסקנת תמחור.</p>')
    bear=[s['claim'] for buckets in report['potential']['evidence'].values() for s in buckets['opposing']]
    bear += [e['description']+' — '+label(e['status']) for e in events.get('events',[]) if e['status'] in {'mixed','structural_concern'}]
    section('10 · הטיעון הנגדי',bullets(bear)+'<p>נדרשת גם בדיקת נגד עצמאית; הרשימה מציגה רק ראיות שהוזנו.</p>')
    section('11 · מה יפריך את התזה?',bullets([f for e in events.get('events',[]) for f in e['falsifiers']]))
    milestones=[{'תיאור':m['description'],'מועד':m['due_at'],'הצלחה':m['success_criterion'],'כישלון':m['failure_criterion']} for m in report['milestones']]
    for o in business.get('opportunities',[]):
        milestones += [{'תיאור':o['name'],'מועד':m['due_at'],'הצלחה':m['success_criterion'],'כישלון':m['failure_criterion'],'עלות':m.get('cost')} for m in o['milestones']]
    section('12 · אבני דרך ובדיקות המשך',table(milestones)+bullets([r for e in events.get('events',[]) for r in e['recovery_requirements']]))
    section('13 · שינויים מאז המחקר הקודם','<p>הדוח הזה הוא תמונת מצב. להשוואה שמורה יש להפעיל את פקודת compare עם מזהי שתי ריצות; אין שינוי מוסק בלי ריצה קודמת.</p>'+details('תזה שסופקה — פרשנות של המשתמש',report['thesis']))
    source_rows=[]
    for s in report['sources']:
        source_rows.append({'מזהה':s['id'],'כותרת':s['title'],'סוג':s['kind'],'זמין מתאריך':s['available_at'],'כתובת או מיקום':s['locator']})
    section('14 · מקורות ומגבלות',table(source_rows)+bullets(report['limitations']))
    demo='<div class="warning">הדגמה סינתטית — הנתונים והחברה בדויים</div>' if report['is_demo'] else '<div class="notice">מחקר לפי המידע שהוזן ולמועד המוצג; אין כאן הבטחת תשואה.</div>'
    return '<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(company['name'])+' · Fundamental Engine</title><style>body{font-family:system-ui,sans-serif;background:#101827;color:#e6edf6;margin:0;padding:24px;line-height:1.7}main{max-width:1120px;margin:auto}header{padding:30px 0}h1{font-size:2.3rem}section{background:#1a2638;padding:22px;margin:18px 0;border-radius:14px}h2{font-size:1.2rem;color:#70d6c2}h3{font-size:1rem}table{border-collapse:collapse;width:100%;font-size:.88rem}td,th{padding:10px;border-bottom:1px solid #34445a;text-align:start;vertical-align:top;overflow-wrap:anywhere}.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;text-align:left}.warning{background:#6f4519;padding:18px;border-radius:12px}.notice{border:1px solid #34445a;padding:14px}.muted{color:#9bacbf}.value{font-size:1.6rem}details{margin-top:16px}@media print{body{background:white;color:#111}section{background:#fff;border:1px solid #ccc;break-inside:avoid}h2{color:#075a50}}</style></head><body><main>'+demo+'<header><p>FUNDAMENTAL ENGINE · '+esc(report['engine_version'])+'</p><h1>'+esc(company['name'])+' · '+esc(company['ticker'])+'</h1><p>מועד המחקר: '+esc(report['as_of'])+' | '+esc(label(report['status']))+'</p></header>'+''.join(sections)+'</main></body></html>'
