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


class BundleRegressionTests(unittest.TestCase):
    """Found on the real Adobe bundle (20.10.2026): FY rows loaded as quarters inflated TTM revenue to 43.5bn."""

    def bundle_fb(self):
        fb = F.new({'ticker': 'FIX', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 'f', 'fmp', 'x', 'filing_derived', '2026-10-19')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-11-28', 100, 'f', 'as_reported', '2026-01-15')
        src = [{'id': 's', 'title': 'releases', 'url': 'https://example.com', 'kind': 'issuer_release', 'published_at': '2026-09-20'}]
        rows = [('FY2025', '2025-11-28', 100)] + [(f'FY2025Q{i}', e, v) for i, e, v in ((1, '2025-02-28', 22), (2, '2025-05-30', 24), (3, '2025-08-29', 25))] \
            + [(f'FY2026Q{i}', e, v) for i, e, v in ((1, '2026-02-27', 26), (2, '2026-05-29', 27), (3, '2026-08-28', 28))]
        F.add_qualitative_bundle(fb, {'sources': src, 'quarterly_facts': [{'metric': 'revenue', 'period': p, 'period_end': e, 'value': v, 'source_ids': ['s']}
                                                                          for p, e, v in rows]
                                      + [{'metric': 'commercial_paper', 'period': 'FY2026Q3', 'period_end': '2026-08-28', 'value': 5, 'source_ids': ['s']},
                                         {'metric': 'debt', 'period': 'FY2026Q3', 'period_end': '2026-08-28', 'value': 50, 'source_ids': ['s']}]})
        return fb

    def test_fiscal_year_rows_stay_annual(self):
        fb = self.bundle_fb()
        t = F.ttm(fb, 'revenue')
        self.assertEqual((t['method'], t['value']), ('annual_plus_ytd', 100 + 26 + 27 + 28 - 22 - 24 - 25))
        self.assertNotIn('2025-11-28', [e for e, _, _ in F.series(fb, 'revenue', 'quarter')])

    def test_commercial_paper_is_not_added_to_issuer_debt_total(self):
        fb = self.bundle_fb()
        self.assertEqual(F.series(fb, 'short_term_borrowings', 'instant'), [])
        self.assertEqual(F.latest_instant(fb, 'long_term_debt_tagged')['value'], 50)

    def test_gap_in_quarters_does_not_make_a_ttm(self):
        fb = self.bundle_fb()
        fb['facts'] = [f for f in fb['facts'] if f['period_end'] not in ('2026-05-29', '2025-05-30')]
        self.assertEqual(F.ttm(fb, 'revenue')['method'], 'latest_fiscal_year')


# ---------------------------------------------------------------- review 2026-10-19 (scratchpad findings, data area)

def _sec(tags):
    """SYNTHETIC companyfacts payload: {tag: [(start, end, val, filed)]} annual 10-K rows."""
    return {'cik': 1, 'facts': {'us-gaap': {t: {'units': {'USD': [{'start': s, 'end': e, 'val': v, 'filed': f, 'form': '10-K',
                                                                   'accn': 'a' + f} for s, e, v, f in rows]}}
                                            for t, rows in tags.items()}}}


class SecAdapterTests(unittest.TestCase):
    def test_every_candidate_tag_is_merged_per_period(self):
        old = [(f'{y}-01-01', f'{y}-12-31', 1000.0 + y - 2015, f'{y + 1}-02-20') for y in (2015, 2016, 2017)]
        new = [(f'{y}-01-01', f'{y}-12-31', 2000.0 + y - 2018, f'{y + 1}-02-20') for y in range(2018, 2026)]
        new.append(('2017-01-01', '2017-12-31', 1999.0, '2019-02-20'))     # comparative filed a year later: not first-filed
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.from_sec_companyfacts(fb, _sec({'Revenues': old, 'RevenueFromContractWithCustomerExcludingAssessedTax': new}), '2026-10-19')
        s = F.series(fb, 'revenue')
        self.assertEqual([e[:4] for e, _, _ in s], [str(y) for y in range(2015, 2026)])
        self.assertEqual(F.value(fb, 'revenue', period_end='2017-12-31'), 1002.0)
        self.assertEqual(F.ttm(fb, 'revenue')['period_end'], '2025-12-31')
        tie = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')     # same filing tags both: tag order breaks the tie
        F.from_sec_companyfacts(tie, _sec({'Revenues': [('2025-01-01', '2025-12-31', 10.0, '2026-02-20')],
                                           'RevenueFromContractWithCustomerExcludingAssessedTax': [('2025-01-01', '2025-12-31', 9.0, '2026-02-20')]}),
                                '2026-10-19')
        self.assertEqual([f['value'] for f in tie['facts'] if f['metric'] == 'revenue'], [10.0])

    def test_fiscal_year_label_is_the_calendar_year_of_the_end(self):
        cases = [('2024-07-01', '2025-06-30', 'FY2025'), ('2024-06-01', '2025-05-31', 'FY2025'),
                 ('2025-01-27', '2026-01-25', 'FY2026'), ('2024-12-30', '2026-01-03', 'FY2025'), ('2024-11-30', '2025-11-28', 'FY2025')]
        for start, end, want in cases:
            fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
            F.from_sec_companyfacts(fb, _sec({'Revenues': [(start, end, 1.0, '2026-04-01')]}), '2026-10-19')
            F.from_fmp_as_reported(fb, {'income': [{'fiscalYear': int(want[2:]), 'period': 'FY', 'date': end, 'data': {'Revenues': 1.0}}]},
                                   '2026-10-19', filed_by_end={end: '2026-04-01'})
            self.assertEqual({f['period'] for f in fb['facts'] if f['metric'] == 'revenue'}, {want}, end)

    def test_fmp_row_without_fiscal_year_is_labelled_from_its_end(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.from_fmp_as_reported(fb, {'income': [{'period': 'FY', 'date': '2025-06-30', 'data': {'Revenues': 5.0}}]}, '2026-10-19')
        F.from_fmp_as_reported(fb, {'income': [{'period': 'Q1', 'date': '2025-09-30', 'data': {'Revenues': 2.0}}]}, '2026-10-19',
                               kind='quarter')
        self.assertEqual(sorted(f['period'] for f in fb['facts']), ['FY2025', 'Q@2025-09-30'])


class AvailabilityTests(unittest.TestCase):
    def test_fact_about_a_period_ending_after_as_of_is_not_usable(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-08-01')
        F.add_source(fb, 'v', 'fmp', 'shares-float', 'vendor_normalized', '2026-10-19')
        f = F.add_fact(fb, 'shares_outstanding_vendor', 'I@2026-08-28', '2026-08-28', 1.0, 'v', 'normalized', '2026-08-01')
        self.assertFalse(F.usable(fb, f))
        self.assertIsNone(F.latest_instant(fb, 'shares_outstanding_vendor'))

    def test_estimated_lags_by_filer(self):
        rows = {'income': [{'fiscalYear': 2025, 'period': 'FY', 'date': '2025-12-31', 'data': {'Revenues': 1.0}}]}
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.from_fmp_as_reported(fb, rows, '2026-10-19')
        self.assertEqual(fb['facts'][0]['available_at'], '2026-03-31')            # 10-K: 90 days
        fpi = F.new({'ticker': 'SYN', 'currency': 'USD', 'filer': 'foreign'}, '2026-10-19')
        F.from_fmp_as_reported(fpi, rows, '2026-10-19')
        self.assertEqual(fpi['facts'][0]['available_at'], '2026-04-30')           # 20-F: 120 days
        self.assertTrue(fpi['facts'][0]['available_at_estimated'])

    def test_bundle_dates_annual_lag_and_item_availability(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-01-15')
        F.add_qualitative_bundle(fb, {'sources': [{'id': 'k', 'kind': 'filing'}, {'id': 'r', 'kind': 'issuer_release', 'published_at': '2026-04-22'},
                                                  {'id': 'p', 'kind': 'press', 'published_at': '2025-12-12'}],
                                      'quarterly_facts': [{'metric': 'cfo', 'period': 'FY2025', 'period_end': '2025-11-28', 'value': 7.0, 'source_ids': ['k']}],
                                      'items': [{'id': 'i1', 'topic': 'segments', 'statement': 'FY segment split', 'kind': 'reported',
                                                 'source_ids': ['k'], 'as_of': '2025-11-28'},
                                                {'id': 'i2', 'topic': 'cash', 'statement': 'later source', 'kind': 'reported',
                                                 'source_ids': ['r', 'k'], 'as_of': '2025-11-28'},
                                                {'id': 'i3', 'topic': 'moat', 'statement': 'dated press', 'kind': 'opinion',
                                                 'source_ids': ['p'], 'as_of': '2025-12-01'}]})
        f = fb['facts'][0]
        self.assertEqual((f['period'], f['available_at'], f['available_at_estimated']), ('FY2025', '2026-02-26', True))   # annual lag
        self.assertFalse(F.usable(fb, f))
        self.assertEqual([x['id'] for x in fb['qualitative']], ['i3'])             # i1 undated -> FY end + 90; i2 waits for 2026-04-22
        self.assertEqual(fb['qualitative'][0]['available_at'], '2025-12-12')
        later = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-03-01')
        F.add_qualitative_bundle(later, {'sources': [{'id': 'k', 'kind': 'filing'}], 'quarterly_facts': [],
                                         'items': [{'id': 'i1', 'topic': 'segments', 'statement': 'FY segment split', 'kind': 'reported',
                                                    'source_ids': ['k'], 'as_of': '2025-11-28'}]})
        self.assertTrue(later['qualitative'] and later['qualitative'][0]['available_at_estimated'])

    def test_undated_sources_capped_at_bundle_retrieval(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_qualitative_bundle(fb, {'as_of': '2026-10-19', 'sources': [{'id': 'n', 'kind': 'press'}],
                                      'items': [{'id': 'i1', 'topic': 'risk', 'statement': 'recent', 'kind': 'reported',
                                                 'source_ids': ['n'], 'as_of': '2026-10-10'}]})
        self.assertEqual(fb['qualitative'][0]['available_at'], '2026-10-19')


class SourceKindTests(unittest.TestCase):
    def test_bundle_kinds_issuer_document_and_secondary(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_qualitative_bundle(fb, {'sources': [{'id': 'f', 'kind': 'filing', 'published_at': '2026-09-18'},
                                                  {'id': 'r', 'kind': 'issuer_release', 'published_at': '2026-09-10', 'retrieval': 'primary_document'},
                                                  {'id': 'p', 'kind': 'press', 'published_at': '2026-09-11'},
                                                  {'id': 'a', 'kind': 'analysis', 'published_at': '2026-09-12'},
                                                  {'id': 'b', 'kind': 'blog', 'published_at': '2026-09-12'}],
                                      'quarterly_facts': [{'metric': 'capex', 'period': 'FY2026Q3', 'period_end': '2026-08-28', 'value': 5.0,
                                                           'source_ids': ['p', 'r']}]})
        src = fb['sources']
        self.assertEqual({k: (v['kind'], v['dataset'], v['retrieval']) for k, v in src.items()},
                         {'web:f': ('issuer_document', 'filing', 'search_excerpt'), 'web:r': ('issuer_document', 'issuer_release', 'primary_document'),
                          'web:p': ('secondary', 'press', 'search_excerpt'), 'web:a': ('secondary', 'analysis', 'search_excerpt'),
                          'web:b': ('secondary', 'blog', 'search_excerpt')})
        f = fb['facts'][0]
        self.assertEqual((f['source_id'], f['available_at']), ('web:r', '2026-09-11'))   # issuer source; latest dated citation
        F.add_source(fb, 'legacy', 'x', 'y', 'document', '2026-10-19')                     # legacy kind still accepted
        self.assertIn('document', F.REPORTED_KINDS)

    def test_secondary_ranks_after_every_other_source(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 'p', 'web', 'press', 'secondary', '2026-10-19')
        F.add_source(fb, 'v', 'av', 'income', 'vendor_normalized', '2026-10-19')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 9.0, 'p', 'as_reported', '2026-01-10')
        F.add_fact(fb, 'revenue', 'AVFY@2025-12-31', '2025-12-31', 10.0, 'v', 'normalized', '2026-03-01')
        self.assertEqual(F.value(fb, 'revenue', period_end='2025-12-31'), 10.0)


class YtdWindowTests(unittest.TestCase):
    def test_ytd_must_follow_the_latest_fiscal_year(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 's', 'sec', 'x', 'filing_derived', '2026-10-19')
        F.add_fact(fb, 'cfo', 'FY2024', '2024-12-31', 400.0, 's', 'as_reported', '2025-02-20')
        F.add_fact(fb, 'cfo', 'YTD@2026-09-30', '2026-09-30', 450.0, 's', 'as_reported', '2026-10-15')
        F.add_fact(fb, 'cfo', 'YTD@2025-09-30', '2025-09-30', 330.0, 's', 'as_reported', '2025-10-15')
        t = F.ttm(fb, 'cfo')
        self.assertEqual((t['method'], t['value'], t['period_end']), ('latest_fiscal_year', 400.0, '2024-12-31'))
        self.assertIn('366 days', t['ytd_rejected'])

    def test_signs_reproduce_the_value(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 's', 'sec', 'x', 'filing_derived', '2026-10-19')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 400.0, 's', 'as_reported', '2026-02-20')
        for end, v in (('2025-03-31', 90), ('2025-06-30', 100), ('2025-09-30', 110), ('2026-03-31', 110), ('2026-06-30', 120), ('2026-09-30', 130)):
            F.add_fact(fb, 'revenue', 'Q@' + end, end, v, 's', 'as_reported', '2026-10-15')
        t = F.ttm(fb, 'revenue')
        self.assertEqual((t['method'], t['value']), ('annual_plus_ytd', 460.0))
        self.assertEqual(sum(s * f['value'] for s, f in zip(t['signs'], t['facts'])), 460.0)
