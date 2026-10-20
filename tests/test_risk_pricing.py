"""Risk register pricing semantics, valuation-error labelling, Hebrew register text and liquidity (risk.py).

'Priced' needs the executed bear path or a cost-of-equity add-on that moves base value materially (the rule of
decision.risk_pricing); a small add-on is partially priced. Fixtures are the hand-built stand-ins of test_risk_review
(the fictional software driver example and synthetic non-demo test doubles); no private evidence is used.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_risk_review import analysis, card, entry, factbook, forecast, live_spec, qa, run, scen, truncate, valuate  # noqa: E402

from fundamental_engine import facts as F                                                                           # noqa: E402
from fundamental_engine import decision, risk                                                                     # noqa: E402

STATUSES = {'priced', 'partially_priced', 'not_priced', 'open_question', 'gate'}
AI_WEAK = {'technology_ai': {'assessment': 'weak', 'evidence_ids': ['syn'], 'note': 'AI agents may reduce seat counts'}}
GEO_WEAK = {'geopolitics': {'assessment': 'weak', 'evidence_ids': ['syn'], 'note': 'single-country supply'}}


def with_add_ons(**adds):
    spec = live_spec()
    fc = forecast(spec)
    fc['cost_of_equity']['components'].update(adds)
    return spec, fc


class RiskPricing(unittest.TestCase):
    def test_small_cost_of_equity_add_on_is_only_partially_priced(self):
        # AN-3: a weak technology_ai rating carried by a 25bp add-on, not in the bear path, was classed accepted_priced
        spec, fc = with_add_ons(adjustment_technology_ai=.0025)
        val = valuate(spec)
        rk, _ = run(spec, fc=fc, val=val, cd=card(dims=AI_WEAK))
        ai = entry(rk, 'biz.technology_ai')
        self.assertFalse(ai['in_bear_case'])
        self.assertEqual(ai['priced_via'], 'forecast.cost_of_equity.components.adjustment_technology_ai')
        self.assertEqual((ai['class'], ai['pricing_status']), ('pricing', 'partially_priced'))
        self.assertIn('lowers base value only', ai['pricing_basis'])
        eff, today = ai['priced_via_effect'], val['result']['annual_values'][0]
        self.assertAlmostEqual(eff['value_effect'], 1 - eff['base_value'] / eff['base_value_without_add_on'])
        self.assertLess(eff['value_effect'], .05)
        self.assertAlmostEqual(rk['pricing_materiality'], max(.5 * (1 - today['bear'] / today['base']), .10))
        self.assertEqual(eff['material_threshold'], rk['pricing_materiality'])
        self.assertEqual((rk['summary']['partially_priced'], rk['summary']['accepted_priced']), (1, 1))   # dil.sbc is in the bear path

    def test_material_add_on_and_the_bear_path_are_priced(self):
        spec, fc = with_add_ons(adjustment_technology_ai=.02)
        rk, _ = run(spec, fc=fc, cd=card(dims=AI_WEAK))
        ai = entry(rk, 'biz.technology_ai')
        self.assertEqual((ai['class'], ai['pricing_status'], ai['in_bear_case']), ('accepted_priced', 'priced', False))
        self.assertGreaterEqual(ai['priced_via_effect']['value_effect'], ai['priced_via_effect']['material_threshold'])
        sbc = entry(rk, 'dil.sbc')
        self.assertEqual((sbc['class'], sbc['pricing_status'], sbc['pricing_basis']), ('accepted_priced', 'priced', 'the executed bear path models it'))

    def test_unquantifiable_add_on_is_partially_priced(self):
        spec, fc = with_add_ons(adjustment_technology_ai=.02)
        rk, _ = run(spec, fc=fc, cd=card(dims=AI_WEAK), val={'status': 'not_computed', 'reason': 'test: no valuation'})
        ai = entry(rk, 'biz.technology_ai')
        self.assertEqual((ai['class'], ai['pricing_status']), ('pricing', 'partially_priced'))
        self.assertIn('effect not quantified', ai['pricing_basis'])
        self.assertNotIn('priced_via_effect', ai)

    def test_every_entry_has_a_documented_status(self):
        rk, _ = run(q=qa(status='blocked', blocking=['debt']), cd=card(dims={}))
        self.assertTrue(rk['register'])
        self.assertTrue(all(r['pricing_status'] in STATUSES and r['pricing_basis'] for r in rk['register']))
        self.assertEqual(entry(rk, 'gate.blocked_data')['pricing_status'], 'gate')
        self.assertEqual(entry(rk, 'biz.technology_ai')['pricing_status'], 'open_question')
        rk, _ = run()
        self.assertEqual(entry(rk, 'biz.technology_ai')['pricing_status'], 'not_priced')   # weak, the bear never names AI, no add-on

    def test_register_status_is_the_decision_rule(self):
        spec, fc = with_add_ons(adjustment_technology_ai=.0025, country_risk_premium=.02)
        val = valuate(spec)
        rk, _ = run(spec, fc=fc, val=val, cd=card(dims={**AI_WEAK, **GEO_WEAK}))
        today, case = val['result']['annual_values'][0], val['case']
        view = {'base': today['base'], 'bear': today['bear'], 'model_cost_of_equity': scen(case, 'base')['cost_of_equity']}
        ref = {p['id']: p['status'] for p in decision._risk_pricing(rk, fc, view, decision._Rates(case, today['current_quote'], 0.))}
        self.assertEqual({r['id']: r['pricing_status'] for r in rk['register']}, ref)
        self.assertEqual((ref['biz.technology_ai'], ref['biz.geopolitics']), ('partially_priced', 'priced'))


class ValuationErrorIsNotCompanyRisk(unittest.TestCase):
    def test_error_size_triggers_in_the_register_are_labelled_valuation_error(self):
        spec = live_spec()
        scen(spec, 'bull')['terminal']['operating_margin'] = .36
        scen(spec, 'bear')['terminal']['operating_margin'] = .16
        rk, _ = run(spec, val=valuate(spec))
        w = entry(rk, 'val.wide_range')
        self.assertTrue(w['valuation_error'])
        self.assertTrue(w['description'].startswith('Size of possible valuation error, not a company risk'))
        self.assertIn('bear-to-bull range 115% of base value', rk['error_size']['reasons'])
        self.assertIn('not a company risk', rk['error_size']['label'])
        spec = truncate(live_spec(), 3)
        rk, _ = run(spec, val=valuate(spec))
        t = entry(rk, 'val.terminal_share')
        self.assertEqual((t['valuation_error'], t['class']), (True, 'pricing'))
        self.assertIn('not a company risk', t['description'])
        self.assertEqual(rk['summary']['valuation_error'], 1)
        self.assertFalse([r for r in rk['register'] if r.get('valuation_error') and not r['id'].startswith('val.')])


class HebrewRegisterText(unittest.TestCase):
    def test_base_rate_entries_carry_hebrew(self):
        an = analysis()
        an['annual']['2023-12-31']['revenue'], an['annual']['2024-12-31']['revenue'] = 300e6, 350e6
        an['annual']['2025-12-31']['revenue'] = 400e6                  # the base path then needs ~30%/yr from a 15%/yr history
        rk, _ = run(an=an)
        rare, hist = entry(rk, 'val.base_rate.rare_sustained_growth'), entry(rk, 'val.base_rate.above_own_history')
        g = rare['values']['implied']['implied_revenue_cagr']
        self.assertIn(f'כ־{g:.0%} בשנה', rare['description_he'])
        self.assertIn('מעט חברות', rare['description_he'])
        self.assertIn(f"כ־{hist['values']['historical_cagr']:.0%} בשנה שהחברה השיגה בעבר", hist['description_he'])

    def test_what_must_be_true_entries_carry_hebrew(self):
        spec = live_spec()
        wmbt = [{'lever': 'growth', 'label': 'average revenue growth', 'status': 'solved', 'required': .27, 'anchor': .14},
                {'lever': 'mature_margin', 'label': 'mature operating margin after SBC', 'status': 'solved', 'required': .40, 'anchor': .26},
                {'lever': 'capital_efficiency', 'label': 'growth investment per unit of new revenue (x base)', 'status': 'solved', 'required': .45, 'anchor': 1.}]
        rk, _ = run(spec, val=valuate(spec, what_must_be_true=wmbt))
        self.assertIn('כ־27% בשנה, מול כ־14% בתרחיש הבסיס', entry(rk, 'val.wmbt.growth')['description_he'])
        self.assertIn('כ־40%, מול כ־26% בתרחיש הבסיס', entry(rk, 'val.wmbt.mature_margin')['description_he'])
        self.assertIn('רק כ־45% מההשקעה', entry(rk, 'val.wmbt.capital_efficiency')['description_he'])
        off = [{'lever': 'mature_margin', 'label': 'mature operating margin after SBC', 'status': 'not_reachable', 'required': None, 'anchor': .26}]
        rk, _ = run(spec, val=valuate(spec, what_must_be_true=off))                   # price 50 above base value ~45.9
        self.assertEqual(entry(rk, 'val.wmbt.mature_margin')['description_he'], risk.WMBT_UNREACHABLE_HE['mature_margin'])


class NegativeCashIsNotLiquidity(unittest.TestCase):
    def test_a_negative_cash_figure_never_becomes_liquidity_or_runway(self):
        an = analysis(fcf=-50e6)
        an['annual']['2025-12-31']['cash'] = -5e8                     # dataqa rejected cash (resolved None); analysis still holds it
        q = qa(cash={'value': None, 'as_of': None}, short_term_investments={'value': None, 'as_of': None})
        liq = risk.liquidity(factbook(), q, an)
        self.assertIsNone(liq['liquid'])
        self.assertIsNone(liq['runway_years'])
        self.assertIn('a data error', liq['liquid_basis'])
        rk, _ = run(q=q, an=an)
        self.assertEqual(entry(rk, 'fin.runway')['description'], 'Negative free cash flow with unknown liquidity.')
        liq = risk.liquidity(factbook(), qa(cash={'value': -2e8, 'as_of': '2026-06-30'}), analysis())
        self.assertIsNone(liq['liquid'])


class MaturitySchedule(unittest.TestCase):
    def test_two_year_wall_from_the_annual_schedule(self):
        fb = factbook()
        sid = next(iter(fb['sources']))
        for m, v in (('debt_maturity_y1', 4e8), ('debt_maturity_y2', 9e8), ('debt_maturity_y3', 1e8)):     # SYNTHETIC schedule
            F.add_fact(fb, m, 'I@2025-12-31', '2025-12-31', v, sid, 'as_reported', '2026-02-15')
        liq = risk.liquidity(fb, qa(), analysis(fcf=1e8))
        self.assertEqual((liq['near_term_maturities'], liq['horizon_years']), (1.3e9, 2))
        self.assertIn('maturity schedule', liq['maturity_basis'])


if __name__ == '__main__':
    unittest.main()
