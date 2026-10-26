import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fundamental_engine import facts as F
from fundamental_engine.evidence_pack import to_inputs, to_bundle, TIERS
from fundamental_engine.forecast import ECON_LEVERS
from fundamental_engine.mcp import TOOLS, Server
from fundamental_engine.supervisor import catalog, packet

ROOT = Path(__file__).resolve().parents[1]
DEMO = json.loads((ROOT / 'examples' / 'evidence_pack_demo.json').read_text(encoding='utf-8'))


def pack():
    return copy.deepcopy(DEMO)


def refused(test, p, fragment):
    with test.assertRaises(ValueError) as e:
        to_inputs(p)
    test.assertIn(fragment, str(e.exception))


class ConversionTests(unittest.TestCase):
    def test_demo_converts_and_every_cited_id_resolves_in_the_factbook(self):
        inputs = to_inputs(pack())
        fb = F.new({'ticker': 'DEMO', 'currency': 'USD'}, DEMO['as_of'])
        F.add_qualitative_bundle(fb, to_bundle(pack()))
        known = set(fb['sources']) | {q['id'] for q in fb['qualitative']}
        cited = [e for o in inputs['forecast']['overrides'] for e in o['evidence_ids']]
        cited += [e for s in inputs['company_path']['scenarios'].values() for e in s['evidence_ids']]
        cited += [e for rows in inputs['analyst_sheet']['scenario_events'].values() for r in rows for e in r['evidence_ids']]
        cited += inputs['analyst_sheet']['key_risk']['evidence_ids'] + inputs['card']['assessments']['customer_concentration']['evidence_ids']
        self.assertTrue(cited)
        self.assertEqual([e for e in cited if e not in known], [])
        self.assertEqual(inputs['forecast']['risk_free']['source_id'], 'web:s_fred')
        self.assertIn('web:s_fred', fb['sources'])
        self.assertEqual({g['period'] for g in fb['guidance']}, {'Q4 2026', 'FY2026'})

    def test_overrides_carry_the_source_tier_contract(self):
        ov = to_inputs(pack())['forecast']['overrides']
        self.assertEqual({o['source_tier'] for o in ov}, {'consensus', 'own_history'})
        self.assertTrue(all(o['path'] in ECON_LEVERS and o['scenario'] in ('bear', 'base', 'bull') for o in ov))
        self.assertTrue(all(set(o) <= {'path', 'scenario', 'value', 'reason', 'evidence_ids', 'source_tier', 'competitors'} for o in ov))
        self.assertEqual(set(TIERS), {'guidance', 'contracted', 'consensus', 'own_history', 'competitor', 'market', 'analyst'})

    def test_company_path_and_scenario_events_contracts(self):
        i = to_inputs(pack())
        cp = i['company_path']
        self.assertEqual((cp['metric'], cp['multiple']), ('eps', 'forward_pe'))
        self.assertEqual(cp['scenarios']['bull']['competitors'], ['PEERA'])
        self.assertTrue(all(len(s['multiple_path']) == 6 for s in cp['scenarios'].values()))
        ev = i['analyst_sheet']['scenario_events']
        self.assertEqual(set(ev), {'bear', 'base', 'bull'})
        self.assertTrue(all(set(r) == {'event_he', 'evidence_ids'} for rows in ev.values() for r in rows))
        self.assertEqual(ev['bear'][1]['evidence_ids'], [])          # the labelled hypothesis keeps no evidence

    def test_company_path_economics_mode(self):
        # the analyst gives the ROIC (with evidence and tier); the engine computes the multiple path (company_path.economics)
        p = pack()
        base = p['company_path']['scenarios']['base']
        hand = base.pop('multiple_path')
        base.update(multiple_from='economics', roic=.28)
        cp = to_inputs(p)['company_path']
        self.assertEqual(cp['scenarios']['base']['multiple_from'], 'economics')
        self.assertEqual(cp['scenarios']['base']['roic'], .28)
        self.assertNotIn('multiple_path', cp['scenarios']['base'])
        self.assertEqual(cp['scenarios']['base']['evidence_ids'], to_inputs(pack())['company_path']['scenarios']['base']['evidence_ids'])
        self.assertEqual(len(cp['scenarios']['bull']['multiple_path']), 6)               # a hand path next to it stays
        self.assertNotIn('multiple_from', to_inputs(pack())['company_path']['scenarios']['base'])   # hand packs: unchanged
        q = copy.deepcopy(p)
        q['company_path']['scenarios']['base']['multiple_path'] = hand
        refused(self, q, 'remove multiple_path')
        q = copy.deepcopy(p)
        q['company_path']['scenarios']['base'].pop('roic')
        refused(self, q, "needs roic")
        q = copy.deepcopy(p)
        q['company_path']['scenarios']['base']['roic'] = 28                                # a percent, not a decimal
        refused(self, q, "needs roic")
        for typed in (4.5, 1.6):                          # a small percent typed as a number (4.5 meant as 4.5%): refused too
            q = copy.deepcopy(p)
            q['company_path']['scenarios']['base']['roic'] = typed
            refused(self, q, "4.5% is 0.045")
        q = copy.deepcopy(p)
        q['company_path']['scenarios']['base']['roic'] = 1.5                                # the bound itself is a decimal
        self.assertEqual(to_inputs(q)['company_path']['scenarios']['base']['roic'], 1.5)
        q = copy.deepcopy(p)
        q['company_path']['scenarios']['base']['multiple_from'] = 'peers'
        refused(self, q, "multiple_from must be")
        q = pack()
        q['company_path']['scenarios']['base']['roic'] = .3
        refused(self, q, 'roic belongs to')
        q = pack()
        q['company_path']['scenarios']['base'].pop('multiple_path')
        refused(self, q, "multiple_path needs 6")

    def test_track_record_ratio_recorded_and_applied_only_where_the_pack_says(self):
        meta = to_inputs(pack())['evidence_pack']
        tr = meta['guidance_track_record']
        self.assertEqual(tr['n'], 3)
        self.assertAlmostEqual(tr['ratio'], (252 / 240 + 275 / 262 + 300 / 285) / 3, places=5)
        adj = meta['guidance_adjustments']                       # the demo: the year in progress, its unreported Q4 only
        self.assertEqual([(a['lever'], a['scenario']) for a in adj], [('current_year_revenue', 'all')])
        self.assertAlmostEqual(adj[0]['after'], 1.16e9 + (tr['ratio'] - 1) * 325e6, delta=1)
        # a guidance-tier growth_anchor needs guidance for the year it sets (FY2027 here), checked against its range
        p = pack()
        p['guidance'].append({'id': 'g_fy27', 'metric': 'revenue', 'period': 'FY2027', 'low': 1.45e9, 'high': 1.55e9, 'unit': 'USD',
                              'issued': '2026-10-20', 'source_id': 's_pr'})
        p['drivers']['growth_anchor'].update(bear=.24, base=.275, bull=.31, source_tier='guidance', adjust_by_track_record=['base'],
                                             evidence_ids=['g_fy27', 's_pr'], reason='FY2027 guided range over FY2026')
        i = to_inputs(p)
        adj = [a for a in i['evidence_pack']['guidance_adjustments'] if a['lever'] == 'growth_anchor']
        self.assertEqual([a['scenario'] for a in adj], ['base'])
        self.assertIn('FY2027', adj[0]['rule'])
        ov = {o['scenario']: o['value'] for o in i['forecast']['overrides'] if o['path'] == 'growth_anchor'}
        self.assertEqual(ov['bear'], .24)
        self.assertAlmostEqual(ov['base'], 1.275 * tr['ratio'] - 1, places=5)
        p['drivers']['growth_anchor']['adjust_by_track_record'] = True
        self.assertEqual(len([a for a in to_inputs(p)['evidence_pack']['guidance_adjustments'] if a['lever'] == 'growth_anchor']), 3)
        p['drivers']['growth_anchor'].pop('adjust_by_track_record')
        refused(self, p, 'states adjust_by_track_record')
        p = pack()
        p.pop('guidance_track')
        refused(self, p, 'needs guidance_track')

    def test_independence_collapses_syndicated_copies(self):
        p = pack()
        p['bottlenecks'].append({'id': 'b2', 'reading': 'wire', 'evidence_ids': ['s_wire', 's_wire_copy']})
        p['drivers']['mature_margin']['evidence_ids'] = ['s_wire', 's_wire_copy']
        p['drivers']['mature_margin']['source_tier'] = 'analyst'
        to_inputs(p)
        p['sources'][-1]['independent'] = True
        refused(self, p, 'syndicated copy is not independent')


