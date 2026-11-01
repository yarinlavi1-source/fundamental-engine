"""Engine bugs from the 01.11.2026 portfolio batch (0.14.2). The pipeline-level tests use the real Adobe fixture; every other
company below is SYNTHETIC test data shaped like the live case it names (invented numbers, not market data):

  NOW   one 5-for-1 split recorded twice (the issuer's XBRL tag and a market feed) multiplied into a factor of 25
  RDDT  DCF reinvestment / terminal ROIC on engine rules next to the evidenced company_path ROIC; vested options
  META  contracted forward-sale proceeds had no route ('Funding marked none but cash financing supplied')
  MSFT  finance leases deducted as debt while reinvestment leaves their right-of-use additions out
  NBIS  a build-phase D&A load from the last fiscal year held forever; value.json said ran=true without a value
  ASTS  a same-family conflict (companyfacts vs the 10-Q read directly) the analyst's filing-cited resolution could not settle
  APH   routed as infrastructure on the keyword 'cable'; interest on existing debt at the fiscal-year rate after a debt jump
"""
import json
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.dirname(__file__))
from fundamental_engine import analysis, company_path as C, dataqa, facts, forecast, model_card, pipeline, ticker_run  # noqa: E402
from fundamental_engine.drivers import build_case  # noqa: E402
from fundamental_engine.valuation import value_company  # noqa: E402
from test_company_path import ADBE, ECON, EV  # noqa: E402
from test_dataqa import M, checks, syn  # noqa: E402
from test_forecast import industrial, scen  # noqa: E402
from test_repair_causes import conflict_fb  # noqa: E402


def base_value(fc):
    return value_company(build_case(fc['spec']))['annual_values'][0]['base']


class SplitOnceTests(unittest.TestCase):
    """(1) NOW: a split recorded by two sources is one corporate action."""

    def split_fb(self, events):
        fb = syn(quarters=False)
        for f in fb['facts']:
            if f['metric'] == 'shares_outstanding' and f['period_end'] == '2025-12-31':
                f['value'] = 98.0 * 5 * M
        fb.setdefault('market', {})['splits'] = events
        return fb

    def test_two_records_of_one_split_give_factor_five_not_twenty_five(self):
        fb = self.split_fb([{'date': '2025-12-05', 'factor': 5.0, 'source_id': 'sec:cf'},
                            {'date': '2025-12-18', 'factor': 5.0, 'source_id': 'ib:px'}])
        qa = dataqa.validate(fb)
        sp = qa['splits'][0]
        self.assertEqual((sp['factor'], sp['decided_by']), (5.0, 'corporate_action'))
        ev = sp['evidence']['split_events']
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]['same_event'][0]['date'], '2025-12-18')
        steps, basis, _ = analysis._split_steps(fb, qa, [('2023-12-31', 100 * M), ('2024-12-31', 99 * M), ('2025-12-31', 490 * M)])
        prod = steps[0]['adjusted_ratio'] * steps[1]['adjusted_ratio']
        self.assertAlmostEqual(prod, .98, places=6)                          # -1%/yr, never -41%/yr
        self.assertEqual(steps[1]['factor'], 5.0)

    def test_issuer_tag_dated_by_its_quarter_context_is_still_one_event(self):
        # review of 27b2bf6: the issuer's tag moved to 2025-10-15 (a context up to ~90 days from the market date) brought x25 back
        for when in ('2025-10-15', '2025-10-02', '2025-11-01'):
            fb = self.split_fb([{'date': when, 'factor': 5.0, 'source_id': 'sec:cf'},
                                {'date': '2025-12-18', 'factor': 5.0, 'source_id': 'ib:px'}])
            sp = dataqa.validate(fb)['splits'][0]
            self.assertEqual(sp['factor'], 5.0, when)
            self.assertAlmostEqual(sp['residual_ratio'], 490 / 99 / 5, places=6)

    def test_a_split_leaving_a_residual_outside_the_band_is_not_decisive(self):
        fb = self.split_fb([{'date': '2025-12-05', 'factor': 2.0, 'source_id': 'sec:cf'}])   # 99M -> 490M is not a 2-for-1
        sp = dataqa.validate(fb)['splits']
        self.assertTrue(not sp or sp[0]['evidence']['tests'].get('corporate_action') != 'supports')

    def test_distinct_splits_and_unknown_factors(self):
        once = dataqa._same_split_once
        two = once([{'date': '2024-01-10', 'factor': 2.0}, {'date': '2024-09-10', 'factor': 2.0}])
        self.assertEqual(len(two), 1)                                        # one count step: one event, the other listed
        self.assertEqual(two[0]['same_event'][0]['date'], '2024-09-10')
        # ...counted twice only where the count ratio needs the product (two real 2-for-1 splits in one step)
        self.assertEqual(dataqa._split_factor_of(two, 4.1), (4.0, True, True))
        self.assertEqual(dataqa._split_factor_of(two, 2.05), (2.0, True, False))
        diff = once([{'date': '2024-01-10', 'factor': 2.0}, {'date': '2024-01-20', 'factor': 3.0}])
        self.assertEqual(len(diff), 2)                                       # different factors: not the same record
        item = once([{'date': '2025-12-01', 'factor': None, 'item': 'q1'}, {'date': '2025-12-18', 'factor': 5.0, 'source_id': 'ib'}])
        self.assertEqual((len(item), item[0]['factor']), (1, 5.0))           # an undated-factor item takes the feed's factor


