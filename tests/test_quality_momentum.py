"""Quality + business-momentum screen: TTM and the derived fourth quarter, each quality gate at its boundary, routing,
price as information only, the analyst-data gates (parse, revisions, beats, our own history), ranking, the earnings radar,
the Hebrew report and the saved-valuation mark (no network)."""
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
        self.assertEqual(qm.classify(g, good_business()), 'incomplete')         # a short history is not a pass

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
        self.assertEqual(qm.classify(g, good_business()), 'incomplete')

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


# ---------------------------------------------------------------- analyst data (Nasdaq pages, in the shapes read on 25.11.2026)

def surprise_json(rows=((9.16, '6.55', '39.85', '11/10/2026'), (6.76, '4.72', '43.22', '8/11/2026'),
                        (4.63, '2.79', '65.95', '5/12/2026'), (3.61, '3.32', '8.73', '2/10/2026'))):
    """The earnings-surprise page: reported EPS as a number, consensus and surprise as strings, newest first."""
    return {'data': {'symbol': 'x', 'earningsSurpriseTable': {'headers': {}, 'rows': [
        {'fiscalQtrEnd': 'Oct 2026', 'dateReported': d, 'eps': e, 'consensusForecast': c, 'percentageSurprise': p} for e, c, p, d in rows]}},
        'status': {'rCode': 200}}


def forecast_json(years=(('Jan 2027', 25.3, 10, 2, 0), ('Jan 2028', 29.59, 9, 2, 0), ('Jan 2029', 31.25, 3, 0, 5))):
    return {'data': {'symbol': 'x', 'quarterlyForecast': {'rows': []}, 'yearlyForecast': {'rows': [
        {'fiscalEnd': fy, 'consensusEPSForecast': c, 'highEPSForecast': None, 'lowEPSForecast': 'N/A', 'noOfEstimates': n, 'up': up, 'down': down}
        for fy, c, n, up, down in years]}}, 'status': {'rCode': 200}}


def momentum_json(current=25.3, month=25.13, week=25.19, fy='Fiscal Year End Jan 2027'):
    return {'data': {'symbol': 'x', 'changeInConsensus': {
        'monthData': {'period': '1 Month \n Ago', 'qtrMean': 7.34, 'yrMean': month}, 'weekData': {'period': '1 Week \n Ago', 'yrMean': week},
        'currentData': {'period': 'Current', 'yrMean': current}, 'fiscalYearEndText': fy}}, 'status': {'rCode': 200}}


def est(**kw):
    """A parsed analyst record; keyword s, f, m replace one page's JSON."""
    return qm.parse_estimates(kw.get('s', surprise_json()), kw.get('f', forecast_json()), kw.get('m', momentum_json()))


def good_business():
    return qm.business_gates(est(), as_of=AS_OF)


