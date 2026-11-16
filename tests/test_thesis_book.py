"""The thesis book in research/ is in one consistent state (P6, 15.11.2026).

- `scripts/publish_valuation.py --check` over the repository's research/ finds no problem: every thesis has a handoff
  and one index row, every newest valuation has its valuation.json, and the price, base value and entry prices agree
  between the params, the record, the thesis numbers block, the index row and the handoff;
- every thesis carries the generated numbers block and a history section;
- the record of a valuation with weighted worlds (NVDA 12.11) is the weighted value, and its handoff keeps the v1
  shape of the per-world entry prices (a dict per world) with the single weighted number under v2;
- small projection fixes: a stale note that explained a null is replaced once the number exists, a wiped scenario
  reads 'equity wiped' in the index row, and named variants are listed in the numbers block.
"""
import copy
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESEARCH = os.path.join(REPO, 'research')
sys.path.insert(0, os.path.join(REPO, 'scripts'))
sys.path.insert(0, REPO)
import lean_model  # noqa: E402
import publish_valuation  # noqa: E402
from fundamental_engine import valuation_record as vr  # noqa: E402


def _record(ticker, as_of):
    rec, _ = vr.record_for_folder(os.path.join(RESEARCH, 'companies', ticker, as_of), RESEARCH, run=lean_model.run)
    return rec


class TestThesisBook(unittest.TestCase):
    def test_publish_check_passes_on_the_research_tree(self):
        lines = []
        problems = publish_valuation.check(RESEARCH, out=lines.append)
        self.assertEqual(problems, [], 'publish --check found problems:\n' + '\n'.join(lines))

    def test_every_thesis_has_the_numbers_block_and_a_history(self):
        comp = os.path.join(RESEARCH, 'companies')
        missing = []
        for t in sorted(os.listdir(comp)):
            tp = os.path.join(comp, t, 'thesis_he.md')
            if not os.path.isfile(tp):
                continue
            with open(tp, encoding='utf-8') as f:
                text = f.read()
            if not vr.BLOCK_RE.search(text):
                missing.append(f'{t}: no numbers block')
            if '\n## היסטוריה' not in text:
                missing.append(f'{t}: no history section')
        self.assertEqual(missing, [])

    def test_every_index_row_is_one_per_company_with_a_thesis(self):
        with open(os.path.join(RESEARCH, vr.INDEX), encoding='utf-8') as f:
            rows = vr.index_rows(f.read())
        self.assertTrue(rows)
        for t, mine in rows.items():
            self.assertEqual(len(mine), 1, t)
            self.assertTrue(os.path.isfile(os.path.join(RESEARCH, 'companies', t, 'thesis_he.md')), t)


class TestWeightedWorlds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = _record('NVDA', '2026-11-12')

    def test_the_record_is_the_weighted_value_of_the_worlds(self):
        rec = self.rec
        self.assertEqual(vr.validate(rec), [])
        self.assertEqual(rec['price']['value'], 161.5)
        self.assertEqual(rec['scenarios']['base']['value_today'], 169.02)     # 0.45 x 182.39 + 0.35 x 162.89 + 0.20 x 149.67
        self.assertEqual(rec['hurdles']['buy_test_price'], 163.15)
        self.assertEqual(rec['hurdles']['margin_of_safety_price'], 155.78)
        worlds = rec['worlds']['worlds']
        self.assertEqual([w['name'] for w in worlds], ['orderly_succession', 'messy_succession', 'export_finding'])
        self.assertAlmostEqual(sum(w['weight'] for w in worlds), 1.0)
        self.assertEqual([w['buy_test_price'] for w in worlds], [176.38, 157.05, 144.03])
        self.assertIsNotNone(rec['model'].get('worlds'))
        weights = {v['name']: v['weight'] for v in rec['variants']}
        self.assertEqual(weights['A_huang_stays'], 0.0)                       # the superseded four-state world
        self.assertEqual(weights['B2_messy_succession'], 0.35)

    def test_the_handoff_keeps_the_v1_dict_per_world_and_the_weighted_number_under_v2(self):
        prev = {'buy_test_price': {'orderly_succession': 176}, 'margin_of_safety_price': {'orderly_succession': 168},
                'annual_return_pct': {'orderly': {'base': 14.8}, 'at_price': 155.39}}
        h = vr.project_handoff(self.rec, prev)
        self.assertEqual(h['buy_test_price'], {'orderly_succession': 176.38, 'messy_succession': 157.05, 'export_finding': 144.03})
        self.assertIsInstance(h['margin_of_safety_price'], dict)
        self.assertNotIn('buy_test_price_carried', h)
        self.assertEqual(h['v2']['worlds']['weighted']['buy_test_price'], 163.15)
        self.assertEqual(h['v2']['buy_test']['price_all_legs'], 163.15)
        self.assertEqual(h['annual_return_pct']['at_price'], 161.5)
        self.assertEqual(h['annual_return_pct']['orderly']['base'], 13.9)        # the old key, at the record's price
        self.assertEqual(h['values']['today']['base'], 169.02)

    def test_the_numbers_block_lists_each_world(self):
        block = vr.numbers_block(self.rec)
        self.assertIn('עולמות', block)
        self.assertIn('176.38$', block)


class TestProjectionFixes(unittest.TestCase):
    def test_a_stale_null_note_is_replaced_when_the_number_exists(self):
        rec = _record('GOOGL', '2026-11-04')
        prev = {'margin_of_safety_price': None, 'margin_of_safety_note': 'null: the saved params carry buy_return 10.27%',
                'thesis_link': None, 'thesis_note': 'no thesis_he.md yet'}
        h = vr.project_handoff(rec, prev)
        self.assertIsNotNone(h['margin_of_safety_price'])
        self.assertNotIn('null:', h['margin_of_safety_note'])
        self.assertNotIn('no thesis', h['thesis_note'])
        self.assertNotIn('margin_of_safety_note', h.get('carried_from_previous') or [])
        # a note beside a number that was already there is carried as it is
        keep = vr.project_handoff(rec, dict(prev, margin_of_safety_price=207.07))
        self.assertEqual(keep['margin_of_safety_note'], prev['margin_of_safety_note'])

    def test_a_wiped_scenario_reads_equity_wiped_in_the_index_row(self):
        rec = _record('NBIS', '2026-11-13')
        self.assertTrue(rec['scenarios']['bear']['wiped'])
        cells = vr.parse_index_row(vr.index_row(rec))['cells']
        self.assertTrue(cells[3].startswith('0$ (ההון נמחק)'), cells[3])
        names = [v['name'] for v in rec.get('variants') or []]
        self.assertIn('funding_variant', names)                               # the financing-realistic case

    def test_named_variants_are_listed_in_the_numbers_block(self):
        rec = _record('AVGO', '2026-11-07')
        block = vr.numbers_block(rec)
        self.assertIn('גרסאות שמורות', block)
        self.assertIn('no_slowdown', block)
        no_var = copy.deepcopy(rec)
        no_var.pop('variants', None)
        self.assertNotIn('גרסאות שמורות', vr.numbers_block(no_var))


if __name__ == '__main__':
    unittest.main()
