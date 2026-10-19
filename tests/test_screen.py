"""Discovery screen tests. Every company below except the ADBE fixture is SYNTHETIC (invented numbers, labelled
'synthetic' in the row source) and built to exercise one opportunity type or one failure mode."""
import importlib.util
import json
import os
import random
import unittest
from datetime import date, timedelta

from fundamental_engine import screen as S

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'adbe_factbook_2026-10-19.json')
AS_OF = '2026-10-19'


def load_fix():
    with open(FIX) as fh:
        return json.load(fh)


def mk(sym, sector, industry=None, **m):
    """Screening row in the adapter schema; growth series oldest first."""
    if 'series' in m:
        m['revenue_growth_series'] = m.pop('series')
        m.setdefault('revenue_growth', m['revenue_growth_series'][-1])
        if len(m['revenue_growth_series']) >= 3:
            p = 1.
            for g in m['revenue_growth_series'][-3:]:
                p *= 1 + g
            m.setdefault('revenue_cagr_3y', p ** (1 / 3) - 1)
            s = m['revenue_growth_series']
            m.setdefault('acceleration', s[-1] - (s[-2] + s[-3]) / 2)
    if 'ev_fcf' in m:                       # EV and FCF from a multiple, revenue and FCF margin
        mult = m.pop('ev_fcf')
        m['fcf_ttm'] = m['revenue_ttm'] * m['fcf_margin']
        m['ev'] = mult * m['fcf_ttm']
        m['market_cap'] = m['ev'] - m.get('net_debt', 0)
    return {'symbol': sym, 'name': f'{sym} (synthetic)', 'sector': sector, 'industry': industry or sector, 'market_cap': m.get('market_cap'),
            'as_of': AS_OF, 'data_grade': 'screening', 'status': 'ok', 'source': {'provider': 'synthetic'}, 'metrics': m, 'basis': {}, 'errors': {}}


def universe():
    B = 1e9
    return [
        # (b) mega-cap quality at a reasonable multiple, Google-like
        mk('MEGA', 'Communication Services', revenue_ttm=400 * B, gross_margin=.58, operating_margin=.32, gross_margin_change=.005,
           operating_margin_trend=.02, fcf_margin=.22, fcf_conversion=1.0, sbc_to_revenue=.05, roic=.28, piotroski=7, series=[.09, .12, .13],
           dilution_yoy=-.015, net_debt=-80 * B, ebitda=150 * B, interest_coverage=200, altman_zone='safe', pe=22,
           consensus_growth_2y=.11, ev_fcf=24),
        # (a) quality compounder, expensive
        mk('COMP', 'Information Technology', 'Application Software', revenue_ttm=20 * B, gross_margin=.78, operating_margin=.36,
           gross_margin_change=.01, operating_margin_trend=.03, fcf_margin=.38, fcf_conversion=1.2, sbc_to_revenue=.06, roic=.40,
           piotroski=7, series=[.19, .21, .23], dilution_yoy=.004, net_debt=-5 * B, ebitda=8 * B, altman_zone='safe', pe=66,
           consensus_growth_2y=.20, ev_fcf=55),
        # (b) quality with moderate growth; a one-year gross-margin dip misses its anchor, Piotroski 6: must not vanish
        mk('STEADY', 'Consumer Staples', 'Household Products', revenue_ttm=60 * B, gross_margin=.40, operating_margin=.21,
           gross_margin_change=-.035, operating_margin_trend=.005, fcf_margin=.16, fcf_conversion=.95, sbc_to_revenue=.01, roic=.22,
           piotroski=6, series=[.05, .06, .055], dilution_yoy=-.02, net_debt=25 * B, ebitda=16 * B, interest_coverage=15,
           altman_zone='safe', pe=21, consensus_growth_2y=.05, ev_fcf=19),
        # value trap: cheap but deteriorating
        mk('TRAP', 'Industrials', 'Industrial Machinery', revenue_ttm=15 * B, gross_margin=.30, operating_margin=.08,
           gross_margin_change=-.04, operating_margin_trend=-.04, fcf_margin=.09, fcf_conversion=1.3, sbc_to_revenue=.005, roic=.07,
           piotroski=3, series=[-.02, -.05, -.08], dilution_yoy=0., net_debt=4 * B, ebitda=1.6 * B, interest_coverage=5,
           altman_zone='grey', pe=7, consensus_growth_2y=-.03, ev_fcf=6),
        # fast grower, heavy dilution, no commercial proof
        mk('HYPE', 'Information Technology', 'Systems Software', revenue_ttm=.8 * B, gross_margin=.22, operating_margin=-.60,
           gross_margin_change=-.05, operating_margin_trend=-.10, fcf_margin=-.45, fcf_margin_change=-.10, sbc_to_revenue=.30,
           series=[.60, .55, .70], dilution_yoy=.22, net_debt=-.43 * B, ebitda=-.4 * B, market_cap=20 * B, ev=19.57 * B,
           fcf_ttm=-.36 * B, net_margin=-.70, consensus_growth_2y=.50),
        # (c) early commercial growth with proof, funded
        mk('EARLY', 'Health Care', 'Health Care Technology', revenue_ttm=1.2 * B, gross_margin=.72, gross_margin_change=.03,
           operating_margin=-.08, operating_margin_trend=.09, fcf_margin=-.02, fcf_margin_change=.08, sbc_to_revenue=.12,
           series=[.45, .42, .40], dilution_yoy=.03, net_debt=-.6 * B, ebitda=-.05 * B, market_cap=12 * B, ev=11.4 * B,
           fcf_ttm=-.024 * B, net_margin=-.10, consensus_growth_2y=.32),
        # (c) early commercial growth, funding-conditional
        mk('EARLY2', 'Information Technology', 'Application Software', revenue_ttm=.5 * B, gross_margin=.65, operating_margin=-.15,
           operating_margin_trend=.06, fcf_margin=-.12, fcf_margin_change=.05, sbc_to_revenue=.15, series=[.35, .33, .30],
           dilution_yoy=.07, net_debt=-.09 * B, ebitda=-.06 * B, market_cap=4 * B, ev=3.91 * B, fcf_ttm=-.06 * B, net_margin=-.18),
        # (d) turning point: accelerating, margins and cash improving, price assumes little
        mk('TURN', 'Consumer Discretionary', 'Specialty Retail', revenue_ttm=9 * B, gross_margin=.42, operating_margin=.11,
           gross_margin_change=.01, operating_margin_trend=.04, fcf_margin=.09, fcf_margin_change=.04, fcf_conversion=1.1,
           sbc_to_revenue=.01, roic=.13, piotroski=8, series=[.02, .03, .09], dilution_yoy=-.01, net_debt=1 * B, ebitda=1.3 * B,
           interest_coverage=12, altman_zone='safe', pe=15, consensus_growth_2y=.08, ev_fcf=13),
        # (d) overreaction: quality intact, price fell 40%
        mk('DIP', 'Health Care', 'Life Sciences Tools & Services', revenue_ttm=8 * B, gross_margin=.55, operating_margin=.22,
           gross_margin_change=-.005, operating_margin_trend=-.01, fcf_margin=.18, fcf_conversion=1.05, sbc_to_revenue=.02, roic=.16,
           piotroski=6, series=[.08, .07, .06], dilution_yoy=-.01, net_debt=2 * B, ebitda=2.2 * B, interest_coverage=14,
           altman_zone='safe', pe=19, consensus_growth_2y=.06, ev_fcf=17, drawdown_from_high=-.40),
        # (e) structural theme, not priced for perfection
        mk('INFRA', 'Industrials', 'Electrical Components & Equipment', revenue_ttm=12 * B, gross_margin=.35, operating_margin=.17,
           gross_margin_change=.015, operating_margin_trend=.02, fcf_margin=.12, fcf_conversion=1.0, sbc_to_revenue=.01, roic=.18,
           piotroski=7, series=[.10, .14, .18], dilution_yoy=0., net_debt=2 * B, ebitda=2.4 * B, interest_coverage=18,
           altman_zone='safe', pe=34, consensus_growth_2y=.15, ev_fcf=32),
        # (e) structural theme already priced for perfection
        mk('HOTAI', 'Industrials', 'Electrical Components & Equipment', revenue_ttm=6 * B, gross_margin=.38, operating_margin=.19,
           gross_margin_change=.01, operating_margin_trend=.03, fcf_margin=.10, fcf_conversion=.9, sbc_to_revenue=.02, roic=.20,
           piotroski=7, series=[.15, .20, .25], dilution_yoy=.005, net_debt=0, ebitda=1.3 * B, interest_coverage=30,
           altman_zone='safe', pe=150, consensus_growth_2y=.22, ev_fcf=160),
        # unsupported economics
        mk('BANK', 'Financials', 'Diversified Banks', revenue_ttm=100 * B, operating_margin=.35, roic=.12, series=[.03, .04, .05],
           market_cap=300 * B, pe=12, piotroski=6),
        # missing most data
        mk('SPARSE', 'Materials', 'Specialty Chemicals', gross_margin=.30, revenue_growth=.04, market_cap=3 * B),
        # distressed
        mk('DISTR', 'Energy', 'Oil & Gas Exploration & Production', revenue_ttm=5 * B, gross_margin=.25, operating_margin=.04,
           fcf_margin=.01, roic=.03, piotroski=2, series=[-.10, -.05, -.12], dilution_yoy=.02, net_debt=9 * B, ebitda=1.2 * B,
           interest_coverage=1.1, altman_zone='distress', market_cap=1 * B, ev=10 * B, fcf_ttm=.05 * B, sbc_to_revenue=.005),
        # mediocre utility: no lens
        mk('MEH', 'Utilities', 'Electric Utilities', revenue_ttm=10 * B, gross_margin=.40, operating_margin=.18, gross_margin_change=0.,
           fcf_margin=.04, fcf_conversion=.4, sbc_to_revenue=.002, roic=.05, piotroski=5, series=[.02, .01, .02], dilution_yoy=.01,
           net_debt=20 * B, ebitda=4 * B, interest_coverage=3.2, altman_zone='grey', pe=24, ev_fcf=60),
    ]