def link_run(extra_forecast=None):
    inputs = {'company_path': deepcopy(ECON)}
    if extra_forecast is not None:
        inputs['forecast'] = extra_forecast
    return pipeline.run(deepcopy(ADBE), inputs)


class CapitalLinkTests(unittest.TestCase):
    """(2) one capital intensity per scenario: the company_path roic sets the DCF's terminal ROIC and reinvestment."""

    @classmethod
    def setUpClass(cls):
        cls.cf = link_run()
        cls.off = link_run({'capital_link': {}})
        cls.cases = {s['name']: s for s in cls.cf['val']['case']['scenarios']}

    def test_terminal_roic_and_reinvestment_follow_the_path(self):
        self.assertEqual(self.cf['stages']['forecast'], 'ok')
        for s in ('base', 'bear'):
            self.assertAlmostEqual(self.cases[s]['terminal']['roic'], ECON['scenarios'][s]['roic'], places=9)
            rule = scen(self.cf['fc']['spec'], s)['reinvestment_rule']['capital_per_revenue']
            self.assertAlmostEqual(min(rule), max(rule), places=6)            # the terminal rule from the first explicit year
            econ = self.cf['company_path']['scenarios'][s]['economics']
            self.assertAlmostEqual(econ['roic_minus_engine_terminal_roic'], 0, places=9)
        flags = {(f['id'], f['scenario']) for f in self.cf['company_path']['flags']}
        self.assertNotIn(('capital_intensity_gap', 'base'), flags)
        rows = {(r['lever'], r['scenario']): r for r in self.cf['fc']['driver_sources']}
        self.assertEqual(rows[('sales_to_capital', 'base')]['tier'], 'own_history')
        self.assertEqual(rows[('sales_to_capital', 'base')]['evidence_ids'], EV)
        self.assertEqual(rows[('terminal_roic', 'bear')]['tier'], 'analyst')       # the path's own tier, a visible judgement
        # the bull keeps a hand multiple path: nothing linked there, the DCF is the engine's
        bull_off = {s['name']: s for s in self.off['val']['case']['scenarios']}['bull']
        self.assertEqual(self.cases['bull']['terminal']['roic'], bull_off['terminal']['roic'])
        self.assertNotEqual(self.cf['val']['result']['annual_values'][0]['base'], self.off['val']['result']['annual_values'][0]['base'])

    def test_an_analyst_override_wins_and_the_gap_is_flagged(self):
        ov = [{'path': 'terminal_roic', 'scenario': 'base', 'value': .20, 'reason': 'own history', 'evidence_ids': EV,
               'source_tier': 'own_history'}]
        cf = link_run({'overrides': ov})
        base = {s['name']: s for s in cf['val']['case']['scenarios']}['base']
        self.assertAlmostEqual(base['terminal']['roic'], .20, places=9)
        gap = [f for f in cf['company_path']['flags'] if f['id'] == 'capital_intensity_gap']
        self.assertEqual([f['scenario'] for f in gap], ['base'])
        self.assertIn('השקעה שונה לכל דולר צמיחה', gap[0]['text_he'])
        rule = scen(cf['fc']['spec'], 'base')['reinvestment_rule']['capital_per_revenue']
        self.assertNotAlmostEqual(min(rule), max(rule), places=6)            # reinvestment not linked to a roic the DCF does not use

    def test_a_roic_at_or_below_terminal_growth_is_not_linked(self):
        low = deepcopy(ECON)
        low['scenarios']['bear']['roic'] = .03
        cf = pipeline.run(deepcopy(ADBE), {'company_path': low})
        self.assertEqual(cf['stages']['forecast'], 'ok')
        self.assertIn('capital_link_below_growth', {w['code'] for w in cf['fc']['warnings']})
        self.assertEqual(cf['stages']['company_path'], 'ok')                 # the path refuses that scenario only (0.14.2)
        self.assertIn(('roic_at_or_below_growth', 'bear'), {(f['id'], f.get('scenario')) for f in cf['company_path']['flags']})
        self.assertTrue(all(r['status'] == 'no_price' for r in cf['company_path']['scenarios']['bear']['rows']))
        self.assertTrue(any(r['status'] == 'priced' for r in cf['company_path']['scenarios']['base']['rows']))

    def test_link_builder_skips_malformed_scenarios(self):
        known = set(ADBE['sources'])
        link = pipeline.capital_link(ECON, known)
        self.assertEqual(set(link), {'base', 'bear'})
        bad = deepcopy(ECON)
        bad['scenarios']['base']['evidence_ids'] = ['nope']
        self.assertEqual(set(pipeline.capital_link(bad, known)), {'bear'})
        self.assertIsNone(pipeline.capital_link(None, known))


