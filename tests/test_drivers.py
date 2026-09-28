import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.drivers import build_case, implied_growth_shift
from fundamental_engine.valuation import value_company, years
from fundamental_engine.valuation_audit import source_access
from fundamental_engine.valuation_tools import (implied_cost_of_equity, discount_rate_band, reverse_price, stress_test)
from fundamental_engine.plain import _confidence
from fundamental_engine.mcp import Server

ROOT = Path(__file__).resolve().parents[1]
def spec(): return json.loads((ROOT / 'examples/valuation/software_drivers.json').read_text())
def infra(): return json.loads((ROOT / 'examples/valuation/infrastructure.json').read_text())
def base_of(case): return next(s for s in case['scenarios'] if s['name'] == 'base')


class DriverSpecTests(unittest.TestCase):
    def test_expansion_matches_ratios(self):
        s = spec(); c = build_case(s); b = base_of(c)
        self.assertEqual(c['prepayment_price_linkage'], 'scaled')
        self.assertEqual(len(c['generated_from']['spec_sha256']), 64)
        stub, first = b['periods'][0], b['periods'][1]
        self.assertEqual(stub['segments'][0]['annual_revenue_per_unit'], 1e9)
        # First full year grows from the last full fiscal year, not from the stub run rate.
        self.assertAlmostEqual(first['segments'][0]['annual_revenue_per_unit'], 950e6 * 1.18)
        rev = first['segments'][0]['annual_revenue_per_unit'] * years(first['start'], first['end'])
        self.assertAlmostEqual(first['sbc_expense'], .11 * rev)
        self.assertAlmostEqual(first['maintenance_capex'], .04 * rev)
        self.assertAlmostEqual(first['change_working_capital_excluding_deferred'], .1 * (950e6 * 1.18 - 950e6))
        # Deferred ledger lands on ratio x annual rate at every boundary.
        r = value_company(c)
        rows = base_of(r)['forecast']
        for p, row in zip(b['periods'], rows):
            self.assertAlmostEqual(row['deferred_revenue'], .35 * p['segments'][0]['annual_revenue_per_unit'], delta=1)
        self.assertEqual(r['status'], 'conditional_valuation')

    def test_rejects_bad_specs(self):
        s = spec(); s['driver_version'] = 2
        with self.assertRaisesRegex(ValueError, 'driver_version'): build_case(s)
        s = spec(); base_of(s)['sbc_pct'] = [.1, .1]
        with self.assertRaisesRegex(ValueError, 'sbc_pct needs 8'): build_case(s)
        s = spec(); base_of(s)['growth'] = [.1] * 8
        with self.assertRaisesRegex(ValueError, 'growth needs 7'): build_case(s)
        s = spec(); del base_of(s)['cash_cost_ratio']
        with self.assertRaisesRegex(ValueError, 'cash_cost_ratio is required'): build_case(s)
        s = spec(); base_of(s)['sbc_policy'] = 'explicit_shares'
        with self.assertRaisesRegex(ValueError, 'cash-equivalent'): build_case(s)

    def test_shrinking_deferred_balance_draws_down_liability(self):
        s = spec(); base_of(s)['deferred_revenue_ratio'] = [.35, .2, .2, .2, .2, .2, .2, .2]
        base_of(s)['prepaid_share'] = .05
        c = build_case(s); b = base_of(c)
        # Billings cannot be negative: the balance runs down through recognition instead.
        self.assertEqual(b['periods'][1]['customer_prepayments'], 0)
        rate = b['periods'][1]['segments'][0]['annual_revenue_per_unit']
        stub_balance = .35 * 1e9
        self.assertAlmostEqual(b['periods'][1]['revenue_from_prepayments'], stub_balance - .2 * rate, delta=1)
        rows = base_of(value_company(c))['forecast']
        self.assertAlmostEqual(rows[1]['deferred_revenue'], .2 * rate, delta=1)

    def test_implied_growth_shift_reconstructs_quote(self):
        s = spec(); r = implied_growth_shift(s)
        self.assertEqual(r['status'], 'solved')
        self.assertAlmostEqual(r['reconstructed_value'], s['quote']['price'], places=4)
        self.assertGreater(r['growth_shift'], 0)
        s['quote']['price'] = value_company(build_case(s))['annual_values'][0]['base']
        self.assertAlmostEqual(implied_growth_shift(s)['growth_shift'], 0, places=5)
        s['quote']['price'] = 1e6
        self.assertEqual(implied_growth_shift(s)['status'], 'not_bracketed')

    def test_mcp_tools(self):
        srv = Server(':memory:'); srv.initialized = True
        out = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': 'build_valuation_from_drivers', 'arguments': {'spec': spec()}}})
        self.assertFalse(out['result']['isError'])
        body = json.loads(out['result']['content'][0]['text'])
        self.assertEqual(body['case']['prepayment_price_linkage'], 'scaled')
        out = srv.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                          'params': {'name': 'valuation_diagnostics', 'arguments': {'case': body['case']}}})
        diag = json.loads(out['result']['content'][0]['text'])
        self.assertEqual(diag['implied_cost_of_equity']['status'], 'solved')
        self.assertIn('quote_inside_band', diag['discount_rate_band'])


