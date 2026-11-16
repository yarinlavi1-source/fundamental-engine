"""forecast_tracker.py on SYNTHETIC data only (no network).

The model is the lean_model fixture (tests/fixtures/lean_model_anet.json) run through scripts/lean_model.py; every
'reported' number below is invented test data, not a company result. Frozen theses are written by lean_freeze into a
temporary ledger with an injected clock.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import forecast_tracker as script  # noqa: E402
import lean_model  # noqa: E402
from fundamental_engine import forecast_tracker as FT  # noqa: E402
from fundamental_engine import lean_freeze as LF  # noqa: E402

M = 1e6
REV, OI, SH = 'Revenues', 'OperatingIncomeLoss', 'WeightedAverageNumberOfDilutedSharesOutstanding'
# (tag, start, end, value, filed, form): the 2025 year, 2026 comparatives, then what is reported after the valuation
FACTS = [
    (REV, '2025-01-01', '2025-12-31', 10000 * M, '2026-02-15', '10-K'),
    (REV, '2026-01-01', '2026-03-31', 3000 * M, '2026-05-05', '10-Q'),
    (REV, '2026-01-01', '2026-06-30', 6200 * M, '2026-08-04', '10-Q'),
    (REV, '2026-01-01', '2026-09-30', 9700 * M, '2026-11-03', '10-Q'),
    (REV, '2026-07-01', '2026-09-30', 3500 * M, '2026-11-03', '10-Q'),
    (REV, '2026-01-01', '2026-12-31', 13500 * M, '2027-02-16', '10-K'),
    (OI, '2026-01-01', '2026-12-31', 6000 * M, '2027-02-16', '10-K'),
    (SH, '2026-01-01', '2026-12-31', 1270 * M, '2027-02-16', '10-K'),
    (REV, '2027-01-01', '2027-03-31', 4200 * M, '2027-05-05', '10-Q'),
    (OI, '2027-01-01', '2027-03-31', 1800 * M, '2027-05-05', '10-Q'),
    (REV, '2026-01-01', '2026-03-31', 3000 * M, '2027-05-05', '10-Q'),          # the comparative, filed again
    (REV, '2027-01-01', '2027-06-30', 9000 * M, '2027-08-04', '10-Q'),
    (OI, '2027-01-01', '2027-06-30', 4050 * M, '2027-08-04', '10-Q'),
    (REV, '2027-04-01', '2027-06-30', 4800 * M, '2027-08-04', '10-Q'),
    (REV, '2027-01-01', '2027-12-31', 19000 * M, '2028-02-15', '10-K'),
    (OI, '2027-01-01', '2027-12-31', 8400 * M, '2028-02-15', '10-K'),
    (SH, '2027-01-01', '2027-12-31', 1260 * M, '2028-02-15', '10-K'),
]


def payload(facts=FACTS, unit='USD', taxonomy='us-gaap'):
    node = {}
    for tag, s, e, v, filed, form in facts:
        u = 'shares' if tag == SH else unit
        node.setdefault(tag, {'units': {}})['units'].setdefault(u, []).append({'start': s, 'end': e, 'val': v, 'filed': filed, 'form': form})
    return {'cik': 1, 'entityName': 'Synthetic Co', 'facts': {taxonomy: node}}


class ForecastTrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'tests', 'fixtures', 'lean_model_anet.json'), encoding='utf-8') as f:
            cls.p = json.load(f)                       # as_of 2026-11-04, base year 2026 ending 2026-12-31
        cls.res = lean_model.run(cls.p)
        cls.base = cls.res['scenarios']['base']

    def score(self, today, facts=FACTS, **kw):
        return FT.score('SYN', kw.pop('params', self.p), self.res, payload(facts), today, **kw)

    def test_model_path_reads_the_lean_result(self):
        path = FT.model_path(self.p, self.res)
        self.assertEqual(path['base_end'].isoformat(), '2026-12-31')
        self.assertAlmostEqual(path['revenue'][2026], 13352.3e6, delta=1e6)
        self.assertEqual((path['growth'][2027], path['margin'][2027]), (0.385, 0.45))
        self.assertEqual(path['eps_operating'][2027], self.base['eps_operating'][2027])
        self.assertEqual(path['value_points'][0][1], self.base['value_today'])
        self.assertEqual(path['value_points'][-1], (path['base_end'].replace(year=2031), self.base['year_end_values'][2031]))

    def test_no_report_since(self):
        r = self.score('2026-11-20')
        self.assertEqual((r['status'], r['read'], r['reported']), ('no_report_since', 'no_report', []))
        self.assertIn('אין דוח מאז', FT._row_he(r))

    def test_annual_filer_without_a_new_annual_report_says_so(self):
        facts = [(REV, '2025-01-01', '2025-12-31', 10000 * M, '2026-04-30', '20-F')]      # no quarters in XBRL
        r = self.score('2026-11-20', facts=facts)
        self.assertEqual((r['status'], r['annual_filer']), ('no_report_since', True))
        self.assertIn('רק פעם בשנה', FT._row_he(r))
        self.assertFalse(self.score('2026-11-20')['annual_filer'])

    def test_completed_base_year_scores_revenue_only(self):
        r = self.score('2027-03-01')
        self.assertEqual(r['status'], 'scored')
        self.assertEqual([(x['fiscal_year'], x['period']) for x in r['reported']], [(2026, 'FY')])
        c = r['completed']
        self.assertAlmostEqual(c['revenue']['error'], 13500 / 13352 - 1, places=4)
        self.assertIsNone(c['margin']['diff_pts'])                 # the model has no margin for its base year
        self.assertIsNone(c['eps_operating']['error'])
        self.assertIn('base year', c['note'])
        self.assertIsNone(r['in_progress'])
        self.assertEqual(r['read'], 'on_track')

    def test_year_in_progress_scores_the_new_part_against_what_the_model_year_still_needed(self):
        r = self.score('2027-05-10')
        ip = r['in_progress']
        model_2027 = self.base['revenue_m'][2027] * M
        self.assertEqual((ip['fiscal_year'], ip['period']), (2027, 'Q1'))
        g = ip['revenue']
        self.assertEqual((g['new_part'], g['new_months'], ip['already_reported']['months']), (4200 * M, 3, 0))
        self.assertAlmostEqual(g['share_of_remainder'], 4200 * M / model_2027)
        self.assertAlmostEqual(g['time_share'], 0.25)
        self.assertAlmostEqual(g['step_per_quarter'], ((model_2027 - 4200 * M) / 3) / (4200 * M) - 1)
        self.assertAlmostEqual(ip['margin']['diff_pts'], 1800 / 4200 - 0.45)
        self.assertIn('no ahead/behind word', ip['notes'][0])
        self.assertEqual(r['read'], 'on_track')                    # the word comes from the completed 2026 year only
        self.assertEqual(r['completed']['fiscal_year'], 2026)
        line = FT._row_he(r)
        self.assertIn('רבעון ראשון של שנת 2027', line)
        self.assertIn('ממה שנשאר במודל לשנה', line)
        q2 = self.score('2027-08-10', since='2027-05-05')['in_progress']             # the second quarter alone
        self.assertEqual((q2['period'], q2['revenue']['new_part'], q2['already_reported']['months']), ('H1', 4800 * M, 3))
        self.assertAlmostEqual(q2['revenue']['share_of_remainder'], 4800 * M / (model_2027 - 4200 * M))
        self.assertAlmostEqual(q2['revenue']['time_share'], 3 / 9)
        self.assertAlmostEqual(q2['margin']['new_part'], (4050 - 1800) / 4800)
        partial_only = self.score('2027-08-10', since='2027-05-05')
        self.assertIsNone(partial_only['completed'])
        self.assertEqual(partial_only['read'], 'partial')          # a partial year alone: numbers, no word

    def test_year_in_progress_in_the_base_year_uses_the_base_revenue_estimate(self):
        p = dict(self.p, as_of='2026-07-01', days_to_base_year_end=183)
        res = lean_model.run(p)
        r = FT.score('SYN', p, res, payload(), '2026-11-10')
        g = r['in_progress']['revenue']
        model = res['scenarios']['base']['revenue_m'][2026] * M
        self.assertEqual((r['in_progress']['fiscal_year'], r['in_progress']['period']), (2026, '9M'))
        self.assertEqual(g['new_part'], 3500 * M)                   # nine months minus the half year reported before
        self.assertAlmostEqual(g['share_of_remainder'], 3500 * M / (model - 6200 * M))
        self.assertAlmostEqual(g['step_per_quarter'], (model - 9700 * M) / (3500 * M) - 1)
        self.assertIsNone(r['in_progress']['margin']['model_year'])  # the model has no margin for its base year
        self.assertEqual(r['read'], 'partial')

    def test_completed_forecast_year_scores_revenue_margin_and_operating_eps(self):
        r = self.score('2028-03-01')
        c = r['completed']
        self.assertEqual(c['fiscal_year'], 2027)
        self.assertAlmostEqual(c['revenue']['error'], 19000e6 / (self.base['revenue_m'][2027] * 1e6) - 1)
        self.assertAlmostEqual(c['margin']['diff_pts'], 8400 / 19000 - 0.45)
        eps = 8400 * (1 - self.p['tax']) / 1260
        self.assertAlmostEqual(c['eps_operating']['actual'], eps)
        self.assertAlmostEqual(c['eps_operating']['error'], eps / self.base['eps_operating'][2027] - 1)
        self.assertIsNone(r['in_progress'])
        self.assertEqual(r['read'], 'ahead')                       # revenue +2.7%, margin within a point
        self.assertIn('שנה מלאה 2027', FT._row_he(r))

    def test_release_date_decides_what_was_reported_after_the_valuation(self):
        late = [f if f[4] != '2026-11-03' else f[:4] + ('2026-11-06', f[5]) for f in FACTS]      # 10-Q filed 2 days after as_of
        facts = late + [(REV, '2025-01-01', '2025-09-30', 7300 * M, '2025-11-04', '10-Q')]
        r = self.score('2026-11-20', facts)
        self.assertEqual(r['status'], 'scored')
        self.assertTrue(r['in_sample_suspected'])                   # no release date: the valuation may hold it already
        self.assertEqual(r['reported'][0]['reported_basis'], 'filing')
        self.assertEqual(FT.summarize([r])['scored_rows'], 0)
        self.assertIn('ייתכן שכבר היה בהערכה', FT._row_he(r))
        # a report filed four weeks after the valuation (a release can precede it by weeks) is suspected too
        slow = [f if f[4] != '2026-11-03' else f[:4] + ('2026-12-02', f[5]) for f in FACTS] + facts[-1:]
        self.assertTrue(self.score('2026-12-10', slow)['in_sample_suspected'])
        before = self.score('2026-11-20', facts, release_dates=['2026-07-30', '2026-10-30'])
        self.assertEqual(before['status'], 'no_report_since')       # released before the valuation, filed after it
        after = self.score('2026-11-20', facts, release_dates=['2026-11-05'])
        self.assertEqual((after['status'], after['in_sample_suspected']), ('scored', False))
        self.assertEqual((after['reported'][0]['reported_on'], after['reported'][0]['reported_basis']), ('2026-11-05', 'release'))
        self.assertEqual(after['in_progress']['revenue']['new_part'], 3500 * M)    # Q3 alone: H1 was public before

    def test_a_year_that_may_be_in_the_valuation_is_not_scored_beside_a_later_report(self):
        p = dict(self.p, as_of='2027-02-10', days_to_base_year_end=-41)             # valued after the year-end results
        res = lean_model.run(p)
        r = FT.score('SYN', p, res, payload(), '2027-06-01')                        # annual report 16.2, first quarter 5.5
        self.assertEqual([x['may_be_in_valuation'] for x in r['reported']], [True, False])
        self.assertFalse(r['in_sample_suspected'])
        self.assertIsNone(r['completed'])                                           # the 2026 year is not scored
        self.assertEqual(r['in_progress']['fiscal_year'], 2027)
        alone = FT.score('SYN', p, res, payload(), '2027-03-01')                    # only the annual report so far
        self.assertTrue(alone['in_sample_suspected'])
        self.assertEqual(alone['completed']['fiscal_year'], 2026)                   # shown, and kept out of the summary
        self.assertEqual(FT.summarize([alone])['scored_rows'], 0)

    def test_a_filing_after_the_scoring_date_is_not_read(self):
        self.assertEqual(self.score('2027-02-10')['status'], 'no_report_since')     # the year ended, filed 16.2
        self.assertEqual(self.score('2027-02-16')['status'], 'scored')

    def test_a_period_off_the_models_fiscal_calendar_is_named(self):
        p = dict(self.p, days_to_base_year_end=148)                                 # a model year ending 31.3.2027
        r = FT.score('SYN', p, lean_model.run(p), payload(), '2027-03-01')
        self.assertEqual(r['status'], 'no_report_since')
        self.assertTrue(any('fiscal calendar' in n for n in r['notes']))
        self.assertFalse(any('fiscal calendar' in n for n in self.score('2027-09-01')['notes']))

    def test_price_against_the_value_path(self):
        v0 = self.base['value_today']
        same_day = self.score('2026-11-04', price={'value': 182.95, 'date': '2026-11-04'})['price']
        self.assertAlmostEqual(same_day['value_path'], v0, places=2)
        self.assertAlmostEqual(same_day['vs_value_path'], 182.95 / v0 - 1, places=4)
        self.assertAlmostEqual(same_day['gap_at_valuation'], 182.95 / v0 - 1, places=4)
        path = FT.model_path(self.p, self.res)
        self.assertAlmostEqual(FT.value_at(path['value_points'], '2027-12-31'), self.base['year_end_values'][2027])
        mid = FT.value_at(path['value_points'], '2027-07-01')
        self.assertTrue(path['value_points'][1][1] < mid < self.base['year_end_values'][2027])
        self.assertIsNone(FT.value_at(path['value_points'], '2026-01-01'))
        later = self.score('2027-01-15', price={'value': 200.0, 'date': '2027-01-14'})['price']
        self.assertEqual(later['age_days'], 71)
        self.assertGreater(later['value_path'], v0)
        split = self.score('2027-01-15', price={'value': 91.0, 'date': '2027-01-14'})      # half the valuation's price
        self.assertIsNone(split['price'])
        self.assertIn('split', split['notes'][0])

    def test_custom_and_remapped_models_are_scored_by_hand(self):
        bank = {'ticker': 'BANK', 'as_of': '2026-11-11', 'price': 1.0, 'scenarios': {'base': {'roe': [0.1]}}}
        r = FT.score('BANK', bank, None, None, '2026-11-20')
        self.assertEqual((r['status'], r['read']), ('custom_model', 'custom'))
        self.assertIn('custom model: scored by hand', r['reason'])
        p = copy.deepcopy(self.p)
        p['scenarios']['base']['m'] = [1.0] * 6                    # 'revenue' that is really profit
        self.assertEqual(FT.score('SYN', p, None, None, '2026-11-20')['status'], 'custom_model')
        self.assertEqual(FT.lean_format(self.p), (True, None))

    def test_other_currency_and_missing_facts_are_not_scored(self):
        r = FT.score('SYN', self.p, self.res, payload(unit='BRL'), '2027-03-01')
        self.assertEqual(r['status'], 'not_scorable')
        self.assertIn('BRL', r['reason'])
        self.assertEqual(FT.score('SYN', self.p, self.res, None, '2027-03-01')['status'], 'not_scorable')

    def test_periods_latest_value_first_filing_and_larger_revenue_tag(self):
        facts = [(REV, '2026-01-01', '2026-12-31', 100.0, '2027-02-01', '10-K'), (REV, '2026-01-01', '2026-12-31', 110.0, '2028-02-01', '10-K'),
                 ('RevenueFromContractWithCustomerExcludingAssessedTax', '2026-01-01', '2026-12-31', 90.0, '2027-01-20', '10-K')]
        row = FT.periods(payload(facts))['revenue'][('2026-01-01', '2026-12-31')]
        self.assertEqual((row['val'], row['first_filed']), (110.0, '2027-01-20'))
        annual = FT.periods(payload([(REV, '2025-01-01', '2025-12-31', 5.0, '2026-04-30', '20-F')]))
        self.assertTrue(annual['annual_only'])
        self.assertFalse(FT.periods(payload())['annual_only'])
        r = FT.score('SYN', self.p, self.res, payload([(REV, '2025-01-01', '2025-12-31', 5.0, '2026-04-30', '20-F')]), '2026-11-20')
        self.assertIn('annual filer', r['notes'][0])

    def test_fiscal_year_tolerates_a_52_53_week_calendar(self):
        p = dict(self.p, as_of='2026-11-07', days_to_base_year_end=-6)             # base year ended 2026-11-01
        path = FT.model_path(p, lean_model.run(p))
        self.assertEqual(FT.fiscal_year('2026-11-01', path)[0], 2026)
        self.assertEqual(FT.fiscal_year('2027-01-31', path)[0], 2027)
        label, end, start = FT.fiscal_year('2027-11-03', path)                    # the next year ends two days 'late'
        self.assertEqual((label, end.isoformat(), start.isoformat()), (2027, '2027-11-01', '2026-11-02'))
        self.assertEqual(FT.fiscal_year('2026-08-02', path)[0], 2026)
        self.assertEqual(len(path['value_points']), 6)                             # no point before as_of

    def test_summary_is_honest_about_n_and_companies(self):
        one = self.score('2027-05-10')
        doc = FT.build([one, self.score('2026-11-20')], [{'ticker': 'CUSTOMCO', 'reason': 'x'}], '2027-05-10')
        s = doc['summary']
        self.assertEqual((s['scored_rows'], s['no_report_since'], s['conclusion']), (1, 1, FT.TOO_FEW))
        self.assertEqual(s['items']['rest_of_year_step']['n'], 1)
        self.assertEqual(s['items']['rest_of_year_step']['conclusion'], FT.TOO_FEW)
        md = FT.render_he(doc)
        self.assertIn('too few to conclude', md)
        self.assertIn('CUSTOMCO', md)
        self.assertIn('| SYN | 2026-11-04 |', md)
        rows = []
        for i in range(8):                                         # eight scored rows with steps of 0..7%
            r = copy.deepcopy(one)
            r['ticker'] = f'S{i % 2}'                              # ... from two companies only
            r['in_progress']['revenue']['step_per_quarter'] = i / 100
            r['price'] = {'price': 1.0, 'date': '2027-05-10', 'value_path': 1.0, 'vs_value_path': i / 10, 'gap_at_valuation': 0.0,
                          'price_at_valuation': 1.0, 'age_days': 187}
            rows.append(r)
        s = FT.summarize(rows)
        it = s['items']['rest_of_year_step']
        self.assertEqual((s['scored_rows'], s['scored_companies'], it['n'], it['companies']), (8, 2, 8, 2))
        self.assertEqual((it['conclusion'], s['conclusion']), (FT.TOO_FEW, FT.TOO_FEW))   # 8 rows, but 2 companies
        for i, r in enumerate(rows):
            r['ticker'] = f'S{i % 3}'
        s = FT.summarize(rows)
        it = s['items']['rest_of_year_step']
        self.assertAlmostEqual(it['median'], 0.035)
        self.assertNotEqual(it['conclusion'], FT.TOO_FEW)
        self.assertNotEqual(s['conclusion'], FT.TOO_FEW)
        self.assertNotEqual(s['items']['price_vs_value_path']['conclusion'], FT.TOO_FEW)
        for r in rows:
            r['price']['age_days'] = 5                             # young valuations: a price gap is not yet a test
        self.assertEqual(FT.summarize(rows)['items']['price_vs_value_path']['conclusion'], FT.TOO_FEW)
        md = FT.render_he(FT.build(rows, [], '2027-05-10'))
        step_line = next(x for x in md.splitlines() if x.startswith('- שנה חלקית: הקפיצה'))
        self.assertNotIn('too few to conclude', step_line)
        self.assertIn('מספר שורות 8', step_line)
        self.assertIn('too few to conclude', next(x for x in md.splitlines() if x.startswith('- מחיר מול מסלול')))
        for r in rows:
            r['mode'] = 'descriptive'                              # frozen after the report: never counted
        self.assertEqual(FT.summarize(rows)['scored_rows'], 0)

    # ---------------------------------------------------------------- frozen theses (lean_freeze into a temp ledger)

    def _freeze(self, root, folder_date, params, clock, next_report='2027-02-16'):
        folder = os.path.join(root, 'research', 'companies', 'SYN', folder_date)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'lean_params.json'), 'w') as f:
            json.dump(params, f)
        with open(os.path.join(folder, 'lean_result.json'), 'w') as f:
            json.dump(json.loads(json.dumps(lean_model.run(params))), f)
        L = LF.open_ledger(os.path.join(root, 'research', 'ledger.jsonl'), clock=lambda: clock)
        return folder, LF.freeze_folder(folder, L, root=root, run=lean_model.run, next_report=next_report, legacy_path='')

    def _cands(self, root):
        L = LF.open_ledger(os.path.join(root, 'research', 'ledger.jsonl'))
        theses = FT.frozen_theses(L.lines())
        cands, custom = script.valuations(root, theses)
        return theses, cands, custom

    def test_scores_the_frozen_ledger_rows_and_flags_an_edited_params_file(self):
        root = tempfile.mkdtemp()
        folder, out = self._freeze(root, '2026-11-04', self.p, datetime(2026, 11, 5, tzinfo=timezone.utc))
        self.assertEqual(out['status'], 'frozen')
        theses, cands, _ = self._cands(root)
        c = cands['SYN'][0]
        self.assertEqual((c['thesis_id'], c['file_check']['holds'], c['path']['source']), (out['thesis_id'], True, 'ledger'))
        self.assertAlmostEqual(c['path']['revenue'][2027], self.base['revenue_m'][2027] * M)
        rows = FT.company_rows('SYN', cands['SYN'], payload(), '2027-03-01')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['mode'], rows[0]['status'], rows[0]['completed']['fiscal_year']), ('test', 'scored', 2026))
        self.assertAlmostEqual(rows[0]['completed']['revenue']['error'], 13500 / self.base['revenue_m'][2026] - 1)
        edited = copy.deepcopy(self.p)                             # someone edits the published params after the freeze
        edited['scenarios']['base']['g'][0] = 0.9
        with open(os.path.join(folder, 'lean_params.json'), 'w') as f:
            json.dump(edited, f)
        _, cands, _ = self._cands(root)
        c = cands['SYN'][0]
        self.assertEqual((c['file_check']['params'], c['file_check']['holds']), ('changed', False))
        self.assertIsNone(c['path']['tax'])                         # the edited file is not read at all
        rows = FT.company_rows('SYN', cands['SYN'], payload(), '2027-03-01')
        self.assertTrue(any('params file changed since the freeze' in n for n in rows[0]['notes']))
        self.assertAlmostEqual(rows[0]['completed']['revenue']['forecast'], self.base['revenue_m'][2026] * M)   # still frozen
        self.assertIn('קבצים שהשתנו אחרי ההקפאה', FT.render_he(FT.build(rows, [], '2027-03-01')))

    def test_three_revisions_before_one_report_count_once_and_a_late_freeze_is_descriptive(self):
        root = tempfile.mkdtemp()
        for i, (d, day) in enumerate((('2026-11-04', 5), ('2026-11-06', 7), ('2026-11-09', 10))):
            p = dict(self.p, as_of=d, days_to_base_year_end=57 - 2 * i, price=180.0 + i)
            self._freeze(root, d, p, datetime(2026, 11, day, tzinfo=timezone.utc))
        theses, cands, _ = self._cands(root)
        self.assertEqual(len(cands['SYN']), 3)
        rows = FT.company_rows('SYN', cands['SYN'], payload(), '2027-03-01')
        scored = [r for r in rows if r['status'] == 'scored']
        self.assertEqual(len(scored), 1)                            # one row for the company and the report
        self.assertEqual((scored[0]['valuation_date'], scored[0]['mode']), ('2026-11-09', 'test'))
        self.assertEqual(FT.summarize(rows)['scored_rows'], 1)
        late = tempfile.mkdtemp()                                   # frozen after the 16.2 annual report
        self._freeze(late, '2026-11-04', self.p, datetime(2027, 2, 20, tzinfo=timezone.utc))
        _, cands, _ = self._cands(late)
        rows = FT.company_rows('SYN', cands['SYN'], payload(), '2027-03-01')
        self.assertEqual(rows[0]['mode'], 'descriptive')
        self.assertIn('frozen on 2027-02-20, after the report', rows[0]['descriptive_reason'])
        self.assertEqual(FT.summarize(rows)['scored_rows'], 0)

    def test_the_weighted_worlds_path_is_scored_and_a_superseded_row_never_is(self):
        root = tempfile.mkdtemp()
        folder, first = self._freeze(root, '2026-11-04', self.p, datetime(2026, 11, 5, tzinfo=timezone.utc))   # main file alone
        low = copy.deepcopy(self.p)                                 # a synthetic second world: slower and thinner
        for s in low['scenarios'].values():
            s['g'] = [g - 0.05 for g in s['g']]
            s['m'] = [x - 0.03 for x in s['m']]
        with open(os.path.join(folder, 'lean_params_low.json'), 'w') as f:
            json.dump(low, f)
        with open(os.path.join(folder, 'worlds.json'), 'w') as f:
            json.dump({'worlds': [{'name': 'main', 'file': 'lean_params.json', 'weight': 0.6},
                                  {'name': 'low', 'file': 'lean_params_low.json', 'weight': 0.4}]}, f)
        L = LF.open_ledger(os.path.join(root, 'research', 'ledger.jsonl'), clock=lambda: datetime(2026, 11, 6, tzinfo=timezone.utc))
        second = LF.freeze_folder(folder, L, root=root, run=lean_model.run, next_report='2027-02-16', legacy_path='')
        self.assertEqual(second['supersedes'], first['thesis_id'])
        theses, cands, _ = self._cands(root)
        self.assertEqual(len(cands['SYN']), 2)                      # both stay on file
        a, b = self.base, lean_model.run(low)['scenarios']['base']
        new = next(c for c in cands['SYN'] if c['thesis_id'] == second['thesis_id'])
        self.assertEqual((new['path']['weighted_worlds'], new['path']['supersedes']), ({'main': 0.6, 'low': 0.4}, first['thesis_id']))
        w_rev = 0.6 * a['revenue_m'][2027] * M + 0.4 * b['revenue_m'][2027] * M
        w_margin = 0.6 * a['op_margin'][2027] + 0.4 * b['op_margin'][2027]
        self.assertAlmostEqual(new['path']['revenue'][2027], w_rev, delta=1)
        self.assertAlmostEqual(new['path']['margin'][2027], w_margin, places=6)
        self.assertTrue(new['file_check']['holds'])
        # the superseded row is never scored, even listed last or with a later recording time than the row replacing it
        old = next(c for c in cands['SYN'] if c['thesis_id'] == first['thesis_id'])
        for cs in (cands['SYN'], [new, old], [new, dict(old, recorded_at='2026-11-07T00:00:00+00:00')]):
            rows = FT.company_rows('SYN', cs, payload(), '2027-05-10')
            scored = [r for r in rows if r['status'] == 'scored']
            self.assertEqual([(r['thesis_id'], r['mode']) for r in scored], [(second['thesis_id'], 'test')] * 2)
            ip = scored[-1]['in_progress']                          # the Q1 2027 report against the weighted 2027 year
            self.assertAlmostEqual(ip['revenue']['model_year'], w_rev, delta=1)
            self.assertAlmostEqual(ip['margin']['model_year'], w_margin, places=6)
            self.assertAlmostEqual(ip['margin']['diff_pts'], 1800 / 4200 - w_margin, places=5)
            self.assertTrue(any('weighted worlds path' in n for n in scored[-1]['notes']))
        self.assertIn('ממוצע משוקלל של העולמות', FT._row_he(scored[-1]))
        # a row that replaces the frozen one only after the report does not take the report: the earlier freeze is the test
        late = dict(new, recorded_at='2027-02-20T00:00:00+00:00')
        rows = FT.company_rows('SYN', [old, late], payload(), '2027-03-01')
        self.assertEqual([(r['thesis_id'], r['mode']) for r in rows if r['status'] == 'scored'], [(first['thesis_id'], 'test')])

    def test_a_folder_never_frozen_is_descriptive_only(self):
        root = tempfile.mkdtemp()
        folder = os.path.join(root, 'research', 'companies', 'SYN', '2026-11-04')
        os.makedirs(folder)
        with open(os.path.join(folder, 'lean_params.json'), 'w') as f:
            json.dump(self.p, f)
        with open(os.path.join(folder, 'lean_result.json'), 'w') as f:
            json.dump(json.loads(json.dumps(self.res)), f)
        os.makedirs(os.path.join(root, 'research', 'companies', 'HEAVY', '2026-11-02'))
        theses, cands, custom = self._cands(root)
        self.assertEqual((cands['SYN'][0]['thesis_id'], cands['SYN'][0]['path']['source']), (None, 'saved result'))
        self.assertEqual([c['ticker'] for c in custom], ['HEAVY'])
        rows = FT.company_rows('SYN', cands['SYN'], payload(), '2027-03-01')
        self.assertEqual(rows[0]['mode'], 'descriptive')
        self.assertIn('never frozen', rows[0]['descriptive_reason'])

    def test_outcomes_are_written_once_through_the_ledger_api(self):
        root = tempfile.mkdtemp()
        self._freeze(root, '2026-11-04', self.p, datetime(2026, 11, 5, tzinfo=timezone.utc))
        theses, cands, _ = self._cands(root)
        by_id = {t['thesis_id']: t for ts in theses.values() for t in ts}
        rows = FT.company_rows('SYN', cands['SYN'], payload(), '2027-03-01')
        oc = FT.outcome_candidates(rows, by_id)
        self.assertEqual([c['forecast_id'].split(':')[-2:] for c in oc], [['revenue', '2026']])   # the base year: revenue only
        self.assertEqual((oc[0]['actual'], oc[0]['observed_at'], oc[0]['basis']), (13500 * M, '2027-02-16', 'reported_revenue'))
        ledger = os.path.join(root, 'research', 'ledger.jsonl')
        orig = LF.open_ledger
        LF.open_ledger = lambda path, clock=None: orig(path, clock=lambda: datetime(2027, 3, 1, tzinfo=timezone.utc))
        try:
            first = script.write_outcomes(ledger, oc)
            second = script.write_outcomes(ledger, oc)
        finally:
            LF.open_ledger = orig
        self.assertEqual((len(first['written']), len(second['written']), len(second['existing'])), (1, 0, 1))
        L = LF.open_ledger(ledger)
        self.assertTrue(L.verify()['ok'])
        self.assertEqual(sum(r.get('type') == 'outcome' for r in L.lines()), 1)
        self.assertEqual(L.score()['resolved'], 1)

    def test_a_custom_scorecard_is_scored_and_a_fired_falsifier_is_listed(self):
        theses = {'custom:BANK:1': {'thesis_id': 'custom:BANK:1', 'as_of': '2026-11-11', 'recorded_at': '2026-11-12T08:00:00+00:00',
                                    'forecasts': [], 'record': {}}}
        sc = {'scorecard_version': 1, 'ticker': 'BANK', 'period': 'Q4 2026', 'period_end': '2026-12-31', 'reported_on': '2027-01-15',
              'sources': [{'title': 'release', 'url': 'https://www.sec.gov/x'}],
              'against': {'thesis_id': 'custom:BANK:1', 'ledger': 'research/ledger.jsonl', 'as_of': '2026-11-11'},
              'items': [{'metric': 'eps', 'model_value': 2.0, 'actual': 2.2, 'unit': 'USD', 'kind': 'actual'},
                        {'metric': 'cet1', 'model_value': 0.11, 'actual': 0.115, 'unit': 'ratio'},
                        {'metric': 'revenue rest of year', 'model_value': 100.0, 'actual': 40.0, 'unit': 'USD', 'kind': 'partial'}],
              'falsifiers': [{'id': 'f1', 'text': 'CET1 below 10%', 'state': 'not_fired'},
                             {'id': 'f2', 'text': 'loan losses above 1%', 'state': 'fired', 'action_if_fired': 'exit', 'acted_on': False},
                             {'id': 'f3', 'text': 'ROE above 15% in 2028', 'state': 'not_measurable'}]}
        self.assertEqual(FT.check_scorecard(sc), [])
        r = FT.score_scorecard(sc, theses)
        self.assertEqual((r['mode'], r['status']), ('test', 'scorecard'))
        self.assertAlmostEqual(r['items'][0]['error'], 0.1)
        self.assertAlmostEqual(r['items'][1]['diff_pts'], 0.005)
        self.assertEqual((r['items'][2]['error'], r['items'][2]['share']), (None, 0.4))   # a part of a year: a share only
        fl = FT.falsifier_list([r])
        self.assertEqual([(f['id'], f['state']) for f in fl], [('f1', 'not_fired'), ('f2', 'fired'), ('f3', 'not_measurable')])
        self.assertTrue(fl[1]['needs_reason'])                     # a fired exit not acted on needs a one-line reason
        self.assertTrue(any('reason_not_acted' in n for n in r['notes']))
        md = FT.render_he(FT.build([r], [], '2027-01-20'))
        self.assertIn('f2: loan losses above 1% | הופעל (חסרה סיבה לאי-פעולה)', md)
        sc['falsifiers'][1]['reason_not_acted'] = 'the loss came from one sold portfolio'
        self.assertFalse(FT.score_scorecard(sc, theses)['falsifiers'][1]['needs_reason'])
        late = dict(theses['custom:BANK:1'], recorded_at='2027-01-16T08:00:00+00:00')
        self.assertEqual(FT.score_scorecard(sc, {'custom:BANK:1': late})['mode'], 'descriptive')
        self.assertEqual(FT.score_scorecard(dict(sc, against={'thesis_id': 'legacy:X'}), theses)['mode'], 'descriptive')
        bad = dict(sc, falsifiers=[{'id': 'f9', 'text': 'x', 'state': 'maybe'}])
        self.assertEqual(FT.score_scorecard(bad, theses)['mode'], 'descriptive')

    def test_new_reports_from_the_filing_index_include_foreign_forms(self):
        recent = {'form': ['6-K', '20-F', '40-F', '8-K', '8-K', '10-Q', '4', 'SC 13G'],
                  'filingDate': ['2026-11-12', '2027-04-30', '2027-03-01', '2027-02-10', '2027-02-11', '2027-02-20', '2026-11-13', '2026-11-14'],
                  'items': ['', '', '', '2.02,9.01', '5.02', '', '', '']}
        ev = FT.filing_events(recent, '2026-11-11', '2027-05-01')
        self.assertEqual([e['form'] for e in ev], ['6-K', '8-K', '10-Q', '40-F', '20-F'])
        root = tempfile.mkdtemp()
        self._freeze(root, '2026-11-04', self.p, datetime(2026, 11, 5, tzinfo=timezone.utc))
        _, cands, _ = self._cands(root)
        rows = FT.company_rows('SYN', cands['SYN'], None, '2026-11-20', filings=FT.filing_events(recent, '2026-11-04', '2026-11-20'))
        self.assertEqual([r['status'] for r in rows], ['report_not_scored', 'not_scorable'])   # a 6-K may be other news:
        self.assertIn('fill a scorecard.json', rows[0]['notes'][0])                            # the newest valuation keeps its row
        self.assertIsNone(rows[0]['mode'])

    def test_a_known_split_rescales_the_close_instead_of_dropping_it(self):
        path = FT.model_path(self.p, self.res)
        px = {'value': 18.3, 'date': '2027-01-14'}                  # a 10-for-1 split after the valuation
        self.assertIsNone(FT.price_check(path, px)[0])
        got, note = FT.price_check(path, px, known_split={'date': '2026-12-01', 'ratio': 10})
        self.assertAlmostEqual(got['price'], 183.0)
        self.assertIn('split of 2026-12-01', note)
        self.assertIsNone(FT.price_check(path, px, known_split={'date': '2026-10-01', 'ratio': 10})[0])   # before the valuation

    def test_review_bias_is_a_warning_row(self):
        mk = lambda t, pre, post: {'ticker': t, 'as_of': '2026-11-10', 'thesis_id': t, 'record': {'review': {
            'pre_review': {'base_today': pre}, 'post_review_base_today': post}}}
        rb = FT.review_changes([mk('A', 100, 110), mk('B', 50, 45), mk('C', 10, 10)])
        self.assertEqual((rb['n'], rb['companies'], rb['conclusion']), (3, 3, FT.TOO_FEW))
        self.assertAlmostEqual(rb['median_change_pct'], 0.0)
        self.assertEqual([x['change'] for x in rb['rows']], [10, -5, 0])


if __name__ == '__main__':
    unittest.main()
