"""dataqa.validate: Stage-1 critical-field validation.

Real fixture: Adobe FY2021-FY2025 (tests/fixtures/adbe_factbook_2026-10-19.json). Every other company below is
SYNTHETIC test data built in this file (company 'SYN', source 'sec:cf' etc.), labelled as such, shaped to
reproduce the project's real data errors: NBIS stale count / missing converts / pre-funded warrants, ASTS net
cash double count, NOW split read as dilution, APH debt only at fair value, MSFT/META definitional net cash.
"""
import json
import os
import unittest
from copy import deepcopy
from datetime import date, timedelta

from fundamental_engine import dataqa
from fundamental_engine import facts as F

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'adbe_factbook_2026-10-19.json')
M = 1e6


def adbe():
    with open(FIX, encoding='utf-8') as f:
        return json.load(f)


def _plus(d, n):
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def checks(qa, code, status=None):
    return [c for c in qa['checks'] if c['code'] == code and (status is None or c['status'] == status)]


# ---------------------------------------------------------------- synthetic company (test data, not a real issuer)

YEARS = {  # fiscal year end -> income / cash-flow values in USD millions (consistent: GP and cash-flow identities hold)
    '2023-12-31': dict(revenue=850, cost_of_revenue=350, gross_profit=500, operating_income=110, net_income=90, cfo=170,
                       cfi=-40, cff=-90, change_in_cash=40, capex=35, sbc=25, buybacks=50, interest_expense=25),
    '2024-12-31': dict(revenue=1000, cost_of_revenue=400, gross_profit=600, operating_income=150, net_income=120, cfo=200,
                       cfi=-50, cff=-100, change_in_cash=50, capex=40, sbc=30, buybacks=60, interest_expense=25),
    '2025-12-31': dict(revenue=1200, cost_of_revenue=480, gross_profit=720, operating_income=200, net_income=160, cfo=260,
                       cfi=-60, cff=-120, change_in_cash=80, capex=45, sbc=35, buybacks=70, interest_expense=25),
}
QFRAC = {'2025-03-31': .233, '2025-06-30': .242, '2025-09-30': .258, '2025-12-31': .267, '2026-03-31': .275, '2026-06-30': .283}
EPS = {'2023-12-31': .90, '2024-12-31': 1.20, '2025-12-31': 1.63}
WAVG = {'2023-12-31': 100.5, '2024-12-31': 99.5, '2025-12-31': 98.5}
CASH = {'2023-12-31': 300.0, '2024-12-31': 350.0, '2025-12-31': 430.0, '2026-03-31': 452.0, '2026-06-30': 474.64}
SHARES = {'2023-12-31': 100.0, '2024-12-31': 99.0, '2025-12-31': 98.0, '2026-03-31': 97.8, '2026-06-30': 97.5}


def syn(as_of='2026-10-19', quarters=True, debt=True):
    """Clean synthetic company: annual + quarterly statements, balance sheets, cover count, price history."""
    fb = F.new({'ticker': 'SYN', 'name': 'Synthetic Co (test data)', 'currency': 'USD'}, as_of)
    F.add_source(fb, 'sec:cf', 'sec', 'companyfacts', 'filing_derived', '2026-10-19T00:00:00Z', note='synthetic test data')
    F.add_source(fb, 'ib:px', 'ib', 'price', 'market_feed', '2026-10-19T00:00:00Z')
    for end, vals in YEARS.items():
        start, avail = _plus(end, -364), _plus(end, 45)
        for m, v in vals.items():
            F.add_fact(fb, m, f'FY{end[:4]}', end, v * M, 'sec:cf', 'as_reported', avail, period_start=start)
        F.add_fact(fb, 'eps_diluted', f'FY{end[:4]}', end, EPS[end], 'sec:cf', 'as_reported', avail, unit='USD/share')
        F.add_fact(fb, 'shares_diluted_wavg', f'FY{end[:4]}', end, WAVG[end] * M, 'sec:cf', 'as_reported', avail)
    if quarters:
        for end, frac in QFRAC.items():
            base = YEARS['2025-12-31']
            for m, v in base.items():
                F.add_fact(fb, m, f'Q@{end}', end, v * frac * M, 'sec:cf', 'as_reported', _plus(end, 35))
    for end in CASH:
        avail = _plus(end, 45 if end.endswith('12-31') else 35)
        if not quarters and not end.endswith('12-31'):
            continue
        for m, v in dict(cash=CASH[end], short_term_investments=100, long_term_investments=50, current_assets=900,
                         total_assets=2000, total_liabilities=1200, equity=800, liabilities_and_equity=2000,
                         operating_lease_current=40, operating_lease_noncurrent=60, shares_outstanding=SHARES[end]).items():
            F.add_fact(fb, m, 'I@' + end, end, v * M, 'sec:cf', 'as_reported', avail)
        if debt:
            F.add_fact(fb, 'long_term_debt', 'I@' + end, end, 500 * M, 'sec:cf', 'as_reported', avail, concept='longtermdebtnoncurrent')
    if quarters:
        F.add_fact(fb, 'shares_outstanding_cover', 'I@2026-07-25', '2026-07-25', 97.4 * M, 'sec:cf', 'as_reported', '2026-08-06')
    hist, d = [], date(2024, 1, 15)
    while d <= date.fromisoformat(as_of):
        lo, hi = {2024: (55, 70), 2025: (60, 85)}.get(d.year, (75, 90))
        hist.append({'date': d.isoformat(), 'close': lo + (hi - lo) * ((d.month % 6) / 5)})
        d = (d.replace(day=1) + timedelta(days=32)).replace(day=15)
    fb['market']['price_history'] = hist
    F.set_price(fb, 80.0, as_of, 'ib:px')
    return fb


def clean_cs(as_of='2026-06-30', debt_principal=500 * M, extra_debt=()):
    return {'as_of': as_of, 'currency': 'USD',
            'cash': [{'id': 'cash', 'kind': 'unrestricted', 'amount': CASH['2026-06-30'] * M, 'source_id': 'sec:cf', 'as_of': '2026-06-30'},
                     {'id': 'sti', 'kind': 'short_term_investments', 'amount': 100 * M, 'source_id': 'sec:cf', 'as_of': '2026-06-30'},
                     {'id': 'lti', 'kind': 'long_term_investments', 'amount': 50 * M, 'source_id': 'sec:cf', 'as_of': '2026-06-30'}],
            'shares': [{'id': 'common', 'kind': 'common', 'count': 97.5 * M, 'source_id': 'sec:cf', 'as_of': '2026-06-30'}],
            'debt': [{'id': 'notes_2030', 'kind': 'bond', 'principal': debt_principal, 'issue_date': '2020-03-01',
                      'maturity': '2030-03-01', 'source_id': 'sec:cf'}] + list(extra_debt)}


def cs_cited(cs, sid):
    for g in ('cash', 'shares', 'debt', 'quasi_debt'):
        for x in cs.get(g, []):
            x['source_id'] = sid
    return cs


# ---------------------------------------------------------------- Adobe (real fixture)

class AdobeFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fb = adbe()
        cls.before = F.fingerprint(cls.fb)
        cls.qa = dataqa.validate(cls.fb)

    def test_contract_shape_and_serialisable(self):
        qa = self.qa
        self.assertEqual(set(qa['fields']), set(dataqa.CRITICAL))
        for name, f in qa['fields'].items():
            for k in ('field', 'status', 'value', 'unit', 'as_of', 'sources', 'facts', 'checks', 'note', 'blocking'):
                self.assertIn(k, f, name)
            self.assertIn(f['status'], dataqa.RANK)
        for c in qa['checks']:
            self.assertIn(c['status'], ('pass', 'fail', 'skip'))
            self.assertIn(c['severity'], ('critical', 'major', 'minor'))
        json.dumps(qa, allow_nan=False)
        self.assertEqual(F.fingerprint(self.fb), self.before, 'validate must not mutate the FactBook')

    def test_debt_scope_resolved_by_rollforward(self):
        d = self.qa['resolved']['debt']
        self.assertEqual(d['value'], 6210000000.0)
        self.assertEqual(d['method'], 'tagged_plus_current')
        self.assertTrue(d['decisive'])
        self.assertEqual(d['decisive_basis'], 'latest_rollforward')
        rf = {c['period']: c['detail'] for c in checks(self.qa, 'debt_rollforward')}
        last = rf['2024-11-29..2025-11-28']['hypotheses']
        self.assertFalse(last['tagged_total']['pass'])            # 4.129 + 2.0 - 1.5 = 4.626 vs 6.21
        self.assertTrue(last['tagged_plus_current']['pass'])      # 5.629 + 2.0 - 1.5 = 6.126 vs 6.21 (hedge/discount residual)
        self.assertAlmostEqual(last['tagged_plus_current']['prior'], 5629000000.0)
        self.assertEqual(self.qa['fields']['debt']['source_status'], 'verified')
        self.assertIsNone(self.qa['fields']['debt']['facts'][0].get('concept'))
        self.assertEqual(self.qa['resolved']['lease_liabilities']['value'], 438000000.0)
        self.assertFalse(self.qa['resolved']['lease_liabilities']['included_in_debt'])

    def test_shares_latest_point_in_time_count(self):
        s = self.qa['resolved']['shares_outstanding']
        self.assertEqual((s['value'], s['as_of'], s['basis']), (389200000.0, '2026-08-28', 'vendor_float'))
        self.assertEqual(self.qa['fields']['shares_outstanding']['status'], 'single_source')   # vendor-normalized only
        self.assertNotIn(s['value'], [f['value'] for f in self.fb['facts'] if f['metric'].endswith('_wavg')])

    def test_ttm_from_latest_fiscal_year_is_stale(self):
        r = self.qa['fields']['revenue_ttm']
        self.assertEqual((r['method'], r['period_end'], r['value']), ('latest_fiscal_year', '2025-11-28', 23769000000.0))
        self.assertEqual(r['status'], 'stale')
        self.assertEqual(r['source_status'], 'verified')          # GP identity confirms the filed value
        self.assertIn('quarterly data missing', r['note'])
        self.assertEqual(self.qa['fields']['cfo_ttm']['source_status'], 'verified')   # cash-flow tie
        self.assertEqual(self.qa['status'], 'limited')
        self.assertEqual(self.qa['blocking'], [])
        self.assertIn('revenue_ttm', self.qa['limited'])

    def test_identities_pass(self):
        for code in ('balance_sheet_identity', 'gross_profit_identity', 'cash_flow_tie', 'cash_rollforward', 'net_income_is_vs_cf'):
            got = checks(self.qa, code)
            self.assertTrue(got, code)
            self.assertFalse([c for c in got if c['status'] == 'fail'], code)

    def test_resolved_balances_and_definitions(self):
        r = self.qa['resolved']
        self.assertEqual(r['cash']['value'], 5431000000.0)
        self.assertEqual(r['short_term_investments']['value'], 1164000000.0)
        self.assertEqual(r['long_term_investments']['value'], 0.0)
        self.assertAlmostEqual(r['net_cash']['value'], 385000000.0)
        self.assertAlmostEqual(r['deferred_revenue']['value'], 7030000000.0)
        self.assertIn('leases', r['net_cash']['definition'])
        self.assertEqual(self.qa['fields']['cash_and_liquid_investments']['value'], 6595000000.0)
        self.assertEqual(self.qa['fields']['convertibles']['status'], 'missing')
        self.assertFalse(self.qa['fields']['convertibles']['blocking'])     # no evidence of an instrument


