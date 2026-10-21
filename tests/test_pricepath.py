"""pricepath: the expected price path lane on SYNTHETIC numbers only (invented companies, not market data). The IBKR-like case
uses figures the owner quoted (2026 EPS 2.65 at a price of 85.7, growth about 24%) as an illustration of the arithmetic, not
as a valuation of Interactive Brokers."""
import unittest

from fundamental_engine import base_rates, facts as F, pricepath as PP
from fundamental_engine.forecast import STAGE

CPI = base_rates.CPI_2015          # revenue stated in 2015 dollars: the size class reads the raw figure


def ibkr_like(**over):
    """Anchors as the owner quoted them: price 85.7, EPS 2.65 (normalized), growth 24% fading; 1bn shares assumed."""
    s0, eps = 1e9, 2.65
    rev = eps * s0 / .50                                     # a 50% net margin puts revenue at 5.3bn
    a = {'status': 'ok', 'as_of': '2026-06-30', 'ticker': 'SYN', 'price': 85.7, 'risk_free': .042, 'erp': .042, 'cpi': base_rates.CPI_NOW,
         'cpi_basis': 'test', 'stage': 'growth', 'cyclical': False, 'revenue': rev, 'net_income': eps * s0, 'shares': s0,
         'g_run': .24, 'g_raw': .24, 'g_basis': 'test', 'g_fiscal_year': .24, 'g_quarter': .24, 'quarter_ref': None,
         'm_ttm': .50, 'm_anchor': .50, 'm_basis': 'test', 'share_cagr': 0., 'roic': .25, 'dividends_ttm': 0., 'dividend_yield': 0.,
         'ttm_period_end': '2026-03-31', 'ttm_method': 'quarters', 'references': {}, 'flags': [], 'notes': []}
    a.update(over)
    return a


class GrowthFadeTests(unittest.TestCase):
    def test_class_median_interpolates_and_is_nominal(self):
        table = dict((c[0], c[3]) for c in base_rates.CLASSES)['>$50,000m']     # medians 2.1 / 1.5 / 1.5 / 1.1 at 1 / 3 / 5 / 10
        self.assertAlmostEqual(PP.class_median(table, 1), .021 + PP.PI)
        self.assertAlmostEqual(PP.class_median(table, 2), .018 + PP.PI)
        self.assertAlmostEqual(PP.class_median(table, 12), .011 + PP.PI)       # flat after the last horizon

    def test_fade_arithmetic(self):
        g, labels = PP.growth_path(.30, 'growth', 'base', 100e6, CPI, years=6)
        table = PP.size_class(100e6, CPI)[1]
        excess = .30 - PP.class_median(table, 1)
        self.assertAlmostEqual(g[0], .30)                                       # lambda 1: year 1 is the run rate
        self.assertEqual(labels[0], '$0-325m')
        rev1 = 100e6 * 1.30 / (1 + PP.PI)
        self.assertAlmostEqual(g[1], PP.class_median(PP.size_class(rev1, CPI)[1], 2) + excess * .5 ** (1 / 2.5))
        self.assertAlmostEqual(g[2] - PP.class_median(PP.size_class(rev1 * (1 + g[1]) / (1 + PP.PI), CPI)[1], 3),
                               (g[1] - PP.class_median(PP.size_class(rev1, CPI)[1], 2)) * .5 ** (1 / 2.5))
        self.assertTrue(all(a > b for a, b in zip(g, g[1:])))                  # monotone fade toward the class median

    def test_class_is_re_read_as_the_company_grows(self):
        g, labels = PP.growth_path(.60, 'early', 'bull', 300e6, CPI, years=4)
        self.assertEqual(labels[0], '$0-325m')
        self.assertEqual(labels[1], '$325-700m')                                # 300m x 1.6 / 1.02 = 470m of 2015 dollars
        g_same, _ = PP.growth_path(.60, 'early', 'bull', 100e6, CPI, years=4)
        self.assertLess(g[1], g_same[1])                                        # the larger class has the lower median

    def test_half_life_scales_by_scenario(self):
        for s, k in PP.HL_SCALE.items():
            g, _ = PP.growth_path(.20, 'mature', s, 100e9, CPI, years=3)
            table = PP.size_class(100e9, CPI)[1]
            excess = .20 - PP.class_median(table, 1)
            rev1 = 100e9 * 1.20 / (1 + PP.PI)
            self.assertAlmostEqual(g[1], PP.class_median(PP.size_class(rev1, CPI)[1], 2) + excess * .5 ** (1 / (3.0 * k)), places=9)

    def test_lambda_shrinks_a_tail_path_and_leaves_a_modest_one_alone(self):
        lam, share = PP.shrink(.60, 'growth', 'base', 100e9, CPI, PP.CEIL['base'])
        self.assertLess(lam, 1.)
        self.assertAlmostEqual(share, PP.CEIL['base'], delta=1e-3)             # bisection lands on the ceiling
        g, _ = PP.growth_path(.60, 'growth', 'base', 100e9, CPI, lam)
        self.assertLess(g[0], .60)                                              # year 1 is shrunk too (the spec's formula)
        self.assertEqual(PP.shrink(.05, 'growth', 'base', 100e9, CPI, PP.CEIL['base']), (1., PP.shrink(.05, 'growth', 'base', 100e9, CPI, None)[1]))
        self.assertEqual(PP.shrink(.60, 'growth', 'bear', 100e9, CPI, PP.CEIL['bear'])[0], 1.)   # the bear has no ceiling
        lam_bull, _ = PP.shrink(.60, 'growth', 'bull', 100e9, CPI, PP.CEIL['bull'])
        self.assertLess(lam_bull, 1.)
        self.assertLess(lam, lam_bull)                                          # the bull may sit deeper in the tail (top 5%)


