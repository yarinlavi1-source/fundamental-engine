"""Review fixes of the company-first ticker path (25.10.2026), end to end on the SYNTHETIC demo company
(tests/fixtures/demo_company: invented statements and price; examples/evidence_pack_demo.json: invented evidence):
ONE yearly table on the owner's table, the reason behind every company-path price in scenarios_he.md, the paragraph's
five-year figure equal to the table's, the guided year and the next year kept apart, market feeds citable on their date,
the pack's tier rules, forecast_options checked like every other block, an evidenced after-SBC margin executed as given,
every engine constant that sets a number listed as a gap, the scenario-order clamp reported, the pack's falsifiers frozen."""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from fundamental_engine import handoff, facts as F, forecast  # noqa: E402
from fundamental_engine.evidence_pack import to_inputs, to_bundle, merge_fragments  # noqa: E402
from fundamental_engine.render import hebrew_table, hebrew_paragraph, markdown  # noqa: E402
from fundamental_engine.scenario_reasons import render_he  # noqa: E402
from fundamental_engine.monitor import thesis_from_casefile, check  # noqa: E402
from fundamental_engine.analyst_sheet import scenario_events  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO = json.loads((ROOT / 'examples' / 'evidence_pack_demo.json').read_text(encoding='utf-8'))
FIXTURE = ROOT / 'tests' / 'fixtures' / 'demo_company'
HEAD = '| שנה | דובי | בסיסי | שורי |'


def pack():
    return copy.deepcopy(DEMO)


def refused(test, p, fragment):
    with test.assertRaises(ValueError) as e:
        to_inputs(p)
    test.assertIn(fragment, str(e.exception))


