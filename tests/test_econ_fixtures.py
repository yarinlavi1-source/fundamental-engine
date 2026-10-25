"""WP0 of the econ layer: the fixtures under tests/fixtures/econ and docs/prereg_econ.md.

No econ engine code exists yet, so these tests pin the fixtures themselves: every file loads, every number carries a source
with a point-in-time date not after the case date, no post text or author is stored, the `expected` blocks are what the
design's arithmetic gives from the fixture inputs (recomputed here, pure arithmetic), the two filed fixtures run through the
existing quarter_review unchanged, the held-out manifest holds names and no numbers, and the preregistration freezes every
threshold and names every control before any phase-C code.
"""
import json
import re
import unittest
from datetime import date
from math import log
from pathlib import Path

from fundamental_engine.quarter import quarter_review

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / 'tests' / 'fixtures' / 'econ'
PREREG = ROOT / 'docs' / 'prereg_econ.md'
COMPANY = ('uber_2025q3', 'uber_2026q2', 'ibkr_2026q2', 'ibkr_2026q3', 'pltr_2026q2', 'rddt_2026q2', 'sofi_2026q2',
           'sn_2025', 'skhy_2026q2', 'nvda_2026q2', 'spcx_2026q3')
FILED = ('pltr_2026q2_filed', 'rddt_2026q2_filed')
KINDS = {'analyst_post', 'issuer_release', 'issuer_filing', 'issuer_transcript', 'data_provider', 'market_data',
         'comparison_journal', 'test_assumption', 'design_record', 'prior_review', 'upstream_review'}
FORBIDDEN_KEYS = {'author', 'text', 'post_text', 'url', 'body', 'excerpt'}     # fx.quote is the repo's currency-quote key
RULES = {'identity_tolerance': '0.02', 'growth_identity_pp': '0.01', 'partial_below': '0.90', 'firm_exposure_high': '0.25',
         'cfo_to_ni_min': '0.80', 'near_threshold': '0.03', 'receivables_gap_pp': '0.10', 'minor_line_share': '0.10',
         'flat_units_abs': '0.02', 'segment_material': '0.25', 'leverage_share_min': '0.667', 'econ_view_min_pp': '0.02'}
HELD_OUT_NAMES = ('SCHW', 'HOOD', 'DASH', 'SNOW', 'DDOG', 'MU', 'ADBE', 'Lucent', 'Nortel', 'Microsoft', 'Apple', 'CoreWeave',
                  'Oracle', 'iRobot')


def load(name):
    return json.loads((DIR / f'{name}.json').read_text(encoding='utf-8'))


