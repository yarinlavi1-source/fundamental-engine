"""Risk register / failure scenarios (risk.py) and the independent review stage (review.py).

Fixtures: the fictional software driver example (is_demo), synthetic NON-demo variants of it (test doubles:
their 'filing' sources and numbers are fictional, labelled as such), and the real ADBE FactBook (10-K values)
with hand-built dataqa/analysis inputs and an ILLUSTRATIVE driver spec (test assumptions, not a forecast).
Upstream stage outputs are constructed by hand per docs/MODULE_CONTRACTS.md so these tests do not depend on
other modules' heuristics. Reviews are dated against a fixed clock (TODAY); signed() binds to the executed
valuation and to the default inputs digest (no analyst inputs) unless a test overrides valuation_sha256/inputs_sha256.
"""
import json
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from fundamental_engine import facts as F, render, risk, review
from fundamental_engine.drivers import build_case
from fundamental_engine.scores import scorecards
from fundamental_engine.valuation import value_company

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'examples/valuation/software_drivers.json').read_text())
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
AS_OF = '2026-09-27'
TODAY = date(2026, 10, 1)                      # injected review clock
BIND = 'bind-to-executed-valuation'            # run() replaces this with the executed valuation's input_sha256 / inputs digest


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
         'statement': 'I read the casefile and the sources; the assumptions are supportable.', 'valuation_sha256': BIND, 'inputs_sha256': BIND}
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