def run(p, mutate=None):
    """pack -> inputs + bundle -> the synthetic demo company folder -> handoff.run_company (the path `deep` takes)."""
    inputs, bundle = to_inputs(p), to_bundle(p)
    if mutate:
        mutate(inputs)
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(FIXTURE, os.path.join(d, 'DEMO'))
        return handoff.run_company(os.path.join(d, 'DEMO'), p['as_of'], inputs=inputs, qualitative=bundle)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fb, cls.cf = run(pack())
        cls.table = hebrew_table(cls.cf)
        cls.reasons = render_he(cls.cf['scenario_reasons'])
        cls.para = hebrew_paragraph(cls.cf)

    def test_every_stage_ran(self):
        self.assertEqual({k: v for k, v in self.cf['stages'].items() if v != 'ok'}, {})
        self.assertEqual(self.cf['errors'], [])
        self.assertEqual(self.cf['company_path']['status'], 'computed')

    def test_one_yearly_table_years_one_to_five_on_the_anniversaries(self):
        t = self.table
        self.assertEqual(t.count(HEAD), 1)
        self.assertEqual(t.count('|---|---|---|---|'), 1)
        for gone in ('| דובי / שורי |', 'שווי משוער בעוד 5 שנים', '| מחיר צפוי למניה', '**שווי למניה בכל שנה',
                     '| היום | ', '| מועד |'):
            self.assertNotIn(gone, t)
        rows = [x for x in t.splitlines() if x.startswith('| שנה ') and x != HEAD]
        self.assertEqual([r.split(' | ')[0] for r in rows], [f'| שנה {k} ({2026 + k}/10)' for k in range(1, 6)])
        # a few short explanations
        self.assertLessEqual(sum(1 for x in t.splitlines() if x.startswith('- ')), 8)
        # the other views stay behind the scenes
        md = markdown(self.cf)
        self.assertIn('שווי למניה בכל שנה, לפי תרחיש (מודל תזרים המזומנים)', md)
        self.assertIn('## Evidence pack (the ticker run)', md)

    def test_every_table_price_has_its_reason_behind_the_scenes(self):
        cp = self.cf['company_path']
        rows = [x for x in self.table.splitlines() if x.startswith('| שנה ') and x != HEAD]
        for s, col in (('bear', 1), ('base', 2), ('bull', 3)):
            x = cp['scenarios'][s]
            for t, line in enumerate(rows, start=1):
                shown = line.split(' | ')[col].split(' (')[0].strip(' |')
                r = x['rows'][t]
                hit = [ln for ln in self.reasons.splitlines() if ln.startswith(f'- שנה {t} (') and f'**{shown}**' in ln]
                self.assertTrue(hit, (s, t, shown))
                ln = hit[0]
                self.assertIn(f"מכפיל {r['multiple']:.1f}", ln)
                for word in ({'justified_high': 'מכפיל גבוה מוצדק', 'peak': 'מכפיל של שיא', 'unproven': 'עוד לא הוכח'}[x['justification']],
                             {'own_history': 'ההיסטוריה של החברה', 'competitor': 'מתחרים ישירים'}[x['source_tier']]):
                    self.assertIn(word, ln)
                for e in r['events']:
                    self.assertIn(e['event_he'], ln)
                self.assertTrue(r['eps_drivers'])
                self.assertTrue(set(x['evidence_ids']) <= set(r['evidence_ids']))
        self.assertIn('מחיר המניה בכל שנה', self.reasons.split('###')[0])
        self.assertIn('מה קובע את הרווח למניה בכל השנים', self.reasons)

    def test_paragraph_five_year_figure_is_the_tables(self):
        base5 = self.cf['company_path']['scenarios']['base']['rows'][5]
        row5 = next(x for x in self.table.splitlines() if x.startswith('| שנה 5 '))
        shown = row5.split(' | ')[2].split(' (')[0].rstrip('$')
        self.assertIn(f'מחיר מותנה (רווח למניה × מכפיל) של כ־{shown}', self.para)
        self.assertIn(f"{base5['annual_return']:+.0%} לשנה", self.para)

    def test_guided_year_and_next_year_apart(self):
        fc = self.cf['fc']
        cur = fc['anchors']['current_fiscal_year']
        tr = self.cf['evidence_pack']['guidance_track_record']['ratio']
        self.assertEqual(cur['end'], '2026-12-31')
        self.assertAlmostEqual(cur['revenue'], 1.16e9 + (tr - 1) * 325e6, delta=1)     # the ratio on the unreported Q4 only
        base = next(s for s in fc['spec']['scenarios'] if s['name'] == 'base')
        self.assertAlmostEqual(base['growth'][0], .275, places=6)                    # FY2027 = consensus, unadjusted
        src = {(r['lever'], r['scenario']): r for r in fc['driver_sources']}
        self.assertEqual(src[('growth_anchor', 'base')]['tier'], 'consensus')

    def test_pack_falsifiers_frozen_and_due(self):
        th = thesis_from_casefile(self.cf, clock=lambda: '2026-10-25T12:00:00Z')
        t = next(t for t in th['triggers'] if t['id'] == 'falsifier:x_rev')
        self.assertEqual((t['op'], t['threshold'], t['by'], t['scenario']), ('<', 320000000, '2027-02-28', 'base'))
        self.assertEqual(th['review_by'], '2027-02-28')
        later = copy.deepcopy(self.fb)
        later['as_of'] = '2027-03-01'
        out = check(th, later, price={'value': 90., 'as_of': '2027-03-01'})
        self.assertTrue(any('falsifier:x_rev' in r for r in out['rereview_reasons']))


