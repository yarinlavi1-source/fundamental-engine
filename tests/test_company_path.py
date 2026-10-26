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
            self.assertEqual(r['multiple_evidence_ids'], EV)
            self.assertTrue(set(EV) <= set(r['evidence_ids']))                       # with the EPS drivers' evidence
            self.assertTrue(r['eps_drivers'])
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
        self.assertIn('| שנה 1 (2027/10) | — |', t)
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
        self.assertEqual(len(lines), 5)                                                # years 1-5; today stays in the casefile
        self.assertIn('אין מחיר: רווח למניה אפסי או שלילי', lines[1])
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

    def test_render_shows_one_yearly_table(self):
        plain, t = hebrew_table(self.plain), hebrew_table(self.cf)
        head = '| שנה | דובי | בסיסי | שורי |'
        self.assertEqual(plain.count(head), 1)                                         # without company_path: the value table
        self.assertIn('**שווי למניה בכל שנה, לפי תרחיש**', plain)
        self.assertEqual(t.count(head), 1)                                             # with it: the company's price table only
        self.assertIn(f'**{C.TITLE_HE}**', t)
        self.assertNotIn('**שווי למניה בכל שנה, לפי תרחיש**', t)
        for x in (plain, t):
            for gone in ('| מחיר צפוי למניה (כ־', '| דובי / שורי |', 'שווי משוער בעוד 5 שנים', '| מועד |'):
                self.assertNotIn(gone, x)
        self.assertNotIn('| היום |', t)                                                # today is the quote and the fair value
        base1 = self.cp['scenarios']['base']['rows'][1]['price']
        self.assertIn(f'| שנה 1 (2027/10) | — | {base1:,.0f}$ (', t)
        self.assertEqual(sum(1 for x in t.splitlines() if x.startswith('| שנה ') and '(' in x), 5)
        self.assertIn('ממודל תזרים המזומנים', t)                                       # the two bases are named

    def test_invariance_of_every_existing_value(self):
        skip = {'company_path', 'stages', 'casefile_sha256', 'inputs_sha256'}

        def no_prices(cf, k):                     # scenario_reasons also explains the company-path prices (price_rows)
            v = deepcopy(cf.get(k))
            if k == 'scenario_reasons':
                for sc in v['scenarios']:
                    sc.pop('price_rows')
            return json.dumps(v, sort_keys=True, default=str)
        for k in set(self.plain) | set(self.cf):
            if k not in skip:
                self.assertEqual(no_prices(self.plain, k), no_prices(self.cf, k), k)
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



# economics mode: the analyst gives the scenario's incremental ROIC; the engine computes the multiple path from its own
# executed EPS path, cost of equity, terminal cost of equity and terminal growth (base and bear), next to a hand path (bull)
ECON = {'metric': 'eps', 'multiple': 'forward_pe',
        'scenarios': {'base': {'multiple_from': 'economics', 'roic': .30, 'justification': 'justified_high', 'source_tier': 'own_history',
                               'reason_he': 'תשואה של כ־30% על ההון שהצמיחה דורשת, כמו בשלוש השנים האחרונות.', 'evidence_ids': EV},
                      'bear': {'multiple_from': 'economics', 'roic': .12, 'justification': 'unproven', 'source_tier': 'analyst',
                               'reason_he': 'התחרות מורידה את התשואה על ההון החדש.', 'evidence_ids': EV},
                      'bull': deepcopy(CP['scenarios']['bull'])},
        'own_history': deepcopy(CP['own_history'])}


