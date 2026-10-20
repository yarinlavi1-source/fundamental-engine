"""Decision and rendering: four separate questions, the closed verdict set, overrides and the Hebrew paragraph.

Fixtures are hand-built stand-ins for the parallel pipeline stages (dataqa, analysis, model_card, risk,
review), shaped per docs/MODULE_CONTRACTS.md. FICTSAAS is the repository's synthetic software driver
example (sources relabelled as a test stand-in where a non-demo case is needed). The ADBE parts use the
real 10-K fixture plus TEST-ONLY forecast paths that are not an underwritten view of Adobe.
"""
import json
import statistics
import unittest
from copy import deepcopy
from pathlib import Path

from fundamental_engine import facts as F
from fundamental_engine.decision import decide, price_for_hurdle, ASSESSMENTS, VERDICTS
from fundamental_engine.drivers import build_case
from fundamental_engine.render import hebrew_paragraph, markdown, CALL_HE
from fundamental_engine.valuation import value_company
from fundamental_engine.valuation_tools import discount_rate_band, implied_cost_of_equity

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'examples/valuation/software_drivers.json').read_text())
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
CERTAINTY = ('בוודאות', 'ודאי', 'מובטח', 'בטוח', 'חד משמעית', 'ללא סיכון', 'אין סיכון')
SECTIONS = ('## In one paragraph', '## Verdict', '## The four questions', '## Expected return', '## Business understanding',
            '## Financial analysis', '## Data validation', '## Forecast assumptions', '## Valuation', '## Risks and failure scenarios',
            '## Review: mechanical check and independent reviewer', '## Limitations', '## Provenance legend')


def valuation_of(spec, price=None, demo=False):
    """pipeline.valuate stand-in: drivers.build_case -> value_company (+ the diagnostic decide reads)."""
    s = deepcopy(spec)
    s['is_demo'] = demo
    if price is not None:
        s['quote']['price'] = price
    if not demo:
        for src in s['sources']:
            if src['kind'] == 'synthetic':
                src.update(kind='filing', title='Test stand-in for a filing (synthetic numbers)', url='https://example.invalid/test-filing')
    case = build_case(s)
    res = value_company(case)
    diag = {'discount_rate_band': discount_rate_band(case)} if res['annual_values'][0]['base'] is not None else {}
    return {'status': res['status'], 'case': case, 'result': res, 'diagnostics': diag, 'what_must_be_true': [], 'errors': []}


def fb_saas(price):
    fb = F.new({'ticker': 'FICTSAAS', 'name': 'Fictional SaaS', 'currency': 'USD'}, '2026-09-27')
    F.add_source(fb, 'synthetic', 'synthetic', 'test fixture', 'synthetic', '2026-09-27')
    # a filing stand-in: override evidence must be a filing/issuer document or a reported/guidance statement, never synthetic data
    F.add_source(fb, 'test-10k', 'issuer', 'annual report (test stand-in)', 'issuer_document', '2026-09-27')
    F.add_qualitative(fb, 'q-retention', 'retention', 'Net revenue retention above 115% (test statement)', 'reported', ['synthetic'], '2026-09-27')
    F.set_price(fb, price, '2026-09-27', 'synthetic')
    return fb


def M(v, unit='ratio'):
    return {'value': v, 'unit': unit, 'formula': 'test fixture', 'inputs': [], 'period': 'FY2025', 'status': 'reported'}


SAAS_METRICS = dict(roic=.24, gross_margin=.78, operating_margin=.19, fcf_margin=.21, cfo_to_net_income=1.25, fcf_after_sbc_margin=.11,
                    revenue_cagr_3y=.16, revenue_growth_last=.14, operating_margin_trend_3y=.03, acquisition_spend_pct_revenue_3y=.02,
                    incremental_roic_3y=.22, share_count_cagr_3y=.01, growth_volatility=.04, sbc_pct_revenue=.10, net_cash=3e8,
                    interest_coverage=25., net_debt_to_ebitda=-1.)


def an_saas(**over):
    m = {**SAAS_METRICS, **over}
    years = ['2021-12-31', '2022-12-31', '2023-12-31', '2024-12-31', '2025-12-31']
    annual = {}
    for i, y in enumerate(years):
        rev = 6e8 * 1.16 ** i
        annual[y] = {'revenue': rev, 'gross_profit': .78 * rev, 'operating_income': (.16 + .01 * i) * rev, 'fcf': .21 * rev,
                     'fcf_after_sbc': .11 * rev, 'sbc': .1 * rev, 'shares_diluted_wavg': 97e6 + i * 1e6, 'cash': 4e8, 'investments': 1e8,
                     'debt': 2e8, 'invested_capital': .8 * rev}
    return {'years': years, 'annual': annual, 'metrics': {k: M(v) for k, v in m.items() if v is not None},
            'ttm': {'period_end': '2026-06-30', 'method': 'quarters', 'revenue': 9.8e8, 'fcf': 2.1e8, 'fcf_after_sbc': 1.1e8, 'cash': 4e8, 'investments': 1e8},
            'flags': [], 'forensics': {'status': 'unavailable', 'reason': 'test fixture'}}


