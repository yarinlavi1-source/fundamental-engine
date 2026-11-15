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
        self.assertEqual(set(self.r), OLD_KEYS | NEW_KEYS)
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
        self.assertEqual(list(rungs), ['margin_of_safety', 'buy_test', 'index', 'hold_floor', 'sell_line'])  # 13 > 12 > 9.14 > 6 > 3
        self.assertEqual([rungs[k]['return_pct'] for k in rungs], [13.0, 12.0, 9.14, 6.0, 3.0])
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
                     'buy_return 13% a year', 'Buy test: FAIL', 'binding leg: base_ours', 'What moves the base value most',
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
            self.assertEqual(set(saved), OLD_KEYS | NEW_KEYS)
            buf = io.StringIO()
            try:
                sys.argv = ['lean_model.py', fixture]
                with contextlib.redirect_stdout(buf):
                    lean_model.main()
            finally:
                sys.argv = argv
            self.assertEqual(json.loads(buf.getvalue()), saved)   # without the flag: the JSON, as before


if __name__ == '__main__':
    unittest.main()