class DiagnosticTests(unittest.TestCase):
    def test_implied_cost_of_equity(self):
        c = build_case(spec())
        c['quote']['price'] = value_company(c)['annual_values'][0]['base']
        r = implied_cost_of_equity(c)
        self.assertEqual(r['status'], 'solved')
        self.assertAlmostEqual(r['implied_cost_of_equity'], base_of(c)['cost_of_equity'], places=5)
        c['quote']['price'] = 1e6
        self.assertEqual(implied_cost_of_equity(c)['status'], 'not_bracketed')

    def test_discount_band_flags_flip(self):
        c = build_case(spec())
        base = value_company(c)['annual_values'][0]['base']
        c['quote']['price'] = base * 1.02
        band = discount_rate_band(c)
        self.assertTrue(band['quote_inside_band'])
        self.assertGreater(band['value_lower_rate'], band['value'])
        self.assertGreater(band['value'], band['value_higher_rate'])
        c['quote']['price'] = base * 3
        self.assertFalse(discount_rate_band(c)['quote_inside_band'])

    def test_price_stress_and_reverse_work_for_subscription_ledger(self):
        c = build_case(spec())
        stressed = next(x for x in stress_test(c)['stresses'] if x['shock'] == 'prices_down_20pct')
        self.assertNotEqual(stressed['status'], 'invalid_stressed_input')
        self.assertLess(stressed['annual_values'][0]['base'], value_company(c)['annual_values'][0]['base'])
        r = reverse_price(c)
        self.assertEqual(r['status'], 'solved')
        self.assertAlmostEqual(r['reconstructed_price'], c['quote']['price'], places=4)

    def test_fixed_prepayments_are_not_scaled(self):
        c = infra()
        self.assertEqual(c.get('prepayment_price_linkage', 'fixed'), 'fixed')
        stressed = next(x for x in stress_test(c)['stresses'] if x['shock'] == 'prices_down_20pct')
        self.assertNotEqual(stressed['status'], 'invalid_stressed_input')
        c['prepayment_price_linkage'] = 'sometimes'
        with self.assertRaisesRegex(ValueError, 'prepayment_price_linkage'): value_company(c)


class SourceAccessTests(unittest.TestCase):
    def real_case(self, retrieval):
        c = build_case(spec()); c['is_demo'] = False
        c['sources'] = [{'id': 'tenq', 'title': 'Quarterly report', 'url': 'https://example.invalid/10q', 'kind': 'filing',
                         'published_at': '2026-08-01', 'available_at': '2026-08-01'}]
        if retrieval: c['sources'][0]['retrieval'] = retrieval
        c['assumptions'][0]['source_ids'] = ['tenq']
        c['quote']['source_ids'] = ['tenq']
        return c

    def test_status(self):
        self.assertEqual(source_access(build_case(spec()))['status'], 'demo')
        self.assertEqual(source_access(self.real_case('primary_document'))['status'], 'primary_read')
        self.assertEqual(source_access(self.real_case(None))['status'], 'secondary_access')
        self.assertEqual(source_access(self.real_case('provider_normalized'))['not_read_directly'][0]['retrieval'], 'provider_normalized')
        with self.assertRaisesRegex(ValueError, 'retrieval'): value_company(self.real_case('rumour'))
        self.assertIn('source_access', value_company(self.real_case('primary_document'))['underwriting_audit'])

    def test_confidence_reasons(self):
        items = [{}] * 5
        case = {'history': [{}] * 5, '_executed_research_status': 'ready_for_conditional_synthesis'}
        ok = {'is_demo': False, 'priced_conclusion_ready': True, 'status': 'conditional_valuation', 'base_issues': [], 'warnings': [],
              'source_access': {'status': 'primary_read'}, 'discount_rate_band': {'quote_inside_band': False, 'relative_width': .3}}
        self.assertEqual(_confidence(case, ok, items)['level'], 'גבוה')
        v = deepcopy(ok); v['source_access'] = {'status': 'secondary_access'}
        r = _confidence(case, v, items)
        self.assertEqual(r['level'], 'בינוני'); self.assertIn('לא נקראו ישירות', r['reasons'][0])
        v = deepcopy(ok); v['discount_rate_band'] = {'quote_inside_band': True, 'relative_width': .3}
        self.assertIn('הופך את המסקנה', _confidence(case, v, items)['reasons'][0])
        v = deepcopy(ok); v['discount_rate_band'] = {'quote_inside_band': False, 'relative_width': .6}
        self.assertIn('רגיש מאוד', _confidence(case, v, items)['reasons'][0])
        v['source_access'] = {'status': 'secondary_access'}
        self.assertEqual(_confidence(case, v, items)['level'], 'נמוך')


class NowRegressionTests(unittest.TestCase):
    """Real 2026-09-28 input: guards the conversion and the honest-confidence path, not the value's correctness."""
    def test_now_reproduces_and_reports_secondary_access(self):
        s = json.loads((ROOT / 'examples/real/now_drivers_2026q3.json').read_text())
        r = value_company(build_case(s))
        today = r['annual_values'][0]
        self.assertEqual(r['status'], 'conditional_valuation')
        self.assertAlmostEqual(today['base'], 95.05, delta=.05)
        self.assertTrue(today['bear'] < today['base'] < today['bull'] < s['quote']['price'])
        self.assertEqual(r['underwriting_audit']['status'], 'internally_reconciled')
        self.assertEqual(r['underwriting_audit']['source_access']['status'], 'secondary_access')
        self.assertAlmostEqual(implied_cost_of_equity(build_case(s))['implied_cost_of_equity'], .0806, delta=.001)
        self.assertAlmostEqual(implied_growth_shift(s)['growth_shift'], .0455, delta=.001)


if __name__ == '__main__':
    unittest.main()
