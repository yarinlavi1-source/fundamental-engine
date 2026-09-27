import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.discovery import scan, compare_discovery, frontier_plan
from fundamental_engine.mcp import Server
from fundamental_engine.supervisor import plan
ROOT=Path(__file__).resolve().parents[1]
def fixture():return json.loads((ROOT/'examples/discovery_demo.json').read_text())
def op(c):return scan(c)['opportunities'][0]
class DiscoveryTests(unittest.TestCase):
 def test_loss_making_company_is_researchable(self):
  c=fixture();self.assertLess(c['financial_context']['net_income'],0);self.assertEqual(op(c)['research_lane'],'underwrite_early_growth')
 def test_no_valuation_required_for_discovery(self):
  c=fixture();self.assertNotIn('quote',c);self.assertEqual(op(c)['evidenced_stage'],'paying_customers')
 def test_no_entry_permission(self):
  r=scan(fixture());self.assertFalse(r['policy']['discovery_is_entry_permission']);self.assertEqual(r['opportunities'][0]['investment_status'],'valuation_and_funding_underwriting_required')
 def test_hypothesis_kept_without_adoption(self):
  c=fixture();c['opportunities'][0]['observation_ids']=[];r=op(c);self.assertEqual(r['research_lane'],'explore');self.assertEqual(r['evidenced_stage'],'hypothesis')
 def test_pilot_not_paid_adoption(self):
  c=fixture();c['opportunities'][0]['observation_ids']=['pilot'];r=op(c);self.assertEqual(r['evidenced_stage'],'pilot');self.assertEqual(r['research_lane'],'monitor_progress')
 def test_design_win_not_revenue(self):
  c=fixture();c['observations'][0]['role']='design_win';c['opportunities'][0]['observation_ids']=['pilot'];self.assertEqual(op(c)['evidenced_stage'],'design_win')
 def test_famous_endorsement_not_adoption(self):
  c=fixture()
  for o in c['observations']:o['kind']='opinion'
  self.assertEqual(op(c)['evidenced_stage'],'hypothesis')
 def test_guidance_not_actual(self):
  c=fixture()
  for o in c['observations']:o['kind']='guidance'
  self.assertEqual(op(c)['research_lane'],'explore')
 def test_unreviewed_does_not_promote(self):
  c=fixture()
  for o in c['observations']:o['reviewed']=False
  self.assertEqual(op(c)['research_lane'],'explore')
 def test_demand_without_capture_is_not_underwritten(self):
  c=fixture();c['opportunities'][0]['observation_ids']=['paid_adoption'];self.assertEqual(op(c)['value_capture'],'unproven');self.assertNotEqual(op(c)['research_lane'],'underwrite_early_growth')
 def test_counterevidence_remains_visible(self):
  c=fixture();o=deepcopy(c['observations'][1]);o.update(id='cancel',direction='opposes');c['observations'].append(o);c['opportunities'][0]['observation_ids'].append('cancel')
  r=op(c);self.assertEqual(r['research_lane'],'resolve_counterevidence');self.assertIn('paid_adoption',r['supported_roles']);self.assertIn('paid_adoption',r['opposing_roles'])
 def test_future_observation_excluded(self):
  c=fixture();c['observations'][1]['observed_at']=c['observations'][1]['reviewed_at']='2027-01-01'
  r=scan(c);self.assertIn('paid_adoption',r['excluded_ids']);self.assertEqual(r['opportunities'][0]['evidenced_stage'],'pilot')
 def test_future_source_excluded(self):
  c=fixture();c['sources'][0]['available_at']='2027-01-01';self.assertEqual(op(c)['research_lane'],'repair_evidence_path')
 def test_future_gap_is_not_observed_fact(self):
  c=fixture();c['opportunities'][0]['pressure_cases'][0]['kind']='observed'
  with self.assertRaises(ValueError):scan(c)
 def test_hypothetical_gap_calculation(self):
  p=op(fixture())['pressure_cases'][0];self.assertEqual(p['shortfall'],50);self.assertEqual(p['demand_to_supply'],1.5);self.assertEqual(p['status'],'conditional_shortfall')
 def test_zero_supply_is_not_infinite_probability(self):
  c=fixture();c['opportunities'][0]['pressure_cases'][0]['supply']=0;self.assertIsNone(op(c)['pressure_cases'][0]['demand_to_supply'])
 def test_repeated_articles_do_not_inflate_origins(self):
  c=fixture();o=deepcopy(c['observations'][0]);o['id']='copy';c['observations'].append(o);c['opportunities'][0]['observation_ids'].append('copy');self.assertEqual(op(c)['distinct_source_origins'],['fictional-original'])
 def test_disconnected_path_rejected(self):
  c=fixture();c['opportunities'][0]['path']=['e0','e2']
  with self.assertRaises(ValueError):scan(c)
 def test_missing_buyer_rejected(self):
  c=fixture();c['opportunities'][0]['payer']=''
  with self.assertRaises(ValueError):scan(c)
 def test_metric_trajectory(self):self.assertEqual(scan(fixture())['trajectories'][0]['status'],'improving')
 def test_metric_deterioration(self):
  c=fixture()
  for p in c['trajectories'][0]['points']:p['value']*=-1
  self.assertEqual(scan(c)['trajectories'][0]['status'],'deteriorating')
 def test_metric_mismatched_scope(self):
  c=fixture();c['trajectories'][0]['points'][0]['scope']='different company'
  with self.assertRaises(ValueError):scan(c)
 def test_quarter_vs_year_not_comparable(self):
  c=fixture();c['trajectories'][0]['points'][0]['period_days']=365;self.assertEqual(scan(c)['trajectories'][0]['status'],'insufficient_comparable_data')
 def test_progress_comparison(self):
  a=fixture();a['opportunities'][0]['observation_ids']=['pilot'];r=compare_discovery(a,fixture());self.assertEqual(r['changes'][0]['before']['evidenced_stage'],'pilot');self.assertEqual(r['changes'][0]['after']['evidenced_stage'],'paying_customers')
 def test_no_cross_security_comparison(self):
  a=fixture();b=fixture();b['company_id']='other'
  with self.assertRaises(ValueError):compare_discovery(a,b)
 def test_frontier_plan_without_ticker(self):
  r=frontier_plan({'theme':'Agent permissions','as_of':'2026-09-27'});self.assertEqual(len(r['tasks']),6)
 def test_research_plan_new_triggers(self):
  r=plan({'ticker':'X','as_of':'2026-09-27','question':'Emerging constraint','archetypes':['software'],'triggers':['bottleneck']});self.assertIn('frontier_research',[p['id'] for p in r['packets']])
 def test_mcp_discovery(self):self.assertEqual(Server('unused.sqlite').call('discovery_scan',{'case':fixture()})['ticker'],'DEMO-EARLY')
 def test_no_input_mutation(self):
  c=fixture();before=deepcopy(c);scan(c);self.assertEqual(c,before)
 def test_synthetic_cannot_be_relabelled(self):
  c=fixture();c['is_demo']=False
  with self.assertRaises(ValueError):scan(c)
 def test_stale_not_current(self):
  c=fixture();c['as_of']='2030-01-01';r=scan(c);self.assertIn('paid_adoption',r['stale_observation_ids'])
 def test_unit_economics_needs_repeat_and_scale(self):
  c=fixture();c['observations'][0]['role']='unit_economics';self.assertNotEqual(op(c)['evidenced_stage'],'demonstrated_unit_economics')

