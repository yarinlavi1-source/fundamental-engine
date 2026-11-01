"""Company-first forecast levers (owner rule 25.10.2026): source tiers on overrides, company evidence over size/type/cross-company
priors, and the visible list of levers still on a default (forecast driver_sources / driver_gaps, the Hebrew table's gap row).

Synthetic companies are TEST DATA ONLY; the ADBE fixture is real 10-K data (tests/fixtures)."""
import os
import sys
import unittest
from copy import deepcopy

sys.path.insert(0, os.path.dirname(__file__))
from test_forecast import ADBE, adbe, grower, industrial, levered, make_an, qa_for, scen, synth  # noqa: E402
from fundamental_engine import forecast, model_card, pipeline  # noqa: E402
from fundamental_engine.render import GAPS_HE, hebrew_table  # noqa: E402

SCEN = ('bear', 'base', 'bull')


def fast_grower():
    """SYNTHETIC profitable $4bn grower: ~59% trailing growth, above the $1-10bn revenue-scale base rate (40%)."""
    rev = [1.0e9, 1.6e9, 2.5e9, 4.0e9]
    annual = {f'{y}-12-31': {'revenue': r, 'operating_income': r * .15, 'sbc': r * .08, 'd_and_a': r * .04, 'capex': r * .05,
                             'cfo': r * .25, 'pretax_income': r * .15, 'income_tax': r * .15 * .21} for y, r in zip(range(2022, 2026), rev)}
    inst = {'2025-12-31': {'cash': 2e9, 'shares_outstanding': 300e6, 'receivables': 600e6, 'payables': 200e6},
            '2022-12-31': {'receivables': 150e6, 'payables': 50e6}}
    fb = synth('2026-03-20', annual, inst, 120.0, kind='filing_derived')
    an = make_an(fb, revenue_growth_last=.6, revenue_cagr_3y=4 ** (1 / 3) - 1, growth_volatility=.05, roic=.3,
                 sales_to_capital_incremental_3y=2.0)
    card = {'business_model': {'primary': 'subscription_software'}, 'stage': {'value': 'growth'}, 'cyclical': {'value': False},
            'funding': {'value': 'self_funded'}, 'valuation': {'method': 'fcff_annual_path', 'archetype': 'software'},
            'dimensions': {'moat': {'assessment': 'adequate'}}}
    return fb, an, card, qa_for(300e6, 2e9, 0., '2025-12-31')


def ov(path, value, tier=None, scenario='base', **extra):
    o = {'path': path, 'scenario': scenario, 'value': value, 'reason': 'test evidence', 'evidence_ids': ['syn:fs'], **extra}
    if tier:
        o['source_tier'] = tier
    return o


def src(fc, lever, s):
    return next(r for r in fc['driver_sources'] if r['lever'] == lever and r['scenario'] == s)


def codes(fc):
    return {w['code'] for w in fc['warnings']}


class SourceTierContractTests(unittest.TestCase):
    def test_tier_is_validated_and_competitor_needs_named_competitors(self):
        fb, an, card, qa = industrial()
        for bad, msg in ((ov('mature_margin', .2, 'rumour'), 'source_tier'),
                         (ov('mature_margin', .2, 'competitor'), 'named direct competitors'),
                         (ov('mature_margin', .2, 'competitor', competitors=[]), 'named direct competitors'),
                         (ov('mature_margin', .2, 'competitor', competitors=['']), 'named direct competitors'),
                         (ov('mature_margin', .2, 'guidance', competitors=['XYZ']), 'only with source_tier')):
            with self.assertRaisesRegex(ValueError, msg):
                forecast.build(fb, an, card, qa, {'overrides': [bad]})
        fc = forecast.build(fb, an, card, qa, {'overrides': [ov('mature_margin', .2, 'competitor', competitors=['PEER1', 'PEER2']),
                                                             ov('sales_to_capital', 2.5)]})
        rec = {(o['path'], o['scenario']): o for o in fc['overrides']}
        self.assertEqual(rec[('mature_margin', 'base')]['source_tier'], 'competitor')
        self.assertEqual(rec[('mature_margin', 'base')]['competitors'], ['PEER1', 'PEER2'])
        self.assertEqual(rec[('sales_to_capital', 'base')]['source_tier'], 'analyst')       # absent = analyst
        self.assertEqual(src(fc, 'mature_margin', 'base')['tier'], 'competitor')
        self.assertEqual(src(fc, 'sales_to_capital', 'base')['tier'], 'analyst')
        self.assertIn('named competitors PEER1, PEER2', next(r for r in fc['assumptions'] if r['id'] == 'override_1')['rationale'])


