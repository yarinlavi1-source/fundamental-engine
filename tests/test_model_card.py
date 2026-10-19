"""model_card.build: business model, stage, funding, valuation-method choice and understanding checklist.

Fixtures: the real ADBE FactBook (tests/fixtures) and SYNTHETIC FactBooks built below (labelled, not real
companies). `analyze_stub` builds the contract-shaped analysis output (docs/MODULE_CONTRACTS.md,
analysis.analyze) by hand from the FactBook because analysis.py is written in parallel; when the real
analysis/dataqa modules are importable, IntegrationTests run them too.
"""
import json
import unittest
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from fundamental_engine import facts
from fundamental_engine import model_card as M
from fundamental_engine.valuation import ROUTES

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
FLOWS = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'sbc', 'd_and_a', 'buybacks',
         'dividends_paid', 'acquisitions')
INSTANTS = ('cash', 'short_term_investments', 'long_term_debt', 'long_term_debt_tagged', 'equity', 'goodwill',
            'deferred_revenue_current', 'deferred_revenue_noncurrent', 'shares_outstanding', 'total_assets')


def _m(value, formula, end):
    return {'value': value, 'unit': 'ratio', 'formula': formula, 'inputs': [], 'period': end, 'status': 'calculation'}


def analyze_stub(fb, ttm=None):
    """Hand-built stand-in for analysis.analyze (contract shape only; simple formulas)."""
    t = facts.annual_table(fb, FLOWS + INSTANTS)
    annual = {}
    for end, r in t.items():
        g = r.get
        dr = None if g('deferred_revenue_current') is None else g('deferred_revenue_current') + (g('deferred_revenue_noncurrent') or 0)
        annual[end] = {'revenue': g('revenue'), 'gross_profit': g('gross_profit'), 'operating_income': g('operating_income'),
                       'net_income': g('net_income'), 'cfo': g('cfo'), 'capex': g('capex'),
                       'fcf': g('cfo') - g('capex') if g('cfo') is not None and g('capex') is not None else None,
                       'sbc': g('sbc'), 'd_and_a': g('d_and_a'), 'buybacks': g('buybacks'), 'dividends': g('dividends_paid'),
                       'acquisitions': g('acquisitions'), 'cash': g('cash'), 'investments': g('short_term_investments'),
                       'debt': g('long_term_debt') or g('long_term_debt_tagged'), 'equity': g('equity'), 'goodwill': g('goodwill'),
                       'deferred_revenue': dr, 'shares_outstanding': g('shares_outstanding')}
    ends = sorted(annual)
    a = [annual[e] for e in ends]
    met = {}
    if a:
        last, end = a[-1], ends[-1]
        div = lambda x, y: x / y if x is not None and y else None
        rev = last['revenue']
        if len(a) >= 4 and a[-4]['revenue'] and rev:
            met['revenue_cagr_3y'] = _m((rev / a[-4]['revenue']) ** (1 / 3) - 1, 'CAGR', end)
            so = [x['shares_outstanding'] for x in (a[-4], last)]
            if all(so):
                met['share_count_cagr_3y'] = _m((so[1] / so[0]) ** (1 / 3) - 1, 'CAGR shares', end)
            fcf3 = [x['fcf'] for x in a[-3:]]
            if all(f is not None for f in fcf3) and sum(fcf3) > 0:
                met['buybacks_pct_fcf_3y'] = _m(sum(x['buybacks'] or 0 for x in a[-3:]) / sum(fcf3), 'buybacks/FCF', end)
            met['acquisition_spend_pct_revenue_3y'] = _m(sum(x['acquisitions'] or 0 for x in a[-3:]) / sum(x['revenue'] for x in a[-3:]),
                                                         'acq/revenue', end)
        if len(a) >= 2 and a[-2]['revenue'] and rev is not None:
            met['revenue_growth_last'] = _m(rev / a[-2]['revenue'] - 1, 'yoy', end)
        for name, num in (('gross_margin', 'gross_profit'), ('operating_margin', 'operating_income'), ('fcf_margin', 'fcf'),
                          ('capex_pct_revenue', 'capex'), ('deferred_revenue_pct_revenue', 'deferred_revenue'),
                          ('sbc_pct_revenue', 'sbc')):
            v = div(last[num], rev)
            if v is not None:
                met[name] = _m(v, num + '/revenue', end)
        if last['capex'] is not None and last['d_and_a']:
            met['capex_to_da'] = _m(last['capex'] / last['d_and_a'], 'capex/DA', end)
        ic = None if None in (last['equity'], last['cash']) else last['equity'] + (last['debt'] or 0) - last['cash']
        if last['operating_income'] is not None and ic and ic > 0:
            met['roic'] = _m(last['operating_income'] * .79 / ic, 'NOPAT/IC', end)
    return {'years': ends, 'annual': annual, 'ttm': ttm or {}, 'metrics': met, 'flags': []}


