"""market_context.py: country risk (Damodaran CRP x exposure), evidenced exposure mechanisms, market regime line.

FactBooks below are SYNTHETIC except the real ADBE FactBook fixture (no business map in it) used for the pipeline runs;
the geography splits and exposures given to ADBE are synthetic test inputs, not research findings."""
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from fundamental_engine import market_context as M, pipeline, forecast, risk  # noqa: E402
from fundamental_engine.render import hebrew_table, hebrew_paragraph, markdown  # noqa: E402
from test_forecast_financials import bank  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
EV = ['fmp:as_reported_income_annual']          # a real FactBook source id of the fixture (the split itself is synthetic)
T = M.country_table()
US, TW = T['countries']['United States']['crp'], T['countries']['Taiwan']['crp']


def fb(geo=None, assets=None, as_of='2026-10-19', country='US'):
    bm = {'source_id': 'sec:annual_report', 'tables': {}}
    if geo:
        bm['tables']['geography'] = {'revenue': {m: {'2025-12-31': v} for m, v in geo.items()},
                                     'total_revenue': {'2025-12-31': 100.}, 'reconciliation': {'year': '2025-12-31'}}
    if assets:
        bm['assets_by_geography'] = {'as_of': '2025-12-31', 'shares': assets}
    return {'as_of': as_of, 'company': {'ticker': 'SYN', 'currency': 'USD', 'country': country},
            'sources': {'sec:annual_report': {}, 's1': {}}, 'qualitative': [{'id': 'q1'}], 'business_map': bm}


def regime(when='2026-10-22', **vals):
    return {'readings': {s: {'value': v, 'date': when} for s, v in vals.items()}}