# ---------------------------------------------------------------- synthetic companies

class SyntheticClean(unittest.TestCase):
    def test_quarters_ttm_fresh_and_verified(self):
        qa = dataqa.validate(syn())
        rev = qa['fields']['revenue_ttm']
        self.assertEqual(rev['method'], 'quarters')
        self.assertAlmostEqual(rev['value'], 1200 * M * (.258 + .267 + .275 + .283))
        self.assertEqual(rev['status'], 'verified')
        self.assertEqual(qa['fields']['cfo_ttm']['status'], 'verified')
        self.assertEqual(qa['fields']['operating_income_ttm']['status'], 'reported')
        self.assertTrue(checks(qa, 'quarters_sum_to_annual', 'pass'))
        self.assertEqual(qa['resolved']['shares_outstanding']['basis'], 'cover')
        self.assertEqual(qa['resolved']['shares_outstanding']['value'], 97.4 * M)
        self.assertEqual(qa['fields']['debt']['status'], 'verified')
        self.assertEqual(qa['resolved']['debt']['method'], 'noncurrent+current')
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'reported')
        self.assertEqual(sorted(qa['limited']), ['convertibles', 'post_period_events'])
        self.assertEqual(qa['status'], 'limited')
        self.assertTrue(checks(qa, 'share_continuity', 'pass'))      # buybacks vs share reduction vs price range

    def test_capital_structure_crosscheck_and_post_period_review_give_ok(self):
        fb = syn()
        F.add_qualitative(fb, 'pp1', 'post_period_event', 'No financing or M&A after 30 Jun 2026 (8-K review)', 'reported',
                          ['sec:cf'], '2026-10-01')
        F.add_source(fb, 'doc:10q', 'issuer', 'filing', 'issuer_document', '2026-10-19')   # the structure read from the 10-Q itself
        qa = dataqa.validate(fb, cs_cited(clean_cs(), 'doc:10q'))
        self.assertEqual(qa['status'], 'ok', qa['limited'])
        self.assertEqual(len(checks(qa, 'capital_structure_crosscheck', 'pass')), 2)
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'verified')
        self.assertEqual(qa['fields']['convertibles']['status'], 'reported')
        self.assertEqual(qa['fields']['convertibles']['value'], 0.0)

    def test_capital_structure_missing_instrument_conflicts(self):
        qa = dataqa.validate(syn(), clean_cs(debt_principal=300 * M))      # statements show 500
        self.assertEqual(qa['fields']['debt']['status'], 'conflict')
        self.assertIn('debt', qa['blocking'])
        self.assertTrue(checks(qa, 'capital_structure_crosscheck', 'fail'))

    def test_newer_capital_structure_bridge_is_used(self):
        cs = clean_cs(as_of='2026-09-15', extra_debt=[{'id': 'cv_2031', 'kind': 'convertible', 'principal': 200 * M,
                                                       'conversion_price': 120.0, 'issue_date': '2026-09-10',
                                                       'maturity': '2031-09-10', 'source_id': 'sec:cf'}])
        cs['cash'].append({'id': 'cv_proceeds', 'kind': 'unrestricted', 'amount': 195 * M, 'source_id': 'sec:cf', 'as_of': '2026-09-10'})
        qa = dataqa.validate(syn(), cs)
        d = qa['resolved']['debt']
        self.assertEqual((d['method'], d['value'], d['as_of']), ('capital_structure', 700 * M, '2026-09-15'))
        self.assertAlmostEqual(qa['resolved']['cash']['value'], (CASH['2026-06-30'] + 195) * M)
        self.assertEqual(qa['fields']['convertibles']['status'], 'reported')
        self.assertAlmostEqual(qa['fields']['convertibles']['conversion_shares'], 200 * M / 120.0)
        pp = qa['fields']['post_period_events']
        self.assertEqual(pp['status'], 'reported')
        self.assertEqual({x['id'] for x in pp['capital_structure_lines']}, {'cv_2031', 'cv_proceeds'})

    def test_lookahead_fact_ignored(self):
        fb = syn()
        F.add_source(fb, 'fmp:float', 'fmp', 'shares-float', 'vendor_normalized', '2026-10-25T00:00:00Z')
        F.add_fact(fb, 'shares_outstanding_vendor', 'I@2026-10-01', '2026-10-01', 50 * M, 'fmp:float', 'normalized', '2026-10-25')
        F.add_fact(fb, 'cash', 'I@2026-09-30', '2026-09-30', 1.0, 'sec:cf', 'as_reported', '2026-11-05')
        qa = dataqa.validate(fb)
        self.assertEqual(qa['resolved']['shares_outstanding']['value'], 97.4 * M)
        self.assertEqual(qa['resolved']['cash']['as_of'], '2026-06-30')
        self.assertEqual(checks(qa, 'lookahead_excluded')[0]['detail']['excluded'], 2)

    def test_stale_share_count_limits(self):
        fb = syn(as_of='2027-01-20')          # cover count 2026-07-25 is 179 days old (NBIS-type stale count)
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['shares_outstanding']['status'], 'stale')
        self.assertIn('shares_outstanding', qa['limited'])
        self.assertNotIn('shares_outstanding', qa['blocking'])

    def test_loss_making_grower_with_dilution(self):
        fb = F.new({'ticker': 'GRO', 'name': 'Grower (synthetic test data)', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 's', 'sec', 'companyfacts', 'filing_derived', '2026-10-19')
        for i, end in enumerate(('2024-12-31', '2025-12-31')):
            av = _plus(end, 60)
            for m, v in dict(revenue=100 + 80 * i, operating_income=-60 - 10 * i, cfo=-40 + 5 * i, capex=30, sbc=25,
                             cfi=-30, cff=200, change_in_cash=130 + 5 * i).items():
                F.add_fact(fb, m, f'FY{end[:4]}', end, v * M, 's', 'as_reported', av)
            for m, v in dict(cash=300 + 135 * i, current_assets=400 + 150 * i, total_assets=600 + 200 * i,
                             liabilities_and_equity=600 + 200 * i, shares_outstanding=50 + 12 * i).items():
                F.add_fact(fb, m, 'I@' + end, end, v * M, 's', 'as_reported', av)
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['operating_income_ttm']['value'], -70 * M)
        self.assertEqual(qa['fields']['cfo_ttm']['status'], 'stale')   # FY TTM 292 days old
        self.assertEqual(qa['resolved']['debt']['method'], 'none_tagged')
        cont = checks(qa, 'share_continuity')[0]
        self.assertAlmostEqual(cont['detail']['change_pct'], .24)        # recorded as issuance, not a split
        self.assertFalse(checks(qa, 'possible_split'))
        self.assertEqual(qa['blocking'], [])
        json.dumps(qa, allow_nan=False)


