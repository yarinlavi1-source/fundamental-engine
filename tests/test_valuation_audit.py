import json
import unittest
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from fundamental_engine.valuation import value_company
from fundamental_engine.plain import plain_verdict
from fundamental_engine.connectors import from_alpha_vantage
from fundamental_engine.growth import payoff, momentum, multiples_view
from fundamental_engine.mcp import Server
from fundamental_engine.valuation_report import render_valuation
ROOT = Path(__file__).resolve().parents[1]

def fixture(live=False):
    c = json.loads((ROOT/'examples/valuation/audited_infrastructure.json').read_text())
    if live:
        # Test double for external evidence; deliberately not a real-company input.
        c['is_demo'] = False
        c['sources'][0]['kind'] = 'filing'
    return c

def base(result):
    return next(s for s in result['scenarios'] if s['name'] == 'base')

def codes(c):
    return {x['code'] for x in value_company(c)['underwriting_audit']['issues']}

class OpeningAuditTests(unittest.TestCase):
    def test_complete_contract_and_demo_boundary(self):
        d = value_company(fixture())['underwriting_audit']
        self.assertEqual(d['issues'], [])
        self.assertFalse(d['eligible_for_research_synthesis'])
        self.assertTrue(value_company(fixture(True))['underwriting_audit']['eligible_for_research_synthesis'])

    def test_missing_underwriting_keeps_math_not_conclusion(self):
        c = fixture(True); c.pop('underwriting'); r = value_company(c)
        self.assertEqual(r['status'], 'underwriting_required')
        self.assertGreater(r['annual_values'][0]['base'], 0)
        self.assertIn('טיוטת חישוב', render_valuation(r))

    def test_omitted_investments_detected(self):
        c = fixture(True); c['opening'].pop('marketable_investments')
        self.assertIn('opening_mismatch', codes(c))

    def test_weighted_shares_cannot_be_current(self):
        c = fixture(True)
        c['underwriting']['opening_reconciliation']['share_basis'] = 'weighted_average_diluted'
        self.assertIn('share_basis', codes(c))

    def test_stale_statement_requires_bridge_and_dates(self):
        c = fixture(True); rec = c['underwriting']['opening_reconciliation']
        rec['statement_as_of'] = '2026-06-30'
        self.assertIn('evidence_missing', codes(c))
        rec['bridge_review'] = {'assumption_ids': ['demo']}
        rec['bridge_events'] = [{'date': '2026-07-31', 'unrestricted_cash_delta': -20e6, 'assumption_ids': ['demo']}]
        self.assertIn('opening_mismatch', codes(c))
        c['opening']['unrestricted_cash'] -= 20e6
        self.assertNotIn('opening_mismatch', codes(c))
        rec['bridge_events'][0]['date'] = '2027-01-01'
        with self.assertRaisesRegex(ValueError, 'Bridge event'): value_company(c)

    def test_restricted_cash_not_subtracted_twice_or_added(self):
        c = fixture(True)
        self.assertNotIn('opening_mismatch', codes(c))
        c['opening']['unrestricted_cash'] += 12e6
        self.assertIn('opening_mismatch', codes(c))

    def test_investments_conserved_on_sale_no_double_value(self):
        c = fixture(); b = base(value_company(c))
        for s in c['scenarios']:
            for p in s['periods']: p['payout_fraction'] = 0
        b = base(value_company(c))
        for s in c['scenarios']: s['periods'][0]['investment_liquidation'] = 100e6
        a = base(value_company(c))
        self.assertAlmostEqual(a['terminal']['raw_equity_value'], b['terminal']['raw_equity_value'])
        self.assertEqual(a['terminal']['marketable_investments'], 0)
        self.assertAlmostEqual(a['terminal']['excess_cash'] - b['terminal']['excess_cash'], 100e6)
        c['scenarios'][0]['periods'][1]['investment_liquidation'] = 1
        with self.assertRaisesRegex(ValueError, 'liquidation'): value_company(c)

    def test_investment_assets_not_automatic_spendable_cash(self):
        c = fixture(); c['opening']['unrestricted_cash'] = 0
        c['opening']['marketable_investments'] = 10e9
        for s in c['scenarios']: s['periods'][0]['growth_capex'] = 1e9
        self.assertEqual(value_company(c)['status'], 'funding_blocked')
        for s in c['scenarios']: s['periods'][0]['investment_liquidation'] = 2e9
        self.assertNotEqual(value_company(c)['status'], 'funding_blocked')

    def test_attribution_cash_and_income_identity(self):
        c = fixture(); b = base(value_company(c))['forecast'][0]
        p = c['scenarios'][1]['periods'][0]
        p.update(investment_income_after_tax=3e6, noncontrolling_distributions=1e6)
        a = base(value_company(c))['forecast'][0]
        self.assertAlmostEqual(a['cash']+a['dividends']-b['cash']-b['dividends'], 2e6)