class Trend(unittest.TestCase):
    """Price is measured and shown, and no gate reads it."""

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
        self.assertAlmostEqual(t['below_high'], 100. / 160. - 1)    # 37.5% below the high; adjusted closes say 0%
        self.assertEqual(t['ma200'], 100.)
        self.assertAlmostEqual(t['ret_12_1'], adj[-22] / adj[-253] - 1)
        self.assertEqual(qm.trend_measures(adj)['below_high'], 0)   # adjusted closes alone stand in when nothing else is saved

    def test_short_history_is_missing(self):
        self.assertIsNone(qm.trend_measures(bars(0.001, n=252)))
        self.assertIsNone(qm.price_info(None, qm.trend_measures(bars(0.001))))

    def test_price_info(self):
        spy = qm.trend_measures(bars(0.0005))
        up = qm.price_info(qm.trend_measures(bars(0.002)), spy)
        self.assertEqual(set(up), {'rel_strength', 'vs_ma200', 'ma200_rising', 'below_high'})
        self.assertGreater(up['rel_strength'], 0)
        self.assertGreater(up['vs_ma200'], 0)
        self.assertTrue(up['ma200_rising'])
        self.assertEqual(up['below_high'], 0)
        t = qm.trend_measures(bars(-0.002))
        down = qm.price_info(t, spy)
        self.assertLess(down['rel_strength'], 0)
        self.assertAlmostEqual(down['vs_ma200'], t['last'] / t['ma200'] - 1)
        self.assertFalse(down['ma200_rising'])
        self.assertAlmostEqual(down['below_high'], t['last'] / t['high_52w'] - 1)
        self.assertIsNone(qm.price_info(t, None)['rel_strength'])   # no SPY bars: the rest is still shown

    def test_no_price_gate_is_left(self):
        self.assertNotIn('max_below_high', qm.GATES)
        self.assertEqual(qm.GATES['divergence_below_high'], 0.25)
        self.assertFalse(hasattr(qm, 'trend_gates'))
        self.assertFalse(hasattr(qm, 'needs_trend'))
        self.assertEqual({g['gate'] for g in good_business()}, {'eps_revisions', 'earnings_beats'})

    def test_divergence_flag_needs_a_fall_of_25_percent_and_both_business_gates(self):
        info = {'rel_strength': -0.4, 'vs_ma200': -0.2, 'ma200_rising': False, 'below_high': -0.43}
        self.assertTrue(qm.divergence(info, good_business()))
        self.assertTrue(qm.divergence(dict(info, below_high=-0.25), good_business()))           # the boundary counts
        self.assertFalse(qm.divergence(dict(info, below_high=-0.2499), good_business()))
        cut = qm.business_gates(est(m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        self.assertFalse(qm.divergence(info, cut))                  # the price fell and analysts are cutting: no flag
        self.assertFalse(qm.divergence(info, qm.business_gates(None)))
        self.assertFalse(qm.divergence(None, good_business()))


class Estimates(unittest.TestCase):
    def test_parse_numbers_and_numeric_strings(self):
        e = est()
        self.assertEqual(e['surprises'][0], {'fiscal_qtr_end': '2026-10', 'date_reported': '2026-11-10', 'eps': 9.16,
                                             'consensus': 6.55, 'surprise_pct': 39.85})
        self.assertEqual([q['date_reported'] for q in e['surprises']], ['2026-11-10', '2026-08-11', '2026-05-12', '2026-02-10'])
        self.assertEqual(e['years'][0], {'fiscal_year_end': '2027-01', 'consensus': 25.3, 'high': None, 'low': None, 'n': 10, 'up': 2, 'down': 0})
        self.assertEqual([y['fiscal_year_end'] for y in e['years']], ['2027-01', '2028-01', '2029-01'])
        self.assertEqual(e['momentum'], {'fiscal_year_end': '2027-01', 'current': 25.3, 'week_ago': 25.19, 'month_ago': 25.13})
        self.assertEqual(e['gaps'], [])
        strings = est(f=forecast_json((('Jan 2027', '25.30', '10', '2', '0'), ('Jan 2028', '$29.59', '9', '2', '0'))),
                      m=momentum_json(current='25.3', month='25.13'))
        self.assertEqual((strings['years'][0]['consensus'], strings['years'][1]['consensus'], strings['years'][0]['up']), (25.3, 29.59, 2))
        self.assertEqual(strings['momentum']['month_ago'], 25.13)

    def test_parse_reads_the_data_node_alone_and_sorts_by_report_date(self):
        s = surprise_json(((6.76, '4.72', '43.22', '8/11/2026'), (9.16, '6.55', '39.85', '11/10/2026')))
        e = qm.parse_estimates(s['data'], forecast_json()['data'], momentum_json()['data'])
        self.assertEqual([q['eps'] for q in e['surprises']], [9.16, 6.76])
        self.assertEqual(e['years'][1]['consensus'], 29.59)

    def test_parse_na_and_a_missing_surprise(self):
        e = est(s=surprise_json(((1.34, '1.34', 'N/A', '11/10/2026'), ('N/A', 'N/A', 'N/A', '8/11/2026'), (-0.62, '-0.84', None, '5/12/2026'),
                                 (0.05, '0', 'N/A', '2/10/2026'))),
                f=forecast_json((('Jan 2027', 'N/A', 'N/A', 'N/A', None), ('bad', 1.0, 1, 0, 0))), m=momentum_json(current='N/A', month=None, week='--'))
        self.assertEqual([q['surprise_pct'] for q in e['surprises']][0], 0.0)           # computed: the feed leaves a met quarter as N/A
        self.assertEqual(len(e['surprises']), 3)                                        # a row without EPS and consensus is dropped
        self.assertAlmostEqual(e['surprises'][1]['surprise_pct'], 0.22 / 0.84 * 100)    # a smaller loss than forecast is a beat
        self.assertIsNone(e['surprises'][2]['surprise_pct'])                            # a zero consensus has no percentage
        self.assertEqual(e['years'], [{'fiscal_year_end': '2027-01', 'consensus': None, 'high': None, 'low': None, 'n': None,
                                       'up': None, 'down': None}])
        self.assertIsNone(e['momentum'])
        self.assertIn('no consensus change on the estimate-momentum page', e['gaps'])

    def test_parse_empty_pages(self):
        for pages in ((None, None, None), ({}, {}, {}), ({'data': None, 'status': {'rCode': 200}},) * 3,
                      ({'data': {'earningsSurpriseTable': None}}, {'data': {'symbol': 'BRK/B', 'quarterlyForecast': None, 'yearlyForecast': None}},
                       {'data': {'changeInConsensus': None}}), ('text', [1, 2], 7)):
            e = qm.parse_estimates(*pages)
            self.assertEqual((e['surprises'], e['years'], e['momentum']), ([], [], None))
            self.assertEqual(len(e['gaps']), 3)
            self.assertTrue(all(g['pass'] is None for g in qm.business_gates(e, as_of=AS_OF)))

    def test_symbol_variants(self):
        self.assertEqual(qm.symbol_variants('BRK-B'), ['BRK-B', 'BRK.B'])
        self.assertEqual(qm.symbol_variants('BRK.B'), ['BRK.B', 'BRK-B'])
        self.assertEqual(qm.symbol_variants('brk/b'), ['BRK.B', 'BRK-B'])              # a slash is never sent
        self.assertEqual(qm.symbol_variants('DELL'), ['DELL'])


class BusinessGates(unittest.TestCase):
    def gates(self, e, **kw):
        return {g['gate']: g for g in qm.business_gates(e, as_of=kw.pop('as_of', AS_OF), **kw)}

    def test_pass(self):
        g = self.gates(est())
        self.assertTrue(g['eps_revisions']['pass'] and g['earnings_beats']['pass'])
        rev = g['eps_revisions']
        self.assertAlmostEqual(rev['value'], 25.3 / 25.13 - 1)
        self.assertEqual((rev['up'], rev['down'], rev['net'], rev['window_days'], rev['source']), (4, 0, 4, 30, 'nasdaq_month'))
        self.assertEqual(g['earnings_beats']['value'], 39.85)                           # the smaller of the two newest
        self.assertEqual(qm.business_columns(list(g.values())),
                         {'eps_revision_pct': rev['value'], 'eps_revision_window_days': 30, 'surprise_min_pct': 39.85})

    def test_revisions_fail_when_more_analysts_cut_than_raise(self):
        f = forecast_json((('Jan 2027', 25.3, 10, 1, 2), ('Jan 2028', 29.59, 9, 1, 1), ('Jan 2029', 31.25, 3, 9, 0)))
        rev = self.gates(est(f=f))['eps_revisions']
        self.assertEqual((rev['net'], rev['pass']), (-1, False))                        # the third year's count is not read
        even = forecast_json((('Jan 2027', 25.3, 10, 1, 2), ('Jan 2028', 29.59, 9, 2, 1)))
        self.assertTrue(self.gates(est(f=even))['eps_revisions']['pass'])               # net 0 is not a cut

    def test_counts_start_at_the_fiscal_year_the_consensus_change_describes(self):
        # a finished year still on the forecast table: the count reads the momentum's year and the one after it
        f = forecast_json((('Jan 2026', 20.0, 10, 0, 9), ('Jan 2027', 25.3, 10, 2, 0), ('Jan 2028', 29.59, 9, 1, 0)))
        e = est(f=f)
        self.assertIn('estimate-momentum describes fiscal year 2027-01, the forecast table starts at 2026-01', e['gaps'])
        rev = self.gates(e)['eps_revisions']
        self.assertEqual((rev['fiscal_year_end'], rev['up'], rev['down'], rev['pass']), ('2027-01', 3, 0, True))

    def test_revisions_fail_when_the_consensus_fell_more_than_one_percent(self):
        for current, ok in ((99.0, True), (98.99, False), (101.0, True)):               # -1.0% is the boundary
            rev = self.gates(est(m=momentum_json(current=current, month=100.0)))['eps_revisions']
            self.assertEqual(rev['pass'], ok, current)
        self.assertAlmostEqual(self.gates(est(m=momentum_json(current=98.99, month=100.0)))['eps_revisions']['value'], -0.0101)

    def test_beats_fail_on_a_miss_larger_than_two_percent_in_either_quarter(self):
        def beats(a, b):
            return self.gates(est(s=surprise_json(((1.0, '1.0', a, '11/10/2026'), (1.0, '1.0', b, '8/11/2026'), (1.0, '1.0', '-50', '5/12/2026')))))['earnings_beats']
        self.assertEqual((beats('5.0', '-2.0')['pass'], beats('5.0', '-2.0')['value']), (True, -2.0))     # a miss of 2% counts as met
        self.assertEqual((beats('5.0', '-2.01')['pass'], beats('5.0', '-2.01')['value']), (False, -2.01))
        self.assertFalse(beats('-3', '10')['pass'])
        self.assertTrue(beats('0', '0')['pass'])                                       # the third, older quarter is not read

    def test_missing_data_is_never_a_pass(self):
        g = self.gates(None)
        self.assertEqual((g['eps_revisions']['pass'], g['earnings_beats']['pass']), (None, None))
        self.assertEqual(qm.classify(qm.quality_gates(metrics()), list(g.values())), 'incomplete')
        self.assertIsNone(self.gates(est(m={}))['eps_revisions']['pass'])               # no consensus to compare with
        self.assertIsNone(self.gates(est(f={}))['eps_revisions']['pass'])               # no count of raised and cut estimates
        self.assertTrue(self.gates(est(f={}))['earnings_beats']['pass'])                # the other gate still reads its page
        one = est(s=surprise_json(((9.16, '6.55', '39.85', '11/10/2026'),)))
        self.assertIsNone(self.gates(one)['earnings_beats']['pass'])                    # one reported quarter is not two
        self.assertTrue(self.gates(one)['eps_revisions']['pass'])

    def test_negative_or_sign_changing_consensus_has_no_percentage_and_the_count_decides(self):
        for current, month in ((-2.72, -2.92), (0.05, -0.10), (-0.05, 0.10), (0.0, 0.4)):
            rev = self.gates(est(m=momentum_json(current=current, month=month)))['eps_revisions']
            self.assertIsNone(rev['value'], (current, month))
            self.assertTrue(rev['pass'], (current, month))                              # 4 raised, 0 cut
            self.assertIn('no usable percentage', rev['note'])
            cut = forecast_json((('Jan 2027', current, 10, 0, 3), ('Jan 2028', 1.0, 9, 1, 0)))
            self.assertFalse(self.gates(est(f=cut, m=momentum_json(current=current, month=month)))['eps_revisions']['pass'])

    def test_zero_consensus_quarter_is_compared_directly(self):
        ok = self.gates(est(s=surprise_json(((0.05, '0', 'N/A', '11/10/2026'), (1.0, '0.9', '11.1', '8/11/2026')))))['earnings_beats']
        self.assertEqual((ok['pass'], ok['value']), (True, 11.1))
        bad = self.gates(est(s=surprise_json(((-0.05, '0', 'N/A', '11/10/2026'), (1.0, '0.9', '11.1', '8/11/2026')))))['earnings_beats']
        self.assertFalse(bad['pass'])

    def test_stale_or_future_reports_are_not_the_last_two_quarters(self):
        old = est(s=surprise_json(((1.0, '0.9', '11', '2/10/2026'), (1.0, '0.9', '11', '11/10/2025'))))
        self.assertIsNone(self.gates(old)['earnings_beats']['pass'])                    # the newest is 278 days old
        self.assertIn('days old', self.gates(old)['earnings_beats']['note'])
        late = est(s=surprise_json(((1.0, '2.0', '-50', '11/20/2026'), (1.0, '0.9', '11', '11/10/2026'), (1.0, '0.9', '12', '8/11/2026'))))
        self.assertEqual(self.gates(late)['earnings_beats']['value'], 11.0)             # reported after the as-of date: not read
        self.assertFalse(self.gates(late, as_of='2026-11-25')['earnings_beats']['pass'])

    def test_our_own_history_lengthens_the_window(self):
        def h(day, consensus, fy='2027-01'):
            return {'as_of': day, 'fiscal_year_end': fy, 'consensus': consensus, 'n': 10}
        e = est()
        self.assertEqual(qm.history_lines(e, AS_OF), [h(AS_OF, 25.3), {'as_of': AS_OF, 'fiscal_year_end': '2028-01', 'consensus': 29.59, 'n': 9},
                                                      {'as_of': AS_OF, 'fiscal_year_end': '2029-01', 'consensus': 31.25, 'n': 3}])
        w = qm.eps_revision(e, [h('2026-09-16', 20.0), h('2026-10-01', 22.0), h('2026-11-01', 25.0)], AS_OF)
        self.assertEqual((w['source'], w['window_days'], w['then'], w['now']), ('own_history', 60, 20.0, 25.3))     # the oldest within 100 days
        self.assertAlmostEqual(w['pct'], 25.3 / 20.0 - 1)
        recent = qm.eps_revision(e, [h('2026-11-01', 30.0)], AS_OF)                     # 14 days: Nasdaq's month is longer
        self.assertEqual((recent['source'], recent['window_days'], recent['then']), ('nasdaq_month', 30, 25.13))
        for rows in ([h('2026-07-01', 20.0)], [h('2026-09-16', 20.0, fy='2028-01')], [h(AS_OF, 20.0)], [h('2026-12-01', 20.0)],
                     [{'as_of': '2026-09-16', 'fiscal_year_end': '2027-01', 'consensus': 'N/A'}]):
            self.assertEqual(qm.eps_revision(e, rows, AS_OF)['source'], 'nasdaq_month', rows)        # too old, another year, today, later, no number
        no_month = est(m={})
        self.assertEqual(qm.eps_revision(no_month, [], AS_OF)['state'], 'no_data')
        short = qm.eps_revision(no_month, [h('2026-11-01', 25.0)], AS_OF)               # no month from Nasdaq: our 14 days are the longest we have
        self.assertEqual((short['source'], short['window_days'], short['state']), ('own_history', 14, 'ok'))
        g = {x['gate']: x for x in qm.business_gates(e, [h('2026-09-16', 26.0)], AS_OF)}
        self.assertEqual((g['eps_revisions']['pass'], g['eps_revisions']['window_days']), (False, 60))   # -2.7% over 60 days: cut
        self.assertIn('60 יום', qm.revision_he(g['eps_revisions']))


class Classify(unittest.TestCase):
    def test_pass_near_miss_fail(self):
        self.assertEqual(qm.classify(qm.quality_gates(metrics()), good_business()), 'pass')
        self.assertEqual(qm.classify(qm.quality_gates(metrics(share_change=0.05)), good_business()), 'near_miss')
        self.assertEqual(qm.classify(qm.quality_gates(metrics(share_change=0.05, fcf=-1.)), good_business()), 'fail')

    def test_one_business_failure_is_a_near_miss_two_gates_is_a_fail(self):
        cut = qm.business_gates(est(m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        self.assertEqual(qm.classify(qm.quality_gates(metrics()), cut), 'near_miss')
        self.assertEqual(qm.classify(qm.quality_gates(metrics(fcf=-1.)), cut), 'fail')
        both = qm.business_gates(est(s=surprise_json(((1.0, '2.0', '-50', '11/10/2026'), (1.0, '0.9', '11', '8/11/2026'))),
                                     m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        self.assertEqual(qm.classify(qm.quality_gates(metrics()), both), 'fail')        # the PayPal pattern: cuts and a miss

    def test_one_trend_failure_is_a_near_miss_two_gates_is_a_fail(self):
        """Lesson L10 (research/lessons.jsonl) names this test: a search for names that 'fell a lot' returned broken
        companies that were not recovering. The name is kept so the lesson still points at a test. Since 25.11.2026 the
        guard is not the price trend: a company whose business weakened fails the business gates, however far it fell,
        and a fall alone neither picks a name nor drops it."""
        cut = qm.business_gates(est(m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        broken = qm.business_gates(est(s=surprise_json(((1.0, '2.0', '-50', '11/10/2026'), (1.0, '0.9', '11', '8/11/2026'))),
                                       m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        for closes in (fallen(0.6), bars(0.002)):
            one, two = screened('ONE', closes=closes, business=cut), screened('TWO', closes=closes, business=broken)
            self.assertEqual((one['status'], one['divergence']), ('near_miss', False))
            self.assertEqual((two['status'], two['divergence']), ('fail', False))
        self.assertEqual(screened('GOOD', closes=fallen(0.6))['status'], 'pass')       # the fall itself decides nothing

    def test_business_data_is_fetched_only_within_one_quality_failure(self):
        two = qm.quality_gates(metrics(share_change=0.05, fcf=-1.))
        self.assertFalse(qm.needs_business(two))
        self.assertEqual(qm.classify(two, None), 'fail')
        self.assertTrue(qm.needs_business(qm.quality_gates(metrics(fcf=-1.))))
        self.assertEqual(qm.classify(qm.quality_gates(metrics(fcf=-1.)), None), 'incomplete')

    def test_every_route_is_classified_on_its_own_gates_plus_the_business_gates(self):
        bank = qm.financial_gates({'rev_growth_ttm': 0.01, 'roe': 0.15, 'bvps_growth': 0.001})
        self.assertEqual(qm.classify(bank, good_business()), 'pass')
        self.assertEqual(qm.classify(bank, qm.business_gates(None)), 'incomplete')
        annual = qm.quality_gates(metrics(basis='annual'))
        self.assertEqual(qm.classify(annual, good_business()), 'pass')


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
        rows = [{'symbol': 'LOW', 'rev_growth_ttm': 0.15, 'margin_change_pts': 0., 'fcf_margin': 0.05, 'eps_revision_pct': -0.005},
                {'symbol': 'TOP', 'rev_growth_ttm': 0.60, 'margin_change_pts': 5., 'fcf_margin': 0.40, 'eps_revision_pct': 0.08},
                {'symbol': 'MID', 'rev_growth_ttm': 0.30, 'margin_change_pts': 6., 'fcf_margin': 0.20, 'eps_revision_pct': 0.01}]
        r = qm.rank(rows)
        self.assertEqual([x['symbol'] for x in r], ['TOP', 'MID', 'LOW'])
        self.assertEqual(r[0]['rank_parts'], {'rev_growth_ttm': 100., 'margin_change_pts': 50., 'fcf_margin': 100., 'eps_revision_pct': 100.})
        self.assertEqual(r[0]['rank_score'], 350.)
        self.assertEqual(r[2]['rank_score'], 0.)
        self.assertEqual(qm.RANK_KEYS, ('rev_growth_ttm', 'margin_change_pts', 'fcf_margin', 'eps_revision_pct'))
        self.assertTrue(all(x['rank_filled'] == [] for x in r))

    def test_missing_revision_is_ranked_as_the_median_of_the_others(self):
        def row(sym, rev, growth=0.2):
            return {'symbol': sym, 'rev_growth_ttm': growth, 'margin_change_pts': 1., 'fcf_margin': 0.1, 'eps_revision_pct': rev}
        r = {x['symbol']: x for x in qm.rank([row('A', 0.00), row('B', 0.02), row('C', 0.10), row('LOSS', None)])}
        self.assertEqual(r['LOSS']['rank_filled'], ['eps_revision_pct'])
        self.assertEqual(r['LOSS']['rank_parts']['eps_revision_pct'], 50.)              # ranked as 0.02 (the median), tied with B
        self.assertEqual(r['B']['rank_parts']['eps_revision_pct'], r['LOSS']['rank_parts']['eps_revision_pct'])
        self.assertEqual((r['A']['rank_parts']['eps_revision_pct'], r['C']['rank_parts']['eps_revision_pct']), (0., 100.))
        self.assertIsNone(r['LOSS']['eps_revision_pct'])                                # the row itself keeps no invented number
        alone = qm.rank([row('X', None), row('Y', None, growth=0.3)])
        self.assertEqual([x['rank_parts']['eps_revision_pct'] for x in alone], [50., 50.])
        with self.assertRaises(KeyError):                                               # the other components are never filled
            qm.rank([{'symbol': 'Z', 'margin_change_pts': 1., 'fcf_margin': 0.1, 'eps_revision_pct': 0.1}])

    def test_price_columns(self):
        p = qm.price_columns({'net_income': 50., 'revenue': 200., 'fcf': 40.}, 1000.)
        self.assertEqual((p['pe'], p['ps'], p['fcf_yield']), (20., 5., 0.04))
        self.assertIsNone(qm.price_columns({'net_income': -5., 'revenue': 200., 'fcf': 40.}, 1000.)['pe'])
        self.assertEqual(qm.price_columns({'net_income': 300., 'revenue': 3000., 'fcf': 0.}, 1000., fx=30.)['pe'], 100.)


def fallen(drop=0.6):
    """Daily bars that rise to a high and then fall by `drop` from it (the DELL pattern)."""
    up = bars(0.004, n=200)
    return up + [up[-1] * (1 - drop * (i + 1) / 60) for i in range(60)]


def screened(symbol='S', closes=None, business=None, **metric_changes):
    """One standard row the way the script builds it: quality gates, business gates, price as information, status."""
    r = qm.evaluate({'symbol': symbol, 'name': symbol + ' Corp', 'sector': 'Technology', 'market_cap': 9e9, 'in_universe': True},
                    series(REV, OP, CFO, CAPEX), '7372', AS_OF)
    r['quality'] = [dict(g, **metric_changes[g['gate']]) if g['gate'] in metric_changes else g for g in r['quality']]
    spy = qm.trend_measures(bars(0.0005))
    r['trend_measures'] = qm.trend_measures(closes or bars(0.002))
    r['price_info'] = qm.price_info(r['trend_measures'], spy)
    r['rel_strength'] = r['price_info']['rel_strength']
    r['business'] = good_business() if business is None else business
    r.update(qm.business_columns(r['business']))
    r.update(qm.price_columns(r, r['market_cap']))
    gates = r['quality'] + r['business']
    r.update(status=qm.classify(r['quality'], r['business']), failed=qm.failed(gates), missing=qm.missing(gates),
             divergence=qm.divergence(r['price_info'], r['business']))
    return r


class PriceIsInformation(unittest.TestCase):
    def test_a_row_60_percent_below_its_high_with_passing_business_gates_passes(self):
        down = screened('DOWN', closes=fallen(0.6))
        self.assertAlmostEqual(down['price_info']['below_high'], -0.6)
        self.assertLess(down['price_info']['vs_ma200'], 0)                             # below its 200-day average too
        self.assertEqual((down['status'], down['failed'], down['missing']), ('pass', [], []))
        self.assertTrue(down['divergence'])
        up = screened('UP')
        self.assertEqual((up['status'], up['divergence']), ('pass', False))

    def test_the_status_is_the_same_for_any_price_path(self):
        cut = qm.business_gates(est(m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        for kw, want in (({}, 'pass'), ({'business': cut}, 'near_miss'), ({'business': qm.business_gates(None)}, 'incomplete'),
                         ({'fcf': {'pass': False, 'value': -1.}}, 'near_miss'), ({'fcf': {'pass': False}, 'business': cut}, 'fail')):
            statuses = {screened('X', closes=c, **kw)['status'] for c in (bars(0.002), bars(-0.002), fallen(0.6), bars(0.))}
            self.assertEqual(statuses, {want}, kw)

    def test_a_falling_price_with_analysts_cutting_is_not_flagged(self):
        cut = qm.business_gates(est(m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        r = screened('WEAK', closes=fallen(0.6), business=cut)
        self.assertEqual((r['status'], r['divergence']), ('near_miss', False))
        self.assertEqual(r['failed'][0]['gate'], 'eps_revisions')


def cal(day, symbol, eps=None, forecast='$1.00', surprise=None, **kw):
    return dict({'symbol': symbol, 'name': symbol + ' Inc.', 'eps': eps, 'surprise': surprise, 'epsForecast': forecast, 'noOfEsts': '8',
                 'fiscalQuarterEnding': 'Oct/2026', 'marketCap': '$9,000,000,000', 'time': 'time-after-hours', '_day': day}, **kw)


class Radar(unittest.TestCase):
    def test_calendar_days_skip_weekends(self):
        days = qm.calendar_days('2026-11-25')                       # a Wednesday
        self.assertEqual((days[0], days[-1]), ('2026-11-16', '2026-12-09'))
        self.assertNotIn('2026-11-21', days)                        # Saturday
        self.assertNotIn('2026-11-15', days)                        # Sunday, 10 days back
        self.assertIn('2026-11-25', days)
        self.assertEqual(len(days), 18)

    def test_parse_calendar(self):
        rows = qm.parse_calendar('2026-11-24', {'data': {'asOf': 'Tue, Nov 24, 2026', 'rows': [
            cal(0, 'DELL', '$9.16', '$6.55', '39.85'), cal(0, 'MDT', '$1.34', '$1.34', 'N/A'), cal(0, 'LOSS', '($0.62)', '($0.84)', 'N/A'),
            cal(0, 'BRK/B', None, '$5.00'), {'name': 'no symbol'}, 'text']}, 'status': {'rCode': 200}})
        self.assertEqual([r['symbol'] for r in rows], ['DELL', 'MDT', 'LOSS', 'BRK-B'])
        self.assertEqual(rows[0], {'date': '2026-11-24', 'symbol': 'DELL', 'name': 'DELL Inc.', 'fiscal_quarter_end': '2026-10', 'eps': 9.16,
                                   'eps_forecast': 6.55, 'surprise_pct': 39.85, 'n_estimates': 8, 'time': 'time-after-hours'})
        self.assertEqual(rows[1]['surprise_pct'], 0.0)              # the feed leaves a met quarter as N/A
        self.assertAlmostEqual(rows[2]['surprise_pct'], 0.22 / 0.84 * 100)
        self.assertEqual((rows[3]['eps'], rows[3]['surprise_pct'], rows[3]['eps_forecast']), (None, None, 5.0))
        for empty in ({'data': {'asOf': 'Sat', 'headers': None, 'rows': None}}, {'data': None}, None, {}, []):
            self.assertEqual(qm.parse_calendar('2026-11-21', empty), [])

    def test_reported_and_upcoming_lists(self):
        cut = qm.business_gates(est(m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        rows = [screened('PASS'), screened('NEAR', business=cut), screened('SMALL'), screened('BOOK', fcf={'pass': False}, business=cut),
                screened('FAIL', fcf={'pass': False}, business=cut), screened('GOOGL')]
        rows[2]['in_universe'] = False                              # below the market value floor, not in the book
        rows[5]['other_classes'] = ['GOOG']
        days = [('2026-11-10', cal(0, 'PASS', '$1.10', '$1.00', '10')),           # +10%: listed
                ('2026-11-10', cal(0, 'NEAR', '$1.05', '$1.00', '5.0')),          # exactly +5% with one failed gate: listed
                ('2026-11-10', cal(0, 'GOOG', '$1.049', '$1.00', '4.9')),         # under +5%: not listed
                ('2026-11-11', cal(0, 'BOOK', '$0.90', '$1.00', '-10')),          # thesis book: listed whatever the surprise
                ('2026-11-11', cal(0, 'FAIL', '$2.00', '$1.00', '100')),          # failed the screen: not listed
                ('2026-11-11', cal(0, 'SMALL', '$2.00', '$1.00', '100')),         # below the floor: not listed
                ('2026-11-12', cal(0, 'GONE', '$2.00', '$1.00', '100')),          # a thesis-book name the screen did not find
                ('2026-11-04', cal(0, 'PASS', '$3.00', '$1.00', '200')),          # 11 days back: outside the window
                ('2026-11-15', cal(0, 'NEAR', None)),                              # reports today, not out yet: upcoming
                ('2026-11-20', cal(0, 'GOOG', None)),                              # the other share class of a passer
                ('2026-11-27', cal(0, 'BOOK', None)), ('2026-11-27', cal(0, 'FAIL', None)),
                ('2026-11-29', cal(0, 'PASS', None)),                              # 14 days ahead: inside
                ('2026-11-30', cal(0, 'PASS', None))]                              # 15 days ahead: outside
        calendar = sum((qm.parse_calendar(d, [r]) for d, r in days), [])
        radar = qm.earnings_radar(calendar, rows, ['BOOK', 'GONE'], AS_OF)
        self.assertEqual([(x['date'], x['symbol']) for x in radar['reported']],
                         [('2026-11-12', 'GONE'), ('2026-11-11', 'BOOK'), ('2026-11-10', 'PASS'), ('2026-11-10', 'NEAR')])
        near = radar['reported'][3]
        self.assertEqual((near['status'], near['failed_gate'], near['surprise_pct'], near['in_book']), ('near_miss', 'eps_revisions', 5.0, False))
        self.assertEqual((radar['reported'][0]['status'], radar['reported'][0]['in_book']), (None, True))
        self.assertEqual((radar['reported'][1]['status'], radar['reported'][1]['surprise_pct']), ('fail', -10.0))
        self.assertEqual([(x['date'], x['symbol']) for x in radar['upcoming']],
                         [('2026-11-15', 'NEAR'), ('2026-11-20', 'GOOGL'), ('2026-11-27', 'BOOK'), ('2026-11-29', 'PASS')])
        self.assertEqual(qm.earnings_radar([], rows, ['BOOK'], AS_OF), {'reported': [], 'upcoming': []})


class Report(unittest.TestCase):
    def res(self):
        r = screened('S')
        spy = qm.trend_measures(bars(0.0005))
        cut = qm.business_gates(est(m=momentum_json(current=24.0, month=25.13)), as_of=AS_OF)
        miss = dict(r, symbol='M', status='near_miss', failed=[dict(gate(r['quality'], 'share_change'), value=0.05)])
        slow = dict(r, symbol='D', status='near_miss', failed=[dict(gate(r['quality'], 'durability'), value=0.04, worst_year=-0.02)])
        weak = screened('W', business=cut)
        down = screened('DN', closes=fallen(0.43), **{'durability': {'pass': False, 'value': 0.035, 'worst_year': -0.136}})
        loss = dict(screened('L', business=qm.business_gates(est(m=momentum_json(current=-2.72, month=-2.92)), as_of=AS_OF)))
        oil = dict(r, symbol='O', route='commodity', sic='1311', status='pass')
        bank = dict(r, symbol='B', status='near_miss', failed=[qm.financial_gates({'rev_growth_ttm': 0.1, 'roe': 0.13, 'bvps_growth': 0.1})[1]])
        gap = dict(screened('U', business=qm.business_gates(None)))
        rows = [r, loss, miss, slow, weak, down]
        days = [('2026-11-10', cal(0, 'S', '$1.10', '$1.00', '10')), ('2026-11-20', cal(0, 'DN', None, '$2.50'))]
        radar = qm.earnings_radar(sum((qm.parse_calendar(d, [x]) for d, x in days), []), rows, ['S'], AS_OF)
        return {'as_of': AS_OF, 'min_cap': 5e9, 'spy': spy, 'counts': {'companies': 2, 'passers': 2, 'near_misses': 4},
                'counts_he': [['x', 1]], 'gaps_he': ['g'], 'passers': qm.rank([r, loss]), 'near_misses': [miss, slow, weak, down],
                'commodity': [oil], 'financials': [], 'financial_near_misses': [bank], 'unread': [gap], 'annual_only': [],
                'thesis_book': [dict(r), gap, {'symbol': 'Z', 'status': 'not_found', 'note': 'n'}],
                'divergence': qm.divergence_rows([('passers', 'עברה', [r, loss]), ('near_misses', 'כמעט', [miss, slow, weak, down])]),
                'radar': dict(radar, gaps=[])}

    def test_hebrew_report_renders(self):
        text = qm.render_he(self.res())
        for needle in ('לא הערכת שווי ולא המלצת קנייה', 'לא נבדק על העבר', '| S |', 'מספר המניות לא גדל מעל 3%', '+5.0%',
                       'B (תשואה על ההון 15% ומעלה: +13.0%)', '| U |', '2026-09-30',
                       'ברבעון האחרון מול אותו רבעון לפני שנה (15% ומעלה)', 'לא ירדו באף אחת משלוש שנות הכספים',
                       '+4.0% בשנה, השנה החלשה -2.0%', 'חברות סחורות מחזוריות: הצמיחה הולכת אחרי המחירים', '| O |', 'עברה את התנאים'):
            self.assertIn(needle, text)
        passers = text.split('## כמעט עברו')[0]
        self.assertNotIn('| O |', passers)

    def test_version_2_sections_and_columns(self):
        text = qm.render_he(self.res())
        for needle in ('# חיפוש מניות: איכות העסק + תנופה עסקית', '**המחיר הוא מידע בלבד, לא תנאי** (מאז 25.11.2026)', 'DELL',
                       'דפי האנליסטים של Nasdaq (הנתונים של Zacks; ממשק ציבורי לא רשמי', 'חלון של כחודש (30 יום)', 'עד 100 יום',
                       'תנופה עסקית, תנאי 1, תחזיות הרווח לא יורדות', 'לא ירדה ביותר מ-1%', 'תנופה עסקית, תנאי 2, עמידה בתחזית',
                       'פספוס של עד 2% נחשב עמידה', 'מחיר (מידע בלבד, לא תנאי)',
                       '| שינוי בתחזית הרווח | הפתעה ברווח, 2 רבעונים | תשואה 12-1 (מידע) | מרחק מהשיא (מידע) |',
                       '+0.7% ב-30 יום; 4 העלו, 0 הורידו', '+39.9% / +43.2%',
                       'אנליסטים לא מורידים תחזיות רווח', '-4.5% ב-30 יום; 4 העלו, 0 הורידו',          # the near miss on revisions
                       'בלי אחוז (תחזית רווח אפס או שלילית); 4 העלו, 0 הורידו', '~ אין לחברה אחוז שינוי בתחזית',
                       'שינוי בתחזית הרווח), בלי משקלות', 'מקבלת ברכיב הזה את החציון של האחרות',
                       '## המחיר ירד, העסק משתפר (1)', '| DN ◆ |', 'מסומנת ◆',
                       '## רדאר דוחות', '### דיווחו ב-10 הימים האחרונים (1)', '### מדווחות ב-14 הימים הקרובים (1)',
                       '| 10.11.2026 | S | S Corp | 2026-10 | 1.10$ | 1.00$ | +10.0% | עברה | כן |',
                       '| 20.11.2026 | אחרי המסחר | DN | DN Corp | 2026-10 | 2.50$ | 8 | כמעט (נכשל: צמיחה עקבית בשלוש השנים האחרונות) | — |',
                       'ושני תנאי התנופה העסקית', 'חסר: אנליסטים לא מורידים תחזיות רווח', 'לוח הדוחות של Nasdaq', 'לא נבדק על העבר'):
            self.assertIn(needle, text)
        for gone in ('מחיר מעל ממוצע 200 יום', 'לא יותר מ-25% מתחת לשיא', 'עוצמה מול המדד', 'ושלושת תנאי המגמה', 'במגמת עלייה'):
            self.assertNotIn(gone, text)
        top = text.split('## מה החיפוש אומר ומה לא')[0]                 # the divergence section sits above the explanations
        self.assertIn('| DN | DN Corp | כמעט | -43% |', top)
        self.assertIn('צמיחה עקבית בשלוש השנים האחרונות (+3.5% בשנה, השנה החלשה -13.6%)', top)
        self.assertNotIn('| S |', top)
        unread = text.split('## לא נכשלו בשום תנאי, אבל חסר להן נתון')[1].split('##')[0]
        self.assertIn('אנליסטים לא מורידים תחזיות רווח, עמידה בתחזית הרווח בשני הרבעונים האחרונים', unread)

    def test_divergence_section_is_capped_and_names_the_rest(self):
        def flagged(sym, growth, **kw):
            return dict(screened(sym, closes=fallen(0.4), **kw), rev_growth_ttm=growth)
        cut_fcf = {'fcf': {'pass': False, 'value': -1.}}
        passers = qm.rank([flagged('P%02d' % i, 0.2 + i / 100) for i in range(14)] + [screened('CALM')])
        near = [flagged('N%02d' % i, 0.2 + i / 100, **cut_fcf) for i in range(14)]
        bank = dict(flagged('BANK', 0.3), route='financial')
        rows = qm.divergence_rows([('passers', 'עברה', passers), ('near_misses', 'כמעט', near), ('financials', 'פיננסים', [bank])])
        self.assertEqual(len(rows), 14 + 14 + 1)                    # CALM is near its high: not flagged
        self.assertEqual([r['symbol'] for r in rows[:14]], [r['symbol'] for r in passers if r['symbol'] != 'CALM'])   # rank order kept
        self.assertEqual([r['symbol'] for r in rows[14:17]], ['N13', 'N12', 'N11'])             # the fastest growing first
        res = dict(self.res(), divergence=rows)
        top = qm.render_he(res).split('## מה החיפוש אומר ומה לא')[0]
        self.assertIn('## המחיר ירד, העסק משתפר (29)', top)
        self.assertEqual(top.count(' Corp | עברה |'), 12)
        self.assertEqual(top.count(' Corp | כמעט |'), 12)
        self.assertIn('| N13 | N13 Corp | כמעט | -40% |', top)
        self.assertIn('עוד מאלה שכמעט עברו (2): N01 (-40%), N00 (-40%).', top)
        self.assertIn('עוד מהעוברות (2): ', top)
        self.assertIn('ברשימות האחרות (פיננסים, זרות עם נתונים שנתיים) (1): BANK (-40%).', top)
        self.assertIn('בטבלה: עד 12 מהעוברות (לפי הדירוג) ועד 12 מאלה שכמעט עברו', top)

    def test_report_without_the_new_lists_still_renders(self):
        res = self.res()
        res['divergence'], res['radar'] = [], {'reported': [], 'upcoming': [], 'gaps': ['2026-11-16']}
        text = qm.render_he(res)
        self.assertIn('## המחיר ירד, העסק משתפר (0)', text)
        self.assertIn('אין היום חברות כאלה ברשימות.', text)
        self.assertIn('ימים שלוח הדוחות שלהם לא נקרא: 2026-11-16.', text)
        del res['divergence'], res['radar']
        self.assertNotIn('רדאר דוחות', qm.render_he(res))


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


def load_screen():
    spec = importlib.util.spec_from_file_location('quality_momentum_screen', os.path.join(REPO, 'scripts', 'quality_momentum_screen.py'))
    screen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(screen)
    return screen


def run_offline(screen, tmp, names, estimates=None, calendar=None, research=None, as_of=AS_OF):
    """The script end to end without a network: every raw file it would fetch is written first and --reuse reads them.
    names: {symbol: (cik, daily closes)}; estimates: {symbol: (surprise, forecast, momentum) page JSONs} (default: the
    passing record; None = the pages gave nothing); calendar: {day: raw rows}. Any fetch fails the test.
    Returns (screen.json, screen_he.md, the output folder)."""
    out = os.path.join(tmp, 'screens', as_of)
    research = research or os.path.join(tmp, 'research')
    os.makedirs(research, exist_ok=True)
    _write(os.path.join(out, 'raw', 'nasdaq__screener_stocks.json'), {'data': {'rows': [
        {'symbol': s, 'name': f'{s} Inc. Common Stock', 'marketCap': '9000000000', 'lastsale': '$%.2f' % b[-1],
         'volume': '1000', 'sector': 'Technology'} for s, (c, b) in names.items()]}})
    _write(os.path.join(out, 'raw', 'sec__company_tickers_exchange.json'),
           {'fields': ['cik', 'name', 'ticker', 'exchange'], 'data': [[c, s, s, 'Nasdaq'] for s, (c, b) in names.items()]})
    for s, (c, b) in names.items():
        _write(os.path.join(out, 'facts', f'CIK{c:010d}.json'), {
            'cik': c, 'v': screen.FACTS_V, 'as_of': as_of, 'series': series(REV, OP, CFO, CAPEX), 'errors': [],
            'sub': {'sic': '7372', 'sic_description': 'services-prepackaged software', 'fiscal_year_end': '1231', 'name': s,
                    'latest_periodic': {'form': '10-Q', 'filed': '2026-10-30', 'period': '2026-09-30'}}})
        pages = (estimates or {}).get(s, (surprise_json(), forecast_json(), momentum_json()))
        raw = {'surprise': None, 'forecast': None, 'momentum': None} if pages is None else {
            k: (j or {}).get('data') for k, j in zip(('surprise', 'forecast', 'momentum'), pages)}
        _write(os.path.join(out, 'estimates', s + '.json'),
               {'symbol': s, 'queried_as': s, 'retrieved_at_utc': as_of + 'T12:00:00+00:00', 'raw': raw,
                'errors': ['surprise: No record found. (%s)' % s] if pages is None else []})
    for s, b in [(s, b) for s, (c, b) in names.items()] + [('SPY', bars(0.0005))]:
        b = [round(x, 4) for x in b]
        _write(os.path.join(out, 'prices', s + '.json'),
               {'symbol': s, 'dates': _dates(len(b)), 'adjclose': b, 'close': b, 'high': b, 'error': None, 'currency': 'USD'})
    for day in qm.calendar_days(as_of):
        _write(os.path.join(out, 'calendar', day + '.json'), {'date': day, 'rows': (calendar or {}).get(day, []), 'error': None})
    with mock.patch.dict(os.environ, {'SEC_USER_AGENT': 'unit-test test@example.invalid'}), redirect_stdout(StringIO()), \
            mock.patch.object(screen, 'fetch', side_effect=AssertionError('the offline test reached the network')) as net:
        screen.main([out, '--as-of', as_of, '--reuse', '--research', research])
    assert not net.called, net.call_args_list
    with open(os.path.join(out, 'screen.json'), encoding='utf-8') as f:
        res = json.load(f)
    with open(os.path.join(out, 'screen_he.md'), encoding='utf-8') as f:
        return res, f.read(), out


class ScreenMarksSavedValuations(unittest.TestCase):
    """The script end to end, offline (--reuse over saved raw files): one passing, one failing and one rejected passer are
    all listed, and the failing and rejected ones are marked."""

    def test_failing_and_rejected_passers_are_marked_not_dropped(self):
        names = {'PASSCO': (1, bars(0.0007)), 'FAILCO': (2, bars(0.002)), 'REJCO': (3, bars(0.002))}     # closes about 120, 168
        with tempfile.TemporaryDirectory() as tmp:
            research = os.path.join(tmp, 'research')
            research_tree(research)
            res, text, _ = run_offline(load_screen(), tmp, names, research=research)
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


class ScreenVersion2(unittest.TestCase):
    """The script end to end, offline: price changes no status, analyst data does, and the new lists are written."""

    NAMES = {'UPCO': (1, bars(0.002)), 'DROPCO': (2, fallen(0.6)), 'CUTCO': (3, bars(0.002)), 'MISSCO': (4, fallen(0.6)),
             'NODATA': (5, bars(0.002)), 'LOSSCO': (6, bars(0.001))}
    ESTIMATES = {'CUTCO': (surprise_json(), forecast_json((('Jan 2027', 24.0, 10, 0, 6), ('Jan 2028', 28.0, 9, 1, 3))),
                           momentum_json(current=24.0, month=25.13)),                          # analysts are cutting
                 'MISSCO': (surprise_json(((1.0, '2.0', '-50', '11/10/2026'), (1.0, '1.5', '-33.3', '8/11/2026'))), forecast_json(),
                            momentum_json(current=20.0, month=25.13)),                         # cuts and misses: the business weakened
                 'NODATA': None,
                 'LOSSCO': (surprise_json(), forecast_json((('Dec 2026', -2.72, 6, 4, 0), ('Dec 2027', -1.5, 6, 4, 1))),
                            momentum_json(current=-2.72, month=-2.92, fy='Fiscal Year End Dec 2026'))}
    CALENDAR = {'2026-11-10': [cal(0, 'DROPCO', '$9.16', '$6.55', '39.85'), cal(0, 'MISSCO', '$1.00', '$2.00', '-50'),
                               cal(0, 'UPCO', '$1.02', '$1.00', '2.0')],
                '2026-11-18': [cal(0, 'CUTCO', None, '$6.00'), cal(0, 'OTHER', None, '$1.00')]}

    def run_screen(self, tmp, **kw):
        return run_offline(load_screen(), tmp, self.NAMES, kw.pop('estimates', self.ESTIMATES), self.CALENDAR, **kw)

    def test_price_changes_no_status_and_the_business_gates_do(self):
        with tempfile.TemporaryDirectory() as tmp:
            res, text, out = self.run_screen(tmp)
            history = os.path.join(tmp, 'screens', '_estimates_history')
            with open(os.path.join(history, 'DROPCO.jsonl'), encoding='utf-8') as f:
                lines = [json.loads(x) for x in f]
            self.assertFalse(os.path.exists(os.path.join(history, 'NODATA.jsonl')))             # nothing to record
        by = {r['symbol']: r for r in res['rows']}
        self.assertEqual({s: r['status'] for s, r in by.items()},
                         {'UPCO': 'pass', 'DROPCO': 'pass', 'LOSSCO': 'pass', 'CUTCO': 'near_miss', 'MISSCO': 'fail', 'NODATA': 'incomplete'})
        drop = by['DROPCO']
        self.assertAlmostEqual(drop['price_info']['below_high'], -0.6, places=3)                # 60% below its high, and a passer
        self.assertLess(drop['price_info']['vs_ma200'], 0)
        self.assertEqual((drop['divergence'], drop['flags'], drop['failed'], drop['missing']), (True, ['price_down_business_up'], [], []))
        self.assertEqual([g['gate'] for g in drop['business']], ['eps_revisions', 'earnings_beats'])
        self.assertAlmostEqual(drop['eps_revision_pct'], 25.3 / 25.13 - 1)
        self.assertEqual((drop['eps_revision_window_days'], drop['surprise_min_pct']), (30, 39.85))
        self.assertIn('trend_measures', drop)
        self.assertAlmostEqual(drop['rel_strength'], drop['price_info']['rel_strength'])
        self.assertNotIn('trend', drop)                                                         # the price gates are gone
        self.assertEqual([r['symbol'] for r in res['passers']], ['DROPCO', 'LOSSCO', 'UPCO'])   # tied on every part: alphabetical
        self.assertEqual(by['LOSSCO']['eps_revision_pct'], None)
        self.assertEqual(next(r for r in res['passers'] if r['symbol'] == 'LOSSCO')['rank_filled'], ['eps_revision_pct'])
        self.assertEqual([(r['symbol'], r['failed'][0]['gate']) for r in res['near_misses']], [('CUTCO', 'eps_revisions')])
        self.assertEqual({g['gate'] for g in by['MISSCO']['failed']}, {'eps_revisions', 'earnings_beats'})
        self.assertFalse(by['MISSCO']['divergence'])                                            # it fell because the business weakened
        self.assertEqual([r['symbol'] for r in res['unread']], ['NODATA'])
        self.assertEqual([g['gate'] for g in by['NODATA']['missing']], ['eps_revisions', 'earnings_beats'])
        self.assertEqual([x['symbol'] for x in res['gaps']['estimates']], ['NODATA'])
        self.assertEqual([r['symbol'] for r in res['divergence']], ['DROPCO'])
        self.assertEqual((res['counts']['divergence'], res['counts']['business_fetched'], res['counts']['analyst_data_gaps']), (1, 6, 1))
        self.assertEqual([(x['date'], x['symbol'], x['surprise_pct']) for x in res['radar']['reported']], [('2026-11-10', 'DROPCO', 39.85)])
        self.assertEqual([(x['date'], x['symbol'], x['failed_gate']) for x in res['radar']['upcoming']], [('2026-11-18', 'CUTCO', 'eps_revisions')])
        self.assertEqual((res['method']['version'], res['method']['rank_keys'][-1]), (2, 'eps_revision_pct'))
        self.assertNotIn('max_below_high', res['method']['gates'])
        self.assertIn('never a gate', res['method']['price'])
        self.assertIn('Zacks', res['method']['business_gates']['source'])
        self.assertIn('not backtested', res['method']['not'])
        self.assertEqual(lines, [{'as_of': AS_OF, 'fiscal_year_end': fy, 'consensus': c, 'n': n}
                                 for fy, c, n in (('2027-01', 25.3, 10), ('2028-01', 29.59, 9), ('2029-01', 31.25, 3))])
        for needle in ('## המחיר ירד, העסק משתפר (1)', '| DROPCO | DROPCO Inc. Common Stock | עברה | -60% |', '| 1 | DROPCO ◆ |',
                       '| CUTCO | CUTCO Inc. Common Stock | 9B$ | אנליסטים לא מורידים תחזיות רווח | -4.5% ב-30 יום; 1 העלו, 9 הורידו |',
                       '### דיווחו ב-10 הימים האחרונים (1)', '| 10.11.2026 | DROPCO |', '### מדווחות ב-14 הימים הקרובים (1)',
                       '| 18.11.2026 | אחרי המסחר | CUTCO |', '| NODATA |', 'חברות בלי נתוני אנליסטים מלאים בדפי Nasdaq',
                       '| המחיר ירד, העסק משתפר | 1 |'):
            self.assertIn(needle, text)

    def test_a_second_run_reads_our_own_history_and_adds_one_line_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = os.path.join(tmp, 'screens', '_estimates_history')
            old = [{'as_of': '2026-09-16', 'fiscal_year_end': '2027-01', 'consensus': 26.0, 'n': 10},
                   {'as_of': '2026-09-16', 'fiscal_year_end': '2028-01', 'consensus': 30.0, 'n': 9}]
            os.makedirs(history)
            with open(os.path.join(history, 'UPCO.jsonl'), 'w', encoding='utf-8') as f:
                f.write(''.join(json.dumps(x) + '\n' for x in old) + 'not json\n')
            res, text, _ = self.run_screen(tmp)
            self.run_screen(tmp)                                                                # the same day again adds nothing
            with open(os.path.join(history, 'UPCO.jsonl'), encoding='utf-8') as f:
                lines = f.read().splitlines()
            with open(os.path.join(history, 'DROPCO.jsonl'), encoding='utf-8') as f:
                self.assertEqual(len(f.read().splitlines()), 3)
        up = next(r for r in res['rows'] if r['symbol'] == 'UPCO')
        rev = up['business'][0]
        self.assertEqual((rev['source'], rev['window_days'], rev['then'], rev['pass']), ('own_history', 60, 26.0, False))   # -2.7% over 60 days
        self.assertEqual(up['status'], 'near_miss')
        self.assertIn('-2.7% ב-60 יום; 4 העלו, 0 הורידו', text)
        self.assertEqual(len(lines), 3 + 3)                                                     # two old, the broken line, three new

    def test_history_is_dated_by_the_day_the_pages_were_read(self):
        screen = load_screen()
        with tempfile.TemporaryDirectory() as tmp:
            real_load = screen._load

            def older(path):                                        # the saved pages were read 26 days before the as-of date
                rec = real_load(path)
                if os.sep + 'estimates' + os.sep in path:
                    rec['retrieved_at_utc'] = '2026-10-20T12:00:00+00:00'
                return rec
            with mock.patch.object(screen, '_load', side_effect=older):
                res, _, _ = run_offline(screen, tmp, {'UPCO': self.NAMES['UPCO']})
            with open(os.path.join(tmp, 'screens', '_estimates_history', 'UPCO.jsonl'), encoding='utf-8') as f:
                lines = [json.loads(x) for x in f]
        self.assertEqual(res['rows'][0]['status'], 'pass')
        self.assertEqual({x['as_of'] for x in lines}, {'2026-10-20'})                           # never the later as-of date


class Fetching(unittest.TestCase):
    """get_estimates and get_calendar against a faked fetch: share-class spellings, 'no record', a network error."""

    def setUp(self):
        self.screen = load_screen()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        for sub in ('estimates', 'calendar'):
            os.makedirs(os.path.join(self.out, sub))
        self.throttle = self.screen.Throttle(0.)

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, answers):
        """answers: {text found in the URL: body dict | Exception | int status}; the first match answers."""
        calls = []

        def fetch(url, headers, attempts=3):
            calls.append((url, dict(headers), attempts))
            for part, ans in answers.items():
                if part in url:
                    if isinstance(ans, Exception):
                        raise ans
                    if isinstance(ans, int):
                        return ans, b''
                    return 200, json.dumps(ans).encode()
            raise AssertionError(url)
        return calls, mock.patch.object(self.screen, 'fetch', side_effect=fetch)

    def test_three_pages_with_browser_headers_saved_as_one_file(self):
        calls, patch = self.fake({'earnings-surprise': surprise_json(), 'earnings-forecast': forecast_json(), 'estimate-momentum': momentum_json()})
        with patch:
            rec = self.screen.get_estimates('DELL', self.out, False, self.throttle)
            again = self.screen.get_estimates('DELL', self.out, True, self.throttle)        # --reuse: read from the file
        self.assertEqual([c[0] for c in calls], ['https://api.nasdaq.com/api/company/DELL/earnings-surprise',
                                                'https://api.nasdaq.com/api/analyst/DELL/earnings-forecast',
                                                'https://api.nasdaq.com/api/analyst/DELL/estimate-momentum'])
        for _, headers, attempts in calls:
            self.assertEqual((headers['Origin'], headers['Referer'], headers['Accept'], attempts),
                             ('https://www.nasdaq.com', 'https://www.nasdaq.com/', 'application/json', 2))      # one retry
            self.assertTrue(headers['User-Agent'].startswith('Mozilla'))
            self.assertNotIn('@', ' '.join(headers.values()))                               # no contact detail goes to Nasdaq
        self.assertEqual((rec['queried_as'], rec['errors'], again['raw']), ('DELL', [], rec['raw']))
        e = qm.parse_estimates(rec['raw']['surprise'], rec['raw']['forecast'], rec['raw']['momentum'])
        self.assertTrue(all(g['pass'] for g in qm.business_gates(e, as_of=AS_OF)))
        self.assertEqual(os.listdir(os.path.join(self.out, 'estimates')), ['DELL.json'])

    def test_share_class_symbol_is_tried_in_its_other_spelling(self):
        no_symbol = {'data': None, 'status': {'rCode': 400, 'bCodeMessage': [{'code': 1001, 'errorMessage': 'Symbol not exists.'}]}}
        no_record = {'data': None, 'status': {'rCode': 200, 'bCodeMessage': [{'code': 1002, 'errorMessage': 'No record found.'}]}}
        calls, patch = self.fake({'/BRK-B/': no_symbol, 'company/BRK.B/': no_record, 'BRK.B/earnings-forecast': {
            'data': {'symbol': 'BRK/B', 'quarterlyForecast': None, 'yearlyForecast': None}, 'status': {'rCode': 200}},
            'BRK.B/estimate-momentum': no_record})
        with patch:
            rec = self.screen.get_estimates('BRK-B', self.out, False, self.throttle)
        self.assertEqual(len(calls), 1 + 3)                         # one page says the hyphen form is unknown, then three dotted
        self.assertEqual(rec['queried_as'], 'BRK.B')
        self.assertTrue(any('No record found' in x for x in rec['errors']))
        self.assertTrue(os.path.exists(os.path.join(self.out, 'estimates', 'BRK-B.json')))  # a real answer: saved as a gap
        e = qm.parse_estimates(rec['raw']['surprise'], rec['raw']['forecast'], rec['raw']['momentum'])
        self.assertTrue(all(g['pass'] is None for g in qm.business_gates(e, as_of=AS_OF)))

    def test_unknown_symbol_and_network_error_are_gaps_not_crashes(self):
        calls, patch = self.fake({'/ZZZZ/': 404})
        with patch:
            rec = self.screen.get_estimates('ZZZZ', self.out, False, self.throttle)
        self.assertEqual((rec['queried_as'], rec['raw'], len(calls)), (None, {'surprise': None, 'forecast': None, 'momentum': None}, 1))
        self.assertTrue(rec['errors'])
        calls, patch = self.fake({'earnings-surprise': surprise_json(), 'earnings-forecast': RuntimeError('reset'), 'estimate-momentum': momentum_json()})
        with patch:
            rec = self.screen.get_estimates('NETERR', self.out, False, self.throttle)
        self.assertTrue(rec['fetch_failed'])
        self.assertIsNone(rec['raw']['forecast'])
        self.assertIsNotNone(rec['raw']['momentum'])
        self.assertFalse(os.path.exists(os.path.join(self.out, 'estimates', 'NETERR.json')))        # not saved: the next run tries again

    def test_calendar_day(self):
        day = {'data': {'asOf': 'Tue, Nov 24, 2026', 'rows': [cal(0, 'DELL', '$9.16', '$6.55', '39.85')]}, 'status': {'rCode': 200}}
        calls, patch = self.fake({'date=2026-11-24': day, 'date=2026-11-21': {'data': {'asOf': 'Sat', 'headers': None, 'rows': None}, 'status': {'rCode': 200}},
                                  'date=2026-11-23': RuntimeError('reset')})
        with patch:
            got = self.screen.get_calendar('2026-11-24', self.out, False, self.throttle)
            weekend = self.screen.get_calendar('2026-11-21', self.out, False, self.throttle)
            failed = self.screen.get_calendar('2026-11-23', self.out, False, self.throttle)
        self.assertEqual(calls[0][0], 'https://api.nasdaq.com/api/calendar/earnings?date=2026-11-24')
        self.assertEqual(qm.parse_calendar('2026-11-24', got['rows'])[0]['surprise_pct'], 39.85)
        self.assertEqual((weekend['rows'], weekend['error']), ([], None))
        self.assertEqual((failed['rows'], failed['error']), ([], 'RuntimeError'))
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, 'calendar'))), ['2026-11-21.json', '2026-11-24.json'])


if __name__ == '__main__':
    unittest.main()
