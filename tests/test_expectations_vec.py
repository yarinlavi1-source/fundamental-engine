import random
import unittest
try:                                    # numpy is an optional research dependency (scripts only); CI has none
    import numpy as np
    from fundamental_engine.expectations_vec import ev_vec, solve_growth
except ImportError:
    np = None
from fundamental_engine.expectations import run_model


@unittest.skipIf(np is None, 'numpy not installed (vectorised research twin only)')
class VecTwin(unittest.TestCase):
    def test_matches_scalar_model(self):
        rnd = random.Random(7)
        for _ in range(50):
            rev, g, m1, mm = rnd.uniform(50, 5000), rnd.uniform(-0.2, 0.8), rnd.uniform(-0.4, 0.3), rnd.uniform(0.08, 0.35)
            d = dict(revenue0=rev, years=10, g1=g, g_terminal=0.03, high_years=5, m1=m1, m_mature=mm, years_to_mature=5,
                     tax=0.21, wacc=0.09, sales_to_capital=1.5, terminal_roic=None)
            self.assertAlmostEqual(run_model(d)['enterprise_value'], float(ev_vec([rev], [g], [m1], [mm])[0]), delta=1e-6 * rev)

    def test_solve_recovers_growth(self):
        rev = np.array([100.0, 1000.0, 300.0]); g = np.array([0.05, 0.25, 0.6]); m1 = np.array([0.1, 0.0, -0.2]); mm = np.array([0.3, 0.25, 0.3])
        ev = ev_vec(rev, g, m1, mm)
        g_hat, ok, *_ = solve_growth(ev, rev, m1, mm)
        self.assertTrue(ok.all())
        np.testing.assert_allclose(g_hat, g, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
