"""Stage 0 tests: frozen golden set, point-in-time guard, snapshot provenance, reproduction results.

Tests that need the private data store (original project files with position sizes, vendor prices, SEC bulk
snapshot) run only when STAGE0_DATA points to it; otherwise they are skipped, never faked.
"""
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S0 = os.path.join(ROOT, 'stage0')
sys.path.insert(0, ROOT)
from fundamental_engine import snapshot as S  # noqa: E402

DATA = os.environ.get('STAGE0_DATA', '/home/claude/stage0_data')
HAVE_DATA = os.path.isdir(os.path.join(DATA, 'golden'))
PRIV = os.environ.get('STAGE0_PRIVATE', os.path.join(DATA, 'private'))
HAVE_PRIV = os.path.isfile(os.path.join(PRIV, 'stage0', 'repro_results.json'))


def load(rel):
    return json.load(open(os.path.join(S0, rel)))


class GoldenSetFrozen(unittest.TestCase):
    def test_json_equals_plan_table(self):
        g = load('golden_set.json')
        plan = open(os.path.join(ROOT, 'docs', 'STAGE0_PLAN.md'), encoding='utf-8').read()
        rows = re.findall(r'^\| (\d+) \| (\w+) \| (\d+) \| (\d{4}-\d\d-\d\d) \|', plan, flags=re.M)
        self.assertEqual(len(rows), 20)
        self.assertEqual([(r[1], int(r[2]), r[3]) for r in rows], [(c['tk'], c['cik'], c['asof']) for c in g['companies']])

    def test_frozen_metadata(self):
        g = load('golden_set.json')
        self.assertEqual(g['frozen'], '2026-10-20')
        self.assertEqual(len({c['tk'] for c in g['companies']}), 20)
        self.assertIn('V', [c['tk'] for c in g['companies']])


class SnapshotHelpers(unittest.TestCase):
    def test_cutoff_and_known_at(self):
        rows = [dict(end='2016-03-31', val=1, filed='2016-04-27'), dict(end='2016-03-31', val=2, filed='2016-06-28'),
                dict(end='2016-06-30', val=3, filed='2016-07-26')]
        self.assertEqual(S.cutoff('2016-06-30'), '2016-06-28')
        known, excluded = S.known_at(rows, '2016-06-30')
        self.assertEqual([r['val'] for r in known], [1, 2])
        self.assertEqual(excluded, 1)

    def test_period_views_restatement(self):
        rows = [dict(start='2016-01-01', end='2016-03-31', val=10, filed='2016-04-27'),
                dict(start='2016-01-01', end='2016-03-31', val=11, filed='2016-06-01')]
        first, latest, changed = S.period_views(rows)
        k = ('2016-01-01', '2016-03-31')
        self.assertEqual(first[k]['val'], 10)
        self.assertEqual(latest[k]['val'], 11)
        self.assertEqual(len(changed), 1)

    def test_manifest_roundtrip_and_tamper(self):
        d = tempfile.mkdtemp()
        try:
            open(os.path.join(d, 'a.json'), 'w').write('{"x": 1}')
            e = S.entry(d, 'a.json', source='test', retrieved_utc='2026-10-20T00:00:00Z', information_date='2026-10-19',
                        code_version='abc', label='test')
            self.assertEqual(S.verify(d, [e]), [])
            open(os.path.join(d, 'a.json'), 'w').write('{"x": 2}')
            self.assertEqual(S.verify(d, [e]), [('a.json', 'sha256 mismatch')])
            with self.assertRaises(ValueError):
                S.entry(d, 'a.json', source='test', retrieved_utc='', information_date='x', code_version='y', label='z')
        finally:
            shutil.rmtree(d)


