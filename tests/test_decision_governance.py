"""Decision governance and report consistency (regressions for the decision/render review).

One required return everywhere (index from the forecast's own cost-of-equity framework, value at the required return
shown to the owner), a marginal band instead of false precision, a hurdle premium built from business and balance-sheet
risk of permanent loss, override governance (a gate on every route, relevant evidence kinds, direction-aware confidence
filter, recomputation after an accepted override), report consistency, 'priced' risk semantics, gate order, lenders'
financial strength, the five-year row and the labelled unreviewed draft. Fixtures are the hand-built stand-ins of
test_decision_render (FICTSAAS synthetic driver example); no private evidence is used.
"""
import re
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_decision_render import SPEC, QA_OK, RK, RV_OK, RV_DRAFT, parts, an_saas, card_saas, casefile  # noqa: E402

from fundamental_engine import facts as F, render                                                                     # noqa: E402
from fundamental_engine.decision import decide, five_year_row, GATE_ALLOWED                                           # noqa: E402
from fundamental_engine.render import hebrew_paragraph, markdown, CALL_HE, RISK_HE                                    # noqa: E402
from fundamental_engine.valuation_tools import implied_cost_of_equity                                                 # noqa: E402

FC = {'assumptions': [], 'cost_of_equity': {'base': .095, 'components': {'risk_free': .04, 'equity_risk_premium': .05}}}
WHY = 'documented reason for the change with a cited filing'


def _ps(v):
    return f'{v:,.0f}' if abs(v) >= 100 else f'{v:,.1f}' if abs(v) >= 10 else f'{v:,.2f}'


def mild_bear_spec():
    """The software example with a bear close to the base, so price can sit above the model base value while the
    bear-loss tolerance still holds."""
    s = deepcopy(SPEC)
    base = next(x for x in s['scenarios'] if x['name'] == 'base')
    bear = next(x for x in s['scenarios'] if x['name'] == 'bear')
    for k in ('growth', 'cash_cost_ratio', 'sbc_pct'):
        bear[k] = deepcopy(base[k])
    bear['growth'] = [g - .01 for g in base['growth']]
    return s


def limited_qa(*missing, stale=()):
    fields = {f: {'field': f, 'status': 'missing', 'value': None, 'sources': []} for f in missing}
    fields.update({f: {'field': f, 'status': 'stale', 'value': 1e9, 'sources': ['synthetic']} for f in stale})
    return {**QA_OK, 'status': 'limited', 'limited': list(missing) + list(stale), 'fields': fields}


def ov(target, value, *ids, why=WHY):
    return {'target': target, 'value': value, 'justification': why, 'evidence_ids': list(ids)}


class RequiredReturnTests(unittest.TestCase):
    def test_index_default_is_the_forecasts_own_market_return(self):
        d = decide(parts(35, fc=FC))
        idx = d['expected_return']['index_expected_return']
        self.assertAlmostEqual(idx['value'], .09)                                 # risk-free 4% + equity premium 5%, not a separate 7%
        self.assertEqual(idx['origin'], 'forecast_cost_of_equity')
        self.assertAlmostEqual(idx['low'], .08)
        self.assertAlmostEqual(idx['high'], .10)
        self.assertAlmostEqual(d['expected_return']['hurdle'], .09 + d['expected_return']['risk_premium'])
        user = decide(parts(35, fc=FC), {'index': {'value': .07, 'basis': 'yield 2% + growth 5% (test)', 'source': 'test input'}})
        self.assertEqual(user['expected_return']['index_expected_return']['origin'], 'user_input')
        self.assertAlmostEqual(user['expected_return']['index_expected_return']['value'], .07)

    def test_owner_sees_the_value_at_the_required_return(self):
        p = parts(35)
        d = decide(p)
        er = d['expected_return']
        pfh = er['price_for_hurdle']['value']
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn(f'השווי בתשואה הנדרשת שלך הוא כ־{_ps(pfh)}', text)
        self.assertIn(f'הנחה של כ־{1 - 35 / pfh:.0%}', text)                     # discount against the value at the required return
        self.assertIn('בשיעור ההיוון של המודל', text)                             # the model-rate value is secondary
        md = markdown(casefile(p, d))
        self.assertIn(f'Value at your required return (the price at which the base IRR equals the', md)
        self.assertEqual(pfh, er['value_at_required_return'])
        case = deepcopy(p['val']['case'])
        case['quote']['price'] = pfh
        self.assertAlmostEqual(implied_cost_of_equity(case)['implied_cost_of_equity'], er['hurdle'], places=6)

    def test_attractive_never_reads_as_price_above_value(self):
        idx = {'value': .06, 'basis': 'low index assumption (test)', 'source': 'test input'}
        runs = []
        for price in (40, 46, 50, 55, 60):
            p = parts(price, spec=mild_bear_spec())
            d = decide(p, {'index': idx})
            if d['verdict']['code'] == 'attractive_at_price':
                runs.append((price, p, d))
        self.assertTrue(any(price > d['valuation_view']['base'] for price, _, d in runs))   # the case that used to say "attractive, price above value"
        for price, p, d in runs:
            self.assertNotIn('גבוה בכ', hebrew_paragraph(casefile(p, d)), price)
        for price, p, d in runs:
            self.assertLessEqual(price, d['expected_return']['value_at_required_return'])
            self.assertNotIn('above it', markdown(casefile(p, d)).split('## The four questions')[0], price)


