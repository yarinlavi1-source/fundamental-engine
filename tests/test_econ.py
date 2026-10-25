"""WP3 of the econ layer: identities, growth split and review (the core math).

The numbers are the analyst posts' figures in tests/fixtures/econ (arithmetic fixtures, not truth); the rules are the
design's (docs/prereg_econ.md, brain/economics.json). Each case is the fixture without its `expected` block, so the
engine never sees the answers; where a test needs a value the post did not give, it is marked SYNTHETIC. Every
behaviour test here fails without WP3 (no econ.review, evaluate_identity or growth_split); the no-input invariance guard
(nothing in the engine calls the review) passes on the base by design. RepairTests fail on the first WP3 commit (f6ce33f):
point in time for market and cycle, every window's growth check, the core summary, degenerate lines, stated figures on
their own basis, the cross-scope NDR and a split factor on an undeclared basis.
"""
import json
import random
import unittest
from copy import deepcopy
from math import log
from pathlib import Path
from unittest import mock

from fundamental_engine import econ, facts as F

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests' / 'fixtures' / 'econ'
# the model each fixture is read through (fixture model_hint is a WP4 expectation, never an engine input)
MODELS = {'uber_2025q3': 'marketplace', 'uber_2026q2': 'marketplace', 'ibkr_2026q2': 'broker', 'ibkr_2026q3': 'broker',
          'pltr_2026q2': 'data_platform_software', 'rddt_2026q2': 'ads_platform', 'sofi_2026q2': 'digital_bank',
          'sn_2025': 'consumer_innovator', 'skhy_2026q2': 'memory_cycle', 'nvda_2026q2': 'gpu_vendor',
          'spcx_2026q3': {'primary': 'subscriber_network', 'secondary': ['compute_leasing', 'consumer_innovator']}}


def fixture(name):
    return json.loads((FIX / f'{name}.json').read_text(encoding='utf-8'))


def case(name):
    """The econ case: the fixture without the answers."""
    d = fixture(name)
    d.pop('expected')
    return d


def run(name, **kw):
    return econ.review(case(name), MODELS[name], **kw)


def row(review, model_id, identity_id, scope='total'):
    hits = [r for r in review['identities'] if (r['model'], r['id'], r['scope']) == (model_id, identity_id, scope)]
    assert len(hits) == 1, (model_id, identity_id, scope, len(hits))
    return hits[0]


def flags(review, key):
    return [f for f in review['flags'] if f['key'] == key]


def numbers(node):
    if isinstance(node, bool) or node is None or isinstance(node, str):
        return
    if isinstance(node, (int, float)):
        yield float(node)
    elif isinstance(node, dict):
        for v in node.values():
            yield from numbers(v)
    elif isinstance(node, list):
        for v in node:
            yield from numbers(v)


# the result fields a derived number may sit under (reviewed: a new numeric field is a deliberate addition here)
DERIVED_KEYS = {'annual_impact', 'annual_net_interest_income', 'annualized_by', 'change', 'computed', 'computed_prior', 'consolidated',
                'current_operating_margin', 'cycle_margin', 'diff', 'explained', 'factor', 'growth', 'growth_ex_pass_through',
                'incremental_share', 'levels_growth', 'line_factor', 'line_growth', 'line_value', 'log', 'ndr', 'ndr_change',
                'ndr_prior', 'net_interest_income', 'normalized_eps', 'normalized_pe', 'operating_margin', 'other_growth',
                'pass_through', 'per_unit_change', 'per_unit_log_share', 'pp', 'price', 'prior', 'prior_diff', 'prior_growth',
                'prior_pass_through', 'product', 'residual_log', 'residual_pp', 'run_rate_eps', 'run_rate_pe', 'share',
                'share_change', 'stated', 'stated_base', 'stated_prior', 'sum', 'units_growth', 'value'}


def unsourced(out, case_):
    """Every number of a review must be an input of the case, a registry rule shown beside the value it judges (under its
    own name or `threshold`), or a result field (DERIVED_KEYS) of an object that says what it was derived from. Returns
    the numbers that are none of these: a constant planted anywhere in the output shows up here, also next to a
    derived_from."""
    inputs = set(numbers(case_))
    rules = {k: float(v) for k, v in econ.rules().items() if not isinstance(v, str)}
    bad = []

    def allowed(key, value, derived):
        return ((derived and key in DERIVED_KEYS) or value in inputs or (key in rules and value == rules[key])
                or (key == 'threshold' and value in rules.values()) or (key == 'economics_version' and value == econ.ECONOMICS_VERSION))

    def visit(key, v, path, derived):
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return
        if isinstance(v, (int, float)):
            if not allowed(key, float(v), derived):
                bad.append((path, v))
        elif isinstance(v, dict):
            here = bool(v.get('derived_from'))
            for k, x in v.items():
                visit(k, x, f'{path}.{k}', here)
        elif isinstance(v, list):                 # a list belongs to the key and the object holding it
            for i, x in enumerate(v):
                visit(key, x, f'{path}.{i}', derived)
    visit(None, out, '', False)
    return bad


