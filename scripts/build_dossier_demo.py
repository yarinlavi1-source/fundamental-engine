"""Synthetic, fully traceable demonstration of an iterative research dossier."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
fin=json.loads((ROOT/'examples/business_financing_demo.json').read_text())
for s in fin['sources']:s['origin_id']='demo-'+s['id']
sid=fin['sources'][0]['id']
case={'case_version':1,'is_demo':True,'as_of':fin['as_of'],
 'identity':{'ticker':fin['company']['ticker'],'company_id':fin['company']['id'],
             'name':fin['company']['name'],'currency':'USD'},
 'question':'האם ההפרעה הבדויה זמנית, והאם הצמיחה משאירה ערך למניה אחרי גיוס?',
 'archetypes':['infrastructure'],'sources':fin['sources'],'observations':[],'claims':[],
 'business_model':{'offering':'שירות מחשוב בדוי','payer':'לקוח עסקי בדוי','customer':'משתמשי השירות',
  'revenue_drivers':['יחידות שירות','מחיר ממומש'],'cost_drivers':['תפעול','תמיכה'],
  'capital_needs':['קיבולת','הון חוזר'],'competition':['חלופות מחשוב'],
  'mechanism':'מכירת יחידות במחיר המכסה את עלות השירות וההשקעה בקיבולת.',
  'claim_ids':['business']},
 'adversarial':{'mechanism':'גיוס במחיר נמוך עשוי להשאיר פחות מהערך למניה המקורית.',
                'decisive_test':'הרץ מסלול גיוס יקר וחלופה ללא מימון והשווה חלק בעלות.', 'claim_ids':['funding_risk']},
 'candidate_conclusion':{'classification':'watchlist'},'financial_input':fin,
 'search_budget':{'max_actions':24,'max_unproductive_attempts':3},'research_log':[]}
for cid,dim,claim in [('identity','identity','זהות חברת ההדגמה והנייר בדויים ומוגדרים במפורש'),
 ('business','business','הכנסות ההדגמה נובעות ממספר יחידות שירות כפול מחיר'),
 ('accounting','accounting','הנתונים הסינתטיים ביחידות USD מלאות ולתקופות שנתיות'),
 ('funding_risk','funding','תרחיש הצמיחה בדוגמה תלוי בגיוס מדלל')]:
 oid=cid+'-observation'
 case['observations'].append({'id':oid,'source_id':sid,'statement':claim,
  'location':'Synthetic demo fixture; not a real filing','review_note':'Checked against explicitly fictional example inputs',
  'kind':'reported_fact','observed_at':'2025-12-31','reviewed_at':case['as_of'],'reviewed':True})
 case['claims'].append({'id':cid,'statement':claim,'kind':'fact','dimension':dim,'materiality':'high',
  'support_ids':[oid],'challenge_ids':[],'reviewed_at':case['as_of']})
(ROOT/'examples/research_dossier_demo.json').write_text(json.dumps(case,ensure_ascii=False,indent=2)+'\n')
request={'ticker':'DEMO','as_of':case['as_of'],'question':case['question'],'archetypes':['infrastructure'],
         'triggers':['revenue_decline','dilution'],'capabilities':{'filings':'available','web':'available','prices':'available','execution':'available','portfolio':'unknown'}}
(ROOT/'examples/research_request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n')
print('Prepared synthetic research dossier and request')