class MarginalBandTests(unittest.TestCase):
    def test_price_inside_the_band_is_marginal_and_said_so(self):
        p = parts(42)                                                             # base IRR ~0.8pp above the required return
        d = decide(p)
        v = d['verdict']
        self.assertEqual(v['code'], 'attractive_at_price')                        # the code is kept
        self.assertEqual(v['margin'], 'marginal')
        self.assertTrue(any(f['code'] == 'quality_but_expensive' for f in v['margin_detail']['flips']))
        self.assertTrue(any(r.startswith('marginal call') for r in d['confidence']['reasons']))
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn('גבולית', text)
        self.assertIn('margin **marginal**', markdown(casefile(p, d)))
        self.assertEqual(decide(parts(30))['verdict']['margin'], 'clear')

    def test_index_range_widens_the_band(self):
        self.assertEqual(decide(parts(35))['verdict']['margin'], 'clear')
        idx = {'value': .0875, 'low': .07, 'high': .115, 'basis': 'wide range (test)', 'source': 'test input'}
        d = decide(parts(35), {'index': idx})
        band = d['expected_return']['band']
        self.assertAlmostEqual(band['hurdle_high'], .115 + d['expected_return']['risk_premium'])
        self.assertEqual(d['verdict']['margin'], 'marginal')

    def test_model_rate_band_is_not_a_flip_check(self):
        p = parts(30)
        p['val']['diagnostics']['discount_rate_band'] = {**p['val']['diagnostics']['discount_rate_band'], 'quote_inside_band': True}
        d = decide(p)
        self.assertFalse([r for r in d['confidence']['reasons'] if 'flips' in r or 'one point' in r])
        self.assertIn('Model-rate sensitivity', markdown(casefile(p, d)))

    def test_valuation_error_widens_the_band_not_the_hurdle(self):
        out = {}
        for level in ('low', 'high'):
            rk = deepcopy(RK)
            rk['error_size'] = {'level': level, 'reasons': ['test']}
            out[level] = decide(parts(35, rk=rk))['expected_return']
        self.assertEqual(out['low']['hurdle'], out['high']['hurdle'])
        self.assertLess(out['low']['band']['step'], out['high']['band']['step'])