class MarketplaceTests(unittest.TestCase):
    def test_1_uber_trips_from_consumers_is_consistent_within_one_pp(self):
        r = run('uber_2025q3')
        t = row(r, 'marketplace', 'trips_from_consumers')
        self.assertEqual(t['status'], 'consistent')
        ch = t['changes'][0]
        self.assertEqual((ch['window'], ch['footing']), ('y/y', 'stated'))      # frequency has only a stated growth
        self.assertEqual({i['id']: i['growth'] for i in ch['inputs']}, {'mapcs': 0.17, 'frequency': 0.04})
        self.assertAlmostEqual(ch['factor'], 1.2168, places=12)                 # 1.17 x 1.04
        c = ch['check']
        self.assertEqual((c['line'], c['line_from'], c['status']), ('trips', 'levels', 'consistent'))
        self.assertAlmostEqual(c['line_factor'], 3.512e9 / 2.879e9, places=12)
        self.assertEqual(round(c['line_factor'], 4), 1.2199)
        self.assertEqual(round(100 * c['residual_pp'], 2), 0.31)                # 0.31pp, inside growth_identity_pp 1pp
        self.assertAlmostEqual(c['residual_log'], log(3.512 / 2.879) - log(1.17) - log(1.04), places=12)
        self.assertEqual(c['growth_identity_pp'], 0.01)
        mapcs = next(k for k in r['kpis'] if k['id'] == 'mapcs')['comparisons'][0]
        self.assertEqual((mapcs['growth'], mapcs['from']), (0.17, 'stated'))  # the levels growth stays visible beside it
        self.assertAlmostEqual(mapcs['levels_growth'], 189 / 161.5 - 1, places=12)
        self.assertEqual(r['models'][0]['core_status'], 'consistent')

    def test_2_uber_mobility_ebitda_per_gross_bookings(self):
        r = run('uber_2025q3')
        m = row(r, 'marketplace', 'segment_profit_per_gross_bookings', 'mobility')
        self.assertEqual((m['status'], m['profit_definition']), ('computed', 'segment adjusted EBITDA'))
        self.assertAlmostEqual(m['value'], 2.04e9 / 25.1e9, places=12)
        self.assertEqual(round(100 * m['value'], 2), 8.13)
        self.assertFalse([x for x in r['identities'] if x['id'] == 'segment_profit_per_gross_bookings' and x['scope'] == 'total'])
        rec = next(x for x in r['measures'] if x['check'] == 'segment_reconciliation')
        self.assertEqual(rec['revenue']['status'], 'consistent')                # 7.68 + 4.48 + 1.31 vs 13.46
        self.assertEqual(rec['segment_profit']['status'], 'not_reconciled')     # segment adjusted EBITDA: corporate costs unallocated

    def test_3a_constant_currency_bookings_are_never_divided_by_reported_revenue(self):
        r = run('uber_2026q2')
        tr = row(r, 'marketplace', 'take_rate')
        self.assertEqual(tr['status'], 'basis_mismatch')
        self.assertNotIn('value', tr)
        self.assertNotIn('changes', tr)
        self.assertEqual({(s['id'], s['currency_basis'], s['growth']) for s in tr['side_by_side']},
                         {('gross_bookings', 'constant_currency', 0.21), ('revenue', 'reported', 0.12)})
        self.assertEqual(tr['questions'], ['supply reported gross bookings growth'])
        self.assertIn({'model': 'marketplace', 'about': 'take_rate', 'scope': 'total', 'question': 'supply reported gross bookings growth'},
                      r['open_questions'])
        self.assertEqual(flags(r, 'take_rate_shift'), [])
        split = r['growth_splits'][0]
        self.assertEqual((split['status'], split['missing'][-1]), ('basis_mismatch', 'take_rate'))
        self.assertEqual(row(run('uber_2025q3'), 'marketplace', 'take_rate')['status'], 'basis_mismatch')   # +21% cc vs +20% reported

    def test_3b_take_rate_shift_is_tagged_reclassification_only_with_issuer_evidence(self):
        c = case('uber_2026q2')
        c['kpis'] = c.pop('variants')['reported_basis']['kpis']    # the fixture's labelled test assumption: +21% reported
        r = econ.review(c, 'marketplace')
        tr = row(r, 'marketplace', 'take_rate')
        self.assertEqual((tr['status'], tr['tag']), ('computed', 'mix'))
        self.assertAlmostEqual(tr['changes'][0]['value'], 1.12 / 1.21 - 1, places=12)
        self.assertEqual(round(100 * tr['changes'][0]['value'], 2), -7.44)
        f, = flags(r, 'take_rate_shift')
        self.assertEqual((f['severity'], f['reclassification'], f['tag'], f['threshold']), ('medium', 'unproven', None, 0.01))
        self.assertTrue(any(q['about'] == 'take_rate' and 'issuer document' in q['question'] for q in r['open_questions']))
        post = deepcopy(c)        # an analyst post never evidences a business-model change
        post['reclassification_evidence'] = {'identity': 'take_rate', 'reason': 'SYNTHETIC', 'evidence_ids': ['comparison_journal_uber_2026q2']}
        self.assertEqual(flags(econ.review(post, 'marketplace'), 'take_rate_shift')[0]['reclassification'], 'unproven')
        issuer = deepcopy(c)      # SYNTHETIC issuer transcript: the tag moves only with this kind of evidence
        issuer['sources']['tx'] = {'kind': 'issuer_transcript', 'available_at': '2026-08-05'}
        issuer['reclassification_evidence'] = {'identity': 'take_rate', 'reason': 'SYNTHETIC business-model change', 'evidence_ids': ['tx']}
        r2 = econ.review(issuer, 'marketplace')
        tr2 = row(r2, 'marketplace', 'take_rate')
        self.assertEqual((tr2['tag'], tr2['registry_tag']), ('reclassification', 'mix'))
        f2, = flags(r2, 'take_rate_shift')
        self.assertEqual((f2['reclassification'], f2['tag'], f2['read_on'], f2['evidence_ids']), ('evidenced', 'reclassification', 'gross_bookings', ['tx']))
        fal, = [x for x in r2['falsifiers'] if x['id'] == 'take_rate']
        self.assertEqual((fal['tag'], fal['read_on']), ('reclassification', 'gross_bookings'))
        self.assertEqual([x['tag'] for x in r2['growth_splits'][0]['factors']], ['reclassification'])


