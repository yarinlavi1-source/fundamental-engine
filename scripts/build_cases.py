"""Reproduce manually reviewed primary-source fixtures; no network or invented prices."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def write(name,data):
    (ROOT/'examples'/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def base(ticker,name,cutoff,url,sector):
    sid=ticker.lower()+'-release'
    return {'schema_version':1,'as_of':cutoff,'currency':'USD','unit':'absolute','is_demo':False,
      'company':{'id':ticker.lower(),'name':name,'ticker':ticker,'currency':'USD','sector':sector},
      'sources':[{'id':sid,'title':name+' annual results release','locator':url,'kind':'earnings_release',
         'published_at':cutoff,'available_at':cutoff,'origin_id':ticker+'-issuer',
         'retrieved_at':'2026-09-26','capture_method':'Manually transcribed from issuer website; tables in millions multiplied by 1,000,000',
         'review_mode':'Retrospective date-level reconstruction, not a contemporaneous archived research record'}],
      'financials':[],'events':[],'signals':[],
      'thesis':{'scope':'Historical reconstruction, no live quote or investment verdict. Unknown financial fields remain missing.'}}


def row(data,start,end,**values):
    return {'start':start,'end':end,'available_at':data['as_of'],'currency':'USD','basis':'GAAP',
            'source_ids':[data['sources'][0]['id']],**{k:v*1_000_000 for k,v in values.items()}}


def dated(data):
    return {'source_ids':[data['sources'][0]['id']],'reviewed_at':data['as_of']}


def evidence(data,role,claim,status='supported'):
    return {**dated(data),'role':role,'claim':claim,'status':status}


def ev(data,eid,description,cause,claim,recurrence='unknown',cash='unknown'):
    return {**dated(data),'id':eid,'observed_at':data['financials'][-1]['end'],
      'description':description,'cause':cause,'management_claim':claim,'recurrence':recurrence,
      'cash_effect':cash,'evidence':[],'impacts':[],'recovery_requirements':[], 'falsifiers':[]}


def impact(data,eid,metric,effect,classification):
    return {**dated(data),'id':eid,'metric':metric,'period_end':data['financials'][-1]['end'],
      'reported_effect':effect*1_000_000,'classification':classification,'measurement':'disclosed','review_status':'supported'}


meta=base('META','Meta Platforms','2023-02-01','https://investor.atmeta.com/investor-news/press-release-details/2023/Meta-Reports-Fourth-Quarter-and-Full-Year-2022-Results/default.aspx','platform')
meta['financials']=[row(meta,'2021-01-01','2021-12-31',revenue=117929,ebit=46753,net_income=39370,cfo=57683,capex=18690),
 row(meta,'2022-01-01','2022-12-31',revenue=116609,ebit=28944,net_income=23200,cfo=50475,capex=31431)]
meta['thesis']['fcf_definition']='CFO minus gross PP&E purchases. Differs from issuer FCF, which nets disposal proceeds and includes finance lease principal.'
e=ev(meta,'fx','השפעת מטבע על ההכנסות','fx','החברה הציגה הכנסות גבוהות יותר בשערי התקופה המקבילה')
e['evidence']=[evidence(meta,'attribution','גשר המטבע מפריד השפעת תרגום מההכנסות המדווחות')]
e['impacts']=[impact(meta,'fx-revenue','revenue',-5956,'comparability')]
e['falsifiers']=['גם לאחר בחינת מטבע נמשכת חולשה בביקוש ובמונטיזציה']
meta['events'].append(e)
e=ev(meta,'restructuring','הוצאות ארגון מחדש והמשך צפוי','restructuring','ההנהלה צופה הוצאות נוספות גם ב-2023',recurrence='repeated',cash='unknown')
e['evidence']=[evidence(meta,'attribution','החברה דיווחה הוצאות ארגון מחדש בשנת 2022')]
e['impacts']=[impact(meta,'restructuring-ebit','ebit',-4610,'booked_exceptional')]
e['recovery_requirements']=['בחינת ההוצאה החוזרת והחיסכון בפועל']
meta['events'].append(e)
meta['signals']=[{**dated(meta),'id':'users','dimension':'adoption','claim':'מספר המשתמשים היומי במשפחת האפליקציות עלה לעומת השנה הקודמת','direction':'positive','review_status':'supported','observed_at':'2022-12-31'}]
meta['business']={**dated(meta),'description':'פלטפורמות חברתיות עם מונטיזציה בפרסום והשקעה בפעילות Reality Labs.',
 'claims':[{**dated(meta),'kind':'segment','claim':'יש להפריד בין Family of Apps לבין Reality Labs','status':'supported'}]}
write('real/meta_2022.json',meta)

ba=base('BA','Boeing','2025-01-28','https://investors.boeing.com/investors/news/press-release-details/2025/Boeing-Reports-Fourth-Quarter-Results/default.aspx','industrial')
ba['financials']=[row(ba,'2023-01-01','2023-12-31',revenue=77794,ebit=-773,net_income=-2242,cfo=5960,capex=1527),
 row(ba,'2024-01-01','2024-12-31',revenue=66517,ebit=-10707,net_income=-11829,cfo=-12080,capex=2230)]
e=ev(ba,'strike','שביתה לצד סיכוני ביצוע בתוכניות','strike','החברה מייחסת חלק מהפגיעה לשביתה ולהסכם העבודה',cash='delayed')
e['evidence']=[evidence(ba,'supports_temporary','השביתה הסתיימה והייצור בתוכניות הרלוונטיות חודש'),
 evidence(ba,'supports_structural','התוצאות כוללות גם חיובים בתוכניות ביטחוניות; חידוש ייצור לבדו אינו התאוששות כלכלית')]
e['recovery_requirements']=['עלייה במסירות ותזרים ללא חיובים חוזרים גדולים']
e['falsifiers']=['דחיות נוספות ועלויות שחורגות מהתחזית']
ba['events'].append(e)
ba['business']={**dated(ba),'description':'מטוסים מסחריים, ביטחון ושירותים; הכרה בהכנסה ומזומן תלויים גם בביצוע ובמסירות.',
 'claims':[{**dated(ba),'kind':'capital_allocation','claim':'גיוס הון במהלך הרבעון חיזק נזילות; אינו מוכיח יצירת ערך למניה','status':'supported'}]}
write('real/boeing_2024.json',ba)

nke=base('NKE','NIKE','2025-06-26','https://investors.nike.com/investors/news-events-and-reports/investor-news/investor-news-details/2025/NIKE-Inc--Reports-Fiscal-2025-Fourth-Quarter-and-Full-Year-Results/default.aspx','nonfinancial')
nke['financials']=[row(nke,'2023-06-01','2024-05-31',revenue=51362,gross_profit=22887,net_income=5700),
 row(nke,'2024-06-01','2025-05-31',revenue=46309,gross_profit=19790,net_income=3219)]
e=ev(nke,'turnaround','ירידה במכירות ובמרווחים במהלך שינוי עסקי','demand','ההנהלה מצפה להתמתנות הלחצים עם ביצוע תוכנית השיפור',cash='lost')
e['evidence']=[evidence(nke,'supports_temporary','תחזית ההנהלה לשיפור היא ציפייה, לא התאוששות מוכחת','unverified'),
 evidence(nke,'supports_structural','ירידה בערוצים ובאזורים לצד לחץ הנחות מחייבת בדיקת ביקוש, מעבר למטבע')]
e['recovery_requirements']=['שיפור מכירות ומרווחים בלי תלות בהנחות מוגברות']
e['falsifiers']=['חולשה מתמשכת במכירות ובמחיר הממומש']
nke['events'].append(e)
nke['business']={**dated(nke),'description':'מוצרי ספורט הנמכרים במכירה ישירה ובסיטונאות.',
 'claims':[{**dated(nke),'kind':'value_chain','claim':'תמהיל ערוצי המכירה משפיע על המרווח','status':'supported'}]}
write('real/nike_2025.json',nke)

# Separate, visibly synthetic acceptance case demonstrating every new input section.
d=json.loads((ROOT/'examples/emerging_demo.json').read_text())
base_source=d['sources'][0]['id']; refs={'source_ids':[base_source],'reviewed_at':'2026-09-26'}
d['events']=[{**refs,'id':'plant','observed_at':'2025-12-31','description':'השבתת מפעל בדויה שהסתיימה',
 'cause':'strike','management_claim':'אירוע מבודד','cash_effect':'delayed','recurrence':'isolated',
 'recovery_requirements':['מסירות חוזרות לקצב רגיל'],'falsifiers':['אובדן הזמנות'],
 'evidence':[{**refs,'role':role,'claim':'ראיית הדגמה בדויה','status':'supported'} for role in ('supports_temporary','recovery','attribution')],
 'impacts':[{**refs,'id':'plant-cost','metric':'ebit','period_end':'2025-12-31','reported_effect':-3_000_000,
   'classification':'booked_exceptional','measurement':'disclosed','review_status':'supported'}]}]
d['business']={**refs,'description':'חברת הדגמה בדויה: יחידות שירות כפול מחיר ליחידה.',
 'revenue_driver':{'label':'Revenue','unit':'USD','op':'product','children':[
  {**refs,'label':'Units','unit':'units','op':'input','value':100000},
  {**refs,'label':'Price','unit':'USD/unit','op':'input','value':500}]},
 'claims':[{**refs,'kind':'moat','claim':'עלויות מעבר — השערה לבדיקה','status':'unverified'}],
 'opportunities':[{**refs,'name':'שירות חדש בדוי','claimed_stage':'scaling','stage_evidence':[
   {**refs,'stage':'paying_customers','status':'supported','claim':'לקוחות משלמים בדוגמה בלבד'}],
   'shareholder_risks':['צורך בגיוס והנפקה במחיר נמוך'],
   'milestones':[{'due_at':'2027-06-30','success_criterion':'הזמנות חוזרות','failure_criterion':'אי חידוש','cost':10_000_000}]}]}
d['ownership_valuation']={'assumptions_as_of':'2026-09-26','source_ids':[base_source],
 'cash':50_000_000,'debt':15_000_000,'shares':20_000_000,'minimum_cash':5_000_000,'other_claims':0,
 'cost_equity':.15,'terminal_capitalization_rate':.12,'terminal_growth':.025,'terminal_roic':.15,
 'sbc_policy':'cash_equivalent_expensed_no_extra_dilution','scenarios':[
 {'name':'צמיחה עם גיוס היפותטי','rationale':'תרחיש סינתטי, לא תחזית לחברה אמיתית',
  'years':[{'growth':.3,'operating_margin':m,'cash_tax_rate':.25,'sales_to_capital':2,
   'base_net_reinvestment':2_000_000,'debt_interest_rate':.08,'debt_repayment':0 if i<2 else 5_000_000,
   'dividend':0,'financing':[{'kind':'equity','commitment':'hypothetical','gross_amount':30_000_000,'fee_rate':.03,'issue_price':5}] if i==0 else []}
   for i,m in enumerate([-.2,0,.12,.2,.25])]}]}
for s in d['valuation']['scenarios']:
    s['reinvestment_mode']='sales_to_capital'
    for year in s['years']:
        year.pop('reinvestment',None);year.update(sales_to_capital=2,base_net_reinvestment=2_000_000)
write('business_financing_demo.json',d)
print('Prepared 3 real historical fixtures and 1 synthetic integration case')
