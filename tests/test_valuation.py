import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.valuation import value_company, sensitivity, years, segment_revenue
from fundamental_engine.valuation_tools import asset_schedule, stress_test, reverse_price, forecast_score
from fundamental_engine.valuation_report import render_valuation
from fundamental_engine.mcp import Server
ROOT=Path(__file__).resolve().parents[1]
def fixture(name='infrastructure'):return json.loads((ROOT/'examples/valuation'/f'{name}.json').read_text())
def base(r):return next(s for s in r['scenarios'] if s['name']=='base')
class ValuationTests(unittest.TestCase):
 def test_all_five_routes(self):
  for name in ('infrastructure','bank','reit','biotech','conglomerate'):
   r=value_company(fixture(name));self.assertEqual(len(r['annual_values']),6);self.assertEqual(r['status'],'conditional_valuation')
 def test_stub(self):
  seg={'name':'x','driver':'capacity','average_units':10,'annual_revenue_per_unit':100,'utilization':.5}
  self.assertEqual(segment_revenue(seg,.25),125)
  self.assertAlmostEqual(base(value_company(fixture()))['forecast'][0]['revenue'],1000*1e6*.8*95/365.25)
 def test_cash_identity(self):
  c=fixture();r=base(value_company(c))
  for p,o in zip(c['scenarios'][1]['periods'],r['forecast']):
   expected=o['cash_open']+o['cfo']-p['growth_capex']-p['maintenance_capex']+p['debt_draw']-p['debt_repayment']+p['equity_proceeds']*(1-p['equity_fee_rate'])-p['debt_fees']-o['dividends']
   self.assertAlmostEqual(o['cash'],expected)
 def test_explicit_dividend_exit_present_value(self):
  c=fixture();r=base(value_company(c));pv=sum(p['dividend_per_share']/1.15**years(c['as_of'],p['end']) for p in r['forecast'])
  pv+=r['terminal']['value_per_share']/1.15**years(c['as_of'],r['forecast'][-1]['end'])
  self.assertAlmostEqual(r['timeline'][0]['value_per_share'],pv)
 def test_ex_dividend_rollforward(self):
  r=base(value_company(fixture()));vs={x['date']:x['value_per_share'] for x in r['timeline']}
  for p in r['forecast']:self.assertAlmostEqual(vs[p['start']],(vs[p['end']]+p['dividend_per_share'])/1.15**years(p['start'],p['end']))
 def test_dilution_reduces_owner_value(self):
  c=fixture();b=base(value_company(c))
  for s in c['scenarios']:s['periods'][0]['equity_issue_price']/=2
  a=base(value_company(c));self.assertGreater(a['forecast'][-1]['shares'],b['forecast'][-1]['shares']);self.assertLess(a['timeline'][0]['value_per_share'],b['timeline'][0]['value_per_share']);self.assertEqual(a['forecast'][0]['cash'],b['forecast'][0]['cash'])
 def test_advance_not_double_counted(self):
  c=fixture();b=base(value_company(c));c['scenarios'][1]['periods'][0]['revenue_from_prepayments']+=1e6;c['scenarios'][1]['periods'][1]['revenue_from_prepayments']-=1e6
  a=base(value_company(c));self.assertAlmostEqual(a['forecast'][0]['cfo'],b['forecast'][0]['cfo']-1e6);self.assertAlmostEqual(a['forecast'][1]['cash'],b['forecast'][1]['cash'])
 def test_impossible_recognition(self):
  c=fixture();c['scenarios'][0]['periods'][0]['revenue_from_prepayments']=1e12
  with self.assertRaisesRegex(ValueError,'Prepayment'):value_company(c)
 def test_unfunded_not_zero_or_free_equity(self):
  c=fixture();c['scenarios'][1]['periods'][0]['growth_capex']=10e9;r=value_company(c)
  self.assertEqual(r['status'],'funding_blocked');self.assertIsNone(r['annual_values'][0]['base'])
 def test_later_cash_does_not_erase_earlier_default(self):
  c=fixture();p=c['scenarios'][1]['periods'];p[0]['growth_capex']=3e9;p[1]['equity_proceeds']=10e9;p[1]['financing_status']='assumed'
  r=base(value_company(c));self.assertGreater(r['forecast'][-1]['cash'],0);self.assertEqual(r['status'],'funding_blocked')
 def test_sbc_double_charge(self):
  c=fixture();c['scenarios'][0]['sbc_policy']='cash_equivalent'
  with self.assertRaisesRegex(ValueError,'same future'):value_company(c)
 def test_cash_sbc_policy(self):
  c=fixture()
  for s in c['scenarios']:
   s['sbc_policy']='cash_equivalent'
   for p in s['periods']:p['sbc_shares']=0
  self.assertEqual(value_company(c)['status'],'conditional_valuation')
 def test_terminal_sbc(self):
  c=fixture();c['scenarios'][0]['terminal']['sbc_in_margin']=False
  with self.assertRaises(ValueError):value_company(c)
 def test_debt_cannot_be_negative(self):
  c=fixture();c['scenarios'][0]['periods'][0]['debt_repayment']=1e12
  with self.assertRaises(ValueError):value_company(c)
 def test_no_tax_refund_on_loss(self):
  c=fixture();c['scenarios'][1]['periods'][0]['fixed_cash_costs']=300e6;r=base(value_company(c))['forecast'][0]
  self.assertEqual(r['cash_taxes'],0);self.assertGreater(r['nol_remaining'],50e6)
 def test_terminal_growth_requires_capital(self):
  t=base(value_company(fixture()))['terminal'];self.assertAlmostEqual(t['next_year_fcff'],t['next_year_nopat']*(1-.025/.15))
 def test_invalid_terminal(self):
  for k,v in [('growth',.1),('roic',.01),('wacc',0)]:
   c=fixture();c['scenarios'][0]['terminal'][k]=v
   with self.assertRaises(ValueError):value_company(c)
 def test_nonfinite_and_bool(self):
  for v in (float('nan'),float('inf'),True):
   c=fixture();c['quote']['price']=v
   with self.assertRaises(ValueError):value_company(c)
 def test_future_evidence(self):
  for path in ('sources','assumptions'):
   c=fixture();c[path][0]['available_at' if path=='sources' else 'as_of']='2030-01-01'
   with self.assertRaises(ValueError):value_company(c)
 def test_missing_refs(self):
  c=fixture();c['scenarios'][0]['periods'][0]['assumption_ids']=[]
  with self.assertRaises(ValueError):value_company(c)
 def test_stale_quote(self):
  c=fixture();c['quote']['as_of']='2026-01-01';self.assertTrue(any('Quote' in w for w in value_company(c)['warnings']))
 def test_period_gap_overlap(self):
  for start in ('2026-12-30','2027-01-01'):
   c=fixture();c['scenarios'][0]['periods'][1]['start']=start
   with self.assertRaises(ValueError):value_company(c)
 def test_stale_opening(self):
  c=fixture();c['opening']['as_of']='2026-06-30'
  with self.assertRaisesRegex(ValueError,'bridged'):value_company(c)
 def test_sector_fit(self):
  c=fixture();c['archetype']='financials'
  with self.assertRaises(ValueError):value_company(c)
 def test_synthetic_label(self):
  c=fixture();c['is_demo']=False
  with self.assertRaises(ValueError):value_company(c)
 def test_unreviewed(self):
  c=fixture();c['assumptions'][0]['review_status']='unreviewed';self.assertEqual(value_company(c)['status'],'unreviewed')
 def test_bank_residual_formula(self):
  r=base(value_company(fixture('bank')));b=r['terminal']['book_equity'];self.assertAlmostEqual(r['terminal']['equity_value'],b+(.13-.11)*b/(.11-.025),delta=1e-6)
 def test_bank_capital_shortfall(self):
  c=fixture('bank');c['scenarios'][1]['periods'][0]['risk_weighted_assets']=100e9;self.assertIsNone(value_company(c)['annual_values'][0]['base'])
 def test_nav_formula(self):self.assertAlmostEqual(value_company(fixture('reit'))['annual_values'][1]['base'],(18e6*1.03/.06+10e6-100e6-5e6)/10e6)
 def test_sotp_claim_bridge(self):
  c=fixture('conglomerate');c['scenarios'][1]['periods'][0]['components'][0]['ownership']=.5
  self.assertAlmostEqual(value_company(c)['annual_values'][1]['base'],((1e9+20e6-100e6)*.5+50e6-10e6-20e6)/50e6)
 def test_duplicate_components(self):
  c=fixture('conglomerate');p=c['scenarios'][0]['periods'][0];p['components'].append(deepcopy(p['components'][0]))
  with self.assertRaisesRegex(ValueError,'Duplicate component'):value_company(c)
 def test_rnpv_cost_and_success_have_different_weights(self):
  c=fixture('biotech');d=c['as_of'];v=(1e9*.3/1.12**years(d,'2032-12-31')-50e6/1.12**years(d,'2031-12-31')+50e6-10e6-20e6)/50e6
  self.assertAlmostEqual(value_company(c)['annual_values'][0]['base'],v)
 def test_rnpv_bad_probability(self):
  c=fixture('biotech');c['opening']['components'][0]['cashflows'][0]['probability']=1.2
  with self.assertRaises(ValueError):value_company(c)
 def test_sensitivity_direction(self):
  cells=sensitivity(fixture(),growth_shifts=(0,));self.assertGreater(cells[0]['value_today'],cells[1]['value_today']);self.assertGreater(cells[1]['value_today'],cells[2]['value_today'])
 def test_stress_dilution(self):
  r=stress_test(fixture());self.assertEqual(len(r['stresses']),5);s=next(s for s in r['stresses'] if s['shock']=='equity_issue_price_down_30pct');self.assertLess(s['annual_values'][0]['base'],r['baseline'][0]['base'])
 def test_reverse_reconstruction(self):
  c=fixture();c['quote']['price']=value_company(c)['annual_values'][0]['base'];r=reverse_price(c,.9,1.1);self.assertEqual(r['status'],'solved');self.assertAlmostEqual(r['multiplier'],1)
 def test_purity_and_hash(self):
  c=fixture();old=deepcopy(c);r=value_company(c);self.assertEqual(c,old);self.assertEqual(r['input_sha256'],value_company(c)['input_sha256']);c['quote']['price']+=1;self.assertNotEqual(r['input_sha256'],value_company(c)['input_sha256'])
 def test_html_escaping(self):
  c=fixture();c['ticker']='<script>alert(1)</script>';h=render_valuation(value_company(c));self.assertNotIn('<script>',h);self.assertIn('&lt;script&gt;',h)
 def test_mcp(self):
  with tempfile.TemporaryDirectory() as d:self.assertEqual(len(Server(str(Path(d)/'sources.sqlite')).call('value_company',{'case':fixture()})['annual_values']),6)
 def test_asset_replacement(self):
  c=[{'id':'gpu','in_service':'2026-01-01','cost':120,'salvage_fraction':0,'depreciation_years':3,'replacement_years':2,'replacement_cost_growth':0}]
  r=asset_schedule(c,['2026-01-01','2027-01-01','2028-01-01','2029-01-01']);self.assertAlmostEqual(r[0]['depreciation'],120*365/1096);self.assertEqual(r[0]['replacement_capex'],0);self.assertEqual(r[1]['replacement_capex'],120)
 def test_forecast_no_lookahead(self):
  o={'company_id':'x','metric':'revenue','target_date':'2026-12-31','forecast_as_of':'2026-01-01','actual_available_at':'2027-02-01','forecast_currency':'USD','actual_currency':'USD','forecast_basis':'GAAP','actual_basis':'GAAP','forecast':100,'actual':80,'low':70,'high':110}
  r=forecast_score([o]);self.assertEqual(r['mean_absolute_error'],20);self.assertEqual(r['interval_coverage'],1);o['forecast_as_of']='2027-01-01'
  with self.assertRaises(ValueError):forecast_score([o])