def card_saas(**over):
    c = {'business_model': {'primary': 'subscription_software', 'secondary': [], 'reasons': ['recurring subscription revenue (test)']},
         'stage': {'value': 'growth', 'computed': 'growth'}, 'cyclical': {'value': False}, 'capital_intensity': {'value': 'low'},
         'funding': {'value': 'self_funded', 'reasons': ['positive FCF']}, 'playbooks': ['software'],
         'driver_tree': [{'driver': 'net new ARR', 'why': 'growth', 'metric': 'revenue_growth_last', 'data_status': 'reported'}],
         'valuation': {'method': 'fcff_annual_path', 'archetype': 'software', 'why': 'positive, recurring cash flows', 'cross_checks': [], 'limitations': []},
         'understanding': [{'id': 'u-model', 'question': 'How does the company make money?', 'critical': True, 'status': 'evidenced',
                            'answer': 'Subscriptions', 'evidence_ids': ['synthetic']}],
         'dimensions': {'switching_costs': {'assessment': 'strong', 'evidence_ids': ['q-retention'], 'source': 'analyst', 'note': 'test'},
                        'retention': {'assessment': 'strong', 'evidence_ids': ['q-retention'], 'source': 'analyst'}},
         'completeness': {'status': 'complete', 'missing': []}}
    c.update(over)
    return c


QA_OK = {'status': 'ok', 'fields': {'shares_outstanding': {'field': 'shares_outstanding', 'status': 'verified', 'value': 1e8, 'unit': 'shares',
                                                           'as_of': '2026-06-30', 'sources': ['synthetic'], 'note': None}},
         'checks': [], 'blocking': [], 'limited': []}
RK = {'register': [{'id': 'R-comp', 'category': 'competition', 'description': 'Bundled suites undercut pricing (test)', 'class': 'pricing',
                    'severity': 'high', 'in_bear_case': True, 'evidence_ids': ['synthetic']}],
      'failure_scenarios': [], 'downside': {}, 'error_size': {'level': 'medium', 'reasons': ['test']}, 'disqualifying': []}
RV_OK = {'status': 'reviewed', 'price_conclusion_allowed': True, 'reasons': [], 'mechanical': {'dataqa': 'ok'}, 'reviewer': None}
RV_DRAFT = {'status': 'unreviewed_draft', 'price_conclusion_allowed': False, 'reasons': ['no signed reviewer input'], 'mechanical': {}, 'reviewer': None}


def parts(price=35, spec=None, **over):
    p = {'fb': fb_saas(price), 'qa': QA_OK, 'an': an_saas(), 'card': card_saas(), 'fc': {'assumptions': []},
         'val': valuation_of(spec or SPEC, price), 'rk': RK, 'rv': RV_OK}
    p.update(over)
    return p


def casefile(p, dc):
    cf = {k: v for k, v in p.items() if k != 'fb'}
    cf.update(company=p['fb']['company'], as_of=p['fb']['as_of'], dc=dc, stages={}, errors=[])
    return cf


def grower_parts():
    an = an_saas(roic=None, operating_margin=-.25, gross_margin=.74, fcf_margin=-.2, cfo_to_net_income=None, revenue_cagr_3y=.45,
                 revenue_growth_last=.5, operating_margin_trend_3y=.08, incremental_roic_3y=None, interest_coverage=None,
                 net_debt_to_ebitda=None, net_cash=6e8, sbc_pct_revenue=.2, share_count_cagr_3y=.04, growth_volatility=.1)
    an['ttm'].update(fcf=-1.2e8, fcf_after_sbc=-2e8)
    card = card_saas(stage={'value': 'early'}, funding={'value': 'unknown'},
                     valuation={'method': 'not_reliably_valuable', 'why': 'no positive cash flow yet to underwrite', 'limitations': ['pre-profit']})
    return parts(an=an, card=card, val={'status': 'not_computed', 'reason': 'model card: not_reliably_valuable'})


class DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = {x: parts(x) for x in (35, 50, 70)}
        cls.d = {x: decide(p) for x, p in cls.p.items()}

    def test_attractive_case(self):
        d = self.d[35]
        self.assertEqual(d['verdict']['code'], 'attractive_at_price')
        self.assertEqual(d['verdict']['label_he'], VERDICTS['attractive_at_price'])
        self.assertEqual(d['verdict']['status'], 'final')
        er = d['expected_return']
        self.assertGreaterEqual(er['base_irr'], er['hurdle'])
        self.assertTrue(er['bear_irr'] < er['base_irr'] < er['bull_irr'])
        self.assertGreater(er['price_for_hurdle']['value'], 35)
        self.assertEqual(er['hurdle'], round(er['index_expected_return']['value'] + er['risk_premium'], 10))
        self.assertIsNot(er['hurdle'], .14)
        self.assertIsInstance(er['meets_owner_aspiration_14pct'], bool)
        self.assertEqual(er['index_expected_return']['provenance'], 'model_assumption')
        self.assertEqual(set(d['assessments']), set(ASSESSMENTS))
        for a in d['assessments'].values():
            self.assertIn(a['level'], ('strong', 'adequate', 'weak', 'unknown'))
            self.assertTrue(a['reasons'])
        json.dumps(d, allow_nan=False)

    def test_return_decomposition_is_exact(self):
        dec = self.d[35]['expected_return']['decomposition']
        self.assertEqual(dec['status'], 'computed')
        prod = (1 + dec['business_growth']) * (1 + dec['multiple_change']) * (1 + dec['cash_yield'])
        self.assertAlmostEqual(prod, 1 + dec['total_annualized'], places=10)
        five = self.d[35]['valuation_view']['five_year']
        self.assertEqual(five['date'], '2031-12-31')          # never the 2030 row relabelled as five years
        total = (five['base_value'] + five['cumulative_dividends_base']) / 35
        self.assertAlmostEqual((1 + dec['total_annualized']) ** five['years'], total, places=8)

    def test_quality_but_expensive_case(self):
        d = self.d[70]
        self.assertEqual(d['verdict']['code'], 'quality_but_expensive')
        self.assertEqual(d['assessments']['business_quality']['level'], 'strong')
        self.assertEqual(d['assessments']['price_attractiveness']['level'], 'weak')
        self.assertLess(d['expected_return']['base_irr'], d['expected_return']['hurdle'])
        pfh = d['verdict']['price_for_hurdle']['value']
        self.assertLess(pfh, 70)
        self.assertTrue(any(f'{pfh:,.2f}' in x for x in d['verdict']['what_would_change_it']))

    def test_quality_unchanged_when_only_price_moves_20pct(self):
        base = self.d[50]
        for price in (40, 60):
            d = decide(parts(price))
            for k in ('business_quality', 'trajectory', 'growth_quality', 'financial_strength', 'management_capital_allocation'):
                self.assertEqual(d['assessments'][k], base['assessments'][k], k)
            self.assertNotEqual(d['expected_return']['base_irr'], base['expected_return']['base_irr'])

    def test_price_for_hurdle_and_monotonic_irr(self):
        irrs = [self.d[x]['expected_return']['base_irr'] for x in (35, 50, 70)]
        self.assertTrue(irrs[0] > irrs[1] > irrs[2])
        d = self.d[50]
        h, pfh = d['expected_return']['hurdle'], d['expected_return']['price_for_hurdle']['value']
        case = deepcopy(self.p[50]['val']['case'])
        case['quote']['price'] = pfh
        self.assertAlmostEqual(implied_cost_of_equity(case)['implied_cost_of_equity'], h, places=6)
        # a higher hurdle needs a lower price
        self.assertLess(price_for_hurdle(case, h + .02)['value'], pfh)

    def test_index_input_replaces_default_and_hurdle_is_not_owner_aspiration(self):
        d = decide(parts(50), {'index': {'value': .06, 'basis': 'earnings yield 4% + nominal growth 2% (test)', 'source': 'test input'}})
        er = d['expected_return']
        self.assertEqual(er['index_expected_return']['value'], .06)
        self.assertAlmostEqual(er['hurdle'], .06 + er['risk_premium'])
        with self.assertRaises(ValueError):
            decide(parts(50), {'index': {'value': .06}})
        with self.assertRaises(ValueError):
            decide(parts(50), {'hurdle': .14})

    def test_draft_review_computes_verdict_but_withholds_price_claim(self):
        p = parts(35, rv=RV_DRAFT)
        d = decide(p)
        self.assertEqual(d['verdict']['code'], 'attractive_at_price')
        self.assertEqual(d['verdict']['status'], 'draft')
        self.assertTrue(d['verdict']['price_claim_withheld'])
        text = hebrew_paragraph(casefile(p, d))
        self.assertNotIn('הנחה של', text)                                         # draft numbers are never phrased as a conclusion
        self.assertNotIn(CALL_HE['attractive_at_price'], text)
        self.assertIn('טיוטה', text)
        self.assertIn('לא מסקנה', text)

    def test_insufficient_evidence_when_data_blocked(self):
        qa = {**QA_OK, 'status': 'blocked', 'blocking': ['shares_outstanding'],
              'fields': {'shares_outstanding': {'field': 'shares_outstanding', 'status': 'conflict', 'value': None, 'sources': ['a', 'b']}}}
        p = parts(35, qa=qa)
        d = decide(p)
        self.assertEqual(d['verdict']['code'], 'insufficient_evidence')
        self.assertEqual(d['verdict']['gate'], 'data_blocked')
        self.assertEqual(d['confidence']['level'], 'low')
        self.assertEqual(d['assessments']['data_uncertainty']['level'], 'weak')
        self.assertEqual(d['assessments']['business_quality']['level'], 'strong')   # still assessed, never hidden
        ok = {'target': 'verdict', 'value': 'index_preferable', 'justification': 'The share count is irrelevant to this view of the business',
              'evidence_ids': ['test-10k']}
        d2 = decide(p, {'overrides': [ok]})
        self.assertEqual(d2['verdict']['code'], 'insufficient_evidence')
        self.assertEqual(d2['overrides'][0]['status'], 'rejected')
        d3 = decide(p, {'overrides': [{**ok, 'value': 'avoid', 'justification': 'Two filings disagree on shares and management will not explain'}]})
        self.assertEqual(d3['verdict']['code'], 'avoid')                           # more restrictive is allowed
        self.assertEqual(d3['mechanical_verdict']['code'], 'insufficient_evidence')

    def test_avoid_on_disqualifying_risk_and_override_cannot_lift_it(self):
        rk = deepcopy(RK)
        rk['register'].append({'id': 'R-audit', 'category': 'accounting', 'description': 'Auditor resigned citing a disagreement (test)',
                               'description_he': 'רואה החשבון התפטר בגלל מחלוקת', 'class': 'disqualifying', 'severity': 'high',
                               'in_bear_case': False, 'evidence_ids': ['synthetic']})
        rk['disqualifying'] = ['R-audit']
        p = parts(35, rk=rk)
        lift = {'target': 'verdict', 'value': 'attractive_at_price', 'justification': 'Management says the auditor change is routine and planned',
                'evidence_ids': ['q-retention']}
        q = {'target': 'business_quality', 'value': 'strong', 'justification': 'Retention evidence shows the franchise is intact',
             'evidence_ids': ['q-retention']}
        d = decide(p, {'overrides': [q, lift]})
        self.assertEqual(d['mechanical_verdict']['code'], 'avoid')
        self.assertEqual(d['verdict']['code'], 'avoid')
        self.assertEqual(d['verdict']['gate'], 'disqualifying_risk')
        status = {o['target']: o['status'] for o in d['overrides']}
        self.assertEqual(status, {'business_quality': 'accepted', 'verdict': 'rejected'})
        self.assertIn('cannot lift', next(o['reason'] for o in d['overrides'] if o['target'] == 'verdict'))
        self.assertEqual(d['assessments']['risk_and_error']['level'], 'weak')
        self.assertIn('רואה החשבון התפטר', hebrew_paragraph(casefile(p, d)))

    def test_insufficient_understanding_is_never_avoid(self):
        rk = deepcopy(RK)
        rk['register'].append({'id': 'R-x', 'category': 'governance', 'description': 'Related-party dealings (test)', 'class': 'disqualifying',
                               'severity': 'high', 'in_bear_case': False, 'evidence_ids': ['synthetic']})
        rk['disqualifying'] = ['R-x']
        card = card_saas(completeness={'status': 'insufficient', 'missing': ['u-model']},
                         understanding=[{'id': 'u-model', 'question': 'How does the company make money?', 'critical': True, 'status': 'open'}])
        for r in (RK, rk):
            d = decide(parts(35, card=card, rk=r))
            self.assertEqual(d['verdict']['code'], 'insufficient_evidence')
            self.assertEqual(d['verdict']['gate'], 'understanding_insufficient')
        self.assertIn('Related-party', d['verdict']['reason'])                    # the risk stays visible

    def test_risk_module_gates_route_to_the_right_verdict(self):
        expect = {'gate.blocked_data': ('insufficient_evidence', 'data_blocked'),
                  'gate.redteam_critical_failure': ('insufficient_evidence', 'valuation_unverified'),
                  'gate.unfunded_plan': ('promising_funding_conditional', 'unfunded_plan'),
                  'gate.equity_impaired': ('avoid', 'impaired_equity'),
                  'gate.manipulation_signal': ('avoid', 'disqualifying_risk')}
        for gid, (code, gate) in expect.items():
            rk = deepcopy(RK)
            rk['register'].append({'id': gid, 'category': 'execution' if gid == 'gate.unfunded_plan' else 'test', 'description': f'{gid} (test)',
                                   'class': 'disqualifying', 'severity': 'high', 'in_bear_case': False, 'evidence_ids': []})
            rk['disqualifying'] = [gid]
            d = decide(parts(35, rk=rk))
            self.assertEqual((d['verdict']['code'], d['verdict']['gate']), (code, gate), gid)
        # risk.py: the understanding gap is a high-severity PRICING entry listed in evidence_gaps, never a veto
        rk = deepcopy(RK)
        rk['register'].append({'id': 'gap.understanding_insufficient', 'category': 'business', 'class': 'pricing', 'severity': 'high',
                               'description': 'Business not yet understood (test)', 'in_bear_case': False, 'evidence_ids': [], 'evidence_gap': True})
        rk['evidence_gaps'] = ['gap.understanding_insufficient']
        d = decide(parts(35, rk=rk))
        self.assertEqual((d['verdict']['code'], d['verdict']['gate']), ('insufficient_evidence', 'understanding_insufficient'))
        lift = {'target': 'verdict', 'value': 'attractive_at_price', 'justification': 'Analyst believes the business model is obvious enough',
                'evidence_ids': ['test-10k']}
        self.assertEqual(decide(parts(35, rk=rk), {'overrides': [lift]})['verdict']['code'], 'insufficient_evidence')

    def test_override_rejected_without_justification_or_known_evidence(self):
        p = self.p[70]
        bad = [{'target': 'verdict', 'value': 'attractive_at_price', 'justification': '', 'evidence_ids': ['synthetic']},
               {'target': 'verdict', 'value': 'attractive_at_price', 'justification': 'Long enough justification but no evidence ids', 'evidence_ids': []},
               {'target': 'verdict', 'value': 'attractive_at_price', 'justification': 'Long enough justification citing a ghost source', 'evidence_ids': ['ghost']},
               {'target': 'price', 'value': 'cheap', 'justification': 'Not a valid override target at all here', 'evidence_ids': ['synthetic']}]
        for o in bad:
            d = decide(p, {'overrides': [o]})
            self.assertEqual(d['verdict']['code'], 'quality_but_expensive')
            self.assertEqual(d['overrides'][0]['status'], 'rejected', o)
        # below the required return the price test fails: a verdict override cannot reach attractive_at_price (change the
        # forecast inputs instead); an evidenced move to an allowed outcome is accepted and stored beside the mechanical one
        up = {'target': 'verdict', 'value': 'attractive_at_price', 'justification': 'Contracted price increases from the last renewal cycle are not in the base case',
              'evidence_ids': ['q-retention']}
        d = decide(p, {'overrides': [up]})
        self.assertEqual(d['verdict']['code'], 'quality_but_expensive')
        self.assertEqual(d['overrides'][0]['status'], 'rejected')
        self.assertIn('cannot lift', d['overrides'][0]['reason'])
        good = {**up, 'value': 'index_preferable', 'justification': 'Renewal pricing evidence suggests the franchise is weaker than modelled'}
        d = decide(p, {'overrides': [good]})
        self.assertEqual(d['verdict']['code'], 'index_preferable')
        self.assertEqual(d['verdict']['overridden_from'], 'quality_but_expensive')
        self.assertEqual(d['mechanical_verdict']['code'], 'quality_but_expensive')
        self.assertEqual(d['overrides'][0]['status'], 'accepted')

    def test_interesting_not_yet_valuable_for_loss_making_grower(self):
        p = grower_parts()
        d = decide(p)
        self.assertEqual(d['verdict']['code'], 'interesting_not_yet_valuable')
        a = d['assessments']
        self.assertIn(a['business_quality']['level'], ('strong', 'adequate'))
        roic = next(s for s in a['business_quality']['signals'] if s['signal'] == 'roic')
        self.assertEqual(roic['level'], 'not_meaningful')                         # operating loss while investing is not "weak quality"
        self.assertEqual(a['trajectory']['level'], 'strong')
        self.assertEqual(a['price_attractiveness']['level'], 'unknown')
        self.assertIsNone(d['expected_return']['base_irr'])
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn('אין בסיס אמין לשווי', text)

    def test_promising_funding_conditional(self):
        spec = deepcopy(SPEC)
        for s in spec['scenarios']:
            s['minimum_cash'] = 2e9                                               # plan needs cash the company does not have
        p = parts(35, spec=spec, card=card_saas(funding={'value': 'needs_external', 'reasons': ['cash below plan needs']}))
        self.assertEqual(p['val']['status'], 'funding_blocked')
        d = decide(p)
        self.assertEqual(d['verdict']['code'], 'promising_funding_conditional')
        self.assertEqual(d['verdict']['gate'], 'unfunded_plan')
        lift = {'target': 'verdict', 'value': 'attractive_at_price', 'justification': 'Banks indicated they would lend when asked informally',
                'evidence_ids': ['test-10k']}
        self.assertEqual(decide(p, {'overrides': [lift]})['verdict']['code'], 'promising_funding_conditional')

    def test_weak_business_cheap_on_model_prefers_index(self):
        an = an_saas(roic=.05, operating_margin=.04, fcf_margin=.03, cfo_to_net_income=.7, gross_margin=.5, operating_margin_trend_3y=-.04)
        d = decide(parts(35, an=an))
        self.assertEqual(d['assessments']['business_quality']['level'], 'weak')
        self.assertGreaterEqual(d['expected_return']['base_irr'], d['expected_return']['hurdle'])
        self.assertEqual(d['verdict']['code'], 'index_preferable')               # a low price alone is not an opportunity

    def test_moderate_grower_with_excellent_economics_keeps_its_quality(self):
        an = an_saas(revenue_cagr_3y=.05, revenue_growth_last=.05, operating_margin_trend_3y=0., roic=.35, operating_margin=.32)
        d = decide(parts(35, an=an))
        self.assertEqual(d['assessments']['business_quality']['level'], 'strong')
        self.assertEqual(d['assessments']['trajectory']['level'], 'adequate')

    def test_forensic_red_flag_withholds_price_claim_until_documented(self):
        an = an_saas()
        an['forensics'] = {'beneish': {'status': 'computed', 'm': -1.2, 'zone': 'likely_manipulator'}}
        p = parts(35, an=an)
        d = decide(p)
        self.assertTrue(d['verdict']['price_claim_withheld'])
        rel = {'target': 'red_flags', 'value': ['accounting_risk'], 'justification': 'Receivables jump is the annual billing change documented in the filing',
               'evidence_ids': ['test-10k']}
        d2 = decide(p, {'overrides': [rel]})
        self.assertFalse(d2['verdict']['price_claim_withheld'])
        self.assertEqual(d2['gates']['red_flags'], ['accounting_risk'])           # the flag stays on record

    def test_missing_stages_degrade_honestly(self):
        d = decide({'fb': fb_saas(35)})
        self.assertEqual(d['verdict']['code'], 'insufficient_evidence')
        self.assertEqual(d['confidence']['level'], 'low')
        self.assertTrue(all(a['level'] == 'unknown' for k, a in d['assessments'].items() if k != 'risk_and_error'))
        json.dumps(d, allow_nan=False)
        cf = {'company': {'ticker': 'FICTSAAS'}, 'as_of': '2026-09-27', 'dc': d}
        md = markdown(cf)
        for s in SECTIONS:
            self.assertIn(s, md)

    def test_demo_valuation_is_never_a_final_price_claim(self):
        p = parts(35)
        p['val'] = valuation_of(SPEC, 35, demo=True)
        d = decide(p)
        self.assertTrue(d['verdict']['price_claim_withheld'])
        self.assertEqual(d['confidence']['level'], 'low')


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for name, p in (('attractive', parts(35)), ('expensive', parts(70)), ('draft', parts(35, rv=RV_DRAFT)),
                        ('grower', grower_parts()), ('blocked', parts(35, qa={**QA_OK, 'status': 'blocked', 'blocking': ['debt']}))):
            cls.cases[name] = casefile(p, decide(p))

    def test_hebrew_paragraph_is_one_short_uncertain_paragraph(self):
        for name, cf in self.cases.items():
            text = hebrew_paragraph(cf)
            n = len(text.split())
            lo, hi = (80, 140) if name in ('attractive', 'expensive', 'draft') else (60, 160)   # owner target 80-140 for price answers
            self.assertTrue(lo <= n <= hi, (name, n, text))
            self.assertNotIn('\n', text)
            for w in CERTAINTY:
                self.assertNotIn(w, text, name)
            self.assertIn('רמת הביטחון בהערכה', text)
        att = hebrew_paragraph(self.cases['attractive'])
        self.assertIn('הערכה מותנית, לא תחזית מחיר', att)
        self.assertIn('2031-12-31', att)
        self.assertIn('הנחה של', att)
        self.assertNotIn('2030', att)

    def test_markdown_contains_all_sections_and_provenance(self):
        md = markdown(self.cases['attractive'])
        for s in SECTIONS:
            self.assertIn(s, md)
        for label in ('[reported', '[calculation]', 'model_assumption', 'Value at your required return', 'price at which the base IRR equals'):
            self.assertIn(label, md)
        self.assertIn('2031-12-31', md)
        self.assertIn('| 2025-12-31 |', md)                                        # five-year financial table
        blocked = markdown(self.cases['blocked'])
        self.assertIn('insufficient_evidence', blocked)
        cf = deepcopy(self.cases['attractive'])
        cf['rv'] = {**RV_OK, 'reviewer': {'reviewer': 'Second analyst', 'reviewed_at': '2026-09-28', 'independent': True, 'statement': 'Checked inputs.',
                                          'findings': [{'severity': 'major', 'text': 'Terminal margin aggressive', 'resolved': True, 'response': 'Cut to 24%'}]},
                    'reviewer_check': {'valid': True, 'errors': []}}
        md = markdown(cf)
        self.assertIn('reviewed_at 2026-09-28', md)
        self.assertIn('[major] Terminal margin aggressive', md)


