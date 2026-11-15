import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import lean_model  # noqa: E402


class LeanModelTest(unittest.TestCase):
    def setUp(self):
        self.p = json.load(open(os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_anet.json')))

    def test_matches_the_anet_run(self):
        r = lean_model.run(self.p)
        base = r['scenarios']['base']
        self.assertAlmostEqual(base['value_today'], 165.6, delta=0.3)
        self.assertAlmostEqual(base['year_end_values'][2031], 271.0, delta=0.5)
        self.assertAlmostEqual(base['annual_return_pct'], 7.9, delta=0.1)
        self.assertAlmostEqual(r['scenarios']['bear']['year_end_values'][2031], 120.1, delta=0.5)
        self.assertAlmostEqual(r['scenarios']['bull']['year_end_values'][2031], 375.4, delta=0.5)

    def test_order_and_reverse_dcf(self):
        r = lean_model.run(self.p)
        s = r['scenarios']
        self.assertLess(s['bear']['value_today'], s['base']['value_today'])
        self.assertLess(s['base']['value_today'], s['bull']['value_today'])
        self.assertEqual(r['market_implied_growth_pct'][0], 38.5)  # guided year held
        self.assertGreater(r['market_implied_growth_pct'][1], 20.0)  # price needs more than the base after it

    def test_net_debt_uses_debt_rate(self):
        p = dict(self.p, net_cash_m=-5000.0)
        # debt is valued at face today; its interest lowers the cash carried to later year-ends
        lo = lean_model.run_scenario(p, self.p['scenarios']['base'])['year_end_values'][2028]
        hi = lean_model.run_scenario(dict(p, debt_rate=0.0), self.p['scenarios']['base'])['year_end_values'][2028]
        self.assertLess(lo, hi)

    def test_calibrated_lens_holds_todays_multiple_and_margin(self):
        r = lean_model.run(self.p)
        cal, base = r['two_lens']['calibrated'], r['scenarios']['base']
        eps0 = base['eps_operating'][min(base['eps_operating'])]
        self.assertAlmostEqual(cal['pe_used'], self.p['price'] / eps0, delta=0.06)
        m0 = self.p['scenarios']['base']['m'][0]
        self.assertTrue(all(x >= m0 - 1e-9 for x in cal['margin_used']))
        self.assertIn(r['two_lens']['ours']['band'], ('buy', 'hold', 'lagging', 'sell'))
        self.assertEqual(r['two_lens']['agree'], cal['band'] == r['two_lens']['ours']['band'])
        # the lens changes only the exit multiple and the margin floor: with both equal to the base it reproduces the base
        same = dict(self.p, calibrated={'pe': self.p['scenarios']['base']['pe'], 'm': self.p['scenarios']['base']['m']})
        self.assertAlmostEqual(lean_model.run(same)['two_lens']['calibrated']['value_today'], base['value_today'], delta=0.01)

    def test_calibrated_override_and_bands(self):
        r = lean_model.run(dict(self.p, calibrated={'pe': 10.0, 'why': 'test'}))
        self.assertTrue(r['two_lens']['calibrated']['pe_overridden'])
        self.assertEqual(r['two_lens']['calibrated']['pe_used'], 10.0)
        self.assertEqual([lean_model.band(x) for x in (13, 12, 9, 6, 4, 3, 2.9)],
                         ['buy', 'buy', 'hold', 'hold', 'lagging', 'lagging', 'sell'])


OLD_KEYS = {'scenarios', 'weighted_value_today', 'weighted_end_value', 'weighted_return_pct', 'index_return_pct', 'buy_price',
            'index_parity_price', 'market_implied_growth_pct', 'market_implied_end_revenue_m', 'sensitivity_base', 'two_lens'}
NEW_KEYS = {'price_ladder', 'buy_test', 'ranges', 'calibrated_diagnostics', 'funding', 'sensitivity', 'reverse_reading',
            'warnings'}
NEW_KEYS_2 = {'inputs', 'dated_values', 'method_gap', 'sensitivity_terminal', 'return_with_distributions'}   # engine upgrade
ALL_KEYS = OLD_KEYS | NEW_KEYS | NEW_KEYS_2
SCENARIO_KEYS = {'revenue_m', 'op_margin', 'eps_operating', 'eps_with_interest', 'dcf_today', 'multiple_today', 'value_today',
                 'year_end_values', 'annual_return_pct', 'ke0_pct'}


class LeanModelReportingTest(unittest.TestCase):
    """The reporting keys of 15.11.2026: they read the model and change none of its numbers."""

    def setUp(self):
        self.p = json.load(open(os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_anet.json')))
        self.base = self.p['scenarios']['base']
        self.r = lean_model.run(self.p)

    def at_price(self, price, **extra):
        return lean_model.run(dict(self.p, price=price, **extra))

    def test_new_information_only_in_new_keys(self):
        self.assertEqual(set(self.r), ALL_KEYS)
        for s in self.r['scenarios'].values():
            self.assertEqual(set(s), SCENARIO_KEYS)
        self.assertEqual(set(self.r['two_lens']), {'ours', 'calibrated', 'agree', 'rule'})
        # the detail switch returns the same result plus internals
        plain = lean_model.run_scenario(self.p, self.base)
        same, det = lean_model.run_scenario(self.p, self.base, detail=True)
        self.assertEqual(plain, same)
        self.assertEqual(round(det['today'], 2), plain['value_today'])
        json.dumps(self.r)   # serialisable

    def test_price_ladder_one_hurdle_per_rung(self):
        lad = self.r['price_ladder']
        rungs = lad['rungs']
        self.assertEqual(list(rungs), ['margin_of_safety', 'margin_of_safety_13', 'buy_test', 'index', 'hold_floor',
                                       'sell_line'])  # 13 = 13 > 12 > 9.14 > 6 > 3
        self.assertEqual([rungs[k]['return_pct'] for k in rungs], [13.0, 13.0, 12.0, 9.14, 6.0, 3.0])
        # the existing keys are two of the rungs, to the cent
        self.assertEqual(rungs['margin_of_safety']['ours'], self.r['buy_price'])
        self.assertEqual(rungs['index']['ours'], self.r['index_parity_price'])
        self.assertEqual(rungs['margin_of_safety']['same_as'], 'buy_price')
        # the 12% rung is not the 13% rung, and each label names its own return
        self.assertGreater(rungs['buy_test']['ours'], rungs['margin_of_safety']['ours'])
        self.assertIn('12%', rungs['buy_test']['label'])
        self.assertIn('13%', rungs['margin_of_safety']['label'])
        end, cal_end = lad['end_value']['ours'], lad['end_value']['calibrated']
        self.assertEqual(end, self.r['scenarios']['base']['year_end_values'][2031])
        for k, rung in rungs.items():
            f = (1 + rung['return_pct'] / 100) ** self.p['years_to_horizon']
            self.assertAlmostEqual(rung['ours'], end / f, delta=0.05, msg=k)
            self.assertAlmostEqual(rung['calibrated'], cal_end / f, delta=0.05, msg=k)
        prices = [rungs[k]['ours'] for k in rungs]
        self.assertEqual(prices, sorted(prices))
        # bought at a rung, the base return is that rung's hurdle
        for k in ('buy_test', 'hold_floor', 'sell_line'):
            got = self.at_price(rungs[k]['ours'])['scenarios']['base']['annual_return_pct']
            self.assertAlmostEqual(got, rungs[k]['return_pct'], delta=0.1, msg=k)
        # with a fixed calibrated multiple the calibrated rung holds on a rerun too
        fixed = dict(self.p, calibrated={'pe': 30.0})
        rung = lean_model.run(fixed)['price_ladder']['rungs']['buy_test']
        again = lean_model.run(dict(fixed, price=rung['calibrated']))
        self.assertAlmostEqual(again['two_lens']['calibrated']['annual_return_pct'], 12.0, delta=0.1)

    def test_ladder_follows_the_bands_override(self):
        r = lean_model.run(dict(self.p, bands={'buy': 15.0, 'hold': 8.0, 'lagging': 4.0}))
        rungs = r['price_ladder']['rungs']
        self.assertEqual((rungs['buy_test']['return_pct'], rungs['hold_floor']['return_pct'], rungs['sell_line']['return_pct']),
                         (15.0, 8.0, 4.0))
        self.assertEqual(r['buy_test']['legs']['base_ours']['threshold_pct'], 15.0)

    def test_buy_test_legs_and_max_price(self):
        bt = self.r['buy_test']
        legs = bt['legs']
        self.assertEqual(list(legs), ['base_ours', 'base_calibrated', 'bull', 'bear'])
        self.assertEqual([legs[k]['threshold_pct'] for k in legs], [12.0, 12.0, 17.0, -10.0])
        s = self.r['scenarios']
        self.assertEqual(legs['base_ours']['return_pct'], s['base']['annual_return_pct'])
        self.assertEqual(legs['base_calibrated']['return_pct'], self.r['two_lens']['calibrated']['annual_return_pct'])
        self.assertEqual(legs['bull']['return_pct'], s['bull']['annual_return_pct'])
        self.assertEqual(legs['bear']['return_pct'], s['bear']['annual_return_pct'])
        for leg in legs.values():
            self.assertEqual(leg['pass'], leg['return_pct'] >= leg['threshold_pct'])
        # ANET at 182.95: base 7.9% and bull 14.9% fail, bear -7.8% passes
        self.assertEqual([legs[k]['pass'] for k in legs], [False, False, False, True])
        self.assertFalse(bt['pass'])
        self.assertTrue(bt['complete'])
        self.assertEqual(legs['base_ours']['max_price'], self.r['price_ladder']['rungs']['buy_test']['ours'])
        self.assertEqual(legs['base_calibrated']['max_price'], self.r['price_ladder']['rungs']['buy_test']['calibrated'])
        self.assertEqual(bt['max_price_passing'], min(leg['max_price'] for leg in legs.values()))
        self.assertEqual(bt['binding_leg'], 'base_ours')
        self.assertEqual(legs[bt['binding_leg']]['max_price'], bt['max_price_passing'])
        self.assertLess(bt['max_price_vs_price_pct'], 0)

    def test_buy_test_passes_below_the_max_price_and_fails_above(self):
        fixed = dict(self.p, calibrated={'pe': 30.0})   # a fixed multiple: all four leg prices hold on a rerun
        top = lean_model.run(fixed)['buy_test']['max_price_passing']
        self.assertTrue(lean_model.run(dict(fixed, price=top * 0.99))['buy_test']['pass'])
        self.assertFalse(lean_model.run(dict(fixed, price=top * 1.02))['buy_test']['pass'])
        self.assertNotIn('rerun', lean_model.run(fixed)['buy_test'])

    def test_a_pass_that_rests_on_rounding_is_marked(self):
        fixed = dict(self.p, calibrated={'pe': 30.0}, years_to_horizon=5.0)
        top = lean_model.run(fixed)['buy_test']
        leg = top['legs'][top['binding_leg']]
        k = 1 + (leg['threshold_pct'] - 0.03) / 100            # a price at which the binding leg returns 0.03 point less
        price = top['max_price_passing'] * ((1 + leg['threshold_pct'] / 100) / k) ** 5.0
        r = lean_model.run(dict(fixed, price=round(price, 4)))
        bt = r['buy_test']
        self.assertGreater(bt['price'], bt['max_price_passing'])
        self.assertEqual(bt['legs'][top['binding_leg']]['return_pct'], leg['threshold_pct'])    # rounded onto the line
        self.assertTrue(bt['pass'] and bt['on_the_line'])
        self.assertTrue(any('rests on rounding' in w for w in r['warnings']))
        self.assertNotIn('on_the_line', lean_model.run(dict(fixed, price=top['max_price_passing'] * 0.99))['buy_test'])
        self.assertNotIn('on_the_line', lean_model.run(dict(fixed, price=top['max_price_passing'] * 1.02))['buy_test'])

    def test_two_year_return_needs_at_least_a_quarter(self):
        self.assertIn('return_pct', self.at_price(100.0, years_to_horizon=3.5)['ranges']['two_year'])
        self.assertNotIn('return_pct', self.at_price(100.0, years_to_horizon=3.001)['ranges']['two_year'])

    def test_buy_test_rerun_price_holds_the_multiple_of_its_own_price(self):
        # the bull and bear legs made easy, so the calibrated leg decides
        p = dict(self.p, scenarios=dict(self.p['scenarios'], base=dict(self.base, pe=40.0),
                                        bull=dict(self.p['scenarios']['bull'], pe=60.0)))
        bt = lean_model.run(p)['buy_test']
        rr = bt['rerun']
        self.assertEqual(rr['binding_leg'], 'base_calibrated')
        self.assertLess(rr['max_price_passing'], bt['max_price_passing'])   # below today's price the held multiple is lower
        self.assertTrue(rr['lower_than_stated'])
        self.assertTrue(any('rerun' in w for w in lean_model.run(p)['warnings']))
        self.assertTrue(lean_model.run(dict(p, price=rr['max_price_passing'] * 0.99))['buy_test']['pass'])
        self.assertFalse(lean_model.run(dict(p, price=rr['max_price_passing'] * 1.02))['buy_test']['pass'])
        # at the rerun price the calibrated return is the hurdle
        cal = lean_model.run(dict(p, price=rr['calibrated_max_price']))['two_lens']['calibrated']
        self.assertAlmostEqual(cal['annual_return_pct'], 12.0, delta=0.15)

    def test_buy_test_without_a_calibrated_lens_is_incomplete(self):
        p = dict(self.p, scenarios={k: dict(d, m=[-0.05] + d['m'][1:]) for k, d in self.p['scenarios'].items()})
        r = lean_model.run(p)   # first-year EPS negative and no calibrated.pe
        self.assertNotIn('annual_return_pct', r['two_lens']['calibrated'])
        bt = r['buy_test']
        self.assertFalse(bt['complete'])
        self.assertFalse(bt['pass'])
        self.assertIsNone(bt['legs']['base_calibrated']['pass'])
        self.assertIsNone(r['price_ladder']['rungs']['buy_test']['calibrated'])
        self.assertNotIn('base_calibrated', r['ranges']['today'])
        self.assertIn('n/a', lean_model.summary(p, r))

    def test_ranges_two_and_five_years(self):
        rg, s = self.r['ranges'], self.r['scenarios']
        self.assertEqual(rg['two_year']['year'], 2028)
        self.assertEqual(rg['five_year']['year'], 2031)
        self.assertAlmostEqual(rg['two_year']['years_from_today'], 2.16, places=2)
        self.assertAlmostEqual(rg['five_year']['years_from_today'], 5.16, places=2)
        for k in ('bear', 'base', 'bull'):
            self.assertEqual(rg['today'][k], s[k]['value_today'])
            self.assertEqual(rg['two_year'][k], s[k]['year_end_values'][2028])
            self.assertEqual(rg['five_year'][k], s[k]['year_end_values'][2031])
            self.assertEqual(rg['five_year']['return_pct'][k], s[k]['annual_return_pct'])
            want = ((s[k]['year_end_values'][2028] / self.p['price']) ** (1 / 2.16) - 1) * 100
            self.assertAlmostEqual(rg['two_year']['return_pct'][k], want, delta=0.06)
        cal = self.r['two_lens']['calibrated']
        self.assertEqual(rg['two_year']['base_calibrated'], cal['year_end_values'][2028])
        self.assertEqual(rg['five_year']['return_pct']['base_calibrated'], cal['annual_return_pct'])
        self.assertLess(rg['two_year']['bear'], rg['two_year']['base'])
        self.assertLess(rg['two_year']['base'], rg['two_year']['bull'])
        self.assertIn('noisiest', rg['note'])

    def test_calibrated_diagnostics_margin_floor(self):
        cd = self.r['calibrated_diagnostics']   # ANET base margin slips 45.0 -> 44.5: the floor binds from 2029
        self.assertTrue(cd['margin_floor_binds'])
        self.assertTrue(cd['margin_changed'])
        self.assertFalse(cd['differs_from_base_only_by_multiple'])
        self.assertEqual(cd['margin_years_changed'], [2029, 2030, 2031, 2032])
        self.assertAlmostEqual(cd['pe_held_to_base_exit'], cd['pe_held'] / 25.0, delta=0.01)
        self.assertAlmostEqual(cd['pe_held'], self.r['two_lens']['calibrated']['pe_used'], delta=0.06)
        gap = cd['gap_to_base']
        self.assertAlmostEqual(gap['value_today'], gap['value_today_from_multiple'] + gap['value_today_from_margin'], delta=0.02)
        self.assertAlmostEqual(gap['return_pts'], gap['return_pts_from_multiple'] + gap['return_pts_from_margin'], delta=0.21)
        self.assertGreater(gap['value_today_from_multiple'], gap['value_today_from_margin'])
        self.assertGreater(gap['value_today_from_margin'], 0)
        # a rising margin path: the floor does not bind and the lenses differ only by the multiple
        up = dict(self.p, scenarios=dict(self.p['scenarios'], base=dict(self.base, m=[0.45, 0.46, 0.47, 0.47, 0.48, 0.48])))
        cd = lean_model.run(up)['calibrated_diagnostics']
        self.assertFalse(cd['margin_floor_binds'])
        self.assertTrue(cd['differs_from_base_only_by_multiple'])
        self.assertEqual(cd['gap_to_base']['value_today_from_margin'], 0.0)
        self.assertIn('only by the exit multiple', cd['reading'])
        # an evidenced margin override is a change, not the floor
        cd = lean_model.run(dict(self.p, calibrated={'m': [0.44] * 6, 'why': 'test'}))['calibrated_diagnostics']
        self.assertTrue(cd['margin_changed'])
        self.assertFalse(cd['margin_floor_binds'])

    def test_calibrated_diagnostics_inflated_multiple_warning(self):
        cd = self.r['calibrated_diagnostics']   # 34.98 held vs 25 exit = 1.40x, EPS 2.9% of the price: no warning
        self.assertFalse(cd['inflated_multiple_warning'])
        self.assertEqual(cd['warning_reasons'], [])
        r = self.at_price(200.0)   # 38.2 held = 1.53x
        self.assertTrue(r['calibrated_diagnostics']['inflated_multiple_warning'])
        self.assertIn('1.5x', r['calibrated_diagnostics']['warning_reasons'][0])
        self.assertIn('not a second opinion', r['calibrated_diagnostics']['reading'])
        self.assertTrue(any('calibrated lens' in w for w in r['warnings']))
        r = self.at_price(300.0)   # EPS 5.23 is 1.7% of 300
        self.assertLess(r['calibrated_diagnostics']['first_year_eps_to_price_pct'], 2.0)
        self.assertEqual(len(r['calibrated_diagnostics']['warning_reasons']), 2)
        # a stated multiple replaces today's: only the ratio test applies to it
        r = self.at_price(300.0, calibrated={'pe': 30.0})
        self.assertFalse(r['calibrated_diagnostics']['inflated_multiple_warning'])
        r = self.at_price(300.0, calibrated={'pe': 40.0})
        self.assertTrue(r['calibrated_diagnostics']['inflated_multiple_warning'])

    def test_no_positive_first_year_eps_is_not_called_an_inflated_multiple(self):
        base = dict(self.base, m=[0.0] + self.base['m'][1:])
        p = dict(self.p, scenarios=dict(self.p['scenarios'], base=base))
        r = lean_model.run(p)
        cd = r['calibrated_diagnostics']
        self.assertIsNone(cd['pe_held'])
        self.assertFalse(cd['inflated_multiple_warning'])            # there is no multiple to call inflated
        self.assertIn('no positive first-year EPS', cd['reading'])
        self.assertEqual(sum('calibrated lens' in w for w in r['warnings']), 1)
        self.assertFalse(r['buy_test']['complete'])
        self.assertNotIn('n/ax', lean_model.summary(p, r))

    def test_funding_path_is_the_models_own(self):
        p = dict(self.p, cash_yield=0.0, debt_rate=0.0)   # no interest: the path is cash at face plus cumulative FCF
        f = lean_model.run(p)['funding']['scenarios']['base']
        self.assertEqual(f['net_cash_m'][2026], 15659)
        total = 15659.0
        for y in range(2027, 2032):
            total += f['fcf_m'][y]
            self.assertAlmostEqual(f['net_cash_m'][y], total, delta=3)
        self.assertEqual((f['low_point_m'], f['low_point_year']), (15659, 2026))
        self.assertFalse(f['flag'])
        self.assertEqual(f['outside_money_m'], 0)
        self.assertNotIn('equity_wiped', f)
        self.assertEqual(self.r['warnings'], [])
        # with interest the path is higher (cash earns) and still starts at face
        g = self.r['funding']['scenarios']['base']
        self.assertEqual(g['net_cash_m'][2026], 15659)
        self.assertGreater(g['net_cash_m'][2031], f['net_cash_m'][2031])
        # the path is the one the valuation uses: the year-5 multiple value is (P/E x NOPAT + that cash) / shares
        _, det = lean_model.run_scenario(self.p, self.base, detail=True)
        self.assertEqual(g['net_cash_m'][2031], round(det['cash'][5]))
        self.assertEqual(g['shares_m'], 1280.0)

    def test_funding_flags_unfunded_capex(self):
        # 30bn of capex beyond the conversion path in each of the first two years: the cash runs out
        d = dict(self.base, charge={'0': 30000.0, '1': 30000.0})
        r = lean_model.run(dict(self.p, scenarios=dict(self.p['scenarios'], base=d)))
        f = r['funding']['scenarios']['base']
        self.assertLess(f['low_point_m'], 0)
        self.assertEqual(f['low_point_year'], 2028)
        self.assertEqual(f['outside_money_m'], -f['low_point_m'])   # started with net cash: all of the net debt is new money
        self.assertTrue(f['flag'])
        self.assertTrue(any('outside money' in x for x in f['reasons']))
        self.assertTrue(any('base funding' in w for w in r['warnings']))
        self.assertFalse(r['funding']['scenarios']['bull']['flag'])
        # the leverage reason needs net debt above 3x the low year's after-tax operating profit
        self.assertEqual(f['net_debt_to_nopat_at_low'] > 3, any('above 3x' in x for x in f['reasons']))
        heavy = dict(self.base, charge={'0': 60000.0, '1': 60000.0})
        f = lean_model.run(dict(self.p, scenarios=dict(self.p['scenarios'], base=heavy)))['funding']['scenarios']['base']
        self.assertGreater(f['net_debt_to_nopat_at_low'], 3)
        self.assertTrue(any('above 3x' in x for x in f['reasons']))
        # existing net debt that only shrinks needs no outside money
        f = lean_model.run(dict(self.p, net_cash_m=-5000.0))['funding']['scenarios']['base']
        self.assertEqual((f['low_point_m'], f['outside_money_m'], f['flag']), (-5000, 0, False))

    def test_equity_wiped_is_never_a_negative_price(self):
        r = lean_model.run(dict(self.p, net_cash_m=-400000.0))
        for k in ('bear', 'base'):
            self.assertEqual(r['scenarios'][k]['value_today'], 0.01)   # the model's floor, unchanged
            self.assertTrue(r['funding']['scenarios'][k]['equity_wiped'])
            self.assertIn(2031, r['funding']['scenarios'][k]['equity_wiped_year_ends'])
        self.assertTrue(any('bear: equity wiped' in w for w in r['warnings']))
        bt = r['buy_test']
        self.assertFalse(bt['pass'])
        self.assertIsNone(bt['max_price_passing'])
        self.assertIn('no price passes', bt['no_price_passes'])
        self.assertTrue(bt['legs']['bear']['equity_wiped_at_horizon'])
        txt = lean_model.summary(dict(self.p, net_cash_m=-400000.0), r)
        self.assertIn('wiped', txt)
        self.assertNotIn('-0.0', txt.split('Price ladder')[0].split('value today')[1].split('\n')[0])

    def test_sensitivity_single_changes(self):
        sv = self.r['sensitivity']
        rows = {r['input']: r for r in sv['rows']}
        self.assertEqual(set(rows), {'operating margin', 'revenue growth', 'exit P/E', 'beta', 'free-cash-flow conversion'})
        effects = [r['abs_effect_value'] for r in sv['rows']]
        self.assertEqual(effects, sorted(effects, reverse=True))
        self.assertEqual(sv['moves_most'], sv['rows'][0]['input'])
        self.assertIn('not a forecast', sv['note'])
        base = self.r['scenarios']['base']
        self.assertEqual(sv['base'], {'value_today': base['value_today'], 'annual_return_pct': base['annual_return_pct']})
        # each row is one direct rerun of the model
        direct = {
            'operating margin': lean_model.run_scenario(self.p, dict(self.base, m=[x + 0.01 for x in self.base['m']])),
            'revenue growth': lean_model.run_scenario(self.p, dict(self.base, g=[x + 0.01 for x in self.base['g']])),
            'exit P/E': lean_model.run_scenario(self.p, dict(self.base, pe=27.0)),
            'beta': lean_model.run_scenario(dict(self.p, beta=1.6), self.base),
            'free-cash-flow conversion': lean_model.run_scenario(self.p, dict(self.base, conv=[x + 0.05 for x in self.base['conv']])),
        }
        for name, want in direct.items():
            up = rows[name]['up']
            self.assertEqual(up['value_today'], want['value_today'], msg=name)
            self.assertEqual(up['annual_return_pct'], want['annual_return_pct'], msg=name)
            self.assertAlmostEqual(up['d_value'], want['value_today'] - base['value_today'], delta=0.011, msg=name)
            self.assertAlmostEqual(up['d_return_pts'], want['annual_return_pct'] - base['annual_return_pct'], delta=0.11, msg=name)
        for name in ('operating margin', 'revenue growth', 'exit P/E', 'free-cash-flow conversion'):
            self.assertGreater(rows[name]['up']['d_value'], 0, msg=name)
            self.assertLess(rows[name]['down']['d_value'], 0, msg=name)
        self.assertLess(rows['beta']['up']['d_value'], 0)
        self.assertGreater(rows['beta']['down']['d_value'], 0)
        # the exit multiple enters the value linearly
        self.assertAlmostEqual(rows['exit P/E']['up']['d_value'], -rows['exit P/E']['down']['d_value'], delta=0.011)
        self.assertEqual(self.r['sensitivity_base']['exit P/E 30'][0],
                         lean_model.run_scenario(self.p, dict(self.base, pe=30.0))['value_today'])   # the old key is untouched

    def test_reverse_reading_flat_margin(self):
        rv = self.r['reverse_reading']
        self.assertEqual(rv['base_margin_path']['growth_pct'], self.r['market_implied_growth_pct'])
        self.assertEqual(rv['base_margin_path']['end_revenue_m'], self.r['market_implied_end_revenue_m'])
        self.assertEqual(rv['base_growth_pct'], [38.5, 20.0, 14.0, 11.0, 10.0, 9.0])
        flat = rv['flat_margin']
        self.assertEqual(flat['margin_held'], 0.45)
        self.assertEqual(flat['growth_pct'][0], 38.5)   # guided year held
        self.assertTrue(flat['solved'])
        self.assertTrue(rv['base_margin_path']['solved'])
        # ANET's base margin slips, so a flat margin needs a little less growth for the same price
        self.assertLess(flat['growth_pct'][1], rv['base_margin_path']['growth_pct'][1])
        self.assertGreater(flat['value_today_at_base_growth'], self.r['scenarios']['base']['value_today'])
        # the flat-margin growth path, run through the model, gives the price
        f = (flat['growth_pct'][1] / 100) / self.base['g'][1]
        d = dict(self.base, m=[0.45] * 6, g=[self.base['g'][0]] + [g * f for g in self.base['g'][1:]])
        self.assertAlmostEqual(lean_model.run_scenario(self.p, d)['value_today'], self.p['price'], delta=1.5)
        # a rising margin path: the flat reading needs more growth than the base-path reading
        up = dict(self.p, scenarios=dict(self.p['scenarios'], base=dict(self.base, m=[0.40, 0.42, 0.44, 0.46, 0.48, 0.50])))
        rv = lean_model.run(up)['reverse_reading']
        self.assertGreater(rv['flat_margin']['growth_pct'][1], rv['base_margin_path']['growth_pct'][1])
        # a price no scaling can reach is said, not shown as a number to trust
        r = self.at_price(5000.0)
        self.assertFalse(r['reverse_reading']['flat_margin']['solved'])
        self.assertTrue(any('reverse reading' in w for w in r['warnings']))

    def test_summary_flag(self):
        import contextlib
        import io
        import tempfile
        txt = lean_model.summary(self.p, self.r)
        for want in ('ANET', 'value today', 'end 2028', 'end 2031', 'Price ladder', 'buy test: base return 12% a year',
                     'company hurdle (buy_return): 13% a year', 'Buy test: FAIL', 'binding leg: base_ours', 'What moves the base value most',
                     '1. revenue growth', 'noisiest', 'Warnings: none'):
            self.assertIn(want, txt)
        self.assertEqual(txt.count('\n  1. ') + txt.count('\n  2. ') + txt.count('\n  3. '), 3)   # top three only
        self.assertNotIn('\n  4. ', txt)
        fixture = os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_anet.json')
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'r.json')
            argv, buf = sys.argv, io.StringIO()
            try:
                sys.argv = ['lean_model.py', fixture, '--out', out, '--summary']
                with contextlib.redirect_stdout(buf):
                    lean_model.main()
            finally:
                sys.argv = argv
            self.assertEqual(buf.getvalue().strip(), txt.strip())
            saved = json.load(open(out))   # the full JSON is still written
            self.assertEqual(set(saved), ALL_KEYS)
            buf = io.StringIO()
            try:
                sys.argv = ['lean_model.py', fixture]
                with contextlib.redirect_stdout(buf):
                    lean_model.main()
            finally:
                sys.argv = argv
            self.assertEqual(json.loads(buf.getvalue()), saved)   # without the flag: the JSON, as before


def load_fixture(name='lean_model_anet.json'):
    with open(os.path.join(ROOT, 'tests', 'fixtures', name), encoding='utf-8') as f:
        return json.load(f)


def without(res, *keys):
    return {k: v for k, v in res.items() if k not in keys}


class LeanModelInputTest(unittest.TestCase):
    """Input checks: a broken file stops before the model runs, with every problem named."""

    def setUp(self):
        self.p = load_fixture()

    def problems(self, p):
        with self.assertRaises(lean_model.ParamsError) as cm:
            lean_model.run(p)
        return cm.exception.problems

    def scenario(self, name='base', **change):
        return dict(self.p, scenarios=dict(self.p['scenarios'], **{name: dict(self.p['scenarios'][name], **change)}))

    def test_probabilities_that_do_not_sum_to_one_are_rejected(self):
        got = self.problems(dict(self.p, probabilities={'bear': 0.3, 'base': 0.5, 'bull': 0.4}))
        self.assertEqual(len(got), 1)
        self.assertIn('sum to 1.2, not 1', got[0])
        self.assertIn('sum to 0.99', self.problems(dict(self.p, probabilities={'bear': 0.25, 'base': 0.5, 'bull': 0.24}))[0])
        # inside the tolerance of 1e-6 the file runs
        lean_model.run(dict(self.p, probabilities={'bear': 0.25, 'base': 0.5, 'bull': 0.2500000004}))
        self.assertTrue(any('not a number' in x for x in self.problems(
            dict(self.p, probabilities={'bear': 0.25, 'base': '0.5', 'bull': 0.25}))))
        self.assertTrue(any('not a number' in x for x in self.problems(dict(self.p, probabilities={'bear': 0.5, 'base': 0.5}))))
        self.assertTrue(any('between 0 and 1' in x for x in self.problems(
            dict(self.p, probabilities={'bear': -0.25, 'base': 1.0, 'bull': 0.25}))))

    def test_a_label_inside_probabilities_is_accepted(self):
        p = dict(self.p, probabilities=dict(self.p['probabilities'], label='subjective'))
        r = lean_model.run(p)
        self.assertEqual(r['inputs']['warnings'], [])
        self.assertEqual(r['weighted_value_today'], lean_model.run(self.p)['weighted_value_today'])

    def test_every_problem_is_listed_at_once(self):
        p = self.scenario(g=self.p['scenarios']['base']['g'] + [0.05])
        p = {k: v for k, v in dict(p, price=0, shares_m=-5.0).items() if k != 'tax'}
        got = self.problems(p)
        for want in ('missing key: tax', 'price must be positive', 'shares_m must be positive',
                     'scenario base: g must be a list of six numbers'):
            self.assertTrue(any(want in x for x in got), want)
        self.assertIn('7 values', ' '.join(got))
        self.assertIn('4 problem(s)', str(lean_model.ParamsError(got)))

    def test_units_and_shapes(self):
        cases = [
            (dict(self.p, rf=4.94), 'rf must be a fraction'),
            (dict(self.p, erp=0), 'erp must be a fraction'),
            (dict(self.p, tax=19.5), 'tax must be a fraction'),
            (dict(self.p, beta_mature=0.0), 'beta_mature must be positive'),
            (dict(self.p, roic_terminal=0.04), 'roic_terminal 0.04 must be above rf'),
            (dict(self.p, rev0_m=0), 'rev0_m must be positive'),
            (dict(self.p, base_year=2026.5), 'base_year must be a whole year'),
            (dict(self.p, price='182.95'), 'price must be a number'),
            (dict(self.p, years_to_horizon=0), 'years_to_horizon must be positive'),
            (dict(self.p, scenarios={}), 'scenarios must be an object'),
            (dict(self.p, scenarios={k: v for k, v in self.p['scenarios'].items() if k != 'base'},
                  probabilities={'bear': 0.5, 'bull': 0.5}), 'there is no base scenario'),
            (dict(self.p, scenarios=dict(self.p['scenarios'], tail=self.p['scenarios']['bear'])), 'unknown scenario name'),
            (self.scenario(m=[45, 45, 44.8, 44.6, 44.5, 44.5]), 'above 100%'),
            (self.scenario(g=[0.2, -1.0, 0.1, 0.1, 0.1, 0.1]), 'takes revenue to zero'),
            (self.scenario(conv=[0.9, 0.9, None, 1, 1, 1]), 'not a number'),
            (self.scenario(pe=0), 'pe must be a positive number'),
            (self.scenario(shares_m=0), 'shares_m must be a positive number'),
            (self.scenario('bear', charge={'2028': 1200.0}), 'not a year index 0-5'),
            (self.scenario('bear', charge={'1': 'x'}), 'must be a number'),
            (dict(self.p, calibrated={'pe': -3}), 'calibrated.pe must be a positive number'),
            (dict(self.p, calibrated={'m': [0.4, 0.4]}), 'calibrated.m must be a list of six numbers'),
            (dict(self.p, bands={'buy': 12.0}), 'bands must be an object'),
            ({k: v for k, v in self.p.items() if k != 'years_to_horizon'}, 'missing key: years_to_horizon'),
            ([1, 2], 'not a JSON object'),
        ]
        for p, want in cases:
            got = self.problems(p)
            self.assertTrue(any(want in x for x in got), (want, got))

    def test_suspicious_inputs_are_warnings_not_errors(self):
        r = lean_model.run(self.scenario('bull', g=[6.0, 0.3, 0.2, 0.1, 0.1, 0.1]))
        self.assertTrue(any('above 500% a year' in w for w in r['inputs']['warnings']))
        self.assertEqual(r['warnings'][:len(r['inputs']['warnings'])], r['inputs']['warnings'])   # repeated in the one list
        # a file without a bull case runs, and says the buy test cannot be complete
        p = dict(self.p, scenarios={k: v for k, v in self.p['scenarios'].items() if k != 'bull'},
                 probabilities={'bear': 0.5, 'base': 0.5})
        r = lean_model.run(p)
        self.assertTrue(any('no bull scenario' in w for w in r['warnings']))
        self.assertFalse(r['buy_test']['complete'])
        self.assertNotIn('bull', r['dated_values']['at']['one_year'])
        lean_model.summary(p, r)
        r = lean_model.run({k: v for k, v in self.p.items() if k != 'as_of'})
        self.assertTrue(any('no as_of' in w for w in r['warnings']))
        self.assertFalse(r['dated_values']['available'])
        self.assertIn('not available', lean_model.summary(self.p, r))
        # a valid file has no input warning and its warnings are what they were
        self.assertEqual(lean_model.run(self.p)['inputs']['warnings'], [])

    def test_every_saved_params_file_passes_without_a_warning(self):
        import glob
        need = set(lean_model.REQUIRED) | {'days_to_base_year_end', 'years_to_horizon'}
        seen = 0
        for path in sorted(glob.glob(os.path.join(ROOT, 'research', 'companies', '*', '*', '*.json'))
                           + glob.glob(os.path.join(ROOT, 'research', 'companies', '*', '*', '*', '*.json'))):
            try:
                with open(path, encoding='utf-8') as f:
                    p = json.load(f)
            except ValueError:
                continue
            if not (isinstance(p, dict) and need <= set(p) and isinstance(p['scenarios'], dict)
                    and all(isinstance(d, dict) and 'g' in d for d in p['scenarios'].values())):
                continue   # not a lean_model params file (BNY's bank model, results, handoffs)
            errors, warns, _ = lean_model.check_params(p)
            self.assertEqual((errors, warns), ([], []), os.path.relpath(path, ROOT))
            seen += 1
        self.assertGreaterEqual(seen, 28)   # the 28 published baselines of 15.11.2026 at least

    def test_command_stops_with_code_2_and_the_reasons(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.json')
            with open(bad, 'w') as f:
                json.dump(dict(self.p, probabilities={'bear': 0.3, 'base': 0.5, 'bull': 0.4}), f)
            out = os.path.join(tmp, 'r.json')
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', 'lean_model.py'), bad, '--out', out],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn('sum to 1.2, not 1', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
            self.assertEqual(r.stdout, '')
            self.assertFalse(os.path.exists(out))   # no result is written for a broken file


class LeanModelDatesTest(unittest.TestCase):
    """base_year_end: the days and years are derived from as_of instead of typed by hand."""

    def setUp(self):
        self.p = load_fixture()
        self.dated = load_fixture('lean_model_dated.json')
        self.r = lean_model.run(self.p)

    def test_without_base_year_end_nothing_changes(self):
        inp = self.r['inputs']
        self.assertFalse(inp['dates_derived'])
        self.assertEqual((inp['days_to_base_year_end'], inp['years_to_horizon']), (57, 5.16))
        self.assertEqual((inp['base_year_end'], inp['base_year_end_source']), ('2026-12-31', 'as_of + days_to_base_year_end'))
        self.assertEqual(inp['horizon_date'], '2031-12-31')
        self.assertNotIn('entered', inp)

    def test_derived_dates_equal_the_entered_ones_on_the_fixture(self):
        r = lean_model.run(self.dated)
        inp = r['inputs']
        self.assertTrue(inp['dates_derived'])
        self.assertEqual((inp['days_to_base_year_end'], inp['years_to_horizon']), (57, 5.16))
        self.assertEqual(inp['entered'], {'days_to_base_year_end': 57, 'years_to_horizon': 5.16})
        self.assertEqual(inp['warnings'], [])
        # every number is the one of the hand-entered file
        self.assertEqual(without(r, 'inputs', 'dated_values'), without(self.r, 'inputs', 'dated_values'))
        self.assertEqual(without(r['dated_values'], 'base_year_end_source'), without(self.r['dated_values'], 'base_year_end_source'))
        self.assertEqual(r['dated_values']['base_year_end_source'], 'base_year_end')
        # the hand keys are not needed any more
        only = {k: v for k, v in self.dated.items() if k not in ('days_to_base_year_end', 'years_to_horizon')}
        self.assertEqual(without(lean_model.run(only), 'inputs'), without(r, 'inputs'))
        # the caller's params are not written to
        self.assertEqual(self.dated, load_fixture('lean_model_dated.json'))

    def test_hand_values_that_differ_raise_a_warning_and_the_derived_are_used(self):
        clean = lean_model.run(self.dated)
        r = lean_model.run(dict(self.dated, years_to_horizon=5.3))
        self.assertTrue(any('years_to_horizon 5.3 in the file differs from the derived 5.16' in w for w in r['warnings']))
        self.assertEqual(r['inputs']['years_to_horizon'], 5.16)
        self.assertEqual(r['scenarios'], clean['scenarios'])
        r = lean_model.run(dict(self.dated, days_to_base_year_end=80))
        self.assertTrue(any('days_to_base_year_end 80 in the file differs from the 57 days' in w for w in r['warnings']))
        self.assertEqual(r['scenarios'], clean['scenarios'])
        # inside 0.02 year: no warning (5.17 against 5.16; 60 days against 57)
        r = lean_model.run(dict(self.dated, years_to_horizon=5.17, days_to_base_year_end=60))
        self.assertEqual(r['inputs']['warnings'], [])
        self.assertEqual(r['scenarios'], clean['scenarios'])

    def test_off_calendar_year_end_and_bad_dates(self):
        # a fiscal year that ended before as_of: negative days, a horizon under five years
        r = lean_model.run(dict(self.dated, base_year_end='2026-05-31'))
        self.assertEqual((r['inputs']['days_to_base_year_end'], r['inputs']['years_to_horizon']), (-157, 4.57))
        self.assertEqual(r['inputs']['horizon_date'], '2031-05-31')
        self.assertEqual(len(r['inputs']['warnings']), 2)   # both hand keys are far from the derived ones
        for p, want in ((dict(self.dated, base_year_end='31.12.2026'), 'is not an ISO date'),
                        (dict(self.dated, as_of='4.11.2026'), 'needs an ISO as_of'),
                        ({k: v for k, v in self.dated.items() if k != 'as_of'}, 'needs an ISO as_of')):
            with self.assertRaises(lean_model.ParamsError) as cm:
                lean_model.run(p)
            self.assertTrue(any(want in x for x in cm.exception.problems), want)
        self.assertEqual(lean_model._shift_years(lean_model._date('2028-02-29'), 1).isoformat(), '2029-02-28')


class LeanModelRepriceTest(unittest.TestCase):
    """--price and --as-of: a saved file is repriced without being copied by hand."""

    def setUp(self):
        self.p = load_fixture()
        self.dated = load_fixture('lean_model_dated.json')

    def test_reprice_at_the_same_price_and_date_is_the_original_run(self):
        for p in (self.p, self.dated):
            q = lean_model.reprice(p, price=p['price'], as_of=p['as_of'])
            r, r0 = lean_model.run(q), lean_model.run(p)
            self.assertEqual(without(r, 'inputs', 'warnings'), without(r0, 'inputs', 'warnings'))
            self.assertEqual(without(r['inputs'], 'repriced', 'warnings'), without(r0['inputs'], 'repriced', 'warnings'))
            self.assertEqual(q['repriced']['price_from'], p['price'])
        self.assertEqual(self.p, load_fixture())   # the saved params are not written to

    def test_price_only(self):
        q = lean_model.reprice(self.p, price=150.0)
        self.assertEqual({k: v for k, v in q.items() if k not in ('price', 'repriced')},
                         {k: v for k, v in self.p.items() if k != 'price'})
        r = lean_model.run(q)
        self.assertEqual(without(r, 'inputs'), without(lean_model.run(dict(self.p, price=150.0)), 'inputs'))
        rp = r['inputs']['repriced']
        self.assertEqual((rp['price_from'], rp['price'], rp['dates_moved']), (182.95, 150.0, False))
        self.assertIn('only the price was changed', rp['note'])
        self.assertEqual(r['warnings'], lean_model.run(dict(self.p, price=150.0))['warnings'])   # nothing was asked of the dates

    def test_as_of_without_base_year_end_does_not_move_the_dates_and_says_so(self):
        q = lean_model.reprice(self.p, price=150.0, as_of='2026-11-20')
        self.assertEqual((q['as_of'], q['days_to_base_year_end'], q['years_to_horizon']), ('2026-11-04', 57, 5.16))
        r = lean_model.run(q)
        rp = r['inputs']['repriced']
        self.assertFalse(rp['dates_moved'])
        self.assertIn('the dates were not moved to 2026-11-20', rp['note'])
        self.assertTrue(any('the dates were not moved to 2026-11-20' in w for w in r['warnings']))
        self.assertEqual(r['scenarios'], lean_model.run(dict(self.p, price=150.0))['scenarios'])
        self.assertIn('Repriced: only the price was changed', lean_model.summary(q, r))

    def test_as_of_with_base_year_end_recomputes_the_dates(self):
        q = lean_model.reprice(self.dated, price=150.0, as_of='2026-11-18')
        self.assertEqual((q['as_of'], q['days_to_base_year_end'], q['years_to_horizon']), ('2026-11-18', 43, 5.12))
        r = lean_model.run(q)
        self.assertTrue(r['inputs']['repriced']['dates_moved'])
        self.assertEqual(r['inputs']['warnings'], [])
        by_hand = dict(self.p, price=150.0, as_of='2026-11-18', days_to_base_year_end=43, years_to_horizon=5.12)
        self.assertEqual(without(r, 'inputs', 'dated_values'), without(lean_model.run(by_hand), 'inputs', 'dated_values'))
        self.assertEqual(r['dated_values']['at']['one_year']['date'], '2027-11-18')
        # fourteen days later the value today is higher (less discounting to the same year-end)
        self.assertGreater(r['scenarios']['base']['value_today'], lean_model.run(dict(self.p, price=150.0))['scenarios']['base']['value_today'])

    def test_bad_reprice_arguments(self):
        for kw, want in (({'price': -1.0}, '--price must be a positive number'), ({'as_of': '20.11.2026'}, 'is not an ISO date')):
            with self.assertRaises(lean_model.ParamsError) as cm:
                lean_model.reprice(self.p, **kw)
            self.assertIn(want, cm.exception.problems[0])

    def test_command_line_reprice(self):
        import hashlib
        import subprocess
        import tempfile
        fixture = os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_dated.json')
        with open(fixture, 'rb') as f:
            before = hashlib.sha256(f.read()).hexdigest()
        script = os.path.join(ROOT, 'scripts', 'lean_model.py')
        with tempfile.TemporaryDirectory() as tmp:
            out, pout = os.path.join(tmp, 'r.json'), os.path.join(tmp, 'p.json')
            r = subprocess.run([sys.executable, script, fixture, '--price', '150', '--as-of', '2026-11-18', '--out', out,
                                '--params-out', pout], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out) as f:
                res = json.load(f)
            with open(pout) as f:
                params = json.load(f)
            self.assertEqual(json.loads(r.stdout), res)
            want = lean_model.run(lean_model.reprice(self.dated, 150.0, '2026-11-18'))
            self.assertEqual(res, json.loads(json.dumps(want)))
            self.assertEqual((params['price'], params['as_of'], params['days_to_base_year_end']), (150.0, '2026-11-18', 43))
            # the written params rerun to the same result
            self.assertEqual(json.loads(json.dumps(lean_model.run(params))), res)
            r = subprocess.run([sys.executable, script, fixture, '--as-of', 'soon'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn('is not an ISO date', r.stderr)
        with open(fixture, 'rb') as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), before)   # the saved file is never written


class LeanModelReportingTwoTest(unittest.TestCase):
    """The reporting keys of the engine upgrade: additive, and a wiped scenario is value 0 / return null in them."""

    def setUp(self):
        self.p = load_fixture()
        self.base = self.p['scenarios']['base']
        self.r = lean_model.run(self.p)

    def test_dated_values_carry_a_date_on_every_year_end(self):
        dv = self.r['dated_values']
        self.assertTrue(dv['available'])
        self.assertEqual({y: row['date'] for y, row in dv['year_ends'].items()},
                         {2027: '2027-12-31', 2028: '2028-12-31', 2029: '2029-12-31', 2030: '2030-12-31', 2031: '2031-12-31'})
        for k in ('bear', 'base', 'bull'):
            self.assertEqual({y: row[k] for y, row in dv['year_ends'].items()}, self.r['scenarios'][k]['year_end_values'])
            self.assertEqual(dv['today'][k], self.r['scenarios'][k]['value_today'])
        self.assertEqual({y: row['base_calibrated'] for y, row in dv['year_ends'].items()},
                         self.r['two_lens']['calibrated']['year_end_values'])
        self.assertEqual(dv['today']['date'], '2026-11-04')

    def test_values_at_exactly_one_two_and_five_years(self):
        at = self.r['dated_values']['at']
        self.assertEqual([at[k]['date'] for k in ('one_year', 'two_year', 'five_year')], ['2027-11-04', '2028-11-04', '2031-11-04'])
        self.assertEqual([at[k]['years_from_today'] for k in ('one_year', 'two_year', 'five_year')], [1, 2, 5])
        ye = self.r['scenarios']['base']['year_end_values']
        base = self.r['scenarios']['base']
        # one year from 4.11.2026 is before the 2027 year-end and after the 2026 one
        self.assertLess(base['value_today'], at['one_year']['base'])
        self.assertLess(at['one_year']['base'], ye[2027])
        self.assertLess(ye[2027], at['two_year']['base'])
        self.assertLess(at['two_year']['base'], ye[2028])
        self.assertLess(ye[2030], at['five_year']['base'])
        self.assertLess(at['five_year']['base'], ye[2031])
        self.assertNotIn('beyond_last_year_end_days', at['five_year'])
        # geometric between the two year-ends: 57 days before the 2031 year-end
        import math
        w = 308 / 365
        want = math.exp(math.log(ye[2030]) + w * (math.log(ye[2031]) - math.log(ye[2030])))
        self.assertAlmostEqual(at['five_year']['base'], want, delta=0.01)
        # the return is a yearly rate over exactly that many years
        for key, n in (('one_year', 1), ('two_year', 2), ('five_year', 5)):
            for k in ('bear', 'base', 'bull', 'base_calibrated'):
                self.assertAlmostEqual(at[key]['return_pct'][k], ((at[key][k] / self.p['price']) ** (1 / n) - 1) * 100, delta=0.06)

    def test_dated_values_follow_the_trackers_interpolation(self):
        sys.path.insert(0, ROOT)
        from fundamental_engine import forecast_tracker
        path = forecast_tracker.model_path(self.p, json.loads(json.dumps(self.r)))
        for key, row in self.r['dated_values']['at'].items():
            self.assertAlmostEqual(row['base'], forecast_tracker.value_at(path['value_points'], row['date']), delta=0.006, msg=key)

    def test_a_date_after_the_last_year_end_is_carried_on_and_marked(self):
        # as_of 159 days after the base year-end (the ORCL shape): as_of + 5 years is past the last modelled year-end
        r = lean_model.run(dict(self.p, days_to_base_year_end=-159, years_to_horizon=4.57))
        at = r['dated_values']['at']
        self.assertEqual(r['dated_values']['base_year_end'], '2026-05-29')
        self.assertNotIn('beyond_last_year_end_days', at['two_year'])
        self.assertEqual(at['five_year']['beyond_last_year_end_days'], 159)
        ye = r['scenarios']['base']['year_end_values']
        want = ye[2031] * (ye[2031] / ye[2030]) ** (159 / 365)
        self.assertAlmostEqual(at['five_year']['base'], want, delta=0.01)
        self.assertIn('159 days after the last year-end', lean_model.summary(self.p, r))

    def test_a_wiped_scenario_is_zero_and_null_in_the_new_keys(self):
        r = lean_model.run(dict(self.p, net_cash_m=-400000.0))
        self.assertEqual(r['scenarios']['bear']['value_today'], 0.01)   # the older keys keep the floor
        dv = r['dated_values']
        self.assertEqual(dv['today']['bear'], 0.0)
        self.assertEqual(dv['year_ends'][2031]['bear'], 0.0)
        for key in ('one_year', 'two_year', 'five_year'):
            self.assertEqual(dv['at'][key]['bear'], 0.0)
            self.assertIsNone(dv['at'][key]['return_pct']['bear'])
            self.assertIn('bear', dv['at'][key]['wiped'])
        g = r['method_gap']['scenarios']['bear']
        self.assertEqual((g['averaged']['value_today'], g['averaged']['end_value'], g['averaged']['return_pct']), (0.0, 0.0, None))
        self.assertTrue(g['dcf']['wiped'] and g['exit_multiple']['wiped'])
        self.assertIsNone(g['dcf']['band'])
        w = r['return_with_distributions']['scenarios']['bear']
        self.assertEqual((w['wiped'], w['end_value'], w['return_pct'], w['return_with_distributions_pct']), (True, 0.0, None, None))
        self.assertIn('wiped', lean_model.summary(self.p, r).split('in 1 year')[1].split('\n')[0])
        json.dumps(r)

    def test_method_gap_is_the_two_halves_of_the_value(self):
        mg = self.r['method_gap']
        self.assertEqual(set(mg['scenarios']), {'bear', 'base', 'bull', 'base_calibrated'})
        for k in ('bear', 'base', 'bull'):
            s, g = self.r['scenarios'][k], mg['scenarios'][k]
            self.assertEqual(g['dcf']['value_today'], s['dcf_today'])
            self.assertEqual(g['exit_multiple']['value_today'], s['multiple_today'])
            self.assertEqual(g['averaged']['value_today'], s['value_today'])
            self.assertEqual(g['averaged']['return_pct'], s['annual_return_pct'])
            self.assertAlmostEqual((g['dcf']['end_value'] + g['exit_multiple']['end_value']) / 2, s['year_end_values'][2031], delta=0.011)
            for m in ('dcf', 'exit_multiple'):
                want = ((g[m]['end_value'] / self.p['price']) ** (1 / self.p['years_to_horizon']) - 1) * 100
                self.assertAlmostEqual(g[m]['return_pct'], want, delta=0.06)
                self.assertEqual(g[m]['band'], lean_model.band(g[m]['return_pct']))
            self.assertEqual(g['exit_pe'], self.p['scenarios'][k]['pe'])
            self.assertAlmostEqual(g['exit_pe_to_dcf_implied'], g['exit_pe'] / g['dcf_implied_exit_pe'], delta=0.02)
        base = mg['scenarios']['base']
        self.assertEqual((base['dcf']['band'], base['exit_multiple']['band'], base['bands_differ']), ('hold', 'hold', False))
        self.assertNotIn('warning', base)

    def test_at_the_dcf_implied_multiple_the_two_methods_agree(self):
        implied = self.r['method_gap']['scenarios']['base']['dcf_implied_exit_pe']
        self.assertLess(implied, self.base['pe'])   # the fixture's exit P/E 25 is richer than its DCF (22.1)
        r = lean_model.run(dict(self.p, scenarios=dict(self.p['scenarios'], base=dict(self.base, pe=implied))))
        g = r['method_gap']['scenarios']['base']
        self.assertAlmostEqual(g['dcf']['end_value'], g['exit_multiple']['end_value'], delta=0.005 * g['dcf']['end_value'])
        self.assertAlmostEqual(g['exit_pe_to_dcf_implied'], 1.0, delta=0.01)
        self.assertFalse(g['pe_gap_wide'])

    def test_methods_in_different_bands_raise_a_warning_outside_the_old_list(self):
        # an exit multiple of 45 puts the multiple alone in the buy band while the DCF stays in hold
        p = dict(self.p, scenarios=dict(self.p['scenarios'], base=dict(self.base, pe=45.0)))
        r = lean_model.run(p)
        g = r['method_gap']['scenarios']['base']
        self.assertEqual((g['dcf']['band'], g['exit_multiple']['band']), ('hold', 'buy'))
        self.assertTrue(g['bands_differ'] and g['pe_gap_wide'])
        self.assertIn('base: the two methods land in different bands', g['warning'])
        self.assertIn(g['warning'], r['method_gap']['warnings'])
        self.assertFalse(any('two methods' in w for w in r['warnings']))   # the older key keeps its content
        self.assertIn('  ! base: the two methods land in different bands', lean_model.summary(p, r))

    def test_terminal_sensitivity_rows(self):
        st = self.r['sensitivity_terminal']
        rows = {r['input']: r for r in st['rows']}
        self.assertEqual(set(rows), {'mature beta', 'terminal ROIC', 'terminal growth'})
        self.assertLess(rows['mature beta']['up']['d_value'], 0)
        self.assertGreater(rows['mature beta']['down']['d_value'], 0)
        self.assertGreater(rows['terminal ROIC']['up']['d_value'], 0)
        self.assertLess(rows['terminal ROIC']['down']['d_value'], 0)
        self.assertGreater(rows['terminal growth']['up']['d_value'], 0)
        self.assertLess(rows['terminal growth']['at_3pct']['d_value'], rows['terminal growth']['down']['d_value'])
        # each side is the model rerun with that one input changed
        up = lean_model.run_scenario(dict(self.p, beta_mature=self.p['beta_mature'] + 0.1), self.base)
        self.assertEqual(rows['mature beta']['up']['value_today'], up['value_today'])
        self.assertEqual(rows['terminal ROIC']['down']['value_today'],
                         lean_model.run_scenario(dict(self.p, roic_terminal=self.p['roic_terminal'] - 0.05), self.base)['value_today'])
        # terminal growth is the risk-free rate unless a row says otherwise
        self.assertEqual(lean_model.run_scenario(self.p, self.base, tg=self.p['rf']), lean_model.run_scenario(self.p, self.base))
        self.assertEqual(rows['terminal growth']['at_3pct']['value_today'],
                         lean_model.run_scenario(self.p, self.base, tg=0.03)['value_today'])
        effects = [r['abs_effect_value'] for r in st['rows']]
        self.assertEqual(effects, sorted(effects, reverse=True))
        both = {r['input']: r['abs_effect_value'] for r in self.r['sensitivity']['rows'] + st['rows']}
        self.assertEqual(st['moves_most_overall']['input'], max(both, key=both.get))
        # the older table is what it was
        self.assertEqual({r['input'] for r in self.r['sensitivity']['rows']},
                         {'operating margin', 'revenue growth', 'exit P/E', 'beta', 'free-cash-flow conversion'})
        # an input that leaves no terminal value gives a null side, not a number
        low = lean_model.run(dict(self.p, beta_mature=0.1))['sensitivity_terminal']
        self.assertIsNone({r['input']: r for r in low['rows']}['mature beta']['down'])
        lean_model.summary(self.p, lean_model.run(dict(self.p, beta_mature=0.1)))

    def test_return_with_distributions_is_a_second_line(self):
        rw = self.r['return_with_distributions']
        base = rw['scenarios']['base']
        self.assertEqual(base['return_pct'], self.r['scenarios']['base']['annual_return_pct'])   # the headline, untouched
        self.assertEqual(base['end_value'], self.r['scenarios']['base']['year_end_values'][2031])
        self.assertGreater(base['return_with_distributions_pct'], base['return_pct'])
        self.assertGreater(base['end_value_with_distributions'], base['end_value'])
        self.assertLessEqual(base['return_with_distributions_at_index_pct'], base['return_with_distributions_pct'])   # ke > index
        self.assertEqual(set(base['paid_out_m']), {2027, 2028, 2029, 2030, 2031})
        self.assertIn('never the headline', rw['note'])
        # paid out and reinvested at the rate the cash earns inside the company, nothing changes: the gap is only the rate
        _, det = lean_model.run_scenario(self.p, self.base, detail=True)
        same, paid = lean_model._paid_out(self.p, det, [self.p['cash_yield'] * (1 - self.p['tax'])] * 5)
        self.assertAlmostEqual(same, det['val_raw'][5], places=6)
        self.assertAlmostEqual(paid[0], det['fcf'][0] + det['cash'][0] * self.p['cash_yield'] * (1 - self.p['tax']), places=6)
        # a company in net debt repays first: nothing is paid out while the debt stands
        debt = lean_model.run(dict(self.p, net_cash_m=-20000.0))['return_with_distributions']['scenarios']['base']
        self.assertEqual(debt['paid_out_m'][2027], 0)
        self.assertGreater(debt['paid_out_m'][2031], 0)
        _, det = lean_model.run_scenario(dict(self.p, net_cash_m=-20000.0), self.base, detail=True)
        same, _ = lean_model._paid_out(dict(self.p, net_cash_m=-20000.0), det, [self.p['cash_yield'] * (1 - self.p['tax'])] * 5)
        self.assertAlmostEqual(same, det['val_raw'][5], places=6)

    def test_a_band_change_with_distributions_is_named_not_applied(self):
        price = round(self.r['price_ladder']['rungs']['buy_test']['ours'] * 1.01, 2)   # just under the buy line
        r = lean_model.run(dict(self.p, price=price))
        self.assertEqual(r['two_lens']['ours']['band'], 'hold')
        rw = r['return_with_distributions']
        self.assertIn('base', rw['band_changes'])
        self.assertEqual((rw['scenarios']['base']['band'], rw['scenarios']['base']['band_with_distributions']), ('hold', 'buy'))
        self.assertFalse(r['buy_test']['legs']['base_ours']['pass'])   # the buy test still reads the headline

    def test_fixed_13_percent_rung(self):
        rungs = self.r['price_ladder']['rungs']
        r13 = rungs['margin_of_safety_13']
        self.assertEqual(r13['return_pct'], 13.0)
        self.assertIn('13%', r13['label'])
        end = self.r['scenarios']['base']['year_end_values'][2031]
        self.assertEqual(r13['ours'], round(end / 1.13 ** self.p['years_to_horizon'], 2))
        self.assertEqual(r13['ours'], rungs['margin_of_safety']['ours'])   # the fixture's buy_return is 13%
        # with a company hurdle of 12% the two rungs differ, and the older rung is what it was
        r = lean_model.run(dict(self.p, buy_return=0.12))
        rungs = r['price_ladder']['rungs']
        self.assertEqual(list(rungs), ['margin_of_safety_13', 'buy_test', 'margin_of_safety', 'index', 'hold_floor', 'sell_line'])
        self.assertEqual(rungs['margin_of_safety_13']['ours'], r13['ours'])   # fixed: it does not follow buy_return
        self.assertEqual(rungs['margin_of_safety_13']['calibrated'], r13['calibrated'])
        mos = rungs['margin_of_safety']
        self.assertEqual((mos['label'], mos['ours'], mos['same_as']),
                         ('entry with a margin of safety: buy_return 12% a year', r['buy_price'], 'buy_price'))
        self.assertEqual(mos['meaning'], 'company hurdle (buy_return): 12% a year')
        self.assertLess(rungs['margin_of_safety_13']['ours'], mos['ours'])
        self.assertEqual(mos['ours'], rungs['buy_test']['ours'])
        # bought at the 13% rung, the base return is 13%
        again = lean_model.run(dict(self.p, price=r13['ours']))
        self.assertAlmostEqual(again['scenarios']['base']['annual_return_pct'], 13.0, delta=0.1)

    def test_summary_shows_the_new_lines(self):
        txt = lean_model.summary(self.p, self.r)
        for want in ('Dates: base year-end 2026-12-31, 57 days from as_of; 5.16 years to the horizon year-end 2031-12-31',
                     'in 1 year (2027-11-04)', 'in 2 years (2028-11-04)', 'in 5 years (2031-11-04)',
                     'with distributions', 'second line, not the headline', 'Method gap', 'DCF implies', 'base (cal.)',
                     'margin of safety: fixed 13% a year', 'terminal inputs', '- mature beta +/- 0.1',
                     '- terminal growth +/- 0.5 point', 'at 3% growth', '- terminal ROIC +/- 5 points'):
            self.assertIn(want, txt)
        at = self.r['dated_values']['at']
        self.assertIn(f'{at["one_year"]["base"]:,.2f}', txt.split('in 1 year')[1].split('\n')[0])
        self.assertIn(f'{self.r["method_gap"]["scenarios"]["base"]["dcf_implied_exit_pe"]:.1f}', txt.split('Method gap')[1])
        self.assertIn('derived from base_year_end', lean_model.summary(load_fixture('lean_model_dated.json'),
                                                                       lean_model.run(load_fixture('lean_model_dated.json'))))


if __name__ == '__main__':
    unittest.main()