def walk(node, path=''):
    """Yields (path, key, value) for every key in nested dicts/lists."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k, v
            yield from walk(v, f'{path}.{k}')
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f'{path}[{i}]')


def referenced_sources(doc):
    out = set()
    for _, k, v in walk(doc):
        if k in ('source_ids', 'change_source_ids'):
            out.update(v)
    return out


class FixtureFilesTests(unittest.TestCase):
    def test_every_listed_fixture_exists_loads_and_nothing_else_is_there(self):
        present = {p.stem for p in DIR.glob('*.json')}
        self.assertEqual(present, set(COMPANY) | set(FILED) | {'held_out'})
        for name in present:
            doc = load(name)
            self.assertIsInstance(doc, dict, name)
            self.assertTrue(doc.get('_provenance'), name)

    def test_company_fixture_contract_and_point_in_time_sources(self):
        for name in COMPANY:
            d = load(name)
            self.assertEqual(d['_schema'], 'econ_fixture_v1', name)
            for k in ('company', 'name', 'period_end', 'period', 'as_of', 'currency', 'sources', 'expected'):
                self.assertIn(k, d, f'{name}.{k}')
            self.assertEqual(d['currency'], 'USD', name)
            self.assertIs(d['reconciled_to_filings'], False, name)
            self.assertIn('not truth', d['_provenance'], name)
            as_of = date.fromisoformat(d['as_of'])
            self.assertLessEqual(date.fromisoformat(d['period_end']), as_of, name)
            self.assertTrue(d['sources'], name)
            for sid, s in d['sources'].items():
                self.assertIn(s['kind'], KINDS, f'{name}:{sid}')
                self.assertLessEqual(date.fromisoformat(s['available_at']), as_of, f'{name}:{sid} available after as_of')
            used = referenced_sources(d)
            self.assertEqual(used - set(d['sources']), set(), f'{name}: unknown source ids')
            self.assertEqual(set(d['sources']) - used, set(), f'{name}: unreferenced sources')
            # every data block (not the derived `expected`) carries source ids somewhere inside it
            for block in ('lines', 'kpis', 'segments', 'funding_links', 'categories', 'bank', 'credit', 'revisions'):
                if block in d:
                    self.assertTrue(referenced_sources(d[block]), f'{name}.{block} without source_ids')

    def test_no_post_text_or_author_in_any_fixture(self):
        for name in COMPANY + FILED + ('held_out',):
            for path, k, v in walk(load(name)):
                self.assertNotIn(k, FORBIDDEN_KEYS, f'{name}{path}.{k}')
                if isinstance(v, str):
                    self.assertNotIn('http', v.lower(), f'{name}{path}.{k}')
                    if k != '_provenance':
                        self.assertLess(len(v), 400, f'{name}{path}.{k} reads like prose')

    def test_filed_fixtures_hold_filing_sources_only(self):
        for name in FILED:
            d = load(name)
            self.assertIn('Filing numbers only', d['_provenance'])
            for sid, s in d['sources'].items():
                self.assertIn(s['kind'], {'issuer_release', 'issuer_filing', 'data_provider'}, f'{name}:{sid}')
            keys = {k for _, k, _ in walk(d)}
            self.assertEqual(keys & {'market', 'one_offs', 'multiple_scenarios', 'falsifiers', 'consensus', 'consensus_source_ids', 'concentration'}, set(), name)
            self.assertEqual(referenced_sources(d) - set(d['sources']), set(), name)


class FiledFixtureQuarterReviewTests(unittest.TestCase):
    """The filed fixtures are valid quarter_review cases today; the WP7 numbers are recomputed from their lines."""

    def _leverage(self, d):
        c, p = d['current'], d['prior_year']
        om, om_p = c['operating_income'] / c['revenue'], p['operating_income'] / p['revenue']
        gm, gm_p = c['gross_profit'] / c['revenue'], p['gross_profit'] / p['revenue']
        ox, ox_p = c['operating_expenses'] / c['revenue'], p['operating_expenses'] / p['revenue']
        self.assertAlmostEqual((om - om_p) - ((gm - gm_p) - (ox - ox_p)), 0, places=12)   # OM = GM - opex/revenue exactly
        return om, om - om_p, gm - gm_p, ox - ox_p, -(ox - ox_p) / (om - om_p)

    def test_pltr_filed_runs_and_pins_wp7_arithmetic(self):
        d = load('pltr_2026q2_filed')
        r = quarter_review(d)
        om, d_om, d_gm, d_ox, share = self._leverage(d)
        self.assertAlmostEqual(om, .471, places=3)
        self.assertAlmostEqual(r['cycle']['operating_margin'], om, places=9)
        self.assertAlmostEqual(r['cycle']['historical_max'], .3159)
        self.assertTrue(r['cycle']['above_history'])
        self.assertAlmostEqual(d_om, .203, places=3)
        self.assertAlmostEqual(d_gm, .039, places=3)
        self.assertAlmostEqual(d_ox, -.164, places=3)
        self.assertAlmostEqual(share, .809, places=3)
        self.assertGreaterEqual(share, 2 / 3)

    def test_rddt_filed_runs_and_pins_wp7_arithmetic(self):
        d = load('rddt_2026q2_filed')
        r = quarter_review(d)
        om, d_om, d_gm, d_ox, share = self._leverage(d)
        self.assertAlmostEqual(om, .288, places=3)
        self.assertAlmostEqual(r['cycle']['historical_median'], .136)
        self.assertAlmostEqual(om - (.136 + .15), .002, places=3)      # fires by 0.2pp; the margin is shown
        self.assertTrue(r['cycle']['above_history'])
        self.assertAlmostEqual(d_om, .152, places=3)
        self.assertAlmostEqual(d_gm, .005, places=3)
        self.assertAlmostEqual(d_ox, -.148, places=3)
        self.assertAlmostEqual(share, .970, places=3)
        other = next(s for s in d['segments'] if s['name'].startswith('Other'))
        self.assertAlmostEqual((other['revenue'] - other['prior_revenue']) / (d['current']['revenue'] - d['prior_year']['revenue']), .0276, places=4)


class ExpectedBlocksDerivedFromInputsTests(unittest.TestCase):
    """The `expected` values are the design's arithmetic applied to the fixture inputs, not transcribed constants."""

    def kpi(self, d, kid, **match):
        rows = [k for k in d['kpis'] if k['id'] == kid and all(k.get(a) == b for a, b in match.items())]
        self.assertEqual(len(rows), 1, (kid, match))
        return rows[0]

    def test_uber_trips_identity_and_ebitda_per_gb(self):
        d = load('uber_2025q3')
        mapc, trips, freq = self.kpi(d, 'mapcs'), self.kpi(d, 'trips'), self.kpi(d, 'frequency')
        units = (1 + mapc['growth']) * (1 + freq['growth'])
        reported = trips['value'] / trips['prior']
        e = d['expected']['trips_identity']
        self.assertAlmostEqual(e['units_factor'], units, places=4)
        self.assertAlmostEqual(e['reported_factor'], reported, places=4)
        self.assertAlmostEqual(e['residual_pp'], (reported - units) * 100, places=2)
        self.assertLess(abs(reported - units), .01)                                   # growth_identity_pp
        self.assertEqual(e['status'], 'consistent')
        mob = next(s for s in d['segments'] if s['name'] == 'Mobility')
        self.assertAlmostEqual(d['expected']['mobility_ebitda_per_gb']['value'], mob['profit'] / mob['gross_bookings'], places=5)
        self.assertEqual(d['_reconciliation']['trips_prior'], 2.868e9)

    def test_uber_basis_switch(self):
        d = load('uber_2026q2')
        gb = self.kpi(d, 'gross_bookings')
        self.assertEqual(gb['currency_basis'], 'constant_currency')
        self.assertEqual(d['expected']['base']['take_rate_identity_status'], 'basis_mismatch')
        self.assertIsNone(d['expected']['base']['take_rate_change'])
        v = d['variants']['reported_basis']['kpis'][0]
        self.assertEqual(d['sources'][v['source_ids'][0]]['kind'], 'test_assumption')
        self.assertAlmostEqual(d['expected']['reported_basis']['take_rate_change'], (1 + d['lines']['revenue']['growth']) / (1 + v['growth']) - 1, places=5)
        self.assertIsNone(d['reclassification_evidence'])

    def test_ibkr_split_margin_interest_lines_and_deceleration(self):
        d = load('ibkr_2026q3')
        rev, acc, eq = d['lines']['revenue'], self.kpi(d, 'accounts'), self.kpi(d, 'client_equity')
        e = d['expected']
        rpa = rev['value'] * 4 / acc['value']
        rpa_p = (rev['value'] / (1 + rev['growth'])) * 4 / (acc['value'] / (1 + acc['growth']))
        self.assertAlmostEqual(e['revenue_per_account']['value'], rpa, places=1)
        self.assertAlmostEqual(e['revenue_per_account']['prior'], rpa_p, places=1)
        self.assertAlmostEqual(e['revenue_per_account']['change'], rpa / rpa_p - 1, places=5)
        self.assertAlmostEqual(e['equity_per_account']['change'], (1 + eq['growth']) / (1 + acc['growth']) - 1, places=5)
        self.assertAlmostEqual(e['revenue_yield_on_equity']['change'], (1 + rev['growth']) / (1 + eq['growth']) - 1, places=5)
        f = e['growth_split']['factors']
        self.assertAlmostEqual(f['accounts'] * f['equity_per_account'] * f['revenue_yield_on_equity'], 1 + rev['growth'], places=4)
        self.assertEqual(e['growth_split']['residual'], 0.0)
        avg, rate = self.kpi(d, 'margin_loans', kpi_basis='average'), self.kpi(d, 'margin_rate')
        prod = (avg['value'] / avg['prior']) * (rate['value'] / rate['prior'])
        self.assertAlmostEqual(e['margin_interest_identity']['product'], prod, places=4)
        self.assertLess(abs(prod - (1 + d['lines']['margin_interest_income']['growth'])), .01)
        com = d['lines']['commissions']
        self.assertAlmostEqual(e['commissions_ex_pass_through_growth']['value'],
                               (com['value'] - com['pass_through']) / (com['value'] / (1 + com['growth']) - com['prior_pass_through']) - 1, places=5)
        s = d['sensitivities'][0]
        self.assertAlmostEqual(e['rate_sensitivity_share']['value'], -s['annual_impact'] / s['base_annual'], places=5)
        prior = d['prior_review_growth']
        self.assertEqual({k: v for k, v in prior.items() if k != 'source_ids'}, load('ibkr_2026q2')['expected']['growth_snapshot_for_deceleration'])
        now = {k['id']: k['growth'] for k in d['kpis'] if k['id'] in ('darts', 'client_equity', 'accounts') or (k['id'] == 'margin_loans' and k['kpi_basis'] == 'balance')}
        self.assertTrue(all(prior[k] - now[k] > .10 for k in ('darts', 'margin_loans', 'client_equity')))
        self.assertLessEqual(abs(now['accounts'] - prior['accounts']), .03)
        he = e['render_he']['how_it_earns']
        for token in ('1,340', '35%', 'מבני', 'מחזורי'):
            self.assertIn(token, he)
        self.assertLessEqual(len(he), 220)

    def test_pltr_expansion_and_top_n(self):
        d = load('pltr_2026q2')
        cust = self.kpi(d, 'customers')
        g = (1 + d['lines']['us_commercial_revenue']['growth_qoq']) / (1 + cust['growth']) - 1
        e = d['expected']['expansion']
        self.assertAlmostEqual(e['revenue_per_customer_growth'], g, places=5)
        self.assertAlmostEqual(e['per_unit_log_share'], log(1 + g) / log(1 + d['lines']['us_commercial_revenue']['growth_qoq']), places=3)
        ndr = self.kpi(d, 'net_revenue_retention')
        self.assertGreater(ndr['value'], 1.10)
        top = self.kpi(d, 'top_n_average_revenue')
        self.assertAlmostEqual(d['expected']['top_n']['average_growth'], top['value'] / top['prior'] - 1, places=4)
        self.assertEqual(d['expected']['top_n']['total'], top['n'] * top['value'])
        self.assertAlmostEqual(d['expected']['top_n']['implied_ttm_revenue_at_share'], top['n'] * top['value'] / self.kpi(d, 'top_n_share')['value'], places=0)
        self.assertEqual(cust['window'], 'q/q');  self.assertEqual(top['window'], 'TTM')      # never compared with each other

    def test_rddt_minor_line_flat_units_and_price_vs_estimates(self):
        d = load('rddt_2026q2')
        oth, tot = d['lines']['other_revenue'], d['lines']['revenue']
        now = oth['share_of_revenue'] * (1 + tot['growth'])
        share = (now - now / (1 + oth['growth'])) / tot['growth']
        self.assertAlmostEqual(d['expected']['thesis_on_minor_line']['other_share_of_incremental_revenue'], share, places=4)
        self.assertLess(oth['share_of_revenue'], .10)
        us = self.kpi(d, 'dau', scope='US')
        self.assertAlmostEqual(d['expected']['core_units_flat']['us_dau_change'], us['value'] / us['prior_quarter'] - 1, places=5)
        self.assertLess(abs(us['value'] / us['prior_quarter'] - 1), .02)
        r, p = d['revisions'], d['prices']
        self.assertEqual((r['metric'], r['target_period'], r['basis'], r['currency']), ('eps', 'FY2027', 'consensus_mean', 'USD'))
        self.assertLessEqual(date.fromisoformat(r['to']['as_of']), date.fromisoformat(d['as_of']))
        est = r['to']['value'] / r['from']['value'] - 1
        self.assertAlmostEqual(d['expected']['price_vs_estimates']['estimate_change'], est, places=5)
        self.assertAlmostEqual(d['expected']['price_vs_estimates']['implied_forward_multiple_change'], (1 + p['change']) / (1 + est) - 1, places=4)
        self.assertEqual(d['sources']['design_record_rddt_revisions']['kind'], 'design_record')   # provenance gap stays visible

    def test_sofi_capital_lever_bounds_and_deposit_benefit(self):
        d = load('sofi_2026q2')
        b, e = d['bank'], d['expected']['bank_capital']
        lo, hi = ((b['capital_ratio'] - t) * b['risk_weighted_assets'] for t in reversed(b['target_ratio_range']))
        self.assertEqual(e['excess_capital_range'], [round(lo), round(hi)])
        tce = b['tangible_book_per_share'] * b['diluted_shares']
        ni = b['rote'] * tce
        self.assertEqual(e['rote_if_all_excess_returned'], [round(ni / (tce - lo), 4), round(ni / (tce - hi), 4)])
        implied = b['growth'] + b['price_to_tangible_book'] * (b['cost_of_equity'] - b['growth'])
        self.assertAlmostEqual(e['price_implied_rote'], implied, places=4)
        self.assertGreater(implied, ni / (tce - hi))                   # capital_lever_insufficient even at the upper bound
        self.assertAlmostEqual(e['headroom_to_minimum_pp'], (b['capital_ratio'] - b['minimum_ratio']) * 100, places=1)
        f = d['funding']
        self.assertAlmostEqual(d['expected']['deposit_benefit']['computed'], f['deposits'] * f['spread_vs_alternative'], places=0)
        self.assertLess(abs(f['deposits'] * f['spread_vs_alternative'] / f['post_annual_benefit'] - 1), .02)
        self.assertIsNone(d['credit']['nco_normalized'])
        self.assertEqual(d['credit']['variants']['normalized_supplied']['nco_normalized']['kind'], 'analyst_estimate')
        self.assertEqual(self.kpi(d, 'products')['kpi_basis'], 'cumulative')

    def test_sn_process_evidence(self):
        d = load('sn_2025')
        c = next(x for x in d['categories'] if x['name'] == 'Cleaning')['revenue']
        e = d['expected']['process']
        self.assertAlmostEqual(e['cleaning_drawdown'], c['2023'] / c['2021'] - 1, places=4)
        self.assertAlmostEqual(e['new_high_over_prior_peak'], c['2025'] / c['2021'] - 1, places=4)
        self.assertLessEqual(c['2023'] / c['2021'] - 1, -.05)
        self.assertLessEqual(2025 - 2021, 4)
        h = d['history']
        i22, i25 = h['research_and_development']['2022'] / h['revenue']['2022'], h['research_and_development']['2025'] / h['revenue']['2025']
        self.assertAlmostEqual(e['rnd_intensity']['2022'], i22, places=5)
        self.assertAlmostEqual(e['rnd_intensity']['2025'], i25, places=5)
        self.assertLessEqual(abs(i25 - i22), .01)
        self.assertAlmostEqual(e['revenue_cagr'], (h['revenue']['2025'] / h['revenue']['2022']) ** (1 / 3) - 1, places=4)
        self.assertGreaterEqual(e['revenue_cagr'], .10)
        self.assertAlmostEqual(e['incremental_revenue_per_incremental_rnd'],
                               (h['revenue']['2025'] - h['revenue']['2022']) / (h['research_and_development']['2025'] - h['research_and_development']['2022']), places=2)
        self.assertEqual(e['machine'], 'working')
        self.assertEqual(e['gross_margin_change_pp'], round((h['gross_margin']['end'] - h['gross_margin']['start']) * 100))
        self.assertNotIn('2022', c); self.assertNotIn('2024', c)         # missing years stay missing

    def test_skhy_normalized_pe_contracts_and_chain(self):
        d = load('skhy_2026q2')
        m, cy = d['market'], d['cycle']
        pe = m['price'] / m['run_rate_eps']
        self.assertAlmostEqual(d['expected']['normalized_pe']['run_rate_pe'], pe, places=3)
        self.assertAlmostEqual(d['expected']['normalized_pe']['margin_only_normalized_pe'], pe * cy['current_operating_margin'] / cy['cycle_margin']['value'], places=2)
        exact = d['lines']['operating_income']['value'] / d['lines']['revenue']['value']
        self.assertAlmostEqual(d['expected']['normalized_pe']['margin_only_normalized_pe_exact_margin'], pe * exact / cy['cycle_margin']['value'], places=3)
        self.assertAlmostEqual(cy['current_operating_margin'], exact, places=3)
        self.assertEqual(cy['cycle_margin']['kind'], 'analyst_estimate')
        self.assertIsNone(d['contracts']['share_of_next_year_volume']); self.assertIsNone(d['contracts']['price_mechanism'])
        self.assertIsNone(d['chain']['share_of_revenue_via_chain'])
        syn = d['chain']['variants']['synthetic_share']
        self.assertEqual(d['sources'][syn['source_ids'][0]]['kind'], 'test_assumption')
        self.assertAlmostEqual(d['expected']['chain']['synthetic_share']['exposed_share'], syn['share_of_revenue_via_chain'] * load('nvda_2026q2')['exposed_share']['value'], places=4)
        self.assertEqual(d['expected']['peak_flag']['severity'], 'high')

    def test_nvda_vendor_financing_ledger(self):
        d = load('nvda_2026q2')
        R = d['revenue_basis']['value'] * d['revenue_basis']['annualize']
        by = {l['id']: l for l in d['funding_links']}
        e = d['expected']['vendor_financing']
        self.assertEqual(e['annualized_revenue'], R)
        self.assertAlmostEqual(e['shares']['funded'], by['fl1']['amount'] / R, places=4)
        self.assertAlmostEqual(e['shares']['committed'], by['fl2']['amount'] / R, places=4)
        self.assertAlmostEqual(e['shares']['take_or_pay'], by['fl4']['amount'] / R, places=4)
        firm = sum(l['amount'] for l in d['funding_links'] if l['status'] in ('funded', 'committed'))
        self.assertEqual(e['firm_exposure'], firm)
        self.assertAlmostEqual(e['shares']['firm'], firm / R, places=4)
        self.assertGreaterEqual(firm / R, .25)
        self.assertTrue(by['fl3']['amount_is_maximum']); self.assertEqual(by['fl3']['status'], 'contingent')
        self.assertAlmostEqual(e['shares']['contingent_ceiling'], by['fl3']['amount'] / R, places=4)
        h = d['half_year']
        self.assertAlmostEqual(e['statement_checks']['cfo_to_ni'], h['operating_cash_flow'] / h['net_income'], places=4)
        ex = h['operating_cash_flow'] / (h['net_income'] - h['non_cash_investment_gains'])
        self.assertAlmostEqual(e['statement_checks']['cfo_to_ni_ex_gains'], ex, places=4)
        self.assertLess(ex, .80); self.assertLessEqual(.80 - ex, .03)                      # fails, and near the threshold
        b = d['balance']
        self.assertAlmostEqual(e['statement_checks']['receivables_growth_6m'], b['receivables_end'] / b['receivables_start'] - 1, places=4)
        self.assertAlmostEqual(e['statement_checks']['receivables_build_to_ni'], h['receivables_build_cash_flow_statement'] / h['net_income'], places=4)
        self.assertEqual(e['level'], 'high')
        self.assertEqual(d['exposed_share']['kind'], 'analyst_estimate')
        t = d['requirement_test']
        self.assertEqual(d['expected']['translate_requirement']['gw_range'], [round(t['revenue'] / t['revenue_per_gw_range'][1], 2), round(t['revenue'] / t['revenue_per_gw_range'][0], 2)])
        self.assertEqual(d['sources'][t['source_ids'][0]]['kind'], 'test_assumption')

    def test_spcx_segments_starlink_sbc_and_prepayments(self):
        d = load('spcx_2026q3')
        seg = {s['name']: s for s in d['segments']}
        rev, oi = d['lines']['revenue']['value'], d['lines']['operating_income']['value']
        e = d['expected']['segments_identity']
        self.assertAlmostEqual(e['revenue_sum'], sum(s['revenue'] for s in d['segments']), places=0)
        self.assertLess(abs(e['revenue_sum'] / rev - 1), .02)
        oi_sum = seg['Space']['operating_income'] + seg['Starlink']['revenue'] * seg['Starlink']['operating_margin'] + seg['SuperIntelligence']['operating_income']
        self.assertAlmostEqual(e['operating_income_sum'], oi_sum, places=0)
        self.assertLess(abs(oi_sum / oi - 1), .02)
        subs, arpu = self.kpi(d, 'subscribers'), self.kpi(d, 'arpu')
        st = d['expected']['starlink_identity']
        self.assertAlmostEqual(st['average_basis_revenue'], (subs['value'] + subs['prior_quarter']) / 2 * arpu['value'] * 3, places=0)
        self.assertAlmostEqual(st['end_basis_revenue'], subs['value'] * arpu['value'] * 3, places=0)
        self.assertLess(st['end_basis_revenue'] / seg['Starlink']['revenue'], .90)             # partial
        self.assertEqual(st['status'], 'partial')
        self.assertAlmostEqual(st['ebitda_minus_capex'], seg['Starlink']['ebitda'] - seg['Starlink']['capex'], places=0)
        self.assertIsNone(seg['SuperIntelligence']['price_per_gw_year'])
        self.assertGreaterEqual(seg['SuperIntelligence']['revenue'] / rev, .25)
        sbc, a = d['lines']['sbc'], d['acquisition_sbc']
        x = d['expected']['acquisition_sbc']
        self.assertAlmostEqual(x['ongoing_ratio'], (sbc['value'] - sbc['cursor_tranche_in_period']) / rev, places=5)
        self.assertAlmostEqual(x['straight_line_tranche_per_year'], a['stock_to_employees'] / a['vesting_years'], places=0)
        self.assertIsNone(a['schedule']); self.assertTrue(a['straight_line_assumed'])
        self.assertAlmostEqual(x['observed_vs_schedule'], a['observed_tranche_in_period'] * 4 / (a['stock_to_employees'] / a['vesting_years']) - 1, places=4)
        self.assertGreater(abs(x['observed_vs_schedule']), .20)
        flat = d['sbc_path_test']['flat_annual_revenue']
        self.assertEqual(d['sources'][d['sbc_path_test']['source_ids'][0]]['kind'], 'test_assumption')
        self.assertEqual(d['expected']['sbc_pct_path']['values'], [round(x['ongoing_ratio'] + x['straight_line_tranche_per_year'] / flat, 4)] * 3 + [round(x['ongoing_ratio'], 4)] * 2)
        b = d['balance']
        self.assertAlmostEqual(d['expected']['customer_prepayment_build']['deferred_revenue_change'], b['deferred_revenue'] - b['deferred_revenue_start_of_year'], places=0)

    def test_hebrew_texts_present_for_the_owner_table(self):
        he = {n: load(n)['expected'].get('render_he') for n in COMPANY}
        self.assertEqual({n for n, v in he.items() if v is None}, {'uber_2026q2', 'ibkr_2026q2'})   # no table text in the design for these
        self.assertTrue(he['uber_2025q3']['how_it_earns'].startswith('יותר משתמשים (+17%)'))
        self.assertIn('ספירה מצטברת', he['sofi_2026q2']['how_it_earns'])
        self.assertIn('10.5%–13%', he['sofi_2026q2']['price_requires_units_extension'])
        self.assertIn('52% מההכנסות', he['spcx_2026q3']['how_it_earns'])
        self.assertIn('105 מיליארד$', he['nvda_2026q2']['risk_fallback'])
        self.assertIn('Nvidia', he['skhy_2026q2']['risk_fallback'])
        self.assertIn('+21%', he['pltr_2026q2']['how_it_earns'])
        self.assertIn('+13%', he['sn_2025']['how_it_earns'])
        self.assertIn('26%', he['rddt_2026q2']['estimates_vs_price'])