def run(spec=None, fb=None, q=None, an=None, cd=None, fc=None, val=None, reviewer=None, today=TODAY, **bind):
    spec = live_spec() if spec is None else spec
    fb, q, an, cd = fb or factbook(), q or qa(), an or analysis(), cd or card()
    fc, val = fc or forecast(spec), val or valuate(spec)
    rk = risk.assess(fb, q, an, cd, fc, val)
    if isinstance(reviewer, dict) and reviewer.get('valuation_sha256') == BIND:
        reviewer = dict(reviewer, valuation_sha256=(val.get('result') or {}).get('input_sha256'))
    if isinstance(reviewer, dict) and reviewer.get('inputs_sha256') == BIND:
        reviewer = dict(reviewer, inputs_sha256=review.inputs_digest(val))
    rv = review.audit(fb, q, an, cd, fc, val, rk, reviewer, today, **bind)
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
        sha, ish = val['result']['input_sha256'], review.inputs_digest(val)
        cases = {'independent': signed(independent=False, valuation_sha256=sha, inputs_sha256=ish),
                 'precedes the valuation cutoff': signed(reviewed_at='2026-09-01', valuation_sha256=sha, inputs_sha256=ish),
                 'unknown assumption id': signed(assumption_reviews={'a1': 'reviewed', 'zz': 'reviewed'}, valuation_sha256=sha, inputs_sha256=ish),
                 'needs a response': signed(findings=[{'severity': 'minor', 'text': 'x', 'resolved': True}], valuation_sha256=sha, inputs_sha256=ish),
                 'different case': signed(valuation_sha256='0' * 64, inputs_sha256=ish)}
        for needle, r in cases.items():
            _, rv = run(spec, val=val, reviewer=r)
            self.assertFalse(rv['price_conclusion_allowed'], needle)
            self.assertTrue(any(needle in e for e in rv['reviewer_check']['errors']), (needle, rv['reviewer_check']['errors']))
            self.assertEqual(rv['reviewer'], r)                                     # recorded as supplied
        _, rv = run(spec, val=val, reviewer=signed(valuation_sha256=sha))
        self.assertTrue(rv['price_conclusion_allowed'])
        self.assertTrue(rv['reviewer_check']['bound_to_valuation'])
        self.assertTrue(rv['reviewer_check']['accepted'])

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

    def test_growth_alone_never_disqualifies(self):
        # NVIDIA fiscal 2027 shape: M-score in the zone only through growth, accruals above 10% from working capital, CFO 86% of NI
        grow = deepcopy(SAFE)
        grow['beneish'] = {'status': 'computed', 'zone': 'likely_manipulator', 'm': -1.16, 'zone_growth_neutral': 'watch',
                           'm_growth_neutral': -1.74 - .1, 'components': {'SGI': 1.65}}
        rk, _ = run(an=analysis(grow, cfo_to_net_income=.86, accruals_ratio=.11))
        self.assertNotIn('gate.manipulation_signal', rk['disqualifying'])
        self.assertEqual(entry(rk, 'acc.beneish')['severity'], 'high')               # still a question in the register
        grow['beneish']['zone_growth_neutral'] = 'likely_manipulator'
        rk, _ = run(an=analysis(grow, cfo_to_net_income=.86, accruals_ratio=.11))     # accruals alone: still not a disqualifier
        self.assertNotIn('gate.manipulation_signal', rk['disqualifying'])

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

    def test_unclassified_business_gets_medium_relevance_on_every_dimension(self):
        # regression: RELEVANCE.get(primary, 'MMMMMM') had 6 letters for 10 dimensions -> IndexError for every unclassified
        # business (publishing, hospitals, big pharma, a blank label), so pipeline.run produced no casefile
        rk, _ = run(cd=card(primary='unclassified', dims={}))
        ents = [e for e in rk['register'] if e['id'].startswith('biz.')]
        self.assertEqual(len(ents), len(risk.DIMENSIONS))
        self.assertEqual({e['severity'] for e in ents}, {'medium'})
        self.assertTrue(all(len(v) == len(risk.DIMENSIONS) for v in risk.RELEVANCE.values()))

    def test_pre_revenue_unlabelled_company_completes_the_pipeline(self):
        from fundamental_engine import pipeline
        fb = F.new({'ticker': 'PREREV', 'name': 'Pre-revenue synthetic test double', 'currency': 'USD', 'sector': '', 'industry': ''}, AS_OF)
        F.add_source(fb, 'syn', 'synthetic', 'synthetic statements', 'synthetic', AS_OF)
        for y, oi in ((2024, -40e6), (2025, -55e6)):
            for metric, v in (('operating_income', oi), ('net_income', oi), ('cfo', oi * .8), ('capex', 3e6)):
                F.add_fact(fb, metric, f'FY{y}', f'{y}-12-31', v, 'syn', 'as_reported', f'{y + 1}-03-01')
        for metric, v in (('cash', 300e6), ('shares_outstanding', 60e6), ('total_assets', 320e6), ('total_liabilities', 20e6)):
            F.add_fact(fb, metric, 'I@2025-12-31', '2025-12-31', v, 'syn', 'as_reported', '2026-03-01')
        F.set_price(fb, 9., AS_OF, 'syn')
        cf = pipeline.run(fb)
        self.assertEqual(cf['stages']['risk'], 'ok', cf['errors'])
        self.assertIn(cf['stages']['decision'], ('ok', 'failed'))           # decision may fail on its own; risk must not crash the run

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
        _, rv = run(an=an, reviewer=signed())                                      # a1 is generic (terminal ids), not margin-specific
        self.assertEqual(self.codes(rv)['margin_vs_history']['status'], 'needs_evidence')
        _, rv = run(fc=forecast(live_spec(), risk_free=None))
        self.assertEqual(self.codes(rv)['terminal_growth_vs_risk_free']['status'], 'skip')

    def test_opening_understating_shares_blocks(self):
        q = qa(shares_outstanding={'value': 1.2e8, 'as_of': AS_OF, 'basis': 'cover'})
        _, rv = run(q=q, reviewer=signed())
        c = self.codes(rv)['opening_vs_validated_data']
        self.assertEqual((c['status'], c['severity']), ('fail', 'critical'))
        self.assertIn('consistency_failure', rv['reason_codes'])
        self.assertEqual(rv['status'], 'blocked')


