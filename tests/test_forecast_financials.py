"""Residual-income path for banks and insurers, on a SYNTHETIC bank (invented numbers, not a real company)."""
import unittest
from datetime import date, timedelta

from fundamental_engine import facts as F, pipeline


def bank(roe=(.13,) * 10, aoci=0., price=60., blocked_debt=False, payout=.5):
    """Ten fiscal years: common equity grows by retained profit; ROE per year as given."""
    fb = F.new({'ticker': 'SYNB', 'name': 'Synthetic test bank (not real)', 'currency': 'USD', 'sector': 'Financials',
                'industry': 'Major Banks', 'fiscal_year_end': '12-31'}, '2026-10-20')
    F.add_source(fb, 'syn', 'synthetic', 'synthetic bank statements', 'synthetic', '2026-10-20T00:00:00Z', note='TEST DATA ONLY')
    book = 100e9
    for i, r in enumerate(roe):
        y = 2016 + i
        end, avail = f'{y}-12-31', f'{y + 1}-02-20'
        if i == 0:
            F.add_fact(fb, 'equity', 'I@2015-12-31', '2015-12-31', book + aoci, 'syn', 'as_reported', '2016-02-20')
            F.add_fact(fb, 'aoci', 'I@2015-12-31', '2015-12-31', aoci, 'syn', 'as_reported', '2016-02-20')
        ni = r * book
        new = book + ni * (1 - payout)
        for m, v in (('net_income', ni), ('net_income_common', ni), ('revenue', ni * 3), ('dividends_paid', ni * payout * .6),
                     ('buybacks', ni * payout * .4)):
            F.add_fact(fb, m, f'FY{y}', end, v, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'equity', 'I@' + end, end, new + aoci, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'aoci', 'I@' + end, end, aoci, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'total_assets', 'I@' + end, end, new * 11, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'total_liabilities', 'I@' + end, end, new * 10, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'shares_outstanding', 'I@' + end, end, 2e9, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'cash', 'I@' + end, end, new * .5, 'syn', 'as_reported', avail)
        if not blocked_debt:
            F.add_fact(fb, 'long_term_debt', 'I@' + end, end, new * .8, 'syn', 'as_reported', avail)
        book = new
    for q, (s_, e_) in enumerate((('2026-01-01', '2026-03-31'), ('2026-04-01', '2026-06-30'))):
        avail = (date.fromisoformat(e_) + timedelta(days=35)).isoformat()
        ni = roe[-1] * book / 4
        for m in ('net_income', 'net_income_common'):
            F.add_fact(fb, m, 'Q@' + e_, e_, ni, 'syn', 'as_reported', avail, period_start=s_)
        F.add_fact(fb, 'revenue', 'Q@' + e_, e_, ni * 3, 'syn', 'as_reported', avail, period_start=s_)
        book += ni * (1 - payout)
        F.add_fact(fb, 'equity', 'I@' + e_, e_, book + aoci, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'aoci', 'I@' + e_, e_, aoci, 'syn', 'as_reported', avail)
        F.add_fact(fb, 'shares_outstanding', 'I@' + e_, e_, 2e9, 'syn', 'as_reported', avail)
    F.add_source(fb, 'syn:px', 'synthetic', 'test price', 'synthetic', '2026-10-20T00:00:00Z')
    F.set_price(fb, price, '2026-10-20', 'syn:px')
    return fb


class ResidualIncomeTests(unittest.TestCase):
    def test_bank_is_valued_on_book_and_roe(self):
        cf = pipeline.run(bank())
        fc, s = cf['fc'], pipeline.summary(cf)
        self.assertEqual((cf['card']['business_model']['primary'], fc['status'], fc['method']), ('financials', 'built', 'residual_income'))
        self.assertEqual(cf['val']['case']['method'], 'residual_income')
        self.assertTrue(s['bear_value'] <= s['base_value'] <= s['bull_value'])
        self.assertIsNotNone(s['base_irr'])
        self.assertAlmostEqual(fc['anchors']['roe']['median'], .13, places=2)
        self.assertIn('regulatory_capital_not_checked', {w['code'] for w in fc['warnings']})
        # persistent ROE above the cost of equity in all ten years keeps 75% of the excess in the base
        self.assertIn('persistent', next(a for a in fc['assumptions'] if a['id'] == 'roe_history')['rationale'])

    def test_value_rises_with_roe_and_book_growth_is_bounded(self):
        lo = pipeline.summary(pipeline.run(bank(roe=(.08,) * 10)))['base_value']
        hi = pipeline.summary(pipeline.run(bank(roe=(.16,) * 10)))['base_value']
        self.assertLess(lo, hi)
        fc = pipeline.run(bank(roe=(.40,) * 10, payout=.1))['fc']     # a 40% ROE does not compound book at 36% a year
        pays = [p['payout_fraction'] for p in fc['paths']['base']]
        self.assertTrue(all(p >= 1 - .09 / .40 for p in pays[1:]))

    def test_terminal_growth_is_the_risk_free_rate(self):
        # perpetual growth at the risk-free rate (the implied ERP's convention), not min(risk-free, 3%), while retained profit
        # funds it (ROE x retention above it); a risk-free rate above 6% is flagged
        from fundamental_engine.forecast import RF_DEFAULT
        fc = pipeline.run(bank())['fc']
        terms = next(a for a in fc['assumptions'] if a['id'] == 'terminal')['values']
        self.assertEqual((terms['base']['growth'], terms['bull']['growth']), (RF_DEFAULT, RF_DEFAULT))
        self.assertLessEqual(terms['bear']['growth'], RF_DEFAULT)      # the bear's lower ROE funds less (ROE x retention)
        self.assertNotIn('terminal_growth_high', {w['code'] for w in fc['warnings']})
        rf = {'value': .07, 'as_of': '2026-10-19', 'title': 'high-rate test value', 'url': 'https://example.org/rf'}
        hi = pipeline.run(bank(), {'forecast': {'risk_free': rf}})['fc']
        self.assertIn('terminal_growth_high', {w['code'] for w in hi['warnings']})
        payout = next(a for a in hi['assumptions'] if a['id'] == 'payout')['values']['payout_fraction_history']
        for t in next(a for a in hi['assumptions'] if a['id'] == 'terminal')['values'].values():
            self.assertLessEqual(t['growth'], min(.07, t['roe'] * (1 - payout)) + 1e-6)

    def test_unrealized_losses_are_not_book(self):
        fc = pipeline.run(bank(aoci=-30e9))['fc']
        self.assertGreater(fc['anchors']['book_equity'], 150e9)             # equity less AOCI, not the depressed reported equity

    def test_fields_the_route_never_reads_do_not_block_it(self):
        cf = pipeline.run(bank(blocked_debt=True))                       # no cash-flow statement: dataqa blocks cfo_ttm
        moved = cf['qa']['not_used_by_route']
        self.assertEqual((moved['route'], moved['status_before']), ('residual_income', 'blocked'))
        self.assertIn('cfo_ttm', moved['fields'])
        self.assertNotIn('cfo_ttm', cf['qa']['blocking'])
        self.assertEqual(cf['fc']['status'], 'built')


if __name__ == '__main__':
    unittest.main()
