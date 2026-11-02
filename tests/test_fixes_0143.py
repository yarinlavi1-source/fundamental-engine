"""0.14.3 fixes: bank revenue from companyfacts, the residual-income route's premiums and owner-facing return, pack routes for a
classification review and a revenue-by-country split, and the routing repairs of the 2026-11-02 audit. Every company below is
SYNTHETIC test data shaped like the live case it names (invented numbers, not market data):

  SOFI  ASC 606 fee revenue (0.62bn) read as total revenue (3.61bn): margins, premiums and quality read from the wrong line
  MA    a fee network with liabilities at 85.7% of assets: 'Credit Services' / 'Financials' wording and SIC 6200 sent it to
        residual income; the forecast's lender check withheld its value whatever the card had found
  TSLA  'software' in a descriptive label routed a 17%-gross-margin car maker as subscription software
  VRT   an infrastructure keyword with capex at 2% and no other type stayed infrastructure
  NU    a foreign filer valued with no country premium and no visible flag
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from fundamental_engine import decision, facts as F, forecast, pipeline  # noqa: E402
from fundamental_engine.evidence_pack import to_inputs  # noqa: E402
from test_evidence_pack import pack  # noqa: E402
from test_forecast import industrial  # noqa: E402
from test_forecast_financials import bank  # noqa: E402
from test_model_card import card, series_years, synth_fb, with_sic  # noqa: E402


def row(start, end, val, filed, form='10-K'):
    return {'start': start, 'end': end, 'val': val, 'filed': filed, 'form': form, 'fy': int(end[:4]), 'fp': 'FY'}


def payload(**tags):
    return {'cik': 1, 'facts': {'us-gaap': {t: {'units': {'USD': rows}} for t, rows in tags.items()}}}


def fy_revenue(p):
    fb = F.new({'ticker': 'SYNB', 'currency': 'USD'}, '2026-11-02')
    F.from_sec_companyfacts(fb, p, '2026-11-02T00:00:00Z')
    return {f['period']: (f['value'], f['basis'], f['concept']) for f in fb['facts'] if f['metric'] == 'revenue' and f['period'].startswith('FY')}


class BankRevenueTests(unittest.TestCase):
    """(a) SOFI: a bank holding company's ASC 606 tag is fee revenue only, never total revenue when a fuller figure exists."""

    def test_total_net_revenue_tag_wins_over_the_asc606_tag(self):
        got = fy_revenue(payload(RevenueFromContractWithCustomerExcludingAssessedTax=[row('2025-01-01', '2025-12-31', 0.62e9, '2026-02-27')],
                                 RevenuesNetOfInterestExpense=[row('2025-01-01', '2025-12-31', 3.61e9, '2026-02-27')]))
        self.assertEqual(got['FY2025'][:2], (3.61e9, 'as_reported'))

    def test_interest_plus_noninterest_income_replaces_a_partial_asc606_line(self):
        got = fy_revenue(payload(RevenueFromContractWithCustomerExcludingAssessedTax=[row('2025-01-01', '2025-12-31', 0.62e9, '2026-02-27')],
                                 InterestIncomeExpenseNet=[row('2025-01-01', '2025-12-31', 2.22e9, '2026-02-27')],
                                 NoninterestIncome=[row('2025-01-01', '2025-12-31', 1.39e9, '2026-02-27')]))
        self.assertAlmostEqual(got['FY2025'][0], 3.61e9)
        self.assertEqual(got['FY2025'][1], 'derived')
        # an operating company tags no NoninterestIncome: its ASC 606 revenue is untouched
        got = fy_revenue(payload(RevenueFromContractWithCustomerExcludingAssessedTax=[row('2025-01-01', '2025-12-31', 5e9, '2026-02-27')],
                                 InterestIncomeExpenseNet=[row('2025-01-01', '2025-12-31', 1e8, '2026-02-27')]))
        self.assertEqual(got['FY2025'][:2], (5e9, 'as_reported'))


def fee_fb(sector, industry, sic=None, lending=False, terms=None):
    """SYNTHETIC Mastercard-like fee network: liabilities 85.7% of assets, revenue 60% of assets, capex 1.5%, no cost of revenue."""
    rev = [2.2e10, 2.5e10, 2.8e10, 3.2e10]
    cols = dict(revenue=rev, operating_income=[r * .58 for r in rev], net_income=[r * .45 for r in rev], cfo=[r * .5 for r in rev],
                capex=[r * .015 for r in rev], d_and_a=[r * .03 for r in rev], cash=[r * .3 for r in rev], equity=[r * .095 for r in rev],
                long_term_debt=[r * .4 for r in rev], total_assets=[r / .605 for r in rev], total_liabilities=[r / .605 * .857 for r in rev],
                shares_outstanding=[9e8] * 4)
    if lending:
        cols['loans'] = [r * .5 for r in rev]
    fb = synth_fb({'sector': sector, 'industry': industry}, series_years(4, start=2022, **cols))
    if sic:
        with_sic(fb, *sic)
    if terms:
        fb['business_map'] = {'business_terms': {'per_10k_words': terms}}
    return fb


