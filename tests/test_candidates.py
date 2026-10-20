"""candidates.py: cards, exploratory path and research queue, on the SYNTHETIC screen universe of test_screen."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import test_screen as T  # noqa: E402

from fundamental_engine import candidates as C, screen as S  # noqa: E402

EV = {'reason': 'signed multi-year supply contracts not yet in revenue',
      'evidence': [{'source': '8-K exhibit 99.1 (SYNTHETIC)', 'date': '2026-09-30', 'statement': 'three-year take-or-pay contract'}]}


class CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = S.scan(T.universe(), {'as_of': T.AS_OF})
        cls.set = C.cards(cls.scan)
        cls.by = {c['symbol']: c for c in cls.set['cards']}

    def test_every_lens_candidate_has_a_complete_card(self):
        lensed = {c['symbol'] for ln in S.LENSES for c in self.scan['lenses'][ln]}
        self.assertEqual(set(self.by), lensed)
        for c in self.set['cards']:
            for k in ('why_found', 'hypotheses', 'falsifiers', 'priced_in', 'missing_data', 'full_analysis', 'research_question'):
                self.assertTrue(c[k], (c['symbol'], k))
            self.assertIn(c['full_analysis']['decision'], ('yes', 'maybe', 'no'))
            self.assertIn('not a recommendation', c['meaning'])

    def test_card_reads_lanes_from_the_scan(self):
        dip = self.by['DIP']
        self.assertEqual({x['lens'] for x in dip['lanes']}, {ln for ln in S.LENSES
                                                             if 'DIP' in [c['symbol'] for c in self.scan['lenses'][ln]]})
        sel = next(e['selected_by'] for e in self.scan['shortlist'] if e['symbol'] == 'DIP')
        self.assertEqual(dip['lanes'][0]['lens'], sel)                      # the lane that selected it comes first
        self.assertEqual(len(dip['falsifiers']), len(dip['lanes']))
        self.assertEqual(dip['lanes'][0]['label_he'], C.LANES_HE[dip['lanes'][0]['lens']])

    def test_shortlisted_names_are_marked_for_full_analysis(self):
        short = {s['symbol'] for s in self.scan['shortlist']}
        for s, c in self.by.items():
            self.assertEqual(c['full_analysis']['decision'] == 'yes', s in short, s)

    def test_data_flags_reach_the_card(self):
        rows = T.universe()
        meg = next(r for r in rows if r['symbol'] == 'MEGA')
        meg['data_flags'] = [{'code': 'fcf_conversion_outlier', 'severity': 'minor', 'message': 'FCF / net income 14.0: dropped'}]
        scan = S.scan(rows, {'as_of': T.AS_OF})
        card = next(c for c in C.cards(scan)['cards'] if c['symbol'] == 'MEGA')
        self.assertIn('data: FCF / net income 14.0: dropped', card['risks'])


class ExploratoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = S.scan(T.universe(), {'as_of': T.AS_OF})

    def test_business_evidence_admits_a_name_the_numbers_did_not(self):
        out = C.cards(self.scan, exploratory={'TRAP': EV})
        trap = next(c for c in out['cards'] if c['symbol'] == 'TRAP')
        self.assertEqual(trap['lanes'][-1]['lens'], 'exploratory')
        self.assertEqual(trap['full_analysis']['decision'], 'yes')
        self.assertEqual(trap['evidence'], EV['evidence'])
        self.assertIn('no lens matched', trap['numeric_failures'])          # the numeric failures stay visible

    def test_evidence_is_required(self):
        with self.assertRaises(ValueError):
            C.cards(self.scan, exploratory={'TRAP': {'reason': 'looks interesting'}})
        with self.assertRaises(ValueError):
            C.cards(self.scan, exploratory={'TRAP': {'reason': 'x', 'evidence': [{'source': 'blog'}]}})

    def test_unsupported_and_unknown_names_are_not_forced_through(self):
        out = C.cards(self.scan, exploratory={'BANK': EV, 'NOPE': EV})
        bank = next(c for c in out['cards'] if c['symbol'] == 'BANK')
        self.assertEqual(bank['full_analysis']['decision'], 'no')
        self.assertIn('sector-specific', bank['full_analysis']['reason'])
        self.assertTrue(any(n.startswith('NOPE: not in the scanned universe') for n in out['notes']))

    def test_distress_needs_a_funding_view_first(self):
        out = C.cards(self.scan, exploratory={'DISTR': EV})
        d = next(c for c in out['cards'] if c['symbol'] == 'DISTR')
        self.assertEqual(d['full_analysis']['decision'], 'maybe')
        self.assertIn('funding', d['full_analysis']['reason'])


class QueueTests(unittest.TestCase):
    def test_queue_rotates_across_lanes_and_respects_the_limit(self):
        scan = S.scan(T.universe(), {'as_of': T.AS_OF})
        q = C.research_queue(C.cards(scan), max_full=4)
        self.assertEqual(len(q), 4)
        self.assertEqual(len({x['symbol'] for x in q}), 4)
        self.assertGreaterEqual(len({x['lane'] for x in q}), 3)              # no single lane fills the queue

    def test_render_he_lists_the_required_fields(self):
        scan = S.scan(T.universe(), {'as_of': T.AS_OF})
        txt = C.render_he(C.cards(scan), limit=2)
        for label in ('למה התגלתה', 'מה יכול להפוך אותה לחריגה', 'סיכונים', 'נתונים חסרים', 'ניתוח מלא'):
            self.assertIn(label, txt)


if __name__ == '__main__':
    unittest.main()