def synth_fb(company, years, as_of='2026-10-19', lag=60):
    """SYNTHETIC FactBook: years = [{'end': 'YYYY-MM-DD', metric: value, ...}] (flows and instants)."""
    fb = facts.new(dict({'ticker': 'SYN', 'name': 'Synthetic test company', 'currency': 'USD'}, **company), as_of)
    facts.add_source(fb, 'syn:10k', 'synthetic', 'synthetic-annual', 'synthetic', as_of + 'T00:00:00Z', note='SYNTHETIC')
    for y in years:
        end = y['end']
        avail = (date.fromisoformat(end) + timedelta(days=lag)).isoformat()
        for k, v in y.items():
            if k == 'end' or v is None:
                continue
            instant = k in facts.INSTANT_TAGS or k in ('total_liabilities',)
            facts.add_fact(fb, k, ('I@' + end) if instant else f'FY{end[:4]}', end, v, 'syn:10k', 'as_reported', avail)
    return fb


def series_years(n, start=2021, **cols):
    """cols: metric -> list of n values (None allowed)."""
    return [dict({'end': f'{start + i}-12-31'}, **{k: v[i] for k, v in cols.items()}) for i in range(n)]


def card(fb, inputs=None, ttm=None, qa=None):
    c = M.build(fb, analyze_stub(fb, ttm), qa, inputs)
    json.dumps(c, allow_nan=False)
    return c


def bank_fb(company=None):
    rev = [2.0e9, 2.1e9, 2.2e9, 2.3e9, 2.4e9]
    return synth_fb({'sector': 'Financials', 'industry': 'Regional Banks'} if company is None else company, series_years(
        5, revenue=rev, net_income=[r * .25 for r in rev], cfo=[r * .35 for r in rev], capex=[5e7] * 5,
        cash=[3e9] * 5, equity=[6e9, 6.3e9, 6.6e9, 6.9e9, 7.2e9], total_assets=[6e10] * 5, total_liabilities=[5.4e10] * 5,
        shares_outstanding=[1e8] * 5))


def fee_network_fb(sector, industry):
    """SYNTHETIC Visa-like payment network: 80% gross margin, 67% operating margin, low capex, lender-unlike balance sheet."""
    rev = [2.4e10, 2.9e10, 3.29e10, 3.58e10, 4.0e10]
    return synth_fb({'sector': sector, 'industry': industry}, series_years(
        5, revenue=rev, gross_profit=[r * .80 for r in rev], operating_income=[r * .67 for r in rev], net_income=[r * .53 for r in rev],
        cfo=[r * .55 for r in rev], capex=[r * .03 for r in rev], d_and_a=[r * .03 for r in rev], sbc=[r * .03 for r in rev],
        cash=[1.6e10] * 5, long_term_debt=[2.0e10] * 5, equity=[3.6e10, 3.5e10, 3.9e10, 3.9e10, 3.8e10], total_assets=[8.5e10] * 5,
        total_liabilities=[4.7e10] * 5, shares_outstanding=[2.1e9, 2.08e9, 2.05e9, 2.0e9, 1.96e9]))


def steady_fb(sector, industry, rev, om, gm, capex_pct):
    """SYNTHETIC operating company with the given revenue path, operating margins, gross margin and capex intensity."""
    n = len(rev)
    return synth_fb({'sector': sector, 'industry': industry}, series_years(
        n, revenue=rev, gross_profit=[r * gm for r in rev], operating_income=[r * m for r, m in zip(rev, om)],
        cfo=[r * (m + .08) for r, m in zip(rev, om)], capex=[r * capex_pct for r in rev], d_and_a=[r * .04 for r in rev],
        cash=[r * .3 for r in rev], equity=[r * .8 for r in rev], long_term_debt=[r * .2 for r in rev], total_assets=[r * 1.6 for r in rev],
        total_liabilities=[r * .8 for r in rev], shares_outstanding=[1e9] * n))


def ai_infra_fb(industry='Software—Infrastructure'):
    return synth_fb({'sector': 'Technology', 'industry': industry}, series_years(
        3, start=2023, revenue=[1e8, 3e8, 8e8], gross_profit=[3e7, 1.2e8, 3.6e8], operating_income=[-6e7, -1.5e8, -2.4e8],
        cfo=[-2e7, -5e7, 1e8], capex=[3e8, 9e8, 1.6e9], d_and_a=[2e7, 6e7, 1.5e8], cash=[2e8, 6e8, 4e8],
        short_term_investments=[0, 0, 1e8], equity=[5e8, 1.2e9, 2e9], shares_outstanding=[1e8, 1.2e8, 1.45e8]))


def industrial_fb(oi_margins, growth=.03, industry='Electrical Equipment', rev0=3e10, as_of='2026-10-19', start=2021):
    n = len(oi_margins)
    rev = [rev0 * (1 + growth) ** i for i in range(n)]
    return synth_fb({'sector': 'Industrials', 'industry': industry}, series_years(
        n, start=start, revenue=rev, gross_profit=[r * .3 for r in rev], operating_income=[r * m for r, m in zip(rev, oi_margins)],
        cfo=[r * (m + .08) for r, m in zip(rev, oi_margins)], capex=[r * .04 for r in rev], d_and_a=[r * .035 for r in rev],
        cash=[3e9] * n, equity=[2e10] * n, long_term_debt=[8e9] * n, shares_outstanding=[1e9] * n), as_of=as_of)


