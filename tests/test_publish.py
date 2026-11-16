"""scripts/publish_valuation.py on a temp research tree: the round trip passes --check, a hand edit fails it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import publish_valuation as pub  # noqa: E402
from fundamental_engine import valuation_record as vr  # noqa: E402

FIXTURE = os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_dated.json')
SCRIPT = os.path.join(ROOT, 'scripts', 'publish_valuation.py')
HEADER = ('# ספר התזות: אינדקס\n\n| חברה | עודכן | מחיר אז | שווי היום (דובי / בסיסי / שורי) | בעוד שנה (בסיסי) | בעוד 5 שנים (בסיסי) | '
          'לפי המכפיל ההיסטורי | מחיר כניסה עם מרווח ביטחון | מסקנה | בדיקה הבאה |\n|---|---|---|---|---|---|---|---|---|---|\n')
OLD_ROWS = ('| [ANET](companies/ANET/thesis_he.md) | 1.10.2026 | 150.00$ | 90$ / **140$** / 200$ | 150$ | 230$ | כ-160$ | כ-120$ | החזקה | דוח |\n'
            '| [OTHR](companies/OTHR/thesis_he.md) | 1.10.2026 | 10.00$ | 5$ / **12$** / 20$ | 13$ | 19$ | כ-11$ | כ-9$ | החזקה | דוח |\n'
            '| [ANET](companies/ANET/thesis_he.md) | 20.10.2026 | 170.00$ | 95$ / **150$** / 210$ | 160$ | 240$ | כ-160$ | כ-125$ | החזקה | דוח |\n')
THESIS = ('# Arista (ANET): תזה והערכת שווי\n\n## שורה תחתונה\nפרוזה שנכתבה ביד ונשארת כמו שהיא.\n\n## שווי הוגן\n\n'
          + vr.BLOCK_START + '\nישן\n' + vr.BLOCK_END + '\n\n## התזה\n- נקודה שנכתבה ביד: 123$.\n')
INPUTS = {'price_time_utc': '2026-11-04 15:00 UTC', 'verdict': {'code': 'hold', 'text_he': 'החזקה: התשואה קרובה למדד'},
          'scenarios': {'bear': 'האטה', 'base': 'לפי התחזית', 'bull': 'מעל התחזית'},
          'conditions': [{'kind': 'add', 'metric': 'price', 'op': '<=', 'threshold': 150.0, 'text_he': 'מחיר מתחת ל-150$'}],
          'falsifiers': [{'kind': 'exit', 'metric': 'revenue_growth', 'op': '<', 'threshold': 0.1, 'text_he': 'צמיחה מתחת ל-10%'}],
          'next_report': {'date': '2027-02-16', 'confirmed': True, 'check': 'תחזית 2027'}, 'last_fresh_pull': {'date': '2026-11-04'},
          'notes_he': ['המספר לשנה הוא הרועש ביותר']}


class Tree:
    """A temp research tree with one lean valuation (ANET, 2026-11-04) ready to publish."""

    def __init__(self, thesis=THESIS):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'research')
        self.folder = os.path.join(self.root, 'companies', 'ANET', '2026-11-04')
        os.makedirs(self.folder)
        shutil.copy(FIXTURE, os.path.join(self.folder, 'lean_params.json'))
        self.write('THESES_he.md', HEADER + OLD_ROWS)
        self.write('companies/ANET/thesis_he.md', thesis)
        self.write('inputs.json', json.dumps(INPUTS, ensure_ascii=False))
        self.said = []

    def path(self, rel):
        return os.path.join(self.root, *rel.split('/'))

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), 'w', encoding='utf-8') as f:
            f.write(text)

    def read(self, rel):
        with open(self.path(rel), encoding='utf-8') as f:
            return f.read()

    def edit_json(self, rel, change):
        obj = json.loads(self.read(rel))
        change(obj)
        self.write(rel, vr.dumps(obj))

    def publish(self, inputs=True):
        return pub.publish(os.path.join(self.folder, 'lean_params.json'), self.path('inputs.json') if inputs else None, out=self.said.append)

    def check(self):
        return pub.check(self.root, out=lambda line: None)

    def kinds(self):
        return sorted({k for k, t, text in self.check() if t == 'ANET'})

    def close(self):
        shutil.rmtree(self.tmp)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)

    def test_round_trip_passes_the_check(self):
        res = self.t.publish()
        rel = sorted(os.path.relpath(p, self.t.root).replace(os.sep, '/') for p in res['written'])
        self.assertEqual(rel, ['THESES_he.md', 'companies/ANET/2026-11-04/lean_result.json', 'companies/ANET/2026-11-04/valuation.json',
                               'companies/ANET/thesis_he.md', 'technical/ANET/2026-11-04/fundamental.json'])
        problems = [p for p in self.t.check() if p[1] == 'ANET']
        self.assertEqual(problems, [])
        rec = json.loads(self.t.read('companies/ANET/2026-11-04/valuation.json'))
        self.assertEqual(vr.validate(rec), [])
        self.assertEqual(rec['falsifiers'][0]['threshold'], 0.1)
        self.assertEqual(rec['next_report']['date'], '2027-02-16')
        h = json.loads(self.t.read('technical/ANET/2026-11-04/fundamental.json'))
        self.assertTrue(all(k in h for k in vr.V1_KEYS))
        self.assertEqual((h['schema_version'], h['price'], h['values']['today']['base']),
                         (2, rec['price']['value'], rec['scenarios']['base']['value_today']))
        result = json.loads(self.t.read('companies/ANET/2026-11-04/lean_result.json'))
        self.assertTrue(all(k in result for k in ('dated_values', 'method_gap', 'price_ladder', 'buy_test')))

    def test_one_index_row_and_prose_untouched(self):
        res = self.t.publish()
        rows = vr.index_rows(self.t.read('THESES_he.md'))
        self.assertEqual((len(rows['ANET']), len(rows['OTHR'])), (1, 1))
        self.assertEqual(len(res['removed_index_rows']), 2)
        self.assertLess(rows['ANET'][0][0], rows['OTHR'][0][0])          # at the place of the first old row
        thesis = self.t.read('companies/ANET/thesis_he.md')
        before, after = THESIS.split(vr.BLOCK_START)[0], THESIS.split(vr.BLOCK_END)[1]
        self.assertTrue(thesis.startswith(before) and thesis.endswith(after))
        self.assertNotIn('ישן', thesis)
        self.assertIn(vr.LABELS_HE['buy_test'], thesis)
        self.assertIn(vr.LABELS_HE['margin_of_safety'], thesis)
        self.t.publish()                                                  # publishing again changes nothing
        self.assertEqual(self.t.read('companies/ANET/thesis_he.md'), thesis)
        self.assertEqual(len(vr.index_rows(self.t.read('THESES_he.md'))['ANET']), 1)

    def test_a_hand_edited_number_fails_the_check(self):
        self.t.publish()
        clean = {rel: self.t.read(rel) for rel in ('technical/ANET/2026-11-04/fundamental.json', 'THESES_he.md', 'companies/ANET/thesis_he.md',
                                                   'companies/ANET/2026-11-04/valuation.json')}
        rec = json.loads(clean['companies/ANET/2026-11-04/valuation.json'])
        base = vr._money(rec['scenarios']['base']['value_today'])
        edits = (
            ('handoff price', 'technical/ANET/2026-11-04/fundamental.json', lambda s: s.replace('"price": 182.95', '"price": 183.95'), 'mismatch'),
            ('handoff base', 'technical/ANET/2026-11-04/fundamental.json',
             lambda s: vr.dumps(_set(json.loads(s), ('values', 'today', 'base'), rec['scenarios']['base']['value_today'] + 3)), 'mismatch'),
            ('handoff entry', 'technical/ANET/2026-11-04/fundamental.json',
             lambda s: vr.dumps(_set(json.loads(s), ('margin_of_safety_price',), rec['hurdles']['margin_of_safety_price'] + 5)), 'entry'),
            ('handoff text', 'technical/ANET/2026-11-04/fundamental.json',
             lambda s: vr.dumps(_set(json.loads(s), ('values', 'today', 'bear'), '0-20 (model 0.01)')), 'text_in_number'),
            ('index base', 'THESES_he.md', lambda s: s.replace(f'**{base}**', '**999$**'), 'mismatch'),
            ('index price', 'THESES_he.md', lambda s: s.replace('182.95$', '181.95$'), 'mismatch'),
            ('thesis block', 'companies/ANET/thesis_he.md', lambda s: s.replace(f'**{base}**', '**998$**'), 'mismatch'),
            ('handoff v2 rung', 'technical/ANET/2026-11-04/fundamental.json',
             lambda s: vr.dumps(_set(json.loads(s), ('v2', 'sell_line_price'), 1.0)), 'mismatch'),
            ('record base', 'companies/ANET/2026-11-04/valuation.json',
             lambda s: vr.dumps(_set(json.loads(s), ('scenarios', 'base', 'value_today'), 1.0)), 'mismatch'),
            ('record text', 'companies/ANET/2026-11-04/valuation.json',
             lambda s: vr.dumps(_set(json.loads(s), ('hurdles', 'index_parity_price'), 'about 200')), 'record'),
            ('record entry', 'companies/ANET/2026-11-04/valuation.json',
             lambda s: vr.dumps(_set(json.loads(s), ('hurdles', 'entry_price', 'value'), 7.77)), 'entry'),
        )
        for name, rel, change, kind in edits:
            with self.subTest(edit=name):
                new = change(clean[rel])
                self.assertNotEqual(new, clean[rel], 'the edit did not change the file')
                self.t.write(rel, new)
                self.assertIn(kind, self.t.kinds())
                self.t.write(rel, clean[rel])
                self.assertEqual(self.t.kinds(), [])

    def test_a_changed_saved_result_is_reported_and_not_overwritten(self):
        self.t.publish()
        self.t.edit_json('companies/ANET/2026-11-04/lean_result.json', lambda r: r['scenarios']['base'].update(value_today=1.0))
        self.assertIn('rerun', self.t.kinds())
        with self.assertRaises(pub.PublishError):
            self.t.publish()

    def test_pairing_and_duplicate_rows(self):
        self.t.publish()
        self.t.write('companies/OTHR/thesis_he.md', '# Other\n')
        self.t.write('technical/LONE/2026-11-01/fundamental.json', '{"ticker": "LONE", "as_of": "2026-11-01", "price": 5}')
        self.t.write('THESES_he.md', self.t.read('THESES_he.md') + OLD_ROWS.splitlines()[0] + '\n')
        got = {(k, t) for k, t, _ in self.t.check()}
        self.assertTrue({('pairing', 'OTHR'), ('pairing', 'LONE'), ('index', 'ANET')} <= got, got)

    def test_thesis_without_markers_is_left_alone_and_the_block_is_printed(self):
        t = Tree(thesis='# ANET\n\n| היום | 1$ | **2$** | 3$ |\n')
        self.addCleanup(t.close)
        res = t.publish()
        self.assertTrue(res['block_printed'])
        self.assertEqual(t.read('companies/ANET/thesis_he.md'), '# ANET\n\n| היום | 1$ | **2$** | 3$ |\n')
        self.assertTrue(any(vr.BLOCK_START in s for s in t.said))
        self.assertIn('mismatch', t.kinds())                             # the hand-written table says 2$, the model does not

    def test_no_thesis_link_to_a_missing_thesis(self):
        os.remove(self.t.path('companies/ANET/thesis_he.md'))
        self.t.publish()
        self.assertIsNone(json.loads(self.t.read('technical/ANET/2026-11-04/fundamental.json'))['thesis_link'])
        self.assertIsNone(json.loads(self.t.read('companies/ANET/2026-11-04/valuation.json'))['thesis_link'])
        self.assertIn('pairing', self.t.kinds())

    def test_without_inputs_the_saved_handoff_texts_are_carried(self):
        self.t.write('technical/ANET/2026-11-04/fundamental.json', vr.dumps({
            'ticker': 'ANET', 'as_of': '2026-11-04', 'price': 182.95, 'verdict_he': 'החזקה', 'drawing_key': [1, 2],
            'scenarios': {'base': {'prob': 0.5, 'he': 'לפי התחזית'}},
            'conditions': {'add': ['מחיר נמוך'], 'proposed_exit': ['צמיחה נעצרת'], 'would_raise_value': ['צבר גדול מהצפוי'], 'watch': ['מלאי']},
            'next_report': {'date': 'late January 2027', 'confirmed': False}}))
        self.t.publish(inputs=False)
        h = json.loads(self.t.read('technical/ANET/2026-11-04/fundamental.json'))
        self.assertEqual((h['verdict_he'], h['drawing_key'], h['conditions']['proposed_exit']), ('החזקה', [1, 2], ['צמיחה נעצרת']))
        # a text under a key of its own stays there only: the v1 lists keep their meaning
        self.assertEqual((h['conditions']['add'], h['conditions']['would_raise_value'], h['conditions']['watch']),
                         (['מחיר נמוך'], ['צבר גדול מהצפוי'], ['מלאי']))
        self.assertNotIn('too_low', h['conditions'])
        rec = json.loads(self.t.read('companies/ANET/2026-11-04/valuation.json'))
        self.assertIn('too_low', [c['kind'] for c in rec['conditions']])   # the record still holds it, typed by its old key
        self.assertEqual(h['v2']['next_report']['window'], {'from': '2027-01-01', 'to': '2027-01-31'})
        self.assertEqual(h['next_report']['date'], 'late January 2027')    # the v1 key keeps its text
        self.assertEqual(self.t.kinds(), [])

    def test_a_republish_without_inputs_keeps_the_judgement_of_the_saved_record(self):
        self.t.publish()
        first = json.loads(self.t.read('companies/ANET/2026-11-04/valuation.json'))
        self.t.said.clear()
        self.t.publish(inputs=False)                     # e.g. after a code change: no --thesis-inputs
        again = json.loads(self.t.read('companies/ANET/2026-11-04/valuation.json'))
        for k in ('verdict', 'conditions', 'falsifiers', 'next_report', 'last_fresh_pull', 'notes_he', 'historical_multiple', 'price'):
            self.assertEqual(again[k], first[k], k)
        self.assertEqual({k: v['condition_he'] for k, v in again['scenarios'].items()},
                         {k: v['condition_he'] for k, v in first['scenarios'].items()})
        self.assertTrue(any('valuation.json' in x for x in self.t.said))
        self.assertEqual(self.t.kinds(), [])

    def test_a_calibrated_rule_sidecar_is_applied_to_the_record_only(self):
        self.t.publish()
        saved = json.loads(self.t.read('companies/ANET/2026-11-04/lean_result.json'))
        self.t.write('companies/ANET/2026-11-04/calibrated_rule.json', json.dumps({'pe_rule': 'net_of_cash', 'why': 'test'}))
        self.t.publish(inputs=False)
        self.assertEqual(json.loads(self.t.read('companies/ANET/2026-11-04/lean_result.json'))['two_lens'], saved['two_lens'])
        rec = json.loads(self.t.read('companies/ANET/2026-11-04/valuation.json'))
        self.assertEqual(rec['second_lens']['pe_rule'], 'net_of_cash')
        self.assertIn('calibrated_rule', rec['model'])
        self.assertLess(rec['second_lens']['value_today'], saved['two_lens']['calibrated']['value_today'])  # ANET holds net cash
        self.assertLessEqual(rec['hurdles']['margin_of_safety_price'], rec['hurdles']['buy_test_price'])
        self.assertEqual(self.t.kinds(), [])

    def test_bad_params_and_variants_are_refused(self):
        p = json.loads(self.t.read('companies/ANET/2026-11-04/lean_params.json'))
        p['probabilities']['base'] = 0.9
        self.t.write('companies/ANET/2026-11-04/lean_params.json', json.dumps(p))
        with self.assertRaises(pub.PublishError):
            self.t.publish()
        self.assertFalse(os.path.exists(self.t.path('companies/ANET/2026-11-04/valuation.json')))
        shutil.copy(FIXTURE, self.t.path('companies/ANET/2026-11-04/lean_params_pre_review.json'))
        with self.assertRaises(pub.PublishError):
            pub.publish(self.t.path('companies/ANET/2026-11-04/lean_params_pre_review.json'), out=self.t.said.append)

    def test_command_line_exit_codes(self):
        self.t.publish()
        self.t.write('THESES_he.md', '\n'.join(ln for ln in self.t.read('THESES_he.md').splitlines() if 'OTHR' not in ln) + '\n')
        run = subprocess.run([sys.executable, SCRIPT, '--check', self.t.root], capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout), (0, ''), run.stderr)
        self.t.write('THESES_he.md', self.t.read('THESES_he.md').replace('182.95$', '181.95$'))
        run = subprocess.run([sys.executable, SCRIPT, '--check', self.t.root], capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(len(run.stdout.strip().splitlines()), 1)
        self.assertIn('mismatch ANET', run.stdout)


def _set(obj, path, value):
    node = obj
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return obj


if __name__ == '__main__':
    unittest.main()