class Conflicts(unittest.TestCase):
    def test_filing_sources_disagree_on_cash_blocks(self):
        fb = syn(quarters=False)
        F.add_source(fb, 'fmp:bs', 'fmp', 'as-reported-balance', 'filing_derived', '2026-10-19')
        F.add_fact(fb, 'cash', 'I@2025-12-31', '2025-12-31', 520 * M, 'fmp:bs', 'as_reported', '2026-02-14')
        qa = dataqa.validate(fb, options={'max_age': {'balance_sheet': 400, 'ttm': 400}})
        # the SEC value still ties to the cash-flow statement, so the FMP figure is a recorded outlier (STI keeps it 'reported')
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'reported')
        self.assertIn('outlier', qa['fields']['cash_and_liquid_investments']['note'])
        self.assertTrue(checks(qa, 'cross_source', 'fail'))
        fb2 = syn(quarters=False)
        F.add_source(fb2, 'fmp:bs', 'fmp', 'as-reported-balance', 'filing_derived', '2026-10-19')
        F.add_fact(fb2, 'cash', 'I@2025-12-31', '2025-12-31', 520 * M, 'fmp:bs', 'as_reported', '2026-02-14')
        fb2['facts'] = [f for f in fb2['facts'] if f['metric'] != 'change_in_cash']     # no corroborating identity
        qa2 = dataqa.validate(fb2, options={'max_age': {'balance_sheet': 400, 'ttm': 400}})
        self.assertEqual(qa2['fields']['cash_and_liquid_investments']['status'], 'conflict')
        self.assertEqual(qa2['status'], 'blocked')
        self.assertIn('cash_and_liquid_investments', qa2['blocking'])

    def test_failed_gross_profit_identity_blocks_revenue(self):
        fb = syn()
        for f in fb['facts']:
            if f['metric'] == 'revenue' and f['period'] == 'Q@2026-06-30':
                f['value'] *= 1.10                                  # revenue no longer equals COGS + gross profit
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['revenue_ttm']['status'], 'conflict')
        self.assertIn('revenue_ttm', qa['blocking'])

    def test_vendor_outlier_corroborated_by_identity(self):
        fb = syn()
        F.add_source(fb, 'av:income', 'alpha_vantage', 'income', 'vendor_normalized', '2026-10-19')
        F.add_fact(fb, 'revenue', 'Q@2026-06-30', '2026-06-30', 300 * M, 'av:income', 'normalized', '2026-08-14', estimated=True)
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['revenue_ttm']['status'], 'verified')
        self.assertIn('outlier', qa['fields']['revenue_ttm']['note'])
        self.assertTrue(checks(qa, 'cross_source', 'fail'))

    def test_independent_vendor_agreement_verifies(self):
        fb = syn()
        F.add_source(fb, 'av:cash', 'alpha_vantage', 'cashflow', 'vendor_normalized', '2026-10-19')
        F.add_fact(fb, 'capex', 'Q@2026-06-30', '2026-06-30', -45 * .283 * M, 'av:cash', 'normalized', '2026-08-14')
        qa = dataqa.validate(fb)
        self.assertIn('cross_source', qa['fields']['capex_ttm']['checks'])
        self.assertTrue(checks(qa, 'cross_source', 'pass'))

    def test_definitional_differences_are_not_conflicts(self):
        fb = syn()
        F.add_source(fb, 'av:bs', 'alpha_vantage', 'balance', 'vendor_normalized', '2026-10-19')
        # vendor "cash" = cash + STI, vendor total debt = debt + operating leases (MSFT/META-type definitions)
        F.add_fact(fb, 'cash', 'I@2026-06-30', '2026-06-30', (CASH['2026-06-30'] + 100) * M, 'av:bs', 'normalized', '2026-08-14')
        F.add_fact(fb, 'debt_total_vendor', 'I@2026-06-30', '2026-06-30', 600 * M, 'av:bs', 'normalized', '2026-08-14')
        qa = dataqa.validate(fb)
        self.assertNotEqual(qa['fields']['cash_and_liquid_investments']['status'], 'conflict')
        self.assertIn('short-term investments', qa['fields']['cash_and_liquid_investments']['note'])
        self.assertEqual(qa['fields']['debt']['status'], 'verified')
        self.assertIn('lease', qa['fields']['debt']['note'])
        self.assertAlmostEqual(qa['resolved']['net_cash']['value'], (CASH['2026-06-30'] + 100 - 500) * M)

    def test_double_counted_short_term_investments(self):
        fb = syn()
        for f in fb['facts']:
            if f['metric'] == 'short_term_investments' and f['period_end'] == '2026-06-30':
                f['value'] = CASH['2026-06-30'] * M                  # ASTS-type: the same amount tagged twice
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'conflict')
        self.assertTrue(checks(qa, 'liquid_assets_within_current_assets', 'fail'))

    def test_missing_cash_blocks(self):
        fb = syn()
        fb['facts'] = [f for f in fb['facts'] if f['metric'] != 'cash']
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'missing')
        self.assertEqual(qa['status'], 'blocked')
        self.assertIn('cash_and_liquid_investments', qa['blocking'])
        self.assertIsNone(qa['resolved']['net_cash']['value'])


class Shares(unittest.TestCase):
    def _split_fb(self, eps_after, wavg_after=None):
        fb = syn(quarters=False)
        for f in fb['facts']:
            if f['metric'] == 'shares_outstanding' and f['period_end'] == '2025-12-31':
                f['value'] = 98.0 * 5 * M                            # 5:1 split during FY2025 (NOW-type)
            if f['metric'] == 'eps_diluted' and f['period_end'] == '2025-12-31':
                f['value'] = eps_after
            if f['metric'] == 'shares_diluted_wavg' and f['period_end'] == '2025-12-31' and wavg_after:
                f['value'] = wavg_after                              # a split restates the whole year's weighted average
        return fb

    def test_split_is_not_dilution(self):
        qa = dataqa.validate(self._split_fb(1.63 / 5, WAVG['2025-12-31'] * 5 * M))
        sp = checks(qa, 'possible_split')
        self.assertEqual(len(sp), 1)
        self.assertEqual(sp[0]['severity'], 'major')
        self.assertEqual(sp[0]['detail']['factor'], 5)
        self.assertTrue(sp[0]['detail']['evidence']['eps_supports_split'])
        self.assertEqual((qa['splits'][0]['factor'], qa['splits'][0]['decided_by']), (5, 'per_share'))
        periods = [c['period'] for c in checks(qa, 'share_continuity')]
        self.assertNotIn('2024-12-31..2025-12-31', periods)          # no 394%-style "dilution" computed
        self.assertNotEqual(qa['fields']['shares_outstanding']['status'], 'conflict')

    def test_split_factor_helper(self):
        self.assertEqual(dataqa._split_factor(4.94), 5)
        self.assertAlmostEqual(dataqa._split_factor(.0502), 1 / 20)       # 1:20 reverse split
        self.assertIsNone(dataqa._split_factor(1.2))
        self.assertIsNone(dataqa._split_factor(0))

    def test_issuance_near_split_factor_with_flat_per_share_data(self):
        qa = dataqa.validate(self._split_fb(1.63))
        self.assertFalse(checks(qa, 'possible_split'))
        self.assertTrue(checks(qa, 'large_share_change'))

    def test_weighted_average_never_used_as_point_in_time(self):
        fb = syn(quarters=False)
        for f in fb['facts']:
            if f['metric'] == 'shares_outstanding' and f['period_end'] == '2025-12-31':
                f['value'] = WAVG['2025-12-31'] * M
        qa = dataqa.validate(fb)
        self.assertTrue(checks(qa, 'shares_not_wavg', 'fail'))
        self.assertIn(qa['fields']['shares_outstanding']['status'], ('single_source', 'stale'))

    def test_vendor_float_reading_the_cover_is_not_an_independent_confirmation(self):
        fb = syn()
        F.add_source(fb, 'fmp:float', 'fmp', 'shares-float', 'vendor_normalized', '2026-10-19')
        F.add_fact(fb, 'shares_outstanding_vendor', 'I@2026-07-25', '2026-07-25', 97.41 * M, 'fmp:float', 'normalized', '2026-10-19')
        qa = dataqa.validate(fb)
        self.assertEqual(qa['resolved']['shares_outstanding']['basis'], 'cover')
        self.assertEqual(qa['fields']['shares_outstanding']['status'], 'reported')     # was 'verified': one cover read twice
        self.assertFalse(checks(qa, 'share_routes')[0]['detail']['agree'][0]['independent'])

    def test_prefunded_warrant_evidence_without_structure_limits(self):
        fb = syn()
        F.add_qualitative(fb, 'w1', 'prefunded_warrant', '21m pre-funded warrants held by a strategic investor', 'reported',
                          ['sec:cf'], '2026-08-01')
        qa = dataqa.validate(fb)
        self.assertTrue(qa['fields']['shares_outstanding'].get('incomplete'))
        self.assertIn('shares_outstanding', qa['limited'])
        cs = clean_cs()
        cs['shares'].append({'id': 'pfw', 'kind': 'prefunded_warrant', 'count': 21 * M, 'strike': .0001, 'source_id': 'sec:cf'})
        qa2 = dataqa.validate(fb, cs)
        self.assertFalse(qa2['fields']['shares_outstanding'].get('incomplete'))
        self.assertAlmostEqual(qa2['resolved']['shares_outstanding']['basic_economic_shares'], (97.5 + 21) * M)


