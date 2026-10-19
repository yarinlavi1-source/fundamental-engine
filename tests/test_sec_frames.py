import unittest

from fundamental_engine import sec_frames as S


def frame(rows):
    return {'taxonomy': 'us-gaap', 'data': [{'accn': f'acc-{c}', 'cik': c, 'entityName': n, 'end': '2025-12-31', 'val': v} for c, n, v in rows]}


class SecFramesTests(unittest.TestCase):
    def test_urls_cover_every_metric(self):
        urls = S.frame_urls(2025)
        self.assertTrue(any(u['url'].endswith('/Revenues/USD/CY2025.json') for u in urls))
        self.assertTrue(any(u['url'].endswith('/CashAndCashEquivalentsAtCarryingValue/USD/CY2025Q4I.json') for u in urls))
        self.assertEqual({u['metric'] for u in urls}, set(S.FRAME_METRICS))

    def test_priority_never_substitutes_components(self):
        merged = S.merge_frames([
            {'metric': 'revenue', 'tag': 'RevenueFromContractWithCustomerExcludingAssessedTax', 'payload': frame([(1, 'A', 90.0)])},
            {'metric': 'revenue', 'tag': 'Revenues', 'payload': frame([(1, 'A', 100.0), (2, 'B', 50.0)])},
        ])
        self.assertEqual(merged[1]['revenue']['value'], 100.0)
        self.assertEqual(merged[1]['revenue']['tag'], 'Revenues')

    def test_rows_flag_ambiguous_debt_and_compute_fcf(self):
        merged = S.merge_frames([
            {'metric': 'cfo', 'tag': 'NetCashProvidedByUsedInOperatingActivities', 'payload': frame([(1, 'A', 30.0)])},
            {'metric': 'capex', 'tag': 'PaymentsToAcquirePropertyPlantAndEquipment', 'payload': frame([(1, 'A', 5.0)])},
            {'metric': 'long_term_debt_tagged', 'tag': 'LongTermDebt', 'payload': frame([(1, 'A', 40.0), (2, 'B', 7.0)])},
            {'metric': 'long_term_debt', 'tag': 'LongTermDebtNoncurrent', 'payload': frame([(2, 'B', 6.0)])},
            {'metric': 'current_portion_ltd', 'tag': 'LongTermDebtCurrent', 'payload': frame([(2, 'B', 1.0)])},
        ])
        rows = {r['symbol']: r for r in S.rows(merged, {1: 'AAA', 2: 'BBB'})}
        self.assertEqual((rows['AAA']['fcf'], rows['AAA']['debt_scope']), (25.0, 'ambiguous'))
        self.assertEqual((rows['BBB']['debt'], rows['BBB']['debt_scope']), (7.0, 'noncurrent_plus_current'))
        self.assertEqual(S.rows(merged, {}), [])

    def test_missing_debt_is_unknown_not_net_cash(self):
        from fundamental_engine import screen
        v = {'revenue': 5e9, 'gross_profit': 2e9, 'operating_income': 6e8, 'net_income': 3e8, 'cfo': 7e8, 'capex': 2e8,
             'cash': 4e8, 'equity': 1e9, 'shares_outstanding': 1e8}      # debt tagged outside FRAME_METRICS (e.g. convertible notes)
        merged = {101: {'name': 'Levered Co', **{k: {'value': x} for k, x in v.items()}}}
        rows = S.rows(merged, {101: 'LEV'}, {'LEV': {'sector': 'Industrials', 'industry': 'Building Products'}})
        self.assertEqual((rows[0]['debt'], rows[0]['debt_scope']), (None, 'missing'))
        r = screen.rows_from_sec_frames(rows, prices={'LEV': {'market_cap': 4e9}}, as_of='2026-10-19')[0]
        for k in ('net_debt', 'ev', 'roic'):
            self.assertNotIn(k, r['metrics'])
        fs = screen.scan([r], {'as_of': '2026-10-19'})['dimensions']['LEV']['financial_strength']
        self.assertFalse(any('net cash' in x for x in fs['reasons']))
        self.assertIsNone(fs['inputs']['net_debt_to_ebitda']['signal'])


if __name__ == '__main__':
    unittest.main()
