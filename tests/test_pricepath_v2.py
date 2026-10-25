"""pricepath spec 2 (docs/prereg_price_path_v2.md) on SYNTHETIC numbers only: invented companies and an invented cross-section,
not market data. One test class per registered rule, with concrete numbers; plus the pin that spec 1's output (the failed,
recorded run and the lane_v1 baseline) is unchanged by the spec-2 code."""
import hashlib
import importlib.util
import json
import math
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_pricepath as T1  # noqa: E402

from fundamental_engine import base_rates, facts as F, pricepath as PP  # noqa: E402
from fundamental_engine.forecast import STAGE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CPI = base_rates.CPI_2015                      # revenue stated in 2015 dollars: the size class reads the raw figure
CLASS = {c[0]: c[3] for c in base_rates.CLASSES}
MEGA, SMALL = CLASS['>$50,000m'], CLASS['$0-325m']
G_BUCKETS = {'<0%': -.05, '0-5%': .02, '5-10%': .07, '10-20%': .15, '20-30%': .25, '>30%': .40}
M_BUCKETS = {'<5%': .03, '5-10%': .07, '10-20%': .15, '>20%': .30}


def anch2(m=.5, rev=5.3e9, price=85.7, shares=1e9, **over):
    """v2 anchors of an invented company (net income = revenue x m, consistent by construction)."""
    a = {'status': 'ok', 'spec_version': 2, 'as_of': '2026-06-30', 'ticker': 'SYN', 'price': price, 'risk_free': .042, 'erp': .042,
         'cpi': CPI, 'cpi_basis': 'test', 'stage': 'growth', 'cyclical': False, 'revenue': rev, 'net_income': rev * m, 'shares': shares,
         'g_run': .24, 'g_raw': .24, 'g_basis': 'test', 'g_fiscal_year': .24, 'g_quarter': .24, 'g_3y': .24, 'quarter_ref': None,
         'm_ttm': m, 'm_lo': m - .10, 'm_hi': m + .05, 'm_mid_diagnostic': m - .05, 'margin_years': 10, 'm_basis': 'test',
         'share_cagr': 0., 'roic': .25, 'dividends_ttm': 0., 'dividend_yield': 0., 'ttm_period_end': '2026-03-31',
         'ttm_method': 'quarters', 'references': {}, 'flags': [], 'notes': []}
    a.update(over)
    return a


def cs_table(cells, as_of='2026-06-30', extra=()):
    """cells = {(growth bucket, margin bucket): [PE, ...]} -> a cross_section_table (entries at the buckets' mid points)."""
    entries, k = [], 0
    for (gb, mb), pes in cells.items():
        for pe in pes:
            entries.append({'symbol': f'C{k:04d}', 'g_run': G_BUCKETS[gb], 'm_ttm': M_BUCKETS[mb], 'pe': pe})
            k += 1
    return PP.cross_section_table(entries + list(extra), as_of, 'synthetic cross-section')


def flat_table(pe=20., n=25, as_of='2026-06-30'):
    return cs_table({(g, m): [pe] * n for g in G_BUCKETS for m in M_BUCKETS}, as_of)


def spread_table(n=25):
    """Every cell: PE = 10 + 2 x (growth index) + (margin index) + 0.8 j, j = 0..n-1 (higher growth pays more)."""
    return cs_table({(g, m): [10 + 2 * gi + mi + .8 * j for j in range(n)]
                     for gi, g in enumerate(G_BUCKETS) for mi, m in enumerate(M_BUCKETS)})


# ---------------------------------------------------------------- spec 1 is unchanged

