"""Portfolio layer tests. Every portfolio here is SYNTHETIC: fictional tickers, invented quantities and
values, placeholder account codes. No private position data belongs in this file."""
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from fundamental_engine import portfolio
from fundamental_engine.portfolio import from_ib, analyze, compare, tags_from_themes, _no_orders, ORDER_KEYS, _k

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AS_OF = '2026-10-19'
FAKE_ACCT = 'U0000000'     # synthetic placeholder; must never be copied into a book


def row(sym, conid, qty, price, avg=None, upnl=None, ac='STK', ccy='USD', **kw):
    r = {'acctId': FAKE_ACCT, 'ticker': sym, 'conid': conid, 'position': qty, 'mktPrice': price,
         'mktValue': qty * price, 'currency': ccy, 'assetClass': ac, **kw}
    if avg is not None:
        r['avgCost'] = avg
    if upnl is not None:
        r['unrealizedPnl'] = upnl
    return r


def synthetic_positions():
    # NLV 1,000,000 = 880,000 positions + 120,000 cash
    return {'positions': [
        row('SEMA', 1001, 1050, 200.0, avg=120.0, upnl=1050 * 80.0),     # GPU designer, 21%, 40% embedded gain
        row('SEMB', 1002, 1000, 100.0, avg=90.0, upnl=10000.0),          # foundry, 10%
        row('AIPW', 1003, 800, 100.0),                                  # data-centre power equipment, 8%, no cost data
        row('BRND', 1004, 1400, 100.0),                                 # consumer brand, 14%
        row('UTLX', 1005, 2000, 50.0, avg=55.0, upnl=-10000.0),          # utility, 10%, embedded loss
        row('IDXF', 1006, 500, 500.0, avg=400.0, upnl=50000.0, ac='ETF'),  # broad index fund, 25%
    ]}


SUMMARY = {'accountcode': {'value': FAKE_ACCT}, 'netliquidation': {'amount': 1000000.0, 'currency': 'USD'},
           'totalcashvalue': {'amount': 120000.0, 'currency': 'USD'}, 'grosspositionvalue': {'amount': 880000.0, 'currency': 'USD'}}


def cf(verdict, base_irr, bear, base, bull, price, as_of='2026-09-15', quality='strong', risk='medium', **kw):
    return {'verdict': verdict, 'base_irr': base_irr, 'bear_value': bear, 'base_value': base, 'bull_value': bull,
            'price': price, 'as_of': as_of, 'quality': quality, 'risk_level': risk, **kw}


INFO = {
    'SEMA': {'sector': 'Technology', 'industry': 'Semiconductors', 'exposures': ['ai_capex_cycle', 'semis_supply_chain', 'china'],
             'exposure_weights': {'ai_capex_cycle': 0.8, 'semis_supply_chain': 0.9, 'china': 0.15},
             'thesis_drivers': ['Hyperscaler AI capex keeps growing', 'GPU share holds'], 'key_risks': ['AI capex digestion'],
             'counterparties': {'HyperscalerCo': 0.5},
             'casefile_summary': cf('quality_but_expensive', 0.09, 120, 210, 300, 150, risk='high')},     # price 150 -> 200 now
    'SEMB': {'sector': 'Technology', 'industry': 'Semiconductors',
             'exposure_weights': {'ai_capex_cycle': 0.6, 'semis_supply_chain': 1.0, 'taiwan_geopolitics': 0.9},
             'thesis_drivers': ['hyperscaler AI capex keeps growing', 'Advanced-node pricing power'], 'key_risks': ['AI capex digestion'],
             'counterparties': {'HyperscalerCo': 0.3},
             'casefile_summary': cf('attractive_at_price', 0.13, 70, 130, 170, 98, as_of='2026-04-01')},   # stale
    'AIPW': {'sector': 'Industrials', 'industry': 'Electrical Equipment',
             'exposure_weights': {'ai_capex_cycle': 0.5, 'power_grid_capex': 0.8, 'rates': 0.3},
             'thesis_drivers': ['Hyperscaler AI capex keeps growing', 'Grid upgrade cycle']},                 # no casefile
    'BRND': {'sector': 'Consumer Discretionary', 'industry': 'Apparel', 'exposures': ['consumer_spending', 'china'],
             'exposure_weights': {'consumer_spending': 0.9, 'china': 0.2}, 'thesis_drivers': ['Brand pricing power'],
             'casefile_summary': cf('quality_but_expensive', 0.08, 70, 105, 140, 98, previous_verdict='attractive_at_price')},
    'UTLX': {'sector': 'Utilities', 'industry': 'Electric Utilities', 'exposure_weights': {'rates': 0.7, 'power_grid_capex': 0.2},
             'thesis_drivers': ['Regulated rate base growth'],
             'casefile_summary': cf('attractive_at_price', 0.10, 52, 70, 85, 58, risk='low')},                 # 50 < bear 52
    'IDXF': {'sector': 'Broad market', 'diversified': True,
             'exposure_weights': {'ai_capex_cycle': 0.3, 'consumer_spending': 0.3, 'rates': 0.3}},
}

SCENARIOS = {
    'ai_capex_slowdown': {'shocks': {'ai_capex_cycle': -0.4, 'semis_supply_chain': -0.1},
                          'mechanism': 'hyperscalers cut capex 30%; GPU and foundry orders fall; power-equipment orders deferred'},
    'rates_up': {'rates': -0.2, 'consumer_spending': -0.1},          # no mechanism stated on purpose
}


def book():
    return from_ib(synthetic_positions(), SUMMARY, AS_OF)


def analysis(**opts):
    return analyze(book(), INFO, {'scenarios': SCENARIOS, **opts})


def walk_keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_keys(v)


class Book(unittest.TestCase):
    def test_parse_weights_and_reconciliation(self):
        b = book()
        self.assertEqual(b['base_currency'], 'USD')
        self.assertEqual(len(b['positions']), 6)
        w = {p['symbol']: p['weight'] for p in b['positions']}
        self.assertAlmostEqual(w['SEMA'], 0.21)
        self.assertAlmostEqual(w['IDXF'], 0.25)
        self.assertAlmostEqual(b['cash_weight'], 0.12)
        r = b['reconciliation']
        self.assertEqual(r['status'], 'reconciled')
        self.assertAlmostEqual(r['difference'], 0.0)
        self.assertAlmostEqual(r['gross_position_check']['difference'], 0.0)
        sema = next(p for p in b['positions'] if p['symbol'] == 'SEMA')
        self.assertAlmostEqual(sema['cost_basis'], 1050 * 120.0)
        self.assertEqual(sema['cost_basis_method'], 'market_value_minus_unrealized_pnl')
        self.assertTrue(b['private'] and b['read_only'])

    def test_unreconciled_difference_is_explained_not_hidden(self):
        s = dict(SUMMARY, netliquidation={'amount': 1050000.0, 'currency': 'USD'})
        r = from_ib(synthetic_positions(), s, AS_OF)['reconciliation']
        self.assertEqual(r['status'], 'unreconciled')
        self.assertAlmostEqual(r['difference'], 50000.0)
        self.assertAlmostEqual(r['difference_pct'], 50000 / 1050000)
        self.assertTrue(r['possible_reasons'])
        # accruals in the summary explain the gap
        s2 = dict(s, accrueddividend={'amount': 50000.0})
        r2 = from_ib(synthetic_positions(), s2, AS_OF)['reconciliation']
        self.assertEqual(r2['status'], 'reconciled')
        self.assertAlmostEqual(r2['difference_after_accruals'], 0.0)

    def test_missing_net_liquidation(self):
        b = from_ib(synthetic_positions(), {}, AS_OF)
        self.assertEqual(b['reconciliation']['status'], 'not_checked')
        self.assertEqual(b['weight_basis'], 'positions_plus_cash')
        self.assertIsNone(b['cash'])

    def test_tolerant_odd_payload_shapes(self):
        pos = {
            'U1111111': [   # synthetic account codes as dict keys
                {'symbol': 'AAAA', 'conid': '7', 'quantity': '1,000', 'market_price': '$10.00', 'market_value': {'amount': '10,000.00'},
                 'average_cost': 8, 'currency': 'usd', 'sec_type': 'STOCK'},
                'garbage row',
                {'description': None, 'foo': 1},
                {'ticker': 'GONE', 'conid': 9, 'position': 0, 'mktPrice': 5, 'mktValue': 0, 'assetClass': 'STK'},
                {'ticker': 'AAAA OPT', 'conid': 8, 'position': 2, 'mktPrice': 5.5, 'multiplier': 100, 'assetClass': 'OPT',
                 'undSym': 'AAAA', 'currency': 'USD'},
                {'ticker': 'USD', 'assetClass': 'CASH', 'position': 5000, 'currency': 'USD'},
            ],
            'U2222222': [
                {'symbol': 'AAAA', 'conid': 7, 'position': 500, 'mktPrice': 10, 'mktValue': 5000, 'avgCost': 8, 'currency': 'USD', 'assetClass': 'STK'},
                {'ticker': 'EURO', 'conid': 11, 'position': 100, 'mktPrice': 20, 'mktValue': 2000, 'currency': 'EUR', 'assetClass': 'STK'},
            ],
        }
        summ = [{'tag': 'NetLiquidation', 'value': '23,300', 'currency': 'USD'}, {'tag': 'AccountCode', 'value': 'U1111111'}]
        b = from_ib(json.dumps(pos), summ, AS_OF)
        syms = {p['symbol']: p for p in b['positions']}
        self.assertEqual(syms['AAAA']['quantity'], 1500)                       # merged across accounts by conid
        self.assertAlmostEqual(syms['AAAA']['value'], 15000.0)
        self.assertAlmostEqual(syms['AAAA']['cost_basis'], 12000.0)
        self.assertEqual(syms['AAAA']['merged_rows'], 2)
        opt = syms['AAAA OPT']
        self.assertAlmostEqual(opt['value'], 1100.0)
        self.assertEqual(opt['value_method'], 'quantity_x_price')
        self.assertEqual(opt['underlying'], 'AAAA')
        self.assertTrue(any('delta' in n for n in opt['notes']))
        self.assertIsNone(syms['EURO']['value'])                               # no FX rate: excluded, not guessed
        self.assertEqual(b['reconciliation']['unconverted'][0]['symbol'], 'EURO')
        self.assertEqual(b['cash'], 5000.0)                                    # from cash rows (summary has none)
        self.assertEqual(b['cash_source'] if 'cash_source' in b else b['reconciliation']['cash_source'], 'positions_cash_rows')
        self.assertEqual(len(b['unparsed']), 2)
        self.assertEqual(b['skipped'][0]['reason'], 'zero quantity (closed position row)')
        self.assertNotIn('U1111111', json.dumps(b))
        self.assertNotIn('U2222222', json.dumps(b))
        # positions 15000 + 1100 (EURO unconverted) + cash 5000 = 21100 vs NLV 23300 -> unreconciled, reasons name FX
        self.assertEqual(b['reconciliation']['status'], 'unreconciled')
        self.assertTrue(any('FX' in r for r in b['reconciliation']['possible_reasons']))
        b2 = from_ib(pos, summ, AS_OF, fx={'EUR': 1.1})
        self.assertAlmostEqual({p['symbol']: p for p in b2['positions']}['EURO']['value'], 2200.0)

    def test_symbol_keyed_dict_and_plain_list(self):
        b = from_ib({'ZZZZ': {'position': 10, 'mktPrice': 3, 'assetClass': 'STK'}}, {'netliquidation': 30})
        self.assertEqual(b['positions'][0]['symbol'], 'ZZZZ')
        self.assertAlmostEqual(b['positions'][0]['weight'], 1.0)
        self.assertIn('as_of not supplied: staleness checks cannot run', b['warnings'])
        self.assertEqual(from_ib([], None)['positions'], [])
        self.assertEqual(from_ib('not json', None)['unparsed'][0]['reason'], 'text is not JSON')

    def test_client_portal_style_row_and_ib_sector_fallback(self):
        r = {'acctId': FAKE_ACCT, 'conid': 424242, 'contractDesc': 'QQQQ', 'position': 10.0, 'mktPrice': 50.0, 'mktValue': 500.0,
             'currency': 'USD', 'avgCost': 40.0, 'avgPrice': 40.0, 'realizedPnl': 0.0, 'unrealizedPnl': 100.0, 'assetClass': 'STK',
             'ticker': 'QQQQ', 'undConid': 0, 'sector': 'Technology', 'group': 'Software'}
        b = from_ib([r], {'netliquidation': {'amount': 1000.0, 'currency': 'USD', 'isNull': False},
                          'totalcashvalue-s': {'amount': 500.0, 'currency': 'USD'}}, AS_OF)
        p = b['positions'][0]
        self.assertEqual((p['symbol'], p['conid'], p['cost_basis'], p['notes']), ('QQQQ', '424242', 400.0, []))
        self.assertEqual(b['cash'], 500.0)                                    # segment value used only because no total exists
        self.assertEqual(b['reconciliation']['status'], 'reconciled')
        h = analyze(b, {}, {})['holdings']['QQQQ']
        self.assertEqual((h['sector'], h['sector_source'], h['industry']), ('Technology', 'ib_position', 'Software'))
        self.assertAlmostEqual(h['tax']['unrealized_gain_fraction'], 0.2)

    def test_no_account_identifier_in_book(self):
        self.assertNotIn(FAKE_ACCT, json.dumps(book()))

    def test_ib_themes_to_tags(self):
        self.assertEqual(tags_from_themes({'themes': [{'name': 'Artificial Intelligence'}, 'Data Centers', {'title': 'Data centers'}]}),
                         ['artificial_intelligence', 'data_centers'])

    def test_ib_linked_themes_wrapper_is_unwrapped(self):
        # shape of the IB connector's get_company_themes payload (synthetic names)
        self.assertEqual(tags_from_themes({'linked_themes': [{'name': 'AI Chips'}]}), ['ai_chips'])
        payload = {'linked_themes': [{'name': 'AI Chips', 'key': 'k1', 'description': 'synthetic',
                                      'linked_companies': [{'rank': 1, 'name': 'Synthetic Co', 'symbol': 'SYNT'}], 'total_count': 1},
                                     {'name': 'Data Centers', 'key': 'k2', 'linked_companies': [], 'total_count': 0}]}
        self.assertEqual(tags_from_themes(payload), ['ai_chips', 'data_centers'])
        self.assertEqual(tags_from_themes(json.dumps(payload)), ['ai_chips', 'data_centers'])
        self.assertEqual(tags_from_themes({'data': {'linked_themes': [{'name': 'Edge AI'}]}}), ['edge_ai'])
        self.assertEqual(tags_from_themes({'payload': [{'theme': 'Edge AI'}], 'count': 1}), ['edge_ai'])   # single list wrapper
        self.assertEqual(tags_from_themes({'a': [{'name': 'X'}], 'b': [{'name': 'Y'}]}), [])            # ambiguous: not guessed


