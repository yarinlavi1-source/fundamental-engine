"""allocation.py: the index is the default; active weight only for an edge after haircuts, sized by edge and downside,
capped per name and per shared driver. Rows are SYNTHETIC comparison rows (handoff.compare shape)."""
import unittest

from fundamental_engine import allocation as A


def row(sym, base, bear_loss, conf='medium', err='medium', disq=False, reviewed=True, bear=None, bull=None):
    return {'symbol': sym, 'irr': {'bear': bear if bear is not None else base - .04, 'base': base, 'bull': bull if bull is not None else base + .04},
            'index_expected_return': .0875, 'loss_to_bear': bear_loss, 'confidence': conf, 'valuation_error_size': err,
            'material_disqualifier': disq, 'judgement': 'excluded until resolved: data' if disq else 'ok',
            'price_claim_withheld': not reviewed, 'review_status': 'passed' if reviewed else 'unreviewed_draft'}


class ConstructTests(unittest.TestCase):
    def test_no_edge_means_index(self):
        plan = A.construct([row('A', .085, .4), row('B', .07, .3)])
        self.assertEqual(plan['active_weight'], 0.)
        self.assertEqual(plan['index_weight'], 1.)
        self.assertAlmostEqual(plan['expected_return']['portfolio_base'], .0875)

    def test_size_grows_with_edge_and_shrinks_with_downside_and_confidence(self):
        plan = A.construct([row('BIG', .16, .3), row('RISKY', .16, .6), row('UNSURE', .16, .3, conf='low')])
        w = {t['symbol']: t['target_weight'] for t in plan['targets']}
        self.assertGreater(w['BIG'], w['RISKY'])
        self.assertGreater(w['BIG'], w['UNSURE'])
        self.assertLessEqual(max(w.values()), A.DEFAULTS['single_name_cap'] + 1e-12)

    def test_disqualified_and_drafts(self):
        plan = A.construct([row('BAD', .25, .2, disq=True), row('DRAFT', .16, .3, reviewed=False)])
        t = {x['symbol']: x for x in plan['targets']}
        self.assertEqual(t['BAD']['target_weight'], 0.)
        self.assertEqual(t['BAD']['status'], 'excluded')
        self.assertGreater(t['DRAFT']['target_weight'], 0.)
        self.assertTrue(plan['provisional'])
        self.assertIn('PROVISIONAL', t['DRAFT']['reading'])

    def test_shared_driver_is_capped_together(self):
        rows = [row(s, .20, .25, conf='high', err='low') for s in ('C1', 'C2', 'C3', 'C4')]
        plan = A.construct(rows, exposures={s: ['ai_capex'] for s in ('C1', 'C2', 'C3', 'C4')})
        self.assertLessEqual(plan['driver_exposure']['ai_capex'], A.DEFAULTS['driver_cap'] + 1e-9)

    def test_current_overweights_are_listed_for_review_not_ordered(self):
        plan = A.construct([row('HELD', .07, .5)], current={'HELD': .2})
        self.assertEqual(plan['holdings_to_review'], ['HELD'])
        self.assertIn('not an order', plan['meaning'])
        self.assertIn('לא פקודה', A.render_he(plan))


if __name__ == '__main__':
    unittest.main()
