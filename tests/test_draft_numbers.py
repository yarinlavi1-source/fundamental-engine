"""A price claim withheld only for review or verification reasons (an unreleased forensic flag, an open material issue, an
unreviewed draft) still shows its numbers in the owner's table and in scenarios_he.md, under the banner 'טיוטה — לא אומתה'
with the reasons and without a buy/sell bottom line; blocked data, a failed valuation and the holds that make the number
unusable (governance evidence, demo inputs, a 'hold' material issue) still show no number. Built on the executed ADBE
casefile (an unreviewed draft with an open material issue), its decision gates changed one state at a time."""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from fundamental_engine import pipeline, scenario_reasons as SR
from fundamental_engine.render import (CALL_HE, DRAFT_BANNER_HE, DRAFT_STATE_HE, hebrew_table, hebrew_paragraph,
                                       value_display, _money)

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
HEAD = '| שנה | דובי | בסיסי | שורי |'
FAIR_ROW = '| שווי הוגן (תרחיש בסיסי) |'
NO_CALL = 'אין עדיין מסקנת קנייה או מכירה'
FORENSIC_HE = 'בדיקות הדוחות הדליקו דגל אדום שעוד לא הוסבר'


def with_gates(cf, **gates):
    out = deepcopy(cf)
    out['dc']['gates'].update(gates)
    return out


def final(cf):
    """The same executed casefile as a final result: no review gap, no open issue, no flag."""
    out = with_gates(cf, draft=[], draft_codes=[], material_issues=[], red_flags=[])
    out['dc']['verdict'].update(price_claim_withheld=False, status='final', withheld_reasons=[])
    return out


def bottom_line(table):
    return next(x for x in table.splitlines() if x.startswith('| שורה תחתונה |'))


class DraftWithNumbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cf = pipeline.run(deepcopy(ADBE))
        dc = cls.cf['dc']
        cls.code = dc['verdict']['code']
        cls.fair = dc['expected_return']['fair_value']['value']
        cls.cur = dc['price']['currency']
        forensic = with_gates(cls.cf, red_flags=['accounting_risk'], red_flags_released=False)
        forensic['dc']['verdict']['withheld_reasons'].append('forensic red flag raised (accounting_risk)')
        material = with_gates(final(cls.cf), material_issues=[{'id': 'cycle_treatment_override', 'hold': False,
                                                                 'description_he': 'האנליסט קבע שהמחזור מבני'}])
        material['dc']['verdict']['price_claim_withheld'] = True
        cls.states = {'unreviewed_with_open_issue': cls.cf, 'forensic_flag': forensic, 'open_material_issue': material}

    def test_the_fixture_is_a_review_only_draft(self):
        v = self.cf['dc']['verdict']
        self.assertTrue(v['price_claim_withheld'])
        self.assertIn(self.code, ('attractive_at_price', 'quality_but_expensive', 'index_preferable'))
        self.assertEqual(self.cf['dc']['gates']['data_blocked'], [])

    def test_review_reasons_show_numbers_under_the_banner_without_a_call(self):
        for name, cf in self.states.items():
            d = value_display(cf)
            self.assertEqual((d['show'], d['draft']), (True, True), name)
            t = hebrew_table(cf)
            self.assertIn(f'> **{DRAFT_BANNER_HE}.** הסיבה: {d["reason_he"]}.', t, name)
            self.assertLess(t.index(DRAFT_BANNER_HE), t.index('| מחיר היום |'), name)          # the banner comes first
            self.assertIn(f'{FAIR_ROW} {_money(self.fair, self.cur)} |', t, name)               # the executed number
            self.assertEqual(t.count(HEAD), 1, name)
            self.assertEqual(sum(1 for x in t.splitlines() if x.startswith('| שנה ') and x != HEAD), 5, name)   # years 1-5
            self.assertIn('| היום |', t, name)
            self.assertIn(NO_CALL, bottom_line(t), name)
            for call in CALL_HE.values():                                               # no buy/sell bottom line anywhere
                self.assertNotIn(call, t, name)
            r = SR.build(cf)
            self.assertTrue(r['values_shown'], name)
            self.assertEqual(r['draft_reason_he'], d['reason_he'], name)
            md = SR.render_he(r)
            self.assertIn(f'> **{DRAFT_BANNER_HE}.** הסיבה: {d["reason_he"]}.', md, name)
            self.assertIn('$', md, name)
        self.assertIn(FORENSIC_HE, value_display(self.states['forensic_flag'])['reason_he'])
        self.assertEqual(value_display(self.states['forensic_flag'])['draft_reasons_he'][-1],     # and the review still to come
                         DRAFT_STATE_HE['underwriting_incomplete'])
        self.assertEqual(value_display(self.states['open_material_issue'])['draft_reasons_he'],
                         ['בתחזית נותרה שאלה מהותית פתוחה: האנליסט קבע שהמחזור מבני'])
        # the numbers are the same as in the final result: only the labels change
        rows = lambda t: [x for x in t.splitlines() if x.startswith('| שנה ') or x.startswith(FAIR_ROW)]
        self.assertEqual(rows(hebrew_table(self.states['forensic_flag'])), rows(hebrew_table(final(self.cf))))

    def test_a_final_result_keeps_its_call_and_has_no_banner(self):
        cf = final(self.cf)
        d = value_display(cf)
        self.assertEqual((d['show'], d['draft'], d['reason_he']), (True, False, None))
        t = hebrew_table(cf)
        self.assertNotIn(DRAFT_BANNER_HE, t)
        self.assertEqual(bottom_line(t), f'| שורה תחתונה | {CALL_HE[self.code]} |')
        r = SR.build(cf)
        self.assertEqual((r['draft'], r['draft_reason_he']), (False, None))
        self.assertNotIn(DRAFT_BANNER_HE, SR.render_he(r))

    def test_the_paragraph_is_unchanged_by_the_table_rule(self):
        # the paragraph keeps its own draft wording (a forensic hold still states no value there)
        self.assertIn('טיוטה', hebrew_paragraph(self.cf))
        self.assertNotIn(DRAFT_BANNER_HE, hebrew_paragraph(self.states['forensic_flag']))


