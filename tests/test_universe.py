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


def frame_row(sym, rev, oi, ni, cfo, capex, gp=None, shares=100e6, cash=500e6, debt=300e6, equity=2e9):
    return {'symbol': sym, 'cik': 1, 'name': sym + ' (SYNTHETIC)', 'sector': 'Industrials', 'industry': 'Industrial Machinery',
            'revenue': rev, 'gross_profit': gp if gp is not None else rev * .4, 'operating_income': oi, 'net_income': ni, 'cfo': cfo,
            'capex': capex, 'fcf': cfo - capex, 'sbc': rev * .01, 'cash': cash, 'debt': debt, 'debt_scope': 'noncurrent_plus_current',
            'equity': equity, 'total_assets': 5e9, 'total_liabilities': 2.5e9, 'shares_outstanding': shares}


def recovery_rows(sym, revs, margins, gm=None, mcap=1.2e9):
    hist = [[frame_row(sym, r, r * m, r * m * .75, r * m * 1.1, r * .03, gp=None if gm is None else r * g)]
            for r, m, g in zip(revs[:-1], margins[:-1], (gm or [None] * len(revs))[:-1])]
    r, m = revs[-1], margins[-1]
    cur = [frame_row(sym, r, r * m, r * m * .75, r * m * 1.1, r * .03, gp=None if gm is None else r * gm[-1])]
    return screen.rows_from_sec_frames(cur, prices={sym: {'market_cap': mcap}}, history=hist, as_of='2026-10-20')


class RecoveryLensTests(unittest.TestCase):
    def test_temporary_setback_in_a_business_that_earned_well(self):
        rows = recovery_rows('DIP', [2.0e9, 2.2e9, 2.4e9, 2.2e9], [.18, .19, .20, .12])      # margin falls 8 pts, revenue -8%
        out = screen.scan(rows, {'as_of': '2026-10-20'})
        self.assertIn('DIP', [c['symbol'] for c in out['lenses']['recovery_mispriced']], out['near_misses'].get('recovery_mispriced'))
        cand = next(c for c in out['lenses']['recovery_mispriced'] if c['symbol'] == 'DIP')
        self.assertIn('temporary_vs_structural_open', cand['tags'])
        self.assertIn('Temporary or structural', cand['why'])

    def test_structural_decline_is_not_a_recovery(self):
        rows = recovery_rows('MELT', [3.0e9, 2.7e9, 2.4e9, 2.1e9], [.18, .16, .14, .10])     # three falling years
        out = screen.scan(rows, {'as_of': '2026-10-20'})
        self.assertNotIn('MELT', [c['symbol'] for c in out['lenses']['recovery_mispriced']])

    def test_never_earned_is_not_a_recovery(self):
        rows = recovery_rows('NEVER', [2.0e9, 2.2e9, 2.4e9, 2.2e9], [.01, .02, .02, -.04])
        out = screen.scan(rows, {'as_of': '2026-10-20'})
        self.assertNotIn('NEVER', [c['symbol'] for c in out['lenses']['recovery_mispriced']])


class ListingLabelCycleTests(unittest.TestCase):
    def test_listing_label_keeps_the_cyclical_cue_after_sic_replacement(self):
        rows = recovery_rows('ORE', [1.0e9, 1.1e9, 1.0e9, 1.6e9], [.17, -.09, -.32, .28])
        rows[0]['industry'], rows[0]['industry_listing'] = 'Mineral Royalty Traders', 'Precious Metals'
        out = screen.scan(rows, {'as_of': '2026-10-20'})
        cy = out['dimensions']['ORE']['profile']['cyclical']
        self.assertTrue(cy and cy['value'])
        self.assertEqual(cy['word'], 'metals')
        self.assertNotIn('ORE', [c['symbol'] for c in out['lenses']['quality_compounder']])


class FrameCoverageTests(unittest.TestCase):
    """A filer whose newest annual frame is not published keeps last year's (flagged); IFRS frames convert at dated rates."""

    def test_lagging_filer_keeps_the_previous_frame_without_counting_a_year_twice(self):
        with tempfile.TemporaryDirectory() as d:
            raw = make_raw(d)
            with open(os.path.join(raw, 'MANIFEST.json')) as f:
                man = json.load(f)
            for c in man['calls']:                       # SYNTHETIC: Op Co (CIK 1) has no CY2025 rows at all
                if c.get('year') == 2025:
                    p = os.path.join(raw, c['file'])
                    with open(p) as f:
                        fr = json.load(f)
                    fr['data'] = [r for r in fr['data'] if r['cik'] != 1]
                    with open(p, 'w') as f:
                        json.dump(fr, f)
            rows, rep = universe.build(raw, '2026-10-20')
            op = next(r for r in rows if r['symbol'] == 'OPCO')
            self.assertIn('stale_frame', [f['code'] for f in op['data_flags'] if isinstance(f, dict)])
            self.assertEqual(op['metrics']['revenue_ttm'], 800e6)                  # CY2024 is its latest
            self.assertNotIn('revenue_growth', op['metrics'])                      # no CY2024-vs-CY2024 'growth'
            self.assertNotIn('no_revenue_frame_latest_year', ' '.join(rep['excluded']))

    def test_ifrs_rows_use_the_reporting_currency_and_dated_rates(self):
        from fundamental_engine import sec_frames as S
        fr = lambda val, cik=9, end='2021-12-31': {'data': [{'cik': cik, 'entityName': 'Syn NV', 'end': end, 'val': val, 'accn': 'x'}]}
        payloads = [{'metric': 'revenue', 'tag': 'Revenue', 'unit': 'EUR', 'payload': fr(800.)},
                    {'metric': 'revenue', 'tag': 'Revenue', 'unit': 'USD', 'payload': fr(900.)},     # convenience translation
                    {'metric': 'cash', 'tag': 'CashAndCashEquivalents', 'unit': 'EUR', 'payload': fr(80.)},
                    {'metric': 'revenue', 'tag': 'Revenue', 'unit': 'TWD', 'payload': fr(3200., cik=8)}]
        rate = lambda ccy, end: {'EUR': .8}.get(ccy)                                  # no TWD rate saved
        rows, skipped = S.merge_ifrs(payloads, rate)
        self.assertEqual(rows[9]['reporting_currency'], 'EUR')
        self.assertAlmostEqual(rows[9]['revenue']['value'], 1000.)                  # 800 EUR / 0.8 EUR per USD
        self.assertEqual(rows[9]['revenue']['value_reporting_currency'], 800.)
        self.assertEqual(skipped, [(8, 'TWD')])                                       # never converted at a guessed rate