class MarginAndMultipleTests(unittest.TestCase):
    def test_margin_paths(self):
        base, fl = PP.margin_path(.20, .20, 'mature', 'base', False, years=6)
        self.assertEqual(base, [.20] * 6)
        self.assertFalse(fl)
        ms = (1 - PP.TAX_STAT) * STAGE['mature']['m_spread']
        bear, _ = PP.margin_path(.20, .20, 'mature', 'bear', False, years=6)
        self.assertAlmostEqual(bear[0], .20 - ms / 4)                          # conv 4: year 1 is already a quarter of the way
        self.assertAlmostEqual(bear[3], .20 - ms)                              # reached in year 4, then holds
        self.assertAlmostEqual(bear[5], .20 - ms)
        self.assertLess(bear[0], base[0])                                      # the bear changes the business from year 1
        bull, _ = PP.margin_path(.20, .20, 'mature', 'bull', False, years=6)
        self.assertAlmostEqual(bull[5], .20 + .75 * ms)
        cyc, _ = PP.margin_path(.30, .12, 'mature', 'base', True, years=5)     # a peak TTM margin converges to mid-cycle in 3 years
        self.assertAlmostEqual(cyc[0], .24)
        self.assertAlmostEqual(cyc[2], .12)
        self.assertEqual(cyc[4], .12)
        floored, fl = PP.margin_path(.02, .02, 'early', 'bear', False, years=8)
        self.assertTrue(fl)
        self.assertEqual(min(floored), PP.M_FLOOR)

    def test_steady_state_multiple_is_one_over_ke(self):
        ni, g = [100.] * 17, [0.] * 17
        raw, clipped = PP.justified_multiple(ni, g, R=.08, ke=.08, rf=.04, h=0)
        self.assertAlmostEqual(raw, 12.5, places=6)                             # Mauboussin & Callahan's commodity multiple at 8%
        self.assertAlmostEqual(PP.justified_multiple(ni, g, R=.08, ke=.08, rf=.04, h=3)[0], 12.5, places=6)

    def test_growth_adds_value_only_when_roic_exceeds_ke(self):
        g = [.03] * 17                                                          # g <= R (clip slack) and g <= rf (no terminal switch)
        ni = [100. * 1.03 ** k for k in range(17)]
        raw = PP.justified_multiple(ni, g, R=.08, ke=.08, rf=.04, h=0)[0]
        self.assertAlmostEqual(raw / 1.03, 12.5, places=6)                     # R = ke: forward multiple 1 / ke whatever the growth
        self.assertAlmostEqual(PP.justified_multiple(ni, g, R=.08, ke=.08, rf=.04, h=4)[0] / 1.03, 12.5, places=6)
        self.assertGreater(PP.justified_multiple(ni, g, R=.30, ke=.08, rf=.04, h=0)[0], raw)   # growth is worth something only when R > ke
        g5 = [.05] * 17                                                         # g above rf: year 10 retains for 5% but the terminal grows at rf
        ni5 = [100. * 1.05 ** k for k in range(17)]
        self.assertLess(PP.justified_multiple(ni5, g5, R=.08, ke=.08, rf=.04, h=0)[0] / 1.05, 12.5)   # the registered switch costs a little value
        g_fast = [.10] * 17                                                     # g > R: the clip at 0 binds and the multiple rises even at R = ke
        ni_fast = [100. * 1.1 ** k for k in range(17)]
        self.assertGreater(PP.justified_multiple(ni_fast, g_fast, R=.08, ke=.08, rf=.04, h=0)[0], 1.1 / .08)

    def test_multiple_compresses_as_growth_fades(self):
        g, _ = PP.growth_path(.30, 'growth', 'base', 2e9, CPI)
        rev, ni = [2e9], [2e9 * .2]
        for x in g[:15]:
            rev.append(rev[-1] * (1 + x))
            ni.append(rev[-1] * .2)
        ms = [PP.justified_multiple(ni, g, R=.30, ke=.09, rf=.04, h=h)[0] for h in range(6)]
        self.assertTrue(all(a > b for a, b in zip(ms, ms[1:])), ms)
        self.assertLessEqual(PP.justified_multiple(ni, g, R=.30, ke=.09, rf=.04, h=0)[1], PP.PE_CAP)


