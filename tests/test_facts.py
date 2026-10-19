import json
import os
import unittest

from fundamental_engine import facts as F

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'adbe_factbook_2026-10-19.json')


def adbe():
    with open(FIX, encoding='utf-8') as f:
        return json.load(f)


def sec_payload():
    def dur(start, end, val, filed, form='10-K', fp='FY'):
        return {'start': start, 'end': end, 'val': val, 'filed': filed, 'form': form, 'fp': fp, 'accn': 'a-' + filed}

    return {'cik': 1, 'entityName': 'Fixture Co', 'facts': {
        'us-gaap': {
            'Revenues': {'units': {'USD': [
                dur('2023-01-01', '2023-12-31', 100.0, '2024-02-20'),
                dur('2024-01-01', '2024-12-31', 120.0, '2025-02-20'),
                dur('2024-01-01', '2024-12-31', 119.0, '2026-02-20'),          # later restatement: ignored for PIT
                dur('2025-01-01', '2025-03-31', 33.0, '2025-05-01', '10-Q', 'Q1'),
                dur('2025-04-01', '2025-06-30', 34.0, '2025-08-01', '10-Q', 'Q2'),
                dur('2025-01-01', '2025-06-30', 67.0, '2025-08-01', '10-Q', 'H1'),
            ]}},
            'CashAndCashEquivalentsAtCarryingValue': {'units': {'USD': [
                {'end': '2024-12-31', 'val': 50.0, 'filed': '2025-02-20', 'form': '10-K'},
                {'end': '2025-06-30', 'val': 55.0, 'filed': '2025-08-01', 'form': '10-Q'}]}},
        },
        'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [
            {'end': '2025-07-25', 'val': 60.0, 'filed': '2025-08-01', 'accn': 'q2'},
            {'end': '2025-07-25', 'val': 40.0, 'filed': '2025-08-01', 'accn': 'q2'}]}}},   # two share classes
    }}