class NoNumbersWhenTheValueIsNotUsable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cf = pipeline.run(deepcopy(ADBE))

    def held(self):
        data = with_gates(self.cf, data_blocked=['critical field cash_and_liquid_investments: conflict'])
        data['dc']['verdict'].update(code='insufficient_evidence')                     # the route decision takes on blocked data
        data_price = with_gates(self.cf, data_blocked=['critical field cash_and_liquid_investments: conflict'])
        failed = with_gates(self.cf, valuation_failed=['the valuation stage failed: ValueError'])
        failed['dc']['verdict'].update(code='insufficient_evidence')
        failed_price = with_gates(self.cf, valuation_failed=['the valuation stage failed: ValueError'])
        gov = with_gates(self.cf, withhold_price=[{'id': 'gov.related_party', 'description_he': 'עסקאות עם צד קשור'}])
        hold = with_gates(self.cf, red_flags=['accounting_risk'],
                          material_issues=[{'id': 'opening_dates_differ', 'hold': True, 'description_he': 'נתוני הפתיחה לא גושרו'}])
        demo = deepcopy(self.cf)
        demo['dc']['valuation_view']['is_demo'] = True
        rejected = with_gates(self.cf, draft_codes=['assumption_rejected'])
        return {'data_blocked': data, 'data_blocked_price_code': data_price, 'valuation_failed': failed,
                'valuation_failed_price_code': failed_price, 'governance_flag': gov, 'forensic_and_hold_issue': hold,
                'demo_inputs': demo, 'assumption_rejected': rejected}

    def test_blocked_failed_or_unusable_valuations_show_no_number(self):
        for name, cf in self.held().items():
            d = value_display(cf)
            self.assertEqual((d['show'], d['draft']), (False, False), name)
            t = hebrew_table(cf)
            self.assertNotIn(FAIR_ROW, t, name)
            self.assertIn('| שווי הוגן | אין עדיין שווי אמין להצגה |', t, name)
            self.assertNotIn(HEAD, t, name)
            self.assertNotIn(DRAFT_BANNER_HE, t, name)
            r = SR.build(cf)
            self.assertEqual((r['values_shown'], r['draft_reason_he']), (False, None), name)
            md = SR.render_he(r)
            self.assertIn('אין שווי להציג', md, name)
            self.assertNotIn('$', md, name)


if __name__ == '__main__':
    unittest.main()