class Analyze(unittest.TestCase):
    def setUp(self):
        self.a = analysis()

    def test_json_safe(self):
        json.dumps(self.a, allow_nan=False)

    def test_concentration(self):
        c = self.a['concentration']
        self.assertEqual(c['top1']['holding'], 'SEMA')
        self.assertAlmostEqual(c['top1']['weight'], 0.21)
        self.assertEqual([x['holding'] for x in c['over_single_name_cap']], ['SEMA'])
        # index fund counts as fully spread: hhi over single names / gross 0.88
        expect = sum((w / 0.88) ** 2 for w in (0.21, 0.10, 0.08, 0.14, 0.10))
        self.assertAlmostEqual(c['hhi'], expect)
        self.assertAlmostEqual(c['effective_n'], 1 / expect)
        self.assertAlmostEqual(c['diversified_weight'], 0.25)

    def test_economic_exposure_not_names(self):
        ai = self.a['exposures']['by_tag']['ai_capex_cycle']
        self.assertAlmostEqual(ai['exposure'], 0.21 * 0.8 + 0.10 * 0.6 + 0.08 * 0.5 + 0.25 * 0.3)
        self.assertAlmostEqual(ai['name_weight'], 0.21 + 0.10 + 0.08 + 0.25)
        self.assertTrue(ai['above_single_name_cap'])
        self.assertEqual(ai['assumed_share'], 0.0)
        sectors = self.a['exposures']['by_sector']
        self.assertAlmostEqual(sectors['Technology']['weight'], 0.31)
        self.assertAlmostEqual(self.a['exposures']['by_counterparty']['HyperscalerCo']['exposure'], 0.21 * 0.5 + 0.10 * 0.3)

    def test_ai_cluster_overlap_hidden_by_sector_labels(self):
        # ai_capex_cycle is carried by SEMA, SEMB, AIPW = 0.39 of 0.63 single-name weight (> half), but inside those carriers
        # there is only one separate driver group (semis: SEMA+SEMB): it is the book's dominant driver, not a broad factor
        ov = self.a['overlaps']
        self.assertEqual(ov['common_factors'], [])
        cl = ov['clusters']
        ai = next(c for c in cl if 'SEMA' in c['holdings'])
        self.assertEqual(set(ai['holdings']), {'SEMA', 'SEMB', 'AIPW'})          # index fund does not chain clusters
        self.assertTrue(ai['hidden_by_sector_labels'])
        self.assertEqual(ai['defining_tag'], 'ai_capex_cycle')
        self.assertAlmostEqual(ai['shared_tags']['ai_capex_cycle'], 0.21 * 0.8 + 0.10 * 0.6 + 0.08 * 0.5)
        self.assertEqual(ai['dominant_tag'], 'semis_supply_chain')                 # SEMA+SEMB alone: 0.21*0.9 + 0.10*1.0
        self.assertAlmostEqual(ai['dominant_exposure'], 0.289)
        self.assertAlmostEqual(ai['net_dominant_exposure'], 0.289)               # no opposite-direction holdings
        self.assertEqual(ai['offsets'], [])
        self.assertTrue(ai['above_single_name_cap'])
        shared = {r['tag']: r for r in ov['shared_tags']}
        self.assertIn('IDXF', shared['ai_capex_cycle']['holdings'])             # but its look-through exposure counts
        self.assertEqual(ai['shared_tags'].keys(), {'ai_capex_cycle', 'semis_supply_chain'})
        rates = next(c for c in cl if c['defining_tag'] == 'rates')
        self.assertEqual(set(rates['holdings']), {'UTLX', 'AIPW'})               # AIPW in both; no chaining UTLX into AI
        self.assertFalse(rates['above_single_name_cap'])
        self.assertFalse(any('IDXF' in c['holdings'] for c in cl))               # a fund never defines a cluster
        # driver_tags is then a no-op for this tag
        self.assertEqual(analysis(driver_tags=['ai_capex_cycle'])['overlaps']['clusters'], cl)

    def test_listed_factor_reports_exposure_and_does_not_link(self):
        a = analysis(factor_tags=['ai_capex_cycle'])
        ov = a['overlaps']
        ai = {f['tag']: f for f in ov['common_factors']}['ai_capex_cycle']
        self.assertEqual(ai['basis'], 'listed in options.factor_tags')
        self.assertEqual(set(ai['holdings']), {'SEMA', 'SEMB', 'AIPW'})          # the index fund is look-through, not a carrier
        self.assertAlmostEqual(ai['carrier_weight_share'], 0.39 / 0.63)
        self.assertAlmostEqual(ai['exposure'], 0.21 * 0.8 + 0.10 * 0.6 + 0.08 * 0.5 + 0.25 * 0.3)
        self.assertAlmostEqual(ai['look_through_exposure'], 0.25 * 0.3)
        self.assertTrue(ai['hidden_by_sector_labels'] and ai['above_single_name_cap'])
        cl = ov['clusters']
        self.assertNotIn('ai_capex_cycle', {t for c in cl for t in c['shared_tags']})
        semis = next(c for c in cl if c['defining_tag'] == 'semis_supply_chain')
        self.assertEqual(set(semis['holdings']), {'SEMA', 'SEMB'})
        shared = {r['tag']: r for r in ov['shared_tags']}
        self.assertTrue(shared['ai_capex_cycle']['common_factor'])
        self.assertFalse(shared['semis_supply_chain']['common_factor'])
        f = a['scenarios']['ai_capex_slowdown']['by_common_factor'][0]
        self.assertEqual(f['tag'], 'ai_capex_cycle')
        self.assertAlmostEqual(f['loss'], -(0.21 * 0.32 + 0.10 * 0.24 + 0.08 * 0.2 + 0.25 * 0.12))   # incl. fund look-through
        self.assertGreater(f['share_of_loss'], 0.75)
        # carriers of a common factor above the cap keep a per-holding review trigger
        rv = {r['holding']: {t['code'] for t in r['triggers']} for r in a['review_needed']}
        for k in ('SEMA', 'SEMB', 'AIPW'):
            self.assertIn('carries_common_factor_above_cap', rv[k])
        self.assertNotIn('carries_common_factor_above_cap', rv.get('BRND', set()))

    def test_factor_tag_options(self):
        a = analysis(factor_tags=['rates'])
        f = {x['tag']: x for x in a['overlaps']['common_factors']}
        self.assertEqual(f['rates']['basis'], 'listed in options.factor_tags')
        self.assertAlmostEqual(f['rates']['exposure'], 0.08 * 0.3 + 0.10 * 0.7 + 0.25 * 0.3)
        self.assertFalse(any(c['defining_tag'] == 'rates' or 'rates' in c['shared_tags'] for c in a['overlaps']['clusters']))
        self.assertIn('rates', a['overlaps']['method']['common_factor_tags'])
        with self.assertRaises(ValueError):
            analysis(factor_tags=['rates'], driver_tags=['rates'])
        with self.assertRaises(ValueError):
            analysis(factor_tags='rates')
        with self.assertRaises(ValueError):
            analysis(factor_weight_share=1.5)

    def test_scenario_concentration(self):
        s = self.a['scenarios']['ai_capex_slowdown']
        impacts = {r['holding']: r['impact'] for r in s['holdings']}
        self.assertAlmostEqual(impacts['SEMA'], -0.4 * 0.8 - 0.1 * 0.9)
        self.assertAlmostEqual(impacts['BRND'], 0.0)
        self.assertIn('BRND', s['no_mapped_exposure'])
        total = 0.21 * -0.41 + 0.10 * -0.34 + 0.08 * -0.2 + 0.25 * -0.12
        self.assertAlmostEqual(s['portfolio_impact'], total)
        self.assertEqual(s['holdings'][0]['holding'], 'SEMA')
        ai = s['by_cluster'][0]
        self.assertEqual(ai['defining_tag'], 'ai_capex_cycle')                  # shocks on its shared tags (ai + semis)
        self.assertAlmostEqual(ai['loss'], 0.21 * -0.41 + 0.10 * -0.34 + 0.08 * -0.2)
        self.assertGreater(ai['share_of_loss'], 0.75)
        self.assertEqual(s['by_common_factor'], [])
        self.assertEqual(self.a['scenarios']['rates_up']['warnings'][0][:21], 'mechanism not stated:')
        own = self.a['scenarios']['simultaneous_own_bear_cases']
        self.assertAlmostEqual({r['holding']: r['impact'] for r in own['holdings']}['SEMA'], 120 / 200 - 1)
        self.assertGreater(own['unknown_weight'], 0)       # AIPW / IDXF have no bear value: unknown, not zero

    def test_explicit_sensitivity_overrides_tags(self):
        info = json.loads(json.dumps(INFO))
        info['BRND']['scenario_sensitivity'] = {'ai_capex_slowdown': -0.05}
        s = analyze(book(), info, {'scenarios': SCENARIOS})['scenarios']['ai_capex_slowdown']
        r = next(x for x in s['holdings'] if x['holding'] == 'BRND')
        self.assertEqual((r['impact'], r['source']), (-0.05, 'holding_estimate'))

    def test_drivers_aggregate_by_weight(self):
        d = self.a['drivers']['thesis_drivers'][0]
        self.assertEqual(d['item'].lower(), 'hyperscaler ai capex keeps growing')
        self.assertAlmostEqual(d['weight'], 0.21 + 0.10 + 0.08)
        self.assertEqual(self.a['drivers']['top_exposures'][0]['tag'], 'ai_capex_cycle')

    def test_review_triggers(self):
        rv = {r['holding']: {t['code'] for t in r['triggers']} for r in self.a['review_needed']}
        self.assertIn('price_moved', rv['SEMA'])
        self.assertIn('above_single_name_cap', rv['SEMA'])
        self.assertIn('in_cluster_above_cap', rv['SEMA'])
        self.assertIn('stale_analysis', rv['SEMB'])
        self.assertIn('no_analysis', rv['AIPW'])
        self.assertIn('verdict_changed', rv['BRND'])
        self.assertIn('below_bear_value', rv['UTLX'])
        idxf = next(r for r in self.a['review_needed'] if r['holding'] == 'IDXF')
        self.assertEqual(idxf['priority'], 'low')
        self.assertEqual(self.a['review_needed'][0]['priority'], 'high')

    def test_review_lookahead_alert_and_currency(self):
        info = json.loads(json.dumps(INFO))
        info['SEMB']['casefile_summary']['as_of'] = '2026-11-01'
        info['AIPW']['monitor_alerts'] = [{'code': 'gross_margin_break'}]
        info['UTLX']['casefile_summary']['currency'] = 'EUR'
        rv = {r['holding']: {t['code'] for t in r['triggers']} for r in analyze(book(), info, {})['review_needed']}
        self.assertIn('analysis_after_book_date', rv['SEMB'])
        self.assertIn('business_changed', rv['AIPW'])
        self.assertIn('price_currency_mismatch', rv['UTLX'])
        self.assertNotIn('below_bear_value', rv['UTLX'])

    def test_assumed_full_exposure_is_labelled(self):
        info = {'SEMA': {'sector': 'Technology', 'exposures': ['ai_capex_cycle']}}
        a = analyze(book(), info, {})
        t = a['exposures']['by_tag']['ai_capex_cycle']
        self.assertEqual(t['holdings'][0]['source'], 'assumed_full')
        self.assertEqual(t['assumed_share'], 1.0)
        self.assertEqual(set(a['exposures']['unclassified']), {'SEMB', 'AIPW', 'BRND', 'UTLX', 'IDXF'})

    def test_same_sector_label_different_drivers(self):
        info = {'X1': {'sector': 'Consumer', 'exposure_weights': {'travel_demand': 0.9}},
                'X2': {'sector': 'Consumer', 'exposure_weights': {'grocery_staples': 0.9}}}
        b = from_ib([row('X1', 1, 10, 10.0), row('X2', 2, 10, 10.0)], {'netliquidation': 200}, AS_OF)
        a = analyze(b, info, {})
        self.assertEqual(a['overlaps']['clusters'], [])
        self.assertEqual(a['overlaps']['same_label_different_drivers'][0]['holdings'], ['X1', 'X2'])

    def test_issuer_grouping_and_options_line(self):
        pos = [row('CLSA', 1, 100, 10.0), row('CLSB', 2, 100, 10.0),
               {'ticker': 'CLSA 261218C', 'conid': 3, 'position': 1, 'mktPrice': 2.0, 'mktValue': 200.0, 'assetClass': 'OPT', 'undSym': 'CLSA'}]
        info = {'CLSA': {'issuer': 'CLS', 'sector': 'Tech'}, 'CLSB': {'issuer': 'CLS', 'sector': 'Tech'}}
        a = analyze(from_ib(pos, {'netliquidation': 2200}, AS_OF), info, {'single_name_cap': 0.5})
        self.assertAlmostEqual(a['weights']['holdings']['CLS'], 1.0)
        self.assertTrue(a['holdings']['CLS']['derivative'])
        self.assertEqual(a['params']['single_name_cap_source'], 'options')

    def test_empty_book(self):
        a = analyze(from_ib([], {'netliquidation': 100, 'totalcashvalue': 100}, AS_OF), {}, {})
        self.assertIsNone(a['concentration']['top1'])
        self.assertEqual(a['review_needed'], [])

    def test_bad_exposure_weight_rejected(self):
        with self.assertRaises(ValueError):
            analyze(book(), {'SEMA': {'exposure_weights': {'ai_capex_cycle': 1.7}}}, {})


