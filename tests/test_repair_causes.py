"""The engine-shaped causes of the 26.10.2026 repair passes, on SYNTHETIC fixtures shaped like the live cases (invented numbers,
not market data):

  CRDO  the fiscal year in progress extrapolated from one quarter's year-over-year growth, although the company had guided the
        next quarter (forecast._quarter_guide); this guide alone leaves the walk-forward, which holds no guidance rows, unchanged
  APH   bought revenue charged again as growth reinvestment in the stub (forecast._acquired_step: it also reads the saved filings'
        pro forma revenue, so it moves walk-forward values of acquirers; docs/CHANGELOG.md has the A/B), and a small stub draw
        against large reported free cash flow read as a funding condition (forecast._stub_draw_immaterial); a cash-short plan
        still flags
  NVDA  a cross-source label difference blocking the verdict, resolved only by an evidenced analyst input (dataqa resolutions)
"""
import copy
import json
import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
from fundamental_engine import dataqa, decision, engine_backtest, facts, forecast, review  # noqa: E402
from fundamental_engine.drivers import build_case  # noqa: E402
from fundamental_engine.evidence_pack import to_inputs  # noqa: E402
from test_forecast import acquirer, grower, make_an, qa_for, scen, synth  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CRDO_PRIOR = (223.074e6, 268.027e6, 407.012e6, 437.003e6)      # shaped like Credo's fiscal 2026 quarters


def quarterly(prior=CRDO_PRIOR, reported=(479.003e6,), guidance=(), as_of=None):
    """SYNTHETIC grower on a calendar fiscal year: the four quarters of 2025, the reported quarters of 2026 and optional dated
    revenue guidance rows (period, low, high, issued_at)."""
    fy = sum(prior)
    annual = {'2023-12-31': 0.4 * fy, '2024-12-31': 0.6 * fy, '2025-12-31': fy}
    annual = {e: {'revenue': r, 'operating_income': r * .25, 'sbc': r * .03, 'd_and_a': r * .03, 'capex': r * .04, 'cfo': r * .28,
                  'pretax_income': r * .25, 'income_tax': r * .25 * .21, 'interest_expense': 1e6} for e, r in annual.items()}
    ends = ['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30']
    q = list(zip(ends, list(prior) + list(reported)))
    last = q[-1][0]
    as_of = as_of or (date.fromisoformat(last) + timedelta(days=50)).isoformat()
    fb = synth(as_of, annual, {last: {'cash': 900e6, 'shares_outstanding': 180e6, 'receivables': 300e6, 'payables': 100e6},
                               '2022-12-31': {'receivables': 100e6, 'payables': 40e6}}, 60.0, kind='filing_derived')
    for end, v in q:
        for m, x in (('revenue', v), ('operating_income', v * .25)):
            facts.add_fact(fb, m, 'Q@' + end, end, x, 'syn:fs', 'as_reported', (date.fromisoformat(end) + timedelta(days=35)).isoformat())
    fb['guidance'] = [{'metric': 'revenue', 'period': p, 'low': lo, 'high': hi, 'issued_at': iss, 'source_ids': ['syn:fs']}
                      for p, lo, hi, iss in guidance]
    ttm = sum(v for _, v in q[-4:])
    an = make_an(fb, revenue_growth_last=.67, revenue_cagr_3y=.5, growth_volatility=.2, roic=.25, sales_to_capital_incremental_3y=2.0)
    an['ttm'] = {'revenue': ttm, 'operating_income': ttm * .25, 'period_end': last, 'method': 'quarters'}
    card = {'business_model': {'primary': 'hardware_manufacturing'}, 'stage': {'value': 'growth'}, 'funding': {'value': 'self_funded'},
            'cyclical': {'value': False}, 'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'},
            'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(180e6, 900e6, 0., last)


def cur(fc):
    return fc['anchors']['current_fiscal_year']