def analysts_path_calc(fy, as_of, ke, ket, gt, roic):
    """The ticker analysts' formula as their scratchpad path_calc.py wrote it (index-based: the fiscal year in progress
    annualized, one year apart after it), on the executed fiscal-year EPS: an independent cross-check of the engine."""
    end0 = years(as_of, fy[0]['fiscal_year_end'])
    n = len(fy)
    e = [fy[0]['eps'] / fy[0]['years']] + [f['eps'] for f in fy[1:]]
    e.append(e[-1] * (1 + gt))
    g = lambda k: e[k] / e[k - 1] - 1
    d = [e[k] * (1 - g(k + 1) / roic) for k in range(n)]
    tv = e[n] * (1 - gt / roic) / (ket - gt)
    out = []
    for t in range(6):
        v = 0.
        for k in range(n):
            end = end0 + k
            if end <= t:
                continue
            rem = min(1., end - t)
            v += d[k] * rem / (1 + ke) ** (end - t - rem / 2)
        v += tv / (1 + ke) ** (end0 + n - 1 - t)
        out.append(v / e[t + 1])
    return out


class EconomicsModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain = pipeline.run(deepcopy(ADBE))
        cls.cf = pipeline.run(deepcopy(ADBE), {'company_path': deepcopy(ECON)})
        cls.cp = cls.cf['company_path']
        cls.val = cls.cf['val']
        cls.case = {s['name']: s for s in cls.val['case']['scenarios']}

    def test_the_engine_computes_the_path_at_its_own_executed_rates_and_eps(self):
        self.assertEqual(self.cf['stages']['company_path'], 'ok')
        for s in ('base', 'bear'):
            x = self.cp['scenarios'][s]
            e, c = x['economics'], self.case[s]
            self.assertEqual((x['multiple_from'], e['status'], e['roic']), ('economics', 'computed', ECON['scenarios'][s]['roic']))
            # the executed rates, read from the valuation case, never typed by the analyst
            self.assertEqual(e['cost_of_equity'], c['cost_of_equity'])
            self.assertEqual(e['terminal_cost_of_equity'], c['terminal']['cost_of_equity'])
            self.assertEqual(e['terminal_growth'], c['terminal']['growth'])
            self.assertEqual(e['terminal_cost_of_equity_source'], 'terminal.cost_of_equity')
            self.assertEqual(e['engine_terminal_roic'], c['terminal']['roic'])
            self.assertAlmostEqual(e['roic_minus_engine_terminal_roic'], e['roic'] - c['terminal']['roic'], places=12)
            self.assertIn('D_k = E_k x (1 - g_(k+1) / ROIC)', e['formula'])
            fy = self.cp['fiscal_years'][s]
            self.assertEqual(e['explicit_fiscal_years'], len(fy))
            self.assertEqual(e['last_explicit_fiscal_year_end'], fy[-1]['fiscal_year_end'])
            self.assertEqual(x['multiple_path'], e['multiple_path'])
            for t, r in enumerate(x['rows']):
                self.assertEqual(r['multiple'], e['multiple_path'][t])
                self.assertEqual(r['eps'], fy[t + 1]['eps'])                     # the executed EPS of the next fiscal year
                self.assertAlmostEqual(r['price'], e['values'][t]['value'], delta=1e-9 * abs(r['price']))   # price = value_t
                self.assertIn('מחושב מהכלכלה של התרחיש', r['reason_he'])
            # the documented formula, leaf by leaf
            ke, ket, gt, roic = e['cost_of_equity'], e['terminal_cost_of_equity'], e['terminal_growth'], e['roic']
            ann = [f['eps'] / f['years'] if f['partial'] else f['eps'] for f in fy]
            nxt = ann[1:] + [ann[-1] * (1 + gt)]
            for k, f in enumerate(fy):
                w = f['years'] if f['partial'] else 1.
                self.assertAlmostEqual(e['fiscal_years'][k]['distribution'], f['eps'] - w * (nxt[k] - ann[k]) / roic, places=10)
            tv = nxt[-1] * (1 - gt / roic) / (ket - gt)
            self.assertAlmostEqual(e['terminal']['value_at_last_fiscal_year_end'], tv, places=9)
            v0 = sum(e['fiscal_years'][k]['distribution'] / (1 + ke) ** (years(self.val['case']['as_of'], f['fiscal_year_end']) - f['years'] / 2)
                     for k, f in enumerate(fy)) + tv / (1 + ke) ** years(self.val['case']['as_of'], fy[-1]['fiscal_year_end'])
            self.assertAlmostEqual(e['values'][0]['value'], v0, places=9)
            # the analysts' own formula (path_calc.py) gives the same multiples within day-count rounding
            ref = analysts_path_calc(fy, self.val['case']['as_of'], ke, ket, gt, roic)
            for m, r in zip(e['multiple_path'], ref):
                self.assertAlmostEqual(m / r, 1, delta=1e-3)
        # the hand path next to it is kept as given, without an economics record
        bull = self.cp['scenarios']['bull']
        self.assertEqual(bull['multiple_path'], CP['scenarios']['bull']['multiple_path'])
        self.assertNotIn('economics', bull)
        self.assertNotIn('multiple_from', bull)

    def test_the_path_follows_the_executed_rates(self):
        """A path the engine computes cannot go stale: another executed Ke moves it (the CRDO/NVDA repair findings)."""
        val = deepcopy(self.val)
        for c in val['case']['scenarios']:
            c['cost_of_equity'] += .01
            c['terminal']['cost_of_equity'] += .01
        moved = C.build(deepcopy(ECON), val, KNOWN)['scenarios']['base']
        self.assertAlmostEqual(moved['economics']['cost_of_equity'], self.case['base']['cost_of_equity'] + .01, places=12)
        before = self.cp['scenarios']['base']['multiple_path']
        self.assertTrue(all(a < b for a, b in zip(moved['multiple_path'], before)))
        # a higher ROIC needs less reinvestment for the same EPS path: a higher multiple
        hi = deepcopy(ECON)
        hi['scenarios']['base']['roic'] = .60
        up = C.build(hi, self.val, KNOWN)['scenarios']['base']['multiple_path']
        self.assertTrue(all(a > b for a, b in zip(up, before)))

    def test_a_year_without_a_positive_value_has_no_price(self):
        val = deepcopy(self.val)
        bear = next(s for s in val['result']['scenarios'] if s['name'] == 'bear')
        for p in bear['forecast']:
            if p['end'] > '2028-11-30':
                p['ebit'] = -5 * abs(p['ebit'])
        rows = C.build(deepcopy(ECON), val, KNOWN)['scenarios']['bear']['rows']
        self.assertEqual((rows[0]['why'], rows[0]['price'], rows[0]['multiple']), ('nonpositive_value', None, None))
        self.assertIn('השווי לפי הכלכלה של התרחיש אינו חיובי', rows[0]['reason_he'])
        self.assertEqual(rows[2]['why'], 'nonpositive_eps')                      # fiscal 2029: a loss
        lines, _ = C.table_he(C.build(deepcopy(ECON), val, KNOWN), lambda x: f'{x:,.0f}$')
        self.assertIn('אין מחיר: רווח למניה אפסי או שלילי', lines[1])

    def test_economics_record_without_eps_or_with_terminal_ke_at_growth(self):
        fy = [{'fiscal_year_end': '2027-06-30', 'years': .5, 'partial': True, 'eps': 1.},
              {'fiscal_year_end': '2028-06-30', 'years': 1., 'partial': False, 'eps': None}]
        self.assertEqual(C.economics(fy, '2026-12-31', .1, .09, .04, .3)['status'], 'not_computed')
        fy[1]['eps'] = 2.
        r = C.economics(fy, '2026-12-31', .1, .04, .04, .3)
        self.assertEqual((r['status'], r['multiple_path']), ('not_computed', [None] * 6))
        self.assertIn('terminal cost of equity', r['reason'])

    def test_peak_label_is_checked_on_computed_multiples(self):
        cp = deepcopy(ECON)
        cp['scenarios']['base']['justification'] = 'peak'
        cp['own_history']['median'] = 5.
        cp['own_history']['low'] = 4.
        out = C.build(cp, self.val, KNOWN)
        f = next(x for x in out['flags'] if x['id'] == 'peak_above_own_median' and x['scenario'] == 'base')
        self.assertAlmostEqual(f['max_multiple'], max(m for m in out['scenarios']['base']['multiple_path'] if m), places=12)

    def test_validation_of_economics_mode(self):
        def bad(change, msg):
            cp = deepcopy(ECON)
            change(cp)
            with self.assertRaisesRegex(ValueError, msg):
                C.validate(cp, KNOWN)
        bad(lambda c: c['scenarios']['base'].update(multiple_path=[30, 28, 26, 24, 22, 20]), 'do not give one')
        bad(lambda c: c['scenarios']['base'].pop('roic'), 'needs roic')
        bad(lambda c: c['scenarios']['base'].update(roic=30), 'needs roic')                 # a percent typed as a number
        bad(lambda c: c['scenarios']['base'].update(roic=0), 'needs roic')
        bad(lambda c: c['scenarios']['base'].update(roic=True), 'needs roic')
        bad(lambda c: c['scenarios']['base'].update(multiple_from='market'), "multiple_from must be")
        bad(lambda c: c['scenarios']['bull'].update(roic=.3), 'roic belongs to')
        bad(lambda c: c['scenarios']['base'].pop('evidence_ids'), 'evidence_ids required')
        bad(lambda c: c['scenarios']['base'].pop('source_tier'), 'source_tier required')
        bad(lambda c: c['scenarios']['base'].update(justification='cheap'), 'justification must be')
        hand = deepcopy(CP)
        hand['scenarios']['base']['multiple_from'] = 'hand'                                  # explicit hand mode: same output
        self.assertEqual(C.validate(hand, KNOWN), C.validate(deepcopy(CP), KNOWN))
        self.assertNotIn('multiple_from', C.validate(deepcopy(CP), KNOWN)['scenarios']['base'])
        # a ROIC at or below the executed terminal growth would make the terminal payout negative: refused at build
        low = deepcopy(ECON)
        low['scenarios']['base']['roic'] = .04
        with self.assertRaisesRegex(ValueError, 'must exceed the executed terminal growth'):
            C.build(low, self.val, KNOWN)
        cf = pipeline.run(deepcopy(ADBE), {'company_path': low})
        self.assertEqual(cf['stages']['company_path'], 'failed')

    def test_table_names_the_computed_multiples(self):
        t = hebrew_table(self.cf)
        self.assertIn('בסיסי — מחושב מהתשואה על ההון (ההיסטוריה של החברה)', t)
        self.assertIn('שורי — מתחרים ישירים (CRDO, MRVL)', t)
        base1 = self.cp['scenarios']['base']['rows'][1]['price']
        self.assertIn(f'{base1:,.0f}$ (', t)

    def test_ticker_run_reports_the_economics(self):
        from fundamental_engine.ticker_run import _compact_path, coherence_md
        out = _compact_path(self.cf)
        self.assertEqual(set(out['economics']), {'base', 'bear'})
        self.assertEqual(out['economics']['base']['cost_of_equity'], self.case['base']['cost_of_equity'])
        self.assertEqual([r['multiple'] for r in out['base']], self.cp['scenarios']['base']['multiple_path'])
        md = coherence_md({'company_path': ECON})
        self.assertIn('computed by the engine from its own EPS path and rates at roic 0.3', md)
        self.assertIn(str(CP['scenarios']['bull']['multiple_path']), md)

    def test_invariance_of_every_existing_value(self):
        for k in set(self.plain) | set(self.cf):
            if k in ('company_path', 'stages', 'casefile_sha256', 'inputs_sha256', 'scenario_reasons'):
                continue
            self.assertEqual(json.dumps(self.plain.get(k), sort_keys=True, default=str),
                             json.dumps(self.cf.get(k), sort_keys=True, default=str), k)
        a, b = deepcopy(self.plain['scenario_reasons']), deepcopy(self.cf['scenario_reasons'])
        for sc in a['scenarios'] + b['scenarios']:
            sc.pop('price_rows')
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
