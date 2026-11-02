"""holdings_watch.py and research_store.py on SYNTHETIC data only (no network).

The thesis is test_monitor's synthetic ADBE-spec thesis frozen into a temporary ledger; later FactBooks, positions,
prices and runs are synthetic test data, not company results. The FactBook builder is injected.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_monitor import Clock, casefile, fy27, later_fb, thesis  # noqa: E402
from fundamental_engine import holdings_watch as HW  # noqa: E402
from fundamental_engine import monitor as M  # noqa: E402
from fundamental_engine import research_store as RS  # noqa: E402
from fundamental_engine.ledger import Ledger  # noqa: E402


def positions(as_of, rows):
    return {'as_of': as_of, 'source': 'synthetic', 'rows': [dict(t=t, shares=10, price=p, value=10 * p, weight=w) for t, p, w in rows]}


class HoldingsWatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.ledger = os.path.join(cls.dir, 'ledger.jsonl')
        cls.th = thesis()
        L = Ledger(cls.ledger, clock=Clock('2026-10-19T15:00:00+00:00'))
        M.freeze(L, cls.th)
        M.freeze(L, M.thesis_from_casefile(casefile(ticker='OLDCO'), clock=Clock('2026-10-19T14:30:00+00:00')))

    def run_update(self, fb, as_of, price=245.0, extra=()):
        def factbook_for(t, d, th):
            if t == 'ADBE':
                return fb
            raise HW.Unavailable('companyfacts report in EUR, the thesis in USD: foreign filer')
        pos = positions(as_of, [('ADBE', price, .6), ('NEWCO', 50.0, .3), *extra])
        return HW.update(pos, self.ledger, as_of, factbook_for)

    def row(self, res, t):
        return next(r for r in res['rows'] if r['ticker'] == t)

    def test_new_holding_and_removed(self):
        res = self.run_update(later_fb(as_of='2026-12-01', fy26={}), '2026-12-01')
        new = self.row(res, 'NEWCO')
        self.assertEqual(new['status'], 'new_holding')
        self.assertIn('fast ticker procedure', new['reason'])
        self.assertEqual([r['ticker'] for r in res['removed']], ['OLDCO'])
        self.assertIn('NEWCO', res['full_rerun'])

    def test_no_new_report_shows_frozen_value_vs_price(self):
        res = self.run_update(later_fb(as_of='2026-12-01', fy26={}), '2026-12-01')
        r = self.row(res, 'ADBE')
        self.assertEqual(r['status'], 'no_new_report')
        v = r['view']
        self.assertEqual(v['price'], 245.0)
        self.assertIsNotNone(v['base_value_today'])
        self.assertEqual(v['expected_return']['kind'], 'proxy')        # the ledger record carries no IRR flows
        md = HW.render_he(res)
        self.assertIn('| ADBE |', md)
        self.assertIn('אין דוח חדש', md)

    def test_implied_irr_when_the_casefile_is_found(self):
        def factbook_for(t, d, th):
            return later_fb(as_of='2026-12-01', fy26={})
        res = HW.update(positions('2026-12-01', [('ADBE', 245.0, 1.0)]), self.ledger, '2026-12-01', factbook_for,
                        casefile_for=lambda t, th: casefile())
        self.assertEqual(self.row(res, 'ADBE')['view']['expected_return']['kind'], 'implied_irr')

    def test_new_report_with_confirmation_is_light(self):
        res = self.run_update(later_fb(as_of='2027-02-01'), '2027-02-01')
        r = self.row(res, 'ADBE')
        self.assertEqual(r['status'], 'light_update', r.get('detail'))
        self.assertTrue(r['detail']['confirmations'])
        self.assertEqual(r['detail']['falsifiers_tripped'], [])
        self.assertEqual(r['conclusion'], 'stands')
        self.assertIn('המסקנה בתוקף', r['line_he'])

    def test_new_report_tripping_a_falsifier_needs_full_rerun(self):
        v = fy27(self.th, rev_mult=.88, margin=.25, fcf_margin=.18)
        res = self.run_update(later_fb(fy27_values=v), '2028-02-20', price=235.0)
        r = self.row(res, 'ADBE')
        self.assertEqual(r['status'], 'full_rerun_needed')
        ids = {x['assumption_id'] for x in r['detail']['falsifiers_tripped']}
        self.assertIn('revenue_growth', ids)
        self.assertTrue(any(c['metric'] == 'revenue' and c['position'] == 'below' for c in r['detail']['changes']))
        self.assertEqual(r['value_signal'], 'down')
        self.assertIn('ADBE', res['full_rerun'])
        self.assertIn('הרצה מלאה', HW.render_he(res))

    def test_data_unavailable_keeps_the_reason_and_the_price_view(self):
        res = self.run_update(later_fb(as_of='2026-12-01', fy26={}), '2026-12-01', extra=[('OLDCO', 240.0, .1)])
        r = self.row(res, 'OLDCO')
        self.assertEqual(r['status'], 'data_unavailable')
        self.assertIn('foreign filer', r['reason'])
        self.assertIsNotNone(r['view']['base_value_today'])
        self.assertEqual(res['removed'], [])

    def test_non_stock_rows_skipped(self):
        pos = positions('2026-12-01', [('NEWCO', 10.0, 1.0)])
        pos['rows'].append({'t': 'SPY 250117C', 'sec_type': 'OPT', 'price': 1.0})
        res = HW.update(pos, self.ledger, '2026-12-01', lambda *a: None)
        self.assertEqual(res['skipped_non_stock'], ['SPY 250117C'])

    def test_union_of_ledgers_and_start_folder(self):
        root = tempfile.mkdtemp()
        tracked = os.path.join(root, 'research', 'forecast_ledger.jsonl')
        RS.append_ledger(RS.thesis_rows(self.ledger, 'ADBE', self.th['as_of']), tracked)
        os.makedirs(os.path.join(root, 'research', 'companies', 'ADBE', self.th['as_of']))
        res = HW.update(positions('2027-02-01', [('ADBE', 245.0, 1.0)]), [os.path.join(root, 'missing.jsonl'), tracked],
                        '2027-02-01', lambda *a: later_fb(as_of='2027-02-01'), root=root)
        r = self.row(res, 'ADBE')
        self.assertEqual(r['thesis_id'], self.th['thesis_id'])
        self.assertEqual(r['start_from'], os.path.join('research', 'companies', 'ADBE', self.th['as_of']))


class ResearchStoreTests(unittest.TestCase):
    def make_run(self, root, t='SYN', d='2026-10-19'):
        run = os.path.join(root, 'runs', 'tickers', f'{t}_{d}')
        files = {f'companies/{t}/inputs.json': {'x': 1}, 'evidence_pack.json': {'facts': []}, 'status.json': {'verified': True},
                 'position/core.json': {}, 'position/field.json': {}, 'position/path.json': {}, 'review/numbers.json': {},
                 'review/counter.json': {}, f'companies/{t}/out/casefile.json': dict(casefile(), as_of=d)}
        for rel, obj in files.items():
            p = os.path.join(run, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w') as f:
                json.dump(obj, f)
        for rel in (f'companies/{t}/out/table_he.md', f'companies/{t}/out/scenarios_he.md', 'gather/numbers.json'):
            p = os.path.join(run, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w') as f:
                f.write('synthetic')
        return run

    def test_save_and_load(self):
        root = tempfile.mkdtemp()
        self.assertIsNone(RS.load('SYN', root))
        run = self.make_run(root)
        th = M.thesis_from_casefile(dict(casefile(ticker='SYN')), clock=Clock('2026-10-19T14:00:00+00:00'))
        lp = os.path.join(root, 'runs', 'tickers', 'forecast_ledger.jsonl')
        M.freeze(Ledger(lp, clock=Clock('2026-10-19T15:00:00+00:00')), th)
        res = RS.save(run, root)
        self.assertEqual(res['missing'], ['companies/SYN/out/paragraph.txt'])
        for f in ('inputs.json', 'evidence_pack.json', 'table_he.md', 'scenarios_he.md', 'position/core.json',
                  'review/numbers.json', 'casefile.json', 'casefile_summary.json', 'thesis.json'):
            self.assertTrue(os.path.isfile(os.path.join(root, 'research', 'companies', 'SYN', '2026-10-19', f)), f)
        self.assertFalse(os.path.exists(os.path.join(root, 'research', 'companies', 'SYN', '2026-10-19', 'gather')))
        self.assertEqual(res['thesis_id'], th['thesis_id'])
        n = len(Ledger(os.path.join(root, RS.LEDGER)).lines())
        self.assertEqual(n, 1 + len(th['forecasts']))
        again = RS.save(run, root)                                    # deduplicated by thesis_id / forecast id
        self.assertEqual(again['ledger_rows_appended'], 0)
        self.assertEqual(len(Ledger(os.path.join(root, RS.LEDGER)).lines()), n)
        got = RS.load('syn', root)
        self.assertEqual((got['as_of'], got['thesis_id'], got['status']), ('2026-10-19', th['thesis_id'], 'verified'))
        self.assertIsNotNone(got['values']['today'])
        theses = HW.latest_theses([os.path.join(root, RS.LEDGER)])
        self.assertEqual(theses['SYN']['thesis_id'], th['thesis_id'])

    def test_older_save_keeps_latest_and_no_thesis_is_a_note(self):
        root = tempfile.mkdtemp()
        RS.save(self.make_run(root, d='2026-10-20'), root)
        res = RS.save(self.make_run(root, d='2026-10-19'), root)
        self.assertFalse(res['latest_updated'])
        self.assertIn('no frozen thesis', res['note'])
        self.assertEqual(RS.load('SYN', root)['as_of'], '2026-10-20')

    def test_bad_run_name(self):
        with self.assertRaises(ValueError):
            RS.save(tempfile.mkdtemp(), tempfile.mkdtemp())


if __name__ == '__main__':
    unittest.main()
