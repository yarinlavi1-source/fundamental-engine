"""forecast.build / what_must_be_true: ADBE fixture (real 10-K values) plus clearly labelled synthetic companies.

The analysis / model-card / dataqa inputs are hand-built from the contract (docs/MODULE_CONTRACTS.md) so these
tests do not depend on the parallel modules; ADBE numbers come from tests/fixtures/adbe_factbook_2026-10-19.json.
"""
import json
import unittest
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from fundamental_engine import facts, forecast
from fundamental_engine.drivers import build_case, RATIO_KEYS, ABSOLUTE_KEYS
from fundamental_engine.valuation import value_company

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
SCEN = ('bear', 'base', 'bull')
METRICS = ['revenue', 'operating_income', 'net_income', 'cfo', 'capex', 'sbc', 'd_and_a', 'buybacks', 'interest_expense',
           'pretax_income', 'income_tax', 'acquisitions']


def make_an(fb, **metrics):
    """Minimal analysis.analyze-shaped output built directly from FactBook facts."""
    t = facts.annual_table(fb, METRICS)
    ann = {e: dict(r, fcf=r.get('cfo', 0) - r.get('capex', 0), dividends=0.) for e, r in t.items()}
    last = max(ann)
    return {'years': sorted(ann), 'annual': ann, 'ttm': dict(ann[last], period_end=last, method='latest_fiscal_year'),
            'metrics': {k: {'value': v, 'formula': 'test fixture', 'status': 'reported'} for k, v in metrics.items()}}


def adbe():
    fb = deepcopy(ADBE)
    rev = [v for _, v, _ in facts.series(fb, 'revenue')]
    growth = [b / a - 1 for a, b in zip(rev, rev[1:])]
    mu = sum(growth) / len(growth)
    an = make_an(fb, revenue_growth_last=growth[-1], revenue_cagr_3y=(rev[-1] / rev[-4]) ** (1 / 3) - 1,
                 growth_volatility=(sum((g - mu) ** 2 for g in growth) / (len(growth) - 1)) ** .5,
                 operating_margin_best_3y=8706 / 23769, sales_to_capital_incremental_3y=None,  # invested capital fell FY22->FY25
                 roic=.62, net_debt_to_ebitda=-.04, acquisition_spend_pct_revenue_3y=.0003)
    # invested capital = equity + debt - cash - investments (analysis.py definition), from the fixture balance sheets
    for e, ic in (('2022-12-02', 14051e6 + 4129e6 - 4236e6 - 1860e6), ('2025-11-28', 11623e6 + 6210e6 - 5431e6 - 1164e6)):
        an['annual'][e]['invested_capital'] = ic
    card = {'business_model': {'primary': 'subscription_software', 'reasons': ['test']}, 'stage': {'value': 'mature'},
            'cyclical': {'value': False}, 'capital_intensity': {'value': 'low'}, 'funding': {'value': 'self_funded'},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'software'},
            'dimensions': {'moat': {'assessment': 'strong'}, 'technology_ai': {'assessment': 'weak'}}}
    qa = {'status': 'limited', 'limited': ['shares_outstanding'],
          'fields': {'shares_outstanding': {'status': 'single_source', 'sources': ['fmp:shares_float']},
                     'cash_and_liquid_investments': {'status': 'reported', 'sources': ['fmp:as_reported_balance_annual']},
                     'debt': {'status': 'reported', 'sources': ['fmp:as_reported_balance_annual']}},
          'resolved': {'shares_outstanding': {'value': 389.2e6, 'as_of': '2026-08-28', 'basis': 'vendor_float'},
                       'cash': {'value': 5431e6, 'as_of': '2025-11-28'}, 'short_term_investments': {'value': 1164e6, 'as_of': '2025-11-28'},
                       'long_term_investments': {'value': 0., 'as_of': '2025-11-28'},
                       'debt': {'value': 6210e6, 'as_of': '2025-11-28', 'method': 'tagged_total'},
                       'deferred_revenue': {'value': 7030e6, 'as_of': '2025-11-28'}}}
    return fb, an, card, qa


def synth(as_of, annual, inst, price, kind='synthetic', ticker='SYN'):
    """Synthetic FactBook (test data only). kind='filing_derived' labels it like filing data to exercise eligibility."""
    fb = facts.new({'ticker': ticker, 'name': 'Synthetic test company', 'currency': 'USD', 'fiscal_year_end': '12-31'}, as_of)
    facts.add_source(fb, 'syn:fs', 'synthetic', 'test-statements', kind, as_of + 'T00:00:00Z', note='TEST DATA ONLY')
    facts.add_source(fb, 'syn:px', 'synthetic', 'test-price', 'synthetic' if kind == 'synthetic' else 'market_feed', as_of + 'T00:00:00Z')
    for end, row in annual.items():
        for m, v in row.items():
            facts.add_fact(fb, m, 'FY' + end[:4], end, v, 'syn:fs', 'as_reported', (date.fromisoformat(end) + timedelta(days=60)).isoformat())
    for end, row in inst.items():
        for m, v in row.items():
            facts.add_fact(fb, m, 'I@' + end, end, v, 'syn:fs', 'as_reported', (date.fromisoformat(end) + timedelta(days=40)).isoformat())
    facts.set_price(fb, price, as_of, 'syn:px')
    return fb


def qa_for(shares, cash, debt, when, deferred=0.):
    return {'status': 'ok', 'resolved': {'shares_outstanding': {'value': shares, 'as_of': when, 'basis': 'balance_sheet'},
                                         'cash': {'value': cash, 'as_of': when}, 'debt': {'value': debt, 'as_of': when, 'method': 'noncurrent+current'},
                                         'deferred_revenue': {'value': deferred, 'as_of': when}}}


def industrial():
    rev = [400e6, 460e6, 520e6, 580e6]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .2, 'sbc': r * .05, 'd_and_a': r * .04, 'capex': r * .05,
                             'cfo': r * .27, 'pretax_income': r * .2, 'income_tax': r * .2 * .21, 'interest_expense': 4e6, 'buybacks': r * .1}
              for y, r in zip(range(2022, 2026), rev)}
    inst = {'2025-12-31': {'cash': 150e6, 'shares_outstanding': 50e6, 'receivables': 80e6, 'payables': 30e6, 'long_term_debt': 100e6},
            '2022-12-31': {'receivables': 56e6, 'payables': 21e6}}
    fb = synth('2026-03-20', annual, inst, 60.0, kind='filing_derived')
    an = make_an(fb, revenue_growth_last=580 / 520 - 1, revenue_cagr_3y=(580 / 400) ** (1 / 3) - 1, growth_volatility=.01, roic=.25,
                 sales_to_capital_incremental_3y=2.0)
    card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'maturing'}, 'funding': {'value': 'self_funded'},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(50e6, 150e6, 100e6, '2025-12-31')


def plateau_builder():
    """SYNTHETIC: six years of build-out, the last three with flat revenue while capital kept rising."""
    rev = [200e6, 300e6, 420e6, 430e6, 435e6, 440e6]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .1, 'sbc': r * .02, 'd_and_a': r * .05, 'capex': r * .09,
                             'cfo': r * .15, 'pretax_income': r * .1, 'income_tax': r * .1 * .21, 'interest_expense': 2e6}
              for y, r in zip(range(2020, 2026), rev)}
    inst = {'2025-12-31': {'cash': 120e6, 'shares_outstanding': 40e6, 'receivables': 50e6, 'payables': 30e6, 'long_term_debt': 50e6},
            '2022-12-31': {'receivables': 45e6, 'payables': 27e6}}
    fb = synth('2026-03-20', annual, inst, 30.0, kind='filing_derived')
    an = make_an(fb, revenue_growth_last=440 / 435 - 1, revenue_cagr_3y=(440 / 420) ** (1 / 3) - 1, growth_volatility=.15, roic=.08,
                 sales_to_capital_incremental_3y=20e6 / 90e6)
    for e, ic in zip(sorted(an['annual']), (100e6, 200e6, 290e6, 330e6, 360e6, 380e6)):
        an['annual'][e]['invested_capital'] = ic
    card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'maturing'}, 'funding': {'value': 'self_funded'},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(40e6, 120e6, 50e6, '2025-12-31')