class PremiumTests(unittest.TestCase):
    def test_premium_is_built_from_permanent_loss_risk(self):
        er = decide(parts(35))['expected_return']
        comps = {c['component']: c['value'] for c in er['premium_components']}
        self.assertEqual(comps['single_stock'], .01)
        self.assertAlmostEqual(er['risk_premium'], .01)                            # net cash, self-funded, not cyclical, strong quality
        an = an_saas(net_cash=-6e8, net_debt_to_ebitda=3.6, interest_coverage=3.)
        card = card_saas(cyclical={'value': True}, funding={'value': 'unknown'})
        risky = decide(parts(35, an=an, card=card))['expected_return']
        rc = {c['component']: c['value'] for c in risky['premium_components']}
        self.assertEqual(rc['cyclicality'], .01)
        self.assertGreater(rc['balance_sheet'], 0)
        self.assertGreater(rc['funding_dependence'], 0)
        self.assertGreater(risky['risk_premium'], er['risk_premium'] + .02)
        self.assertAlmostEqual(risky['hurdle'], risky['index_expected_return']['value'] + risky['risk_premium'])

    def test_data_and_understanding_gaps_lower_confidence_once(self):
        rk = deepcopy(RK)
        rk['error_size'] = {'level': 'medium', 'reasons': ['test'], 'process_gaps': [
            {'code': 'data_limited', 'severity': 'medium', 'detail': 'x'}, {'code': 'understanding_partial', 'severity': 'medium', 'detail': 'y'},
            {'code': 'dimensions_unassessed', 'severity': 'medium', 'detail': 'z', 'ids': ['biz.technology_ai', 'biz.competition']}]}
        card = card_saas(completeness={'status': 'partial', 'missing': ['u-why']},
                         understanding=card_saas()['understanding'] + [{'id': 'u-why', 'question': 'Why do customers pay?', 'critical': True, 'status': 'open'}])
        p = parts(35, qa=limited_qa('convertibles', 'post_period_events'), rk=rk, card=card)
        d = decide(p)
        rs = d['confidence']['reasons']
        self.assertEqual(len([r for r in rs if 'single-source' in r or 'stale' in r or 'missing' in r or 'opening balance' in r]), 1, rs)
        self.assertEqual(len([r for r in rs if 'understanding' in r or 'unassessed' in r]), 1, rs)
        he = ' '.join(d['confidence']['reasons_he'])
        self.assertIn('האג"ח להמרה', he)                                          # the actual gaps are named
        self.assertIn('אירועים אחרי תאריך הדוח', he)
        self.assertNotIn('ממקור יחיד או שהם ישנים', he)
        self.assertIn('השפעת בינה מלאכותית', he)
        clean = decide(parts(35, rk=rk))
        self.assertEqual(clean['expected_return']['hurdle'], d['expected_return']['hurdle'])   # data gaps are not in the hurdle


class OverrideGovernanceTests(unittest.TestCase):
    def test_every_route_has_a_gate(self):
        unknown_quality = an_saas(roic=None, operating_margin=None, fcf_margin=None, cfo_to_net_income=None)
        weak = an_saas(roic=.05, operating_margin=.04, fcf_margin=.03, cfo_to_net_income=.7, gross_margin=.5, operating_margin_trend_3y=-.04)
        for p in (parts(35), parts(45), parts(70), parts(35, an=weak), parts(70, an=weak),
                  parts(35, an=unknown_quality, card=card_saas(dimensions={}))):
            v = decide(p)['verdict']
            self.assertIn(v['gate'], GATE_ALLOWED, v['code'])

    def test_quality_not_demonstrated_cannot_be_overridden_to_attractive(self):
        an = an_saas(roic=None, operating_margin=None, fcf_margin=None, cfo_to_net_income=None)
        p = parts(35, an=an, card=card_saas(dimensions={}))
        d = decide(p, {'overrides': [ov('verdict', 'attractive_at_price', 'q-retention')]})
        self.assertEqual(d['mechanical_verdict']['code'], 'interesting_not_yet_valuable')
        self.assertEqual(d['mechanical_verdict']['gate'], 'quality_unproven')
        self.assertEqual(d['verdict']['code'], 'interesting_not_yet_valuable')
        self.assertEqual(d['overrides'][0]['status'], 'rejected')

    def test_override_evidence_must_be_a_relevant_kind(self):
        p = parts(35)
        fb = p['fb']
        F.add_source(fb, 'ib:last', 'ib', 'price snapshot', 'market_feed', '2026-09-27')
        F.add_source(fb, 'fmp:float', 'fmp', 'shares-float', 'vendor_normalized', '2026-09-27')
        F.add_source(fb, 'fmp:est', 'fmp', 'estimates', 'consensus', '2026-09-27')
        F.add_qualitative(fb, 'q-view', 'competition', 'A columnist thinks the moat is fine (test)', 'opinion', ['synthetic'], '2026-09-27')
        p['fc'] = {'assumptions': [{'id': 'margin_base', 'kind': 'model', 'source_ids': []}]}
        for bad in ('ib:last', 'fmp:float', 'fmp:est', 'q-view', 'margin_base', 'synthetic', 'R-comp'):
            d = decide(p, {'overrides': [ov('business_quality', 'adequate', bad)]})
            self.assertEqual(d['overrides'][0]['status'], 'rejected', bad)
            self.assertIn('not accepted', d['overrides'][0]['reason'])
        for good in ('test-10k', 'q-retention'):
            self.assertEqual(decide(p, {'overrides': [ov('business_quality', 'adequate', good)]})['overrides'][0]['status'], 'accepted', good)
        # data accuracy needs the document itself, not a statement about the business
        self.assertEqual(decide(p, {'overrides': [ov('data_uncertainty', 'strong', 'q-retention')]})['overrides'][0]['status'], 'rejected')
        self.assertEqual(decide(p, {'overrides': [ov('data_uncertainty', 'strong', 'test-10k')]})['overrides'][0]['status'], 'accepted')

    def test_confidence_filter_only_for_evidence_backed_upgrades(self):
        p = parts(35, qa=limited_qa('convertibles'))
        data = lambda d: [r for r in d['confidence']['reasons'] if r.startswith('data gaps')]
        self.assertTrue(data(decide(p)))
        down = decide(p, {'overrides': [ov('data_uncertainty', 'weak', 'test-10k')]})
        self.assertEqual(down['overrides'][0]['direction'], 'downgrade')
        self.assertTrue(data(down))                                               # a downgrade never silences the data reason
        up = decide(p, {'overrides': [ov('data_uncertainty', 'strong', 'test-10k')]})
        self.assertEqual(up['overrides'][0]['direction'], 'upgrade')
        self.assertFalse(data(up))

    def test_accepted_override_recomputes_the_required_return_everywhere(self):
        p = parts(35)
        d = decide(p, {'overrides': [ov('financial_strength', 'weak', 'test-10k')]})
        er, v, m = d['expected_return'], d['verdict'], d['mechanical_verdict']
        self.assertGreater(er['hurdle'], er['mechanical']['hurdle'])
        self.assertEqual(v['hurdle_used'], er['hurdle'])
        self.assertEqual(m['hurdle_used'], er['mechanical']['hurdle'])
        self.assertEqual(er['price_for_hurdle']['hurdle'], er['hurdle'])
        self.assertIn(f"{er['hurdle'] * 100:.1f}%", d['assessments']['price_attractiveness']['reasons'][0])
        md = markdown(casefile(p, d))
        self.assertIn('effective after accepted overrides; mechanical', md)
        self.assertIn('mechanical', er['hurdle_basis'] + ' ' + str(er['mechanical']))


class ReportConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = parts(35)
        cls.d = decide(cls.p)
        cls.cf = casefile(cls.p, cls.d)
        cls.md = markdown(cls.cf)
        cls.text = hebrew_paragraph(cls.cf)

    def test_decomposition_is_labelled_a_re_rating_scenario(self):
        self.assertIn("Re-rating scenario at the model's cost of equity", self.md)
        self.assertIn('| total (re-rating scenario) |', self.md)
        self.assertNotIn('| total |', self.md)

    def test_hebrew_five_year_includes_distributions_and_the_total(self):
        self.assertIn('חלוקות (רכישות עצמיות/דיבידנד)', self.text)
        self.assertNotIn('לא כולל דיבידנדים', self.text)
        f5 = self.d['expected_return']['five_year_at_required_return']
        self.assertIn(f"סך הכול כ־{_ps(f5['base_value'] + f5['cumulative_distributions_base'])}", self.text)
        self.assertIn(f"{f5['change_including_distributions']:.0%}", self.text)
        self.assertNotIn('לא כולל דיבידנדים', self.text)

    def test_no_unformatted_floats(self):
        p = parts(35)
        p['val']['what_must_be_true'] = [{'lever': 'exit_multiple', 'label': 'exit multiple', 'unit': 'x', 'required': 2.0557774118971057,
                                          'anchor': 1.2345678912, 'status': 'solved', 'meaning': 'test'}]
        md = markdown(casefile(p, decide(p)))
        self.assertEqual(re.findall(r'\d\.\d{5,}', md), [])

    def test_top_risk_is_the_most_material_risk_not_charged_sbc(self):
        rk = deepcopy(RK)
        rk['register'] = [{'id': 'dil.sbc', 'category': 'dilution', 'class': 'pricing', 'severity': 'medium', 'in_bear_case': False,
                           'description': 'SBC 10% of revenue', 'evidence_ids': []},
                          {'id': 'biz.competition', 'category': 'business', 'class': 'pricing', 'severity': 'high', 'in_bear_case': False,
                           'description': 'competition: not assessed', 'assessed': False, 'status': 'unassessed', 'relevance': 'high'},
                          {'id': 'biz.technology_ai', 'category': 'business', 'class': 'pricing', 'severity': 'high', 'in_bear_case': False,
                           'description': 'technology ai: not assessed', 'assessed': False, 'status': 'unassessed', 'relevance': 'high'}]
        rv = {**RV_OK, 'mechanical': {'redteam': {'derived_inputs': {'sbc_treatment': {'value': 'expensed'}}}}}
        p = parts(35, rk=rk, rv=rv)
        text = hebrew_paragraph(casefile(p, decide(p)))
        self.assertIn('הסיכון המרכזי: ' + RISK_HE['technology_ai'], text)
        self.assertNotIn(RISK_HE['sbc'], text)
        rk['register'].append({'id': 'gov.material_weakness', 'category': 'governance', 'class': 'pricing', 'severity': 'high', 'in_bear_case': False,
                               'description': 'Evidenced material weakness', 'withhold_price': True, 'description_he': 'חולשה מהותית בבקרה הפנימית'})
        rk['withhold_price'] = ['gov.material_weakness']
        p = parts(35, rk=rk, rv=rv)
        self.assertIn('הסיכון המרכזי: חולשה מהותית בבקרה הפנימית', hebrew_paragraph(casefile(p, decide(p))))

    def test_gate_ids_have_their_own_hebrew_text(self):
        he = render._risk_he({'id': 'gate.redteam_critical_failure', 'category': 'valuation'})
        self.assertNotEqual(he, RISK_HE['valuation'])
        self.assertIn('הבדיקה המכנית', he)
        for gid in ('gate.blocked_data', 'gate.unfunded_plan', 'gate.equity_impaired', 'gate.manipulation_signal', 'gate.distress',
                    'gap.understanding_insufficient'):
            self.assertTrue(render._risk_he({'id': gid, 'category': 'valuation'}), gid)
            self.assertNotEqual(render._risk_he({'id': gid, 'category': 'valuation'}), RISK_HE['valuation'])

    def test_review_block_without_a_reviewer_is_the_mechanical_check(self):
        rv = {'status': 'blocked', 'price_conclusion_allowed': False, 'reasons': ['valuation_audit failed (test)'], 'mechanical': {}, 'reviewer': None}
        d = decide(parts(35, rv=rv))
        self.assertEqual(d['verdict']['gate'], 'review_blocked')
        self.assertIn('הבדיקה המכנית', d['verdict']['reason_he'])
        self.assertNotIn('הבלתי תלויה', d['verdict']['reason_he'])
        self.assertIn('No independent reviewer: only the mechanical check ran', markdown(casefile(parts(35), self.d)))

    def test_collection_notes_are_shown_in_the_data_section(self):
        cf = deepcopy(self.cf)
        cf['collection_notes'] = ['price snapshot dated 2026-10-01 is after as_of 2026-09-27; not used']
        md = markdown(cf)
        data = md.split('## Data validation')[1].split('## Forecast assumptions')[0]
        self.assertIn('price snapshot dated 2026-10-01 is after as_of', data)


