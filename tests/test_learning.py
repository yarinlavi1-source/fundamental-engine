"""learning.py: what research rounds teach the engine. Company folders below are SYNTHETIC (tickers S1..S4)."""
import json
import os
import tempfile
import unittest

from fundamental_engine import learning


def company(root, ticker, bm, overrides, special=None, warnings=()):
    d = os.path.join(root, ticker)
    os.makedirs(os.path.join(d, 'out'))
    cf = {'company': {'ticker': ticker}, 'card': {'business_model': {'primary': bm}, 'stage': {'value': 'growth'}},
          'dc': {'verdict': {'code': 'fair'}, 'valuation_view': {'price': 10., 'base': 11., 'bear': 7., 'bull': 15.}},
          'fc': {'overrides': [{'path': p, 'scenario': s, 'original': o, 'value': v, 'reason': 'r', 'evidence_ids': ['e']}
                               for p, s, o, v in overrides],
                 'warnings': [{'code': c, 'severity': 'medium'} for c in warnings]}, 'errors': []}
    with open(os.path.join(d, 'out', 'casefile.json'), 'w') as f:
        json.dump(cf, f)
    with open(os.path.join(d, 'inputs.json'), 'w') as f:
        json.dump({'forecast': special or {}, 'analyst_sheet': {'mispricing': {}}}, f)


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.round = os.path.join(self.tmp.name, 'research_SYN')
        os.makedirs(self.round)
        for t in ('S1', 'S2', 'S3'):           # three industrials whose capital needs the engine overstated
            company(self.round, t, 'industrial', [('sales_to_capital', 'base', 2.5, 5.0), ('mature_margin', 'base', .2, .15)],
                    warnings=('margin_floor',))
        company(self.round, 'S4', 'platform_marketplace', [('sales_to_capital', 'base', 2.2, 3.5)],
                special={'operating_funding': {'amount': 1., 'reason': 'deposits', 'evidence_ids': ['e']},
                         'nonrecurring': [{'kind': 'acquired_iprd', 'period_end': '2026-03-31', 'operating_income': -5., 'reason': 'r'}]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_corrections_directions_and_candidates_need_several_companies_of_one_type(self):
        rep = learning.report([self.round])
        self.assertEqual(rep['companies'], 4)
        s2c = next(x for x in rep['corrections']['by_lever'] if x['path'] == 'sales_to_capital')
        self.assertEqual(s2c['directions'], {'raised': 4})
        cands = rep['candidates']
        self.assertEqual({(c['path'], c['business_model'], c['direction']) for c in cands},
                         {('sales_to_capital', 'industrial', 'raised'), ('mature_margin', 'industrial', 'lowered')})
        self.assertNotIn('platform_marketplace', {c['business_model'] for c in cands})     # one company is judgement, not a rule

    def test_special_inputs_warnings_and_fix_log(self):
        log = {'rounds': [{'round': 'research_SYN', 'companies': ['S1', 'S2', 'S3', 'S4'],
                           'fixes': [{'kind': 'engine_bug'}, {'kind': 'missing_input'}]}]}
        rep = learning.report([self.round], None, log)
        self.assertEqual({(x['input'], x['kind']) for x in rep['special_inputs']},
                         {('operating_funding', None), ('nonrecurring', 'acquired_iprd')})
        self.assertEqual(rep['warnings'][0], {'code': 'margin_floor', 'count': 3, 'companies': ['S1', 'S2', 'S3']})
        self.assertEqual(rep['engine_fixes'][0]['fixes_per_company'], .5)
        self.assertEqual(rep['ledger'], {'status': 'no_ledger'})
        self.assertIn('לולאת למידה', learning.render_he(rep))


if __name__ == '__main__':
    unittest.main()