class QuarterlyGuideTests(unittest.TestCase):
    """B1: year to date + the guided next quarter + the quarters left, instead of year-to-date growth x the prior months."""

    def test_crdo_shaped_year_uses_the_guided_quarter_and_its_sequential_step(self):
        old = forecast.build(*quarterly())
        self.assertAlmostEqual(cur(old)['revenue'], 479.003e6 + (sum(CRDO_PRIOR) - 223.074e6) * 479.003e6 / 223.074e6, delta=1)
        fc = forecast.build(*quarterly(guidance=[('Q2 FY2026', 525e6, 535e6, '2026-05-05')]))
        step = 530e6 / 479.003e6 - 1
        want = 479.003e6 + 530e6 + 530e6 * (1 + step) + 530e6 * (1 + step) ** 2
        c = cur(fc)
        self.assertAlmostEqual(c['revenue'], want, delta=1)
        self.assertAlmostEqual(c['revenue'] / 1e6, 2244.3, delta=.1)                 # not the 2,866.9M extrapolation
        self.assertEqual(c['kind'], 'model')                                         # two quarters are still modelled
        g = c['quarter_guide']
        self.assertEqual((g['rule'], g['quarter'], g['quarters_reported']), ('sequential', 'Q2 FY2026', 1))
        self.assertAlmostEqual(g['sequential_growth'], step)
        self.assertIn("guided midpoint for Q2 FY2026", c['basis'])
        self.assertIn("sequential growth 10.6%", c['basis'])
        row = next(a for a in fc['assumptions'] if a['id'] == 'revenue_current_fy')
        self.assertIn('syn:fs', row['source_ids'])

    def test_a_seasonal_year_applies_the_guided_year_over_year_growth_to_last_years_quarters(self):
        prior = (100e6, 105e6, 95e6, 140e6)                                            # the year before dips in Q3: seasonal
        fc = forecast.build(*quarterly(prior=prior, reported=(102e6, 107e6), guidance=[('Q3 FY2026', 98e6, 100e6, '2026-08-01')]))
        g = cur(fc)['quarter_guide']
        self.assertEqual(g['rule'], 'seasonal')
        self.assertAlmostEqual(cur(fc)['revenue'], 209e6 + 99e6 + 140e6 * 99 / 95, delta=1)

    def test_a_calendar_quarter_label_is_read_only_for_a_calendar_fiscal_year(self):
        fc = forecast.build(*quarterly(guidance=[('Q2 2026', 525e6, 535e6, '2026-05-05')]))
        self.assertEqual(cur(fc)['quarter_guide']['quarter'], 'Q2 2026')
        fb, an, card, qa = quarterly(guidance=[('Q2 2026', 525e6, 535e6, '2026-05-05')])
        fb['company']['fiscal_year_end'] = '06-30'                       # 'Q2 2026' is ambiguous for a June fiscal year
        self.assertEqual(forecast._quarter_revenue_guidance(fb), {})

    def test_a_guided_decline_is_held_flat_not_compounded(self):
        fc = forecast.build(*quarterly(guidance=[('Q2 FY2026', 420e6, 430e6, '2026-05-05')]))
        self.assertEqual(cur(fc)['quarter_guide']['rule'], 'flat')
        self.assertAlmostEqual(cur(fc)['revenue'], 479.003e6 + 3 * 425e6, delta=1)

    def test_a_guided_last_quarter_closes_the_year_as_guidance(self):
        fc = forecast.build(*quarterly(reported=(479.003e6, 530e6, 590e6), guidance=[('FY2026 Q4', 640e6, 660e6, '2026-11-01')]))
        c = cur(fc)
        self.assertEqual((c['kind'], c['quarter_guide']['rule']), ('guidance', 'none'))
        self.assertAlmostEqual(c['revenue'], 479.003e6 + 530e6 + 590e6 + 650e6, delta=1)

    def test_fiscal_year_guidance_and_an_analyst_input_still_come_first(self):
        g = [('Q2 FY2026', 525e6, 535e6, '2026-05-05'), ('FY2026', 2.40e9, 2.40e9, '2026-05-05')]
        fc = forecast.build(*quarterly(guidance=g))
        self.assertEqual((cur(fc)['kind'], cur(fc)['revenue']), ('guidance', 2.40e9))
        self.assertNotIn('quarter_guide', cur(fc))
        fb, an, card, qa = quarterly(guidance=g[:1])
        cyr = {'value': 2.45e9, 'fiscal_year_end': '2026-12-31', 'basis': 'analyst', 'evidence_ids': ['syn:fs'], 'source_tier': 'analyst'}
        fc = forecast.build(fb, an, card, qa, {'current_year_revenue': cyr})
        self.assertEqual((fc['status'], cur(fc)['revenue'], cur(fc)['kind']), ('built', 2.45e9, 'model'))
        g = next(r for r in fc['driver_sources'] if r['lever'] == 'growth_anchor' and r['scenario'] == 'base')
        self.assertEqual(g['tier'], 'analyst')                                    # its own tier, never relabelled

    def test_walk_forward_shape_is_unchanged_without_a_qualifying_guide(self):
        base = forecast.build(*quarterly())
        with mock.patch.object(forecast, '_quarter_revenue_guidance', lambda fb: {}):
            off = forecast.build(*quarterly(guidance=[('Q2 FY2026', 525e6, 535e6, '2026-05-05')]))
        self.assertEqual(off['spec_sha256'], base['spec_sha256'])
        for rows in ([('Q2 FY2026', 525e6, 535e6, None)],                 # undated
                     [('Q2 FY2026', 525e6, 535e6, '2026-06-01')],         # issued after as_of
                     [('Q3 FY2026', 600e6, 610e6, '2026-05-05')],         # not the next unreported quarter
                     [('Q2 FY2025', 260e6, 270e6, '2025-05-05')],         # a quarter of the last fiscal year
                     [('H2 FY2026', 1.2e9, 1.3e9, '2026-05-05')]):        # not a quarter label
            fc = forecast.build(*quarterly(guidance=rows))
            self.assertEqual(fc['spec_sha256'], base['spec_sha256'], rows)
            self.assertNotIn('quarter_guide', cur(fc))

    def test_walk_forward_row_from_saved_filings_is_identical(self):
        folder = str(ROOT / 'tests' / 'fixtures' / 'demo_company')
        row = engine_backtest.company_row(folder, '2026-10-20', 42.0, .043, .0423)
        with mock.patch.object(forecast, '_quarter_guide', lambda *a: None):
            same = engine_backtest.company_row(folder, '2026-10-20', 42.0, .043, .0423)
        self.assertIsNotNone(row)
        self.assertEqual(row, same)