def grower(margin_scale=1., cash=30e6):
    """Loss-making subscription grower; margin_scale > 1 deepens the losses."""
    rows = ((2023, 150e6, -45e6, 20e6), (2024, 200e6, -40e6, 26e6), (2025, 260e6, -36e6, 33e6))
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': oi * margin_scale, 'sbc': sbc, 'd_and_a': r * .035, 'capex': r * .04,
                             'cfo': oi * margin_scale + sbc, 'pretax_income': oi * margin_scale, 'income_tax': 0.} for y, r, oi, sbc in rows}
    inst = {'2025-12-31': {'cash': cash, 'shares_outstanding': 80e6, 'receivables': 40e6, 'payables': 15e6},
            '2022-12-31': {'receivables': 22e6, 'payables': 9e6}}
    fb = synth('2026-03-15', annual, inst, 12.0)
    an = make_an(fb, revenue_growth_last=.30, growth_volatility=.03, sales_to_capital_incremental_3y=2.5)
    card = {'business_model': {'primary': 'subscription_software'}, 'stage': {'value': 'growth'}, 'funding': {'value': 'needs_external'},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'software'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(80e6, cash, 0., '2025-12-31')


def scen(spec, name):
    return next(s for s in spec['scenarios'] if s['name'] == name)


class AdbeForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fb, cls.an, cls.card, cls.qa = adbe()
        cls.fc = forecast.build(cls.fb, cls.an, cls.card, cls.qa)
        cls.case = build_case(cls.fc['spec'])
        cls.val = value_company(cls.case)

    def test_builds_and_values_three_ordered_scenarios(self):
        fc, val = self.fc, self.val
        self.assertEqual(fc['status'], 'built')
        today = val['annual_values'][0]
        self.assertTrue(0 < today['bear'] <= today['base'] <= today['bull'], today)
        self.assertEqual({s['status'] for s in val['scenarios']}, {'conditional'})
        # generated assumptions use kind 'model'; valuation.validate accepts it (no 'Unknown assumption kind')
        kinds = {a['kind'] for a in fc['spec']['assumptions']}
        self.assertIn('model', kinds)
        self.assertLessEqual(kinds, {'reported', 'guidance', 'analyst', 'model'})
        # unreviewed until an independent reviewer marks them: arithmetic runs, no synthesis eligibility
        self.assertEqual(val['status'], 'unreviewed')
        self.assertFalse(val['underwriting_audit']['eligible_for_research_synthesis'])
        self.assertEqual(fc['preview']['today'], {k: today[k] for k in SCEN})

    def test_every_driver_key_is_explicit(self):
        spec = self.fc['spec']
        self.assertTrue(spec['strict_drivers'])
        self.assertEqual(self.case['generated_from']['defaulted_keys'], [])
        n = len(spec['boundaries'])
        for s in spec['scenarios']:
            for k in list(RATIO_KEYS) + list(ABSOLUTE_KEYS):
                self.assertIsInstance(s[k], list, k)
                self.assertEqual(len(s[k]), n, k)
            self.assertEqual(len(s['growth']), n - 1)
            self.assertEqual(s['sbc_policy'], 'cash_equivalent')
            self.assertTrue(s['terminal']['sbc_in_margin'])
            self.assertLessEqual(s['terminal']['growth'], min(forecast.RF_DEFAULT, .03))

    def test_calendar_stub_and_report_dates(self):
        spec, fc = self.fc['spec'], self.fc
        self.assertEqual(spec['boundaries'][0], '2026-11-30')          # nominal fiscal year end 11-30 after as_of
        self.assertGreaterEqual(len(spec['boundaries']), 11)            # stub + 10 fiscal years for a ~10% grower
        rd = spec['report_dates']
        self.assertEqual(rd[0], spec['as_of'])
        future = [d for d in rd if d > spec['as_of']]
        self.assertGreaterEqual(len(future), 5)
        self.assertTrue(set(future) <= set(spec['boundaries']))
        self.assertGreaterEqual(rd[-1], '2031-10-19')                   # covers the fifth anniversary of as_of
        self.assertIn('2031-10-19', rd)                                  # ... as its own valuation date
        self.assertEqual(spec['interim_boundaries'], ['2031-10-19'])
        self.assertIn('2031-10-19', [r['date'] for r in self.val['annual_values']])
        # current fiscal year revenue = FY2025 grown at the trailing anchor; stub runs at the late-year rate
        cur = fc['anchors']['current_fiscal_year']
        self.assertAlmostEqual(spec['last_full_year_revenue'], cur['revenue'], places=0)
        self.assertGreater(cur['revenue'], 23769e6)
        self.assertGreater(spec['stub_annual_revenue_rate'], spec['last_full_year_revenue'])
        self.assertEqual(spec['opening']['as_of'], spec['as_of'])

    def test_margin_identity_matches_driver_arithmetic(self):
        # cash_cost_ratio = 1 - (operating margin + sbc_pct + depreciation_pct): executed EBIT margin equals the path
        for res in self.val['scenarios']:
            path = {p['end']: p for p in self.fc['paths'][res['name']]}
            for row in res['forecast']:
                fy = min(e for e in path if e >= row['end'])            # a split fiscal year keeps its fiscal-year margin
                self.assertAlmostEqual(row['ebit'] / row['revenue'], path[fy]['operating_margin'], places=6)
            t = scen(self.fc['spec'], res['name'])['terminal']
            self.assertAlmostEqual(t['operating_margin'], res['forecast'][-1]['ebit'] / res['forecast'][-1]['revenue'], places=6)
        for tr in self.val['underwriting_audit']['terminal_transitions']:
            self.assertFalse(tr['growth_cliff'] or tr['margin_jump'], tr)

    def test_scenarios_differ_by_business_outcomes(self):
        spec, fc = self.fc['spec'], self.fc
        bear, base, bull = (scen(spec, s) for s in SCEN)
        steps = [i for i, e in enumerate(spec['boundaries'][:-1]) if e not in spec['interim_boundaries']]   # growth index i = period i+1
        self.assertTrue(all(bear['growth'][i] < base['growth'][i] < bull['growth'][i] for i in steps))
        self.assertLessEqual(bear['growth'][0], base['growth'][0] / 2 + 1e-9)      # realistic disappointment
        self.assertLess(bear['terminal']['operating_margin'], base['terminal']['operating_margin'])
        self.assertGreaterEqual(bull['terminal']['operating_margin'], base['terminal']['operating_margin'])
        self.assertEqual(len({s['cost_of_equity'] for s in spec['scenarios']}), 1)  # same Ke: scenarios differ by business only
        # growth costs capital: positive growth -> positive growth capex; bear needs more capital per unit of growth
        self.assertTrue(all(c > 0 for c in base['growth_capex_pct']))
        s2c = {s: next(a for a in fc['assumptions'] if a['id'] == 'reinvestment')['values'][s]['sales_to_capital'] for s in SCEN}
        self.assertLess(s2c['bear'], s2c['base'])
        self.assertLess(s2c['base'], s2c['bull'])
        self.assertEqual(fc['anchors']['sales_to_capital']['used'], 5.0)           # capital-light history -> type ceiling, not free growth

    def test_opening_balance_and_reconciliation_from_dataqa(self):
        spec = self.fc['spec']
        op, rec = spec['opening'], spec['underwriting']['opening_reconciliation']
        self.assertEqual(op['shares'], 389.2e6)
        self.assertEqual(op['unrestricted_cash'], 5431e6)
        self.assertEqual(op['marketable_investments'], 1164e6)
        self.assertEqual(op['debt'], 6210e6)
        self.assertEqual(rec['share_basis'], 'point_in_time_common_outstanding')
        self.assertEqual(rec['statement_as_of'], '2025-11-28')
        self.assertEqual(rec['common_shares_outstanding'] + rec['incremental_dilutive_shares'], op['shares'])
        issues = {i['code'] for i in self.val['underwriting_audit']['issues']}
        self.assertNotIn('opening_mismatch', issues)
        self.assertIn('stale_statement', issues)                       # Nov-2025 balance sheet is honestly reported as stale
        self.assertEqual(self.val['underwriting_audit']['source_access']['status'], 'secondary_access')

    def test_ledger_sources_and_review_blocks(self):
        spec = self.fc['spec']
        ids = {a['id'] for a in spec['assumptions']}
        src = {s['id']: s for s in spec['sources']}
        for a in spec['assumptions']:
            self.assertEqual(a['review_status'], 'unreviewed')
            self.assertTrue(a['rationale'].strip() and a['falsifier'].strip())
            self.assertTrue(a['source_ids'] and all(s in src for s in a['source_ids']))
        for s in spec['sources']:
            self.assertLessEqual(s['published_at'], s['available_at'])
            self.assertLessEqual(s['available_at'], spec['as_of'])
        self.assertEqual(src['fmp:as_reported_balance_annual']['kind'], 'filing')
        self.assertEqual(src['fmp:as_reported_balance_annual']['retrieval'], 'provider_normalized')
        self.assertEqual(src['ib:snapshot']['kind'], 'market_data')
        refs = set(spec['opening']['assumption_ids'])
        for s in spec['scenarios']:
            refs |= set(s['assumption_ids']) | set(s['period_assumption_ids']) | set(s['terminal']['assumption_ids'])
        for blocks in spec['underwriting']['scenario_reviews'].values():
            self.assertEqual(set(blocks), {'demand_and_capacity', 'funding_and_claims', 'competitive_response', 'maturity'})
            for b in blocks.values():
                refs |= set(b['assumption_ids'])
        self.assertLessEqual(refs, ids)
        rows = {r['id']: r for r in self.fc['assumptions']}
        self.assertEqual(rows['open_balance']['kind'], 'reported')
        self.assertEqual(rows['growth_base']['provenance'], 'model_assumption')
        self.assertEqual(spec['underwriting']['consensus_review']['status'], 'unavailable')

    def test_cost_of_equity_is_documented_table_not_beta(self):
        ke = self.fc['cost_of_equity']
        comp = ke['components']
        self.assertEqual(comp['risk_free'], forecast.RF_DEFAULT)
        self.assertEqual(comp['equity_risk_premium'], forecast.ERP_DEFAULT)
        self.assertAlmostEqual(ke['base'], comp['risk_free'] + comp['equity_risk_premium'] + sum(a['value'] for a in comp['adjustments']))
        self.assertIn('technology_ai', json.dumps(comp['adjustments']))
        self.assertTrue(ke['bear'] == ke['base'] == ke['bull'])
        for s in SCEN:
            self.assertLess(ke['terminal_wacc'][s], ke[s] + 1e-12)

    def test_warnings_name_the_weak_inputs(self):
        codes = {w['code'] for w in self.fc['warnings']}
        self.assertTrue({'no_consensus', 'stale_ttm', 'opening_dates_differ', 'stale_balance_sheet', 'sales_to_capital_capped'} <= codes, codes)
        an = deepcopy(self.an)
        for r in an['annual'].values():
            r.pop('invested_capital', None)
        self.assertIn('sales_to_capital_default', {w['code'] for w in forecast.build(self.fb, an, self.card, self.qa)['warnings']})

    def test_deterministic_spec_hash(self):
        again = forecast.build(*adbe())
        self.assertEqual(again['spec_sha256'], self.fc['spec_sha256'])
        self.assertEqual(json.dumps(again['spec'], sort_keys=True), json.dumps(self.fc['spec'], sort_keys=True))
        json.dumps(self.fc, allow_nan=False)                                       # whole output is JSON-safe (no NaN/inf)

    def test_what_must_be_true(self):
        price = self.fc['spec']['quote']['price']
        rows = {r['lever']: r for r in forecast.what_must_be_true(self.fc['spec'], price)}
        self.assertEqual(set(rows), {'growth', 'mature_margin', 'capital_efficiency', 'cost_of_equity'})
        for k in ('growth', 'mature_margin', 'cost_of_equity'):
            self.assertEqual(rows[k]['status'], 'solved', rows[k])
        # base value is above the quote, so the price needs LESS than the base on every operating lever
        self.assertLess(rows['growth']['required'], rows['growth']['anchor'])
        self.assertLess(rows['mature_margin']['required'], rows['mature_margin']['anchor'])
        self.assertGreater(rows['cost_of_equity']['required'], rows['cost_of_equity']['anchor'])
        cap = rows['capital_efficiency']
        if cap['status'] == 'solved':
            self.assertGreater(cap['required'], 1)
        else:   # even the most capital-hungry feasible case stays above the price: say so, do not invent a number
            self.assertTrue(all(v > price for v in cap['detail']['range_values']))
            self.assertIn('stays above the price', cap['meaning'])
        # the solved shift of the first five fiscal-year growth rates (what the text says) reproduces the quote
        spec = deepcopy(self.fc['spec'])
        shift = rows['growth']['required'] - rows['growth']['anchor']
        self.assertEqual(rows['growth']['detail']['years_shifted'], 5)
        self.assertIn('next five fiscal years', rows['growth']['meaning'])
        steps = [i for i, e in enumerate(spec['boundaries'][:-1]) if e not in spec['interim_boundaries']][:5]
        b = scen(spec, 'base')
        b['growth'] = [g + (shift if i in steps else 0) for i, g in enumerate(b['growth'])]
        # growth costs capital: capex follows the engine's own capital per unit of new revenue on the shifted path
        b['growth_capex_pct'], b['maintenance_capex_pct'] = forecast._capex_for_path(spec, b, b['reinvestment_rule'])
        self.assertAlmostEqual(value_company(build_case(spec))['annual_values'][0]['base'], price, delta=.01)
        self.assertIn('growth is not free', rows['growth']['meaning'])
        far = {r['lever']: r for r in forecast.what_must_be_true(self.fc['spec'], 1e6)}
        self.assertTrue(all(r['status'] == 'not_reachable' and r['required'] is None for r in far.values()))
        self.assertEqual(forecast.what_must_be_true(None, 10), [])


class ConsensusAndInputsTests(unittest.TestCase):
    def consensus_fb(self):
        fb, an, card, qa = adbe()
        rows = [{'date': '2026-11-27', 'revenueAvg': 25.9e9, 'revenueLow': 25.6e9, 'revenueHigh': 26.2e9, 'numAnalystsRevenue': 20},
                {'date': '2027-11-26', 'revenueAvg': 28.1e9, 'revenueLow': 27.0e9, 'revenueHigh': 29.0e9, 'numAnalystsRevenue': 18},
                {'date': '2028-11-24', 'revenueAvg': 30.3e9, 'revenueLow': 28.5e9, 'revenueHigh': 32.0e9, 'numAnalystsRevenue': 12}]
        facts.from_fmp_estimates(fb, rows, '2026-10-19T22:20:00Z', as_of='2026-10-15', sid='test:consensus')   # TEST VALUES
        fb['estimates'].append({'metric': 'revenue', 'period_end': '2027-11-26', 'fiscal_year': 2027, 'value': 99e9,
                                'source_id': 'test:consensus', 'as_of': '2026-12-01'})                          # look-ahead row
        return fb, an, card, qa

    def test_consensus_anchors_first_years_and_is_compared(self):
        fb, an, card, qa = self.consensus_fb()
        fc = forecast.build(fb, an, card, qa)
        spec = fc['spec']
        base = scen(spec, 'base')
        self.assertAlmostEqual(spec['last_full_year_revenue'], 25.9e9, places=0)
        self.assertAlmostEqual(base['growth'][0], 28.1 / 25.9 - 1, places=6)
        self.assertAlmostEqual(base['growth'][1], 30.3 / 28.1 - 1, places=6)
        self.assertLessEqual(scen(spec, 'bear')['growth'][0], 27.0 / 25.9 - 1 + 1e-9)       # bear at or below consensus low
        rows = {r['id']: r for r in fc['assumptions']}
        self.assertEqual(rows['growth_base']['kind'], 'analyst')
        self.assertEqual(rows['growth_base']['provenance'], 'vendor_estimate')
        self.assertEqual(rows['revenue_current_fy']['kind'], 'analyst')
        self.assertIn('test:consensus', rows['growth_base']['source_ids'])
        self.assertEqual({s['id']: s['kind'] for s in spec['sources']}['test:consensus'], 'analyst_estimates')
        cr = spec['underwriting']['consensus_review']
        self.assertEqual(cr['status'], 'compared')
        val = value_company(build_case(spec))
        comp = val['underwriting_audit']['comparisons']
        self.assertEqual(len(comp), 1)
        self.assertLess(abs(comp[0]['relative_difference']), .01)
        codes = {w['code'] for w in fc['warnings']}
        self.assertIn('lookahead_estimates_ignored', codes)
        self.assertNotIn('no_consensus', codes)

    def test_risk_free_input_cited_and_lookahead_rejected(self):
        fb, an, card, qa = adbe()
        rf = {'value': .045, 'as_of': '2026-10-16', 'title': 'US 10y Treasury constant maturity (test value)', 'url': 'https://example.org/dgs10'}
        fc = forecast.build(fb, an, card, qa, {'risk_free': rf})
        self.assertEqual(fc['cost_of_equity']['components']['risk_free'], .045)
        cited = next(a for a in fc['spec']['assumptions'] if a['id'] == 'cost_of_equity')['source_ids']
        self.assertIn('input:risk_free', cited)
        with self.assertRaisesRegex(ValueError, 'look-ahead'):
            forecast.build(fb, an, card, qa, {'risk_free': dict(rf, as_of='2026-10-20')})

    def test_overrides_need_reason_and_evidence_and_are_recorded(self):
        fb, an, card, qa = adbe()
        ok = {'path': 'mature_margin', 'scenario': 'base', 'value': .30, 'reason': 'AI pricing pressure (test)',
              'evidence_ids': ['fmp:as_reported_income_annual']}
        for bad, msg in ((dict(ok, reason=''), 'reason'), (dict(ok, evidence_ids=[]), 'evidence'),
                         (dict(ok, evidence_ids=['nope']), 'unknown evidence'), (dict(ok, path='magic'), 'unknown path'),
                         (dict(ok, value=float('nan')), 'finite')):
            with self.assertRaisesRegex(ValueError, msg):
                forecast.build(fb, an, card, qa, {'overrides': [bad]})
        raw = {'path': 'growth', 'scenario': 'bull', 'value': .2, 'reason': 'test: new product line', 'evidence_ids': ['fmp:as_reported_income_annual']}
        fc = forecast.build(fb, an, card, qa, {'overrides': [ok, raw]})
        spec = fc['spec']
        self.assertAlmostEqual(scen(spec, 'base')['terminal']['operating_margin'], .30, places=5)   # ratios rounded to 1e-6
        self.assertEqual(scen(spec, 'bull')['growth'], [.2] * len(scen(spec, 'bull')['growth']))
        rec = {(o['path'], o['scenario']): o for o in fc['overrides']}
        self.assertAlmostEqual(rec[('mature_margin', 'base')]['original'], 8706 / 23769, places=6)
        self.assertEqual(rec[('mature_margin', 'base')]['value'], .30)
        rows = {r['id']: r for r in fc['assumptions']}
        self.assertEqual(rows['override_1']['kind'], 'analyst')
        self.assertIn('override_1', scen(spec, 'base')['assumption_ids'])
        self.assertIn('override_2', scen(spec, 'bull')['period_assumption_ids'])
        value_company(build_case(spec))

    def test_reviews_require_a_named_reviewer(self):
        with self.assertRaisesRegex(ValueError, 'named reviewer'):
            forecast.build(*adbe(), {'reviews': {'tax': {'status': 'reviewed'}}})

    def test_unsupported_methods_and_missing_opening(self):
        fb, an, card, qa = adbe()
        for method in ('residual_income', 'nav', 'rnpv', 'sotp', 'not_reliably_valuable'):
            c = deepcopy(card)
            c['valuation']['method'] = method
            fc = forecast.build(fb, an, c, qa)
            self.assertEqual(fc['status'], 'method_not_supported_by_forecast')
            self.assertIsNone(fc['spec'])
            self.assertTrue(fc['reason'])
        c = deepcopy(card)
        c['business_model']['primary'] = 'financials'
        self.assertEqual(forecast.build(fb, an, c, qa)['status'], 'method_not_supported_by_forecast')
        q = deepcopy(qa)
        del q['resolved']['shares_outstanding']
        fc = forecast.build(fb, an, card, q)
        self.assertEqual(fc['status'], 'insufficient_data')
        self.assertIn('shares_outstanding', fc['reason'])


class SyntheticCompanyTests(unittest.TestCase):
    def test_reviewed_forecast_becomes_eligible(self):
        fb, an, card, qa = industrial()
        fc = forecast.build(fb, an, card, qa)
        first = value_company(build_case(fc['spec']))
        self.assertEqual(first['status'], 'unreviewed')
        self.assertFalse(first['underwriting_audit']['eligible_for_research_synthesis'])
        reviews = {a['id']: {'status': 'reviewed', 'reviewer': 'independent test reviewer', 'note': 'checked'} for a in fc['assumptions']}
        fc2 = forecast.build(fb, an, card, qa, {'reviews': reviews})
        self.assertTrue(all(a['review_status'] == 'reviewed' and a['reviewer'] for a in fc2['spec']['assumptions']))
        val = value_company(build_case(fc2['spec']))
        self.assertEqual(val['underwriting_audit']['issues'], [])
        self.assertTrue(val['underwriting_audit']['eligible_for_research_synthesis'])
        self.assertEqual(val['status'], 'conditional_valuation')
        today = val['annual_values'][0]
        self.assertTrue(today['bear'] <= today['base'] <= today['bull'])

    def test_loss_making_grower_gets_flagged_assumed_financing(self):
        fb, an, card, qa = grower()
        fc = forecast.build(fb, an, card, qa)
        spec = fc['spec']
        self.assertTrue(spec['is_demo'])                                        # synthetic sources are never a real valuation
        funded = [s for s in SCEN if fc['funding'][s]['status'] == 'assumed_external_financing']
        self.assertTrue(funded)
        val = value_company(build_case(spec))
        for s in funded:
            self.assertEqual(scen(spec, s)['financing_status'], 'assumed')
            self.assertGreater(sum(scen(spec, s)['debt_draw']) + sum(scen(spec, s)['equity_proceeds']), 0)
            self.assertIn(f'financing_{s}', {a['id'] for a in spec['assumptions']})
            self.assertEqual(next(r for r in val['scenarios'] if r['name'] == s)['status'], 'conditional')
            self.assertTrue(any('financing is assumed' in i for i in next(r for r in val['scenarios'] if r['name'] == s)['issues']))
        issues = {m['id']: m for m in spec['underwriting']['material_issues']}
        self.assertEqual(issues[f'assumed_financing_{funded[0]}']['status'], 'unresolved')
        self.assertIn('financing_assumed', {w['code'] for w in fc['warnings']})
        # payout only after free cash flow turns positive: never distribute while borrowing
        for s in funded:
            sc = scen(spec, s)
            last_draw = max(i for i, (x, y) in enumerate(zip(sc['debt_draw'], sc['equity_proceeds'])) if x or y)
            self.assertEqual(sum(sc['payout_fraction'][:last_draw + 1]), 0)
        # disabling assumed financing leaves the plan unfunded and value_company withholds values
        fc2 = forecast.build(fb, an, card, qa, {'financing': 'none'})
        self.assertEqual(value_company(build_case(fc2['spec']))['status'], 'funding_blocked')
        self.assertIn('funding_blocked', {w['code'] for w in fc2['warnings']})

    def test_deep_burner_is_funded_by_priced_dilution_not_free_debt(self):
        fb, an, card, qa = grower(margin_scale=6., cash=40e6)
        card['stage']['value'] = 'early'
        card['valuation']['method'] = 'scenario_survival_fcff'
        fc = forecast.build(fb, an, card, qa)
        val = value_company(build_case(fc['spec']))
        for s in SCEN:
            f, sc = fc['funding'][s], scen(fc['spec'], s)
            self.assertEqual(f['status'], 'assumed_external_financing')
            self.assertGreater(f['equity_raised'], 0)                    # beyond debt capacity: equity at the quote, with fees
            self.assertGreater(f['new_financing_shares'], 0)
            rows = next(r for r in val['scenarios'] if r['name'] == s)['forecast']
            for p, row in zip(build_case(fc['spec'])['scenarios'][SCEN.index(s)]['periods'], rows):
                ebitda = row['ebitda_before_sbc'] / ((date.fromisoformat(row['end']) - date.fromisoformat(row['start'])).days / 365.25)
                if p['debt_draw']:                                       # assumed debt only within 3x EBITDA before SBC
                    self.assertLessEqual(row['debt'], forecast.DEBT_CAPACITY * ebitda + 2)
            self.assertLess(rows[-1]['original_ownership'], .6)          # dilution priced into per-share values
        self.assertNotEqual(val['status'], 'funding_blocked')
        today = val['annual_values'][0]
        self.assertTrue(0 <= today['bear'] <= today['base'] <= today['bull'] < today['current_quote'])
        self.assertTrue({'financing_assumed', 'survival_model'} <= {w['code'] for w in fc['warnings']})
        self.assertIn('needs_external_funding', json.dumps(fc['cost_of_equity']['components']['adjustments']))
        # without external financing the gap stays open and values are withheld, never free capital
        fc2 = forecast.build(fb, an, card, qa, {'financing': 'none'})
        self.assertEqual(value_company(build_case(fc2['spec']))['status'], 'funding_blocked')

    def test_normalized_cycle_uses_mid_cycle_margin(self):
        margins = (.05, .15, .25, .10, .20)
        annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * m, 'sbc': r * .01, 'd_and_a': r * .06, 'capex': r * .08,
                                 'cfo': r * (m + .07), 'pretax_income': r * m, 'income_tax': r * m * .22, 'interest_expense': 10e6}
                  for y, r, m in zip(range(2021, 2026), (900e6, 1100e6, 1300e6, 1000e6, 1200e6), margins)}
        inst = {'2025-12-31': {'cash': 200e6, 'shares_outstanding': 100e6, 'receivables': 150e6, 'inventory': 200e6, 'payables': 100e6},
                '2022-12-31': {'receivables': 140e6, 'inventory': 180e6, 'payables': 95e6}}
        fb = synth('2026-03-01', annual, inst, 25.0)
        an = make_an(fb, revenue_growth_last=.2, revenue_cagr_3y=(1200 / 1100) ** (1 / 3) - 1, revenue_cagr_5y=None,
                     growth_volatility=.17, sales_to_capital_incremental_3y=1.0)
        card = {'business_model': {'primary': 'commodity'}, 'stage': {'value': 'mature'}, 'cyclical': {'value': True},
                'valuation': {'method': 'normalized_cycle_fcff', 'archetype': 'commodity'}, 'dimensions': {}}
        fc = forecast.build(fb, an, card, qa_for(100e6, 200e6, 150e6, '2025-12-31'))
        self.assertEqual(fc['status'], 'built')
        base = scen(fc['spec'], 'base')
        revs = (900e6, 1100e6, 1300e6, 1000e6, 1200e6)          # through the cycle: sum(EBIT) / sum(revenue), not the mean margin
        self.assertAlmostEqual(base['terminal']['operating_margin'], sum(r * m for r, m in zip(revs, margins)) / sum(revs), places=6)
        self.assertLess(scen(fc['spec'], 'bear')['terminal']['operating_margin'], base['terminal']['operating_margin'])
        self.assertIn('cyclical', json.dumps(fc['cost_of_equity']['components']['adjustments']))
        val = value_company(build_case(fc['spec']))
        self.assertIsNotNone(val['annual_values'][0]['base'])

    def test_quarterly_ttm_sets_current_margin_and_recent_growth(self):
        fb, an, card, qa = industrial()
        fb['as_of'] = '2026-08-20'
        fb['market']['price']['as_of'] = '2026-08-20'
        an['ttm'] = {'period_end': '2026-06-30', 'method': 'quarters', 'revenue': 610e6, 'operating_income': 125e6, 'sbc': 30e6}
        fc = forecast.build(fb, an, card, qa)
        g = fc['anchors']['growth']
        self.assertAlmostEqual(g['recent_annualized'], (610 / 580) ** (1 / ((date(2026, 6, 30) - date(2025, 12, 31)).days / 365.25)) - 1, places=9)
        self.assertAlmostEqual(fc['anchors']['operating_margin']['current'], 125 / 610)
        self.assertIn('TTM', fc['anchors']['operating_margin']['basis'])
        self.assertIn('TTM revenue', fc['anchors']['current_fiscal_year']['basis'])
        self.assertAlmostEqual(fc['paths']['base'][0]['operating_margin'], 125 / 610, places=5)
        self.assertNotIn('stale_ttm', {w['code'] for w in fc['warnings']})

    def test_high_grower_gets_longer_horizon(self):
        fb, an, card, qa = grower()
        an['metrics']['revenue_growth_last']['value'] = .45
        an['metrics']['revenue_cagr_3y'] = {'value': .40}
        fc = forecast.build(fb, an, card, qa)
        self.assertGreaterEqual(len(fc['spec']['boundaries']) - 1, 15)
        for tr in value_company(build_case(fc['spec']))['underwriting_audit']['terminal_transitions']:
            self.assertFalse(tr['growth_cliff'] or tr['margin_jump'], tr)