class PricePathTests(unittest.TestCase):
    def test_ibkr_like_path_compresses_the_multiple_and_orders_the_scenarios(self):
        out = PP.price_path(ibkr_like())
        self.assertAlmostEqual(out['multiple_today'], 85.7 / 2.65, places=4)   # 32.3x normalized trailing earnings
        self.assertAlmostEqual(out['cost_of_equity'], .042 + .042 + STAGE['growth']['ke'])
        base = out['scenarios']['base']['years']
        self.assertEqual([y['year'] for y in base], [1, 2, 3, 4, 5])
        mults = [y['multiple'] for y in base]
        self.assertTrue(all(a > b for a, b in zip(mults, mults[1:])), mults)   # the multiple compresses year by year
        self.assertLess(mults[-1], out['multiple_today'])
        self.assertTrue(all(a > b for a, b in zip([y['growth'] for y in base], [y['growth'] for y in base][1:])))
        for h in range(5):
            bear, mid, bull = (out['scenarios'][s]['years'][h] for s in ('bear', 'base', 'bull'))
            self.assertLess(bear['price'], mid['price'])
            self.assertLess(mid['price'], bull['price'])
            self.assertLess(bear['growth'], mid['growth'])
            self.assertLess(bear['margin'], mid['margin'])                      # the bear changes the business, not only the multiple
            self.assertLess(bear['justified_multiple'], mid['justified_multiple'])
        self.assertEqual(out['spec_version'], 1)
        self.assertEqual(len(out['input_sha256']), 64)
        self.assertEqual(out['years_basis'][1], 'run_rate')
        self.assertEqual(out['years_basis'][2], 'base_rate_fade')
        self.assertIn('never authorize a trade', out['band_statement'])
        self.assertEqual(PP.price_path(ibkr_like())['output_sha256'], out['output_sha256'])   # deterministic

    def test_premium_halves_per_half_life_and_converges_to_the_justified_multiple(self):
        out = PP.price_path(ibkr_like())
        d0 = out['premium_today']
        self.assertGreater(d0, 0)                                               # 32x is above what the fundamentals justify here
        hl = out['scenarios']['base']['half_life']
        self.assertEqual(hl, STAGE['growth']['half_life'])
        for y in out['scenarios']['base']['years']:
            self.assertAlmostEqual(y['premium_left'], d0 * .5 ** (y['year'] / hl), places=4)
            self.assertAlmostEqual(y['multiple'], y['justified_multiple'] + y['premium_left'], places=4)
        self.assertAlmostEqual(out['scenarios']['bear']['half_life'], hl * .8)
        self.assertAlmostEqual(out['scenarios']['bull']['half_life'], hl * 1.3)
        y_bear, y_bull = out['scenarios']['bear']['years'][2], out['scenarios']['bull']['years'][2]
        self.assertLess(y_bear['premium_left'], y_bull['premium_left'])        # the bear loses the premium fastest

    def test_premium_is_capped_at_the_input_cap(self):
        out = PP.price_path(ibkr_like(price=2.65 * 200))                       # 200x earnings
        self.assertIn('multiple_capped', out['flags'])
        self.assertAlmostEqual(out['premium_today'], PP.PE_INPUT_CAP - out['justified_today'], places=5)
        self.assertLessEqual(max(y['multiple'] for y in out['scenarios']['bull']['years']), PP.PE_CAP)

    def test_roic_missing_means_no_value_creation(self):
        out = PP.price_path(ibkr_like(roic=None, g_run=0., share_cagr=0.))
        self.assertAlmostEqual(out['return_on_capital'], out['cost_of_equity'])
        # at R = ke, zero growth and the class median fade the justified multiple is near 1 / ke
        self.assertAlmostEqual(out['justified_today'], 1 / out['cost_of_equity'], delta=1.0)

    def test_live_inputs_need_evidence_and_dates(self):
        a = ibkr_like()
        with self.assertRaises(ValueError):
            PP.price_path(a, {'guidance': {'revenue_growth_fy1': {'value': .30}}})
        with self.assertRaises(ValueError):
            PP.price_path(a, {'guidance': {'revenue_growth_fy1': {'value': .30, 'evidence_ids': ['e1'], 'issued_at': '2026-07-15'}}})
        out = PP.price_path(a, {'guidance': {'revenue_growth_fy1': {'value': .30, 'evidence_ids': ['e1'], 'issued_at': '2026-05-01'}},
                                'track_record': {'rows': 6, 'median_revenue_surprise': .02}})
        self.assertEqual(out['years_basis'][1], 'guided')
        self.assertTrue(out['live_inputs_used'])
        self.assertAlmostEqual(out['scenarios']['base']['years'][0]['growth'], 1.30 * 1.02 - 1, places=6)
        self.assertNotEqual(out['output_sha256'], PP.price_path(a)['output_sha256'])
        lifted = PP.price_path(a, {'backlog': {'value': 6e9, 'revenue_ttm': a['revenue'], 'evidence_ids': ['e2'], 'issued_at': '2026-05-01'}})
        self.assertEqual([x for x in lifted['assumptions'] if x['id'] == 'base_rate_ceiling'][0]['value'], PP.CEIL_BACKLOG)