class CountryRiskTests(unittest.TestCase):
    def test_packaged_table_is_dated_and_sourced(self):
        self.assertEqual(T['as_of'], '2026-01-05')
        self.assertTrue(T['url'].startswith('https://pages.stern.nyu.edu/~adamodar/'))
        self.assertAlmostEqual(US, .0023)                       # Aa1 since the May 2025 Moody's downgrade
        self.assertAlmostEqual(TW, .0078)
        self.assertAlmostEqual(T['mature_market_erp'] + US, T['us_erp'])
        self.assertIn('Western Europe', T['regions'])

    def test_two_country_example_sixty_us_forty_taiwan(self):
        """add-on = 0.6 x 0 + 0.4 x (CRP_TW - CRP_US) = 0.4 x (0.78% - 0.23%) = 0.22%."""
        cr = M.country_risk(fb(), {'geography': {'shares': {'United States': .6, 'Taiwan': .4}, 'evidence_ids': ['s1'],
                                                 'note': 'synthetic split'}})
        self.assertEqual(cr['status'], 'applied')
        self.assertAlmostEqual(cr['add_on'], .4 * (TW - US), places=12)
        self.assertAlmostEqual(cr['add_on'], .0022, places=12)
        self.assertEqual(cr['channel'], 'revenue')
        self.assertEqual(cr['forecast_input']['evidence_ids'], ['s1'])
        self.assertEqual(cr['forecast_input']['as_of'], T['as_of'])

    def test_disclosed_members_iso_codes_aggregates_and_states(self):
        cr = M.country_risk(fb({'US': 60., 'TW': 30., 'Rest Of World': 10.}), {})
        self.assertAlmostEqual(cr['add_on'], .3 * (TW - US), places=12)
        self.assertAlmostEqual(cr['revenue']['unmapped_share'], .1)
        self.assertTrue(any('name no country' in q for q in cr['open_questions']))     # aggregate: no add-on, an open question
        st = M.country_risk(fb({'TX': 50., 'CA': 30., 'NY': 20.}), {})                 # stpr: members: California, not Canada
        self.assertTrue(st['revenue']['by_state'])
        self.assertEqual({r['mapped_to'] for r in st['revenue']['rows']}, {'United States'})
        self.assertAlmostEqual(st['add_on'], 0.)
        dom = M.country_risk(fb({'Country Of Domicile': 70., 'US': 30.}, country='Taiwan'), {})
        self.assertAlmostEqual(dom['add_on'], .7 * (TW - US), places=12)
        reg = M.country_risk(fb({'US': 50., 'Europe Middle East And Africa': 50.}), {})
        self.assertAlmostEqual(reg['add_on'], .5 * (T['regions']['EMEA']['crp'] - US), places=12)

    def test_no_credit_below_the_us_base_and_overlap_refused(self):
        de = M.country_risk(fb({'DE': 100.}), {})                    # Germany Aaa: CRP 0 < US 0.23%
        self.assertEqual(de['add_on'], 0.)
        # two complete splits on one axis (regions and countries, Oracle / Uber style): the one naming the most is used
        two = M.country_risk(fb({'Americas': 66., 'EMEA': 23., 'Asia Pacific': 11., 'US': 59., 'GB': 4., 'JP': 3., 'Other Countries': 34.}), {})
        self.assertEqual({r['member'] for r in two['revenue']['rows']}, {'Americas', 'EMEA', 'Asia Pacific'})   # the tighter bound
        self.assertAlmostEqual(two['add_on'], .23 * (T['regions']['EMEA']['crp'] - US) + .11 * (T['regions']['Asia']['crp'] - US), places=9)
        self.assertEqual(two['status'], 'partial')                                       # 'Americas' names no country
        self.assertIn('overlapping', two['revenue']['note'])
        ov = M.country_risk(fb({'US': 70., 'Americas': 80., 'TW': 45.}), {})   # overlapping and no subset adds up to the total
        self.assertEqual(ov['status'], 'no_exposure_data')
        self.assertIsNone(ov['forecast_input'])
        self.assertTrue(any('overlapping' in q for q in ov['open_questions']))
        part = M.country_risk(fb({'US': 20.}), {})          # 80% not itemized: the add-on is a lower bound, never 'no exposure'
        self.assertEqual(part['status'], 'partial')
        self.assertAlmostEqual(part['uncovered_share'], .8)
        self.assertIn('רף תחתון', M.country_risk_he({'country_risk': part}))

    def test_operations_channel_and_the_larger_of_the_two(self):
        cr = M.country_risk(fb({'US': 100.}, assets={'TW': .8, 'US': .2}), {})
        self.assertEqual(cr['channel'], 'operations')
        self.assertAlmostEqual(cr['add_on'], .8 * (TW - US), places=12)
        self.assertAlmostEqual(cr['revenue']['add_on'], 0.)
        mixed = M.country_risk(fb({'US': 100.}, assets={'Non Us': .8, 'US': .2}), {})   # an aggregate in the asset table
        self.assertIsNone(mixed['operations'])
        self.assertEqual(mixed['channel'], 'revenue')

    def test_no_exposure_data_no_default_and_point_in_time(self):
        cr = M.country_risk(fb(), {})
        self.assertEqual(cr['status'], 'no_exposure_data')
        self.assertIsNone(cr['add_on'])
        self.assertTrue(cr['open_questions'])
        old = M.country_risk(fb({'TW': 100.}, as_of='2025-06-30'), {})
        self.assertEqual(old['status'], 'table_after_as_of')
        self.assertIsNone(old['forecast_input'])

    def test_override_validation(self):
        bad = [{'shares': {'Taiwan': 1.2}, 'evidence_ids': ['s1'], 'note': 'x'},
               {'shares': {'Taiwan': .6, 'Japan': .6}, 'evidence_ids': ['s1'], 'note': 'x'},
               {'shares': {'Taiwan': .4}, 'evidence_ids': [], 'note': 'x'},
               {'shares': {'Taiwan': .4}, 'evidence_ids': ['nope'], 'note': 'x'},
               {'shares': {'Taiwan': .4}, 'evidence_ids': ['s1']}]
        for g in bad:
            with self.assertRaises(ValueError):
                M.country_risk(fb(), {'geography': g})
        with self.assertRaises(ValueError):
            M.build(fb(), {'unknown': 1})


