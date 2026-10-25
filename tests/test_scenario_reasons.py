"""Scenario reasons (25.10.2026): bear/base/bull in the owner's words (דובי / בסיסי / שורי) and, behind the scenes, the
condition and the business of every dated scenario price, read from the executed casefile and never revalued.

The ADBE FactBook is the real fixture; its forecast is the engine's own mechanical draft, not an underwritten view of Adobe.
The FICTSAAS casefiles (tests/test_decision_render) are synthetic and carry no forecast paths, on purpose: missing data must
become a gap, never a number. Scenario events in these tests are TEST text, not research."""
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from fundamental_engine import pipeline, scenario_reasons as SR                                    # noqa: E402
from fundamental_engine.decision import decide                                                       # noqa: E402
from fundamental_engine.render import hebrew_table, hebrew_paragraph, markdown, value_display         # noqa: E402
import test_decision_render as T                                                                     # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
# the scenario names the owner retired (25.10.2026): 'likely' is not a scenario name
OLD_NAMES = ('תרחיש רע', 'תרחיש טוב', 'תרחיש סביר', 'התרחיש הסביר', 'התרחיש הרע', 'התרחיש הטוב', 'בתרחיש הרע',
             'בתרחיש הטוב', 'התרחיש השמרני', 'התרחיש האופטימי', 'סביר (בסיס)')
NOTE_PREFIX = ('- דובי = אם', '- בסיסי = אם', '- שורי = אם')
_CACHE = {}


def adbe_cf(inputs=None):
    key = json.dumps(inputs, sort_keys=True)
    if key not in _CACHE:
        _CACHE[key] = json.loads(json.dumps(pipeline.run(deepcopy(ADBE), deepcopy(inputs)), default=str))
    return deepcopy(_CACHE[key])


def synthetic(name):
    p = {'attractive': lambda: T.parts(35), 'expensive': lambda: T.parts(70), 'draft': lambda: T.parts(35, rv=T.RV_DRAFT),
         'grower': T.grower_parts, 'blocked': lambda: T.parts(35, qa={**T.QA_OK, 'status': 'blocked', 'blocking': ['debt']}),
         'demo': lambda: T.parts(35, val=T.valuation_of(T.SPEC, 35, demo=True))}[name]()
    return json.loads(json.dumps(T.casefile(p, decide(p)), default=str))


def by_name(r):
    return {s['name']: s for s in r['scenarios']}


def eps_of(row):
    """Independent EPS of one executed full-year forecast row: (EBIT - interest - cash taxes) / average shares."""
    return (row['ebit'] - row['interest'] - row['cash_taxes']) / ((row['shares_open'] + row['shares']) / 2)


class OwnerWords(unittest.TestCase):
    """R1: bear = דובי, base = בסיסי, bull = שורי wherever the owner reads a scenario name; other 'סביר' stays."""

    def test_rendered_fixtures_use_the_owner_words(self):
        cases = {n: synthetic(n) for n in ('attractive', 'expensive', 'draft', 'grower', 'blocked', 'demo')}
        cases['adbe'] = adbe_cf()
        for name, cf in cases.items():
            for text in (hebrew_table(cf), hebrew_paragraph(cf), markdown(cf), SR.render_he(SR.build(cf))):
                for w in OLD_NAMES:
                    self.assertNotIn(w, text, (name, w))
        for name in ('attractive', 'adbe'):
            t = hebrew_table(cases[name])
            self.assertEqual(t.count('| שנה | דובי | בסיסי | שורי |'), 1)        # ONE yearly table
            self.assertIn('| שווי הוגן (תרחיש בסיסי) |', t)
            self.assertNotIn('| דובי / שורי |', t)                              # today's range: the yearly table's first row
            self.assertIn('אם התרחיש הבסיסי מתממש', t)

    def test_non_scenario_reasonable_is_untouched(self):
        from fundamental_engine import analyst_sheet, decision, candidates
        self.assertEqual(analyst_sheet.LEVEL_HE['adequate'], 'סביר')                  # a quality level, not a scenario
        self.assertEqual(decision.LEVEL_HE['adequate'], 'סבירה')
        self.assertEqual(candidates.LANES_HE['quality_fair_price'], 'איכות במחיר סביר')       # 'a fair price' lens
        self.assertIn('והמגמה העסקית סבירה', hebrew_paragraph(adbe_cf()))              # 'a reasonable trend' stays
        self.assertIn("'חוב סביר'", (ROOT / 'fundamental_engine/plain.py').read_text(encoding='utf-8'))
        self.assertIn('תשואה סבירה', (ROOT / 'fundamental_engine/plain.py').read_text(encoding='utf-8'))
        self.assertIn('בתרחיש הבסיס', (ROOT / 'fundamental_engine/risk.py').read_text(encoding='utf-8'))   # already 'base'

    def test_plain_verdict_and_analyst_sheet_name_the_scenarios(self):
        from fundamental_engine.plain import plain_verdict
        p = json.loads((ROOT / 'examples/plain_demo.json').read_text())
        p['valuation_case'] = json.loads((ROOT / 'examples/valuation/infrastructure.json').read_text())
        price = plain_verdict(p)['price']
        self.assertEqual(price['bucket'], 'cheap_even_bear')                          # the bucket key is unchanged
        self.assertEqual(price['headline'], 'זול — גם בתרחיש הדובי')
        self.assertIn('בתרחיש השורי', price['explain'])
        sheet = adbe_cf()['analyst_sheet']
        asym = next(q for q in sheet['questions'] if q['key'] == 'asymmetry')
        self.assertIn('בתרחיש השורי', asym['reading_he'])
        self.assertIn('לוודא שהתרחיש הדובי כולל את הפחד המרכזי', asym['to_close_he'])
        from fundamental_engine.valuation import value_company
        from fundamental_engine.valuation_report import render_valuation
        self.assertIn('<th>דובי</th><th>בסיסי</th><th>שורי</th>', render_valuation(value_company(p['valuation_case'])))


