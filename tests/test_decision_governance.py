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
from test_decision_render import SPEC, QA_OK, RK, RV_OK, RV_DRAFT, RV_REVIEWER, parts, an_saas, card_saas, casefile, fb_saas  # noqa: E402

from fundamental_engine import facts as F, render                                                                     # noqa: E402
from fundamental_engine.decision import decide, five_year_row, GATE_ALLOWED                                           # noqa: E402
from fundamental_engine.render import hebrew_paragraph, markdown, CALL_HE, RISK_HE                                    # noqa: E402
from fundamental_engine.valuation import value_company                                                                # noqa: E402
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
        idx = {'value': .095, 'basis': 'index assumption above the model rate (test)', 'source': 'test input'}
        d = decide(p, {'index': idx})                                             # required return 10.5% > model 10%: two values
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
        self.assertTrue(runs)
        # a low index no longer pulls the required return below the model's own cost of equity: attractive never above base value
        self.assertFalse(any(price > d['valuation_view']['base'] for price, _, d in runs))
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
        self.assertAlmostEqual(band['hurdle_high'], .115 + d['expected_return']['permanent_loss_premium'])
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
        self.assertAlmostEqual(er['permanent_loss_premium'], .01)                  # net cash, self-funded, not cyclical, strong quality
        self.assertAlmostEqual(er['hurdle'], er['index_expected_return']['value'] + er['risk_premium'])
        an = an_saas(net_cash=-6e8, net_debt_to_ebitda=3.6, interest_coverage=3.)
        card = card_saas(cyclical={'value': True}, funding={'value': 'unknown'})
        risky = decide(parts(35, an=an, card=card))['expected_return']
        rc = {c['component']: c['value'] for c in risky['premium_components']}
        self.assertEqual(rc['cyclicality'], .01)
        self.assertGreater(rc['balance_sheet'], 0)
        self.assertGreater(rc['funding_dependence'], 0)
        self.assertGreater(risky['permanent_loss_premium'], er['permanent_loss_premium'] + .02)
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
        self.assertEqual(decide(p)['assessments']['management_capital_allocation']['level'], 'unknown')
        for bad in ('ib:last', 'fmp:float', 'fmp:est', 'q-view', 'margin_base', 'synthetic', 'R-comp'):   # an UPGRADE needs relevant evidence
            d = decide(p, {'overrides': [ov('management_capital_allocation', 'strong', bad)]})
            self.assertEqual(d['overrides'][0]['status'], 'rejected', bad)
            self.assertIn('not accepted', d['overrides'][0]['reason'])
        for good in ('test-10k', 'q-retention'):
            self.assertEqual(decide(p, {'overrides': [ov('management_capital_allocation', 'strong', good)]})['overrides'][0]['status'], 'accepted', good)
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
        self.assertIn(f"{er['hurdle'] * 100:.2f}%", d['assessments']['price_attractiveness']['reasons'][0])
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



# ---------------------------------------------------------------- second review: economics, governance and report regressions

FC_LEV = {'assumptions': [], 'cost_of_equity': {'base': .14, 'components': {
    'risk_free': .0425, 'equity_risk_premium': .045, 'unlevered': .0925,
    'leverage': {'ke': .14, 'unlevered': .0925, 'kd': .05, 'debt_weight': .5, 'd_to_e': 1.0}}}}


def levered_spec():
    """The software example re-levered: net debt 2.0bn, model cost of equity 14% (Ku 9.25% + leverage), terminal WACC 8%."""
    s = deepcopy(SPEC)
    s['opening']['debt'] = 2.5e9
    for sc in s['scenarios']:
        sc['cost_of_equity'] = .14
        sc['terminal']['wacc'] = .08
    return s


def wide_bear_spec(margin=.14):
    s = deepcopy(SPEC)
    next(x for x in s['scenarios'] if x['name'] == 'bear')['terminal']['operating_margin'] = margin
    return s


def lev_parts(price):
    return parts(price, spec=levered_spec(), fc=FC_LEV, an=an_saas(net_cash=-2e9, net_debt_to_ebitda=2.5, interest_coverage=5.))


