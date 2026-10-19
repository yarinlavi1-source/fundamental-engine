import unittest
from datetime import date
from fundamental_engine.pit import quarterly, instant, available, ttm_at, company


def fact(start, end, val, filed, form='10-Q'):
    return {'start': start, 'end': end, 'val': val, 'filed': filed, 'form': form}


FACTS = {'us-gaap': {'Revenues': {'units': {'USD': [
    fact('2024-01-01', '2024-03-31', 100, '2024-05-01'),
    fact('2024-04-01', '2024-06-30', 110, '2024-08-01'),
    fact('2024-01-01', '2024-06-30', 210, '2024-08-01'),
    fact('2024-07-01', '2024-09-30', 120, '2024-11-01'),
    fact('2024-01-01', '2024-09-30', 330, '2024-11-01'),
    fact('2024-01-01', '2024-12-31', 460, '2025-02-15', '10-K'),         # Q4 only via FY - 9M = 130
    fact('2024-04-01', '2024-06-30', 999, '2025-02-15', '10-K'),         # later restatement: ignored (first filed wins)
    fact('2025-01-01', '2025-03-31', 140, '2025-05-01'),
]}}}}


class PIT(unittest.TestCase):
    def test_quarters_first_filed_and_q4_derived(self):
        q = quarterly(FACTS, ['Revenues'])
        self.assertEqual(q['2024-06-30'], (110.0, '2024-08-01'))
        self.assertEqual(q['2024-12-31'], (130.0, '2025-02-15'))

    def test_no_lookahead(self):
        q = quarterly(FACTS, ['Revenues'])
        self.assertEqual(len(available(q, date(2025, 2, 16))), 3)       # Q4 filed 2/15 not usable until 2/17
        self.assertEqual(len(available(q, date(2025, 2, 17))), 4)

    def test_ttm(self):
        q = quarterly(FACTS, ['Revenues'])
        v, end = ttm_at(q, date(2025, 6, 1))
        self.assertEqual((v, end), (500.0, '2025-03-31'))               # 110+120+130+140
        self.assertEqual(ttm_at(q, date(2025, 6, 1), offset=1)[0], 460.0)

    def test_instant_first_filed(self):
        f = {'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [
            {'end': '2024-07-25', 'val': 10, 'filed': '2024-08-01'}, {'end': '2024-07-25', 'val': 11, 'filed': '2024-09-01'}]}}}}
        self.assertEqual(instant(f, ['EntityCommonStockSharesOutstanding'], 'dei', 'shares'), {'2024-07-25': (10.0, '2024-08-01')})

    def test_company_record(self):
        rec = company({'entityName': 'X', 'facts': FACTS})
        self.assertFalse(rec['financial'])
        self.assertIn('2024-12-31', rec['rev'])


if __name__ == '__main__':
    unittest.main()


class MultiClass(unittest.TestCase):
    def test_cover_shares_summed_across_classes_same_filing(self):
        f = {'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [
            {'end': '2024-07-25', 'val': 10, 'filed': '2024-08-01', 'accn': 'a1'},
            {'end': '2024-07-25', 'val': 3, 'filed': '2024-08-01', 'accn': 'a1'},
            {'end': '2024-07-25', 'val': 10, 'filed': '2024-08-01', 'accn': 'a1'}]}}}}
        self.assertEqual(instant(f, ['EntityCommonStockSharesOutstanding'], 'dei', 'shares', sum_classes=True), {'2024-07-25': (13.0, '2024-08-01')})


