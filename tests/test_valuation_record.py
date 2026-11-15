"""The valuation record: schema, the lean builder, the adapters of the custom models in Git and the resolver."""
import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(ROOT, 'research')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import lean_model  # noqa: E402
from fundamental_engine import valuation_record as vr  # noqa: E402

FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_dated.json')


def fixture_record(inputs=None):
    p = vr.load(FIXTURE)
    res = json.loads(json.dumps(lean_model.run(p)))
    files = {'params': vr.file_ref(FIXTURE, ROOT), 'code': vr.file_ref(os.path.join(ROOT, 'scripts', 'lean_model.py'), ROOT),
             'result': {'path': 'in memory (test)', 'sha256': hashlib.sha256(json.dumps(res, sort_keys=True).encode()).hexdigest()}}
    return p, vr.build_from_lean(p, res, inputs, files)


class RecordTest(unittest.TestCase):
    def test_lean_record_is_valid_and_holds_the_model_numbers(self):
        p, rec = fixture_record()
        self.assertEqual(vr.validate(rec), [])
        res = json.loads(json.dumps(lean_model.run(p)))
        self.assertEqual(rec['price']['value'], p['price'])
        self.assertEqual(rec['scenarios']['base']['value_today'], res['scenarios']['base']['value_today'])
        self.assertEqual(rec['hurdles']['margin_of_safety_price'], res['price_ladder']['rungs']['margin_of_safety_13']['ours'])
        self.assertEqual(rec['hurdles']['index_parity_price'], res['index_parity_price'])
        self.assertEqual(rec['hurdles']['entry_price'], {'value': rec['hurdles']['margin_of_safety_price'],
                                                         'rung': 'margin_of_safety_13', 'note': None})
        years = rec['base_path']['years']
        self.assertEqual(len(years), 6)
        self.assertTrue(all(vr._iso(y['fiscal_year_end']) for y in years.values()))
        self.assertEqual([r['date'] for r in rec['scenarios']['base']['dated_values']],
                         [res['dated_values']['year_ends'][y]['date'] for y in sorted(res['dated_values']['year_ends'])])
        self.assertEqual(set(rec['scenarios']['base']['at']), {'one_year', 'two_year', 'five_year'})

    def test_buy_test_price_is_the_rerun_price_when_the_model_warns(self):
        p = vr.load(FIXTURE)
        res = json.loads(json.dumps(lean_model.run(p)))
        res['buy_test']['rerun'] = dict(res['buy_test'].get('rerun') or {}, max_price_passing=1.23, binding_leg='base_calibrated',
                                        lower_than_stated=True)
        rec = vr.build_from_lean(p, res)
        self.assertEqual(rec['hurdles']['buy_test_price'], 1.23)
        self.assertTrue(rec['hurdles']['buy_test']['rerun_used'])
        self.assertEqual(rec['hurdles']['buy_test']['stated_price'], res['buy_test']['max_price_passing'])

    def test_text_in_a_numeric_field_is_rejected(self):
        _, rec = fixture_record()
        for path, value in ((('scenarios', 'bear', 'value_today'), '0-20 (model 0.01)'), (('hurdles', 'margin_of_safety_price'), 'about 95'),
                            (('scenarios', 'bear', 'annual_return_pct'), 'about -100'), (('weighted', 'value_today'), '107'),
                            (('hurdles', 'rungs', 0, 'price'), 'about 95'), (('hurdles', 'entry_price', 'value'), '95 or less'),
                            (('second_lens', 'dated_values', 0, 'value'), '~130'), (('share_basis', 'model_shares_m'), '2,050')):
            bad = copy.deepcopy(rec)
            node = bad
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = value
            with self.subTest(path=path):
                self.assertTrue(any('number' in m or 'numeric' in m for m in vr.validate(bad)), vr.validate(bad))

    def test_entry_price_must_be_a_ladder_rung(self):
        _, rec = fixture_record()
        rec['hurdles']['entry_price']['value'] += 1.0
        self.assertTrue(any('not a rung' in m for m in vr.validate(rec)))
        _, rec = fixture_record({'entry_price': {'rung': 'buy_test'}})
        self.assertEqual(rec['hurdles']['entry_price']['value'], rec['hurdles']['base_12_price'])
        self.assertEqual(vr.validate(rec), [])

    def test_typed_conditions_and_falsifiers(self):
        inputs = {'conditions': [{'id': 'add1', 'kind': 'add', 'metric': 'price', 'op': '<=', 'threshold': 99.14, 'text_he': 'מחיר מתחת ל-99'}],
                  'falsifiers': [{'kind': 'exit', 'metric': 'bookings_growth', 'op': '<', 'threshold': 0.10, 'by_date': '2027-02-28',
                                  'text_he': 'צמיחת ההזמנות מתחת ל-10%'}],
                  'verdict': {'code': 'hold', 'text_he': 'החזקה'}, 'next_report': {'date': '~2027-02-04', 'confirmed': False, 'check': 'הזמנות'},
                  'last_fresh_pull': {'date': '2026-11-13'}}
        _, rec = fixture_record(inputs)
        self.assertEqual(vr.validate(rec), [])
        self.assertEqual(rec['falsifiers'][0]['id'], 'f1')
        self.assertEqual(rec['falsifiers'][0]['state'], 'open')
        self.assertEqual(rec['next_report']['date'], None)
        self.assertEqual(rec['next_report']['window'], {'from': '2027-02-01', 'to': '2027-02-07'})
        for change, word in (({'kind': 'maybe'}, 'kind'), ({'threshold': 'about 10%'}, 'threshold'), ({'by_date': 'February'}, 'by_date'),
                             ({'state': 'done'}, 'state'), ({'text_he': ''}, 'text_he')):
            bad = copy.deepcopy(rec)
            bad['falsifiers'][0].update(change)
            with self.subTest(change=change):
                self.assertTrue(any(word in m for m in vr.validate(bad)), vr.validate(bad))
        bad = copy.deepcopy(rec)
        bad['next_report']['date'] = 'early February 2027'
        self.assertTrue(any('next_report/date' in m for m in vr.validate(bad)))
        bad = copy.deepcopy(rec)
        bad['scenarios']['base']['probability'] = 0.9
        self.assertTrue(any('sum to' in m for m in vr.validate(bad)))

    def test_historical_multiple_is_computed_by_the_locked_rule(self):
        hm = {'company_type': 'stable_profitable', 'next_year_figure': 4.0, 'median': 20.0, 'p25': 18.0, 'p75': 25.0, 'months': 60,
              'source': 'fixture'}
        _, rec = fixture_record({'historical_multiple': hm})
        self.assertEqual((rec['historical_multiple']['value'], rec['historical_multiple']['low'], rec['historical_multiple']['high']),
                         (80.0, 72.0, 100.0))
        self.assertTrue(rec['historical_multiple']['computed'])
        _, rec = fixture_record()
        self.assertIsNone(rec['historical_multiple']['value'])
        self.assertTrue(rec['historical_multiple']['note'])

    def test_handoff_keeps_every_v1_key_and_what_the_previous_file_held(self):
        _, rec = fixture_record({'verdict': {'text_he': 'החזקה'}, 'scenarios': {'base': 'צמיחה לפי התחזית'}})
        previous = {'ticker': rec['ticker'], 'sell_price': {'pre': 907}, 'values': {'tail_end_2031': 38.84, 'today': {'base': 1}},
                    'annual_return_pct': {'tail': -10.7}}
        h = vr.project_handoff(rec, previous)
        self.assertTrue(all(k in h for k in vr.V1_KEYS))
        self.assertEqual(h['schema_version'], 2)
        self.assertEqual(h['price'], rec['price']['value'])
        self.assertEqual(h['values']['today']['base'], rec['scenarios']['base']['value_today'])
        self.assertEqual(h['buy_test_price'], rec['hurdles']['buy_test']['stated_price'])
        self.assertEqual(set(h) - set(vr.V1_KEYS), {'schema_version', 'v2', 'sell_price', 'carried_from_previous'})
        self.assertEqual(h['v2']['buy_test']['price_all_legs'], rec['hurdles']['buy_test_price'])
        self.assertEqual(h['v2']['entry_price']['value'], rec['hurdles']['margin_of_safety_price'])
        clash = vr.project_handoff(rec, {'price_ladder': {'old': 1}, 'buy_test': 'old shape', 'buy_test_price': {'a': 176}})
        self.assertEqual((clash['price_ladder'], clash['buy_test'], clash['buy_test_price_carried']), ({'old': 1}, 'old shape', {'a': 176}))
        self.assertEqual(clash['buy_test_price'], rec['hurdles']['buy_test']['stated_price'])
        self.assertEqual(h['margin_of_safety_price'], rec['hurdles']['margin_of_safety_price'])
        self.assertEqual(h['scenarios']['base'], {'prob': rec['scenarios']['base']['probability'], 'he': 'צמיחה לפי התחזית'})
        self.assertEqual(h['sell_price'], {'pre': 907})
        self.assertEqual(h['values']['tail_end_2031'], 38.84)
        self.assertEqual(h['annual_return_pct']['tail'], -10.7)
        self.assertEqual(h['carried_from_previous'], ['annual_return_pct.tail', 'sell_price', 'values.tail_end_2031'])
        self.assertEqual(vr.check_handoff_numbers(h), [])

    def test_inputs_from_a_v1_handoff(self):
        h = vr.load(os.path.join(RESEARCH, 'technical', 'UBER', '2026-11-13', 'fundamental.json'))
        inputs = vr.inputs_from_handoff(h)
        self.assertEqual(inputs['verdict']['text_he'], h['verdict_he'])
        self.assertEqual(len(inputs['conditions']), len(h['conditions']['add']) + len(h['conditions']['proposed_trim']))
        self.assertEqual({c['kind'] for c in inputs['falsifiers']}, {'exit'})