class CompanyEvidenceBeatsPriorsTests(unittest.TestCase):
    def test_scale_cap_binds_only_without_company_evidence(self):
        fb, an, card, qa = fast_grower()
        plain = forecast.build(fb, an, card, qa)
        g = plain['anchors']['growth']
        self.assertEqual(g['scale_cap']['cap'], .4)
        self.assertAlmostEqual(g['trailing_anchor'], .4)
        self.assertIn('growth_scale_capped', codes(plain))
        self.assertEqual(src(plain, 'growth_anchor', 'base')['default_class'], 'SIZE')
        self.assertIn('growth_anchor', [x['lever'] for x in plain['driver_gaps']])
        # a legacy (untiered) override keeps every earlier behaviour: the size cap still bounds the trailing anchor
        legacy = forecast.build(fb, an, card, qa, {'overrides': [ov('growth_anchor', .55)]})
        self.assertAlmostEqual(legacy['anchors']['growth']['trailing_anchor'], .4)
        self.assertIn('growth_scale_capped', codes(legacy))
        for tier in ('guidance', 'contracted', 'consensus', 'own_history'):
            fc = forecast.build(fb, an, card, qa, {'overrides': [ov('growth_anchor', .55, tier)]})
            g = fc['anchors']['growth']
            self.assertGreater(g['trailing_anchor'], .55, tier)                 # the company's own trailing growth, uncapped
            self.assertAlmostEqual(g['trailing_anchor'], g['scale_cap']['raw_anchor'])
            self.assertGreater(fc['anchors']['current_fiscal_year']['growth'], .4)
            self.assertEqual(fc['paths']['base'][1]['growth'], .55)
            self.assertNotIn('growth_scale_capped', codes(fc))
            self.assertNotIn('growth_start_capped', codes(fc))
            self.assertIn('growth_scale_cap_not_applied', codes(fc))           # the base rate stays a warning row
            r = src(fc, 'growth_anchor', 'base')
            self.assertEqual((r['tier'], r['default_class'], r['evidence_ids']), (tier, None, ['syn:fs']))

    def test_evidenced_half_life_sets_bear_and_bull_not_the_stage_table(self):
        fb, an, card, qa = grower()                     # card stage growth: stage half-life 2.5 years

        def ratio(fc, s):
            p, gT = fc['paths'][s], scen(fc['spec'], s)['terminal']['growth']
            return (p[3]['growth'] - gT) / (p[2]['growth'] - gT)
        for tier, hl in (('own_history', 6.), (None, 2.5)):
            fc = forecast.build(fb, an, card, qa, {'overrides': [ov('growth_half_life', 6., tier)]})
            self.assertAlmostEqual(ratio(fc, 'base'), .5 ** (1 / 6.), places=3)
            self.assertAlmostEqual(ratio(fc, 'bear'), .5 ** (1 / (.8 * hl)), places=3, msg=tier)
            self.assertAlmostEqual(ratio(fc, 'bull'), .5 ** (1 / (1.3 * hl)), places=3, msg=tier)
        r = src(forecast.build(fb, an, card, qa, {'overrides': [ov('growth_half_life', 6., 'own_history')]}), 'growth_half_life', 'bear')
        self.assertEqual((r['tier'], r['default_class']), ('own_history', 'CONST'))   # the bear multiple is still a generic rule

    def test_size_relabel_is_dropped_when_growth_is_evidenced(self):
        data = {}
        self.assertEqual(model_card._profitable_stage(.2, 30e9, .3, True, [], 'growth 20%', .3, data), 'maturing')
        self.assertEqual(data['size_relabel']['prior'], 'VERY_LARGE')
        fb, an, card, qa = industrial()
        card['stage'] = {'value': 'maturing', 'override': None, 'data': {'size_relabel': data['size_relabel']}}
        self.assertEqual(forecast.build(fb, an, card, qa)['anchors']['route']['stage'], 'maturing')
        legacy = forecast.build(fb, an, card, qa, {'overrides': [ov('growth_anchor', .12)]})
        self.assertEqual(legacy['anchors']['route']['stage'], 'maturing')
        fc = forecast.build(fb, an, card, qa, {'overrides': [ov('growth_anchor', .12, 'guidance')]})
        self.assertEqual(fc['anchors']['route']['stage'], 'growth')
        self.assertEqual(fc['anchors']['route']['size_relabel']['evidence_ids'], ['syn:fs'])
        self.assertIn('size_relabel_not_applied', codes(fc))
        card['stage']['override'] = {'value': 'maturing', 'reason': 'analyst', 'evidence_ids': []}   # an analyst stage stands
        self.assertEqual(forecast.build(fb, an, card, qa, {'overrides': [ov('growth_anchor', .12, 'guidance')]})['anchors']['route']['stage'],
                         'maturing')

    def test_evidenced_margin_and_capital_set_bear_and_bull_around_the_evidence(self):
        fb, an, card, qa = industrial()                 # type-typical 12%, ceiling 20%
        m = lambda fc, s: scen(fc['spec'], s)['terminal']['operating_margin']
        legacy = forecast.build(fb, an, card, qa, {'overrides': [ov('mature_margin', .3)]})
        self.assertLess(m(legacy, 'bull'), m(legacy, 'base'))                # the type ceiling had capped the bull below the evidence
        fc = forecast.build(fb, an, card, qa, {'overrides': [ov('mature_margin', .3, 'own_history'), ov('sales_to_capital', 3., 'own_history')]})
        self.assertLess(m(fc, 'bear'), .3)
        self.assertAlmostEqual(m(fc, 'base'), .3, places=5)
        self.assertGreater(m(fc, 'bull'), .3)
        cpr = {s: scen(fc['spec'], s)['reinvestment_rule']['capital_per_revenue'][0] for s in SCEN}   # 1 / sales-to-capital today
        for s, k in (('bear', .8), ('base', 1.), ('bull', 1.2)):
            self.assertAlmostEqual(cpr[s], 1 / (3. * k), places=5)                  # around the evidenced 3.0, not the history
        self.assertEqual(src(fc, 'mature_margin', 'bull')['tier'], 'own_history')
        self.assertEqual(src(fc, 'sales_to_capital', 'bear')['tier'], 'own_history')