class Render(unittest.TestCase):
    def test_value_table_without_company_path_has_years_one_to_five(self):
        p = pack()
        fb, cf = run(p, mutate=lambda i: i.pop('company_path'))
        t = hebrew_table(cf)
        self.assertEqual(t.count(HEAD), 1)
        self.assertIn('**שווי למניה בכל שנה, לפי תרחיש**', t)
        rows = [x.split(' | ')[0] for x in t.splitlines() if (x.startswith('| היום |') or x.startswith('| שנה ')) and x != HEAD]
        self.assertEqual(rows[0], '| היום')
        self.assertNotIn('2026/12', t.split('**שווי למניה בכל שנה')[1])                 # the stub year-end 9 weeks away
        self.assertEqual([re.sub(r' \(.*', '', r) for r in rows[1:]], [f'| שנה {k}' for k in range(1, 6)])
        self.assertIn('| מחיר המניה לפי רווח × מכפיל | לא חושב', t)                    # the pack asked for it
        for gone in ('| דובי / שורי |', 'שווי משוער בעוד 5 שנים', '| מחיר צפוי למניה'):
            self.assertNotIn(gone, t)

    def test_wording_of_the_warning_rows(self):
        from fundamental_engine.render import _share_he, _change_he
        self.assertEqual(_share_he(.94), 'כ־94% מהחברות בגודל הזה הצליחו בכך בעבר')
        self.assertTrue(_share_he(.12).startswith('רק כ־12%'))
        self.assertEqual(_change_he(-.06), 'ירידה של כ־6%')
        self.assertEqual(_change_he(.15), 'צמיחה של כ־15%')