def aph_shaped(cfo_q=(.8e9, .85e9, 1.0e9, 1.1e9), margin=.3, cash=1.5e9):
    """SYNTHETIC: a fast-growing manufacturer whose minimum cash (3 months of cash costs) is all of today's cash, with a balance
    sheet four months before as_of and quarterly cash flows; margin .3 draws only in the stub, .2 for several years."""
    rev = {2022: 10e9, 2023: 11e9, 2024: 12e9, 2025: 14e9}
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * margin, 'sbc': r * .005, 'd_and_a': r * .04, 'capex': r * .04,
                             'cfo': r * .22, 'pretax_income': r * margin, 'income_tax': r * margin * .21, 'interest_expense': .1e9}
              for y, r in rev.items()}
    inst = {'2026-06-30': {'cash': cash, 'shares_outstanding': 1e9, 'receivables': 3e9, 'payables': 1.5e9, 'long_term_debt': 2e9},
            '2022-12-31': {'receivables': 2e9, 'payables': 1e9}}
    fb = synth('2026-10-26', annual, inst, 50.0, kind='filing_derived')
    q = (('2025-03-31', 3.2e9), ('2025-06-30', 3.4e9), ('2025-09-30', 3.6e9), ('2025-12-31', 3.8e9), ('2026-03-31', 4.5e9), ('2026-06-30', 5e9))
    avail = lambda end: (date.fromisoformat(end) + timedelta(days=35)).isoformat()
    for end, v in q:
        for m, x in (('revenue', v), ('operating_income', v * margin)):
            facts.add_fact(fb, m, 'Q@' + end, end, x, 'syn:fs', 'as_reported', avail(end))
    for (end, _), c in zip(q[2:], cfo_q):
        facts.add_fact(fb, 'cfo', 'Q@' + end, end, c, 'syn:fs', 'as_reported', avail(end))
        facts.add_fact(fb, 'capex', 'Q@' + end, end, .15e9, 'syn:fs', 'as_reported', avail(end))
    an = make_an(fb, revenue_growth_last=14 / 12 - 1, revenue_cagr_3y=(14 / 10) ** (1 / 3) - 1, growth_volatility=.05, roic=.2,
                 sales_to_capital_incremental_3y=1.0)
    an['ttm'] = {'revenue': 17.9e9, 'operating_income': 17.9e9 * margin, 'period_end': '2026-06-30', 'method': 'quarters'}
    card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'maturing'}, 'funding': {'value': 'self_funded'},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(1e9, cash, 2e9, '2026-06-30')


