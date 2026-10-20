"""backtest.py: split handling for historical market values, look-ahead cut and share-basis check (SYNTHETIC data)."""
import unittest

from fundamental_engine import backtest as B


def px(months, splits=()):
    return {'months': months, 'splits': list(splits)}


class SplitTests(unittest.TestCase):
    def test_market_value_uses_splits_after_the_share_count_date(self):
        # 2:1 split on 2024-03-01; shares counted 2023-12-31 (100); split-adjusted close in July 2024 = 50 (quoted 50)
        p = px([('2024-07', 50., 50.)], [('2024-03-01', 2.)])
        self.assertEqual(B._factor(p, '2023-12-31'), 2.)
        self.assertEqual(B._factor(p, '2024-07-01'), 1.)
        # a reverse split AFTER formation must not inflate the value: 1-for-100 in 2026
        q = px([('2024-07', 500., 500.)], [('2026-01-05', .01)])
        self.assertEqual(B._factor(q, '2023-12-31') * 500. * 100, 500.)   # adjusted 500 x 100 shares x .01 = quoted 5 x 100

    def test_forward_total_return_and_gap(self):
        p = px([('2024-07', 10., 9.), ('2025-07', 12., 11.)])
        self.assertAlmostEqual(B.forward({'X': p}, 'X', '2024-07-01'), 11 / 9 - 1)
        self.assertIsNone(B.forward({'X': px([('2024-07', 10., 9.)])}, 'X', '2024-07-01'))

    def test_share_basis_check_flags_depositary_mismatch(self):
        merged = {2025: {1: {'shares_outstanding': {'value': 500e6, 'end': '2025-12-31'}},
                         2: {'shares_outstanding': {'value': 100e6, 'end': '2025-12-31'}}}}
        prices = {'ADR': px([('2026-09', 10., 10.)]), 'OK': px([('2026-09', 10., 10.)])}
        bad = B.share_basis_check(merged, {1: 'ADR', 2: 'OK'}, prices, {'ADR': 1e9, 'OK': 1e9})
        self.assertEqual(set(bad), {'ADR'})                                # 500m ordinary shares x $10 vs $1bn listed


if __name__ == '__main__':
    unittest.main()