class Fixes20261019(unittest.TestCase):
    def test_sixteen_week_q4_is_derived(self):
        # Costco-style: 12/12/12-week Q1-Q3, FY 52 weeks -> Q4 = 16 weeks (112 days)
        f = {'us-gaap': {'Revenues': {'units': {'USD': [
            fact('2015-08-31', '2015-11-22', 100, '2015-12-20'),
            fact('2015-11-23', '2016-02-14', 100, '2016-03-10'), fact('2015-08-31', '2016-02-14', 200, '2016-03-10'),
            fact('2016-02-15', '2016-05-08', 100, '2016-06-05'), fact('2015-08-31', '2016-05-08', 300, '2016-06-05'),
            fact('2015-08-31', '2016-08-28', 440, '2016-10-12', '10-K')]}}}}
        q = quarterly(f, ['Revenues'])
        self.assertEqual(q['2016-08-28'], (140.0, '2016-10-12'))
        self.assertEqual(ttm_at(q, date(2016, 11, 1))[0], 440.0)

    def test_share_fallback_chain(self):
        from fundamental_engine.pit import shares_at
        rec = {'shares': {}, 'shares_bs': {'2020-04-30': (210.0, '2020-06-01')},
               'shares_wavg': {'2020-04-30': (205.0, '2020-06-01')}}
        self.assertEqual(shares_at(rec, date(2020, 6, 30)), ('2020-04-30', 210.0, 'balance_sheet'))
        rec['shares'] = {'2020-05-31': (220.0, '2020-06-01')}
        self.assertEqual(shares_at(rec, date(2020, 6, 30))[2], 'dei_cover')
        rec2 = {'shares': {}, 'shares_bs': {}, 'shares_wavg': {'2020-04-30': (205.0, '2020-06-01')}}
        self.assertEqual(shares_at(rec2, date(2020, 6, 30))[2], 'weighted_basic')
        self.assertEqual(shares_at(rec2, date(2021, 6, 30)), (None, None, None))     # stale beyond 200 days

    def test_weighted_shares_never_differenced(self):
        from fundamental_engine.pit import share_durations
        f = {'us-gaap': {'WeightedAverageNumberOfSharesOutstandingBasic': {'units': {'shares': [
            fact('2024-01-01', '2024-09-30', 300, '2024-11-01'), fact('2024-01-01', '2024-06-30', 290, '2024-08-01'),
            fact('2024-07-01', '2024-09-30', 305, '2024-11-01')]}}}}
        self.assertEqual(share_durations(f), {'2024-09-30': (305.0, '2024-11-01')})


class EarliestAcrossConcepts(unittest.TestCase):
    def test_on_time_old_tag_beats_late_new_tag(self):
        f = {'us-gaap': {
            'Revenues': {'units': {'USD': [fact('2017-10-01', '2017-12-31', 99, '2019-02-21', '10-K')]}},
            'SalesRevenueNet': {'units': {'USD': [fact('2017-10-01', '2017-12-31', 98, '2018-02-15', '10-K')]}}}}
        self.assertEqual(quarterly(f, ['Revenues', 'SalesRevenueNet'])['2017-12-31'], (98.0, '2018-02-15'))

    def test_same_filing_keeps_concept_order(self):
        f = {'us-gaap': {
            'Revenues': {'units': {'USD': [fact('2017-10-01', '2017-12-31', 100, '2018-02-15', '10-K')]}},
            'SalesRevenueGoodsNet': {'units': {'USD': [fact('2017-10-01', '2017-12-31', 60, '2018-02-15', '10-K')]}}}}
        self.assertEqual(quarterly(f, ['Revenues', 'SalesRevenueGoodsNet'])['2017-12-31'][0], 100.0)


class DerivedBeatsLateDirect(unittest.TestCase):
    def test_on_time_derivation_wins_over_direct_filed_a_year_later(self):
        f = {'us-gaap': {'OperatingIncomeLoss': {'units': {'USD': [
            fact('2014-07-01', '2015-03-31', 300, '2015-04-23'),
            fact('2014-07-01', '2015-06-30', 280, '2015-07-31', '10-K'),
            fact('2015-04-01', '2015-06-30', -21, '2016-07-28', '10-K')]}}}}       # quarterly note in next 10-K
        self.assertEqual(quarterly(f, ['OperatingIncomeLoss'])['2015-06-30'], (-20.0, '2015-07-31'))