def gate(fc):
    """decision._funding on this forecast's executed case: (funding gates, immaterial notes)."""
    g = {'funding': [], 'funding_conditional': []}
    decision._funding(g, build_case(fc['spec']), fc, fc['spec']['underwriting']['material_issues'])
    return g['funding'], g.get('funding_immaterial') or []


class AcquiredStepTests(unittest.TestCase):
    """B2: revenue an acquisition brought into the current year's step is not charged again as growth reinvestment."""

    def _gc0(self, fc):
        return {s: fc['paths'][s][0]['growth_capex_pct'] for s in ('bear', 'base', 'bull')}

    def test_pro_forma_bought_step_leaves_the_stub_reinvestment(self):
        fc = forecast.build(*acquirer())
        with mock.patch.object(forecast, '_acquired_step', lambda *a: None):
            old = forecast.build(*acquirer())
        stub = fc['anchors']['stub']
        acq = stub['acquired_step_excluded']
        r_fy = cur(fc)['revenue']
        d_rev0 = r_fy - 125e9
        organic = 80 / 78 - 1
        self.assertAlmostEqual(acq['amount'], d_rev0 - 125e9 * organic, delta=1e3)     # last-year closing, no FY pro forma: estimate
        self.assertAlmostEqual(stub['revenue_change_reinvested'], 125e9 * organic, delta=1e3)
        rule = scen(fc['spec'], 'base')['reinvestment_rule']
        self.assertAlmostEqual(rule['stub_revenue_change'], 125e9 * organic, delta=1)
        new, was = self._gc0(fc), self._gc0(old)
        for s in new:
            self.assertAlmostEqual(new[s] / was[s], (d_rev0 - acq['amount']) / d_rev0, places=4)
        self.assertIn('acquired_step_not_reinvested', {w['code'] for w in fc['warnings']})
        row = next(a for a in fc['assumptions'] if a['id'] == 'reinvestment')
        self.assertIn('excludes', row['rationale'])

    def test_a_closing_in_the_year_in_progress_excludes_the_pre_deal_size_only(self):
        fb, an, card, qa = acquirer()
        an['acquisition_years'] = [{'period_end': '2026-06-30', 'fiscal_period': 'YTD@2026-06-30', 'current_year_to_date': True}]
        fc = forecast.build(fb, an, card, qa)
        acq = fc['anchors']['stub']['acquired_step_excluded']
        rem = (date(2026, 12, 31) - date(2026, 6, 30)).days / 365.25
        rate = (78e9 - 50e9) / ((date(2026, 6, 30) - date(2025, 12, 31)).days / 365.25)
        self.assertEqual(acq['source'], 'pro_forma')
        self.assertAlmostEqual(acq['amount'], min(28e9 + rate * rem, acq['cap_above_organic']), delta=1e3)

    def test_an_evidenced_analyst_input_names_the_acquisition(self):
        fb, an, card, qa = industrial_like()
        ar = {'value': 50e6, 'fiscal_year_end': '2026-12-31', 'acquisition': 'TargetCo', 'closed': '2025-12-01',
              'reason': 'the 10-K acquisition note: TargetCo revenue of about 50M a year', 'evidence_ids': ['syn:fs']}
        fc = forecast.build(fb, an, card, qa, {'acquired_revenue': ar})
        acq = fc['anchors']['stub']['acquired_step_excluded']
        self.assertEqual((acq['source'], acq['amount'], acq['acquisition']), ('input', 50e6, 'TargetCo'))
        for bad, msg in (({'evidence_ids': []}, 'evidence_ids'), ({'closed': '2027-01-01'}, 'look-ahead'),
                         ({'acquisition': ' '}, 'name the acquisition'), ({'extra': 1}, 'an object with')):
            with self.assertRaises(ValueError) as e:
                forecast.build(fb, an, card, qa, {'acquired_revenue': {**ar, **bad}})
            self.assertIn(msg, str(e.exception))
        out = forecast.build(fb, an, card, qa, {'acquired_revenue': {**ar, 'fiscal_year_end': '2027-12-31'}})
        self.assertEqual(out['status'], 'method_not_supported_by_forecast')                   # a wrong year is refused, not applied
        self.assertIn('fiscal year in progress', out['reason'])
        late = forecast.build(fb, an, card, qa, {'acquired_revenue': {**ar, 'closed': '2026-02-01'}})   # after the balance sheet
        self.assertNotIn('acquired_step_excluded', late['anchors']['stub'])
        self.assertEqual({w['code']: w['severity'] for w in late['warnings']}['acquired_revenue_after_statement'], 'high')

    def test_an_oversized_input_is_capped_at_the_step_above_organic_growth(self):
        """An analyst input above the step above the filings' organic growth would stop charging organic growth too (and with
        it a stub draw and its funding gate): it is capped like the filings' reading and flagged for the reviewer."""
        fb, an, card, qa = acquirer()
        filings = forecast.build(fb, an, card, qa)['anchors']['stub']
        cap = filings['acquired_step_excluded']['cap_above_organic']
        ar = {'value': 100e9, 'fiscal_year_end': '2026-12-31', 'acquisition': 'TargetCo', 'closed': '2025-09-01',
              'reason': 'the 10-K acquisition note', 'evidence_ids': ['syn:fs']}
        fc = forecast.build(*acquirer(), {'acquired_revenue': ar})
        stub = fc['anchors']['stub']
        acq = stub['acquired_step_excluded']
        self.assertEqual(acq['source'], 'input')
        self.assertAlmostEqual(acq['amount'], cap, delta=1)
        self.assertAlmostEqual(stub['revenue_change_reinvested'], 125e9 * (80 / 78 - 1), delta=1e3)    # organic step still charged
        self.assertGreater(stub['revenue_change_reinvested'], 0)
        self.assertIn('capped at', acq['basis'])
        w = {x['code']: x for x in fc['warnings']}
        self.assertEqual(w['acquired_revenue_capped']['severity'], 'medium')
        self.assertIn('organic growth 2.6%', w['acquired_revenue_capped']['message'])
        small = forecast.build(*acquirer(), {'acquired_revenue': {**ar, 'value': 20e9}})      # below the cap: taken as given
        self.assertEqual(small['anchors']['stub']['acquired_step_excluded']['amount'], 20e9)
        self.assertNotIn('acquired_revenue_capped', {x['code'] for x in small['warnings']})
        whole = forecast.build(*industrial_like(), {'acquired_revenue': {**ar, 'value': 1e15}})  # no organic reading: the step
        acq = whole['anchors']['stub']['acquired_step_excluded']
        self.assertNotIn('cap_above_organic', acq)
        self.assertEqual(whole['anchors']['stub']['revenue_change_reinvested'], 0.)
        self.assertIn('whole revenue step', {x['code']: x for x in whole['warnings']}['acquired_revenue_capped']['message'])

    def test_no_acquisition_keeps_every_fixture_identical(self):
        for make in (grower, aph_shaped, quarterly):
            a = forecast.build(*make())
            with mock.patch.object(forecast, '_acquired_step', lambda *x: None):
                b = forecast.build(*make())
            self.assertEqual(a['spec_sha256'], b['spec_sha256'])