class RiskPricingTests(unittest.TestCase):
    def _rk(self, add_on_key):
        rk = deepcopy(RK)
        rk['register'].append({'id': 'biz.technology_ai', 'category': 'business', 'class': 'accepted_priced', 'severity': 'high', 'in_bear_case': False,
                               'priced_via': f'forecast.cost_of_equity.components.{add_on_key}', 'assessed': True, 'assessment': 'weak',
                               'description': 'technology ai: assessed weak', 'evidence_ids': ['q-retention']})
        return rk

    def test_small_cost_of_equity_add_on_is_only_partially_priced(self):
        fc = {'assumptions': [], 'cost_of_equity': {'base': .1, 'components': {'risk_free': .0425, 'equity_risk_premium': .045, 'adjustment_weak_technology_ai': .0025}}}
        p = parts(35, rk=self._rk('adjustment_weak_technology_ai'), fc=fc)
        d = decide(p)
        pr = {x['id']: x for x in d['risk_pricing']}
        self.assertEqual(pr['biz.technology_ai']['status'], 'partially_priced')
        self.assertLess(pr['biz.technology_ai']['value_effect'], .10)
        self.assertEqual(pr['R-comp']['status'], 'priced')                        # in the executed bear path
        md = markdown(casefile(p, d))
        self.assertIn('partially_priced', md)
        self.assertIn('הסיכון המרכזי: ' + RISK_HE['technology_ai'], hebrew_paragraph(casefile(p, d)))   # not hidden as "priced"
        big = {'assumptions': [], 'cost_of_equity': {'base': .1, 'components': {'risk_free': .0425, 'equity_risk_premium': .045, 'adjustment_weak_technology_ai': .04}}}
        d2 = decide(parts(35, rk=self._rk('adjustment_weak_technology_ai'), fc=big))
        self.assertEqual({x['id']: x for x in d2['risk_pricing']}['biz.technology_ai']['status'], 'priced')


