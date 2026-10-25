"""WP7 of the econ layer: quarter_review(case.economics) and the identity-based operating-leverage peak exception.

The exception's numbers are the filed Q2-26 figures (tests/fixtures/econ/*_filed.json, filing numbers only): PLTR leverage
share 0.809 and RDDT 0.970 drop the peak flag to operating_leverage_or_peak (medium); SKHY (no gross profit or opex, and
memory_cycle is price-cyclical) stays peak_margin_run_rate (high). Rules frozen in docs/prereg_econ.md section 1.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from fundamental_engine import econ, quarter as Q
from fundamental_engine.quarter import quarter_review

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests' / 'fixtures' / 'econ'
EXAMPLES = ROOT / 'examples' / 'quarter'


def filed(name):
    return json.loads((FIX / f'{name}_filed.json').read_text(encoding='utf-8'))


def example(name):
    return json.loads((EXAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def subset(name, model):
    """An econ fixture as case.economics: without the meta the quarter case supplies and without the answers."""
    d = json.loads((FIX / f'{name}.json').read_text(encoding='utf-8'))
    for k in ('company', 'period_end', 'as_of', 'currency', 'expected', 'model_hint'):
        d.pop(k, None)
    d['model'] = model
    return d


def peak_flags(r):
    return [(f['key'], f['severity']) for f in r['flags'] if f['key'] in Q.QUARTER_OWNED_CHECKS]


# the quarter examples carry source_ids only; the econ subset names its sources (kind, available_at): nothing is assumed
SKHY_SRC = {'analyst_post_skhy_2026q2': {'kind': 'analyst_post', 'available_at': '2026-07-31'}}
UBER_SRC = {'analyst_post_uber_2025q3': {'kind': 'analyst_post', 'available_at': '2025-11-04'}}


def with_econ(case, economics):
    c = deepcopy(case)
    c['economics'] = economics
    return c


class PeakExceptionTests(unittest.TestCase):
    def test_pltr_filed_leverage_share_0809_is_medium(self):
        r = quarter_review(with_econ(filed('pltr_2026q2'), {'model': 'data_platform_software'}))
        self.assertEqual(peak_flags(r), [('operating_leverage_or_peak', 'medium')])
        pe = r['cycle']['peak_exception']
        d = pe['decomposition']
        self.assertEqual(round(r['cycle']['operating_margin'], 3), 0.471)
        self.assertEqual(round(r['cycle']['historical_max'], 3), 0.316)
        self.assertEqual((round(100 * d['delta_operating_margin'], 1), round(100 * d['delta_gross_margin'], 1),
                          round(100 * d['delta_opex_ratio'], 1)), (20.3, 3.9, -16.4))
        self.assertAlmostEqual(d['residual'], 0, places=12)              # OM = GM - opex/revenue exactly
        self.assertTrue(d['current']['reconciles'] and d['prior_year']['reconciles'])
        self.assertEqual(round(pe['leverage_share'], 3), 0.809)
        self.assertAlmostEqual(pe['leverage_share'], -d['delta_opex_ratio'] / d['delta_operating_margin'], places=12)
        self.assertEqual((pe['applies'], pe['severity'], pe['reasons'], pe['leverage_share_min']), (True, 'medium', [], 0.667))
        self.assertEqual([t['fired'] for t in pe['peak_tests']], [True, True])
        he = next(f['he'] for f in r['flags'] if f['key'] == 'operating_leverage_or_peak')
        for part in ('47.1%', '31.6%', '81%', '20.3', '-16.4', '+3.9', 'מינוף תפעולי', 'רווח שיא'):   # both readings and the share
            self.assertIn(part, he)
        self.assertIn(he, r['next_quarter_watch'])                         # medium: watched, never silent

    def test_rddt_filed_leverage_share_0970_and_the_02pp_margin(self):
        r = quarter_review(with_econ(filed('rddt_2026q2'), {'model': 'ads_platform'}))
        self.assertEqual(peak_flags(r), [('operating_leverage_or_peak', 'medium')])
        pe = r['cycle']['peak_exception']
        d = pe['decomposition']
        hist_max, median15 = pe['peak_tests']
        self.assertEqual((hist_max['fired'], median15['fired']), (False, True))
        self.assertEqual((round(r['cycle']['operating_margin'], 3), round(r['cycle']['historical_median'], 3),
                          round(median15['threshold'], 3)), (0.288, 0.136, 0.286))
        self.assertEqual(round(100 * median15['above_threshold'], 1), 0.2)          # the 0.2pp margin is shown
        self.assertEqual(median15['near_threshold'], econ.rules()['near_threshold'])
        self.assertEqual((round(100 * d['delta_operating_margin'], 1), round(100 * d['delta_gross_margin'], 1),
                          round(100 * d['delta_opex_ratio'], 1)), (15.2, 0.5, -14.8))
        self.assertEqual(round(pe['leverage_share'], 3), 0.970)
        he = next(f['he'] for f in r['flags'] if f['key'] == 'operating_leverage_or_peak')
        self.assertIn('28.8%', he)
        self.assertIn('28.6%', he)
        self.assertIn('97%', he)

    def test_skhy_stays_high_without_gross_profit_or_opex_and_as_memory_cycle_regardless(self):
        r = quarter_review(with_econ(example('skhy_2026q2'), {'model': 'memory_cycle', 'sources': SKHY_SRC}))
        self.assertEqual(peak_flags(r), [('peak_margin_run_rate', 'high')])
        pe = r['cycle']['peak_exception']
        self.assertEqual([x.split(':')[0] for x in pe['reasons']], ['gross_profit_and_opex_missing', 'price_cyclical_model'])
        self.assertNotIn('decomposition', pe)
        self.assertEqual(pe['price_cyclical_models'], ['memory_cycle'])
        # synthetic gross profit and opex with a leverage share well above 2/3: memory_cycle still keeps the flag high
        c = example('skhy_2026q2')
        c['current'].update(gross_profit=45.0e9, operating_expenses=3.7e9)          # 45.0 - 3.7 = 41.3 = operating income
        c['prior_year'].update(gross_profit=14.0e9, operating_expenses=7.7e9)       # 14.0 - 7.7 = 6.3
        r = quarter_review(with_econ(c, {'model': 'memory_cycle', 'sources': SKHY_SRC}))
        pe = r['cycle']['peak_exception']
        self.assertGreater(pe['leverage_share'], 1)
        self.assertEqual(peak_flags(r), [('peak_margin_run_rate', 'high')])
        self.assertEqual([x.split(':')[0] for x in pe['reasons']], ['price_cyclical_model'])
        # the same numbers under a model that is not price-cyclical: the model condition is what decides
        r = quarter_review(with_econ(c, {'model': 'data_platform_software', 'sources': SKHY_SRC}))
        self.assertEqual(peak_flags(r), [('operating_leverage_or_peak', 'medium')])
        with self.assertRaisesRegex(ValueError, 'sources'):                  # no source kind or date is assumed
            quarter_review(with_econ(c, {'model': 'memory_cycle'}))

    def test_conditions_unknown_model_cyclical_card_identity_and_share(self):
        pltr = filed('pltr_2026q2')
        cases = {
            'model_not_known': {},                                                              # unclassified: not assessed
            'cyclical_card_type': {'model': 'data_platform_software', 'card_type': 'semiconductor'},
        }
        for reason, sub in cases.items():
            r = quarter_review(with_econ(pltr, sub))
            self.assertEqual(peak_flags(r), [('peak_margin_run_rate', 'high')], reason)
            self.assertEqual([x.split(':')[0] for x in r['cycle']['peak_exception']['reasons']], [reason])
        self.assertEqual(quarter_review(with_econ(pltr, {}))['economics']['status'], 'not_assessed')
        # opex stated as total costs (cost of revenue included): the identity does not reconcile, the split is refused
        c = deepcopy(pltr)
        c['current']['operating_expenses'] = c['current']['revenue'] - c['current']['operating_income']
        r = quarter_review(with_econ(c, {'model': 'data_platform_software'}))
        pe = r['cycle']['peak_exception']
        self.assertEqual((peak_flags(r), pe['decomposition']['current']['reconciles']), ([('peak_margin_run_rate', 'high')], False))
        self.assertEqual([x.split(':')[0] for x in pe['reasons']], ['identity_not_reconciled'])
        self.assertNotIn('leverage_share', pe)
        # a margin rise led by gross margin (price or mix): the share is below 2/3 and the flag stays high
        c = deepcopy(pltr)
        c['prior_year'].update(gross_profit=0.70 * 1003697000, operating_expenses=0.70 * 1003697000 - 269317000)
        r = quarter_review(with_econ(c, {'model': 'data_platform_software'}))
        pe = r['cycle']['peak_exception']
        self.assertLess(pe['leverage_share'], econ.rules()['leverage_share_min'])
        self.assertEqual([x.split(':')[0] for x in pe['reasons']], ['leverage_share_below_min'])
        self.assertEqual(peak_flags(r), [('peak_margin_run_rate', 'high')])
        # above the history but below last year's margin: no rise to attribute
        c = deepcopy(pltr)
        c['prior_year'].update(operating_income=0.5 * 1003697000, gross_profit=0.85 * 1003697000,
                               operating_expenses=0.35 * 1003697000)
        pe = quarter_review(with_econ(c, {'model': 'data_platform_software'}))['cycle']['peak_exception']
        self.assertEqual([x.split(':')[0] for x in pe['reasons']], ['no_margin_expansion'])

    def test_no_economics_no_change(self):
        """No-input invariance: without case.economics the peak flag, the cycle block and every key stay as before."""
        for name, case in (('pltr', filed('pltr_2026q2')), ('rddt', filed('rddt_2026q2')), ('skhy', example('skhy_2026q2')),
                           ('uber', example('uber_2025q3')), ('sn', example('sn_2025')), ('sofi', example('sofi_2026q2'))):
            r = quarter_review(case)
            self.assertNotIn('economics', r, name)
            self.assertNotIn('peak_exception', r['cycle'] or {}, name)
            self.assertFalse([f for f in r['flags'] if f['key'].startswith('econ.') or f['key'] == 'operating_leverage_or_peak'], name)
            self.assertFalse([f for f in r['falsifiers'] if 'econ' in f], name)
            self.assertEqual(peak_flags(r), [('peak_margin_run_rate', 'high')] if name in ('pltr', 'rddt', 'skhy') else [], name)
        he = next(f['he'] for f in quarter_review(example('skhy_2026q2'))['flags'] if f['key'] == 'peak_margin_run_rate')
        self.assertEqual(he, 'שולי רווח תפעולי של 76% מול חציון היסטורי של 29%: רווח רבעוני כפול 4 הוא רווח שיא, לא רווח מנורמל '
                             'לאורך מחזור.')
        # a case without a peak reads the same with an economics block, apart from the economics additions
        a, b = quarter_review(example('uber_2025q3')), quarter_review(with_econ(example('uber_2025q3'), {'sources': UBER_SRC}))
        for k in set(a) - {'input_sha256'}:
            self.assertEqual(a[k], b[k], k)


class EconomicsBlockTests(unittest.TestCase):
    def test_skhy_margin_only_normalized_pe_1985_through_quarter_review(self):
        r = quarter_review(with_econ(example('skhy_2026q2'), subset('skhy_2026q2', 'memory_cycle')))
        e = r['economics']
        m, = [x for x in e['measures'] if x['check'] == 'margin_only_normalized']
        self.assertEqual((m['status'], m['label'], m['attributed']), ('computed', 'margin_only', True))
        self.assertEqual(round(m['run_rate_pe'], 2), 7.54)
        self.assertEqual(round(m['normalized_pe'], 2), 19.85)                   # 132 / 17.5 x 0.763 / 0.29
        self.assertIn('floor', m['note'])
        self.assertEqual(peak_flags(r), [('peak_margin_run_rate', 'high')])
        self.assertEqual(e['checks_run_by_quarter'], [{'check': 'peak_margin_run_rate', 'model': 'memory_cycle', 'where': 'cycle'}])
        self.assertFalse([x for x in e['checks_not_run'] if x['check'] in Q.QUARTER_OWNED_CHECKS])
        self.assertEqual((e['company'], e['as_of'], e['period']), ('SK hynix (ADR line)', '2026-07-31', 'quarter'))  # inherited
        self.assertIn('chain', e['unavailable'])            # the NVDA upstream review exists only after the quarter's as_of
        self.assertIn(('econ.lta_unproven', 'info'), [(f['key'], f['severity']) for f in r['flags']])

    def test_rddt_flags_merge_as_econ_keys_and_falsifiers_feed(self):
        c = with_econ(filed('rddt_2026q2'), subset('rddt_2026q2', 'ads_platform'))
        c['falsifiers'] = [{'id': 'us_dau', 'metric': 'US DAUq q/q', 'direction': 'below', 'threshold': 0.0,
                            'econ': {'id': 'dau', 'scope': 'us', 'field': 'growth'}}]
        r = quarter_review(c)
        self.assertEqual(peak_flags(r), [('operating_leverage_or_peak', 'medium')])
        merged = [f for f in r['flags'] if f['key'].startswith('econ.')]
        self.assertEqual(sorted({f['key'] for f in merged}),
                         ['econ.core_units_flat', 'econ.disclosure_dropped', 'econ.thesis_on_minor_line'])
        self.assertEqual(len(merged), len(r['economics']['flags']))         # each econ flag once, the numbers stay in economics
        own = {f['key'] for f in r['flags'] if not f['key'].startswith('econ.')}
        self.assertFalse(own & {'econ.' + k for k in own})
        self.assertTrue(all(f['he'] and f['severity'] in Q.SEVERITY for f in merged))
        self.assertIn('us: ', next(f['he'] for f in merged if f['key'] == 'econ.disclosure_dropped'))
        bound, *tracked = r['falsifiers']
        self.assertEqual((bound['id'], bound['status'], round(bound['current'], 4)), ('us_dau', 'broken', round(53.2 / 53.5 - 1, 4)))
        self.assertEqual((bound['econ']['status'], bound['econ']['unobservable_from']), ('unobservable', '2026-Q4'))
        self.assertIn('thesis_falsifier_broken', {f['key'] for f in r['flags']})
        self.assertEqual(sorted((t['id'], t['econ']['scope'], t['status']) for t in tracked),
                         [('econ.arpu', 'total', 'tracked'), ('econ.arpu', 'us', 'tracked'), ('econ.dau', 'us_logged_in', 'tracked')])
        self.assertTrue(all(t['threshold'] is None for t in tracked))     # no threshold invented for a tracked row

    def test_ibkr_identity_falsifier_bound_by_change(self):
        q = {'company': 'IBKR', 'period_end': '2026-09-30', 'as_of': '2026-10-21', 'currency': 'USD',
             'source_ids': ['analyst_post_ibkr_2026q3'], 'current': {'revenue': 1.87e9}, 'prior_year': {'revenue': 1.87e9 / 1.16},
             'falsifiers': [{'id': 'yield', 'metric': 'revenue yield on client equity y/y', 'direction': 'below',
                             'threshold': -0.05, 'econ': {'id': 'revenue_yield_on_equity', 'field': 'change'}},
                            {'id': 'nna', 'metric': 'net new accounts', 'direction': 'below', 'threshold': 300000,
                             'econ': {'id': 'net_new_accounts', 'field': 'value'}, 'prior': 400000}]}
        r = quarter_review(with_econ(q, subset('ibkr_2026q3', 'broker')))
        y, n = r['falsifiers']
        self.assertEqual((round(y['current'], 4), y['status'], y['econ']['kind']), (-0.0866, 'broken', 'identity'))
        self.assertEqual((n['current'], n['status']), (391000.0, 'weakening'))
        self.assertEqual(len(r['falsifiers']), 2)                            # both econ falsifiers bound: none listed twice
        self.assertIn(('econ.per_unit_vs_units', 'info'), [(f['key'], f['severity']) for f in r['flags']])
        # a falsifier whose KPI is not supplied is missing, not judged
        q['falsifiers'][1]['econ'] = {'id': 'net_new_accounts', 'field': 'growth'}
        n = quarter_review(with_econ(q, subset('ibkr_2026q3', 'broker')))['falsifiers'][1]
        self.assertEqual((n['current'], n['status']), (None, 'tracked'))

    def test_hash_is_the_reviewed_econ_case_and_deterministic(self):
        c = with_econ(filed('pltr_2026q2'), subset('pltr_2026q2', 'data_platform_software'))
        a, b = quarter_review(c), quarter_review(deepcopy(c))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        e = subset('pltr_2026q2', 'data_platform_software')
        q = filed('pltr_2026q2')
        built = {**e, **{k: q[k] for k in Q.ECON_INHERITED}, 'period': 'quarter', 'sources': {**e['sources'], **q['sources']}}
        self.assertEqual(a['economics']['input_sha256'], econ.review(built)['input_sha256'])
        self.assertNotEqual(a['input_sha256'], quarter_review(filed('pltr_2026q2'))['input_sha256'])

    def test_refusals(self):
        pltr = filed('pltr_2026q2')
        bad = {'as_of': ({'model': 'data_platform_software', 'as_of': '2026-10-21'}, 'economics.as_of'),
               'period': ({'model': 'data_platform_software', 'period': 'year'}, 'economics.period'),
               'card': ({'model': 'data_platform_software', 'card_type': 'spaceship'}, 'card_type'),
               'source': ({'model': 'data_platform_software',
                           'sources': {'pltr_8k_q2_2026_ex991': {'kind': 'issuer_release', 'available_at': '2026-09-01'}}},
                          'economics.sources'),
               'shape': ('data_platform_software', 'economics')}
        for name, (sub, msg) in bad.items():
            with self.assertRaisesRegex(ValueError, msg, msg=name):
                quarter_review(with_econ(pltr, sub))
        f = {'id': 'x', 'metric': 'x', 'direction': 'below', 'threshold': 0.0, 'econ': {'id': 'customers', 'field': 'growth'}}
        with self.assertRaisesRegex(ValueError, 'needs case.economics'):
            quarter_review({**pltr, 'falsifiers': [f]})
        for ref, msg in (({'id': 'customers', 'field': 'level'}, 'econ.field'), ({'id': 'dau', 'field': 'value'}, 'econ.id'),
                         ({'id': 'customers', 'field': 'value', 'extra': 1}, 'econ: ')):
            with self.assertRaisesRegex(ValueError, msg):
                quarter_review(with_econ({**pltr, 'falsifiers': [{**f, 'econ': ref}]}, {'model': 'data_platform_software'}))
        with self.assertRaisesRegex(ValueError, 'current and econ'):
            quarter_review(with_econ({**pltr, 'falsifiers': [{**f, 'current': 1.0}]}, {'model': 'data_platform_software'}))

    def test_contracts_with_the_registry(self):
        self.assertEqual(tuple(econ.QUARTER_CHECKS), Q.QUARTER_OWNED_CHECKS)
        checks = econ.registry()['checks']
        self.assertIn('quarter._cycle', checks['peak_margin_run_rate']['rule'])
        flags = {c for c, s in checks.items() if s['kind'] == 'flag'} - set(Q.QUARTER_OWNED_CHECKS)
        self.assertEqual(flags, set(Q.ECON_FLAG_HE))                          # every econ flag has its Hebrew text
        self.assertEqual(checks['operating_leverage_or_peak']['severity'], 'medium')
        r = econ.review(subset('skhy_2026q2', 'memory_cycle') | {'company': 'SKHY', 'period_end': '2026-06-30',
                                                                  'as_of': '2026-10-21', 'currency': 'USD'})
        row, = [x for x in r['checks_not_run'] if x['check'] == 'peak_margin_run_rate']
        self.assertIn('quarter_review', row['reason'])                        # standalone: owned by quarter_review


if __name__ == '__main__':
    unittest.main()
