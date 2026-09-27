"""Reproducible fictional fixtures; never real-company calibrated forecasts."""
import json
from copy import deepcopy
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'examples/valuation'
OUT.mkdir(parents=True, exist_ok=True)
ASOF = '2026-09-27'
DATES = [ASOF] + [f'{y}-12-31' for y in range(2026, 2031)]
def common(archetype, method):
    return {'valuation_version':1,'is_demo':True,'as_of':ASOF,'company_id':'fictional-test','ticker':'FICTIONAL','currency':'USD','unit':'absolute','archetype':archetype,'method':method,
      'quote':{'price':20,'currency':'USD','as_of':ASOF,'source_ids':['synthetic']},
      'sources':[{'id':'synthetic','title':'Fictional arithmetic fixture','url':'synthetic://fixture','kind':'synthetic','published_at':ASOF,'available_at':ASOF}],
      'assumptions':[{'id':'demo','kind':'analyst','as_of':ASOF,'review_status':'reviewed','source_ids':['synthetic'],'rationale':'Synthetic demonstration, not calibrated.','falsifier':'Never use as an actual company valuation.'}],
      'opening':{'as_of':ASOF,'assumption_ids':['demo']},'report_dates':DATES,'scenarios':[]}
def periods(): return [{'start':a,'end':b,'assumption_ids':['demo']} for a,b in zip(DATES,DATES[1:])]
def scenario(name,ke): return {'name':name,'thesis':'Fictional '+name+' case; no probability estimate.','assumption_ids':['demo'],'cost_of_equity':ke}
def save(name,c): (OUT/(name+'.json')).write_text(json.dumps(c,indent=2)+'\n')
c=common('infrastructure','operating')
c['opening'].update(unrestricted_cash=1e9,debt=300e6,shares=100e6,deferred_revenue=100e6,tax_loss_carryforward=50e6)
for name,scale,ke in [('bear',.7,.18),('base',1.,.15),('bull',1.3,.12)]:
 s=scenario(name,ke);s['sbc_policy']='explicit_shares';s['periods']=periods()
 for i,p in enumerate(s['periods']):
  p.update(segments=[{'name':'Compute','driver':'capacity','average_units':(1000+600*i)*scale,'annual_revenue_per_unit':1e6,'utilization':.8,'cash_cost_ratio':.42}],
   fixed_cash_costs=40e6 if i==0 else 140e6,sbc_expense=5e6 if i==0 else 20e6,sbc_shares=250000 if i==0 else 1e6,depreciation=10e6 if i==0 else 90e6,
   debt_draw=200e6 if i==0 else 0,debt_repayment=0 if i<3 else 100e6,interest_rate=.08,nol_usage_limit=.8,tax_rate=.25,
   customer_prepayments=100e6 if i==0 else 0,revenue_from_prepayments=20e6 if i==0 else 45e6,change_working_capital_excluding_deferred=5e6 if i==0 else 20e6,
   maintenance_capex=10e6 if i==0 else 100e6,growth_capex=400e6 if i==0 else (500e6 if i<3 else 150e6),
   equity_proceeds=200e6 if i==0 else 0,equity_issue_price=20,equity_fee_rate=.02,debt_fees=4e6 if i==0 else 0,
   financing_status='assumed' if i==0 else 'none',minimum_cash=100e6,payout_fraction=0 if i<3 else .4)
 s['terminal']={'assumption_ids':['demo'],'growth':.025,'wacc':.1+(ke-.15),'roic':.15,'operating_margin':.32,'tax_rate':.25,'sbc_in_margin':True,'net_runoff_obligation':0,'other_claims':0,'working_capital_rationale':'No remaining advance balance; ongoing reinvestment through terminal ROIC.'}
 c['scenarios'].append(s)
save('infrastructure',c)
b=common('financials','residual_income');b['opening'].update(book_equity=1e9,shares=100e6)
for name,roe in [('bear',.08),('base',.13),('bull',.18)]:
 s=scenario(name,.11);s['periods']=periods()
 for p in s['periods']:p.update(roe=roe,payout_fraction=.4,risk_weighted_assets=6e9,required_capital_ratio=.12)
 s['terminal']={'assumption_ids':['demo'],'growth':.025,'roe':roe};b['scenarios'].append(s)
save('bank',b)
b=common('reit','nav');b['opening'].update(asset_value=300e6,unrestricted_cash=10e6,debt=100e6,other_claims=0,shares=10e6)
for name,rate in [('bear',.07),('base',.06),('bull',.05)]:
 s=scenario(name,.11);s['periods']=periods()
 for i,p in enumerate(s['periods']):p.update(properties=[{'forward_noi':18e6*1.03**(i+1),'cap_rate':rate,'ownership':1}],other_asset_value=0,unrestricted_cash=10e6,debt=100e6,other_claims=0,selling_costs_and_taxes=5e6,shares=10e6)
 b['scenarios'].append(s)
save('reit',b)
for method,archetype in [('rnpv','biotech'),('sotp','conglomerate')]:
 b=common(archetype,method)
 component=({'id':'program-A','cashflows':[{'date':'2032-12-31','cash_flow':1e9,'probability':.3},{'date':'2031-12-31','cash_flow':-50e6,'probability':1}]} if method=='rnpv' else {'id':'segment-A','basis':'enterprise','value':1e9,'cash':20e6,'debt':100e6,'other_claims':0,'ownership':1})
 base={'components':[component],'unrestricted_cash':50e6,'holding_debt':10e6,'other_claims':0,'corporate_costs_pv':20e6,'shares':50e6}
 b['opening'].update(deepcopy(base))
 for name,scale in [('bear',.7),('base',1),('bull',1.3)]:
  s=scenario(name,.12);s['periods']=periods()
  for p in s['periods']:
   p.update(deepcopy(base))
   if method=='rnpv':p['components'][0]['cashflows'][0]['probability']*=scale
   else:p['components'][0]['value']*=scale
  b['scenarios'].append(s)
 save(archetype,b)