class BrokerTests(unittest.TestCase):
    def test_4_ibkr_revenue_per_account_splits_into_mix_and_yield(self):
        r = run('ibkr_2026q3')
        rpa = row(r, 'broker', 'revenue_per_account')
        self.assertEqual((rpa['variant'], rpa['variants']['end']['annualized_by']), ('end', 4))
        self.assertNotIn('average', rpa['variants'])                         # no start-of-quarter accounts supplied
        self.assertAlmostEqual(rpa['value'], 1.87e9 * 4 / 5.58e6, places=9)
        self.assertEqual(round(rpa['value'], 1), 1340.5)
        ch = rpa['changes'][0]
        self.assertAlmostEqual(ch['prior'], (1.87e9 / 1.16) * 4 / (5.58e6 / 1.35), places=9)
        self.assertEqual(round(ch['prior'], 1), 1560.1)
        self.assertEqual(round(100 * ch['value'], 2), -14.07)
        st, = rpa['stated']
        self.assertEqual((st['kpi_basis'], st['variant'], st['status'], st['stated'], st['stated_prior']), ('balance', 'end', 'consistent', 1340.0, 1560.0))
        self.assertEqual(rpa['status'], 'consistent')                        # computed agrees with the post's own figure
        epa, yld = row(r, 'broker', 'equity_per_account'), row(r, 'broker', 'revenue_yield_on_equity')
        self.assertEqual((epa['tag'], yld['tag']), ('mix', 'cyclical'))
        self.assertEqual((round(epa['value']), round(epa['changes'][0]['prior'])), (172939, 183833))
        self.assertEqual(round(100 * epa['changes'][0]['value'], 2), -5.93)
        self.assertEqual((round(100 * yld['value'], 3), round(100 * yld['changes'][0]['prior'], 3)), (0.775, 0.849))
        self.assertEqual(round(100 * yld['changes'][0]['value'], 2), -8.66)
        mix, cyc = 1 + epa['changes'][0]['value'], 1 + yld['changes'][0]['value']
        self.assertEqual((round(mix, 4), round(cyc, 4)), (0.9407, 0.9134))
        self.assertAlmostEqual(mix * cyc, 1.16 / 1.35, places=12)            # 0.85926: only the yield part may revert
        self.assertEqual(round(mix * cyc, 5), 0.85926)
        s = r['growth_splits'][0]
        self.assertEqual((s['line'], s['window'], s['status']), ('revenue', 'y/y', 'consistent'))
        self.assertEqual([(f['id'], f['tag']) for f in s['factors']],
                         [('accounts', 'structural'), ('equity_per_account', 'mix'), ('revenue_yield_on_equity', 'cyclical')])
        self.assertEqual([round(f['factor'], 5) for f in s['factors']], [1.35, 0.94074, 0.91339])
        self.assertLess(abs(s['residual']['log']), 1e-12)                   # ln 1.16 = ln 1.35 + ln 0.9407 + ln 0.9134 + 0
        self.assertEqual({t['tag']: round(t['log'], 4) for t in s['by_tag']}, {'structural': 0.3001, 'mix': -0.0611, 'cyclical': -0.0906})
        f, = flags(r, 'per_unit_vs_units')
        self.assertEqual((f['severity'], f['units'], f['units_growth'], f['threshold']), ('info', 'accounts', 0.35, 0.05))
        self.assertEqual(round(100 * f['per_unit_change'], 2), -14.07)
        self.assertEqual(r['models'][0]['core_status'], 'consistent')

    def test_5_ibkr_margin_interest_is_average_balance_times_rate(self):
        mi = row(run('ibkr_2026q3'), 'broker', 'margin_interest')
        ch = mi['changes'][0]
        self.assertEqual((ch['variant'], ch['footing']), ('average', 'levels'))   # both factors carry levels: exact
        g = {i['id']: 1 + i['growth'] for i in ch['inputs']}
        self.assertEqual((round(g['margin_loans'], 4), round(g['margin_rate'], 4), round(ch['factor'], 4)), (1.4689, 0.9239, 1.3572))
        self.assertAlmostEqual(ch['factor'], (104 / 70.8) * (0.0425 / 0.046), places=12)
        self.assertEqual((ch['check']['line_growth'], ch['check']['status']), (0.36, 'consistent'))
        self.assertEqual(round(100 * ch['check']['residual_pp'], 2), 0.28)
        lc = mi['line_check']       # level: 104bn x 4.25% a year x 3/12 = 1.105bn against 1.11bn reported
        self.assertAlmostEqual(lc['computed'], 104e9 * 0.0425 * 3 / 12, places=3)
        self.assertEqual((lc['status'], round(100 * lc['diff'], 2)), ('consistent', -0.45))
        self.assertEqual(mi['status'], 'consistent')
        self.assertIn('per year', mi['variants']['average']['derived_from'])

    def test_6_ibkr_pass_through_and_rate_sensitivity(self):
        r = run('ibkr_2026q3')
        com, = [x for x in r['lines'] if x['id'] == 'commissions']
        pt = com['pass_through']
        self.assertAlmostEqual(pt['growth_ex_pass_through'], (602 - 30) / (602 / 1.12 - 0) - 1, places=12)
        self.assertEqual(round(100 * pt['growth_ex_pass_through'], 2), 6.42)
        us, other = [m for m in r['measures'] if m['check'] == 'rate_sensitivity_share']
        self.assertEqual((us['driver'], us['annualized_by'], us['annual_net_interest_income']), ('US policy rate -25bp', 4, 4.6e9))
        self.assertAlmostEqual(us['share'], 83e6 / (1.15e9 * 4), places=12)
        self.assertEqual(round(100 * us['share'], 2), 1.80)
        self.assertEqual(round(100 * other['share'], 2), 0.76)
        q2 = run('ibkr_2026q2')        # Q2's sensitivity is stated against revenue; without the NII line nothing is divided
        m, = [m for m in q2['measures'] if m['check'] == 'rate_sensitivity_share']
        self.assertEqual(m['status'], 'not_computed')
        c = case('ibkr_2026q3')
        c['sensitivities'][0]['driver'] = 'SYNTHETIC policy rate -100bp'
        m100 = next(m for m in econ.review(c, 'broker')['measures'] if m['driver'].startswith('SYNTHETIC'))
        self.assertEqual((m100['status'], 'share' in m100), ('not_computed', False))       # never rescaled to 25bp
        c = case('ibkr_2026q3')
        c['lines']['commissions'].pop('prior_pass_through')
        pt = next(x for x in econ.review(c, 'broker')['lines'] if x['id'] == 'commissions')['pass_through']
        self.assertEqual(pt['status'], 'not_computed')                  # a missing prior pass-through is asked, not assumed 0

    def test_7_ibkr_cyclical_deceleration_across_two_reviews(self):
        q2 = run('ibkr_2026q2')
        q3 = run('ibkr_2026q3', prior=q2)
        d, = [x for x in q3['readings'] if x['check'] == 'cyclical_deceleration']
        self.assertIs(d['value'], True)
        self.assertEqual({(c['id'], c['kpi_basis']): round(100 * c['change']) for c in d['cyclical']},
                         {('darts', 'average'): -18, ('margin_loans', 'balance'): -31, ('client_equity', 'balance'): -13})
        self.assertEqual({c['id']: round(100 * c['change']) for c in d['structural']}, {'accounts': 1})
        self.assertEqual((d['cyclical_slowdown_pp'], d['structural_stable_pp']), (0.1, 0.03))
        self.assertEqual(q3['prior_review'], {'period_end': '2026-06-30', 'input_sha256': q2['input_sha256']})
        alone, = [x for x in run('ibkr_2026q3')['readings'] if x['check'] == 'cyclical_deceleration']
        self.assertIsNone(alone['value'])
        c = case('ibkr_2026q3')        # SYNTHETIC: accounts growth 40% instead of 35% (+6pp): structural not stable
        next(k for k in c['kpis'] if k['id'] == 'accounts')['growth'] = 0.40
        d2, = [x for x in econ.review(c, 'broker', prior=q2)['readings'] if x['check'] == 'cyclical_deceleration']
        self.assertIs(d2['value'], False)
        with self.assertRaises(ValueError):
            econ.review(case('ibkr_2026q2'), 'broker', prior=q3)      # a prior must be earlier
        with self.assertRaises(ValueError):
            econ.review(case('pltr_2026q2'), 'broker', prior=q2)      # and of the same company