ALT = {'index': {'symbol': 'IDXF', 'expected_return': 0.08, 'basis': 'synthetic: earnings yield + nominal growth',
                 'exposures': {'ai_capex_cycle': 0.3, 'consumer_spending': 0.3, 'rates': 0.3}},
       'cash': {'yield': 0.04},
       'holdings': {}}

DUP = {'symbol': 'SEMC', 'base_irr': 0.12, 'bear_value': 50, 'price': 100, 'risk_level': 'high', 'quality': 'strong',
       'verdict': 'attractive_at_price', 'exposure_weights': {'ai_capex_cycle': 0.9, 'semis_supply_chain': 0.9}}
DIV = {'symbol': 'WATR', 'base_irr': 0.15, 'downside_to_bear': -0.25, 'risk_level': 'medium', 'quality': 'adequate',
       'verdict': 'attractive_at_price', 'exposure_weights': {'water_infrastructure': 0.9, 'rates': 0.2}}


def by(out, code, target=None):
    return [o for o in out['options'] if o['option'] == code and (target is None or o['target'] == target)]


class Compare(unittest.TestCase):
    def setUp(self):
        self.a = analysis()

    def test_duplicate_vs_diversifying_candidate(self):
        dup, div = compare(DUP, self.a, ALT), compare(DIV, self.a, ALT)
        self.assertEqual(dup['diversification']['new_tags'], [])
        self.assertEqual(dup['diversification']['reading'], 'mostly more of exposures already held')
        self.assertIn('water_infrastructure', div['diversification']['new_tags'])
        self.assertEqual(div['diversification']['reading'], 'adds mostly new sources of return')
        add = by(dup, 'add_new')[0]
        ai = next(t for t in add['risk_change']['tag_changes'] if t['tag'] == 'ai_capex_cycle')
        self.assertAlmostEqual(ai['change'], 0.03 * 0.9)
        self.assertTrue(ai['above_single_name_cap_after'])
        self.assertTrue(any('ai_capex_cycle' in r and 'cap' in r for r in add['reasons_against']))
        # scenario: the duplicate deepens the AI slowdown loss, the diversifier does not
        ch = add['risk_change']['scenario_changes']['ai_capex_slowdown']['change']
        self.assertAlmostEqual(ch, 0.03 * (-0.4 * 0.9 - 0.1 * 0.9))
        self.assertAlmostEqual(by(div, 'add_new')[0]['risk_change']['scenario_changes']['ai_capex_slowdown']['change'], 0.0)
        self.assertAlmostEqual(by(div, 'add_new')[0]['expected_return']['portfolio_delta'], 0.03 * (0.15 - 0.04))
        self.assertAlmostEqual(dup['candidate']['downside_to_bear'], -0.5)

    def test_replace_with_embedded_gain_shows_tax_hurdle(self):
        out = compare(DIV, self.a, ALT)
        rep = by(out, 'replace', 'SEMA')[0]
        tax = rep['tax']
        self.assertEqual(tax['status'], 'assessed')
        g, t, r, H = 80.0 / 200.0, 0.25, 0.09, 5
        keep = (1 + r) ** H - t * ((1 + r) ** H - (1 - g))
        s = 1 - t * g
        expect = ((keep / s - t) / (1 - t)) ** (1 / H) - 1
        self.assertAlmostEqual(tax['unrealized_gain_fraction'], g)
        self.assertAlmostEqual(tax['after_tax_hurdle'], expect, places=6)
        self.assertGreater(tax['after_tax_hurdle'], tax['pre_tax_hurdle'])
        self.assertAlmostEqual(tax['immediate_tax_share_of_portfolio'], t * g * 0.03)
        self.assertTrue(tax['candidate_clears_after_tax'])
        self.assertAlmostEqual(rep['expected_return']['portfolio_delta'], 0.03 * (0.15 - 0.09))
        # no cost data -> tax not assessed (never guessed)
        self.assertEqual(by(out, 'replace', 'BRND')[0]['tax']['status'], 'not_assessed')
        utl = by(out, 'replace', 'UTLX')[0]['tax']
        self.assertAlmostEqual(utl['after_tax_hurdle'], 0.10, places=6)      # loss: nothing realized, hurdle = holding IRR
        self.assertTrue(any('realized loss' in c for c in utl['caveats']))

    def test_candidate_must_clear_hurdle_or_reason_against(self):
        weak = dict(DIV, base_irr=0.095)
        rep = by(compare(weak, self.a, ALT), 'replace', 'SEMA')[0]
        self.assertFalse(rep['tax']['candidate_clears_after_tax'])
        self.assertTrue(any('after 25% tax' in r for r in rep['reasons_against']))

    def test_options_set_index_cash_nothing(self):
        out = compare(DIV, self.a, ALT)
        codes = {o['option'] for o in out['options']}
        self.assertTrue({'add_new', 'add_to_existing', 'replace', 'index', 'hold_cash', 'do_nothing'} <= codes)
        idx = by(out, 'index')[0]
        self.assertEqual(idx['target'], 'IDXF')
        self.assertAlmostEqual(idx['expected_return']['portfolio_delta'], 0.03 * 0.04)
        self.assertAlmostEqual(out['comparisons']['candidate_vs_index']['excess'], 0.07)
        nothing = by(out, 'do_nothing')[0]
        self.assertTrue(any('SEMA above the single-name cap' in r for r in nothing['reasons_against']))
        self.assertAlmostEqual(out['comparisons']['excess_return_per_unit_bear_loss']['candidate'], 0.11 / 0.25)

    def test_unsupported_verdict_is_a_reason_not_a_filter(self):
        out = compare(dict(DIV, verdict='quality_but_expensive'), self.a, ALT)
        add = by(out, 'add_new')[0]
        self.assertTrue(any('quality_but_expensive' in r for r in add['reasons_against']))
        self.assertTrue(by(out, 'do_nothing')[0]['reasons_for'])

    def test_candidate_already_held_offers_add_and_trim(self):
        out = compare({'symbol': 'SEMA', 'base_irr': 0.09, 'bear_value': 120, 'price': 200, 'verdict': 'quality_but_expensive',
                       'exposure_weights': INFO['SEMA']['exposure_weights']}, self.a, ALT)
        self.assertTrue(out['candidate']['held'])
        self.assertTrue(by(out, 'add_to_existing', 'SEMA'))
        trim = by(out, 'trim', 'SEMA')[0]
        self.assertEqual(trim['tax']['status'], 'assessed')
        self.assertLess(trim['risk_change']['after']['top1']['weight'], 0.21)
        self.assertNotIn('SEMA', trim['risk_change']['after']['over_single_name_cap'])

    def test_low_cash_flags_funding(self):
        a = analyze(from_ib(synthetic_positions(), dict(SUMMARY, totalcashvalue={'amount': 10000.0}, netliquidation={'amount': 890000.0}), AS_OF),
                    INFO, {'scenarios': SCENARIOS})
        add = by(compare(DIV, a, ALT), 'add_new')[0]
        self.assertTrue(any('requires a sale or margin' in r for r in add['reasons_against']))
        self.assertEqual(add['funding']['mode'], 'cash+pro_rata_sale')
        self.assertAlmostEqual(add['funding']['from_cash'], 10000 / 890000)
        self.assertAlmostEqual(sum(add['illustrative_weight_change'].values()), 10000 / 890000)   # net new money = available cash
        self.assertLessEqual(add['invested']['gross_after'], 1 + 1e-9)
        self.assertFalse(add['invested']['uses_margin_after'])
        self.assertIn('cash is insufficient', add['label'])

    def test_no_orders_anywhere(self):
        out = compare(DUP, self.a, ALT)
        keys = {_k(k) for k in walk_keys(out)} | {_k(k) for k in walk_keys(self.a)}
        self.assertFalse(keys & ORDER_KEYS)
        self.assertNotIn('quantity', {_k(k) for k in walk_keys(out)})        # sizes are weights, never share counts
        self.assertTrue(all(o['executable'] is False for o in out['options']))
        self.assertFalse({'recommendation', 'selected', 'best_option', 'action'} & set(walk_keys(out)))
        json.dumps(out, allow_nan=False)
        with open(portfolio.__file__, encoding='utf-8') as f:
            src = f.read()
        for forbidden in ('create_order', 'order_instruction(', 'mcp__', 'import requests', 'urllib'):
            self.assertNotIn(forbidden, src)
        with self.assertRaises(AssertionError):
            _no_orders({'options': [{'side': 'BUY', 'limit_price': 1}]})
        _no_orders({'by_tag': {'orders': {'exposure': 0.1}}})                  # a data key named like a field is fine

    def test_short_positions_are_not_funding_sources(self):
        b = from_ib([row('SHRT', 1, -100, 10.0), row('LONG', 2, 200, 10.0)], {'netliquidation': 2000, 'totalcashvalue': 1000}, AS_OF)
        a = analyze(b, {}, {})
        self.assertAlmostEqual(a['concentration']['gross_invested'], 1.5)
        self.assertAlmostEqual(a['concentration']['net_invested'], 0.5)
        out = compare({'symbol': 'NEW1', 'base_irr': 0.1}, a, {'cash': {'yield': 0.03}})
        self.assertEqual({o['target'] for o in out['options'] if o['option'] == 'replace'}, {'LONG'})

    def test_candidate_without_symbol_rejected(self):
        with self.assertRaises(ValueError):
            compare({'base_irr': 0.1}, self.a, ALT)

    def test_missing_alternatives_degrade_gracefully(self):
        out = compare({'symbol': 'NEWX'}, self.a, None)
        add = by(out, 'add_new')[0]
        self.assertIsNone(add['expected_return']['portfolio_delta'])
        self.assertTrue(any('no exposure tags stated' in r for r in add['reasons_against']))
        self.assertTrue(any('no_verdict' in r for r in add['reasons_against']))
        json.dumps(out, allow_nan=False)



