"""portfolio_health.py, due, research-latest and the P9 parts of holdings_watch on FIXTURE data only (no network).

Positions, rules and valuation folders are synthetic test data built in a temporary tree from the lean_model fixture
(tests/fixtures/lean_model_dated.json); no real holding, amount or valuation is read.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_monitor import Clock, later_fb, thesis  # noqa: E402
from fundamental_engine import holdings_watch as HW  # noqa: E402
from fundamental_engine import monitor as M  # noqa: E402
from fundamental_engine import portfolio_health as PH  # noqa: E402
from fundamental_engine import research_store as RS  # noqa: E402
from fundamental_engine.ledger import Ledger  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'lean_model_dated.json')
LM = PH.lean_module()


def fixture():
    with open(FIX, encoding='utf-8') as f:
        return json.load(f)


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)


def lean_folder(root, t, d, **over):
    """research/companies/<t>/<d>/ with fixture lean params (ticker and date replaced) and their lean_model result."""
    p = fixture()
    p.update(ticker=t, as_of=d, **over)
    folder = os.path.join(root, 'research', 'companies', t, d)
    _dump(os.path.join(folder, 'lean_params.json'), p)
    _dump(os.path.join(folder, 'lean_result.json'), LM.run(p))
    return p


def handoff(root, t, d, **over):
    obj = {'ticker': t, 'as_of': d, 'price': 100.0, 'verdict_he': 'סינתטי', 'values': {'today': {'bear': 50, 'base': 110, 'bull': 160}},
           'conditions': {'add': ['תנאי הוספה סינתטי'], 'proposed_exit': ['מפריך סינתטי']}, 'next_report': {'date': '2026-11-20'}}
    obj.update(over)
    _dump(os.path.join(root, 'research', 'technical', t, d, 'fundamental.json'), obj)


def expected(p, price, when, bands):
    """Hand route: the params with base_year_end, repriced, run; the bands of the base return in each lens."""
    q, _ = PH.with_base_year_end(p)
    res = LM.run(LM.reprice(q, price=price, as_of=when))
    ours, cal = res['scenarios']['base']['annual_return_pct'], res['two_lens']['calibrated']['annual_return_pct']
    return res, ours, cal, PH.band(ours, bands), PH.band(cal, bands)


class Tree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.p_a = lean_folder(cls.root, 'AAA', '2026-11-04')
        handoff(cls.root, 'AAA', '2026-11-04', price=cls.p_a['price'])
        base = fixture()['scenarios']['base']
        eps_pe = LM.run(fixture())['two_lens']['calibrated']['pe_today']
        scen = copy.deepcopy(fixture()['scenarios'])
        scen['base'] = dict(base, pe=round(eps_pe / 2, 1))          # the held multiple is 2x the base exit: not a 2nd opinion
        cls.p_b = lean_folder(cls.root, 'BBB', '2026-11-05', scenarios=scen)
        cls.rules = PH.load_rules(obj={
            'study_fund': {'amount': 3000.0, 'currency': 'USD', 'draft': PH.DRAFT},
            'index_funds': {'tickers': ['IDX']},
            'holdings': {'AAA': {'role': 'core', 'tags': ['chain']}, 'BBB': {'role': 'small_bet', 'tags': ['chain']},
                         'IDX': {'role': 'index'}},
            'linked_exposure': {'chain': {'label_he': 'שרשרת', 'note_above': 0.25}}})
        cls.pos = {'as_of': '2026-11-15', 'source': 'synthetic', 'cash': 100.0, 'rows': [
            {'t': 'AAA', 'price': 175.0, 'value': 2500.0}, {'t': 'BBB', 'price': 185.0, 'value': 600.0},
            {'t': 'GAP', 'price': 10.0, 'value': 400.0}, {'t': 'IDX', 'price': 800.0, 'value': 3400.0}]}
        cls.doc = PH.health(cls.pos, cls.rules, cls.root)

    def row(self, t):
        return next(r for r in self.doc['rows'] if r['ticker'] == t)


class HealthTests(Tree):
    def test_index_share_weights_and_linked_exposure_by_hand(self):
        total = 2500 + 600 + 400 + 3400 + 100 + 3000
        pf = self.doc['portfolio']
        self.assertAlmostEqual(pf['total'], total)
        self.assertAlmostEqual(pf['index_share'], (3400 + 3000) / total)
        self.assertEqual(pf['index_position'], 'inside')                 # 64.0% inside 60-70%
        self.assertAlmostEqual(pf['stock_share'], 3500 / total)
        self.assertEqual(pf['stock_position'], 'inside')
        self.assertEqual(pf['index_funds'], ['IDX'])
        self.assertAlmostEqual(self.row('AAA')['weight'], 2500 / total)
        self.assertAlmostEqual(pf['linked_exposure']['chain']['weight'], 3100 / total)
        self.assertEqual(sorted(pf['linked_exposure']['chain']['tickers']), ['AAA', 'BBB'])
        md = PH.render_he(self.doc)
        self.assertIn('הימור מתואם אחד', md)                             # 31.0% above the drafted 25% line

    def test_caps(self):
        aaa, bbb = self.row('AAA')['results_he'], self.row('BBB')['results_he']
        self.assertTrue(any('מעל התקרה של 20%' in x for x in aaa), aaa)  # 25% of all the money
        self.assertTrue(any('מעל 5% להימור קטן' in x for x in bbb), bbb)  # 6%
        self.assertFalse(any('מעל 15%' in x for x in bbb))
        for x in aaa + bbb:
            for word in ('מכור', 'קנה ', 'תקנה', 'תמכור'):
                self.assertNotIn(word, x)                                 # rule results, never orders

    def test_bands_per_lens_match_a_hand_rerun(self):
        _, ours, cal, b1, b2 = expected(self.p_a, 175.0, '2026-11-15', self.rules['_bands'])
        lens = self.row('AAA')['lenses']
        self.assertEqual((lens['ours']['return_pct'], lens['second']['return_pct']), (ours, cal))
        self.assertEqual((lens['ours']['band'], lens['second']['band']), (b1, b2))
        self.assertEqual(lens['agree'], b1 == b2)
        rp = self.row('AAA')['reprice']
        self.assertTrue(rp['dates_moved'])
        self.assertEqual(rp['as_of_used'], '2026-11-15')
        self.assertFalse(rp['base_year_end_derived'])                    # the fixture has base_year_end

    def test_reprice_at_the_saved_price_and_date_reproduces_the_saved_returns(self):
        pos = {'as_of': '2026-11-04', 'rows': [{'t': 'AAA', 'price': self.p_a['price'], 'value': 1.0}]}
        r = PH.health(pos, self.rules, self.root)['rows'][0]
        saved = LM.run(self.p_a)
        self.assertEqual(r['lenses']['ours']['return_pct'], saved['scenarios']['base']['annual_return_pct'])
        self.assertEqual(r['lenses']['second']['return_pct'], saved['two_lens']['calibrated']['annual_return_pct'])
        self.assertEqual(r['buy_test']['pass'], saved['buy_test']['pass'])
        self.assertEqual(r['buy_test']['max_price_passing'], saved['buy_test']['max_price_passing'])

    def test_second_lens_not_a_second_opinion(self):
        lens = self.row('BBB')['lenses']
        self.assertTrue(lens['second']['not_second_opinion'])
        self.assertIsNone(lens['agree'])
        self.assertEqual(lens['decision_band'], lens['ours']['band'])
        self.assertTrue(any('אינה דעה שנייה' in x for x in self.row('BBB')['results_he']))

    def test_buy_test_legs(self):
        bt = self.row('AAA')['buy_test']
        self.assertEqual(set(bt['legs']), {'base_ours', 'base_calibrated', 'bull', 'bear'})
        for leg, x in bt['legs'].items():
            self.assertEqual(x['pass'], x['return_pct'] >= x['threshold_pct'], leg)
        self.assertEqual(bt['pass'], all(x['pass'] for x in bt['legs'].values()))

    def test_holding_without_tracked_params_is_a_gap(self):
        gap = self.row('GAP')
        self.assertNotIn('lenses', gap)
        self.assertTrue(any('no dated valuation folder' in g for g in gap['gaps']))
        self.assertTrue(any('no role' in g for g in gap['gaps']))
        self.assertTrue(gap['results_he'][0].startswith('חסר'))

    def test_age_next_report_and_conditions(self):
        a = self.row('AAA')
        self.assertEqual(a['valuation']['age_days'], 11)
        self.assertEqual(a['next_report']['earliest'], '2026-11-20')
        self.assertFalse(a['next_report_passed'])
        self.assertEqual((a['conditions']['count'], a['conditions']['untyped']), (2, 2))

    def test_lagging_switch(self):
        lens = {'ours': {'band': 'lagging'}, 'second': {'band': 'lagging', 'not_second_opinion': False}, 'agree': True,
                'decision_band': 'lagging', 'edge_distance_pts': 1.5}
        row = {'role': 'core', 'weight': 0.08, 'lenses': lens, 'buy_test': {}, 'conditions': {'hit': []}, 'gaps': []}
        hold = PH._results_he(row, self.rules)
        self.assertTrue(any('בלי כסף חדש' in x for x in hold), hold)
        self.assertFalse(any('מעל התקרה של 6%' in x for x in hold))
        cap = PH.load_rules(obj={'lagging_core_rule': {'value': 'cap_6pct'}})
        got = PH._results_he(row, cap)
        self.assertTrue(any('מוגבלת ל-6%' in x for x in got) and any('מעל התקרה של 6%' in x for x in got), got)
        self.assertEqual(PH.load_rules(obj={'lagging_core_rule': 'other'})['_problems'][0][:17], 'lagging_core_rule')
        doc = PH.health({'as_of': '2026-11-15', 'rows': []}, cap, self.root)
        self.assertEqual(doc['lagging_core_reading']['value'], 'cap_6pct')
        self.assertIn('lagging_core_rule', PH.load_rules(obj={'lagging_core_rule': {'value': 'cap_6pct', 'draft': PH.DRAFT}})['_drafts'])

    def test_custom_record_reprice(self):
        rec = {'as_of': '2026-11-13', 'price': {'value': 100.0}, 'hurdles': {'years_to_horizon': 5.0, 'horizon_date': '2031-11-13'},
               'scenarios': {'bear': {'annual_return_pct': -5.0}, 'base': {'annual_return_pct': 8.0}, 'bull': {'annual_return_pct': 20.0}},
               'second_lens': {'annual_return_pct': 10.0}}
        same = PH.reprice_custom(rec, 100.0, '2026-11-13')
        self.assertEqual(same['returns'], {'bear': -5.0, 'base': 8.0, 'bull': 20.0, 'base_calibrated': 10.0})
        later = PH.reprice_custom(rec, 110.0, '2027-11-13')
        yrs = 5.0 - 365 / 365.25
        self.assertAlmostEqual(later['returns']['base'], round(((100 * 1.08 ** 5 / 110) ** (1 / yrs) - 1) * 100, 1))

    def test_price_scale_guard_and_private_output(self):
        pos = {'as_of': '2026-11-15', 'rows': [{'t': 'AAA', 'price': self.p_a['price'] / 2, 'value': 1.0}]}
        r = PH.health(pos, self.rules, self.root)['rows'][0]
        self.assertNotIn('lenses', r)
        self.assertTrue(any('share unit' in g for g in r['gaps']))
        with self.assertRaises(ValueError):
            PH.write(self.doc, os.path.join(self.root, 'research', 'x'), self.root)
        paths = PH.write(self.doc, os.path.join(self.root, 'runs', 'portfolio_2026-11-15', 'health_auto'), self.root)
        self.assertTrue(os.path.isfile(paths['markdown']))


class DueTests(Tree):
    def test_due_lists(self):
        handoff(self.root, 'OLDR', '2026-10-01', next_report={'date': '2026-10-28'})       # passed, no newer valuation
        handoff(self.root, 'LATER', '2026-11-01', next_report={'date': 'late January 2027'})
        handoff(self.root, 'NODATE', '2026-11-01', next_report={})
        filings = {'AAA': {'events': [{'form': '6-K', 'date': '2026-11-10', 'kind': 'x'}], 'source': 'fixture'},
                   'BBB': {'events': [{'form': '10-Q', 'date': '2026-11-10', 'kind': 'quarterly report'}], 'source': 'fixture'}}
        doc = PH.due(self.root, '2026-11-15', 30, held=['AAA', 'BBB', 'OLDR'], filings=filings)
        st = {r['ticker']: r['status'] for r in doc['rows']}
        self.assertEqual(st['AAA'], 'upcoming')
        self.assertEqual(st['OLDR'], 'passed_without_update')
        self.assertEqual(st['LATER'], 'later')
        self.assertEqual(st['NODATE'], 'no_date')
        self.assertIn('AAA', doc['upcoming'])
        self.assertEqual(doc['passed_without_update'], ['OLDR'])
        self.assertEqual(sorted(doc['held_stale']), ['BBB', 'OLDR'])        # a 10-Q after the valuation; a passed report
        self.assertEqual(doc['held_6k_to_check'], ['AAA'])                  # a 6-K alone is not called a report
        self.assertIn('OLDR', PH.render_due_he(doc))


class ResearchLatestTests(unittest.TestCase):
    def test_lean_only_and_mixed(self):
        root = tempfile.mkdtemp()
        lean_folder(root, 'LEANONLY', '2026-11-06')
        self.assertIsNone(RS.load('LEANONLY', root))                       # the old answer: none
        got = RS.latest('LEANONLY', root)
        self.assertEqual((got['as_of'], got['kind']), ('2026-11-06', 'lean'))
        self.assertIsNotNone(got['summary']['value_today']['base'])
        heavy = os.path.join(root, 'research', 'companies', 'MIX', '2026-11-01')
        _dump(os.path.join(heavy, 'casefile_summary.json'), {'decision': {}})
        _dump(os.path.join(root, 'research', 'companies', 'MIX', 'latest.json'),
              {'ticker': 'MIX', 'as_of': '2026-11-01', 'folder': os.path.join('research', 'companies', 'MIX', '2026-11-01')})
        self.assertEqual(RS.latest('MIX', root)['kind'], 'engine')         # only the heavy folder: it is in force
        lean_folder(root, 'MIX', '2026-11-13')
        got = RS.latest('mix', root)
        self.assertEqual((got['as_of'], got['kind']), ('2026-11-13', 'lean'))
        self.assertEqual(got['heavy_engine_save']['as_of'], '2026-11-01')
        self.assertTrue(got['heavy_engine_save']['superseded'])
        self.assertEqual(RS.load('MIX', root)['as_of'], '2026-11-01')      # load keeps its meaning (latest.json)
        self.assertIsNone(RS.latest('NONE', root))


class HoldingsWatchP9Tests(unittest.TestCase):
    def test_value_key_shapes(self):
        self.assertEqual(HW.value_key_date('end_2031'), ('ours', '2031-12-31', 2031))
        self.assertEqual(HW.value_key_date('end_fy2031_jan_2032'), ('ours', '2032-01-31', 2031))
        self.assertEqual(HW.value_key_date('june_2032'), ('ours', '2032-06-30', None))
        self.assertEqual(HW.value_key_date('calibrated_june_2032'), ('calibrated', '2032-06-30', None))
        self.assertEqual(HW.value_key_date('calibrated_end_fy2031', {2031: '2032-01-31'}), ('calibrated', '2032-01-31', 2031))
        for k in ('weighted_today_25_50_25', 'depwave_evidenced_end_2031', 'tail_end_2031', 'today_note'):
            self.assertIsNone(HW.value_key_date(k), k)
        d = HW.dated_handoff_values({'end_fy2031_jan_2032': {'base': 300}, 'calibrated_end_fy2031': 320,
                                     'june_2028': {'base': 200}, 'weighted_today_1': 5})
        self.assertEqual([r['date'] for r in d['ours']], ['2028-06-30', '2032-01-31'])
        self.assertEqual(d['calibrated'], [{'date': '2032-01-31', 'value': 320.0}])
        self.assertEqual(d['other_keys'], ['weighted_today_1'])

    def test_record_preferred_with_both_lenses_and_handoff_judgement(self):
        root = tempfile.mkdtemp()
        p = lean_folder(root, 'AAA', '2026-11-04')
        handoff(root, 'AAA', '2026-11-04', price=p['price'])
        pic = HW.saved_picture(root, 'AAA', '2026-11-15')
        self.assertEqual((pic['source'], pic['date']), ('valuation_record', '2026-11-04'))
        self.assertEqual(pic['verdict_he'], 'סינתטי')                       # judgement from the handoff of the same date
        self.assertEqual(len(pic['conditions']) + len(pic['falsifiers']), 2)
        self.assertIsNotNone(pic['second_lens']['last_year'])
        handoff(root, 'AAA', '2026-11-10', price=p['price'])                # a newer handoff wins
        self.assertEqual(HW.saved_picture(root, 'AAA', '2026-11-15')['source'], 'fundamental_picture')

    def _ledger(self):
        led = os.path.join(tempfile.mkdtemp(), 'l.jsonl')
        M.freeze(Ledger(led, clock=Clock('2026-10-19T15:00:00+00:00')), thesis())
        with open(led, 'a') as f:                                           # a lean-ledger row is not a monitor thesis
            f.write(json.dumps({'type': 'thesis', 'entity': 'LEANX', 'made_at': '2026-11-01', 'thesis_id': 'lean:LEANX',
                                'record': {'frozen_by': 'lean_freeze/1', 'ticker': 'LEANX', 'as_of': '2026-11-01'}}) + '\n')
        return led

    def test_foreign_filer_cannot_tell_and_old_engine_label(self):
        led = self._ledger()
        self.assertNotIn('LEANX', HW.latest_theses(led))

        def fb(t, d, th):
            b = later_fb(as_of='2026-12-01', fy26={})
            b['company'] = dict(b['company'], quarterly_xbrl=False)
            return b
        pos = {'as_of': '2026-12-01', 'rows': [{'t': 'ADBE', 'price': 245.0, 'weight': 1.0}]}
        r = HW.update(pos, led, '2026-12-01', fb)['rows'][0]
        self.assertEqual(r['status'], 'cannot_tell')
        self.assertIn('6-K', r['line_he'])
        self.assertEqual(r['filing_check']['basis'], 'old_engine_ledger_thesis')
        r2 = HW.update(pos, led, '2026-12-01', lambda *a: later_fb(as_of='2026-12-01', fy26={}))['rows'][0]
        self.assertEqual(r2['status'], 'no_new_report')
        md = HW.render_he(HW.update(pos, led, '2026-12-01', lambda *a: later_fb(as_of='2026-12-01', fy26={})))
        self.assertIn('המנוע הישן', md)
        self.assertEqual(HW.xbrl_forms({'facts': {'ifrs-full': {'Revenue': {'units': {'TWD': [{'form': '20-F'}, {'form': '6-K'}]}}}}}),
                         {'20-F', '6-K'})


if __name__ == '__main__':
    unittest.main()