class SoftwareBankTests(unittest.TestCase):
    def test_8_pltr_expansion_led_on_us_commercial_q_on_q(self):
        r = run('pltr_2026q2')
        rpc = row(r, 'data_platform_software', 'revenue_per_customer', 'us_commercial')
        ch = rpc['changes'][0]
        self.assertEqual((rpc['status'], ch['window'], rpc['value']), ('computed', 'q/q', None))   # no customer count level
        self.assertAlmostEqual(ch['value'], 1.28 / 1.06 - 1, places=12)
        self.assertEqual(round(100 * ch['value'], 2), 20.75)
        total = row(r, 'data_platform_software', 'revenue_per_customer')
        self.assertEqual((total['status'], total['found_at']), ('scope_mismatch', ['us_commercial']))
        s, = [x for x in r['growth_splits'] if x['scope'] == 'us_commercial']
        per = next(f for f in s['factors'] if f['id'] == 'revenue_per_customer')
        self.assertAlmostEqual(per['share'], log(1.28 / 1.06) / log(1.28), places=12)
        self.assertEqual(round(per['share'], 3), 0.764)
        e, = [x for x in r['readings'] if x['check'] == 'expansion_led']
        self.assertEqual((e['value'], e['scope'], e['ndr'], e['ndr_scope'], e['expansion_ndr_min']), (True, 'us_commercial', 1.57, 'total', 1.1))
        self.assertEqual(round(e['ndr_change'], 2), 0.07)
        c = case('pltr_2026q2')        # SYNTHETIC: NDR 1.05, below expansion_ndr_min
        next(k for k in c['kpis'] if k['id'] == 'net_revenue_retention')['value'] = 1.05
        self.assertIs(next(x for x in econ.review(c, 'data_platform_software')['readings'] if x['check'] == 'expansion_led')['value'], False)

    def test_9_sofi_products_per_member_is_cumulative(self):
        r = run('sofi_2026q2')
        p = row(r, 'digital_bank', 'products_per_member')
        self.assertAlmostEqual(p['value'], 24.4 / 15.8, places=12)
        self.assertEqual((round(p['value'], 3), p['label'], p['tag']), (1.544, 'cumulative, not active', 'mix'))
        rd, = [x for x in r['readings'] if x['check'] == 'cumulative_kpi_not_activity']
        self.assertEqual(rd['identity'], 'products_per_member')
        dep = row(r, 'digital_bank', 'deposit_benefit_identity')      # 45.5bn x 156bp, a value per year (no line supplied)
        self.assertAlmostEqual(dep['value'], 45.5e9 * 0.0156, places=3)
        self.assertEqual(dep['variants']['end']['window'], 'year')
        self.assertEqual((r['models'][0]['core_status'], r['models'][0]['missing']), ('missing', ['earning_assets', 'net_interest_margin']))


class SegmentTests(unittest.TestCase):
    def test_10_spcx_segments_reconcile_to_the_consolidated_lines(self):
        rec = next(m for m in run('spcx_2026q3')['measures'] if m['check'] == 'segment_reconciliation')
        rev, oi = rec['revenue'], rec['operating_income']
        self.assertAlmostEqual(rev['sum'], 14.58e9, places=0)
        self.assertEqual((rev['consolidated'], rev['status'], round(100 * rev['diff'], 2)), (14.6e9, 'consistent', -0.14))
        self.assertAlmostEqual(oi['sum'], -0.007e9 + 5.25e9 * 0.45 + 0.431e9, places=0)
        self.assertEqual((round(oi['sum'] / 1e9, 4), oi['status'], round(100 * oi['diff'], 2)), (2.7865, 'consistent', -0.13))
        self.assertIn('segments[Starlink].operating_margin', oi['derived_from'])  # 5.25 x 45%, derived and labelled

    def test_11_starlink_is_partial_with_the_residual_named(self):
        r = run('spcx_2026q3')
        st = row(r, 'subscriber_network', 'revenue_identity', 'starlink')
        self.assertEqual((st['status'], st['variant']), ('partial', 'average'))
        avg, end = st['variants']['average'], st['variants']['end']
        self.assertAlmostEqual(avg['value'], (12.0e6 + 13.6e6) / 2 * 69 * 3, places=3)
        self.assertAlmostEqual(end['value'], 13.6e6 * 69 * 3, places=3)
        self.assertEqual((round(avg['value'] / 1e9, 2), round(100 * avg['value'] / 5.25e9, 1)), (2.65, 50.5))
        self.assertEqual((round(end['value'] / 1e9, 3), round(100 * end['value'] / 5.25e9, 1)), (2.815, 53.6))
        lc = st['line_check']
        self.assertEqual((lc['line_value'], round(lc['explained'], 3), lc['partial_below']), (5.25e9, 0.505, 0.9))
        res = st['residual']
        self.assertEqual((res['name'], res['status']), ('revenue not in subscribers x arpu', 'open'))
        self.assertAlmostEqual(res['value'], 5.25e9 - avg['value'], places=3)
        self.assertIn({'model': 'subscriber_network', 'about': 'revenue_identity', 'scope': 'starlink', 'question': res['question']},
                      r['open_questions'])
        summ = r['models'][0]
        self.assertEqual((summ['model'], summ['core_status']), ('subscriber_network', 'partial'))
        self.assertEqual([(x['identity'], x['scope']) for x in summ['residuals']], [('revenue_identity', 'starlink')])
        cap = row(r, 'compute_leasing', 'capex_per_gw')
        self.assertEqual((cap['status'], cap['check']), ('not_computed', 'capex_per_unit_lag'))
        self.assertTrue(cap['reason'].startswith('construction lag'))
        self.assertNotIn('value', cap)
        si = row(r, 'compute_leasing', 'revenue_identity', 'superintelligence')     # 52% of revenue, contracts unknown
        self.assertEqual((si['status'], si['missing']), ('missing', ['utilization', 'price_per_gw_year']))