def reviewer_rv(**over):
    return {**RV_REVIEWER, **over}


class LeveredRequiredReturnTests(unittest.TestCase):
    def test_required_return_is_never_below_the_models_own_cost_of_equity(self):
        p = lev_parts(33)
        d = decide(p)
        er, view = d['expected_return'], d['valuation_view']
        self.assertGreaterEqual(er['hurdle'], .14 - 1e-12)                     # index 8.75% + premium would be ~10%
        comps = {c['component']: c['value'] for c in er['premium_components']}
        self.assertIn('model_cost_of_equity_floor', comps)
        self.assertTrue(er['model_cost_of_equity_floor']['binding'])
        self.assertAlmostEqual(er['hurdle'], er['index_expected_return']['value'] + er['risk_premium'])
        self.assertLessEqual(er['value_at_required_return'], view['base'] * (1 + 1e-9))   # never 3-8x the model value
        self.assertGreater(33, view['base'])
        self.assertNotEqual(d['verdict']['code'], 'attractive_at_price')       # a price above the model base value is not "cheap"
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn('המחיר גבוה בכ', text)                                     # not "64-86% below value"
        self.assertNotIn('הנחה של', text)
        self.assertIn('סיכון המינוף', d['verdict']['reason_he'])

    def test_terminal_wacc_moves_by_the_equity_weight_for_irr_and_values(self):
        p = lev_parts(20)
        d = decide(p)
        er = d['expected_return']
        self.assertAlmostEqual(er['debt_weight'], .5)
        low = next(r for r in er['band']['rows'] if r['label'].startswith('required return -'))
        c = deepcopy(p['val']['case'])
        for sc in c['scenarios']:
            shift = low['hurdle'] - sc['cost_of_equity']
            sc['cost_of_equity'] = low['hurdle']
            sc['terminal']['wacc'] += .5 * shift                                 # Harris-Pringle: (1 - w) x the change, not 1:1
        self.assertAlmostEqual(low['value_at_required_return'], value_company(c)['annual_values'][0]['base'], places=6)
        at_value = decide(lev_parts(round(er['value_at_required_return'], 6)))
        self.assertAlmostEqual(at_value['expected_return']['base_irr'], er['hurdle'], places=4)   # the IRR uses the same convention


class PremiumAndQualityTests(unittest.TestCase):
    def grower(self, **card_over):
        an = an_saas(roic=None, operating_margin=-.25, gross_margin=.74, fcf_margin=-.2, cfo_to_net_income=None, revenue_cagr_3y=.45,
                     revenue_growth_last=.5, operating_margin_trend_3y=.08, incremental_roic_3y=None, interest_coverage=None,
                     net_debt_to_ebitda=None, net_cash=6e8)
        card = card_saas(stage={'value': 'early'}, dimensions={'pricing_power': {
            'assessment': 'strong', 'source': 'computed', 'evidence_ids': ['test-10k'],
            'note': 'proxy: gross margin 74%, stable (a proxy, not proof)'}}, **card_over)
        return parts(15, an=an, card=card)

    def test_loss_making_early_grower_pays_for_unproven_economics_and_gross_margin_counts_once(self):
        p = self.grower()
        d = decide(p)
        er, bq = d['expected_return'], d['assessments']['business_quality']
        comps = {c['component']: c['value'] for c in er['premium_components']}
        self.assertAlmostEqual(comps['unproven_economics'], .03)              # the forecast's own early-stage add-on
        self.assertGreater(er['permanent_loss_premium'], decide(parts(15))['expected_return']['permanent_loss_premium'] + .02)
        cp = next(s for s in bq['signals'] if s['signal'] == 'competitive_position')
        self.assertEqual(cp['level'], 'unknown')                              # the gross-margin proxy is not a second signal
        self.assertIn('computed proxies not counted', cp['note'])
        self.assertEqual(bq['level'], 'adequate')                              # capped until unit economics are evidenced
        self.assertNotIn('עדויות ליתרון תחרותי', d['business_reason_he'])

    def test_cyclical_quality_does_not_follow_the_cycle_position(self):
        series = [{'roic': x} for x in (.09, .03, .15, .04, .10)]
        card = card_saas(business_model={'primary': 'industrial', 'secondary': [], 'reasons': ['test']},
                         cyclical={'value': True, 'normalize': True, 'normalized_operating_margin': {'value': .096}})
        levels = {}
        for name, roic, opm in (('peak', .15, .15), ('trough', .02, .02)):
            an = an_saas(roic=roic, operating_margin=opm, fcf_margin=.06, cfo_to_net_income=.9)
            an['forensics'] = {'roic': {'series': series}}
            bq = decide(parts(35, an=an, card=card))['assessments']['business_quality']
            levels[name] = bq['level']
            sig = {s['signal']: s for s in bq['signals']}
            self.assertAlmostEqual(sig['roic']['value'], .082)                 # cycle average, not the latest year
            self.assertAlmostEqual(sig['operating_margin']['value'], .096)
            self.assertTrue(sig['roic_durability']['core'])
        self.assertEqual(levels['peak'], levels['trough'])