class PackRules(unittest.TestCase):
    def test_growth_anchor_states_the_next_fiscal_year(self):
        p = pack()
        p['drivers']['growth_anchor']['period'] = 'FY2026'
        refused(self, p, 'growth_anchor is the growth of FY2027')
        p['drivers']['growth_anchor'].pop('period')
        refused(self, p, 'period required')

    def test_guidance_and_consensus_values_inside_the_cited_range(self):
        p = pack()
        p['drivers']['growth_anchor']['base'] = .9
        refused(self, p, 'outside the cited consensus range')
        p['drivers']['growth_anchor']['deviation'] = 'stated reason'
        self.assertIn('deviation', [o for o in to_inputs(p)['forecast']['overrides'] if o['scenario'] == 'base'][0]['reason'])
        p = pack()
        p['drivers']['growth_anchor'].update(source_tier='guidance', adjust_by_track_record=False, evidence_ids=['g_fy26', 's_pr'])
        refused(self, p, 'needs a revenue guidance item for FY2027')

    def test_current_year_adjusts_only_the_remainder(self):
        meta = to_inputs(pack())['evidence_pack']
        a = meta['guidance_adjustments'][0]
        self.assertEqual((a['lever'], a['before'], a['remainder']), ('current_year_revenue', 1.16e9, 325e6))
        p = pack()
        p['drivers']['current_year_revenue']['remainder'] = 'g_fy26'
        refused(self, p, 'unreported remainder only')
        p = pack()
        p['drivers']['current_year_revenue']['base'] = 1.2e9
        refused(self, p, 'no scenario values')

    def test_tiers(self):
        p = pack()
        p['drivers']['mature_margin']['evidence_ids'] = ['c_fy27_eps']
        refused(self, p, 'cannot rest on consensus alone')
        p = pack()
        p['drivers']['mature_margin'].update(source_tier='guidance', evidence_ids=['f_rev_2025', 's_10k'])
        refused(self, p, 'tier guidance needs a guidance item')
        p = pack()
        p['company_path']['scenarios']['bull']['evidence_ids'] = ['s_px']
        refused(self, p, "needs a source of the named competitor")
        p = pack()
        p['drivers']['mature_margin']['source_tier'] = {'bear': 'analyst', 'base': 'own_history', 'bull': 'own_history'}
        tiers = {o['scenario']: o['source_tier'] for o in to_inputs(p)['forecast']['overrides'] if o['path'] == 'mature_margin'}
        self.assertEqual(tiers, {'bear': 'analyst', 'base': 'own_history', 'bull': 'own_history'})

    def test_market_feeds_citable_on_their_date_documents_after_the_lag(self):
        p = pack()
        for s in p['sources']:
            if s['id'] in ('s_px', 's_fred'):
                s['date'] = p['as_of']
        i = to_inputs(p)
        self.assertEqual(i['forecast']['risk_free']['source_id'], 'web:s_fred')
        fb = F.new({'ticker': 'DEMO', 'currency': 'USD'}, p['as_of'])
        F.add_qualitative_bundle(fb, to_bundle(p))
        self.assertIn('web:s_px', fb['sources'])
        p = pack()
        p['sources'].append({'id': 's_8k', 'kind': 'sec_filing', 'title': '8-K', 'url': 'https://example.invalid/8k',
                             'date': '2026-10-24', 'independent': True})
        p['customers'].append({'id': 'cu_8k', 'share': .2, 'period': 'Q3 2026', 'source_ids': ['s_8k']})
        p['key_risk']['evidence_ids'] = ['cu_8k']
        refused(self, p, 'item cu_8k is not citable at as_of')

    def test_forecast_options_and_market_checked_like_every_block(self):
        p = pack()
        p['forecast_options'] = {'cyclical': {'treatment': 'structural', 'reason': 'ramp, not a cycle', 'evidence_ids': ['s_10k']},
                                 'horizon_years': 10}
        fo = to_inputs(p)['forecast']
        self.assertEqual(fo['cyclical']['evidence_ids'], ['web:s_10k'])
        fb, cf = run(p)
        self.assertEqual(cf['stages']['forecast'], 'ok')
        self.assertNotIn('הטיפול במחזוריות', hebrew_table(cf))
        self.assertNotIn('מחזורית |', hebrew_table(cf))
        for bad, msg in (({'horizon_years': 'ten'}, 'an integer from 1 to 30'), ({'financing': 'maybe'}, 'one of'),
                         ({'cyclical': {'treatment': 'x', 'reason': 'r', 'evidence_ids': ['s_10k']}}, 'treatment one of')):
            p = pack()
            p['forecast_options'] = bad
            refused(self, p, msg)
        p = pack()
        p['market']['risk_free']['as_of'] = '2027-01-01'
        refused(self, p, 'no look-ahead')
        p = pack()
        p['market']['risk_free']['value'] = 'four percent'
        refused(self, p, 'value must be a rate')

    def test_malformed_input_is_refused(self):
        for change, msg in ((lambda p: p['notes'].append(42), 'a note is a non-empty text'),
                            (lambda p: p.update(identity='Demo'), 'identity: must be an object'),
                            (lambda p: p['consensus'][0].update(n=-3), 'positive integer'),
                            (lambda p: p['consensus'][0].update(low='n/a'), 'low and high must be numbers'),
                            (lambda p: p['bottlenecks'][0].pop('id'), "missing ['id']"),
                            (lambda p: p.pop('company_path'), 'company_path required')):
            p = pack()
            change(p)
            refused(self, p, msg)

    def test_event_with_unknown_keys_is_listed_not_dropped(self):
        ev, rej = scenario_events({'analyst_sheet': {'scenario_events': {'base': [{'event_he': 'x', 'evidence_id': ['a']}]}}},
                                  {'sources': {}, 'qualitative': []})
        self.assertEqual(ev.get('base') or [], [])
        self.assertEqual(rej[0]['code'], 'unknown_keys')

    def test_merge_dedupes_sources_by_url(self):
        a = {'sources': [{'id': 'nm_10k', 'url': 'u1', 'kind': 'sec_filing'}], 'facts': [{'id': 'nm_f', 'source_ids': ['nm_10k']}]}
        b = {'sources': [{'id': 'rp_10k', 'url': 'u1', 'kind': 'sec_filing'}], 'facts': [{'id': 'rp_f', 'source_ids': ['rp_10k']}],
             'competitors': [{'ticker': 'peera', 'why': 'w', 'source_ids': ['rp_10k']}]}
        out, rep = merge_fragments('demo', '2026-10-25', {'name': 'D'}, [a, b], {'key_risk': {'reading_he': 'r', 'evidence_ids': ['rp_10k']}})
        self.assertEqual([s['id'] for s in out['sources']], ['nm_10k'])
        self.assertEqual(out['facts'][1]['source_ids'], ['nm_10k'])
        self.assertEqual(out['key_risk']['evidence_ids'], ['nm_10k'])
        self.assertEqual(rep['renamed_sources'], {'rp_10k': 'nm_10k'})
        with self.assertRaises(ValueError):
            merge_fragments('demo', '2026-10-25', {}, [a, a], {})


