"""Foreign issuers: reporting currency, IFRS facts, dated exchange rates, depositary ratios and the quote per ordinary share.
Every payload, report sentence and rate below is SYNTHETIC (test data only)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from fundamental_engine import business, forecast, fx, pipeline, runbook  # noqa: E402
from fundamental_engine import facts as F  # noqa: E402
from test_forecast import secular_cyclical  # noqa: E402


def dur(start, end, val, filed, accn, form='20-F'):
    return {'start': start, 'end': end, 'val': val, 'filed': filed, 'form': form, 'fp': 'FY', 'accn': accn}


def ifrs_payload():
    """A 20-F filer tagging ifrs-full in EUR with USD convenience translations of a few lines."""
    return {'cik': 2, 'entityName': 'Synthetic NV', 'facts': {
        'ifrs-full': {
            'Revenue': {'units': {'EUR': [dur('2024-01-01', '2024-12-31', 1000.0, '2025-02-20', 'a24'),
                                          dur('2025-01-01', '2025-12-31', 1150.0, '2026-02-20', 'a25')],
                                  'USD': [dur('2025-01-01', '2025-12-31', 1290.0, '2026-02-20', 'a25')]}},
            'ProfitLossFromOperatingActivities': {'units': {'EUR': [dur('2025-01-01', '2025-12-31', 300.0, '2026-02-20', 'a25')]}},
            'CashAndCashEquivalents': {'units': {'EUR': [{'end': '2025-12-31', 'val': 400.0, 'filed': '2026-02-20', 'form': '20-F',
                                                           'accn': 'a25'}]}},
            'Inventories': {'units': {'EUR': [{'end': '2025-12-31', 'val': 90.0, 'filed': '2026-02-20', 'form': '20-F', 'accn': 'a25'}]}},
        }}}


def write_fx(folder, sid, rows):
    with open(os.path.join(folder, f'{sid}.csv'), 'w', encoding='utf-8') as f:
        f.write(f'observation_date,{sid}\n' + ''.join(f'{d},{v}\n' for d, v in rows))


class CurrencyTests(unittest.TestCase):
    def test_latest_annual_report_decides_and_usd_translations_are_ignored(self):
        self.assertEqual(F.reporting_currency(ifrs_payload()), 'EUR')
        self.assertEqual(F.reporting_currency({'facts': {}}), 'USD')

    def test_ifrs_facts_are_read_in_the_reporting_currency(self):
        fb = F.new({'ticker': 'SNV', 'currency': 'EUR'}, '2026-10-20')
        F.from_sec_companyfacts(fb, ifrs_payload(), '2026-10-20')
        self.assertEqual(F.value(fb, 'revenue', period_end='2025-12-31'), 1150.0)          # EUR, not the USD translation
        self.assertEqual(F.value(fb, 'operating_income', period_end='2025-12-31'), 300.0)
        self.assertEqual(F.latest_instant(fb, 'cash')['value'], 400.0)
        self.assertEqual(F.get(fb, 'revenue', period_end='2025-12-31')['unit'], 'EUR')


class RateTests(unittest.TestCase):
    def test_dated_rate_inverted_when_quoted_per_unit_and_never_stale(self):
        with tempfile.TemporaryDirectory() as d:
            write_fx(d, 'DEXUSEU', [('2026-10-14', '1.10'), ('2026-10-15', '.'), ('2026-10-16', '1.125')])
            write_fx(d, 'DEXTAUS', [('2026-10-16', '31.5')])
            eur = fx.rate('EUR', '2026-10-18', d)                        # weekend: Friday's observation
            self.assertEqual(eur['date'], '2026-10-16')
            self.assertAlmostEqual(eur['value'], 1 / 1.125)             # EUR per USD
            self.assertAlmostEqual(fx.rate('TWD', '2026-10-20', d)['value'], 31.5)
            self.assertIsNone(fx.rate('TWD', '2026-10-30', d))           # more than 7 days old
            self.assertIsNone(fx.rate('JPY', '2026-10-20', d))           # series not saved
            self.assertIsNone(fx.rate('RUB', '2026-10-20', d))           # no series known
            self.assertEqual(fx.rate('USD', '2026-10-20', d)['value'], 1.)


class LocalRateTests(unittest.TestCase):
    def test_local_long_term_yield_dated_and_never_stale(self):
        with tempfile.TemporaryDirectory() as d:
            write_fx(d, 'IRLTLT01DEM156N', [('2026-08-01', '2.60'), ('2026-09-01', '2.55')])
            r = fx.local_rate('EUR', '2026-10-20', d)
            self.assertEqual((r['value'], r['date']), (.0255, '2026-09-01'))
            self.assertIsNone(fx.local_rate('EUR', '2026-12-20', d))         # more than 75 days old
            self.assertIsNone(fx.local_rate('TWD', '2026-10-20', d))         # no series known

    def test_cash_flows_are_discounted_in_their_own_currency(self):
        def eur(local=None):
            fb, an, card, qa, _ = secular_cyclical()
            fb['company']['currency'] = 'EUR'
            fb['market']['price']['currency'] = 'EUR'
            if local is not None:
                F.add_source(fb, 'fred:IRLTLT01DEM156N', 'fred', 'IRLTLT01DEM156N', 'market_feed', '2026-02-01')
                fb['market']['risk_free'] = {'value': local, 'as_of': '2026-02-01', 'source_id': 'fred:IRLTLT01DEM156N', 'currency': 'EUR'}
            return forecast.build(fb, an, card, qa)
        own, usd = eur(.025), eur()
        self.assertEqual(own['cost_of_equity']['components']['risk_free'], .025)
        self.assertNotIn('risk_free_currency', {w['code'] for w in own['warnings']})
        self.assertEqual(usd['cost_of_equity']['components']['risk_free'], forecast.RF_DEFAULT)
        self.assertIn('risk_free_currency', {w['code'] for w in usd['warnings'] if w['severity'] == 'high'})
        self.assertLess(own['cost_of_equity']['base'], usd['cost_of_equity']['base'])

    def test_index_return_is_shifted_by_the_rate_gap(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'companies', 'SNV', 'out'))
            with open(os.path.join(d, 'companies', 'QUEUE.json'), 'w') as f:
                json.dump({'queue': [{'symbol': 'SNV', 'lane': 'quality'}]}, f)
            cf = {'company': {'ticker': 'SNV', 'currency': 'EUR'}, 'fc': {'cost_of_equity': {'components': {'risk_free': .025}}},
                  'dc': {'expected_return': {'base_irr': .09, 'index_expected_return': .08}}}
            with open(os.path.join(d, 'companies', 'SNV', 'out', 'casefile.json'), 'w') as f:
                json.dump(cf, f)
            with open(os.path.join(d, 'cal.json'), 'w') as f:
                json.dump({'engine_market_return': .07}, f)
            res = runbook.run(d, '2026-10-20', names=('',), calibration=os.path.join(d, 'cal.json'))
            row = next(r for r in res['rows'] if r['symbol'] == 'SNV')
            self.assertAlmostEqual(row['index_expected_return'], .07 + .025 - forecast.RF_DEFAULT)


class DepositaryRatioTests(unittest.TestCase):
    def test_stated_ratios_in_words_numbers_and_share_classes(self):
        cases = [('<p>Each ADS represents five common shares.</p>', 5.),
                 ('<p>American Depositary Shares, each representing 0.5 ordinary shares</p>', .5),
                 ('<p>American Depositary Receipts, each representing one B Share of the company</p>', 1.),
                 ('<p>ADSs, each of which represents the right to receive twenty-five Class A ordinary shares</p>', 25.)]
        for html, want in cases:
            r = business.ads_ratio(html)
            self.assertEqual((r['ratio'], r['ambiguous']), (want, False), html)

    def test_direct_listing_and_conflicting_ratios(self):
        cover = ('<p>Securities registered pursuant to Section 12(b): Title of each class Trading symbol Name of each exchange '
                 'Ordinary shares, nominal value EUR 0.09 per share SNV Nasdaq</p>')
        direct = business.ads_ratio(cover)
        self.assertEqual((direct['ratio'], direct['direct_listing']), (1., True))
        both = business.ads_ratio('<p>Each ADS represents five ordinary shares.</p><p>Each ADS represents ten ordinary shares.</p>')
        self.assertTrue(both['ambiguous'])
        self.assertIsNone(business.ads_ratio('<p>No securities of this kind.</p>'))


class QuoteConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw, self.fx_dir = os.path.join(self.tmp.name, 'raw'), os.path.join(self.tmp.name, 'fx')
        os.makedirs(self.raw)
        os.makedirs(self.fx_dir)
        self.old = pipeline.FX_DIR
        pipeline.FX_DIR = self.fx_dir

    def tearDown(self):
        pipeline.FX_DIR = self.old
        self.tmp.cleanup()

    def fb(self, ccy, quote_ccy, report=None, ads=False):
        if report is not None:
            with open(os.path.join(self.raw, 'sec__annual_report.htm'), 'w', encoding='utf-8') as f:
                f.write(report)
        fb = F.new({'ticker': 'SYN', 'currency': ccy, 'depositary_shares': ads}, '2026-10-20')
        F.add_source(fb, 'listing:price', 'nasdaq', 'last sale', 'market_feed', '2026-10-17')
        F.set_price(fb, 200.0, '2026-10-16', 'listing:price', currency=quote_ccy)
        return fb

    def test_depositary_quote_becomes_one_ordinary_share_in_the_reporting_currency(self):
        write_fx(self.fx_dir, 'DEXTAUS', [('2026-10-16', '32.0')])
        fb, notes = self.fb('TWD', 'USD', '<p>Each ADS represents five common shares.</p>', ads=True), []
        pipeline._convert_quote(fb, self.raw, '2026-10-20', notes)
        p = fb['market']['price']
        self.assertEqual(p['currency'], 'TWD')
        self.assertAlmostEqual(p['value'], 200.0 * 32.0 / 5)
        self.assertEqual(p['conversion']['shares_per_depositary_share'], 5.)
        self.assertIn('fx:fred', fb['sources'])

    def test_direct_listing_converts_currency_only(self):
        write_fx(self.fx_dir, 'DEXUSEU', [('2026-10-16', '1.25')])
        cover = '<p>Title of each class Ordinary shares, nominal value EUR 0.09 per share SNV Nasdaq</p>'
        fb, notes = self.fb('EUR', 'USD', cover), []
        pipeline._convert_quote(fb, self.raw, '2026-10-20', notes)
        self.assertAlmostEqual(fb['market']['price']['value'], 200.0 / 1.25)
        self.assertTrue(fb['market']['price']['conversion']['direct_listing'])

    def test_without_a_rate_or_ratio_nothing_is_guessed(self):
        fb, notes = self.fb('DKK', 'USD', '<p>Each ADS represents one B share.</p>'), []
        pipeline._convert_quote(fb, self.raw, '2026-10-20', notes)                 # no DKK series saved
        self.assertEqual((fb['market']['price']['currency'], fb['market']['price']['value']), ('USD', 200.0))
        self.assertIn('no dated exchange rate', notes[-1])

    def test_usd_depositary_share_is_divided_by_its_ratio_or_withheld(self):
        fb, notes = self.fb('USD', 'USD', '<p>Each ADS represents four ordinary shares.</p>', ads=True), []
        pipeline._convert_quote(fb, self.raw, '2026-10-20', notes)
        self.assertAlmostEqual(fb['market']['price']['value'], 50.0)
        os.remove(os.path.join(self.raw, 'sec__annual_report.htm'))
        fb2, notes2 = self.fb('USD', 'USD', ads=True), []
        pipeline._convert_quote(fb2, self.raw, '2026-10-20', notes2)
        self.assertEqual(fb2['market']['price']['value'], 200.0)
        self.assertIn('depositary', fb2['market']['price']['unresolved'])


IX = """<html><xbrli:context id="fy25"><xbrli:entity></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate>
<xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="fy24"><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="i25"><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="a25"><xbrli:segment><xbrldi:explicitMember dimension="ifrs-full:ClassesOfShareCapitalAxis">syn:AMember</xbrldi:explicitMember>
</xbrli:segment><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="b25"><xbrli:segment><xbrldi:explicitMember dimension="ifrs-full:ClassesOfShareCapitalAxis">syn:BMember</xbrldi:explicitMember>
</xbrli:segment><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>
<xbrli:unit id="sh"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
<ix:nonFraction name="ifrs-full:Revenue" contextRef="fy25" unitRef="eur" decimals="-6" scale="6">1,300</ix:nonFraction>
<ix:nonFraction name="ifrs-full:Revenue" contextRef="fy24" unitRef="eur" decimals="-6" scale="6">1,140</ix:nonFraction>
<ix:nonFraction name="ifrs-full:DepreciationExpense" contextRef="fy25" unitRef="eur" decimals="-6" scale="6">60</ix:nonFraction>
<ix:nonFraction name="ifrs-full:AmortisationExpense" contextRef="fy25" unitRef="eur" decimals="-6" scale="6">15</ix:nonFraction>
<ix:nonFraction name="ifrs-full:CashAndCashEquivalents" contextRef="i25" unitRef="eur" decimals="-6" scale="6">450</ix:nonFraction>
<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="a25" unitRef="sh" decimals="0">100</ix:nonFraction>
<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="b25" unitRef="sh" decimals="0">300</ix:nonFraction>
</html>"""


class AnnualReportInlineTests(unittest.TestCase):
    def test_periods_missing_from_companyfacts_are_read_from_the_document(self):
        fb = F.new({'ticker': 'SNV', 'currency': 'EUR'}, '2026-10-20')
        cf = ifrs_payload()
        cf['facts']['ifrs-full']['Revenue']['units']['EUR'].pop()                          # companyfacts stops at FY2024
        F.from_sec_companyfacts(fb, cf, '2026-10-20')
        payload = F.inline_payload(business.parse_ixbrl(IX), business.ix_units(IX), '2026-03-01', 'x25', '20-F')
        n = F.from_annual_inline(fb, payload, '2024-12-31', '2026-10-20', 'https://www.sec.gov/synthetic.htm')
        self.assertGreater(n, 0)
        r = F.get(fb, 'revenue', period_end='2025-12-31')
        self.assertEqual((r['value'], r['source_id'], r['available_at']), (1300e6, 'sec:annual_report_ixbrl', '2026-03-03'))
        self.assertEqual(F.value(fb, 'revenue_prior_comparable', 'CMP@2025-12-31'), 1140e6)    # the report's comparative column
        self.assertEqual([v for e, v, _ in F.series(fb, 'revenue') if e == '2024-12-31'], [1000.0])  # companyfacts keeps 2024
        da = F.get(fb, 'd_and_a', period_end='2025-12-31')                                   # depreciation + amortisation lines
        self.assertEqual((da['value'], da['basis']), (75e6, 'derived'))

    def test_ifrs_share_classes_on_the_cover_are_summed(self):
        cs = business.business_map(IX)['cover_shares']
        self.assertEqual((cs['total'], len(cs['by_class'])), (400., 2))

    def test_alpha_vantage_rows_in_another_currency_are_skipped(self):
        fb = F.new({'ticker': 'SNV', 'currency': 'EUR'}, '2026-10-20')
        rows = [{'fiscalDateEnding': '2026-06-30', 'reportedCurrency': 'USD', 'totalRevenue': '900'},
                {'fiscalDateEnding': '2026-03-31', 'reportedCurrency': 'EUR', 'totalRevenue': '800'}]
        F.from_alpha_vantage(fb, {'income': {'quarterlyReports': rows}}, '2026-10-20')
        q = F.series(fb, 'revenue', 'quarter')
        self.assertEqual([(e, v, f['unit']) for e, v, f in q], [('2026-03-31', 800.0, 'EUR')])
        self.assertIn('USD skipped', fb['collection_notes'][-1])


class WebConsensusTests(unittest.TestCase):
    def test_consensus_from_a_public_page_is_secondary_and_dated_at_retrieval(self):
        fb = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-20')
        n = F.from_consensus_table(fb, {'provider': 'yahoo_finance', 'url': 'https://finance.yahoo.com/quote/SYN/analysis/',
                                        'retrieved_at': '2026-10-20T16:25:00+00:00',
                                        'rows': [{'metric': 'revenue', 'period_end': '2026-12-31', 'value': 1.6e10, 'n_analysts': 45}]})
        self.assertEqual(n, 1)
        self.assertEqual(fb['sources']['web:consensus']['retrieval'], 'secondary')
        self.assertEqual((fb['estimates'][0]['as_of'], fb['estimates'][0]['value']), ('2026-10-20', 1.6e10))
        early = F.new({'ticker': 'SYN', 'currency': 'USD'}, '2026-10-01')
        with self.assertRaises(ValueError):                     # a page read after the cutoff is look-ahead
            F.from_consensus_table(early, {'provider': 'x', 'retrieved_at': '2026-10-20', 'rows': []})


class IndustryTermsTests(unittest.TestCase):
    def test_density_per_100k_words(self):
        t = business.industry_terms('<p>' + 'lithography systems for semiconductor wafer fabs ' * 10 + '</p>')
        self.assertEqual(t['counts']['semiconductor'], 30)
        self.assertAlmostEqual(t['per_100k_words']['semiconductor'], 30 / 60 * 1e5)


if __name__ == '__main__':
    unittest.main()
