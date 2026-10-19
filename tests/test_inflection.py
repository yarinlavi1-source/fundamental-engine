import unittest
from datetime import date, timedelta
from fundamental_engine.inflection import features, scorecard, stage


def series(values, start=date(2020, 3, 31), filed_lag=40):
    out, e = {}, start
    for v in values:
        out[e.isoformat()] = (float(v), (e + timedelta(days=filed_lag)).isoformat())
        m = e.month + 3; y = e.year + (m - 1) // 12; m = (m - 1) % 12 + 1
        e = date(y, m, 30 if m in (6, 9) else 31)
    return out


def rec(rev, oi, goodwill=None):
    r = {'rev': series(rev), 'oi': series(oi), 'gp': {}, 'ocf': {}, 'capex': {}, 'sbc': {}, 'shares': {}}
    r['goodwill'] = series(goodwill) if goodwill else {}
    return r


T = date(2023, 2, 20)     # after Q4 2022 is filed (12 quarters 2020Q1..2022Q4)


class Inflection(unittest.TestCase):
    def test_accelerating_and_more_profitable(self):
        rev = [100] * 4 + [105, 106, 108, 110] + [120, 128, 138, 150]
        oi = [5] * 4 + [5, 5, 6, 6] + [9, 12, 15, 19]
        f = features(rec(rev, oi), T)
        self.assertGreater(f['accel_2q'], 0.03)
        self.assertGreaterEqual(f['accel_streak'], 2)
        self.assertEqual(f['growth_character'], 'accelerating_and_more_profitable')
        names = {c['component']: c['direction'] for c in scorecard(f)}
        self.assertEqual(names['YoY acceleration (2q)'], 1)
        self.assertEqual(names['incremental operating margin'], 1)

    def test_single_quarter_spike_not_an_inflection(self):
        rev = [100] * 8 + [100, 100, 100, 160]
        oi = [10] * 12
        f = features(rec(rev, oi), T)
        sc = {c['component']: c['direction'] for c in scorecard(f)}
        self.assertEqual(sc['YoY acceleration (2q)'], 1 if f['accel_streak'] >= 2 else 0)
        self.assertLess(f['accel_streak'], 2)

    def test_acquired_growth_flagged(self):
        rev = [100] * 8 + [150] * 4
        oi = [10] * 12
        gw = [50] * 8 + [400] * 4
        f = features(rec(rev, oi, gw), T)
        self.assertTrue(f['acquisition_driven_risk'])
        self.assertEqual(f['growth_character'], 'acquisition_driven')

    def test_turning_profitable_stage(self):
        rev = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210]
        oi = [-30, -28, -25, -22, -18, -14, -10, -6, 2, 5, 8, 12]
        self.assertEqual(features(rec(rev, oi), T)['stage'], 'turning_profitable')

    def test_no_lookahead(self):
        rev = [100] * 12; oi = [10] * 12
        self.assertIsNone(features(rec(rev, oi), date(2021, 1, 10)))   # fewer than 8 quarters usable


if __name__ == '__main__':
    unittest.main()