class ReviewerGate(unittest.TestCase):
    """Regressions: no price conclusion without an accepted (valid, independent, bound, dated) reviewer."""

    def test_author_review_status_is_a_note_not_a_signoff(self):
        spec = live_spec()
        spec['assumptions'][0].update(review_status='reviewed', reviewer='me (the analyst who wrote the forecast)')
        val = valuate(spec)
        self.assertEqual(val['result']['status'], 'conditional_valuation')         # the author's own status satisfies the audit
        self.assertTrue(val['result']['underwriting_audit']['eligible_for_research_synthesis'])
        _, rv = run(spec, val=val)
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('review_required', rv['reason_codes'])
        self.assertEqual(rv['mechanical']['valuation_audit']['author_review_notes']['assumption_ids'], ['a1'])
        self_review = signed(reviewer='me', independent=False)
        _, rv = run(spec, val=val, reviewer=self_review)                            # an invalid reviewer is not ignored
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('review_required', rv['reason_codes'])
        self.assertTrue(any('independent must be true' in e for e in rv['reviewer_check']['errors']))
        _, rv = run(spec, val=val, reviewer=signed(assumption_reviews={}))         # accepted, but signs nothing: author notes do not fill in
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('review_incomplete', rv['reason_codes'])
        _, rv = run(spec, val=val, reviewer=signed())
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])

    def test_review_must_be_bound_to_this_valuation(self):
        _, rv = run(reviewer=signed(valuation_sha256=None))
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertFalse(rv['reviewer_check']['valid'])
        self.assertTrue(any('valuation_sha256: required' in e for e in rv['reviewer_check']['errors']))
        spec_a, spec_b = live_spec(), live_spec()
        for s in spec_b['scenarios']:
            s['terminal']['operating_margin'] += .08                               # revision B, never seen by the reviewer
        review_of_a = signed(valuation_sha256=valuate(spec_a)['result']['input_sha256'])
        _, rv_a = run(spec_a, reviewer=review_of_a)
        _, rv_b = run(spec_b, reviewer=review_of_a)
        self.assertTrue(rv_a['price_conclusion_allowed'])
        self.assertFalse(rv_b['price_conclusion_allowed'])
        self.assertFalse(rv_b['reviewer_check']['bound_to_valuation'])

    def test_reviewed_at_must_lie_between_cutoff_and_today(self):
        early = signed(reviewed_at='2026-10-05')
        _, rv = run(reviewer=early, today=date(2026, 10, 1))
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertTrue(any('after today 2026-10-01' in e for e in rv['reviewer_check']['errors']), rv['reviewer_check']['errors'])
        _, rv = run(reviewer=early, today='2026-10-10')
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])
        _, rv = run(reviewer=signed(reviewed_at='2099-12-31'), today=None)          # default clock: date.today()
        self.assertFalse(rv['reviewer_check']['valid'])
        self.assertFalse(rv['price_conclusion_allowed'])

    def test_an_author_side_signer_cannot_be_the_independent_reviewer(self):
        spec = live_spec()
        spec['assumptions'][0].update(review_status='reviewed', reviewer='Jane Analyst (author)')
        val = valuate(spec)
        _, rv = run(spec, val=val, reviewer=signed(reviewer='jane  ANALYST'))      # case assumption reviewer, normalized
        rc = rv['reviewer_check']
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertEqual((rc['valid'], rc['independent'], rc['accepted'], rc['self_review']), (False, False, False, ['Jane Analyst (author)']))
        self.assertIn('review_required', rv['reason_codes'])
        self.assertTrue(any('author-side signer' in e for e in rc['errors']), rc['errors'])
        inputs = {'analyst': 'Lavi, Dana', 'forecast': {'reviews': {'a1': {'status': 'unreviewed', 'reviewer': 'Omer Ben-Ami'}}}}
        authors = review.input_authors(inputs)
        self.assertEqual(authors, ['Lavi, Dana', 'Omer Ben-Ami'])
        for name in ('Dana Lavi', 'omer ben ami (independent)'):                     # analyst named in inputs; forecast 'reviews' signer
            _, rv = run(reviewer=signed(reviewer=name), authors=authors)
            self.assertFalse(rv['price_conclusion_allowed'], name)
            self.assertFalse(rv['reviewer_check']['independent'], name)
            self.assertEqual(len(rv['reviewer_check']['self_review']), 1, name)
        _, rv = run(reviewer=signed(reviewer='Noa Second Reader'), authors=authors)
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])
        self.assertEqual(rv['reviewer_check']['self_review'], [])

    def test_review_is_bound_to_the_analyst_inputs(self):
        val = valuate(live_spec())
        self.assertEqual(review.inputs_digest(val), review.inputs_digest(val, {'decision': {}, 'reviewer': {'x': 1}}))  # empty = none; reviewer excluded
        after = {'decision': {'overrides': [{'target': 'business_quality', 'value': 'strong', 'justification': 'added after the review was signed',
                                             'evidence_ids': ['syn']}]}}
        for k, v in (('decision', after['decision']), ('card', {'business_model': {'primary': 'industrial'}}), ('forecast', {'horizon_years': 7})):
            self.assertNotEqual(review.inputs_digest(val, {k: v}), review.inputs_digest(val), k)
        _, rv = run(val=val, reviewer=signed(), inputs_sha256=review.inputs_digest(val, after))   # signed before the override was added
        rc = rv['reviewer_check']
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertEqual((rc['bound_to_valuation'], rc['bound_to_inputs'], rc['accepted']), (True, False, False))
        self.assertTrue(any('inputs_sha256 does not match' in e for e in rc['errors']), rc['errors'])
        self.assertEqual(rv['binding']['inputs_sha256'], review.inputs_digest(val, after))
        _, rv = run(val=val, reviewer=signed(inputs_sha256=review.inputs_digest(val, after)), inputs_sha256=review.inputs_digest(val, after))
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])
        _, rv = run(val=val, reviewer=signed(inputs_sha256=None))
        self.assertFalse(rv['reviewer_check']['accepted'])
        self.assertTrue(any('inputs_sha256: required' in e for e in rv['reviewer_check']['errors']))

    def test_gap_no_signoff_can_close_is_primary_evidence_missing(self):
        spec = live_spec()
        spec['sources'][0]['kind'] = 'document'                                    # the 10-K reached only as a generic document
        val = valuate(spec)
        self.assertTrue({i['code'] for i in val['result']['underwriting_audit']['issues']} <= review.REVIEW_ONLY)
        for reviewer in (None, signed()):
            _, rv = run(spec, val=val, reviewer=reviewer)
            self.assertFalse(rv['price_conclusion_allowed'])
            self.assertEqual(rv['reason_codes'], ['primary_evidence_missing'])
            self.assertIn('cannot close', rv['reasons'][0])
            self.assertFalse(rv['mechanical']['valuation_audit']['full_signoff_would_close'])
            self.assertEqual(rv['status'], 'unreviewed_draft')