class AdsMemoryTests(unittest.TestCase):
    def test_12_rddt_minor_line_flat_units_and_dropped_disclosure(self):
        r = run('rddt_2026q2')
        t, = flags(r, 'thesis_on_minor_line')
        self.assertEqual((t['severity'], t['line'], t['share'], t['threshold']), ('medium', 'other_revenue', 0.054, 0.1))
        self.assertAlmostEqual(t['incremental_share'], 0.054 * (1 - 1 / 1.24) / (1 - 1 / 1.61), places=12)
        self.assertEqual(round(100 * t['incremental_share'], 2), 2.76)
        us, = [f for f in flags(r, 'core_units_flat') if f['scope'] == 'us']
        self.assertEqual((us['severity'], us['window'], round(100 * us['growth'], 2), us['threshold']), ('medium', 'q/q', -0.56, 0.02))
        self.assertEqual((us['other_window'], round(100 * us['other_growth'], 2)), ('y/y', 5.77))   # shown beside, never hidden
        fal, = [x for x in r['falsifiers'] if x['id'] == 'dau' and x['scope'] == 'us']
        self.assertEqual((fal['status'], fal['last_period'], fal['from_period']), ('unobservable', '2026-Q3', '2026-Q4'))
        d, = flags(r, 'disclosure_dropped')
        self.assertEqual((d['severity'], d['kpi'], d['scope']), ('medium', 'dau', 'us'))
        other = [x for x in r['falsifiers'] if x['id'] == 'dau' and x['scope'] == 'us_logged_in']
        self.assertEqual(other[0]['status'], 'tracked')                    # the dropped disclosure is the US total only
        li = row(r, 'ads_platform', 'logged_in_share', 'us')               # one KPI read at two scopes
        self.assertEqual((li['read_at'], round(li['value'], 4)), ({'part': 'us_logged_in', 'total': 'us'}, round(23.1 / 53.2, 4)))
        c = case('rddt_2026q2')
        c['disclosure_changes'][0]['kind'] = 'opinion'                     # an opinion does not drop a disclosure
        self.assertEqual(flags(econ.review(c, 'ads_platform'), 'disclosure_dropped'), [])

    def test_13_skhy_margin_only_normalized_pe_is_a_floor(self):
        r = run('skhy_2026q2')
        m, = [x for x in r['measures'] if x['check'] == 'margin_only_normalized']
        self.assertEqual((m['label'], m['current_margin_from'], m['cycle_margin_kind'], m['attributed']),
                         ('margin_only', 'stated', 'analyst_estimate', True))
        self.assertIn('floor', m['note'])
        self.assertEqual(round(m['run_rate_pe'], 2), 7.54)
        self.assertAlmostEqual(m['normalized_pe'], 132 / 17.5 * 0.763 / 0.29, places=12)
        self.assertEqual(round(m['normalized_pe'], 2), 19.85)
        self.assertEqual(round(m['at_line_margin']['normalized_pe'], 3), 19.856)    # 41.3 / 54.1 = 76.34%
        ident = row(r, 'memory_cycle', 'revenue_identity')
        self.assertEqual((ident['status'], ident['missing']), ('missing', ['bits']))
        self.assertEqual(r['models'][0]['core_status'], 'missing')
        self.assertEqual(r['growth_splits'][0]['unassigned']['includes'], ['bits'])