def levered(price):
    """SYNTHETIC indebted industrial (test data only): debt 800M at 6%, EBIT about 116M."""
    rev = [400e6, 460e6, 520e6, 580e6]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .2, 'sbc': r * .02, 'd_and_a': r * .04, 'capex': r * .05,
                             'cfo': r * .2, 'pretax_income': r * .2 - 48e6, 'income_tax': (r * .2 - 48e6) * .21,
                             'interest_expense': 48e6, 'buybacks': 0.} for y, r in zip(range(2022, 2026), rev)}
    inst = {'2025-12-31': {'cash': 150e6, 'shares_outstanding': 50e6, 'receivables': 80e6, 'payables': 30e6, 'long_term_debt': 800e6},
            '2022-12-31': {'receivables': 56e6, 'payables': 21e6}}
    fb = synth('2026-03-20', annual, inst, price, kind='filing_derived')
    an = make_an(fb, revenue_growth_last=580 / 520 - 1, revenue_cagr_3y=(580 / 400) ** (1 / 3) - 1, growth_volatility=.01, roic=.12,
                 sales_to_capital_incremental_3y=2.0, net_debt_to_ebitda=650 / 139.2)
    card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'maturing'}, 'funding': {'value': 'self_funded'},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(50e6, 150e6, 800e6, '2025-12-31')