class GapVisibilityTests(unittest.TestCase):
    def test_every_lever_has_a_recorded_source_in_order(self):
        fc = forecast.build(*industrial())
        order = [(r['lever'], r['scenario']) for r in fc['driver_sources']]
        # financing terms only where a scenario uses them (assumed external money, or a debt without interest history)
        want = [(lv, s) for lv, _ in forecast.DRIVER_LEVERS if lv != 'financing_terms'
                for s in (('all',) if lv in ('cyclical', 'horizon') else SCEN)]
        self.assertEqual(order, want)
        for r in fc['driver_sources']:
            self.assertEqual(set(r), {'lever', 'scenario', 'tier', 'evidence_ids', 'default_class', 'binding_bound'})
            self.assertIn(r['tier'], forecast.SOURCE_TIERS + ('default',))
        gaps = {g['lever']: g for g in fc['driver_gaps']}
        for lever in ('margin_convergence', 'minimum_cash_months'):           # stage-table constants that set yearly numbers
            self.assertEqual(gaps[lever]['classes'], ['CONST'])
        self.assertNotIn('tax_rate', gaps)                                    # its own effective tax
        self.assertNotIn('working_capital', gaps)                             # its own working capital
        self.assertEqual(gaps['terminal_roic']['classes'], ['XSEC'])
        self.assertEqual(gaps['mature_margin']['classes'], ['TYPE'])
        self.assertEqual(gaps['horizon']['scenarios'], ['all'])
        self.assertNotIn('cyclical', gaps)                       # not cyclical by its own record

    def test_no_gaps_when_every_lever_is_evidenced(self):
        fb, an, card, qa = industrial()
        evid = [ov(p, v, t, 'all') for p, v, t in (
            ('growth_anchor', .1, 'guidance'), ('growth_half_life', 4., 'own_history'), ('mature_margin', .2, 'own_history'),
            ('sales_to_capital', 2., 'own_history'), ('terminal_roic', .2, 'competitor'), ('sbc_pct', .03, 'guidance'),
            ('cost_of_equity', .095, 'market'), ('margin_convergence_years', 4, 'own_history'),
            ('minimum_cash_months', 3, 'own_history'))]
        evid[4]['competitors'] = ['PEER1']
        fc = forecast.build(fb, an, card, qa, {'overrides': evid, 'horizon_years': 10})
        self.assertEqual(fc['status'], 'built')
        self.assertEqual(fc['driver_gaps'], [])
        self.assertTrue(all(r['default_class'] is None for r in fc['driver_sources']))
        self.assertIn(f'| {GAPS_HE} | אין |', hebrew_table({'fc': fc, 'dc': {}}))
        plain = forecast.build(fb, an, card, qa)
        row = next(x for x in hebrew_table({'fc': plain, 'dc': {}}).splitlines() if GAPS_HE in x)
        for g in plain['driver_gaps']:
            self.assertIn(g['label_he'], row)

    def test_binding_bounds_are_reported(self):
        fc = forecast.build(*adbe())                    # capital-light history: sales-to-capital at the type ceiling
        self.assertEqual(src(fc, 'sales_to_capital', 'base')['binding_bound'], 'type_sales_to_capital_range')
        fb, an, card, qa = industrial()
        fc = forecast.build(fb, an, card, qa, {'overrides': [ov('growth_anchor', 2.5, 'guidance')]})
        self.assertEqual(src(fc, 'growth_anchor', 'base')['binding_bound'], 'growth_range_-50%_+200%')

    def test_pipeline_carries_the_gap_list_to_the_decision_and_the_table(self):
        cf = pipeline.run(deepcopy(ADBE))
        self.assertEqual(cf['dc']['default_levers'], cf['fc']['driver_gaps'])
        self.assertTrue(cf['dc']['default_levers'])
        row = next(x for x in hebrew_table(cf).splitlines() if GAPS_HE in x)
        self.assertIn(forecast.LEVER_HE['terminal_roic'], row)
        self.assertEqual(sum(GAPS_HE in x for x in hebrew_table(cf).splitlines()), 1)


