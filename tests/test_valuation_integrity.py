"""valuation.value_company economics regressions (2026-10-20 review): riskless financial assets at face, payout-policy
neutrality, tax-loss carryforwards left at the horizon, and rational conversion of convertibles.

Fixture: the fictional software driver example (examples/valuation/software_drivers.json, is_demo); every number is a
test double. Each test failed on the engine before the repair.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.drivers import build_case
from fundamental_engine.valuation import value_company, years

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'examples/valuation/software_drivers.json').read_text())
SCEN = ('bear', 'base', 'bull')


def today(spec):
    r = value_company(build_case(spec))
    return {k: r['annual_values'][0][k] for k in SCEN}, r


def scen(x, name):
    return next(s for s in x['scenarios'] if s['name'] == name)


class FinancialAssetsAtFace(unittest.TestCase):
    def test_treasuries_add_their_face_value_whatever_the_horizon(self):
        # 100m more investments earning risk-free after tax, held to the horizon: worth 1.00 per share (100m shares), not
        # their low return discounted at the 10% cost of equity (about 0.6 before the repair)
        base, _ = today(SPEC)
        s = deepcopy(SPEC)
        s['opening']['marketable_investments'] += 100e6
        for sc in s['scenarios']:
            sc['investment_income_after_tax'] = sc['investment_income_after_tax'] + 100e6 * .0425 * (1 - .21)
        more, _ = today(s)
        for k in SCEN:
            self.assertAlmostEqual(more[k] - base[k], 1.0, places=6)

    def test_payout_policy_does_not_change_value(self):
        # retained cash earns nothing in the cash flows, so before the repair a lower payout destroyed value (Modigliani-Miller)
        values = []
        for payout in (1., .5, 0.):
            s = deepcopy(SPEC)
            for sc in s['scenarios']:
                sc['payout_fraction'] = payout
            v, r = today(s)
            self.assertNotEqual(r['status'], 'funding_blocked')                # computed, no funding gap
            values.append(v)
        for k in SCEN:
            self.assertAlmostEqual(values[0][k], values[1][k], places=6)
            self.assertAlmostEqual(values[0][k], values[2][k], places=6)

    def test_roll_back_identity_includes_the_financial_asset_credit(self):
        _, r = today(SPEC)
        res = scen(r, 'base')
        vals = {x['date']: x['value_per_share'] for x in res['boundary_values']}
        for p in res['forecast']:
            disc = 1.1 ** years(p['start'], p['end'])
            a = p['financial_assets_open']
            credit = (a - (a + p['investment_income_after_tax']) / disc) / p['shares_open']
            self.assertAlmostEqual(vals[p['start']], (vals[p['end']] + p['dividend_per_share']) / disc + credit)


class HorizonClaimsAndShields(unittest.TestCase):
    def test_tax_losses_left_at_the_horizon_shelter_terminal_profit(self):
        s = deepcopy(SPEC)
        s['opening']['tax_loss_carryforward'] = 5e9           # far more than the explicit years can use
        for sc in s['scenarios']:
            sc['nol_usage_limit'] = .8
        _, r = today(s)
        t = scen(r, 'base')['terminal']
        self.assertGreater(t['nol_remaining'], 0)
        sc = scen(build_case(s), 'base')
        ebit, g, wacc, tax = t['next_year_revenue'] * sc['terminal']['operating_margin'], sc['terminal']['growth'], sc['terminal']['wacc'], sc['terminal']['tax_rate']
        left, pv, k = t['nol_remaining'], 0., 0
        while left > 1e-6 and k < 200:
            k += 1
            used = min(left, ebit * (1 + g) ** (k - 1) * .8)
            pv += used * tax / (1 + wacc) ** k
            left -= used
        self.assertAlmostEqual(t['nol_tax_shield'], pv, delta=1)
        self.assertAlmostEqual(t['raw_equity_value'], t['enterprise_value'] + pv + t['excess_cash'] + t['marketable_investments']
                               - t['debt'] - t['other_claims'] - t['net_runoff_obligation'], delta=1)

    def test_in_the_money_convertible_converts_and_out_of_the_money_does_not(self):
        straight, _ = today(SPEC)
        for price, expect_conversion in ((5., True), (500., False)):
            s = deepcopy(SPEC)
            s['opening']['convertibles'] = [{'id': 'cv1', 'claim': 150e6, 'conversion_shares': 150e6 / price}]   # claim inside the 200m debt
            conv, r = today(s)
            for k in SCEN:
                v, n0, n = straight[k], 100e6, 150e6 / price
                if expect_conversion:
                    self.assertAlmostEqual(conv[k], (v * n0 + 150e6) / (n0 + n), places=6)
                    self.assertLess(conv[k], v)
                else:
                    self.assertAlmostEqual(conv[k], v, places=9)
        bad = deepcopy(SPEC)
        bad['opening']['convertibles'] = [{'id': 'cv1', 'claim': 900e6, 'conversion_shares': 1e6}]
        with self.assertRaisesRegex(ValueError, 'Convertible claims exceed opening debt'):
            today(bad)


if __name__ == '__main__':
    unittest.main()