class IbMcpShapeTests(unittest.TestCase):
    """The Interactive Brokers MCP connector names the ticker 'contract_description' (seen 2026-10-20)."""

    def test_contract_description_is_the_symbol(self):
        rows = {'positions': [
            {'contract_id': 1, 'contract_description': 'SEMA', 'position': 10, 'market_price': 50.0, 'market_value': 500.0,
             'currency': 'USD', 'average_price': 40.0, 'unrealized_pnl': 100.0, 'asset_class': 'STK'},
            {'contract_id': 2, 'contract_description': 'BRND', 'position': 5, 'market_price': 100.0, 'market_value': 500.0,
             'currency': 'USD', 'average_price': 110.0, 'unrealized_pnl': -50.0, 'asset_class': 'STK'}]}
        book = from_ib(rows, {'currency': 'USD', 'net_liquidation': 1000.0, 'total_cash_value': 0.0}, as_of='2026-10-19')
        self.assertEqual(sorted(p['symbol'] for p in book['positions']), ['BRND', 'SEMA'])
        self.assertEqual(book['reconciliation']['status'], 'reconciled')
        self.assertAlmostEqual(analyze(book)['concentration']['top1']['weight'], 0.5)
        for p in book['positions']:                       # the ticker is given by the payload, not inferred
            self.assertEqual(p['notes'], [])
            self.assertEqual(p['symbol_source'], 'contract_description')

    def test_confirm_ticker_note_only_when_inferred(self):
        rows = [{'contract_id': 3, 'contract_description': 'SEMA JAN27 50 C', 'position': 1, 'market_price': 2.0, 'market_value': 200.0,
                 'currency': 'USD', 'asset_class': 'OPT'},
                {'conid': 4, 'description': 'Synthetic Widgets Inc', 'position': 10, 'mktPrice': 10.0, 'mktValue': 100.0,
                 'currency': 'USD', 'assetClass': 'STK'},
                {'conid': 5, 'name': 'Widgetco', 'position': 10, 'mktPrice': 10.0, 'mktValue': 100.0, 'currency': 'USD', 'assetClass': 'STK'},
                {'conid': 6, 'ticker': 'TICK', 'description': 'Tick Corp', 'position': 10, 'mktPrice': 10.0, 'mktValue': 100.0,
                 'currency': 'USD', 'assetClass': 'STK'}]
        ps = {p['conid']: p for p in from_ib(rows, {'netliquidation': 500.0}, AS_OF)['positions']}
        note = 'symbol taken from the first word of the description: confirm the ticker'
        for conid, sym in (('3', 'SEMA'), ('4', 'SYNTHETIC'), ('5', 'WIDGETCO')):
            self.assertEqual(ps[conid]['symbol'], sym)
            self.assertEqual(ps[conid]['symbol_source'], 'inferred_from_description')
            self.assertIn(note, ps[conid]['notes'])
        self.assertEqual((ps['6']['symbol'], ps['6']['symbol_source']), ('TICK', 'payload_symbol'))
        self.assertNotIn(note, ps['6']['notes'])


class PrivacyAndFunds(unittest.TestCase):
    def test_account_key_never_becomes_symbol(self):
        p = {FAKE_ACCT: {'position': 10, 'mktPrice': 5.0, 'mktValue': 50.0, 'currency': 'USD', 'assetClass': 'STK'},
             'DU1234567': {'position': 1, 'mktPrice': 5.0, 'mktValue': 5.0, 'currency': 'USD', 'assetClass': 'STK'}}
        b = from_ib(p, {}, AS_OF)
        self.assertNotIn(FAKE_ACCT, json.dumps(b))
        self.assertNotIn('DU1234567', json.dumps(b))
        named = from_ib({'ABCD': {'position': 1, 'mktPrice': 5.0, 'mktValue': 5.0, 'currency': 'USD', 'assetClass': 'STK'}}, {}, AS_OF)
        self.assertEqual([x['symbol'] for x in named['positions']], ['ABCD'])

    def test_etf_without_info_is_diversified_and_stock_flagged(self):
        b = from_ib({'positions': [row('FUNDX', 2001, 100, 10.0, ac='ETF'), row('ONEX', 2002, 100, 10.0)]},
                    {'netliquidation': {'amount': 2000.0, 'currency': 'USD'}}, AS_OF)
        a = analyze(b, {}, {})
        self.assertTrue(a['holdings']['FUNDX']['diversified'])
        self.assertFalse(a['holdings']['ONEX']['diversified'])
        codes = {t['holding']: [c['code'] for c in t['triggers']] for t in a['review_needed']}
        self.assertIn('fund_status_unknown', codes.get('ONEX', []))
        self.assertNotIn('fund_status_unknown', codes.get('FUNDX', []))


# A synthetic book where one broad factor (long duration / rates) is carried by nearly every name while the
# company drivers form three separate groups. NLV 1,000,000: six driver names at 12% each, one consumer name
# at 8%, a broad index fund at 10% and 10% cash.
BROAD_INFO = {
    'AIC1': {'sector': 'Technology', 'exposure_weights': {'ai_capex_cycle': 0.8, 'rates_long_duration': 0.5}},
    'AIC2': {'sector': 'Industrials', 'exposure_weights': {'ai_capex_cycle': 0.7, 'rates_long_duration': 0.5}},
    'ADS1': {'sector': 'Communication', 'exposure_weights': {'digital_advertising': 0.8, 'rates_long_duration': 0.4}},
    'ADS2': {'sector': 'Communication', 'exposure_weights': {'digital_advertising': 0.7, 'rates_long_duration': 0.4}},
    'SWR1': {'sector': 'Technology', 'exposure_weights': {'enterprise_software': 0.8, 'rates_long_duration': 0.6}},
    'SWR2': {'sector': 'Technology', 'exposure_weights': {'enterprise_software': 0.7, 'rates_long_duration': 0.6}},
    'CONS': {'sector': 'Consumer', 'exposure_weights': {'consumer_spending': 0.9}},
    'BIDX': {'sector': 'Broad market', 'diversified': True, 'exposure_weights': {'rates_long_duration': 0.3, 'ai_capex_cycle': 0.2}},
}


def broad_book(cash=100000.0):
    rows = [row('AIC1', 3001, 1200, 100.0, avg=50.0, upnl=1200 * 50.0)]          # 50% embedded gain
    rows += [row(s, 3002 + i, 1200, 100.0) for i, s in enumerate(('AIC2', 'ADS1', 'ADS2', 'SWR1', 'SWR2'))]
    rows += [row('CONS', 3010, 800, 100.0), row('BIDX', 3011, 1000, 100.0, ac='ETF')]
    nlv = 900000.0 + cash
    return from_ib({'positions': rows}, {'netliquidation': {'amount': nlv, 'currency': 'USD'},
                                         'totalcashvalue': {'amount': cash, 'currency': 'USD'}}, AS_OF)