class MarginOfSafetyTests(unittest.TestCase):
    def test_bear_loss_is_measured_at_the_required_return(self):
        idx = {'value': .095, 'basis': 'index above the model rate (test)', 'source': 'test input'}
        p = parts(35)
        d = decide(p, {'index': idx})
        er, v = d['expected_return'], d['verdict']
        c = deepcopy(p['val']['case'])
        for sc in c['scenarios']:
            sc['terminal']['wacc'] += er['hurdle'] - sc['cost_of_equity']
            sc['cost_of_equity'] = er['hurdle']
        bear_h = value_company(c)['annual_values'][0]['bear']
        self.assertAlmostEqual(v['margin_of_safety']['bear_value'], bear_h, places=6)
        self.assertAlmostEqual(v['margin_of_safety']['loss_to_bear'], 1 - bear_h / 35, places=6)
        self.assertLess(bear_h, d['valuation_view']['bear'])                   # 10.5% required return vs the 10% model rate
        self.assertEqual(v['margin_of_safety']['basis'], 'at your required return')
        self.assertAlmostEqual(er['at_required_return']['bear'], bear_h, places=6)

    def test_bear_loss_near_the_tolerance_is_marginal(self):
        p = parts(34, spec=wide_bear_spec(.14))
        d = decide(p)
        v = d['verdict']
        self.assertEqual(v['code'], 'attractive_at_price')
        self.assertLess(abs(v['margin_of_safety']['loss_to_bear'] - .40), .05)
        self.assertEqual(v['margin'], 'marginal')
        self.assertIn('bear_loss_tolerance', [f['test'] for f in v['margin_detail']['flips']])
        self.assertTrue(any('margin-of-safety tolerance' in r for r in d['confidence']['reasons']))
        self.assertIn('קרוב לסף מרווח הביטחון', hebrew_paragraph(casefile(p, d)))

    def test_marginal_text_names_the_side_where_the_call_flips(self):
        d = decide(parts(42))
        md = d['verdict']['margin_detail']
        f = next(x for x in md['flips'] if x['test'] == 'required_return')
        self.assertEqual(f['side'], 'higher')
        self.assertAlmostEqual(f['threshold'], d['expected_return']['base_irr'], places=2)   # flips where the IRR meets the hurdle
        self.assertIn('גבוהה מכ־', md['text_he'])
        self.assertNotIn(' בין כ־', md['text_he'])


