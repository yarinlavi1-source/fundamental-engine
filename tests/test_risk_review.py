"""Risk register / failure scenarios (risk.py) and the independent review stage (review.py).

Fixtures: the fictional software driver example (is_demo), synthetic NON-demo variants of it (test doubles:
their 'filing' sources and numbers are fictional, labelled as such), and the real ADBE FactBook (10-K values)
with hand-built dataqa/analysis inputs and an ILLUSTRATIVE driver spec (test assumptions, not a forecast).
Upstream stage outputs are constructed by hand per docs/MODULE_CONTRACTS.md so these tests do not depend on
other modules' heuristics.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine import facts as F, risk, review
from fundamental_engine.drivers import build_case
from fundamental_engine.scores import scorecards
from fundamental_engine.valuation import value_company

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'examples/valuation/software_drivers.json').read_text())
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
AS_OF = '2026-09-27'


def m(v):
    return {'value': v, 'unit': 'ratio', 'formula': 'synthetic test input', 'inputs': [], 'period': 'FY2025', 'status': 'calculation'}


def factbook(estimates=()):
    fb = F.new({'ticker': 'FICTSAAS', 'name': 'Fictional SaaS (synthetic test double)', 'currency': 'USD'}, AS_OF)
    F.add_source(fb, 'syn', 'synthetic', 'synthetic statements', 'synthetic', AS_OF)
    for y, rev in ((2023, 680e6), (2024, 800e6), (2025, 950e6)):
        F.add_fact(fb, 'revenue', f'FY{y}', f'{y}-12-31', rev, 'syn', 'as_reported', f'{y + 1}-03-01')
    F.set_price(fb, 50, AS_OF, 'syn')
    fb['estimates'] = [dict(e) for e in estimates]
    return fb


def qa(status='ok', blocking=(), limited=(), **resolved):
    r = {'shares_outstanding': {'value': 1e8, 'as_of': AS_OF, 'basis': 'cover', 'source_id': 'syn'},
         'cash': {'value': 4e8, 'as_of': AS_OF, 'source_id': 'syn'}, 'short_term_investments': {'value': 1e8, 'as_of': AS_OF},
         'long_term_investments': {'value': 0.0, 'as_of': AS_OF}, 'debt': {'value': 2e8, 'as_of': AS_OF, 'method': 'noncurrent+current'}}
    r.update(resolved)
    fields = {f: {'field': f, 'status': 'conflict', 'note': 'test double'} for f in list(blocking) + list(limited)}
    return {'status': status, 'fields': fields, 'checks': [], 'resolved': r, 'blocking': list(blocking), 'limited': list(limited)}


METRICS = {'revenue_cagr_3y': .18, 'operating_margin_best_3y': .17, 'sbc_pct_revenue': .12, 'capex_pct_revenue': .05, 'cfo_to_net_income': 1.5,
           'accruals_ratio': -.04, 'roic': .25, 'roic_ex_goodwill': .35, 'incremental_roic_3y': .3, 'net_debt_to_ebitda': -1.5, 'net_cash': 3e8,
           'interest_coverage': 25., 'share_count_cagr_3y': .01}
SAFE = {'beneish': {'status': 'computed', 'zone': 'unlikely', 'm': -2.6, 'components': {'SGI': 1.19}},
        'altman': {'status': 'computed', 'zone': 'safe', 'z': 6.0, 'model': "Z''"}, 'piotroski': {'status': 'computed', 'zone': 'mixed', 'score': 5, 'out_of': 9}}


def analysis(forensics=None, fcf=132.5e6, **over):
    metrics = dict(METRICS, **over)
    return {'years': ['2023-12-31', '2024-12-31', '2025-12-31'],
            'annual': {'2023-12-31': {'revenue': 680e6}, '2024-12-31': {'revenue': 800e6},
                       '2025-12-31': {'revenue': 950e6, 'operating_income': 150e6, 'net_income': 120e6, 'cfo': 180e6, 'capex': 47.5e6, 'fcf': fcf}},
            'ttm': {}, 'metrics': {k: m(v) for k, v in metrics.items() if v is not None},
            'forensics': deepcopy(SAFE) if forensics is None else forensics, 'flags': []}


def card(primary='subscription_software', stage='growth', funding='self_funded', completeness='complete', dims=None):
    d = {'competition': {'assessment': 'adequate', 'evidence_ids': ['syn'], 'note': 'two scaled rivals', 'source': 'analyst'},
         'technology_ai': {'assessment': 'weak', 'evidence_ids': ['syn'], 'note': 'AI agents may reduce seat counts', 'source': 'analyst'}}
    return {'business_model': {'primary': primary, 'secondary': [], 'reasons': ['test'], 'override': None},
            'stage': {'value': stage, 'reasons': [], 'computed': stage, 'override': None}, 'funding': {'value': funding, 'reasons': ['test']},
            'dimensions': d if dims is None else dims,
            'completeness': {'status': completeness, 'missing': [] if completeness == 'complete' else ['understanding:why_customers_pay (open)']}}


def forecast(spec, assumptions=(), risk_free=.042):
    return {'spec': spec, 'assumptions': list(assumptions), 'anchors': {}, 'warnings': [],
            'cost_of_equity': {'bear': .1, 'base': .1, 'bull': .1, 'components': {'risk_free': risk_free, 'equity_risk_premium': .055}}}


def valuate(spec, **extra):
    case = build_case(spec)
    res = value_company(case)
    return dict({'status': res['status'], 'case': case, 'result': res, 'diagnostics': {}, 'what_must_be_true': [], 'capital_check': None, 'errors': []}, **extra)


def live_spec():
    """Non-demo test double: complete underwriting, one UNREVIEWED assumption (only review gaps remain)."""
    s = deepcopy(SPEC)
    s['is_demo'] = False
    s['sources'] = [{'id': '10k', 'title': 'Fictional 10-K (test double)', 'url': 'test://10k', 'kind': 'filing', 'retrieval': 'primary_document',
                     'published_at': '2026-02-20', 'available_at': '2026-02-22'},
                    {'id': 'quote', 'title': 'Fictional quote (test double)', 'url': 'test://quote', 'kind': 'market_feed',
                     'published_at': AS_OF, 'available_at': AS_OF}]
    s['quote']['source_ids'] = ['quote']
    s['assumptions'] = [{'id': 'a1', 'kind': 'analyst', 'as_of': AS_OF, 'review_status': 'unreviewed', 'source_ids': ['10k'],
                         'rationale': 'Fictional test-double drivers; not a real company.', 'falsifier': 'Any figure differing from the test double.'}]
    for node in [s['opening']] + s['scenarios'] + [sc['terminal'] for sc in s['scenarios']]:
        node['assumption_ids'] = ['a1']
    ids = {'assumption_ids': ['a1']}
    s['underwriting'] = {
        'opening_reconciliation': {**ids, 'statement_as_of': AS_OF, 'share_basis': 'point_in_time_common_outstanding', 'cash_and_equivalents': 4e8,
                                   'restricted_cash': 0, 'short_term_investments': 1e8, 'long_term_investments': 0, 'investment_haircut': 0, 'debt': 2e8,
                                   'common_shares_outstanding': 1e8, 'incremental_dilutive_shares': 0, 'dilution_review': dict(ids),
                                   'claims_review': dict(ids), 'bridge_events': []},
        'scenario_reviews': {n: {k: dict(ids) for k in ('demand_and_capacity', 'funding_and_claims', 'competitive_response', 'maturity', 'transition_exception')}
                             for n in ('bear', 'base', 'bull')},
        'consensus_review': {**ids, 'status': 'unavailable'}, 'material_issues': []}
    return s


def signed(**over):
    r = {'reviewer': 'Independent reviewer (test double)', 'reviewed_at': '2026-09-28', 'independent': True, 'assumption_reviews': {'a1': 'reviewed'},
         'findings': [{'severity': 'major', 'text': 'Terminal margin needs a peer comparison.', 'resolved': True, 'response': 'Peer range 22-30% cited.'}],
         'statement': 'I read the casefile and the sources; the assumptions are supportable.'}
    r.update(over)
    return r


def truncate(spec, n):
    s = deepcopy(spec)
    s['boundaries'] = s['boundaries'][:n]
    s['report_dates'] = [s['as_of']] + s['boundaries']
    for sc in s['scenarios']:
        for k, v in list(sc.items()):
            if isinstance(v, list) and k != 'assumption_ids':
                sc[k] = v[:n - 1] if k == 'growth' else v[:n]
    return s


def scen(spec, name):
    return next(s for s in spec['scenarios'] if s['name'] == name)


def run(spec=None, fb=None, q=None, an=None, cd=None, fc=None, val=None, reviewer=None):
    spec = live_spec() if spec is None else spec
    fb, q, an, cd = fb or factbook(), q or qa(), an or analysis(), cd or card()
    fc, val = fc or forecast(spec), val or valuate(spec)
    rk = risk.assess(fb, q, an, cd, fc, val)
    rv = review.audit(fb, q, an, cd, fc, val, rk, reviewer)
    json.dumps(rk, allow_nan=False)
    json.dumps(rv, allow_nan=False)
    return rk, rv


def entry(rk, rid):
    return next((r for r in rk['register'] if r['id'] == rid), None)


class DemoAndSignOff(unittest.TestCase):
    def test_demo_example_executes_but_never_allows_a_price_conclusion(self):
        rk, rv = run(SPEC, reviewer=signed(assumption_reviews={'demo': 'reviewed'}))
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('is_demo', rv['reason_codes'])
        self.assertEqual(rv['status'], 'unreviewed_draft')
        self.assertEqual(rk['disqualifying'], [])
        today = valuate(SPEC)['result']['annual_values'][0]
        self.assertAlmostEqual(rk['error_size']['value_range_rel'], (today['bull'] - today['bear']) / today['base'])
        names = [s['name'] for s in rk['failure_scenarios']]
        self.assertEqual(names, ['bear', 'permanent_loss:growth_stall'])
        bear, perm = rk['failure_scenarios']
        self.assertAlmostEqual(bear['value_per_share'], today['bear'])
        self.assertLess(perm['value_per_share'], bear['value_per_share'])          # zero growth + compressed margin is worse than bear
        self.assertTrue(perm['observable_signs'] and perm['edits'] and perm['input_sha256'])
        d = rk['downside']
        self.assertAlmostEqual(d['loss_to_bear'], 1 - today['bear'] / 50)
        self.assertGreater(d['loss_to_permanent'], d['loss_to_bear'])

    def test_non_demo_needs_an_independent_signoff_that_closes_only_review_gaps(self):
        spec = live_spec()
        val = valuate(spec)
        self.assertEqual(val['result']['status'], 'unreviewed')
        self.assertTrue({i['code'] for i in val['result']['underwriting_audit']['issues']} <= review.REVIEW_ONLY)
        _, rv0 = run(spec, val=val)
        self.assertFalse(rv0['price_conclusion_allowed'])
        self.assertEqual(rv0['status'], 'unreviewed_draft')
        self.assertIn('review_required', rv0['reason_codes'])
        self.assertIsNone(rv0['reviewer'])                                          # never fabricated
        _, rv1 = run(spec, val=val, reviewer=signed())
        self.assertTrue(rv1['price_conclusion_allowed'], rv1['reasons'])
        self.assertEqual(rv1['status'], 'reviewed')
        self.assertTrue(rv1['mechanical']['valuation_audit']['reaudit_with_reviewer']['eligible'])
        self.assertEqual(val['result']['reproduction_input']['assumptions'][0]['review_status'], 'unreviewed')   # sign-off applied to a copy

    def test_signoff_cannot_close_a_non_review_gap(self):
        spec = live_spec()
        del spec['underwriting']['consensus_review']
        _, rv = run(spec, reviewer=signed())
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('underwriting_incomplete', rv['reason_codes'])

    def test_reviewer_shape_is_validated_and_recorded(self):
        spec = live_spec()
        val = valuate(spec)
        cases = {'independent': signed(independent=False), 'precedes the valuation cutoff': signed(reviewed_at='2026-09-01'),
                 'unknown assumption id': signed(assumption_reviews={'a1': 'reviewed', 'zz': 'reviewed'}),
                 'needs a response': signed(findings=[{'severity': 'minor', 'text': 'x', 'resolved': True}]),
                 'different case': signed(valuation_sha256='0' * 64)}
        for needle, r in cases.items():
            _, rv = run(spec, val=val, reviewer=r)
            self.assertFalse(rv['price_conclusion_allowed'], needle)
            self.assertTrue(any(needle in e for e in rv['reviewer_check']['errors']), (needle, rv['reviewer_check']['errors']))
            self.assertEqual(rv['reviewer'], r)                                     # recorded as supplied
        _, rv = run(spec, val=val, reviewer=signed(valuation_sha256=val['result']['input_sha256']))
        self.assertTrue(rv['price_conclusion_allowed'])
        self.assertTrue(rv['reviewer_check']['bound_to_valuation'])

    def test_rejection_and_unresolved_critical_finding(self):
        _, rv = run(reviewer=signed(assumption_reviews={'a1': 'rejected'}))
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('assumption_rejected', rv['reason_codes'])
        crit = signed(findings=[{'severity': 'critical', 'text': 'Churn data contradicts the base growth path.', 'resolved': False}])
        _, rv = run(reviewer=crit)
        self.assertEqual(rv['status'], 'blocked')
        self.assertIn('critical_finding_unresolved', rv['reason_codes'])
        major = signed(findings=[{'severity': 'major', 'text': 'Peer margins not cited.', 'resolved': False}])
        _, rv = run(reviewer=major)
        self.assertTrue(rv['price_conclusion_allowed'])                             # open item, not a veto
        self.assertTrue(any(o['code'] == 'reviewer_major_finding' for o in rv['open_items']))


class DisqualifyingGates(unittest.TestCase):
    def test_blocked_data_is_disqualifying_and_blocks_review(self):
        rk, rv = run(q=qa('blocked', blocking=['debt']), reviewer=signed())
        self.assertIn('gate.blocked_data', rk['disqualifying'])
        self.assertEqual(entry(rk, 'gate.blocked_data')['class'], 'disqualifying')
        self.assertEqual(rv['status'], 'blocked')
        self.assertIn('data_blocked', rv['reason_codes'])
        self.assertFalse(rv['price_conclusion_allowed'])

    def test_unfunded_base_plan_is_disqualifying(self):
        spec = live_spec()
        scen(spec, 'base')['minimum_cash'] = 3e9                                    # base plan needs more cash than it has
        val = valuate(spec)
        self.assertEqual(val['result']['status'], 'funding_blocked')
        rk, rv = run(spec, val=val, reviewer=signed())
        self.assertIn('gate.unfunded_plan', rk['disqualifying'])
        self.assertIn('funding', rv['mechanical']['redteam']['critical_failed'])  # derived from rows: financing_modeled False
        self.assertEqual(rv['status'], 'blocked')

    def test_bear_only_funding_gap_is_priced_not_disqualifying(self):
        spec = live_spec()
        scen(spec, 'bear')['growth_capex_pct'] = .35                               # bear burns cash, base/bull stay funded
        rk, rv = run(spec, reviewer=signed())
        self.assertNotIn('gate.unfunded_plan', rk['disqualifying'])
        self.assertEqual(rk['disqualifying'], [])
        gap = entry(rk, 'fin.funding_gap.bear')
        self.assertEqual((gap['class'], gap['severity']), ('pricing', 'high'))
        ff = next(s for s in rk['failure_scenarios'] if s['name'] == 'permanent_loss:funding_failure')
        self.assertEqual(ff['status'], 'computed')
        self.assertGreater(ff['dilution']['new_shares'], 0)                         # loss shows up as dilution at a depressed price
        self.assertLess(ff['dilution']['original_ownership_retained'], 1)
        self.assertEqual(rv['status'], 'unreviewed_draft')                          # bear values withheld -> no price claim, not a veto
        self.assertIn('funding_blocked', rv['reason_codes'])

    def test_beneish_needs_weak_cash_conversion_to_disqualify(self):
        manip = deepcopy(SAFE)
        manip['beneish'] = {'status': 'computed', 'zone': 'likely_manipulator', 'm': -1.2, 'components': {'SGI': 1.4}}
        rk, _ = run(an=analysis(manip, cfo_to_net_income=.55, accruals_ratio=.12))
        self.assertIn('gate.manipulation_signal', rk['disqualifying'])
        self.assertEqual(entry(rk, 'acc.cash_conversion')['class'], 'pricing')
        rk, rv = run(an=analysis(manip), reviewer=signed())
        self.assertEqual(rk['disqualifying'], [])
        b = entry(rk, 'acc.beneish')
        self.assertEqual((b['class'], b['severity'], b['in_bear_case']), ('pricing', 'high', False))
        self.assertTrue(rv['price_conclusion_allowed'])

    def test_distress_needs_altman_negative_fcf_and_refinancing(self):
        distress = deepcopy(SAFE)
        distress['altman'] = {'status': 'computed', 'zone': 'distress', 'z': .4, 'model': "Z''"}

        def fb_with(maturity):
            fb = factbook()
            fb['capital_structure'] = {'as_of': AS_OF, 'currency': 'USD',
                                       'cash': [{'id': 'c', 'kind': 'unrestricted', 'amount': 5e7, 'source_id': 'syn', 'as_of': AS_OF}],
                                       'shares': [{'id': 'sh', 'kind': 'common', 'count': 1e8, 'source_id': 'syn', 'as_of': AS_OF}],
                                       'debt': [{'id': 'tl', 'kind': 'term', 'principal': 6e8, 'maturity': maturity, 'source_id': 'syn'}]}
            return fb
        q = qa(cash={'value': 5e7, 'as_of': AS_OF}, short_term_investments={'value': 0.0, 'as_of': AS_OF}, debt={'value': 6e8, 'as_of': AS_OF})
        rk, _ = run(fb=fb_with('2027-06-30'), q=q, an=analysis(distress, fcf=-4e7))
        self.assertIn('gate.distress', rk['disqualifying'])
        self.assertTrue(rk['liquidity']['refinancing_need'])
        rk, _ = run(fb=fb_with('2031-06-30'), q=q, an=analysis(distress, fcf=-4e7))
        self.assertNotIn('gate.distress', rk['disqualifying'])
        self.assertEqual(entry(rk, 'acc.altman')['class'], 'pricing')
        self.assertAlmostEqual(rk['liquidity']['runway_years'], 1.25)                # 50m liquid / 40m annual burn
        self.assertEqual(entry(rk, 'fin.runway')['severity'], 'high')                # below 1.5 years
        self.assertEqual(entry(rk, 'fin.runway')['class'], 'pricing')

    def test_understanding_gap_is_an_evidence_gap_not_a_veto_and_equity_impaired_is(self):
        rk, rv = run(cd=card(completeness='insufficient'), reviewer=signed())
        self.assertEqual(rk['disqualifying'], [])
        self.assertEqual(rk['evidence_gaps'], ['gap.understanding_insufficient'])
        g = entry(rk, 'gap.understanding_insufficient')
        self.assertEqual((g['class'], g['severity']), ('pricing', 'high'))
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('understanding_insufficient', rv['reason_codes'])
        self.assertEqual(rv['status'], 'unreviewed_draft')
        spec = live_spec()
        scen(spec, 'base')['terminal']['other_claims'] = 1e11
        rk, _ = run(spec)
        self.assertIn('gate.equity_impaired', rk['disqualifying'])


class PricingRisks(unittest.TestCase):
    def test_terminal_share_above_80pct_is_a_pricing_risk_not_a_veto(self):
        spec = truncate(live_spec(), 3)
        val = valuate(spec)
        self.assertGreater(val['result']['scenarios'][1]['terminal_share_of_present_value'], .8)
        rk, rv = run(spec, val=val, reviewer=signed())
        t = entry(rk, 'val.terminal_share')
        self.assertEqual(t['class'], 'pricing')
        self.assertNotIn('val.terminal_share', rk['disqualifying'])
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])
        check = next(c for c in rv['mechanical']['redteam']['result']['checks'] if c['code'] == 'terminal_share')
        self.assertEqual(check['status'], 'passed_with_limitations')

    def test_unassessed_dimensions_get_type_relevance_not_invented_risk(self):
        rk, _ = run(cd=card(primary='semiconductor', dims={}))
        g = entry(rk, 'biz.geopolitics')
        self.assertEqual((g['severity'], g['status'], g['assessed'], g['class'], g['in_bear_case']), ('high', 'unassessed', False, 'pricing', False))
        self.assertIn('not assessed', g['description'])
        rk, _ = run(cd=card(dims={}))
        self.assertEqual(entry(rk, 'biz.geopolitics')['severity'], 'low')
        self.assertEqual(entry(rk, 'biz.competition')['severity'], 'high')
        self.assertEqual(entry(rk, 'biz.customer_concentration')['severity'], 'low')
        rk, _ = run(cd=card(stage='early', dims={}))
        self.assertEqual(entry(rk, 'biz.customer_concentration')['severity'], 'high')

    def test_in_bear_case_requires_the_bear_to_model_and_name_the_risk(self):
        rk, _ = run()
        ai = entry(rk, 'biz.technology_ai')                                         # bear is worse on growth and margin but never names AI
        self.assertEqual((ai['class'], ai['in_bear_case']), ('pricing', False))
        self.assertIn('bear_link', ai)
        spec = live_spec()
        scen(spec, 'bear')['thesis'] = 'AI agents cut seat counts: slower growth and price pressure on renewals.'
        rk, _ = run(spec)
        ai = entry(rk, 'biz.technology_ai')
        self.assertEqual((ai['class'], ai['in_bear_case'], ai['severity']), ('accepted_priced', True, 'high'))
        sbc = entry(rk, 'dil.sbc')                                                  # lever-intrinsic: bear charges more SBC
        self.assertTrue(sbc['in_bear_case'])
        same = live_spec()
        for s in same['scenarios']:
            s['thesis'] = 'AI agents cut seat counts.'
        scen(same, 'bear').update({k: scen(same, 'base')[k] for k in ('growth', 'cash_cost_ratio', 'sbc_pct', 'terminal')})
        rk, _ = run(same)
        self.assertFalse(entry(rk, 'biz.technology_ai')['in_bear_case'])           # named but not modeled
        self.assertFalse(rk['bear_levers']['growth']['modeled'])

    def test_discount_rate_component_marks_a_risk_priced(self):
        spec = live_spec()
        fc = forecast(spec)
        fc['cost_of_equity']['components']['country_risk_premium'] = .02
        rk, _ = run(spec, fc=fc, cd=card(dims={'geopolitics': {'assessment': 'weak', 'evidence_ids': ['syn'], 'note': 'single-country supply'}}))
        g = entry(rk, 'biz.geopolitics')
        self.assertEqual(g['class'], 'accepted_priced')
        self.assertEqual(g['priced_via'], 'forecast.cost_of_equity.components.country_risk_premium')
        self.assertFalse(g['in_bear_case'])

    def test_what_must_be_true_directions(self):
        spec = live_spec()
        price = 50
        wmbt = [{'lever': 'growth', 'label': 'average revenue growth', 'status': 'solved', 'required': .27, 'anchor': .14},
                {'lever': 'mature_margin', 'label': 'mature operating margin after SBC', 'status': 'solved', 'required': .40, 'anchor': .26},
                {'lever': 'capital_efficiency', 'label': 'growth investment per unit of new revenue (x base)', 'status': 'solved', 'required': 1.97, 'anchor': 1.},
                {'lever': 'cost_of_equity', 'label': 'annual return implied by the price', 'status': 'solved', 'required': .06, 'anchor': .1}]
        rk, _ = run(spec, val=valuate(spec, what_must_be_true=wmbt))
        self.assertEqual(entry(rk, 'val.wmbt.growth')['severity'], 'high')
        self.assertEqual(entry(rk, 'val.wmbt.mature_margin')['severity'], 'high')   # 40% vs best history 17%
        self.assertIsNone(entry(rk, 'val.wmbt.capital_efficiency'))                 # 1.97x capital still works: a cushion
        self.assertIsNone(entry(rk, 'val.wmbt.cost_of_equity'))                     # expected return belongs to decision
        wmbt[2]['required'] = .45
        rk, _ = run(spec, val=valuate(spec, what_must_be_true=wmbt))
        self.assertEqual(entry(rk, 'val.wmbt.capital_efficiency')['severity'], 'high')
        self.assertLess(price, 1e9)

    def test_no_valuation_degrades_honestly(self):
        val = {'status': 'not_computed', 'reason': 'no forecast spec for this valuation method'}
        rk, rv = run(val=val)
        self.assertTrue(all(s['status'] == 'not_computed' for s in rk['failure_scenarios']))
        self.assertIsNone(rk['downside']['bear_value'])
        self.assertEqual(rk['error_size']['level'], 'high')
        self.assertIn('valuation_not_executed', rv['reason_codes'])
        self.assertFalse(rv['price_conclusion_allowed'])


class RedTeamDerivation(unittest.TestCase):
    def test_flags_come_from_execution_not_declarations(self):
        spec = live_spec()
        for s in spec['scenarios']:
            s['sbc_pct'] = 0                                                         # model ignores 12%-of-revenue SBC history
        spec['last_full_year_revenue'] = 1.3e9                                     # start does not tie to recognized revenue
        declared = {'revenue_basis': 'recognized', 'sbc_treatment': 'expensed', 'financing_modeled': True, 'margin_basis': 'ebit_after_sbc'}
        fc = forecast(spec)
        fc['redteam_model'] = declared
        rk, rv = run(spec, fc=fc, reviewer=signed())
        rt = rv['mechanical']['redteam']
        self.assertTrue(rt['inputs_derived'])
        self.assertEqual(rt['derived_inputs']['margin_basis']['value'], 'adjusted_ebitda')
        self.assertIsNone(rt['derived_inputs']['revenue_basis']['value'])
        self.assertIn('does not tie', rt['derived_inputs']['revenue_basis']['derived_from'])
        self.assertIn('sbc', rt['critical_failed'])
        self.assertIn('revenue_basis', rt['critical_unverifiable'])
        self.assertFalse(rt['usable'])
        self.assertIn('gate.redteam_critical_failure', rk['disqualifying'])
        self.assertIn('redteam_not_usable', rv['reason_codes'])
        self.assertEqual(rv['status'], 'blocked')

    def test_structure_from_dataqa_is_labelled_by_construction(self):
        _, rv = run(reviewer=signed())
        rt = rv['mechanical']['redteam']
        self.assertEqual(rt['capital_structure_source'], 'dataqa_resolved')
        self.assertEqual(sorted(rt['by_construction']), ['cash_reconciles', 'debt_complete', 'shares_current'])
        self.assertEqual(rt['derived_inputs']['revenue_basis']['value'], 'recognized')
        self.assertEqual(rt['derived_inputs']['funding_need']['value'], 0)
        self.assertTrue(rt['usable'])

    def test_current_year_bridge_counts_as_recognized_only_when_documented(self):
        spec = live_spec()
        spec['last_full_year_revenue'] = 950e6 * 1.08
        bridge = {'id': 'revenue_current_fy', 'topic': 'current fiscal year revenue', 'kind': 'model', 'source_ids': ['syn'],
                  'values': {'revenue': 950e6 * 1.08, 'growth': .08}}
        _, rv = run(spec)
        self.assertIn('revenue_basis', rv['mechanical']['redteam']['critical_unverifiable'])
        _, rv = run(spec, fc=forecast(spec, [bridge]))
        d = rv['mechanical']['redteam']['derived_inputs']['revenue_basis']
        self.assertEqual(d['value'], 'recognized')
        self.assertIn('one-step current-year bridge', d['derived_from'])

    def test_capital_structure_after_cutoff_is_rejected(self):
        fb = factbook()
        fb['capital_structure'] = {'as_of': '2026-12-31', 'currency': 'USD', 'cash': [], 'debt': [],
                                   'shares': [{'id': 'sh', 'kind': 'common', 'count': 1e8, 'source_id': 'syn'}]}
        rk, rv = run(fb=fb)
        rt = rv['mechanical']['redteam']
        self.assertIn('look-ahead', rt['error'])
        self.assertFalse(rt['usable'])


class Consistency(unittest.TestCase):
    def codes(self, rv):
        return {c['code']: c for c in rv['mechanical']['consistency']}

    def test_consensus_gap_needs_explanation_and_later_estimates_are_ignored(self):
        spec = live_spec()
        fy27 = next(r for r in valuate(spec)['result']['scenarios'][1]['forecast'] if r['end'] == '2027-12-31')['revenue']
        est = {'metric': 'revenue', 'fiscal_year': 2027, 'period_end': '2027-12-31', 'value': fy27 * 1.5, 'source_id': 'syn', 'as_of': AS_OF}
        _, rv = run(spec, fb=factbook([est]), reviewer=signed())
        c = self.codes(rv)['revenue_vs_consensus']
        self.assertEqual(c['status'], 'needs_explanation')
        self.assertAlmostEqual(c['detail']['relative_difference'], 1 / 1.5 - 1)
        self.assertTrue(rv['price_conclusion_allowed'])                             # an open question, not a veto
        self.assertTrue(any(o['code'] == 'revenue_vs_consensus' for o in rv['open_items']))
        _, rv = run(spec, fb=factbook([dict(est, as_of='2026-10-15')]))
        self.assertEqual(self.codes(rv)['revenue_vs_consensus']['status'], 'skip')   # published after the cutoff

    def test_margin_terminal_growth_and_share_checks(self):
        an = analysis(operating_margin_best_3y=.05, share_count_cagr_3y=.06, sbc_pct_revenue=.03)
        _, rv = run(an=an, fc=forecast(live_spec(), risk_free=.02))
        c = self.codes(rv)
        self.assertEqual(c['margin_vs_history']['status'], 'needs_evidence')
        self.assertEqual(c['terminal_growth_vs_risk_free']['status'], 'fail')
        self.assertEqual(c['share_path_vs_history']['status'], 'needs_explanation')
        _, rv = run(an=an, reviewer=signed())
        self.assertEqual(self.codes(rv)['margin_vs_history']['status'], 'reviewed')
        _, rv = run(fc=forecast(live_spec(), risk_free=None))
        self.assertEqual(self.codes(rv)['terminal_growth_vs_risk_free']['status'], 'skip')

    def test_opening_understating_shares_blocks(self):
        q = qa(shares_outstanding={'value': 1.2e8, 'as_of': AS_OF, 'basis': 'cover'})
        _, rv = run(q=q, reviewer=signed())
        c = self.codes(rv)['opening_vs_validated_data']
        self.assertEqual((c['status'], c['severity']), ('fail', 'critical'))
        self.assertIn('consistency_failure', rv['reason_codes'])
        self.assertEqual(rv['status'], 'blocked')


def adbe_inputs():
    """Real ADBE 10-K history (fixture) with hand-built dataqa/analysis outputs and an ILLUSTRATIVE spec (test assumptions)."""
    fb, as_of = deepcopy(ADBE), ADBE['as_of']
    keys = ('revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'sbc', 'd_and_a', 'sm', 'ga', 'receivables',
            'current_assets', 'current_liabilities', 'ppe', 'total_assets', 'total_liabilities', 'equity', 'long_term_debt_tagged', 'cash',
            'shares_diluted_wavg', 'income_tax', 'pretax_income')
    t = F.annual_table(fb, keys)
    ends = sorted(t)
    rows = [{'fiscal_year': int(e[:4]), 'period_end': e, 'revenue': r['revenue'], 'gross_profit': r['gross_profit'], 'operating_income': r['operating_income'],
             'net_income': r['net_income'], 'operating_cash_flow': r['cfo'], 'capex': r['capex'], 'receivables': r['receivables'],
             'current_assets': r['current_assets'], 'current_liabilities': r['current_liabilities'], 'ppe': r['ppe'], 'total_assets': r['total_assets'],
             'total_liabilities': r['total_liabilities'], 'equity': r['equity'], 'long_term_debt': r['long_term_debt_tagged'], 'cash': r['cash'],
             'depreciation': r['d_and_a'], 'sga': r['sm'] + r['ga'], 'shares_diluted': r['shares_diluted_wavg'], 'income_tax': r['income_tax'],
             'pretax_income': r['pretax_income']} for e, r in t.items()]
    last, three = t[ends[-1]], t[ends[-4]]
    inst = {k: F.latest_instant(fb, k) for k in ('cash', 'short_term_investments', 'long_term_debt_tagged', 'shares_outstanding_vendor')}
    q = {'status': 'limited', 'blocking': [], 'limited': ['shares_outstanding', 'cash_and_liquid_investments'], 'checks': [],
         'fields': {'shares_outstanding': {'field': 'shares_outstanding', 'status': 'single_source', 'sources': ['fmp:shares_float']},
                    'cash_and_liquid_investments': {'field': 'cash_and_liquid_investments', 'status': 'stale', 'sources': ['fmp:as_reported_balance_annual']}},
         'resolved': {'shares_outstanding': {'value': inst['shares_outstanding_vendor']['value'], 'as_of': inst['shares_outstanding_vendor']['period_end'],
                                             'basis': 'vendor_float', 'source_id': 'fmp:shares_float'},
                      'cash': {'value': inst['cash']['value'], 'as_of': inst['cash']['period_end'], 'source_id': 'fmp:as_reported_balance_annual'},
                      'short_term_investments': {'value': inst['short_term_investments']['value'], 'as_of': inst['short_term_investments']['period_end']},
                      'long_term_investments': {'value': 0.0, 'as_of': inst['cash']['period_end']},
                      'debt': {'value': inst['long_term_debt_tagged']['value'], 'as_of': inst['long_term_debt_tagged']['period_end'], 'method': 'tagged_total'}}}
    fz = scorecards(rows, 'software')
    metrics = {'sbc_pct_revenue': last['sbc'] / last['revenue'], 'capex_pct_revenue': last['capex'] / last['revenue'],
               'cfo_to_net_income': last['cfo'] / last['net_income'], 'revenue_cagr_3y': (last['revenue'] / three['revenue']) ** (1 / 3) - 1,
               'share_count_cagr_3y': (last['shares_diluted_wavg'] / three['shares_diluted_wavg']) ** (1 / 3) - 1,
               'operating_margin_best_3y': max(t[e]['operating_income'] / t[e]['revenue'] for e in ends[-3:]), 'roic': fz['roic']['roic_last'],
               'net_cash': inst['cash']['value'] + inst['short_term_investments']['value'] - inst['long_term_debt_tagged']['value']}
    an = {'years': ends, 'annual': {e: {'revenue': r['revenue'], 'net_income': r['net_income'], 'fcf': r['cfo'] - r['capex']} for e, r in t.items()},
          'ttm': {}, 'metrics': {k: m(v) for k, v in metrics.items()}, 'forensics': fz, 'flags': []}
    cd = card(stage='maturing', completeness='partial',
              dims={'technology_ai': {'assessment': 'weak', 'evidence_ids': [], 'note': 'generative AI tools may commoditise creative workflows', 'source': 'analyst'}})
    ids = ['illustrative']
    spec = {'driver_version': 1, 'is_demo': False, 'as_of': as_of, 'company_id': 'adobe', 'ticker': 'ADBE', 'currency': 'USD', 'unit': 'absolute',
            'archetype': 'software', 'method': 'operating',
            'quote': {'price': fb['market']['price']['value'], 'currency': 'USD', 'as_of': as_of, 'source_ids': ['ib:snapshot']},
            'sources': [{'id': 'fmp:as_reported_balance_annual', 'title': 'Adobe FY2025 10-K via FMP as-reported', 'url': 'https://financialmodelingprep.com',
                         'kind': 'filing', 'retrieval': 'provider_normalized', 'published_at': '2026-01-14', 'available_at': '2026-01-16'},
                        {'id': 'ib:snapshot', 'title': 'IB last trade', 'url': 'ib://snapshot', 'kind': 'market_feed', 'published_at': as_of, 'available_at': as_of}],
            'assumptions': [{'id': 'illustrative', 'kind': 'analyst', 'as_of': as_of, 'review_status': 'unreviewed', 'source_ids': ['fmp:as_reported_balance_annual'],
                             'rationale': 'Illustrative test drivers on real FY2025 figures; not a forecast or a recommendation.',
                             'falsifier': 'Not used for any investment conclusion.'}],
            'opening': {'as_of': as_of, 'assumption_ids': ids, 'unrestricted_cash': inst['cash']['value'],
                        'marketable_investments': inst['short_term_investments']['value'], 'debt': inst['long_term_debt_tagged']['value'],
                        'shares': inst['shares_outstanding_vendor']['value'], 'deferred_revenue': 7.03e9, 'tax_loss_carryforward': 0},
            'boundaries': ['2027-10-19', '2028-10-19', '2029-10-19', '2030-10-19', '2031-10-19', '2032-10-19'],
            'last_full_year_revenue': last['revenue'], 'segment_name': 'Digital Media and Experience', 'scenarios': []}
    spec['report_dates'] = [as_of] + spec['boundaries'][:5]
    for name, growth, ccr, tm in (('bear', [.05, .04, .035, .03, .03, .025], .55, .30), ('base', [.09, .08, .07, .06, .05, .04], .53, .34),
                                  ('bull', [.12, .11, .10, .08, .07, .05], .51, .37)):
        spec['scenarios'].append({'name': name, 'thesis': f'Illustrative {name} path (test assumption).', 'assumption_ids': ids, 'cost_of_equity': .095,
                                  'growth': growth, 'cash_cost_ratio': ccr, 'sbc_pct': .08, 'depreciation_pct': .035, 'maintenance_capex_pct': .0075,
                                  'growth_capex_pct': 0, 'tax_rate': .19, 'interest_rate': .04, 'deferred_revenue_ratio': .3, 'working_capital_ratio': 0,
                                  'prepaid_share': .8, 'payout_fraction': 1.0, 'minimum_cash': 2e9,
                                  'terminal': {'assumption_ids': ids, 'growth': .025, 'wacc': .085, 'roic': .25, 'operating_margin': tm, 'tax_rate': .19,
                                               'sbc_in_margin': True, 'net_runoff_obligation': 0, 'other_claims': 0,
                                               'working_capital_rationale': 'Deferred revenue renews in a going concern.'}})
    return fb, q, an, cd, forecast(spec, risk_free=.0425), spec


class AdobeRealHistory(unittest.TestCase):
    def test_quality_compounder_is_not_rejected_and_risks_are_specific(self):
        fb, q, an, cd, fc, spec = adbe_inputs()
        rk, rv = run(spec, fb=fb, q=q, an=an, cd=cd, fc=fc)
        self.assertEqual(rk['disqualifying'], [])
        self.assertEqual(entry(rk, 'dil.sbc')['severity'], 'medium')               # 1,942 / 23,769 = 8.2% of revenue
        self.assertIsNone(entry(rk, 'dil.share_growth'))                            # diluted shares shrank ~3%/yr
        self.assertFalse(any(r['id'].startswith('acc.') and r['severity'] == 'high' for r in rk['register']))
        ai = entry(rk, 'biz.technology_ai')
        self.assertEqual(ai['severity'], 'medium')
        self.assertIn('without evidence ids', ai['description'])
        liq = rk['liquidity']
        self.assertEqual(liq['near_term_maturities'], 0)                            # FY2024 current portion is not carried into FY2025
        self.assertIn('2025-11-28', liq['maturity_basis'])
        self.assertFalse(liq['refinancing_need'])
        self.assertEqual({r['id'] for r in rk['register'] if r['category'] == 'data'}, {'data.limited.shares_outstanding', 'data.limited.cash_and_liquid_investments'})
        bear, perm = rk['failure_scenarios'][:2]
        self.assertEqual(perm['name'], 'permanent_loss:growth_stall')
        self.assertLess(perm['value_per_share'], bear['value_per_share'])
        self.assertGreater(rk['downside']['loss_to_permanent'], rk['downside']['loss_to_bear'])
        rt = rv['mechanical']['redteam']
        self.assertEqual(rt['derived_inputs']['revenue_basis']['value'], 'recognized')
        self.assertTrue(rt['usable'])
        self.assertFalse(rv['price_conclusion_allowed'])                            # no underwriting block, unreviewed: a draft
        self.assertEqual(rv['status'], 'unreviewed_draft')
        self.assertIn('underwriting_incomplete', rv['reason_codes'])
        c = {x['code']: x for x in rv['mechanical']['consistency']}
        self.assertEqual(c['opening_vs_validated_data']['status'], 'pass')
        self.assertEqual(c['share_path_vs_history']['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
