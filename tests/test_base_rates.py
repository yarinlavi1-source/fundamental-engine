import unittest

from fundamental_engine import base_rates, calibration


class BaseRateTests(unittest.TestCase):
    def test_rows_are_distributions(self):
        for label, lo, hi, table in base_rates.CLASSES:
            for n, (freqs, med) in table.items():
                self.assertEqual(len(freqs), len(base_rates.EDGES) + 1, (label, n))
                self.assertAlmostEqual(sum(freqs), 100., delta=.6, msg=(label, n))   # the book's rounding

    def test_share_at_least_reads_the_book(self):
        mega10 = dict((c[0], c[3]) for c in base_rates.CLASSES)['>$50,000m'][10][0]
        # Base Rate Book exhibit 4, >$50bn, 10 years: 17.2% grew 5-10%, 3.0% 10-15%, 0.9% 15-20% (real)
        self.assertAlmostEqual(base_rates.share_at_least(mega10, .05), .211, places=3)
        self.assertAlmostEqual(base_rates.share_at_least(mega10, .075), .086 + .039, places=3)   # half of the 5-10% bin
        self.assertEqual(base_rates.share_at_least(mega10, -1.), 1.)
        self.assertEqual(base_rates.share_at_least(mega10, .6), 0.)

    def test_reference_class_restates_sales_in_2015_dollars(self):
        f = base_rates.CPI_2015 / base_rates.CPI_NOW
        self.assertEqual(base_rates.reference_class(70e9)[0], '>$25,000m')       # $70bn today ~ $49bn of 2015
        self.assertEqual(base_rates.reference_class(51e9 / f)[0], '>$50,000m')
        self.assertEqual(base_rates.reference_class(200e6 / f)[0], '$0-325m')

    def test_outside_view_is_real_and_monotone(self):
        v = base_rates.outside_view(100e9, {5: .10, 10: .08})
        h5, h10 = v['horizons'][5], v['horizons'][10]
        self.assertAlmostEqual(h10['real_cagr'], 1.08 / (1 + base_rates.INFLATION) - 1)
        lo = base_rates.outside_view(100e9, {10: .03})['horizons'][10]['share_at_least']
        self.assertGreater(lo, h10['share_at_least'])
        self.assertAlmostEqual(h5['class_median_real'], .015)
        self.assertIsNone(base_rates.outside_view(0, {5: .1}))
        self.assertIn('צמיחה ריאלית', base_rates.describe_he(v, 10))

    def test_cagr(self):
        self.assertAlmostEqual(base_rates.cagr([.1, .1], 2), .1)
        self.assertIsNone(base_rates.cagr([.1], 2))


class MeanRiskAdjustmentTests(unittest.TestCase):
    def test_calibration_reports_the_value_weighted_net_adjustment(self):
        sums = [{'symbol': 'A', 'base_irr': .08, 'cost_of_equity': .09, 'risk_adjustment': .01},
                {'symbol': 'B', 'base_irr': .07, 'cost_of_equity': .085, 'risk_adjustment': -.01}]
        out = calibration.market_implied(sums, {'A': 3., 'B': 1.}, '2026-10-20')
        self.assertAlmostEqual(out['mean_risk_adjustment'], .005)


if __name__ == '__main__':
    unittest.main()