class FailedStageTests(unittest.TestCase):
    def test_a_crashed_valuation_stage_is_an_evidence_gap_never_a_business_judgement(self):
        rv = {'status': 'blocked', 'price_conclusion_allowed': False, 'reasons': ['valuation_not_executed: valuation failed: ValueError: boom'],
              'mechanical': {}, 'reviewer': None}
        p = parts(35, val={'status': 'not_computed', 'reason': 'valuation failed: ValueError: boom'}, rv=rv)
        d = decide(p)
        v = d['verdict']
        self.assertEqual((v['code'], v['gate']), ('insufficient_evidence', 'valuation_failed'))
        self.assertEqual(v['status'], 'draft')
        self.assertIn('נכשל', v['reason_he'])
        self.assertNotIn('תזרים מזומנים חיובי', v['what_would_change_it_he'])
        self.assertIn('טיוטה', hebrew_paragraph(casefile(p, d)))
        legit = decide(parts(35, val={'status': 'not_computed', 'reason': 'no forecast spec for this valuation method'}))
        self.assertEqual(legit['verdict']['gate'], 'no_valuation')


class OverrideBypassTests(unittest.TestCase):
    def test_assessment_upgrades_cannot_reach_a_verdict_the_mechanical_route_refuses(self):
        unproven = parts(35, an=an_saas(roic=None, operating_margin=None, fcf_margin=None, cfo_to_net_income=None), card=card_saas(dimensions={}))
        weak = parts(30, an=an_saas(roic=.05, operating_margin=.04, fcf_margin=.03, cfo_to_net_income=.7, gross_margin=.5, operating_margin_trend_3y=-.04))
        for p in (unproven, weak):
            m = decide(p)['verdict']
            d = decide(p, {'overrides': [ov('business_quality', 'strong', 'test-10k', why='one two three four five')]})
            self.assertEqual(d['verdict']['code'], m['code'])
            self.assertNotEqual(d['verdict']['code'], 'attractive_at_price')
            self.assertEqual(d['overrides'][0]['status'], 'rejected')
            self.assertIn('cannot bypass a gate', d['overrides'][0]['reason'])

    def test_an_upgrade_never_lowers_the_required_return(self):
        p = parts(37, an=an_saas(net_cash=-9e8, net_debt_to_ebitda=4.5, interest_coverage=2.5))
        mech = decide(p)
        d = decide(p, {'overrides': [ov('financial_strength', 'strong', 'test-10k', why='q q q q q')]})
        self.assertEqual(d['expected_return']['hurdle'], mech['expected_return']['hurdle'])
        self.assertEqual(d['verdict']['margin'], mech['verdict']['margin'])
        self.assertEqual(d['overrides'][0]['effect'], 'recorded only: no effect on the verdict, the required return or the confidence')

    def test_the_owner_text_says_when_a_result_rests_on_an_analyst_override(self):
        p = parts(35)
        d = decide(p, {'overrides': [ov('verdict', 'index_preferable', 'R-comp', why='bundled suites will undercut renewal pricing')]})
        self.assertEqual(d['overrides'][0]['status'], 'accepted')
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn('הכרעת אנליסט מתועדת', text)
        self.assertIn(d['mechanical_verdict']['label_he'], text)
        self.assertTrue(any(r.startswith('rests on accepted analyst overrides') for r in d['confidence']['reasons']))

    def test_bear_loss_gate_and_shared_routes_are_not_lifted_by_a_verdict_override(self):
        self.assertNotIn('attractive_at_price', GATE_ALLOWED['bear_loss_beyond_tolerance'])
        p = parts(36, spec=wide_bear_spec(.12))
        m = decide(p)['verdict']
        self.assertEqual(m['gate'], 'bear_loss_beyond_tolerance')
        F.add_source(p['fb'], 'sec:inc', 'sec', 'income statement (annual)', 'filing_derived', '2026-09-27')
        d = decide(p, {'overrides': [ov('verdict', 'attractive_at_price', 'sec:inc', why='the bear case is too harsh in my judgement overall')]})
        self.assertEqual(d['verdict']['code'], 'quality_but_expensive')
        weak = an_saas(roic=.03, operating_margin=.02, fcf_margin=.01, cfo_to_net_income=.6, gross_margin=.4, operating_margin_trend_3y=-.04)
        unfunded = parts(35, an=weak, card=card_saas(funding={'value': 'needs_external', 'reasons': ['test']}))
        self.assertEqual(decide(unfunded)['verdict']['code'], 'avoid')
        d = decide(unfunded, {'overrides': [ov('verdict', 'promising_funding_conditional', 'test-10k')]})
        self.assertEqual(d['verdict']['code'], 'avoid')
        novalue = parts(35, an=weak, val={'status': 'not_computed', 'reason': 'no forecast spec for this valuation method'})
        self.assertEqual(decide(novalue)['verdict']['code'], 'index_preferable')
        d = decide(novalue, {'overrides': [ov('verdict', 'interesting_not_yet_valuable', 'test-10k')]})
        self.assertEqual(d['verdict']['code'], 'index_preferable')

    def test_a_statement_dataset_alone_does_not_carry_an_upgrade(self):
        p = parts(35)
        F.add_source(p['fb'], 'sec:inc', 'sec', 'income statement (annual)', 'filing_derived', '2026-09-27')
        alone = decide(p, {'overrides': [ov('management_capital_allocation', 'strong', 'sec:inc')]})
        self.assertEqual(alone['overrides'][0]['status'], 'rejected')
        self.assertIn('alone does not carry', alone['overrides'][0]['reason'])
        both = decide(p, {'overrides': [ov('management_capital_allocation', 'strong', 'sec:inc', 'test-10k')]})
        self.assertEqual(both['overrides'][0]['status'], 'accepted')