class DiscoveryDossierTests(unittest.TestCase):
 def dossier(self):
  d=json.loads((ROOT/'examples/research_dossier_demo.json').read_text());a=fixture();d['as_of']=a['as_of'];d['financial_input']['as_of']=a['as_of'];a['company_id']=d['identity']['company_id'];a['ticker']=d['identity']['ticker']
  src=d['sources'][-1];a['sources']=[dict(id='demo',title=src['title'],url=src['locator'],kind=src['kind'],origin_id=src['origin_id'],published_at=src['published_at'],available_at=src['available_at'])]
  d['sources'].append(dict(src,id='demo'))
  d['discovery_input']=a
  return d
 def test_dossier_discovery_survives_incomplete_valuation(self):
  from fundamental_engine.supervisor import review
  d=self.dossier();d['financial_input'].pop('valuation',None);d['financial_input'].pop('ownership_valuation',None);d['candidate_conclusion']={'classification':'watchlist'}
  r=review(d);self.assertEqual(r['discovery']['opportunities'][0]['research_lane'],'underwrite_early_growth')
 def test_dossier_identity_mismatch(self):
  from fundamental_engine.supervisor import review
  d=self.dossier();d['discovery_input']['ticker']='OTHER'
  with self.assertRaisesRegex(ValueError,'identity'):review(d)
 def test_dossier_origin_substitution(self):
  from fundamental_engine.supervisor import review
  d=self.dossier();d['discovery_input']['sources'][0]['origin_id']='invented-independent'
  with self.assertRaisesRegex(ValueError,'provenance'):review(d)

if __name__=='__main__':unittest.main()
