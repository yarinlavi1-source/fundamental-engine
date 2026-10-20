"""engine_backtest: evaluation arithmetic on SYNTHETIC rows and prices (invented numbers, not market data)."""
import unittest

from fundamental_engine import engine_backtest as E


def px(start, end, months=('2020-06', '2021-06'), splits=()):
    return {'months': [(months[0], start, start), (months[1], end, end)], 'splits': list(splits)}


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        # ten companies: engine IRR rises with the name index; forward returns rise with it too
        self.rows = [{'symbol': f'S{i}', 'as_of': '2020-06-30', 'base_irr': .02 * i, 'bear_irr': .01 * i - .03,
                      'market_cap': 1e9 * (i + 1), 'qa_status': 'blocked' if i == 9 else 'ok',
                      'earnings_yield': .1 - .01 * i, 'fcf_yield': .05} for i in range(10)]
        self.prices = {f'S{i}': px(100., 100. * (1 + .03 * i)) for i in range(10)}
        self.prices['SPY'] = px(300., 330.)

    def test_quintiles_controls_and_ic(self):
        res = E.evaluate({'2020-07-01': self.rows}, self.prices, cost=0.)
        p = res['periods'][0]
        self.assertEqual(p['books']['engine_top_quintile']['members'], ['S8', 'S9'])
        self.assertEqual(p['books']['engine_top_quintile_unblocked']['members'], ['S8'])      # 9 unblocked names: a quintile of 1
        self.assertEqual(p['books']['engine_bottom_quintile']['members'], ['S0', 'S1'])
        self.assertEqual(p['books']['earnings_yield_top_quintile']['members'], ['S0', 'S1'])
        self.assertAlmostEqual(p['books']['engine_top_quintile']['ret'], (.24 + .27) / 2)
        self.assertAlmostEqual(p['benchmark'], .10)
        self.assertAlmostEqual(p['ic'], 1.0)
        caps = [1e9 * (i + 1) for i in range(10)]
        mkt = sum(min(.02 * i, .40) * c for i, c in enumerate(caps)) / sum(caps)
        self.assertAlmostEqual(p['engine_market_return'], mkt)
        irr = {r['symbol']: r['base_irr'] for r in self.rows}
        self.assertEqual(sorted(p['books']['engine_positive_edge']['members']), sorted(s for s, x in irr.items() if x > mkt))

    def test_traded_price_restores_later_splits(self):
        p = px(50., 60., splits=[('2022-01-10', 4.)])                  # split-adjusted 50 was 200 as traded in June 2020
        self.assertAlmostEqual(E.traded_price(p, '2020-06-30'), 200.)

    def test_rate_month_is_the_month_before_formation(self):
        as_of, rf = E.formation_inputs('2020-07-01', {'2020-06': .0073, '2020-07': .0062})
        self.assertEqual((as_of, rf), ('2020-06-30', .0073))


if __name__ == '__main__':
    unittest.main()
