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
        self.assertAlmostEqual(today['base'], 95.05, delta=.05)
        self.assertTrue(today['bear'] < today['base'] < today['bull'] < s['quote']['price'])
        # the 2026-09-28 spec reinvests nothing in its last explicit year while growing 6%, then the terminal reinvests
        # g/ROIC = 12%: a cash-flow cliff the audit now flags until a reviewed transition exception is supplied
        tr = {t['scenario']: t for t in r['underwriting_audit']['terminal_transitions']}
        self.assertTrue(all(t['reinvestment_jump'] for t in tr.values()))
        self.assertLess(tr['base']['last_net_reinvestment_rate'], 0)
        self.assertAlmostEqual(tr['base']['terminal_reinvestment_rate'], .12)
        self.assertEqual(r['status'], 'underwriting_required')
        self.assertEqual({i['path'] for i in r['underwriting_audit']['issues']}, {f'{x}.transition_exception' for x in ('bear', 'base', 'bull')})
        self.assertEqual(r['underwriting_audit']['source_access']['status'], 'secondary_access')
        self.assertAlmostEqual(implied_cost_of_equity(build_case(s))['implied_cost_of_equity'], .0806, delta=.001)
        self.assertAlmostEqual(implied_growth_shift(s)['growth_shift'], .0455, delta=.001)


if __name__ == '__main__':
    unittest.main()


class EquityRaiseTests(unittest.TestCase):
    """Equity raises dilute existing holders at an explicit issue price instead of a debt stand-in."""

    def test_equity_proceeds_issue_shares_at_the_stated_price(self):
        import json
        from fundamental_engine.drivers import build_case
        from fundamental_engine.valuation import value_company
        spec = json.load(open('examples/valuation/software_drivers.json'))
        base = next(s for s in spec['scenarios'] if s['name'] == 'base')
        n = len(spec['boundaries'])
        base['equity_proceeds'] = [100e6] + [0] * (n - 1)
        base['equity_issue_price'] = 40.0
        base['financing_status'] = 'committed'
        case = build_case(spec)
        p0 = case['scenarios'][[s['name'] for s in case['scenarios']].index('base')]['periods'][0]
        self.assertEqual((p0['equity_proceeds'], p0['equity_issue_price'], p0['financing_status']), (100e6, 40.0, 'committed'))
        rows = next(r for r in value_company(case)['scenarios'] if r['name'] == 'base')['forecast']
        self.assertAlmostEqual(rows[0]['new_financing_shares'], 2.5e6)
        self.assertLess(rows[0]['original_ownership'], 1.0)


class InterimAndSettlementTests(unittest.TestCase):
    """A split fiscal year keeps one annual rate; SBC settled in shares is dilution, not cash, and never above the charge."""

    def test_interim_boundary_continues_the_fiscal_year(self):
        s = spec()
        b = s['boundaries']
        mid = '2028-06-30'
        self.assertTrue(b[1] < mid < b[2])
        s2 = deepcopy(s)
        s2['boundaries'] = sorted(b + [mid])
        s2['interim_boundaries'] = [mid]
        i = s2['boundaries'].index(mid)
        for sc in s2['scenarios']:
            for k, v in list(sc.items()):
                if isinstance(v, list) and k not in ('growth', 'assumption_ids', 'period_assumption_ids') and len(v) == len(b):
                    sc[k] = v[:i + 1] + v[i:]
            if isinstance(sc['growth'], list):
                sc['growth'] = sc['growth'][:i] + [.5] + sc['growth'][i:]        # ignored: a continuation has no growth step
        rows0, rows1 = (base_of(value_company(build_case(x)))['forecast'] for x in (s, s2))
        rate = lambda r: r['revenue'] / years(r['start'], r['end'])
        self.assertAlmostEqual(rate(rows1[i]), rate(rows1[i + 1]))
        self.assertAlmostEqual(rows1[i]['revenue'] + rows1[i + 1]['revenue'], rows0[i]['revenue'], delta=1)
        self.assertAlmostEqual(rows1[-1]['revenue'], rows0[-1]['revenue'], delta=1)
        s2['interim_boundaries'] = [b[-1]]
        with self.assertRaisesRegex(ValueError, 'interim_boundaries'):
            build_case(s2)

    def test_sbc_share_settlement(self):
        s = spec()
        n = len(s['boundaries'])
        r0 = base_of(value_company(build_case(s)))['forecast']
        base_of(s)['sbc_share_settlement'] = [0, 10e6] + [0] * (n - 2)
        c = build_case(s)
        rows = base_of(value_company(c))['forecast']
        price = base_of(c)['periods'][1]['equity_issue_price']
        self.assertAlmostEqual(rows[1]['cfo'] - r0[1]['cfo'], 10e6, delta=1)            # not paid in cash
        self.assertAlmostEqual(rows[1]['sbc_settlement_shares'], 10e6 / price)          # paid in new shares
        base_of(s)['sbc_share_settlement'] = [0, 1e12] + [0] * (n - 2)
        with self.assertRaisesRegex(ValueError, 'exceeds the SBC expense'):
            value_company(build_case(s))