class AdobeTests(unittest.TestCase):
    def setUp(self):
        self.c = card(ADBE)

    def test_business_model_and_signature(self):
        bm = self.c['business_model']
        self.assertEqual(bm['primary'], 'subscription_software')
        self.assertEqual(bm['evidence'], 'text+financials')
        self.assertEqual(bm['secondary'], [])
        self.assertGreater(bm['signature']['gross_margin']['value'], .85)
        self.assertGreater(bm['signature']['deferred_revenue_pct_revenue']['value'], .25)
        self.assertLess(bm['signature']['capex_pct_revenue']['value'], .02)
        self.assertTrue(any('subscription economics' in r for r in bm['reasons']))

    def test_stage_capital_funding(self):
        c = self.c
        self.assertIn(c['stage']['value'], ('maturing', 'mature'))
        self.assertAlmostEqual(c['stage']['data']['revenue_growth_used']['value'], (23769 / 17606) ** (1 / 3) - 1, places=6)
        self.assertEqual(c['stage']['confidence'], 'normal')
        self.assertFalse(c['cyclical']['value'])
        self.assertEqual(c['capital_intensity']['value'], 'low')
        self.assertEqual(c['funding']['value'], 'self_funded')
        self.assertEqual(c['funding']['data']['basis'], 'operating_cash_flow')
        self.assertAlmostEqual(c['funding']['data']['fcf']['value'], 10031e6 - 179e6)
        self.assertEqual(c['playbooks'], ['software'])

    def test_valuation_route(self):
        v = self.c['valuation']
        self.assertEqual(v['method'], 'fcff_annual_path')
        self.assertEqual(v['archetype'], 'software')
        self.assertEqual(ROUTES[v['archetype']], 'operating')
        self.assertEqual(v['route'], 'operating')
        self.assertTrue(v['default_for_type'])
        self.assertIsNone(v['override_reason'])
        self.assertTrue(any(x.startswith('reverse_dcf') for x in v['cross_checks']))

    def test_drivers_understanding_dimensions(self):
        c = self.c
        tree = {d['driver']: d for d in c['driver_tree']}
        self.assertEqual(tree['gross_margin']['data_status'], 'available')
        self.assertEqual(tree['net_revenue_retention']['data_status'], 'missing')
        self.assertEqual(tree['sales_marketing_efficiency']['data_status'], 'available')   # S&M facts exist
        self.assertEqual(c['driver_coverage']['forecast_basis'], 'revenue_level')
        self.assertTrue(any('revenue-level' in n for n in c['notes']))
        und = {q['id']: q for q in c['understanding']}
        self.assertEqual(und['who_funds_growth']['status'], 'evidenced')
        self.assertEqual(und['who_funds_growth']['source'], 'computed')
        self.assertIn('fmp:as_reported_cashflow_annual', und['who_funds_growth']['evidence_ids'])
        self.assertEqual(und['why_customers_pay']['status'], 'open')
        self.assertEqual(c['completeness']['status'], 'partial')
        self.assertEqual(c['dimensions']['pricing_power']['assessment'], 'strong')
        self.assertEqual(c['dimensions']['pricing_power']['source'], 'computed')
        self.assertEqual(c['dimensions']['customer_concentration']['assessment'], 'unknown')
        self.assertEqual(c['dimensions']['capital_allocation']['source'], 'computed')
        self.assertEqual(set(c['dimensions']), set(M.DIMENSIONS))

    def test_pure_and_deterministic(self):
        fb = deepcopy(ADBE)
        before = facts.fingerprint(fb)
        inputs = {'assessments': {'moat': {'assessment': 'strong', 'evidence_ids': ['fmp:as_reported_income_annual'], 'note': 'x'}}}
        frozen = deepcopy(inputs)
        a, b = card(fb, inputs), card(fb, inputs)
        self.assertEqual(a, b)
        self.assertEqual(facts.fingerprint(fb), before)
        self.assertEqual(inputs, frozen)
        self.assertEqual(a['dimensions']['moat']['source'], 'analyst')
        self.assertEqual(a['dimensions']['moat']['evidence_status'], 'evidenced')