class OverrideEvidenceTests(unittest.TestCase):
    def test_laundered_evidence_is_rejected(self):
        p = parts(35)
        fb = p['fb']
        F.add_source(fb, 'web:blog', 'web', 'blog post', 'secondary', '2026-09-27')
        F.add_qualitative(fb, 'q-blog', 'moat', 'A blogger says the moat is wide (test)', 'reported', ['web:blog'], '2026-09-27')
        F.add_qualitative(fb, 'q-nosrc', 'moat', 'The moat is wide (test, no source)', 'reported', [], '2026-09-27')
        F.add_qualitative(fb, 'q-synth', 'moat', 'The moat is wide (test, synthetic source)', 'reported', ['synthetic'], '2026-09-27')
        p['val'] = deepcopy(p['val'])
        p['val']['case']['sources'].append({'id': 'input:risk_free', 'title': 'my own note on rates', 'url': 'input://risk_free', 'kind': 'filing',
                                            'published_at': '2026-09-27', 'available_at': '2026-09-27'})
        for eid in ('q-blog', 'q-nosrc', 'q-synth', 'input:risk_free'):
            d = decide(p, {'overrides': [ov('management_capital_allocation', 'strong', eid)]})
            self.assertEqual(d['overrides'][0]['status'], 'rejected', eid)
        d = decide(p, {'overrides': [ov('data_uncertainty', 'strong', 'input:risk_free')]})
        self.assertEqual(d['overrides'][0]['status'], 'rejected')
        self.assertIn('analyst_input', d['overrides'][0]['reason'])

    def test_conservative_downgrades_are_never_blocked_by_evidence_kind(self):
        p = parts(35)
        F.add_qualitative(p['fb'], 'q-view', 'competition', 'A columnist thinks competition is rising (test)', 'opinion', ['synthetic'], '2026-09-27')
        for o in (ov('verdict', 'avoid', 'R-comp'), ov('verdict', 'index_preferable', 'q-view'), ov('verdict', 'avoid', why='too much risk'),
                  ov('verdict', 'insufficient_evidence'), ov('business_quality', 'weak', 'R-comp')):
            d = decide(p, {'overrides': [o]})
            self.assertEqual(d['overrides'][0]['status'], 'accepted', o)
            self.assertEqual(d['overrides'][0]['direction'], 'downgrade', o)
        ghost = decide(p, {'overrides': [ov('verdict', 'avoid', 'ghost-id')]})
        self.assertEqual(ghost['overrides'][0]['status'], 'rejected')            # ids must still exist

    def test_a_no_op_verdict_override_is_rejected_and_keeps_the_margin(self):
        p = parts(42)
        mech = decide(p)
        self.assertEqual(mech['verdict']['margin'], 'marginal')
        d = decide(p, {'overrides': [ov('verdict', 'attractive_at_price', 'test-10k')]})
        self.assertEqual(d['overrides'][0]['status'], 'rejected')
        self.assertIn('no-op', d['overrides'][0]['reason'])
        self.assertEqual(d['verdict']['margin'], 'marginal')
        self.assertIn('גבולית', hebrew_paragraph(casefile(p, d)))
        real = decide(p, {'overrides': [ov('verdict', 'index_preferable', 'R-comp')]})
        self.assertEqual(real['verdict']['code'], 'index_preferable')
        self.assertEqual(real['verdict']['margin'], 'marginal')                  # the mechanical margin is carried, not erased
        self.assertIn('applies_to', real['verdict']['margin_detail'])

    def test_user_index_is_a_market_return_with_a_range(self):
        with self.assertRaises(ValueError):
            decide(parts(55, fc=FC), {'index': {'value': .03, 'basis': 'x', 'source': 'x'}})   # below the 4% risk-free rate
        with self.assertRaises(ValueError):
            decide(parts(55, fc=FC), {'index': {'value': -.05, 'basis': 'x', 'source': 'x'}})
        d = decide(parts(55, fc=FC), {'index': {'value': .07, 'basis': 'yield + growth (test)', 'source': 'test input'}})
        idx = d['expected_return']['index_expected_return']
        self.assertEqual(idx['provenance'], 'user_assumption')
        self.assertAlmostEqual(idx['low'], .06)
        self.assertAlmostEqual(idx['high'], .08)
        self.assertGreaterEqual(d['expected_return']['hurdle'], d['valuation_view']['model_cost_of_equity'])


