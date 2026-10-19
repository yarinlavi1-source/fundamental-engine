import json
import unittest
from pathlib import Path
from fundamental_engine.expectations import run_model, value_per_share, solve_lever, expectation_gap, growth_path

ROOT = Path(__file__).resolve().parents[1]
CS_PLAIN = {'as_of': '2026-01-01', 'currency': 'USD', 'cash': [], 'debt': [],
            'shares': [{'id': 'sh', 'kind': 'common', 'count': 100.0, 'source_id': 's'}]}


def drivers(**kw):
    d = dict(revenue0=1000.0, years=10, g1=0.0, g_terminal=0.0, high_years=5, m1=0.2, m_mature=0.2, years_to_mature=5,
             tax=0.0, wacc=0.10, sales_to_capital=2.0, terminal_roic=0.10)
    d.update(kw)
    return d


class Model(unittest.TestCase):
    def test_no_growth_perpetuity_identity(self):
        m = run_model(drivers())
        self.assertAlmostEqual(m['enterprise_value'], 1000 * 0.2 / 0.10, places=6)

    def test_growth_requires_reinvestment(self):
        cheap = run_model(drivers(g1=0.2, sales_to_capital=10.0))['enterprise_value']
        dear = run_model(drivers(g1=0.2, sales_to_capital=0.5))['enterprise_value']
        self.assertGreater(cheap, dear)          # same growth, more capital needed -> less value

    def test_growth_without_excess_return_adds_little(self):
        # ROIC on new capital == WACC: growth should neither create nor destroy much value
        base = run_model(drivers(m1=0.1, m_mature=0.1, sales_to_capital=1.0, terminal_roic=0.10))['enterprise_value']
        grow = run_model(drivers(g1=0.15, m1=0.1, m_mature=0.1, sales_to_capital=1.0, terminal_roic=0.10))['enterprise_value']
        self.assertLess(abs(grow / base - 1), 0.25)

    def test_growth_path_fades(self):
        p = growth_path(0.5, 0.03, 3, 10)
        self.assertEqual(p[:3], [0.5, 0.5, 0.5]); self.assertAlmostEqual(p[-1], 0.03)


class ReverseSolve(unittest.TestCase):
    def test_round_trip_growth(self):
        d = drivers(g1=0.25, m_mature=0.25, sales_to_capital=3.0, terminal_roic=0.2)
        price = value_per_share(d, CS_PLAIN)[0]
        r = solve_lever(drivers(g1=0.05, m_mature=0.25, sales_to_capital=3.0, terminal_roic=0.2), CS_PLAIN, price, 'growth_level')
        self.assertEqual(r['status'], 'solved')
        self.assertAlmostEqual(r['required'], 0.25, places=4)

    def test_round_trip_margin(self):
        d = drivers(g1=0.1, m_mature=0.31)
        price = value_per_share(d, CS_PLAIN)[0]
        r = solve_lever(drivers(g1=0.1, m_mature=0.1), CS_PLAIN, price, 'mature_margin')
        self.assertAlmostEqual(r['required'], 0.31, places=4)

    def test_unreachable_reported_not_invented(self):
        r = solve_lever(drivers(), CS_PLAIN, 1e9, 'mature_margin')
        self.assertEqual(r['status'], 'not_reachable')


class GapReport(unittest.TestCase):
    def test_components_and_base_rate(self):
        d = drivers(g1=0.10, m_mature=0.2)
        price = value_per_share(drivers(g1=0.30, m_mature=0.2, sales_to_capital=2.0), CS_PLAIN)[0]
        rep = expectation_gap({'price': price, 'capital_structure': CS_PLAIN, 'anchor': d,
                               'evidence': {'demonstrated_cagr_3y': {'value': 0.12, 'source_ids': ['10k']}},
                               'base_rate_fn': lambda g: 0.05 if g > 0.25 else 0.5})
        c = rep['components'][0]
        self.assertEqual(c['status'], 'evidence_below_requirement')
        self.assertEqual(rep['gap_class'], 'price_requires_rare_outcome')
        self.assertIn('10k', c['source_ids'])

    def test_nbis_bridge_runs_with_converts(self):
        nb = json.loads((ROOT / 'examples/real/nbis_capital_2026-08-24.json').read_text())
        d = drivers(revenue0=11e9, revenue_ttm=1.355e9, g1=0.45, g_terminal=0.03, high_years=3, m1=-0.05, m_mature=0.15,
                    years_to_mature=4, tax=0.15, wacc=0.10, sales_to_capital=0.6, invested_capital0=28e9, terminal_roic=None)
        rep = expectation_gap({'price': 173.39, 'capital_structure': nb['capital_structure'], 'anchor': d})
        self.assertGreater(rep['market']['fully_diluted_shares'], 330e6)
        self.assertTrue(any(l['status'] == 'solved' for l in rep['what_must_be_true']))


if __name__ == '__main__':
    unittest.main()
