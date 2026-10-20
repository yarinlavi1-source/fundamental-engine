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
        scen(spec, 'base')['growth'] = [g + (shift if i in steps else 0) for i, g in enumerate(scen(spec, 'base')['growth'])]
        self.assertAlmostEqual(value_company(build_case(spec))['annual_values'][0]['base'], price, delta=.01)
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
        self.assertAlmostEqual(base['terminal']['operating_margin'], sum(margins) / len(margins), places=6)
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
        # Ke relevered from Ku (Harris-Pringle) and listed as its own row: Ke = sum of the rows; WACC = Ku - w Kd t
        self.assertAlmostEqual(fc['cost_of_equity']['base'], comp['unlevered'] + (comp['unlevered'] - lev['kd']) * lev['d_to_e'])
        self.assertAlmostEqual(fc['cost_of_equity']['base'], comp['risk_free'] + comp['equity_risk_premium'] + sum(a['value'] for a in comp['adjustments']))
        self.assertAlmostEqual(fc['cost_of_equity']['terminal_wacc']['base'], comp['unlevered'] - lev['debt_weight'] * lev['kd'] * .21, places=6)
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
        self.assertAlmostEqual(scen(fc['spec'], 'base')['terminal']['operating_margin'], sum(m for _, _, m in hist) / len(hist), places=6)
        for j in range(len(base)):
            rb, rm, ru = (fc['paths'][s][j]['revenue_rate'] for s in SCEN)
            self.assertTrue(rb <= rm <= ru)
        self.assertLess(fc['paths']['base'][-1]['revenue_rate'] * scen(fc['spec'], 'base')['terminal']['operating_margin'], 220e6 * .25 * .6)
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
        card['dimensions'] = {'moat': {'assessment': 'unknown'},
                              'pricing_power': {'assessment': 'strong', 'source': 'computed', 'evidence_ids': ['fmp:as_reported_income_annual']}}
        fc = forecast.build(fb, an, card, qa)
        self.assertEqual(fc['anchors']['route']['moat'], 'strong')
        self.assertIn('computed proxy', fc['anchors']['route']['moat_basis'])
        self.assertIn('moat_computed_proxy', {w['code'] for w in fc['warnings']})
        del card['dimensions']['pricing_power']
        fc2 = forecast.build(fb, an, card, qa)
        row = next(a for a in fc2['assumptions'] if a['id'] == 'moat_basis')
        self.assertEqual(row['values']['moat'], 'unknown')
        self.assertGreater(row['values']['margin_competed_away'], .02)                # quantified, not silent
        self.assertIn('moat_basis', fc2['spec']['underwriting']['scenario_reviews']['base']['competitive_response']['assumption_ids'])
        self.assertLess(value_company(build_case(fc2['spec']))['annual_values'][0]['base'],
                        value_company(build_case(fc['spec']))['annual_values'][0]['base'])

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


if __name__ == '__main__':
    unittest.main()