class ErrorSizeScope(unittest.TestCase):
    def test_process_gaps_do_not_raise_the_valuation_error(self):
        rk0, _ = run()
        rk1, _ = run(q=qa('limited', limited=['debt']), cd=card(completeness='partial', dims={}))
        e0, e1 = rk0['error_size'], rk1['error_size']
        self.assertEqual(e0['level'], 'medium')
        self.assertEqual(e1['level'], e0['level'])                                   # same valuation, same possible valuation error
        self.assertEqual(e1['reasons'], e0['reasons'])
        self.assertEqual([g['code'] for g in e0['process_gaps']], ['dimensions_unassessed'])     # moat, retention not in the card
        self.assertEqual([g['code'] for g in e1['process_gaps']], ['data_limited', 'understanding_partial', 'dimensions_unassessed'])
        self.assertIn('biz.competition', next(g for g in e1['process_gaps'] if g['code'] == 'dimensions_unassessed')['ids'])
        rk2, _ = run(q=qa('blocked', blocking=['debt']), cd=card(completeness='insufficient'))
        self.assertEqual(rk2['error_size']['level'], 'medium')
        self.assertEqual({g['code']: g['severity'] for g in rk2['error_size']['process_gaps']},
                         {'data_blocked': 'high', 'understanding_insufficient': 'high', 'dimensions_unassessed': 'medium'})