class BankTests(unittest.TestCase):
    def test_bank_residual_income(self):
        c = card(bank_fb())
        self.assertEqual(c['business_model']['primary'], 'financials')
        self.assertEqual(c['valuation']['method'], 'residual_income')
        self.assertEqual(c['valuation']['archetype'], 'financials')
        self.assertEqual(c['valuation']['route'], 'residual_income')
        self.assertEqual(c['playbooks'], ['financials'])
        self.assertIn('loans_deposits', [d['driver'] for d in c['driver_tree']])

    def test_bank_never_fcff_even_with_overrides(self):
        ov = {'method_override': {'method': 'fcff_annual_path', 'reason': 'analyst prefers DCF'}}
        c = card(bank_fb(), ov)
        self.assertEqual(c['valuation']['method'], 'residual_income')
        self.assertEqual(c['valuation']['override_rejected']['method'], 'fcff_annual_path')
        both = dict(ov, business_model={'primary': 'subscription_software', 'secondary': [], 'reason': 'vendor label wrong'})
        for inputs in (both, {'business_model': {'primary': 'platform_marketplace', 'reason': 'fee platform'}}):
            v = card(bank_fb(), inputs)['valuation']
            self.assertNotIn(v['method'], M.OPERATING_METHODS)
            self.assertNotEqual(v['route'], 'operating')
        # GICS-style and vendor-style labels
        for ind in ('Banks—Regional', 'Diversified Banks', 'Insurance—Property & Casualty', 'Mortgage REITs'):
            c = card(bank_fb({'sector': 'Financial Services', 'industry': ind}))
            self.assertEqual(c['valuation']['method'], 'residual_income', ind)

    def test_unlabelled_balance_sheet_lender_not_forced_into_fcff(self):
        c = card(bank_fb({}))
        self.assertTrue(c['business_model']['flags']['possible_financial'])
        self.assertEqual(c['valuation']['method'], 'not_reliably_valuable')
        self.assertEqual(c['valuation']['intended_method'], 'residual_income')

    def test_payment_network_is_platform(self):
        c = card(bank_fb({'sector': 'Financials', 'industry': 'Transaction & Payment Processing Services'}))
        self.assertEqual(c['business_model']['primary'], 'platform_marketplace')

    def test_fee_networks_and_exchanges_get_operating_routes(self):
        for ind in ('Transaction & Payment Processing Services', 'Financial Exchanges & Data'):
            c = card(fee_network_fb('Financials', ind))
            bm, v = c['business_model'], c['valuation']
            self.assertEqual(bm['primary'], 'platform_marketplace', ind)
            self.assertNotIn('financials', bm['secondary'])                    # the sector is only a fallback
            self.assertFalse(bm['flags']['text_financials'])
            self.assertIn(v['method'], M.OPERATING_METHODS, ind)
        # vendor label lists lenders and networks together: conservative, but a reasoned override clears it
        fb = fee_network_fb('Financial Services', 'Financial - Credit Services')
        self.assertEqual(card(fb)['valuation']['method'], 'residual_income')
        c = card(fb, {'business_model': {'primary': 'platform_marketplace', 'reason': 'fee network: no loans, deposits or float (10-K)'},
                      'method_override': {'method': 'fcff_annual_path', 'reason': 'asset-light fee network'}})
        self.assertFalse(c['business_model']['flags']['text_financials'])
        self.assertTrue(c['business_model']['computed']['text_financials'])
        self.assertNotIn('financials', c['business_model']['secondary'])
        self.assertEqual(c['valuation']['method'], 'fcff_annual_path')
        self.assertNotIn('override_rejected', c['valuation'])


class GrowthInfrastructureTests(unittest.TestCase):
    def test_loss_making_ai_infrastructure_grower(self):
        c = card(ai_infra_fb())
        bm = c['business_model']
        self.assertEqual(bm['primary'], 'capital_intensive_infrastructure')
        self.assertIn('subscription_software', bm['secondary'])          # the label is kept, economics decide primary
        self.assertIn(c['stage']['value'], ('early', 'growth'))
        self.assertTrue(c['stage']['evidence_needed'])
        self.assertEqual(c['capital_intensity']['value'], 'high')
        self.assertEqual(c['funding']['value'], 'needs_external')
        self.assertAlmostEqual(c['funding']['data']['runway_years']['value'], 5e8 / 1.5e9)
        v = c['valuation']
        self.assertEqual(v['method'], 'scenario_survival_fcff')
        self.assertEqual(v['archetype'], 'infrastructure')
        self.assertEqual(v['confidence'], 'lower')
        self.assertTrue(any('funding' in r for r in v['requirements']))
        self.assertIn('infrastructure', c['playbooks'])
        self.assertIn('software', c['playbooks'])
        drivers = [d['driver'] for d in c['driver_tree']]
        for d in ('installed_capacity', 'utilization', 'price_per_unit', 'capex_per_unit', 'funding'):
            self.assertIn(d, drivers)
        und = {q['id']: q for q in c['understanding']}
        self.assertEqual(und['who_funds_growth']['status'], 'open')
        self.assertIn('years of burn', und['who_funds_growth']['computed_hint'])
        self.assertIn('funding', c['research_focus']['packets'])

    def test_gics_infrastructure_label(self):
        c = card(ai_infra_fb('Internet Services & Infrastructure'))
        self.assertEqual(c['business_model']['primary'], 'capital_intensive_infrastructure')
        self.assertEqual(c['business_model']['evidence'], 'text+financials')

    def test_profitable_grower_gets_fade_path(self):
        rev = [1e9, 1.35e9, 1.8e9, 2.4e9]
        fb = synth_fb({'sector': 'Information Technology', 'industry': 'Application Software'}, series_years(
            4, revenue=rev, gross_profit=[r * .8 for r in rev], operating_income=[r * .12 for r in rev],
            cfo=[r * .25 for r in rev], capex=[r * .03 for r in rev], cash=[2e9] * 4, equity=[3e9] * 4,
            deferred_revenue_current=[r * .3 for r in rev], shares_outstanding=[1e8] * 4))
        c = card(fb)
        self.assertEqual(c['stage']['value'], 'growth')
        self.assertEqual(c['valuation']['method'], 'fcff_annual_path')
        self.assertTrue(any('fade' in r for r in c['valuation']['requirements']))
        self.assertTrue(any(x.startswith('what_must_be_true') for x in c['valuation']['cross_checks']))