def _fb(as_of='2020-06-30'):
    fb = F.new({'ticker': 'SYN', 'name': 'synthetic (not real)', 'currency': 'USD'}, as_of)
    F.add_source(fb, 'sec:companyfacts', 'sec', 'companyfacts', 'filing_derived', as_of)
    return fb


class AnchorTests(unittest.TestCase):
    def _company(self, margins, ttm_margin, cyclical):
        fb = _fb()
        ends = ['2015-12-31', '2016-12-31', '2017-12-31', '2018-12-31', '2019-12-31']
        annual = {}
        for e, m in zip(ends, margins):
            F.add_fact(fb, 'revenue', f'FY{e[:4]}', e, 1e9, 'sec:companyfacts', 'as_reported', f'{int(e[:4]) + 1}-02-20', period_start=f'{e[:4]}-01-01')
            F.add_fact(fb, 'net_income', f'FY{e[:4]}', e, 1e9 * m, 'sec:companyfacts', 'as_reported', f'{int(e[:4]) + 1}-02-20', period_start=f'{e[:4]}-01-01')
            annual[e] = {'revenue': 1e9, 'net_income': 1e9 * m}
        for q, v in (('2019-03-31', 240e6), ('2019-06-30', 250e6), ('2019-09-30', 250e6), ('2019-12-31', 260e6), ('2020-03-31', 300e6)):
            F.add_fact(fb, 'revenue', f'Q@{q}', q, v, 'sec:companyfacts', 'as_reported', '2020-05-01', period_start=q[:8] + '01')
        # a quarter FILED after as_of: must not be used (the facts layer withholds it)
        F.add_fact(fb, 'revenue', 'Q@2020-06-30', '2020-06-30', 400e6, 'sec:companyfacts', 'as_reported', '2020-08-01', period_start='2020-04-01')
        an = {'ttm': {'revenue': 1.06e9, 'net_income': 1.06e9 * ttm_margin, 'period_end': '2020-03-31', 'method': 'quarters', 'dividends': 50e6},
              'metrics': {'revenue_growth_last': {'value': 0.}, 'revenue_cagr_5y': {'value': .04}, 'share_count_cagr_3y': {'value': -.02},
                          'roic': {'value': .15}},
              'annual': annual}
        qa = {'resolved': {'shares_outstanding': {'value': 100e6, 'as_of': '2020-04-30', 'basis': 'cover', 'source_id': 'sec:companyfacts'}}}
        card = {'stage': {'value': 'mature'}, 'cyclical': {'value': cyclical}}
        return fb, an, qa, card

    def test_cyclical_normalization_lowers_normalized_eps_at_a_peak(self):
        fb, an, qa, card = self._company([.10, .08, .12, .10, .25], .30, cyclical=True)
        a = PP.anchors(fb, an, qa, card, 50., .01, .05, cpi=255.802)
        self.assertEqual(a['status'], 'ok')
        self.assertAlmostEqual(a['m_anchor'], .10)                             # the median of the fiscal-year margins, not 30%
        self.assertAlmostEqual(a['g_run'], .04)                                 # the 5-year trend, not the last leg
        self.assertIn('cyclical_normalized', a['flags'])
        out = PP.price_path(a)
        self.assertAlmostEqual(out['eps_normalized'], 1.06e9 * .10 / 100e6)
        self.assertLess(out['eps_normalized'], 1.06e9 * .30 / 100e6)
        self.assertGreater(out['multiple_today'], 50. / (1.06e9 * .30 / 100e6))   # a higher price-to-normalized-earnings multiple
        self.assertAlmostEqual(out['scenarios']['base']['years'][0]['margin'], .30 + (.10 - .30) / 3, places=6)
        fb2, an2, qa2, card2 = self._company([.10, .08, .12, .10, .25], .30, cyclical=False)
        self.assertAlmostEqual(PP.anchors(fb2, an2, qa2, card2, 50., .01, .05)['m_anchor'], .30)

    def test_quarter_growth_ignores_a_fact_filed_after_as_of(self):
        fb, an, qa, card = self._company([.1] * 5, .1, cyclical=False)
        a = PP.anchors(fb, an, qa, card, 50., .01, .05)
        self.assertEqual(a['quarter_ref']['quarter_end'], '2020-03-31')        # not the June quarter filed in August
        self.assertAlmostEqual(a['g_quarter'], 300e6 / 240e6 - 1)
        self.assertAlmostEqual(a['g_run'], (0. + 300e6 / 240e6 - 1) / 2)       # mean of the fiscal-year and quarter run rates
        self.assertEqual(a['references']['shares']['basis'], 'cover')
        self.assertAlmostEqual(a['dividend_yield'], 50e6 / (50. * 100e6))

    def test_exclusions_and_clipping(self):
        fb, an, qa, card = self._company([.1] * 5, -.05, cyclical=False)
        self.assertEqual(PP.anchors(fb, an, qa, card, 50., .01, .05)['reasons'], ['TTM net income not positive'])
        fb, an, qa, card = self._company([.1] * 5, .1, cyclical=False)
        an['ttm']['net_income'] = None
        a = PP.anchors(fb, an, qa, card, 50., .01, .05)                        # facts.ttm has FY2019 (latest fiscal year): different period end
        self.assertEqual(a['reasons'], ['TTM net income missing'])
        an['metrics']['revenue_growth_last'] = {'value': 1.5}
        an['ttm']['net_income'] = 1.06e9 * .1
        a = PP.anchors(fb, an, qa, card, 50., .01, .05)
        self.assertIn('run_rate_clipped', a['flags'])
        self.assertAlmostEqual(a['g_run'], PP.G_RUN[1])

    def test_eps_cagr_is_split_consistent(self):
        fb = _fb('2022-06-30')
        for e, v, filed in (('2018-09-29', 11.91, '2018-11-07'), ('2019-09-28', 11.89, '2019-11-02'), ('2020-09-26', 3.28, '2020-11-01'),
                            ('2021-09-25', 5.61, '2021-10-31')):
            F.add_fact(fb, 'eps_diluted', f'FY{e[:4]}', e, v, 'sec:companyfacts', 'as_reported', filed, period_start=f'{int(e[:4]) - 1}-10-01')
        qa = {'splits': [{'from': '2020-07-17', 'to': '2020-09-26', 'factor': 4.0, 'evidence': {'split_events': [{'date': '2020-08-28'}]}}]}
        self.assertAlmostEqual(PP.eps_cagr_3y(fb, qa), (5.61 / (11.91 / 4)) ** (1 / 3) - 1)
        self.assertAlmostEqual(PP.eps_cagr_3y(fb, {}), (5.61 / 11.91) ** (1 / 3) - 1)    # without the split: the wrong baseline