class Debt(unittest.TestCase):
    def _tagged(self, cur_now, cur_prior, issued=None, repaid=None, ltd_now=600, ltd_prior=600):
        fb = syn(quarters=False, debt=False)
        for end, ltd, cur in (('2024-12-31', ltd_prior, cur_prior), ('2025-12-31', ltd_now, cur_now)):
            F.add_fact(fb, 'long_term_debt_tagged', 'I@' + end, end, ltd * M, 'sec:cf', 'as_reported', _plus(end, 45), concept='longtermdebt')
            if cur:
                F.add_fact(fb, 'current_portion_ltd', 'I@' + end, end, cur * M, 'sec:cf', 'as_reported', _plus(end, 45), concept='longtermdebtcurrent')
        for m, v in (('debt_issued', issued), ('debt_repaid', repaid)):
            if v:
                F.add_fact(fb, m, 'FY2025', '2025-12-31', v * M, 'sec:cf', 'as_reported', '2026-02-14')
        return fb

    def test_rollforward_picks_tagged_total(self):
        # LongTermDebt here is the TOTAL: 600 + 100 issued - 0 = 700 incl. current 150
        qa = dataqa.validate(self._tagged(cur_now=150, cur_prior=100, issued=100, ltd_now=700, ltd_prior=600))
        self.assertEqual(qa['resolved']['debt']['method'], 'tagged_total')
        self.assertEqual(qa['resolved']['debt']['value'], 700 * M)

    def test_unresolved_material_scope_is_limited(self):
        # no issuance/repayment facts and both hypotheses fail the roll-forward -> not decisive, current portion material
        qa = dataqa.validate(self._tagged(cur_now=150, cur_prior=0, ltd_now=900, ltd_prior=600),
                             options={'max_age': {'balance_sheet': 400}})
        d = qa['resolved']['debt']
        self.assertFalse(d['decisive'])
        self.assertEqual(d['method'], 'tagged_plus_current')          # conservative, and said so
        self.assertEqual(qa['fields']['debt']['status'], 'single_source')
        self.assertIn('debt', qa['limited'])
        self.assertTrue(checks(qa, 'debt_scope', 'fail'))

    def test_vanished_debt_line_without_repayment_blocks_instead_of_zero(self):
        fb = syn(debt=False)
        for end in [e for e in CASH if e != '2026-06-30']:
            F.add_fact(fb, 'long_term_debt_tagged', 'I@' + end, end, 300 * M, 'sec:cf', 'as_reported', _plus(end, 40), concept='LongTermDebt')
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['debt']['status'], 'conflict')
        self.assertIn('debt', qa['blocking'])
        self.assertTrue(checks(qa, 'debt_line_continuity', 'fail'))

    def test_debt_tagged_only_as_components_is_summed(self):
        def rows(v, end, filed):
            return [{'end': end, 'val': v * M, 'filed': filed, 'form': '10-Q', 'fy': 2026, 'fp': 'Q2'}]
        payload = {'cik': 1, 'facts': {'us-gaap': {                      # SYNTHETIC values shaped like Collegium's tags
            'LongTermLoansPayable': {'units': {'USD': rows(797.8, '2026-06-30', '2026-08-07')}},
            'LoansPayableCurrent': {'units': {'USD': rows(55.0, '2026-06-30', '2026-08-07')}},
            'ConvertibleLongTermNotesPayable': {'units': {'USD': rows(238.7, '2026-06-30', '2026-08-07')}},
            'LongTermDebtNoncurrent': {'units': {'USD': rows(400.0, '2025-12-31', '2026-02-20')}}}}}
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-20')
        F.from_sec_companyfacts(fb, payload, '2026-10-20')
        got = {(f['metric'], f['period_end']): f for f in fb['facts']}
        nc = got[('long_term_debt', '2026-06-30')]
        self.assertAlmostEqual(nc['value'], (797.8 + 238.7) * M)
        self.assertEqual((nc['basis'], nc['concept']), ('derived', 'sum:LongTermLoansPayable+ConvertibleLongTermNotesPayable'))
        self.assertAlmostEqual(got[('current_portion_ltd', '2026-06-30')]['value'], 55.0 * M)
        self.assertEqual(got[('long_term_debt', '2025-12-31')]['concept'], 'LongTermDebtNoncurrent')   # a total line wins its date

    def test_immaterial_convertibles_without_terms_do_not_block(self):
        fb = syn()
        F.add_fact(fb, 'convertible_debt', 'I@2026-06-30', '2026-06-30', 5 * M, 'sec:cf', 'as_reported', '2026-08-05',
                   concept='ConvertibleNotesPayable')                       # SYNTHETIC: $5m against ~$8bn of market value
        qa = dataqa.validate(fb)
        self.assertNotIn('convertibles', qa['blocking'])
        self.assertIn('immaterial', qa['fields']['convertibles']['note'])
        F.add_fact(fb, 'convertible_debt', 'I@2026-06-30', '2026-06-30', 900 * M, 'sec:cf', 'as_reported', '2026-08-05',
                   concept='ConvertibleSeniorNotes')
        fb['facts'] = [f for f in fb['facts'] if not (f['metric'] == 'convertible_debt' and f['value'] == 5 * M)]
        self.assertIn('convertibles', dataqa.validate(fb)['blocking'])       # material: terms required

    def test_notes_payable_tag_is_read_as_debt(self):
        self.assertIn('longtermnotespayable', F.INSTANT_TAGS['long_term_debt_tagged'])
        self.assertIn('notespayablecurrent', F.INSTANT_TAGS['current_portion_ltd'])

    def test_fair_value_only_debt_is_a_limited_proxy(self):
        fb = syn(quarters=False, debt=False)
        F.add_fact(fb, 'debt_fair_value', 'I@2025-12-31', '2025-12-31', 480 * M, 'sec:cf', 'as_reported', '2026-02-14',
                   concept='longtermdebtfairvalue')
        qa = dataqa.validate(fb, options={'max_age': {'balance_sheet': 400}})
        self.assertEqual(qa['resolved']['debt']['method'], 'fair_value_proxy')
        self.assertEqual(qa['fields']['debt']['status'], 'single_source')
        self.assertNotIn('debt', qa['blocking'])

    def test_no_debt_line_with_interest_expense_blocks(self):
        qa = dataqa.validate(syn(debt=False))         # interest expense 25 on revenue 1200: debt exists but is not tagged
        self.assertEqual(qa['fields']['debt']['status'], 'missing')
        self.assertIn('debt', qa['blocking'])
        fb = syn(debt=False)
        fb['facts'] = [f for f in fb['facts'] if f['metric'] != 'interest_expense']
        qa2 = dataqa.validate(fb)
        self.assertEqual((qa2['resolved']['debt']['value'], qa2['resolved']['debt']['method']), (0.0, 'none_tagged'))
        self.assertNotIn('debt', qa2['blocking'])


class Convertibles(unittest.TestCase):
    def test_convertible_fact_without_terms_blocks(self):
        fb = syn()
        F.add_fact(fb, 'convertible_notes', 'I@2026-06-30', '2026-06-30', 200 * M, 'sec:cf', 'as_reported', '2026-08-04',
                   concept='convertiblenotespayable')
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['convertibles']['status'], 'missing')
        self.assertTrue(qa['fields']['convertibles']['blocking'])
        self.assertEqual(qa['status'], 'blocked')

    def test_qualitative_mention_without_terms_blocks_until_structure_given(self):
        fb = syn()
        F.add_qualitative(fb, 'c1', 'convertible', 'Issued 2031 convertible senior notes in March 2026', 'reported', ['sec:cf'], '2026-04-01')
        self.assertIn('convertibles', dataqa.validate(fb)['blocking'])
        cs = clean_cs(extra_debt=[{'id': 'cv', 'kind': 'convertible', 'principal': 200 * M, 'issue_date': '2026-03-20',
                                   'maturity': '2031-03-15', 'source_id': 'sec:cf'}])       # terms absent: no conversion price
        qa = dataqa.validate(fb, cs)
        self.assertIn('convertibles', qa['blocking'])
        self.assertEqual(qa['fields']['convertibles']['note'].count('conversion_price'), 1)
        cs['debt'][-1]['conversion_price'] = 150.0
        qa2 = dataqa.validate(fb, cs)
        self.assertNotIn('convertibles', qa2['blocking'])
        self.assertAlmostEqual(qa2['fields']['convertibles']['value'], 200 * M)

    def test_capital_structure_after_as_of_is_ignored(self):
        cs = clean_cs(as_of='2026-12-31')
        qa = dataqa.validate(syn(), cs)
        self.assertTrue(checks(qa, 'capital_structure_lookahead', 'fail'))
        self.assertEqual(qa['resolved']['debt']['method'], 'noncurrent+current')