class PermanentLossSeverity(unittest.TestCase):
    def test_shrinking_bear_permanent_loss_is_not_milder_than_bear(self):
        spec = live_spec()
        bear_growth = [-.20, -.15, -.10, -.05, -.02, 0., 0.]
        scen(spec, 'bear')['growth'] = bear_growth
        for kind, cd, expect in (('growth_stall', card(), bear_growth), ('funding_failure', card(stage='early'), [g * 1.5 for g in bear_growth])):
            s2, _ = risk.variant_spec(spec, kind)
            for got, want in zip(scen(s2, 'bear')['growth'], expect):
                self.assertAlmostEqual(got, want)
            rk, _ = run(spec, cd=cd)
            by = {s['name']: s for s in rk['failure_scenarios']}
            perm = by['permanent_loss:' + kind]
            self.assertLess(perm['value_per_share'], by['bear']['value_per_share'], kind)
            self.assertIs(perm['not_more_severe_than_bear'], False)
            self.assertGreater(rk['downside']['loss_to_permanent'], rk['downside']['loss_to_bear'])
        s2, _ = risk.variant_spec(live_spec(), 'funding_failure')                  # growing bear: half the growth, as before
        self.assertAlmostEqual(scen(s2, 'bear')['growth'][0], scen(live_spec(), 'bear')['growth'][0] / 2)

    def test_variant_not_below_bear_is_flagged(self):
        p = risk._permanent(live_spec(), 'growth_stall', -.25, 50, bear_value=0.)
        self.assertEqual(p['status'], 'computed')
        self.assertIs(p['not_more_severe_than_bear'], True)
        self.assertIn('not below the bear', p['severity_note'])

    def test_variants_drop_the_bears_own_funding_plan(self):
        # regression (2026-10-20 review): every variant kept the bear's assumed equity/debt and its SBC settled in shares, so
        # value_company raised ('Funding marked none but cash financing supplied'; 'SBC share settlement exceeds the SBC expense')
        # and the funding-dependent names lost their permanent-loss value
        spec = live_spec()
        n = len(spec['boundaries'])
        stub = spec['stub_annual_revenue_rate'] * (date(2026, 12, 31) - date(2026, 9, 27)).days / 365.25
        scen(spec, 'bear').update(financing_status='assumed', debt_draw=[0., 50e6] + [0.] * (n - 2), interest_rate=[.05] + [.06] * (n - 1),
                                  equity_proceeds=[0., 0., 80e6] + [0.] * (n - 3),
                                  sbc_share_settlement=[.099 * stub, .099 * 950e6 * 1.1] + [0.] * (n - 2))
        self.assertIsNotNone(value_company(build_case(spec))['annual_values'][0]['bear'])     # the bear itself is a valid case
        for kind in ('funding_failure', 'growth_stall', 'cyclical_collapse', 'utilization_collapse'):
            s2, edits = risk.variant_spec(spec, kind)
            vb = scen(s2, 'bear')
            self.assertEqual(vb['financing_status'], 'none', kind)
            for k in ('equity_proceeds', 'debt_draw', 'sbc_share_settlement'):
                self.assertEqual(sum(vb[k]), 0., (kind, k))
            if kind in ('funding_failure', 'growth_stall'):
                self.assertEqual(vb['interest_rate'], [.05] * n)                                 # existing-debt rate, no assumed new money
            self.assertIn("bear's own funding plan removed", edits[0])
            p = risk._permanent(spec, kind, -.25, 50)
            self.assertEqual(p['status'], 'computed', (kind, p.get('reason')))
            self.assertIsNotNone(p['value_per_share'])

    def test_variant_gaps_use_investments_before_a_depressed_raise(self):
        spec = live_spec()
        bear = scen(spec, 'bear')
        bear.update(cash_cost_ratio=[.95] * len(spec['boundaries']), payout_fraction=0.)       # a burner: the variant runs short
        detail = {}
        case = build_case(risk.variant_spec(spec, 'growth_stall')[0])
        res, raised, issue = risk.fund_with_equity(case, 'bear', 50, detail)
        self.assertIsNotNone(res)
        self.assertGreater(detail['investments_liquidated'], 99e6)                              # the 100m of investments go first
        self.assertGreater(raised, 0)
        self.assertEqual(issue, 50 * (1 - risk.ISSUE_DISCOUNT))