def _row(sym, formation, price, base, bear, bull, **extra):
    years = lambda ps: [{'year': h + 1, 'price': p, 'growth': .05, 'margin': .1, 'eps': 1., 'justified_multiple': 15., 'multiple': 15.,
                         'premium_left': 0., 'payout': .5} for h, p in enumerate(ps)]
    r = {'symbol': sym, 'formation': formation, 'as_of': formation[:4] + '-06-30', 'status': 'ok', 'price': price, 'stage': 'mature',
         'cyclical': False, 'size_class': '>$50,000m', 'multiple_today': 20., 'justified_today': 15., 'premium_today': 5.,
         'scenarios': {'base': {'lambda': 1., 'base_rate_share_5y': .4, 'years': years(base)},
                       'bear': {'lambda': 1., 'base_rate_share_5y': .6, 'years': years(bear)},
                       'bull': {'lambda': .9, 'base_rate_share_5y': .05, 'years': years(bull)}},
         'flags': [], 'eps_cagr_3y': .05, 'dividend_yield': .01, 'pe_regression_diagnostic': {'ratio_justified_to_reg': .8}}
    r.update(extra)
    return r


def _px(series):
    """{yyyy-mm: close} -> load_prices-like payload."""
    return {'months': [(m, c, c) for m, c in sorted(series.items())], 'splits': []}