class PreregistrationTests(unittest.TestCase):
    def test_held_out_manifest_names_only(self):
        d = load('held_out')
        self.assertEqual(d['_schema'], 'econ_held_out_v1')
        for path, k, v in walk(d):
            self.assertFalse(isinstance(v, (int, float)) and not isinstance(v, bool), f'number in held_out{path}.{k}: {v!r}')
        blob = json.dumps(d)
        for n in HELD_OUT_NAMES:
            self.assertIn(n, blob, n)
        self.assertEqual({c['name'] for c in d['h2_vendor_financing']['positive_controls']}, {'Lucent Technologies', 'Nortel Networks'})
        self.assertGreaterEqual(len(d['h2_vendor_financing']['negative_controls']), 2)
        self.assertTrue(all(c['expected_reading'] is None for c in d['h2_vendor_financing']['live_cases_reported_only']))
        self.assertEqual(d['wp12_broker_walk_forward']['names'], ['IBKR', 'SCHW', 'HOOD'])

    def test_prereg_document_freezes_rules_names_and_metric(self):
        text = PREREG.read_text(encoding='utf-8')
        for rule, value in RULES.items():
            self.assertRegex(text, rf'\| {re.escape(rule)} \| {re.escape(value)} \|', rule)
        for n in HELD_OUT_NAMES:
            self.assertIn(n, text, n)
        for needle in ('H1', 'H2', 'H3', 'ECON_TEST_RESULTS.md', 'MALE_3', 'before 2001-01-01', 'Deviations log', 'phase-C'):
            self.assertIn(needle, text, needle)
        self.assertIn('Written 21.10.2026', text)


if __name__ == '__main__':
    unittest.main()