class ContractTests(unittest.TestCase):
    def test_14_every_fixture_twice_gives_identical_output_and_hash(self):
        for name in MODELS:
            a, b = run(name), run(name)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), name)
            self.assertEqual(a['input_sha256'], F.digest(case(name)), name)
            json.dumps(a, allow_nan=False)
            self.assertLessEqual({r['status'] for r in a['identities']}, set(econ.IDENTITY_STATUSES), name)
            self.assertLessEqual({m['core_status'] for m in a['models']}, set(econ.IDENTITY_STATUSES), name)
            c = case(name)
            random.Random(7).shuffle(c.get('kpis') or [])              # the order of the KPI rows never matters
            s = econ.review(c, MODELS[name])
            s.pop('input_sha256')
            a.pop('input_sha256')
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(s, sort_keys=True), name)
        self.assertEqual(set(econ.CORE_ORDER), set(econ.IDENTITY_STATUSES))
        c = case('ibkr_2026q3')
        c['lines']['revenue']['value'] = 1.871e9
        self.assertNotEqual(econ.review(c, 'broker')['input_sha256'], run('ibkr_2026q3')['input_sha256'])

    def test_15_anti_invention_every_number_is_an_input_or_derived(self):
        for name in MODELS:
            self.assertEqual(unsourced(run(name), case(name)), [], name)
        out = run('ibkr_2026q3')
        self.assertFalse({0.4242, 0.31, 0.1234} & set(numbers(case('ibkr_2026q3'))))     # the planted values are no inputs
        out['flags'][0]['confidence'] = 0.4242                             # a planted constant beside a derived_from
        out['identities'][0]['variants']['end']['value'] = 1340.5          # and one that replaced a derived value is caught
        out['readings'].append({'check': 'planted', 'value': 0.31})       # by the exact tests; one without provenance here
        self.assertEqual(unsourced(out, case('ibkr_2026q3')), [('.flags.0.confidence', 0.4242), ('.readings.1.value', 0.31)])
        real = econ._flag

        def planted(ix, key, model_id, scope, **data):                     # a constant planted inside the engine
            return {**real(ix, key, model_id, scope, **data), 'haircut': 0.1234}
        with mock.patch.object(econ, '_flag', planted):
            bad = unsourced(run('ibkr_2026q3'), case('ibkr_2026q3'))
        self.assertEqual(bad, [('.flags.0.haircut', 0.1234)])

        def unexplained(ix, key, model_id, scope, **data):                # a derived number that drops its derivation
            data.pop('derived_from', None)
            return real(ix, key, model_id, scope, **data)
        with mock.patch.object(econ, '_flag', unexplained):
            bad = unsourced(run('ibkr_2026q3'), case('ibkr_2026q3'))
        self.assertEqual([k for k, _ in bad], ['.flags.0.per_unit_change'])

    def test_case_is_not_mutated_and_models_are_not_assessed_without_selection(self):
        c = case('spcx_2026q3')
        before = deepcopy(c)
        econ.review(c, MODELS['spcx_2026q3'])
        self.assertEqual(c, before)
        for sel in (None, 'unclassified', {'primary': 'unclassified'}):
            r = econ.review(case('ibkr_2026q3'), sel)
            self.assertEqual((r['status'], r['identities'], r['flags'], r['models']), ('not_assessed', [], [], []))
        with self.assertRaises(ValueError):
            econ.review(case('ibkr_2026q3'), 'bank_tree')                # an unknown model is refused, never relabelled

    def test_public_functions_equal_the_review_rows(self):
        c = case('ibkr_2026q3')
        r = econ.review(c, 'broker')
        self.assertEqual(econ.evaluate_identity(c, 'broker', 'revenue_per_account'), row(r, 'broker', 'revenue_per_account'))
        self.assertEqual(econ.growth_split(c, 'broker'), r['growth_splits'][0])
        self.assertEqual(econ.identity('broker', 'revenue_per_account')['type'], 'per_unit')      # the WP1 lookup is unchanged
        self.assertEqual(econ.evaluate_identity(case('pltr_2026q2'), 'data_platform_software', 'revenue_per_customer', 'US_Commercial')['scope'],
                         'us_commercial')

    def test_bases_scopes_windows_and_point_in_time(self):
        c = case('ibkr_2026q3')        # SYNTHETIC: accounts stated as a flow, a basis the registry does not declare
        next(k for k in c['kpis'] if k['id'] == 'accounts')['kpi_basis'] = 'flow'
        rpa = row(econ.review(c, 'broker'), 'broker', 'revenue_per_account')
        self.assertEqual((rpa['status'], rpa['questions']), ('basis_mismatch', ['supply accounts on a balance basis (stated flow)']))
        c = case('ibkr_2026q3')        # SYNTHETIC: client equity stated in EUR in a USD case
        next(k for k in c['kpis'] if k['id'] == 'client_equity')['unit'] = 'EUR'
        r = econ.review(c, 'broker')
        yld = row(r, 'broker', 'revenue_yield_on_equity')                 # USD revenue over EUR equity: never divided
        self.assertEqual((yld['status'], yld['questions']), ('basis_mismatch', ['supply client equity in USD']))
        epa = row(r, 'broker', 'equity_per_account')                      # EUR per account is a figure, but not the post's USD one
        self.assertEqual((epa['status'], [x['status'] for x in epa['stated']]), ('computed', ['not_compared']))
        self.assertEqual(r['growth_splits'][0]['status'], 'basis_mismatch')
        c = case('ibkr_2026q3')        # SYNTHETIC: a margin rate averaged over a trailing year against a quarter's interest
        next(k for k in c['kpis'] if k['id'] == 'margin_rate')['window'] = 'TTM'
        mi = row(econ.review(c, 'broker'), 'broker', 'margin_interest')
        self.assertEqual((mi['status'], mi['windows']), ('scope_mismatch', {'margin_interest_income': 'quarter', 'margin_rate': 'TTM'}))
        self.assertNotIn('value', mi)
        c = case('uber_2025q3')        # SYNTHETIC: a trailing-year Mobility figure beside the quarter's: the quarter is read
        c['kpis'].append({'id': 'gross_bookings', 'value': 98e9, 'kpi_basis': 'flow', 'scope': 'mobility', 'window': 'TTM',
                          'source_ids': ['analyst_post_uber_2025q3']})
        self.assertEqual(row(econ.review(c, 'marketplace'), 'marketplace', 'take_rate', 'mobility')['value'], 7.68e9 / 25.1e9)
        c = case('ibkr_2026q3')        # SYNTHETIC: a source dated after the case is invisible
        c['sources']['late'] = {'kind': 'issuer_release', 'available_at': '2026-10-22'}
        next(k for k in c['kpis'] if k['id'] == 'accounts')['source_ids'] = ['late']
        r = econ.review(c, 'broker')
        self.assertIn('kpis[accounts|total|balance]', r['unavailable'])
        self.assertEqual(row(r, 'broker', 'revenue_per_account')['status'], 'missing')
        bad = [lambda c: c['kpis'][0].update(source_ids=['nope']), lambda c: c['kpis'][0].update(kpi_basis='active'),
               lambda c: c['kpis'].append(dict(c['kpis'][0])), lambda c: c['kpis'][0].update(growth=-1.0),
               lambda c: c['lines']['revenue'].update(window='qoq'), lambda c: c.update(period='fortnight'),
               lambda c: c['kpis'][0].update(scope=' ')]
        for mutate in bad:
            c = case('ibkr_2026q3')
            mutate(c)
            with self.assertRaises(ValueError):
                econ.review(c, 'broker')

    def test_no_input_invariance_nothing_in_the_engine_calls_the_review(self):
        import fundamental_engine
        pkg = Path(fundamental_engine.__file__).parent
        callers = sorted(p.name for p in pkg.glob('*.py') if p.name != 'econ.py'
                         and any(s in p.read_text(encoding='utf-8') for s in ('econ.review', 'evaluate_identity', 'growth_split(')))
        self.assertEqual(callers, [])