def car_fb(industry, sic=None):
    """SYNTHETIC Tesla-like car maker: gross margin 17%, inventory 14% of revenue, no prepaid subscriptions."""
    rev = [8.1e10, 9.7e10, 9.8e10, 9.5e10]
    fb = synth_fb({'sector': 'Consumer Discretionary', 'industry': industry}, series_years(
        4, start=2022, revenue=rev, gross_profit=[r * .17 for r in rev], operating_income=[r * .06 for r in rev], cfo=[r * .15 for r in rev],
        capex=[r * .09 for r in rev], d_and_a=[r * .05 for r in rev], inventory=[r * .14 for r in rev], cash=[r * .3 for r in rev],
        equity=[r * .7 for r in rev], total_assets=[r * 1.2 for r in rev], total_liabilities=[r * .5 for r in rev], shares_outstanding=[3.2e9] * 4))
    return with_sic(fb, *sic) if sic else fb


class RoutingTests(unittest.TestCase):
    def primary(self, fb):
        c = card(fb)
        return c['business_model']['primary'], c['valuation']['method'], c['business_model']['flags']

    def test_tsla_software_word_in_a_descriptive_label(self):
        p, m, _ = self.primary(car_fb('Automobile Manufacturers (with energy storage and a software, robotaxi and robotics business)'))
        self.assertEqual(p, 'hardware_manufacturing')
        p, _, _ = self.primary(car_fb('Electric vehicles, energy storage and AI software', sic=(3711, 'Motor Vehicles & Passenger Car Bodies')))
        self.assertEqual(p, 'hardware_manufacturing')

    def test_mastercard_wordings_stay_a_fee_platform(self):
        mc = (('Credit Services', 'Financial Services', None, None),
              ('Payment networks', 'Financials', (6199, 'Finance Services'), None),
              ('Financial exchanges & data', 'Financials', (6200, 'Security & Commodity Brokers'), None),
              ('Payments technology', 'Financials', None, {'payment_network': 85}))
        for industry, sector, sic, terms in mc:
            p, m, flags = self.primary(fee_fb(sector, industry, sic, terms=terms))
            self.assertEqual((p, m), ('platform_marketplace', 'fcff_annual_path'), industry)
            self.assertTrue(flags['operating_asset_base'], industry)
        # without the business-section vocabulary a sector-only 'Financials' stays financials (conservative)
        self.assertEqual(self.primary(fee_fb('Financials', 'Payments technology'))[0], 'financials')
        # a fee label with a loan book is a lender
        self.assertEqual(self.primary(fee_fb('Financials', 'Credit Services', lending=True))[0], 'financials')

    def test_infrastructure_keyword_without_capex_or_alternative(self):
        rev = [5.7e9, 6.9e9, 8.0e9, 9.2e9]
        fb = synth_fb({'sector': 'Data center equipment', 'industry': 'Data center power and thermal infrastructure'}, series_years(
            4, start=2022, revenue=rev, gross_profit=[r * .36 for r in rev], operating_income=[r * .17 for r in rev], cfo=[r * .15 for r in rev],
            capex=[r * .02 for r in rev], d_and_a=[r * .03 for r in rev], cash=[r * .1 for r in rev], equity=[r * .4 for r in rev],
            total_assets=[r * 1.1 for r in rev], total_liabilities=[r * .7 for r in rev], shares_outstanding=[3.8e8] * 4))
        self.assertEqual(self.primary(fb)[0], 'industrial')
        fb['company']['sector'] = 'Industrials'
        self.assertEqual(self.primary(fb)[0], 'industrial')


class LenderCheckTests(unittest.TestCase):
    """MA: the forecast's lender check follows the card's operating-asset-base finding."""

    def test_operating_asset_base_is_a_low_note_not_a_withheld_value(self):
        fb, an, cd, qa = industrial()
        for m, v in (('total_assets', 10e9), ('total_liabilities', 9.1e9)):
            F.add_fact(fb, m, 'I@2025-12-31', '2025-12-31', v, 'syn:fs', 'as_reported', '2026-02-10')
        cd = copy.deepcopy(cd)
        cd['business_model'].setdefault('flags', {})['operating_asset_base'] = True
        fc = forecast.build(fb, an, cd, qa)
        codes = {w['code'] for w in fc['warnings']}
        self.assertNotIn('possible_lender', codes)
        self.assertIn('lender_check_operating_base', codes)
        self.assertNotIn('possible_lender', {m['id'] for m in fc['spec']['underwriting']['material_issues']})