class FactBookTests(unittest.TestCase):
    def test_adbe_fixture_reads_reported_values(self):
        fb = adbe()
        rev = [(e, v) for e, v, _ in F.series(fb, 'revenue')]
        self.assertEqual(rev[-1], ('2025-11-28', 23769000000.0))
        self.assertEqual(len(rev), 5)
        self.assertEqual(F.value(fb, 'operating_income', 'FY2025'), 8706000000.0)
        t = F.ttm(fb, 'revenue')
        self.assertEqual(t['method'], 'latest_fiscal_year')
        self.assertEqual(fb['market']['price']['value'], 246.87)
        # ambiguous debt scope is kept separate, never silently treated as total or noncurrent
        self.assertEqual(F.latest_instant(fb, 'long_term_debt_tagged')['value'], 6210000000.0)
        self.assertIsNone(F.latest_instant(fb, 'long_term_debt'))

    def test_lookahead_is_impossible_through_accessors(self):
        fb = adbe()
        fb['as_of'] = '2025-12-31'          # FY2025 10-K estimated available 2026-02-11
        self.assertEqual(F.series(fb, 'revenue')[-1][0], '2024-11-29')
        self.assertIsNone(F.get(fb, 'revenue', 'FY2025'))
        with self.assertRaises(ValueError):
            F.set_price(fb, 1.0, '2026-01-05', 'ib:snapshot')

    def test_sec_first_filed_value_and_filing_lag(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-06-30')
        F.from_sec_companyfacts(fb, sec_payload(), '2026-06-30T00:00:00Z')
        self.assertEqual(F.value(fb, 'revenue', period_end='2024-12-31'), 120.0)   # first filed, not the restatement
        f = F.get(fb, 'revenue', period_end='2024-12-31')
        self.assertEqual(f['available_at'], '2025-02-22')                          # filed + 2 days
        self.assertFalse(f['available_at_estimated'])
        q = [(e, v) for e, v, _ in F.series(fb, 'revenue', 'quarter')]
        self.assertIn(('2025-06-30', 34.0), q)
        cover = F.latest_instant(fb, 'shares_outstanding_cover')
        self.assertEqual(cover['value'], 100.0)                                     # classes summed
        early = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2025-02-21')
        F.from_sec_companyfacts(early, sec_payload(), '2026-06-30T00:00:00Z')
        self.assertIsNone(F.get(early, 'revenue', period_end='2024-12-31'))         # filed 02-20, usable 02-22

    def test_alpha_vantage_is_normalized_and_estimated(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-06-30')
        F.from_alpha_vantage(fb, {'income': {'annualReports': [{'fiscalDateEnding': '2025-12-31', 'totalRevenue': '500',
                                                                'operatingIncome': 'None'}]},
                                  'cashflow': {'annualReports': [{'fiscalDateEnding': '2025-12-31', 'capitalExpenditures': '-40'}]}},
                             '2026-06-30T00:00:00Z')
        f = F.get(fb, 'revenue')
        self.assertEqual((f['basis'], f['available_at_estimated']), ('normalized', True))
        self.assertEqual(F.value(fb, 'capex'), 40.0)
        self.assertIsNone(F.get(fb, 'operating_income'))

    def test_filing_derived_beats_vendor_normalized(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-06-30')
        F.add_source(fb, 'v', 'alpha_vantage', 'income', 'vendor_normalized', '2026-06-30')
        F.add_source(fb, 's', 'sec', 'companyfacts', 'filing_derived', '2026-06-30')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 10, 'v', 'normalized', '2026-03-01')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 11, 's', 'as_reported', '2026-03-01')
        self.assertEqual(F.value(fb, 'revenue', 'FY2025'), 11)
        self.assertEqual(len(F.candidates(fb, 'revenue', 'FY2025')), 2)

    def test_rejects_unsourced_and_nonfinite(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-06-30')
        with self.assertRaises(ValueError):
            F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 1, 'missing', 'as_reported', '2026-01-01')
        F.add_source(fb, 's', 'sec', 'x', 'filing_derived', '2026-06-30')
        with self.assertRaises(ValueError):
            F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', float('nan'), 's', 'as_reported', '2026-01-01')
        with self.assertRaises(ValueError):
            F.add_qualitative(fb, 'q1', 'moat', 'later claim', 'opinion', ['s'], '2026-07-01')


if __name__ == '__main__':
    unittest.main()


class TtmTests(unittest.TestCase):
    def test_annual_plus_year_to_date(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-10-01')
        F.add_source(fb, 's', 'sec', 'x', 'filing_derived', '2026-10-01')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-11-28', 100, 's', 'as_reported', '2026-01-15')
        for end, v in (('2025-02-28', 22), ('2025-05-30', 24), ('2026-02-27', 25), ('2026-05-29', 27)):
            F.add_fact(fb, 'revenue', 'Q@' + end, end, v, 's', 'as_reported', '2026-07-01')
        t = F.ttm(fb, 'revenue')
        self.assertEqual((t['method'], t['value'], t['period_end']), ('annual_plus_ytd', 106, '2026-05-29'))

    def test_unmatched_newer_quarter_is_reported_not_mixed(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-10-01')
        F.add_source(fb, 's', 'sec', 'x', 'filing_derived', '2026-10-01')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-11-28', 100, 's', 'as_reported', '2026-01-15')
        F.add_fact(fb, 'revenue', 'Q@2026-02-27', '2026-02-27', 25, 's', 'as_reported', '2026-04-01')
        t = F.ttm(fb, 'revenue')
        self.assertEqual((t['method'], t['value'], t['newer_quarters_unmatched']), ('latest_fiscal_year', 100, ['2026-02-27']))

    def test_bundle_dates_gate_availability(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-09-15')
        F.add_qualitative_bundle(fb, {'sources': [{'id': 's1', 'title': 'Q3 release', 'url': 'https://example.com/q3', 'kind': 'issuer_release',
                                                   'published_at': '2026-09-20', 'retrieval': 'search_excerpt'},
                                                  {'id': 's2', 'title': 'Q2 release', 'url': 'https://example.com/q2', 'kind': 'issuer_release',
                                                   'published_at': '2026-06-12', 'retrieval': 'search_excerpt'}],
                                      'quarterly_facts': [{'metric': 'revenue', 'period': 'FY2026Q3', 'period_end': '2026-08-28', 'value': 7.0, 'source_ids': ['s1']},
                                                          {'metric': 'revenue', 'period': 'FY2026Q2', 'period_end': '2026-05-29', 'value': 6.0, 'source_ids': ['s2']}],
                                      'items': [{'id': 'i1', 'topic': 'moat', 'statement': 'later', 'kind': 'opinion', 'source_ids': ['s1'], 'as_of': '2026-09-20'}]})
        self.assertEqual([e for e, _, _ in F.series(fb, 'revenue', 'quarter')], ['2026-05-29'])   # Q3 published after as_of
        self.assertEqual(fb['qualitative'], [])
        self.assertEqual(fb['sources']['web:s1']['retrieval'], 'search_excerpt')


class YtdTests(unittest.TestCase):
    def test_ttm_from_year_to_date_cash_flows(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-10-01')
        F.add_source(fb, 's', 'sec', 'x', 'filing_derived', '2026-10-01')
        F.add_fact(fb, 'cfo', 'FY2025', '2025-11-28', 100, 's', 'as_reported', '2026-01-15')
        F.add_fact(fb, 'cfo', 'YTD@2025-08-29', '2025-08-29', 70, 's', 'as_reported', '2025-09-20')
        F.add_fact(fb, 'cfo', 'YTD@2026-08-28', '2026-08-28', 80, 's', 'as_reported', '2026-09-20')
        t = F.ttm(fb, 'cfo')
        self.assertEqual((t['method'], t['value'], t['period_end']), ('annual_plus_ytd', 110, '2026-08-28'))