class InvarianceTests(unittest.TestCase):
    """No overrides: every value is what the engine produced before source tiers existed (spec hashes and values at 6a0db09),
    re-pinned on 29.10.2026 for forecast.TERMINAL_ASSET_RATE (terminal debt earns its tax shield only; ADBE 636.08 -> 634.25,
    industrial 47.30 -> 46.57, levered 27.97 -> 25.91, grower 6.23 -> 6.32: its plan's horizon debt now earns a shield;
    fast_grower's value unchanged, its spec records the horizon debt weight); re-pinned for 0.14.1 (the shield a per-scenario
    bridge item at one rate, terminal.debt_weight 0, debt_tax_shield_rate in the spec): base values move by under 0.001%; re-pinned for the return scale (terminal.ke_leverage_premium
    in the spec): values unchanged."""
    BEFORE = {'adbe': ('20d3ccbec7a3cc727c105636dd50130cc2a1249026c33e9e406726f9ca9bae5c', 634.2504229009998),
              'industrial': ('29116ee00cfb98aa685b70a3bffcb4db0c08981241b8c80cb08ba62de3be422b', 46.57400578264891),
              'grower': ('f8584908b5964bbcba7e046293380ccd4b337bb8682a96d43a2373efc5f483d2', 6.323941064003434),
              'levered': ('d06b7127ce9db1967e30d5414311d6105dea0683386b17bf33086411e5cb6b67', 25.91325417317471),
              'fast_grower': ('fb8b83930dea2517418206c17efb1d86071c0d8f4a605fdc536b780b9568a846', 132.68099168372305)}

    def test_values_unchanged_without_overrides(self):
        for name, f in (('adbe', adbe), ('industrial', industrial), ('grower', grower), ('levered', lambda: levered(60.)),
                        ('fast_grower', fast_grower)):
            sha, base = self.BEFORE[name]
            fc = forecast.build(*f())
            self.assertEqual(fc['spec_sha256'], sha, name)
            self.assertAlmostEqual(fc['preview']['today']['base'], base, places=9, msg=name)

    def test_pipeline_values_unchanged_without_overrides(self):
        cf = pipeline.run(deepcopy(ADBE))
        self.assertEqual(cf['fc']['spec_sha256'], '3791b8329ea8ef01b983df685718f19f351b6fab70607a87b5d77db7feaac801')


if __name__ == '__main__':
    unittest.main()
