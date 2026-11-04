import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import lean_model  # noqa: E402


class LeanModelTest(unittest.TestCase):
    def setUp(self):
        self.p = json.load(open(os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_anet.json')))

    def test_matches_the_anet_run(self):
        r = lean_model.run(self.p)
        base = r['scenarios']['base']
        self.assertAlmostEqual(base['value_today'], 165.6, delta=0.3)
        self.assertAlmostEqual(base['year_end_values'][2031], 271.0, delta=0.5)
        self.assertAlmostEqual(base['annual_return_pct'], 7.9, delta=0.1)
        self.assertAlmostEqual(r['scenarios']['bear']['year_end_values'][2031], 120.1, delta=0.5)
        self.assertAlmostEqual(r['scenarios']['bull']['year_end_values'][2031], 375.4, delta=0.5)

    def test_order_and_reverse_dcf(self):
        r = lean_model.run(self.p)
        s = r['scenarios']
        self.assertLess(s['bear']['value_today'], s['base']['value_today'])
        self.assertLess(s['base']['value_today'], s['bull']['value_today'])
        self.assertEqual(r['market_implied_growth_pct'][0], 38.5)  # guided year held
        self.assertGreater(r['market_implied_growth_pct'][1], 20.0)  # price needs more than the base after it

    def test_net_debt_uses_debt_rate(self):
        p = dict(self.p, net_cash_m=-5000.0)
        # debt is valued at face today; its interest lowers the cash carried to later year-ends
        lo = lean_model.run_scenario(p, self.p['scenarios']['base'])['year_end_values'][2028]
        hi = lean_model.run_scenario(dict(p, debt_rate=0.0), self.p['scenarios']['base'])['year_end_values'][2028]
        self.assertLess(lo, hi)


if __name__ == '__main__':
    unittest.main()