BROAD_SCEN = {'rates_up': {'shocks': {'rates_long_duration': -0.2}, 'mechanism': 'long yields rise; long-duration equities de-rate'}}
SOFT = {'symbol': 'NSWR', 'base_irr': 0.10, 'downside_to_bear': -0.3, 'verdict': 'attractive_at_price', 'quality': 'strong',
        'risk_level': 'medium', 'exposure_weights': {'enterprise_software': 0.8, 'rates_long_duration': 0.2}}


class BroadFactorDoesNotChainClusters(unittest.TestCase):
    def setUp(self):
        self.a = analyze(broad_book(), BROAD_INFO, {'scenarios': BROAD_SCEN})

    def test_driver_clusters_stay_separate(self):
        ov = self.a['overlaps']
        members = sorted(sorted(c['holdings']) for c in ov['clusters'])
        self.assertEqual(members, [['ADS1', 'ADS2'], ['AIC1', 'AIC2'], ['SWR1', 'SWR2']])
        self.assertEqual({c['defining_tag'] for c in ov['clusters']}, {'ai_capex_cycle', 'digital_advertising', 'enterprise_software'})
        self.assertFalse(any('rates_long_duration' in c['shared_tags'] for c in ov['clusters']))

    def test_common_factor_reported_with_total_exposure(self):
        self.assertNotIn('rates_long_duration', {c['defining_tag'] for c in self.a['overlaps']['clusters']})
        f = self.a['overlaps']['common_factors']
        self.assertEqual([x['tag'] for x in f], ['rates_long_duration'])
        r = f[0]
        self.assertEqual(set(r['holdings']), {'AIC1', 'AIC2', 'ADS1', 'ADS2', 'SWR1', 'SWR2'})
        self.assertAlmostEqual(r['carrier_weight_share'], 0.72 / 0.80)
        self.assertAlmostEqual(r['single_name_exposure'], 0.12 * (0.5 + 0.5 + 0.4 + 0.4 + 0.6 + 0.6))
        self.assertAlmostEqual(r['look_through_exposure'], 0.10 * 0.3)
        self.assertAlmostEqual(r['exposure'], 0.36 + 0.03)
        self.assertAlmostEqual(r['exposure'], self.a['exposures']['by_tag']['rates_long_duration']['exposure'])
        self.assertTrue(r['above_single_name_cap'])
        loss = self.a['scenarios']['rates_up']['by_common_factor'][0]
        self.assertEqual(loss['tag'], 'rates_long_duration')
        self.assertAlmostEqual(loss['loss'], -0.2 * 0.39)
        self.assertAlmostEqual(loss['share_of_loss'], 1.0)

    def test_pairs_and_caveats(self):
        ov = self.a['overlaps']
        pairs = {tuple(sorted(p['holdings'])): p for p in ov['pairs']}
        self.assertNotIn(('ADS1', 'AIC1'), pairs)                                # sharing only the common factor is not a link
        self.assertEqual(pairs[('AIC1', 'AIC2')]['shared_common_factors'], ['rates_long_duration'])
        caveats = ' '.join(ov['method']['caveats'])
        self.assertIn('single-linkage', caveats)
        self.assertIn('look-through', caveats)
        self.assertTrue(all('look-through' in t['note'] for t in ov['shared_tags']))


class CompareFundingAndAttribution(unittest.TestCase):
    def test_no_cash_add_is_funded_pro_rata_never_above_100pct(self):
        b = broad_book(cash=0.0)
        a = analyze(b, BROAD_INFO, {'scenarios': BROAD_SCEN})
        out = compare(SOFT, a, {'index': {'symbol': 'BIDX', 'expected_return': 0.07}, 'cash': {'yield': 0.04}})
        add = by(out, 'add_new')[0]
        self.assertAlmostEqual(sum(add['illustrative_weight_change'].values()), 0.0)   # no money that the book does not have
        self.assertEqual(add['funding']['mode'], 'pro_rata_sale')
        self.assertAlmostEqual(add['funding']['pro_rata_sale'], 0.03)
        self.assertLessEqual(add['invested']['gross_after'], 1 + 1e-9)
        self.assertFalse(add['invested']['uses_margin_after'])
        self.assertTrue(any('requires a sale or margin' in r for r in add['reasons_against']))
        # pro-rata across every other long holding, the index fund included
        w = {p['symbol']: p['weight'] for p in b['positions']}
        self.assertAlmostEqual(add['illustrative_weight_change']['AIC1'], -0.03 * w['AIC1'])
        self.assertAlmostEqual(add['illustrative_weight_change']['BIDX'], -0.03 * w['BIDX'])
        # the candidate's 0.2 long-duration exposure is below the book average (0.39 / 0.9 invested): exposure falls
        tc = next(t for t in add['risk_change']['tag_changes'] if t['tag'] == 'rates_long_duration')
        self.assertLess(tc['change'], 0)
        self.assertAlmostEqual(tc['added_position_exposure'], 0.2)
        self.assertFalse(any('raises rates_long_duration' in r for r in add['reasons_against']))
        self.assertTrue(any('lowers rates_long_duration' in r for r in add['reasons_for']))
        # the sale realizes AIC1's embedded gain; the other holdings have no cost basis
        tax = add['tax']
        self.assertEqual((tax['kind'], tax['status']), ('pro_rata_funding_sale', 'partially_assessed'))
        self.assertAlmostEqual(tax['immediate_tax_share_of_portfolio'], 0.25 * 0.5 * 0.03 * w['AIC1'])
        self.assertAlmostEqual(tax['sold_weight_without_cost_basis'], 0.03 * (1 - w['AIC1']))
        # sold holdings have no base IRR: the return effect is not invented
        self.assertIsNone(add['expected_return']['portfolio_delta'])
        self.assertIn('base IRR unknown', add['expected_return']['formula'])
        for code in ('add_new', 'index'):
            o = by(out, code)[0]
            self.assertLessEqual(o['invested']['gross_after'], 1 + 1e-9, code)
            self.assertLessEqual(sum(o['illustrative_weight_change'].values()), 1e-9, code)
        self.assertTrue(by(out, 'hold_cash')[0]['caveats'])
        json.dumps(out, allow_nan=False)

    def test_cash_funded_add_below_book_average_is_not_a_cap_reason(self):
        a = analyze(broad_book(), BROAD_INFO, {'scenarios': BROAD_SCEN})
        add = by(compare(SOFT, a, {'cash': {'yield': 0.04}}), 'add_new')[0]
        self.assertFalse(any('raises rates_long_duration' in r for r in add['reasons_against']))
        self.assertEqual(add['funding']['mode'], 'cash')
        tc = next(t for t in add['risk_change']['tag_changes'] if t['tag'] == 'rates_long_duration')
        self.assertAlmostEqual(tc['change'], 0.03 * 0.2)                         # absolute exposure rises: more is invested
        self.assertLess(tc['share_of_invested_after'], tc['share_of_invested_before'])
        self.assertTrue(any('rates_long_duration' in c and 'share of invested assets falls' in c for c in add['caveats']))
        # above the book average the warning stays, with the attribution
        hot = dict(SOFT, exposure_weights={'enterprise_software': 0.5, 'rates_long_duration': 0.9})
        add2 = by(compare(hot, a, {'cash': {'yield': 0.04}}), 'add_new')[0]
        r = [x for x in add2['reasons_against'] if 'raises rates_long_duration' in x]
        self.assertEqual(len(r), 1)
        self.assertIn('book average', r[0])

    def test_already_on_margin_is_labelled(self):
        b = from_ib({'positions': [row('LEV1', 4001, 1100, 100.0)]},
                    {'netliquidation': {'amount': 100000.0, 'currency': 'USD'}, 'totalcashvalue': {'amount': -10000.0, 'currency': 'USD'}}, AS_OF)
        a = analyze(b, {'LEV1': {'sector': 'Tech', 'exposure_weights': {'x_driver': 0.5}}}, {})
        out = compare({'symbol': 'NEWL', 'base_irr': 0.1, 'exposure_weights': {'y_driver': 0.5}}, a, {'cash': {'yield': 0.04}})
        add = by(out, 'add_new')[0]
        self.assertEqual(add['funding']['mode'], 'pro_rata_sale')
        self.assertTrue(any('margin in use' in c for c in add['caveats']))
        self.assertAlmostEqual(add['invested']['gross_after'], add['invested']['gross_before'])
        # nothing left to sell: the shortfall is margin and is named as such
        lone = from_ib({'positions': []}, {'netliquidation': {'amount': 100000.0}, 'totalcashvalue': {'amount': 100000.0}}, AS_OF)
        a2 = analyze(lone, {}, {})
        a2['weights']['cash'] = 0.0      # synthetic: no cash and no holdings
        add2 = by(compare({'symbol': 'NEWM', 'base_irr': 0.1}, a2, None), 'add_new')[0]
        self.assertEqual(add2['funding']['mode'], 'margin')
        self.assertTrue(add2['invested']['uses_margin_after'])
        self.assertTrue(any('uses margin' in r for r in add2['reasons_against']))


class CliEntryAndCompanyArg(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'fundamental_engine.cli', *args], capture_output=True, text=True,
                              cwd=ROOT, timeout=120)

    def test_module_entry_point_runs_main(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 2)                    # argparse: a command is required (was a silent exit 0)
        self.assertIn('usage', r.stderr)
        with tempfile.TemporaryDirectory() as d:
            pos = os.path.join(d, 'positions.json')
            with open(pos, 'w', encoding='utf-8') as f:
                json.dump(synthetic_positions(), f)
            r = self.run_cli('portfolio', pos, '--as-of', AS_OF)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(json.loads(r.stdout)['book']['positions']), 6)

    def test_json_object_arg(self):
        from fundamental_engine.cli import json_object_arg
        self.assertEqual(json_object_arg(' {"ticker": "SYNT"} ', '--company'), {'ticker': 'SYNT'})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'company.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'ticker': 'SYNT'}, f)
            self.assertEqual(json_object_arg(path, '--company'), {'ticker': 'SYNT'})
        with self.assertRaisesRegex(ValueError, 'inline JSON is invalid'):
            json_object_arg('{ticker: SYNT}', '--company')
        with self.assertRaisesRegex(ValueError, 'neither an existing file nor inline JSON'):
            json_object_arg('SYNT', '--company')
        with self.assertRaisesRegex(ValueError, 'Non-finite'):
            json_object_arg('{"x": NaN}', '--company')

    def test_pipeline_accepts_inline_company_json(self):
        from fundamental_engine import cli, pipeline
        seen = {}

        def fake(raw_dir, company, as_of):
            seen.update(company=company, as_of=as_of)
            raise ValueError('synthetic stop after the company argument was parsed')
        with tempfile.TemporaryDirectory() as d:
            for company, code in (('{"ticker": "SYNT", "currency": "USD"}', 2), ('{"ticker": ', 2)):
                seen.clear()
                argv = ['fundamental', 'pipeline', d, '--company', company, '--as-of', AS_OF, '--out', os.path.join(d, 'out')]
                with mock.patch.object(pipeline, 'factbook_from_run_dir', fake), mock.patch.object(sys, 'argv', argv), \
                        mock.patch('sys.stderr', new_callable=io.StringIO) as err:
                    with self.assertRaises(SystemExit) as ex:
                        cli.main()
                self.assertEqual(ex.exception.code, code)
                if company.endswith('}'):
                    self.assertEqual(seen, {'company': {'ticker': 'SYNT', 'currency': 'USD'}, 'as_of': AS_OF})
                else:
                    self.assertEqual(seen, {})
                    self.assertIn('--company: inline JSON is invalid', err.getvalue())