def denied_fmp():
    deny = 'ACCESS DENIED: synthetic denial text. Please inform the user to upgrade.'
    return {'symbol': 'NODATA', 'retrieved_utc': '2026-10-19T22:30:30Z', 'km': {'marketCap': None}, 'rt': {}, 'growth': [], 'scores': {},
            'estimates': [], 'errors': {'A': 'skipped: symbol denied on D', 'D': deny}, 'allowed': False}


OPTS = {'universe': 'synthetic test universe', 'themes': {'INFRA': ['grid build-out'], 'HOTAI': ['AI power demand']}}


def run(rows=None, **kw):
    rows = universe() + S.rows_from_fmp_screen([denied_fmp()]) if rows is None else rows
    return S.scan(rows, {**OPTS, **kw})


def syms(out, lens):
    return [c['symbol'] for c in out['lenses'][lens]]


class Lenses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run()

    def test_megacap_quality_at_reasonable_multiple_is_found(self):
        self.assertIn('MEGA', syms(self.out, 'quality_fair_price'))
        self.assertIn('MEGA', [e['symbol'] for e in self.out['shortlist']])
        self.assertEqual(self.out['dimensions']['MEGA']['business_quality']['level'], 'strong')

    def test_moderate_grower_with_one_missed_anchor_does_not_vanish(self):
        d = self.out['dimensions']['STEADY']
        self.assertEqual(d['business_quality']['inputs']['gross_margin_change']['signal'], -1)   # misses the stability anchor
        self.assertEqual(d['business_quality']['level'], 'strong')
        self.assertEqual(d['growth_quality']['level'], 'adequate')                              # 5-6% growth is not penalized
        self.assertIn('STEADY', syms(self.out, 'quality_fair_price'))

    def test_value_trap_does_not_top_quality_at_fair_price(self):
        self.assertNotIn('TRAP', syms(self.out, 'quality_fair_price'))
        self.assertNotEqual(syms(self.out, 'quality_fair_price')[0], 'TRAP')
        self.assertEqual(self.out['dimensions']['TRAP']['valuation']['level'], 'strong')
        self.assertEqual(self.out['dimensions']['TRAP']['growth_quality']['level'], 'weak')
        self.assertIn('value trap', self.out['excluded']['TRAP'])

    def test_fast_diluting_grower_without_proof_does_not_rank_high(self):
        for ln in S.LENSES:
            self.assertNotIn('HYPE', syms(self.out, ln)[:3], ln)
        self.assertNotIn('HYPE', [e['symbol'] for e in self.out['shortlist']])
        self.assertIn('HYPE', self.out['excluded'])
        self.assertEqual(self.out['dimensions']['HYPE']['expected_return_proxy']['status'], 'not_meaningful')

    def test_every_lens_has_its_archetype(self):
        self.assertIn('COMP', syms(self.out, 'quality_compounder'))
        self.assertNotIn('COMP', syms(self.out, 'quality_fair_price'))          # excellent but expensive stays out of (b)
        self.assertIn('commercial_proof', self.out['excluded']['HYPE'])         # in scope of (c), failures named
        self.assertEqual(syms(self.out, 'early_commercial_growth')[:2], ['EARLY', 'EARLY2'])
        early2 = next(c for c in self.out['lenses']['early_commercial_growth'] if c['symbol'] == 'EARLY2')
        self.assertIn('funding_conditional', early2['tags'])
        self.assertIn('TURN', syms(self.out, 'mispriced_turning'))
        dip = next(c for c in self.out['lenses']['mispriced_turning'] if c['symbol'] == 'DIP')
        self.assertIn('overreaction_candidate', dip['tags'])
        self.assertIn('INFRA', syms(self.out, 'structural_tailwind'))

    def test_structural_theme_already_priced_is_a_near_miss(self):
        self.assertNotIn('HOTAI', syms(self.out, 'structural_tailwind'))
        nm = {x['symbol']: x for x in self.out['near_misses']['structural_tailwind']}
        self.assertEqual(nm['HOTAI']['failed'], 'not_already_priced')
        self.assertEqual(self.out['dimensions']['HOTAI']['priced_in']['class'], 'priced_for_perfection')

    def test_disqualifiers_are_separate_and_explained(self):
        ex = self.out['excluded']
        self.assertTrue(ex['BANK'].startswith('not supported'))
        self.assertTrue(ex['SPARSE'].startswith('unreliable data'))
        self.assertTrue(ex['DISTR'].startswith('distress'))
        self.assertTrue(ex['MEH'].startswith('no lens matched'))
        self.assertEqual([x['symbol'] for x in self.out['no_data']], ['NODATA'])
        self.assertNotIn('upgrade', json.dumps(self.out['no_data']))        # vendor text is classified, never copied

    def test_every_symbol_with_data_is_shortlisted_or_excluded_with_a_reason(self):
        short = {e['symbol'] for e in self.out['shortlist']}
        self.assertEqual(short | set(self.out['excluded']), set(self.out['dimensions']))
        self.assertFalse(short & set(self.out['excluded']))
        self.assertTrue(all(isinstance(v, str) and v for v in self.out['excluded'].values()))

    def test_candidates_explain_why_and_ordering(self):
        for ln, cands in self.out['lenses'].items():
            for c in cands:
                self.assertTrue(c['why'])
                self.assertTrue(c['ordering']['meaning'])
                self.assertTrue(c['ordering']['fields'])
        for e in self.out['shortlist']:
            self.assertTrue(e['reason'])
            self.assertTrue(e['check_first'][0].startswith('Reconcile screening-grade'))

    def test_shortlist_is_top_n_per_lens_deduplicated(self):
        union = run(top_n=1, shortlist_mode='union')
        firsts = {cands[0]['symbol'] for cands in union['lenses'].values() if cands}
        self.assertEqual({e['symbol'] for e in union['shortlist']}, firsts)
        distinct = run(top_n=2)
        picked = [e['symbol'] for e in distinct['shortlist']]
        self.assertEqual(len(picked), len(set(picked)))
        for ln, cands in distinct['lenses'].items():
            mine = [e for e in distinct['shortlist'] if e['selected_by'] == ln]
            self.assertEqual(len(mine), min(2, len({c['symbol'] for c in cands} - (set(picked) - {e['symbol'] for e in mine}))), ln)
            for e in mine:
                self.assertIn(ln, [x['lens'] for x in e['lenses']])
        self.assertTrue(firsts <= set(picked))

    def test_output_is_json_without_nan_and_policy_is_explicit(self):
        json.dumps(self.out, allow_nan=False)
        self.assertFalse(self.out['policy']['is_recommendation'])
        self.assertFalse(self.out['policy']['valuation_gap_used_for_ranking'])
        self.assertTrue(any('not a recommendation' in n for n in self.out['notes']))
        self.assertTrue(any('Survivorship' in n for n in self.out['notes']))