class AdaptersTest(unittest.TestCase):
    """The custom models kept in Git, read from the tracked files (nothing is written)."""
    CASES = (('BNY', '2026-11-11', 'bank', 157.8, 152.7), ('SPCX', '2026-11-13', 'segment', 70.06, 140.66),
             ('META', '2026-11-13', 'three_lane', 513.2, 634.68), ('NU', '2026-11-13', 'lean_remapped', 13.38, 12.08))

    def test_adapters_give_valid_records(self):
        for t, d, kind, base, price in self.CASES:
            with self.subTest(ticker=t):
                rec, _ = vr.record_for_folder(os.path.join(RESEARCH, 'companies', t, d), RESEARCH, run=lean_model.run)
                self.assertEqual(vr.validate(rec), [])
                self.assertEqual((rec['model']['kind'], rec['scenarios']['base']['value_today'], rec['price']['value']), (kind, base, price))
                self.assertEqual(rec['model']['result']['sha256'], vr.sha256_file(os.path.join(ROOT, rec['model']['result']['path'])))
                self.assertTrue(all(vr._iso(r['date']) for r in rec['scenarios']['base']['dated_values']))
                h = vr.project_handoff(rec)
                self.assertTrue(all(k in h for k in vr.V1_KEYS))

    def test_a_missing_rung_is_null_with_a_note(self):
        rec, _ = vr.record_for_folder(os.path.join(RESEARCH, 'companies', 'BNY', '2026-11-11'), RESEARCH)
        self.assertIsNone(rec['hurdles']['margin_of_safety_price'])
        self.assertIn('null', rec['hurdles']['note'])
        self.assertEqual(rec['hurdles']['entry_price']['value'], 131.58)

    def test_a_wiped_scenario_is_zero_with_no_return(self):
        rec, _ = vr.record_for_folder(os.path.join(RESEARCH, 'companies', 'NBIS', '2026-11-13'), RESEARCH, run=lean_model.run)
        bear = rec['scenarios']['bear']
        self.assertEqual((bear['wiped'], bear['value_today'], bear['annual_return_pct']), (True, 0.0, None))
        self.assertIsNone(rec['hurdles']['buy_test_price'])
        self.assertEqual(vr.validate(rec), [])