class ForecastLevers(unittest.TestCase):
    def overrides(self, inputs, extra):
        inputs['forecast']['overrides'] += extra

    def test_evidenced_after_sbc_margin_is_executed_with_an_sbc_override(self):
        ev = ['web:s_10k']
        extra = [{'path': 'sbc_pct', 'scenario': 'all', 'value': .12, 'reason': 'own SBC', 'evidence_ids': ev, 'source_tier': 'own_history'}]
        fb, cf = run(pack(), mutate=lambda i: self.overrides(i, extra))
        for sc in cf['fc']['spec']['scenarios']:
            want = {'bear': .2, 'base': .3, 'bull': .38}[sc['name']]
            self.assertAlmostEqual(sc['terminal']['operating_margin'], want, places=5)

    def test_scenario_order_clamp_is_reported(self):
        extra = [{'path': 'growth_anchor', 'scenario': 'bull', 'value': .276, 'reason': 'x', 'evidence_ids': ['web:s_est'],
                  'source_tier': 'consensus'}]
        fb, cf = run(pack(), mutate=lambda i: (i['forecast'].__setitem__('overrides', [o for o in i['forecast']['overrides']
                                                                                         if not (o['path'] == 'growth_anchor' and o['scenario'] == 'bull')] + extra)))
        src = {(r['lever'], r['scenario']): r for r in cf['fc']['driver_sources']}
        self.assertEqual(src[('growth_anchor', 'bull')]['binding_bound'], 'scenario_order_0.5pp')
        self.assertIn('scenario_order_clamped', [w['code'] for w in cf['fc']['warnings']])

    def test_a_default_every_override_replaced_is_not_warned_about(self):
        fb, cf = run(pack())
        self.assertIn('sales_to_capital_capped', [w['code'] for w in cf['fc']['warnings']])
        extra = [{'path': 'sales_to_capital', 'scenario': s, 'value': v, 'reason': 'own', 'evidence_ids': ['web:s_10k'],
                  'source_tier': 'own_history'} for s, v in (('bear', 1.5), ('base', 2.), ('bull', 2.5))]
        fb, cf = run(pack(), mutate=lambda i: self.overrides(i, extra))
        self.assertFalse([w for w in cf['fc']['warnings'] if w['code'].startswith('sales_to_capital')])
        fb, cf = run(pack(), mutate=lambda i: self.overrides(i, extra[1:]))
        w = next(w for w in cf['fc']['warnings'] if w['code'] == 'sales_to_capital_capped')
        self.assertIn('replaced by an evidenced override in base, bull', w['message'] if 'message' in w else str(w))

    def test_constants_that_set_numbers_are_gaps_and_judgement_stays_a_gap(self):
        fb, cf = run(pack())
        gaps = {g['lever']: g for g in cf['fc']['driver_gaps']}
        for lever in ('margin_convergence', 'minimum_cash_months'):
            self.assertIn(lever, gaps)
        extra = [{'path': 'margin_convergence_years', 'scenario': 'all', 'value': 4, 'reason': 'own ramp', 'evidence_ids': ['web:s_10k'],
                  'source_tier': 'own_history'},
                 {'path': 'growth_half_life', 'scenario': 'all', 'value': 3, 'reason': 'judgement', 'evidence_ids': ['web:s_10k'],
                  'source_tier': 'analyst'}]
        fb, cf = run(pack(), mutate=lambda i: self.overrides(i, extra))
        gaps = {g['lever']: g for g in cf['fc']['driver_gaps']}
        self.assertNotIn('margin_convergence', gaps)
        self.assertEqual(gaps['growth_half_life']['classes'], ['JUDGEMENT'])
        with self.assertRaises(ValueError):
            forecast._check_overrides({'sources': {'a': {}}}, [{'path': 'margin_convergence_years', 'value': 0, 'reason': 'r',
                                                                 'evidence_ids': ['a']}])