class Mechanics(unittest.TestCase):
    def test_sector_cap_keeps_capped_names_as_near_misses(self):
        rows = []
        for i, pe in enumerate((16, 17, 18, 19)):
            r = universe()[0]
            r['symbol'] = f'Q{i}'
            r['metrics'] = dict(r['metrics'], pe=pe, fcf_ttm=r['metrics']['fcf_ttm'] * (1 + i / 10))
            rows.append(r)
        out = S.scan(rows + universe()[1:4], {'sector_cap': 2})
        lens = out['lenses']['quality_fair_price']
        self.assertEqual(sum(1 for c in lens if c['sector'] == 'Communication Services'), 2)
        capped = [x for x in out['near_misses']['quality_fair_price'] if x['failed'] == 'sector_cap']
        self.assertEqual(len(capped), 2)
        self.assertTrue(all('sector cap' in x['reason'] for x in capped))
        uncapped = S.scan(rows + universe()[1:4], {'sector_cap': None})
        self.assertEqual(sum(1 for c in uncapped['lenses']['quality_fair_price'] if c['sector'] == 'Communication Services'), 4)

    def test_low_margin_high_turnover_quality_is_not_a_margin_casualty(self):
        r = mk('WAREHOUSE', 'Consumer Staples', 'Consumer Staples Merchandise Retail', revenue_ttm=250e9, gross_margin=.13,
               operating_margin=.035, gross_margin_change=.002, operating_margin_trend=.002, fcf_margin=.03, fcf_conversion=1.5,
               sbc_to_revenue=.004, roic=.22, piotroski=7, series=[.07, .06, .08], dilution_yoy=0., net_debt=-5e9, ebitda=12e9,
               interest_coverage=60, altman_zone='safe', pe=48, ev_fcf=55)
        d = S.scan([r] + universe(), OPTS)['dimensions']['WAREHOUSE']['business_quality']
        self.assertEqual(d['inputs']['margin_level']['signal'], 0)
        self.assertIn('capital turnover', d['inputs']['margin_level']['note'])
        self.assertEqual(d['level'], 'strong')

    def test_deterministic_and_order_independent(self):
        rows = universe() + S.rows_from_fmp_screen([denied_fmp()])
        a = S.scan(rows, OPTS)
        shuffled = list(rows)
        random.Random(7).shuffle(shuffled)
        b = S.scan(shuffled, OPTS)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_missing_data_robustness(self):
        rows = [mk('EMPTY1', 'Industrials'), mk('ONLYCAP', 'Industrials', market_cap=1e9),
                mk('HALF', 'Industrials', revenue_ttm=1e9, gross_margin=.5, operating_margin=.2, roic=.2, series=[.1, .1, .1],
                   fcf_margin=.1, market_cap=5e9, net_debt=0.)]
        rows[0]['metrics'] = {'gross_margin': float('nan'), 'roic': None, 'revenue_growth_series': [None, .1]}
        out = S.scan(rows)
        json.dumps(out, allow_nan=False)
        self.assertIn('EMPTY1', [x['symbol'] for x in out['no_data']])
        self.assertIn('ONLYCAP', [x['symbol'] for x in out['no_data']])
        self.assertIn('HALF', out['dimensions'])
        half = out['dimensions']['HALF']
        self.assertIn('NOPAT', half['priced_in']['multiple_basis'])
        self.assertIn('SBC unknown', half['priced_in']['cross_check']['basis'])     # owner-FCF cross-check before SBC, and says so
        self.assertTrue(any('SBC unknown' in x for x in half['expected_return_proxy']['reasons']))
        self.assertEqual(half['business_quality']['inputs']['piotroski']['signal'], None)
        self.assertEqual(S.scan([])['shortlist'], [])

    def test_look_ahead_rows_rejected(self):
        r = mk('LATE', 'Industrials', revenue_ttm=1e9, gross_margin=.5)
        r['as_of'] = '2026-12-01'
        with self.assertRaises(ValueError):
            S.scan([r], {'as_of': AS_OF})

    def test_bad_options_rejected(self):
        with self.assertRaises(ValueError):
            S.scan([], {'top_nn': 3})
        with self.assertRaises(ValueError):
            S.scan([], {'sector_cap': 0})

    def test_implied_growth_round_trip(self):
        g, status = S.implied_growth(1.03 / .06)
        self.assertEqual(status, 'solved')
        self.assertAlmostEqual(g, .03, places=6)
        g1, _ = S.implied_growth(20)
        g2, _ = S.implied_growth(40)
        self.assertLess(g1, g2)
        self.assertEqual(S.implied_growth(1e6)[1], 'above_range')

    def test_expected_return_proxy_formula(self):
        out = run()
        er = out['dimensions']['MEGA']['expected_return_proxy']
        c = er['components']
        self.assertAlmostEqual(er['value'], c['owner_fcf_yield'] + c['sustainable_growth'] - (c['excess_dilution'] or 0), places=12)
        m = out['dimensions']['MEGA']['metrics']
        self.assertAlmostEqual(c['owner_fcf_yield'], (m['fcf_ttm'] - m['sbc_ttm']) / m['market_cap'], places=12)   # FCF after interest / equity
        self.assertEqual(c['excess_dilution'], 0.)                         # buybacks are not added back
        hype = out['dimensions']['HYPE']
        self.assertIsNone(hype['expected_return_proxy']['value'])

    def test_rank_changes_explain_moves(self):
        a = run()
        rows = universe()
        for r in rows:
            if r['symbol'] == 'MEGA':
                r['metrics'].update(revenue_growth_series=[.05, -.02, -.06], revenue_growth=-.06, revenue_cagr_3y=-.01,
                                    acceleration=-.075, gross_margin_change=-.04, operating_margin_trend=-.06)
        b = S.scan(rows, OPTS)
        ch = S.rank_changes(a, b)
        left = {x['symbol']: x for x in ch['left']}
        self.assertIn('MEGA', left)
        self.assertIn('growth_quality', left['MEGA']['level_changes'])
        self.assertTrue(left['MEGA']['reason'])
        self.assertTrue(any(x['symbol'] == 'MEGA' and x['to'] is None for x in ch['lens_moves']['quality_fair_price']))
        self.assertEqual(S.rank_changes(a, a)['entered'], [])