class Year1NoteTests(unittest.TestCase):
    """(4) the yearly table says when year 1 carries the whole gap from the quote to the model value."""

    def test_flag_and_table_note(self):
        cf = link_run()
        cp = cf['company_path']
        y0, q = cp['scenarios']['base']['rows'][0]['price'], cp['quote']
        flag = [f for f in cp['flags'] if f['id'] == 'year1_carries_gap']
        self.assertEqual(bool(flag), abs(y0 / q - 1) > C.YEAR0_GAP_NOTE)
        rows, notes = C.table_he(cp, lambda v: f'{v:,.0f}$')
        if flag:
            note = next(n for n in notes if 'השינוי בשנה 1 כולל' in n)
            # review of 27b2bf6: the note showed today's EPS x multiple as a second 'worth today' number beside the fair value
            self.assertNotIn(f'{y0:,.0f}', note)
            self.assertNotIn('שווה כבר היום', note)
        near = deepcopy(cf)
        near['company_path']['quote'] = y0 * 1.1
        val = deepcopy(cf['val'])
        val['case']['quote']['price'] = y0 * 1.1
        rebuilt = C.build(deepcopy(ECON), val, set(ADBE['sources']) | {x['id'] for x in ADBE.get('qualitative') or []},
                          None, cf['fc'])
        self.assertFalse([f for f in rebuilt['flags'] if f['id'] == 'year1_carries_gap'])


def fc_with(inputs, fixture=industrial):
    fb, an, card, qa = fixture()
    return forecast.build(fb, an, card, qa, inputs), fb


class CommittedEquityTests(unittest.TestCase):
    """(3) META: contracted equity proceeds are committed financing at the contracted price."""

    def test_contracted_forward_sale_is_committed_and_priced(self):
        base_fc, _ = fc_with(None)
        n = len(base_fc['spec']['boundaries'])
        proceeds = [0.] * n
        proceeds[1] = 60e6                                                 # settles in the first full fiscal year
        ov = [{'path': 'equity_proceeds', 'scenario': 'all', 'value': proceeds, 'reason': 'forward sale agreement',
               'evidence_ids': ['syn:fs'], 'source_tier': 'contracted'},
              {'path': 'equity_issue_price', 'scenario': 'all', 'value': 75.0, 'reason': 'initial forward price',
               'evidence_ids': ['syn:fs'], 'source_tier': 'contracted'}]
        fc, _ = fc_with({'overrides': ov})
        self.assertEqual(fc['status'], 'built', fc.get('reason'))
        sc = scen(fc['spec'], 'base')
        self.assertEqual(sc['financing_status'], 'committed')
        self.assertEqual(sc['equity_issue_price'][1], 75.0)
        self.assertEqual(sc['equity_issue_price'][0], fc['spec']['quote']['price'])
        res = value_company(build_case(fc['spec']))
        rows = next(s for s in res['scenarios'] if s['name'] == 'base')['forecast']
        self.assertAlmostEqual(rows[1]['new_financing_shares'], 60e6 / 75.0, places=3)
        self.assertNotIn('financing is assumed', ' '.join(next(s for s in res['scenarios'] if s['name'] == 'base')['issues']))
        # the same money without a contract is assumed, never 'none' with cash in it (the old ValueError)
        ov2 = [dict(ov[0], source_tier='analyst')]
        fc2, _ = fc_with({'overrides': ov2})
        self.assertEqual(fc2['status'], 'built', fc2.get('reason'))
        self.assertEqual(scen(fc2['spec'], 'base')['financing_status'], 'assumed')
        # contracted proceeds are the filing's NET proceeds: no second 3% fee; an assumed amount keeps the fee
        self.assertEqual(sc['equity_fee_rate'], 0.)
        self.assertEqual(scen(fc2['spec'], 'base')['equity_fee_rate'], forecast.EQUITY_FEE)

    def test_issue_price_only_contracted_and_only_for_contracted_proceeds(self):
        # review of 27b2bf6: META's price at tier analyst and 3000 (4.9x the quote) ran without a problem and raised the value
        base_fc, _ = fc_with(None)
        n = len(base_fc['spec']['boundaries'])
        proceeds = [0.] * n
        proceeds[1] = 60e6
        eq = {'path': 'equity_proceeds', 'scenario': 'all', 'value': proceeds, 'reason': 'forward sale agreement',
              'evidence_ids': ['syn:fs'], 'source_tier': 'contracted'}
        px = {'path': 'equity_issue_price', 'scenario': 'all', 'value': 3000.0, 'reason': 'x', 'evidence_ids': ['syn:fs'],
              'source_tier': 'analyst'}
        with self.assertRaisesRegex(ValueError, 'only a contracted price'):
            fc_with({'overrides': [eq, px]})
        with self.assertRaisesRegex(ValueError, 'equity_proceeds overridden at source_tier contracted'):
            fc_with({'overrides': [dict(eq, source_tier='analyst'), dict(px, source_tier='contracted')]})
        with self.assertRaisesRegex(ValueError, 'equity_proceeds overridden at source_tier contracted'):
            fc_with({'overrides': [dict(px, source_tier='contracted')]})            # a price with no proceeds at all
        lst = [3000.0] * n                                                       # a list: only the contracted period takes it
        fc, _ = fc_with({'overrides': [eq, dict(px, source_tier='contracted', value=lst)]})
        sc = scen(fc['spec'], 'base')
        self.assertEqual(sc['equity_issue_price'][1], 3000.0)
        self.assertTrue(all(p == fc['spec']['quote']['price'] for i, p in enumerate(sc['equity_issue_price']) if i != 1))

    def test_bad_issue_price_refused(self):
        with self.assertRaisesRegex(ValueError, 'contracted issue price must be positive'):
            fc_with({'overrides': [{'path': 'equity_issue_price', 'scenario': 'all', 'value': 0, 'reason': 'x',
                                    'evidence_ids': ['syn:fs'], 'source_tier': 'contracted'}]})