class ResolverTest(unittest.TestCase):
    def test_latest_valuation_on_the_tracked_tree(self):
        lv = vr.latest_valuation(RESEARCH, 'UBER', lean_model.run)
        self.assertEqual((lv['as_of'], lv['kind'], lv['older_folders']), ('2026-11-13', 'lean', ['2026-11-06']))
        self.assertEqual(lv['record']['scenarios']['base']['value_today'], 114.14)
        lv = vr.latest_valuation(RESEARCH, 'ASTS')
        self.assertEqual((lv['kind'], lv['record']), ('engine', None))
        self.assertIsNone(vr.latest_valuation(RESEARCH, 'NOSUCH'))

    def test_a_saved_record_is_returned_and_the_newest_folder_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            for d in ('2026-01-05', '2026-03-01'):
                os.makedirs(os.path.join(tmp, 'companies', 'TT', d))
            with open(os.path.join(tmp, 'companies', 'TT', '2026-01-05', vr.RECORD_NAME), 'w') as f:
                f.write('{"schema": "old"}')
            lv = vr.latest_valuation(tmp, 'TT')
            self.assertEqual((lv['as_of'], lv['kind'], lv['record']), ('2026-03-01', 'gather_only', None))
            with open(os.path.join(tmp, 'companies', 'TT', '2026-03-01', vr.RECORD_NAME), 'w') as f:
                f.write('{"schema": "x"}')
            lv = vr.latest_valuation(tmp, 'TT')
            self.assertEqual((lv['record'], lv['record_source']), ({'schema': 'x'}, vr.RECORD_NAME))


if __name__ == '__main__':
    unittest.main()