def adbe_parts():
    """Real ADBE 10-K values -> analysis-shaped metrics (formulas stated); TEST-ONLY forecast paths."""
    fb = deepcopy(ADBE)
    t = F.annual_table(fb, ['revenue', 'gross_profit', 'operating_income', 'net_income', 'cfo', 'capex', 'sbc', 'acquisitions', 'income_tax',
                            'pretax_income', 'interest_expense', 'cash', 'short_term_investments', 'long_term_debt_tagged',
                            'current_portion_ltd', 'equity', 'shares_outstanding', 'shares_diluted_wavg'])
    ends = sorted(t)
    annual = {}
    for e in ends:
        r = t[e]
        debt = r.get('long_term_debt_tagged', 0) + r.get('current_portion_ltd', 0)
        inv = r['equity'] + debt - r['cash'] - r['short_term_investments']
        annual[e] = {'revenue': r['revenue'], 'gross_profit': r['gross_profit'], 'operating_income': r['operating_income'],
                     'net_income': r['net_income'], 'cfo': r['cfo'], 'capex': r['capex'], 'fcf': r['cfo'] - r['capex'], 'sbc': r['sbc'],
                     'fcf_after_sbc': r['cfo'] - r['capex'] - r['sbc'], 'shares_diluted_wavg': r['shares_diluted_wavg'],
                     'shares_outstanding': r['shares_outstanding'], 'cash': r['cash'], 'investments': r['short_term_investments'],
                     'debt': debt, 'equity': r['equity'], 'invested_capital': inv}
    a, a3 = annual[ends[-1]], annual[ends[-4]]
    r = t[ends[-1]]
    rev = [annual[e]['revenue'] for e in ends]
    yoy = [b / x - 1 for x, b in zip(rev, rev[1:])]
    om = lambda x: x['operating_income'] / x['revenue']
    m = {'revenue_cagr_3y': (a['revenue'] / a3['revenue']) ** (1 / 3) - 1, 'revenue_growth_last': yoy[-1], 'growth_volatility': statistics.pstdev(yoy),
         'gross_margin': a['gross_profit'] / a['revenue'], 'operating_margin': om(a), 'operating_margin_trend_3y': om(a) - om(a3),
         'fcf_margin': a['fcf'] / a['revenue'], 'fcf_after_sbc_margin': a['fcf_after_sbc'] / a['revenue'], 'cfo_to_net_income': a['cfo'] / a['net_income'],
         'roic': a['operating_income'] * (1 - r['income_tax'] / r['pretax_income']) / a['invested_capital'], 'sbc_pct_revenue': a['sbc'] / a['revenue'],
         'share_count_cagr_3y': (a['shares_outstanding'] / a3['shares_outstanding']) ** (1 / 3) - 1,
         'net_cash': a['cash'] + a['investments'] - a['debt'], 'interest_coverage': a['operating_income'] / r['interest_expense'],
         'acquisition_spend_pct_revenue_3y': sum(annual[e].get('acquisitions', 0) or 0 for e in ends[-3:]) / sum(rev[-3:])}
    an = {'years': ends, 'annual': annual, 'metrics': {k: {**M(v), 'period': 'FY2025', 'status': 'reported'} for k, v in m.items()},
          'ttm': {'period_end': ends[-1], 'method': 'latest_fiscal_year', **{k: a[k] for k in ('revenue', 'fcf', 'fcf_after_sbc', 'cash', 'investments')}},
          'flags': [], 'forensics': {'status': 'unavailable', 'reason': 'not computed in this test'}}
    card = card_saas(stage={'value': 'maturing'}, dimensions={}, understanding=[
        {'id': 'u-model', 'question': 'How does Adobe make money?', 'critical': True, 'status': 'evidenced', 'answer': 'Creative/Document Cloud subscriptions',
         'evidence_ids': ['fmp:as_reported_income_annual']}])
    src = [{'id': 'adbe-10k', 'title': 'Adobe 10-K FY2025 via FMP as-reported (test fixture)', 'url': 'https://www.sec.gov/cgi-bin/browse-edgar?CIK=0000796343',
            'kind': 'filing', 'published_at': '2026-01-15', 'available_at': '2026-02-11', 'retrieval': 'provider_normalized'},
           {'id': 'ib-last', 'title': 'IB last trade (test fixture)', 'url': 'ib://snapshot/ADBE', 'kind': 'market_feed',
            'published_at': '2026-10-19', 'available_at': '2026-10-19', 'retrieval': 'market_feed'}]
    sc = lambda name, g, ccr, tm: {'name': name, 'thesis': f'TEST-ONLY {name} path, not an underwritten Adobe view.', 'assumption_ids': ['test-adbe'],
                                   'cost_of_equity': .09, 'growth': g, 'cash_cost_ratio': ccr, 'sbc_pct': .08, 'depreciation_pct': .035,
                                   'maintenance_capex_pct': .008, 'growth_capex_pct': 0, 'tax_rate': .19, 'interest_rate': .04,
                                   'deferred_revenue_ratio': .29, 'working_capital_ratio': 0, 'prepaid_share': .8, 'payout_fraction': 1.,
                                   'nol_usage_limit': 0, 'minimum_cash': 4e9,
                                   'terminal': {'assumption_ids': ['test-adbe'], 'growth': .03, 'wacc': .09, 'roic': .3, 'operating_margin': tm,
                                                'tax_rate': .19, 'sbc_in_margin': True, 'net_runoff_obligation': 0, 'other_claims': 0,
                                                'working_capital_rationale': 'Deferred revenue renews in a going concern.'}}
    spec = {'driver_version': 1, 'is_demo': False, 'as_of': '2026-10-19', 'company_id': 'adobe', 'ticker': 'ADBE', 'currency': 'USD',
            'unit': 'absolute', 'archetype': 'software', 'method': 'operating',
            'quote': {'price': 246.87, 'currency': 'USD', 'as_of': '2026-10-19', 'source_ids': ['ib-last']}, 'sources': src,
            'assumptions': [{'id': 'test-adbe', 'kind': 'analyst', 'as_of': '2026-10-19', 'review_status': 'unreviewed', 'source_ids': ['adbe-10k'],
                             'rationale': 'TEST-ONLY paths anchored on FY2025 growth and margins.', 'falsifier': 'Any reviewed Adobe forecast replaces these.'}],
            'opening': {'as_of': '2026-10-19', 'assumption_ids': ['test-adbe'], 'unrestricted_cash': 5.431e9, 'marketable_investments': 1.164e9,
                        'debt': 6.21e9, 'shares': 389.2e6, 'deferred_revenue': 7.03e9, 'tax_loss_carryforward': 0},
            'report_dates': ['2026-10-19', '2026-11-30', '2027-11-30', '2028-11-30', '2029-11-30', '2030-11-30', '2031-11-30'],
            'boundaries': ['2026-11-30', '2027-11-30', '2028-11-30', '2029-11-30', '2030-11-30', '2031-11-30', '2032-11-30', '2033-11-30'],
            'stub_annual_revenue_rate': 25.9e9, 'last_full_year_revenue': 25.9e9, 'segment_name': 'Subscriptions',
            'scenarios': [sc('bear', [.04, .03, .02, .02, .02, .02, .02], .55, .27), sc('base', [.09, .08, .08, .07, .06, .05, .04], .52, .34),
                          sc('bull', [.11, .11, .10, .09, .08, .07, .06], .50, .38)]}
    return {'fb': fb, 'qa': {**QA_OK, 'status': 'limited', 'limited': ['shares_outstanding'], 'fields': {}}, 'an': an, 'card': card,
            'fc': {'assumptions': []}, 'val': valuation_of(spec), 'rk': {**RK, 'register': []}, 'rv': RV_DRAFT}