class OptionsTests(unittest.TestCase):
    """(6) RDDT: vested in-the-money options by the treasury method at every value."""

    def test_treasury_method_at_each_value(self):
        plain, _ = fc_with(None)
        opts = [{'kind': 'vested_options', 'count': 5e6, 'strike': 20.0, 'reason': '10-Q note: exercisable options',
                 'evidence_ids': ['syn:fs']}]
        fc, _ = fc_with({'options': opts})
        self.assertEqual(fc['spec']['opening']['options'], [{'id': 'vested_options_1', 'count': 5e6, 'strike': 20.0}])
        v0, v1 = base_value(plain), base_value(fc)
        shares = fc['spec']['opening']['shares']
        self.assertGreater(v0, 20.0)
        self.assertAlmostEqual(v1, (v0 * shares + 5e6 * 20.0) / (shares + 5e6), delta=1e-6 * v0)
        far, _ = fc_with({'options': [dict(opts[0], strike=v0 * 10)]})
        self.assertAlmostEqual(base_value(far), v0, delta=1e-9 * v0)           # out of the money: no dilution
        self.assertIn('options_treasury', {w['code'] for w in fc['warnings']})
        self.assertAlmostEqual(C.option_diluted(100., 90., [{'count': 10., 'strike': 50.}]), (9000 + 500) / 100)
        self.assertEqual(C.option_diluted(40., 90., [{'count': 10., 'strike': 50.}]), 40.)

    def test_validation(self):
        for bad, msg in (({'kind': 'rsu'}, 'kind must be one of'), ({'count': 0}, 'count must be positive'),
                         ({'evidence_ids': ['nope']}, 'evidence_ids'), ({'reason': ''}, 'written reason')):
            o = {'kind': 'vested_options', 'count': 1e6, 'strike': 10., 'reason': 'note', 'evidence_ids': ['syn:fs'], **bad}
            with self.assertRaisesRegex(ValueError, msg):
                fc_with({'options': [o]})


class FinanceLeaseTests(unittest.TestCase):
    """(5) MSFT: one treatment of finance leases."""

    def test_warns_unless_reinvestment_is_lease_inclusive(self):
        claim = [{'kind': 'finance_leases', 'amount': 20e6, 'reason': 'lease note', 'evidence_ids': ['syn:fs']}]
        fc, _ = fc_with({'debt_like_claims': claim})
        w = [x for x in fc['warnings'] if x['code'] == 'finance_leases_reinvestment_basis']
        self.assertEqual((len(w), w[0]['severity']), (1, 'high'))
        ov = [{'path': 'sales_to_capital', 'scenario': 'all', 'value': 1.5, 'reason': 'cash capex + finance-lease ROU additions',
               'evidence_ids': ['syn:fs'], 'source_tier': 'own_history'}]
        fc2, _ = fc_with({'debt_like_claims': claim, 'overrides': ov})
        self.assertNotIn('finance_leases_reinvestment_basis', {x['code'] for x in fc2['warnings']})


