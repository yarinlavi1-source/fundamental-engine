import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import lean_model  # noqa: E402
from fundamental_engine import lean_freeze as LF  # noqa: E402
from fundamental_engine.ledger import Ledger, is_late  # noqa: E402

LEAN_MODEL = os.path.join(ROOT, 'scripts', 'lean_model.py')


class Clock:
    """Injectable system clock (the ledger stamps recorded_at from it)."""
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def read(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def dump(obj, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)


class LeanFreezeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, 'research'))
        self.legacy = os.path.join(self.root, 'research', 'forecast_ledger.jsonl')
        with open(self.legacy, 'w') as f:
            f.write('{"type": "thesis", "thesis_id": "old"}\n')
        self.params = read(os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_anet.json'))   # as_of 2026-11-04
        self.clock = Clock('2026-11-06T10:00:00+00:00')
        self.ledger = os.path.join(self.root, 'research', 'ledger.jsonl')
        self.L = LF.open_ledger(self.ledger, clock=self.clock)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def folder(self, ticker='ANET', as_of='2026-11-04', params=None, files=None):
        d = os.path.join(self.root, 'research', 'companies', ticker, as_of)
        os.makedirs(d, exist_ok=True)
        if files is None:
            p = dict(copy.deepcopy(params or self.params), ticker=ticker, as_of=as_of)
            files = {'lean_params.json': p, 'lean_result.json': lean_model.run(p)}
        for name, obj in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
            dump(obj, os.path.join(d, name))
        return d

    def freeze(self, d, **kw):
        return LF.freeze_folder(d, self.L, root=self.root, run=lean_model.run, lean_model_path=LEAN_MODEL, **kw)

    def rows(self, kind=None):
        return [r for r in self.L.lines() if kind is None or r['type'] == kind]

    def test_thesis_and_forecast_rows(self):
        d = self.folder()
        before = sha(os.path.join(d, 'lean_params.json')), sha(self.legacy)
        out = self.freeze(d, next_report='2027-02-10')
        self.assertEqual(out['status'], 'frozen')
        self.assertEqual(self.L.verify(), {'ok': True, 'entries': 21, 'legacy_entries': 0})
        first = self.rows()[0]                                        # the note that names the legacy file
        self.assertEqual((first['type'], first['id']), ('event', LF.GENESIS_ID))
        self.assertIn('sha256:' + before[1], first['evidence_ids'])
        t = self.rows('thesis')[0]
        rec, saved = t['record'], read(os.path.join(d, 'lean_result.json'))
        self.assertEqual((t['entity'], t['made_at'], rec['as_of'], rec['price']), ('ANET', '2026-11-04', '2026-11-04', 182.95))
        self.assertEqual(rec['files']['params']['sha256'], before[0])
        self.assertEqual(rec['files']['result']['sha256'], sha(os.path.join(d, 'lean_result.json')))
        self.assertEqual(rec['files']['lean_model']['sha256'], sha(LEAN_MODEL))
        self.assertEqual(rec['values']['today'], {k: saved['scenarios'][k]['value_today'] for k in LF.SCEN})
        self.assertEqual(rec['values']['year_end_dates'], {str(y): f'{y}-12-31' for y in range(2027, 2032)})
        self.assertEqual(rec['values']['year_end']['base']['2031'], saved['scenarios']['base']['year_end_values']['2031'])
        self.assertEqual(rec['calibrated']['value_today'], saved['two_lens']['calibrated']['value_today'])
        self.assertEqual(rec['buy_test']['pass'], saved['buy_test']['pass'])
        self.assertEqual(rec['buy_test']['max_price_passing'], saved['buy_test']['max_price_passing'])
        self.assertTrue(rec['rerun']['reproduces_saved_result'])
        self.assertIs(rec['on_time'], True)
        self.assertIs(rec['late_recording'], False)
        fc = self.rows('forecast')
        self.assertEqual(sorted({f['metric'] for f in fc}), ['eps_operating', 'operating_margin', 'revenue'])
        self.assertEqual(len(fc), 7 + 6 + 6)                          # revenue also for the unfinished base year
        rev = next(f for f in fc if f['metric'] == 'revenue' and f['context']['fiscal_year'] == 2028)
        self.assertEqual(rev['scenarios'], {k: saved['scenarios'][k]['revenue_m']['2028'] * 1e6 for k in LF.SCEN})
        self.assertEqual((rev['period_end'], rev['unit'], rev['basis'], rev['thesis_id']), ('2028-12-31', 'USD', 'reported_revenue', t['thesis_id']))
        m = next(f for f in fc if f['metric'] == 'operating_margin' and f['context']['fiscal_year'] == 2027)
        self.assertEqual((m['unit'], m['currency'], m['scenarios']['base']), ('ratio', None, self.params['scenarios']['base']['m'][0]))
        self.assertEqual(before, (sha(os.path.join(d, 'lean_params.json')), sha(self.legacy)))   # inputs and legacy file untouched

    def test_second_run_adds_nothing(self):
        d = self.folder()
        self.freeze(d)
        n = len(self.rows())
        self.clock.t = '2026-11-07T10:00:00+00:00'
        again = self.freeze(d)
        self.assertEqual((again['status'], again['rows_written'], len(self.rows())), ('unchanged', 0, n))
        # a result file rewritten with more keys but the same published numbers is not a new valuation
        path = os.path.join(d, 'lean_result.json')
        dump(dict(read(path), added_later={'x': 1}), path)
        again = self.freeze(d)
        self.assertEqual((again['status'], len(self.rows())), ('unchanged', n))
        self.assertIn('rewritten', again['note'])

    def test_changed_params_supersede(self):
        d = self.folder()
        first = self.freeze(d, falsifiers=[{'id': 'f1', 'kind': 'exit', 'metric': 'operating_margin', 'op': '<', 'threshold': 0.4}])
        p = copy.deepcopy(self.params)
        p['scenarios']['base']['m'] = [x - 0.02 for x in p['scenarios']['base']['m']]
        self.folder(params=p)
        self.clock.t = '2026-11-09T10:00:00+00:00'
        second = self.freeze(d)
        self.assertEqual(second['status'], 'superseded an earlier freeze')
        self.assertNotEqual(second['thesis_id'], first['thesis_id'])
        old, new = self.rows('thesis')
        self.assertEqual(new['record']['supersedes'], old['thesis_id'])
        self.assertEqual(new['made_at'], '2026-11-09')                # an edit after the freeze is dated by the clock, not by as_of
        self.assertIn('recording day', new['record']['made_at_basis'])
        self.assertEqual(new['record']['falsifiers'], old['record']['falsifiers'])   # kept when not supplied again
        self.assertLess(new['record']['values']['today']['base'], old['record']['values']['today']['base'])
        fc = [f for f in self.rows('forecast') if f['thesis_id'] == new['thesis_id']]
        self.assertEqual(len(fc), 19)
        for f in fc:
            self.assertEqual(f['supersedes'], f"{old['thesis_id']}:{f['metric']}:{f['context']['fiscal_year']}")
            self.assertEqual(f['context']['vintage'], 'revision')
        self.assertEqual(len(self.rows('forecast')), 38)              # the originals stay on file
        self.assertTrue(self.L.verify()['ok'])
        self.assertEqual([x['thesis_id'] for x in LF.status(self.L)['in_force']], [new['thesis_id']])

    def test_newer_valuation_supersedes_and_order(self):
        self.freeze(self.folder())
        self.clock.t = '2026-11-14T10:00:00+00:00'
        newer = self.freeze(self.folder(as_of='2026-11-13', params=dict(self.params, days_to_base_year_end=48, price=190.0)))
        old, new = self.rows('thesis')
        self.assertEqual((new['made_at'], new['record']['supersedes'], new['record']['supersede_reason']),
                         ('2026-11-13', old['thesis_id'], 'a newer valuation of the same company'))
        self.assertEqual(newer['forecasts_written'], 19)
        # an older valuation frozen after a newer one is on file: recorded, dated by the clock, never backdated
        late = self.freeze(self.folder(ticker='OLD', as_of='2026-11-05'))
        row = self.rows('thesis')[-1]
        self.assertEqual((late['status'], row['made_at'], row['record']['as_of']), ('frozen', '2026-11-14', '2026-11-05'))
        self.assertIn('earlier than an entry already on file', row['record']['made_at_basis'])
        self.assertTrue(self.L.verify()['ok'])

    def test_late_recording_is_flagged_not_refused(self):
        self.clock.t = '2026-11-15T10:00:00+00:00'                    # 11 days after as_of
        out = self.freeze(self.folder(), next_report='2026-11-10')
        rec = self.rows('thesis')[0]['record']
        self.assertEqual((out['status'], rec['recording_lag_days'], rec['late_recording'], rec['on_time']), ('frozen', 11, True, False))
        self.assertTrue(all(is_late(f) and f['context']['on_time'] is False for f in self.rows('forecast')))

    def test_on_time(self):
        self.assertIs(LF.on_time('2026-11-15', '2026-11-17'), True)
        self.assertIs(LF.on_time('2026-11-17', '2026-11-17'), None)   # the day of the report: cannot tell
        self.assertIs(LF.on_time('2026-11-18', {'date': '2026-11-17', 'confirmed': True}), False)
        self.assertIs(LF.on_time('2026-11-15', '~2026-11-17'), None)  # inside the 3-day window
        self.assertIs(LF.on_time('2026-11-15', 'late January 2027'), True)
        self.assertIs(LF.on_time('2027-03-02', 'Q4 2026 report, Feb-Mar 2027'), None)
        self.assertIs(LF.on_time('2027-04-01', 'Feb-Mar 2027'), False)
        self.assertIs(LF.on_time('2026-11-15', 'Q4 2026, early 2027'), None)
        self.assertIs(LF.on_time('2026-11-15', None), None)
        # default: the handoff written for this valuation
        h = os.path.join(self.root, 'research', 'technical', 'ANET', '2026-11-04')
        os.makedirs(h)
        dump({'ticker': 'ANET', 'next_report': {'date': '~2027-02-16', 'confirmed': False}}, os.path.join(h, 'fundamental.json'))
        self.freeze(self.folder())
        rec = self.rows('thesis')[0]['record']
        self.assertEqual((rec['on_time'], rec['next_report']['earliest'], rec['next_report']['source']),
                         (True, '2027-02-13', 'research/technical/ANET/2026-11-04/fundamental.json'))

    def test_outcome_in_a_fresh_checkout(self):
        self.freeze(self.folder())
        fresh = os.path.join(tempfile.mkdtemp(), 'ledger.jsonl')      # only the tracked file survives a re-clone
        shutil.copy(self.ledger, fresh)
        L = Ledger(fresh, clock=Clock('2027-02-20T12:00:00+00:00'))
        f = next(r for r in L.lines() if r['type'] == 'forecast' and r['metric'] == 'revenue' and r['context']['fiscal_year'] == 2026)
        L.outcome(forecast_id=f['id'], actual=13.4e9, source_id='10-K FY2026', observed_at='2027-02-18', unit='USD', currency='USD',
                  basis='reported_revenue')
        self.assertTrue(L.verify()['ok'])
        self.assertEqual(L.score()['resolved'], 1)
        with self.assertRaises(ValueError):                           # an outcome on another definition is refused
            m = next(r for r in L.lines() if r['type'] == 'forecast' and r['metric'] == 'operating_margin')
            L.outcome(forecast_id=m['id'], actual=0.4, source_id='x', observed_at='2028-02-18', unit='ratio', basis='gaap_margin')

    def test_variants_and_pre_review(self):
        d = self.folder()
        pre = copy.deepcopy(self.params)
        pre['scenarios']['base']['pe'] += 3
        var = dict(copy.deepcopy(self.params), beta=1.3)
        self.folder(files={'lean_params_pre_review.json': pre, 'lean_params_low_beta.json': var,
                           'lean_result_low_beta.json': lean_model.run(var),
                           'review_findings.json': {'pre_review': {'base_today': 1.0}, 'final': {'base_today': 2.0}, 'critical_findings': 1}})
        self.freeze(d)
        rec = self.rows('thesis')[0]['record']
        self.assertEqual(rec['review']['pre_review']['base_today'], lean_model.run(pre)['scenarios']['base']['value_today'])
        self.assertEqual(rec['review']['post_review_base_today'], rec['values']['today']['base'])
        self.assertEqual(rec['review']['review_findings']['critical_findings'], 1)
        self.assertEqual([v['name'] for v in rec['variants']], ['low_beta'])
        self.assertEqual(rec['variants'][0]['value_today']['base'], lean_model.run(var)['scenarios']['base']['value_today'])
        self.assertEqual(len(self.rows('thesis')), 1)                 # variants are not theses of their own

    def test_custom_models(self):
        remap = copy.deepcopy(self.params)
        for s in remap['scenarios'].values():
            s['m'] = [1.0] * 6                                        # the revenue line is not sales
        a = self.freeze(self.folder(ticker='RMP', params=remap))
        seg = {'scenarios': {k: {'value_today': v, 'year_end_values': {'2027': v * 1.1, '2031': v * 1.6}, 'annual_return_pct': r,
                                 'path': {'2027': {'rev': 10.0 * (1 + i), 'ebit_m': 0.1 * (1 + i)}}}
                             for i, (k, v, r) in enumerate((('bear', 10.0, -12.0), ('base', 50.0, 13.0), ('bull', 90.0, 20.0)))},
               'calibrated': {'value_today': 60.0, 'annual_return_pct': 14.0}, 'price_for_12pct': 44.0}
        b = self.freeze(self.folder(ticker='SEG', files={'params.json': {'ticker': 'SEG', 'as_of': '2026-11-04', 'price': 40.0,
                                                                         'days_to_base_year_end': 57}, 'results.json': seg,
                                                         'model.py': 'x = 1'}))
        lanes = {'config': {'price': 600.0}, 'scenarios': {k: {'today': v, 'ye': {'2031': v * 1.5}, 'return_pct': r}
                                                           for k, v, r in (('bear', 200.0, -10.5), ('base', 500.0, 6.5), ('bull', 900.0, 19.0))},
                 'lenses': {'calibrated': {'end_2031': 1000.0, 'return_pct': 10.4, 'band': 'hold'}}}
        c = self.freeze(self.folder(ticker='LAN', files={'model_result.json': lanes, 'model/dcf_params.json':
                                                         {'as_of': '2026-10-27', 'days_to_base_year_end': 65}, 'model/m.py': 'x = 1'}))
        recs = {t['entity']: t['record'] for t in self.rows('thesis')}
        for t in ('RMP', 'SEG', 'LAN'):
            self.assertEqual(recs[t]['kind'], 'custom')
            self.assertTrue(recs[t]['note'].startswith('custom model'))
        self.assertEqual((a['model'], a['forecasts_written'], c['forecasts_written']), ('remapped_lean', 0, 0))
        self.assertEqual((b['model'], b['forecasts_written']), ('segment', 2))
        self.assertEqual(recs['SEG']['buy_test']['pass'], False)      # the bear leg (-12%) fails the -10% line
        self.assertEqual(recs['SEG']['buy_test']['saved_keys'], {'price_for_12pct': 44.0})
        self.assertEqual((recs['LAN']['price'], recs['LAN']['as_of'], recs['LAN']['values']['year_end_dates']), (600.0, '2026-11-04', {'2031': '2031-12-31'}))
        self.assertEqual(recs['LAN']['calibrated']['year_end'], {'2031': 1000.0})
        self.assertEqual(sorted(recs['LAN']['files']['inputs']), ['model/dcf_params.json', 'model/m.py'])
        self.assertTrue(self.L.verify()['ok'])

    def test_freeze_all_in_as_of_order(self):
        self.folder(ticker='BBB', as_of='2026-11-05')
        self.folder(ticker='AAA', as_of='2026-11-06')
        self.folder(ticker='SKP', as_of='2026-11-04')
        os.makedirs(os.path.join(self.root, 'research', 'companies', 'OLD', '2026-10-28', 'gather'))
        half = os.path.join(self.root, 'research', 'companies', 'NEW', '2026-11-06')
        os.makedirs(half)
        dump(self.params, os.path.join(half, 'lean_params.json'))
        out = LF.freeze_all(self.root, self.L, run=lean_model.run, lean_model_path=LEAN_MODEL, skip=('SKP',),
                            next_reports={'AAA': '2027-02-01', 'BBB/2026-11-05': {'date': '2026-11-05', 'confirmed': True}})
        self.assertEqual([(r['ticker'], r['on_time']) for r in out['frozen']], [('BBB', False), ('AAA', True)])
        self.assertEqual(sorted(x['folder'].split('/')[2] for x in out['left_out']), ['NEW', 'OLD', 'SKP'])
        self.assertTrue(out['ledger']['ok'])
        again = LF.freeze_all(self.root, self.L, run=lean_model.run, lean_model_path=LEAN_MODEL, skip=('SKP',))
        self.assertEqual([r['rows_written'] for r in again['frozen']], [0, 0])

    def test_ticker_keyed_next_report_is_for_the_latest_valuation_only(self):
        # two valuations of one company, frozen together after a report that fell between them: the upcoming report
        # date (keyed by the ticker) must not make the older one on time
        self.clock.t = '2026-11-15T10:00:00+00:00'
        self.folder(ticker='AAA', as_of='2026-11-11')
        self.folder(ticker='AAA', as_of='2026-11-13', params=dict(self.params, price=self.params['price'] * 0.9))
        out = LF.freeze_all(self.root, self.L, run=lean_model.run, lean_model_path=LEAN_MODEL, next_reports={'AAA': 'February 2027'})
        self.assertEqual([(r['as_of'], r['on_time']) for r in out['frozen']], [('2026-11-11', None), ('2026-11-13', True)])

    def test_named_pair(self):
        d = self.folder()
        with self.assertRaises(ValueError):
            LF.freeze(os.path.join(d, 'lean_params.json'), os.path.join(self.root, 'x.json'), ledger=self.L)
        out = LF.freeze(os.path.join(d, 'lean_params.json'), os.path.join(d, 'lean_result.json'), ledger=self.L, root=self.root)
        self.assertEqual((out['status'], out['forecasts_written']), ('frozen', 19))


class RepoLedgerTest(unittest.TestCase):
    """The tracked ledger verifies, and the legacy file it names in its first row is still byte-identical."""

    def test_repo_ledger(self):
        path = os.path.join(ROOT, LF.LEDGER)
        if not os.path.exists(path):
            self.skipTest('research/ledger.jsonl not written yet')
        L = Ledger(path)
        self.assertTrue(L.verify()['ok'])
        first = L.lines()[0]
        self.assertEqual(first['id'], LF.GENESIS_ID)
        self.assertIn('sha256:' + sha(os.path.join(ROOT, LF.LEGACY)), first['evidence_ids'])


if __name__ == '__main__':
    unittest.main()