def peak_cyclical(stage='mature'):
    """SYNTHETIC cyclical whose latest fiscal year is a revenue and margin peak (test data only)."""
    hist = [(2020, 100e6, .05), (2021, 140e6, .15), (2022, 90e6, -.02), (2023, 150e6, .18), (2024, 120e6, .10), (2025, 220e6, .25)]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * m, 'sbc': r * .01, 'd_and_a': r * .06, 'capex': r * .07,
                             'cfo': r * (m + .07), 'pretax_income': r * m, 'income_tax': max(0, r * m * .21), 'interest_expense': 2e6}
              for y, r, m in hist}
    inst = {'2025-12-31': {'cash': 40e6, 'shares_outstanding': 10e6, 'receivables': 30e6, 'payables': 15e6, 'long_term_debt': 40e6},
            '2022-12-31': {'receivables': 14e6, 'payables': 8e6}}
    fb = synth('2026-03-20', annual, inst, 50.0, kind='filing_derived')
    an = make_an(fb, revenue_growth_last=220 / 120 - 1, revenue_cagr_3y=(220 / 140) ** (1 / 3) - 1, revenue_cagr_5y=(220 / 100) ** (1 / 5) - 1,
                 growth_volatility=.4, roic=.2, sales_to_capital_incremental_3y=1.0)
    card = {'business_model': {'primary': 'commodity'}, 'stage': {'value': stage}, 'cyclical': {'value': True, 'normalize': True},
            'capital_intensity': {'value': 'high'}, 'funding': {'value': 'self_funded'},
            'valuation': {'method': 'normalized_cycle_fcff', 'archetype': 'commodity'}, 'dimensions': {'moat': {'assessment': 'weak'}}}
    return fb, an, card, qa_for(10e6, 40e6, 40e6, '2025-12-31'), hist


def last_reinvestment(case, res, name):
    """Net reinvestment / NOPAT in the last explicit period, from the executed case (capex - depreciation + working capital
    - deferred revenue change)."""
    p = next(s for s in case['scenarios'] if s['name'] == name)['periods'][-1]
    row = next(r for r in res['scenarios'] if r['name'] == name)['forecast'][-1]
    net = (p['maintenance_capex'] + p['growth_capex'] - p['depreciation'] + p['change_working_capital_excluding_deferred']
           - (p['customer_prepayments'] - p['revenue_from_prepayments']))
    return net / (row['ebit'] * (1 - p['tax_rate']))