def builder():
    """SYNTHETIC build-phase company shaped like NBIS: revenue grew 4x after the last fiscal year, capex ~5x revenue, D&A 80% of
    the last fiscal year's revenue but ~34% of the trailing twelve months'."""
    from test_forecast import make_an, qa_for, synth
    annual = {'2024-12-31': {'revenue': 100e6, 'operating_income': -150e6, 'sbc': 15e6, 'd_and_a': 60e6, 'capex': 400e6,
                             'cfo': -40e6, 'pretax_income': -150e6, 'income_tax': 0.},
              '2025-12-31': {'revenue': 500e6, 'operating_income': -300e6, 'sbc': 75e6, 'd_and_a': 400e6, 'capex': 2500e6,
                             'cfo': -50e6, 'pretax_income': -300e6, 'income_tax': 0.}}
    inst = {'2026-06-30': {'cash': 8e9, 'shares_outstanding': 300e6, 'receivables': 200e6, 'payables': 300e6}}
    fb = synth('2026-10-30', annual, inst, 60.0, kind='filing_derived')
    for end, start, r, oi, da in (('2025-09-30', '2025-07-01', 140e6, -80e6, 60e6), ('2025-12-31', '2025-10-01', 200e6, -90e6, 80e6),
                                  ('2026-03-31', '2026-01-01', 300e6, -100e6, 100e6), ('2026-06-30', '2026-04-01', 420e6, -110e6, 120e6)):
        for m, v in (('revenue', r), ('operating_income', oi), ('d_and_a', da)):
            facts.add_fact(fb, m, 'Q@' + end, end, v, 'syn:fs', 'as_reported', end[:8] + '28', period_start=start)
    an = make_an(fb, revenue_growth_last=4.0, growth_volatility=.5, sales_to_capital_incremental_3y=.3)
    an['ttm'] = {'revenue': 1060e6, 'operating_income': -380e6, 'period_end': '2026-06-30', 'method': 'quarters'}
    card = {'business_model': {'primary': 'capital_intensive_infrastructure'}, 'stage': {'value': 'growth'},
            'funding': {'value': 'needs_external'}, 'valuation': {'method': 'fcff_annual_path', 'archetype': 'infrastructure'},
            'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(300e6, 8e9, 0., '2026-06-30')


class BuildPhaseTests(unittest.TestCase):
    """(7) NBIS/ASTS: the D&A load on the margin's own basis; an evidenced build-phase start; honest 'not valued'."""

    def test_ttm_load_replaces_the_fiscal_year_load_only_when_the_path_is_impossible(self):
        fb, an, card, qa = builder()
        self.assertAlmostEqual(forecast._ttm_load(fb, 'd_and_a', '2025-12-31'), 360 / 1060, places=6)
        ov = [{'path': 'mature_margin', 'scenario': s, 'value': v, 'reason': 'contracted unit economics', 'evidence_ids': ['syn:fs'],
               'source_tier': 'contracted'} for s, v in (('bear', .15), ('base', .25), ('bull', .3))]
        fc = forecast.build(fb, an, card, qa, {'overrides': ov})
        self.assertEqual(fc['status'], 'built', fc.get('reason'))
        self.assertIn('da_load_ttm', {w['code'] for w in fc['warnings']})
        self.assertAlmostEqual(scen(fc['spec'], 'base')['depreciation_pct'][0], 360 / 1060, places=5)
        # without the TTM quarters the fiscal-year load stays and the forecast says what it needs
        fb2 = deepcopy(fb)
        fb2['facts'] = [f for f in fb2['facts'] if not (f['metric'] == 'd_and_a' and f['period'].startswith('Q@'))]
        fc2 = forecast.build(fb2, an, card, qa, {'overrides': ov})
        self.assertEqual(fc2['status'], 'method_not_supported_by_forecast')
        self.assertIn('build_phase', fc2['reason'])

    def test_evidenced_build_phase_start(self):
        fb, an, card, qa = builder()
        ov = [{'path': 'mature_margin', 'scenario': s, 'value': v, 'reason': 'contracted unit economics', 'evidence_ids': ['syn:fs'],
               'source_tier': 'contracted'} for s, v in (('bear', .15), ('base', .25), ('bull', .3))]
        bp = {'start_margin': -.3, 'depreciation_pct': .3, 'sbc_pct': .08, 'reason': 'guided revenue and the depreciation schedule',
              'evidence_ids': ['syn:fs'], 'source_tier': 'guidance'}
        fc = forecast.build(fb, an, card, qa, {'overrides': ov, 'build_phase': bp})
        self.assertEqual(fc['status'], 'built', fc.get('reason'))
        self.assertIn('build_phase_start', {w['code'] for w in fc['warnings']})
        p = scen(fc['spec'], 'base')
        self.assertAlmostEqual(p['sbc_pct'][0], .08, places=6)
        self.assertAlmostEqual(p['depreciation_pct'][-1], .3, places=6)        # D&A converges to the evidenced load
        for bad in ({'start_margin': -3}, {'reason': ''}, {'evidence_ids': ['nope']}, {'start_margin': None, 'depreciation_pct': None,
                                                                                         'sbc_pct': None}):
            with self.assertRaises(ValueError):
                forecast.build(fb, an, card, qa, {'build_phase': {**bp, **bad}})

    def test_build_phase_is_not_applied_to_a_normal_company(self):
        # review of 27b2bf6: depreciation_pct 0.01 on MSFT/META added the D&A roll-off to the margin (+24% / +28% of value)
        fb, an, card, qa = industrial()
        ev = [next(k for k, v in fb['sources'].items() if v.get('kind') == 'filing_derived')]
        plain = forecast.build(fb, an, card, qa, {})
        bp = {'depreciation_pct': .01, 'sbc_pct': .01, 'start_margin': .5, 'source_tier': 'own_history', 'reason': 'probe',
              'evidence_ids': ev}
        fc = forecast.build(fb, an, card, qa, {'build_phase': bp})
        self.assertEqual(fc['status'], 'built', fc.get('reason'))
        self.assertIn('build_phase_not_applied', {w['code'] for w in fc['warnings']})
        self.assertNotIn('build_phase_start', {w['code'] for w in fc['warnings']})
        self.assertAlmostEqual(base_value(fc), base_value(plain), places=9)

    def test_build_phase_needs_the_filing_and_a_company_tier(self):
        fb, an, card, qa = builder()
        fb['sources']['web:press'] = {'kind': 'secondary', 'provider': 'press', 'dataset': 'press', 'retrieval': 'search_excerpt'}
        bp = {'start_margin': -.3, 'depreciation_pct': .3, 'sbc_pct': .08, 'reason': 'the 10-Q cost lines', 'evidence_ids': ['syn:fs'],
              'source_tier': 'own_history'}
        for bad in ({'source_tier': 'analyst'}, {'source_tier': 'consensus'}, {'evidence_ids': ['web:press']}):
            with self.assertRaises(ValueError) as cm:
                forecast.build(fb, an, card, qa, {'build_phase': {**bp, **bad}})
            self.assertIn('build_phase', str(cm.exception))

    def test_build_phase_da_load_never_adds_margin(self):
        fb, an, card, qa = builder()
        ov = [{'path': 'terminal_roic', 'scenario': 'all', 'value': .15, 'reason': 'contracted unit economics', 'evidence_ids': ['syn:fs'],
               'source_tier': 'contracted'}]
        out = {}
        for d in (.05, .3):
            bp = {'start_margin': -.3, 'depreciation_pct': d, 'sbc_pct': .08, 'reason': 'the 10-Q cost lines',
                  'evidence_ids': ['syn:fs'], 'source_tier': 'own_history'}
            fc = forecast.build(fb, an, card, qa, {'overrides': ov, 'build_phase': bp})
            self.assertEqual(fc['status'], 'built', fc.get('reason'))
            p = scen(fc['spec'], 'base')
            self.assertAlmostEqual(min(p['depreciation_pct']), max(p['depreciation_pct']), places=6)   # start = maintenance level
            out[d] = [round(1 - c - x - y, 9) for c, x, y in zip(p['cash_cost_ratio'], p['sbc_pct'], p['depreciation_pct'])]
        self.assertEqual(out[.05], out[.3])                  # a lower D&A load never raises the operating margin path

    def test_asts_shape_values_with_its_evidenced_start(self):
        # SYNTHETIC, shaped like ASTS 2026-06: TTM operating margin about -660%, stock comp 128% and capex 19x revenue
        from test_forecast import make_an, qa_for, synth
        annual = {'2024-12-31': {'revenue': 4e6, 'operating_income': -250e6, 'sbc': 20e6, 'd_and_a': 20e6, 'capex': 100e6,
                                 'cfo': -200e6, 'pretax_income': -250e6, 'income_tax': 0.},
                  '2025-12-31': {'revenue': 20e6, 'operating_income': -330e6, 'sbc': 26e6, 'd_and_a': 14.4e6, 'capex': 380e6,
                                 'cfo': -250e6, 'pretax_income': -330e6, 'income_tax': 0.}}
        inst = {'2026-06-30': {'cash': 4e9, 'shares_outstanding': 300e6, 'receivables': 10e6, 'payables': 50e6}}
        fb = synth('2026-10-30', annual, inst, 40.0, kind='filing_derived')
        for end, start, r, oi, da in (('2025-09-30', '2025-07-01', 6e6, -90e6, 6e6), ('2025-12-31', '2025-10-01', 8e6, -95e6, 6e6),
                                      ('2026-03-31', '2026-01-01', 14e6, -100e6, 8e6), ('2026-06-30', '2026-04-01', 32e6, -110e6, 15e6)):
            for m, v in (('revenue', r), ('operating_income', oi), ('d_and_a', da)):
                facts.add_fact(fb, m, 'Q@' + end, end, v, 'syn:fs', 'as_reported', end[:8] + '28', period_start=start)
        an = make_an(fb, revenue_growth_last=4.0, growth_volatility=.5, sales_to_capital_incremental_3y=.3)
        an['ttm'] = {'revenue': 60e6, 'operating_income': -396e6, 'period_end': '2026-06-30', 'method': 'quarters'}
        card = {'business_model': {'primary': 'capital_intensive_infrastructure'}, 'stage': {'value': 'growth'},
                'funding': {'value': 'needs_external'}, 'valuation': {'method': 'fcff_annual_path', 'archetype': 'infrastructure'},
                'dimensions': {'moat': {'assessment': 'adequate'}}}
        qa = qa_for(300e6, 4e9, 0., '2026-06-30')
        ov = [{'path': 'mature_margin', 'scenario': s, 'value': v, 'reason': 'named competitor margin', 'evidence_ids': ['syn:fs'],
               'source_tier': 'contracted'} for s, v in (('bear', .17), ('base', .27), ('bull', .274))]
        none = forecast.build(fb, an, card, qa, {'overrides': ov})
        self.assertEqual(none['status'], 'method_not_supported_by_forecast')
        self.assertIn('inputs.build_phase', none['reason'])
        bp = {'start_margin': -.95, 'depreciation_pct': .25, 'sbc_pct': .30, 'reason': 'guided revenue and the 10-Q cost lines',
              'evidence_ids': ['syn:fs'], 'source_tier': 'guidance'}
        fc = forecast.build(fb, an, card, qa, {'overrides': ov, 'build_phase': bp})
        self.assertEqual(fc['status'], 'built', fc.get('reason'))
        self.assertIn('build_phase_start', {w['code'] for w in fc['warnings']})
        p = scen(fc['spec'], 'base')
        self.assertAlmostEqual(p['depreciation_pct'][0], .25, places=6)
        self.assertLessEqual(max(c['terminal']['operating_margin'] for c in fc['spec']['scenarios']), .274 + 1e-9)

    def test_value_json_says_not_valued(self):
        cf = {'stages': {'forecast': 'ok', 'valuation': 'ok', 'company_path': 'ok'},
              'fc': {'status': 'method_not_supported_by_forecast', 'reason': 'cash-cost ratio outside driver bounds'},
              'val': {'status': 'not_computed', 'reason': 'cash-cost ratio outside driver bounds'},
              'company_path': {'status': 'not_available', 'reason': 'no executed valuation'}}
        v = ticker_run.valued(cf, True)
        self.assertFalse(v['valued'])
        self.assertTrue(v['failed_at'].startswith('not valued: forecast method_not_supported_by_forecast'))
        self.assertIn('לא הוערך', v['not_valued_he'])
        ok = {'stages': {'forecast': 'ok', 'valuation': 'ok'}, 'fc': {'status': 'built'},
              'val': {'status': 'unreviewed', 'result': {'annual_values': [{'base': 12.5}]}}}
        self.assertEqual(ticker_run.valued(ok), {'valued': True, 'failed_at': None})
        # review of 27b2bf6: a funding_blocked valuation (no base value) or one with no base value is not a valuation
        blocked = deepcopy(ok)
        blocked['val'] = {'status': 'funding_blocked', 'result': {'annual_values': [{'base': None}]}}
        v = ticker_run.valued(blocked)
        self.assertFalse(v['valued'])
        self.assertIn('funding_blocked', v['failed_at'])
        self.assertEqual(v['not_valued_he'], ticker_run.NOT_VALUED_HE['funding'])
        self.assertNotIn('\n', v['not_valued_he'])
        for bad in (None, float('nan'), True):
            x = deepcopy(ok)
            x['val']['result']['annual_values'][0]['base'] = bad
            self.assertFalse(ticker_run.valued(x)['valued'], bad)
        x = deepcopy(ok)
        x['val']['status'] = 'funding_blocked'                               # blocked even with a number left in place
        self.assertFalse(ticker_run.valued(x)['valued'])
        capped = deepcopy(ok)
        capped['fc']['warnings'] = [{'code': 'leverage_capped', 'severity': 'high', 'message': 'debt weight 60% (capped)'}]
        v = ticker_run.valued(capped)
        self.assertFalse(v['valued'])
        self.assertEqual(v['not_valued_he'], ticker_run.NOT_VALUED_HE['leverage'])
        bp = ticker_run.valued(cf, True)
        self.assertEqual(bp['not_valued_he'], ticker_run.NOT_VALUED_HE['build_phase'])
        self.assertTrue(ticker_run.valued({'stages': {'forecast': 'failed'}})['failed_at'].startswith('engine stages'))


class SameFamilyResolutionTests(unittest.TestCase):
    """(8) ASTS: a resolution citing the filing document settles a disagreement between two copies of that filing."""

    def fb(self):
        fb = conflict_fb()
        fb['facts'] = [f for f in fb['facts'] if f['source_id'] != 'av:balance']
        facts.add_fact(fb, 'short_term_investments', 'I@2026-06-30', '2026-06-30', 0., 'web:q10', 'as_reported', '2026-08-26')
        facts.add_fact(fb, 'cash', 'I@2026-06-30', '2026-06-30', 20e9, 'web:q10', 'as_reported', '2026-08-26')
        fb['sources']['web:q10']['retrieval'] = 'primary_document'          # the 10-Q read directly
        return fb

    RES = {'field': 'cash_and_liquid_investments', 'metric': 'short_term_investments', 'resolution': 'use_source',
           'period_end': '2026-06-30', 'source_id': 'web:q10', 'reason': 'one cash line: the money-market funds are cash equivalents'}

    def test_document_evidence_wins_inside_the_family(self):
        fb = self.fb()
        q0 = dataqa.validate(fb)
        self.assertIn('cash_and_liquid_investments', q0['blocking'])
        q = dataqa.validate(fb, None, {'resolutions': [{**self.RES, 'evidence_ids': ['web:q10']}]})
        self.assertNotIn('cash_and_liquid_investments', q['blocking'])
        self.assertEqual(q['resolved']['short_term_investments']['value'], 0.)
        self.assertEqual(q['resolutions'][0]['status'], 'applied')

    def test_a_search_excerpt_of_the_filing_does_not(self):
        # review of 27b2bf6: 'read directly' means retrieval primary_document, not a search excerpt of the 10-Q
        fb = self.fb()
        fb['sources']['web:q10']['retrieval'] = 'search_excerpt'
        q = dataqa.validate(fb, None, {'resolutions': [{**self.RES, 'evidence_ids': ['web:q10']}]})
        self.assertIn('cash_and_liquid_investments', q['blocking'])

    def test_a_transcription_alone_does_not(self):
        fb = self.fb()
        q = dataqa.validate(fb, None, {'resolutions': [{**self.RES, 'source_id': 'sec:companyfacts',
                                                        'evidence_ids': ['sec:companyfacts']}]})
        self.assertIn('cash_and_liquid_investments', q['blocking'])
        self.assertEqual(q['resolutions'][0]['status'], 'unused')


class RoutingTests(unittest.TestCase):
    """(9) APH: an infrastructure keyword with a capital-light signature yields to the label's other type or the SIC code."""

    def company(self, industry, sic=3678):
        return {'industry': industry, 'sector': 'Information Technology', 'sic': sic, 'sic_description': 'Electronic Connectors'}

    def bm(self, company, capex):
        fb = syn(quarters=False)
        fb['company'].update(company)
        for f in fb['facts']:
            if f['metric'] == 'capex':
                f['value'] = f['value'] * capex
        qa = dataqa.validate(fb)
        an = analysis.analyze(fb, qa)
        return model_card.build(fb, an, qa, None)['business_model']

    def test_capital_light_connector_maker_is_hardware(self):
        bm = self.bm(self.company('Electronic connectors and interconnect: connectors, cable assemblies, sensors'), 1.)
        self.assertEqual(bm['primary'], 'hardware_manufacturing')
        self.assertTrue(any('economics before a keyword' in r for r in bm['reasons']))
        sic_only = self.bm(self.company('Cable assemblies and harnesses'), 1.)
        self.assertEqual(sic_only['primary'], 'hardware_manufacturing')     # from the SIC code (3660-3699)

    def test_a_real_network_keeps_infrastructure(self):
        # capex 45/1200 = 3.75% in the fixture; x8 = 30% of revenue: a cable network's signature
        bm = self.bm(self.company('Cable and satellite', sic=4841), 8.)
        self.assertEqual(bm['primary'], 'capital_intensive_infrastructure')


class InterestRunRateTests(unittest.TestCase):
    """(10) APH: after a debt jump the existing debt's coupon reads the latest reported quarters, not the fiscal year."""

    def test_latest_quarters_after_a_debt_jump(self):
        fb = facts.new({'ticker': 'SYN', 'name': 'Synthetic test company', 'currency': 'USD', 'fiscal_year_end': '12-31'}, '2026-11-01')
        facts.add_source(fb, 'syn:fs', 'synthetic', 'test', 'filing_derived', '2026-11-01T00:00:00Z', note='TEST DATA ONLY')
        facts.add_fact(fb, 'interest_expense', 'FY2025', '2025-12-31', 367.8e6, 'syn:fs', 'as_reported', '2026-02-10',
                       period_start='2025-01-01')
        facts.add_fact(fb, 'long_term_debt', 'I@2025-12-31', '2025-12-31', 15.0e9, 'syn:fs', 'as_reported', '2026-02-10')
        for end, start, v in (('2025-06-30', '2025-04-01', 80.9e6), ('2025-09-30', '2025-07-01', 80.7e6),
                              ('2025-12-31', '2025-10-01', 129.7e6), ('2026-03-31', '2026-01-01', 207.9e6),
                              ('2026-06-30', '2026-04-01', 213.7e6), ('2026-09-30', '2026-07-01', 212.3e6)):
            facts.add_fact(fb, 'interest_expense', 'Q@' + end, end, v, 'syn:fs', 'as_reported', end[:8] + '28', period_start=start)
        use, basis = forecast._interest_run_rate(fb, 367.8e6, '2026-09-30', 17.87e9)
        self.assertGreater(use / 17.87e9, .045)                              # ~4.8%, not 2.06% (367.8M / 17.87B)
        self.assertIn('2026-09-30', basis)


class PackInputsTests(unittest.TestCase):
    """The new pack routes: forecast_options options / build_phase, drivers equity_issue_price, the finance-lease note."""

    def test_pack_routes(self):
        from test_evidence_pack import pack, refused
        from fundamental_engine.evidence_pack import to_inputs
        p = pack()
        p['forecast_options'] = {
            'options': [{'kind': 'vested_options', 'count': 5.6e6, 'strike': 39.29, 'reason': 'equity note', 'evidence_ids': ['s_10q']}],
            'build_phase': {'start_margin': -.4, 'sbc_pct': .1, 'source_tier': 'own_history', 'reason': 'the 10-Q cost lines',
                            'evidence_ids': ['s_10q']},
            'debt_like_claims': [{'kind': 'finance_leases', 'amount': 1e9, 'reason': 'lease note', 'evidence_ids': ['s_10q']}]}
        p['drivers']['equity_issue_price'] = {'all': 600.0, 'source_tier': 'contracted', 'evidence_ids': ['s_10q'],
                                              'reason': 'forward price'}
        i = to_inputs(p)
        f = i['forecast']
        self.assertEqual(f['options'][0]['evidence_ids'], ['web:s_10q'])
        self.assertEqual((f['build_phase']['start_margin'], f['build_phase']['sbc_pct']), (-.4, .1))
        self.assertIn('equity_issue_price', {o['path'] for o in f['overrides']})
        self.assertTrue(any('finance leases are deducted' in n for n in i['evidence_pack']['notes']))
        for opt, frag in (({'options': [{'kind': 'rsu', 'count': 1, 'strike': 1, 'reason': 'x', 'evidence_ids': ['s_10q']}]}, 'kind one of'),
                          ({'options': [{'kind': 'warrants', 'count': 1, 'strike': 1, 'reason': 'x', 'evidence_ids': ['s_est']}]},
                           "company's own filing"),
                          ({'build_phase': {'start_margin': -5, 'source_tier': 'own_history', 'reason': 'x', 'evidence_ids': ['s_10q']}},
                           'start_margin')):
            q = pack()
            q['forecast_options'] = opt
            refused(self, q, frag)


if __name__ == '__main__':
    unittest.main()