class SbcAndMarginSignoff(unittest.TestCase):
    def test_token_sbc_is_not_expensed(self):
        spec = live_spec()
        for s in spec['scenarios']:
            s['sbc_pct'] = .002                                                    # history 12% of revenue
        rk, rv = run(spec, reviewer=signed())
        rt = rv['mechanical']['redteam']
        self.assertIsNone(rt['derived_inputs']['sbc_treatment']['value'])
        self.assertEqual(rt['derived_inputs']['margin_basis']['value'], 'adjusted_ebitda')
        self.assertIn('sbc', rt['critical_failed'])
        self.assertFalse(rv['price_conclusion_allowed'])
        fade = {'id': 'sbc_fade', 'topic': 'stock-based compensation fade', 'kind': 'guidance', 'source_ids': ['10k']}
        _, rv = run(spec, fc=forecast(spec, [fade]), reviewer=signed())
        d = rv['mechanical']['redteam']['derived_inputs']['sbc_treatment']
        self.assertEqual(d['value'], 'expensed')
        self.assertIn('evidenced fade', d['derived_from'])
        _, rv = run(reviewer=signed())                                             # 10% of revenue modelled vs 12% history: expensed
        self.assertEqual(rv['mechanical']['redteam']['derived_inputs']['sbc_treatment']['value'], 'expensed')

    def test_margin_signoff_needs_a_margin_specific_assumption(self):
        an = analysis(operating_margin_best_3y=.05)
        _, rv = run(an=an, reviewer=signed())                                      # only the terminal/generic id a1 is signed
        c = next(x for x in rv['mechanical']['consistency'] if x['code'] == 'margin_vs_history')
        self.assertEqual(c['status'], 'needs_evidence')
        margin = {'id': 'a1', 'topic': 'mature operating margin (after SBC)', 'kind': 'analyst', 'source_ids': ['10k']}
        _, rv = run(an=an, fc=forecast(live_spec(), [margin]), reviewer=signed())
        c = next(x for x in rv['mechanical']['consistency'] if x['code'] == 'margin_vs_history')
        self.assertEqual((c['status'], c['detail']['margin_assumption_ids']), ('reviewed', ['a1']))
        _, rv = run(an=an, fc=forecast(live_spec(), [margin]), reviewer=signed(independent=False))
        c = next(x for x in rv['mechanical']['consistency'] if x['code'] == 'margin_vs_history')
        self.assertEqual(c['status'], 'needs_evidence')                            # a sign-off counts only from an accepted reviewer