if __name__=='__main__':unittest.main()

class IntegrationAuditTests(unittest.TestCase):
 def annual_dossier(self):
  d=json.loads((ROOT/'examples/research_dossier_demo.json').read_text())
  a=fixture();d['as_of']=a['as_of'];d['financial_input']['as_of']=a['as_of']
  a['company_id']=d['identity']['company_id'];a['ticker']=d['identity']['ticker']
  src=d['sources'][-1];a['sources']=[{'id':src['id'],'title':src['title'],'url':src['locator'],'kind':src['kind'],'published_at':src['published_at'],'available_at':src['available_at']}]
  a['quote']['source_ids']=[src['id']];a['assumptions'][0]['source_ids']=[src['id']]
  d['annual_valuation_input']=a
  d['candidate_conclusion']={'classification':'watchlist','valuation_basis':{'model':'annual_path','scenario':'base'},'rationale':'Synthetic integration check only.'}
  return d
 def test_supervisor_executes_annual_model(self):
  from fundamental_engine.supervisor import review
  r=review(self.annual_dossier());self.assertEqual(r['annual_valuation']['status'],'conditional_valuation');self.assertEqual(r['candidate_conclusion']['valuation_basis']['model'],'annual_path')
 def test_supervisor_rejects_source_substitution(self):
  from fundamental_engine.supervisor import review
  d=self.annual_dossier();d['annual_valuation_input']['sources'][0]['url']='https://unrelated.invalid'
  with self.assertRaisesRegex(ValueError,'provenance'):review(d)
 def test_quote_cannot_manipulate_intrinsic_value(self):
  c=fixture();b=value_company(c)['annual_values'][0]['base'];c['quote']['price']*=3
  self.assertEqual(value_company(c)['annual_values'][0]['base'],b)
 def test_crossed_scenarios_not_silently_sorted(self):
  c=fixture();c['scenarios'][0]['cost_of_equity']=.001;c['scenarios'][0]['terminal']['wacc']=.026
  r=value_company(c);self.assertTrue(any('cross' in w for w in r['warnings']));self.assertGreater(r['annual_values'][0]['bear'],r['annual_values'][0]['base'])
 def test_invalid_funding_status(self):
  c=fixture();c['scenarios'][0]['periods'][0]['financing_status']='none'
  with self.assertRaises(ValueError):value_company(c)
 def test_derived_overflow_rejected(self):
  c=fixture();c['scenarios'][0]['periods'][0]['segments'][0]['average_units']=1e308
  with self.assertRaises((ValueError,OverflowError)):value_company(c)
 def test_mixed_metric_evaluation_rejected(self):
  o={'company_id':'x','metric':'revenue','target_date':'2026-12-31','forecast_as_of':'2026-01-01','actual_available_at':'2027-02-01','forecast_currency':'USD','actual_currency':'USD','forecast_basis':'GAAP','actual_basis':'GAAP','forecast':100,'actual':80,'low':70,'high':110}
  b=dict(o,metric='shares')
  with self.assertRaisesRegex(ValueError,'one metric'):forecast_score([o,b])
 def test_asset_bad_dates_and_lives(self):
  with self.assertRaises(ValueError):asset_schedule([],['2030-01-01','2026-01-01'])
 def test_specialist_opening_scenarios(self):
  c=fixture('reit');c['scenarios'][0]['opening_snapshot']=dict(c['opening'],asset_value=200e6)
  r=value_company(c);self.assertLess(r['annual_values'][0]['bear'],r['annual_values'][0]['base'])