class EstimatesAndHorizonTests(unittest.TestCase):
    def comparison(self):
        c = fixture(True)
        r = base(value_company(c))['forecast'][1]
        e = {'id': 'next_year', 'metric': 'revenue', 'scenario': 'base',
             'period_start': r['start'], 'period_end': r['end'], 'currency': 'USD', 'unit': 'absolute',
             'as_of': c['as_of'], 'available_at': c['as_of'], 'external_basis': 'GAAP', 'model_basis': 'GAAP',
             'external_value': r['revenue'], 'assumption_ids': ['demo']}
        c['underwriting']['consensus_review'].update(status='compared', comparisons=[e])
        return c, e

    def test_comparable_value_computed_from_forecast(self):
        c, e = self.comparison(); a = value_company(c)['underwriting_audit']
        self.assertEqual(a['issues'], [])
        self.assertEqual(a['comparisons'][0]['relative_difference'], 0)
        e['external_value'] *= 2
        self.assertIn('evidence_missing', codes(c))
        e['difference_explanation'] = {'assumption_ids': ['demo']}
        self.assertNotIn('evidence_missing', codes(c)) # Disagreement is allowed when explained.

    def test_eps_gaap_adjusted_and_proxy_cannot_mix_silently(self):
        c, e = self.comparison(); e.update(metric='earnings_proxy_per_share', external_value=2.25, external_basis='adjusted')
        self.assertIn('eps_basis_mismatch', codes(c))
        e['basis_reconciliation'] = {'assumption_ids': ['demo'], 'adjustment_to_model_basis': 0}
        self.assertNotIn('eps_basis_mismatch', codes(c))
        self.assertIn('evidence_missing', codes(c)) # Large remaining economic difference needs explanation.

    def test_estimate_dates_years_currency_and_zero(self):
        c, e = self.comparison(); e['period_start'] = '2027-01-01'
        self.assertIn('estimate_period_mismatch', codes(c))
        c, e = self.comparison(); e['as_of'] = '2026-01-01'
        self.assertIn('stale_estimate', codes(c))
        c, e = self.comparison(); e['available_at'] = '2027-01-01'
        with self.assertRaisesRegex(ValueError, 'unavailable'): value_company(c)
        c, e = self.comparison(); e['currency'] = 'EUR'
        with self.assertRaisesRegex(ValueError, 'currency'): value_company(c)
        c, e = self.comparison(); e['external_value'] = 0
        self.assertIn('evidence_missing', codes(c))

    def test_terminal_cliff_requires_specific_review(self):
        c = fixture(True)
        for s in c['scenarios']:
            s['periods'][-1]['segments'][0]['annual_revenue_per_unit'] *= 2
            c['underwriting']['scenario_reviews'][s['name']].pop('transition_exception')
        r = value_company(c)
        self.assertTrue(any(t['growth_cliff'] for t in r['underwriting_audit']['terminal_transitions']))
        self.assertFalse(r['underwriting_audit']['eligible_for_research_synthesis'])

    def test_report_horizon_can_end_before_growth_forecast(self):
        c = fixture()
        for s in c['scenarios']:
            last = deepcopy(s['periods'][-1]); last.update(start='2030-12-31', end='2031-12-31', revenue_from_prepayments=0, debt_repayment=0)
            s['periods'].append(last)
        r = value_company(c)
        self.assertEqual(r['annual_values'][-1]['date'], '2030-12-31')
        self.assertTrue(all(t['terminal_date'] == '2031-12-31' for t in r['underwriting_audit']['terminal_transitions']))

    def test_material_conflict_and_unreviewed_inputs_block(self):
        c = fixture(True)
        c['underwriting']['material_issues'] = [{'id': 'eps', 'status': 'unresolved', 'description': 'Conflicting EPS definitions'}]
        self.assertIn('material_issue_unresolved', codes(c))
        c['assumptions'][0]['review_status'] = 'unreviewed'
        self.assertIn('unreviewed_assumption', codes(c))