class ExposureTests(unittest.TestCase):
    def test_validation_needs_evidence_share_and_a_rate_for_margin(self):
        known = {'s1'}
        ok = M.exposures([{'kind': 'china_export_controls', 'share_of_revenue': .25, 'evidence_ids': ['s1'], 'note': 'x'},
                          {'kind': 'china_export_controls', 'share_of_revenue': .05, 'evidence_ids': ['s1'], 'note': 'y'}], known)
        self.assertEqual([x['id'] for x in ok], ['china_export_controls', 'china_export_controls_2'])
        self.assertEqual(ok[0]['shock'], 'revenue')
        self.assertIn('מגבלות יצוא לסין', ok[0]['label_he'])
        for it in ({'kind': 'invasion', 'share_of_revenue': .2, 'evidence_ids': ['s1'], 'note': 'x'},
                   {'kind': 'sanctions', 'share_of_revenue': .2, 'evidence_ids': [], 'note': 'x'},
                   {'kind': 'sanctions', 'share_of_revenue': 0., 'evidence_ids': ['s1'], 'note': 'x'},
                   {'kind': 'sanctions', 'share_of_supply': .2, 'evidence_ids': ['s1'], 'note': 'x'},
                   {'kind': 'tariffs', 'share_of_supply': .4, 'evidence_ids': ['s1'], 'note': 'x'},          # margin: no rate given
                   {'kind': 'sanctions', 'share_of_revenue': .2, 'cost_rate': .1, 'evidence_ids': ['s1'], 'note': 'x'},
                   {'kind': 'sanctions', 'share_of_revenue': .2, 'evidence_ids': ['s1']}):
            with self.assertRaises(ValueError):
                M.exposures([it], known)

    def test_shock_is_sized_only_from_the_evidenced_share(self):
        x = M.exposures([{'kind': 'china_export_controls', 'share_of_revenue': .25, 'evidence_ids': ['s1'], 'note': 'x'}], {'s1'})[0]
        row = {'growth': [.10, .08, .06], 'cash_cost_ratio': .6, 'terminal': {'operating_margin': .3}}
        M.shock_row(row, x, 4, 1)
        self.assertAlmostEqual(row['growth'][0], 1.10 * .75 - 1)          # the first full year loses exactly 25% of revenue
        self.assertEqual(row['growth'][1:], [.08, .06])                   # later years keep their growth on the smaller base
        self.assertEqual(row['cash_cost_ratio'], .6)                      # no operating deleverage assumed
        t = M.exposures([{'kind': 'tariffs', 'share_of_revenue': .4, 'cost_rate': .25, 'evidence_ids': ['s1'], 'note': 'x'},
                         {'kind': 'tariffs', 'share_of_supply': .4, 'cost_rate': .25, 'evidence_ids': ['s1'], 'note': 'x'}], {'s1'})
        r1 = {'growth': .05, 'cash_cost_ratio': [.6, .6], 'terminal': {'operating_margin': .3}}
        M.shock_row(r1, t[0], 2, 0)
        self.assertEqual([round(c, 12) for c in r1['cash_cost_ratio']], [.7, .7])         # + 40% x 25% of revenue
        self.assertAlmostEqual(r1['terminal']['operating_margin'], .2)
        r2 = {'growth': .05, 'cash_cost_ratio': [.6, .5], 'terminal': {'operating_margin': .3}}
        M.shock_row(r2, t[1], 2, 0)
        self.assertEqual([round(c, 12) for c in r2['cash_cost_ratio']], [.66, .55])       # exposed 40% of costs + 25%
        big = M.exposures([{'kind': 'taiwan_supply', 'share_of_supply': 1., 'evidence_ids': ['s1'], 'note': 'x'}], {'s1'})[0]
        r3 = {'growth': [.1], 'cash_cost_ratio': .6, 'terminal': {'operating_margin': .3}}
        edits = M.shock_row(r3, big, 1, 0)
        self.assertEqual(r3['growth'][0], M.GROWTH_FLOOR)
        self.assertTrue(any('clamped' in e for e in edits))


