"""universe.py: broad discovery universe from saved SEC frames + SEC ticker map + Nasdaq listings (SYNTHETIC raw folder)."""
import json
import os
import tempfile
import unittest

from fundamental_engine import universe, screen


def _frame(rows, end='2025-12-31', start='2025-01-01'):
    return {'taxonomy': 'us-gaap', 'data': [{'accn': f'a{c}', 'cik': c, 'entityName': n, 'start': start, 'end': end, 'val': v}
                                            for c, n, v in rows]}


def make_raw(d):
    """Three SYNTHETIC issuers: OPCO (operating, listed), BANKCO (bank SIC, listed), OTCCO (OTC only)."""
    raw = os.path.join(d, 'raw')
    os.makedirs(os.path.join(raw, 'frames'))
    os.makedirs(os.path.join(raw, 'submissions'))
    tick = {'fields': ['cik', 'name', 'ticker', 'exchange'],
            'data': [[1, 'Op Co', 'OPCO', 'Nasdaq'], [1, 'Op Co', 'OPCB', 'Nasdaq'], [2, 'Bank Co', 'BANK', 'NYSE'], [3, 'Otc Co', 'OTCC', 'OTC']]}
    nas = {'data': {'asOf': None, 'rows': [
        {'symbol': 'OPCO', 'lastsale': '$50.00', 'marketCap': '4000000000', 'sector': 'Technology', 'industry': 'Computer Software', 'country': 'United States'},
        {'symbol': 'OPCB', 'lastsale': '$49.00', 'marketCap': '1000000000', 'sector': 'Technology', 'industry': 'Computer Software', 'country': 'United States'},
        {'symbol': 'BANK', 'lastsale': '$20.00', 'marketCap': '3000000000', 'sector': 'Finance', 'industry': 'Major Banks', 'country': 'United States'}]}}
    calls = []
    for name, payload in (('sec__company_tickers_exchange.json', tick), ('nasdaq__screener_stocks.json', nas)):
        with open(os.path.join(raw, name), 'w') as f:
            json.dump(payload, f)
        calls.append({'file': name, 'status': 200, 'sha256': 'x'})
    vals = {2024: {'revenue': 800., 'operating_income': 160., 'net_income': 120., 'cfo': 200., 'capex': 20.},
            2025: {'revenue': 1000., 'operating_income': 250., 'net_income': 190., 'cfo': 260., 'capex': 25.}}
    tags = {'revenue': 'Revenues', 'operating_income': 'OperatingIncomeLoss', 'net_income': 'NetIncomeLoss',
            'cfo': 'NetCashProvidedByUsedInOperatingActivities', 'capex': 'PaymentsToAcquirePropertyPlantAndEquipment'}
    for y, v in vals.items():
        for m, x in v.items():
            name = f'frames/{m}__{tags[m]}__CY{y}.json'
            rows = [(1, 'Op Co', x * 1e6), (2, 'Bank Co', x * 2e6), (3, 'Otc Co', x * 1e6)]
            with open(os.path.join(raw, name), 'w') as f:
                json.dump(_frame(rows, f'{y}-12-31', f'{y}-01-01'), f)
            calls.append({'file': name, 'status': 200, 'metric': m, 'tag': tags[m], 'year': y})
    with open(os.path.join(raw, 'MANIFEST.json'), 'w') as f:
        json.dump({'calls': calls}, f)
    for cik, sic, desc in ((1, '7372', 'Services-Prepackaged Software'), (2, '6022', 'State Commercial Banks')):
        sub = {'cik': str(cik), 'sic': sic, 'sicDescription': desc, 'fiscalYearEnd': '1231', 'category': 'Large accelerated filer',
               'entityType': 'operating', 'stateOfIncorporation': 'DE',
               'filings': {'recent': {'form': ['10-Q', '10-K'], 'filingDate': ['2026-08-01', '2026-02-20']}}}
        with open(os.path.join(raw, 'submissions', f'CIK{cik:010d}.json'), 'w') as f:
            json.dump(sub, f)
    return raw


class UniverseBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = make_raw(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_and_coverage(self):
        rows, rep = universe.build(self.raw, '2026-10-20', min_market_cap=1e8)
        syms = {r['symbol']: r for r in rows}
        self.assertEqual(set(syms), {'OPCO', 'BANK'})                       # OTC listing excluded and counted
        self.assertEqual(rep['excluded'], {'not_listed_on_nyse_nasdaq_or_no_listing_match': 1})
        op = syms['OPCO']
        self.assertEqual(op['metrics']['market_cap'], 5e9)                  # multi-class: both listed classes summed
        self.assertTrue(any('multi-class' in n for n in op.get('notes', [])))
        self.assertAlmostEqual(op['metrics']['revenue_growth'], .25)
        self.assertIn('coverage', rep)
        self.assertEqual(rep['by_exchange'], {'Nasdaq': 1, 'NYSE': 1})

    def test_size_floor_is_reported(self):
        rows, rep = universe.build(self.raw, '2026-10-20', min_market_cap=3.5e9)
        self.assertEqual({r['symbol'] for r in rows}, {'OPCO'})
        self.assertEqual(rep['excluded'].get('market_cap_below_3,500,000,000'), 1)

    def test_sec_profile_routes_banks_away_from_operating_lenses(self):
        rows, _ = universe.build(self.raw, '2026-10-20', min_market_cap=1e8)
        prof = universe.sec_profiles(self.raw, {1: 'OPCO', 2: 'BANK'})
        self.assertEqual(prof['BANK']['balance_sheet_economics'], 'bank or savings institution')
        self.assertEqual(prof['OPCO']['annual_form'], '10-K')
        universe.apply_profiles(rows, prof)
        bank = next(r for r in rows if r['symbol'] == 'BANK')
        self.assertEqual((bank['industry'], bank['industry_listing']), ('State Commercial Banks', 'Major Banks'))
        why, _ = screen._support(bank, screen._options(None))
        self.assertIn('SEC SIC 6022', why)
        self.assertIsNone(screen._support(next(r for r in rows if r['symbol'] == 'OPCO'), screen._options(None))[0])


class SanityTests(unittest.TestCase):
    def row(self, **m):
        return {'symbol': 'X', 'metrics': dict(m), 'basis': {k: 'test' for k in m}}

    def test_component_revenue_is_critical_and_blocks_ranking(self):
        r = universe.sanity(self.row(operating_margin=1.79, net_margin=1.03, pe=3.2, revenue_ttm=133e6))
        self.assertEqual(r['data_flags'][0]['code'], 'implausible_margin')
        self.assertNotIn('pe', r['metrics'])
        r.update(data_grade='sec_frames', as_of='2026-10-20')
        du = screen._data_uncertainty({'row': r, 'm': r['metrics']}, '2026-10-20')
        self.assertEqual(du['level'], 'weak')
        self.assertIn('data check failed', du['reasons'][0])

    def test_minor_and_major_flags(self):
        r = universe.sanity(self.row(net_margin=.0002, pe=30000., fcf_conversion=1500., operating_margin=.39))
        self.assertEqual({f['code'] for f in r['data_flags']}, {'negligible_earnings', 'fcf_conversion_outlier'})
        self.assertNotIn('pe', r['metrics'])
        r = universe.sanity(self.row(net_margin=.53, operating_margin=.25, pe=5.3))
        self.assertEqual(r['data_flags'][0]['code'], 'non_operating_earnings')
        r = universe.sanity(self.row(net_margin=.19, operating_margin=.21, pe=1.3))
        self.assertEqual(r['data_flags'][-1]['severity'], 'critical')

    def test_clean_row_has_no_flags(self):
        self.assertEqual(universe.sanity(self.row(net_margin=.12, operating_margin=.18, pe=22.))['data_flags'], [])


if __name__ == '__main__':
    unittest.main()