class AdobeFixtureTests(unittest.TestCase):
    def test_real_fixture_quality_and_full_report(self):
        p = adbe_parts()
        d = decide(p)
        a = d['assessments']
        self.assertEqual(a['business_quality']['level'], 'strong')               # ~57% ROIC, 37% op margin, CFO/NI 1.4
        self.assertIn(a['trajectory']['level'], ('strong', 'adequate'))
        self.assertEqual(a['financial_strength']['level'], 'strong')
        self.assertEqual(a['growth_quality']['level'], 'strong')                  # buybacks shrink the count, little M&A
        self.assertIn(d['verdict']['code'], ('attractive_at_price', 'quality_but_expensive'))
        self.assertTrue(d['verdict']['price_claim_withheld'])                    # unreviewed draft
        self.assertEqual(d['valuation_view']['five_year']['date'], '2031-11-30')
        self.assertEqual(d['expected_return']['decomposition']['status'], 'computed')
        cf = casefile(p, d)
        md = markdown(cf)
        self.assertIn('23,769', md)                                               # FY2025 revenue, USD m
        self.assertIn('Adobe Inc.', md)
        n = len(hebrew_paragraph(cf).split())
        self.assertTrue(80 <= n <= 140, n)                                        # unreviewed draft, numbers labelled
        q = a['business_quality']
        cheaper = deepcopy(p)
        F.set_price(cheaper['fb'], 246.87 * .8, '2026-10-19', 'ib:snapshot')
        self.assertEqual(decide(cheaper)['assessments']['business_quality'], q)


if __name__ == '__main__':
    unittest.main()