class RefusalTests(unittest.TestCase):
    def test_unknown_keys_at_every_level(self):
        for path in (('x',), ('identity', 'x'), ('sources', 0, 'x'), ('drivers', 'growth_anchor', 'x'),
                     ('company_path', 'scenarios', 'base', 'x'), ('potential', 'threats', 'x')):
            p = pack()
            node = p
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = 1
            refused(self, p, 'unknown keys')

    def test_unknown_and_uncitable_evidence(self):
        p = pack()
        p['key_risk']['evidence_ids'] = ['nope']
        refused(self, p, 'unknown evidence id nope')
        p = pack()
        p['sources'].append({'id': 's_late', 'kind': 'press', 'title': 't', 'url': 'u', 'date': '2026-10-24', 'independent': True})
        p['key_risk']['evidence_ids'] = ['s_late']
        refused(self, p, 'not citable at as_of')
        p['sources'][-1]['date'] = '2026-11-01'
        refused(self, p, 'look-ahead')

    def test_consensus_is_never_evidence_of_a_fact(self):
        p = pack()
        p['facts'][0]['source_ids'] = ['s_est']
        refused(self, p, 'never evidence of a fact')
        p = pack()
        p['consensus'][0]['source_id'] = 's_pr'
        refused(self, p, 'kind consensus')

    def test_guidance_is_the_issuers_and_tiers_need_matching_evidence(self):
        p = pack()
        p['guidance'][0]['source_id'] = 's_wire'
        refused(self, p, 'not an issuer document')
        p = pack()
        p['drivers']['mature_margin']['source_tier'] = 'consensus'
        refused(self, p, 'tier consensus needs evidence')
        p = pack()
        p['drivers']['mature_margin']['source_tier'] = 'peer_median'
        refused(self, p, 'source_tier must be one of')

    def test_competitors_must_be_named_with_a_reason(self):
        p = pack()
        p['company_path']['scenarios']['bull']['competitors'] = ['ZZZZ']
        refused(self, p, 'not a named competitor')
        p = pack()
        p['company_path']['scenarios']['bull'].pop('competitors')
        refused(self, p, 'needs the named competitors')
        p = pack()
        p['company_path']['peers'][0]['ticker'] = 'OTHER'
        refused(self, p, 'not a named direct competitor')
        p = pack()
        p['competitors'][0].pop('why')
        refused(self, p, "missing ['why']")
        p = pack()
        p['company_path']['scenarios']['base']['competitors'] = ['PEERA']
        refused(self, p, 'competitors belong to tier competitor only')

    def test_readings_and_events_are_evidenced_or_labelled(self):
        p = pack()
        p['potential']['threats'] = {'reading': 'x'}
        refused(self, p, 'label the reading hypothesis')
        p = pack()
        p['potential'].pop('share_trajectory')
        refused(self, p, 'share_trajectory required')
        p = pack()
        p['scenario_events']['bull'] = []
        refused(self, p, 'scenario_events.bull')
        p = pack()
        p['scenario_events']['bear'][1].pop('hypothesis')
        refused(self, p, 'label the event hypothesis')

    def test_paths_levers_and_falsifiers(self):
        p = pack()
        p['company_path']['scenarios']['base']['multiple_path'] = [40, 35]
        refused(self, p, 'multiple_path needs 6')
        p = pack()
        p['drivers']['peer_growth'] = copy.deepcopy(p['drivers']['mature_margin'])
        refused(self, p, 'unknown lever')
        p = pack()
        p['drivers']['mature_margin']['base'] = [.3, .3]
        refused(self, p, 'economic levers take one number')
        p = pack()
        p['falsifiers'][0]['by'] = '2026-01-01'
        refused(self, p, 'future date')

    def test_errors_are_listed_together(self):
        p = pack()
        p['x'] = 1
        p['key_risk']['evidence_ids'] = ['nope']
        with self.assertRaises(ValueError) as e:
            to_inputs(p)
        self.assertIn('unknown keys', str(e.exception))
        self.assertIn('unknown evidence id nope', str(e.exception))