def mrow(sym, conid, value, gain=None, ac='STK', price=100.0, **kw):
    """Synthetic row by market value; gain = unrealized gain fraction of value (None: no cost data)."""
    r = {'ticker': sym, 'conid': conid, 'position': value / price, 'mktPrice': price, 'mktValue': value, 'currency': 'USD',
         'assetClass': ac, **kw}
    if gain is not None:
        r['unrealizedPnl'] = value * gain
    return r


def summ(nlv, cash):
    return {'netliquidation': {'amount': nlv, 'currency': 'USD'}, 'totalcashvalue': {'amount': cash, 'currency': 'USD'}}


def cfs(irr, bear, price=100.0):
    return {'verdict': 'attractive_at_price', 'base_irr': irr, 'bear_value': bear, 'base_value': 120, 'bull_value': 150,
            'price': price, 'as_of': '2026-10-01', 'quality': 'strong', 'risk_level': 'medium'}


CONNECTOR_ROWS = [{'contract_id': 1, 'contract_description': 'AAAA', 'position': 50, 'market_price': 100.0, 'market_value': 5000.0,
                   'currency': 'USD', 'asset_class': 'STK'},
                  {'contract_id': 2, 'contract_description': 'BBBB', 'position': 40, 'market_price': 100.0, 'market_value': 4000.0,
                   'currency': 'USD', 'asset_class': 'STK'}]
CONNECTOR_SUMMARY = {'currency': 'USD', 'net_liquidation': 10000.0, 'total_cash_value': 1000.0}


class AccountBlocksAndOrderRows(unittest.TestCase):
    """Payload shapes that must never put an account id, alias or holder name into the book (synthetic ids)."""

    def assert_real_rows_only(self, payload, secrets):
        b = from_ib(payload, CONNECTOR_SUMMARY, AS_OF)
        self.assertEqual(sorted(p['symbol'] for p in b['positions']), ['AAAA', 'BBBB'], payload)
        self.assertEqual(b['reconciliation']['status'], 'reconciled')
        text = json.dumps(b) + json.dumps(analyze(b))
        for secret in secrets:
            self.assertNotIn(secret, text)
        return b

    def test_account_block_with_a_total_is_a_wrapper(self):
        for key in ('U***4321', 'XXXX4321', 'Joint-IRA', 'All', 'U7654321', 'DU7654321'):
            self.assert_real_rows_only({key: {'market_value': 9000.0, 'positions': CONNECTOR_ROWS}}, [key])
            self.assert_real_rows_only({key: {'market_value': 9000.0, 'AAAA': {'position': 50, 'mktPrice': 100.0},
                                              'BBBB': {'position': 40, 'mktPrice': 100.0}}}, [key])
        self.assert_real_rows_only([{'acctId': 'U7654321', 'accountTitle': 'Jane Q Holder', 'name': 'Jane Q Holder',
                                     'market_value': 9000.0, 'positions': CONNECTOR_ROWS}], ['U7654321', 'Jane', 'JANE'])

    def test_account_level_row_and_non_ticker_keys_are_unparsed(self):
        acct_row = {'acctId': 'U7654321', 'accountTitle': 'Jane Q Holder', 'name': 'Jane Q Holder', 'market_value': 9000.0}
        b = from_ib([acct_row] + CONNECTOR_ROWS, CONNECTOR_SUMMARY, AS_OF)
        self.assertEqual(sorted(p['symbol'] for p in b['positions']), ['AAAA', 'BBBB'])
        self.assertIn('account-level row', b['unparsed'][0]['reason'])
        for key in ('U***4321', 'Joint-IRA', 'All', 'XXXX4321', 'acct 1'):
            b = from_ib({key: {'position': 10, 'mktPrice': 5.0, 'mktValue': 50.0, 'assetClass': 'STK'}}, {}, AS_OF)
            self.assertEqual(b['positions'], [], key)
            self.assertIn('not look like a ticker', b['unparsed'][0]['reason'])
            self.assertNotIn(key, json.dumps(b))
        b = from_ib({'ZZZZ': {'position': 10, 'mktPrice': 3, 'assetClass': 'STK'}}, {'netliquidation': 30})
        self.assertEqual((b['positions'][0]['symbol'], b['positions'][0]['symbol_source']), ('ZZZZ', 'dict_key'))

    def test_row_with_identifier_less_sub_rows_stays_a_row(self):
        r = dict(CONNECTOR_ROWS[0], lots=[{'quantity': 25, 'price': 80.0}, {'quantity': 25, 'price': 90.0}])
        b = from_ib({'positions': [r, CONNECTOR_ROWS[1]]}, CONNECTOR_SUMMARY, AS_OF)
        self.assertEqual(sorted(p['symbol'] for p in b['positions']), ['AAAA', 'BBBB'])
        self.assertEqual(b['unparsed'], [])

    def test_orders_payload_is_refused_not_held(self):
        orders = {'orders': [
            {'contract_id': 2, 'contract_description': 'CCCC', 'side': 'SELL', 'quantity': 50, 'order_type': 'LIMIT',
             'limit_price': 20.0, 'price': 20.0, 'status': 'Submitted', 'currency': 'USD', 'asset_class': 'STK'},
            {'conId': 3, 'symbol': 'DDDD', 'action': 'BUY', 'totalQuantity': 10, 'lmtPrice': 9.5, 'price': 9.5},
            {'contract_id_ex': '4', 'contract_description': 'EEEE', 'action': 'SELL', 'size': 10, 'price': 99.5},
            {'conid': 5, 'ticker': 'FFFF', 'position': 10, 'mktPrice': 5.0, 'status': 'Filled'}]}
        b = from_ib(orders, CONNECTOR_SUMMARY, AS_OF)
        self.assertEqual(b['positions'], [])
        self.assertEqual(len(b['unparsed']), 4)
        self.assertTrue(all('order-like row' in u['reason'] for u in b['unparsed']))
        self.assertTrue(any('order-like row(s) refused' in w for w in b['warnings']))
        # 'side': 'long' describes a position, not an order
        b = from_ib([dict(CONNECTOR_ROWS[0], side='long')], CONNECTOR_SUMMARY, AS_OF)
        self.assertEqual([p['symbol'] for p in b['positions']], ['AAAA'])

    def test_unparsed_rows_list_only_known_field_names(self):
        b = from_ib({'positions': [{'U7654321': 10, 'DU7654321': 5, 'XXXX4321': 1, 'position': 15, 'market_value': 1000.0}]},
                    CONNECTOR_SUMMARY, AS_OF)
        u = b['unparsed'][0]
        self.assertEqual((u['keys'], u['other_keys']), (['marketvalue', 'position'], 3))
        for secret in ('7654321', 'XXXX4321', 'xxxx4321'):
            self.assertNotIn(secret, json.dumps(b))


class OrderGuardAndPassThrough(unittest.TestCase):
    def test_order_words_as_data_names_are_fine(self):
        # tag, symbol, sector, scenario, counterparty and theme names are data; only fields are checked
        for word in sorted(ORDER_KEYS):
            syms = [f'{word.upper()[:10]}{i}' for i in range(4)]
            pos = [mrow(s, 9000 + i, 2000.0) for i, s in enumerate(syms)]
            info = {s: {'sector': word, 'exposure_weights': {word: 0.6, f'own_{i}': 0.4} if i < 2 else {f'own_{i}': 0.8},
                        'counterparties': {word: 0.5}, 'ib_themes': [word]} for i, s in enumerate(syms)}
            a = analyze(from_ib(pos, summ(10000.0, 2000.0), AS_OF), info,
                        {'scenarios': {word: {'shocks': {word: -0.2}, 'mechanism': 'synthetic'}}})
            self.assertEqual(a['overlaps']['pairs'][0]['shared'], {word: 0.6}, word)
            out = compare({'symbol': word.upper(), 'base_irr': 0.1, 'exposure_weights': {word: 0.5}}, a, {'cash': {'yield': 0.03}})
            json.dumps(out, allow_nan=False)
        with self.assertRaises(AssertionError):
            _no_orders({'options': [{'conId': 1, 'action': 'SELL', 'totalQuantity': 10, 'lmtPrice': 99.5}]})
        with self.assertRaises(AssertionError):
            _no_orders({'x': {'contract_id_ex': '1', 'action': 'SELL', 'size': 10}})

    def test_order_shaped_pass_through_fields_become_text(self):
        order = {'conId': 1, 'action': 'SELL', 'totalQuantity': 10, 'lmtPrice': 99.5, 'auxPrice': 0, 'outsideRth': True}
        b = from_ib(CONNECTOR_ROWS, CONNECTOR_SUMMARY, AS_OF)
        a = analyze(b, {'AAAA': {'exposure_weights': {'x': 1}, 'casefile_summary': dict(cfs(0.1, 70), verdict=order),
                                 'monitor_alerts': [order, {'code': 'margin_break', 'detail': 'gross margin -5pp', 'date': '2026-10-01',
                                                            'side': 'SELL'}, 'guidance cut']}},
                    {'scenarios': {'s': {'shocks': {'x': -0.1}, 'mechanism': order}}})
        alerts = next(t for r in a['review_needed'] for t in r['triggers'] if t['code'] == 'business_changed')['alerts']
        self.assertEqual(alerts, [{'detail': 'alert without code or detail'},
                                  {'code': 'margin_break', 'detail': 'gross margin -5pp', 'date': '2026-10-01'},
                                  {'detail': 'guidance cut'}])
        self.assertIsInstance(a['scenarios']['s']['mechanism'], str)
        self.assertIsInstance(a['holdings']['AAAA']['pricing']['verdict'], str)
        out = compare({'symbol': 'BBBB', 'base_irr': 0.1}, a,
                      {'index': {'symbol': 'IDX', 'expected_return': 0.07, 'basis': order}, 'cash': {'yield': 0.03}},
                      {'tax_rate_source': order})
        self.assertIsInstance(out['comparisons']['candidate_vs_index']['index_basis'], str)
        self.assertIsInstance(out['assumptions']['tax_rate_source'], str)
        keys = {_k(k) for k in walk_keys(out)} | {_k(k) for k in walk_keys(a)}
        self.assertFalse(keys & {'action', 'totalquantity', 'lmtprice', 'auxprice', 'outsiderth', 'side'})
        with self.assertRaises(ValueError):
            compare({'symbol': 'BBBB'}, a, None, {'horizon_years': 'five'})
        with self.assertRaises(ValueError):
            analyze(b, {}, {'review_age_days': '120'})

    def test_themes_only_from_theme_payloads(self):
        self.assertEqual(tags_from_themes({'contracts': [{'symbol': 'AAAA', 'name': 'Acme Holdings Inc', 'conid': 1}]}), [])
        self.assertEqual(tags_from_themes({'results': [{'symbol': 'AAAA', 'description': 'Acme', 'underlying_contract_id': 1}],
                                           'note': 'synthetic'}), [])
        self.assertEqual(tags_from_themes({'results': [{'symbol': 'AAAA', 'name': 'Acme Holdings Inc'}]}), [])
        self.assertEqual(tags_from_themes({'accounts': ['U7654321']}), [])
        self.assertEqual(tags_from_themes(['U7654321', 'Data Centers']), ['data_centers'])
        self.assertEqual(tags_from_themes({'account_id': 'U7654321', 'linked_themes': [{'name': 'AI Infrastructure'}]}),
                         ['ai_infrastructure'])
        self.assertEqual(tags_from_themes({'U7654321': {'linked_themes': [{'name': 'AI Infrastructure'}]}}), ['ai_infrastructure'])
        self.assertEqual(tags_from_themes({'linked_themes': []}), [])


