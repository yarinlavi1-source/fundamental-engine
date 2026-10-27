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
    re-pinned at 9c53ebd (27.10.2026), where debt is weighed against the equity less the financial assets valued at face: adbe
    636.08 -> 643.44 (debt weight 0.8% -> 2.5%), industrial 47.30 -> 47.76, levered 27.97 -> 27.76 (cash above the minimum no
    longer offsets its debt); grower and fast_grower carry no debt (values unchanged, the cost-of-equity row's text changed)."""
    BEFORE = {'adbe': ('c1872d9dc142b6cfb8384c1a015bcdcfb809bd63282e2eb6d7252e584ae81e2d', 643.4363761952735),
              'industrial': ('415cf5b0fde2fc655460c34274cc361fd338f7d7897edbff9fe156d673e055c6', 47.75843640214632),
              'grower': ('fc6d9587ded78d69c8a282b0e1e6f64881c90471fb4b6ecc22f24bb2b0949c34', 6.226562711983232),
              'levered': ('1adbbb14357b4f10f23675098a562e3f88feaa91c357c04f133f8c60e697b72b', 27.757945918278786),
              'fast_grower': ('39f322fc2b8adbf4a1076a4decc8d3967f3309aa42e0d7d500aa805db9d94c02', 132.68099168372305)}

    def test_values_unchanged_without_overrides(self):
        for name, f in (('adbe', adbe), ('industrial', industrial), ('grower', grower), ('levered', lambda: levered(60.)),
                        ('fast_grower', fast_grower)):
            sha, base = self.BEFORE[name]
            fc = forecast.build(*f())
            self.assertEqual(fc['spec_sha256'], sha, name)
            self.assertAlmostEqual(fc['preview']['today']['base'], base, places=9, msg=name)

    def test_pipeline_values_unchanged_without_overrides(self):
        cf = pipeline.run(deepcopy(ADBE))
        self.assertEqual(cf['fc']['spec_sha256'], '7e6a65c755f3fe47a2e0e4b76f553455d09b59c83a8e70ae31ff9226c18dc853')


if __name__ == '__main__':
    unittest.main()
