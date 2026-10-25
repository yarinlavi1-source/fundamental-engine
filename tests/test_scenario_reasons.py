"""Scenario reasons (25.10.2026): bear/base/bull in the owner's words (דובי / בסיסי / שורי) and, behind the scenes, the
condition and the business of every dated scenario price, read from the executed casefile and never revalued.

The ADBE FactBook is the real fixture; its forecast is the engine's own mechanical draft, not an underwritten view of Adobe.
The FICTSAAS casefiles (tests/test_decision_render) are synthetic and carry no forecast paths, on purpose: missing data must
become a gap, never a number. Scenario events in these tests are TEST text, not research."""
import contextlib
import io
import json
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
            self.assertIn('| מועד | דובי | בסיסי | שורי |', t)
            self.assertIn('| שווי הוגן (תרחיש בסיסי) |', t)
            self.assertIn('| דובי / שורי |', t)
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
        cls.yearly = cls.cf['dc']['expected_return']['yearly_at_model_cost_of_equity']['rows']
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
        self.assertEqual(row['drivers']['revenue'], fy['revenue'])
        self.assertAlmostEqual(row['drivers']['revenue_growth'], path['growth'], places=6)
        self.assertAlmostEqual(row['drivers']['operating_margin_after_sbc'], fy['ebit'] / fy['revenue'], places=6)
        self.assertAlmostEqual(row['drivers']['eps'], eps_of(fy), places=4)
        self.assertAlmostEqual(row['drivers']['fcf_per_share'],
                               (fy['cfo'] - fy['maintenance_capex'] - fy['growth_capex']) / ((fy['shares_open'] + fy['shares']) / 2), places=4)
        self.assertEqual(row['drivers']['diluted_shares'], fy['shares'])
        today = base['rows'][0]
        anchor = self.cf['fc']['anchors']['current_fiscal_year']
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
        self.assertEqual(self.cf['analyst_sheet']['scenario_events_ignored'], ['tail'])
        r = by_name(self.cf['scenario_reasons'])
        self.assertEqual(r['bear']['condition']['kind'], 'analyst_events')
        self.assertIn('לקוחות גדולים עוברים', r['bear']['condition']['if_he'])
        self.assertIn('השערה: הראיה no-such-source לא נמצאה בתיק', r['bear']['condition']['if_he'])
        self.assertIn('השערה: אין לו ראיה מתועדת', r['bull']['condition']['if_he'])
        self.assertEqual(r['base']['condition']['kind'], 'mechanical')               # no events: the model's own mechanics
        self.assertIn('ובמודל:', r['bear']['condition']['if_he'])                    # the event is tied to the model's drivers
        md = SR.render_he(self.cf['scenario_reasons'])
        self.assertIn('**התנאי (אירועים מהאנליסט):**', md)
        self.assertIn('השערה בלי ראיה מתועדת', md)
        self.assertIn('- דובי = אם לקוחות גדולים עוברים', hebrew_table(self.cf))

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
        yr = cf['dc']['expected_return']['yearly_at_model_cost_of_equity']['rows']
        self.assertEqual([x['value'] for x in base['rows']], [y['base'] for y in yr])
        self.assertIn('חסר בתיק', SR.render_he(r))
        # without the yearly table: the dated values of the executed valuation, through the fifth anniversary
        del cf['dc']['expected_return']['yearly_at_model_cost_of_equity']
        r2 = SR.build(cf)
        self.assertEqual(r2['source']['values'], 'val.result.annual_values')
        self.assertIn('yearly_rows', [g['code'] for g in r2['gaps']])
        five = cf['dc']['valuation_view']['five_year']['date']
        av = [a for a in cf['val']['result']['annual_values'] if a['date'] <= five]
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
            self.assertIn('| מועד | דובי | בסיסי | שורי |', out.joinpath('table_he.md').read_text(encoding='utf-8'))

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


if __name__ == '__main__':
    unittest.main()