class EconomicsPathEndToEnd(unittest.TestCase):
    """The demo company with every multiple path computed by the engine (company_path multiple_from 'economics'): the pack
    gives each scenario's ROIC, the engine its own EPS path and rates; the yearly table, its reasons and the casefile agree,
    and no other value moves."""

    @classmethod
    def setUpClass(cls):
        cls.hand = run(pack())[1]
        p = pack()
        for s, roic in (('bear', .12), ('base', .25), ('bull', .40)):
            x = p['company_path']['scenarios'][s]
            x.pop('multiple_path')
            x.update(multiple_from='economics', roic=roic)
        cls.fb, cls.cf = run(p)

    def test_engine_computed_path_end_to_end(self):
        self.assertEqual({k: v for k, v in self.cf['stages'].items() if v != 'ok'}, {})
        cp = self.cf['company_path']
        case = {c['name']: c for c in self.cf['val']['case']['scenarios']}
        for s in ('bear', 'base', 'bull'):
            e = cp['scenarios'][s]['economics']
            self.assertEqual(e['status'], 'computed')
            self.assertEqual((e['cost_of_equity'], e['terminal_growth']), (case[s]['cost_of_equity'], case[s]['terminal']['growth']))
            for t, r in enumerate(cp['scenarios'][s]['rows']):
                if r['status'] == 'priced':
                    self.assertAlmostEqual(r['price'], e['values'][t]['value'], delta=1e-9 * abs(r['price']))
        t = hebrew_table(self.cf)
        self.assertEqual(t.count(HEAD), 1)
        base5 = cp['scenarios']['base']['rows'][5]
        self.assertIn(f"{base5['price']:,.1f}$" if base5['price'] < 100 else f"{base5['price']:,.0f}$", t)
        self.assertIn('מחושב מהתשואה על ההון', t)
        reasons = render_he(self.cf['scenario_reasons'])
        self.assertIn('מחושב מהכלכלה של התרחיש', reasons)
        # every value outside the company path is the hand run's (the path is display only)
        for k in ('fc', 'val', 'rk', 'dc', 'an', 'card', 'qa'):
            self.assertEqual(json.dumps(self.cf[k], sort_keys=True, default=str), json.dumps(self.hand[k], sort_keys=True, default=str), k)


if __name__ == '__main__':
    unittest.main()


class CurrentYearGuidanceTierWithoutAFiscalYearRow(unittest.TestCase):
    """CRDO 26.10.2026: inputs.forecast.current_year_revenue at source tier 'guidance' when the FactBook holds no guidance
    row for that fiscal year (only a quarterly guide) stopped the forecast with a TypeError; its own evidence ids carry it."""

    def test_forecast_runs_and_cites_the_inputs_own_evidence(self):
        p = pack()
        inputs, bundle = to_inputs(p), to_bundle(p)
        cyr = inputs['forecast']['current_year_revenue']
        self.assertEqual(cyr['source_tier'], 'guidance')
        bundle['guidance'] = [g for g in bundle.get('guidance') or [] if 'FY' not in str(g.get('period', ''))]
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(FIXTURE, os.path.join(d, 'DEMO'))
            fb, cf = handoff.run_company(os.path.join(d, 'DEMO'), p['as_of'], inputs=inputs, qualitative=bundle)
        self.assertEqual(cf['stages']['forecast'], 'ok', cf.get('errors'))
        row = next(a for a in cf['fc']['assumptions'] if a['id'] == 'revenue_current_fy')
        self.assertTrue(set(cyr['evidence_ids']) & set(row['source_ids']), row['source_ids'])
