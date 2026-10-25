"""WP5 of the econ layer: overlays for funding, the supplier chain, acquisition SBC, bank capital, contract coverage and
segments, and business.demand_tags.

The numbers are the analyst posts' figures in tests/fixtures/econ (arithmetic fixtures, not truth; the WP0 `expected`
blocks are never engine inputs); the rules are the design's (docs/prereg_econ.md, brain/economics.json overlays). Where a
test needs a value the post did not give it is marked SYNTHETIC. Every behaviour test fails without WP5 (no overlay
functions, no `overlays` in the review, no business.demand_tags); the no-wiring guard passes on the base by design.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from fundamental_engine import business as B, econ, facts as F
from test_business import ctx, fact
from test_econ import MODELS, case, numbers, run, unsourced

WRAPPERS = ('flags', 'questions', 'model', 'unavailable', 'as_of', 'company', 'economics_version', 'registry_sha256',
            'input_sha256', 'engine_version', 'boundary')


def keys_of(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from keys_of(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys_of(v)


def flag_keys(out):
    return [f['key'] for f in out['flags']]


def synthetic_vf(**over):
    """SYNTHETIC: a software company with no customer financing, its 10-K giving two matched receivables windows."""
    c = {'company': 'SYN', 'period_end': '2025-12-31', 'as_of': '2026-02-20', 'currency': 'USD', 'period': 'year',
         'sources': {'k10': {'kind': 'issuer_filing', 'available_at': '2026-02-20'}},
         'lines': {'revenue': {'value': 20e9, 'growth': 0.10, 'period': 'year', 'source_ids': ['k10']}},
         'funding_links': [],
         'statements': {'period': 'year', 'operating_cash_flow': 9e9, 'net_income': 7e9, 'non_cash_gains': 0, 'source_ids': ['k10']},
         'balance': {'receivables_start': 2.0e9, 'receivables_end': 2.2e9, 'receivables_window_months': 12, 'source_ids': ['k10']},
         'receivables_windows': [{'label': 'FY2024', 'months': 12, 'receivables_start': 1.8e9, 'receivables_end': 2.0e9,
                                  'revenue_growth': 0.12, 'source_ids': ['k10']}]}
    c.update(over)
    return c


class VendorFinancingTests(unittest.TestCase):
    """Design WP5 'NVDA ledger'."""

    @classmethod
    def setUpClass(cls):
        cls.vf = econ.vendor_financing(case('nvda_2026q2'))

    def test_nvda_ledger_shares_firm_and_ceiling_apart(self):
        vf = self.vf
        self.assertAlmostEqual(vf['annualized_revenue']['value'], 384.8e9, delta=1)
        self.assertEqual(vf['annualized_revenue']['annualized_by'], 4)
        led = vf['ledger']
        pct = {g: round(led[g]['share'] * 100, 1) for g in ('funded', 'committed', 'take_or_pay', 'firm', 'contingent_ceiling')}
        self.assertEqual(pct, {'funded': 25.7, 'committed': 6.5, 'take_or_pay': 9.4, 'firm': 41.6, 'contingent_ceiling': 27.3})
        self.assertEqual((led['firm']['amount'], led['firm']['links']), (160e9, ['fl1', 'fl2', 'fl4']))
        self.assertNotIn('fl3', led['firm']['links'])                          # the guarantee ceiling is never summed in
        self.assertEqual((led['contingent_ceiling']['amount'], led['contingent_ceiling']['includes_maximum']), (105e9, ['fl3']))
        self.assertIn('never summed', led['contingent_ceiling']['label'])
        self.assertIn('not revenue at risk', vf['label'])
        self.assertTrue(vf['level']['firm'])

    def test_nvda_statement_checks_on_matched_windows_only(self):
        sc = self.vf['statement_checks']
        self.assertEqual((sc['window']['from'], sc['window']['period'], sc['window']['label']), ('half_year', 'half', 'H1 FY2027'))
        cfo = sc['cfo_to_ni']
        self.assertEqual(round(cfo['cfo_to_ni']['value'], 4), 0.6305)                  # 74.4 / 118
        self.assertEqual(round(cfo['ex_gains']['value'], 3), 0.789)                    # 74.4 / (118 - 23.7)
        self.assertEqual(cfo['status'], 'fails')
        near = cfo['ex_gains']['near']                                                  # within 0.03 of 0.80: shown with it
        self.assertEqual((near['threshold'], near['near_threshold'], near['display']), (0.8, 0.03, '0.789 near_threshold (0.80)'))
        rv = sc['receivables_vs_revenue']
        self.assertEqual(round(rv['windows'][0]['receivables_growth']['value'], 4), 0.6364)   # 63 / 38.5 - 1 over 6 months
        self.assertEqual(rv['status'], 'not_computed')
        self.assertTrue(rv['windows'][0]['reason'].startswith('window_mismatch'))     # revenue grows y/y and q/q, not over 6 months
        self.assertNotIn('gap', rv['windows'][0])
        bs = sc['receivables_build_share']
        self.assertEqual((bs['build']['value'], bs['build']['basis'], round(bs['share']['value'], 4)), (24.6e9, 'cash-flow statement', 0.2085))
        self.assertEqual((bs['balance_change']['value'], bs['balance_change']['same_window']), (24.5e9, True))   # both kept
        self.assertEqual(sc['related_party_share']['status'], 'not_supplied')

    def test_nvda_level_high_and_the_exposed_share_never_sets_it(self):
        lv = self.vf['level']
        self.assertEqual((lv['value'], lv['failing'], lv['firm'], lv['statement']), ('high', ['cfo_to_ni'], True, True))
        es = self.vf['exposed_share']
        self.assertEqual((es['value'], es['kind'], es['attributed'], es['sets_level']), (0.25, 'analyst_estimate', True, False))
        self.assertIn('never sets the level', es['label'])
        for v in (None, 0.0, 0.99):                       # removed or changed, the level does not move
            c = case('nvda_2026q2')
            if v is None:
                c.pop('exposed_share')
            else:
                c['exposed_share']['value'] = v
            self.assertEqual(econ.vendor_financing(c)['level']['value'], 'high', v)

    def test_nvda_guarantee_if_called_in_full_against_spendable_cash(self):
        g = self.vf['guarantee_ceiling']
        self.assertIn('if called in full', g['label'])
        self.assertEqual((g['contingent'], g['spendable_cash'], g['spendable_cash_ref']), (105e9, 56.6e9, 'balance.cash_and_bonds'))
        self.assertEqual(round(g['multiple_of_spendable_cash']['value'], 3), 1.855)
        self.assertEqual(g['investment_assets_beside']['marketable_equities'], 42.8e9)   # beside spendable cash, never added
        self.assertFalse([k for k in keys_of(self.vf) if 'prob' in k.lower()])           # no probability anywhere

    def test_syndicated_reports_count_once_and_conflicts_are_refused(self):
        c = case('nvda_2026q2')         # SYNTHETIC: a second outlet reports the same funded stake
        c['sources']['outlet_b'] = {'kind': 'news', 'available_at': '2026-10-21'}
        c['funding_links'].append({**c['funding_links'][0], 'source_ids': ['outlet_b']})
        vf = econ.vendor_financing(c)
        self.assertEqual(vf['ledger']['funded']['amount'], 99e9)
        self.assertEqual(next(l for l in vf['links'] if l['id'] == 'fl1')['source_ids'], ['analyst_post_nvda_2026q2', 'outlet_b'])
        self.assertEqual([m['id'] for m in vf['merged_reports']], ['fl1'])
        c['funding_links'][-1]['amount'] = 100e9         # the same id with other terms: one figure per deal
        with self.assertRaises(ValueError):
            econ.vendor_financing(c)
        c = case('nvda_2026q2')         # SYNTHETIC: another id with identical terms is counted, and asked about
        c['funding_links'].append({**c['funding_links'][1], 'id': 'fl5'})
        vf = econ.vendor_financing(c)
        self.assertEqual(vf['ledger']['committed']['amount'], 50e9)
        self.assertTrue(any('fl2 and fl5' in q['question'] for q in vf['questions']))

    def test_customer_prepayments_are_not_vendor_financing(self):
        c = case('nvda_2026q2')         # SYNTHETIC: a customer prepays the company
        c['funding_links'].append({'id': 'fl6', 'kind': 'prepayment', 'status': 'funded', 'amount': 20e9,
                                   'direction': 'customer_funds_company', 'source_ids': ['analyst_post_nvda_2026q2']})
        led = econ.vendor_financing(c)['ledger']
        self.assertEqual((led['firm']['amount'], led['customer_funded']['amount']), (160e9, 20e9))

    def test_level_is_three_valued_and_controls(self):
        c = case('nvda_2026q2')         # no ledger supplied: the firm side is undecided, never read as zero
        c.pop('funding_links')
        lv = econ.vendor_financing(c)['level']
        self.assertEqual((lv['value'], lv['could_be'], lv['undetermined']), ('elevated', 'high', ['firm_exposure']))
        lv = econ.vendor_financing(synthetic_vf())['level']          # negative control shape: none found, both checks pass
        self.assertEqual((lv['value'], lv['firm'], lv['statement'], lv['undetermined']), ('none', False, False, []))
        out = econ.vendor_financing(synthetic_vf(
            balance={'receivables_start': 2.0e9, 'receivables_end': 2.8e9, 'receivables_window_months': 12, 'source_ids': ['k10']},
            receivables_windows=[{'label': 'FY2024', 'months': 12, 'receivables_start': 1.8e9, 'receivables_end': 2.6e9,
                                  'revenue_growth': 0.12, 'source_ids': ['k10']}]))
        rv = out['statement_checks']['receivables_vs_revenue']       # +40% vs +10% and +44% vs +12% in two matched windows
        self.assertEqual((rv['status'], rv['matched'], rv['failing']), ('fails', ['balance', 'FY2024'], ['balance', 'FY2024']))
        self.assertEqual(round(rv['windows'][0]['gap']['value'], 4), 0.3)
        self.assertEqual(out['level']['value'], 'elevated')
        one = econ.vendor_financing(synthetic_vf(receivables_windows=[]))     # one matched window cannot decide the check
        self.assertEqual(one['statement_checks']['receivables_vs_revenue']['status'], 'not_computed')
        self.assertEqual((one['level']['value'], one['level']['could_be']), ('none', 'elevated'))
        nog = synthetic_vf()
        nog['statements'].pop('non_cash_gains')                      # gains unknown: the ratio the level reads is not computed
        self.assertEqual(econ.vendor_financing(nog)['statement_checks']['cfo_to_ni']['status'], 'undetermined')


class ChainTests(unittest.TestCase):
    """Design WP5 'SKHY chain'."""

    @classmethod
    def setUpClass(cls):
        cls.nv = run('nvda_2026q2')

    def test_skhy_chain_unquantified_with_the_missing_number_named(self):
        out = econ.chain(case('skhy_2026q2'), self.nv)
        self.assertEqual((out['status'], out['missing'], out['exposure']), ('unquantified', ['SKHY revenue share via NVDA'], None))
        self.assertEqual((out['upstream']['linked'], out['upstream']['review_sha256']), (True, self.nv['input_sha256']))
        self.assertEqual(out['upstream']['funding_level'], 'high')
        alone = econ.chain(case('skhy_2026q2'))
        self.assertEqual(alone['missing'], ['SKHY revenue share via NVDA', 'NVDA exposed share (its econ review)'])

    def test_synthetic_share_times_upstream_exposed_share_labelled_with_both_sources(self):
        c = case('skhy_2026q2')         # SYNTHETIC share (the fixture's labelled test assumption)
        c['chain']['share_of_revenue_via_chain'] = {'value': 0.3, 'source_ids': ['test_assumption_chain_share']}
        out = econ.chain(c, self.nv)
        self.assertEqual(out['status'], 'computed')
        self.assertAlmostEqual(out['exposure']['value'], 0.075, places=12)
        lab = ' | '.join(out['exposure']['labelled_with'])
        self.assertIn('test_assumption_chain_share (test_assumption)', lab)
        self.assertIn('analyst_estimate', lab)
        self.assertIn(self.nv['input_sha256'][:12], lab)
        r = econ.review(c, 'memory_cycle', upstream=self.nv)       # upstream numbers are cited, never copied in as facts
        plain = econ.review(c, 'memory_cycle')
        for k in ('kpis', 'lines', 'identities', 'growth_splits', 'measures'):
            self.assertEqual(r[k], plain[k], k)
        self.assertEqual(r['overlays']['funded_customer_chain']['exposure'], out['exposure'])

    def test_link_is_checked_by_sha256_company_and_date(self):
        c = case('skhy_2026q2')
        c['chain']['upstream_review_sha256'] = self.nv['input_sha256']
        self.assertEqual(econ.chain(c, self.nv)['upstream']['linked_by'], 'the sha256 the case cites')
        c['chain']['upstream_review_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            econ.chain(c, self.nv)
        with self.assertRaises(ValueError):
            econ.chain(case('skhy_2026q2'), run('ibkr_2026q3'))     # a review of another company
        late = {**self.nv, 'as_of': '2026-10-22'}                    # SYNTHETIC: a review dated after the case
        out = econ.chain(case('skhy_2026q2'), late)
        self.assertEqual((out['upstream']['linked'], out['status']), (False, 'unquantified'))
        with self.assertRaises(ValueError):                           # an upstream review without a chain block
            econ.review(case('ibkr_2026q3'), 'broker', upstream=self.nv)


class ContractCoverageTests(unittest.TestCase):
    """Design WP5 'SKHY LTAs'."""

    def test_skhy_ltas_unproven_and_no_damping(self):
        out = econ.contract_coverage(case('skhy_2026q2'))
        self.assertEqual((out['status'], out['missing'], out['cycle_damping']),
                         ('lta_unproven', ['share_of_next_year_volume', 'price_mechanism'], 'none'))
        self.assertEqual(([f['key'] for f in out['flags']], out['flags'][0]['severity']), (['lta_unproven'], 'info'))
        self.assertEqual((out['items']['tenor_years'], out['customers'], out['items']['deposits']), ([3, 5], 10, True))

    def test_all_four_items_need_an_issuer_document(self):
        c = case('skhy_2026q2')         # SYNTHETIC: all four items
        c['contracts'].update(share_of_next_year_volume=0.5, price_mechanism='floor')
        out = econ.contract_coverage(c)
        self.assertEqual((out['status'], out['missing'], out['evidence']['issuer_document']), ('lta_unproven', [], False))
        c['sources']['skhy_filing'] = {'kind': 'issuer_filing', 'available_at': '2026-07-31'}
        c['contracts']['source_ids'] = ['skhy_filing']
        out = econ.contract_coverage(c)
        self.assertEqual((out['status'], out['flags']), ('evidenced', []))
        self.assertTrue(out['cycle_damping'].startswith('eligible'))


class AcquisitionSbcTests(unittest.TestCase):
    """Design WP5 'SPCX SBC'."""

    def test_spcx_ongoing_ratio_straight_line_and_schedule_mismatch(self):
        out = econ.acquisition_sbc(case('spcx_2026q3'))
        self.assertEqual(round(out['ratios']['ongoing']['value'], 4), 0.0781)        # (2.36 - 1.22) / 14.6, not 16.2%
        self.assertEqual(round(out['ratios']['headline']['value'], 4), 0.1616)
        self.assertEqual((out['schedule']['basis'], out['schedule']['per_year']['value']), ('straight_line_assumed', 7.8e9))
        self.assertEqual(out['readings'][0]['check'], 'straight_line_assumed')
        self.assertEqual((out['observed_annualized']['value'], out['observed_annualized']['annualized_by']), (4.88e9, 4))
        self.assertEqual((round(out['vs_schedule']['value'], 4), out['vs_schedule']['status']), (-0.3744, 'mismatch'))
        self.assertEqual([(f['key'], f['severity']) for f in out['flags']], [('tranche_schedule_mismatch', 'medium')])
        self.assertTrue(any('vesting schedule' in q['question'] for q in out['questions']))
        self.assertTrue(out['dilution'].startswith('counted as real'))
        self.assertEqual(out['tranche_ends_after_years'], 3)

    def test_an_evidenced_schedule_replaces_the_assumption(self):
        c = case('spcx_2026q3')         # SYNTHETIC: a front-loaded schedule whose first year matches the quarter
        c['acquisition_sbc'].update(schedule=[4.88e9, 9.26e9, 9.26e9], schedule_year=1)
        out = econ.acquisition_sbc(c)
        self.assertEqual((out['schedule']['basis'], out['vs_schedule']['status'], out['flags']), ('evidenced', 'consistent', []))
        self.assertNotIn('readings', out)
        c['acquisition_sbc'].pop('schedule_year')
        self.assertNotIn('vs_schedule', econ.acquisition_sbc(c))          # which vesting year is asked, never assumed


class SegmentTests(unittest.TestCase):
    """Design WP5 'SPCX segments'."""

    def test_spcx_superintelligence_unexplained_and_segment_measures(self):
        out = econ.segments(case('spcx_2026q3'), MODELS['spcx_2026q3'])
        rows = {r['name']: r for r in out['segments']}
        si = rows['SuperIntelligence']
        self.assertEqual(round(si['share_of_revenue']['value'] * 100, 1), 51.9)       # of the segments; 51.8% of reported
        self.assertEqual(round(si['share_of_reported_revenue']['value'], 4), 0.5178)
        self.assertEqual((si['material'], si['explained'], si['not_disclosed']),
                         (True, False, ['price_per_gw_year', 'tenor', 'customer_credit']))
        self.assertEqual(si['identity_missing'], {'revenue_identity': ['utilization', 'price_per_gw_year']})
        [f] = out['flags']
        self.assertEqual((f['key'], f['severity'], f['segment'], f['scope']), ('segment_unexplained', 'high', 'SuperIntelligence', 'superintelligence'))
        self.assertLessEqual({'price_per_gw_year', 'tenor', 'customer_credit'}, set(f['missing']))
        star = rows['Starlink']               # partial with the residual named: read, not unexplained
        self.assertEqual((star['explained'], star['explained_by']), (True, ['subscriber_network.revenue_identity (partial)']))
        self.assertEqual(star['ebitda_minus_capex']['value'], 1.96e9)                # 3.5 - 1.54
        self.assertEqual(round(rows['Space']['rnd_to_revenue']['value'] * 100), 69)    # 1.23 / 1.77
        self.assertFalse(rows['Space']['material'])

    def test_spcx_deferred_revenue_build_is_customer_prepayment(self):
        pre = econ.vendor_financing(case('spcx_2026q3'))['statement_checks']['customer_prepayment_build']
        self.assertEqual((pre['build']['value'], round(pre['growth']['value'] * 100, 1), pre['status']), (5.4e9, 44.6, 'flag'))
        self.assertTrue(pre['comparison'].startswith('not_computed: window_mismatch'))
        r = run('spcx_2026q3')
        got = {(f['key'], f.get('overlay')) for f in r['flags']}
        self.assertLessEqual({('segment_unexplained', 'segments'), ('tranche_schedule_mismatch', 'acquisition_sbc'),
                              ('customer_prepayment_build', 'vendor_financing')}, got)
        self.assertEqual(r['overlays']['vendor_financing']['level']['value'], 'not_assessed')   # no ledger, gains unknown

    def test_segments_without_a_model_read_under_the_selected_one(self):
        out = econ.segments(case('uber_2025q3'), 'marketplace')
        rows = {r['name']: r for r in out['segments']}
        self.assertTrue(rows['Mobility']['explained'])        # take rate from Mobility's own gross bookings
        self.assertEqual([f['segment'] for f in out['flags']], ['Delivery'])       # no bookings for Delivery
        self.assertEqual(rows['Delivery']['not_disclosed'], ['gross_bookings'])
        self.assertIsNone(econ.segments(case('uber_2025q3'))['segments'][0]['explained'])   # no model: not judged


class BankTests(unittest.TestCase):
    """Design WP5 'SOFI capital', 'SOFI funding', 'SOFI credit'."""

    @classmethod
    def setUpClass(cls):
        cls.bc = econ.bank_capital(case('sofi_2026q2'))

    def test_sofi_excess_capital_at_both_ends_and_the_capital_lever(self):
        bc = self.bc
        self.assertAlmostEqual(bc['tangible_common_equity']['value'], 9.60072e9, delta=1)      # 7.34 x 1.308bn
        self.assertAlmostEqual(bc['net_income_annual']['value'], 0.8160612e9, delta=1)        # 8.5% x TCE
        rng = bc['excess_capital_range']
        self.assertEqual((round(rng['min'] / 1e9, 4), round(rng['max'] / 1e9, 4), rng['midpoint']), (1.8506, 3.3116, 'never used'))
        self.assertEqual([r['target_ratio'] for r in bc['excess_capital']], [0.15, 0.12])
        midpoint = (rng['min'] + rng['max']) / 2
        self.assertFalse([v for v in numbers(bc) if abs(v - midpoint) < 1])                 # never the answer
        b = bc['rote_if_all_excess_returned']
        self.assertEqual((round(b['min'] * 100, 1), round(b['max'] * 100, 1)), (10.5, 13.0))
        self.assertIn('upper bound', b['label'])
        pir = bc['price_implied_rote']
        self.assertEqual((round(pir['value'], 4), pir['price_to_tangible_book'], pir['cost_of_equity'], pir['growth']), (0.169, 2.15, 0.1, 0.04))
        self.assertEqual(round(pir['at_market_price']['value'], 4), 0.1667)              # 15.5 / 7.34 = 2.11, beside
        lever = bc['capital_lever']
        self.assertEqual((lever['insufficient'], round(lever['gap']['min'] * 100, 1), round(lever['gap']['max'] * 100, 1)), (True, 3.9, 6.4))
        self.assertEqual(round(bc['headroom_to_minimum']['value'] * 100, 1), 8.3)
        self.assertEqual(flag_keys(bc), ['nco_basis', 'capital_lever_insufficient'])
        self.assertEqual({f['key'] for f in bc['quarter_flags']}, {'bank_price_requires_higher_rote', 'equity_from_marks'})
        r = run('sofi_2026q2')                      # quarter's own flags are shown, never merged into econ flags
        self.assertFalse({'bank_price_requires_higher_rote', 'equity_from_marks'} & set(flag_keys(r)))

    def test_cost_of_equity_and_growth_are_never_defaulted(self):
        c = case('sofi_2026q2')
        c['bank'].pop('cost_of_equity')
        bc = econ.bank_capital(c)
        self.assertEqual(bc['price_implied_rote']['status'], 'not_computed')
        self.assertNotIn('capital_lever', bc)
        self.assertNotIn('capital_lever_insufficient', flag_keys(bc))
        self.assertTrue(any('explicit inputs' in q['question'] for q in bc['questions']))

    def test_sofi_funding_benefit_is_consistent(self):
        res = econ.evaluate_identity(case('sofi_2026q2'), 'digital_bank', 'deposit_benefit_identity')
        lc = res['line_check']
        self.assertEqual((res['status'], lc['computed'], lc['line_value'], round(lc['diff'] * 100, 2)),
                         ('consistent', 709.8e6, 712e6, -0.31))                             # 45.5bn x 156bp vs the post's 712m
        nr = {x['ref'] for x in run('sofi_2026q2')['not_read']}
        self.assertLessEqual({'funding.deposits', 'funding.spread_vs_alternative'}, nr)    # read once, from kpis[]

    def test_sofi_nco_basis_and_a_supplied_normalized_rate(self):
        cr = self.bc['credit']
        self.assertEqual((cr['status'], cr['nco_used'], cr['nco_headline']), ('headline_only', None, 0.0262))
        self.assertEqual([f['severity'] for f in self.bc['flags'] if f['key'] == 'nco_basis'], ['high'])
        self.assertTrue(any(q['about'] == 'bank_capital.nco_basis' for q in self.bc['questions']))
        c = case('sofi_2026q2')
        c['credit']['nco_normalized'] = c['credit']['variants']['normalized_supplied']['nco_normalized']
        bc = econ.bank_capital(c)
        self.assertEqual((bc['credit']['nco_used'], bc['credit']['status']), (0.037, 'normalized'))
        self.assertIn('normalized (analyst estimate)', bc['credit']['label'])
        self.assertNotIn('nco_basis', flag_keys(bc))


class DemandTagsTests(unittest.TestCase):
    def test_six_tags_with_concepts_from_a_synthetic_payload(self):
        # SYNTHETIC inline XBRL: two fiscal years, consolidated and one dimensional guarantee fact
        g = 'us-gaap:GuaranteeTypeAxis'
        c = [ctx('D25', '2025-01-01', '2025-12-31'), ctx('D24', '2024-01-01', '2024-12-31'), ctx('Q25', '2025-10-01', '2025-12-31'),
             ctx('I25', instant='2025-12-31'), ctx('I24', instant='2024-12-31'), ctx('G25', instant='2025-12-31', dims={g: 'syn:LeaseMember'})]
        f = [fact('us-gaap:GuaranteeObligationsMaximumExposure', 'I25', '1,200'), fact('us-gaap:GuaranteeObligationsMaximumExposure', 'G25', 800),
             fact('us-gaap:UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount', 'I25', '3,400'),
             fact('us-gaap:RevenueFromRelatedParties', 'D25', 150), fact('us-gaap:RevenueFromRelatedParties', 'D24', 120),
             fact('us-gaap:RevenueFromRelatedParties', 'Q25', 40),
             fact('us-gaap:ContractWithCustomerLiability', 'I25', 900), fact('us-gaap:ContractWithCustomerLiability', 'I24', 700),
             fact('us-gaap:ResearchAndDevelopmentExpense', 'D25', '2,500'), fact('us-gaap:AdvertisingExpense', 'D25', 640)]
        html = '<html><body><ix:header><ix:resources>' + ''.join(c) + '</ix:resources></ix:header>' + ''.join(f) + '</body></html>'
        out = B.demand_tags(B.parse_ixbrl(html))
        got = {k: (t['concept'], t['latest']['value'], t['latest']['date']) for k, t in out['tags'].items()}
        self.assertEqual(got, {
            'guarantee_maximum_exposure': ('us-gaap:GuaranteeObligationsMaximumExposure', 1200e6, '2025-12-31'),
            'purchase_obligations': ('us-gaap:UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount', 3400e6, '2025-12-31'),
            'related_party_revenue': ('us-gaap:RevenueFromRelatedParties', 150e6, '2025-12-31'),
            'deferred_revenue': ('us-gaap:ContractWithCustomerLiability', 900e6, '2025-12-31'),
            'research_and_development': ('us-gaap:ResearchAndDevelopmentExpense', 2500e6, '2025-12-31'),
            'advertising': ('us-gaap:AdvertisingExpense', 640e6, '2025-12-31')})
        self.assertEqual(out['tags']['guarantee_maximum_exposure']['dimensional_facts'], 1)    # counted, never summed
        self.assertEqual(out['tags']['related_party_revenue']['by_date'], {'2024-12-31': 120e6, '2025-12-31': 150e6})   # the quarter is not a year
        self.assertEqual(out['tags']['deferred_revenue']['by_date'], {'2024-12-31': 700e6, '2025-12-31': 900e6})
        self.assertEqual(out['not_tagged'], [])
        empty = B.demand_tags(B.parse_ixbrl('<html></html>'))
        self.assertEqual((set(empty['tags'].values()), len(empty['not_tagged'])), ({None}, 6))

    def test_tags_are_the_registry_lines_and_nothing_calls_them_yet(self):
        lines = econ.registry()['lines']
        for key, concept, _ in B.DEMAND_TAGS:
            self.assertEqual(lines[key]['xbrl'], [concept.split(':', 1)[1]], key)
        import fundamental_engine
        pkg = Path(fundamental_engine.__file__).parent
        callers = sorted(p.name for p in pkg.glob('*.py') if p.name != 'business.py' and 'demand_tags(' in p.read_text(encoding='utf-8'))
        self.assertEqual(callers, [])                     # the business map calls it from WP9


class ReviewIntegrationTests(unittest.TestCase):
    def test_overlays_run_only_when_supplied_and_applicable(self):
        self.assertEqual(run('ibkr_2026q3')['overlays'], {})
        self.assertEqual(list(run('nvda_2026q2')['overlays']), ['vendor_financing'])
        self.assertEqual(list(run('spcx_2026q3')['overlays']), ['vendor_financing', 'acquisition_sbc', 'segments'])
        c = case('skhy_2026q2')         # SYNTHETIC: a bank block on a memory maker: not applicable, named
        c['bank'] = deepcopy(case('sofi_2026q2')['bank'])
        c['bank']['source_ids'] = ['analyst_post_skhy_2026q2']
        r = econ.review(c, 'memory_cycle')
        self.assertNotIn('bank_capital', r['overlays'])
        self.assertIn('bank_capital overlay, which applies to digital_bank, broker', next(x['reason'] for x in r['not_read'] if x['ref'] == 'bank'))
        nr = run('nvda_2026q2')['checks_not_run']
        self.assertNotIn('funding_level', [x['check'] for x in nr])
        self.assertEqual(next(x['reason'] for x in nr if x['check'] == 'segment_unexplained'),
                         'overlay segments: its inputs are not supplied in the case')
        self.assertEqual(econ.review(case('ibkr_2026q3'), None)['overlays'], {})             # not assessed: nothing runs

    def test_review_overlay_equals_the_public_function(self):
        for name, oid, fn in (('nvda_2026q2', 'vendor_financing', econ.vendor_financing), ('sofi_2026q2', 'bank_capital', econ.bank_capital),
                              ('spcx_2026q3', 'acquisition_sbc', econ.acquisition_sbc), ('skhy_2026q2', 'contract_coverage', econ.contract_coverage)):
            r, alone = run(name), fn(case(name))
            self.assertEqual(r['overlays'][oid], {k: v for k, v in alone.items() if k not in WRAPPERS}, name)
            self.assertEqual(sorted(json.dumps(f, sort_keys=True) for f in r['flags'] if f.get('overlay') == oid),
                             sorted(json.dumps(f, sort_keys=True) for f in alone['flags']), name)
        seg = econ.segments(case('spcx_2026q3'), MODELS['spcx_2026q3'])
        self.assertEqual(run('spcx_2026q3')['overlays']['segments'], {k: v for k, v in seg.items() if k not in WRAPPERS})

    def test_point_in_time_for_links_blocks_and_estimates(self):
        c = case('nvda_2026q2')         # SYNTHETIC: a source dated after the case
        c['sources']['late'] = {'kind': 'news', 'available_at': '2026-10-22'}
        c['funding_links'][0]['source_ids'] = ['late']
        c['exposed_share']['source_ids'] = ['late']
        vf = econ.vendor_financing(c)
        self.assertEqual((vf['ledger']['funded']['amount'], 'exposed_share' in vf), (0, False))
        self.assertLessEqual({'funding_links[fl1]', 'exposed_share'}, set(vf['unavailable']))
        c = case('sofi_2026q2')
        c['sources']['late'] = {'kind': 'issuer_filing', 'available_at': '2026-07-31'}
        c['bank']['source_ids'] = ['late']
        bc = econ.bank_capital(c)
        self.assertEqual((bc['capital'], 'bank' in bc['unavailable']), ({'status': 'unavailable'}, True))

    def test_determinism_hash_and_no_mutation(self):
        calls = ((econ.vendor_financing, 'nvda_2026q2', {}), (econ.bank_capital, 'sofi_2026q2', {}), (econ.acquisition_sbc, 'spcx_2026q3', {}),
                 (econ.contract_coverage, 'skhy_2026q2', {}), (econ.segments, 'spcx_2026q3', {'model': MODELS['spcx_2026q3']}),
                 (econ.chain, 'skhy_2026q2', {'upstream': run('nvda_2026q2')}))
        for fn, name, kw in calls:
            c = case(name)
            before = deepcopy(c)
            a, b = fn(c, **kw), fn(case(name), **kw)
            self.assertEqual(c, before, name)
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True), name)
            self.assertEqual(a['input_sha256'], F.digest(c), name)
            json.dumps(a, allow_nan=False)

    def test_anti_invention_on_every_overlay(self):
        nv = run('nvda_2026q2')
        sk = case('skhy_2026q2')
        sk['chain']['share_of_revenue_via_chain'] = {'value': 0.3, 'source_ids': ['test_assumption_chain_share']}
        so = case('sofi_2026q2')
        so['credit']['nco_normalized'] = so['credit']['variants']['normalized_supplied']['nco_normalized']
        outs = [(econ.vendor_financing(case('nvda_2026q2')), case('nvda_2026q2')), (econ.bank_capital(so), so),
                (econ.acquisition_sbc(case('spcx_2026q3')), case('spcx_2026q3')),
                (econ.segments(case('spcx_2026q3'), MODELS['spcx_2026q3']), case('spcx_2026q3')),
                (econ.chain(sk, nv), sk), (econ.review(sk, 'memory_cycle', upstream=nv), sk),
                (econ.vendor_financing(synthetic_vf()), synthetic_vf())]
        for out, c in outs:
            self.assertEqual(unsourced(out, c), [], out.get('overlay') or 'review')
        real = econ._oflag

        def planted(ix, key, overlay_id, scope='total', **data):           # a constant planted inside an overlay
            return {**real(ix, key, overlay_id, scope, **data), 'haircut': 0.1234}
        with mock.patch.object(econ, '_oflag', planted):
            bad = unsourced(econ.bank_capital(case('sofi_2026q2')), case('sofi_2026q2'))
        self.assertEqual(sorted({p.split('.')[-1] for p, _ in bad}), ['haircut'])

    def test_no_input_invariance_nothing_in_the_engine_calls_the_overlays(self):
        import fundamental_engine
        pkg = Path(fundamental_engine.__file__).parent
        names = ('econ.vendor_financing', 'econ.chain(', 'econ.acquisition_sbc', 'econ.bank_capital', 'econ.contract_coverage',
                 'econ.segments(')
        callers = sorted(p.name for p in pkg.glob('*.py') if p.name != 'econ.py' and any(n in p.read_text(encoding='utf-8') for n in names))
        self.assertEqual(callers, [])


if __name__ == '__main__':
    unittest.main()