class RegimeTests(unittest.TestCase):
    def test_band_edges(self):
        c = lambda **kw: M.classify_regime(regime(**kw))['levels']
        self.assertEqual(c(BAA10Y=1.74)['credit'], 'tight')
        self.assertEqual(c(BAA10Y=1.75)['credit'], 'normal')
        self.assertEqual(c(BAA10Y=3.09)['credit'], 'wide')
        self.assertEqual(c(BAA10Y=3.10)['credit'], 'stress')
        self.assertEqual(c(VIXCLS=13.9)['volatility'], 'calm')
        self.assertEqual(c(VIXCLS=29.9)['volatility'], 'elevated')
        self.assertEqual(c(VIXCLS=30.)['volatility'], 'high')
        self.assertEqual(c(T10Y2Y=-.01)['curve'], 'inverted')
        self.assertEqual(c(T10Y2Y=0.)['curve'], 'flat')
        self.assertEqual(c(T10Y2Y=1.5)['curve'], 'steep')
        rich = M.classify_regime(regime(BAA10Y=3.5, VIXCLS=35., T10Y2Y=-.5), erp={'value': .034, 'as_of': '2026-10-01'})
        self.assertEqual(set(rich['flags']), {'curve_inverted', 'credit_stress', 'high_volatility', 'market_rich'})
        self.assertEqual(M.classify_regime({}, erp={'value': .051, 'as_of': '2026-10-01'})['levels']['erp'], 'cheap')

    def test_hebrew_line_and_point_in_time(self):
        r = M.classify_regime(regime(DGS10=4.96, BAA10Y=1.46, VIXCLS=19.03, T10Y2Y=.31), erp={'value': .042, 'as_of': '2026-10-01'},
                              engine_market={'value': .0696, 'basis': 'calibration'})
        line = r['line_he']
        for s in ('מצב השוק ב־22.10.2026', 'ריבית ל־10 שנים 4.96%', 'פערי אשראי 1.46% (צרים)', 'תנודתיות (VIX) 19.0 (רגילה)',
                  'עקום התשואות +0.31% (רגיל)', 'פרמיית הסיכון של השוק 4.20% (קרובה לממוצע ההיסטורי)', 'כ־7.0%', 'לא משנה את השווי'):
            self.assertIn(s, line)
        self.assertIn('רקע שוק', r['short_he'])
        mixed = regime(DGS10=4.96)
        mixed['readings']['T10Y2Y'] = {'value': .31, 'date': '2026-10-23'}
        self.assertTrue(M.classify_regime(mixed)['line_he'].startswith('מצב השוק ב־22.10.2026–23.10.2026: '))
        late = M.classify_regime(regime(DGS10=4.96), as_of='2026-10-19')
        self.assertIsNone(late['line_he'])
        self.assertEqual(late['dropped_after_as_of'], ['DGS10'])
        self.assertIsNone(M.classify_regime({})['line_he'])

    def test_cli_macro_readings_move_no_value(self):
        """--macro (with_regime) adds the regime alone: a company with a readable geography gets no country-risk row from it;
        --country-risk (with_country_risk) or the company's own market_context switches the row on."""
        r = {'readings': {'DGS10': {'value': 4.96, 'date': '2026-10-16'}}}
        f = fb({'TW': 100.})
        only = M.with_regime({'forecast': {}}, r)
        self.assertIs(only['market_context']['country_risk'], False)
        self.assertEqual(M.build(f, only['market_context'])['country_risk']['status'], 'not_requested')
        self.assertIsNone(M.build(f, only['market_context'])['country_risk']['forecast_input'])
        on = M.with_regime(M.with_country_risk({'forecast': {}}), r)
        self.assertNotIn('country_risk', on['market_context'])
        self.assertAlmostEqual(M.build(f, on['market_context'])['country_risk']['add_on'], TW - US, places=12)
        own = M.with_regime({'market_context': {'exposures': []}}, r)
        self.assertNotIn('country_risk', own['market_context'])
        off = M.with_country_risk({'market_context': {'country_risk': False}})
        self.assertIs(off['market_context']['country_risk'], False)
        self.assertEqual(M.with_regime({'market_context': {'regime': {'readings': {}}}}, r)['market_context']['regime'], {'readings': {}})

    def test_read_regime_from_saved_files(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'DGS10.csv').write_text('observation_date,DGS10\n2025-10-15,4.02\n2026-10-16,5.10\n2026-10-19,.\n2026-10-20,5.12\n')
            Path(d, 'MANIFEST.json').write_text(json.dumps({'retrieved_at_utc': '2026-10-25T08:00:00+00:00', 'calls': [
                {'series': 'DGS10', 'file': 'DGS10.csv', 'url': M.FRED_URL.format('DGS10'), 'retrieved_at_utc': '2026-10-25T08:00:00+00:00'}]}))
            r = M.read_regime(d, '2026-10-19')
            self.assertEqual(r['readings']['DGS10']['value'], 5.10)          # '.' skipped, nothing after as_of
            self.assertEqual(r['readings']['DGS10']['date'], '2026-10-16')
            self.assertEqual(r['readings']['DGS10']['year_ago']['value'], 4.02)
            self.assertEqual(set(r['missing']), {'BAA10Y', 'VIXCLS', 'T10Y2Y'})
            inp = M.with_regime({'forecast': {}}, r)
            self.assertEqual(inp['market_context']['regime'], r)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plain = pipeline.run(deepcopy(ADBE))
        geo = {'shares': {'United States': .6, 'Taiwan': .4}, 'evidence_ids': EV, 'note': 'synthetic split for the test'}
        cls.crp = pipeline.run(deepcopy(ADBE), {'market_context': {'geography': geo}})
        cls.reg = pipeline.run(deepcopy(ADBE), {'market_context': {'regime': regime('2026-10-16', DGS10=5.10, BAA10Y=1.46, VIXCLS=19.03,
                                                                                  T10Y2Y=.31)}})
        cls.exp = pipeline.run(deepcopy(ADBE), {'market_context': {'exposures': [
            {'kind': 'china_export_controls', 'share_of_revenue': .25, 'evidence_ids': EV, 'note': 'synthetic exposure for the test'}]}})

    def test_no_input_invariance(self):
        again = pipeline.run(deepcopy(ADBE), {})
        self.assertEqual(again['casefile_sha256'], self.plain['casefile_sha256'])
        self.assertNotIn('market_context', self.plain)
        self.assertNotIn('market_context', self.plain['stages'])
        comps = self.plain['fc']['cost_of_equity']['components']
        self.assertNotIn('adjustment_country_risk', comps)

    def test_country_risk_row_in_the_cost_of_equity_and_the_terminal(self):
        a, b = self.plain['fc']['cost_of_equity']['components'], self.crp['fc']['cost_of_equity']['components']
        row = next(x for x in b['adjustments'] if x['factor'] == 'country_risk')
        self.assertAlmostEqual(row['value'], .4 * (TW - US), places=12)
        self.assertAlmostEqual(b['table_rate'] - a['table_rate'], row['value'], places=12)
        self.assertIn('country_risk', b['terminal']['kept'])                          # structural: the perpetuity keeps it
        self.assertAlmostEqual(b['terminal']['table_rate'] - a['terminal']['table_rate'], row['value'], places=12)
        led = next(x for x in self.crp['fc']['assumptions'] if x['id'] == 'cost_of_equity')
        self.assertIn('country_risk +0.22%', led['rationale'])
        self.assertIn('input:country_risk', led['source_ids'])
        self.assertLess(self.crp['val']['result']['annual_values'][0]['base'], self.plain['val']['result']['annual_values'][0]['base'])
        comp = {c['component']: c['value'] for c in self.crp['dc']['expected_return']['premium_components']}
        self.assertAlmostEqual(comp['country_risk'], row['value'])
        self.assertAlmostEqual(self.crp['dc']['expected_return']['hurdle'] - self.plain['dc']['expected_return']['hurdle'], row['value'], places=9)
        self.assertIn('סיכון מדינה', hebrew_table(self.crp))
        self.assertIn('## Market context', markdown(self.crp))

    def test_country_risk_input_is_validated(self):
        p = self.plain
        good = {'value': .002, 'as_of': '2026-01-05', 'channel': 'revenue', 'title': 't', 'url': 'u', 'detail': 'd', 'evidence_ids': EV}
        for bad in ({**good, 'value': .5}, {**good, 'as_of': '2026-12-01'}, {**good, 'evidence_ids': []}, {**good, 'extra': 1}):
            with self.assertRaises(ValueError):
                forecast.build(ADBE, p['an'], p['card'], p['qa'], {'country_risk': bad})

    def test_regime_is_context_only(self):
        for k in ('val', 'dc', 'fc', 'rk'):
            a, b = json.dumps(self.plain[k], sort_keys=True, default=str), json.dumps(self.reg[k], sort_keys=True, default=str)
            self.assertEqual(a, b, k)                                  # no value, rate, scenario or verdict reads the regime
        self.assertEqual(self.reg['market_context']['country_risk']['status'], 'no_exposure_data')   # ADBE fixture: no geography
        self.assertIn('| מצב השוק ב־16.10.2026 (רקע) | ריבית ל־10 שנים 5.10%; פערי אשראי 1.46% (צרים)', hebrew_table(self.reg))
        self.assertNotEqual(hebrew_table(self.reg), hebrew_table(self.plain))
        self.assertTrue(hebrew_paragraph(self.reg))

    def test_exposure_becomes_a_named_failure_scenario_and_counter_thesis(self):
        self.assertEqual(json.dumps(self.plain['val'], sort_keys=True, default=str), json.dumps(self.exp['val'], sort_keys=True, default=str))
        sc = next(s for s in self.exp['rk']['failure_scenarios'] if s['name'] == 'exposure:china_export_controls')
        base = self.plain['val']['result']['annual_values'][0]['base']
        self.assertEqual(sc['status'], 'computed')
        self.assertAlmostEqual(sc['base_value'], base)
        self.assertLess(sc['base_value_with_shock'], base)
        self.assertLess(sc['value_per_share'], self.plain['val']['result']['annual_values'][0]['bear'])
        self.assertTrue(any('25.0%' in e for e in sc['base_edits']))
        reg = next(r for r in self.exp['rk']['register'] if r['id'] == 'geo.exposure.china_export_controls')
        self.assertEqual(reg['pricing_status'], 'not_priced')
        self.assertIn('failure scenario exposure:china_export_controls', reg['pricing_basis'])
        self.assertIn('מגבלות יצוא לסין', reg['description_he'])
        # sized from the share alone: a larger evidenced share costs more, the same share the same
        spec = self.plain['fc']['spec']
        price = self.plain['val']['result']['annual_values'][0]['current_quote']
        x = M.exposures([{'kind': 'china_export_controls', 'share_of_revenue': .5, 'evidence_ids': EV, 'note': 'x'}], set(EV))[0]
        v50, _, _, _ = risk._run_variant(risk.exposure_variant(spec, x, 'base')[0], price)
        self.assertLess(v50, sc['base_value_with_shock'])
        geo = next(q for q in self.exp['analyst_sheet']['questions'] if q['key'] == 'geopolitics')
        self.assertEqual(geo['status'], 'evidenced')
        self.assertIn('זה תרחיש, לא הסתברות', geo['reading_he'])
        self.assertIn(f"חשיפה: מגבלות יצוא לסין (כ־25% מההכנסות): אם זה יקרה, השווי בתרחיש הסביר נמוך בכ־{sc['base_effect']:.0%}",
                      hebrew_table(self.exp))
        self.assertAlmostEqual(sc['base_effect'], 1 - sc['base_value_with_shock'] / base)

    def test_bank_route_takes_the_same_country_risk_row(self):
        geo = {'shares': {'United States': .6, 'Taiwan': .4}, 'evidence_ids': ['syn'], 'note': 'synthetic split for the test'}
        a, b = pipeline.run(bank()), pipeline.run(bank(), {'market_context': {'geography': geo}})
        self.assertEqual((a['fc']['method'], b['fc']['method']), ('residual_income', 'residual_income'))
        ca, cb = a['fc']['cost_of_equity']['components'], b['fc']['cost_of_equity']['components']
        self.assertNotIn('adjustment_country_risk', ca)
        self.assertAlmostEqual(cb['adjustment_country_risk'], .4 * (TW - US), places=12)
        self.assertAlmostEqual(cb['table_rate'] - ca['table_rate'], .4 * (TW - US), places=12)
        self.assertIn('country risk', cb['formula'])
        led = next(x for x in b['fc']['assumptions'] if x['id'] == 'cost_of_equity')
        self.assertIn('input:country_risk', led['source_ids'])
        self.assertLess(pipeline.summary(b)['base_value'], pipeline.summary(a)['base_value'])

    def test_failed_market_context_is_recorded_not_applied(self):
        cf = pipeline.run(deepcopy(ADBE), {'market_context': {'exposures': [{'kind': 'tariffs', 'share_of_supply': .4,
                                                                             'evidence_ids': EV, 'note': 'no rate'}]}})
        self.assertEqual(cf['stages']['market_context'], 'failed')
        self.assertTrue(any(e['stage'] == 'market_context' for e in cf['errors']))
        self.assertEqual(json.dumps(cf['val'], sort_keys=True, default=str), json.dumps(self.plain['val'], sort_keys=True, default=str))


class BrainTests(unittest.TestCase):
    def test_protocol_registered_and_packaged(self):
        from fundamental_engine.supervisor import packet
        p = packet('market_context')
        self.assertTrue(p['content'].startswith('#'))
        self.assertIn('evidence_ids', p['content'])
        cfg = (ROOT / 'pyproject.toml').read_text()
        self.assertIn('brain/*.json', cfg)                       # country_risk.json ships with the package
        self.assertTrue(os.path.exists(M.COUNTRY_RISK_FILE))


if __name__ == '__main__':
    unittest.main()
