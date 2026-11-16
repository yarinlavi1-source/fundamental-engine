"""Quality + trend screen: TTM and the derived fourth quarter, each gate at its boundary, routing, trend, ranking, and the
saved-valuation mark (no network)."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from fundamental_engine import quality_momentum as qm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AS_OF = '2026-11-15'
# fiscal year = calendar year; the fourth quarter is reported only inside the full year
Q_ENDS = [('2024-01-01', '2024-03-31'), ('2024-04-01', '2024-06-30'), ('2024-07-01', '2024-09-30'), ('2024-10-01', '2024-12-31'),
          ('2025-01-01', '2025-03-31'), ('2025-04-01', '2025-06-30'), ('2025-07-01', '2025-09-30'), ('2025-10-01', '2025-12-31'),
          ('2026-01-01', '2026-03-31'), ('2026-04-01', '2026-06-30'), ('2026-07-01', '2026-09-30')]


def income_facts(vals):
    """Facts the way a 10-Q/10-K income statement reports: Q1-Q3 as 3-month facts, 9-month year-to-date, the full year."""
    out = []
    for (s, e), v in zip(Q_ENDS, vals):
        if not e.endswith('12-31'):
            out.append({'start': s, 'end': e, 'val': v})
    for y in (2024, 2025, 2026):
        q = [v for (s, e), v in zip(Q_ENDS, vals) if s.startswith(str(y))]
        if len(q) >= 3:
            out.append({'start': f'{y}-01-01', 'end': f'{y}-09-30', 'val': sum(q[:3])})
        if len(q) == 4:
            out.append({'start': f'{y}-01-01', 'end': f'{y}-12-31', 'val': sum(q)})
    return out


def ytd_facts(vals):
    """Facts the way a cash-flow statement reports: year-to-date only (3, 6, 9, 12 months)."""
    out = []
    for y in (2024, 2025, 2026):
        run = 0
        for (s, e), v in zip(Q_ENDS, vals):
            if s.startswith(str(y)):
                run += v
                out.append({'start': f'{y}-01-01', 'end': e, 'val': run})
    return out


def year_facts(by_year):
    """Full-year revenue facts for earlier fiscal years: {2022: 200, ...}."""
    return [{'start': f'{y}-01-01', 'end': f'{y}-12-31', 'val': v} for y, v in sorted(by_year.items())]


def series(rev, op, cfo, capex, shares=(100, 100), equity=(1000, 1100), ni=None, history=None):
    """history: full-year revenue before 2024 (default 200 and 260: with 320 and 400 after them, 26% a year, no down year)."""
    history = {2022: 200, 2023: 260} if history is None else history
    return {'taxonomy': 'us-gaap', 'currency': 'USD', 'forms': {'10-Q': '2026-10-30', '10-K': '2026-02-20'},
            'durations': {'revenue': year_facts(history) + income_facts(rev), 'operating_income': income_facts(op), 'cfo': ytd_facts(cfo),
                          'capex': ytd_facts(capex), 'net_income': income_facts(ni or op),
                          'diluted_shares': [{'start': '2025-07-01', 'end': '2025-09-30', 'val': shares[0]},
                                             {'start': '2026-07-01', 'end': '2026-09-30', 'val': shares[1]}]},
            'instants': {'equity': [{'end': '2025-09-30', 'val': equity[0]}, {'end': '2026-09-30', 'val': equity[1]}]}}


REV = [80, 80, 80, 80, 100, 100, 100, 100, 100, 110, 130]          # TTM 440 against 400; quarter 130 against 100
OP = [8, 8, 8, 8, 10, 10, 10, 10, 20, 22, 26]
CFO = [9] * 8 + [30, 30, 30]
CAPEX = [1] * 8 + [5, 5, 5]


def metrics(**kw):
    m = {'basis': 'quarterly', 'rev_growth_ttm': 0.20, 'rev_growth_q': 0.20, 'operating_income': 5., 'margin_change_pts': 0.5,
         'fcf': 3., 'share_change': 0.01, 'rev_cagr_3y': 0.20, 'rev_min_year_growth': 0.05}
    m.update(kw)
    return m


def passes(gates, name):
    return next(g['pass'] for g in gates if g['gate'] == name)


def gate(gates, name):
    return next(g for g in gates if g['gate'] == name)


class Series(unittest.TestCase):
    def test_fourth_quarter_is_full_year_minus_nine_months(self):
        q = qm.quarterly(income_facts(REV))
        self.assertEqual(q['2025-12-31'], 100)
        self.assertEqual(q['2024-12-31'], 80)
        self.assertEqual(len(q), 11)

    def test_cash_flow_quarters_from_year_to_date(self):
        q = qm.quarterly(ytd_facts(CFO))
        self.assertEqual([q[e] for _, e in Q_ENDS], CFO)

    def test_fourth_quarter_from_three_quarters_when_nine_months_missing(self):
        facts = [f for f in income_facts(REV) if not (f['start'].endswith('01-01') and f['end'].endswith('09-30'))]
        self.assertEqual(qm.quarterly(facts)['2025-12-31'], 100)

    def test_three_month_fact_repeating_the_full_year_is_ignored(self):
        facts = income_facts(REV) + [{'start': '2025-10-01', 'end': '2025-12-31', 'val': 400}]      # the 2025 total, mis-tagged
        self.assertEqual(qm.quarterly(facts)['2025-12-31'], 100)

    def test_one_revenue_concept_for_every_period(self):
        total = [dict(f, tag='Revenues') for f in income_facts(REV)]
        part = [dict(f, val=f['val'] / 2, tag='Part') for f in income_facts(REV)]
        merged = sorted(total[:8] + part[8:], key=lambda f: (f['end'], f['start']))      # a component in the newer periods
        facts, mixed = qm.pick_revenue({'revenue': merged, 'revenue_by_tag': {'Revenues': total, 'Part': part}}, AS_OF)
        self.assertFalse(mixed)
        self.assertEqual({f['tag'] for f in facts}, {'Revenues'})
        facts, mixed = qm.pick_revenue({'revenue': merged}, AS_OF)
        self.assertTrue(mixed)
        s = series(REV, OP, CFO, CAPEX)
        s['durations']['revenue'] = merged
        r = qm.evaluate({'symbol': 'M', 'sector': 'Energy'}, s, '1311', AS_OF)
        self.assertIsNone(passes(r['quality'], 'rev_growth_ttm'))
        self.assertIsNone(passes(r['quality'], 'rev_growth_q'))

    def test_quarter_of_a_missing_report_is_year_to_date_minus_the_last_quarter(self):
        facts = [{'start': '2026-01-01', 'end': '2026-06-30', 'val': 246}, {'start': '2026-04-01', 'end': '2026-06-30', 'val': 129},
                 {'start': '2026-01-01', 'end': '2026-09-30', 'val': 392}, {'start': '2026-07-01', 'end': '2026-09-30', 'val': 146}]
        self.assertEqual(qm.quarterly(facts), {'2026-03-31': 117, '2026-06-30': 129, '2026-09-30': 146})
        only_ytd = [{'start': '2026-01-01', 'end': '2026-09-30', 'val': 392}, {'start': '2026-07-01', 'end': '2026-09-30', 'val': 146}]
        self.assertEqual(qm.quarterly(only_ytd), {'2026-09-30': 146})                   # six months are not a quarter

    def test_share_counts_are_not_derived(self):
        facts = [{'start': '2025-01-01', 'end': '2025-12-31', 'val': 100}, {'start': '2025-01-01', 'end': '2025-09-30', 'val': 99}]
        self.assertEqual(qm.quarterly(facts, derive=False), {})

    def test_ttm_and_prior_ttm(self):
        q = qm.quarterly(income_facts(REV))
        self.assertEqual(qm.ttm(q, '2026-09-30'), 100 + 100 + 110 + 130)
        self.assertEqual(qm.ttm(q, '2026-09-30', 4), 80 + 100 + 100 + 100)

    def test_ttm_is_none_when_a_quarter_is_missing(self):
        q = qm.quarterly(income_facts(REV))
        del q['2026-03-31']
        self.assertIsNone(qm.ttm(q, '2026-09-30'))

    def test_fundamentals(self):
        m = qm.fundamentals(series(REV, OP, CFO, CAPEX, shares=(100, 102)), AS_OF)
        self.assertEqual(m['basis'], 'quarterly')
        self.assertEqual(m['period_end'], '2026-09-30')
        self.assertAlmostEqual(m['rev_growth_ttm'], 440 / 380 - 1)
        self.assertAlmostEqual(m['rev_growth_q'], 0.30)
        self.assertAlmostEqual(m['op_margin'], 78 / 440)
        self.assertAlmostEqual(m['margin_change_pts'], (78 / 440 - 38 / 380) * 100)
        self.assertEqual(m['fcf'], (9 + 90) - (1 + 15))
        self.assertAlmostEqual(m['share_change'], 0.02)
        self.assertAlmostEqual(m['roe'], 78 / 1050)
        self.assertAlmostEqual(m['bvps_growth'], (1100 / 102) / (1000 / 100) - 1)

    def test_extract_keeps_the_latest_filing_and_the_larger_revenue_tag(self):
        def f(s, e, v, filed):
            return {'start': s, 'end': e, 'val': v, 'filed': filed, 'form': '10-Q'}
        payload = {'cik': 1, 'entityName': 'X', 'facts': {'us-gaap': {
            'Revenues': {'units': {'USD': [f('2026-07-01', '2026-09-30', 50, '2026-10-30')]}},
            'RevenueFromContractWithCustomerExcludingAssessedTax': {'units': {'USD': [f('2026-07-01', '2026-09-30', 130, '2026-10-30')]}},
            'WeightedAverageNumberOfDilutedSharesOutstanding': {'units': {'shares': [
                f('2025-07-01', '2025-09-30', 10, '2025-10-30'), f('2025-07-01', '2025-09-30', 100, '2026-10-30')]}},
            'StockholdersEquity': {'units': {'USD': [{'end': '2026-09-30', 'val': 7, 'filed': '2026-10-30', 'form': '10-Q'}]}}}}}
        s = qm.extract_series(payload)
        self.assertEqual(s['durations']['revenue'][0]['val'], 130)
        self.assertEqual(s['durations']['diluted_shares'][0]['val'], 100)      # restated for a split in the later filing
        self.assertEqual(s['instants']['equity'][0]['val'], 7)
        self.assertEqual(s['currency'], 'USD')

    def test_proxy_statement_fact_does_not_replace_the_annual_report(self):
        def f(v, filed, form):
            return {'start': '2025-01-01', 'end': '2025-12-31', 'val': v, 'filed': filed, 'form': form}
        payload = {'facts': {'us-gaap': {
            'Revenues': {'units': {'USD': [f(4e9, '2026-02-20', '10-K')]}},
            'NetIncomeLoss': {'units': {'USD': [f(3.5e9, '2026-02-20', '10-K'), f(3500, '2026-04-16', 'DEF 14A')]}}}}}
        s = qm.extract_series(payload)
        self.assertEqual([x['val'] for x in s['durations']['net_income']], [3.5e9])     # the proxy repeats it in millions
        self.assertNotIn('DEF 14A', s['forms'])

    def test_nothing_filed_after_the_as_of_date_is_read(self):
        def f(s, e, v, filed):
            return {'start': s, 'end': e, 'val': v, 'filed': filed, 'form': '10-Q'}
        payload = {'facts': {'us-gaap': {'Revenues': {'units': {'USD': [
            f('2026-04-01', '2026-06-30', 100, '2026-08-01'), f('2026-04-01', '2026-06-30', 90, '2026-11-20'),
            f('2026-07-01', '2026-09-30', 120, '2026-11-20')]}}}}}
        s = qm.extract_series(payload, as_of='2026-11-15')
        self.assertEqual([(x['end'], x['val']) for x in s['durations']['revenue']], [('2026-06-30', 100.)])
        late = series(REV, OP, CFO, CAPEX)
        for x in late['durations']['revenue']:
            x['filed'] = '2026-11-20' if x['end'] == '2026-09-30' else '2026-08-01'
        self.assertEqual(qm.fundamentals(late, AS_OF)['period_end'], '2026-06-30')      # a cache built later is still cut

    def test_capex_is_the_total_line_or_the_sum_of_component_lines(self):
        def f(v):
            return {'units': {'USD': [{'start': '2026-01-01', 'end': '2026-09-30', 'val': v, 'filed': '2026-11-05', 'form': '10-Q'}]}}
        rev = {'Revenues': f(100)}
        oil = qm.extract_series({'facts': {'us-gaap': dict(rev, PaymentsToAcquireOilAndGasPropertyAndEquipment=f(46),
                                                           PaymentsToAcquireOtherPropertyPlantAndEquipment=f(4))}})
        self.assertEqual(oil['durations']['capex'][0]['val'], 50)                       # wells + other equipment, not 4
        total = qm.extract_series({'facts': {'us-gaap': dict(rev, PaymentsToAcquirePropertyPlantAndEquipment=f(60),
                                                             PaymentsToAcquireOtherPropertyPlantAndEquipment=f(4))}})
        self.assertEqual(total['durations']['capex'][0]['val'], 60)                     # a component is inside the total
        ship = qm.extract_series({'facts': {'us-gaap': dict(rev, PaymentsForProceedsFromProductiveAssets=f(199),
                                                            PaymentsToAcquireOtherPropertyPlantAndEquipment=f(1))}})
        self.assertEqual(ship['durations']['capex'][0]['val'], 199)                     # vessels sit in the net line

    def test_share_count_equal_for_the_quarter_and_the_year_to_date_is_kept(self):
        sh = [{'start': '2025-07-01', 'end': '2025-09-30', 'val': 100}, {'start': '2025-01-01', 'end': '2025-09-30', 'val': 100},
              {'start': '2026-07-01', 'end': '2026-09-30', 'val': 101}, {'start': '2026-01-01', 'end': '2026-09-30', 'val': 101}]
        self.assertEqual(qm.quarterly(sh, derive=False), {'2025-09-30': 100, '2026-09-30': 101})
        s = series(REV, OP, CFO, CAPEX)
        s['durations']['diluted_shares'] = sh
        self.assertAlmostEqual(qm.fundamentals(s, AS_OF)['share_change'], 0.01)

    def test_split_restated_in_the_later_filing_is_not_dilution(self):
        def f(s, e, v, filed):
            return {'start': s, 'end': e, 'val': v, 'filed': filed, 'form': '10-Q'}
        payload = {'facts': {'us-gaap': {
            'Revenues': {'units': {'USD': [f('2026-07-01', '2026-09-30', 130, '2026-10-30')]}},
            'WeightedAverageNumberOfDilutedSharesOutstanding': {'units': {'shares': [
                f('2025-07-01', '2025-09-30', 100, '2025-10-30'), f('2025-07-01', '2025-09-30', 1000, '2026-10-30'),
                f('2026-07-01', '2026-09-30', 1010, '2026-10-30')]}}}}}
        s = series(REV, OP, CFO, CAPEX)
        s['durations']['diluted_shares'] = qm.extract_series(payload)['durations']['diluted_shares']
        self.assertAlmostEqual(qm.fundamentals(s, AS_OF)['share_change'], 0.01)         # 10-for-1 split, not +910%

    def test_latest_quarter_older_than_five_months_is_flagged(self):
        s = series(REV, OP, CFO, CAPEX)
        self.assertFalse(qm.fundamentals(s, AS_OF)['stale'])
        m = qm.fundamentals(s, '2027-03-10')
        self.assertTrue(m['stale'])
        self.assertTrue(any('five months' in g for g in m['gaps']))

    def test_durability_reads_four_completed_fiscal_years(self):
        d = qm.durability(year_facts({2022: 200, 2023: 260}) + income_facts(REV))
        self.assertEqual(d['fy_ends'], ['2022-12-31', '2023-12-31', '2024-12-31', '2025-12-31'])
        self.assertEqual(d['fy_revenue'], [200, 260, 320, 400])
        self.assertAlmostEqual(d['rev_cagr_3y'], 2 ** (1 / 3) - 1)
        self.assertAlmostEqual(d['rev_min_year_growth'], 320 / 260 - 1)
        self.assertIsNone(qm.durability(year_facts({2023: 260}) + income_facts(REV)))             # three years only
        self.assertIsNone(qm.durability(year_facts({2021: 150, 2023: 260}) + income_facts(REV)))  # a year is missing
        # the concept in use is short: the merge of every revenue concept is read and named
        d = qm.durability(income_facts(REV), year_facts({2022: 200, 2023: 260}) + income_facts(REV))
        self.assertEqual(d['durability_basis'], 'merged_revenue_concepts')

    def test_fiscal_year_filed_after_the_as_of_date_is_not_a_completed_year(self):
        s = series(REV, OP, CFO, CAPEX)
        for f in s['durations']['revenue']:
            f['filed'] = '2027-02-20' if f['end'] == '2025-12-31' and f['start'] == '2025-01-01' else '2026-10-30'
        self.assertIsNone(qm.fundamentals(s, AS_OF).get('rev_cagr_3y'))        # 2022-2024 only: three years

    def test_margin_jump_is_flagged_not_gated(self):
        m = qm.fundamentals(series(REV, [-8, -8, -8, -8, -10, -10, -10, -10, 20, 22, 26], CFO, CAPEX), AS_OF)
        self.assertTrue(m['margin_jump'])
        self.assertTrue(passes(qm.quality_gates(m), 'margin_change_pts'))
        self.assertFalse(qm.fundamentals(series(REV, OP, CFO, CAPEX), AS_OF)['margin_jump'])

    def test_annual_only_filer(self):
        s = {'taxonomy': 'ifrs-full', 'currency': 'TWD', 'forms': {'20-F': '2026-04-15'}, 'instants': {},
             'durations': {'revenue': [{'start': '2024-01-01', 'end': '2024-12-31', 'val': 100}, {'start': '2025-01-01', 'end': '2025-12-31', 'val': 130}],
                           'operating_income': [{'start': '2024-01-01', 'end': '2024-12-31', 'val': 40}, {'start': '2025-01-01', 'end': '2025-12-31', 'val': 55}]}}
        r = qm.evaluate({'symbol': 'F', 'sector': 'Technology'}, s, '3674', AS_OF)
        self.assertEqual((r['route'], r['basis']), ('annual_only', 'annual'))
        self.assertAlmostEqual(r['rev_growth_ttm'], 0.30)
        self.assertNotIn('rev_growth_q', [g['gate'] for g in r['quality']])
        self.assertTrue(r['stale'])                                  # a year that ended 10 months ago is flagged as old
        self.assertIsNone(passes(r['quality'], 'durability'))       # two years of history: the three-year gate is missing


class Gates(unittest.TestCase):
    def test_all_pass(self):
        self.assertTrue(all(g['pass'] for g in qm.quality_gates(metrics())))
        self.assertEqual(len(qm.quality_gates(metrics())), 7)
        self.assertEqual(len(qm.quality_gates(metrics(basis='annual'))), 6)

    def test_boundaries(self):
        for name, key, ok, bad in (('rev_growth_ttm', 'rev_growth_ttm', 0.15, 0.1499), ('rev_growth_q', 'rev_growth_q', 0.15, 0.1499),
                                   ('operating_income', 'operating_income', 0.01, 0.), ('margin_change_pts', 'margin_change_pts', -1.0, -1.01),
                                   ('fcf', 'fcf', 0.01, 0.), ('share_change', 'share_change', 0.03, 0.0301)):
            self.assertTrue(passes(qm.quality_gates(metrics(**{key: ok})), name), name)
            self.assertFalse(passes(qm.quality_gates(metrics(**{key: bad})), name), name)

    def test_durability_gate_needs_both_no_down_year_and_ten_percent_a_year(self):
        for cagr, worst, ok in ((0.10, 0.0, True), (0.0999, 0.05, False), (0.30, -0.001, False), (0.30, 0.0, True)):
            g = qm.quality_gates(metrics(rev_cagr_3y=cagr, rev_min_year_growth=worst))
            self.assertEqual(passes(g, 'durability'), ok, (cagr, worst))
        g = qm.quality_gates(metrics(rev_cagr_3y=None, rev_min_year_growth=None))
        self.assertIsNone(passes(g, 'durability'))
        self.assertEqual(qm.classify(g, good_trend()), 'incomplete')            # a short history is not a pass

    def test_one_strong_year_after_a_fall_fails_durability(self):
        # a commodity upswing: the latest 12 months and the latest quarter grow fast, the fiscal years before fell
        r = qm.evaluate({'symbol': 'O', 'sector': 'Technology'}, series(REV, OP, CFO, CAPEX, history={2022: 500, 2023: 380}), '7372', AS_OF)
        self.assertTrue(passes(r['quality'], 'rev_growth_ttm') and passes(r['quality'], 'rev_growth_q'))
        self.assertFalse(passes(r['quality'], 'durability'))
        self.assertAlmostEqual(r['rev_min_year_growth'], 380 / 500 - 1)
        self.assertEqual(gate(r['quality'], 'durability')['worst_year'], r['rev_min_year_growth'])

    def test_boundary_survives_float_arithmetic(self):
        self.assertTrue(passes(qm.quality_gates(metrics(rev_growth_ttm=115 / 100 - 1)), 'rev_growth_ttm'))

    def test_missing_number_is_not_a_pass(self):
        g = qm.quality_gates(metrics(fcf=None))
        self.assertIsNone(passes(g, 'fcf'))
        self.assertEqual(qm.classify(g, good_trend()), 'incomplete')

    def test_financial_gates(self):
        g = qm.financial_gates({'rev_growth_ttm': 0.01, 'roe': 0.15, 'bvps_growth': 0.001})
        self.assertTrue(all(x['pass'] for x in g))
        self.assertFalse(passes(qm.financial_gates({'rev_growth_ttm': 0.01, 'roe': 0.1499, 'bvps_growth': 0.01}), 'roe'))
        self.assertFalse(passes(qm.financial_gates({'rev_growth_ttm': 0.01, 'roe': 0.2, 'bvps_growth': 0.}), 'bvps_growth'))
        self.assertFalse(passes(qm.financial_gates({'rev_growth_ttm': 0., 'roe': 0.2, 'bvps_growth': 0.1}), 'rev_growth_ttm'))


def bars(daily, n=260, start=100.):
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * (1 + daily))
    return out


def good_trend():
    return qm.trend_gates(qm.trend_measures(bars(0.002)), qm.trend_measures(bars(0.0005)))


class Trend(unittest.TestCase):
    def test_measures(self):
        c = [float(i) for i in range(1, 301)]                       # 1..300, rising one a day
        t = qm.trend_measures(c)
        self.assertAlmostEqual(t['ret_12_1'], 279 / 48 - 1)        # the close 21 days ago over the close 252 days ago
        self.assertAlmostEqual(t['ma200'], sum(range(101, 301)) / 200)
        self.assertAlmostEqual(t['ma200_21d_ago'], sum(range(80, 280)) / 200)
        self.assertEqual(t['high_52w'], 300)
        self.assertEqual(t['below_high'], 0)

    def test_high_and_average_use_split_adjusted_prices_and_the_return_uses_total_return(self):
        close = [100.] * 300
        high = [100.] * 300
        high[-100] = 160.                                           # an intraday high the closes never show
        adj = [80. + 20. * i / 299 for i in range(300)]             # dividends lower the adjusted past
        t = qm.trend_measures(adj, close, high)
        self.assertEqual(t['high_52w'], 160.)
        self.assertAlmostEqual(t['below_high'], 100. / 160. - 1)    # 37.5% below: a falling knife; adjusted closes say 0%
        self.assertEqual(t['ma200'], 100.)
        self.assertAlmostEqual(t['ret_12_1'], adj[-22] / adj[-253] - 1)
        self.assertEqual(qm.trend_measures(adj)['below_high'], 0)   # adjusted closes alone stand in when nothing else is saved

    def test_short_history_is_missing(self):
        self.assertIsNone(qm.trend_measures(bars(0.001, n=252)))
        g = qm.trend_gates(None, qm.trend_measures(bars(0.001)))
        self.assertTrue(all(x['pass'] is None for x in g))

    def test_gates(self):
        spy = qm.trend_measures(bars(0.0005))
        self.assertTrue(all(g['pass'] for g in good_trend()))
        weak = qm.trend_gates(qm.trend_measures(bars(0.0002)), spy)
        self.assertFalse(passes(weak, 'rel_strength'))
        self.assertTrue(passes(weak, 'above_rising_200d'))
        falling = qm.trend_gates(qm.trend_measures(bars(-0.001)), spy)
        self.assertFalse(passes(falling, 'above_rising_200d'))

    def test_distance_from_high_boundary(self):
        spy = qm.trend_measures(bars(0.))
        base = bars(0.)
        at = base[:-40] + [200.] + base[-39:-1] + [150.]            # exactly 25% below the high
        below = base[:-40] + [200.] + base[-39:-1] + [149.]
        self.assertTrue(passes(qm.trend_gates(qm.trend_measures(at), spy), 'near_high'))
        self.assertFalse(passes(qm.trend_gates(qm.trend_measures(below), spy), 'near_high'))

    def test_price_above_a_falling_average_fails(self):
        c = bars(-0.002)
        c[-1] = c[-150]                                             # a bounce above the average, which still falls
        t = qm.trend_measures(c)
        self.assertGreater(t['last'], t['ma200'])
        g = qm.trend_gates(t, qm.trend_measures(bars(-0.01)))
        self.assertFalse(passes(g, 'above_rising_200d'))


class Classify(unittest.TestCase):
    def test_pass_near_miss_fail(self):
        self.assertEqual(qm.classify(qm.quality_gates(metrics()), good_trend()), 'pass')
        self.assertEqual(qm.classify(qm.quality_gates(metrics(share_change=0.05)), good_trend()), 'near_miss')
        self.assertEqual(qm.classify(qm.quality_gates(metrics(share_change=0.05, fcf=-1.)), good_trend()), 'fail')

    def test_one_trend_failure_is_a_near_miss_two_gates_is_a_fail(self):
        weak = qm.trend_gates(qm.trend_measures(bars(0.0002)), qm.trend_measures(bars(0.0005)))
        self.assertEqual(qm.classify(qm.quality_gates(metrics()), weak), 'near_miss')
        self.assertEqual(qm.classify(qm.quality_gates(metrics(fcf=-1.)), weak), 'fail')

    def test_trend_is_fetched_only_within_one_quality_failure(self):
        two = qm.quality_gates(metrics(share_change=0.05, fcf=-1.))
        self.assertFalse(qm.needs_trend(two))
        self.assertEqual(qm.classify(two, None), 'fail')
        self.assertTrue(qm.needs_trend(qm.quality_gates(metrics(fcf=-1.))))


class Routing(unittest.TestCase):
    def test_financials(self):
        self.assertTrue(qm.is_financial('6021', 'Technology'))
        self.assertTrue(qm.is_financial('6331', ''))
        self.assertTrue(qm.is_financial(None, 'Finance'))
        self.assertFalse(qm.is_financial('7389', 'Consumer Discretionary'))
        self.assertFalse(qm.is_financial('6798', 'Real Estate'))        # a REIT is outside 6000-6499

    def test_a_bank_never_meets_the_operating_gates(self):
        r = qm.evaluate({'symbol': 'B', 'sector': 'Finance'}, series(REV, OP, CFO, CAPEX), '6021', AS_OF)
        self.assertEqual(r['route'], 'financial')
        self.assertEqual([g['gate'] for g in r['quality']], ['rev_growth_ttm', 'roe', 'bvps_growth'])

    def test_standard_route(self):
        r = qm.evaluate({'symbol': 'S', 'sector': 'Technology'}, series(REV, OP, CFO, CAPEX), '7372', AS_OF)
        self.assertEqual(r['route'], 'standard')
        self.assertTrue(all(g['pass'] for g in r['quality']))

    def test_commodity_names_are_routed_to_their_own_table(self):
        for sic in ('1311', '2911', '1040', '3312', '2873', '4412', '4922', 1381):
            self.assertTrue(qm.is_commodity(sic, 'X Corp'), sic)
        for sic in ('7372', '3674', '3357', '5500', None, 'n/a'):
            self.assertFalse(qm.is_commodity(sic, 'X Corp'), sic)
        self.assertTrue(qm.is_commodity('4400', 'International Seaways Inc. Common Stock'))
        self.assertFalse(qm.is_commodity('4400', 'Royal Caribbean Cruises Ltd. Common Stock'))
        r = qm.evaluate({'symbol': 'O', 'name': 'Oil Co', 'sector': 'Energy'}, series(REV, OP, CFO, CAPEX), '1311', AS_OF)
        self.assertEqual(r['route'], 'commodity')                   # every gate passes, and it is still not a standard row
        self.assertTrue(all(g['pass'] for g in r['quality']))
        self.assertEqual(r['commodity_group'], 'crude petroleum and natural gas')

    def test_universe(self):
        rows = [{'symbol': 'AAA', 'name': 'Alpha Inc. Class A Common Stock', 'marketCap': '9000000000', 'lastsale': '$10', 'volume': '100'},
                {'symbol': 'AAB', 'name': 'Alpha Inc. Class B Common Stock', 'marketCap': '9000000000', 'lastsale': '$10', 'volume': '900'},
                {'symbol': 'AA^P', 'name': 'Alpha Inc. 5% Preferred', 'marketCap': '9000000000', 'lastsale': '$25', 'volume': '9'},
                {'symbol': 'AAW', 'name': 'Alpha Inc. Warrants', 'marketCap': '9000000000', 'lastsale': '$1', 'volume': '9'},
                {'symbol': 'SML', 'name': 'Small Co Common Stock', 'marketCap': '4000000000', 'lastsale': '$1', 'volume': '9'},
                {'symbol': 'BK/B', 'name': 'Book Co Class B', 'marketCap': '100', 'lastsale': '$1', 'volume': '9'},
                {'symbol': 'ADR', 'name': 'Foreign SA American Depositary Shares each representing one unit', 'marketCap': '7e9', 'lastsale': '$5', 'volume': '9'},
                {'symbol': 'NOC', 'name': 'No Cik Corp', 'marketCap': '8e9', 'lastsale': '$5', 'volume': '9'}]
        out, counts, no_cik = qm.build_universe(rows, {'AAA': 1, 'AAB': 1, 'SML': 2, 'BK-B': 3, 'ADR': 4}, 5e9, keep=['BK-B'])
        self.assertEqual([r['symbol'] for r in out], ['AAB', 'ADR', 'BK-B'])
        self.assertEqual(out[0]['other_classes'], ['AAA'])
        self.assertFalse(out[2]['in_universe'])
        self.assertEqual(counts['companies'], 2)
        self.assertEqual([x['symbol'] for x in no_cik], ['NOC'])


class Ranking(unittest.TestCase):
    def test_percentiles(self):
        self.assertEqual(qm.percentile_ranks([1, 2, 3]), [0., 50., 100.])
        self.assertEqual(qm.percentile_ranks([5, 5]), [50., 50.])
        self.assertEqual(qm.percentile_ranks([7]), [50.])

    def test_order_and_components(self):
        rows = [{'symbol': 'LOW', 'rev_growth_ttm': 0.15, 'margin_change_pts': 0., 'fcf_margin': 0.05, 'rel_strength': 0.01},
                {'symbol': 'TOP', 'rev_growth_ttm': 0.60, 'margin_change_pts': 5., 'fcf_margin': 0.40, 'rel_strength': 0.50},
                {'symbol': 'MID', 'rev_growth_ttm': 0.30, 'margin_change_pts': 6., 'fcf_margin': 0.20, 'rel_strength': 0.10}]
        r = qm.rank(rows)
        self.assertEqual([x['symbol'] for x in r], ['TOP', 'MID', 'LOW'])
        self.assertEqual(r[0]['rank_parts'], {'rev_growth_ttm': 100., 'margin_change_pts': 50., 'fcf_margin': 100., 'rel_strength': 100.})
        self.assertEqual(r[0]['rank_score'], 350.)
        self.assertEqual(r[2]['rank_score'], 0.)

    def test_price_columns(self):
        p = qm.price_columns({'net_income': 50., 'revenue': 200., 'fcf': 40.}, 1000.)
        self.assertEqual((p['pe'], p['ps'], p['fcf_yield']), (20., 5., 0.04))
        self.assertIsNone(qm.price_columns({'net_income': -5., 'revenue': 200., 'fcf': 40.}, 1000.)['pe'])
        self.assertEqual(qm.price_columns({'net_income': 300., 'revenue': 3000., 'fcf': 0.}, 1000., fx=30.)['pe'], 100.)


class Report(unittest.TestCase):
    def test_hebrew_report_renders(self):
        r = qm.evaluate({'symbol': 'S', 'name': 'S Corp', 'sector': 'Technology', 'market_cap': 9e9}, series(REV, OP, CFO, CAPEX), '7372', AS_OF)
        spy = qm.trend_measures(bars(0.0005))
        r['trend_measures'] = qm.trend_measures(bars(0.002))
        r['trend'] = qm.trend_gates(r['trend_measures'], spy)
        r['rel_strength'] = r['trend_measures']['ret_12_1'] - spy['ret_12_1']
        r.update(qm.price_columns(r, r['market_cap']), status='pass', failed=[], missing=[])
        miss = dict(r, symbol='M', status='near_miss', failed=[dict(gate(r['quality'], 'share_change'), value=0.05)])
        slow = dict(r, symbol='D', status='near_miss', failed=[dict(gate(r['quality'], 'durability'), value=0.04, worst_year=-0.02)])
        oil = dict(r, symbol='O', route='commodity', sic='1311', status='pass')
        bank = dict(r, symbol='B', status='near_miss', failed=[qm.financial_gates({'rev_growth_ttm': 0.1, 'roe': 0.13, 'bvps_growth': 0.1})[1]])
        gap = dict(r, symbol='U', status='incomplete', failed=[], missing=[dict(gate(r['quality'], 'fcf'), value=None, **{'pass': None})])
        res = {'as_of': AS_OF, 'min_cap': 5e9, 'spy': spy, 'counts': {'companies': 2, 'passers': 1, 'near_misses': 1},
               'counts_he': [['x', 1]], 'gaps_he': ['g'], 'passers': qm.rank([r]), 'near_misses': [miss, slow], 'commodity': [oil], 'financials': [],
               'financial_near_misses': [bank], 'unread': [gap],
               'annual_only': [], 'thesis_book': [dict(r), {'symbol': 'Z', 'status': 'not_found', 'note': 'n'}]}
        text = qm.render_he(res)
        for needle in ('לא הערכת שווי ולא המלצת קנייה', 'לא נבדק על העבר', '| S |', 'מספר המניות לא גדל מעל 3%', '+5.0%',
                       'B (תשואה על ההון 15% ומעלה: +13.0%)', '| U |', 'תזרים מזומנים חופשי חיובי', '2026-09-30',
                       'ברבעון האחרון מול אותו רבעון לפני שנה (15% ומעלה)', 'לא ירדו באף אחת משלוש שנות הכספים',
                       '+4.0% בשנה, השנה החלשה -2.0%', 'חברות סחורות מחזוריות: הצמיחה הולכת אחרי המחירים', '| O |', 'עברה את התנאים'):
            self.assertIn(needle, text)
        passers = text.split('## כמעט עברו')[0]
        self.assertNotIn('| O |', passers)


# ---------------------------------------------------------------- saved valuations (the MSFT slip)

LEAN = qm.lean_module()
FIXTURE = os.path.join(REPO, 'tests', 'fixtures', 'lean_model_dated.json')     # ANET 04.11: price 182.95, buy-test price 150.99


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f)


def lean_folder(root, ticker, day):
    with open(FIXTURE, encoding='utf-8') as f:
        p = json.load(f)
    p['ticker'] = ticker
    _write(os.path.join(root, 'companies', ticker, day, 'lean_params.json'), p)
    _write(os.path.join(root, 'companies', ticker, day, 'lean_result.json'), LEAN.run(copy.deepcopy(p)))


def research_tree(root):
    """PASSCO and FAILCO hold the same lean valuation (04.11); REJCO and CUST exist only as rejected checks; ENG holds a
    heavy-engine folder; LATE holds a lean valuation and a newer rejected check."""
    for t in ('PASSCO', 'FAILCO', 'LATE'):
        lean_folder(root, t, '2026-11-04')
    _write(os.path.join(root, 'companies', 'ENG', '2026-11-01', 'casefile_summary.json'), {})
    rows = [{'ticker': 'REJCO', 'date': '2026-11-05', 'model': 'lean', 'price': 150.0, 'base_value_today': 120.0,
             'buy_test_pass': False, 'buy_test_price': 100.0, 'verdict': 'not a buy'},
            {'ticker': 'CUST', 'date': '2026-11-03', 'model': 'custom', 'price': 100.0, 'buy_test_pass': False, 'buy_test_price': None,
             'margin_of_safety_price_13pct': 70.0, 'verdict': 'not a buy'},
            {'ticker': 'LATE', 'date': '2026-11-10', 'model': 'lean', 'price': 180.0, 'buy_test_pass': False, 'buy_test_price': 100.0,
             'verdict': 'not a buy'}]
    with open(os.path.join(root, 'rejected.jsonl'), 'w', encoding='utf-8') as f:
        f.write(''.join(json.dumps(r) + '\n' for r in rows))


class SavedValuation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        research_tree(self.root)
        self.book = qm.saved_book(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def sv(self, sym, price, price_date='2026-11-13'):
        return qm.saved_valuation(self.book, sym, price, AS_OF, price_date, LEAN)

    def test_lean_valuation_is_rerun_at_the_screen_price(self):
        params = os.path.join(self.root, 'companies', 'FAILCO', '2026-11-04', 'lean_params.json')
        with open(params, 'rb') as f:
            before = f.read()
        ok, bad = self.sv('PASSCO', 120.0), self.sv('FAILCO', 182.95)
        self.assertEqual((ok['state'], ok['kind'], ok['basis'], ok['date'], ok['age_days']), ('pass', 'lean', 'lean_rerun', '2026-11-04', 11))
        self.assertTrue(ok['buy_test_pass'])
        self.assertGreater(ok['buy_test_price'], 120.0)
        self.assertEqual((bad['state'], bad['buy_test_pass']), ('fail', False))
        self.assertEqual(bad['buy_test_price_saved'], 150.99)
        self.assertAlmostEqual(bad['buy_test_price'], 150.99, delta=1.0)     # dates moved to the close: a little later
        self.assertEqual(self.sv('FAILCO', 182.95, price_date=None)['buy_test_price'], 150.99)
        self.assertIsNone(bad['rejected'])
        with open(params, 'rb') as f:
            self.assertEqual(f.read(), before)                      # the saved inputs are never changed

    def test_dates_move_only_forward(self):
        self.assertTrue(self.sv('PASSCO', 120.0)['repriced']['dates_moved'])
        self.assertFalse(self.sv('PASSCO', 120.0, price_date='2026-11-01')['repriced']['dates_moved'])

    def test_rejected_name_is_tested_and_marked(self):
        r = self.sv('REJCO', 140.0)
        self.assertEqual((r['state'], r['kind'], r['basis'], r['buy_test_price']), ('fail', 'rejected', 'rejected_price', 100.0))
        self.assertEqual(r['rejected']['date'], '2026-11-05')
        self.assertIn('נדחתה 05.11.2026', qm.saved_he(r))
        self.assertIn('**לא עוברת**', qm.saved_he(r))
        low = self.sv('REJCO', 90.0)
        self.assertEqual(low['state'], 'pass')                      # below the saved buy-test price: still marked as rejected
        self.assertIn('נדחתה', qm.saved_he(low))

    def test_custom_rejected_without_a_buy_test_price(self):
        self.assertEqual((self.sv('CUST', 110.0)['state'], self.sv('CUST', 110.0)['basis']), ('fail', 'failed_lower'))
        self.assertEqual((self.sv('CUST', 90.0)['state'], self.sv('CUST', 90.0)['basis']), ('unknown', 'no_test_price'))

    def test_newer_rejected_check_wins_over_an_older_folder(self):
        r = self.sv('LATE', 120.0)          # the folder's own buy test passes at 120; the newer rejected check does not
        self.assertEqual((r['state'], r['kind'], r['date']), ('fail', 'rejected', '2026-11-10'))
        self.assertIn('older research valuation', r['note'])

    def test_engine_folder_unknown_nothing_saved_none(self):
        e = self.sv('ENG', 50.0)
        self.assertEqual((e['state'], e['kind'], e['basis']), ('unknown', 'engine', 'engine'))
        n = self.sv('NOPE', 50.0)
        self.assertEqual(n['state'], 'none')
        self.assertEqual(qm.saved_he(n), 'אין')
        self.assertEqual(qm.saved_he(None), '—')

    def test_weighted_worlds_are_tested_when_the_folder_has_them(self):
        folder = os.path.join(self.root, 'companies', 'WRLD', '2026-11-04')
        lean_folder(self.root, 'WRLD', '2026-11-04')
        with open(os.path.join(folder, 'lean_params.json'), encoding='utf-8') as f:
            p = json.load(f)
        low = copy.deepcopy(p)
        low['scenarios']['base']['pe'] = round(p['scenarios']['base']['pe'] * 0.7, 1)
        _write(os.path.join(folder, 'lean_params_low.json'), low)
        spec = {'worlds': [{'name': 'main', 'file': 'lean_params.json', 'weight': 0.5},
                           {'name': 'low', 'file': 'lean_params_low.json', 'weight': 0.5}]}
        _write(os.path.join(folder, 'worlds.json'), spec)
        r = qm.saved_valuation(qm.saved_book(self.root), 'WRLD', 140.0, AS_OF, None, LEAN)
        bt = LEAN.run(LEAN.reprice(copy.deepcopy(p), price=140.0), worlds=spec, base_dir=folder)['worlds']['weighted']['buy_test']
        rerun = bt.get('rerun') or {}
        want = rerun.get('max_price_passing') if rerun.get('lower_than_stated') else bt.get('max_price_passing')
        main_only = LEAN.run(LEAN.reprice(copy.deepcopy(p), price=140.0))['buy_test']['max_price_passing']
        self.assertEqual((r['basis'], r['buy_test_price'], r['buy_test_pass']), ('lean_rerun', want, bt.get('pass')))
        self.assertNotEqual(want, main_only)                        # the weighted price, not the main file's alone

    def test_price_far_from_the_valuation_or_missing_is_not_compared(self):
        self.assertEqual((self.sv('FAILCO', 182.95 * 2)['state'], self.sv('FAILCO', 182.95 * 2)['basis']), ('unknown', 'scale'))
        self.assertEqual(self.sv('PASSCO', None)['basis'], 'no_price')


def _dates(n, last='2026-11-13'):
    end = date.fromisoformat(last)
    return [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


class ScreenMarksSavedValuations(unittest.TestCase):
    """The script end to end, offline (--reuse over saved raw files): one passing, one failing and one rejected passer are
    all listed, and the failing and rejected ones are marked."""

    def test_failing_and_rejected_passers_are_marked_not_dropped(self):
        spec = importlib.util.spec_from_file_location('quality_momentum_screen', os.path.join(REPO, 'scripts', 'quality_momentum_screen.py'))
        screen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(screen)
        names = {'PASSCO': (1, 0.0007), 'FAILCO': (2, 0.002), 'REJCO': (3, 0.002)}     # cik, daily drift (closes about 120, 168)
        with tempfile.TemporaryDirectory() as tmp:
            research, out = os.path.join(tmp, 'research'), os.path.join(tmp, 'screen')
            research_tree(research)
            _write(os.path.join(out, 'raw', 'nasdaq__screener_stocks.json'), {'data': {'rows': [
                {'symbol': s, 'name': f'{s} Inc. Common Stock', 'marketCap': '9000000000', 'lastsale': '$%.2f' % (100 * (1 + d) ** 259),
                 'volume': '1000', 'sector': 'Technology'} for s, (c, d) in names.items()]}})
            _write(os.path.join(out, 'raw', 'sec__company_tickers_exchange.json'),
                   {'fields': ['cik', 'name', 'ticker', 'exchange'], 'data': [[c, s, s, 'Nasdaq'] for s, (c, d) in names.items()]})
            for s, (c, d) in names.items():
                _write(os.path.join(out, 'facts', f'CIK{c:010d}.json'), {
                    'cik': c, 'v': screen.FACTS_V, 'as_of': AS_OF, 'series': series(REV, OP, CFO, CAPEX), 'errors': [],
                    'sub': {'sic': '7372', 'sic_description': 'services-prepackaged software', 'fiscal_year_end': '1231', 'name': s,
                            'latest_periodic': {'form': '10-Q', 'filed': '2026-10-30', 'period': '2026-09-30'}}})
            for s, d in list((s, d) for s, (c, d) in names.items()) + [('SPY', 0.0005)]:
                b = [round(x, 4) for x in bars(d)]
                _write(os.path.join(out, 'prices', s + '.json'),
                       {'symbol': s, 'dates': _dates(len(b)), 'adjclose': b, 'close': b, 'high': b, 'error': None, 'currency': 'USD'})
            with mock.patch.dict(os.environ, {'SEC_USER_AGENT': 'unit-test test@example.invalid'}), redirect_stdout(StringIO()):
                screen.main([out, '--as-of', AS_OF, '--reuse', '--research', research])
            with open(os.path.join(out, 'screen.json'), encoding='utf-8') as f:
                res = json.load(f)
            with open(os.path.join(out, 'screen_he.md'), encoding='utf-8') as f:
                text = f.read()
        states = {r['symbol']: (r['saved_valuation']['state'], r['saved_valuation']['kind']) for r in res['passers']}
        self.assertEqual(states, {'PASSCO': ('pass', 'lean'), 'FAILCO': ('fail', 'lean'), 'REJCO': ('fail', 'rejected')})
        self.assertEqual((res['counts']['passers_saved_fail'], res['counts']['passers_saved_pass']), (2, 1))
        self.assertEqual({r['symbol'] for r in res['saved_valuations'] if r['list'] == 'passers'}, {'PASSCO', 'FAILCO', 'REJCO'})
        book = {r['symbol']: r['saved_valuation']['state'] for r in res['thesis_book']}
        self.assertEqual(book['ENG'], 'unknown')                    # not listed by the feed: still shown with its saved valuation
        passers = text.split('## כמעט עברו')[0]
        for s in ('| PASSCO |', '| FAILCO |', '| REJCO |', 'הערכה שמורה', 'נדחתה 05.11.2026', '**לא עוברת**'):
            self.assertIn(s, passers)
        self.assertIn('ולא ה-XBRL frames', text)
        warn = next(ln for ln in text.splitlines() if ln.startswith('**הערכה שמורה שנכשלת'))
        self.assertIn('FAILCO', warn)
        self.assertIn('REJCO', warn)
        self.assertNotIn('PASSCO', warn)
        self.assertIn('## הערכות שמורות מול מחיר החיפוש (3)', text)


if __name__ == '__main__':
    unittest.main()