class ValuationIntegrityTests(unittest.TestCase):
    """Regression tests for the 2026-10-19 forecast review (each failed on the previous engine)."""

    def test_value_does_not_depend_on_the_quote(self):
        seen = []
        for q in (5., 15., 30., 60.):
            fc = forecast.build(*levered(q))
            today = value_company(build_case(fc['spec']))['annual_values'][0]
            seen.append((round(today['bear'], 9), round(today['base'], 9), round(today['bull'], 9), fc['cost_of_equity']['base'],
                         fc['cost_of_equity']['terminal_wacc']['base']))
        self.assertEqual(len(set(seen)), 1, seen)
        comp = fc['cost_of_equity']['components']
        lev = comp['leverage']
        self.assertEqual(lev['status'], 'converged')
        self.assertGreater(lev['debt_weight'], .2)                       # model value, not the quote, carries the weight
        # the table rate is the cost of equity at market-average leverage: unlevered at MARKET_DE with the EXPECTED return on debt,
        # relevered (Harris-Pringle) at the model's own D/E; Ke = sum of the rows; WACC = E/V Ke + D/V (Kd_e - Kd_promised x tax)
        ku = (comp['table_rate'] + lev['kd_expected'] * forecast.MARKET_DE) / (1 + forecast.MARKET_DE)
        self.assertAlmostEqual(comp['asset_unlevered'], ku)
        self.assertAlmostEqual(lev['kd_expected'], forecast.RF_DEFAULT + forecast.DEBT_EXPECTED_PREMIUM)
        self.assertAlmostEqual(fc['cost_of_equity']['base'], max(comp['table_rate'], ku + (ku - lev['kd_expected']) * lev['d_to_e']))
        self.assertAlmostEqual(fc['cost_of_equity']['base'], comp['risk_free'] + comp['equity_risk_premium'] + sum(a['value'] for a in comp['adjustments']))
        w = lev['debt_weight']
        self.assertAlmostEqual(fc['cost_of_equity']['terminal_wacc']['base'],
                               (1 - w) * fc['cost_of_equity']['base'] + w * (lev['kd_expected'] - lev['kd_promised'] * .21), places=6)
        self.assertIn('adjustment_leverage_relevered', comp)

    def test_cyclical_at_peak_is_valued_around_the_cycle(self):
        fb, an, card, qa, hist = peak_cyclical(stage='growth')
        fc = forecast.build(fb, an, card, qa)
        self.assertEqual(fc['status'], 'built')
        self.assertEqual(fc['anchors']['route']['stage'], 'mature')               # a cyclical peak is never a growth stage
        self.assertIn('cyclical_stage_normalized', {w['code'] for w in fc['warnings']})
        self.assertNotIn('stage:growth', json.dumps(fc['cost_of_equity']['components']['adjustments']))
        cyc = fc['anchors']['cycle']
        mean = sum(r for _, r, _ in hist) / len(hist)
        self.assertAlmostEqual(cyc['mean_revenue'], mean)
        self.assertLessEqual(cyc['long_run_growth']['base'], forecast.CYCLE_TREND[1])
        cur = fc['anchors']['current_fiscal_year']['revenue']
        self.assertLess(cur, 220e6)                                              # never the peak grown at a trough-to-peak CAGR
        base = fc['paths']['base']
        trend = cyc['trend_at_current_fy']
        for j, p in enumerate(base):
            if j >= forecast.CYCLE_CONV['base']:                                  # reverted: on the mid-cycle trend path
                self.assertLess(p['revenue_rate'], 1.01 * trend * (1 + cyc['long_run_growth']['base']) ** j)
        agg = sum(r * m for _, r, m in hist) / sum(r for _, r, _ in hist)              # the cycle's own average EBIT / average revenue
        self.assertAlmostEqual(scen(fc['spec'], 'base')['terminal']['operating_margin'], agg, places=6)
        self.assertGreater(agg, sum(m for _, _, m in hist) / len(hist) + .02)            # operating leverage: above the simple mean
        for j in range(len(base)):
            rb, rm, ru = (fc['paths'][s][j]['revenue_rate'] for s in SCEN)
            self.assertTrue(rb <= rm <= ru)
        j = forecast.CYCLE_CONV['base']                                                # reverted: mid-cycle EBIT, not peak EBIT
        self.assertLess(fc['paths']['base'][j]['revenue_rate'] * fc['paths']['base'][j]['operating_margin'], 220e6 * .25 * .6)
        today = value_company(build_case(fc['spec']))['annual_values'][0]
        self.assertTrue(today['bear'] <= today['base'] <= today['bull'] < 3 * today['base'])

    def test_current_year_uses_reported_year_to_date(self):
        rev = [751e6, 826e6, 909e6, 1000e6]
        annual = {f'{2022 + i}-12-31': {'revenue': r, 'operating_income': r * .2, 'sbc': r * .03, 'd_and_a': r * .04, 'capex': r * .04,
                                         'cfo': r * .23, 'pretax_income': r * .2, 'income_tax': r * .2 * .21, 'interest_expense': 1e6}
                  for i, r in enumerate(rev)}
        fb = synth('2026-11-15', annual, {'2025-12-31': {'cash': 200e6, 'shares_outstanding': 100e6, 'receivables': 100e6, 'payables': 40e6}},
                   15.0, kind='filing_derived')
        for end, v in (('2025-03-31', 250e6), ('2025-06-30', 250e6), ('2025-09-30', 250e6), ('2026-03-31', 150e6), ('2026-06-30', 150e6),
                       ('2026-09-30', 150e6)):           # SYNTHETIC: revenue collapses in the current fiscal year
            for m, x in (('revenue', v), ('operating_income', v * .2)):
                facts.add_fact(fb, m, 'Q@' + end, end, x, 'syn:fs', 'as_reported', (date.fromisoformat(end) + timedelta(days=35)).isoformat())
        an = make_an(fb, revenue_growth_last=.1, revenue_cagr_3y=.1, growth_volatility=.01, roic=.2, sales_to_capital_incremental_3y=2.0)
        card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'maturing'}, 'funding': {'value': 'self_funded'},
                'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
        for method in ('annual_plus_ytd', 'quarters'):
            an['ttm'] = {'revenue': 700e6, 'operating_income': 140e6, 'period_end': '2026-09-30', 'method': method}
            fc = forecast.build(fb, an, card, qa_for(100e6, 200e6, 0., '2025-12-31'))
            cur = fc['anchors']['current_fiscal_year']
            self.assertAlmostEqual(cur['revenue'], 450e6 + 150e6, delta=1)        # YTD 450 + Q4 at the YTD year-over-year run rate
            self.assertIn('year to date', cur['basis'])
            self.assertAlmostEqual(fc['anchors']['growth']['recent_annualized'], 450 / 750 - 1)
            self.assertAlmostEqual(fc['spec']['stub_annual_revenue_rate'], 150e6 / ((date(2026, 12, 31) - date(2026, 9, 30)).days / 365.25), delta=1)

    def test_self_funded_grower_spends_investments_and_settles_sbc_in_shares_never_debt(self):
        rev = [470e6, 660e6, 900e6]
        annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * m, 'sbc': r * .25, 'd_and_a': r * .03, 'capex': r * .04,
                                 'cfo': r * c, 'pretax_income': r * m, 'income_tax': 0.} for y, r, m, c in
                  zip((2023, 2024, 2025), rev, (-.30, -.22, -.14), (.08, .15, .20))}
        fb = synth('2026-03-20', annual, {'2025-12-31': {'cash': 420e6, 'shares_outstanding': 192e6, 'receivables': 160e6, 'payables': 36e6},
                                          '2023-12-31': {'receivables': 85e6, 'payables': 19e6}}, 71.0)
        an = make_an(fb, revenue_growth_last=900 / 660 - 1, revenue_cagr_3y=None, growth_volatility=.05, sales_to_capital_incremental_3y=2.5)
        card = {'business_model': {'primary': 'subscription_software'}, 'stage': {'value': 'growth'}, 'funding': {'value': 'needs_external'},
                'valuation': {'method': 'fcff_annual_path', 'archetype': 'software'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
        qa = qa_for(192e6, 420e6, 0., '2025-12-31')
        qa['resolved']['short_term_investments'] = {'value': 1080e6, 'as_of': '2025-12-31'}
        fc = forecast.build(fb, an, card, qa)
        for s in SCEN:
            f = fc['funding'][s]
            self.assertEqual((f['debt_assumed'], f['equity_raised']), (0., 0.), s)
            self.assertNotEqual(f['status'], 'assumed_external_financing')
        self.assertGreater(fc['funding']['base']['investments_liquidated'], 0)
        # the card said needs_external, the executed base plan does not: no funding add-on to the cost of equity
        self.assertNotIn('needs_external_funding', json.dumps(fc['cost_of_equity']['components']['adjustments']))
        # no investments: a shortfall caused by the cash-equivalent SBC charge becomes dilution, not debt
        qa2 = qa_for(192e6, 60e6, 0., '2025-12-31')
        fc2 = forecast.build(fb, an, card, qa2)
        b = fc2['funding']['base']
        self.assertGreater(b['sbc_settled_in_shares'], 0)
        self.assertGreater(b['sbc_settlement_shares'], 0)
        self.assertEqual(b['debt_assumed'], 0.)
        rows = next(r for r in value_company(build_case(fc2['spec']))['scenarios'] if r['name'] == 'base')['forecast']
        self.assertGreater(rows[-1]['shares'], 192e6)
        self.assertIn('internal_funding_base', {a['id'] for a in fc2['assumptions']} | {a['id'] for a in fc2['assumptions'] if a['id'].startswith('financing')})

    def test_growth_anchor_is_scale_aware(self):
        rev = [30e6, 230e6, 1920e6, 5100e6]                # SYNTHETIC: 300%+ trailing growth from a tiny base
        annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .17, 'sbc': r * .1, 'd_and_a': r * .3, 'capex': r * 2.,
                                 'cfo': r * .5, 'pretax_income': r * .1, 'income_tax': 0.} for y, r in zip(range(2022, 2026), rev)}
        fb = synth('2026-03-20', annual, {'2025-12-31': {'cash': 1.5e9, 'shares_outstanding': 480e6}}, 100.)
        an = make_an(fb, revenue_growth_last=5100 / 1920 - 1, revenue_cagr_3y=(5100 / 30) ** (1 / 3) - 1, growth_volatility=1.,
                     sales_to_capital_incremental_3y=.4)
        card = {'business_model': {'primary': 'capital_intensive_infrastructure'}, 'stage': {'value': 'growth'}, 'funding': {'value': 'needs_external'},
                'valuation': {'method': 'scenario_survival_fcff', 'archetype': 'infrastructure'}, 'dimensions': {}}
        fc = forecast.build(fb, an, card, qa_for(480e6, 1.5e9, 18e9, '2025-12-31'))
        self.assertEqual(fc['status'], 'built')
        g = fc['anchors']['growth']
        self.assertEqual(g['scale_cap']['cap'], .4)                                # 1-10bn of revenue
        self.assertLessEqual(g['trailing_anchor'], .4)
        self.assertLessEqual(fc['anchors']['current_fiscal_year']['growth'], .4 + 1e-9)
        self.assertIn('growth_scale_capped', {w['code'] for w in fc['warnings']})
        facts.add_fact(fb, 'rpo', 'I@2025-12-31', '2025-12-31', 25.9e9, 'syn:fs', 'as_reported', '2026-02-10')
        self.assertEqual(forecast.build(fb, an, card, qa_for(480e6, 1.5e9, 18e9, '2025-12-31'))['anchors']['growth']['scale_cap']['cap'], .6)

    def test_terminal_reinvestment_and_depreciation_are_continuous(self):
        fb, an, card, qa = adbe()
        fc = forecast.build(fb, an, card, qa)
        case = build_case(fc['spec'])
        res = value_company(case)
        for s in SCEN:
            sc, t = scen(fc['spec'], s), scen(fc['spec'], s)['terminal']
            self.assertAlmostEqual(sc['depreciation_pct'][-1], sc['maintenance_capex_pct'][-1], places=6)
            tr = next(x for x in res['underwriting_audit']['terminal_transitions'] if x['scenario'] == s)
            self.assertFalse(tr['reinvestment_jump'], tr)
            self.assertLess(abs(last_reinvestment(case, res, s) - t['growth'] / t['roic']), .10)
        # reinvestment identity per period: growth capex + working capital - deferred revenue change = need from the ledger rule
        need = fc['anchors']['reinvestment_identity']['base']['net_reinvestment_target']
        b = next(x for x in case['scenarios'] if x['name'] == 'base')['periods']
        for i in (0, 1):
            net = b[i]['growth_capex'] + b[i]['change_working_capital_excluding_deferred'] - (b[i]['customer_prepayments'] - b[i]['revenue_from_prepayments'])
            self.assertAlmostEqual(net, need[i], delta=abs(need[i]) * 1e-3)          # ratios are stored to 1e-6
        a = {x['id']: x for x in fc['assumptions']}
        self.assertIn('never released', a['funding_policy']['rationale'])
        self.assertGreater(a['funding_policy']['values']['minimum_cash_per_share'], 0)
        # the audit flags an explicit last year that reinvests nothing while the terminal reinvests g/ROIC
        bad = deepcopy(fc['spec'])
        sc = scen(bad, 'base')
        sc['growth_capex_pct'][-1], sc['depreciation_pct'][-1] = 0., .05
        sc['cash_cost_ratio'][-1] -= .05 - fc['spec']['scenarios'][1]['depreciation_pct'][-1]
        r = value_company(build_case(bad))
        tr = next(x for x in r['underwriting_audit']['terminal_transitions'] if x['scenario'] == 'base')
        self.assertTrue(tr['reinvestment_jump'])
        self.assertIn('base.transition_exception', {i['path'] for i in r['underwriting_audit']['issues']})

    def test_declining_business_may_keep_declining(self):
        rev = [500e6 * .92 ** i for i in range(5)]       # SYNTHETIC: -8% a year
        annual = {f'{2021 + i}-12-31': {'revenue': r, 'operating_income': r * .15, 'sbc': r * .01, 'd_and_a': r * .05, 'capex': r * .03,
                                         'cfo': r * .19, 'pretax_income': r * .15, 'income_tax': r * .15 * .21, 'interest_expense': 1e6}
                  for i, r in enumerate(rev)}
        fb = synth('2026-03-20', annual, {'2025-12-31': {'cash': 50e6, 'shares_outstanding': 20e6, 'receivables': 40e6, 'payables': 20e6}},
                   20.0, kind='filing_derived')
        an = make_an(fb, revenue_growth_last=-.08, revenue_cagr_3y=-.08, growth_volatility=.005, roic=.15)
        card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'decline'}, 'funding': {'value': 'self_funded'},
                'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'weak'}}}
        fc = forecast.build(fb, an, card, qa_for(20e6, 50e6, 0., '2025-12-31'))
        t = {s: scen(fc['spec'], s)['terminal']['growth'] for s in SCEN}
        self.assertLess(t['base'], 0)
        self.assertLessEqual(t['bear'], min(0., t['base']))
        self.assertGreaterEqual(t['bear'], forecast.DECLINE_FLOOR)
        self.assertTrue(all(g < 0 for g in scen(fc['spec'], 'base')['growth'] if g))    # no forced recovery
        val = value_company(build_case(fc['spec']))
        self.assertTrue(0 < val['annual_values'][0]['bear'] <= val['annual_values'][0]['base'])

    def test_fifth_anniversary_is_a_valuation_date_for_any_as_of(self):
        fb, an, card, qa = industrial()                          # as_of 2026-03-20, December fiscal year
        fc = forecast.build(fb, an, card, qa)
        val = value_company(build_case(fc['spec']))
        row = next(r for r in val['annual_values'] if r['date'] == '2031-03-20')
        self.assertTrue(all(row[k] is not None for k in SCEN))
        rows = next(r for r in val['scenarios'] if r['name'] == 'base')['forecast']
        split = [r for r in rows if r['end'] in ('2031-03-20', '2031-12-31')]
        self.assertAlmostEqual(split[0]['revenue'] / ((date(2031, 3, 20) - date(2030, 12, 31)).days / 365.25),
                               split[1]['revenue'] / ((date(2031, 12, 31) - date(2031, 3, 20)).days / 365.25), places=0)   # one annual rate
        short = forecast.build(fb, an, card, qa, {'horizon_years': 3})
        self.assertIn('2031-03-20', short['spec']['report_dates'])
        self.assertGreaterEqual(short['spec']['boundaries'][-1], '2032-12-31')        # one full fiscal year after the anniversary
        self.assertIn('horizon_extended_to_fifth_year', {w['code'] for w in short['warnings']})

    def test_unknown_moat_uses_the_computed_record_or_states_the_haircut(self):
        fb, an, card, qa = adbe()
        an['metrics']['incremental_roic_3y'] = {'value': .25}
        an['forensics'] = {'roic': {'series': [{'fiscal_year': f'FY{y}', 'roic': r} for y, r in zip(range(2021, 2026), (.37, .38, .44, .45, .62))]}}
        card['dimensions'] = {'moat': {'assessment': 'unknown'},
                              'pricing_power': {'assessment': 'strong', 'source': 'computed', 'evidence_ids': ['fmp:as_reported_income_annual']}}
        fc = forecast.build(fb, an, card, qa)
        route, row = fc['anchors']['route'], next(a for a in fc['assumptions'] if a['id'] == 'moat_basis')
        self.assertEqual(route['moat'], 'unknown')
        self.assertIn('computed proxy', route['moat_basis'])
        w = row['values']['proxy_weight']
        self.assertEqual((w['base'], w['bull'], w['bear']), (1., 1., 0.))           # the bear tests the moat failing
        t = {s: scen(fc['spec'], s)['terminal'] for s in SCEN}
        hurdle = next(a for a in fc['assumptions'] if a['id'] == 'moat_basis')['values']['unlevered_cost_of_capital']
        self.assertAlmostEqual(t['bear']['roic'], hurdle + .01, places=6)           # unassessed-moat bear rule, not 70% of a 40% ROIC
        self.assertGreater(t['base']['roic'], .3)
        self.assertIn('moat_computed_proxy', {x['code'] for x in fc['warnings']})
        alt = row['values']['value_today_without_proxy']                              # the proxy's value effect is shown
        self.assertLess(alt['base'], fc['preview']['today']['base'])
        self.assertAlmostEqual(alt['bear'], fc['preview']['today']['bear'], delta=.02 * alt['bear'])
        self.assertIn('moat_proxy', {m['id'] for m in fc['spec']['underwriting']['material_issues'] if m['status'] == 'unresolved'})
        # blocked: a missing incremental ROIC is not a pass; an analyst row (even 'unknown') is final; an analyst 'weak' on AI or
        # substitutes, and unassessed FactBook evidence on the moat, block the proxy
        for change in ('no_incremental', 'analyst_unknown', 'weak_ai', 'moat_evidence'):
            fb2, an2, card2 = deepcopy(fb), deepcopy(an), deepcopy(card)
            if change == 'no_incremental':
                del an2['metrics']['incremental_roic_3y']
            elif change == 'analyst_unknown':
                card2['dimensions']['moat'] = {'assessment': 'unknown', 'source': 'analyst', 'evidence_ids': ['fmp:as_reported_income_annual']}
            elif change == 'weak_ai':
                card2['dimensions']['technology_ai'] = {'assessment': 'weak', 'source': 'analyst', 'evidence_ids': ['fmp:as_reported_income_annual']}
            else:
                facts.add_qualitative(fb2, 'q1', 'moat', 'Research firm cut its moat rating (TEST ITEM)', 'opinion', ['fmp:as_reported_income_annual'],
                                      fb2['as_of'])
            fc2 = forecast.build(fb2, an2, card2, qa)
            r2 = next(a for a in fc2['assumptions'] if a['id'] == 'moat_basis')
            self.assertEqual(r2['values']['proxy_weight'], {'bear': 0., 'base': 0., 'bull': 0.}, change)
            self.assertNotIn('moat_computed_proxy', {x['code'] for x in fc2['warnings']}, change)
            self.assertGreater(r2['values']['margin_competed_away'], .02, change)   # quantified, not silent
            if change == 'analyst_unknown':
                self.assertIn('analyst assessment', fc2['anchors']['route']['moat_basis'])
            self.assertLess(fc2['preview']['today']['base'], fc['preview']['today']['base'])
        self.assertIn('moat_basis', fc['spec']['underwriting']['scenario_reviews']['base']['competitive_response']['assumption_ids'])

    def test_moat_proxy_is_continuous_in_its_evidence(self):
        # a 1pp move in gross margin around the old 60% pricing-power cutoff no longer flips base value by half
        fb, an, card, qa = adbe()
        an['metrics']['incremental_roic_3y'] = {'value': .25}
        an['forensics'] = {'roic': {'series': [{'fiscal_year': f'FY{y}', 'roic': .3} for y in range(2021, 2026)]}}
        card['dimensions'] = {'moat': {'assessment': 'unknown'}}
        vals = []
        for gm in (.59, .60):
            fb2 = deepcopy(fb)
            for f in fb2['facts']:
                if f['metric'] == 'gross_profit':
                    rev = next(x['value'] for x in fb2['facts'] if x['metric'] == 'revenue' and x['period_end'] == f['period_end'] and x['period'] == f['period'])
                    f['value'] = gm * rev
            fc = forecast.build(fb2, an, card, qa)
            vals.append((fc['preview']['today']['base'], next(a for a in fc['assumptions'] if a['id'] == 'moat_basis')['values']['proxy_weight']['base']))
        self.assertTrue(0 < vals[0][1] < vals[1][1] < 1, vals)
        self.assertLess(abs(vals[1][0] / vals[0][0] - 1), .05, vals)

    def test_guidance_provenance_sbc_period_and_no_revenue(self):
        fb, an, card, qa = adbe()
        fb['guidance'] = [{'metric': 'revenue', 'period': 'FY2026', 'low': 26.5e9, 'high': 26.7e9, 'issued_at': '2026-09-10',
                           'source_ids': ['fmp:as_reported_income_annual']}]                  # TEST VALUES
        row = next(a for a in forecast.build(fb, an, card, qa)['assumptions'] if a['id'] == 'revenue_current_fy')
        self.assertEqual((row['kind'], row['provenance']), ('guidance', 'management_guidance'))
        fb, an, card, qa = industrial()
        fb['as_of'] = fb['market']['price']['as_of'] = '2026-08-20'
        an['ttm'] = {'period_end': '2026-06-30', 'method': 'quarters', 'revenue': 610e6, 'operating_income': 125e6}
        fc = forecast.build(fb, an, card, qa)
        self.assertAlmostEqual(fc['anchors']['sbc_pct'], .05)                         # FY SBC over FY revenue, not over TTM revenue
        empty = synth('2026-03-20', {f'{y}-12-31': {'operating_income': -30e6, 'cfo': -25e6} for y in (2024, 2025)},
                      {'2025-12-31': {'cash': 200e6, 'shares_outstanding': 50e6}}, 8.0)
        out = forecast.build(empty, {'annual': {}, 'ttm': {}, 'metrics': {}}, card, qa_for(50e6, 200e6, 0., '2025-12-31'))
        self.assertEqual(out['status'], 'insufficient_data')

    def test_source_kinds_from_issuer_documents(self):
        fb = facts.new({'ticker': 'SRC', 'currency': 'USD'}, '2026-10-19')
        for sid, kind, ds, retr in (('web:q', 'issuer_document', 'filing', 'search_excerpt'), ('web:r', 'document', 'issuer_release', None),
                                    ('web:p', 'secondary', 'press', None), ('web:x', 'issuer_document', 'issuer_release', 'primary_document')):
            facts.add_source(fb, sid, 'web', ds, kind, '2026-10-19T00:00:00Z')
            if retr:
                fb['sources'][sid]['retrieval'] = retr
        out = {s['id']: (s['kind'], s.get('retrieval')) for s in forecast._case_sources(fb, ['web:q', 'web:r', 'web:p', 'web:x'], {})}
        self.assertEqual(out, {'web:q': ('filing', 'search_excerpt'), 'web:r': ('issuer_release', 'search_excerpt'),
                               'web:p': ('secondary_source', 'search_excerpt'), 'web:x': ('issuer_release', 'primary_document')})

    def test_unknown_inputs_are_noted_and_unknown_levers_rejected(self):
        fb, an, card, qa = industrial()
        o = {'path': 'mature_margin', 'scenario': 'base', 'value': .1, 'reason': 'test', 'evidence_ids': ['syn:fs']}
        fc = forecast.build(fb, an, card, qa, {'override': [o]})
        self.assertEqual(fc['overrides'], [])
        self.assertTrue(fc['notes'] and "'override'" in fc['notes'][0])
        self.assertIn('input_ignored', {w['code'] for w in fc['warnings']})
        with self.assertRaisesRegex(ValueError, 'unknown keys'):
            forecast.build(fb, an, card, qa, {'overrides': [dict(o, senario='bull')]})
        with self.assertRaisesRegex(ValueError, 'financing'):
            forecast.build(fb, an, card, qa, {'financing': 'equity_only'})
        with self.assertRaisesRegex(ValueError, 'unknown keys'):
            forecast.build(fb, an, card, qa, {'risk_free': {'value': .04, 'title': 'x', 'date': '2026-01-01'}})