class RealCapitalStructure(unittest.TestCase):
    """examples/real/nbis_capital_2026-08-24.json: a statement-date + post-period bridge (secured facility, new converts,
    an 800M note exchange) with pre-funded warrants. Statement facts come from the same file's 'reported' block."""

    def test_nbis_bridge(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'examples', 'real', 'nbis_capital_2026-08-24.json')
        with open(path, encoding='utf-8') as fh:
            d = json.load(fh)
        cs, rep = d['capital_structure'], d['reported']
        fb = F.new({'ticker': 'NBIS', 'currency': 'USD'}, '2026-08-24')
        for sid in {x['source_id'] for g in ('cash', 'shares', 'debt', 'quasi_debt') for x in cs.get(g, [])}:
            F.add_source(fb, sid, 'issuer', sid, 'filing_derived', '2026-08-24')
        for m, v in (('cash', rep['cash_and_equivalents']), ('long_term_debt', rep['debt_principal_outstanding']),
                     ('shares_outstanding', rep['shares_count']), ('total_assets', 30e9)):
            F.add_fact(fb, m, 'I@2026-06-30', '2026-06-30', v, '6k_q2_2026', 'as_reported', '2026-08-14')
        qa = dataqa.validate(fb, cs)
        r = qa['resolved']
        self.assertEqual(r['cash']['basis'], 'capital_structure')
        self.assertAlmostEqual(r['cash']['value'], 8042.1e6 + 775e6 + 5680e6)
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'reported')   # statement-dated lines tie
        # the post-period exchange changed pre-existing notes: limited with an explanation, not a false conflict
        self.assertEqual(r['debt']['method'], 'capital_structure')
        self.assertEqual(qa['fields']['debt']['status'], 'single_source')
        self.assertIn('post-period exchanges', qa['fields']['debt']['note'])
        self.assertEqual(r['shares_outstanding']['basis'], 'capital_structure')
        self.assertAlmostEqual(r['shares_outstanding']['basic_economic_shares'] - r['shares_outstanding']['value'], 21065936)
        self.assertEqual(qa['fields']['convertibles']['status'], 'reported')
        self.assertGreater(qa['fields']['convertibles']['conversion_shares'], 0)
        self.assertEqual(qa['fields']['post_period_events']['status'], 'reported')
        self.assertEqual(r['long_term_investments']['value'], 1606.7e6)                     # shown separately, not spendable
        self.assertAlmostEqual(r['net_cash']['net_cash_incl_long_term_investments'] - r['net_cash']['value'], 1606.7e6)


class MixedProviderQuarters(unittest.TestCase):
    def test_quarters_dated_differently_by_providers_still_give_quarterly_ttm(self):
        fb = F.new({'ticker': 'MIX', 'name': 'Mixed dating (synthetic test data)', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 'sec', 'sec', 'companyfacts', 'filing_derived', '2026-10-19')
        F.add_source(fb, 'av', 'alpha_vantage', 'income', 'vendor_normalized', '2026-10-19')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-12-27', 400 * M, 'sec', 'as_reported', '2026-02-20')
        for sec_end, av_end, v in (('2025-09-27', '2025-09-30', 95), ('2025-12-27', '2025-12-31', 105),
                                   ('2026-03-28', '2026-03-31', 110), ('2026-06-27', '2026-06-30', 120)):
            F.add_fact(fb, 'revenue', f'Q@{sec_end}', sec_end, v * M, 'sec', 'as_reported', _plus(sec_end, 40))
            F.add_fact(fb, 'revenue', f'Q@{av_end}', av_end, v * M, 'av', 'normalized', _plus(av_end, 45), estimated=True)
        self.assertEqual(F.ttm(fb, 'revenue')['method'], 'quarters')              # facts.series matches dates within 7 days
        f = dataqa.validate(fb)['fields']['revenue_ttm']
        self.assertEqual((f['method'], f['value'], f['period_end']), ('quarters', 430 * M, '2026-06-27'))
        self.assertEqual(f['status'], 'verified')                 # SEC and AV agree on every quarter



# ---------------------------------------------------------------- review 2026-10-19 (scratchpad findings, data area; SYNTHETIC data)

def _quarterly_book():
    fb = F.new({'ticker': 'SYN', 'name': 'Synthetic (test data)', 'currency': 'USD'}, '2026-10-19')
    F.add_source(fb, 'k', 'sec', '10-K', 'filing_derived', '2026-10-19')
    F.add_source(fb, 'q', 'sec', '10-Q', 'filing_derived', '2026-10-19')
    F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 400.0, 'k', 'as_reported', '2026-02-20')
    for end, v in (('2025-03-31', 90), ('2025-06-30', 100), ('2025-09-30', 110), ('2026-03-31', 110), ('2026-06-30', 120), ('2026-09-30', 130)):
        F.add_fact(fb, 'revenue', 'Q@' + end, end, float(v), 'q', 'as_reported', min(_plus(end, 30), '2026-10-15'))
    F.add_fact(fb, 'capex', 'FY2025', '2025-12-31', -40.0, 'k', 'as_reported', '2026-02-20')
    F.add_fact(fb, 'capex', 'YTD@2026-09-30', '2026-09-30', -33.0, 'q', 'as_reported', '2026-10-15')
    F.add_fact(fb, 'capex', 'YTD@2025-09-30', '2025-09-30', -28.0, 'q', 'as_reported', '2025-10-15')
    return fb


class TtmNeverResummed(unittest.TestCase):
    def test_annual_plus_ytd_subtracts_the_prior_year(self):
        fb = _quarterly_book()
        qa = dataqa.validate(fb)
        r = qa['fields']['revenue_ttm']
        self.assertEqual((r['method'], r['value']), ('annual_plus_ytd', F.ttm(fb, 'revenue')['value']))
        self.assertEqual(r['value'], 460.0)                                   # was 1060: prior-year quarters added
        self.assertEqual(r['signs'], [1, 1, 1, 1, -1, -1, -1])
        self.assertEqual(qa['fields']['capex_ttm']['value'], 45.0)            # 40 + 33 - 28, sign-normalised
        self.assertTrue(checks(qa, 'ttm_matches_facts', 'pass'))
        self.assertFalse(checks(qa, 'ttm_matches_facts', 'fail'))


class StaleTtm(unittest.TestCase):
    def test_ttm_older_than_twice_the_limit_is_missing(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 'k', 'sec', '10-K', 'filing_derived', '2026-10-19')
        for m in ('revenue', 'cfo'):
            F.add_fact(fb, m, 'FY2017', '2017-12-31', 100.0, 'k', 'as_reported', '2018-02-20')
        qa = dataqa.validate(fb)
        r = qa['fields']['revenue_ttm']
        self.assertEqual((r['status'], r['source_status']), ('missing', 'reported'))
        self.assertIn('revenue_ttm', qa['blocking'])
        fb['as_of'] = '2018-12-31'                      # 365 days: stale, not missing
        self.assertEqual(dataqa.validate(fb)['fields']['revenue_ttm']['status'], 'stale')


def _count_book(counts, wavg=None, eps=None, as_of='2026-10-19', closes=None, basis=None):
    """SYNTHETIC: year-end share counts {end: count} filed 50 days later, optional weighted averages / EPS, optional closes."""
    fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, as_of)
    F.add_source(fb, 'k', 'sec', '10-K', 'filing_derived', as_of)
    for end, so in counts.items():
        av = _plus(end, 50)
        F.add_fact(fb, 'shares_outstanding', 'I@' + end, end, so, 'k', 'as_reported', av)
        F.add_fact(fb, 'total_assets', 'I@' + end, end, 900e6, 'k', 'as_reported', av)
        if wavg:
            F.add_fact(fb, 'shares_diluted_wavg', f'FY{end[:4]}', end, wavg[end], 'k', 'as_reported', av)
        if eps:
            F.add_fact(fb, 'eps_diluted', f'FY{end[:4]}', end, eps[end], 'k', 'as_reported', av, unit='USD/share')
    if closes:
        fb['market']['price_history'] = [{'date': d, 'close': c} for d, c in closes]
    if basis:
        fb['market']['price_history_basis'] = basis
    return fb