class ReasonsOnTheRealFixture(unittest.TestCase):
    """R2 on the ADBE pipeline casefile: values copied, drivers read, P/E arithmetic, conditions from the mechanics."""

    @classmethod
    def setUpClass(cls):
        cls.cf = adbe_cf()
        cls.r = cls.cf['scenario_reasons']
        from fundamental_engine.render import yearly_table_rows               # the rows the owner's table shows
        cls.yearly = [r for _, r in yearly_table_rows(cls.cf['dc']['expected_return'])]
        cls.res = {s['name']: s for s in cls.cf['val']['result']['scenarios']}

    def test_status_and_rows_are_the_owner_table(self):
        self.assertEqual(self.r['status'], 'computed')
        self.assertTrue(self.r['values_shown'])
        self.assertTrue(self.r['draft'])                                   # unreviewed: the table says draft, so do the reasons
        self.assertEqual(self.r['source']['values'], 'dc.expected_return.yearly_at_model_cost_of_equity')
        self.assertEqual([s['label_he'] for s in self.r['scenarios']], ['דובי', 'בסיסי', 'שורי'])
        av = {a['date']: a for a in self.cf['val']['result']['annual_values']}
        for s in self.r['scenarios']:
            self.assertEqual([row['date'] for row in s['rows']], [y['date'] for y in self.yearly])
            for row, y in zip(s['rows'], self.yearly):
                self.assertEqual(row['value'], y[s['name']])                    # exactly the executed number
                self.assertEqual(row['value'], av[row['date']][s['name']])
                self.assertEqual(row['cumulative_distributions'], y['distributions'][s['name']])
                self.assertTrue(row['why_he'].startswith('אם '))
        self.assertEqual(by_name(self.r)['base']['rows'][0]['value'], self.cf['dc']['valuation_view']['base'])

    def test_drivers_are_read_from_the_executed_rows_and_paths(self):
        base = by_name(self.r)['base']
        row = next(x for x in base['rows'] if x['date'] == '2027-11-30')
        fy = next(p for p in self.res['base']['forecast'] if p['end'] == '2027-11-30')
        path = next(p for p in self.cf['fc']['paths']['base'] if p['end'] == '2027-11-30')
        anchor = self.cf['fc']['anchors']['current_fiscal_year']
        self.assertEqual(row['drivers']['revenue'], fy['revenue'])
        # executed growth over the current-year anchor (the first full year); the path's rate is kept as the assumption
        self.assertAlmostEqual(row['drivers']['revenue_growth'], fy['revenue'] / anchor['revenue'] - 1, places=6)
        self.assertEqual(row['driver_sources']['revenue_growth'], 'executed over fc.anchors.current_fiscal_year')
        self.assertAlmostEqual(row['drivers']['revenue_growth_assumption'], path['growth'], places=6)
        self.assertAlmostEqual(row['drivers']['operating_margin_after_sbc'], fy['ebit'] / fy['revenue'], places=6)
        self.assertAlmostEqual(row['drivers']['eps'], eps_of(fy), places=4)
        self.assertAlmostEqual(row['drivers']['fcf_per_share'],
                               (fy['cfo'] - fy['maintenance_capex'] - fy['growth_capex']) / ((fy['shares_open'] + fy['shares']) / 2), places=4)
        self.assertEqual(row['drivers']['diluted_shares'], fy['shares'])
        today = base['rows'][0]
        self.assertEqual((today['drivers']['revenue'], today['driver_sources']['revenue']),
                         (anchor['revenue'], 'fc.anchors.current_fiscal_year'))       # current year: the model's full-year anchor
        self.assertIsNone(today['drivers']['eps'])                                    # a partial year has no EPS
        five = base['rows'][-1]                                                      # fifth anniversary inside a fiscal year
        self.assertEqual(five['fiscal_year']['end'], '2031-11-30')
        self.assertTrue(five['fiscal_year']['full_year'])                             # the interim split is merged back

    def test_implied_pe_is_value_over_next_full_year_eps(self):
        for s in self.r['scenarios']:
            raw = self.res[s['name']]['forecast']
            for row in s['rows']:
                pe = row['implied_pe']
                self.assertGreaterEqual(pe['next_fiscal_year_start'], row['date'])   # starts on or after the row date
                # the executed rows of that fiscal year (an interim split at the fifth anniversary is two rows)
                part = [p for p in raw if p['start'] >= pe['next_fiscal_year_start'] and p['end'] <= pe['next_fiscal_year_end']]
                flow = sum(p['ebit'] - p['interest'] - p['cash_taxes'] for p in part)
                eps = flow / ((part[0]['shares_open'] + part[-1]['shares']) / 2)
                self.assertAlmostEqual(pe['eps_next_fiscal_year'], eps, places=4)
                self.assertAlmostEqual(pe['value'], round(row['value'] / eps, 2), places=6)
        merged = next(x for x in by_name(self.r)['base']['rows'] if x['date'] == '2030-11-30')['implied_pe']
        self.assertEqual((merged['next_fiscal_year_start'], merged['next_fiscal_year_end']), ('2030-11-30', '2031-11-30'))
        today = by_name(self.r)['base']['rows'][0]['implied_pe']
        self.assertEqual(today['next_fiscal_year_end'], '2027-11-30')               # the stub to 2026-11-30 is skipped
        self.assertAlmostEqual(today['value'], 591.4019627958602 / 21.0205, delta=.05)
        # earnings that turn negative: no P/E, the reason said, the value untouched
        cf = deepcopy(self.cf)
        bear = next(s for s in cf['val']['result']['scenarios'] if s['name'] == 'bear')
        nxt = next(p for p in bear['forecast'] if p['end'] == '2028-11-30')
        nxt['ebit'] = -abs(nxt['ebit'])
        row = next(x for x in by_name(SR.build(cf))['bear']['rows'] if x['date'] == '2027-11-30')
        self.assertIsNone(row['implied_pe']['value'])
        self.assertIn('negative', row['implied_pe']['reason'])
        self.assertLess(row['implied_pe']['eps_next_fiscal_year'], 0)
        self.assertNotIn('פי ', row['then_he'])

    def test_mechanical_condition_without_events(self):
        for s in self.r['scenarios']:
            c = s['condition']
            self.assertEqual((c['kind'], c['events']), ('mechanical', []))
            path = [p for p in self.cf['fc']['paths'][s['name']] if p['growth'] is not None]
            self.assertIn(f"{path[0]['growth'] * 100:.1f}%", c['if_he'])
            self.assertIn(f"{path[-1]['growth'] * 100:.1f}%", c['if_he'])
            m = self.cf['fc']['paths'][s['name']]
            self.assertIn(f"{m[0]['operating_margin'] * 100:.1f}%", c['if_he'])
            self.assertIn(f"{m[-1]['operating_margin'] * 100:.1f}%", c['if_he'])
            self.assertIn('בלי מימון חיצוני', c['if_he'])                           # ADBE is self-funded in every scenario
            self.assertIn('שיעור היוון', c['if_he'])
            self.assertEqual(c['drivers']['funding']['status'], self.cf['fc']['funding'][s['name']]['status'])
        bear, bull = by_name(self.r)['bear'], by_name(self.r)['bull']
        self.assertIn('ירד', bear['condition']['if_he'])                             # the bear changes the business
        self.assertIn('יעלה', bull['condition']['if_he'])

    def test_assumption_ids_and_reviews(self):
        base = by_name(self.r)['base']
        spec = next(s for s in self.cf['fc']['spec']['scenarios'] if s['name'] == 'base')
        self.assertEqual(base['assumption_ids']['scenario'], spec['assumption_ids'])
        self.assertEqual(base['assumption_ids']['periods'], spec['period_assumption_ids'])
        rev = self.cf['val']['case']['underwriting']['scenario_reviews']['base']
        self.assertEqual(base['assumption_ids']['scenario_reviews'], {k: v['assumption_ids'] for k, v in sorted(rev.items())})
        self.assertEqual(base['assumption_ids']['review_status']['growth_base'], 'unreviewed')

    def test_owner_table_gets_one_condition_line_per_scenario_and_the_rest_stays_behind(self):
        t = hebrew_table(self.cf)
        lines = t.split('\n')
        for p in NOTE_PREFIX:
            self.assertEqual(sum(1 for x in lines if x.startswith(p)), 1, p)
        for x in lines:
            if x.startswith(NOTE_PREFIX):
                self.assertNotIn('$', x)                                            # the condition only, no price dump
        md = SR.render_he(self.r)
        for s in self.r['scenarios']:
            self.assertIn(f"### {s['label_he']}", md)
            section = md.split(f"### {s['label_he']}")[1].split('### ')[0]
            self.assertEqual(sum(1 for x in section.split('\n') if x.startswith('- ')), len(s['rows']))
        self.assertIn('לא תחזית', md)