class GovernanceDimensions(unittest.TestCase):
    def test_weak_governance_and_management_enter_the_register(self):
        cd = card(dims={'governance': {'assessment': 'weak', 'evidence_ids': ['syn'], 'note': 'dual-class shares, related-party leases'},
                        'management': {'assessment': 'weak', 'evidence_ids': ['syn'], 'note': 'CFO resigned; auditor dismissed'}})
        rk, rv = run(cd=cd, reviewer=signed())
        for rid in ('biz.governance', 'biz.management'):
            r = entry(rk, rid)
            self.assertEqual((r['class'], r['severity'], r['assessed']), ('pricing', 'high', True), rid)
        self.assertEqual(rk['withhold_price'], [])                                 # weak, but no evidenced red flag
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])
        rk, _ = run(cd=card(dims={}))
        self.assertEqual({entry(rk, f'biz.{d}')['status'] for d in ('governance', 'management', 'moat', 'retention')}, {'unassessed'})
        self.assertEqual(entry(rk, 'biz.retention')['severity'], 'high')            # subscription software

    def test_evidenced_red_flags_withhold_the_price_until_a_documented_override(self):
        cd = card(dims={'governance': {'assessment': 'weak', 'evidence_ids': ['syn'], 'note': 'material weakness in internal control; restatement of FY2024'}})
        fb = factbook()
        F.add_source(fb, 'k10_next', 'issuer', 'following annual report (test double)', 'issuer_document', AS_OF)
        F.add_qualitative(fb, 'q_gc', 'auditor report', 'The auditor expressed substantial doubt about the ability to continue as a going concern.',
                          'reported', ['syn'], AS_OF)
        rk, rv = run(fb=fb, cd=cd, reviewer=signed())
        self.assertEqual(rk['withhold_price'], ['gov.going_concern', 'gov.material_weakness', 'gov.restatement'])
        self.assertEqual(rk['disqualifying'], [])                                   # withheld, not vetoed
        self.assertIn('q_gc', entry(rk, 'gov.going_concern')['evidence_ids'])
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertIn('governance_red_flag', rv['reason_codes'])
        self.assertEqual(rv['status'], 'unreviewed_draft')
        why = 'Remediation was completed and tested by the auditor in the following annual report.'
        ov = {rid: {'justification': why, 'evidence_ids': ['k10_next']} for rid in rk['withhold_price']}
        _, rv = run(fb=fb, cd=cd, reviewer=signed(risk_overrides=ov))
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])
        self.assertEqual(rv['risk_overrides_applied'], rk['withhold_price'])
        _, rv = run(fb=fb, cd=cd, reviewer=signed(risk_overrides={'gov.going_concern': {'justification': 'fine', 'evidence_ids': ['zz']}}))
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertTrue(any('risk_overrides.gov.going_concern' in e for e in rv['reviewer_check']['errors']))

    def test_risk_override_needs_filing_documents_beyond_the_flags_own_evidence(self):
        fb = factbook()
        for sid, kind in (('k10_fy25', 'issuer_document'), ('k10a', 'issuer_document'), ('snap', 'market_feed'), ('cons', 'consensus'),
                          ('vend', 'vendor_normalized'), ('press', 'secondary')):
            F.add_source(fb, sid, 'test', f'{sid} (test double)', kind, AS_OF)
        F.add_qualitative(fb, 'q_gc', 'auditor report', 'The auditor expressed substantial doubt about the ability to continue as a going concern.',
                          'reported', ['k10_fy25'], AS_OF)
        rk, _ = run(fb=fb)
        self.assertEqual(rk['withhold_price'], ['gov.going_concern'])
        self.assertEqual(set(entry(rk, 'gov.going_concern')['evidence_ids']), {'q_gc', 'k10_fy25'})
        why = 'The amended annual report records the completed refinancing and the auditor withdrew the doubt.'

        def release(ev):
            return run(fb=fb, reviewer=signed(risk_overrides={'gov.going_concern': {'justification': why, 'evidence_ids': ev}}))[1]
        # a filing document alongside never launders a weaker id; '10k' is an analyst-written case source, not a FactBook source
        for sid, cls in (('snap', 'market_feed'), ('cons', 'consensus'), ('vend', 'vendor'), ('syn', 'synthetic'), ('press', 'secondary'),
                         ('10k', 'analyst_input')):
            rv = release([sid, 'k10a'])
            self.assertFalse(rv['price_conclusion_allowed'], sid)
            self.assertIn('governance_red_flag', rv['reason_codes'])
            self.assertEqual(rv['risk_overrides_applied'], [])
            self.assertTrue(any(f'{sid} ({cls})' in e for e in rv['reviewer_check']['errors']), rv['reviewer_check']['errors'])
        rv = release(['k10_fy25', 'q_gc'])                                          # the flag's own evidence never releases it
        self.assertFalse(rv['price_conclusion_allowed'])
        self.assertTrue(any("besides the flag's own evidence" in e and 'k10_fy25, q_gc is ignored' in e for e in rv['reviewer_check']['errors']),
                        rv['reviewer_check']['errors'])
        rv = release(['k10_fy25', 'k10a'])                                          # own evidence may be cited beside a fresh filing document
        self.assertTrue(rv['price_conclusion_allowed'], rv['reasons'])
        self.assertEqual(rv['risk_overrides_applied'], ['gov.going_concern'])

    def test_negated_or_unevidenced_statements_are_not_red_flags(self):
        fb = factbook()
        F.add_qualitative(fb, 'q_icfr', 'internal control', 'Management concluded there was no material weakness in internal control.', 'reported', ['syn'], AS_OF)
        F.add_qualitative(fb, 'q_op', 'short report', 'A short seller alleges a restatement of revenue is coming.', 'opinion', ['syn'], AS_OF)
        cd = card(dims={'governance': {'assessment': 'weak', 'evidence_ids': [], 'note': 'possible material weakness (asserted)'}})
        rk, rv = run(fb=fb, cd=cd, reviewer=signed())
        self.assertEqual(rk['withhold_price'], [])
        self.assertNotIn('governance_red_flag', rv['reason_codes'])


class RenderReview(unittest.TestCase):
    def test_review_section_names_checks_that_pass_by_construction(self):
        _, rv = run(reviewer=signed())
        text = '\n'.join(render._review({'rv': rv}))
        self.assertIn('capital structure from dataqa_resolved', text)
        self.assertIn('passing by construction, not independent evidence: cash_reconciles, debt_complete, shares_current', text)


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