class GateOrderAndLenderTests(unittest.TestCase):
    def test_no_valuation_is_checked_before_review_blocked(self):
        rv = {'status': 'blocked', 'price_conclusion_allowed': False, 'reasons': ['valuation_not_executed: forecast unavailable'], 'mechanical': {}, 'reviewer': None}
        d = decide(parts(35, val={'status': 'not_computed', 'reason': 'no route for this business model'}, rv=rv))
        self.assertEqual(d['verdict']['gate'], 'no_valuation')
        self.assertEqual(d['verdict']['code'], 'interesting_not_yet_valuable')

    def test_lender_financial_strength_is_not_graded_on_net_cash(self):
        card = card_saas(business_model={'primary': 'financials', 'secondary': [], 'reasons': ['deposits and loans (test)']})
        an = an_saas(net_cash=4e9, interest_coverage=1.2, net_debt_to_ebitda=None)
        for y in an['annual']:
            an['annual'][y].pop('net_income', None)
        fs = decide(parts(35, an=an, card=card, fc=FC))['assessments']['financial_strength']
        self.assertEqual(fs['level'], 'unknown')                                  # not assessed rather than "strong" on net cash
        self.assertNotIn('leverage', [s['signal'] for s in fs['signals']])
        years = sorted(an['annual'])
        an['annual'][years[-2]].update(net_income=1.0e9, equity=9.6e9)
        an['annual'][years[-1]].update(net_income=1.2e9, equity=10.4e9)
        fs = decide(parts(35, an=an, card=card, fc=FC))['assessments']['financial_strength']
        roe = next(s for s in fs['signals'] if s['signal'] == 'roe_vs_cost_of_equity')
        self.assertAlmostEqual(roe['value'], 1.2e9 / 10e9)
        self.assertEqual(fs['level'], 'adequate')                                 # 12% ROE vs 9.5% cost of equity


class FiveYearRowTests(unittest.TestCase):
    ROWS = [{'date': d, 'base': 1.} for d in ('2026-03-20', '2026-12-31', '2027-12-31', '2028-12-31', '2029-12-31', '2030-12-31', '2031-12-31')]

    def test_nearest_row_within_the_window_with_its_real_horizon(self):
        f = five_year_row(self.ROWS, '2026-03-20')
        self.assertEqual(f['date'], '2030-12-31')                                 # 79 days before the anniversary, not None
        self.assertEqual(f['status'], 'nearest')
        self.assertAlmostEqual(f['years'], 4.78, places=2)

    def test_exact_anniversary_is_preferred(self):
        rows = self.ROWS[:6] + [{'date': '2031-03-20', 'base': 2.}] + self.ROWS[6:]
        f = five_year_row(rows, '2026-03-20')
        self.assertEqual((f['date'], f['status']), ('2031-03-20', 'exact'))

    def test_rows_outside_the_window_say_why(self):
        f = five_year_row(self.ROWS[:5], '2026-03-20')
        self.assertIsNotNone(f)
        self.assertEqual(f['status'], 'outside_window')
        self.assertIsNone(f['row'])
        self.assertIn('2029-12-31', f['reason'])


class DraftParagraphTests(unittest.TestCase):
    def test_unreviewed_draft_shows_its_numbers_labelled_never_as_a_conclusion(self):
        p = parts(35, rv=RV_DRAFT)
        d = decide(p)
        text = hebrew_paragraph(casefile(p, d))
        pfh = d['expected_return']['price_for_hurdle']['value']
        self.assertIn(_ps(pfh), text)
        self.assertIn('טיוטה', text)
        self.assertIn('לא מסקנה', text)
        self.assertIn('חלוקות (רכישות עצמיות/דיבידנד)', text)
        self.assertNotIn(CALL_HE['attractive_at_price'], text)
        self.assertNotIn('הנחה של', text)
        self.assertTrue(80 <= len(text.split()) <= 140, len(text.split()))
        self.assertIn('unreviewed draft: not a conclusion', markdown(casefile(p, d)))

    def test_red_flag_draft_still_withholds_the_numbers(self):
        an = an_saas()
        an['forensics'] = {'beneish': {'status': 'computed', 'm': -1.2, 'zone': 'likely_manipulator'}}
        p = parts(35, an=an)
        d = decide(p)
        text = hebrew_paragraph(casefile(p, d))
        self.assertNotIn(_ps(d['expected_return']['price_for_hurdle']['value']), text)
        self.assertIn('דגל אדום', text)


if __name__ == '__main__':
    unittest.main()
