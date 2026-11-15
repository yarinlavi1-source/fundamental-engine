import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import lean_model  # noqa: E402
from fundamental_engine import lean_check as LC  # noqa: E402
from fundamental_engine import locked_rules as LR  # noqa: E402

FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_dated.json')
LESSONS = os.path.join(ROOT, 'research', 'lessons.jsonl')
SCRIPT = os.path.join(ROOT, 'scripts', 'lean_check.py')
CATEGORIES = {'inputs', 'growth path', 'margin', 'multiple', 'funding', 'capital structure', 'dates and hurdles',
              'publishing', 'data source', 'process'}
FIELDS = ('id', 'date', 'company', 'what_went_wrong', 'category', 'check', 'enforcement', 'blocks_buy', 'source')


def params():
    with open(FIXTURE) as f:
        return json.load(f)


def lessons():
    with open(LESSONS, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def fact(start, end, val, filed, form='10-Q'):
    return {'start': start, 'end': end, 'val': val, 'filed': filed, 'form': form}


def facts(ytd=9100e6, prior_ytd=7000e6, prior_fy=10000e6, diluted=1280e6, cover=1270e6, cash=9000e6, invest=5000e6, debt=1000e6):
    """A small companyfacts payload for a calendar-year company: nine months of 2026 reported, 2025 complete."""
    return {'facts': {
        'us-gaap': {
            'Revenues': {'units': {'USD': [
                fact('2025-01-01', '2025-12-31', prior_fy, '2026-02-15', '10-K'),
                fact('2025-01-01', '2025-09-30', prior_ytd, '2025-10-30'),
                fact('2026-01-01', '2026-09-30', ytd, '2026-10-30')]}},
            'WeightedAverageNumberOfDilutedSharesOutstanding': {'units': {'shares': [
                fact('2026-01-01', '2026-09-30', diluted * 0.99, '2026-10-30'),
                fact('2026-07-01', '2026-09-30', diluted, '2026-10-30')]}},
            'CashAndCashEquivalentsAtCarryingValue': {'units': {'USD': [{'end': '2026-09-30', 'val': cash, 'filed': '2026-10-30'}]}},
            'ShortTermInvestments': {'units': {'USD': [{'end': '2026-09-30', 'val': invest, 'filed': '2026-10-30'}]}},
            'LongTermDebt': {'units': {'USD': [{'end': '2026-09-30', 'val': debt, 'filed': '2026-10-30'}]}}},
        'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [{'end': '2026-10-25', 'val': cover, 'filed': '2026-10-30'}]}}}}}


def run(p, **kw):
    kw.setdefault('lessons', lessons())
    return LC.run_checks(lean_model, p, **kw)


def item(doc, id):
    return next(x for x in doc['items'] if x['id'] == id)


def buyable(p):
    """The fixture at a price where the buy test passes."""
    q = lean_model.reprice(copy.deepcopy(p), 110.0)
    assert lean_model.run(copy.deepcopy(q))['buy_test']['pass']
    return q


class LessonsFileTest(unittest.TestCase):
    def test_every_line_has_the_fields_and_a_known_category(self):
        rows = lessons()
        self.assertGreaterEqual(len(rows), 27)
        self.assertEqual(len({r['id'] for r in rows}), len(rows))
        for r in rows:
            for k in FIELDS:
                self.assertIn(k, r, r['id'])
            self.assertIn(r['category'], CATEGORIES, r['id'])
            self.assertIsInstance(r['blocks_buy'], bool, r['id'])
            self.assertTrue(r['enforcement'] == 'manual' or r['enforcement'].startswith(('auto: ', 'test: ')), r['id'])
            if r['enforcement'] == 'manual':
                self.assertIn(r.get('ask'), ('always', 'buy', 'prev', 'asset_heavy', 'never'), r['id'])
        self.assertEqual([f'L{i:02d}' for i in range(1, 28)], sorted(r['id'] for r in rows if r['id'].startswith('L')))

    def test_every_auto_lesson_names_a_check_that_exists_with_the_same_blocking(self):
        by_fn = {}
        for id, (fn, _, blocks) in LC.AUTO.items():
            by_fn.setdefault(fn, []).append(blocks)
        for r in lessons():
            if r['enforcement'].startswith('auto: '):
                mod, fn = r['enforcement'][6:].split('.')
                self.assertEqual(mod, 'lean_check', r['id'])
                self.assertTrue(callable(getattr(LC, fn, None)), r['id'])
                self.assertIn(fn, by_fn, r['id'])
                self.assertEqual(r['blocks_buy'], any(by_fn[fn]), r['id'])
        self.assertTrue(all(callable(getattr(LC, fn)) for fn in by_fn))

    def test_every_test_lesson_names_a_test_that_exists(self):
        for r in lessons():
            if r['enforcement'].startswith('test: '):
                mod, name = r['enforcement'][6:].rsplit('.', 1)
                m = importlib.import_module(mod)
                found = any(hasattr(c, name) for c in vars(m).values() if isinstance(c, type))
                self.assertTrue(found, f"{r['id']}: {mod}.{name}")

    def test_the_unrecorded_lesson_says_so(self):
        r = next(x for x in lessons() if x['id'] == 'L27')
        self.assertEqual(r['source'], 'session note, not on file')


class ModelWarningsTest(unittest.TestCase):
    def test_a_clean_run_passes_the_model_items(self):
        doc = run(params())
        for id in ('inputs', 'lenses_agree', 'funding', 'equity_wiped', 'reverse_reading'):
            self.assertEqual(item(doc, id)['status'], 'pass', id)
        self.assertEqual(item(doc, 'probabilities_label')['status'], 'fail')      # the fixture has no label
        p = params()
        p['probabilities']['label'] = 'subjective (judgement)'
        self.assertEqual(item(run(p), 'probabilities_label')['status'], 'pass')

    def test_lenses_in_different_bands_fail(self):
        p = params()
        p['calibrated'] = {'pe': 60.0, 'why': 'test'}
        doc = run(p)
        self.assertEqual(item(doc, 'lenses_agree')['status'], 'fail')

    def test_unfunded_base_blocks_and_unfunded_bear_does_not(self):
        p = params()
        p['net_cash_m'] = -60000.0
        f = item(run(p), 'funding')
        self.assertEqual((f['status'], f['blocks_buy']), ('fail', True))
        p = params()
        p['scenarios']['bear']['conv'] = [-3.0] * 6
        f = item(run(p), 'funding')
        self.assertEqual((f['status'], f['blocks_buy']), ('fail', False))

    def test_base_methods_in_different_bands_fail_without_blocking(self):
        p = params()
        p['scenarios']['base']['pe'] = 8.0
        res = lean_model.run(copy.deepcopy(p))
        m = item(run(p), 'method_gap')
        self.assertEqual(m['status'], 'fail' if res['method_gap']['scenarios']['base']['bands_differ'] else 'pass')
        self.assertTrue(res['method_gap']['scenarios']['base']['bands_differ'])
        self.assertFalse(m['blocks_buy'])


class ReconciliationTest(unittest.TestCase):
    """2026: nine months 9,100 against 7,000 (+30%); the last quarter of 2025 was 3,000: the year points to 13,000."""

    def setUp(self):
        self.p = params()
        self.p.pop('notes', None)
        self.p['rev0_m'] = 13000.0

    def test_without_facts_not_applicable(self):
        doc = run(self.p)
        for id in ('reconcile_revenue', 'reconcile_shares', 'reconcile_net_cash'):
            self.assertEqual(item(doc, id)['status'], 'not_applicable')

    def test_revenue_within_three_percent_passes(self):
        r = item(run(self.p, facts=facts()), 'reconcile_revenue')
        self.assertEqual(r['status'], 'pass')
        self.assertEqual(r['gap_pct'], 0.0)

    def test_a_known_one_off_needs_to_be_stated(self):
        self.p['rev0_m'] = 12400.0                                   # 600 of one-off revenue taken out, said nowhere
        r = item(run(self.p, facts=facts()), 'reconcile_revenue')
        self.assertEqual((r['status'], r['gap_pct']), ('fail', -4.6))
        self.p['one_off_revenue_m'] = 600.0                          # stated as a number
        self.assertEqual(item(run(self.p, facts=facts()), 'reconcile_revenue')['status'], 'pass')
        del self.p['one_off_revenue_m']
        self.p['notes'] = {'base_year': '600 of one-quarter partnership revenue taken out of 2026'}
        r = item(run(self.p, facts=facts()), 'reconcile_revenue')
        self.assertEqual((r['status'], r['reason_in']), ('pass', 'notes.base_year'))

    def test_a_reported_base_year_is_compared_directly(self):
        f = facts()
        f['facts']['us-gaap']['Revenues']['units']['USD'].append(fact('2026-01-01', '2026-12-31', 12000e6, '2027-02-15', '10-K'))
        r = item(run(self.p, facts=f), 'reconcile_revenue')
        self.assertEqual((r['status'], r['gap_pct']), ('fail', 8.3))
        r = item(run(self.p, facts=f, facts_cutoff='2026-11-04'), 'reconcile_revenue')   # as the valuation stood
        self.assertEqual(r['status'], 'pass')

    def test_shares_over_three_percent_apart_need_a_reason(self):
        r = item(run(self.p, facts=facts()), 'reconcile_shares')
        self.assertEqual(r['status'], 'pass')
        r = item(run(self.p, facts=facts(cover=1200e6)), 'reconcile_shares')
        self.assertEqual(r['status'], 'fail')
        self.assertEqual(r['gaps_pct']['cover-page count'], 6.7)
        self.p['notes'] = {'shares': 'diluted count plus the convert shares above the cap'}
        self.assertEqual(item(run(self.p, facts=facts(cover=1200e6)), 'reconcile_shares')['status'], 'pass')

    def test_net_cash_against_cash_and_debt_tags(self):
        self.p['net_cash_m'] = 13500.0                               # reported 9,000 + 5,000 - 1,000 = 13,000
        r = item(run(self.p, facts=facts()), 'reconcile_net_cash')
        self.assertEqual(r['status'], 'pass')
        self.p['net_cash_m'] = 30000.0
        r = item(run(self.p, facts=facts()), 'reconcile_net_cash')
        self.assertEqual(r['status'], 'fail')
        f = facts()
        del f['facts']['us-gaap']['LongTermDebt']
        self.assertEqual(item(run(self.p, facts=f), 'reconcile_net_cash')['status'], 'not_applicable')


class GuidanceAndPathTest(unittest.TestCase):
    def test_no_guidance_block_is_open_until_answered(self):
        g = item(run(params()), 'guidance')
        self.assertEqual((g['status'], g['blocks_buy']), ('open', True))
        self.assertEqual(item(run(params(), answers={'L14': 'no guide given; consensus 18.4B'}), 'guidance')['status'], 'pass')

    def test_the_guided_year_inside_and_outside_the_guide(self):
        p = params()                                                 # base 2027 revenue 18,493, margin 45%
        p['guidance'] = {'year': 2027, 'revenue_low': 18000, 'revenue_high': 19000, 'margin_low': 0.43, 'margin_high': 0.46,
                         'source': 'Q3 release'}
        self.assertEqual(item(run(p), 'guidance')['status'], 'pass')
        p['guidance']['revenue_low'] = 19000
        p['guidance']['revenue_high'] = 20000
        g = item(run(p), 'guidance')
        self.assertEqual(g['status'], 'fail')
        self.assertIn('outside the guide', g['detail'])
        p['guidance'] = [{'year': 2026, 'revenue_low': 13300, 'revenue_high': 13400, 'source': 'release'},
                         {'year': 2027, 'margin_low': 0.47, 'source': 'call'}]
        g = item(run(p), 'guidance')
        self.assertEqual(g['status'], 'fail')
        self.assertIn('2027 margin', g['detail'])
        p['guidance'] = {'year': 2027, 'revenue_low': 18000, 'revenue_high': 19000}
        self.assertIn('no source', item(run(p), 'guidance')['detail'])

    def test_digestion_year(self):
        self.assertIsNone(LC.has_digestion_year([0.583, 0.378, 0.12, 0.08, 0.06, 0.05]))     # a fade is not one
        self.assertEqual(LC.has_digestion_year([0.583, 0.217, -0.08, 0.06, 0.06, 0.05]), 2)
        self.assertEqual(LC.has_digestion_year([0.18, 0.03, 0.05, 0.05, 0.04, 0.04]), 1)
        p = params()                                                 # no tags, first year +38.5%, a plain fade
        self.assertEqual(item(run(p), 'digestion_year')['status'], 'open')
        self.assertEqual(item(run(p, answers={'L21': 'sells to enterprises, not a capex cycle'}), 'digestion_year')['status'], 'pass')
        p['tags'] = ['not_capex_cycle']
        self.assertEqual(item(run(p), 'digestion_year')['status'], 'not_applicable')
        p['tags'] = ['capex_cycle']
        self.assertEqual(item(run(p), 'digestion_year')['status'], 'open')
        p['scenarios']['base']['g'] = [0.385, 0.2, -0.05, 0.11, 0.1, 0.09]
        self.assertEqual(item(run(p), 'digestion_year')['status'], 'pass')


class PriceTest(unittest.TestCase):
    def test_trading_days(self):
        self.assertEqual(LC.trading_days_between('2026-11-13', '2026-11-16'), 1)             # Friday to Monday
        self.assertEqual(LC.trading_days_between('2026-11-04', '2026-11-04'), 0)
        self.assertEqual(LC.trading_days_between('2026-11-04', '2026-11-11'), 5)

    def test_no_quote_and_no_time_is_open(self):
        self.assertEqual(item(run(params()), 'price')['status'], 'open')

    def test_a_stale_price_fails(self):
        p = params()
        p['price_time_utc'] = '2026-10-27 20:00 (close)'             # six trading days before as_of
        self.assertEqual(item(run(p), 'price')['status'], 'fail')
        p['price_time_utc'] = '2026-11-03 21:00'
        self.assertEqual(item(run(p), 'price')['status'], 'pass')
        d = item(run(params(), today='2026-11-15'), 'price')         # the valuation itself is seven trading days old
        self.assertEqual(d['status'], 'fail')
        self.assertIn('reprice', d['detail'])

    def test_a_quote_more_than_three_percent_away_fails_and_is_rerun(self):
        p = params()
        self.assertEqual(item(run(p, price=p['price'] * 1.02), 'price')['status'], 'pass')
        d = item(run(p, price=p['price'] * 0.94), 'price')
        self.assertEqual((d['status'], d['move_pct']), ('fail', -6.0))
        self.assertIn('buy_test_pass_at_quote', d)
        self.assertTrue(d['blocks_buy'])


class RulesTest(unittest.TestCase):
    def test_no_judgement_block_is_open(self):
        j = item(run(params()), 'judgement')
        self.assertEqual((j['status'], j['blocks_buy']), ('open', True))

    def test_a_block_that_differs_without_a_reason_fails(self):
        p = params()
        p['judgement'] = {'beta': {'raw': 1.2, 'adjusted': 1.134, 'weeks': 104, 'window': {'from': '2024-11-01', 'to': '2026-10-30'},
                                   'used': 1.5, 'reason_if_different': ''},
                          'exit_pe': {'own_median': 25.0, 'basis': 'forward_pe', 'months': 60, 'ratio_to_model_eps': 1.0, 'used': 25.0},
                          'beta_mature': {'value': 1.1, 'source': 'sector table'},
                          'roic_terminal': {'value': 0.6, 'source': 'own 5-year ROIC'}}
        j = item(run(p), 'judgement')
        self.assertEqual(j['status'], 'fail')
        self.assertIn('reason_if_different is empty', j['detail'])
        p['judgement']['beta']['reason_if_different'] = 'two-year window holds the 2025 drawdown'
        self.assertEqual(item(run(p), 'judgement')['status'], 'pass')

    def test_rf_and_erp_against_the_market_file(self):
        market = LR.load_market()
        p = params()
        self.assertEqual(item(run(p, market=market), 'market_rates')['status'], 'pass')
        p['rf'] = 0.049
        m = item(run(p, market=market), 'market_rates')
        self.assertEqual((m['status'], m['blocks_buy']), ('fail', False))
        self.assertEqual(item(run(p), 'market_rates')['status'], 'not_applicable')


class PreviousTest(unittest.TestCase):
    def test_no_previous_params(self):
        doc = run(params())
        self.assertEqual(item(doc, 'prev_inputs')['status'], 'not_applicable')
        self.assertEqual(item(doc, 'prev_levels')['status'], 'not_applicable')

    def test_each_changed_input_with_its_own_effect(self):
        prev, new = params(), params()
        new['scenarios']['base']['pe'] = 22.0
        new['shares_m'] = 1300.0
        new['notes'] = {'pe': 'a note is not an input'}
        d = item(run(new, prev=prev), 'prev_inputs')['diff']
        self.assertEqual([r['input'] for r in d['changes']], ['scenarios.base.pe', 'shares_m'])
        for r in d['changes']:
            alone = copy.deepcopy(prev)
            if r['input'] == 'shares_m':
                alone['shares_m'] = 1300.0
            else:
                alone['scenarios']['base']['pe'] = 22.0
            want = lean_model.run(alone)['scenarios']['base']['value_today'] - d['base_value_before']
            self.assertAlmostEqual(r['base_value_effect'], want, places=2)
        self.assertAlmostEqual(d['base_value_change'], d['base_value_after'] - d['base_value_before'], places=2)

    def test_a_change_of_odds_alone_moves_only_the_weighted_value(self):
        prev, new = params(), params()
        new['probabilities'] = {'bear': 0.35, 'base': 0.5, 'bull': 0.15}
        it = item(run(new, prev=prev), 'prev_inputs')
        d = it['diff']
        self.assertEqual(d['base_value_change'], 0.0)
        group = next(r for r in d['changes'] if r['input'] == 'probabilities (all)')
        self.assertEqual(group['base_value_effect'], 0.0)
        self.assertAlmostEqual(group['weighted_value_effect'], d['weighted_value_change'], places=2)
        self.assertLess(d['weighted_value_change'], 0)
        self.assertIn('odds alone', it['detail'])

    def test_a_rebased_year_hiding_a_level_rise(self):
        prev, new = params(), params()
        new['rev0_m'] = round(prev['rev0_m'] * 1.26, 1)              # the growth rates are untouched
        lv = item(run(new, prev=prev), 'prev_levels')
        self.assertEqual(lv['status'], 'fail')
        self.assertEqual(lv['levels']['hidden_level_change_years'], [2027, 2028, 2029, 2030, 2031, 2032])
        self.assertEqual(lv['levels']['years'][1]['revenue_change_pct'], 26.0)
        self.assertEqual(item(run(new, prev=prev, answers={'L23': 'base year +26% after the Q3 report'}), 'prev_levels')['status'], 'pass')

    def test_a_visible_growth_change_is_not_called_hidden(self):
        prev, new = params(), params()
        new['scenarios']['base']['g'] = [0.45, 0.2, 0.14, 0.11, 0.1, 0.09]
        lv = item(run(new, prev=prev), 'prev_levels')
        self.assertEqual(lv['levels']['hidden_level_change_years'], [2028, 2029, 2030, 2031, 2032])
        self.assertEqual(lv['status'], 'fail')                       # later years: same growth, a level 4.7% higher
        new['scenarios']['base']['g'] = [0.39, 0.2, 0.14, 0.11, 0.1, 0.09]
        self.assertEqual(item(run(new, prev=prev), 'prev_levels')['status'], 'pass')


class ManualAndExitTest(unittest.TestCase):
    def test_manual_questions_come_from_the_lessons_file(self):
        doc = run(params())
        ask = {x['id']: x for x in doc['items'] if x['id'].startswith('ask.')}
        for id in ('ask.L11', 'ask.L12', 'ask.L15', 'ask.L16', 'ask.S04'):
            self.assertEqual(ask[id]['status'], 'open', id)
        self.assertEqual(ask['ask.L18']['status'], 'not_applicable')            # not a buy at the fixture price
        self.assertEqual(ask['ask.P06']['status'], 'not_applicable')            # no previous params
        self.assertNotIn('ask.L20', ask)                                        # a process rule, outside the valuation
        doc = run(params(), answers={'L11': 'none: no one-off revenue in 2026', 'L16': 'x'})
        ask = {x['id']: x for x in doc['items'] if x['id'].startswith('ask.')}
        self.assertEqual(ask['ask.L11']['status'], 'pass')
        self.assertEqual(ask['ask.L16']['status'], 'open')                      # one letter is not an answer
        self.assertEqual(doc['answers_given'], ['L11'])

    def test_asset_heavy_questions_follow_the_tags(self):
        p = params()
        self.assertEqual(item(run(p), 'ask.L13')['status'], 'not_applicable')
        p['tags'] = ['asset_heavy']
        self.assertEqual(item(run(p), 'ask.L13')['status'], 'open')

    def test_not_a_buy_never_blocks(self):
        doc = run(params())
        self.assertFalse(doc['buy_test_pass'])
        self.assertEqual((doc['exit_code'], doc['blocking']), (0, []))
        self.assertIn('ask.L11', doc['would_block_a_buy'])

    def test_a_pass_at_the_quote_blocks_like_a_buy(self):
        p = params()                                                         # not a buy at the params price
        doc = run(p, price=110.0)                                            # the fixture's buy test passes at 110
        self.assertFalse(doc['buy_test_pass'])
        self.assertTrue(doc['buy_test_pass_at_quote'])
        self.assertEqual(doc['exit_code'], 1)
        self.assertIn('price', doc['blocking'])
        self.assertEqual(item(doc, 'ask.L18')['status'], 'open')             # the buy-only questions are asked
        self.assertIn('at the quote', doc['verdict'])
        doc = run(p, price=p['price'] * 0.94)                                # moved, but still not a buy at the quote
        self.assertFalse(doc['buy_test_pass_at_quote'])
        self.assertEqual((doc['exit_code'], doc['blocking']), (0, []))

    def test_a_buy_with_open_blocking_items_exits_non_zero_until_they_are_closed(self):
        p = buyable(params())
        doc = run(p)
        self.assertTrue(doc['buy_test_pass'])
        self.assertEqual(doc['exit_code'], 1)
        self.assertIn('ask.L18', doc['blocking'])
        answers = {r['id']: 'answered in the test' for r in lessons()}
        p['probabilities']['label'] = 'subjective'
        doc = run(p, answers=answers, price=p['price'])
        self.assertEqual([x['id'] for x in doc['items'] if x['status'] in ('fail', 'open') and x['blocks_buy']], [])
        self.assertEqual(doc['exit_code'], 0)
        doc = run(p, answers=answers, price=p['price'] * 1.05)                  # the quote moved 5%
        self.assertEqual((doc['exit_code'], doc['blocking']), (1, ['price']))

    def test_the_params_are_never_changed(self):
        p = buyable(params())
        before = copy.deepcopy(p)
        prev = params()
        prev_before = copy.deepcopy(prev)
        run(p, prev=prev, facts=facts(), price=100.0, today='2026-11-15', market=LR.load_market())
        self.assertEqual(p, before)
        self.assertEqual(prev, prev_before)

    def test_render_is_a_short_table(self):
        text = LC.render(run(params()), show=('fail', 'open'))
        self.assertIn('lean_check ANET 2026-11-04', text)
        self.assertIn('OPEN', text)
        self.assertNotIn('\npass ', text)
        self.assertIn('not a buy at this price: nothing blocks', text)


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params = os.path.join(self.tmp, 'lean_params.json')
        shutil.copy(FIXTURE, self.params)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def cmd(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_nothing_is_written_without_save(self):
        with open(self.params, 'rb') as f:
            before = f.read()
        r = self.cmd(self.params)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('OPEN', r.stdout)
        self.assertEqual(os.listdir(self.tmp), ['lean_params.json'])
        r = self.cmd(self.params, '--save')
        self.assertEqual(sorted(os.listdir(self.tmp)), ['lean_check.json', 'lean_params.json'])
        with open(self.params, 'rb') as f:
            self.assertEqual(f.read(), before)
        with open(os.path.join(self.tmp, 'lean_check.json')) as f:
            doc = json.load(f)
        self.assertEqual((doc['ticker'], doc['check_version']), ('ANET', LC.CHECK_VERSION))

    def test_answers_file_next_to_the_params_is_read(self):
        with open(os.path.join(self.tmp, 'lean_check_answers.json'), 'w') as f:
            json.dump({'L11': 'none: no one-off revenue'}, f)
        doc = json.loads(self.cmd(self.params, '--json').stdout)
        self.assertEqual(doc['answers_given'], ['L11'])

    def test_a_buy_with_open_items_exits_one_and_broken_params_exit_two(self):
        with open(self.params) as f:
            p = json.load(f)
        with open(self.params, 'w') as f:
            json.dump(buyable(p), f)
        self.assertEqual(self.cmd(self.params).returncode, 1)
        p['probabilities'] = {'bear': 0.3, 'base': 0.5, 'bull': 0.4}
        with open(self.params, 'w') as f:
            json.dump(p, f)
        r = self.cmd(self.params)
        self.assertEqual(r.returncode, 2)
        self.assertIn('cannot be run', r.stderr)


if __name__ == '__main__':
    unittest.main()