class EvaluateTests(unittest.TestCase):
    def test_metrics_band_and_verdict_on_invented_rows(self):
        rows, prices = [], {'SPY': _px({'2020-06': 100., '2021-06': 110., '2022-06': 100., '2023-06': 120.})}
        # five companies, formation 2020: base path = exactly the realized price for S0..S2, 20% high for S3, 40% low for S4
        real = {'S0': 1.10, 'S1': 1.30, 'S2': 0.80, 'S3': 1.00, 'S4': 1.50}
        for i, (s, r) in enumerate(real.items()):
            prices[s] = _px({'2020-06': 100., '2021-06': 100. * r, '2022-06': 100. * r, '2023-06': 100. * r * 1.1})
            pred = 100. * r * (1.2 if s == 'S3' else .6 if s == 'S4' else 1.)
            rows.append(_row(s, '2020-07-01', 100., [pred] * 5, [pred * .7] * 5, [pred * 1.4] * 5))
        rows.append(_row('S5', '2020-07-01', 100., [100.] * 5, [80.] * 5, [120.] * 5, status='excluded', reasons=['TTM net income not positive']))
        rows.append(_row('S6', '2020-07-01', 100., [100.] * 5, [80.] * 5, [120.] * 5))      # no prices: no realized price
        engine = [{'symbol': s, 'formation': '2020-07-01', 'base_irr': .08} for s in ('S0', 'S1', 'S2', 'S3')]   # S4 has no engine row
        res = PP.evaluate(rows, prices, engine)
        h1 = res['horizons'][1]
        self.assertEqual(h1['excluded'], {'no lane path': 1, 'no realized price': 1, 'no engine base_irr': 1, 'no trailing 3-year EPS CAGR': 0})
        self.assertEqual(h1['exclusion_reasons'], {'TTM net income not positive': 1})
        self.assertEqual(h1['lane_only']['n'], 5)
        self.assertEqual(h1['common']['n'], 4)
        m = h1['common']['metrics']['lane_base']
        self.assertEqual(m['n'], 4)
        self.assertAlmostEqual(m['within25'], 1.)                              # S3 is 20% high (inside +-25%); S4 is absent from the common sample
        self.assertAlmostEqual(h1['lane_only']['metrics']['lane_base']['within25'], .8)
        self.assertAlmostEqual(m['mdale'], 0.)
        self.assertAlmostEqual(m['bias_median'], 0.)
        rw = h1['common']['metrics']['random_walk']
        self.assertAlmostEqual(rw['bias_median'], (-__import__('math').log(1.1) + 0.) / 2)
        self.assertAlmostEqual(h1['common']['metrics']['drift_7']['n'], 4)
        self.assertAlmostEqual(h1['common']['metrics']['engine_irr']['n'], 4)
        self.assertAlmostEqual(h1['common']['metrics']['engine_irr']['bias_median'], __import__('statistics').median(
            [__import__('math').log(1.07 / r) for r in (1.1, 1.3, .8, 1.)]))
        self.assertAlmostEqual(h1['common']['metrics']['analyst_const']['bias_median'], __import__('statistics').median(
            [__import__('math').log(1.05 / r) for r in (1.1, 1.3, .8, 1.)]))
        band = h1['common']['band']
        self.assertEqual(band, {'n': 4, 'within_band': 1., 'below_bear': 0., 'above_bull': 0.})
        lo = h1['lane_only']['band']
        self.assertAlmostEqual(lo['above_bull'], .2)                           # S4: actual 150 above the bull 126
        self.assertEqual(h1['common']['head_to_head']['random_walk']['share_lane_closer'], .75)   # S3 ties with nothing; S0..S2 exact
        self.assertEqual(h1['common']['deflation_factors']['2020-07-01']['spy_ratio'], 1.1)
        self.assertAlmostEqual(h1['common']['deflation_factors']['2020-07-01']['sample_median_ratio'], 1.05)
        self.assertEqual(res['horizons'][5]['n_in_range'], 7)                   # 2020 formations are inside the 5-year window (6.2: 2016-2021)
        self.assertEqual(res['horizons'][5]['excluded']['no realized price'], 6)   # S0-S4 stop in 2023, S6 has no prices: no sample
        self.assertEqual(res['horizons'][5]['common']['n'], 0)
        v = res['verdict']
        self.assertIn(v['verdict'], ('PASS', 'PARTIAL', 'FAIL'))
        self.assertFalse(v['S1_accuracy'])                                      # horizons 3 and 5 have no sample: not testable, so not a pass
        self.assertEqual(res['diagnostics']['n_rows_ok'], 6)
        self.assertIn('>$50,000m', res['diagnostics']['by_size_class'])

    def test_predictions_and_realized_ratio(self):
        r = _row('S', '2019-07-01', 50., [55., 60., 65., 70., 75.], [40.] * 5, [80.] * 5)
        p = PP.predictions(r, 3, base_irr=.10)
        self.assertEqual(p['lane_base'], 65.)
        self.assertAlmostEqual(p['drift_7'], 50 * 1.07 ** 3)
        self.assertAlmostEqual(p['analyst_const'], 50 * 1.05 ** 3)
        self.assertAlmostEqual(p['engine_irr'], 50 * 1.09 ** 3)               # 10% implied return less the 1% yield: price only
        self.assertAlmostEqual(p['engine_irr_total'], 50 * 1.10 ** 3)
        self.assertNotIn('analyst_const', PP.predictions(dict(r, eps_cagr_3y=None), 1))
        px = _px({'2019-06': 10., '2020-06': 12., '2022-05': 15.})
        self.assertAlmostEqual(PP.realized_ratio(px, '2019-06-30', 1), 1.2)
        self.assertAlmostEqual(PP.realized_ratio(px, '2019-06-30', 3), 1.5)    # May 2022 within the two-month tolerance
        self.assertIsNone(PP.realized_ratio(px, '2019-06-30', 5))

    def test_measured_error_and_verdict_are_recorded_and_shown(self):
        self.assertEqual(sorted(PP.MEASURED), [1, 3, 5])
        for h, m in PP.MEASURED.items():
            self.assertGreater(m['n'], 1000)
            self.assertLess(m['bias_median'], -.10)                             # S2 failed at every horizon: that is the record
            self.assertGreater(m['mdale'], abs(m['bias_median']) * .9)
        self.assertTrue(PP.MEASURED_VERDICT.startswith('FAIL'))
        self.assertEqual(PP.TABLE_STATUS, 'not_shown')
        out = PP.price_path(ibkr_like())
        self.assertEqual(out['table_status'], 'not_shown')
        self.assertEqual(out['measured_error'], PP.MEASURED)
        for s in ('FAIL', '2016-2025', 'within +-25% in 53%', 'median bias -0.50', 'never authorize a trade'):
            self.assertIn(s, out['band_statement'])

    def test_constants_are_the_registered_ones(self):
        self.assertEqual(sorted(PP.CPI_MAY), list(range(2016, 2026)))
        self.assertEqual((PP.PE_FLOOR, PP.PE_CAP, PP.PE_INPUT_CAP), (5., 60., 60.))
        self.assertEqual(PP.CEIL, {'base': .10, 'bull': .05, 'bear': None})
        self.assertEqual(PP.FORMATION_YEARS[5], (2016, 2021))
        self.assertEqual(PP.SPEC_VERSION, 1)


if __name__ == '__main__':
    unittest.main()
