"""Company price path per scenario (company_path.py): EPS of the next fiscal year from the executed forecast x an evidenced
forward multiple per scenario; validation, the peak flag, an absent scenario, unpriced rows, the owner's table and the
invariance of every existing value."""
import json
import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from fundamental_engine import pipeline, company_path as C  # noqa: E402
from fundamental_engine.render import hebrew_table  # noqa: E402
from fundamental_engine.valuation import years  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
EV = ['fmp:as_reported_income_annual']
KNOWN = set(ADBE['sources']) | {q['id'] for q in ADBE.get('qualitative') or []}
# ALAB-like multiple paths (a fast grower's forward P/E compressing as growth fades), applied to the fixture's own EPS
CP = {'metric': 'eps', 'multiple': 'forward_pe',
      'scenarios': {'base': {'multiple_path': [60, 52, 45, 40, 36, 33], 'justification': 'justified_high', 'source_tier': 'own_history',
                             'reason_he': 'צמיחה מהירה ותשואה גבוהה על ההון; המכפיל מתכווץ עם הצמיחה.', 'evidence_ids': EV},
                    'bull': {'multiple_path': [70, 68, 66, 64, 62, 60], 'justification': 'peak', 'source_tier': 'competitor',
                             'competitors': ['CRDO', 'MRVL'], 'reason_he': 'מחזור השקעות בתשתיות מחשוב בשיאו.', 'evidence_ids': EV}},
      'own_history': {'low': 35, 'median': 62, 'high': 110, 'years': '2024-2026', 'evidence_ids': EV},
      'peers': [{'ticker': 'CRDO', 'forward_pe': 55, 'growth': .6, 'evidence_ids': EV}]}


def fy_rows(scenario, end, val):
    """The executed period rows of the fiscal year ending at end (a fiscal year split by the fifth anniversary has two)."""
    interim = set(val['case']['generated_from']['interim_boundaries'])
    rows = next(s for s in val['result']['scenarios'] if s['name'] == scenario)['forecast']
    i = next(k for k, r in enumerate(rows) if r['end'] == end)
    out = [rows[i]]
    while i > 0 and rows[i - 1]['end'] in interim:
        i -= 1
        out.insert(0, rows[i])
    return out


class CompanyPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain = pipeline.run(deepcopy(ADBE))
        cls.cf = pipeline.run(deepcopy(ADBE), {'company_path': deepcopy(CP)})
        cls.cp = cls.cf['company_path']
        cls.val = cls.cf['val']

    def test_arithmetic_price_is_multiple_times_next_fiscal_year_eps(self):
        self.assertEqual(self.cf['stages']['company_path'], 'ok')
        self.assertEqual(self.cp['status'], 'computed')
        quote = self.val['case']['quote']['price']
        fys = [f['fiscal_year_end'] for f in self.cp['fiscal_years']['base']]
        self.assertTrue(self.cp['fiscal_years']['base'][0]['partial'])           # fiscal year 0 = the one in progress
        for t, r in enumerate(self.cp['scenarios']['base']['rows']):
            self.assertEqual(r['fiscal_year_end'], fys[t + 1])
            ps = fy_rows('base', r['fiscal_year_end'], self.val)
            ni = sum(p['ebit'] - p['interest'] - p['cash_taxes'] + p['investment_income_after_tax'] - p['noncontrolling_distributions']
                     for p in ps)
            dur = [years(p['start'], p['end']) for p in ps]
            sh = sum((p['shares_open'] + p['shares']) / 2 * d for p, d in zip(ps, dur)) / sum(dur)
            self.assertAlmostEqual(r['eps'], ni / sh, places=9)
            self.assertAlmostEqual(r['price'], CP['scenarios']['base']['multiple_path'][t] * ni / sh, places=6)
            self.assertAlmostEqual(r['operating_margin'], sum(p['ebit'] for p in ps) / sum(p['revenue'] for p in ps), places=12)
            if t:
                self.assertEqual(r['date'], f'{2026 + t}-10-19')
                self.assertAlmostEqual(r['annual_return'], (r['price'] / quote) ** (1 / t) - 1, places=12)
            else:
                self.assertEqual(r['date'], '2026-10-19')
                self.assertIsNone(r['annual_return'])
            self.assertIn('רווח למניה', r['reason_he'])
            self.assertIn(CP['scenarios']['base']['reason_he'], r['reason_he'])
            self.assertEqual(r['evidence_ids'], EV)
        # the fiscal year split by the fifth anniversary (2031-10-19) is summed back over both periods
        split = next(r for r in self.cp['scenarios']['base']['rows'] if r['fiscal_year_end'] == '2031-11-30')
        self.assertEqual(len(fy_rows('base', split['fiscal_year_end'], self.val)), 2)
        self.assertIn('מתחרים ישירים: CRDO, MRVL', self.cp['scenarios']['bull']['rows'][0]['reason_he'])

    def test_weighted_shares_and_residual_income_rows(self):
        rows = [{'start': '2026-01-01', 'end': '2026-12-31', 'net_income': 120., 'shares': 12., 'shares_open': 8.},
                {'start': '2026-12-31', 'end': '2027-12-31', 'net_income': 150., 'shares': 10.}]
        fy = C.fiscal_years(rows)
        self.assertAlmostEqual(fy[0]['weighted_shares'], 10., places=12)
        self.assertAlmostEqual(fy[0]['eps'], 12., places=12)
        self.assertAlmostEqual(fy[1]['eps'], 15., places=12)
        self.assertIsNone(fy[1]['revenue'])

    def test_missing_scenario_is_absent_never_defaulted(self):
        self.assertNotIn('bear', self.cp['scenarios'])
        self.assertEqual(self.cp['absent'], ['bear'])
        t = hebrew_table(self.cf)
        self.assertIn('| היום | — |', t)
        self.assertIn('תרחיש דובי: לא הוזן מכפיל מנומק', t)

    def test_peak_flag(self):
        f = next(x for x in self.cp['flags'] if x['id'] == 'peak_above_own_median')
        self.assertEqual((f['scenario'], f['own_median'], f['max_multiple'], f['years']), ('bull', 62., 70., [0, 1, 2, 3]))
        self.assertIn('ההסבר שניתן: מחזור השקעות בתשתיות מחשוב בשיאו', f['text_he'])
        self.assertIn(f['text_he'], hebrew_table(self.cf))
        cp = deepcopy(CP)
        cp['scenarios']['bull']['multiple_path'] = [60, 58, 56, 54, 52, 50]               # at or below the own median: no flag
        self.assertEqual(C.build(cp, self.val, KNOWN)['flags'], [])
        del cp['own_history']
        self.assertEqual([x['id'] for x in C.build(cp, self.val, KNOWN)['flags']], ['peak_unchecked'])
        cp['scenarios']['bull']['justification'] = 'unproven'
        self.assertEqual(C.build(cp, self.val, KNOWN)['flags'], [])

    def test_unpriced_rows_say_why(self):
        val = deepcopy(self.val)
        bear = next(s for s in val['result']['scenarios'] if s['name'] == 'bear')
        for p in bear['forecast']:
            if '2028-11-30' < p['end'] <= '2029-11-30':
                p['ebit'] = -abs(p['ebit'])
        cp = deepcopy(CP)
        cp['scenarios']['bear'] = {'multiple_path': [20, 18, 16, 15, 14, 13], 'justification': 'unproven', 'source_tier': 'analyst',
                                   'reason_he': 'צמיחה נעצרת.', 'evidence_ids': EV}
        out = C.build(cp, val, KNOWN)
        r = out['scenarios']['bear']['rows'][2]                                       # year 2 reads fiscal 2029
        self.assertEqual((r['status'], r['why'], r['price']), ('no_price', 'nonpositive_eps', None))
        self.assertLess(r['eps'], 0)
        self.assertIn('רווח למניה אפסי או שלילי', r['reason_he'])
        self.assertEqual(out['scenarios']['bear']['rows'][1]['status'], 'priced')
        lines, _ = C.table_he(out, lambda x: f'{x:,.0f}$')
        self.assertIn('אין מחיר: רווח למניה אפסי או שלילי', lines[2])
        bear['status'] = 'funding_blocked'
        self.assertEqual({r['why'] for r in C.build(cp, val, KNOWN)['scenarios']['bear']['rows']}, {'funding_blocked'})
        bear['status'] = 'conditional'
        bear['forecast'] = bear['forecast'][:4]
        rows = C.build(cp, val, KNOWN)['scenarios']['bear']['rows']
        self.assertEqual([r.get('why') for r in rows][3:], ['beyond_forecast'] * 3)

    def test_validation(self):
        def bad(change, msg):
            cp = deepcopy(CP)
            change(cp)
            with self.assertRaisesRegex(ValueError, msg):
                C.validate(cp, KNOWN)
        bad(lambda c: c.update(metric='fcf'), 'metric must be')
        bad(lambda c: c.update(extra=1), 'unknown keys')
        bad(lambda c: c['scenarios'].update(tail=c['scenarios']['base']), 'scenarios must be')
        bad(lambda c: c['scenarios']['base'].update(multiple_path=[60, 52, 45]), 'multiple_path needs 6')
        bad(lambda c: c['scenarios']['base'].update(multiple_path=[60, 52, 45, 40, 0, 33]), 'multiple_path needs 6')
        bad(lambda c: c['scenarios']['base'].pop('evidence_ids'), 'evidence_ids required')
        bad(lambda c: c['scenarios']['base'].update(evidence_ids=['made_up']), 'unknown evidence ids')
        bad(lambda c: c['scenarios']['base'].pop('source_tier'), 'source_tier required')
        bad(lambda c: c['scenarios']['base'].update(source_tier='sector'), 'source_tier required')
        bad(lambda c: c['scenarios']['bull'].pop('competitors'), 'named competitors')
        bad(lambda c: c['scenarios']['base'].update(competitors=['X']), 'tier competitor only')
        bad(lambda c: c['scenarios']['base'].update(justification='cheap'), 'justification must be')
        bad(lambda c: c['scenarios']['base'].update(reason_he=' '), 'reason_he')
        bad(lambda c: c['own_history'].update(low=70), 'low <= median <= high')
        bad(lambda c: c['peers'][0].pop('growth'), 'growth number')
        self.assertEqual(C.validate(deepcopy(CP))['scenarios']['base']['source_tier'], 'own_history')     # known=None skips ids

    def test_render_replaces_the_universe_path_row(self):
        plain, t = hebrew_table(self.plain), hebrew_table(self.cf)
        self.assertNotIn(C.TITLE_HE, plain)
        self.assertIn('| מחיר צפוי למניה (כ־', plain)
        self.assertIn(f'**{C.TITLE_HE}**', t)
        self.assertIn('| שנה | דובי | בסיסי | שורי |', t)
        self.assertNotIn('| מחיר צפוי למניה (כ־', t)
        self.assertNotIn('מחיר צפוי למניה = המחיר של היום', t)
        self.assertIn('**שווי למניה בכל שנה, לפי תרחיש**', t)                         # the DCF value rows stay
        base0 = self.cp['scenarios']['base']['rows'][0]['price']
        self.assertIn(f'| היום | — | {base0:,.0f}$ |', t)
        self.assertEqual(sum(1 for x in t.splitlines() if x.startswith('| שנה ') and '(' in x), 5)

    def test_invariance_of_every_existing_value(self):
        skip = {'company_path', 'stages', 'casefile_sha256', 'inputs_sha256'}
        for k in set(self.plain) | set(self.cf):
            if k not in skip:
                self.assertEqual(json.dumps(self.plain.get(k), sort_keys=True, default=str),
                                 json.dumps(self.cf.get(k), sort_keys=True, default=str), k)
        self.assertNotIn('company_path', self.plain)
        self.assertNotIn('company_path', self.plain['stages'])
        self.assertEqual(pipeline.run(deepcopy(ADBE), {})['casefile_sha256'], self.plain['casefile_sha256'])

    def test_bad_input_fails_the_stage_and_keeps_the_generic_row(self):
        cp = deepcopy(CP)
        cp['scenarios']['base']['evidence_ids'] = ['made_up']
        cf = pipeline.run(deepcopy(ADBE), {'company_path': cp})
        self.assertEqual(cf['stages']['company_path'], 'failed')
        self.assertIn('unknown evidence ids', cf['errors'][-1]['error'])
        self.assertIsNone(cf['company_path'])
        self.assertEqual(hebrew_table(cf), hebrew_table(self.plain))
        self.assertEqual(cf['dc'], self.plain['dc'])


if __name__ == '__main__':
    unittest.main()
