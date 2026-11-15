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


def picture(root, t, d, **over):
    """A synthetic saved fundamental picture (research/technical/<T>/<date>/fundamental.json)."""
    folder = os.path.join(root, 'research', 'technical', t, d)
    os.makedirs(folder, exist_ok=True)
    obj = {'ticker': t, 'as_of': d, 'price': 240.0, 'verdict_he': 'החזקה (סינתטי)', 'buy_test_price': 230.0,
           'margin_of_safety_price': 221.0,
           'values': {'today': {'bear': 150.0, 'base': 270.0, 'bull': 380.0}, 'end_2027': {'bear': 160.0, 'base': 300.0, 'bull': 420.0},
                      'end_2031': {'bear': 200.0, 'base': 450.0, 'bull': 640.0}},
           'annual_return_pct': {'bear': -3.0, 'base': 13.0, 'bull': 21.0}}
    obj.update(over)
    with open(os.path.join(folder, 'fundamental.json'), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)


class IndexFundAndBaselineTests(unittest.TestCase):
    """The two failures of 15.11.2026 (synthetic data): an index fund reported as a company to value, and a value
    printed from a thesis in another currency / share unit or from an older thesis than the saved picture."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.ledger = os.path.join(cls.dir, 'ledger.jsonl')
        cls.th = thesis()
        L = Ledger(cls.ledger, clock=Clock('2026-10-19T15:00:00+00:00'))
        M.freeze(L, cls.th)
        # the same synthetic thesis recorded in another currency per ordinary share (a foreign filer held as an ADR)
        cls.tw = dict(cls.th, ticker='TWCO', thesis_id='TWCO:2026-10-19:synthetic', currency='TWD',
                      price_at_analysis=dict(cls.th['price_at_analysis'], currency='TWD'))
        M.freeze(L, cls.tw)

    def update(self, rows, root=None, as_of='2026-12-01', **pos_over):
        pos = positions(as_of, rows)
        pos.update(pos_over)

        def factbook_for(t, d, th):
            fb = later_fb(as_of=as_of, fy26={})
            fb['company'] = dict(fb['company'], ticker=t, currency=th.get('currency') or 'USD')     # same synthetic books
            return fb
        return HW.update(pos, self.ledger, as_of, factbook_for, root=root)

    def row(self, res, t):
        return next(r for r in res['rows'] if r['ticker'] == t)

    def test_index_fund_is_not_a_new_holding(self):
        pos = positions('2026-12-01', [('CSPX', 800.0, .5), ('NEWCO', 50.0, .2)])
        pos['rows'].append({'t': 'BONDX', 'asset_class': 'ETF', 'price': 20.0, 'weight': .1})
        pos['rows'].append({'t': 'WIDE', 'kind': 'fund', 'price': 30.0, 'weight': .1})
        pos['rows'].append({'t': 'spy', 'sec_type': 'STK', 'price': 600.0, 'weight': .05})      # brokers type ETFs as STK
        pos['rows'].append({'t': 'NAMEDX', 'name': 'Some S&P 500 Index Fund ETF', 'price': 9.0, 'weight': .05})
        res = HW.update(pos, self.ledger, '2026-12-01', lambda *a: None)
        for t in ('CSPX', 'BONDX', 'WIDE', 'SPY'):
            r = self.row(res, t)
            self.assertEqual((r['status'], r['action']), ('index_fund', 'none'), t)
            self.assertNotIn('value_comparison', r)
        self.assertIn('INDEX_FUNDS', self.row(res, 'CSPX')['reason'])
        self.assertIn('type ETF', self.row(res, 'BONDX')['reason'])
        self.assertEqual(self.row(res, 'NAMEDX')['status'], 'new_holding')        # never guessed from the name
        self.assertEqual(sorted(res['index_funds']), ['BONDX', 'CSPX', 'SPY', 'WIDE'])
        self.assertEqual(sorted(res['full_rerun']), ['NAMEDX', 'NEWCO'])
        self.assertEqual(res['counts']['index_fund'], 4)
        self.assertEqual(res['skipped_non_stock'], [])
        md = HW.render_he(res)
        line = next(x for x in md.splitlines() if x.startswith('| CSPX |'))
        self.assertIn('קרן מדד, לא חברה: לא מוערכת', line)
        self.assertNotIn('הרצה מלאה', line)
        self.assertEqual(HW.stock_rows(pos)[1], [])                              # funds are not 'skipped' rows either

    def test_named_set_is_one_constant(self):
        self.assertEqual(HW.INDEX_FUNDS, {'CSPX', 'VOO', 'SPY', 'IVV', 'VTI', 'VUAA', 'SXR8', 'QQQ', 'VWRA', 'IWDA'})

    def test_other_currency_gives_the_reason_not_a_value(self):
        res = self.update([('TWCO', 40.0, 1.0)])
        r = self.row(res, 'TWCO')
        vc = r['value_comparison']
        self.assertIsNone(vc['source'])
        self.assertIsNone(vc['base_value_today'])
        self.assertEqual(vc['skipped_sources'][0]['code'], 'currency')
        self.assertIn('TWD', vc['reason'])
        self.assertNotIn('view', r)                                                # no stale price view either
        self.assertTrue(r['position_currency_assumed'])
        self.assertEqual(r['status'], 'no_new_report')                             # the filing check still ran
        line = next(x for x in HW.render_he(res).splitlines() if x.startswith('| TWCO |'))
        self.assertIn('אין השוואה', line)
        self.assertIn('TWD', line)
        self.assertNotIn('מתחת לשווי', line)
        self.assertNotIn('מחוץ לטווח', line)
        # the same thesis against a position stated in its own currency is compared
        same = self.update([('TWCO', 245.0, 1.0)], currency='TWD')
        self.assertEqual(self.row(same, 'TWCO')['value_comparison']['source'], 'ledger_thesis')

    def test_other_share_unit_gives_the_reason(self):
        res = self.update([('ADBE', 245.0 * 5, 1.0)])                              # e.g. one ADR = 5 ordinary shares
        vc = self.row(res, 'ADBE')['value_comparison']
        self.assertEqual((vc['source'], vc['skipped_sources'][0]['code']), (None, 'price_scale'))
        pos = positions('2026-12-01', [('ADBE', 245.0, 1.0)])
        pos['rows'][0]['unit'] = 'ADR'
        th = dict(self.th, ticker='UNITCO', thesis_id='UNITCO:2026-10-19:synthetic', unit='ordinary share')
        led = os.path.join(tempfile.mkdtemp(), 'l.jsonl')
        M.freeze(Ledger(led, clock=Clock('2026-10-19T15:00:00+00:00')), th)
        pos['rows'][0]['t'] = 'UNITCO'
        res = HW.update(pos, led, '2026-12-01', lambda *a: later_fb(as_of='2026-12-01', fy26={}))
        self.assertEqual(self.row(res, 'UNITCO')['value_comparison']['skipped_sources'][0]['code'], 'unit')

    def test_newer_fundamental_picture_is_preferred(self):
        root = tempfile.mkdtemp()
        picture(root, 'ADBE', '2026-10-01')                                        # older than the thesis: ignored
        res = self.update([('ADBE', 245.0, 1.0)], root=root)
        self.assertEqual(self.row(res, 'ADBE')['value_comparison']['source'], 'ledger_thesis')
        picture(root, 'ADBE', '2026-11-20')
        res = self.update([('ADBE', 245.0, 1.0)], root=root)
        r = self.row(res, 'ADBE')
        vc = r['value_comparison']
        self.assertEqual((vc['source'], vc['date']), ('fundamental_picture', '2026-11-20'))
        self.assertEqual(vc['path'], os.path.join('research', 'technical', 'ADBE', '2026-11-20', 'fundamental.json'))
        self.assertEqual((vc['base_value_today'], vc['bear_value_today'], vc['bull_value_today']), (270.0, 150.0, 380.0))
        self.assertAlmostEqual(vc['discount_to_base'], 1 - 245.0 / 270.0)
        self.assertEqual((vc['buy_test_price'], vc['margin_of_safety_price']), (230.0, 221.0))
        self.assertEqual(vc['last_year']['date'], '2031-12-31')
        yrs = 5 + 30 / 365.25
        self.assertAlmostEqual(vc['expected_return']['value'], (450.0 / 245.0) ** (1 / yrs) - 1, places=3)
        self.assertEqual(vc['expected_return']['kind'], 'proxy')
        self.assertIsNotNone(r['view']['base_value_today'])                        # the old key stays (ledger thesis view)
        self.assertEqual(r['status'], 'no_new_report')
        md = HW.render_he(res)
        line = next(x for x in md.splitlines() if x.startswith('| ADBE |'))
        self.assertIn('270 מול 245.00', line)
        self.assertIn('הערכה שמורה 2026-11-20', line)
        self.assertIn('מחיר מבחן הקנייה 230', md)
        self.assertIn('החזקה (סינתטי)', md)
        early = self.update([('ADBE', 245.0, 1.0)], root=root, as_of='2026-11-10')   # before the picture's date: not read
        self.assertEqual(self.row(early, 'ADBE')['value_comparison']['source'], 'ledger_thesis')
        self.assertEqual(HW.fundamental_picture(root, 'ADBE', '2026-11-10')['date'], '2026-10-01')
        self.assertEqual(HW.fundamental_picture(root, 'ADBE')['date'], '2026-11-20')
        ledger_line = next(x for x in HW.render_he(self.update([('ADBE', 245.0, 1.0)])).splitlines() if x.startswith('| ADBE |'))
        self.assertIn('תזה מוקפאת 2026-10-19', ledger_line)

    def test_picture_replaces_a_thesis_in_another_currency(self):
        root = tempfile.mkdtemp()
        picture(root, 'TWCO', '2026-11-20', price=41.0, values={'today': {'bear': 20.0, 'base': 44.0, 'bull': 60.0}})
        vc = self.row(self.update([('TWCO', 40.0, 1.0)], root=root), 'TWCO')['value_comparison']
        self.assertEqual((vc['source'], vc['base_value_today']), ('fundamental_picture', 44.0))
        self.assertEqual(vc['skipped_sources'][0]['source'], 'ledger_thesis')
        self.assertIsNone(vc['expected_return']['value'])                          # no year-end value in this picture

    def test_older_picture_used_for_lack_of_a_comparable_thesis_is_not_called_newer(self):
        root = tempfile.mkdtemp()
        picture(root, 'TWCO', '2026-10-01', price=41.0,                              # older than the thesis of 2026-10-19
                values={'today': {'bear': 20.0, 'base': 44.0, 'bull': 60.0}, 'end_2031': {'base': -5.0}})
        res = self.update([('TWCO', 40.0, 1.0)], root=root)
        r = self.row(res, 'TWCO')
        vc = r['value_comparison']
        self.assertEqual((vc['source'], vc['older_than_thesis']), ('fundamental_picture', True))
        self.assertNotIn('חדשה יותר', r['line_he'])
        self.assertIn('ישנה מהתזה המוקפאת', r['line_he'])
        self.assertIsNone(vc['expected_return']['value'])                          # a negative year-end value gives no return
        self.assertIn('| TWCO |', HW.render_he(res))

    def test_picture_without_standard_base_or_on_another_scale_falls_back(self):
        root = tempfile.mkdtemp()
        picture(root, 'ADBE', '2026-11-20', values={'by_world': {'a': 1}})
        vc = self.row(self.update([('ADBE', 245.0, 1.0)], root=root), 'ADBE')['value_comparison']
        self.assertEqual(vc['source'], 'ledger_thesis')
        self.assertEqual(vc['skipped_sources'][0]['code'], 'no_base_value')
        picture(root, 'ADBE', '2026-11-21', price=500.0)                           # built on a price twice today's
        vc = self.row(self.update([('ADBE', 245.0, 1.0)], root=root), 'ADBE')['value_comparison']
        self.assertEqual((vc['source'], vc['skipped_sources'][0]['code']), ('ledger_thesis', 'price_scale'))
        picture(root, 'ADBE', '2026-11-22', values={'weighted_base_today': 260, 'by_world': {}}, buy_test_price={'a': 250, 'b': 230})
        res = self.update([('ADBE', 245.0, 1.0)], root=root)
        vc = self.row(res, 'ADBE')['value_comparison']
        self.assertEqual((vc['source'], vc['base_value_today']), ('fundamental_picture', 260.0))
        self.assertIn('weighted', vc['base_basis'])
        self.assertIn('a: 250, b: 230', HW.render_he(res))

    def test_picture_without_a_thesis_is_not_a_new_holding(self):
        root = tempfile.mkdtemp()
        picture(root, 'NEWCO', '2026-11-20', price=50.0, values={'today': {'bear': 30.0, 'base': 60.0, 'bull': 80.0}})
        res = self.update([('NEWCO', 50.0, 1.0)], root=root)
        r = self.row(res, 'NEWCO')
        self.assertEqual((r['status'], r['action']), ('valuation_only', 'none'))
        self.assertEqual(r['value_comparison']['base_value_today'], 60.0)
        self.assertEqual(res['full_rerun'], [])
        self.assertIn('אין תזה מוקפאת', HW.render_he(res))

    def test_newer_run_not_read_here_is_flagged(self):
        root = tempfile.mkdtemp()
        for d in ('ADBE_2026-10-19', 'ADBE_2026-11-04', 'ADBE_2026-11-09_review', 'ADBEX_2026-11-30'):
            os.makedirs(os.path.join(root, 'runs', 'tickers', d))
        res = self.update([('ADBE', 245.0, 1.0)], root=root)
        r = self.row(res, 'ADBE')
        self.assertEqual(r['newer_saved_run'], {'date': '2026-11-04', 'path': os.path.join('runs', 'tickers', 'ADBE_2026-11-04')})
        self.assertIn('הערך בטבלה ישן', r['line_he'])
        picture(root, 'ADBE', '2026-11-20')                                        # the picture is the newest: no flag
        self.assertNotIn('newer_saved_run', self.row(self.update([('ADBE', 245.0, 1.0)], root=root), 'ADBE'))

    def test_old_result_without_the_new_keys_still_renders(self):
        res = self.update([('ADBE', 245.0, 1.0)])
        for r in res['rows']:
            r.pop('value_comparison', None)
        res.pop('index_funds')
        self.assertIn('תזה מוקפאת 2026-10-19', HW.render_he(res))


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
        self.assertEqual(RS.load('SYN', root)['saved'], False)              # an unsaved run is named, labelled unsaved
        res = RS.save(run, root)
        self.assertEqual(sorted(res['missing']), sorted(['companies/SYN/out/paragraph.txt', 'identity.json', 'understand/business.json',
                                                         'gather/documents.json', 'gather/field.json', 'gather/macro.json']))
        dest = os.path.join(root, 'research', 'companies', 'SYN', '2026-10-19')
        for f in ('inputs.json', 'evidence_pack.json', 'table_he.md', 'scenarios_he.md', 'position/core.json',
                  'review/numbers.json', 'casefile_core.json', 'casefile_summary.json', 'gather/numbers.json'):
            self.assertTrue(os.path.isfile(os.path.join(dest, f)), f)
        for f in ('casefile.json', 'thesis.json'):                          # 0.14.3: the extract and the tracked ledger instead
            self.assertFalse(os.path.exists(os.path.join(dest, f)), f)
        with open(os.path.join(dest, 'casefile_core.json')) as f:
            core = json.load(f)
        with open(os.path.join(run, 'companies', 'SYN', 'out', 'casefile.json')) as f:
            full = json.load(f)
        a = M.thesis_from_casefile(core, clock=Clock('2026-10-19T14:00:00+00:00'))
        b = M.thesis_from_casefile(full, clock=Clock('2026-10-19T14:00:00+00:00'))
        self.assertEqual((a['thesis_id'], a.get('irr_basis')), (b['thesis_id'], b.get('irr_basis')))
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

    def test_save_scrubs_contact_addresses(self):
        root = tempfile.mkdtemp()
        run = self.make_run(root)
        with open(os.path.join(run, 'gather', 'numbers.json'), 'w') as f:
            f.write('{"note": "fallback User-Agent app someone@example.com"}')
        RS.save(run, root)
        with open(os.path.join(root, 'research', 'companies', 'SYN', '2026-10-19', 'gather', 'numbers.json')) as f:
            text = f.read()
        self.assertNotIn('@example.com', text)
        self.assertIn('<contact>', text)

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