class SurfaceTests(unittest.TestCase):
    def test_cli_writes_inputs_and_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, '-m', 'fundamental_engine', 'pack-to-inputs', str(ROOT / 'examples' / 'evidence_pack_demo.json'),
                                '--out', os.path.join(d, 'inputs.json'), '--bundle', os.path.join(d, 'b.json')],
                               cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(Path(d, 'inputs.json').read_text(encoding='utf-8')), to_inputs(pack()))
            self.assertEqual(json.loads(Path(d, 'b.json').read_text(encoding='utf-8')), to_bundle(pack()))

    def test_mcp_tool(self):
        self.assertIn('evidence_pack_to_inputs', [t['name'] for t in TOOLS])
        srv = Server(':memory:')
        srv.initialized = True
        call = lambda p, i=1: srv.handle({'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                                          'params': {'name': 'evidence_pack_to_inputs', 'arguments': {'pack': p}}})['result']
        out = call(pack())
        self.assertFalse(out['isError'])
        self.assertIn('company_path', json.loads(out['content'][0]['text'])['inputs'])
        bad = pack()
        bad['x'] = 1
        self.assertTrue(call(bad, 2)['isError'])

    def test_skeleton_packets_and_workflow_ship(self):
        ids = [p['id'] for p in catalog()['packets']]
        self.assertIn('sources_registry', ids)
        mp = packet('master_process')['content']
        for phase in ('Identify', 'Gather', 'Understand', 'Position', 'Value', 'Review', 'Deliver'):
            self.assertIn(phase, mp)
        for word in ('Entry', 'Outputs', 'Exit', 'what breaks', 'what must happen'):
            self.assertIn(word, mp)
        self.assertIn('syndicated', packet('sources_registry')['content'])
        wf = (ROOT / '.claude' / 'workflows' / 'ticker.js').read_text(encoding='utf-8')
        self.assertTrue(wf.startswith('export const meta = {'))
        self.assertIn("name: 'ticker'", wf)
        for phase in ('Identify', 'Gather', 'Understand', 'Position', 'Value', 'Review', 'Deliver'):
            self.assertIn(f"phase('{phase}')", wf)
        for banned in ('Date.now', 'Math.random', 'new Date()', 'create_order', 'create_alert', 'create_watchlist('):
            self.assertNotIn(banned, wf)


if __name__ == '__main__':
    unittest.main()