def no_payout_bank():
    fb = bank()
    fb['facts'] = [f for f in fb['facts'] if f['metric'] not in ('dividends_paid', 'buybacks')]
    return fb


class ResidualIncomeRouteTests(unittest.TestCase):
    def test_lender_premiums_do_not_read_margins_or_fcf(self):
        """(b) SOFI: unproven_economics and funding_dependence never read operating/FCF margins on the residual-income route."""
        cf = pipeline.run(bank())
        comps = {c['component']: c for c in cf['dc']['expected_return']['premium_components']}
        self.assertNotIn('unproven_economics', comps)
        self.assertNotIn('FCF margin', json.dumps(comps))
        self.assertIn(comps['funding_dependence']['basis'].split(':')[0],
                      ('the plan depends on outside capital', 'self-funded (model card)', 'balance-sheet lender'))

    def test_theoretical_distributions_are_labelled(self):
        """(e) a bank that pays nothing: base_irr counts the model's distributions; the owner-facing figure says so."""
        cf = pipeline.run(no_payout_bank())
        self.assertFalse(cf['fc']['anchors']['distribution_history'])
        td = cf['dc']['expected_return']['owner_facing_return'].get('theoretical_distributions')
        self.assertTrue(td and td['applies'])
        payer = pipeline.run(bank())
        self.assertNotIn('theoretical_distributions', payer['dc']['expected_return']['owner_facing_return'])
        self.assertEqual(payer['fc']['spec'], pipeline.run(bank())['fc']['spec'])

    def test_foreign_filer_without_country_split_is_a_draft(self):
        """(d) NU: country risk requested, no exposure data: a material issue, not a silent US rate."""
        fb = bank()
        fb['company']['annual_form'] = '20-F'
        cf = pipeline.run(fb, {'market_context': {}})
        issues = {m['id']: m['status'] for m in cf['fc']['material_issues']}
        self.assertEqual(issues.get('country_risk_unmeasured'), 'unresolved')
        self.assertIn('country_risk_unmeasured', {m['id'] for m in decision._issues(None, cf['fc'])})
        geo = {'shares': {'Brazil': .91, 'Mexico': .07}, 'note': 'revenue by country FY2025 (TEST)', 'evidence_ids': ['syn']}
        cf2 = pipeline.run(fb, {'market_context': {'geography': geo}})
        self.assertFalse(cf2['fc']['material_issues'])
        self.assertTrue(any(a['factor'] == 'country_risk' for a in cf2['fc']['cost_of_equity']['components']['adjustments']))
        us = pipeline.run(bank(), {'market_context': {}})                       # a US filer: an open question, no issue
        self.assertFalse(us['fc']['material_issues'])


class PackRouteTests(unittest.TestCase):
    def test_classification_review_and_revenue_geography(self):
        """(c) MA and (d) NU: both reach the engine through the pack, so a plain ticker-value rerun keeps them."""
        p = pack()
        p['forecast_options'] = {
            'classification_review': {'reviewer': 'review_numbers', 'reason': 'client funds 8.5% of liabilities; no loan book',
                                      'evidence_ids': ['s_10q']},
            'revenue_geography': {'shares': {'Brazil': .913, 'Mexico': .07}, 'basis': 'revenue by country', 'as_of': '2025-12-31',
                                  'reason': '20-F note 34b', 'evidence_ids': ['s_10k']}}
        got = to_inputs(p)
        rv = got['forecast']['reviews']['classification']
        self.assertEqual((rv['status'], rv['evidence']), ('reviewed', ['web:s_10q']))
        self.assertEqual(got['market_context']['geography']['shares'], {'Brazil': .913, 'Mexico': .07})
        for opt, bad, frag in (('classification_review', {'evidence_ids': ['s_av']}, 'filing document'),
                               ('revenue_geography', {'shares': {'Brazil': .9, 'Mexico': .2}}, 'at most 1'),
                               ('revenue_geography', {'evidence_ids': ['s_est']}, "company's own document")):
            q = pack()
            q['forecast_options'] = {opt: {**p['forecast_options'][opt], **bad}}
            with self.assertRaises(ValueError) as e:
                to_inputs(q)
            self.assertIn(frag, str(e.exception))


if __name__ == '__main__':
    unittest.main()