class CapCrossingAndDriverClusters(unittest.TestCase):
    def cap_book(self, w_ai):
        others = (1.0 - 0.4 - 2 * w_ai) / 4 if w_ai == 0.12 else 0.09
        pos = [mrow('A1', 601, w_ai * 1e6), mrow('A2', 602, w_ai * 1e6)] + [mrow(f'O{i}', 610 + i, others * 1e6) for i in range(4)]
        cash = 1e6 - sum(p['mktValue'] for p in pos)
        info = {'A1': {'sector': 'Tech', 'exposure_weights': {'ai': 0.8}, 'casefile_summary': cfs(0.10, 70)},
                'A2': {'sector': 'Industrials', 'exposure_weights': {'ai': 0.8}, 'casefile_summary': cfs(0.10, 70)}}
        info.update({f'O{i}': {'sector': f'S{i}', 'exposure_weights': {f'd{i}': 0.9}, 'casefile_summary': cfs(0.10, 70)} for i in range(4)})
        return analyze(from_ib(pos, summ(1e6, cash), AS_OF), info, {})

    def add(self, a, ew):
        cand = {'symbol': 'A3', 'base_irr': 0.12, 'bear_value': 60, 'price': 100, 'verdict': 'attractive_at_price',
                'exposure_weights': {'ai': ew}}
        return by(compare(cand, a, {'cash': {'yield': 0.04}}, {'trial_weight': 0.05}), 'add_new')[0]

    def test_cash_funded_add_that_crosses_the_cap_is_a_reason_against(self):
        a = self.cap_book(0.12)                                            # ai cluster 0.192, below the 20% cap
        self.assertAlmostEqual(a['overlaps']['clusters'][0]['dominant_exposure'], 0.192)
        add = self.add(a, 0.3)
        tc = next(t for t in add['risk_change']['tag_changes'] if t['tag'] == 'ai')
        self.assertAlmostEqual(tc['after'], 0.192 + 0.05 * 0.3)            # 0.207 > 0.20
        self.assertTrue(tc['crosses_single_name_cap'])
        self.assertLess(tc['share_of_invested_after'], tc['share_of_invested_before'])
        r = [x for x in add['reasons_against'] if x.startswith('pushes ai exposure')]
        self.assertEqual(len(r), 1)
        self.assertIn('new breach', r[0])
        self.assertFalse(any('only because' in c for c in add['caveats'] + add['reasons_against']))

    def test_linked_carrier_of_a_tag_above_the_cap_is_a_reason_even_when_its_share_falls(self):
        a = self.cap_book(0.14)                                            # ai 0.224, already above the cap
        add = self.add(a, 0.3)                                             # 0.3 >= link threshold, below the book average
        tc = next(t for t in add['risk_change']['tag_changes'] if t['tag'] == 'ai')
        self.assertFalse(tc['crosses_single_name_cap'])
        self.assertLess(tc['share_of_invested_after'], tc['share_of_invested_before'])
        self.assertTrue(any(x.startswith('raises ai exposure') and 'part of that bet' in x for x in add['reasons_against']))
        low = self.add(a, 0.2)                                             # below the link threshold: caveat, accurately worded
        self.assertFalse(any('raises ai' in x for x in low['reasons_against']))
        cav = [c for c in low['caveats'] if c.startswith('ai exposure rises')]
        self.assertEqual(len(cav), 1)
        self.assertIn('carries ai at 0.20, below the link threshold', cav[0])
        self.assertNotIn('only because', cav[0])

    def test_two_name_driver_dominating_the_book_stays_a_cluster(self):
        # the module's fit-check case: first AI name at the cap, a second added, the rest in other drivers and an index fund
        pos = [mrow('AI1', 701, 200000.0), mrow('AI2', 702, 100000.0)] + [mrow(f'S{i}', 703 + i, 80000.0) for i in range(3)] \
            + [mrow('IDX', 706, 400000.0, ac='ETF')]
        info = {'AI1': {'sector': 'Tech', 'exposure_weights': {'ai': 0.8}}, 'AI2': {'sector': 'Industrials', 'exposure_weights': {'ai': 0.8}},
                'IDX': {'diversified': True, 'exposure_weights': {'ai': 0.25}}}
        info.update({f'S{i}': {'sector': f'X{i}', 'exposure_weights': {f'd{i}': 0.9}} for i in range(3)})
        a = analyze(from_ib(pos, summ(1e6, 60000.0), AS_OF), info, {})
        self.assertEqual(a['overlaps']['common_factors'], [])              # carriers hold 56% of single names, but no separate groups
        c = a['overlaps']['clusters'][0]
        self.assertEqual((set(c['holdings']), c['defining_tag']), ({'AI1', 'AI2'}, 'ai'))
        self.assertAlmostEqual(c['dominant_exposure'], 0.2 * 0.8 + 0.1 * 0.8)
        self.assertTrue(c['above_single_name_cap'])
        rv = {r['holding']: {t['code'] for t in r['triggers']} for r in a['review_needed']}
        self.assertIn('in_cluster_above_cap', rv['AI1'])
        self.assertIn('in_cluster_above_cap', rv['AI2'])

    def test_factor_share_counts_every_single_name(self):
        # 10 single names x 9%; only A1, A2 (ai 0.8) and X1 have holdings info
        names = ['A1', 'A2', 'X1'] + [f'N{i}' for i in range(7)]
        b = from_ib([mrow(s, 200 + i, 90000.0) for i, s in enumerate(names)], summ(1e6, 1e5), AS_OF)
        info = {'A1': {'sector': 'Tech', 'exposure_weights': {'ai': 0.8}}, 'A2': {'sector': 'Industrials', 'exposure_weights': {'ai': 0.8}},
                'X1': {'sector': 'Staples', 'exposure_weights': {'grocery': 0.9}}}
        a = analyze(b, info, {'single_name_cap': 0.15})
        self.assertEqual(a['overlaps']['common_factors'], [])
        self.assertEqual([(sorted(c['holdings']), round(c['dominant_exposure'], 6)) for c in a['overlaps']['clusters']],
                         [(['A1', 'A2'], 0.144)])
        f = analyze(b, info, {'single_name_cap': 0.15, 'factor_tags': ['ai']})['overlaps']['common_factors'][0]
        self.assertAlmostEqual(f['carrier_weight_share'], 0.18 / 0.90)
        self.assertAlmostEqual(f['single_name_weight_without_exposures'], 0.63)
        self.assertIn('2 of 10 single names', f['reading'])


SHORT_INFO = {'L1': {'sector': 'Tech', 'exposure_weights': {'ai': 0.8}}, 'L2': {'sector': 'Industrials', 'exposure_weights': {'ai': 0.8}},
              'S1': {'sector': 'Tech', 'exposure_weights': {'ai': 0.8}}, 'L4': {'sector': 'Utilities', 'exposure_weights': {'rates': 0.6}},
              'S2': {'sector': 'Utilities', 'exposure_weights': {'rates': 0.6}}, 'L3': {'sector': 'Staples', 'exposure_weights': {'grocery': 0.9}}}