def _monthly(start, end, before, after, split_month):
    """Closes on the 28th of each month from start to end: `before` until split_month (YYYY-MM), `after` from then on."""
    out, y, m = [], int(start[:4]), int(start[5:7])
    while f'{y:04d}-{m:02d}-28' <= end:
        d = f'{y:04d}-{m:02d}-28'
        out.append((d, after if d[:7] >= split_month else before))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class SplitEvidence(unittest.TestCase):
    """Only positive evidence makes a split; disagreeing or missing evidence leaves the step unresolved (issuance)."""
    ENDS = ('2022-12-31', '2023-12-31', '2024-12-31', '2025-12-31')

    def test_real_split_after_a_rally_is_not_issuance(self):
        # 2:1 split, stock +50% over the year (as traded 100 -> 75), weighted average restated: was 'large_share_change' (price)
        counts = dict(zip(self.ENDS, (100e6, 101e6, 102e6, 204e6)))
        fb = _count_book(counts, wavg=counts, eps=dict(zip(self.ENDS, (2.0, 2.2, 2.4, 1.4))),
                         closes=[(_plus(e, -1), c) for e, c in zip(self.ENDS, (60.0, 80.0, 100.0, 75.0))])
        qa = dataqa.validate(fb)
        self.assertEqual([(s['factor'], s['decided_by']) for s in qa['splits']], [(2, 'per_share')])
        self.assertFalse(checks(qa, 'large_share_change'))
        self.assertEqual(qa['splits'][0]['evidence']['tests']['price'], 'untested')        # unknown-basis, sparse closes

    def test_split_adjusted_closes_never_decide(self):
        # real 5:1 split, closes split-ADJUSTED (flat through the split): was ruled issuance by the price test
        counts = dict(zip(self.ENDS, (100e6, 100e6, 100e6, 500e6)))
        closes = _monthly('2024-11-28', '2025-12-28', 100.0, 100.0, '2025-07')         # adjusted: no jump at the split
        fb = _count_book(counts, wavg=dict(zip(self.ENDS, (100e6, 100e6, 100e6, 500e6))), closes=closes, basis='split_adjusted')
        qa = dataqa.validate(fb)
        self.assertEqual([(s['factor'], s['decided_by']) for s in qa['splits']], [(5, 'per_share')])
        ev = qa['splits'][0]['evidence']
        self.assertEqual((ev['price_basis'], ev['tests']['price']), ('split_adjusted', 'untested'))

    def test_dense_as_traded_closes_locate_the_split_bar(self):
        counts = dict(zip(self.ENDS, (100e6, 100e6, 100e6, 200e6)))
        fb = _count_book(counts, closes=_monthly('2024-11-28', '2025-12-28', 80.0, 41.0, '2025-07'), basis='as_traded')
        qa = dataqa.validate(fb)
        self.assertEqual([(s['factor'], s['decided_by']) for s in qa['splits']], [(2, 'price')])
        flat = _count_book(counts, closes=_monthly('2024-11-28', '2025-12-28', 80.0, 80.0, '2025-07'), basis='as_traded')
        qa2 = dataqa.validate(flat)
        self.assertEqual(qa2['splits'], [])
        self.assertEqual(checks(qa2, 'large_share_change')[0]['detail']['decided_by'], 'price')

    def test_equity_raise_doubling_the_count_stays_dilution(self):
        # March raise: 100M -> 200M, weighted average 99.5M -> 180M (time-weighted): was removed as a 'per_share' split
        counts = dict(zip(self.ENDS, (98e6, 99e6, 100e6, 200e6)))
        wavg = dict(zip(self.ENDS, (97e6, 98.5e6, 99.5e6, 180e6)))
        qa = dataqa.validate(_count_book(counts, wavg=wavg, eps={e: -50e6 / w for e, w in wavg.items()}))
        self.assertEqual((qa['splits'], qa['unresolved_splits']), ([], []))
        self.assertEqual(checks(qa, 'large_share_change')[0]['detail']['decided_by'], 'per_share')
        bare = dataqa.validate(_count_book(counts))                          # counts only: was removed as an 'untested' split
        self.assertEqual(bare['splits'], [])
        self.assertEqual([(x['factor'], x['decided_by']) for x in bare['unresolved_splits']], [(2, 'untested')])
        self.assertIn('both readings', checks(bare, 'split_unresolved')[0]['message'])

    def test_split_plus_issuance_in_one_step_keeps_the_issuance(self):
        # 5:1 split plus ~6% issuance in the same fiscal year (count x5.30): was no split check, the whole jump as dilution
        ends = ('2017-12-31', '2018-12-31', '2019-12-31', '2020-12-31')
        counts = dict(zip(ends, (169e6, 173e6, 181e6, 960e6)))
        qa = dataqa.validate(_count_book(counts, wavg=dict(zip(ends, (165e6, 171e6, 177e6, 930e6))), as_of='2021-03-01'))
        sp = qa['splits']
        self.assertEqual((sp[0]['factor'], sp[0]['decided_by']), (5, 'per_share'))
        self.assertAlmostEqual(sp[0]['residual_ratio'], 960 / 181 / 5)

    def test_dated_split_decides_and_disagreeing_evidence_is_unresolved(self):
        counts = dict(zip(self.ENDS, (100e6, 100e6, 100e6, 200e6)))
        fb = _count_book(counts)
        fb['market']['splits'] = [{'date': '2025-07-15', 'factor': 2, 'source_id': 'k'}]
        self.assertEqual([(s['factor'], s['decided_by']) for s in dataqa.validate(fb)['splits']], [(2, 'corporate_action')])
        mixed = _count_book(counts, wavg=dict(zip(self.ENDS, (100e6, 100e6, 100e6, 150e6))),       # weighted: an issuance
                            closes=_monthly('2024-11-28', '2025-12-28', 80.0, 41.0, '2025-07'), basis='as_traded')   # price: a split
        qa = dataqa.validate(mixed)
        self.assertEqual(qa['splits'], [])
        self.assertEqual(qa['unresolved_splits'][0]['decided_by'], 'conflicting')


class SourceIndependence(unittest.TestCase):
    def test_same_source_typed_twice_is_not_a_confirmation(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 'q', 'sec', '10-Q', 'filing_derived', '2026-10-19')
        F.add_fact(fb, 'shares_outstanding_cover', 'I@2026-10-01', '2026-10-01', 100e6, 'q', 'as_reported', '2026-10-10', unit='shares')
        cs = {'as_of': '2026-10-01', 'currency': 'USD', 'shares': [{'id': 'common', 'kind': 'common', 'count': 100e6, 'source_id': 'q'}]}
        qa = dataqa.validate(fb, cs)
        self.assertEqual(qa['fields']['shares_outstanding']['status'], 'reported')
        self.assertFalse(checks(qa, 'share_routes')[0]['detail']['agree'][0]['independent'])

    def test_same_source_capital_structure_ties_without_verifying(self):
        qa = dataqa.validate(syn(), clean_cs())                               # statements and structure both cite sec:cf
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'reported')
        self.assertIn('not an independent confirmation', qa['fields']['cash_and_liquid_investments']['note'])
        self.assertTrue(checks(qa, 'capital_structure_crosscheck', 'pass'))

    def test_secondary_source_never_conflicts_with_a_filing_value(self):
        def book(with_blog):
            fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
            F.from_fmp_as_reported(fb, {'income': [{'fiscalYear': 2026, 'period': 'FY', 'date': '2026-06-30',
                                                    'data': {'Revenues': 1000e6}}]}, '2026-10-19', filed_by_end={'2026-06-30': '2026-08-01'})
            if with_blog:
                F.add_qualitative_bundle(fb, {'sources': [{'id': 'ai', 'kind': 'analysis', 'published_at': '2026-08-05'}],
                                              'quarterly_facts': [{'metric': 'revenue', 'period': 'FY2026', 'period_end': '2026-06-30',
                                                                   'value': 989e6, 'source_ids': ['ai']}]})
            return fb
        qa = dataqa.validate(book(True))
        r = qa['fields']['revenue_ttm']
        self.assertEqual(r['status'], 'reported')                              # was conflict (blocking)
        self.assertNotIn('revenue_ttm', qa['blocking'])
        self.assertIn('secondary source web:ai', r['note'])
        self.assertEqual(r['status'], dataqa.validate(book(False))['fields']['revenue_ttm']['status'])

    def test_secondary_only_value_is_single_source(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_qualitative_bundle(fb, {'sources': [{'id': 'p', 'kind': 'press', 'published_at': '2026-08-05'}],
                                      'quarterly_facts': [{'metric': 'revenue', 'period': 'FY2026', 'period_end': '2026-06-30',
                                                           'value': 989e6, 'source_ids': ['p']}]})
        self.assertEqual(dataqa.validate(fb)['fields']['revenue_ttm']['status'], 'single_source')


class LatestBalanceDate(unittest.TestCase):
    def test_partial_quarter_end_lines_set_the_latest_balance_date(self):
        fb = adbe()
        F.add_qualitative_bundle(fb, {'as_of': '2026-10-19', 'sources': [{'id': 'q3', 'kind': 'filing', 'published_at': '2026-09-18'}],
                                      'quarterly_facts': [{'metric': m, 'period': 'FY2026Q3', 'period_end': '2026-08-28', 'value': v,
                                                           'source_ids': ['q3']} for m, v in (('cash', 4.0e9), ('short_term_investments', 1.2e9))]})
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['post_period_events']['as_of'], '2026-08-28')
        self.assertEqual(qa['resolved']['cash']['as_of'], F.latest_instant(fb, 'cash')['period_end'])
        inv = qa['fields']['investments']
        self.assertNotEqual(inv['status'], 'missing')                          # was "no balance sheet usable at as_of"
        self.assertEqual((inv['value'], inv['as_of']), (0.0, '2025-11-28'))
        self.assertIn('partial', inv['note'])


if __name__ == '__main__':
    unittest.main()


# ---------------------------------------------------------------- review 2026-10-20 (point-in-time and sources; SYNTHETIC data)

class PointInTimeRegressions(unittest.TestCase):
    def test_capital_structure_line_issued_after_as_of_is_not_read(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-09-15')
        F.add_source(fb, 'sec', 'sec', 'companyfacts', 'filing_derived', '2026-09-15')
        for m, v in (('shares_outstanding', 50.0), ('long_term_debt', 200.0), ('cash', 100.0), ('total_assets', 900.0)):
            F.add_fact(fb, m, 'I@2026-08-28', '2026-08-28', v, 'sec', 'as_reported', '2026-09-10')
        cs = {'as_of': '2026-08-28', 'currency': 'USD',
              'shares': [{'id': 'common', 'kind': 'common', 'count': 50.0, 'source_id': 'sec', 'as_of': '2026-08-28'},
                         {'id': 'new', 'kind': 'common', 'count': 25.0, 'source_id': 'sec', 'issue_date': '2026-10-01'}],
              'debt': [{'id': 'notes', 'kind': 'bond', 'principal': 200.0, 'issue_date': '2020-01-01', 'maturity': '2030-01-01', 'source_id': 'sec'},
                       {'id': 'cv9', 'kind': 'convertible', 'principal': 300.0, 'conversion_price': 10.0, 'issue_date': '2026-10-05',
                        'maturity': '2031-10-05', 'source_id': 'sec'}]}
        qa = dataqa.validate(fb, cs)
        sh = qa['fields']['shares_outstanding']
        self.assertEqual((sh['value'], sh['as_of']), (50.0, '2026-08-28'))          # was 75 dated 2026-10-01 (age -16 days)
        self.assertGreaterEqual(sh['age_days'], 0)
        self.assertEqual(qa['fields']['convertibles']['instruments'], [])
        self.assertEqual(qa['fields']['post_period_events']['capital_structure_lines'], [])
        self.assertEqual({x['id'] for x in checks(qa, 'capital_structure_lookahead')[0]['detail']['excluded']}, {'new', 'cv9'})

    def test_items_are_read_by_their_availability_date(self):
        fb = syn(as_of='2026-09-15')
        F.add_qualitative(fb, 'ev1', 'post_period_event', 'note offering after the quarter', 'reported', ['sec:cf'], '2026-08-28')
        fb['qualitative'][-1]['available_at'] = '2026-10-12'          # re-cut FactBook: the 10-Q stating it was public later
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['post_period_events']['status'], 'missing')   # was 'reported' (read by its as_of)

    def test_post_period_review_needs_an_event_after_the_balance_sheet(self):
        fb = syn()
        F.add_qualitative(fb, 'old', 'subsequent_event', 'event before the latest balance sheet', 'reported', ['sec:cf'], '2025-01-20')
        self.assertEqual(dataqa.validate(fb)['fields']['post_period_events']['status'], 'missing')   # was 'reported'
        F.add_qualitative(fb, 'new', 'subsequent_event', 'no financing since quarter end', 'reported', ['sec:cf'], '2026-08-15')
        self.assertEqual(dataqa.validate(fb)['fields']['post_period_events']['events'], ['new'])