class RedFlagReleaseTests(unittest.TestCase):
    def flagged(self, **over):
        an = an_saas()
        an['forensics'] = {'beneish': {'status': 'computed', 'm': -1.2, 'zone': 'likely_manipulator'}}
        return parts(28, an=an, **over)

    def test_forensic_release_needs_a_reviewer_and_filing_evidence(self):
        rel = ov('red_flags', ['accounting_risk'], 'test-10k', why='ok ok ok ok ok')
        alone = decide(self.flagged(), {'overrides': [rel]})
        self.assertTrue(alone['verdict']['price_claim_withheld'])
        statement = decide(self.flagged(rv=reviewer_rv()), {'overrides': [ov('red_flags', ['accounting_risk'], 'q-retention')]})
        self.assertTrue(statement['verdict']['price_claim_withheld'])          # a statement on another topic does not explain it
        p = self.flagged(rv=reviewer_rv())
        ok = decide(p, {'overrides': [rel]})
        self.assertFalse(ok['verdict']['price_claim_withheld'])
        text = hebrew_paragraph(casefile(p, ok))
        self.assertNotIn('שעוד לא הוסבר', text)                                 # never both "unexplained" and released
        self.assertTrue(any('released' in r for r in ok['confidence']['reasons']))

    def test_reviewer_release_of_a_governance_flag_needs_filing_evidence_that_addresses_it(self):
        rk = deepcopy(RK)
        rk['register'].append({'id': 'gov.material_weakness', 'category': 'governance', 'class': 'pricing', 'severity': 'high', 'in_bear_case': False,
                               'description': 'Evidenced material weakness', 'evidence_ids': ['q-mw'], 'withhold_price': True,
                               'description_he': 'חולשה מהותית בבקרה הפנימית'})
        rk['withhold_price'] = ['gov.material_weakness']
        for ids, held in ((['ib:last'], True), (['q-mw'], True), (['test-10k'], False)):
            rv = reviewer_rv(risk_overrides_applied=['gov.material_weakness'])
            rv['reviewer'] = {**rv['reviewer'], 'risk_overrides': {'gov.material_weakness': {'justification': 'a b c d e', 'evidence_ids': ids}}}
            p = parts(35, rk=rk, rv=rv)
            F.add_source(p['fb'], 'ib:last', 'ib', 'price snapshot', 'market_feed', '2026-09-27')
            F.add_qualitative(p['fb'], 'q-mw', 'internal control', 'Management identified a material weakness (test)', 'reported', ['test-10k'], '2026-09-27')
            d = decide(p)
            self.assertEqual(d['verdict']['price_claim_withheld'], held, ids)


