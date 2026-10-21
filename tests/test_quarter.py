import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.quarter import quarter_review
from fundamental_engine.mcp import Server
from fundamental_engine.supervisor import plan, packet
ROOT=Path(__file__).resolve().parents[1]
def load(name):return json.loads((ROOT/'examples/quarter'/name).read_text())
def keys(r):return {f['key'] for f in r['flags']}
S=['s']
def base(**kw):
    c={'company':'X','period_end':'2026-06-30','as_of':'2026-07-31','currency':'USD','source_ids':S,
       'current':{'revenue':120.,'operating_income':12.,'net_income':20.,'diluted_shares':10.},'prior_year':{'revenue':100.,'operating_income':10.}}
    c.update(kw);return c


class NormalizationTests(unittest.TestCase):
    def test_symmetric_after_tax_and_recurring_kept(self):
        c=base(one_offs=[{'label':'stake gain','kind':'gain','amount':10,'tax_rate':.2,'source_ids':S},
                         {'label':'settlement','kind':'charge','amount':5,'pretax':False,'source_ids':S},
                         {'label':'annual restructuring','kind':'charge','amount':3,'pretax':False,'recurring':True,'source_ids':S}])
        n=quarter_review(c)['normalization']
        self.assertAlmostEqual(n['normalized_net_income'],20-8+5)
        self.assertAlmostEqual(n['normalized_eps'],1.7)
        self.assertEqual([k['label'] for k in n['kept']],['annual restructuring'])

    def test_no_default_tax_and_unusual_opex_added_back(self):
        with self.assertRaisesRegex(ValueError,'tax_rate'):
            quarter_review(base(one_offs=[{'kind':'gain','amount':1,'source_ids':S}]))
        c=base(unusual_opex=[{'label':'legal','amount':4,'source_ids':S}])
        c['current']['operating_expenses']=50.;c['prior_year']['operating_expenses']=40.
        with self.assertRaisesRegex(ValueError,'unusual_opex'):quarter_review(c)
        c['unusual_opex'][0]['tax_rate']=.25
        r=quarter_review(c)
        self.assertAlmostEqual(r['normalization']['normalized_net_income'],23)
        self.assertAlmostEqual(r['operating_leverage']['opex_growth_ex_unusual'],.15)
        self.assertAlmostEqual(r['operating_leverage']['incremental_operating_margin'],.1)

    def test_uber_example_headline_and_liquidity(self):
        r=quarter_review(load('uber_2025q3.json'))
        self.assertLess(r['normalization']['normalized_net_income'],1e9)
        self.assertTrue({'headline_distorted','restricted_not_spendable','adjusted_vs_reported_gap','no_expectations_data'}<=keys(r))
        self.assertEqual(r['liquidity']['spendable_liquidity'],8.9e9)
        self.assertAlmostEqual(r['liquidity']['net_cash'],-1.7e9)
        self.assertIsNone(r['liquidity']['convertible_share_of_debt'])
        self.assertGreater(r['operating_leverage']['spread_ex_unusual'],0)
        self.assertLess(r['operating_leverage']['spread_reported'],0)