class PublicationAndRegressionTests(unittest.TestCase):
    def plain(self):
        c = json.loads((ROOT/'examples/plain_demo.json').read_text())
        c.update(is_demo=False, valuation_case=fixture(True))
        return c

    def test_no_cheap_or_expensive_verdict_without_executed_review(self):
        c = self.plain(); c['research_status'] = 'ready_for_conditional_synthesis'
        for price in (.01, 100000):
            c['valuation_case']['quote']['price'] = price
            r = plain_verdict(c)
            self.assertEqual(r['price']['bucket'], 'review_required')
            self.assertEqual(r['bottom_line']['light'], '⚪')
            self.assertIn('טיוטה', r['text'])
            self.assertIsNone(r['valuation_lane']['payoff'])

    def test_dates_and_demo_identity_cannot_bypass_gate(self):
        c = self.plain(); c['valuation_case']['is_demo'] = True
        with self.assertRaisesRegex(ValueError, 'match'): plain_verdict(c)
        c = self.plain(); c['as_of'] = '2026-09-28'
        with self.assertRaisesRegex(ValueError, 'match'): plain_verdict(c)

    def test_axti_point_in_time_dilution_and_investment_snapshot(self):
        c = json.loads((ROOT/'examples/real/axti_plain_2026q2.json').read_text())
        r = plain_verdict(c)
        d = next(x for x in r['items'] if x['key'] == 'dilution')
        self.assertAlmostEqual(d['figure']['shares_increase_since_last_annual'], 65570000/55337000-1)
        self.assertLess(d['figure']['shares_increase_since_last_annual'], .20)
        self.assertEqual(c['balance']['short_term_investments']+c['balance']['long_term_investments'], 303631000)
        self.assertIsNone(r['valuation'])
        c['current_shares']['basis'] = 'weighted_average_diluted'
        with self.assertRaisesRegex(ValueError, 'point_in_time'): plain_verdict(c)

    def test_mapper_preserves_financial_definitions(self):
        inc = {'symbol':'X','annualReports':[{'fiscalDateEnding':'2025-12-31','reportedCurrency':'USD','totalRevenue':'100'}]}
        bal = {'symbol':'X','annualReports':[{'fiscalDateEnding':'2025-12-31','reportedCurrency':'USD',
            'commonStockSharesOutstanding':'10','cashAndShortTermInvestments':'50','cashAndCashEquivalentsAtCarryingValue':'30','shortTermInvestments':'20','longTermInvestments':'80'}]}
        r = from_alpha_vantage(inc,bal)['history'][0]
        self.assertEqual(r['shares_outstanding'],10); self.assertNotIn('shares_diluted',r)
        self.assertEqual(r['cash'],30); self.assertEqual(r['short_term_investments'],20)

    def test_complete_reproduction_and_tool(self):
        c = fixture(); r = value_company(c)
        self.assertEqual(r['reproduction_input'], c)
        self.assertEqual(sha256(json.dumps(c,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest(), r['input_sha256'])
        self.assertEqual(value_company(r['reproduction_input'])['annual_values'], r['annual_values'])
        a = Server(':memory:').call('valuation_audit',{'case':c})
        self.assertEqual(a['input_sha256'],r['input_sha256']); self.assertEqual(a['issues'],[])

    def test_minorities_not_flat_haircut_to_parent_proxy(self):
        self.assertEqual(multiples_view(value_company(fixture()),20,.145)['rows'], [])
        self.assertIsNone(payoff({'bear':1,'base':8,'bull':24},75))

    def test_estimate_revision_cannot_roll_fiscal_year_or_basis(self):
        c = {'as_of':'2026-09-28', 'estimate_revisions':[
            {'as_of':'2026-08-01','value':1,'source_ids':['x'],'metric':'eps','period_end':'2027-12-31','basis':'GAAP','unit':'per_share','currency':'USD'},
            {'as_of':'2026-09-01','value':2,'source_ids':['x'],'metric':'eps','period_end':'2028-12-31','basis':'GAAP','unit':'per_share','currency':'USD'}]}
        with self.assertRaisesRegex(ValueError,'target period'): momentum(c)

if __name__ == '__main__': unittest.main()

class DossierBindingTests(unittest.TestCase):
    def paired(self):
        # All data remains fictional; kinds mock live-source gating only.
        d = json.loads((ROOT/'examples/research_dossier_demo.json').read_text())
        v = fixture(True)
        d['as_of'] = v['as_of']; d['is_demo'] = False
        d['financial_input']['as_of'] = v['as_of']; d['financial_input']['is_demo'] = False
        for group in (d['sources'], d['financial_input']['sources']):
            for s in group: s['kind'] = 'filing'
        v['ticker'] = d['identity']['ticker']; v['company_id'] = d['identity']['company_id']
        src = d['sources'][-1]
        v['sources'] = [{'id':src['id'],'title':src['title'],'url':src['locator'],'kind':src['kind'],
                         'published_at':src['published_at'],'available_at':src['available_at']}]
        v['quote']['source_ids'] = [src['id']]; v['assumptions'][0]['source_ids'] = [src['id']]
        d['annual_valuation_input'] = deepcopy(v)
        d['candidate_conclusion'] = {'classification':'watchlist','valuation_basis':{'model':'annual_path','scenario':'base'}}
        p = json.loads((ROOT/'examples/plain_demo.json').read_text())
        p.update(is_demo=False,ticker=v['ticker'],valuation_case=v,research_dossier=d)
        return p

    def test_complete_live_contract_can_reach_price_comparison(self):
        p = self.paired(); r = plain_verdict(p)
        self.assertTrue(r['valuation']['priced_conclusion_ready'])
        self.assertNotEqual(r['price']['bucket'], 'review_required')

    def test_different_reviewed_model_cannot_be_reused(self):
        p = self.paired(); p['valuation_case']['quote']['price'] += 1
        with self.assertRaisesRegex(ValueError, 'identical valuation'): plain_verdict(p)

    def test_research_review_rejects_material_audit_issue_in_both_directions(self):
        from fundamental_engine.supervisor import review
        for classification, price in [('conditional_attractive',.01), ('not_attractive',100000)]:
            p = self.paired(); d = p['research_dossier']
            d['annual_valuation_input']['quote']['price'] = price
            d['annual_valuation_input'].pop('underwriting')
            d['candidate_conclusion'].update(classification=classification,rationale='Test both directions')
            r = review(d)
            self.assertFalse(r['gates']['priced_conclusion_ready'])
            self.assertEqual(r['candidate_conclusion']['effective_classification'],'unresolved')
