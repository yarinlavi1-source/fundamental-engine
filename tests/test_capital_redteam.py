import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.capital import accreted_value, per_share, market_view, summarize, validate
from fundamental_engine.redteam import audit, FAILED, PASSED, LIMITED, UNVERIFIABLE

ROOT = Path(__file__).resolve().parents[1]
NBIS = json.loads((ROOT / 'examples/real/nbis_capital_2026-08-24.json').read_text())


def synth():
    return {'as_of': '2026-01-01', 'currency': 'USD',
            'cash': [{'id': 'c', 'kind': 'unrestricted', 'amount': 100.0, 'source_id': 's'}],
            'shares': [{'id': 'sh', 'kind': 'common', 'count': 10.0, 'source_id': 's'}],
            'debt': [{'id': 'cv', 'kind': 'convertible', 'principal': 100.0, 'conversion_price': 20.0, 'accretion_to': 1.0,
                      'issue_date': '2025-01-01', 'maturity': '2030-01-01', 'source_id': 's'}]}


def model_ok():
    return {'revenue_basis': 'recognized', 'backlog_treatment': 'probability_weighted', 'prepayments_treatment': 'working_capital_with_reversal',
            'margin_basis': 'ebit_after_sbc', 'sbc_treatment': 'expensed', 'capex_to_revenue_recent': 4.0, 'capex_linked_to_growth': True,
            'valuation_basis': 'fcff_dcf', 'funding_need': 20e9, 'financing_modeled': True, 'terminal_share': 0.7,
            'scenario_values': {'bear': 1, 'base': 2, 'bull': 3}}


class AccretionAndConversion(unittest.TestCase):
    def test_geometric_accretion_endpoints(self):
        inst = {'id': 'x', 'principal': 100.0, 'accretion_to': 1.21, 'issue_date': '2020-01-01', 'maturity': '2030-01-01'}
        self.assertAlmostEqual(accreted_value(inst, '2020-01-01'), 100.0)
        self.assertAlmostEqual(accreted_value(inst, '2030-01-01'), 121.0)
        self.assertAlmostEqual(accreted_value(inst, '2025-01-01'), 110.0, delta=0.05)   # sqrt(1.21) halfway
        self.assertAlmostEqual(accreted_value(dict(inst, accreted_value=113.0), '2025-01-01'), 113.0)

    def test_convert_stays_debt_when_out_of_money(self):
        # EV 50 + cash 100 - debt 100 = 50 equity / 10 sh = 5 < conversion 20 -> no conversion
        r = per_share(synth(), 50.0)
        self.assertEqual(r['converted'], [])
        self.assertAlmostEqual(r['value_per_share'], 5.0)

    def test_convert_converts_when_in_money_fixed_point(self):
        # EV 400: as debt -> (400+100-100)/10 = 40 > 20 -> convert: (400+100)/(10+5) = 33.33 > 20 consistent
        r = per_share(synth(), 400.0)
        self.assertEqual(r['converted'], ['cv'])
        self.assertAlmostEqual(r['value_per_share'], 500 / 15)

    def test_treasury_method_options(self):
        cs = synth(); cs['debt'] = []
        cs['shares'].append({'id': 'opt', 'kind': 'option', 'count': 2.0, 'strike': 10.0, 'source_id': 's'})
        r = per_share(cs, 300.0)          # v solves v = 400 / (10 + 2*(1-10/v)) -> 35
        self.assertAlmostEqual(r['value_per_share'], 35.0, places=5)

    def test_impaired_equity_flagged_not_negative(self):
        r = per_share(synth(), -50.0)
        self.assertEqual(r['status'], 'equity_impaired')
        self.assertEqual(r['value_per_share'], 0.0)

    def test_validation_rejects_unsourced_and_lookahead(self):
        cs = synth(); cs['cash'][0].pop('source_id')
        with self.assertRaises(ValueError): validate(cs)
        cs = synth(); cs['cash'][0]['as_of'] = '2027-01-01'
        with self.assertRaises(ValueError): validate(cs)