class RepairTests(unittest.TestCase):
    """WP3 review findings: each test fails on f6ce33f."""

    def test_market_and_cycle_are_read_point_in_time(self):
        late = {'kind': 'market_data', 'available_at': '2099-01-01'}     # SYNTHETIC: a source published after as_of

        def margin_only(c):
            r = econ.review(c, 'memory_cycle')
            m, = [x for x in r['measures'] if x['check'] == 'margin_only_normalized']
            return m, r['unavailable']
        c = case('skhy_2026q2')
        c['sources']['late'] = late
        c['market']['source_ids'] = ['late']                              # a price published after as_of is not used
        m, un = margin_only(c)
        self.assertEqual((m['status'], 'normalized_pe' in m, m['unavailable'], un), ('not_computed', False, ['market'], ['market']))
        c = case('skhy_2026q2')
        c['sources']['late'] = late
        c['cycle']['cycle_margin']['source_ids'] = ['late']               # nor a cycle margin
        m, un = margin_only(c)
        self.assertEqual((m['status'], 'normalized_pe' in m, un), ('not_computed', False, ['cycle.cycle_margin']))
        c = case('skhy_2026q2')        # the cycle block's stated margin is not read; the cycle margin has its own visible source
        c['sources']['late'] = late
        c['cycle']['source_ids'] = ['late']
        m, un = margin_only(c)
        self.assertEqual((m['status'], m['current_margin_from'], un, 'at_line_margin' in m), ('computed', 'lines', ['cycle'], False))
        self.assertAlmostEqual(m['normalized_pe'], 132 / 17.5 * (41.3e9 / 54.1e9) / 0.29, places=9)
        c = case('skhy_2026q2')
        c['sources']['late'] = late
        c['market']['source_ids'] = c['cycle']['source_ids'] = c['cycle']['cycle_margin']['source_ids'] = ['late']
        m, un = margin_only(c)
        self.assertEqual((m['status'], un), ('not_computed', ['cycle', 'cycle.cycle_margin', 'market']))
        c = case('ibkr_2026q3')        # a sensitivity and a disclosure change dated after as_of are listed, not dropped silently
        c['sources']['late'] = dict(late, kind='analyst_post')
        c['sensitivities'][1]['source_ids'] = ['late']
        r = econ.review(c, 'broker')
        self.assertEqual([m['driver'] for m in r['measures'] if m['check'] == 'rate_sensitivity_share'], ['US policy rate -25bp'])
        self.assertIn('sensitivities[1]', r['unavailable'])
        c = case('rddt_2026q2')
        c['sources']['late'] = dict(late, kind='analyst_post')
        c['disclosure_changes'][0]['source_ids'] = ['late']
        r = econ.review(c, 'ads_platform')
        self.assertEqual(flags(r, 'disclosure_dropped'), [])
        self.assertIn('disclosure_changes[0]', r['unavailable'])

    def test_identity_status_folds_the_growth_check_of_every_window(self):
        c = case('uber_2025q3')        # SYNTHETIC q/q growths: MAPCs +5%, frequency +2% (product 1.071); trips only q/q 2.0bn
        k = {x['id']: x for x in c['kpis']}
        k['mapcs']['growth_qoq'], k['frequency']['growth_qoq'] = 0.05, 0.02
        k['trips'].pop('prior')
        k['trips']['prior_quarter'] = 2.0e9                                # trips +75.6% q/q against the factors' +7.1%
        t = econ.evaluate_identity(c, 'marketplace', 'trips_from_consumers')
        yoy, qoq = t['changes']
        self.assertEqual((yoy['window'], 'check' in yoy, qoq['window'], qoq['check']['status']), ('y/y', False, 'q/q', 'inconsistent'))
        self.assertAlmostEqual(qoq['check']['residual_pp'], 3.512e9 / 2.0e9 - 1.05 * 1.02, places=12)
        self.assertEqual(round(qoq['check']['residual_pp'], 3), 0.685)
        self.assertEqual(t['status'], 'inconsistent')                     # the row never contradicts its own growth check
        k['trips']['prior'] = 2.879e9                                      # the post's y/y trips back: a consistent y/y hides nothing
        r = econ.review(c, 'marketplace')
        t = row(r, 'marketplace', 'trips_from_consumers')
        self.assertEqual([(ch['window'], ch['check']['status']) for ch in t['changes']], [('y/y', 'consistent'), ('q/q', 'inconsistent')])
        self.assertEqual((t['status'], r['models'][0]['core_status']), ('inconsistent', 'inconsistent'))
        self.assertEqual(r['check_window'], econ.CHECK_WINDOW)

    def test_core_status_never_hides_an_inconsistent_scope(self):
        c = case('ibkr_2026q3')        # SYNTHETIC: the post's own figure 2,000 (computed 1,340.5) and a Europe scope that computes
        st = next(k for k in c['kpis'] if k['id'] == 'revenue_per_account')
        st['value'] = 2000.0
        c['kpis'].append({'id': 'accounts', 'value': 1.0e6, 'prior': 0.8e6, 'kpi_basis': 'balance', 'scope': 'europe',
                          'source_ids': st['source_ids']})
        c['lines']['europe_revenue'] = {'value': 3.0e8, 'prior': 2.5e8, 'scope': 'europe', 'period': 'quarter', 'source_ids': st['source_ids']}
        r = econ.review(c, 'broker')
        self.assertEqual((row(r, 'broker', 'revenue_per_account')['status'], row(r, 'broker', 'revenue_per_account', 'europe')['status']),
                         ('inconsistent', 'computed'))
        m = r['models'][0]
        core, = [x for x in m['core'] if x['id'] == 'revenue_per_account']
        self.assertEqual((m['core_status'], core['scope'], core['status']), ('inconsistent', 'total', 'inconsistent'))
        self.assertEqual(core['scopes'], [{'scope': 'total', 'status': 'inconsistent'}, {'scope': 'europe', 'status': 'computed'}])
        p = run('pltr_2026q2')['models'][0]          # a row that computed something still beats a total that computed nothing
        self.assertEqual([(x['id'], x['scope'], x['status']) for x in p['core']],
                         [('revenue_per_customer', 'us_commercial', 'computed'), ('revenue_identity', 'us_commercial', 'consistent')])
        self.assertEqual(p['core_status'], 'computed')

    def test_degenerate_lines_are_refused_never_divided(self):
        for nii in (0.0, -1.0e9):          # SYNTHETIC net interest income of zero and below zero
            c = case('ibkr_2026q3')
            c['lines']['net_interest_income']['value'] = nii
            ms = [m for m in econ.review(c, 'broker')['measures'] if m['check'] == 'rate_sensitivity_share']
            self.assertEqual([(m['status'], 'share' in m) for m in ms], [('not_computed', False)] * 2, nii)
            self.assertIn('is not positive', ms[0]['reason'])
        for oi in (0.0, -5.0e9):           # SYNTHETIC operating income of zero and below zero beside the stated 76.3% margin
            c = case('skhy_2026q2')
            c['lines']['operating_income']['value'] = oi
            m, = [x for x in econ.review(c, 'memory_cycle')['measures'] if x['check'] == 'margin_only_normalized']
            self.assertEqual((m['status'], m['current_margin_from'], m['at_line_margin']['status']), ('computed', 'stated', 'not_computed'), oi)
            self.assertNotIn('normalized_pe', m['at_line_margin'])
            self.assertEqual(round(m['normalized_pe'], 2), 19.85)
            c['cycle'].pop('current_operating_margin')                     # and with no stated margin nothing is normalized
            m, = [x for x in econ.review(c, 'memory_cycle')['measures'] if x['check'] == 'margin_only_normalized']
            self.assertEqual((m['status'], 'normalized_pe' in m), ('not_computed', False), oi)

    def test_each_stated_figure_is_compared_on_its_own_basis(self):
        c = case('ibkr_2026q3')
        st = next(k for k in c['kpis'] if k['id'] == 'revenue_per_account')
        avg = dict(st, kpi_basis='average', value=1500.0)                 # SYNTHETIC: a second figure on an average basis
        a, b = deepcopy(c), deepcopy(c)
        a['kpis'].append(avg)
        b['kpis'].insert(0, avg)
        ra = econ.evaluate_identity(a, 'broker', 'revenue_per_account')
        self.assertEqual(ra, econ.evaluate_identity(b, 'broker', 'revenue_per_account'))      # the row order never matters
        self.assertEqual([(x['kpi_basis'], x['variant'], x['status']) for x in ra['stated']],
                         [('average', None, 'not_compared'), ('balance', 'end', 'consistent')])
        self.assertIn('no average value', ra['stated'][0]['reason'])
        self.assertEqual(ra['status'], 'consistent')                       # never compared with the end value
        next(k for k in a['kpis'] if k['id'] == 'accounts')['prior_quarter'] = 5.2e6     # SYNTHETIC start-of-quarter accounts
        r = econ.evaluate_identity(a, 'broker', 'revenue_per_account')
        x = r['stated'][0]
        self.assertEqual((x['kpi_basis'], x['variant'], x['status']), ('average', 'average', 'inconsistent'))
        self.assertAlmostEqual(x['computed'], 1.87e9 * 4 / ((5.2e6 + 5.58e6) / 2), places=6)
        self.assertEqual((r['stated'][1]['status'], r['status']), ('consistent', 'inconsistent'))
        c['kpis'].append(dict(st, scope='europe', value=1200.0))          # SYNTHETIC: a figure where no row computes
        nr = econ.review(c, 'broker')['not_read']
        self.assertIn({'ref': 'kpis[revenue_per_account|europe|balance]',
                       'reason': 'no row of this identity at europe computed a value to compare it with'}, nr)
        for basis in ('rate', 'average'):  # SYNTHETIC Mobility take rate 30.6%: revenue and bookings are period rows, so a
            u = case('uber_2025q3')        # rate or an average figure is compared with the period value 7.68 / 25.1
            u['kpis'].append({'id': 'take_rate', 'value': 0.306, 'kpi_basis': basis, 'scope': 'mobility', 'source_ids': u['kpis'][0]['source_ids']})
            x, = econ.evaluate_identity(u, 'marketplace', 'take_rate', 'mobility')['stated']
            self.assertEqual((x['variant'], x['status']), ('period', 'consistent'), basis)
            self.assertAlmostEqual(x['computed'], 7.68e9 / 25.1e9, places=12)

    def test_expansion_led_names_a_cross_scope_ndr(self):
        r = run('pltr_2026q2')
        e, = [x for x in r['readings'] if x['check'] == 'expansion_led']
        self.assertEqual((e['value'], e['scope'], e['ndr_scope']), (True, 'us_commercial', 'total'))
        self.assertTrue(e['scope_note'].startswith('cross-scope'))
        self.assertIn({'model': 'data_platform_software', 'about': 'expansion_led', 'scope': 'us_commercial',
                       'question': 'supply net revenue retention for us_commercial (reported for total only)'}, r['open_questions'])
        c = case('pltr_2026q2')        # SYNTHETIC: an NDR reported for US commercial itself is read at that scope
        c['kpis'].append({'id': 'net_revenue_retention', 'value': 1.6, 'kpi_basis': 'rate', 'scope': 'us_commercial', 'window': 'TTM',
                          'source_ids': c['kpis'][0]['source_ids']})
        r = econ.review(c, 'data_platform_software')
        e, = [x for x in r['readings'] if x['check'] == 'expansion_led']
        self.assertEqual((e['ndr'], e['ndr_scope'], 'scope_note' in e), (1.6, 'us_commercial', False))
        self.assertEqual([q for q in r['open_questions'] if q['about'] == 'expansion_led'], [])

    def test_growth_split_reads_no_factor_on_an_undeclared_basis(self):
        c = case('uber_2025q3')        # SYNTHETIC: frequency stated as a flow; the registry declares an average
        next(k for k in c['kpis'] if k['id'] == 'frequency')['kpi_basis'] = 'flow'
        s = econ.growth_split(c, 'marketplace')
        self.assertEqual((s['status'], s['problems']),
                         ('basis_mismatch', [{'id': 'frequency', 'problem': 'kpi_basis', 'stated': 'flow', 'declared': ['average']}]))
        self.assertNotIn('frequency', [f['id'] for f in s['factors']])     # its growth is never read as the factor's
        self.assertIn('frequency', s['missing'])
        self.assertIn('supply frequency on an average basis (stated flow)', [q['question'] for q in econ.review(c, 'marketplace')['open_questions']])


if __name__ == '__main__':
    unittest.main()
