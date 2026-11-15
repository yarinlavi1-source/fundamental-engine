"""forecast_tracker.py on SYNTHETIC data only (no network).

The model is the lean_model fixture (tests/fixtures/lean_model_anet.json) run through scripts/lean_model.py; every
'reported' number below is invented test data, not a company result.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import forecast_tracker as script  # noqa: E402
import lean_model  # noqa: E402
from fundamental_engine import forecast_tracker as FT  # noqa: E402

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

    def test_year_in_progress_compares_ytd_growth_with_the_full_year_path(self):
        r = self.score('2027-05-10')
        ip = r['in_progress']
        self.assertEqual((ip['fiscal_year'], ip['period']), (2027, 'Q1'))
        self.assertAlmostEqual(ip['revenue_growth']['ytd_actual'], 0.40)
        self.assertEqual(ip['revenue_growth']['model_full_year'], 0.385)
        self.assertAlmostEqual(ip['revenue_growth']['diff_pts'], 0.015)
        self.assertAlmostEqual(ip['margin']['diff_pts'], 1800 / 4200 - 0.45)
        self.assertIn('partial year', ip['note'])
        self.assertEqual(r['read'], 'behind')                      # revenue within 2 points, margin 2.1 points below
        self.assertEqual(r['completed']['fiscal_year'], 2026)      # the completed year stays in the row
        line = FT._row_he(r)
        self.assertIn('רבעון ראשון של שנת 2027', line)
        self.assertIn('במודל לשנה מלאה', line)
        r = self.score('2027-08-10')
        self.assertEqual((r['in_progress']['period'], r['read']), ('H1', 'ahead'))

    def test_year_in_progress_in_the_base_year_uses_the_base_revenue_estimate(self):
        p = dict(self.p, as_of='2026-07-01', days_to_base_year_end=183)
        r = FT.score('SYN', p, lean_model.run(p), payload(), '2026-11-10')
        g = r['in_progress']['revenue_growth']
        self.assertEqual((r['in_progress']['fiscal_year'], r['in_progress']['period']), (2026, '9M'))
        self.assertIsNone(g['ytd_actual'])                          # no 2025 nine-month comparative in the facts: a gap
        self.assertAlmostEqual(g['model_full_year'], 13352.3 / 10000 - 1, places=3)
        self.assertIsNone(r['in_progress']['margin']['model_full_year'])
        self.assertEqual(r['read'], 'not_scorable')

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
        self.assertAlmostEqual(after['in_progress']['revenue_growth']['ytd_actual'], 9700 / 7300 - 1)
        self.assertAlmostEqual(after['in_progress']['revenue_growth']['model_full_year'], 13352.3 / 10000 - 1, places=3)

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

    def test_summary_is_honest_about_n(self):
        one = self.score('2027-05-10')
        doc = FT.build([one, self.score('2026-11-20')], [{'ticker': 'CUSTOMCO', 'reason': 'x'}], '2027-05-10')
        s = doc['summary']
        self.assertEqual((s['scored_rows'], s['no_report_since'], s['conclusion']), (1, 1, FT.TOO_FEW))
        self.assertEqual(s['items']['revenue_growth_year_in_progress']['n'], 1)
        self.assertEqual(s['items']['revenue_growth_year_in_progress']['conclusion'], FT.TOO_FEW)
        md = FT.render_he(doc)
        self.assertIn('too few to conclude', md)
        self.assertIn('CUSTOMCO', md)
        self.assertIn('| SYN | 2026-11-04 |', md)
        rows = []
        for i in range(8):                                         # eight scored rows with growth gaps of 0..7 points
            r = copy.deepcopy(one)
            r['ticker'] = f'S{i}'
            r['in_progress']['revenue_growth']['diff_pts'] = i / 100
            r['price'] = {'price': 1.0, 'date': '2027-05-10', 'value_path': 1.0, 'vs_value_path': i / 10, 'gap_at_valuation': 0.0,
                          'price_at_valuation': 1.0, 'age_days': 187}
            rows.append(r)
        s = FT.summarize(rows)
        it = s['items']['revenue_growth_year_in_progress']
        self.assertEqual((s['scored_rows'], s['scored_companies'], it['n']), (8, 8, 8))
        self.assertAlmostEqual(it['median'], 0.035)
        self.assertNotEqual(it['conclusion'], FT.TOO_FEW)
        self.assertNotEqual(s['conclusion'], FT.TOO_FEW)
        self.assertNotEqual(s['items']['price_vs_value_path']['conclusion'], FT.TOO_FEW)
        for r in rows:
            r['price']['age_days'] = 5                             # young valuations: a price gap is not yet a test
        self.assertEqual(FT.summarize(rows)['items']['price_vs_value_path']['conclusion'], FT.TOO_FEW)
        md = FT.render_he(FT.build(rows, [], '2027-05-10'))
        growth_line = next(x for x in md.splitlines() if x.startswith('- צמיחת הכנסות מתחילת השנה'))
        self.assertNotIn('too few to conclude', growth_line)
        self.assertIn('מספר שורות 8', growth_line)
        self.assertIn('too few to conclude', next(x for x in md.splitlines() if x.startswith('- מחיר מול מסלול')))

    def test_script_finds_the_valuations_and_the_custom_companies(self):
        root = tempfile.mkdtemp()
        for rel in ('AAA/2026-11-01/lean_params.json', 'AAA/2026-11-01/lean_params_variant.json', 'AAA/2026-11-09/lean_params.json',
                    'BBB/2026-11-02/casefile_core.json', 'CCC/2026-11-03/lean_params_only_variant.json'):
            p = os.path.join(root, 'research', 'companies', rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w') as f:
                f.write('{}')
        entries, custom = script.valuations(root)
        self.assertEqual([(e['ticker'], e['folder_date'], os.path.basename(e['path'])) for e in entries],
                         [('AAA', '2026-11-01', 'lean_params.json'), ('AAA', '2026-11-09', 'lean_params.json'),
                          ('CCC', '2026-11-03', 'lean_params_only_variant.json')])
        self.assertEqual([os.path.basename(x) for x in entries[0]['variants_not_scored']], ['lean_params_variant.json'])
        self.assertEqual([c['ticker'] for c in custom], ['BBB'])
        self.assertIn('custom model: scored by hand', custom[0]['reason'])


if __name__ == '__main__':
    unittest.main()