class ArithmeticRegressions(unittest.TestCase):
    def test_debt_current_already_contains_commercial_paper(self):
        def book(cur_concept, cur):
            fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-03-01')
            F.add_source(fb, 'sec', 'sec', 'companyfacts', 'filing_derived', '2026-03-01')
            for m, v, c in (('long_term_debt', 40e9, 'longtermdebtnoncurrent'), ('current_portion_ltd', cur, cur_concept),
                            ('short_term_borrowings', 2e9, 'commercialpaper'), ('cash', 8e9, None), ('total_assets', 90e9, None)):
                F.add_fact(fb, m, 'I@2025-12-31', '2025-12-31', v, 'sec', 'as_reported', '2026-02-15', concept=c)
            return dataqa.validate(fb)
        qa = book('debtcurrent', 3e9)                                    # DebtCurrent = 1.0 current maturities + 2.0 CP
        self.assertEqual(qa['resolved']['debt']['value'], 43e9)          # was 45bn (CP counted twice)
        self.assertEqual(qa['resolved']['net_cash']['value'], -35e9)
        self.assertEqual(book('longtermdebtcurrent', 1e9)['resolved']['debt']['value'], 43e9)


class SourceFamilyRegressions(unittest.TestCase):
    def _sec_fmp(self, fmp=True, av=None):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_source(fb, 'sec:cf', 'sec', 'companyfacts', 'filing_derived', '2026-10-19')
        F.add_fact(fb, 'revenue', 'FY2025', '2025-12-31', 1000e6, 'sec:cf', 'as_reported', '2026-02-22')
        F.add_fact(fb, 'shares_outstanding', 'I@2025-12-31', '2025-12-31', 100e6, 'sec:cf', 'as_reported', '2026-02-22')
        if fmp:
            F.from_fmp_as_reported(fb, {'income': [{'fiscalYear': 2025, 'period': 'FY', 'date': '2025-12-31', 'data': {'Revenues': 1000e6}}],
                                        'balance': [{'fiscalYear': 2025, 'period': 'FY', 'date': '2025-12-31',
                                                     'data': {'CommonStockSharesOutstanding': 100e6}}]},
                                   '2026-10-19', filed_by_end={'2025-12-31': '2026-02-20'})
        if av:
            F.add_source(fb, 'av:income', 'alpha_vantage', 'income', 'vendor_normalized', '2026-10-19')
            F.add_fact(fb, 'revenue', 'AVFY@2025-12-31', '2025-12-31', av, 'av:income', 'normalized', '2026-03-31', estimated=True)
        return dataqa.validate(fb, options={'max_age': {'ttm': 400, 'shares': 400}})

    def test_one_filing_through_two_vendors_is_not_two_confirmations(self):
        qa = self._sec_fmp()
        self.assertEqual((qa['fields']['revenue_ttm']['status'], qa['fields']['shares_outstanding']['status']), ('reported', 'reported'))
        self.assertEqual(self._sec_fmp(fmp=False, av=1080e6)['fields']['revenue_ttm']['status'], 'conflict')
        self.assertEqual(self._sec_fmp(av=1080e6)['fields']['revenue_ttm']['status'], 'conflict')     # was 'verified' by the copy

    def test_press_share_count_never_displaces_or_judges_a_filing_count(self):
        def qa(press):
            fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
            F.add_source(fb, 'sec:cf', 'sec', 'companyfacts', 'filing_derived', '2026-10-19')
            F.add_fact(fb, 'shares_outstanding', 'I@2026-06-30', '2026-06-30', 100e6, 'sec:cf', 'as_reported', '2026-08-01', unit='shares')
            F.add_qualitative_bundle(fb, {'as_of': '2026-10-19', 'sources': [{'id': 'p', 'kind': 'press', 'published_at': '2026-08-05'}],
                                          'quarterly_facts': [{'metric': 'shares_outstanding', 'period': 'FY2026Q2', 'period_end': '2026-06-30',
                                                               'measured_at': '2026-07-02', 'value': press, 'source_ids': ['p']}]})
            return dataqa.validate(fb)
        for press in (100.2e6, 104e6):                                   # were 'verified' (agrees) and a blocking 'conflict'
            q = qa(press)
            s, r = q['fields']['shares_outstanding'], q['resolved']['shares_outstanding']
            self.assertEqual((s['status'], s['value'], r['source_id']), ('reported', 100e6, 'sec:cf'))
            self.assertNotIn('shares_outstanding', q['blocking'])
            self.assertIn('neither a confirmation nor a conflict', s['note'])

    def test_capital_structure_lines_citing_press_or_the_same_provider_only_tie(self):
        fb = syn()
        F.add_source(fb, 'web:p', 'web', 'press', 'secondary', '2026-10-19')
        F.add_source(fb, 'sec:10q', 'sec', '10-Q', 'filing_derived', '2026-10-19')
        for sid, cash in (('web:p', CASH['2026-06-30'] * M), ('sec:10q', CASH['2026-06-30'] * M), ('web:p', CASH['2026-06-30'] * 1.1 * M)):
            cs = cs_cited(clean_cs(), sid)
            cs['cash'][0]['amount'] = cash
            qa = dataqa.validate(fb, cs)
            self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], 'reported', sid)   # were 'verified' / 'conflict'
            self.assertNotIn('cash_and_liquid_investments', qa['blocking'])

    def test_vendor_totals_never_lift_a_non_filing_value(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_qualitative_bundle(fb, {'as_of': '2026-10-19', 'sources': [{'id': 'p', 'kind': 'press', 'published_at': '2026-08-05'}],
                                      'quarterly_facts': [{'metric': 'debt', 'period': 'FY2026Q2', 'period_end': '2026-06-30', 'value': 900e6,
                                                           'source_ids': ['p']}]})
        F.add_source(fb, 'av:balance', 'alpha_vantage', 'balance', 'vendor_normalized', '2026-10-19')
        F.add_fact(fb, 'debt_total_vendor', 'I@2026-06-30', '2026-06-30', 900e6, 'av:balance', 'normalized', '2026-08-14')
        F.add_fact(fb, 'shares_outstanding', 'I@2026-06-30', '2026-06-30', 100e6, 'av:balance', 'normalized', '2026-08-14')
        F.add_source(fb, 'fmp:float', 'fmp', 'shares-float', 'vendor_normalized', '2026-10-19')
        F.add_fact(fb, 'shares_outstanding_vendor', 'I@2026-07-01', '2026-07-01', 100e6, 'fmp:float', 'normalized', '2026-08-20')
        qa = dataqa.validate(fb)
        self.assertEqual(qa['fields']['debt']['status'], 'single_source')                 # was 'verified' (press + AV)
        self.assertEqual(qa['fields']['shares_outstanding']['status'], 'single_source')   # was 'verified' (AV + FMP float)

    def test_disagreements_inside_the_issuer_and_between_secondary_sources_surface(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-19')
        F.add_qualitative_bundle(fb, {
            'as_of': '2026-10-19',
            'sources': [{'id': 'rel', 'kind': 'issuer_release', 'published_at': '2026-07-28'}, {'id': 'q10', 'kind': 'filing', 'published_at': '2026-08-07'},
                        {'id': 'p1', 'kind': 'press', 'published_at': '2026-08-05'}, {'id': 'a1', 'kind': 'analysis', 'published_at': '2026-08-06'}],
            'quarterly_facts': [{'metric': 'cfo', 'period': 'FY2026', 'period_end': '2026-06-30', 'value': 1000e6, 'source_ids': ['rel']},
                                {'metric': 'cfo', 'period': 'FY2026', 'period_end': '2026-06-30', 'value': 950e6, 'source_ids': ['q10']},
                                {'metric': 'revenue', 'period': 'FY2026', 'period_end': '2026-06-30', 'value': 1100e6, 'source_ids': ['p1']},
                                {'metric': 'revenue', 'period': 'FY2026', 'period_end': '2026-06-30', 'value': 1000e6, 'source_ids': ['a1']}],
            'conflicts': [{'topic': 'capital_allocation', 'description': 'SBC for FY2026: 1,942M (10-K) vs 1,973M (proxy)', 'source_ids': ['q10']},
                          {'topic': 'x', 'description': 'settled', 'source_ids': ['q10'], 'resolved': True}]})
        qa = dataqa.validate(fb)
        cfo = qa['fields']['cfo_ttm']
        self.assertEqual((cfo['value'], cfo['status']), (950e6, 'single_source'))           # was the release's 1,000m, 'reported'
        self.assertTrue(checks(qa, 'issuer_revision', 'fail'))
        self.assertIn('secondary source web:a1', qa['fields']['revenue_ttm']['note'])        # the other figure was recorded nowhere
        self.assertTrue(checks(qa, 'secondary_spread'))
        rc = checks(qa, 'research_conflict')
        self.assertEqual([c['fields'] for c in rc], [['sbc_ttm']])
        self.assertIn('research_conflict', qa['fields']['sbc_ttm']['checks'])


class NegativeLiquidAssets(unittest.TestCase):
    """UTIL regression: a negative cash balance tied to the cash-flow statement passed as 'verified' (status limited for
    other fields) and the valuation then crashed on 'unrestricted_cash: below 0'. It is a critical data error now."""

    def shifted(self, metric, by, end=None):
        fb = syn()
        for f in fb['facts']:
            if f['metric'] == metric and (end is None or f['period_end'] == end):
                f['value'] += by * M                             # every period moves together: the roll-forward still ties
        return fb

    def test_negative_cash_blocks_and_never_reaches_the_valuation(self):
        from fundamental_engine import pipeline
        fb = self.shifted('cash', -1000)
        qa = dataqa.validate(fb)
        fld = qa['fields']['cash_and_liquid_investments']
        self.assertEqual((qa['status'], fld['status'], fld['value'], fld['blocking']), ('blocked', 'conflict', None, True))
        self.assertIn('cash_and_liquid_investments', qa['blocking'])
        self.assertAlmostEqual(fld['rejected_value'], (CASH['2026-06-30'] - 1000 + 100) * M)
        c = checks(qa, 'liquid_assets_nonnegative', 'fail')
        self.assertEqual([(x['severity'], x['fields']) for x in c], [('critical', ['cash_and_liquid_investments'])])
        self.assertTrue(checks(qa, 'cash_rollforward', 'pass'))                    # internally consistent, still impossible
        self.assertIsNone(qa['resolved']['cash']['value'])
        self.assertIsNone(qa['resolved']['net_cash']['value'])
        cf = pipeline.run(fb)
        self.assertEqual(cf['errors'], [])
        self.assertEqual((cf['stages']['valuation'], cf['val']['status']), ('ok', 'not_computed'))
        self.assertIn('opening cash', cf['val']['reason'])

    def test_negative_short_term_investments_are_rejected(self):
        qa = dataqa.validate(self.shifted('short_term_investments', -150, '2026-06-30'))
        fld = qa['fields']['cash_and_liquid_investments']
        self.assertEqual((fld['status'], fld['value']), ('conflict', None))
        self.assertIn('cash_and_liquid_investments', qa['blocking'])
        self.assertIn('short_term_investments', checks(qa, 'liquid_assets_nonnegative', 'fail')[0]['detail'])

    def test_negative_long_term_investments_are_rejected_not_used(self):
        qa = dataqa.validate(self.shifted('long_term_investments', -80, '2026-06-30'))
        inv = qa['fields']['investments']
        self.assertEqual((inv['status'], inv['value'], inv['blocking']), ('conflict', None, False))
        self.assertAlmostEqual(inv['rejected_value'], -30 * M)
        self.assertIn('investments', qa['limited'])
        self.assertIsNone(qa['resolved']['long_term_investments']['value'])
        self.assertEqual(checks(qa, 'liquid_assets_nonnegative', 'fail')[0]['fields'], ['investments'])
        self.assertEqual(qa['fields']['cash_and_liquid_investments']['status'], dataqa.validate(syn())['fields']['cash_and_liquid_investments']['status'])


class InvestmentTagChangeTests(unittest.TestCase):
    """Regression from the NVIDIA 2026-10-20 run: marketable securities moved from MarketableSecuritiesCurrent to
    DebtSecuritiesCurrent, listed stakes sit in EquitySecuritiesFvNi, and the engine read ~$77bn of investments as zero."""

    def test_new_tags_are_read_from_companyfacts(self):
        def rows(v, end, filed):
            return [{'end': end, 'val': v, 'filed': filed, 'form': '10-Q', 'accn': 'a' + filed}]
        payload = {'cik': 1, 'facts': {'us-gaap': {
            'MarketableSecuritiesCurrent': {'units': {'USD': rows(49.0, '2025-10-26', '2025-11-19')}},
            'DebtSecuritiesCurrent': {'units': {'USD': rows(34.1, '2026-07-26', '2026-08-26')}},
            'DebtSecuritiesNoncurrent': {'units': {'USD': rows(12.8, '2026-07-26', '2026-08-26')}},
            'EquitySecuritiesFvNi': {'units': {'USD': rows(42.8, '2026-07-26', '2026-08-26')}}}}}   # SYNTHETIC values
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-20')
        F.from_sec_companyfacts(fb, payload, '2026-10-20')
        got = {(f['metric'], f['period_end']): f['value'] for f in fb['facts']}
        self.assertEqual(got[('short_term_investments', '2025-10-26')], 49.0)
        self.assertEqual(got[('short_term_investments', '2026-07-26')], 34.1)
        self.assertEqual(got[('long_term_investments', '2026-07-26')], 12.8)
        self.assertEqual(got[('equity_securities_fv', '2026-07-26')], 42.8)

    def test_vanished_investment_line_is_flagged_not_silently_zero(self):
        fb = syn()
        fb['facts'] = [f for f in fb['facts'] if not (f['metric'] == 'short_term_investments' and f['period_end'] == '2026-06-30')]
        qa = dataqa.validate(fb)
        hit = checks(qa, 'investment_line_continuity', 'fail')
        self.assertEqual(len(hit), 1, qa['checks'])
        self.assertEqual(hit[0]['detail']['last_period_end'], '2026-03-31')
        self.assertNotEqual(qa['fields']['cash_and_liquid_investments']['status'], 'verified')
        self.assertIn('cash_and_liquid_investments', qa['limited'])
        # the clean company keeps no such flag
        self.assertEqual(checks(dataqa.validate(syn()), 'investment_line_continuity'), [])

    def test_listed_equity_stakes_resolved_separately(self):
        fb = syn()
        F.add_fact(fb, 'equity_securities_fv', 'I@2026-06-30', '2026-06-30', 70 * M, 'sec:cf', 'as_reported', _plus('2026-06-30', 35))
        r = dataqa.validate(fb)['resolved']
        self.assertEqual(r['equity_securities_fv']['value'], 70 * M)
        self.assertEqual(r['cash']['value'] + r['short_term_investments']['value'],
                         dataqa.validate(syn())['resolved']['cash']['value'] + dataqa.validate(syn())['resolved']['short_term_investments']['value'])
        self.assertEqual(dataqa.validate(syn())['resolved']['equity_securities_fv']['value'], 0.0)


class OpeningCheckWithListedStakesTests(unittest.TestCase):
    def test_listed_stakes_are_part_of_validated_liquid_assets(self):
        from fundamental_engine import review
        qa = {'resolved': {'cash': {'value': 22.4}, 'short_term_investments': {'value': 34.1}, 'long_term_investments': {'value': 0.0},
                           'equity_securities_fv': {'value': 42.8}, 'debt': {'value': 33.4}, 'shares_outstanding': {'value': 24.1}}}
        case = {'opening': {'unrestricted_cash': 22.4, 'marketable_investments': 76.9, 'debt': 33.4, 'shares': 24.1}}
        self.assertEqual(review._opening_check(qa, case, None)['status'], 'pass')
        case['opening']['marketable_investments'] = 90.0          # more than validated: still caught
        self.assertEqual(review._opening_check(qa, case, None)['status'], 'fail')


class DepreciationTagTests(unittest.TestCase):
    def test_plain_depreciation_tag_is_read_as_d_and_a(self):
        # SYNTHETIC payload shaped like Alphabet's: only us-gaap:Depreciation is tagged
        payload = {'cik': 1, 'facts': {'us-gaap': {'Depreciation': {'units': {'USD': [
            {'start': '2025-01-01', 'end': '2025-12-31', 'val': 21.1, 'filed': '2026-02-04', 'form': '10-K', 'accn': 'a1'}]}}}}}
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-20')
        F.from_sec_companyfacts(fb, payload, '2026-10-20')
        got = [f for f in fb['facts'] if f['metric'] == 'd_and_a']
        self.assertEqual([(f['period_end'], f['value']) for f in got], [('2025-12-31', 21.1)])

    def test_combined_tag_still_preferred_in_a_tie(self):
        rows = lambda v: [{'start': '2025-01-01', 'end': '2025-12-31', 'val': v, 'filed': '2026-02-04', 'form': '10-K', 'accn': 'a1'}]
        payload = {'cik': 1, 'facts': {'us-gaap': {'Depreciation': {'units': {'USD': rows(20.0)}},
                                                   'DepreciationDepletionAndAmortization': {'units': {'USD': rows(23.0)}}}}}
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-20')
        F.from_sec_companyfacts(fb, payload, '2026-10-20')
        self.assertEqual([f['value'] for f in fb['facts'] if f['metric'] == 'd_and_a'], [23.0])