class StageTests(unittest.TestCase):
    def test_large_company_one_loss_year_is_not_early(self):
        c = card(industrial_fb([.12, .12, .11, .12, -.03]))
        self.assertNotIn(c['stage']['value'], ('early', 'growth'))
        self.assertEqual(c['stage']['value'], 'mature')
        self.assertTrue(any('one loss year' in r for r in c['stage']['reasons']))
        # profile.classify (kept as a side-by-side cross-check) still shows its known defect
        self.assertEqual(c['stage']['cross_check']['lifecycle'], 'young_growth')
        self.assertEqual(c['valuation']['method'], 'fcff_annual_path')

    def test_two_loss_years_or_falling_revenue_is_turnaround(self):
        self.assertEqual(card(industrial_fb([.12, .12, .11, -.02, -.03]))['stage']['value'], 'turnaround')
        self.assertEqual(card(industrial_fb([.12, .12, .11, .1, -.03], growth=-.08))['stage']['value'], 'turnaround')

    def test_smaller_profitable_history_with_loss_is_not_early(self):
        c = card(industrial_fb([.1, .1, .1, -.05], growth=.2, rev0=2e8))
        self.assertNotEqual(c['stage']['value'], 'early')

    def test_sub_annual_data_does_not_relabel_stage(self):
        fb = industrial_fb([.12, .12, .12, .12, .12], growth=.04)
        base = card(fb)['stage']['value']
        ttm = {'period_end': '2026-06-30', 'method': 'quarters', 'revenue': 5e10, 'operating_income': -1e10}
        c = card(fb, ttm=ttm)
        self.assertEqual(c['stage']['value'], base)
        self.assertTrue(any('not relabelled' in r for r in c['stage']['reasons']))
        self.assertEqual(c['stage']['data']['ttm_watch']['period_end'], '2026-06-30')

    def test_stale_and_missing_latest_data_are_stated(self):
        fb = industrial_fb([.12, .12, .12, .12], as_of='2026-10-19', start=2020)       # last FY 2023-12-31
        c = card(fb)
        self.assertEqual(c['stage']['confidence'], 'low')
        self.assertTrue(any('newer annual report is probably missing' in r for r in c['stage']['reasons']))
        self.assertTrue(any(m.startswith('stage:') for m in c['completeness']['missing']))
        fb = industrial_fb([.12, .12, .12, .12])
        an = analyze_stub(fb)
        an['annual'][max(an['annual'])]['operating_income'] = None
        c = M.build(fb, an, None)
        self.assertTrue(any('operating income missing' in r and '(older year)' in r for r in c['stage']['reasons']))

    def test_latest_year_without_revenue_is_stated(self):
        fb = industrial_fb([.12, .12, .12, .12])
        an = analyze_stub(fb)
        an['annual']['2025-12-31'] = {'revenue': None, 'cash': 1e9}
        c = M.build(fb, an, None)
        self.assertTrue(any('2025-12-31 has no revenue' in r for r in c['stage']['reasons']))
        self.assertEqual(c['stage']['data']['latest_fiscal_year_end'], '2024-12-31')

    def test_non_usd_does_not_apply_usd_scale(self):
        n = 5
        rev = [3e12 * 1.03 ** i for i in range(n)]
        fb = synth_fb({'sector': 'Industrials', 'industry': 'Electrical Equipment', 'currency': 'JPY'}, series_years(
            n, revenue=rev, operating_income=[r * m for r, m in zip(rev, [.1, .1, .1, .1, -.02])], cfo=[r * .12 for r in rev],
            capex=[r * .04 for r in rev], cash=[5e11] * n, equity=[2e12] * n))
        c = card(fb)
        self.assertTrue(any('defined in USD' in r for r in c['stage']['reasons']))
        self.assertEqual(c['stage']['value'], 'mature')

    def test_stage_override(self):
        fb = industrial_fb([.12] * 5)
        c = card(fb, {'stage': {'value': 'decline', 'reason': 'largest customer lost in Q3 (8-K)'}})
        self.assertEqual(c['stage']['value'], 'decline')
        self.assertEqual(c['stage']['computed'], 'mature')
        self.assertEqual(c['stage']['override']['reason'], 'largest customer lost in Q3 (8-K)')


class OverrideTests(unittest.TestCase):
    def test_overrides_without_reason_rejected(self):
        for inputs in ({'business_model': {'primary': 'platform_marketplace'}},
                       {'business_model': {'primary': 'platform_marketplace', 'reason': '  '}},
                       {'stage': {'value': 'growth'}},
                       {'method_override': {'method': 'normalized_cycle_fcff'}},
                       {'business_model': {'primary': 'not_a_type', 'reason': 'x'}},
                       {'method_override': {'method': 'magic_multiple', 'reason': 'x'}},
                       {'method_override': {'method': 'residual_income', 'archetype': 'software', 'reason': 'x'}},
                       {'assessments': {'moat': {'assessment': 'excellent'}}},
                       {'assessments': {'vibes': {'assessment': 'strong'}}},
                       {'understanding': {'why_customers_love_it': {'answer': 'x'}}}):
            with self.assertRaises(ValueError, msg=str(inputs)):
                M.build(ADBE, analyze_stub(ADBE), None, inputs)

    def test_overrides_with_reason_are_kept_next_to_computed(self):
        c = card(ADBE, {'business_model': {'primary': 'platform_marketplace', 'reason': 'marketplace for creative assets'},
                        'method_override': {'method': 'normalized_cycle_fcff', 'reason': 'advertising-linked demand cycle'}})
        bm, v = c['business_model'], c['valuation']
        self.assertEqual(bm['primary'], 'platform_marketplace')
        self.assertEqual(bm['computed']['primary'], 'subscription_software')
        self.assertIn('subscription_software', bm['secondary'])
        self.assertEqual(v['method'], 'normalized_cycle_fcff')
        self.assertFalse(v['default_for_type'])
        self.assertEqual(v['override_reason'], 'advertising-linked demand cycle')
        self.assertEqual(v['computed']['method'], 'fcff_annual_path')
        self.assertEqual(ROUTES[v['archetype']], 'operating')


