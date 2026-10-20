"""Regression tests for review-verified defects (one class per fix; synthetic data labelled).

Each test reproduces the reviewer's case: it failed (or passed the bad input) on the code
before the fix and pins the corrected behaviour.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.redteam import audit, FAILED, PASSED, LIMITED, UNVERIFIABLE
from fundamental_engine.supervisor import review
from fundamental_engine.plain import plain_verdict
from fundamental_engine.decision_summary import decision_summary, red_flag_gate
from fundamental_engine.drivers import build_case
from fundamental_engine.connectors import from_alpha_vantage
from fundamental_engine.history import history, quarters

ROOT = Path(__file__).resolve().parents[1]
def load(p): return json.loads((ROOT / p).read_text())
NBIS = load('examples/real/nbis_capital_2026-08-24.json')


def model_ok():
    return {'revenue_basis': 'recognized', 'backlog_treatment': 'probability_weighted', 'prepayments_treatment': 'working_capital_with_reversal',
            'margin_basis': 'ebit_after_sbc', 'sbc_treatment': 'expensed', 'capex_to_revenue_recent': 4.0, 'capex_linked_to_growth': True,
            'valuation_basis': 'fcff_dcf', 'funding_need': 20e9, 'financing_modeled': True, 'terminal_share': 0.7,
            'scenario_values': {'bear': 1, 'base': 2, 'bull': 3}}


def nbis(**reported):
    p = deepcopy(NBIS); p['model'] = model_ok(); p['reported'].update(reported)
    return p


def converts_list():
    return [{'id': x['id'], 'principal': x['principal']} for x in NBIS['capital_structure']['debt'] if x['kind'] == 'convertible']


class RedteamVerdictPrecedence(unittest.TestCase):
    """Fix 1: a major failure must never upgrade a critical UNVERIFIABLE to usable."""
    def test_major_failure_plus_critical_unverifiable_is_not_usable(self):
        p = nbis(); p['reported'].pop('cash_and_equivalents')
        p['model']['unit_economics'] = {'revenue': 100, 'active_units': 1, 'revenue_per_unit': 10}   # major FAILED
        r = audit(p)
        self.assertEqual(r['verdict'], UNVERIFIABLE)
        self.assertFalse(r['valuation_usable'])
        self.assertEqual(r['major_failures'], ['unit_coherence'])

    def test_major_failure_alone_is_limited_and_listed(self):
        p = nbis(statement_as_of='2026-06-30')
        p['model']['unit_economics'] = {'revenue': 100, 'active_units': 1, 'revenue_per_unit': 10}
        r = audit(p)
        self.assertEqual(r['verdict'], LIMITED)
        self.assertIn('unit_coherence', r['limitations'])

    def test_major_unverifiable_lowers_passed(self):
        self.assertEqual(audit(nbis(statement_as_of='2026-06-30'))['verdict'], PASSED)
        p = nbis(statement_as_of='2026-06-30'); p['model'].pop('sbc_treatment')
        r = audit(p)
        self.assertEqual(r['verdict'], LIMITED)
        self.assertIn('sbc', r['unverified'])


class RedteamEventWindow(unittest.TestCase):
    """Fix 2: only events in (statement date, as_of] adjust expected figures."""
    def test_lookahead_event_is_ignored_not_reconciled(self):
        p = nbis(statement_as_of='2026-06-30')
        p['events_after_statement'].append({'date': '2026-09-30', 'type': 'equity_raise', 'cash_delta': 1e9, 'source_id': 'future'})
        p['capital_structure']['cash'].append({'id': 'future_cash', 'kind': 'unrestricted', 'amount': 1e9, 'source_id': 'future'})
        r = audit(p)
        self.assertIn('cash_reconciles', r['critical_failures'])
        window = next(c for c in r['checks'] if c['code'] == 'events_window')
        self.assertEqual(window['evidence']['ignored'][0]['reason'], 'after_as_of_lookahead')

    def test_pre_statement_event_is_not_double_counted(self):
        p = nbis(statement_as_of='2026-06-30')
        p['events_after_statement'].append({'date': '2026-06-15', 'type': 'secured_debt', 'cash_delta': 5e8, 'source_id': 'old'})
        p['capital_structure']['cash'].append({'id': 'dup', 'kind': 'unrestricted', 'amount': 5e8, 'source_id': 'old'})
        r = audit(p)
        self.assertIn('cash_reconciles', r['critical_failures'])
        self.assertEqual(next(c for c in r['checks'] if c['code'] == 'events_window')['evidence']['ignored'][0]['reason'],
                         'on_or_before_statement_date')

    def test_missing_statement_date_is_flagged(self):
        r = audit(nbis())
        self.assertTrue(r['valuation_usable'])
        self.assertIn('events_window', r['limitations'])


class RedteamDeclarations(unittest.TestCase):
    """Fix 3: undeclared model facts are visible UNVERIFIABLE rows; nothing declared cannot pass."""
    def test_package_declaring_only_revenue_basis_cannot_pass(self):
        p = deepcopy(NBIS); p['reported']['statement_as_of'] = '2026-06-30'; p['model'] = {'revenue_basis': 'recognized'}
        r = audit(p)
        self.assertEqual(r['verdict'], UNVERIFIABLE)
        self.assertFalse(r['valuation_usable'])
        self.assertTrue({'reinvestment', 'funding', 'backlog', 'sbc', 'terminal_share', 'scenario_order'} <= set(r['unverified']))

    def test_explicit_light_capex_and_no_funding_need_pass(self):
        p = nbis(statement_as_of='2026-06-30'); p['model'].update(capex_to_revenue_recent=.05, funding_need=0)
        r = audit(p)
        self.assertEqual(r['verdict'], PASSED)
        self.assertEqual({c['code']: c['status'] for c in r['checks']}['funding'], PASSED)


class RedteamInstrumentLevel(unittest.TestCase):
    """Fix 4: dropped instruments / phantom shares or cash no longer hide inside aggregate tolerance."""
    def test_dropping_two_100m_converts_fails_when_instruments_reported(self):
        p = nbis(statement_as_of='2026-06-30', convertibles=converts_list())
        self.assertEqual(audit(p)['critical_failures'], [])
        p['capital_structure']['debt'] = [d for d in p['capital_structure']['debt'] if d['id'] not in {'cv_2029_jun', 'cv_2031_jun'}]
        r = audit(p)
        self.assertEqual({c['code']: c['status'] for c in r['checks']}['debt_complete'], PASSED)   # 1.4% hides in 2% aggregate
        self.assertIn('debt_instruments', r['critical_failures'])
        self.assertEqual(sorted(next(c for c in r['checks'] if c['code'] == 'debt_instruments')['evidence']['missing']),
                         ['cv_2029_jun', 'cv_2031_jun'])

    def test_instrument_principal_mismatch_and_named_exchange(self):
        p = nbis(statement_as_of='2026-06-30', convertibles=converts_list())
        next(d for d in p['capital_structure']['debt'] if d['id'] == 'cv_2030_sep')['principal'] *= .9
        self.assertIn('debt_instruments', audit(p)['critical_failures'])
        p = nbis(statement_as_of='2026-06-30', convertibles=converts_list())
        p['reported']['convertibles'].append({'id': 'retired', 'principal': 50e6})
        p['events_after_statement'].append({'date': '2026-07-01', 'type': 'redemption', 'instrument_id': 'retired',
                                            'debt_principal_delta': -50e6, 'cash_delta': 0, 'source_id': 's'})
        p['reported']['debt_principal_outstanding'] += 50e6
        self.assertNotIn('debt_instruments', audit(p)['critical_failures'])

    def test_phantom_shares_and_cash_fail(self):
        p = nbis(statement_as_of='2026-06-30')
        p['capital_structure']['shares'].append({'id': 'phantom', 'kind': 'common', 'count': 2e6, 'source_id': 'x'})   # +0.65%
        self.assertIn('shares_current', audit(p)['critical_failures'])
        p = nbis(statement_as_of='2026-06-30')
        p['capital_structure']['cash'].append({'id': 'phantom', 'kind': 'unrestricted', 'amount': 150e6, 'source_id': 'x'})   # +1.0%
        self.assertIn('cash_reconciles', audit(p)['critical_failures'])


def live_dossier():
    """Fictional numbers with live-source kinds; mirrors tests/test_valuation_audit.paired()."""
    d = load('examples/research_dossier_demo.json')
    v = load('examples/valuation/audited_infrastructure.json'); v['is_demo'] = False; v['sources'][0]['kind'] = 'filing'
    d['as_of'] = v['as_of']; d['is_demo'] = False
    d['financial_input']['as_of'] = v['as_of']; d['financial_input']['is_demo'] = False
    for group in (d['sources'], d['financial_input']['sources']):
        for s in group: s['kind'] = 'filing'
    v['ticker'] = d['identity']['ticker']; v['company_id'] = d['identity']['company_id']
    src = d['sources'][-1]
    v['sources'] = [{'id': src['id'], 'title': src['title'], 'url': src['locator'], 'kind': src['kind'],
                     'published_at': src['published_at'], 'available_at': src['available_at']}]
    v['quote']['source_ids'] = [src['id']]; v['assumptions'][0]['source_ids'] = [src['id']]
    d['annual_valuation_input'] = v
    d['candidate_conclusion'] = {'classification': 'watchlist', 'valuation_basis': {'model': 'annual_path', 'scenario': 'base'}}
    return d


def attractive_at(price, **extra):
    d = live_dossier(); d['annual_valuation_input']['quote']['price'] = price
    d['candidate_conclusion'].update(classification='conditional_attractive', rationale='Synthetic margin test', **extra)
    return review(d)['candidate_conclusion']


class SupervisorMarginOfSafety(unittest.TestCase):
    """Fix 5: value > quote is not enough; base margin and bear tolerance decide the label."""
    def test_wide_margin_and_tolerable_bear_is_attractive(self):
        c = attractive_at(20)          # base 48.5, bear 22.5 (financial assets at face since v0.11)
        self.assertEqual(c['effective_classification'], 'conditional_attractive')
        self.assertTrue(c['price_test']['margin_ok'] and c['price_test']['bear_ok'])
        self.assertEqual(c['price_test']['terms_source'], 'engine_default')

    def test_thin_margin_or_fragile_bear_is_low_margin(self):
        thin = attractive_at(46.2)     # +5.0% vs base: a bare sign test accepted this
        self.assertTrue(thin['accepted_for_synthesis'])
        self.assertEqual(thin['effective_classification'], 'conditional_attractive_low_margin')
        self.assertFalse(thin['price_test']['margin_ok'])
        fragile = attractive_at(35)    # +39% vs base, but bear 36% below the quote
        self.assertTrue(fragile['price_test']['margin_ok'])
        self.assertFalse(fragile['price_test']['bear_ok'])
        self.assertEqual(fragile['effective_classification'], 'conditional_attractive_low_margin')

    def test_dossier_terms_need_rationale(self):
        c = attractive_at(30, margin_of_safety={'base_margin': .2, 'bear_tolerance': .4, 'rationale': 'Contracted revenue floor; documented.'})
        self.assertEqual(c['effective_classification'], 'conditional_attractive')
        self.assertEqual(c['price_test']['terms_source'], 'dossier')
        with self.assertRaisesRegex(ValueError, 'rationale'): attractive_at(30, margin_of_safety={'bear_tolerance': .4})


class SupervisorArchetype(unittest.TestCase):
    """Fix 6: the executed valuation archetype must belong to the dossier."""
    def test_mismatch_blocks_priced_conclusion(self):
        self.assertTrue(review(live_dossier())['gates']['priced_conclusion_ready'])
        d = live_dossier(); d['archetypes'] = ['software']
        r = review(d)
        self.assertFalse(r['gates']['priced_conclusion_ready'])
        self.assertFalse(r['gates']['valuation_archetype_matches_dossier'])
        self.assertIn('archetype_mismatch', r['candidate_conclusion']['price_blockers'])
        self.assertTrue(any(q['reason'] == 'archetype_mismatch' for q in r['next_actions']))


class SupervisorHypotheticalFinancing(unittest.TestCase):
    """Fix 7: an ownership scenario funded only by uncommitted financing cannot price."""
    def ownership(self):
        d = live_dossier(); d.pop('annual_valuation_input')
        d['financial_input']['quote']['price'] = 5     # below the 6.56 ownership value
        name = d['financial_input']['ownership_valuation']['scenarios'][0]['name']
        d['candidate_conclusion'] = {'classification': 'conditional_attractive', 'rationale': 'Synthetic',
                                     'valuation_basis': {'model': 'ownership', 'scenario': name}}
        return d

    def test_hypothetical_dependence_blocks_ownership_basis(self):
        r = review(self.ownership())
        self.assertFalse(r['gates']['priced_conclusion_ready'])
        self.assertEqual(r['candidate_conclusion']['effective_classification'], 'unresolved')
        self.assertEqual(r['candidate_conclusion']['financing_dependency']['status'], 'conditional_on_hypothetical_financing')
        self.assertIn('conditional_on_hypothetical_financing', r['candidate_conclusion']['price_blockers'])

    def test_committed_financing_is_not_blocked(self):
        d = self.ownership()
        for y in d['financial_input']['ownership_valuation']['scenarios'][0]['years']:
            for f in y.get('financing', []): f['commitment'] = 'committed'
        r = review(d)
        self.assertTrue(r['gates']['funded_without_hypothetical_financing'])
        self.assertIsNone(r['candidate_conclusion']['financing_dependency'])

    def test_annual_basis_reports_but_is_not_blocked_by_ownership_side_model(self):
        r = review(live_dossier())
        self.assertTrue(r['gates']['priced_conclusion_ready'])
        self.assertFalse(r['candidate_conclusion']['financing_dependency']['blocks_price_conclusion'])


class SupervisorCriticalClaims(unittest.TestCase):
    """Fix 8: claim kind cannot exempt a critical claim from evidence."""
    def test_unsupported_critical_opinion_or_assumption_blocks(self):
        for kind in ('opinion', 'assumption'):
            d = load('examples/research_dossier_demo.json')
            d['claims'].append({'id': 'moat', 'statement': 'Switching costs protect pricing for a decade.', 'kind': kind,
                                'dimension': 'moat', 'materiality': 'critical', 'support_ids': [], 'challenge_ids': [],
                                'reviewed_at': d['as_of']})
            r = review(d)
            self.assertFalse(r['gates']['material_claims_resolved'], kind)
            self.assertEqual(r['status'], 'research_incomplete')
            self.assertTrue(any(q['claim_id'] == 'moat' for q in r['questions']))
            d['claims'][-1]['support_ids'] = ['business-observation']
            self.assertTrue(review(d)['gates']['material_claims_resolved'], kind)


def full(year, **kw):
    base = dict(revenue=1000, gross_profit=400, operating_income=150, net_income=100, operating_cash_flow=130, capex=30,
                shares_diluted=100, total_assets=2000, current_assets=600, current_liabilities=300, total_liabilities=900,
                retained_earnings=500, receivables=150, sga=200, depreciation=50, ppe=500, long_term_debt=400, total_debt=400,
                cash=200, equity=1100, pretax_income=125, income_tax=25)
    base.update(kw)
    return {'fiscal_year': f'FY{year}', 'period_end': f'{year}-12-31', 'source_ids': ['synthetic'], **base}


class PlainPrivateKeys(unittest.TestCase):
    """Fix 9: caller-supplied '_executed_research_status' cannot fake a research review."""
    def test_private_status_is_ignored(self):
        c = load('examples/real/avgo_plain_2025.json'); c['_executed_research_status'] = 'ready_for_conditional_synthesis'
        r = plain_verdict(c)
        self.assertEqual(r['ignored_private_keys'], ['_executed_research_status'])
        self.assertTrue(any('research_review' in x for x in r['confidence']['reasons']))


class RedFlagGate(unittest.TestCase):
    """Fix 10: Beneish/Altman red flags withhold the price call unless an evidenced override covers them."""
    def test_plain_withholds_attractive_price_on_beneish_flag(self):
        c = load('examples/plain_demo.json'); c['valuation_case'] = load('examples/valuation/infrastructure.json')
        clean = plain_verdict(c)['price']['bucket']
        c['history'] = [full(2024), full(2025, revenue=1500, receivables=500, net_income=300, operating_cash_flow=20, gross_profit=450)]
        c['stage_reason'] = 'Synthetic fixture keeps the demo stage.'
        r = plain_verdict(c)
        self.assertEqual(r['scores']['beneish']['zone'], 'likely_manipulator')
        self.assertEqual(r['price']['bucket'], 'review_required')
        self.assertIn(r['price']['withheld_bucket'], {clean, 'cheap', 'cheap_even_bear'})
        self.assertEqual(r['red_flag_gate']['price_call'], 'withheld')
        self.assertIn('דגל אדום', r['bottom_line']['explain'])

    def summary_inputs(self):
        case = {'ticker': 'TEST', 'as_of': '2026-09-28', 'is_demo': False,
                'research_dossier': {'identity': {'ticker': 'TEST'}, 'as_of': '2026-09-28',
                    'sources': [{'id': 's', 'available_at': '2026-09-01'}],
                    'observations': [{'id': 'o', 'source_id': 's', 'reviewed': True, 'review_note': 'Receivables tied to a one-off government order',
                                      'reviewed_at': '2026-09-20', 'observed_at': '2026-08-31'}]}}
        report = {'ticker': 'TEST', 'as_of': case['as_of'], 'currency': 'USD', 'stage': 'growth',
                  'items': [{'key': 'accounting_risk', 'points': 1}], 'confidence': {'level': 'בינוני'},
                  'price': {'bucket': 'cheap', 'quote': 80, 'bear': 60, 'base': 100, 'bull': 140},
                  'valuation': {'priced_conclusion_ready': True, 'annual_values': [{'date': '2026-09-28', 'base': 100}]}}
        return report, case

    def test_summary_withholds_and_documented_override_releases(self):
        report, case = self.summary_inputs()
        out = decision_summary(report, case)
        self.assertFalse(out['price_conclusion_eligible'])
        self.assertIsNone(out['metrics']['discount_to_base_value'])
        self.assertIn('דגל אדום', out['text'])
        case['red_flag_override'] = {'flags': ['accounting_risk'], 'observation_ids': ['o'],
                                     'justification': 'Receivable jump is one government order collected after year end.'}
        out = decision_summary(report, case)
        self.assertTrue(out['price_conclusion_eligible'])
        self.assertEqual(out['red_flag_gate']['price_call'], 'override_accepted')
        self.assertIn('דגל אדום', out['text'])            # flag text kept even when released

    def test_override_must_cover_every_flag_and_be_evidenced(self):
        report, case = self.summary_inputs()
        report['items'].append({'key': 'distress', 'points': 1})
        case['red_flag_override'] = {'flags': ['accounting_risk'], 'observation_ids': ['o'],
                                     'justification': 'Receivable jump is one government order collected after year end.'}
        self.assertEqual(red_flag_gate(case, report['items'])['price_call'], 'withheld')
        for bad in ({'justification': 'trust me'}, {'observation_ids': ['missing']}, {'observation_ids': []}, {'flags': ['momentum']}):
            c = deepcopy(case); c['red_flag_override'].update(bad)
            with self.assertRaises(ValueError): red_flag_gate(c, report['items'])


class PlainStageOverride(unittest.TestCase):
    """Fix 11: an explicit stage that differs from the computed one needs a reason."""
    def test_bare_override_rejected_and_reasoned_override_recorded(self):
        c = load('examples/plain_demo.json'); c['stage'] = 'emerging'
        with self.assertRaisesRegex(ValueError, 'stage_reason'): plain_verdict(c)
        c['stage_reason'] = 'Analyst: first commercial year, revenue base too small for growth bars.'
        r = plain_verdict(c)
        self.assertEqual(r['stage_basis'], {'computed': 'growth', 'used': 'emerging', 'override': True, 'reason': c['stage_reason']})
        self.assertFalse(plain_verdict(load('examples/plain_demo.json'))['stage_basis']['override'])


class DriverDefaults(unittest.TestCase):
    """Fix 12: zero/0.8 ratio defaults are listed, and strict mode refuses them."""
    def spec(self): return load('examples/valuation/software_drivers.json')

    def test_defaults_are_listed_with_warning(self):
        s = self.spec(); base = next(x for x in s['scenarios'] if x['name'] == 'base'); base.pop('sbc_pct'); base.pop('prepaid_share', None)
        g = build_case(s)['generated_from']
        used = {(d['scenario'], d['key']): d['default'] for d in g['defaulted_keys']}
        self.assertEqual(used[('base', 'sbc_pct')], 0)
        self.assertEqual(used[('base', 'prepaid_share')], .8)
        self.assertIn('sbc_pct=0', g['warnings'][0])

    def test_strict_mode_raises_until_every_ratio_is_explicit(self):
        s = self.spec(); s['strict_drivers'] = True
        with self.assertRaisesRegex(ValueError, 'strict_drivers'): build_case(s)
        for sc in s['scenarios']: sc['nol_usage_limit'] = 0
        g = build_case(s)['generated_from']
        self.assertEqual(g['defaulted_keys'], []); self.assertTrue(g['strict_drivers'])
        s['strict_drivers'] = 'yes'
        with self.assertRaisesRegex(ValueError, 'boolean'): build_case(s)


class PointInTimeAvailability(unittest.TestCase):
    """Fix 13: a period that ended before as_of but was not yet filed is excluded."""
    def av(self):
        inc = {'symbol': 'X', 'annualReports': [{'fiscalDateEnding': d, 'reportedCurrency': 'USD', 'totalRevenue': r}
                                                for d, r in (('2025-12-31', '1000'), ('2024-12-31', '800'))],
               'quarterlyReports': [{'fiscalDateEnding': d, 'reportedCurrency': 'USD', 'totalRevenue': '250'}
                                    for d in ('2025-09-30', '2025-12-31')]}
        return inc

    def test_unfiled_periods_are_excluded_and_estimates_labelled(self):
        r = from_alpha_vantage(self.av(), as_of='2026-02-10')
        self.assertEqual([x['period_end'] for x in r['history']], ['2024-12-31'])      # FY2025 10-K estimated 2026-03-16
        self.assertEqual([q['period_end'] for q in r['recent_quarters']], ['2025-09-30'])
        self.assertEqual(r['history'][0]['available_at'], '2025-03-16')
        self.assertTrue(r['history'][0]['available_at_estimated'])
        late = from_alpha_vantage(self.av(), as_of='2026-03-20')
        self.assertEqual([x['period_end'] for x in late['history']], ['2024-12-31', '2025-12-31'])
        fast = from_alpha_vantage(self.av(), as_of='2026-02-10', annual_lag_days=30)
        self.assertEqual(len(fast['history']), 2)
        with self.assertRaisesRegex(ValueError, 'lag'): from_alpha_vantage(self.av(), annual_lag_days=-1)

    def test_history_enforces_available_at(self):
        rows = from_alpha_vantage(self.av())['history']           # no as_of: rows still carry availability
        case = {'as_of': '2026-02-10', 'history': rows}
        with self.assertRaisesRegex(ValueError, 'look-ahead'): history(case)
        case['as_of'] = '2026-03-20'; self.assertEqual(len(history(case)), 2)
        bad = {'as_of': '2026-03-20', 'history': [dict(rows[0], available_at='2024-06-30')]}
        with self.assertRaisesRegex(ValueError, 'precedes'): history(bad)
        q = {'as_of': '2026-01-20', 'recent_quarters': [{'period_end': '2025-12-31', 'revenue': 1, 'source_ids': ['s'], 'available_at': '2026-02-14'}]}
        with self.assertRaisesRegex(ValueError, 'look-ahead'): quarters(q)


if __name__ == '__main__':
    unittest.main()