def utility(industry='Utilities - Regulated Electric', debt=13.9e9):
    """SYNTHETIC regulated electric utility (test data only): 40% operating margin, capex above CFO, net debt about 4.4x EBITDA."""
    rev = [4.4e9, 4.7e9, 5.0e9, 5.3e9, 5.6e9]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .40, 'sbc': r * .004, 'd_and_a': r * .16, 'capex': r * .42,
                             'cfo': r * .5, 'pretax_income': r * .40 - debt * .045, 'income_tax': (r * .40 - debt * .045) * .21,
                             'interest_expense': debt * .045} for y, r in zip(range(2021, 2026), rev)}
    inst = {'2025-12-31': {'cash': .25e9, 'shares_outstanding': 4e8, 'receivables': .6e9, 'payables': .5e9, 'long_term_debt': debt},
            '2022-12-31': {'receivables': .5e9, 'payables': .42e9}}
    fb = synth('2026-03-20', annual, inst, 60.0, kind='filing_derived')
    fb['company'].update(sector='Utilities', industry=industry)
    an = make_an(fb, revenue_growth_last=.057, revenue_cagr_3y=.06, growth_volatility=.005, roic=.065, sales_to_capital_incremental_3y=.35)
    card = {'business_model': {'primary': 'capital_intensive_infrastructure'}, 'stage': {'value': 'mature'}, 'funding': {'value': 'self_funded'},
            'capital_intensity': {'value': 'high'}, 'valuation': {'method': 'fcff_annual_path', 'archetype': 'infrastructure'},
            'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(4e8, .25e9, debt, '2025-12-31')


def steel_at_peak():
    """SYNTHETIC steel maker whose margin rose every year to a record (no completed cycle in the history; test data only)."""
    hist = [(2021, 9e9, .06), (2022, 10e9, .09), (2023, 12e9, .14), (2024, 15e9, .20), (2025, 18e9, .27)]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * m, 'sbc': r * .005, 'd_and_a': r * .04, 'capex': r * .05,
                             'cfo': r * (m + .03), 'pretax_income': r * m, 'income_tax': r * m * .21, 'interest_expense': 50e6}
              for y, r, m in hist}
    inst = {'2025-12-31': {'cash': 2e9, 'shares_outstanding': 2e8, 'receivables': 2e9, 'inventory': 2.5e9, 'payables': 1.5e9, 'long_term_debt': 1e9},
            '2022-12-31': {'receivables': 1.2e9, 'inventory': 1.5e9, 'payables': 1e9}}
    fb = synth('2026-03-20', annual, inst, 118.0, kind='filing_derived')
    an = make_an(fb, revenue_growth_last=.2, revenue_cagr_3y=(18 / 10) ** (1 / 3) - 1, growth_volatility=.05, roic=.4,
                 sales_to_capital_incremental_3y=1.2)
    card = {'business_model': {'primary': 'commodity'}, 'stage': {'value': 'growth'}, 'funding': {'value': 'self_funded'},
            'cyclical': {'value': True, 'normalize': False, 'position': 'peak', 'basis': 'industry_flag', 'data_shows_cycle': False},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'commodity'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(2e8, 2e9, 1e9, '2025-12-31'), hist


class RepairRegressionTests(unittest.TestCase):
    """2026-10-20 adversarial review of the forecast/valuation rewrite: each test failed on the engine before the repair."""

    def test_higher_coupon_on_the_same_debt_lowers_equity_value(self):
        # the promised coupon used to be the relevering Kd: a higher coupon lowered Ke and the terminal WACC and RAISED value
        seen = []
        for interest in (48e6, 72e6, 96e6):
            fb, an, card, qa = levered(30.)
            for f in fb['facts']:
                if f['metric'] == 'interest_expense':
                    f['value'] = interest
            for e in an['annual']:
                an['annual'][e]['interest_expense'] = interest
            fc = forecast.build(fb, an, card, qa)
            lev = fc['cost_of_equity']['components']['leverage']
            seen.append((fc['preview']['today']['base'], fc['cost_of_equity']['base'], lev['kd_expected'], lev['kd_promised'],
                         {w['code'] for w in fc['warnings']}))
        self.assertTrue(seen[0][0] > seen[1][0] > seen[2][0], seen)
        self.assertTrue(seen[0][1] <= seen[1][1] <= seen[2][1], seen)           # never a lower Ke for a costlier borrower
        self.assertEqual({x[2] for x in seen}, {forecast.RF_DEFAULT + forecast.DEBT_EXPECTED_PREMIUM})
        self.assertAlmostEqual(seen[2][3], .12)
        self.assertIn('debt_rate_unusual', seen[2][4])                              # 12% is outside risk-free + 0..6pp: a data question

    def test_regulated_utility_is_not_double_levered_and_a_capped_weight_is_a_draft(self):
        fc = forecast.build(*utility())
        comp, lev = fc['cost_of_equity']['components'], fc['cost_of_equity']['components']['leverage']
        self.assertIn('regulated_utility', [a['factor'] for a in comp['adjustments']])
        self.assertEqual(lev['status'], 'converged')
        self.assertTrue(.065 < fc['cost_of_equity']['base'] < .10, fc['cost_of_equity']['base'])   # was ~18.9% (leverage on a beta-1 Ku)
        self.assertGreater(fc['preview']['today']['base'], 30)
        merchant = forecast.build(*utility('Utilities - Renewable'))                   # no regulated returns: market asset risk
        self.assertNotIn('regulated_utility', [a['factor'] for a in merchant['cost_of_equity']['components']['adjustments']])
        self.assertGreater(merchant['cost_of_equity']['base'], fc['cost_of_equity']['base'])
        capped = forecast.build(*utility('Utilities - Renewable', debt=30e9))
        self.assertEqual(capped['cost_of_equity']['components']['leverage']['status'], 'capped')
        issues = {m['id']: m for m in capped['spec']['underwriting']['material_issues']}
        self.assertEqual(issues['leverage_at_cap']['status'], 'unresolved')             # the cap constant sets the value: a draft
        self.assertIn('leverage_capped', {w['code'] for w in capped['warnings']})

    def test_new_shares_are_issued_at_the_lower_of_quote_and_own_value(self):
        rev = [470e6, 660e6, 900e6]
        annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * m, 'sbc': r * .25, 'd_and_a': r * .03, 'capex': r * .04,
                                 'cfo': r * c, 'pretax_income': r * m, 'income_tax': 0.} for y, r, m, c in
                  zip((2023, 2024, 2025), rev, (-.30, -.22, -.14), (.08, .15, .20))}
        an_card = lambda fb: (make_an(fb, revenue_growth_last=900 / 660 - 1, revenue_cagr_3y=None, growth_volatility=.05, sales_to_capital_incremental_3y=2.5),
                              {'business_model': {'primary': 'subscription_software'}, 'stage': {'value': 'growth'}, 'funding': {'value': 'needs_external'},
                               'valuation': {'method': 'fcff_annual_path', 'archetype': 'software'}, 'dimensions': {'moat': {'assessment': 'adequate'}}})
        out = {}
        for q in (2., 71., 200.):
            fb = synth('2026-03-20', annual, {'2025-12-31': {'cash': 60e6, 'shares_outstanding': 192e6, 'receivables': 160e6, 'payables': 36e6},
                                              '2023-12-31': {'receivables': 85e6, 'payables': 19e6}}, q)
            an, card = an_card(fb)
            out[q] = forecast.build(fb, an, card, qa_for(192e6, 60e6, 0., '2025-12-31'))
        b = {q: fc['funding']['base'] for q, fc in out.items()}
        self.assertEqual(b[71.]['status'], 'share_settled')                            # not 'self_funded': SBC is partly paid in shares
        self.assertGreater(b[71.]['sbc_settled_in_shares'], 0)
        # above the scenario's own value the quote no longer matters (it moved 24% between quotes 20 and 200 before)
        self.assertAlmostEqual(out[71.]['preview']['today']['base'], out[200.]['preview']['today']['base'], places=6)
        self.assertTrue(all(x['price'] <= 71. and not x['quote_binding'] for x in b[71.]['issue_prices']))
        self.assertFalse(b[71.]['price_dependent'])
        # a quote below the scenario's value dilutes at the quote: stated, with the value at the scenario's own price
        self.assertTrue(b[2.]['price_dependent'])
        self.assertIn('value_depends_on_quote', {w['code'] for w in out[2.]['warnings']})
        self.assertGreater(b[2.]['value_if_issued_at_own_value'], out[2.]['preview']['today']['base'])
        self.assertNotIn('same at any price', next(a for a in out[2.]['assumptions'] if a['id'] == 'cost_of_equity')['rationale'])

    def test_growth_lever_charges_capital_for_the_growth_it_adds(self):
        fb, an, card, qa = industrial()
        fc = forecast.build(fb, an, card, qa)
        price = 1.3 * fc['preview']['today']['base']
        row = next(r for r in forecast.what_must_be_true(fc['spec'], price) if r['lever'] == 'growth')
        self.assertEqual(row['status'], 'solved')
        shift = row['detail']['growth_shift']
        spec = deepcopy(fc['spec'])
        spec['quote']['price'] = price
        b = scen(spec, 'base')
        steps = [i for i, e in enumerate(spec['boundaries'][:-1]) if e not in spec['interim_boundaries']][:5]
        b['growth'] = [g + (shift if i in steps else 0) for i, g in enumerate(b['growth'])]
        free = forecast._value_paused(deepcopy(spec))                                    # the old lever: capex a fixed share of revenue
        b['growth_capex_pct'], b['maintenance_capex_pct'] = forecast._capex_for_path(spec, b, b['reinvestment_rule'])
        # distributions pause where the faster path needs the cash (value-neutral); then the solved path reproduces the price
        self.assertAlmostEqual(forecast._value_paused(spec), price, delta=.01 * price)
        self.assertGreater(free, price * 1.001)                                          # growth was nearly free before

    def test_implied_growth_shift_also_charges_capital(self):
        from fundamental_engine.drivers import implied_growth_shift
        fb, an, card, qa = industrial()
        fc = forecast.build(fb, an, card, qa)
        spec = deepcopy(fc['spec'])
        spec['quote']['price'] = 1.1 * fc['preview']['today']['base']
        r = implied_growth_shift(spec)
        self.assertEqual(r['status'], 'solved', r)
        self.assertIn('reinvestment rule', r['limitation'])
        b = scen(spec, 'base')
        steps = [i for i, e in enumerate(spec['boundaries'][:-1]) if e not in spec['interim_boundaries']]
        b['growth'] = [g + (r['growth_shift'] if i in steps else 0) for i, g in enumerate(b['growth'])]
        b['growth_capex_pct'], b['maintenance_capex_pct'] = forecast._capex_for_path(spec, b, b['reinvestment_rule'])
        self.assertAlmostEqual(value_company(build_case(spec))['annual_values'][0]['base'], spec['quote']['price'], delta=.001 * spec['quote']['price'])

    def test_declining_business_releases_capital_in_the_explicit_years(self):
        rev = [500e6 * .92 ** i for i in range(5)]
        annual = {f'{2021 + i}-12-31': {'revenue': r, 'operating_income': r * .15, 'sbc': r * .01, 'd_and_a': r * .05, 'capex': r * .03,
                                         'cfo': r * .19, 'pretax_income': r * .15, 'income_tax': r * .15 * .21, 'interest_expense': 1e6}
                  for i, r in enumerate(rev)}
        fb = synth('2026-03-20', annual, {'2025-12-31': {'cash': 50e6, 'shares_outstanding': 20e6, 'receivables': 40e6, 'payables': 20e6}},
                   20.0, kind='filing_derived')
        an = make_an(fb, revenue_growth_last=-.08, revenue_cagr_3y=-.08, growth_volatility=.005, roic=.15)
        card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'decline'}, 'funding': {'value': 'self_funded'},
                'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'weak'}}}
        fc = forecast.build(fb, an, card, qa_for(20e6, 50e6, 0., '2025-12-31'))
        res = value_company(build_case(fc['spec']))
        for tr in res['underwriting_audit']['terminal_transitions']:
            self.assertFalse(tr['reinvestment_jump'], tr)                                 # the default build passes its own continuity check
            self.assertLess(tr['last_net_reinvestment_rate'], 0)
        self.assertNotIn('transition_exception', json.dumps(res['underwriting_audit']['issues']))
        self.assertNotIn('replacement_exception', json.dumps(res['underwriting_audit']['issues']))   # assets still partly replaced
        base = scen(fc['spec'], 'base')
        self.assertLess(min(base['maintenance_capex_pct']), fc['anchors']['capex_pct_3y'] - 1e-6)   # assets not replaced: capital released

    def test_amortization_roll_off_is_not_a_recurring_cash_cost(self):
        fb, an, card, qa = adbe()                                                          # strong moat card: margin target held
        fc = forecast.build(fb, an, card, qa)
        sc = scen(fc['spec'], 'base')
        ebitda = [1 - c for c in sc['cash_cost_ratio']]                                    # EBITDA before SBC
        dep0, maint = fc['anchors']['da_pct'], sc['maintenance_capex_pct'][-1]
        self.assertGreater(dep0 - maint, .02)
        target = next(a for a in fc['assumptions'] if a['id'] == 'margin_base')['values']['base']['mature']
        acq = .0003 / (dep0 - maint)
        self.assertAlmostEqual(sc['terminal']['operating_margin'], target + (dep0 - maint) * (1 - acq), places=5)
        self.assertGreater(ebitda[-1], ebitda[0] - sc['sbc_pct'][0] + sc['sbc_pct'][-1] - .005)   # EBITDA not cut by the D&A roll-off
        an['metrics']['acquisition_spend_pct_revenue_3y'] = {'value': .05}                # recurring acquisitions: amortization is a cost
        fc2 = forecast.build(fb, an, card, qa)
        self.assertAlmostEqual(scen(fc2['spec'], 'base')['terminal']['operating_margin'], target, places=5)

    def test_cyclical_peak_without_card_normalization_is_valued_around_the_cycle(self):
        fb, an, card, qa, hist = steel_at_peak()
        fc = forecast.build(fb, an, card, qa)
        route = fc['anchors']['route']
        self.assertTrue(route['cycle_path'] and route['cycle_peak'])
        self.assertEqual(route['stage'], 'mature')                                        # never staged as a grower at the peak
        w = {x['code']: x for x in fc['warnings']}
        self.assertEqual(w['cyclical_peak_unconfirmed']['severity'], 'high')
        issues = {m['id']: m for m in fc['spec']['underwriting']['material_issues']}
        self.assertEqual(issues['cyclical_peak_unconfirmed']['status'], 'unresolved')
        mid = sum(r * m for _, r, m in hist) / sum(r for _, r, _ in hist)
        self.assertLessEqual(scen(fc['spec'], 'bear')['terminal']['operating_margin'], mid)
        self.assertAlmostEqual(scen(fc['spec'], 'base')['terminal']['operating_margin'], mid, places=5)
        self.assertLess(fc['anchors']['current_fiscal_year']['revenue'], 18e9)          # the peak is not grown
        row = next(a for a in fc['assumptions'] if a['id'] == 'cycle_position')
        self.assertGreater(row['values']['value_if_structural']['base'], fc['preview']['today']['base'])   # the switch's value effect
        # an analyst who shows the margin is structural keeps the margin path, but the bear still tests the cycle
        ok = {'treatment': 'structural', 'reason': 'new low-cost capacity (TEST)', 'evidence_ids': ['syn:fs']}
        fc2 = forecast.build(fb, an, card, qa, {'cyclical': ok})
        self.assertFalse(fc2['anchors']['route']['cycle_path'])
        self.assertLessEqual(scen(fc2['spec'], 'bear')['terminal']['operating_margin'], mid + 1e-9)
        self.assertEqual({m['id']: m['status'] for m in fc2['spec']['underwriting']['material_issues']}['cyclical_peak_unconfirmed'], 'unresolved')
        rv = {'cycle_position': {'status': 'reviewed', 'reviewer': 'independent test reviewer'}}
        fc3 = forecast.build(fb, an, card, qa, {'cyclical': ok, 'reviews': rv})
        self.assertEqual({m['id']: m['status'] for m in fc3['spec']['underwriting']['material_issues']}['cyclical_peak_unconfirmed'], 'resolved')
        for bad in ({'treatment': 'maybe', 'reason': 'x', 'evidence_ids': ['syn:fs']}, {'treatment': 'structural', 'reason': '', 'evidence_ids': ['syn:fs']},
                    {'treatment': 'structural', 'reason': 'x', 'evidence_ids': ['nope']}):
            with self.assertRaises(ValueError):
                forecast.build(fb, an, card, qa, {'cyclical': bad})

    def test_lender_shaped_balance_sheet_is_a_visible_draft(self):
        fb, an, card, qa = industrial()
        for m, v in (('total_assets', 10e9), ('total_liabilities', 9.1e9)):
            facts.add_fact(fb, m, 'I@2025-12-31', '2025-12-31', v, 'syn:fs', 'as_reported', '2026-02-10')
        fc = forecast.build(fb, an, card, qa)
        self.assertIn('possible_lender', {w['code'] for w in fc['warnings']})
        self.assertEqual({m['id']: m['status'] for m in fc['spec']['underwriting']['material_issues']}['possible_lender'], 'unresolved')
        facts.add_fact(fb, 'cost_of_revenue', 'FY2025', '2025-12-31', 300e6, 'syn:fs', 'as_reported', '2026-02-10')   # an operating business
        self.assertNotIn('possible_lender', {w['code'] for w in forecast.build(fb, an, card, qa)['warnings']})

    def test_survival_bear_margin_is_anchored_to_the_companys_history(self):
        fb, an, card, qa = utility()
        card['valuation']['method'] = 'scenario_survival_fcff'
        fc = forecast.build(fb, an, card, qa)
        bear = scen(fc['spec'], 'bear')['terminal']['operating_margin']
        self.assertGreater(bear, .40 - forecast.STAGE['mature']['m_spread'] - .01)        # was 0.35 x the 30% type margin = 10.5%

    def test_minimum_cash_disclosure_uses_the_peak_and_acquired_revenue_is_flagged(self):
        fb, an, card, qa = adbe()
        facts.add_qualitative(fb, 'acq1', 'revenue_sources', 'Acquisition of a SEO software company closed 2026-04-28 for $1.9B (TEST ITEM).',
                              'reported', ['fmp:as_reported_income_annual'], '2026-08-28')
        fc = forecast.build(fb, an, card, qa)
        row = next(a for a in fc['assumptions'] if a['id'] == 'funding_policy')
        peak = max(scen(fc['spec'], 'base')['minimum_cash'])
        self.assertAlmostEqual(row['values']['minimum_cash_peak'], peak, delta=1)
        self.assertAlmostEqual(row['values']['minimum_cash_per_share'], peak / 389.2e6, places=6)
        self.assertIn('anchor_includes_acquired_revenue', {w['code'] for w in fc['warnings']})

    def test_convertibles_from_dataqa_are_valued_if_converted(self):
        fb, an, card, qa = industrial()
        qa['fields'] = {'convertibles': {'status': 'reported', 'instruments': [{'id': 'cv29', 'principal': 80e6, 'conversion_price': 10.,
                                                                                'conversion_shares': 8e6, 'accreted_claim': 80e6, 'source_id': 'syn:fs'}]}}
        fc = forecast.build(fb, an, card, qa)
        self.assertEqual(fc['spec']['opening']['convertibles'], [{'id': 'cv29', 'claim': 80e6, 'conversion_shares': 8e6}])
        plain = forecast.build(*industrial())
        v, w = fc['preview']['today']['base'], plain['preview']['today']['base']
        self.assertGreater(w, 10)                                                          # deep in the money at the model's value
        self.assertLess(v, w)
        self.assertIn('if-converted', next(a for a in fc['assumptions'] if a['id'] == 'dilution')['rationale'])


