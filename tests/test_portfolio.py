"""Portfolio layer tests. Every portfolio here is SYNTHETIC: fictional tickers, invented quantities and
values, placeholder account codes. No private position data belongs in this file."""
import json
import math
import unittest
from fundamental_engine import portfolio
from fundamental_engine.portfolio import from_ib, analyze, compare, tags_from_themes, _no_orders, ORDER_KEYS, _k

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
        cl = self.a['overlaps']['clusters']
        ai = next(c for c in cl if 'SEMA' in c['holdings'])
        self.assertEqual(set(ai['holdings']), {'SEMA', 'SEMB', 'AIPW'})          # index fund does not chain clusters
        self.assertTrue(ai['hidden_by_sector_labels'])
        self.assertEqual(ai['defining_tag'], 'ai_capex_cycle')
        self.assertAlmostEqual(ai['shared_tags']['ai_capex_cycle'], 0.21 * 0.8 + 0.10 * 0.6 + 0.08 * 0.5)
        self.assertEqual(ai['dominant_tag'], 'semis_supply_chain')                 # SEMA+SEMB alone: 0.21*0.9 + 0.10*1.0
        self.assertAlmostEqual(ai['dominant_exposure'], 0.289)
        self.assertTrue(ai['above_single_name_cap'])
        shared = {r['tag']: r for r in self.a['overlaps']['shared_tags']}
        self.assertIn('IDXF', shared['ai_capex_cycle']['holdings'])             # but its look-through exposure counts
        self.assertEqual(ai['shared_tags'].keys(), {'ai_capex_cycle', 'semis_supply_chain'})
        rates = next(c for c in cl if c['defining_tag'] == 'rates')
        self.assertEqual(set(rates['holdings']), {'UTLX', 'AIPW'})               # AIPW in both; no chaining UTLX into AI
        self.assertFalse(rates['above_single_name_cap'])

    def test_scenario_concentration(self):
        s = self.a['scenarios']['ai_capex_slowdown']
        impacts = {r['holding']: r['impact'] for r in s['holdings']}
        self.assertAlmostEqual(impacts['SEMA'], -0.4 * 0.8 - 0.1 * 0.9)
        self.assertAlmostEqual(impacts['BRND'], 0.0)
        self.assertIn('BRND', s['no_mapped_exposure'])
        total = 0.21 * -0.41 + 0.10 * -0.34 + 0.08 * -0.2 + 0.25 * -0.12
        self.assertAlmostEqual(s['portfolio_impact'], total)
        self.assertEqual(s['holdings'][0]['holding'], 'SEMA')
        self.assertGreater(s['by_cluster'][0]['share_of_loss'], 0.75)
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
        self.assertTrue(any('funding would need a sale or margin' in r for r in add['reasons_against']))

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


if __name__ == '__main__':
    unittest.main()


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