class EvidenceAndHypotheses(unittest.TestCase):
    """Events come only from the analyst; without evidence ids the FactBook holds, they are hypotheses, said in Hebrew."""

    EVENTS = {'bear': [{'event_he': 'לקוחות גדולים עוברים לכלי עיצוב מבוססי בינה מלאכותית (TEST)',
                        'evidence_ids': ['fmp:as_reported_income_annual']},
                       {'event_he': 'מחירי המנויים יורדים (TEST)', 'evidence_ids': ['no-such-source']}],
              'bull': [{'event_he': 'מוצרי הבינה המלאכותית מוסיפים הכנסה משלמת (TEST)', 'evidence_ids': []}],
              'tail': [{'event_he': 'not a scenario', 'evidence_ids': []}]}

    @classmethod
    def setUpClass(cls):
        cls.plain = adbe_cf()
        cls.cf = adbe_cf({'analyst_sheet': {'scenario_events': cls.EVENTS}})

    def test_events_are_validated_against_the_factbook_and_hypotheses_are_named(self):
        ev = self.cf['analyst_sheet']['scenario_events']
        self.assertEqual([e['hypothesis'] for e in ev['bear']], [False, True])
        self.assertEqual(ev['bear'][1]['unknown_ids'], ['no-such-source'])
        self.assertTrue(ev['bull'][0]['hypothesis'])                                # no ids at all
        self.assertNotIn('tail', ev)
        self.assertEqual([(x['scenario'], x['code']) for x in self.cf['analyst_sheet']['scenario_events_rejected']],
                         [('tail', 'unknown_scenario')])
        r = by_name(self.cf['scenario_reasons'])
        self.assertEqual(r['bear']['condition']['kind'], 'analyst_events')
        self.assertIn('לקוחות גדולים עוברים', r['bear']['condition']['if_he'])
        self.assertIn('השערה: הראיה no-such-source לא נמצאה בתיק', r['bear']['condition']['if_he'])
        self.assertIn('השערה: אין לו ראיה מתועדת', r['bull']['condition']['if_he'])
        self.assertEqual(r['base']['condition']['kind'], 'mechanical')               # no events: the model's own mechanics
        self.assertIn('ובמודל:', r['bear']['condition']['if_he'])                    # the event is tied to the model's drivers
        md = SR.render_he(self.cf['scenario_reasons'])
        self.assertIn('**התנאי (אירועים מהאנליסט):**', md)
        self.assertIn('- אחד מ־2 האירועים בתנאי הוא השערה בלי ראיה מתועדת בתיק.', md)       # bear: 1 of 2
        self.assertIn('- האירוע בתנאי הוא השערה בלי ראיה מתועדת בתיק.', md)                 # bull: its only event
        self.assertIn('"tail" (צריך bear, base או bull)', md)                                  # the rejected key is said
        table = hebrew_table(self.cf)
        self.assertIn('- דובי = אם לקוחות גדולים עוברים', table)
        self.assertIn('(השערה: אין ראיה מתועדת בתיק)', table)                                  # no raw ids for the owner
        self.assertNotIn('no-such-source', table)

    def test_events_change_no_number(self):
        a, b = self.plain, self.cf
        for k in ('val', 'dc', 'fc', 'rk', 'rv', 'card', 'an', 'qa', 'stages', 'errors', 'business', 'segment_view'):
            self.assertEqual(json.dumps(a[k], sort_keys=True), json.dumps(b[k], sort_keys=True), k)
        for s, t in zip(a['scenario_reasons']['scenarios'], b['scenario_reasons']['scenarios']):
            self.assertEqual([r['value'] for r in s['rows']], [r['value'] for r in t['rows']])

    def test_unvalidated_events_are_checked_against_the_casefile_evidence(self):
        cf = deepcopy(self.plain)
        cf['analyst_sheet']['scenario_events'] = {'base': [{'event_he': 'TEST event', 'evidence_ids': ['ib:snapshot']},
                                                           {'event_he': 'TEST event 2', 'evidence_ids': ['nope']}]}
        r = SR.build(cf)
        ev = by_name(r)['base']['condition']['events']
        self.assertEqual([e['hypothesis'] for e in ev], [False, True])
        self.assertIn('events_unvalidated', [g['code'] for g in by_name(r)['base']['gaps']])
        self.assertEqual(by_name(r)['base']['status'], 'partial')

    def test_analyst_sheet_unchanged_without_the_input(self):
        from fundamental_engine import analyst_sheet as A
        self.assertNotIn('scenario_events', self.plain['analyst_sheet'])
        events, ignored = A.scenario_events({'analyst_sheet': {'scenario_events': {'base': [{'event_he': 'x', 'evidence_ids': ['s1']}]}}},
                                            {'sources': {'s1': {}}, 'qualitative': []})
        self.assertEqual((events['base'][0]['hypothesis'], ignored), (False, []))
        self.assertEqual(A.scenario_events({}, {'sources': {}}), (None, []))


