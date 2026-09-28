import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.growth import momentum, choose_lane, payoff, lane_verdict, required_revenue, multiples_view
from fundamental_engine.valuation import value_company
from fundamental_engine.plain import plain_verdict
ROOT=Path(__file__).resolve().parents[1]
def load(p):return json.loads((ROOT/p).read_text())
def row(y,rev,**kw):
    r={'fiscal_year':f'FY{y}','period_end':f'{y}-12-31','revenue':rev,'source_ids':['s']};r.update(kw);return r


class MomentumTests(unittest.TestCase):
    def test_palantir_point_in_time(self):
        doc=load('examples/real/pltr_momentum.json')
        early,later=[momentum({'as_of':s['as_of'],'recent_quarters':s['recent_quarters']}) for s in doc['snapshots']]
        self.assertEqual(early['label'],'weak')
        self.assertEqual(later['label'],'strong')
        self.assertEqual({s['key'] for s in later['signals'] if s['score']>0},{'acceleration','margin_expansion'})

    def test_beats_revisions_and_guards(self):
        qs=[{'period_end':f'202{4+i//4}-{3*(i%4)+3:02d}-{30 if (i%4) in (1,2) else 31}','revenue':100+10*i,'source_ids':['s']} for i in range(8)]
        c={'as_of':'2026-09-28','recent_quarters':qs,
           'expectations_track':[{'period_end':'2025-12-31','revenue':170,'guidance_midpoint':160,'next_guidance_midpoint':180,'prior_next_expectation':170,'source_ids':['g']}],
           'estimate_revisions':[{'as_of':'2025-06-30','value':600,'source_ids':['c']},{'as_of':'2026-06-30','value':700,'source_ids':['c']}]}
        m=momentum(c);keys={s['key']:s['score'] for s in m['signals']}
        self.assertEqual(keys['beats'],1);self.assertEqual(keys['revisions'],1)
        c['estimate_revisions'][1]['as_of']='2027-01-01'
        with self.assertRaisesRegex(ValueError,'estimate_revisions'):momentum(c)


class LaneTests(unittest.TestCase):
    def kind(self,stage,flags=()):return {'stage':stage,'flags':[{'key':f} for f in flags]}

    def test_nvidia_like_is_intrinsic_axti_is_potential(self):
        nv=[row(2023+i,1e10*(2**i),operating_income=.5e10*(2**i),operating_cash_flow=.45e10*(2**i),capex=.03e10*(2**i)) for i in range(3)]
        self.assertEqual(choose_lane(self.kind('high_growth'),nv)['lane'],'intrinsic')
        losing=[row(2024,100,operating_income=-30,operating_cash_flow=-20,capex=5),row(2025,160,operating_income=-10,operating_cash_flow=-5,capex=5)]
        self.assertEqual(choose_lane(self.kind('young_growth'),losing)['lane'],'potential')
        self.assertEqual(choose_lane(self.kind('high_growth',['inflection']),nv)['lane'],'potential')

    def test_payoff_math_and_validation(self):
        today={'bear':1,'base':8,'bull':24,'tail':72}
        p=payoff(today,75,{'bear':.3,'base':.4,'bull':.25,'tail':.05})
        self.assertAlmostEqual(p['expected_value'],.3+3.2+6+3.6)
        self.assertEqual(p['probability_below_price'],1);self.assertFalse(p['probabilities_are_default'])
        self.assertTrue(payoff(today,75)['probabilities_are_default'])
        with self.assertRaisesRegex(ValueError,'sum'):payoff(today,75,{'bear':.5,'base':.5,'bull':.5,'tail':0})
        with self.assertRaisesRegex(ValueError,'cover'):payoff({'bear':1,'base':8,'bull':24},75,{'bear':.3,'base':.4,'bull':.25,'tail':.05})

    def test_verdict_matrix(self):
        today={'bear':5,'base':10,'bull':30,'tail':120}
        strong,neutral,weak=({'label':x} for x in ('strong','neutral','weak'))
        pay=lambda q:payoff(today,q,{'bear':.25,'base':.4,'bull':.25,'tail':.1})
        self.assertIn('מוצדק',lane_verdict('potential',strong,pay(20),today,20)['call'])
        self.assertEqual(lane_verdict('potential',neutral,pay(20),today,20)['light'],'🟡')
        self.assertIn('בלי ראיות',lane_verdict('potential',weak,pay(20),today,20)['call'])
        self.assertIn('ההצלחה הגדולה',lane_verdict('potential',strong,pay(60),today,60)['call'])
        self.assertEqual(lane_verdict('potential',strong,pay(200),today,200)['light'],'🔴')
        self.assertEqual(lane_verdict('intrinsic',strong,pay(20),today,20)['light'],'🟠')

    def test_required_revenue_formula(self):
        self.assertAlmostEqual(required_revenue(1000,100,.1,.2,tax=.2,exit_pe=25,years=5),900*1.1**5/(.2*.8*25))


class IntegrationTests(unittest.TestCase):
    def test_tail_scenario_executes_and_is_checked(self):
        v=load('examples/valuation/infrastructure.json')
        tail=deepcopy(next(s for s in v['scenarios'] if s['name']=='bull'));tail['name']='tail'
        for p in tail['periods']:p['segments'][0]['annual_revenue_per_unit']*=1.5
        v['scenarios'].append(tail);r=value_company(v)
        self.assertGreater(r['annual_values'][0]['tail'],r['annual_values'][0]['bull'])
        low=deepcopy(next(s for s in v['scenarios'] if s['name']=='bear'));low['name']='tail';v['scenarios'][-1]=low
        self.assertTrue(any('tail value below bull' in w for w in value_company(v)['warnings']))
        dup=load('examples/valuation/infrastructure.json');dup['scenarios'].append(deepcopy(dup['scenarios'][0]))
        with self.assertRaisesRegex(ValueError,'unique'):value_company(dup)

    def test_plain_uses_lane_for_inflecting_company(self):
        c=load('examples/real/axti_plain_2026q2.json')
        r=plain_verdict(c)
        self.assertEqual(r['valuation_lane']['lane']['lane'],'potential')
        self.assertEqual(r['valuation_lane']['momentum']['label'],'strong')
        self.assertIsNone(r['valuation_lane']['payoff'])

    def test_multiples_view(self):
        v=load('examples/valuation/infrastructure.json');r=value_company(v)
        r['_cost_of_equity']={s['name']:s['cost_of_equity'] for s in v['scenarios']}
        m=multiples_view(r,20)
        base=next(x for x in m['rows'] if x['scenario']=='base')
        self.assertAlmostEqual(base['price_in_year_at_pe'][25],base['eps']*25)


if __name__=='__main__':
    unittest.main()