class UnderstandingTests(unittest.TestCase):
    def fb(self):
        fb = deepcopy(ADBE)
        facts.add_qualitative(fb, 'q:cc', 'customers', 'Creative Cloud subscriptions are the main revenue source (10-K).',
                              'reported', ['fmp:as_reported_income_annual'], '2026-01-15')
        fb['qualitative'].append({'id': 'q:future', 'topic': 'retention', 'statement': 'dated after as_of', 'kind': 'reported',
                                  'source_ids': ['ib:snapshot'], 'as_of': '2026-12-01'})      # look-ahead attempt
        return fb

    def test_statuses(self):
        u = {'why_customers_pay': {'answer': 'workflow standard for creative professionals', 'evidence_ids': ['q:cc']},
             'why_they_dont_switch': {'answer': 'file formats and skills lock-in'},
             'what_proves_thesis_wrong': {'answer': 'net new ARR declines two years', 'evidence_ids': ['nope']},
             'what_must_happen_to_succeed': {'answer': 'AI features monetized', 'evidence_ids': ['q:future']},
             'what_could_cause_failure': {'answer': 'generative tools commoditize editing', 'evidence_ids': ['ib:snapshot']}}
        c = card(self.fb(), {'understanding': u})
        q = {x['id']: x for x in c['understanding']}
        self.assertEqual(q['why_customers_pay']['status'], 'evidenced')
        self.assertEqual(q['why_they_dont_switch']['status'], 'asserted')
        self.assertEqual(q['what_proves_thesis_wrong']['status'], 'asserted')
        self.assertIn('nope', q['what_proves_thesis_wrong']['note'])
        self.assertEqual(q['what_must_happen_to_succeed']['status'], 'asserted')      # future-dated item is not evidence
        self.assertEqual(q['what_could_cause_failure']['status'], 'evidenced')
        self.assertEqual(q['customer_concentration']['status'], 'open')
        self.assertFalse(q['customer_concentration']['critical'])
        self.assertEqual(c['completeness']['status'], 'partial')
        for qid in ('why_they_dont_switch', 'what_proves_thesis_wrong', 'what_must_happen_to_succeed'):
            u[qid]['evidence_ids'] = ['q:cc']
        c = card(self.fb(), {'understanding': u})
        self.assertEqual(c['completeness']['status'], 'complete')
        # driver availability through a qualitative topic; the future-dated item is ignored
        tree = {d['driver']: d for d in c['driver_tree']}
        self.assertEqual(tree['customers']['data_status'], 'available')
        self.assertEqual(tree['net_revenue_retention']['data_status'], 'missing')

    def test_evidence_published_after_as_of_is_not_evidence(self):
        rev = [1e9, 1.2e9, 1.4e9, 1.6e9]
        fb = synth_fb({'sector': 'Information Technology', 'industry': 'Application Software'}, series_years(
            4, revenue=rev, gross_profit=[r * .8 for r in rev], operating_income=[r * .2 for r in rev], cfo=[r * .25 for r in rev],
            capex=[r * .02 for r in rev], cash=[5e8] * 4, equity=[1e9] * 4, shares_outstanding=[1e8] * 4), as_of='2025-12-01')
        facts.add_qualitative_bundle(fb, {'retrieved_at': '2026-10-19T00:00:00Z',
            'sources': [{'id': 'q3_call', 'kind': 'issuer_release', 'title': 'Q3 FY2026 call', 'published_at': '2026-09-15'}],
            'items': [{'id': 'later', 'topic': 'retention', 'statement': 'NRR 120%', 'kind': 'reported', 'source_ids': ['q3_call'],
                       'as_of': '2026-09-15'}]})
        c = card(fb, {'understanding': {'why_they_dont_switch': {'answer': 'NRR 120%', 'evidence_ids': ['web:q3_call']}},
                      'assessments': {'retention': {'assessment': 'strong', 'evidence_ids': ['web:q3_call']}}})
        q = next(x for x in c['understanding'] if x['id'] == 'why_they_dont_switch')
        self.assertEqual(q['status'], 'asserted')
        self.assertIn('web:q3_call', q['note'])
        self.assertEqual(c['dimensions']['retention']['evidence_status'], 'asserted')
        self.assertEqual(c['dimensions']['retention']['unknown_evidence_ids'], ['web:q3_call'])

    def test_insufficient_without_business_model_evidence(self):
        rev = [1e8, 1.1e8, 1.2e8, 1.3e8]
        fb = synth_fb({}, series_years(4, revenue=rev, gross_profit=[r * .4 for r in rev], operating_income=[r * .1 for r in rev],
                                      cfo=[r * .12 for r in rev], capex=[r * .06 for r in rev], cash=[5e7] * 4))
        c = card(fb)
        self.assertEqual(c['business_model']['primary'], 'unclassified')
        self.assertEqual(c['business_model']['evidence'], 'none')
        self.assertEqual(c['completeness']['status'], 'insufficient')
        self.assertEqual(c['valuation']['archetype'], 'nonfinancial')

    def test_kpi_fact_lookahead_is_ignored(self):
        fb = deepcopy(ADBE)
        facts.add_fact(fb, 'net_revenue_retention', 'FY2025', '2025-11-28', 1.1, 'fmp:as_reported_income_annual', 'as_reported',
                       '2026-12-01')
        facts.add_fact(fb, 'arr', 'FY2025', '2025-11-28', 2.5e10, 'fmp:as_reported_income_annual', 'as_reported', '2026-01-20')
        tree = {d['driver']: d for d in card(fb)['driver_tree']}
        self.assertEqual(tree['net_revenue_retention']['data_status'], 'missing')
        self.assertEqual(tree['revenue_per_customer']['data_status'], 'available')