class NothingPrivateInRepo(unittest.TestCase):
    """Position sizes, account value and the list of held names stay outside Git (CLAUDE.md rule)."""

    def test_no_private_material(self):
        pat = re.compile(r'^(HOLD|NAV|POS) *= *[\d{]', re.M)
        for top in ('stage0', 'docs', 'scripts'):
            for dirpath, _, files in os.walk(os.path.join(ROOT, top)):
                for f in files:
                    if not (f.startswith('stage0') or f.startswith('STAGE0') or f.startswith('SNAPSHOT') or 'stage0' in dirpath):
                        continue
                    txt = open(os.path.join(dirpath, f), encoding='utf-8', errors='ignore').read()
                    self.assertIsNone(pat.search(txt), f)
                    self.assertNotRegex(txt, r'"(weight|value|POS|NAV|HOLD|held)"\s*:', f)
                    self.assertNotRegex(txt, r"(?i)yarin'?s? (holdings|portfolio)", f)
        self.assertFalse(os.path.exists(os.path.join(S0, 'legacy')))


class PR7PitCheck(unittest.TestCase):
    def test_pit_layer_matches_golden_views(self):
        r = load('pr7_pit_check.json')
        self.assertEqual(len(r), 20)
        self.assertTrue(all(x['identical'] for x in r))


@unittest.skipUnless(HAVE_PRIV, 'private reproduction results not available (set STAGE0_PRIVATE)')
class PrivateReproductionSummary(unittest.TestCase):
    """Reads the private results; asserts only aggregate properties, so nothing private is in this file."""

    @classmethod
    def setUpClass(cls):
        cls.r = json.load(open(os.path.join(PRIV, 'stage0', 'repro_results.json')))

    def test_no_unexplained_deviation(self):
        self.assertEqual(len(self.r['cases']), 10)
        for c in self.r['cases']:
            if c['case'] == '1_holdings_1210_as_preserved':      # preserved code is not the version that ran; see 1b
                self.assertTrue(all(d['field'] == 'nm' or d['field'].startswith('scen.') or d['field'] in ('action', 'notes')
                                    for d in c['summary']['deviations_over_1pct']))
                continue
            self.assertEqual(c['summary']['deviations'], 0, c['case'])

    def test_sec_reconstruction_has_no_look_ahead(self):
        for c in self.r['cases']:
            for x in c.get('sec_reconstruction', []):
                self.assertLessEqual(x['latest_filed_used'], x['filed_max_allowed'], c['case'])


class PriceAudit(unittest.TestCase):
    def test_every_price_checked_and_as_traded(self):
        a = load('price_audit.json')
        self.assertEqual(a['summary']['UNVERIFIED'], [])
        self.assertEqual(a['summary']['MISSING'], [])
        for c in a['companies']:
            b = c['as_traded']
            self.assertLessEqual(b['bar_date'], c['asof'], c['tk'])
            self.assertEqual(b['bar_date'][:7], c['asof'][:7], c['tk'])
            ib, y = c['checks']['ib'], c['checks']['yahoo']
            second = ib if 'rel_diff' in ib else y
            self.assertLessEqual(abs(second['rel_diff']), 0.01, c['tk'])
            if 'note' in b:
                self.assertNotIn('BEFORE the close', b['note'], c['tk'])


@unittest.skipUnless(HAVE_DATA, 'private stage 0 data store not available (set STAGE0_DATA)')
class GoldenSnapshots(unittest.TestCase):
    def test_manifest_hashes(self):
        m = json.load(open(os.path.join(DATA, 'golden', 'MANIFEST.json')))
        self.assertEqual(S.verify(os.path.join(DATA, 'golden'), m), [])
        for e in m:
            for k in ('source', 'retrieved_utc', 'information_date', 'code_version', 'label', 'sha256'):
                self.assertTrue(e.get(k), (e['file'], k))

    def test_pit_views_have_no_late_facts(self):
        g = load('golden_set.json')
        for c in g['companies']:
            v = json.load(open(os.path.join(DATA, 'golden', 'pit', f"{c['tk']}_{c['asof']}.json")))
            cut = S.cutoff(c['asof'])
            self.assertEqual(v['cutoff_filed_on_or_before'], cut)
            self.assertTrue(all(r['filed'] <= cut for r in v['facts']), c['tk'])
            self.assertIn('RECONSTRUCTED', v['label'])
            self.assertIn('Not an original snapshot', v['label'])


if __name__ == '__main__':
    unittest.main()