def industrial_like():
    """SYNTHETIC steady industrial with a year to date (no acquisition in its numbers)."""
    rev = [400e6, 460e6, 520e6, 580e6]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .2, 'sbc': r * .05, 'd_and_a': r * .04, 'capex': r * .05,
                             'cfo': r * .27, 'pretax_income': r * .2, 'income_tax': r * .2 * .21, 'interest_expense': 4e6}
              for y, r in zip(range(2022, 2026), rev)}
    fb = synth('2026-08-20', annual, {'2025-12-31': {'cash': 150e6, 'shares_outstanding': 50e6, 'receivables': 80e6, 'payables': 30e6,
                                                     'long_term_debt': 100e6}}, 60.0, kind='filing_derived')
    for end, v in (('2025-03-31', 140e6), ('2025-06-30', 145e6), ('2026-03-31', 175e6), ('2026-06-30', 180e6)):
        facts.add_fact(fb, 'revenue', 'Q@' + end, end, v, 'syn:fs', 'as_reported', (date.fromisoformat(end) + timedelta(days=35)).isoformat())
    an = make_an(fb, revenue_growth_last=580 / 520 - 1, revenue_cagr_3y=(580 / 400) ** (1 / 3) - 1, growth_volatility=.01, roic=.25,
                 sales_to_capital_incremental_3y=2.0)
    card = {'business_model': {'primary': 'industrial'}, 'stage': {'value': 'maturing'}, 'funding': {'value': 'self_funded'},
            'valuation': {'method': 'fcff_annual_path', 'archetype': 'industrial'}, 'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(50e6, 150e6, 100e6, '2025-12-31')


class StubDrawTests(unittest.TestCase):
    """B2: a stub-only debt draw covered by reported free cash flow since the balance sheet is not a funding condition; a real
    funding need still is."""

    def test_stub_only_draw_covered_by_reported_free_cash_flow_is_immaterial(self):
        fc = forecast.build(*aph_shaped())
        cover = (.8e9 + .85e9 + 1.0e9 + 1.1e9 - 4 * .15e9) * (date(2026, 10, 26) - date(2026, 6, 30)).days / 365.25
        for s in ('bear', 'base', 'bull'):
            f = fc['funding'][s]
            self.assertEqual(f['status'], 'assumed_external_financing')               # the draw stays in the model, priced
            self.assertEqual([d['period_end'] for d in f['draws']], ['2026-12-31'])
            self.assertAlmostEqual(f['stub_draw_immaterial']['cover'], cover, delta=1)
            self.assertLessEqual(f['debt_assumed'], cover)
        self.assertEqual(fc['spec']['underwriting']['material_issues'], [])
        self.assertNotIn('needs_external_funding', [a['factor'] for a in fc['cost_of_equity']['components']['adjustments']])
        codes = {w['code']: w['severity'] for w in fc['warnings']}
        self.assertEqual(codes.get('stub_draw_immaterial'), 'low')
        self.assertNotIn('financing_assumed', codes)
        self.assertEqual(codes.get('minimum_cash_is_all_cash'), 'low')
        row = next(a for a in fc['assumptions'] if a['id'] == 'financing_base')
        self.assertIn('IMMATERIAL', row['rationale'])
        gates, notes = gate(fc)
        self.assertEqual(gates, [])
        self.assertEqual({n['scenario'] for n in notes}, {'bear', 'base'})

    def test_negative_reported_free_cash_flow_keeps_the_funding_gate(self):
        fc = forecast.build(*aph_shaped(cfo_q=(-.1e9,) * 4))
        self.assertFalse(any(fc['funding'][s].get('stub_draw_immaterial') for s in ('bear', 'base', 'bull')))
        self.assertEqual({m['id'] for m in fc['spec']['underwriting']['material_issues']},
                         {'assumed_financing_bear', 'assumed_financing_base', 'assumed_financing_bull'})
        self.assertIn('needs_external_funding', [a['factor'] for a in fc['cost_of_equity']['components']['adjustments']])
        gates, notes = gate(fc)
        self.assertEqual(len(gates), 2)
        self.assertEqual(notes, [])

    def test_draws_past_the_stub_and_equity_raises_keep_the_funding_gate(self):
        fc = forecast.build(*aph_shaped(margin=.2))                                  # base needs money for several years
        base = fc['funding']['base']
        self.assertGreater(len(base['draws']), 1)
        self.assertNotIn('stub_draw_immaterial', base)
        self.assertIn('assumed_financing_base', {m['id'] for m in fc['spec']['underwriting']['material_issues']})
        self.assertTrue(any('base' in x for x in gate(fc)[0]))
        g = forecast.build(*grower(margin_scale=2.))                                 # the cash-short loss maker
        self.assertFalse(any(g['funding'][s].get('stub_draw_immaterial') for s in ('bear', 'base', 'bull')))
        self.assertTrue(gate(g)[0])

    def test_an_evidenced_minimum_cash_override_silences_the_all_cash_note(self):
        fb, an, card, qa = aph_shaped()
        ov = [{'path': 'minimum_cash_months', 'scenario': 'all', 'value': .5, 'reason': 'the cash the company holds against its costs',
               'evidence_ids': ['syn:fs'], 'source_tier': 'own_history'}]
        fc = forecast.build(fb, an, card, qa, {'overrides': ov})
        self.assertNotIn('minimum_cash_is_all_cash', {w['code'] for w in fc['warnings']})


def conflict_fb(av_sti=70e9):
    """SYNTHETIC balance sheet: the SEC tag holds debt securities (30) while the vendor's short-term investments add equity
    securities (70), shaped like NVIDIA's 10-Q of 2026-07-26."""
    fb = facts.new({'ticker': 'SYN', 'name': 'Synthetic test company', 'currency': 'USD', 'fiscal_year_end': '12-31'}, '2026-10-26')
    facts.add_source(fb, 'sec:companyfacts', 'sec', 'companyfacts', 'filing_derived', '2026-10-26T00:00:00Z', note='TEST DATA ONLY')
    facts.add_source(fb, 'av:balance', 'alpha_vantage', 'balance', 'vendor_normalized', '2026-10-26T00:00:00Z')
    facts.add_source(fb, 'web:q10', 'issuer', 'filing', 'issuer_document', '2026-10-26')
    fb['sources']['web:q10']['published_at'] = '2026-08-26'
    facts.add_source(fb, 'web:wire', 'press', 'news', 'secondary', '2026-10-26')
    for m, v in (('cash', 20e9), ('short_term_investments', 30e9), ('current_assets', 120e9), ('total_assets', 200e9)):
        facts.add_fact(fb, m, 'I@2026-06-30', '2026-06-30', v, 'sec:companyfacts', 'as_reported', '2026-08-26')
    for m, v in (('cash', 20e9), ('short_term_investments', av_sti)):
        facts.add_fact(fb, m, 'I@2026-06-30', '2026-06-30', v, 'av:balance', 'normalized', '2026-08-27')
    facts.add_qualitative(fb, 'q_note8', 'investments', 'Note 8: debt securities 30B, equity securities 40B (TEST ITEM)', 'reported',
                          ['web:q10'], '2026-08-26')
    return fb


LABEL = {'field': 'cash_and_liquid_investments', 'metric': 'short_term_investments', 'resolution': 'label_difference',
         'source_id': 'av:balance', 'reason': 'the vendor adds equity securities to the debt securities the SEC tag holds (Note 8)',
         'evidence_ids': ['q_note8']}


class DataResolutionTests(unittest.TestCase):
    """B3: a cross-source label conflict is resolved only by an evidenced, recorded analyst input; without one the block stays."""

    def test_without_a_resolution_the_conflict_blocks(self):
        q = dataqa.validate(conflict_fb())
        self.assertEqual(q['fields']['cash_and_liquid_investments']['status'], 'conflict')
        self.assertIn('cash_and_liquid_investments', q['blocking'])
        self.assertNotIn('resolutions', q)

    def test_a_label_difference_with_filing_evidence_unblocks_and_is_recorded(self):
        fb = conflict_fb()
        q = dataqa.validate(fb, None, {'resolutions': [LABEL]})
        f = q['fields']['cash_and_liquid_investments']
        self.assertNotIn('cash_and_liquid_investments', q['blocking'])
        self.assertIn('cash_and_liquid_investments', q['limited'])                   # it rests on a resolution: limited
        self.assertEqual(f['value'], 50e9)                                           # the chosen value never moves
        self.assertIn('analyst resolution (label difference)', f['note'])
        r = q['resolutions'][0]
        self.assertEqual(r['status'], 'applied')
        self.assertEqual((r['applied'][0]['other_source'], r['applied'][0]['other_value']), ('av:balance', 70e9))
        self.assertIn('analyst_resolution', {c['code'] for c in q['checks']})
        self.assertEqual(f['analyst_resolutions'][0]['evidence_ids'], ['q_note8'])
        base = dataqa.validate(fb)
        self.assertEqual({k: v.get('value') for k, v in base['resolved'].items() if isinstance(v, dict)},
                         {k: v.get('value') for k, v in q['resolved'].items() if isinstance(v, dict)})

    def test_without_filing_evidence_or_a_match_the_block_stays(self):
        fb = conflict_fb()
        for bad, status in (({'evidence_ids': ['web:wire']}, 'rejected'), ({'evidence_ids': ['av:balance']}, 'rejected'),
                            ({'reason': ''}, 'rejected'), ({'source_id': 'nope'}, 'rejected'), ({'metric': 'cash'}, 'unused'),
                            ({'resolution': 'override'}, 'rejected'), ({'field': 'debt'}, 'rejected')):
            q = dataqa.validate(fb, None, {'resolutions': [{**LABEL, **bad}]})
            self.assertIn('cash_and_liquid_investments', q['blocking'], bad)
            self.assertEqual(q['resolutions'][0]['status'], status, bad)
            self.assertTrue(any(c['code'] == 'analyst_resolution' and c['status'] == status for c in q['checks']))
        with self.assertRaises(ValueError):
            dataqa.validate(fb, None, {'resolutions': {'not': 'a list'}})

    def test_use_source_chooses_the_confirmed_value(self):
        res = {**LABEL, 'resolution': 'use_source', 'period_end': '2026-06-30', 'evidence_ids': ['web:q10'],
               'reason': 'the 10-Q balance sheet line includes the equity securities'}
        q = dataqa.validate(conflict_fb(), None, {'resolutions': [res]})
        self.assertNotIn('cash_and_liquid_investments', q['blocking'])
        self.assertEqual(q['resolved']['short_term_investments']['value'], 70e9)
        self.assertEqual(q['resolutions'][0]['status'], 'applied')
        no_date = dataqa.validate(conflict_fb(), None, {'resolutions': [{k: v for k, v in res.items() if k != 'period_end'}]})
        self.assertEqual(no_date['resolutions'][0]['status'], 'rejected')

    def test_use_source_without_a_disagreement_moves_nothing(self):
        """Values within tolerance: the resolution resolves nothing, so it never reorders the candidates; the chosen value, its
        source and the field status stay those of the run without it, and 'unused ... nothing changed' is true."""
        res = {**LABEL, 'resolution': 'use_source', 'period_end': '2026-06-30', 'evidence_ids': ['web:q10'],
               'reason': 'the 10-Q balance sheet line'}
        fb = conflict_fb(av_sti=30.2e9)
        q, base = dataqa.validate(fb, None, {'resolutions': [res]}), dataqa.validate(fb)
        self.assertEqual(q['resolutions'][0]['status'], 'unused')
        self.assertEqual(q['resolved']['short_term_investments'], base['resolved']['short_term_investments'])
        f, f0 = q['fields']['cash_and_liquid_investments'], base['fields']['cash_and_liquid_investments']
        self.assertEqual((f['value'], f['status'], f['sources']), (f0['value'], f0['status'], f0['sources']))
        self.assertEqual(f['value'], 50e9)
        self.assertTrue(any(c['code'] == 'analyst_resolution' and c['status'] == 'unused' and 'nothing changed' in c['message']
                            for c in q['checks']))
        both = dataqa.validate(conflict_fb(), None, {'resolutions': [res, {**res, 'metric': 'cash', 'reason': 'same cash'}]})
        self.assertEqual([r['status'] for r in both['resolutions']], ['applied', 'unused'])     # the other one still applies
        self.assertEqual(both['resolved']['short_term_investments']['value'], 70e9)

    def test_the_review_signs_the_resolution(self):
        val = {}
        plain = review.inputs_digest(val, {'forecast': {}})
        self.assertEqual(plain, review.inputs_digest(val, {'forecast': {}, 'other': 1}))
        self.assertNotEqual(plain, review.inputs_digest(val, {'forecast': {}, 'dataqa_resolutions': [LABEL]}))

    def test_the_pack_carries_it_with_filing_evidence_only(self):
        p = json.loads((ROOT / 'examples' / 'evidence_pack_demo.json').read_text(encoding='utf-8'))
        p['dataqa_resolutions'] = [{**LABEL, 'evidence_ids': ['s_10q']}]
        out = to_inputs(copy.deepcopy(p))['dataqa_resolutions'][0]
        self.assertEqual((out['source_id'], out['evidence_ids']), ('av:balance', ['web:s_10q']))
        p['dataqa_resolutions'] = [{**LABEL, 'evidence_ids': ['s_pr']}]
        with self.assertRaises(ValueError) as e:
            to_inputs(copy.deepcopy(p))
        self.assertIn('filing evidence required', str(e.exception))
        p['dataqa_resolutions'] = [{**LABEL, 'source_id': 's_av', 'evidence_ids': ['s_10q'], 'resolution': 'use_source'}]
        with self.assertRaises(ValueError) as e:
            to_inputs(copy.deepcopy(p))
        self.assertIn('required for use_source', str(e.exception))

    def test_the_pack_carries_an_evidenced_acquisition(self):
        p = json.loads((ROOT / 'examples' / 'evidence_pack_demo.json').read_text(encoding='utf-8'))
        p.setdefault('forecast_options', {})['acquired_revenue'] = {'value': 5e6, 'acquisition': 'TargetCo', 'closed': '2026-03-01',
                                                                     'reason': 'the 10-Q acquisition note', 'evidence_ids': ['s_10q']}
        out = to_inputs(copy.deepcopy(p))['forecast']['acquired_revenue']
        self.assertEqual((out['fiscal_year_end'], out['evidence_ids']), ('2026-12-31', ['web:s_10q']))
        p['forecast_options']['acquired_revenue']['evidence_ids'] = ['s_wire']
        with self.assertRaises(ValueError) as e:
            to_inputs(copy.deepcopy(p))
        self.assertIn("company's own documents", str(e.exception))


if __name__ == '__main__':
    unittest.main()