class NBISGolden(unittest.TestCase):
    """Real filing numbers (6-K Q2 2026, 20-F 2025, Aug 2026 6-Ks)."""
    def test_economic_shares_and_debt(self):
        s = summarize(NBIS['capital_structure'])
        self.assertAlmostEqual(s['basic_economic_shares'], 308_721_154)
        self.assertAlmostEqual(sum(d['principal'] for d in NBIS['capital_structure']['debt']), 14_222.5e6)
        self.assertAlmostEqual(s['restricted_cash'], 1056.0e6)          # reported, never netted as spendable

    def test_market_view_converts_in_money_at_173(self):
        m = market_view(NBIS['capital_structure'], 173.39)
        self.assertEqual(sorted(m['in_the_money_converts']), ['cv_2029_jun', 'cv_2030_sep', 'cv_2031_jun', 'cv_2032_sep'])
        self.assertAlmostEqual(m['potential_conversion_shares_all'] / 1e6, 68.6, delta=0.1)

    def test_audit_passes_with_correct_inputs(self):
        r = audit(dict(NBIS, model=model_ok()))
        self.assertTrue(r['valuation_usable'], r)
        self.assertEqual(r['critical_failures'], [])

    def test_audit_fails_on_the_18_10_share_count(self):
        bad = deepcopy(NBIS)
        bad['capital_structure']['shares'] = [{'id': 'wa', 'kind': 'common', 'count': 280.38e6, 'source_id': 'yahoo'}]
        r = audit(dict(bad, model=model_ok()))
        self.assertIn('shares_current', r['critical_failures'])
        self.assertFalse(r['valuation_usable'])

    def test_audit_fails_when_converts_missing(self):
        bad = deepcopy(NBIS)
        bad['capital_structure']['debt'] = [d for d in bad['capital_structure']['debt'] if d['kind'] != 'convertible']
        self.assertIn('debt_complete', audit(dict(bad, model=model_ok()))['critical_failures'])

    def test_audit_fails_on_cash_double_count(self):
        bad = deepcopy(NBIS)   # the ASTS error class: cash and investments added twice
        bad['capital_structure']['cash'].append({'id': 'dup', 'kind': 'short_term_investments', 'amount': 8042.1e6, 'source_id': 'yahoo_totalCash'})
        self.assertIn('cash_reconciles', audit(dict(bad, model=model_ok()))['critical_failures'])


class ModelFaults(unittest.TestCase):
    def fails(self, **change):
        m = model_ok(); m.update(change)
        return audit(dict(NBIS, model=m))['critical_failures']

    def test_arr_as_revenue(self):
        self.assertIn('revenue_basis', self.fails(revenue_basis='arr'))

    def test_arr_as_forward_indicator_is_only_limited(self):
        r = audit(dict(NBIS, model=dict(model_ok(), revenue_basis='arr', start_revenue_from='recognized_or_guidance')))
        self.assertEqual(r['critical_failures'], [])
        self.assertEqual(r['verdict'], LIMITED)

    def test_backlog_guaranteed(self):
        self.assertIn('backlog', self.fails(backlog_treatment='guaranteed'))

    def test_prepayments_as_profit_or_net_cash(self):
        self.assertIn('prepayments', self.fails(prepayments_treatment='profit'))
        self.assertIn('prepayments', self.fails(prepayments_treatment='net_cash'))

    def test_sbc_excluded(self):
        self.assertIn('sbc', self.fails(margin_basis='adjusted_ebitda', sbc_treatment='excluded'))

    def test_capital_heavy_without_reinvestment_or_ebitda_only(self):
        self.assertIn('reinvestment', self.fails(capex_linked_to_growth=False))
        self.assertIn('reinvestment', self.fails(valuation_basis='ev_ebitda_only'))

    def test_unfunded_plan(self):
        self.assertIn('funding', self.fails(financing_modeled=False))

    def test_scenarios_cross(self):
        self.assertIn('scenario_order', self.fails(scenario_values={'bear': 3, 'base': 2, 'bull': 1}))

    def test_missing_reports_are_unverifiable_not_passed(self):
        p = dict(NBIS, model=model_ok()); p = deepcopy(p); p['reported'] = {}
        r = audit(p)
        self.assertEqual(r['verdict'], UNVERIFIABLE)
        self.assertFalse(r['valuation_usable'])


if __name__ == '__main__':
    unittest.main()