class DraftTextTests(unittest.TestCase):
    def test_draft_names_the_mechanical_result_and_labels_the_analyst_result(self):
        p = parts(35, rv=RV_DRAFT)
        d = decide(p, {'overrides': [ov('verdict', 'index_preferable', 'R-comp')]})
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn(f'התוצאה המכנית היא "{d["mechanical_verdict"]["label_he"]}"', text)
        self.assertIn(f'אחרי הכרעת אנליסט מתועדת היא "{d["verdict"]["label_he"]}"', text)
        q = decide(p, {'overrides': [ov('business_quality', 'adequate', 'R-comp')]})
        qt = hebrew_paragraph(casefile(p, q))
        self.assertIn('לפי הכרעת אנליסט מתועדת (במדדים המכניים: חזקה', qt)
        self.assertIn('אחרי התאמת האנליסט', qt)                                 # the shown value uses the adjusted hurdle

    def test_rejected_assumption_withholds_numbers_and_is_not_called_unreviewed(self):
        rv = {'status': 'unreviewed_draft', 'price_conclusion_allowed': False, 'reasons': ['assumption_rejected: reviewer rejected: demographics_tailwind'],
              'reason_codes': ['assumption_rejected'], 'mechanical': {}, 'reviewer': RV_REVIEWER['reviewer'], 'reviewer_check': RV_REVIEWER['reviewer_check']}
        p = parts(35, rv=rv)
        d = decide(p)
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn('דחה הנחה', text)
        self.assertNotIn('טרם עבר בדיקה בלתי תלויה', text)
        self.assertNotIn('נתוני הדגמה', text)                                    # 'demographics' is not demo data
        self.assertNotIn(_ps(d['expected_return']['value_at_required_return']), text)

    def test_draft_state_comes_from_structured_flags_not_words(self):
        rv = {'status': 'unreviewed_draft', 'price_conclusion_allowed': False, 'reason_codes': ['underwriting_incomplete'], 'mechanical': {}, 'reviewer': None,
              'reasons': ['underwriting_incomplete: forensic accountant memo and demographics_tailwind assumption lack evidence']}
        p = parts(35, rv=rv)
        text = hebrew_paragraph(casefile(p, decide(p)))
        self.assertNotIn('נתוני הדגמה', text)
        self.assertNotIn('דגל אדום', text)
        self.assertIn('פערים בביסוס ההנחות', text)

    def test_every_draft_is_labelled_and_long_enough(self):
        spec = deepcopy(SPEC)
        for s in spec['scenarios']:
            s['minimum_cash'] = 2e9
        p = parts(35, spec=spec, card=card_saas(funding={'value': 'needs_external', 'reasons': ['cash below plan needs']}), rv=RV_DRAFT)
        d = decide(p)
        self.assertEqual((d['verdict']['code'], d['verdict']['status']), ('promising_funding_conditional', 'draft'))
        text = hebrew_paragraph(casefile(p, d))
        self.assertIn('טיוטה', text)
        rk = deepcopy(RK)
        rk['register'].append({'id': 'gate.distress', 'category': 'distress', 'class': 'disqualifying', 'severity': 'high', 'in_bear_case': False,
                               'description': 'distress (test)', 'evidence_ids': []})
        rk['disqualifying'] = ['gate.distress']
        p = parts(35, rk=rk, rv=RV_DRAFT)
        d = decide(p)
        text = hebrew_paragraph(casefile(p, d))
        self.assertEqual(d['verdict']['code'], 'avoid')
        self.assertIn('טיוטה', text)
        self.assertTrue(80 <= len(text.split()) <= 140, len(text.split()))