class OtherTypesTests(unittest.TestCase):
    def test_cyclical_commodity(self):
        rev = [1e10, 1.4e10, 8e9, 1.2e10, 1.5e10]
        om = [.15, .3, .02, .2, .32]
        fb = synth_fb({'sector': 'Materials', 'industry': 'Copper'}, series_years(
            5, revenue=rev, operating_income=[r * m for r, m in zip(rev, om)], cfo=[r * (m + .05) for r, m in zip(rev, om)],
            capex=[r * .12 for r in rev], d_and_a=[r * .08 for r in rev], cash=[2e9] * 5, equity=[2e10] * 5))
        c = card(fb)
        self.assertEqual(c['business_model']['primary'], 'commodity')
        self.assertTrue(c['cyclical']['value'])
        self.assertAlmostEqual(c['cyclical']['normalized_operating_margin']['value'], sum(om) / 5)
        self.assertEqual(c['cyclical']['position'], 'peak')
        self.assertEqual(c['valuation']['method'], 'normalized_cycle_fcff')
        self.assertEqual(c['valuation']['archetype'], 'commodity')

    def test_industry_cyclicality_is_a_flag_unless_history_or_analyst_shows_a_cycle(self):
        rev = [2.74e10, 3.32e10, 3.58e10, 3.95e10, 4.35e10]
        fb = steady_fb('Information Technology', 'Semiconductors', rev, [.40, .42, .41, .42, .43], .70, .02)   # Broadcom-like
        c = card(fb)
        cy, v = c['cyclical'], c['valuation']
        self.assertEqual((cy['value'], cy['industry_flag'], cy['data_shows_cycle'], cy['normalize']), (True, True, False, False))
        self.assertEqual(v['method'], 'fcff_annual_path')
        self.assertTrue(any('not normalized' in x for x in v['limitations']))
        c = card(fb, {'cyclical': {'value': True, 'reason': 'memory pricing cycle visible in 2019 and 2023 segment data'}})
        self.assertEqual((c['cyclical']['basis'], c['valuation']['method']), ('analyst', 'normalized_cycle_fcff'))
        with self.assertRaises(ValueError):
            card(fb, {'cyclical': {'value': True}})
        # a booking platform labelled with hotels and cruise lines: platform signature, 'hotel' does not normalize it
        rev = [1.0e10, 1.25e10, 1.5e10, 1.8e10, 2.1e10]
        c = card(steady_fb('Consumer Discretionary', 'Hotels, Resorts & Cruise Lines', rev, [.2, .25, .27, .28, .3], .82, .02))
        self.assertEqual((c['business_model']['primary'], c['business_model']['secondary']), ('platform_marketplace', ['consumer_brand']))
        self.assertEqual(c['valuation']['method'], 'fcff_annual_path')

    def test_vendor_dash_labels_and_mixed_labels(self):
        tt = lambda sec, ind: (M.text_type({'sector': sec, 'industry': ind}) or {}).get('type')
        self.assertEqual(tt('Consumer Cyclical', 'Auto - Manufacturers'), 'hardware_manufacturing')
        self.assertEqual(tt('Healthcare', 'Medical - Devices'), 'hardware_manufacturing')
        self.assertEqual(tt('Financials', 'Multi-Sector Holdings'), 'conglomerate')
        self.assertEqual(tt('Real Estate', 'REIT - Mortgage'), 'financials')
        rev = [4.70e11, 5.14e11, 5.75e11, 6.38e11, 7.17e11]                  # Amazon-like: retail margins, heavy capex
        c = card(steady_fb('Consumer Discretionary', 'Broadline Retail', rev, [.053, .024, .064, .108, .11], .48, .17))
        self.assertEqual((c['business_model']['primary'], c['business_model']['secondary']), ('consumer_brand', ['platform_marketplace']))
        self.assertTrue(any('hybrid' in x for x in c['valuation']['limitations']))

    def test_specialist_routes_need_their_data(self):
        rev = [5e8] * 4
        base = dict(revenue=rev, operating_income=[1.5e8] * 4, cfo=[2.5e8] * 4, capex=[3e8] * 4, cash=[1e8] * 4, equity=[4e9] * 4)
        reit = synth_fb({'sector': 'Real Estate', 'industry': 'REIT—Industrial'}, series_years(4, **base))
        v = card(reit)['valuation']
        self.assertEqual((v['method'], v['intended_method'], v['archetype']), ('not_reliably_valuable', 'nav', 'reit'))
        facts.add_qualitative(reit, 'q:noi', 'NOI', 'Same-property NOI by asset (supplement).', 'reported', ['syn:10k'], '2026-03-01')
        self.assertEqual(card(reit)['valuation']['method'], 'nav')
        bio = synth_fb({'sector': 'Health Care', 'industry': 'Biotechnology'}, series_years(
            3, start=2023, revenue=[0, 0, 2e6], operating_income=[-1e8, -1.4e8, -1.8e8], cfo=[-9e7, -1.3e8, -1.6e8],
            capex=[1e6] * 3, cash=[6e8, 4.5e8, 3e8], rd=[8e7, 1.1e8, 1.4e8]))
        c = card(bio)
        self.assertEqual(c['business_model']['primary'], 'pre_revenue_biotech')
        self.assertEqual(c['stage']['value'], 'early')
        self.assertEqual((c['valuation']['method'], c['valuation']['intended_method']), ('not_reliably_valuable', 'rnpv'))
        facts.add_qualitative(bio, 'q:pipe', 'pipeline', 'Lead asset in phase 2 (10-K).', 'reported', ['syn:10k'], '2026-03-01')
        v = card(bio)['valuation']
        self.assertEqual((v['method'], v['archetype'], v['route']), ('rnpv', 'biotech', 'rnpv'))
        cong = synth_fb({'sector': 'Industrials', 'industry': 'Industrial Conglomerates'}, series_years(4, **base))
        self.assertEqual(card(cong)['valuation']['intended_method'], 'sotp')

    def test_commercial_pharma_is_not_forced_into_pre_revenue(self):
        rev = [9e9, 9.5e9, 1e10, 1.05e10]
        fb = synth_fb({'sector': 'Health Care', 'industry': 'Drug Manufacturers—General'}, series_years(
            4, revenue=rev, gross_profit=[r * .75 for r in rev], operating_income=[r * .3 for r in rev], cfo=[r * .3 for r in rev],
            capex=[r * .04 for r in rev], rd=[r * .2 for r in rev], cash=[5e9] * 4, equity=[2e10] * 4))
        c = card(fb)
        self.assertNotEqual(c['business_model']['primary'], 'pre_revenue_biotech')
        self.assertIn('biotech', c['playbooks'])
        self.assertEqual(c['valuation']['method'], 'fcff_annual_path')
        self.assertTrue(any('not forced' in r for r in c['business_model']['reasons']))

    def test_empty_analysis_is_unknown_not_crash(self):
        c = M.build(ADBE, {}, None)
        json.dumps(c, allow_nan=False)
        self.assertEqual(c['stage']['confidence'], 'none')
        self.assertEqual(c['valuation']['method'], 'not_reliably_valuable')
        self.assertEqual(c['funding']['value'], 'unknown')
        self.assertEqual(c['capital_intensity']['value'], 'unknown')

    def test_dataqa_resolved_liquidity_and_status(self):
        qa = {'status': 'blocked', 'blocking': ['debt'], 'limited': [],
              'resolved': {'cash': {'value': 4e9, 'as_of': '2025-11-28', 'source_ids': ['fmp:as_reported_balance_annual'], 'status': 'reported'},
                           'short_term_investments': {'value': 1e9, 'as_of': '2025-11-28', 'source_ids': ['fmp:as_reported_balance_annual']}}}
        c = card(ADBE, qa=qa)
        self.assertEqual(c['funding']['data']['liquidity']['value'], 5e9)
        self.assertIn('dataqa.resolved', c['funding']['data']['liquidity']['formula'])
        self.assertIn('fmp:as_reported_balance_annual', c['funding']['data']['evidence_ids'])
        self.assertTrue(any('blocked' in x for x in c['valuation']['limitations']))
        self.assertTrue(any('blocked' in n for n in c['notes']))

    def test_tables_are_consistent(self):
        playbooks = {p['id'] for p in json.loads((ROOT / 'fundamental_engine/brain/catalog.json').read_text())['packets']}
        for t in M.TYPES + (M.UNCLASSIFIED,):
            self.assertIn(ROUTES[M.ARCHETYPE[t]], {'operating', 'residual_income', 'nav', 'rnpv', 'sotp'})
            self.assertTrue(set(M.PLAYBOOKS[t]) <= playbooks, t)
            self.assertTrue(3 <= len(M.DRIVERS[t]) <= 6, t)
        for m, a in M.METHOD_ARCHETYPE.items():
            self.assertEqual(ROUTES[a], M.METHOD_ROUTE[m])


try:
    from fundamental_engine import analysis as _analysis, dataqa as _dataqa
except ImportError:      # written in parallel; the integrator's run exercises this
    _analysis = _dataqa = None


@unittest.skipUnless(_analysis and _dataqa, 'analysis.py / dataqa.py not available yet')
class IntegrationTests(unittest.TestCase):
    def test_adbe_with_real_analysis(self):
        qa = _dataqa.validate(ADBE)
        c = M.build(ADBE, _analysis.analyze(ADBE, qa), qa)
        json.dumps(c, allow_nan=False)
        self.assertEqual(c['business_model']['primary'], 'subscription_software')
        self.assertIn(c['stage']['value'], ('maturing', 'mature'))
        self.assertEqual(c['valuation']['method'], 'fcff_annual_path')
        self.assertEqual(c['funding']['value'], 'self_funded')


if __name__ == '__main__':
    unittest.main()