class SourceAndRateContractTests(unittest.TestCase):
    def test_case_sources_carry_the_document_date_and_how_it_was_read(self):
        fb = facts.new({'ticker': 'SRC', 'currency': 'USD'}, '2026-10-19')
        rows = (('web:q', 'issuer_document', 'filing', 'primary_document', '2026-08-01'),
                ('web:reg', 'primary_document', 'regulator', 'primary_document', '2026-07-01'),
                ('web:st', 'primary_document', 'official_statistics', None, '2026-06-01'),
                ('fmp:is', 'filing_derived', 'income-statement', 'primary_document', None),
                ('fmp:v', 'vendor_normalized', 'ratios', 'not_a_retrieval', None))
        for sid, kind, ds, retr, pub in rows:
            facts.add_source(fb, sid, 'web', ds, kind, '2026-10-19T00:00:00Z')
            if retr:
                fb['sources'][sid]['retrieval'] = retr
            if pub:
                fb['sources'][sid]['published_at'] = pub
        facts.add_fact(fb, 'revenue', 'Q2-2026', '2026-06-30', 1e9, 'web:q', 'as_reported', '2026-08-03')
        facts.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 4e9, 'fmp:is', 'as_reported', '2026-02-20')
        out = {s['id']: s for s in forecast._case_sources(fb, [r[0] for r in rows], {})}
        self.assertEqual({k: (s['kind'], s.get('retrieval')) for k, s in out.items()},
                         {'web:q': ('filing', 'primary_document'), 'web:reg': ('regulator', 'primary_document'),
                          'web:st': ('official_statistics', 'search_excerpt'), 'fmp:is': ('filing', 'primary_document'),
                          'fmp:v': ('provider_data', 'provider_normalized')})
        self.assertEqual((out['web:q']['published_at'], out['web:q']['available_at']), ('2026-08-01', '2026-08-03'))
        self.assertEqual(out['web:reg']['published_at'], '2026-07-01')                     # document date even without facts
        self.assertEqual((out['fmp:is']['published_at'], out['fmp:is']['available_at']), ('2026-02-20', '2026-02-20'))
        self.assertEqual({k for k, s in out.items() if s.get('published_at_estimated')}, {'fmp:is', 'fmp:v'})
        for s in out.values():
            self.assertTrue(s['published_at'] <= s['available_at'] <= '2026-10-19', s)

    def test_a_rate_typed_into_the_inputs_is_analyst_input(self):
        fb, an, card, qa = adbe()
        rf = {'value': .045, 'as_of': '2026-10-16', 'title': 'US 10y Treasury constant maturity (test value)', 'url': 'https://example.org/dgs10'}
        for kind in ('filing', ' Filing', 'earnings_release', 'issuer_release', 'official_statistics', 'regulator', 'issuer_document',
                     'filing_derived'):
            with self.assertRaisesRegex(ValueError, 'analyst input'):
                forecast.build(fb, an, card, qa, {'risk_free': dict(rf, kind=kind)})
        with self.assertRaisesRegex(ValueError, 'retrieval'):
            forecast.build(fb, an, card, qa, {'risk_free': dict(rf, retrieval='read_it')})
        fc = forecast.build(fb, an, card, qa, {'risk_free': dict(rf, kind='market_data'),
                                               'equity_risk_premium': {'value': .05, 'title': 'own ERP view (test)'}})
        src = {s['id']: s for s in fc['spec']['sources']}
        rfs, erps = src['input:risk_free'], src['input:equity_risk_premium']
        self.assertEqual((rfs['kind'], rfs['stated_kind'], rfs['published_at']), ('analyst_input', 'market_data', '2026-10-16'))
        self.assertNotIn('published_at_estimated', rfs)
        self.assertEqual(erps['kind'], 'analyst_input')
        self.assertTrue(erps['published_at_estimated'])                                     # undated: the FactBook date

    def test_terminal_debt_weight_is_in_the_case_and_rate_diagnostics_use_the_equity_weight(self):
        from fundamental_engine import valuation_tools as vt
        from fundamental_engine.decision import price_for_hurdle
        fc = forecast.build(*levered(15.))
        w = fc['cost_of_equity']['components']['leverage']['debt_weight']
        self.assertGreater(w, .2)
        case = build_case(fc['spec'])
        self.assertEqual({s['terminal']['debt_weight'] for s in case['scenarios']}, {w})
        self.assertEqual(vt.debt_weight(case)[0], w)

        def at(shift):
            c = deepcopy(case)
            b = scen(c, 'base')
            b['cost_of_equity'] += shift
            b['terminal']['wacc'] += (1 - w) * shift                                       # Harris-Pringle, never 1:1
            return value_company(c)['annual_values'][0]['base']
        band = vt.discount_rate_band(case)
        self.assertAlmostEqual(band['value_lower_rate'], at(-.01), places=6)
        self.assertAlmostEqual(band['value_higher_rate'], at(.01), places=6)
        self.assertAlmostEqual(band['terminal_wacc_shift'], (1 - w) * .01)
        r = vt.implied_cost_of_equity(case)
        self.assertEqual(r['status'], 'solved')
        self.assertAlmostEqual(at(r['shift']), case['quote']['price'], places=4)
        h = r['implied_cost_of_equity'] + .01                                              # the decision layer's price at a hurdle
        c = deepcopy(case)
        c['quote']['price'] = price_for_hurdle(case, h, w)['value']
        self.assertAlmostEqual(vt.implied_cost_of_equity(c)['implied_cost_of_equity'], h, places=6)