class FundingInTheCondition(unittest.TestCase):
    def test_assumed_external_financing_is_named_in_the_condition_and_the_note(self):
        cf = adbe_cf()
        cf['fc']['funding']['bear'].update(status='assumed_external_financing', equity_raised=2e9, debt_assumed=5e8,
                                           new_financing_shares=19.46e6)                 # TEST values on a copy
        bear = by_name(SR.build(cf))['bear']
        self.assertIn('מימון חיצוני שהמודל מניח ועוד לא הובטח', bear['condition']['if_he'])
        self.assertIn('גיוס הון של כ־2.0 מיליארד$, כ־5.0% מניות חדשות', bear['condition']['if_he'])
        self.assertIn('חוב חדש של כ־500 מיליון$', bear['condition']['if_he'])
        self.assertIn('ובמימון חיצוני שעוד לא הובטח', bear['note_he'])


class MissingAndHeld(unittest.TestCase):
    def test_missing_data_becomes_gaps_not_numbers(self):
        cf = synthetic('attractive')                                     # fc = {'assumptions': []}: no paths, anchors or funding
        r = SR.build(cf)
        self.assertEqual(r['status'], 'partial')
        base = by_name(r)['base']
        codes = {g['code'] for g in base['gaps']}
        self.assertTrue({'no_forecast_paths', 'current_year_revenue', 'growth', 'funding'} <= codes, codes)
        today = base['rows'][0]
        self.assertIsNone(today['drivers']['revenue'])                     # only a stub of the current year: not filled in
        first_full = next(x for x in base['rows'] if x['fiscal_year']['full_year'])
        self.assertIsNone(first_full['drivers']['revenue_growth'])        # no previous full year to grow from
        self.assertEqual(base['condition']['drivers']['growth_first']['source'], 'executed forecast rows')
        from fundamental_engine.render import yearly_table_rows
        yr = [y for _, y in yearly_table_rows(cf['dc']['expected_return'])]
        self.assertEqual([x['value'] for x in base['rows']], [y['base'] for y in yr])
        self.assertIn('חסר בתיק', SR.render_he(r))
        # without the yearly table: the dated values of the executed valuation, through the fifth anniversary
        del cf['dc']['expected_return']['yearly_at_model_cost_of_equity']
        r2 = SR.build(cf)
        self.assertEqual(r2['source']['values'], 'val.result.annual_values')
        self.assertIn('yearly_rows', [g['code'] for g in r2['gaps']])
        five = cf['dc']['valuation_view']['five_year']['date']
        av = [a for a in cf['val']['result']['annual_values'] if a['date'] <= five]
        av = [a for _, a in yearly_table_rows({'yearly_at_model_cost_of_equity': {'rows': av}})]   # today and years 1-5
        self.assertEqual([x['value'] for x in by_name(r2)['bull']['rows']], [a['bull'] for a in av])

    def test_held_or_unavailable_valuation_shows_no_value(self):
        for name in ('demo', 'blocked', 'grower'):
            cf = synthetic(name)
            r = SR.build(cf)
            self.assertEqual(r['status'], 'not_available', name)
            self.assertFalse(r['values_shown'])
            self.assertEqual(r['why_no_values_he'], value_display(cf)['reason_he'])
            for s in r['scenarios']:
                self.assertEqual((s['rows'], s['condition'], s['note_he']), ([], None, None))
            md = SR.render_he(r)
            self.assertIn('אין שווי להציג', md)
            self.assertNotIn('$', md)
            self.assertFalse(any(x.startswith(NOTE_PREFIX) for x in hebrew_table(cf).split('\n')))
        self.assertIn('הדגמה', SR.build(synthetic('demo'))['why_no_values_he'])

    def test_empty_casefile(self):
        r = SR.build({})
        self.assertEqual((r['status'], r['values_shown']), ('not_available', False))
        self.assertIn('אין שווי להציג', SR.render_he(r))


class DeterminismAndInvariance(unittest.TestCase):
    def test_same_input_same_output_and_the_casefile_is_not_touched(self):
        for cf in (adbe_cf(), synthetic('attractive')):
            before = json.dumps(cf, sort_keys=True)
            a, b = SR.build(cf), SR.build(json.loads(json.dumps(cf)))
            self.assertEqual(json.dumps(a, sort_keys=False, allow_nan=False), json.dumps(b, sort_keys=False, allow_nan=False))
            self.assertEqual(json.dumps(cf, sort_keys=True), before)
            self.assertEqual(SR.render_he(a), SR.render_he(b))
        cf = adbe_cf()
        stored = cf.pop('scenario_reasons')
        self.assertEqual(json.dumps(SR.build(cf)), json.dumps(stored))   # the saved casefile reproduces its own reasons

    def test_pipeline_outputs_are_identical_without_the_reasons(self):
        """Adding casefile['scenario_reasons'] changes no other key: the same run with the builder failing on purpose
        differs only in that key and the hash that covers it; the owner's table differs only by the condition lines."""
        with_ = adbe_cf()
        with mock.patch.object(SR, 'build', side_effect=ValueError('switched off for the invariance test')):
            without = json.loads(json.dumps(pipeline.run(deepcopy(ADBE)), default=str))
        self.assertEqual(without['scenario_reasons']['status'], 'failed')
        self.assertEqual(set(with_), set(without))
        for k in with_:
            if k not in ('scenario_reasons', 'casefile_sha256'):
                self.assertEqual(json.dumps(with_[k], sort_keys=True), json.dumps(without[k], sort_keys=True), k)
        self.assertNotIn('scenario_reasons', with_['stages'])                  # not a stage: 'stages' is unchanged
        t_with, t_without = hebrew_table(with_), hebrew_table(without)
        self.assertEqual([x for x in t_with.split('\n') if not x.startswith(NOTE_PREFIX)], t_without.split('\n'))
        self.assertEqual(hebrew_paragraph(with_), hebrew_paragraph(without))