class ReportTextTests(unittest.TestCase):
    def test_value_sentence_uses_one_rate_and_says_when_the_required_return_value_fails(self):
        p = parts(35)
        d = decide(p)
        text = hebrew_paragraph(casefile(p, d))
        ar = d['expected_return']['at_required_return']
        self.assertIn(f'טווח תרחישים בתשואה זו {_ps(ar["bear"])}–{_ps(ar["bull"])}', text)
        bad = deepcopy(d)
        bad['expected_return'].update(value_at_required_return=None, price_for_hurdle={'value': None, 'status': 'invalid', 'reason': 'test'})
        text = hebrew_paragraph(casefile(p, bad))
        self.assertIn('השווי בתשואה הנדרשת לא ניתן לחישוב', text)
        self.assertIn('בשיעור ההיוון של המודל', text)

    def test_five_year_change_states_the_re_rating_assumption_and_its_rate(self):
        p = parts(35)
        d = decide(p)
        text = hebrew_paragraph(casefile(p, d))
        f5 = d['expected_return']['five_year_at_required_return']
        self.assertIn('יתכנס לשווי בתשואה הנדרשת', text)
        self.assertIn(f"(כ־{f5['annualized_including_distributions']:.0%} לשנה)", text)
        self.assertIn('assumes a re-rating', markdown(casefile(p, d)))

    def test_main_risk_never_says_the_price_embeds_success_next_to_a_discount(self):
        rk = deepcopy(RK)
        rk['register'] = [{'id': 'val.base_rate.rare_sustained_growth', 'category': 'valuation', 'class': 'pricing', 'severity': 'high', 'in_bear_case': False,
                           'description': 'Base case implies 22%/yr revenue growth; sustained growth above 20% is rare.', 'evidence_ids': []},
                          {'id': 'val.wmbt.growth', 'category': 'valuation', 'class': 'pricing', 'severity': 'high', 'in_bear_case': False,
                           'description': 'To justify the price, growth must reach 30%.', 'evidence_ids': []}]
        p = parts(35, rk=rk)
        text = hebrew_paragraph(casefile(p, decide(p)))
        self.assertIn('הסיכון המרכזי: ' + RISK_HE['rare_sustained_growth'], text)
        self.assertNotIn(RISK_HE['valuation'], text)
        self.assertNotIn('שהמחיר דורש', text)

    def test_irr_bound_is_hebrew(self):
        d = decide(parts(8))
        self.assertIsNone(d['expected_return']['base_irr'])
        self.assertIn('מעל ', d['verdict']['reason_he'])
        self.assertNotIn('above', d['verdict']['reason_he'])
        self.assertNotIn('כ־מעל', d['verdict']['reason_he'])

    def test_one_precision_for_the_required_return_and_its_parts(self):
        d = decide(parts(46), {'index': {'value': .0925, 'basis': 'test index', 'source': 'test input'}})   # IRR within 1pp: one decimal
        he = d['verdict']['reason_he']
        m = re.search(r'כ־([0-9.]+)% \(מדד רחב כ־([0-9.]+)% ועוד פרמיית סיכון של כ־([0-9.]+)%\)', he)
        self.assertIsNotNone(m, he)
        total, idx, prem = (float(x) for x in m.groups())
        self.assertAlmostEqual(idx + prem, total, places=6)
        self.assertIn(f"{d['expected_return']['hurdle'] * 100:.2f}%", d['verdict']['reason'])   # English reason = report precision
        self.assertFalse(re.findall(r'price (?:above|at or below) [0-9,]+\.[0-9]{2}\b', ' '.join(d['verdict']['what_would_change_it'])))

    def test_decomposition_states_growth_over_the_real_anchor_span(self):
        d = decide(parts(35))
        dec = d['expected_return']['decomposition']
        self.assertEqual(dec['anchor_end'], '2026-06-30')                     # the reported TTM ends before as_of 2026-09-27
        g = (dec['end']['value'] / dec['anchor']['value']) ** (1 / dec['anchor_span_years']) - 1
        self.assertAlmostEqual(dec['business_growth_over_anchor_span'], g)
        self.assertGreater(dec['anchor_span_years'], dec['years'])
        self.assertIn('anchor-to-end span', markdown(casefile(parts(35), d)))


if __name__ == '__main__':
    unittest.main()
