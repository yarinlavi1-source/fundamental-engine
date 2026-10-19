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
        # current fiscal year revenue = FY2025 grown at the trailing anchor; stub runs at the late-year rate
        cur = fc['anchors']['current_fiscal_year']
        self.assertAlmostEqual(spec['last_full_year_revenue'], cur['revenue'], places=0)
        self.assertGreater(cur['revenue'], 23769e6)
        self.assertGreater(spec['stub_annual_revenue_rate'], spec['last_full_year_revenue'])
        self.assertEqual(spec['opening']['as_of'], spec['as_of'])

    def test_margin_identity_matches_driver_arithmetic(self):
        # cash_cost_ratio = 1 - (operating margin + sbc_pct + depreciation_pct): executed EBIT margin equals the path
        for res in self.val['scenarios']:
            path = self.fc['paths'][res['name']]
            for row, p in zip(res['forecast'], path):
                self.assertAlmostEqual(row['ebit'] / row['revenue'], p['operating_margin'], places=6)
            t = scen(self.fc['spec'], res['name'])['terminal']
            self.assertAlmostEqual(t['operating_margin'], res['forecast'][-1]['ebit'] / res['forecast'][-1]['revenue'], places=6)
        for tr in self.val['underwriting_audit']['terminal_transitions']:
            self.assertFalse(tr['growth_cliff'] or tr['margin_jump'], tr)

    def test_scenarios_differ_by_business_outcomes(self):
        spec, fc = self.fc['spec'], self.fc
        bear, base, bull = (scen(spec, s) for s in SCEN)
        self.assertTrue(all(a < b < c for a, b, c in zip(bear['growth'], base['growth'], bull['growth'])))
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
        # the solved growth shift reproduces the quote when applied to the spec
        spec = deepcopy(self.fc['spec'])
        shift = rows['growth']['required'] - rows['growth']['anchor']
        scen(spec, 'base')['growth'] = [g + shift for g in scen(spec, 'base')['growth']]
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
            self.assertGreater(sum(scen(spec, s)['debt_draw']), 0)
            self.assertIn(f'financing_{s}', {a['id'] for a in spec['assumptions']})
            self.assertEqual(next(r for r in val['scenarios'] if r['name'] == s)['status'], 'conditional')
            self.assertTrue(any('financing is assumed' in i for i in next(r for r in val['scenarios'] if r['name'] == s)['issues']))
        issues = {m['id']: m for m in spec['underwriting']['material_issues']}
        self.assertEqual(issues[f'assumed_financing_{funded[0]}']['status'], 'unresolved')
        self.assertIn('financing_assumed', {w['code'] for w in fc['warnings']})
        # payout only after free cash flow turns positive: never distribute while borrowing
        for s in funded:
            sc = scen(spec, s)
            last_draw = max(i for i, x in enumerate(sc['debt_draw']) if x)
            self.assertEqual(sum(sc['payout_fraction'][:last_draw + 1]), 0)
        # disabling assumed financing leaves the plan unfunded and value_company withholds values
        fc2 = forecast.build(fb, an, card, qa, {'financing': 'none'})
        self.assertEqual(value_company(build_case(fc2['spec']))['status'], 'funding_blocked')
        self.assertIn('funding_blocked', {w['code'] for w in fc2['warnings']})

    def test_deep_burner_is_withheld_not_silently_funded(self):
        fb, an, card, qa = grower(margin_scale=6., cash=40e6)
        card['stage']['value'] = 'early'
        card['valuation']['method'] = 'scenario_survival_fcff'
        fc = forecast.build(fb, an, card, qa)
        blocked = [s for s in SCEN if fc['funding'][s]['status'] == 'unfunded']
        self.assertTrue(blocked)
        for s in blocked:
            self.assertEqual(sum(scen(fc['spec'], s)['debt_draw']), 0)
            self.assertIn('did not converge', fc['funding'][s]['reverted'])
        val = value_company(build_case(fc['spec']))
        self.assertEqual(val['status'], 'funding_blocked')
        self.assertTrue({'funding_blocked', 'survival_model'} <= {w['code'] for w in fc['warnings']})

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


if __name__ == '__main__':
    unittest.main()