class Adapters(unittest.TestCase):
    def test_fmp_record_mapping(self):
        rec = {'symbol': 'SYN', 'retrieved_utc': '2026-10-19T22:30:30Z', 'allowed': True,
               'km': {'marketCap': 100e9, 'enterpriseValueTTM': 95e9, 'evToEBITDATTM': 19.0, 'evToFreeCashFlowTTM': 23.75,
                      'freeCashFlowYieldTTM': .04, 'returnOnInvestedCapitalTTM': .25, 'stockBasedCompensationToRevenueTTM': .03,
                      'capexToRevenueTTM': .04, 'incomeQualityTTM': 1.2},
               'rt': {'grossProfitMarginTTM': .6, 'operatingProfitMarginTTM': .25, 'netProfitMarginTTM': .2, 'priceToEarningsRatioTTM': 25,
                      'interestCoverageRatioTTM': 40, 'revenuePerShareTTM': 20, 'freeCashFlowPerShareTTM': 4, 'netIncomePerShareTTM': 4},
               'growth': [{'date': '2023-12-31', 'revenueGrowth': .08, 'grossProfitGrowth': .09, 'operatingIncomeGrowth': .1,
                           'weightedAverageSharesDilutedGrowth': -.01},
                          {'date': '2025-12-31', 'revenueGrowth': .12, 'grossProfitGrowth': .14, 'operatingIncomeGrowth': .2,
                           'weightedAverageSharesDilutedGrowth': -.02},
                          {'date': '2024-12-31', 'revenueGrowth': .10, 'grossProfitGrowth': .1, 'operatingIncomeGrowth': .1,
                           'weightedAverageSharesDilutedGrowth': -.015}],
               'scores': {'altmanZScore': 6.0, 'piotroskiScore': 8, 'revenue': 25e9},
               'estimates': [{'date': '2026-12-31', 'revenueAvg': 28e9, 'numAnalystsRevenue': 20},
                             {'date': '2027-12-31', 'revenueAvg': 31e9, 'numAnalystsRevenue': 18},
                             {'date': '2028-12-31', 'revenueAvg': 34e9, 'numAnalystsRevenue': 10},
                             {'date': '2025-12-31', 'revenueAvg': 25e9, 'numAnalystsRevenue': 25}],
               'errors': {'A': None, 'B': None, 'C': None, 'D': None, 'E': None}}
        meta = [{'symbol': 'SYN', 'name': 'Synthetic Co', 'sector': 'Information Technology', 'sub_industry': 'Application Software'}]
        r = S.rows_from_fmp_screen([json.dumps(rec)], meta)[0]
        m = r['metrics']
        self.assertEqual((r['status'], r['data_grade'], r['industry'], r['as_of']), ('ok', 'screening', 'Application Software', '2026-10-19'))
        self.assertAlmostEqual(m['net_debt'], -5e9)
        self.assertAlmostEqual(m['ebitda'], 5e9)
        self.assertAlmostEqual(m['fcf_margin'], .2)
        self.assertAlmostEqual(m['fcf_ttm'], 4e9)
        self.assertEqual(m['revenue_growth_series'], [.08, .10, .12])         # sorted by date, oldest first
        self.assertAlmostEqual(m['acceleration'], .12 - .09)
        self.assertAlmostEqual(m['consensus_growth_2y'], (34 / 28) ** .5 - 1)  # only fiscal years ending after as_of
        self.assertAlmostEqual(m['gross_margin_change'], .6 - .6 * 1.12 / 1.14)
        self.assertEqual(m['altman_zone'], 'safe')
        self.assertEqual(m['dilution_yoy'], -.02)
        self.assertIn('FMP', r['basis']['roic'])
        out = S.scan([r])
        json.dumps(out, allow_nan=False)
        self.assertIn('SYN', out['dimensions'])

    def test_fmp_partial_denial_keeps_data_and_records_gap(self):
        rec = {'symbol': 'PART', 'retrieved_utc': '2026-10-19T00:00:00Z', 'km': {'marketCap': 5e9, 'enterpriseValueTTM': 6e9},
               'rt': {'grossProfitMarginTTM': .5, 'operatingProfitMarginTTM': .2}, 'growth': [], 'scores': {}, 'estimates': [],
               'errors': {'E': 'ACCESS DENIED: tier', 'C': 'timeout'}}
        r = S.rows_from_fmp_screen([rec])[0]
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['errors'], {'financial-estimates': 'access_denied', 'financial-statement-growth': 'error'})
        out = S.scan([r])
        self.assertTrue(any('vendor calls failed' in x for x in out['dimensions']['PART']['data_uncertainty']['reasons']))

    def test_factbook_adapter_on_adbe_fixture(self):
        fb = load_fix()
        r = S.rows_from_factbooks([fb])[0]
        m = r['metrics']
        self.assertEqual((r['symbol'], r['data_grade'], r['as_of']), ('ADBE', 'factbook', '2026-10-19'))
        self.assertGreater(m['gross_margin'], .8)
        self.assertTrue(.05 < m['revenue_growth'] < .2)
        self.assertLess(m['dilution_yoy'], 0)                              # buybacks shrink the diluted count
        self.assertTrue(300e6 < m['market_cap'] / 246.87 < 500e6)           # IB price x latest share count
        self.assertIn('latest share count', r['basis']['market_cap'])
        self.assertIn('Piotroski', r['basis']['piotroski'])
        out = S.scan([r] + universe(), OPTS)
        d = out['dimensions']['ADBE']
        self.assertEqual(d['business_quality']['level'], 'strong')
        self.assertIn(d['data_uncertainty']['level'], ('adequate', 'strong'))
        self.assertTrue('ADBE' in {e['symbol'] for e in out['shortlist']} or 'ADBE' in out['excluded'])

    def test_factbook_price_history_is_point_in_time(self):
        fb = load_fix()
        d0 = date(2025, 10, 20)
        hist = [{'date': (d0 + timedelta(days=7 * i)).isoformat(), 'close': 300 - 1.0 * i} for i in range(53)]
        fb['market']['price_history'] = hist + [{'date': '2026-12-31', 'close': 999.}]      # future point must be ignored
        m = S.rows_from_factbooks([fb])[0]['metrics']
        self.assertAlmostEqual(m['drawdown_from_high'], hist[-1]['close'] / 300 - 1)
        self.assertGreater(m['volatility_1y'], 0)
        self.assertLess(m['volatility_1y'], .2)

    def test_factbook_adapter_never_reads_future_facts(self):
        fb = load_fix()
        fb2 = json.loads(json.dumps(fb))
        fb2['as_of'] = '2025-06-30'                                     # before FY2025 was filed
        fb2['market']['price']['as_of'] = '2025-06-30'
        r1, r2 = S.rows_from_factbooks([fb])[0], S.rows_from_factbooks([fb2])[0]
        self.assertNotEqual(r1['metrics']['revenue_ttm'], r2['metrics']['revenue_ttm'])
        self.assertLess(len(r2['metrics']['revenue_growth_series']), len(r1['metrics']['revenue_growth_series']))

    def test_factbook_adapter_uses_analysis_and_dataqa_when_given(self):
        fb = load_fix()
        an = {'years': ['2024-11-29', '2025-11-28'],
              'annual': {'2024-11-29': {'revenue': 100., 'gross_profit': 80., 'operating_income': 30., 'net_income': 25., 'fcf': 30.,
                                        'shares_diluted_wavg': 10.},
                         '2025-11-28': {'revenue': 110., 'gross_profit': 90., 'operating_income': 36., 'net_income': 28., 'fcf': 35.,
                                        'shares_diluted_wavg': 9.8}},
              'ttm': {'revenue': 112., 'gross_profit': 92., 'operating_income': 37., 'net_income': 29., 'fcf': 36., 'sbc': 5., 'method': 'quarters'},
              'metrics': {'roic': {'value': .3}, 'net_debt_to_ebitda': {'value': .5}},
              'forensics': {'piotroski': {'status': 'computed', 'score': 6, 'out_of': 8}, 'altman': {'status': 'computed', 'zone': 'safe', 'model': 'Z'}}}
        qa = {'status': 'ok', 'resolved': {'shares_outstanding': {'value': 400e6}, 'net_cash': {'value': 1e9}}}
        r = S.rows_from_factbooks([fb], analyses={'ADBE': an}, qas=[qa])[0]
        m = r['metrics']
        self.assertEqual(r['data_grade'], 'verified')
        self.assertEqual((m['roic'], m['altman_zone'], m['net_debt']), (.3, 'safe', -1e9))
        self.assertAlmostEqual(m['piotroski'], 6 / 8 * 9)
        self.assertAlmostEqual(m['market_cap'], 246.87 * 400e6)
        self.assertAlmostEqual(m['revenue_growth'], .1)

    @unittest.skipUnless(all(importlib.util.find_spec(f'fundamental_engine.{x}') for x in ('dataqa', 'analysis')), 'pipeline modules absent')
    def test_factbook_adapter_with_real_dataqa_and_analysis(self):
        from fundamental_engine import analysis, dataqa
        fb = load_fix()
        qa = dataqa.validate(fb)
        an = analysis.analyze(fb, qa)
        light, full = S.rows_from_factbooks([fb])[0], S.rows_from_factbooks([fb], [an], [qa])[0]
        self.assertEqual(full['data_grade'], 'verified' if qa['status'] == 'ok' else 'factbook')
        self.assertEqual(full['source']['dataqa_status'], qa['status'])
        self.assertTrue(full['basis']['roic'].startswith('analysis metric'))
        self.assertAlmostEqual(full['metrics']['revenue_ttm'], light['metrics']['revenue_ttm'])
        out = S.scan([full] + universe(), OPTS)
        json.dumps(out, allow_nan=False)
        self.assertEqual(out['dimensions']['ADBE']['business_quality']['level'], 'strong')

    def test_pit_adapter_point_in_time(self):
        def series(values, start=date(2022, 3, 31), lag=40):
            out, e = {}, start
            for v in values:
                out[e.isoformat()] = (float(v), (e + timedelta(days=lag)).isoformat())
                mo = e.month + 3; y = e.year + (mo - 1) // 12; mo = (mo - 1) % 12 + 1
                e = date(y, mo, 30 if mo in (6, 9) else 31)
            return out
        rev = [100 * 1.05 ** i for i in range(16)]
        rec = {'rev': series(rev), 'oi': series([v * .2 for v in rev]), 'gp': series([v * .6 for v in rev]),
               'ocf': series([v * .25 for v in rev]), 'capex': series([v * .05 for v in rev]), 'sbc': series([v * .02 for v in rev]),
               'ni': series([v * .15 for v in rev]), 'shares': {'2025-09-30': (1000., '2025-11-01'), '2024-09-30': (1010., '2024-11-01')},
               'cash': {'2025-12-31': (300., '2026-02-09')}, 'sti': {}, 'ltd': {'2025-12-31': (100., '2026-02-09')}, 'ltd_cur': {},
               'equity': {'2025-12-31': (900., '2026-02-09')}, 'goodwill': {}, 'name': 'PIT Synthetic'}
        t = date(2026, 2, 20)                                            # 16 quarters 2022Q1..2025Q4 usable
        r = S.rows_from_pit({'PITS': rec}, {'PITS': {'price': 10.}}, t, {'PITS': {'sector': 'Industrials'}})[0]
        m = r['metrics']
        self.assertEqual((r['status'], r['data_grade'], r['sector']), ('ok', 'pit_sec', 'Industrials'))
        self.assertAlmostEqual(m['revenue_growth'], 1.05 ** 4 - 1, places=9)
        self.assertEqual(len(m['revenue_growth_series']), 3)
        self.assertAlmostEqual(m['net_debt'], -200.)
        self.assertAlmostEqual(m['market_cap'], 10000.)
        self.assertAlmostEqual(m['gross_margin'], .6)
        early = S.rows_from_pit({'PITS': rec}, {'PITS': {'price': 10.}}, date(2023, 6, 1))[0]
        self.assertEqual(early['status'], 'no_data')                     # fewer than 8 usable quarters then
        before_cash = S.rows_from_pit({'PITS': rec}, {'PITS': {'price': 10.}}, date(2026, 2, 5))[0]
        self.assertNotIn('net_debt', before_cash['metrics'])               # 2025-12-31 balance sheet not yet filed: debt unknown,
        self.assertNotIn('ev', before_cash['metrics'])                     # never 0 (which would read as net cash)
        self.assertNotIn('roic', before_cash['metrics'])

    def test_sec_frames_rows_are_accepted(self):
        cur = [{'symbol': 'FRM', 'cik': 1, 'name': 'Frame Co', 'sector': 'Industrials', 'industry': 'Machinery', 'revenue': 1100.,
                'gross_profit': 440., 'operating_income': 165., 'net_income': 120., 'cfo': 180., 'capex': 40., 'fcf': 140., 'sbc': 10.,
                'cash': 200., 'short_term_investments': 0., 'debt': 300., 'debt_scope': 'ambiguous', 'equity': 900., 'total_assets': 2000.,
                'goodwill': 100., 'shares_outstanding': 100., 'data_grade': 'sec_frames', 'source': 'SEC XBRL frames'}]
        prev = [dict(cur[0], revenue=1000., gross_profit=380., operating_income=140., fcf=120., shares_outstanding=101.)]
        r = S.rows_from_sec_frames(cur, {'FRM': {'price': 30.}}, history=[prev], as_of=AS_OF)[0]
        m = r['metrics']
        self.assertAlmostEqual(m['revenue_growth'], .1)
        self.assertAlmostEqual(m['market_cap'], 3000.)
        self.assertAlmostEqual(m['ev'], 3100.)
        self.assertIn('ambiguous', r['basis']['net_debt'])
        out = S.scan(cur)                                                 # flat sec_frames rows convert automatically
        self.assertIn('FRM', out['dimensions'])


