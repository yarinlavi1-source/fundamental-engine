"""WP6 of the econ layer: process and expansion evidence (design goal 4: moat and expansion as evidence to cite, never a
rating).

The numbers are the analyst posts' figures in tests/fixtures/econ (arithmetic fixtures, not truth; the WP0 `expected`
blocks are never engine inputs); the rules are the frozen screens (docs/prereg_econ.md section 1, brain/economics.json
rules). Where a test needs a value the post did not give it is marked SYNTHETIC. Every behaviour test fails without WP6
(no econ.process_evidence / econ.expansion, no process_evidence reading, cash_conversion flag or top-N measure in the
review); the no-wiring guard passes on the base by design.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from fundamental_engine import econ, model_card as M
from test_econ import MODELS, case, run, unsourced

ROOT = Path(__file__).resolve().parents[1]


def reading(review, check):
    hits = [r for r in review['readings'] if r['check'] == check]
    assert len(hits) == 1, (check, len(hits))
    return hits[0]


def measure(review, check):
    hits = [m for m in review['measures'] if m['check'] == check]
    assert len(hits) == 1, (check, len(hits))
    return hits[0]


def flags(review, key):
    return [f for f in review['flags'] if f['key'] == key]


def not_run(review, check):
    return [x['reason'] for x in review['checks_not_run'] if x['check'] == check]


def synthetic_process(**over):
    """SYNTHETIC: a consumer company with one category that never fell, R&D intensity rising 2.2pp while revenue growth
    slows from 10% to 4.5% a year (the shape the H3 iRobot control must not read as `working`)."""
    c = {'company': 'SYN', 'period_end': '2023-12-31', 'as_of': '2024-02-20', 'currency': 'USD', 'period': 'year',
         'sources': {'k10': {'kind': 'issuer_filing', 'available_at': '2024-02-20'}},
         'lines': {'revenue': {'value': 1150.0, 'prior': 1100.0, 'period': 'year', 'source_ids': ['k10']}},
         'categories': [{'name': 'Robots', 'revenue': {'2021': 600.0, '2022': 640.0, '2023': 650.0}, 'source_ids': ['k10']}],
         'history': {'revenue': {'2021': 1000.0, '2022': 1100.0, '2023': 1150.0, 'source_ids': ['k10']},
                     'research_and_development': {'2021': 100.0, '2023': 140.0, 'source_ids': ['k10']}}}
    c.update(over)
    return c


class ProcessEvidenceTests(unittest.TestCase):
    """Design WP6 'SN process'."""

    @classmethod
    def setUpClass(cls):
        cls.r = run('sn_2025')
        cls.p = reading(cls.r, 'process_evidence')

    def test_sn_cleaning_falls_and_makes_a_new_high_within_four_years(self):
        cleaning = next(c for c in self.p['categories'] if c['name'] == 'Cleaning')
        e, = cleaning['episodes']
        self.assertEqual((e['peak_year'], e['trough_year'], e['high_year']), ('2021', '2023', '2025'))
        self.assertAlmostEqual(e['drawdown'], 1.80 / 1.95 - 1, places=12)
        self.assertEqual(round(100 * e['drawdown'], 1), -7.7)
        self.assertAlmostEqual(e['recovery'], 2.20 / 1.80 - 1, places=12)
        self.assertEqual(round(100 * e['recovery'], 1), 22.2)
        self.assertAlmostEqual(e['new_high_over_prior_peak'], 2.20 / 1.95 - 1, places=12)
        self.assertEqual(round(100 * e['new_high_over_prior_peak'], 1), 12.8)
        self.assertEqual((e['years_to_new_high'], e['renewed'], cleaning['renewed']), (4, True, True))   # 4 years: inclusive
        self.assertEqual(cleaning['missing_years'], ['2022', '2024'])                                   # never interpolated
        self.assertIn('2022, 2024 have no figure', cleaning['caveat'])
        self.assertEqual(self.p['renewed_categories'], ['Cleaning'])
        self.assertEqual(self.p['new_categories'], ['Food Prep', 'Beauty'])     # the source's label, echoed
        food = next(c for c in self.p['categories'] if c['name'] == 'Food Prep')
        self.assertEqual((food['renewed'], food['growth']), (None, {'2025': 0.316}))   # growth alone shows no fall

    def test_sn_innovation_intensity_flat_while_revenue_grows_twenty_percent(self):
        p = self.p
        rd, ads = p['intensity']['series']
        self.assertEqual((p['intensity']['window'], rd['id'], ads['id']), (['2022', '2025'], 'research_and_development', 'advertising'))
        self.assertAlmostEqual(rd['start'], 215 / 3700, places=12)
        self.assertAlmostEqual(rd['end'], 368 / 6400, places=12)
        self.assertEqual((round(100 * rd['start'], 2), round(100 * rd['end'], 2)), (5.81, 5.75))
        self.assertEqual((rd['trend'], rd['display'], rd['rising_by_rule']), ('flat', '-0.06pp', False))
        self.assertAlmostEqual(p['revenue']['cagr'], (6400 / 3700) ** (1 / 3) - 1, places=12)
        self.assertEqual((round(100 * p['revenue']['cagr'], 1), round(100 * rd['cagr'], 1)), (20.0, 19.6))
        self.assertEqual(p['revenue']['span_years'], 3)
        self.assertAlmostEqual(p['incremental_revenue_per_incremental_rd']['value'], 2700 / 153, places=12)
        self.assertEqual(round(p['incremental_revenue_per_incremental_rd']['value'], 1), 17.6)
        a, = ads['stated']                                          # advertising in 2025 only: 9.9%, trend unknown
        self.assertEqual((a['year'], round(100 * a['intensity'], 1)), ('2025', 9.9))
        self.assertEqual((ads['trend'], ads['missing']), ('unknown', ['2022']))
        self.assertIn('supply advertising for 2022 to read its intensity trend', [q['question'] for q in self.r['open_questions']])
        self.assertEqual(p['revenue']['growth_trend']['value'], None)  # two stated years give one window: no slowdown read

    def test_sn_machine_working_gross_margin_change_and_cash_conversion(self):
        p = self.p
        self.assertEqual(p['machine'], 'working')
        self.assertEqual(p['conditions'], {'renewal': True, 'intensity_flat_or_falling': True, 'revenue_cagr_at_least_min': True,
                                           'intensity_rising_by_rule': None, 'growth_slows': None})
        gm = p['gross_margin']
        self.assertAlmostEqual(gm['change'], 0.11, places=12)
        self.assertEqual(gm['display'], '+11pp')
        self.assertNotIn('stable', json.dumps(p))                     # printed as a change, never "stable"
        f, = flags(self.r, 'cash_conversion')
        self.assertEqual((f['severity'], f['fcf_to_net_income'], f['threshold'], f['threshold_rule']), ('medium', 0.67, 0.8, 'cfo_to_ni_min'))
        self.assertIn('operating cash flow minus capex', f['fcf_basis'])  # the FCF basis is labelled
        self.assertEqual(measure(self.r, 'cash_conversion')['status'], 'fails')
        for check in ('process_evidence', 'cash_conversion'):
            self.assertEqual(not_run(self.r, check), [])
        self.assertNotIn('categories', {x['ref'] for x in self.r['not_read']})
        self.assertIn('cash.cash_to_debt', {x['ref'] for x in self.r['not_read']})   # read by nothing: named, not dropped

    def test_moat_is_never_rated_or_set(self):
        adbe = json.loads((ROOT / 'tests' / 'fixtures' / 'adbe_factbook_2026-10-19.json').read_text(encoding='utf-8'))
        before = M.build(deepcopy(adbe), {}, None)['dimensions']['moat']
        sn = case('sn_2025')
        econ.review(sn, 'consumer_innovator')
        econ.process_evidence(sn['categories'], {k: sn['history'][k] for k in ('revenue', 'research_and_development', 'advertising')},
                              sn['history']['gross_margin'])
        self.assertEqual(M.build(deepcopy(adbe), {}, None)['dimensions']['moat'], before)    # card.dimensions.moat unchanged
        self.assertTrue({'moat', 'rating', 'score', 'assessment'}.isdisjoint(self.p))
        self.assertIn('never a moat rating', self.p['boundary'])
        import fundamental_engine
        pkg = Path(fundamental_engine.__file__).parent                # nothing in the engine reads the evidence
        callers = sorted(p.name for p in pkg.glob('*.py') if p.name != 'econ.py'
                         and any(s in p.read_text(encoding='utf-8') for s in ('process_evidence', 'econ.expansion', 'top_n_expansion')))
        self.assertEqual(callers, [])
        for mod in ('model_card.py', 'forecast.py'):                   # the card's moat and the forecast's moat proxy
            text = (pkg / mod).read_text(encoding='utf-8')
            self.assertFalse(any(s in text for s in ('import econ', 'from .econ', 'econ.review')), mod)

    def test_public_function_equals_the_review_reading(self):
        sn = case('sn_2025')
        h = sn['history']
        pub = econ.process_evidence(sn['categories'], {k: h[k] for k in ('revenue', 'research_and_development', 'advertising')},
                                    h['gross_margin'])
        self.assertEqual(pub['questions'], [{'about': 'process_evidence', 'scope': 'total',
                                             'question': 'supply advertising for 2022 to read its intensity trend'}])
        for k in ('questions', 'input_sha256', 'engine_version'):
            pub.pop(k)
        mine = dict(self.p)
        mine.pop('model')
        self.assertEqual(pub, mine)
        self.assertEqual(econ.process_evidence(None, None)['machine'], 'unknown')        # without inputs


class ProcessRuleTests(unittest.TestCase):
    """The frozen screens at their edges (SYNTHETIC series)."""

    def cleaning(self, series):
        return econ.process_evidence([{'name': 'C', 'revenue': series}], None)['categories'][0]

    def test_renewal_window_drawdown_and_open_episodes(self):
        self.assertTrue(self.cleaning({'2021': 1.95, '2023': 1.80, '2025': 2.20})['renewed'])      # 4 years: inside
        late = self.cleaning({'2021': 1.95, '2023': 1.80, '2026': 2.20})                           # 5 years: outside
        self.assertEqual((late['renewed'], late['episodes'][0]['years_to_new_high']), (False, 5))
        shallow = self.cleaning({'2021': 1.95, '2022': 1.88, '2023': 2.00})                        # -3.6%: no fall
        self.assertEqual((shallow['renewed'], shallow['episodes']), (False, []))
        self.assertTrue(self.cleaning({'2021': 2.00, '2022': 1.90, '2023': 2.01})['renewed'])     # exactly -5% counts
        pending = self.cleaning({'2023': 2.0, '2024': 1.7, '2025': 1.8})                           # window open to 2027
        self.assertEqual((pending['renewed'], pending['episodes'][0]['high_year']), (None, None))
        shut = self.cleaning({'2019': 2.0, '2021': 1.7, '2024': 1.9})                              # window shut in 2023
        self.assertIs(shut['renewed'], False)
        self.assertEqual(self.cleaning({'FY2024': 1.0})['renewed'], None)                         # one year reads nothing
        two = econ.process_evidence([{'name': 'A', 'revenue': {'2021': 2.0, '2022': 1.8, '2023': 2.1}},
                                     {'name': 'B', 'revenue': {'2021': 1.0, '2022': 1.1}}], None)
        self.assertEqual((two['renewed_categories'], two['conditions']['renewal']), (['A'], True))

    def test_failing_unproven_and_unknown(self):
        r = econ.review(synthetic_process(), 'consumer_innovator')
        p = reading(r, 'process_evidence')
        rd, = p['intensity']['series']
        self.assertAlmostEqual(rd['change'], 140 / 1150 - 100 / 1000, places=12)        # +2.17pp: rising by the rule
        self.assertEqual((rd['trend'], rd['rising_by_rule']), ('rising', True))
        g = p['revenue']['growth_trend']
        self.assertEqual((g['value'], round(g['earlier_growth'], 4), round(g['latest_growth'], 4)), (True, 0.1, 0.0455))
        self.assertEqual((p['conditions']['renewal'], p['machine']), (False, 'failing'))
        self.assertEqual(not_run(r, 'cash_conversion'), [not_run(r, 'cash_conversion')[0]])     # no FCF inputs: named
        self.assertIn('needs a cash block', not_run(r, 'cash_conversion')[0])
        # SYNTHETIC: SN with revenue 5.0bn in 2022 (R&D 290m, intensity flat): CAGR 8.6% < 10% -> unproven, reason named
        sn = case('sn_2025')
        sn['history']['revenue']['2022'], sn['history']['research_and_development']['2022'] = 5.0e9, 290e6
        p = reading(econ.review(sn, 'consumer_innovator'), 'process_evidence')
        self.assertEqual((p['machine'], round(p['revenue']['cagr'], 4)), ('unproven', round((6.4 / 5.0) ** (1 / 3) - 1, 4)))
        self.assertIn('revenue CAGR below rules.machine_growth_min', p['reasons'])
        # SPCX reads consumer_innovator as a secondary model without category or intensity history: unknown, asked for
        spcx = run('spcx_2026q3')
        p = reading(spcx, 'process_evidence')
        self.assertEqual((p['machine'], set(p['conditions'].values())), ('unknown', {None}))
        self.assertTrue(any(q['about'] == 'process_evidence' for q in spcx['open_questions']))

    def test_missing_years_are_never_interpolated_and_blocks_are_point_in_time(self):
        p = econ.process_evidence(None, {'revenue': {'2021': 100.0, '2023': 130.0, '2024': None},
                                         'research_and_development': {'2022': 9.0, '2023': 10.0}})
        rd, = p['intensity']['series']
        self.assertEqual((rd['trend'], rd['missing'], p['revenue']['missing_years']), ('unknown', ['2021'], ['2022', '2024']))
        self.assertEqual(rd['no_revenue_for'], ['2022'])                     # no 2022 revenue: no 2022 intensity either
        self.assertIsNone(p['conditions']['intensity_flat_or_falling'])
        c = case('sn_2025')
        c['sources']['late'] = {'kind': 'issuer_filing', 'available_at': '2026-12-01'}      # SYNTHETIC: after as_of
        c['categories'][0]['source_ids'] = ['late']
        c['history']['research_and_development']['source_ids'] = ['late']
        r = econ.review(c, 'consumer_innovator')
        self.assertLessEqual({'categories[Cleaning]', 'history.research_and_development'}, set(r['unavailable']))
        p = reading(r, 'process_evidence')
        self.assertEqual(([x['name'] for x in p['categories']], p['machine']), (['Food Prep', 'Beauty'], 'unproven'))
        for bad in ({'categories': [{'name': 'A', 'revenue': {'21': 1.0}, 'source_ids': ['analyst_post_sn_2026']}]},
                    {'categories': [{'name': 'A', 'revenue': {'2021': 1.0}, 'source_ids': ['analyst_post_sn_2026']},
                                    {'name': 'a', 'revenue': {'2021': 1.0}, 'source_ids': ['analyst_post_sn_2026']}]},
                    {'categories': [{'name': 'A', 'revenues': {}, 'source_ids': ['analyst_post_sn_2026']}]},
                    {'cash': {'fcf_to_net_income': 0.67, 'free_cash_flow': 1.0, 'source_ids': ['comparison_journal_sn']}}):
            c = case('sn_2025')
            c.update(bad)
            with self.assertRaises(ValueError):
                econ.review(c, 'consumer_innovator')

    def test_cash_conversion_from_lines_and_an_unstated_basis(self):
        c = case('sn_2025')
        del c['cash']['fcf_basis']
        r = econ.review(c, 'consumer_innovator')
        self.assertEqual(flags(r, 'cash_conversion')[0]['fcf_basis'], 'not stated')
        self.assertTrue(any(q['about'] == 'cash_conversion' for q in r['open_questions']))
        # SYNTHETIC: the lines give operating cash flow 900, capex 200 and net income 1,000 -> FCF / NI 0.70
        c = synthetic_process()
        c['lines'].update(operating_cash_flow={'value': 900.0, 'period': 'year', 'source_ids': ['k10']},
                          capex={'value': 200.0, 'period': 'year', 'source_ids': ['k10']},
                          net_income={'value': 1000.0, 'period': 'year', 'source_ids': ['k10']})
        m = measure(econ.review(c, 'consumer_innovator'), 'cash_conversion')
        self.assertEqual((m['free_cash_flow'], m['value'], m['status'], m['fcf_basis']),
                         (700.0, 0.7, 'fails', 'operating cash flow - capex (case lines)'))
        c['lines']['capex']['value'] = 80.0                                   # 0.82: passes, and near 0.80 it says so
        m = measure(econ.review(c, 'consumer_innovator'), 'cash_conversion')
        self.assertEqual((m['status'], m['near']['threshold']), ('passes', 0.8))
        self.assertEqual(flags(econ.review(c, 'consumer_innovator'), 'cash_conversion'), [])


class ExpansionTests(unittest.TestCase):
    """Design WP6 'PLTR top-N' and the software expansion reading."""

    @classmethod
    def setUpClass(cls):
        cls.r = run('pltr_2026q2')
        cls.t = measure(cls.r, 'top_n_expansion')

    def test_pltr_top_n_average_total_and_implied_revenue(self):
        t = self.t
        self.assertEqual((t['n'], t['scope'], t['window']), (20, 'total', 'TTM'))     # TTM, all customers
        self.assertEqual((t['average']['growth'], round(100 * t['average']['levels_growth'], 1)), (0.67, 67.0))
        self.assertAlmostEqual(t['average']['levels_growth'], 124 / 74.25 - 1, places=12)
        self.assertEqual((t['total']['value'], t['total']['from']), (2.48e9, 'n x average'))
        self.assertAlmostEqual(t['implied_revenue']['value'], 6.2e9, places=3)
        self.assertEqual((t['share']['trend'], t['share']['prior']), ('unknown', None))     # prior share absent
        self.assertIn('re-ranked', t['note'])
        self.assertEqual(t['revenue_check']['status'], 'not_computed')                      # no TTM revenue supplied
        self.assertEqual(flags(self.r, 'top_n_inconsistent'), [])
        self.assertEqual(not_run(self.r, 'top_n_inconsistent'), ['no revenue on the top-N scope and window (total, TTM) is supplied'])
        self.assertEqual(not_run(self.r, 'top_n_expansion'), [])

    def test_never_compared_with_the_us_commercial_q_on_q_split(self):
        nc = {x['with']: x for x in self.t['not_compared']}
        split = nc['growth_splits[data_platform_software@us_commercial]']
        self.assertEqual((split['status'], split['scope'], split['window']), ('scope_mismatch', 'us_commercial', 'quarter q/q'))
        self.assertEqual((nc['lines.revenue']['status'], nc['lines.revenue']['window']), ('scope_mismatch', 'quarter'))
        self.assertEqual(self.t['vs_revenue_growth']['status'], 'not_computed')            # nothing computed from them
        e = reading(self.r, 'expansion_led')                                                 # customers x revenue per customer
        self.assertEqual((e['units'], e['units_growth'], round(100 * e['per_unit_growth'], 2)), ('customers', 0.06, 20.75))
        self.assertAlmostEqual(e['per_unit_growth'], 1.28 / 1.06 - 1, places=12)

    def test_supplied_ttm_revenue_more_than_five_percent_away_is_inconsistent(self):
        c = case('pltr_2026q2')            # SYNTHETIC TTM revenue: 6.6bn is 6.45% above 6.2bn, 6.4bn is 3.2% above
        c['ttm_revenue'] = {'value': 6.6e9, 'growth': 0.50, 'source_ids': ['pltr_8k_q2_2026_ex991']}
        r = econ.review(c, 'data_platform_software')
        f, = flags(r, 'top_n_inconsistent')
        self.assertAlmostEqual(f['diff'], 6.6 / 6.2 - 1, places=12)
        self.assertEqual((f['severity'], f['threshold'], f['implied_revenue'], f['supplied']), ('medium', 0.05, 6.2e9, 6.6e9))
        self.assertEqual(f['near']['threshold'], 0.05)                                       # 6.45% sits near 5%: shown
        v = measure(r, 'top_n_expansion')['vs_revenue_growth']                               # the same window: compared
        self.assertAlmostEqual(v['gap_pp'], 124 / 74.25 - 1 - 0.50, places=12)
        self.assertEqual(v['reading'], 'top_n_grew_faster')
        self.assertEqual(not_run(r, 'top_n_inconsistent'), [])
        c['ttm_revenue']['value'] = 6.4e9
        r = econ.review(c, 'data_platform_software')
        self.assertEqual((flags(r, 'top_n_inconsistent'), measure(r, 'top_n_expansion')['revenue_check']['status']), ([], 'consistent'))
        c['ttm_revenue']['scope'] = 'us_commercial'                                          # another scope: never compared
        t = measure(econ.review(c, 'data_platform_software'), 'top_n_expansion')
        self.assertEqual(t['revenue_check']['status'], 'not_computed')
        self.assertIn('ttm_revenue', {x['with'] for x in t['not_compared'] if x['status'] == 'scope_mismatch'})
        c['ttm_revenue']['window'] = 'quarter'
        with self.assertRaises(ValueError):
            econ.review(c, 'data_platform_software')

    def test_top_n_rows_must_share_n_and_window(self):
        c = case('pltr_2026q2')            # SYNTHETIC: the share stated for the top 10
        next(k for k in c['kpis'] if k['id'] == 'top_n_share')['n'] = 10
        r = econ.review(c, 'data_platform_software')
        self.assertEqual([m for m in r['measures'] if m['check'] == 'top_n_expansion'], [])
        self.assertIn('scope_mismatch', not_run(r, 'top_n_expansion')[0])
        c = case('pltr_2026q2')            # SYNTHETIC: N not stated on the share
        del next(k for k in c['kpis'] if k['id'] == 'top_n_share')['n']
        r = econ.review(c, 'data_platform_software')
        self.assertIn('kpis[top_n_share|total|rate]', {x['ref'] for x in r['not_read']})
        t = measure(r, 'top_n_expansion')
        self.assertNotIn('implied_revenue', t)                                               # no share: no implied revenue
        self.assertNotIn('share', t)
        self.assertEqual(not_run(r, 'top_n_inconsistent'), ['the top-N total and share are needed for the implied revenue'])
        c = case('pltr_2026q2')
        c['kpis'] = [k for k in c['kpis'] if not k['id'].startswith('top_n')]
        r = econ.review(c, 'data_platform_software')
        self.assertTrue(not_run(r, 'top_n_expansion') and not_run(r, 'top_n_inconsistent'))
        c = case('pltr_2026q2')
        next(k for k in c['kpis'] if k['id'] == 'top_n_share')['n'] = 0
        with self.assertRaises(ValueError):
            econ.review(c, 'data_platform_software')

    def test_public_expansion_equals_the_review(self):
        c = case('pltr_2026q2')
        split = econ.growth_split(c, 'data_platform_software', 'us_commercial')
        ndr = {'value': 1.57, 'prior': 1.50, 'scope': 'total', 'window': 'TTM', 'ref': 'kpis[net_revenue_retention|total|rate]'}
        top = {'n': 20, 'scope': 'total', 'window': 'TTM', 'average': 124e6, 'prior_average': 74.25e6, 'average_growth': 0.67,
               'share': 0.40, 'refs': {'average': 'kpis[top_n_average_revenue|total|flow]', 'share': 'kpis[top_n_share|total|rate]'},
               'source_ids': ['analyst_post_pltr_2026q2']}
        out = econ.expansion(dict(split, measure='quarter'), top, ndr)
        self.assertEqual(out['expansion_led'], reading(self.r, 'expansion_led'))
        self.assertEqual((round(out['ndr']['change'], 2), out['ndr']['scope']), (0.07, 'total'))
        for k in ('average', 'total', 'share', 'implied_revenue', 'revenue_check'):
            self.assertEqual(out['top_n'][k], self.t[k], k)
        self.assertEqual(out['top_n']['not_compared'], [x for x in self.t['not_compared'] if x['with'].startswith('growth_splits')])
        self.assertEqual(out['checks_not_run'], [{'check': 'top_n_inconsistent', 'reason': self.t['revenue_check']['reason']}])
        self.assertEqual(econ.expansion(dict(split, measure='quarter'), top, ndr), out)     # deterministic
        self.assertIn('never compared across scopes or windows', out['boundary'])
        same = dict(split, scope='total', measure='TTM', window='y/y', line_growth={'growth': 0.48})   # SYNTHETIC same window
        v = econ.expansion(same, top, None)['top_n']['vs_revenue_growth']
        self.assertAlmostEqual(v['gap_pp'], 124 / 74.25 - 1.48, places=12)


class ContractTests(unittest.TestCase):
    def test_every_fixture_is_deterministic_and_every_number_sourced(self):
        for name in MODELS:
            a, b = run(name), run(name)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), name)
            self.assertEqual(unsourced(a, case(name)), [], name)
        p = reading(run('sn_2025'), 'process_evidence')
        self.assertEqual(p['rules'], {k: econ.rules()[k] for k in econ.PROCESS_RULES})
        self.assertLessEqual({p['machine']}, set(econ.MACHINE_STATES))


if __name__ == '__main__':
    unittest.main()