def _canon(o):
    """Floats rounded to 10 decimals: the pins hold across Python versions (3.12 sums floats with compensation) and libms."""
    if isinstance(o, float):
        return round(o, 10)
    if isinstance(o, dict):
        return {str(k): _canon(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_canon(v) for v in o]
    return o


def _h(o):
    return hashlib.sha256(json.dumps(_canon(o), sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


# Computed from the spec-1 code of commit cc2f3bd (before any spec-2 code; identical under Python 3.11, 3.12 and 3.13): the whole
# price_path output (paths, ledger, band statement), its own output_sha256, and the evaluate() result of the spec-1 tests' rows.
SPEC1_PINS = {
    'ibkr_like': ('75dc8ce7067624f45aedd2343d3622423361889c3f61d561ae1019678b2e5501', 'fb7f176df798e35b76e4604dcbbb072b1f782e038b6784ecd21eace386b1ddcf'),
    'negative_premium': ('dea656b508cc9448821f7f339447588fa4682351eea56f36392832d88cef1a7a', 'ccfea789b794b0677fdb13530c988166915acdb123f77f9e1f03b3121d08833f'),
    'inverting': ('0dc3b8b4bab3d7e12bb13dbb96f12bcef3973deda56d7d6ac26863b1e779eb8c', '2d58276557b7e8d159bcac1cd64f6e89e9af629ffb4cd9e7f152d9096b798fff'),
    'thin_margin': ('35b8e21947700c2ded4f9c8070761c605cd71496bab0603b76f21242318e7e0a', '4ca4b695059a89e294db48ef5abf9755638d1680f8cf6281ae9736104b358d40'),
    'capped_200x': ('f9575124cdb8b050fae0beff7aad2b713d0c8d25ffb98d0dcfa34ca84911cba3', 'cc7872abe7d13df338df68ffb2d33a281af56e23c9472c9ad421f13db3efd326'),
    'guided': ('8e751b393f06c068f72125d3b96b266cc2201ed5f8c3ba2bdc81ebc05c665d85', '023043b758e8148126653912717428749a418941a07c0e6a806e0d43c9b90d84'),
    'cyclical_anchors': ('558394572bb0844f5c296cfb2af10ddda4784ad7f82dd889d55815d3a5c40ad9', '296a9ab1d667dd2a2f6c4a943cc44e84456482283e9801f8bbe1f4a9129cf31d'),
}
SPEC1_ANCHORS_PIN = '474ca27c7041cc3a0f18d850fc8c1541585625a75a08472e1480f8f59332c9f0'
SPEC1_EVALUATE_PIN = '0855920673e67d57ff83d8865516eca900ef4e93fbc35024ee90b541b776fe16'


class Spec1UnchangedTests(unittest.TestCase):
    """v2 section 6.4 needs the spec-1 code unchanged (lane_v1 = a deterministic re-run of it); the spec-2 code must not move
    a single byte of a spec-1 output."""

    def _cases(self):
        fb, an, qa, card = T1.AnchorTests()._company([.10, .08, .12, .10, .25], .30, cyclical=True)
        cyc = PP.anchors(fb, an, qa, card, 50., .01, .05, cpi=255.802)
        guided = {'guidance': {'revenue_growth_fy1': {'value': .30, 'evidence_ids': ['e1'], 'issued_at': '2026-05-01'}},
                  'track_record': {'rows': 6, 'median_revenue_surprise': .02}}
        return {'ibkr_like': (T1.ibkr_like(), None), 'negative_premium': (T1.ibkr_like(price=2.65 * 10), None),
                'inverting': (T1.ibkr_like(stage='mature', g_run=-.05, g_raw=-.05, price=2.65 * .4, roic=.12), None),
                'thin_margin': (T1.ibkr_like(m_ttm=.03, m_anchor=.03, net_income=.03 * 5.3e9, price=.03 * 5.3 * 30), None),
                'capped_200x': (T1.ibkr_like(price=2.65 * 200), None), 'guided': (T1.ibkr_like(), guided),
                'cyclical_anchors': (cyc, None)}

    def test_spec1_price_path_output_hashes_are_pinned(self):
        cases = self._cases()
        self.assertEqual(_h(cases['cyclical_anchors'][0]), SPEC1_ANCHORS_PIN)       # spec-1 anchors unchanged too
        for name, (anch, inputs) in cases.items():
            out = PP.price_path(anch, inputs)
            self.assertEqual((_h(out), out['output_sha256']), SPEC1_PINS[name], name)
            self.assertEqual(out, PP.price_path(anch, inputs, spec_version=1), name)   # the default is spec 1
            self.assertEqual(out['spec_version'], 1)

    def test_spec1_evaluate_is_pinned(self):
        rows, prices = [], {'SPY': T1._px({'2020-06': 100., '2021-06': 110., '2022-06': 100., '2023-06': 120.})}
        for s, r in {'S0': 1.10, 'S1': 1.30, 'S2': 0.80, 'S3': 1.00, 'S4': 1.50}.items():
            prices[s] = T1._px({'2020-06': 100., '2021-06': 100. * r, '2022-06': 100. * r, '2023-06': 100. * r * 1.1})
            pred = 100. * r * (1.2 if s == 'S3' else .6 if s == 'S4' else 1.)
            rows.append(T1._row(s, '2020-07-01', 100., [pred] * 5, [pred * .7] * 5, [pred * 1.4] * 5))
        rows.append(T1._row('S5', '2020-07-01', 100., [100.] * 5, [80.] * 5, [120.] * 5, status='excluded', reasons=['TTM net income not positive']))
        rows.append(T1._row('S6', '2020-07-01', 100., [100.] * 5, [80.] * 5, [120.] * 5))
        engine = [{'symbol': s, 'formation': '2020-07-01', 'base_irr': .08} for s in ('S0', 'S1', 'S2', 'S3')]
        self.assertEqual(_h(PP.evaluate(rows, prices, engine)), SPEC1_EVALUATE_PIN)

    def test_specs_do_not_mix(self):
        with self.assertRaises(ValueError):
            PP.price_path(anch2(), spec_version=1)                  # spec-2 anchors on the spec-1 method
        with self.assertRaises(ValueError):
            PP.price_path(T1.ibkr_like(), spec_version=2, cross_section=flat_table())
        with self.assertRaises(ValueError):
            PP.price_path(T1.ibkr_like(), spec_version=3)
        with self.assertRaises(ValueError):
            PP.anchors({}, {}, {}, {}, 1., .01, .05, spec_version=3)
        self.assertEqual(PP.SPEC_VERSIONS, (1, 2))


# ---------------------------------------------------------------- 3.1 anchors: run rate and own margin history

def company(margins=(.10, .08, .12, .10, .25), ttm_margin=.30, cyclical=False, quarters=True, metrics=None):
    """Five invented fiscal years (revenue 1bn each), optional quarters (Q1 2019 240m ... Q1 2020 300m), TTM revenue 1.06bn."""
    fb = T1._fb()
    annual = {}
    for e, m in zip(['2015-12-31', '2016-12-31', '2017-12-31', '2018-12-31', '2019-12-31'][-len(margins):] if margins else [], margins):
        F.add_fact(fb, 'revenue', f'FY{e[:4]}', e, 1e9, 'sec:companyfacts', 'as_reported', f'{int(e[:4]) + 1}-02-20', period_start=f'{e[:4]}-01-01')
        F.add_fact(fb, 'net_income', f'FY{e[:4]}', e, 1e9 * m, 'sec:companyfacts', 'as_reported', f'{int(e[:4]) + 1}-02-20', period_start=f'{e[:4]}-01-01')
        annual[e] = {'revenue': 1e9, 'net_income': 1e9 * m}
    if quarters:
        for q, v in (('2019-03-31', 240e6), ('2019-06-30', 250e6), ('2019-09-30', 250e6), ('2019-12-31', 260e6), ('2020-03-31', 300e6)):
            F.add_fact(fb, 'revenue', f'Q@{q}', q, v, 'sec:companyfacts', 'as_reported', '2020-05-01', period_start=q[:8] + '01')
    met = {'revenue_growth_last': {'value': 0.}, 'revenue_cagr_3y': {'value': .06}, 'revenue_cagr_5y': {'value': .04},
           'share_count_cagr_3y': {'value': -.02}, 'roic': {'value': .15}}
    met.update(metrics or {})
    an = {'ttm': {'revenue': 1.06e9, 'net_income': 1.06e9 * ttm_margin, 'period_end': '2020-03-31', 'method': 'quarters', 'dividends': 50e6},
          'metrics': {k: v for k, v in met.items() if v is not None}, 'annual': annual}
    qa = {'resolved': {'shares_outstanding': {'value': 100e6, 'as_of': '2020-04-30', 'basis': 'cover', 'source_id': 'sec:companyfacts'}}}
    card = {'stage': {'value': 'mature'}, 'cyclical': {'value': cyclical}}
    return fb, an, qa, card


class RunRateTests(unittest.TestCase):
    def test_run_rate_is_the_mean_of_fiscal_year_quarter_and_three_year_cagr(self):
        a = PP.anchors(*company(), 50., .01, .05, spec_version=2)
        self.assertEqual((a['status'], a['spec_version']), ('ok', 2))
        self.assertAlmostEqual(a['g_quarter'], 300e6 / 240e6 - 1)                    # Q1 2020 vs Q1 2019: +25%
        self.assertAlmostEqual(a['g_run'], (0. + .25 + .06) / 3)                    # 10.33%; spec 1 read (0 + 25%) / 2 = 12.5%
        self.assertEqual(a['g_basis'], 'mean of latest fiscal year growth, latest quarter vs a year earlier, 3-year revenue CAGR')
        self.assertAlmostEqual(PP.anchors(*company(), 50., .01, .05)['g_run'], .125)   # spec 1 unchanged

    def test_available_legs_only_and_at_least_one(self):
        a = PP.anchors(*company(quarters=False, metrics={'revenue_growth_last': None}), 50., .01, .05, spec_version=2)
        self.assertAlmostEqual(a['g_run'], .06)                                      # the 3-year CAGR alone
        a = PP.anchors(*company(quarters=False), 50., .01, .05, spec_version=2)
        self.assertAlmostEqual(a['g_run'], .03)                                      # (0% + 6%) / 2
        x = PP.anchors(*company(quarters=False, metrics={'revenue_growth_last': None, 'revenue_cagr_3y': None}), 50., .01, .05, spec_version=2)
        self.assertEqual(x, {'status': 'excluded', 'reasons': ['no growth run rate (no fiscal-year, quarterly or 3-year comparison)']})

    def test_cyclical_keeps_spec1_trend_rule_and_the_clip(self):
        a = PP.anchors(*company(cyclical=True), 50., .01, .05, spec_version=2)
        self.assertAlmostEqual(a['g_run'], .04)                                      # revenue_cagr_5y, the last leg ignored
        self.assertIn('cyclical_growth_trend', a['flags'])
        a = PP.anchors(*company(cyclical=True, metrics={'revenue_cagr_5y': None, 'revenue_cagr_3y': {'value': .40}}), 50., .01, .05, spec_version=2)
        self.assertAlmostEqual(a['g_run'], PP.CYC_G[1])                              # 3-year trend clipped to +25%
        a = PP.anchors(*company(metrics={'revenue_growth_last': {'value': 1.5}}), 50., .01, .05, spec_version=2)
        self.assertAlmostEqual(a['g_raw'], (1.5 + .25 + .06) / 3)
        self.assertEqual(a['g_run'], PP.G_RUN[1])
        self.assertIn('run_rate_clipped', a['flags'])


class MarginTests(unittest.TestCase):
    MS = (1 - PP.TAX_STAT) * STAGE['mature']['m_spread']                            # 0.0316 after tax

    def test_ttm_margin_is_the_base_for_a_cyclical_and_history_sets_the_band(self):
        a = PP.anchors(*company(cyclical=True), 50., .01, .05, spec_version=2)
        self.assertAlmostEqual(a['m_ttm'], .30)                                      # no mid-cycle cut (spec 1 used 10%)
        self.assertEqual((a['m_lo'], a['m_hi'], a['margin_years']), (.08, .25, 5))
        self.assertAlmostEqual(a['m_mid_diagnostic'], .10)                           # spec 1's anchor, a diagnostic now
        out = PP.price_path(a, spec_version=2, cross_section=flat_table(as_of='2020-06-30'))
        self.assertEqual({y['margin'] for y in out['scenarios']['base']['years']}, {.30})
        self.assertAlmostEqual(out['eps_ttm'], 1.06e9 * .30 / 100e6)                # reported, not normalized
        self.assertAlmostEqual(out['multiple_today'], 50. / (1.06e9 * .30 / 100e6), places=5)
        # a cyclical at a peak: its bear goes to its own trough (8%) but not below half today's margin (15%)
        self.assertEqual(out['margin_history']['bear_target'], .15)
        self.assertEqual(out['margin_history']['bear_basis'], 'half_of_ttm')

    def test_targets_each_bound(self):
        t, b = PP.margin_targets(.30, .08, .25, 'mature')
        self.assertEqual((t['bear'], b['bear']), (.15, 'half_of_ttm'))              # max(min(8%, 26.8%), 15%)
        self.assertAlmostEqual(t['bull'], .30 + .75 * self.MS)                      # max(25%, 32.4%) under the 45% cap
        self.assertEqual(b['bull'], 'stage_spread')
        t, b = PP.margin_targets(.12, .07, .20, 'mature')
        self.assertEqual((t['bear'], b['bear']), (.07, 'own_low'))                  # own worst year below TTM - spread (8.8%)
        self.assertAlmostEqual(t['bull'], .18)                                      # own best 20% capped at 1.5 x 12%
        self.assertEqual(b['bull'], '1.5x_ttm')
        t, b = PP.margin_targets(.20, .19, .21, 'mature')
        self.assertAlmostEqual(t['bear'], .20 - self.MS)                            # the stage spread is the worse
        self.assertAlmostEqual(t['bull'], .20 + .75 * self.MS)
        self.assertEqual((b['bear'], b['bull']), ('stage_spread', 'stage_spread'))
        t, b = PP.margin_targets(.20, .19, .26, 'mature')
        self.assertEqual((t['bull'], b['bull']), (.26, 'own_high'))

    def test_path_converges_over_the_stage_years_and_floors(self):
        m, fl = PP.margin_path_v2(.30, .15, 'mature', years=6)                       # conv 4: a quarter of the way a year
        for got, want in zip(m, [.2625, .225, .1875, .15, .15, .15]):
            self.assertAlmostEqual(got, want)
        self.assertFalse(fl)
        self.assertEqual(PP.margin_path_v2(.30, .30, 'mature', years=3)[0], [.30] * 3)   # the base is held exactly
        t, _ = PP.margin_targets(.015, .005, .02, 'mature')                          # thin: the bear's target is half of 1.5%
        self.assertAlmostEqual(t['bear'], .0075)
        m, fl = PP.margin_path_v2(.015, t['bear'], 'mature', years=5)
        self.assertTrue(fl)
        self.assertEqual(m[3:], [PP.M_FLOOR] * 2)

    def test_floor_applies_to_every_path_as_spec1(self):
        # section 10, clarification 3: a 0.5% margin -- the base is lifted to the 1% floor like the bear and the bull (flagged),
        # otherwise the floored bear would sit above the base
        out = PP.price_path(anch2(m=.005, m_lo=.002, m_hi=.008, price=20. * .005 * 5.3), spec_version=2, cross_section=flat_table())
        for s in PP.SCEN:
            self.assertEqual({y['margin'] for y in out['scenarios'][s]['years']}, {PP.M_FLOOR})
            self.assertIn(f'margin_floored_{s}', out['flags'])
        self.assertTrue(out['ordering_checked'])

    def test_short_history_uses_the_stage_spread(self):
        a = PP.anchors(*company(margins=(.10, .12)), 50., .01, .05, spec_version=2)
        self.assertIn('margin_history_short', a['flags'])
        self.assertAlmostEqual(a['m_lo'], .30 - self.MS)
        self.assertAlmostEqual(a['m_hi'], .30 + self.MS)
        self.assertIsNone(a['m_mid_diagnostic'])
        x = PP.anchors(*company(ttm_margin=-.01), 50., .01, .05, spec_version=2)
        self.assertEqual(x['reasons'], ['TTM net income not positive'])


# ---------------------------------------------------------------- 3.2 growth: class quantiles, one half-life, ordering

class GrowthTargetTests(unittest.TestCase):
    def test_class_quantiles_from_the_bins(self):
        f1 = MEGA[1][0]                                                              # > $50bn, 1 year: sums to 100
        self.assertAlmostEqual(PP.class_quantile(f1, .25), -.05 + .05 * 1.4 / 16.9)    # -4.59% real: inside the -5..0% bin
        self.assertAlmostEqual(PP.class_quantile(f1, .75), .05 + .05 * 12.7 / 15.)     # +9.23% real
        self.assertAlmostEqual(PP.class_quantile(f1, .5), .05 * 9.5 / 21.8)            # 2.18%; the book prints 2.1
        self.assertAlmostEqual(base_rates.share_at_least(f1, PP.class_quantile(f1, .25)), .75)   # the inverse of share_at_least
        self.assertAlmostEqual(base_rates.share_at_least(f1, PP.class_quantile(f1, .75)), .25)

    def test_targets_are_nominal_interpolated_and_by_size_class(self):
        p = {h: (PP.class_quantile(MEGA[h][0], .25), PP.class_quantile(MEGA[h][0], .75)) for h in (1, 3, 5, 10)}
        self.assertAlmostEqual(PP.class_target(MEGA, 1, 'bear'), p[1][0] + PP.PI)
        self.assertAlmostEqual(PP.class_target(MEGA, 1, 'bull'), p[1][1] + PP.PI)
        self.assertAlmostEqual(PP.class_target(MEGA, 1, 'base'), .021 + PP.PI)       # the printed median, as spec 1
        self.assertAlmostEqual(PP.class_target(MEGA, 2, 'bear'), (p[1][0] + p[3][0]) / 2 + PP.PI)
        self.assertAlmostEqual(PP.class_target(MEGA, 4, 'bull'), (p[3][1] + p[5][1]) / 2 + PP.PI)
        self.assertAlmostEqual(PP.class_target(MEGA, 12, 'bear'), p[10][0] + PP.PI)  # flat after the last horizon
        self.assertAlmostEqual(PP.class_target(MEGA, 1, 'bear'), -.025858, places=6)
        self.assertAlmostEqual(PP.class_target(MEGA, 1, 'bull'), .112333, places=6)
        self.assertAlmostEqual(PP.class_target(SMALL, 1, 'bear'), .052517, places=6)  # a small company's p25 is a mega's p75
        self.assertAlmostEqual(PP.class_target(SMALL, 1, 'bull'), .300488, places=6)

    def test_targets_are_clamped_around_the_printed_median(self):
        one_bin = (0.,) * 6 + (100.,) + (0.,) * 9                                     # every observation in [0, 5%)
        table = {h: (one_bin, -5.) for h in (1, 3, 5, 10)}                           # a printed median the bins contradict
        self.assertAlmostEqual(PP.class_target(table, 1, 'base'), -.03)
        self.assertAlmostEqual(PP.class_target(table, 1, 'bear'), -.03)              # p25 (1.25% + PI) clamped down to the median
        self.assertAlmostEqual(PP.class_target(table, 1, 'bull'), .0375 + PP.PI)


class GrowthPathTests(unittest.TestCase):
    def test_every_scenario_fades_at_the_stage_half_life_to_its_own_target(self):
        hl = STAGE['mature']['half_life']
        for s, g1 in (('bear', .05 - .03), ('base', .05), ('bull', .05 + .03)):
            g, labels, targets, bound = PP.growth_path_v2(g1, 'mature', s, 100e9, CPI, years=6)
            self.assertEqual(set(labels), {'>$50,000m'})
            self.assertEqual(bound, 0)
            excess = g1 - PP.class_target(MEGA, 1, s)
            for h in range(1, 7):
                self.assertAlmostEqual(targets[h - 1], PP.class_target(MEGA, h, s))
                self.assertAlmostEqual(g[h - 1], PP.class_target(MEGA, h, s) + excess * .5 ** ((h - 1) / hl))   # HL_SCALE dropped

    def test_bull_is_floored_at_the_base_when_it_crosses_into_a_lower_median_class(self):
        base = PP.growth_path_v2(0., 'growth', 'base', 300e6, CPI)[0]
        raw, raw_labels, _, _ = PP.growth_path_v2(.06, 'growth', 'bull', 300e6, CPI)
        self.assertEqual(raw_labels[2], '$325-700m')                                  # the bull grew out of the $0-325m class
        self.assertLess(raw[2], base[2])                                              # 2.65% < 5.60%: inverted without the bound
        g, _, _, bound = PP.growth_path_v2(.06, 'growth', 'bull', 300e6, CPI, floor=base)
        self.assertEqual(g[2], base[2])
        self.assertEqual(g[:2], raw[:2])
        self.assertEqual(bound, 3)
        self.assertTrue(all(x >= y for x, y in zip(g, base)))

    def test_bear_is_capped_at_the_base(self):
        base = PP.growth_path_v2(.05, 'mature', 'base', 320e6, CPI)[0]
        raw = PP.growth_path_v2(.02, 'mature', 'bear', 320e6, CPI)[0]
        self.assertGreater(raw[1], base[1])                                           # 3.55% > 2.63% in year 2
        g, _, _, bound = PP.growth_path_v2(.02, 'mature', 'bear', 320e6, CPI, cap=base)
        self.assertEqual(g[1], base[1])
        self.assertEqual(bound, 2)
        self.assertTrue(all(x <= y for x, y in zip(g, base)))

    def test_the_bound_feeds_the_price_path(self):
        out = PP.price_path(anch2(m=.10, rev=300e6, g_run=0., g_raw=0., stage='growth', price=10. * .10 * .3),
                            spec_version=2, cross_section=flat_table())
        self.assertIn('growth_ordered_bull', out['flags'])
        y = {s: out['scenarios'][s]['years'] for s in PP.SCEN}
        self.assertEqual(y['bull'][2]['growth'], y['base'][2]['growth'])
        self.assertTrue(out['ordering_checked'])

    def test_ceiling_is_measured_on_the_bounded_path(self):
        lam, share = PP.shrink_v2(.60, 'growth', 'base', 100e9, CPI, PP.CEIL['base'])
        self.assertLess(lam, 1.)
        self.assertAlmostEqual(share, PP.CEIL['base'], delta=1e-3)
        self.assertEqual(PP.shrink_v2(.60, 'growth', 'bear', 100e9, CPI, PP.CEIL['bear'])[0], 1.)


# ---------------------------------------------------------------- 3.4 the cross-section table and its fallback ladder

class CrossSectionTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual([PP.growth_bucket(g) for g in (-.0001, 0., .0499, .05, .10, .20, .2999, .30, 2.)],
                         ['<0%', '0-5%', '0-5%', '5-10%', '10-20%', '20-30%', '20-30%', '>30%', '>30%'])
        self.assertEqual([PP.margin_bucket(m) for m in (.01, .05, .10, .1999, .20, .9)], ['<5%', '5-10%', '10-20%', '10-20%', '>20%', '>20%'])

    def test_quantiles_and_pe_range(self):
        pes = [10. + k for k in range(20)]                                             # 10 .. 29
        t = cs_table({('5-10%', '10-20%'): pes}, extra=[{'symbol': 'BAD1', 'g_run': .07, 'm_ttm': .15, 'pe': .5},
                                                       {'symbol': 'BAD2', 'g_run': .07, 'm_ttm': .15, 'pe': 600.}])
        self.assertEqual((t['n'], t['n_outside_pe_range']), (20, 2))                  # unit defects and near-zero earnings out
        for q, want in ((.25, 14.75), (.50, 19.5), (.75, 24.25)):
            m, cell = PP.cs_multiple(t, .07, .15, q)
            self.assertAlmostEqual(m, want)
            self.assertEqual(cell, {'growth_bucket': '5-10%', 'margin_bucket': '10-20%', 'level': 'cell', 'n': 20, 'q': q})

    def test_fallback_ladder(self):
        t = cs_table({('5-10%', '10-20%'): [20.] * 19, ('5-10%', '>20%'): [30.] * 5,            # cell 19 < CELL_MIN, row 24
                      ('<0%', '<5%'): [8.] * 10,                                                  # row of 10 only
                      ('20-30%', '>20%'): [40.] * 20})
        m, cell = PP.cs_multiple(t, .07, .15, .5)
        self.assertEqual((cell['level'], cell['n']), ('growth_row', 24))
        self.assertAlmostEqual(m, 20.)                                                 # median of 19 x 20 and 5 x 30
        self.assertAlmostEqual(PP.cs_multiple(t, .07, .15, .8)[0], 20. + 10. * .4)     # (n - 1) x .8 = 18.4: 40% from 20 to 30
        m, cell = PP.cs_multiple(t, -.05, .03, .5)
        self.assertEqual((cell['level'], cell['n']), ('cross_section', 54))           # the whole table
        self.assertAlmostEqual(m, PP.quantile(t['all'], .5))
        m, cell = PP.cs_multiple(t, .25, .30, .5)
        self.assertEqual((cell['level'], m), ('cell', 40.))
        m, cell = PP.cs_multiple(t, .15, .30, .5)                                     # a bucket with no row at all
        self.assertEqual(cell['level'], 'cross_section')

    def test_table_is_deterministic_and_point_in_time(self):
        ents = [{'symbol': f'S{k}', 'g_run': .01 * k, 'm_ttm': .02 + .01 * k, 'pe': 5. + k} for k in range(40)]
        a = PP.cross_section_table(ents, '2020-06-30', 'synthetic')
        random.Random(7).shuffle(ents)
        self.assertEqual(PP.cross_section_table(ents, '2020-06-30', 'synthetic')['sha256'], a['sha256'])
        with self.assertRaises(ValueError):
            PP.cross_section_table([{'symbol': 'X', 'g_run': 0., 'm_ttm': .1, 'pe': 900.}], '2020-06-30', 'synthetic')
        with self.assertRaises(ValueError):                                            # a table from after as_of is look-ahead
            PP.price_path(anch2(as_of='2019-06-30'), spec_version=2, cross_section=flat_table())
        with self.assertRaises(ValueError):
            PP.price_path(anch2(), spec_version=2)
        tables = {'2019-07-01': dict(a, as_of='2019-06-30'), '2020-07-01': a}
        self.assertEqual(PP.latest_cross_section(tables, '2020-01-15')['as_of'], '2019-06-30')
        self.assertEqual(PP.latest_cross_section(tables, '2026-06-30')['as_of'], '2020-06-30')
        with self.assertRaises(ValueError):
            PP.latest_cross_section(tables, '2018-12-31')

    def test_entry_is_the_raw_trailing_pe(self):
        e = PP.cross_section_entry(anch2(), 'SYN')
        self.assertAlmostEqual(e['pe'], 85.7 * 1e9 / 2.65e9)
        self.assertEqual((e['g_run'], e['m_ttm']), (.24, .5))


# ---------------------------------------------------------------- 3.5 the log premium, its clip and the multiple range

class PremiumTests(unittest.TestCase):
    def test_log_premium_halves_at_the_stage_half_life_toward_the_class_quantile(self):
        out = PP.price_path(anch2(), spec_version=2, cross_section=flat_table(20.))
        m0 = 85.7 / 2.65                                                              # 32.3x raw trailing
        self.assertAlmostEqual(out['multiple_today'], m0, places=5)
        self.assertAlmostEqual(out['premium_log'], math.log(m0 / 20.), places=6)
        self.assertNotIn('premium_clipped', out['flags'])
        hl = STAGE['growth']['half_life']
        for s in PP.SCEN:                                                             # every quantile of a flat table is 20x
            for y in out['scenarios'][s]['years']:
                self.assertAlmostEqual(y['premium_log_left'], math.log(m0 / 20.) * .5 ** (y['year'] / hl), places=6)
                self.assertAlmostEqual(y['multiple'], 20. * (m0 / 20.) ** (.5 ** (y['year'] / hl)), places=5)
        self.assertAlmostEqual(out['scenarios']['base']['years'][4]['multiple'], 20. * (m0 / 20.) ** (.5 ** 2), places=5)   # 22.5x

    def test_premium_is_clipped_at_three_times_the_class_median(self):
        out = PP.price_path(anch2(price=2.65 * 200), spec_version=2, cross_section=flat_table(20.))   # 200x vs a 20x class
        self.assertAlmostEqual(out['premium_log_raw'], math.log(10.), places=6)
        self.assertAlmostEqual(out['premium_log'], math.log(3.), places=6)
        self.assertIn('premium_clipped', out['flags'])
        self.assertAlmostEqual(out['scenarios']['base']['years'][0]['multiple'], 20. * 3. ** (.5 ** (1 / 2.5)), places=4)
        low = PP.price_path(anch2(price=2.65 * 2), spec_version=2, cross_section=flat_table(20.))      # 2x: a tenth
        self.assertAlmostEqual(low['premium_log'], -math.log(3.), places=6)

    def test_multiple_range_is_five_to_120(self):
        hi = PP.price_path(anch2(price=2.65 * 300), spec_version=2, cross_section=flat_table(100.))   # 100x class, L_0 = ln 3
        self.assertEqual(hi['scenarios']['base']['years'][0]['multiple'], PP.PE_CAP_V2)
        self.assertIn('multiple_capped_base', hi['flags'])
        lo = PP.price_path(anch2(price=2.65 * 1.1), spec_version=2, cross_section=flat_table(3.))      # 3x class
        self.assertEqual(lo['scenarios']['bull']['years'][4]['multiple'], PP.PE_FLOOR_V2)
        self.assertIn('multiple_floored_bull', lo['flags'])
        self.assertEqual((PP.PE_FLOOR_V2, PP.PE_CAP_V2, PP.CELL_MIN, PP.CS_PE_RANGE), (5., 120., 20, (1., 500.)))

    def test_targets_are_the_scenario_quantiles_of_the_year_h_cell(self):
        t = spread_table()
        out = PP.price_path(anch2(), spec_version=2, cross_section=t)
        for s in PP.SCEN:
            for y in out['scenarios'][s]['years']:
                c = y['cross_section']
                want, cell = PP.cs_multiple(t, y['growth'], y['margin'], PP.CS_Q[s])
                self.assertEqual((c['growth_bucket'], c['margin_bucket'], c['level'], c['q']), (cell['growth_bucket'], cell['margin_bucket'], 'cell', PP.CS_Q[s]))
                self.assertEqual(c['n'], 25)
                self.assertAlmostEqual(c['target'], want, places=5)
        base = out['scenarios']['base']['years']
        self.assertEqual(base[0]['cross_section']['growth_bucket'], '20-30%')        # 24% in year 1
        self.assertEqual(base[4]['cross_section']['growth_bucket'], '10-20%')        # faded down the ladder: the target falls


# ---------------------------------------------------------------- 3.6 ordering by construction, asserted

class OrderingTests(unittest.TestCase):
    def test_prices_and_drivers_are_ordered_every_year(self):
        for a in (anch2(), anch2(stage='mature', g_run=-.25, g_raw=-.25, m=.03, m_lo=-.02, m_hi=.05, price=1.),
                  anch2(stage='early', g_run=.60, g_raw=.60, rev=50e6, m=.02, price=200 * .02 * 50e6 / 1e9)):
            out = PP.price_path(a, spec_version=2, cross_section=spread_table())
            self.assertTrue(out['ordering_checked'])
            for h in range(5):
                bear, mid, bull = (out['scenarios'][s]['years'][h] for s in PP.SCEN)
                for k in PP.ORDER_KEYS:
                    self.assertLessEqual(bear[k], mid[k], (k, h))
                    self.assertLessEqual(mid[k], bull[k], (k, h))

    def test_bear_multiple_never_above_the_base(self):
        # high-margin cells pay 10-20x, every lower-margin cell 30-50x: the bear's margin falls into the 10-20% bucket and its
        # own p25 there (35x) is above the base's median (15x); the construction takes the base's
        cells = {(g, m): ([10. + j * 10. / 24 for j in range(25)] if m == '>20%' else [30. + j * 20. / 24 for j in range(25)])
                 for g in G_BUCKETS for m in M_BUCKETS}
        out = PP.price_path(anch2(m=.21, m_lo=.10, m_hi=.25, price=15. * .21 * 5.3), spec_version=2, cross_section=cs_table(cells))
        y = {s: out['scenarios'][s]['years'] for s in PP.SCEN}
        self.assertEqual(y['bear'][0]['cross_section']['margin_bucket'], '10-20%')
        self.assertGreater(y['bear'][0]['multiple_before_ordering'], y['base'][0]['multiple'])
        self.assertEqual(y['bear'][0]['multiple'], y['base'][0]['multiple'])
        self.assertIn('multiple_ordered_bear', out['flags'])

    def test_a_violation_is_an_error_not_a_flag(self):
        good = {s: [{'growth': g, 'margin': .1, 'multiple': 20., 'price': p}] for s, g, p in (('bear', .01, 9.), ('base', .02, 10.), ('bull', .03, 11.))}
        self.assertTrue(PP.check_order(good))
        bad = dict(good, bear=[{'growth': .01, 'margin': .1, 'multiple': 20., 'price': 10.5}])
        with self.assertRaises(PP.OrderingError):
            PP.check_order(bad)
        real = PP.margin_path_v2

        def inverted(m_ttm, target, stage, years=PP.PATH_YEARS, m1=None):          # a bear whose margin rises: a broken construction
            return real(m_ttm, m_ttm + .05 if target < m_ttm else target, stage, years, m1)
        with mock.patch.object(PP, 'margin_path_v2', inverted):
            with self.assertRaises(PP.OrderingError) as cm:
                PP.price_path(anch2(), spec_version=2, cross_section=flat_table())
        self.assertIn('margin', str(cm.exception))
        self.assertTrue(issubclass(PP.OrderingError, AssertionError))

    def test_guided_margin_keeps_the_order(self):
        guided = {'guidance': {'margin_fy1': {'value': .70, 'evidence_ids': ['e1'], 'issued_at': '2026-05-01'},
                               'revenue_growth_fy1': {'value': .30, 'evidence_ids': ['e1'], 'issued_at': '2026-05-01'}}}
        out = PP.price_path(anch2(), guided, spec_version=2, cross_section=flat_table())
        y1 = {s: out['scenarios'][s]['years'][0] for s in PP.SCEN}
        self.assertEqual(y1['base']['margin'], .70)                                    # section 5: guided m_1 in the base
        self.assertEqual(y1['bull']['margin'], .70)                                    # the bull's year 1 is not below it
        self.assertLess(y1['bear']['margin'], .70)
        self.assertEqual(out['years_basis'][1], 'guided')
        self.assertTrue(out['live_inputs_used'])


# ---------------------------------------------------------------- 3.6 diagnostics and hashing

class DiagnosticsTests(unittest.TestCase):
    def test_output_carries_the_registered_diagnostics(self):
        t = spread_table()
        out = PP.price_path(anch2(), spec_version=2, cross_section=t)
        self.assertEqual(out['spec_version'], 2)
        self.assertEqual(out['cross_section'], {'as_of': '2026-06-30', 'source': 'synthetic cross-section', 'n': t['n'], 'sha256': t['sha256']})
        self.assertEqual(out['cross_section_today']['level'], 'cell')
        g = out['gordon']
        self.assertAlmostEqual(g['today']['ratio_to_cross_section'], g['today']['multiple'] / out['cross_section_today']['multiple'], places=5)
        self.assertEqual(len(g['years']), 5)
        self.assertAlmostEqual(g['years'][0]['ratio_to_cross_section'], g['years'][0]['multiple'] / g['years'][0]['cross_section'], places=5)
        # the Gordon multiple is spec 1's justified multiple on the v2 base path, never in a price
        a = anch2()
        self.assertLessEqual(g['today']['multiple'], PP.PE_CAP)
        self.assertEqual(out['margin_history']['m_lo'], .40)
        self.assertEqual(out['margin_history']['m_hi'], .55)
        self.assertEqual(out['margin_history']['bear_target'], .40)                   # own low 40% vs TTM - 5.53%: the own low
        self.assertEqual(out['margin_history']['bull_basis'], 'own_high')
        self.assertTrue(out['ordering_checked'])
        self.assertEqual(out['table_status'], 'not_shown')
        self.assertTrue(out['verdict'].startswith('FAIL'))
        for s in ('measured on the 2016-2025', 'never authorize a trade', 'not a measured confidence interval'):
            self.assertIn(s, out['band_statement'].lower())
        self.assertEqual(PP.price_path(a, spec_version=2, cross_section=t)['output_sha256'], out['output_sha256'])   # deterministic
        t2 = spread_table(n=26)
        self.assertNotEqual(PP.price_path(a, spec_version=2, cross_section=t2)['input_sha256'], out['input_sha256'])   # the table is an input
        self.assertEqual(out['scenarios']['bear']['half_life'], out['scenarios']['bull']['half_life'])

    def test_the_registered_run_is_recorded_and_not_shown(self):
        """docs/PRICE_PATH_V2_RESULTS_2026-10-25.md: the single run of section 6.1 (25.10.2026) FAILED S2 at 3 / 5 years and S1;
        S3 and S4 passed. Section 7: FAIL -> recorded in docs, not shown; the band statement carries the measured error."""
        self.assertEqual(sorted(PP.MEASURED_V2), [1, 3, 5])
        self.assertEqual({h: m['n'] for h, m in PP.MEASURED_V2.items()}, {h: m['n'] for h, m in PP.MEASURED.items()})  # same common sample
        self.assertLessEqual(abs(PP.MEASURED_V2[1]['bias_median']), .10)                     # S2 held at 1 year only
        for h in (3, 5):
            m = PP.MEASURED_V2[h]
            self.assertGreater(abs(m['bias_median']), .10)                                    # S2 failed: that is the record
            self.assertLess(m['mdale'], PP.MEASURED[h]['mdale'])                              # better than spec 1 ...
            self.assertGreater(m['mdale'], PP.MEASURED_BASELINES['engine_irr'][h]['mdale'])   # ... but not than the engine IRR path
            self.assertGreaterEqual(m['within_band'], .50)                                    # S3 held (a wide band)
            self.assertLessEqual(max(m['below_bear'], m['above_bull']), PP.S3_TAIL_V2)
        for h, m in PP.MEASURED_V2.items():
            self.assertGreater(m['mdale'], PP.MEASURED_BASELINES['drift_7'][h]['mdale'] + .03)   # S1's mechanical leg failed
            self.assertAlmostEqual(m['within_band'] + m['below_bear'] + m['above_bull'], 1., delta=.002)
        self.assertTrue(PP.MEASURED_VERDICT_V2.startswith('FAIL'))
        self.assertEqual(PP.TABLE_STATUS_V2, 'not_shown')
        out = PP.price_path(anch2(), spec_version=2, cross_section=spread_table())
        self.assertEqual((out['table_status'], out['measured_error']), ('not_shown', PP.MEASURED_V2))
        for s in ('FAIL', '2016-2025', 'within +-25% in 53%', 'median bias -0.12', 'median bias -0.21', 'never authorize a trade'):
            self.assertIn(s, out['band_statement'])
        self.assertNotIn('untested', out['band_statement'])
        self.assertEqual(PP.price_path(anch2(), spec_version=2, cross_section=spread_table())['output_sha256'], out['output_sha256'])

    def test_recorded_numbers_are_the_saved_run(self):
        """MEASURED_V2 is read off the run's summary.json (kept in Git), rounded to 3 decimals, never typed independently."""
        path = os.path.join(ROOT, 'runs', 'backtest', 'pricepath_v2', 'summary.json')
        if not os.path.exists(path):
            self.skipTest('runs/backtest/pricepath_v2/summary.json not present')
        with open(path) as f:
            s = json.load(f)
        self.assertEqual((s['spec_version'], s['verdict']['verdict'], s['diagnostics']['ordering_assertions']), (2, 'FAIL', 0))
        self.assertEqual((s['verdict']['S1_accuracy'], s['verdict']['S2_bias'], s['verdict']['S3_band'], s['verdict']['S4_attribution']),
                         (False, False, True, True))
        for h, m in PP.MEASURED_V2.items():
            c = s['horizons'][str(h)]['common']
            got = dict(c['metrics']['lane_base'], **c['band'])
            self.assertEqual(m['n'], got['n'])
            for k in ('bias_median', 'mdale', 'within25', 'within_band', 'below_bear', 'above_bull'):
                self.assertAlmostEqual(m[k], got[k], delta=.0005, msg=(h, k))
            for name, by_h in PP.MEASURED_BASELINES.items():                                # same common sample, same baselines
                self.assertAlmostEqual(by_h[h]['mdale'], c['metrics'][name]['mdale'], delta=.0005, msg=(h, name))
            self.assertAlmostEqual(PP.MEASURED[h]['mdale'], c['metrics']['lane_v1']['mdale'], delta=.0005)


# ---------------------------------------------------------------- sections 6-7: evaluation

def _row2(sym, formation, price, base, bear=None, bull=None, rev=100., margin=.1, shares=1., m0=20., cell=('5-10%', '10-20%'), **extra):
    """An invented v2 row: year h has revenue, margin, shares and the multiple that makes price = rev x margin / shares x multiple."""
    def years(ps):
        return [{'year': h + 1, 'price': p, 'revenue': rev, 'margin': margin, 'shares': shares, 'multiple': p * shares / (rev * margin),
                 'growth': .05, 'cross_section': {'level': 'cell'}} for h, p in enumerate(ps)]
    r = {'symbol': sym, 'formation': formation, 'as_of': formation[:4] + '-06-30', 'status': 'ok', 'price': price, 'stage': 'mature',
         'cyclical': False, 'size_class': '>$50,000m', 'multiple_today': m0, 'premium_log': 0., 'premium_log_raw': 0.,
         'cross_section_today': {'growth_bucket': cell[0], 'margin_bucket': cell[1], 'level': 'cell', 'n': 25, 'q': .5},
         'gordon': {'today': {'ratio_to_cross_section': .6}, 'years': [{'ratio_to_cross_section': .55}] * 5},
         'margin_history': {'bear_basis': 'own_low', 'bull_basis': 'stage_spread'},
         'scenarios': {'base': {'lambda': 1., 'base_rate_share_5y': .4, 'years': years(base)},
                       'bear': {'lambda': 1., 'base_rate_share_5y': .6, 'years': years(bear or [p * .7 for p in base])},
                       'bull': {'lambda': 1., 'base_rate_share_5y': .1, 'years': years(bull or [p * 1.4 for p in base])}},
         'anchors': {'revenue': rev, 'net_income': rev * margin, 'shares': shares},
         'flags': [], 'eps_cagr_3y': .05, 'dividend_yield': 0.}
    r.update(extra)
    return r


def _v1_row(sym, formation, price, base, rev=100., margin=.1, shares=1.):
    """A spec-1 row (the test_pricepath helper) whose years carry revenue, margin, shares and the consistent multiple."""
    r = T1._row(sym, formation, price, [base] * 5, [base * .8] * 5, [base * 1.4] * 5)
    for z in r['scenarios'].values():
        for y in z['years']:
            y.update(revenue=rev, margin=margin, shares=shares, multiple=y['price'] * shares / (rev * margin))
    return r


class EvaluateV2Tests(unittest.TestCase):
    def test_attribution_identity_with_a_split(self):
        lane = _row2('A', '2020-07-01', 100., [99.] * 5, rev=110., margin=.10, shares=1.)['scenarios']['base']['years']
        later = {'price': 120., 'anchors': {'revenue': 100., 'net_income': 12., 'shares': 1.}}
        t = PP.attribution_terms(lane, 1, 100., 1.2, later)
        self.assertAlmostEqual(t['e_rev'], math.log(1.1))
        self.assertAlmostEqual(t['e_margin'], math.log(.10 / .12))
        self.assertAlmostEqual(t['e_shares'], 0.)
        self.assertAlmostEqual(t['e_multiple'], math.log(9. / 10.))                    # lane 9x vs realized 120 x 1 / 12 = 10x
        self.assertAlmostEqual(t['e_total'], math.log(99. / 120.))
        self.assertAlmostEqual(t['residual'], 0.)
        split = PP.attribution_terms(lane, 1, 100., 1.2, {'price': 60., 'anchors': {'revenue': 100., 'net_income': 12., 'shares': 2.}})
        for k in PP.ATTR_TERMS:                                                       # a 2:1 split between t and t + 1 changes nothing
            self.assertAlmostEqual(split[k], t[k])
        self.assertIsNone(PP.attribution_terms(lane, 1, 100., 1.2, {'price': 120., 'anchors': {'revenue': 100., 'net_income': -1., 'shares': 1.}}))

    def _sample(self):
        """Formation 2020, h = 1: realized x1.2 for every name; lane base 120 (exact) for A-D, 150 for E; v1 at 100."""
        prices = {'SPY': T1._px({'2020-06': 100., '2021-06': 110.})}
        rows, v1_rows, engine = [], [], []
        for s in 'ABCDE':
            prices[s] = T1._px({'2020-06': 100., '2021-06': 120.})
            rows.append(_row2(s, '2020-07-01', 100., [150. if s == 'E' else 120.] * 5, rev=110., margin=.1,
                              m0=.5 if s == 'D' else 20., cell=('>30%', '>20%') if s == 'C' else ('5-10%', '10-20%')))
            rows.append(_row2(s, '2021-07-01', 120., [130.] * 5, rev=100., margin=.12))   # the realized side at t + 1
            v1_rows.append(_v1_row(s, '2020-07-01', 100., 100.))
            engine.append({'symbol': s, 'formation': '2020-07-01', 'base_irr': .07})
        v1_rows = [r for r in v1_rows if r['symbol'] != 'B']                           # B has no spec-1 path
        rows.append({'symbol': 'X', 'formation': '2020-07-01', 'as_of': '2020-06-30', 'status': 'error',
                     'reasons': ['OrderingError: ordering violated in year 2: price bear 1 / base 0.9 / bull 2']})
        return rows, prices, engine, v1_rows

    def test_lane_v1_baseline_attribution_and_robustness(self):
        rows, prices, engine, v1_rows = self._sample()
        res = PP.evaluate_v2(rows, prices, engine, v1_rows)
        h1 = res['horizons'][1]
        self.assertEqual(h1['excluded'], {'no lane path': 1, 'no realized price': 5, 'no engine base_irr': 0,    # the 2021 rows
                                          'no trailing 3-year EPS CAGR': 0, 'no lane_v1 path': 1})
        self.assertEqual(h1['common']['n'], 4)                                         # A, C, D, E (B lacks lane_v1)
        m = h1['common']['metrics']
        self.assertAlmostEqual(m['lane_v1']['bias_median'], math.log(100. / 120.))
        self.assertAlmostEqual(m['lane_base']['bias_median'], 0.)                       # three exact, E 25% high
        self.assertEqual(h1['common']['head_to_head']['lane_v1']['share_lane_closer'], .75)
        self.assertIn('>30% x >20%', h1['common']['by']['cross_section_cell'])
        rob = h1['common']['defect_rows_removed']
        self.assertEqual((rob['n'], rob['removed']), (3, ['D 2020']))                  # raw M_0 0.5x is a unit defect row
        a = res['attribution'][1]
        self.assertEqual((a['n'], a['n_no_usable_row_at_t_plus_h']), (4, 0))
        self.assertAlmostEqual(a['lane_base']['e_rev']['median'], math.log(1.1))
        self.assertAlmostEqual(a['lane_base']['e_margin']['median'], math.log(.10 / .12))
        self.assertLess(a['lane_base']['residual_max_abs'], 1e-12)
        self.assertAlmostEqual(a['lane_v1']['e_total']['median'], math.log(100. / 120.))
        self.assertIn('cross_section_cell', a['by'])
        self.assertEqual(res['attribution'].get(5), None)                               # 2020 has no realized 5-year price here
        d = res['diagnostics']
        self.assertEqual(d['ordering_assertions'], 1)                                   # must be 0 in a real run: counted, never hidden
        self.assertEqual(d['status_counts'], {'ok': 10, 'excluded': 0, 'error': 1})
        self.assertEqual(d['cell_usage']['formation']['cell'], 1.)
        self.assertEqual(res['verdict']['verdict'], 'FAIL')                             # h = 3 / 5 untestable: S2 not a pass

    def test_verdict_rules(self):
        def hz(lane=.30, v1=.40, bias=.05, within=.6, below=.2, above=.2):
            mets = {'lane_base': {'mdale': lane, 'bias_median': bias}, 'analyst_const': {'mdale': .40}, 'engine_irr': {'mdale': .35},
                    'lane_v1': {'mdale': v1}, 'random_walk': {'mdale': .30}, 'drift_7': {'mdale': .28}}
            return {h: {'common': {'metrics': mets, 'band': {'n': 100, 'within_band': within, 'below_bear': below, 'above_bull': above}}} for h in (1, 3, 5)}

        def attr(rev=.02, margin=-.05, mult=.08):
            return {3: {'lane_base': {'n': 50, 'e_rev': {'median': rev}, 'e_margin': {'median': margin}, 'e_multiple': {'median': mult}}}}
        self.assertEqual(PP._verdict_v2(hz(), attr())['verdict'], 'PASS')
        v = PP._verdict_v2(hz(above=.32), attr())                                       # a 32% tail: spec 1's 35% passes it, spec 2 does not
        self.assertEqual((v['verdict'], v['S3_band']), ('PARTIAL', False))
        v = PP._verdict_v2(hz(v1=.29), attr())                                          # not better than lane_v1: S1 fails
        self.assertEqual((v['verdict'], v['S1_accuracy']), ('PARTIAL', False))
        v = PP._verdict_v2(hz(), attr(margin=-.11))                                     # offsetting errors: S4 fails -> FAIL
        self.assertEqual((v['verdict'], v['S4_attribution']), ('FAIL', False))
        v = PP._verdict_v2(hz(bias=-.11), attr())
        self.assertEqual((v['verdict'], v['S2_bias']), ('FAIL', False))
        self.assertEqual(PP._verdict_v2(hz(), {})['S4_attribution'], False)             # untestable is not a pass


# ---------------------------------------------------------------- the runner's two passes (scripts/backtest_pricepath.py --spec 2)

def _script():
    spec = importlib.util.spec_from_file_location('backtest_pricepath_v2_test', os.path.join(ROOT, 'scripts', 'backtest_pricepath.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunnerTests(unittest.TestCase):
    def _anchor_rows(self):
        rows = []
        for k in range(30):
            g, m = (-.10, .01, .07, .15, .25, .45)[k % 6], (.03, .07, .15, .30)[k % 4]
            a = anch2(m=m, rev=1e9 * (1 + k), price=(12. + k) * m * (1 + k), as_of='2020-06-30', ticker=f'S{k:02d}', g_run=g, g_raw=g,
                      m_lo=m * .6, m_hi=m * 1.3, stage='mature')
            rows.append({'symbol': f'S{k:02d}', 'formation': '2020-07-01', 'as_of': '2020-06-30', 'price': a['price'], 'status': 'ok',
                         'stage': 'mature', 'cyclical': False, 'eps_cagr_3y': .05, 'dividend_yield': 0., 'anchors': a})
        rows.append({'symbol': 'LOSS', 'formation': '2020-07-01', 'as_of': '2020-06-30', 'status': 'excluded', 'reasons': ['TTM net income not positive']})
        rows.append({'symbol': 'BROKEN', 'formation': '2020-07-01', 'as_of': '2020-06-30', 'price': 10., 'status': 'ok',
                     'anchors': anch2(as_of='2020-06-30', stage='mature', m_lo=None)})
        return rows

    def test_pass_two_builds_the_table_from_every_company_then_the_paths(self):
        mod = _script()
        arows = self._anchor_rows()
        out, table = mod.formation_paths(list(reversed(arows)), lambda as_of: mod.table_source('test', as_of))
        self.assertEqual(table['n'], 31)                                               # every ok anchor, the broken one included
        self.assertEqual(table['source'], 'universe QUEUE_test: spec-2 anchors of every company at 2020-06-30 (pass 1 of scripts/backtest_pricepath.py --spec 2)')
        self.assertEqual([r['symbol'] for r in out], sorted(r['symbol'] for r in arows))   # deterministic order
        by = {r['symbol']: r for r in out}
        self.assertEqual(by['LOSS']['status'], 'excluded')
        self.assertEqual(by['BROKEN']['status'], 'error')                               # recorded, never hidden
        self.assertTrue(by['BROKEN']['reasons'][0].startswith('TypeError'))
        ok = [r for r in out if r['status'] == 'ok']
        self.assertEqual(len(ok), 30)
        r = by['S07']
        self.assertEqual(r['cross_section']['sha256'], table['sha256'])
        self.assertNotIn('status', r['anchors'])
        self.assertTrue(r['ordering_checked'])
        for k in mod.ROW_KEYS_V2:
            self.assertIn(k, r)
        self.assertEqual(r['output_sha256'], PP.price_path(arows[7]['anchors'], spec_version=2, cross_section=table)['output_sha256'])
        # the evaluator reads the rows it writes: one more formation as the realized side, invented prices
        nxt = [dict(x, formation='2021-07-01', as_of='2021-06-30', anchors=dict(x['anchors'], as_of='2021-06-30')) for x in arows[:30]]
        out2, table2 = mod.formation_paths(nxt, lambda as_of: mod.table_source('test', as_of))
        prices = {'SPY': T1._px({'2020-06': 100., '2021-06': 110.})}
        prices.update({x['symbol']: T1._px({'2020-06': x['price'], '2021-06': x['price'] * 1.1}) for x in arows[:30]})
        v1 = [_v1_row(x['symbol'], '2020-07-01', x['price'], x['price']) for x in arows[:30]]
        engine = [{'symbol': x['symbol'], 'formation': '2020-07-01', 'base_irr': .07} for x in arows[:30]]
        res = PP.evaluate_v2(out + out2, prices, engine, v1, {'2020-07-01': table, '2021-07-01': table2})
        self.assertEqual(res['horizons'][1]['common']['n'], 30)
        self.assertEqual((res['attribution'][1]['n'], res['attribution'][1]['lane_v1']['n']), (30, 30))
        self.assertLess(res['attribution'][1]['lane_base']['residual_max_abs'], 1e-4)    # rounded outputs: the identity to 1e-4
        self.assertEqual(sorted(res['diagnostics']['tables']), ['2020-07-01', '2021-07-01'])
        self.assertIn('Verdict', mod.report_v2(res, 'test'))

    def test_cli_pass_two_and_evaluation_end_to_end(self):
        """Pass 2 from an anchors file, then `--spec 2 --evaluate-only` on invented Yahoo-format prices: summary and report."""
        import tempfile
        from argparse import Namespace
        from datetime import datetime, timezone
        mod = _script()
        arows = [r for r in self._anchor_rows() if r['symbol'] != 'BROKEN']
        later = [dict(x, formation='2021-07-01', as_of='2021-06-30', anchors=dict(x['anchors'], as_of='2021-06-30'))
                 for x in arows if x['status'] == 'ok']
        syms = [r['symbol'] for r in arows if r['status'] == 'ok']
        with tempfile.TemporaryDirectory() as d:
            out, pdir = os.path.join(d, 'out'), os.path.join(d, 'prices')
            os.makedirs(out)
            os.makedirs(pdir)
            for s in syms:
                os.makedirs(os.path.join(d, 'companies', s))
                with open(os.path.join(d, 'companies', s, 'company.json'), 'w') as f:
                    json.dump({'ticker': s}, f)
            for s in syms + ['SPY']:
                p0 = next((r['price'] for r in arows if r['symbol'] == s), 100.)
                ts = [int(datetime(y, 6, 15, tzinfo=timezone.utc).timestamp()) for y in (2020, 2021, 2022)]
                closes = [p0, p0 * 1.1, p0 * 1.2]
                with open(os.path.join(pdir, f'{s}.json'), 'w') as f:
                    json.dump({'chart': {'result': [{'timestamp': ts, 'indicators': {'quote': [{'close': closes}], 'adjclose': [{'adjclose': closes}]}}]}}, f)
            with open(os.path.join(out, 'anchors.jsonl'), 'w') as f:
                for r in arows + later:
                    f.write(json.dumps(r) + '\n')
            v1 = os.path.join(d, 'v1.jsonl')
            with open(v1, 'w') as f:
                for r in arows:
                    if r['status'] == 'ok':
                        f.write(json.dumps(_v1_row(r['symbol'], '2020-07-01', r['price'], r['price'])) + '\n')
            eng = os.path.join(d, 'engine.jsonl')
            with open(eng, 'w') as f:
                for s in syms:
                    f.write(json.dumps({'symbol': s, 'formation': '2020-07-01', 'base_irr': .07}) + '\n')
            mod._paths_pass(Namespace(workers=1, name='test'), ['2020-07-01', '2021-07-01'], os.path.join(out, 'anchors.jsonl'),
                            os.path.join(out, 'rows.jsonl'), os.path.join(out, 'tables.json'))
            with mock.patch('sys.stdout'):
                mod.main([d, '--name', 'test', '--from', '2020', '--to', '2021', '--prices', pdir, '--out', out, '--spec', '2',
                          '--v1-rows', v1, '--engine-rows', eng, '--evaluate-only', '--workers', '1', '--symbols'] + syms)
            with open(os.path.join(out, 'summary.json')) as f:
                res = json.load(f)
            with open(os.path.join(out, 'REPORT.md')) as f:
                report = f.read()
        self.assertEqual(res['spec_version'], 2)
        self.assertEqual(sorted(res['run']['tables']), ['2020-07-01', '2021-07-01'])
        self.assertEqual(res['run']['n_v1_rows_ok'], 30)
        self.assertEqual(res['horizons']['1']['common']['n'], 30)                    # 2021: no engine or spec-1 row (excluded)
        self.assertEqual(res['horizons']['1']['excluded']['no lane_v1 path'], 30)
        self.assertEqual(res['attribution']['1']['n'], 30)
        self.assertEqual(res['diagnostics']['ordering_assertions'], 0)
        self.assertIn(res['verdict']['verdict'], ('PASS', 'PARTIAL', 'FAIL'))
        for s in ('spec 2', 'Attribution (6.6', 'Ordering assertions (must be 0): **0**', 'S4_h3_attribution_terms_within_0.10'):
            self.assertIn(s, report)

    def test_spec_two_needs_the_v1_rows(self):
        mod = _script()
        with self.assertRaises(SystemExit):
            with mock.patch('sys.stderr'):
                mod.main(['/nonexistent', '--spec', '2'])


if __name__ == '__main__':
    unittest.main()