def fmp(sym, mcap, nd, rev, gm, om, nm, fcfm, sbcm, roic, growth, dom=0., dgm=0., dfm=None, dil=0., cons=None, pio=7, z=5., icov=None):
    """SYNTHETIC FMP-shaped screening record (as runs/universe_*/batches lines). growth: FY revenue growth oldest first;
    dom/dgm/dfm: last-FY change in operating/gross/FCF margin, written as vendor item growth with its raw sign."""
    fcf, ni, ev = rev * fcfm, rev * nm, mcap + nd
    g = growth[-1]
    item = lambda m1, d: None if d is None or m1 - d == 0 else ((1 + g) * m1 - (m1 - d)) / (m1 - d)
    gr = [{'date': f'{2025 - len(growth) + 1 + i}-12-31', 'revenueGrowth': x, 'grossProfitGrowth': .0 if i < len(growth) - 1 else item(gm, dgm),
           'operatingIncomeGrowth': None if i < len(growth) - 1 else item(om, dom), 'weightedAverageSharesDilutedGrowth': dil,
           'freeCashFlowGrowth': None if i < len(growth) - 1 else item(fcfm, dfm)} for i, x in enumerate(growth)]
    est = [{'date': f'{y}-12-31', 'revenueAvg': rev * 1.03 * (1 + cons) ** k, 'numAnalystsRevenue': 20}
           for k, y in enumerate((2026, 2027, 2028))] if cons is not None else []
    return {'symbol': sym, 'retrieved_utc': AS_OF + 'T20:00:00Z', 'errors': {},
            'km': {'marketCap': mcap, 'enterpriseValueTTM': ev, 'evToFreeCashFlowTTM': ev / fcf if fcf else None,
                   'evToEBITDATTM': ev / (rev * (om + .04)) if om + .04 else None, 'freeCashFlowYieldTTM': fcf / mcap,
                   'returnOnInvestedCapitalTTM': roic, 'stockBasedCompensationToRevenueTTM': sbcm},
            'rt': {'grossProfitMarginTTM': gm, 'operatingProfitMarginTTM': om, 'netProfitMarginTTM': nm, 'interestCoverageRatioTTM': icov,
                   'priceToEarningsRatioTTM': mcap / ni if ni else None, 'freeCashFlowPerShareTTM': fcf / 1e9,
                   'revenuePerShareTTM': rev / 1e9, 'netIncomePerShareTTM': ni / 1e9},
            'growth': gr, 'scores': {'altmanZScore': z, 'piotroskiScore': pio, 'revenue': rev, 'marketCap': mcap}, 'estimates': est}


