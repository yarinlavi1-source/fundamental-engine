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

    def test_loss_years_before_breakeven_leave_the_median(self):
        # a SYNTHETIC scale-up bank: six loss years while building, then four profitable years (NU-shaped)
        roe = (-.20, -.30, -.10, -.08, -.05, -.02, .15, .25, .28, .30)
        fc = pipeline.run(bank(roe=roe, payout=0.))['fc']
        self.assertIn('pre_breakeven_roe_excluded', {w['code'] for w in fc['warnings']})
        self.assertGreater(fc['anchors']['roe']['median'], .15)          # the profitable years only (average-equity ROE)
        self.assertGreater(fc['anchors']['roe']['lower_quartile'], 0)
        # a later loss year keeps the full history (a credit cycle, not a build-out)
        fc2 = pipeline.run(bank(roe=roe[:-1] + (-.05,), payout=0.))['fc']
        self.assertNotIn('pre_breakeven_roe_excluded', {w['code'] for w in fc2['warnings']})

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

    def test_rate_above_the_contract_bound_builds_with_a_visible_flag(self):
        # ROE x retention 36% and a 12% risk-free: growth that followed the rate failed the contract ('growth: above 0.08')
        rf = {'value': .12, 'as_of': '2026-10-19', 'title': 'high-rate test value', 'url': 'https://example.org/rf'}
        cf = pipeline.run(bank(roe=(.40,) * 10, payout=.1), {'forecast': {'risk_free': rf}})
        fc = cf['fc']
        self.assertEqual(fc['status'], 'built', fc.get('reason'))
        from fundamental_engine.valuation import TERMINAL_GROWTH_MAX
        terms = next(a for a in fc['assumptions'] if a['id'] == 'terminal')
        self.assertEqual({t['growth'] for t in terms['values'].values()}, {TERMINAL_GROWTH_MAX})
        self.assertIn('ROE - 0.1pp', terms['rationale'])                 # the ledger states every bound the code applies
        w = {x['code']: x for x in fc['warnings']}
        self.assertEqual(w['terminal_growth_capped']['severity'], 'high')
        self.assertNotIn('terminal_growth_high', w)
        self.assertGreater(pipeline.summary(cf)['base_value'], 0)

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


def filed_bank(**kw):
    """The synthetic bank plus a filing DOCUMENT source (a 6-K read for the later balance sheet and capital ratios)."""
    fb = bank(**kw)
    F.add_source(fb, 'doc:6k', 'synthetic', 'filing', 'issuer_document', '2026-09-30T00:00:00Z', url='https://example.org/6k',
                 note='TEST DATA ONLY')
    return fb


SCEN_BD = {'roe_start': .28, 'roe_year5': .26, 'roe_terminal': .16, 'book_growth_start': .17, 'payout': 0.,
           'terminal_reason': 'synthetic: deposit franchise keeps an excess return', 'source_tier': 'own_history',
           'evidence_ids': ['syn'], 'reason': 'synthetic: trailing ROE and first-half book growth'}