class CycleListingBankTests(unittest.TestCase):
    def test_skhy_peak_run_rate_and_adr_premium(self):
        r=quarter_review(load('skhy_2026q2.json'))
        self.assertTrue({'peak_margin_run_rate','listing_premium','headline_distorted','unanchored_multiples'}<=keys(r))
        self.assertEqual(r['market']['run_rate_basis'],'peak_quarter_x4')
        self.assertAlmostEqual(r['market']['other_listing']['premium'],.245,places=2)
        self.assertLess(r['multiple_scenarios'][0]['implied_annual_return'],0)
        self.assertEqual(r['currency_conversion']['reporting_currency'],'KRW')
        c=load('skhy_2026q2.json');del c['fx']
        with self.assertRaisesRegex(ValueError,'fx'):quarter_review(c)

    def test_sofi_bank_rote_math(self):
        r=quarter_review(load('sofi_2026q2.json'))
        b=r['bank']
        self.assertAlmostEqual(b['justified_price_to_tangible_book'],.75)
        self.assertAlmostEqual(b['rote_implied_by_price'],.04+15.5/7.34*.06)
        self.assertAlmostEqual(b['excess_capital_vs_target']/1e9,.053*48.7)
        self.assertTrue({'bank_price_requires_higher_rote','cumulative_kpi','segment_margin_compression','reinvesting_upside','equity_from_marks'}<=keys(r))
        lending=next(s for s in r['segments'] if s['name']=='Lending')
        self.assertGreater(lending['share_of_positive_profit'],.6)
        c=load('sofi_2026q2.json');c['bank']['growth']=.12
        with self.assertRaisesRegex(ValueError,'cost_of_equity'):quarter_review(c)


class ExpectationsTests(unittest.TestCase):
    def test_guidance_direction_consensus_and_track(self):
        c=base(guidance=[{'metric':'revenue','low':130,'high':134,'prior_low':126,'prior_high':130,'consensus':135,'comparable_actual':110,'source_ids':S}],
               guidance_track=[{'low':100,'high':104,'actual':110,'same_basis':True,'source_ids':S}])
        with self.assertRaisesRegex(ValueError,'consensus_source_ids'):quarter_review(c)
        c['guidance'][0]['consensus_source_ids']=['c']
        r=quarter_review(c);g=r['guidance'][0]
        self.assertEqual(g['direction'],'raised');self.assertLess(g['vs_consensus'],0)
        self.assertAlmostEqual(g['implied_growth'],132/110-1)
        self.assertIn('guide_below_consensus',keys(r));self.assertNotIn('no_expectations_data',keys(r))
        self.assertTrue(r['guidance_track']['small_sample'])
        c['guidance_track'][0]['same_basis']=False
        with self.assertRaisesRegex(ValueError,'same_basis'):quarter_review(c)

    def test_sn_rate_guidance_concentration_falsifiers(self):
        r=quarter_review(load('sn_2025.json'))
        self.assertEqual(r['guidance_track']['pattern'],'conservative')
        self.assertAlmostEqual(r['guidance_track']['mean_surprise_vs_midpoint'],(.24+.047)/2)
        self.assertIn('customer_concentration',keys(r))
        self.assertEqual({f['id']:f['status'] for f in r['falsifiers']},{'rnd_intensity':'intact','gross_margin':'intact'})
        c=load('sn_2025.json');c['falsifiers'][1]['current']=.45
        self.assertIn('thesis_falsifier_broken',keys(quarter_review(c)))

    def test_cumulative_kpi_and_sources_required(self):
        c=base(kpis=[{'name':'products','value':5,'prior':4,'basis':'cumulative','source_ids':S}])
        self.assertIn('cumulative_kpi',keys(quarter_review(c)))
        c['kpis'][0]['basis']='nice'
        with self.assertRaisesRegex(ValueError,'basis'):quarter_review(c)
        with self.assertRaisesRegex(ValueError,'source_ids'):quarter_review(base(source_ids=[]))
        with self.assertRaisesRegex(ValueError,'unknown line'):quarter_review(base(current={'revenue':1,'hype':2}))


class IntegrationTests(unittest.TestCase):
    def test_mcp_plan_packet_and_text(self):
        r=Server('unused.sqlite').call('quarter_review',{'case':load('uber_2025q3.json')})
        self.assertIn('אחרי ניקוי חד-פעמיים',r['text']);self.assertEqual(len(r['input_sha256']),64)
        p=plan({'ticker':'X','as_of':'2026-10-01','question':'results','triggers':['earnings_event']})
        self.assertIn('quarter_review',[x['id'] for x in p['packets']])
        self.assertIn('falsif',packet('quarter_review')['content'].lower())