class LongWindowSalesToCapitalTests(unittest.TestCase):
    def test_a_three_year_plateau_does_not_set_the_capital_need(self):
        fb, an, card, qa = plateau_builder()
        fc = forecast.build(fb, an, card, qa)
        codes = {w['code'] for w in fc['warnings']}
        self.assertIn('sales_to_capital_long_window', codes)
        used = next(a for a in fc['assumptions'] if a['id'] == 'reinvestment')
        self.assertIn('history 0.22', used['rationale'])
        self.assertIn('base 0.86', used['rationale'])         # 5-year ratio (440-200)/(380-100), not the plateau's 0.22



class MoatProxyFallbackTests(unittest.TestCase):
    def test_operating_margin_stands_in_when_no_gross_margin_is_reported(self):
        h = {'gross_margins': [], 'operating_margins': [.56, .57, .58, .58, .58], 'roic_avg': .45, 'roic_series': [1] * 5, 'inc_roic': .40}
        p = forecast._moat_proxy({'dimensions': {}}, h, {'qualitative': []}, False)
        self.assertGreater(p['weight'], .9)
        self.assertIn('no gross margin reported', p['evidence'])
        h['operating_margins'] = [.40, .30, .22, .25, .21]                     # falling and volatile: no pricing-power evidence
        self.assertEqual(forecast._moat_proxy({'dimensions': {}}, h, {'qualitative': []}, False)['weight'], 0.)


class ThinMarginCapTests(unittest.TestCase):
    """An established company that never earned the type-typical margin keeps its own best margin (McKesson, Cigna 2026-10)."""
    def _route(self, stage):
        return {'stage': stage, 'type': (None, .15, .30, None), 'cycle_path': False, 'method': 'fcff', 'moat': 'unknown',
                'moat_weights': {'base': 0, 'bear': 0, 'bull': 0}, 'cycle_peak': False}

    def _h(self):
        ms = [.032, .034, .033, .035, .036, .034, .035, .036, .041, .036]
        return {'m0': .036, 'm_best3': .041, 'm_all': ms, 'rows': [(str(2016 + i), {'revenue': 100., 'operating_income': m * 100})
                                                                    for i, m in enumerate(ms)]}

    def test_mature_base_holds_own_median_margin(self):
        t, why = forecast._margin_targets(self._route('mature'), self._h(), lambda *a: None)
        self.assertAlmostEqual(t['base'], .036)                       # today's 3.6% (above the 3.5% median); never the best year 4.1%
        self.assertIn('own median margin', why)
        self.assertGreaterEqual(t['bull'], .041)

    def test_swinging_margins_are_not_structurally_thin(self):
        h = self._h()
        h['m_all'] = [-.29, -.10, .127, -.036, -.041, .049, .076, .03, .02, .036]       # losses and a 12.7% year: scaling, not thin
        t, why = forecast._margin_targets(self._route('maturing'), h, lambda *a: None)
        self.assertNotIn('own median margin', why)
        self.assertGreater(t['base'], .05)

    def test_growth_stage_still_converges(self):
        t, _ = forecast._margin_targets(self._route('growth'), self._h(), lambda *a: None)
        self.assertGreater(t['base'], .08)


def acquirer(pro_forma=True):
    """SYNTHETIC retailer (shaped like Dick's Sporting Goods 2026): 100 a year growing 3%, buys a business with 60 of yearly
    revenue during the last fiscal year; the year to date (H1) shows +60% against months before the closing."""
    rev = {2023: 94.3e9, 2024: 97.1e9, 2025: 125e9}
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .08, 'sbc': r * .005, 'd_and_a': r * .03, 'capex': r * .03,
                             'cfo': r * .09, 'pretax_income': r * .08, 'income_tax': r * .08 * .24} for y, r in rev.items()}
    inst = {'2025-12-31': {'cash': 5e9, 'shares_outstanding': 1e9, 'receivables': 3e9, 'payables': 8e9}}
    fb = synth('2026-10-20', annual, inst, 100.0, kind='filing_derived')
    for end, start, r in (('2025-03-31', '2025-01-01', 25e9), ('2025-06-30', '2025-04-01', 25e9),
                          ('2026-03-31', '2026-01-01', 40e9), ('2026-06-30', '2026-04-01', 40e9)):
        for m, k in (('revenue', 1.), ('operating_income', .08)):
            facts.add_fact(fb, m, 'Q@' + end, end, r * k, 'syn:fs', 'as_reported', end[:8] + '28', period_start=start)
    if pro_forma:                                                     # H1 2025 as if the business had been owned: 50 + 30
        facts.add_fact(fb, 'revenue_pro_forma', 'PF@2025-01-01..2025-06-30', '2025-06-30', 78e9, 'syn:fs', 'as_reported',
                       '2026-07-28', period_start='2025-01-01')
    an = make_an(fb, revenue_growth_last=.287, revenue_cagr_3y=.10, growth_volatility=.1, roic=.15, sales_to_capital_incremental_3y=2.0)
    an['acquisition_years'] = [{'period_end': '2025-12-31', 'fiscal_period': 'FY2025'}]
    an['metrics']['revenue_growth_series'] = {'value': [{'period_end': '2024-12-31', 'growth': .03}, {'period_end': '2025-12-31', 'growth': .287}]}
    card = {'business_model': {'primary': 'consumer_brand'}, 'stage': {'value': 'mature'}, 'funding': {'value': 'self_funded'},
            'cyclical': {'value': False}, 'valuation': {'method': 'fcff_annual_path', 'archetype': 'consumer'},
            'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(1e9, 5e9, 0., '2025-12-31')


class AcquiredRevenueTests(unittest.TestCase):
    def test_pro_forma_makes_bought_revenue_a_level_shift(self):
        fc = forecast.build(*acquirer())
        self.assertEqual(fc['status'], 'built')
        base = scen(fc['spec'], 'base')['growth']
        self.assertLess(base[0], .04)                                 # organic 80/78 - 1 = 2.6%, never the +60% year to date
        cur = fc['anchors']['current_fiscal_year']
        self.assertAlmostEqual(cur['revenue'], 80e9 + (125e9 - 50e9) * 80 / 78, delta=1e6)
        self.assertIn('organic growth', cur['basis'])
        g = next(a for a in fc['assumptions'] if a['id'] == 'growth_base')
        self.assertIn('Faded from organic growth 2.6%', g['rationale'])

    def test_without_pro_forma_pre_deal_growth_is_used_and_flagged(self):
        fc = forecast.build(*acquirer(pro_forma=False))
        self.assertLess(scen(fc['spec'], 'base')['growth'][0], .04)   # pre-deal like-for-like 3%
        w = {x['code']: x for x in fc['warnings']}
        self.assertEqual(w['acquired_growth_unverified']['severity'], 'high')


class CyclicalGrowthStartTests(unittest.TestCase):
    def test_flagged_cyclical_starts_from_long_run_growth(self):
        fb, an, card, qa = ytd_surge()
        an['metrics']['revenue_cagr_5y'] = {'value': .04, 'formula': 'test fixture', 'status': 'reported'}
        card['cyclical'] = {'value': True, 'normalize': False, 'basis': 'industry_flag', 'industry_flag': True}
        fc = forecast.build(fb, an, card, qa)
        self.assertLess(scen(fc['spec'], 'base')['growth'][0], .05)     # not the +100% year to date faded
        self.assertIn('cyclical_growth_start', {w['code'] for w in fc['warnings']})
        g = next(a for a in fc['assumptions'] if a['id'] == 'growth_base')
        self.assertIn('long-run like-for-like growth 4.0%', g['rationale'])


if __name__ == '__main__':
    unittest.main()


def ytd_surge():
    """SYNTHETIC large company whose year to date doubles (shaped like NVIDIA fiscal 2027): $200bn last year, +100% YTD."""
    rev = {2023: 60e9, 2024: 120e9, 2025: 200e9}
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .55, 'sbc': r * .03, 'd_and_a': r * .02, 'capex': r * .03,
                             'cfo': r * .5, 'pretax_income': r * .55, 'income_tax': r * .55 * .15} for y, r in rev.items()}
    inst = {'2025-12-31': {'cash': 50e9, 'shares_outstanding': 10e9, 'receivables': 30e9, 'payables': 10e9}}
    fb = synth('2026-08-20', annual, inst, 100.0, kind='filing_derived')
    for end, start, r in (('2025-03-31', '2025-01-01', 40e9), ('2025-06-30', '2025-04-01', 45e9),
                          ('2026-03-31', '2026-01-01', 80e9), ('2026-06-30', '2026-04-01', 90e9)):
        for m, k in (('revenue', 1.), ('operating_income', .6)):
            facts.add_fact(fb, m, 'Q@' + end, end, r * k, 'syn:fs', 'as_reported', end[:8] + '28', period_start=start)
    an = make_an(fb, revenue_growth_last=.667, revenue_cagr_3y=.82, growth_volatility=.2, roic=.8, sales_to_capital_incremental_3y=2.0)
    card = {'business_model': {'primary': 'semiconductor'}, 'stage': {'value': 'growth'}, 'funding': {'value': 'self_funded'},
            'cyclical': {'value': False}, 'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'},
            'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(10e9, 50e9, 0., '2025-12-31')


class GrowthStartCapTests(unittest.TestCase):
    """Regression from the NVIDIA 2026-10-20 run: a doubling year to date started the next year at +73% although the
    warning said the anchor was capped at the 15% revenue-scale base rate."""

    def test_first_forecast_year_respects_the_scale_cap(self):
        fc = forecast.build(*ytd_surge())
        self.assertEqual(fc['status'], 'built')
        self.assertAlmostEqual(fc['anchors']['current_fiscal_year']['growth'], 1.0)      # the current year keeps the evidence
        base = scen(fc['spec'], 'base')['growth']
        self.assertAlmostEqual(base[0], .15)
        self.assertTrue(all(g <= .15 + 1e-9 for g in base))
        self.assertIn('growth_start_capped', {w['code'] for w in fc['warnings']})

    def test_analyst_override_with_evidence_lifts_the_cap(self):
        fb, an, card, qa = ytd_surge()
        fc = forecast.build(fb, an, card, qa, {'overrides': [{'path': 'growth_anchor', 'scenario': 'base', 'value': .6,
                                                             'reason': 'company guidance for next year (test)', 'evidence_ids': ['syn:fs']}]})
        self.assertAlmostEqual(scen(fc['spec'], 'base')['growth'][0], .6)


class GrowthOverrideReinvestmentTests(unittest.TestCase):
    """Regression from the NVIDIA 2026-10-20 run: a raw growth override kept the growth capex of the discarded engine path."""

    def test_growth_override_is_charged_its_own_capital(self):
        fb, an, card, qa = ytd_surge()
        n = len(scen(forecast.build(fb, an, card, qa)['spec'], 'base')['growth'])
        def capex(path):
            fc = forecast.build(fb, an, card, qa, {'overrides': [{'path': 'growth', 'scenario': 'base', 'value': [path] * n,
                                                                 'reason': 'test path', 'evidence_ids': ['syn:fs']}]})
            sc = scen(fc['spec'], 'base')
            return sc['growth_capex_pct'], fc
        fast, fc = capex(.30)
        slow, _ = capex(.02)
        self.assertGreater(sum(fast[1:]), sum(slow[1:]) * 2)          # more growth, more capital
        self.assertIn('growth_capex_recomputed', {w['code'] for w in fc['warnings']})
        reinv = next(a for a in fc['assumptions'] if a['id'] == 'reinvestment')
        self.assertEqual([round(x, 6) for x in fast], [round(x, 6) for x in reinv['values']['base']['growth_capex_pct']])

    def test_explicit_capex_override_is_respected(self):
        fb, an, card, qa = ytd_surge()
        sc0 = scen(forecast.build(fb, an, card, qa)['spec'], 'base')
        n, m = len(sc0['growth']), len(sc0['growth_capex_pct'])
        fc = forecast.build(fb, an, card, qa, {'overrides': [
            {'path': 'growth', 'scenario': 'base', 'value': [.3] * n, 'reason': 'test path', 'evidence_ids': ['syn:fs']},
            {'path': 'growth_capex_pct', 'scenario': 'base', 'value': [.01] * m, 'reason': 'test capex', 'evidence_ids': ['syn:fs']}]})
        self.assertEqual(scen(fc['spec'], 'base')['growth_capex_pct'], [.01] * m)