class BankDriversTests(unittest.TestCase):
    """inputs.forecast.bank_drivers: the analyst's evidenced ROE / book growth / payout, later opening book, capital."""

    def run_bd(self, bd, **kw):
        return pipeline.run(filed_bank(**kw), {'forecast': {'bank_drivers': bd}})

    def test_absent_leaves_the_rules(self):
        fc = pipeline.run(filed_bank())['fc']
        codes = {w['code'] for w in fc['warnings']}
        self.assertIn('regulatory_capital_not_checked', codes)
        self.assertFalse(codes & {'bank_drivers_applied', 'bank_opening_applied', 'bank_payout_from_growth'})
        self.assertEqual({a['kind'] for a in fc['assumptions'] if a['id'].startswith('roe_')}, {'reported', 'model'})
        self.assertNotIn('floor_by_scenario', next(a for a in fc['assumptions'] if a['id'] == 'payout')['values'])
        self.assertTrue(all(p['risk_weighted_assets'] == 0. for sc in fc['spec']['scenarios'] for p in sc['periods']))

    def test_applied_moves_roe_growth_and_value(self):
        roe = (.13,) * 10
        plain = pipeline.run(filed_bank(roe=roe, payout=0.))
        cf = self.run_bd({'base': dict(SCEN_BD)}, roe=roe, payout=0.)
        fc = cf['fc']
        self.assertEqual(fc['status'], 'built', fc.get('reason'))
        self.assertIn('bank_drivers_applied', {w['code'] for w in fc['warnings']})
        base = fc['paths']['base']
        self.assertGreater(base[0]['roe'], .27)
        # book growth 17% (above the 8% cap, funded by retained profit) sets the payout: 1 - growth / ROE, not the 50% default
        self.assertLess(base[0]['payout_fraction'], .5)
        self.assertAlmostEqual(fc['spec']['scenarios'][1]['terminal']['roe'], .16)
        row = next(a for a in fc['assumptions'] if a['id'] == 'roe_base')
        self.assertEqual(row['kind'], 'analyst')
        self.assertGreater(pipeline.summary(cf)['base_value'], pipeline.summary(plain)['base_value'])
        self.assertEqual(fc['paths']['bear'], plain['fc']['paths']['bear'])      # an unset scenario keeps the rules

    def test_no_payout_and_no_history_follows_growth_and_says_so(self):
        fb = filed_bank()
        fb['facts'] = [f for f in fb['facts'] if f['metric'] not in ('dividends_paid', 'buybacks')]
        bd = {s: {k: v for k, v in SCEN_BD.items() if k != 'payout'} for s in ('bear', 'base', 'bull')}
        fc = pipeline.run(fb, {'forecast': {'bank_drivers': bd}})['fc']
        codes = {w['code'] for w in fc['warnings']}
        self.assertIn('bank_payout_from_growth', codes)
        self.assertNotIn('payout_unknown', codes)
        self.assertEqual(next(a for a in fc['assumptions'] if a['id'] == 'payout')['values']['floor_by_scenario']['base'], 0.)

    def test_later_opening_book_and_capital(self):
        op = {'book_equity': 160e9, 'shares': 2.1e9, 'as_of': '2026-09-30', 'reason': 'synthetic Q3 balance sheet',
              'evidence_ids': ['doc:6k']}
        rc = {'risk_weighted_assets': 1000e9, 'required_capital_ratio': .105, 'as_of': '2026-09-30',
              'reason': 'synthetic CET1 requirement with buffers', 'evidence_ids': ['doc:6k']}
        fc = self.run_bd({'opening': op, 'regulatory_capital': rc})['fc']
        self.assertEqual((fc['anchors']['book_equity'], fc['anchors']['statement_as_of'], fc['anchors']['shares']),
                         (160e9, '2026-09-30', 2.1e9))
        codes = {w['code'] for w in fc['warnings']}
        self.assertIn('bank_opening_applied', codes)
        self.assertNotIn('regulatory_capital_not_checked', codes)
        p = fc['spec']['scenarios'][1]['periods']
        self.assertEqual(p[0]['required_capital_ratio'], .105)
        self.assertGreater(p[-1]['risk_weighted_assets'], p[0]['risk_weighted_assets'] > 1000e9 - 1)
        # a requirement the opening book does not meet is flagged
        hi = self.run_bd({'regulatory_capital': dict(rc, required_capital_ratio=.25)})['fc']
        self.assertIn('capital_below_requirement', {w['code'] for w in hi['warnings']})
        # an opening not after the FactBook's last balance sheet is not applied
        old = self.run_bd({'opening': dict(op, as_of='2026-03-31')})['fc']
        self.assertIn('bank_opening_not_applied', {w['code'] for w in old['warnings']})
        self.assertEqual(old['anchors']['statement_as_of'], '2026-06-30')

    def test_unevidenced_or_out_of_band_is_refused(self):
        op = {'book_equity': 160e9, 'as_of': '2026-09-30', 'reason': 'r', 'evidence_ids': ['doc:6k']}
        bad = {
            'unknown evidence': {'base': dict(SCEN_BD, evidence_ids=['nowhere'])},
            'no reason': {'base': dict(SCEN_BD, reason=' ')},
            'roe out of range': {'base': dict(SCEN_BD, roe_start=.9)},
            'tier': {'base': dict(SCEN_BD, source_tier='analyst')},
            'growth not funded': {'base': dict(SCEN_BD, payout=.6)},
            'terminal above Ke without a reason': {'base': {k: v for k, v in SCEN_BD.items() if k != 'terminal_reason'}},
            'opening without a filing document': {'opening': dict(op, evidence_ids=['syn'])},
            'opening after as_of': {'opening': dict(op, as_of='2026-12-31')},
            'opening out of band': {'opening': dict(op, book_equity=1e9)},
            'capital ratio': {'regulatory_capital': {'risk_weighted_assets': 1e12, 'required_capital_ratio': .5, 'as_of': '2026-09-30',
                                                     'reason': 'r', 'evidence_ids': ['doc:6k']}},
        }
        for name, bd in bad.items():
            with self.subTest(name):
                cf = self.run_bd(bd)
                self.assertIsNone(cf['fc'])
                self.assertTrue(any(e['stage'] == 'forecast' and e['error'].startswith('ValueError: bank_drivers')
                                    for e in cf['errors']), cf['errors'])

    def test_company_path_sums_the_fifth_anniversary_split(self):
        # the residual-income case has no generated_from: company_path reads the forecast calendar's interim boundary, so the
        # stub after the fifth anniversary is not read as a whole next fiscal year
        cp = {'metric': 'eps', 'multiple': 'forward_pe',
              'scenarios': {'base': {'multiple_from': 'economics', 'roic': .2, 'justification': 'justified_high',
                                     'source_tier': 'own_history', 'reason_he': 'בדיקה', 'evidence_ids': ['syn']}}}
        cf = pipeline.run(filed_bank(), {'company_path': cp})
        fy = cf['company_path']['fiscal_years']['base']
        self.assertFalse(any(f['partial'] for f in fy[1:]))
        self.assertTrue(all(f['fiscal_year_end'].endswith('12-31') for f in fy))


if __name__ == '__main__':
    unittest.main()