class ShortsOptionsAndScenarios(unittest.TestCase):
    def short_book(self):
        pos = [mrow('L1', 801, 170000.0), mrow('L2', 802, 150000.0), mrow('S1', 804, -150000.0), mrow('L4', 806, 100000.0),
               mrow('S2', 805, -100000.0), mrow('L3', 803, 200000.0)]
        return from_ib(pos, summ(1e6, 630000.0), AS_OF)

    def test_shorts_offset_instead_of_joining_a_cluster(self):
        a = analyze(self.short_book(), SHORT_INFO, {})
        ov = a['overlaps']
        self.assertEqual(ov['common_factors'], [])                          # long AI carriers 0.32 of 0.87 single-name weight
        self.assertEqual(len(ov['clusters']), 1)
        c = ov['clusters'][0]
        self.assertEqual((sorted(c['holdings']), c['offsets']), (['L1', 'L2'], ['S1']))
        self.assertAlmostEqual(c['dominant_exposure'], (0.17 + 0.15) * 0.8)
        self.assertAlmostEqual(c['net_dominant_exposure'], (0.17 + 0.15 - 0.15) * 0.8)
        self.assertFalse(c['above_single_name_cap'])                         # 13.6% net
        offs = {o['tag']: o for o in ov['offsets']}
        self.assertAlmostEqual(offs['rates']['net_exposure'], 0.0)
        self.assertEqual((offs['rates']['holdings'], offs['rates']['offsets']), (['L4'], ['S2']))
        self.assertEqual(offs['ai']['offsets'], ['S1'])
        pairs = {tuple(sorted(p['holdings'])) for p in ov['pairs']}
        self.assertEqual(pairs, {('L1', 'L2')})                              # no 'one economic bet' between a long and a short
        rv = {r['holding']: {t['code'] for t in r['triggers']} for r in a['review_needed']}
        self.assertFalse(any('in_cluster_above_cap' in v for v in rv.values()))
        f = analyze(self.short_book(), SHORT_INFO, {'factor_tags': ['ai']})['overlaps']['common_factors'][0]
        self.assertEqual(sorted(f['holdings']), ['L1', 'L2'])
        self.assertAlmostEqual(f['carrier_weight_share'], 0.32 / 0.87)

    def test_long_put_lowers_the_holding_and_keeps_the_stock_basis(self):
        put_occ = {'ticker': 'GROC 261218P00090000', 'conid': 807, 'position': 10, 'mktPrice': 10.0, 'multiplier': 100,
                   'mktValue': 10000.0, 'assetClass': 'OPT', 'undSym': 'GROC', 'currency': 'USD'}
        put_field = {'ticker': 'GROC PUT1', 'conid': 808, 'position': 10, 'mktPrice': 10.0, 'multiplier': 100, 'mktValue': 10000.0,
                     'assetClass': 'OPT', 'undSym': 'GROC', 'currency': 'USD', 'putOrCall': 'P'}
        call = {'ticker': 'GROC JAN27 50 C', 'conid': 809, 'position': 5, 'mktPrice': 10.0, 'multiplier': 100, 'mktValue': 5000.0,
                'assetClass': 'OPT', 'undSym': 'GROC', 'currency': 'USD'}
        info = {'GROC': {'sector': 'Staples', 'exposure_weights': {'grocery': 1.0}}, 'X': {'sector': 'Other', 'exposure_weights': {'o': 1.0}}}
        scen = {'scenarios': {'g': {'shocks': {'grocery': -0.3}, 'mechanism': 'price war'}}}
        for opt_row in (put_occ, put_field):
            b = from_ib([mrow('GROC', 803, 190000.0, gain=0.5), mrow('X', 900, 300000.0), opt_row], summ(1e6, 5e5), AS_OF)
            self.assertEqual(next(p for p in b['positions'] if p['asset_class'] == 'OPT')['option_right'], 'P')
            a = analyze(b, info, scen)
            h = a['holdings']['GROC']
            self.assertAlmostEqual(h['weight'], 0.18)                        # 0.19 stock - 0.01 put market value
            self.assertAlmostEqual(a['exposures']['by_tag']['grocery']['exposure'], 0.18)
            self.assertAlmostEqual(a['scenarios']['g']['portfolio_impact'], -0.054)     # smaller loss than the stock alone
            self.assertAlmostEqual(h['tax']['unrealized_gain_fraction'], 0.5)
            self.assertEqual(h['tax']['gain_basis'], 'non-derivative lines')
            self.assertEqual(h['derivatives']['puts_counted_negative'], 1)
        b = from_ib([mrow('GROC', 803, 190000.0, gain=0.5), call], summ(1e6, 805000.0), AS_OF)
        self.assertEqual(b['positions'][1]['option_right'], 'C')
        self.assertAlmostEqual(analyze(b, info, {})['holdings']['GROC']['weight'], 0.195)

    def test_common_factor_attribution_includes_holding_estimates(self):
        pos = [mrow(f'R{i}', 10 + i, 150000.0) for i in range(6)]
        info = {f'R{i}': {'sector': f'S{i}', 'exposure_weights': {'rates': 0.6, f'd{i}': 0.8}} for i in range(6)}
        for i in range(4):
            info[f'R{i}']['scenario_sensitivity'] = {'rates_up': -0.12}
        scen = {'rates_up': {'shocks': {'rates': -0.2}, 'mechanism': 'yields up'}}
        b = from_ib(pos, summ(1e6, 1e5), AS_OF)
        s = analyze(b, info, {'scenarios': scen, 'factor_tags': ['rates']})['scenarios']['rates_up']
        self.assertAlmostEqual(s['portfolio_impact'], -0.108)
        f = s['by_common_factor'][0]
        self.assertAlmostEqual(f['loss'], -0.108)
        self.assertAlmostEqual(f['share_of_loss'], 1.0)
        self.assertEqual(sorted(f['holding_estimates_included']), ['R0', 'R1', 'R2', 'R3'])
        # without the listing, rates spans no separate driver groups: one rates cluster, same loss
        a = analyze(b, info, {'scenarios': scen})
        self.assertEqual(a['overlaps']['common_factors'], [])
        self.assertAlmostEqual(a['scenarios']['rates_up']['by_cluster'][0]['loss'], -0.108)


class FundedSaleTaxBearAndText(unittest.TestCase):
    def zero_cash(self):
        pos = [mrow('G1', 401, 400000.0, gain=0.5), mrow('G2', 402, 300000.0, gain=-0.2), mrow('G3', 403, 200000.0, gain=0.25),
               mrow('F1', 404, 100000.0, gain=0.1, ac='ETF')]
        info = {'G1': {'sector': 'Tech', 'exposure_weights': {'ai': 0.8}, 'casefile_summary': cfs(0.10, 60)},
                'G2': {'sector': 'Staples', 'exposure_weights': {'grocery': 0.9}, 'casefile_summary': cfs(0.06, 80)},
                'G3': {'sector': 'Health', 'exposure_weights': {'pharma': 0.9}, 'casefile_summary': cfs(0.08, 70)},
                'F1': {'sector': 'Broad', 'diversified': True, 'exposure_weights': {'ai': 0.2}}}
        return analyze(from_ib(pos, summ(1e6, 0.0), AS_OF), info, {})

    CAND = {'symbol': 'NEWW', 'base_irr': 0.15, 'bear_value': 50, 'price': 100, 'verdict': 'attractive_at_price',
            'exposure_weights': {'water': 0.9}}

    def test_sale_tax_nets_realized_losses(self):
        add = by(compare(self.CAND, self.zero_cash(), {'cash': {'yield': 0.04}}, {'trial_weight': 0.05}), 'add_new')[0]
        tax = add['tax']
        sold = {'G1': 0.02, 'G2': 0.015, 'G3': 0.01, 'F1': 0.005}
        for k, v in sold.items():
            self.assertAlmostEqual(-add['illustrative_weight_change'][k], v)
        self.assertAlmostEqual(tax['immediate_tax_share_of_portfolio'], 0.25 * (0.5 * 0.02 - 0.2 * 0.015 + 0.25 * 0.01 + 0.1 * 0.005))
        self.assertAlmostEqual(tax['realized_loss_share_of_portfolio'], -0.2 * 0.015)
        self.assertAlmostEqual(tax['loss_carried_forward_share_of_portfolio'], 0.0)
        self.assertTrue(any('G2' in c and 'offset gains' in c for c in tax['caveats']))
        self.assertTrue(any('0.25% of the portfolio in tax' in r for r in add['reasons_against']))
        # a sale that realizes a net loss pays no tax now and carries the loss forward
        losers = json.loads(json.dumps(self.zero_cash()))
        for k in ('G1', 'G3', 'F1'):
            losers['holdings'][k]['tax']['unrealized_gain_fraction'] = -0.1
        tax2 = by(compare(self.CAND, losers, {'cash': {'yield': 0.04}}, {'trial_weight': 0.05}), 'add_new')[0]['tax']
        self.assertEqual(tax2['immediate_tax_share_of_portfolio'], 0.0)
        self.assertAlmostEqual(tax2['loss_carried_forward_share_of_portfolio'], 0.1 * 0.035 + 0.2 * 0.015)

    def test_bear_effect_nets_known_sold_holdings(self):
        a = self.zero_cash()
        known = 0.02 * -0.4 + 0.015 * -0.2 + 0.01 * -0.3
        add = by(compare(self.CAND, a, {'cash': {'yield': 0.04}}, {'trial_weight': 0.05}), 'add_new')[0]
        self.assertAlmostEqual(add['bear_case_portfolio_effect'], 0.05 * -0.5 - known)        # F1 (0.5%) has no bear value
        self.assertAlmostEqual(add['funding']['sold_weight_without_bear_value'], 0.005)
        self.assertTrue(any('0.5% of the sale has none' in c for c in add['caveats']))
        # the held index uses the index downside, as its return uses the index expected return
        alt = {'cash': {'yield': 0.04}, 'index': {'symbol': 'F1', 'expected_return': 0.07, 'downside': -0.3}}
        add = by(compare(self.CAND, a, alt, {'trial_weight': 0.05}), 'add_new')[0]
        self.assertAlmostEqual(add['bear_case_portfolio_effect'], 0.05 * -0.5 - known - 0.005 * -0.3)
        self.assertEqual(add['funding']['sold_weight_without_bear_value'], 0)

    def test_hold_cash_forgone_return_matches_the_funded_add(self):
        out = compare(self.CAND, self.zero_cash(), {'cash': {'yield': 0.04}}, {'trial_weight': 0.05})
        add, hc = by(out, 'add_new')[0], by(out, 'hold_cash')[0]
        er = 0.02 * (0.15 - 0.10) + 0.015 * (0.15 - 0.06) + 0.01 * (0.15 - 0.08) + 0.005 * (0.15 - 0.04)
        self.assertIsNone(add['expected_return']['portfolio_delta'])        # F1 has no base IRR and is not the index here
        self.assertTrue(any('forgone return unknown' in r for r in hc['reasons_against']))
        alt = {'cash': {'yield': 0.04}, 'index': {'symbol': 'F1', 'expected_return': 0.04}}
        out = compare(self.CAND, self.zero_cash(), alt, {'trial_weight': 0.05})
        add, hc = by(out, 'add_new')[0], by(out, 'hold_cash')[0]
        self.assertAlmostEqual(add['expected_return']['portfolio_delta'], er)
        self.assertEqual(hc['reasons_against'], [f"forgoes {add['expected_return']['reading']} vs adding NEWW as simulated in the "
                                                 f"add option (funding: pro_rata_sale)"])
        self.assertNotIn(f"{0.05 * (0.15 - 0.04) * 100:+.2f}", hc['reasons_against'][0])

    def test_margin_text_does_not_claim_100pct(self):
        a = analyze(from_ib([mrow('M1', 1, 1e6, gain=0.4)], summ(1e6, 0.0), AS_OF),
                    {'M1': {'sector': 'A', 'exposure_weights': {'x': 0.9}, 'casefile_summary': cfs(0.10, 70)}}, {})
        out = compare({'symbol': 'M1', 'base_irr': 0.10, 'bear_value': 70, 'price': 100, 'verdict': 'attractive_at_price',
                       'exposure_weights': {'x': 0.9}}, a, {'cash': {'yield': 0.04}}, {'trial_weight': 0.05})
        add = by(out, 'add_to_existing', 'M1')[0]
        self.assertEqual(add['funding']['mode'], 'margin')
        self.assertTrue(any('goes above 100% invested' in r for r in add['reasons_against']))
        self.assertFalse(any('stays within 100%' in r for r in add['reasons_against']))

    def test_effective_n_is_information_only(self):
        out = compare(DUP, analysis(), ALT)
        for o in out['options']:
            self.assertFalse(any('effective number of holdings' in r for r in o['reasons_for'] + o['reasons_against']), o['option'])
        self.assertIn('not used as a reason', by(out, 'add_new')[0]['risk_change']['effective_n_note'])


class CliPortfolioOptions(unittest.TestCase):
    def test_portfolio_options_flag(self):
        with tempfile.TemporaryDirectory() as d:
            pos, hold = os.path.join(d, 'positions.json'), os.path.join(d, 'holdings.json')
            with open(pos, 'w', encoding='utf-8') as f:
                json.dump(synthetic_positions(), f)
            with open(hold, 'w', encoding='utf-8') as f:
                json.dump(INFO, f)
            run = lambda *extra: subprocess.run([sys.executable, '-m', 'fundamental_engine.cli', 'portfolio', pos, '--as-of', AS_OF,
                                                 '--holdings', hold, *extra], capture_output=True, text=True, cwd=ROOT, timeout=120)
            r = run('--options', json.dumps({'factor_tags': ['ai_capex_cycle'], 'scenarios': SCENARIOS}))
            self.assertEqual(r.returncode, 0, r.stderr)
            a = json.loads(r.stdout)['analysis']
            self.assertEqual(a['params']['factor_tags'], ['ai_capex_cycle'])
            self.assertIn('ai_capex_slowdown', a['scenarios'])
            r = run('--options', '{"factor_tags": ')
            self.assertEqual(r.returncode, 2)
            self.assertIn('--options: inline JSON is invalid', r.stderr)


if __name__ == '__main__':
    unittest.main()