B = 1e9
GOOG_LIKE = ('GOOGLIKE', 2000 * B, -80 * B, 370 * B, .59, .32, .29, .20, .065, .30, [.09, .14, .14])
FILLERS = [fmp(f'F{i}', mc, 2 * B, rev, .40, .15, .11, .10, .01, .15, [.05, .05, .06], cons=.05, z=4)
           for i, (mc, rev) in enumerate(((50 * B, 20 * B), (40 * B, 15 * B), (30 * B, 10 * B), (20 * B, 8 * B)))]


def fmp_scan(recs, sectors, industries=None, **opts):
    meta = [{'symbol': r['symbol'], 'sector': sectors.get(r['symbol'], 'Industrials'),
             'sub_industry': (industries or {}).get(r['symbol'], 'Diversified Support Services')} for r in recs]
    return S.scan(S.rows_from_fmp_screen(recs, meta, as_of=AS_OF), {'as_of': AS_OF, **opts})


class ReviewRegressions(unittest.TestCase):
    """Discovery review findings (SYNTHETIC companies, FMP-shaped records through the adapter)."""

    def test_google_like_megacap_is_found_at_a_fair_price(self):
        out = fmp_scan([fmp(*GOOG_LIKE, dom=.02, dgm=.01, dil=-.02, cons=.11, z=12)] + FILLERS,
                       {'GOOGLIKE': 'Communication Services'}, {'GOOGLIKE': 'Interactive Media & Services'})
        self.assertIn('GOOGLIKE', syms(out, 'quality_fair_price'))           # P/E ~19, 11% consensus, ROIC 30%
        pi = out['dimensions']['GOOGLIKE']['priced_in']
        self.assertIn('NOPAT', pi['multiple_basis'])                          # capex/SBC do not read as an expensive price
        self.assertIn(pi['level'], ('strong', 'adequate'))
        self.assertGreater(pi['cross_check']['multiple'], 35)                 # owner-FCF view still shown (~40x)

    def test_return_proxy_is_owner_fcf_over_equity(self):
        cash = fmp('CASHBOX', 10 * B, -8.5 * B, 6 * B, .50, .12, .10, .10, .005, .30, [.03, .02, .04], cons=.03, z=8)
        out = fmp_scan([cash] + FILLERS, {'CASHBOX': 'Consumer Discretionary'}, {'CASHBOX': 'Apparel Retail'})
        er, m = out['dimensions']['CASHBOX']['expected_return_proxy'], out['dimensions']['CASHBOX']['metrics']
        self.assertLess(er['value'], .12)                                     # was 41%: FCF incl. interest on cash / EV of 1.5bn
        self.assertAlmostEqual(er['components']['owner_fcf_yield'], m['owner_fcf'] / m['market_cap'])

    def test_cyclical_peak_is_not_ranked_as_a_compounder(self):
        peak = fmp('PEAKCYC', 12 * B, -1 * B, 20 * B, .25, .18, .14, .12, .002, .22, [.05, .35, .30], dgm=.04, dom=.05, dil=-.03,
                   cons=-.10, pio=8, z=4)
        grow = fmp('BUILDER', 25 * B, 1 * B, 8 * B, .22, .13, .09, .10, .01, .21, [.15, .18, .16], dom=.015, cons=.14, z=5, icov=20)
        out = fmp_scan([peak, grow] + FILLERS, {'PEAKCYC': 'Materials'}, {'PEAKCYC': 'Steel', 'BUILDER': 'Construction & Engineering'})
        for ln in ('quality_compounder', 'quality_fair_price', 'mispriced_turning'):
            self.assertNotIn('PEAKCYC', syms(out, ln), ln)
            self.assertIn(('PEAKCYC', 'not_cyclical_peak'), [(x['symbol'], x['failed']) for x in out['near_misses'][ln]], ln)
        d = out['dimensions']['PEAKCYC']
        self.assertIn('cyclical_peak', [f['code'] for f in d['risk']['flags']])
        self.assertEqual(d['growth_quality']['inputs']['consensus_outlook']['signal'], -1)
        self.assertNotIn('PEAKCYC', [e['symbol'] for e in out['shortlist']])
        builder = next(c for c in out['lenses']['quality_compounder'] if c['symbol'] == 'BUILDER')
        self.assertIn('through_cycle', builder['tags'])                       # cyclical industry, no peak signs: tagged, not barred

    def test_fmp_loss_making_grower_can_prove_commercial_traction(self):
        burn = fmp('BURN', 12 * B, -1.5 * B, 1 * B, .75, -.12, -.10, -.03, .18, -.10, [.55, .48, .42], dgm=.02, dom=.18, dfm=.12,
                   dil=.04, cons=.35, pio=5, z=6)
        row = S.rows_from_fmp_screen([burn], [{'symbol': 'BURN', 'sector': 'Information Technology', 'sub_industry': 'Systems Software'}],
                                     as_of=AS_OF)[0]
        self.assertAlmostEqual(row['metrics']['operating_margin_trend'], .18)  # -30% -> -12%, from a negative base
        self.assertAlmostEqual(row['metrics']['fcf_margin_change'], .12)       # freeCashFlowGrowth: -15% -> -3%
        out = S.scan([row], {'as_of': AS_OF})
        self.assertEqual(syms(out, 'early_commercial_growth'), ['BURN'])
        self.assertEqual(out['lenses']['early_commercial_growth'][0]['ordering']['fields'][0]['value'], 4)

    def test_structural_lens_needs_per_share_funding_and_a_price_check(self):
        story = fmp('STORY30', 6 * B, -1.5 * B, .3 * B, .45, -.60, -.62, -.55, .25, None, [.60, .50, .45], dgm=.03, dom=.15, dil=.30,
                    cons=.45, pio=3, z=3.1)
        burn = fmp('BURN', 12 * B, -1.5 * B, 1 * B, .75, -.12, -.10, -.03, .18, -.10, [.55, .48, .42], dgm=.02, dom=.18, dil=.04, cons=.35, z=6)
        out = fmp_scan([story, burn] + FILLERS, {})
        self.assertNotIn('STORY30', syms(out, 'structural_tailwind'))
        self.assertNotIn('STORY30', [e['symbol'] for e in out['shortlist']])
        nm = {x['symbol']: x for x in out['near_misses']['structural_tailwind']}
        self.assertEqual(nm['BURN']['failed'], 'not_already_priced')          # numbers only, price check unavailable
        themed = fmp_scan([story, burn] + FILLERS, {}, themes={'BURN': ['grid software'], 'STORY30': ['electrification']})
        self.assertIn('BURN', syms(themed, 'structural_tailwind'))
        nm = {x['symbol']: x for x in themed['near_misses']['structural_tailwind']}
        self.assertEqual(nm['STORY30']['failed'], 'per_share_funding')        # a theme does not excuse 30%/yr dilution

    def test_missing_debt_is_never_net_cash_in_factbook_rows(self):
        fb = load_fix()
        debt = ('long_term_debt', 'long_term_debt_tagged', 'current_portion_ltd', 'short_term_borrowings')
        fb['facts'] = [f for f in fb['facts'] if f['metric'] not in debt]
        m = S.rows_from_factbooks([fb])[0]['metrics']
        self.assertGreater(m['market_cap'], 0)
        self.assertNotIn('net_debt', m)
        self.assertNotIn('ev', m)

    def test_sector_cap_without_sector_uses_industry_or_nothing(self):
        recs = [fmp(f'Q{i}', 30 * B, -1 * B, 10 * B, .5, .25, .2, .18, .01, .25, [.08, .09, .10], dil=-.01, cons=.08, z=6)
                for i in range(5)]
        meta = [{'symbol': r['symbol'], 'sub_industry': 'Household Products' if i < 3 else None} for i, r in enumerate(recs)]
        out = S.scan(S.rows_from_fmp_screen(recs, meta, as_of=AS_OF), {'as_of': AS_OF})
        fair = syms(out, 'quality_fair_price')
        self.assertEqual(len(fair), 4)                                        # 2 Household Products + 2 without any label
        capped = [x for x in out['near_misses']['quality_fair_price'] if x['failed'] == 'sector_cap']
        self.assertEqual(len(capped), 1)
        self.assertIn('Household Products', capped[0]['reason'])

    def test_unsupported_reuses_model_card_text_rules(self):
        recs = [fmp(sym, 300 * B, 150 * B, 80 * B, .5, .38, .28, .30, .01, .11, [.05, .06, .04], cons=.04, z=.3)
                for sym in ('BANKF', 'MREIT', 'PAYNET')]
        recs[2] = fmp('PAYNET', 650 * B, 5 * B, 40 * B, .80, .67, .53, .52, .03, .40, [.11, .10, .11], dil=-.02, cons=.10, z=9)
        out = fmp_scan(recs + FILLERS, {'BANKF': 'Financial Services', 'MREIT': 'Real Estate', 'PAYNET': 'Financials'},
                       {'BANKF': 'Banks - Diversified', 'MREIT': 'REIT - Mortgage', 'PAYNET': 'Transaction & Payment Processing Services'})
        self.assertTrue(out['excluded']['BANKF'].startswith('not supported'))
        self.assertTrue(out['excluded']['MREIT'].startswith('not supported'))
        self.assertFalse(out['excluded'].get('PAYNET', '').startswith('not supported'))
        self.assertEqual(out['universe']['not_supported'], 2)


if __name__ == '__main__':
    unittest.main()