class CommandsAndTool(unittest.TestCase):
    def _main(self, argv):
        from fundamental_engine import cli
        out = io.StringIO()
        with mock.patch.object(sys, 'argv', ['fundamental'] + argv), contextlib.redirect_stdout(out):
            cli.main()
        return out.getvalue()

    def test_pipeline_writes_the_reasons_and_the_command_shows_them(self):
        cf = adbe_cf()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(pipeline, 'run', lambda fb, inp=None: deepcopy(cf)):
            self._main(['pipeline', '--factbook', str(ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json'), '--index', 'none',
                        '--out', d])
            saved = json.loads(Path(d, 'casefile.json').read_text(encoding='utf-8'))
            md = Path(d, 'scenarios_he.md').read_text(encoding='utf-8')
            table = Path(d, 'table_he.md').read_text(encoding='utf-8')
            self.assertEqual(saved['scenario_reasons'], cf['scenario_reasons'])
            self.assertEqual(md, SR.render_he(cf['scenario_reasons']))
            self.assertTrue(any(x.startswith('- בסיסי = אם') for x in table.split('\n')))
            text = self._main(['scenario-reasons', str(Path(d, 'casefile.json'))])
            self.assertEqual(text.strip(), SR.render_he(SR.build(saved)).strip())
            full = json.loads(self._main(['scenario-reasons', str(Path(d, 'casefile.json')), '--json']))
            self.assertEqual(full['status'], 'computed')

    def test_deep_writes_the_reasons_next_to_the_table(self):
        from fundamental_engine import handoff
        cf = adbe_cf()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'companies', 'ADBE'))
            with mock.patch.object(handoff, 'run_company', lambda folder, as_of, inputs=None: (deepcopy(ADBE), deepcopy(cf))):
                self._main(['deep', d, '--as-of', '2026-10-19', '--symbols', 'ADBE', '--index', 'none'])
            out = Path(d, 'companies', 'ADBE', 'out')
            self.assertEqual(out.joinpath('scenarios_he.md').read_text(encoding='utf-8'), SR.render_he(cf['scenario_reasons']))
            saved = json.loads(out.joinpath('casefile.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['scenario_reasons']['status'], 'computed')
            self.assertIn('| שנה | דובי | בסיסי | שורי |', out.joinpath('table_he.md').read_text(encoding='utf-8'))

    def test_mcp_tool(self):
        from fundamental_engine.mcp import Server, TOOLS
        self.assertIn('scenario_reasons', {t['name'] for t in TOOLS})
        cf = adbe_cf()
        res = Server(':memory:').call('scenario_reasons', {'casefile': cf})
        self.assertEqual(json.dumps(res['reasons']), json.dumps(SR.build(cf)))
        self.assertIn('### בסיסי', res['text_he'])

    def test_brain_packets_carry_the_rule(self):
        from fundamental_engine.supervisor import packet
        for topic in ('annual_valuation', 'plain_language', 'master_process'):
            text = packet(topic)['content']
            self.assertIn('scenario_reasons', text, topic)
            self.assertIn('דובי', text, topic)
        self.assertIn('hypothes', packet('annual_valuation')['content'])
        self.assertNotIn('bad/good', packet('plain_language')['content'])


def scale_up(as_of, price=95.):
    """The SYNTHETIC scale-up platform of tests/test_pipeline (December fiscal year, FY2021-FY2025 reported by 20 January),
    valued on any date after the last report: a valuation date just after the fiscal year end leaves a 0.9-1.0 year stub."""
    from fundamental_engine import facts as F
    rev, om, sbc = [17.5e9, 31.9e9, 37.3e9, 44.0e9, 52.0e9], [-.22, -.06, .03, .06, .11], [.09, .06, .05, .043, .035]
    fb = F.new({'ticker': 'UBX', 'name': 'Synthetic travel platform', 'currency': 'USD', 'fiscal_year_end': '12-31',
                'sector': 'Industrials', 'industry': 'Travel Services'}, as_of)
    F.add_source(fb, 'syn:fs', 'synthetic', 'test-statements', 'filing_derived', as_of + 'T00:00:00Z', note='SYNTHETIC')
    F.add_source(fb, 'syn:px', 'synthetic', 'test-price', 'market_feed', as_of + 'T00:00:00Z')
    for i, (r, m, sb) in enumerate(zip(rev, om, sbc)):
        end, avail = f'{2021 + i}-12-31', f'{2022 + i}-01-20'
        oi = r * m
        flows = {'revenue': r, 'gross_profit': r * .4, 'cost_of_revenue': r * .6, 'operating_income': oi, 'sbc': r * sb,
                 'd_and_a': r * .015, 'capex': r * .005, 'cfo': oi + r * sb + r * .015, 'pretax_income': oi,
                 'income_tax': max(oi, 0) * .21, 'net_income': oi - max(oi, 0) * .21, 'interest_expense': 5e8}
        for k, v in flows.items():
            F.add_fact(fb, k, 'FY' + end[:4], end, v, 'syn:fs', 'as_reported', avail, period_start=f'{2021 + i}-01-01')
        c = 9e9 * r / rev[-1]
        inst = {'cash': c, 'equity': r * .6 + c - 10e9, 'long_term_debt': 10e9, 'total_assets': r * .9 + c,
                'total_liabilities': r * .3 + 10e9, 'receivables': r * .15, 'payables': r * .05, 'shares_outstanding': 2.1e9,
                'current_assets': c + r * .2, 'current_liabilities': r * .25, 'ppe': r * .05}
        for k, v in inst.items():
            F.add_fact(fb, k, 'I@' + end, end, v, 'syn:fs', 'as_reported', avail)
    F.set_price(fb, price, as_of, 'syn:px')
    return fb


MARKET = {'forecast': {'risk_free': {'value': .0515, 'as_of': '2025-12-01', 'title': 'test', 'retrieval': 'provider_normalized'},
                       'equity_risk_premium': {'value': .042, 'as_of': '2025-12-01', 'title': 'test',
                                               'retrieval': 'provider_normalized'}}}


class CurrentFiscalYearJustAfterTheYearEnd(unittest.TestCase):
    """Review fix: a valuation date 1-36 days after the fiscal year end leaves a 0.9-1.0 year stub; it is the current fiscal
    year (anchor revenue, no EPS), never 'the next fiscal year' of the implied P/E, and raises no false growth gap."""

    @classmethod
    def setUpClass(cls):
        cls.cf = json.loads(json.dumps(pipeline.run(scale_up('2026-01-25'), deepcopy(MARKET)), default=str))
        cls.r = cls.cf['scenario_reasons']

    def test_the_stub_is_the_current_fiscal_year_and_the_pe_uses_the_next_one(self):
        stub = self.cf['fc']['anchors']['stub']
        self.assertTrue(.9 <= stub['years'] < 1)                                         # the case the old rule misread
        self.assertTrue(self.r['values_shown'])
        anchor = self.cf['fc']['anchors']['current_fiscal_year']
        for s in self.r['scenarios']:
            today = s['rows'][0]
            self.assertEqual(today['fiscal_year'], {'start': '2026-01-25', 'end': '2026-12-31', 'full_year': False})
            self.assertEqual(today['drivers']['revenue'], anchor['revenue'])            # the model full year, not the stub
            self.assertIsNone(today['drivers']['eps'])
            pe = today['implied_pe']
            self.assertEqual((pe['next_fiscal_year_start'], pe['next_fiscal_year_end']), ('2026-12-31', '2027-12-31'))
            fy27 = next(p for p in next(x for x in self.cf['val']['result']['scenarios'] if x['name'] == s['name'])['forecast']
                        if p['end'] == '2027-12-31')
            self.assertAlmostEqual(pe['eps_next_fiscal_year'], eps_of(fy27), places=4)
            if eps_of(fy27) > 0:
                self.assertAlmostEqual(pe['value'], round(today['value'] / eps_of(fy27), 2), places=6)
            self.assertNotIn('growth', [g['code'] for g in s['gaps']])                  # the current-year anchor is held
        self.assertNotIn('היום (שנת הכספים', SR.render_he(self.r))

    def test_a_first_period_that_starts_on_the_year_end_is_a_full_year(self):
        rows = [{'start': '2025-12-31', 'end': '2026-12-31', 'revenue': 1.}, {'start': '2026-12-31', 'end': '2027-12-31', 'revenue': 1.}]
        self.assertEqual([p['_full'] for p in SR._fiscal_years(rows, set(), '2025-12-31', None)], [True, True])
        late = [{'start': '2026-01-05', 'end': '2026-12-31', 'revenue': 1.}, {'start': '2026-12-31', 'end': '2027-12-31', 'revenue': 1.}]
        self.assertEqual([p['_full'] for p in SR._fiscal_years(late, set(), '2026-01-05', None)], [False, True])
        self.assertEqual([p['_full'] for p in SR._fiscal_years(late, set(), '2026-01-05', '2026-01-05')], [True, True])


class ResidualIncomeRoute(unittest.TestCase):
    """Review fix: a bank (residual income) gets its own drivers: the ROE path, payout and book value per share, earnings =
    model net income / shares; three different values never share one condition. The synthetic bank is demo data, so the
    owner's table would hold it; value_display is opened here to read the route."""

    @classmethod
    def setUpClass(cls):
        import test_forecast_financials as TF
        from fundamental_engine import render
        cls.cf = json.loads(json.dumps(pipeline.run(TF.bank()), default=str))
        real = render.value_display
        with mock.patch.object(render, 'value_display', lambda c: dict(real(c), show=True, reason_he=None)):
            cls.r = SR.build(cls.cf)

    def test_conditions_follow_the_roe_path_and_differ(self):
        self.assertEqual(self.r['method'], 'residual_income')
        conds = [s['condition']['if_he'] for s in self.r['scenarios']]
        self.assertEqual(len(set(conds)), 3)
        for s in self.r['scenarios']:
            path = self.cf['fc']['paths'][s['name']]
            self.assertIn(f"{path[0]['roe'] * 100:.1f}%", s['condition']['if_he'])
            self.assertIn(f"{path[-1]['roe'] * 100:.1f}%", s['condition']['if_he'])
            self.assertIn('התשואה על ההון העצמי', s['note_he'])
            self.assertNotIn('condition_not_distinct', [g['code'] for g in s['gaps']])
            self.assertNotIn('no_forecast_paths', [g['code'] for g in s['gaps']])
        self.assertIn('תרד מכ־12.5%', by_name(self.r)['bear']['condition']['if_he'])          # the bear changes the business

    def test_eps_is_net_income_per_share_and_the_merged_year_sums_its_parts(self):
        for s in self.r['scenarios']:
            raw = next(x for x in self.cf['val']['result']['scenarios'] if x['name'] == s['name'])['forecast']
            row = next(x for x in s['rows'] if x['date'] == '2027-12-31')
            fy = next(p for p in raw if p['end'] == '2027-12-31')
            self.assertAlmostEqual(row['drivers']['eps'], fy['net_income'] / fy['shares'], places=4)
            self.assertAlmostEqual(row['drivers']['book_value_per_share'], fy['book_equity'] / fy['shares'], places=4)
            nxt = next(p for p in raw if p['end'] == '2028-12-31')
            self.assertAlmostEqual(row['implied_pe']['value'], round(row['value'] / (nxt['net_income'] / nxt['shares']), 2), places=6)
            five = s['rows'][-1]                                       # the fifth anniversary inside FY2031: two model periods
            parts = [p for p in raw if p['start'] >= '2030-12-31' and p['end'] <= '2031-12-31']
            self.assertEqual(len(parts), 2)
            self.assertAlmostEqual(five['drivers']['eps'], sum(p['net_income'] for p in parts) / parts[-1]['shares'], places=4)
        self.assertIn('אומדן רווח למניה = הרווח הנקי במודל', SR.render_he(self.r))

    def test_a_route_that_is_not_read_says_so_and_identical_conditions_are_flagged(self):
        cf = adbe_cf()
        cf['val']['case']['method'] = 'nav'                                # TEST: a route this module does not break down
        r = SR.build(cf)
        for s in r['scenarios']:
            self.assertEqual((s['condition']['kind'], s['condition']['if_he'], s['note_he']), ('route_not_read', None, None))
            self.assertIn('route_drivers', [g['code'] for g in s['gaps']])
        scen = [{'name': n, 'status': 'computed', 'gaps': [], 'condition': {'if_he': 'אם X.'}} for n in SR.SCENARIOS]
        SR._distinct(scen, [{'date': 'd', 'bear': 1., 'base': 2., 'bull': 2.}])
        self.assertEqual([[g['code'] for g in x['gaps']] for x in scen], [['condition_not_distinct']] * 3)
        self.assertEqual({x['status'] for x in scen}, {'partial'})


class ReviewWording(unittest.TestCase):
    """Review fixes in the Hebrew: dated growth in the note, executed growth in the rows, losses named, future tense,
    the earnings estimate labelled, the long-run rate, readable gaps and hypotheses, no -0.0."""

    @classmethod
    def setUpClass(cls):
        cls.cf = adbe_cf()
        cls.r = cls.cf['scenario_reasons']
        cls.md = SR.render_he(cls.r)

    def test_rows_show_executed_growth_and_the_terminal_rate_is_named(self):
        base = by_name(self.r)['base']
        raw = {p['end']: p for p in next(x for x in self.cf['val']['result']['scenarios'] if x['name'] == 'base')['forecast']}
        row = next(x for x in base['rows'] if x['date'] == '2028-11-30')
        executed = raw['2028-11-30']['revenue'] / raw['2027-11-30']['revenue'] - 1
        self.assertAlmostEqual(row['drivers']['revenue_growth'], executed, places=6)
        self.assertIn(f'(+{executed * 100:.1f}%)', row['then_he'])
        tk = self.cf['fc']['cost_of_equity']['terminal_cost_of_equity']['base']
        self.assertEqual(base['condition']['drivers']['terminal_cost_of_equity']['value'], round(tk, 6))
        self.assertIn(f'בשנים המפורשות וכ־{tk * 100:.1f}% לטווח הארוך', base['condition']['if_he'])

    def test_notes_name_the_year_and_the_end_date(self):
        for cf in (self.cf, synthetic('attractive')):
            for s in SR.build(cf)['scenarios']:
                d = s['condition']['drivers']
                self.assertNotIn('בשנה הראשונה', s['note_he'])
                self.assertIn(f"בשנה שמסתיימת ב־{d['growth_first']['fiscal_year_end'][:7].replace('-', '/')}", s['note_he'])
                self.assertIn(f"עד {d['growth_last']['fiscal_year_end'][:7].replace('-', '/')}", s['note_he'])

    def test_losses_and_a_return_to_growth_read_as_such(self):
        d = {'growth_first': {'value': -.05, 'fiscal_year_end': '2027-12-31'}, 'growth_last': {'value': .03, 'fiscal_year_end': '2035-12-31'},
             'operating_margin_first': {'value': -.2, 'fiscal_year_end': '2026-12-31'},
             'operating_margin_last': {'value': .1, 'fiscal_year_end': '2035-12-31'}, 'cost_of_equity': {'value': .1}}
        c = SR._condition(d, [], 'USD', 1e9, 'operating', True)
        for text in (c['if_he'], c['short_he']):
            self.assertIn('ואחר כך יחזרו לצמוח', text)
            self.assertNotIn('תאיץ', text)
            self.assertIn('מהפסד תפעולי', text)
            self.assertIn('לרווח תפעולי של כ־10.0%', text)
            self.assertNotIn('כ־-', text)
        d['operating_margin_last']['value'] = -.05
        self.assertIn('וההפסד התפעולי יקטן מכ־20.0% מההכנסות לכ־5.0%', SR._condition(d, [], 'USD', 1e9, 'operating', True)['short_he'])
        cf = deepcopy(self.cf)                                             # an executed operating loss in a row
        bear = next(s for s in cf['val']['result']['scenarios'] if s['name'] == 'bear')
        fy = next(p for p in bear['forecast'] if p['end'] == '2028-11-30')
        fy['ebit'] = -abs(fy['ebit'])
        row = next(x for x in by_name(SR.build(cf))['bear']['rows'] if x['date'] == '2028-11-30')
        self.assertIn('הפסד תפעולי', row['then_he'])
        self.assertNotIn('רווח תפעולי -', row['then_he'])

    def test_future_tense_estimate_label_and_rounding(self):
        self.assertIn('עד אז יחולקו כ־', self.md)
        self.assertNotIn('עד אז חולקו', self.md)
        self.assertIn('לא רווח GAAP', self.md)
        for line in self.md.split('\n'):
            if '(פי ' in line:                         # operating route: named apart from the yearly price table's EPS
                self.assertIn('על הרווח התפעולי למניה של השנה הבאה', line)
            self.assertNotIn(' רווח למניה כ־', line.replace('רווח תפעולי למניה כ־', ''))
        self.assertIn('as a whole number', self.r['rounding'])
        self.assertEqual(str(SR._r(-1e-9, 4)), '0.0')
        def zeros(x):
            if isinstance(x, dict):
                return [z for v in x.values() for z in zeros(v)]
            if isinstance(x, list):
                return [z for v in x for z in zeros(v)]
            return [x] if isinstance(x, float) and x == 0 and math.copysign(1, x) < 0 else []
        self.assertEqual(zeros(self.r), [])                                          # no negative zero in any number

    def test_gaps_read_as_plain_nouns_and_hypotheses_agree_in_number(self):
        md = SR.render_he(SR.build(synthetic('attractive')))
        line = next(x for x in md.split('\n') if x.startswith('- חסר בתיק:'))
        self.assertNotIn('אין בתיק', line)
        self.assertIn('צמיחה לשנה שמסתיימת ב־2027/12', line)
        self.assertIn('הכנסות של כל השנה הנוכחית (עד 2026/12)', line)
        for he in SR.GAP_HE.values():
            self.assertNotIn(';', he)                                     # ';' separates the items
        ev = lambda h: {'event_he': 'x', 'hypothesis': h}
        self.assertEqual(SR._hyp_he([ev(True)]), 'האירוע בתנאי הוא השערה בלי ראיה מתועדת בתיק.')
        self.assertEqual(SR._hyp_he([ev(True), ev(False)]), 'אחד מ־2 האירועים בתנאי הוא השערה בלי ראיה מתועדת בתיק.')
        self.assertEqual(SR._hyp_he([ev(True), ev(True)]), 'כל 2 האירועים בתנאי הם השערות בלי ראיה מתועדת בתיק.')
        self.assertEqual(SR._hyp_he([ev(True), ev(True), ev(False)]), '2 מתוך 3 האירועים בתנאי הם השערות בלי ראיה מתועדת בתיק.')
        cf = deepcopy(self.cf)
        cf['analyst_sheet']['scenario_events'] = {'base': [{'event_he': 'TEST', 'evidence_ids': ['nope-1', 'nope-2'],
                                                            'unknown_ids': ['nope-1', 'nope-2'], 'hypothesis': True}]}
        self.assertIn('השערה: הראיות nope-1, nope-2 לא נמצאו בתיק', by_name(SR.build(cf))['base']['condition']['if_he'])


class TableRowsAndUnfundedScenario(unittest.TestCase):
    """Review fix: dated values only for the dates the owner's yearly table shows; with one scenario unvalued the table has
    no yearly part, so the reasons list no dated price and say so, and the unvalued scenario gets no discount-rate clause."""

    def test_one_scenario_without_values(self):
        cf = adbe_cf()
        cf.pop('scenario_reasons')
        for row in cf['dc']['expected_return']['yearly_at_model_cost_of_equity']['rows']:
            row['bear'] = None                                             # TEST: the shape of a funding-blocked bear
        next(s for s in cf['val']['result']['scenarios'] if s['name'] == 'bear')['status'] = 'funding_blocked'
        cf['fc']['funding']['bear']['status'] = 'unfunded'
        r = SR.build(cf)
        self.assertTrue(r['values_shown'])
        self.assertFalse(r['yearly_table'])
        self.assertEqual([len(s['rows']) for s in r['scenarios']], [0, 0, 0])
        self.assertIn('no_yearly_table', [g['code'] for g in r['gaps']])
        bear = by_name(r)['bear']
        self.assertIn('ולכן לתרחיש הזה אין שווי', bear['condition']['if_he'])
        self.assertNotIn('שיעור היוון', bear['condition']['if_he'])
        self.assertIn('שיעור היוון', by_name(r)['base']['condition']['if_he'])
        md = SR.render_he(r)
        self.assertNotIn('כל מחיר בטבלה השנתית', md)
        self.assertIn('בטבלת השווי אין טבלה שנתית', md)
        self.assertNotIn('$', md)
        self.assertNotIn('| מועד |', hebrew_table(dict(cf, scenario_reasons=r)))
        from fundamental_engine.render import yearly_table_rows
        ok = adbe_cf()
        dates = [row['date'] for _, row in yearly_table_rows(ok['dc']['expected_return'])]
        for s in ok['scenario_reasons']['scenarios']:
            self.assertEqual([row['date'] for row in s['rows']], dates)


class MalformedEventsAndInput(unittest.TestCase):
    """Review fixes: evidence ids of any shape never crash the analyst sheet; a single id as text is one id; whatever cannot
    be read is listed and said, never dropped silently; a malformed casefile is invalid input, not an operator failure."""

    FB = {'sources': {'s1': {}}, 'qualitative': []}

    def ev(self, raw):
        from fundamental_engine import analyst_sheet as A
        return A.scenario_events({'analyst_sheet': {'scenario_events': raw}}, self.FB)

    def test_every_bad_shape_is_rejected_with_a_reason(self):
        cases = {'bear_as_text': ({'bear': 'TEST'}, ('bear', None, 'not_list')),
                 'event_as_text': ({'bull': ['TEST']}, ('bull', 0, 'event_not_object')),
                 'wrong_field': ({'bear': [{'text_he': 'TEST', 'evidence_ids': ['s1']}]}, ('bear', 0, 'unknown_keys')),
                 'no_text': ({'bear': [{'evidence_ids': ['s1']}]}, ('bear', 0, 'no_event_he')),
                 'typo_ids': ({'bear': [{'event_he': 'TEST', 'evidence_id': ['s1']}]}, ('bear', 0, 'unknown_keys')),
                 'unknown_key': ({'Bear': [{'event_he': 'TEST', 'evidence_ids': ['s1']}]}, ('Bear', None, 'unknown_scenario')),
                 'ids_int': ({'bear': [{'event_he': 'TEST', 'evidence_ids': 5}]}, ('bear', 0, 'bad_evidence_ids')),
                 'ids_true': ({'bear': [{'event_he': 'TEST', 'evidence_ids': True}]}, ('bear', 0, 'bad_evidence_ids')),
                 'not_object': (['TEST'], (None, None, 'not_object'))}
        for name, (raw, want) in cases.items():
            events, rejected = self.ev(raw)
            self.assertEqual(events, {}, name)
            self.assertEqual([(x['scenario'], x['index'], x['code']) for x in rejected], [want], name)

    def test_a_single_id_as_text_is_one_id(self):
        events, rejected = self.ev({'bear': [{'event_he': 'TEST', 'evidence_ids': 's1'}]})
        self.assertEqual((events['bear'][0]['evidence_ids'], events['bear'][0]['hypothesis'], rejected), (['s1'], False, []))
        events, rejected = self.ev({'bear': [{'event_he': 'TEST', 'evidence_ids': ['s1', 7]}]})
        self.assertEqual(events['bear'][0]['evidence_ids'], ['s1'])
        self.assertEqual([x['code'] for x in rejected], ['evidence_ids_items_dropped'])
        from fundamental_engine.analyst_sheet import evidence_id_list
        self.assertEqual(evidence_id_list('fmp:as_reported_income_annual'), (['fmp:as_reported_income_annual'], None))

    def test_bad_events_leave_the_sheet_and_the_risk_row_and_are_said(self):
        kr = {'reading_he': 'TEST risk reading', 'evidence_ids': ['fmp:as_reported_income_annual']}
        cf = adbe_cf({'analyst_sheet': {'key_risk': kr, 'scenario_events': {'bear': [{'event_he': 'TEST', 'evidence_ids': 5}],
                                                                             'Bull': [{'event_he': 'TEST', 'evidence_ids': []}]}}})
        self.assertEqual(cf['stages']['analyst_sheet'], 'ok')
        self.assertFalse([e for e in cf['errors'] if e['stage'] == 'analyst_sheet'])
        self.assertEqual(cf['analyst_sheet']['key_risk']['reading_he'], 'TEST risk reading')
        self.assertIn('TEST risk reading', hebrew_table(cf))
        r = cf['scenario_reasons']
        self.assertIn('events_rejected', [g['code'] for g in by_name(r)['bear']['gaps']])
        self.assertIn('events_rejected', [g['code'] for g in r['gaps']])               # 'Bull' is not a scenario name
        self.assertEqual(r['status'], 'partial')
        md = SR.render_he(r)
        self.assertIn('אירועים שהאנליסט סיפק לא נקראו', md)
        self.assertIn('ל־evidence_ids של אירוע 1 יש סוג לא תקין', md)
        self.assertIn('"Bull"', md)
        plain = adbe_cf()
        for k in ('val', 'dc', 'fc'):
            self.assertEqual(json.dumps(cf[k], sort_keys=True), json.dumps(plain[k], sort_keys=True), k)

    def test_text_id_from_the_pipeline_is_evidence_not_characters(self):
        cf = adbe_cf({'analyst_sheet': {'scenario_events': {'bear': [{'event_he': 'TEST event',
                                                                      'evidence_ids': 'fmp:as_reported_income_annual'}]}}})
        e = by_name(cf['scenario_reasons'])['bear']['condition']['events'][0]
        self.assertEqual((e['evidence_ids'], e['unknown_ids'], e['hypothesis']), (['fmp:as_reported_income_annual'], [], False))

    def test_malformed_casefile_is_invalid_input(self):
        from fundamental_engine.mcp import Server
        for bad in ({'dc': 'x'}, {'val': {'result': {'scenarios': 'x'}}}, 5):
            with self.assertRaises(ValueError):
                SR.build(bad)
        server = Server(':memory:')
        server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
        server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        out = server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                             'params': {'name': 'scenario_reasons', 'arguments': {'casefile': {'dc': 'x'}}}})
        self.assertTrue(out['result']['isError'])
        self.assertIn('Invalid research input', out['result']['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
