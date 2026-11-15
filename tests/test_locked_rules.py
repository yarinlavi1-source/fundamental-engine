"""locked_rules.py on fixed synthetic series (no network): known beta, known quartiles, the judgement block.

The price series are built from stated weekly returns, so the expected beta is exact. Two tests read tracked research
files: the saved BNY multiple history (its own summary must be reproduced) and research/market.json.
"""
import json
import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from fundamental_engine import locked_rules as LR  # noqa: E402

FRIDAY = date(2026, 11, 13)


def spy_returns(n):
    """A fixed, non-constant list of weekly returns between -3% and +3%."""
    return [((i * 37) % 13 - 6) / 200 for i in range(n)]


def weekly_series(returns, last=FRIDAY, start=100.0):
    """{Friday ISO date: close} whose weekly returns are `returns`, the last close on `last`."""
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    n = len(closes)
    return {(last - timedelta(days=7 * (n - 1 - i))).isoformat(): c for i, c in enumerate(closes)}


def stock_and_spy(beta, n=104, alpha=0.001, last=FRIDAY):
    rs = spy_returns(n)
    return weekly_series([alpha + beta * r for r in rs], last), weekly_series(rs, last, 500.0)


def judgement(**over):
    block = {
        'beta': {'raw': 1.2, 'adjusted': 1.134, 'weeks': 104, 'window': {'from': '2024-11-15', 'to': '2026-11-13'},
                 'used': 1.13, 'reason_if_different': ''},
        'exit_pe': {'own_median': 24.8, 'p25': 23.6, 'p75': 26.6, 'months': 60, 'basis': 'forward_pe',
                    'ratio_to_model_eps': 1.0, 'used': 25, 'reason_if_different': ''},
        'hist_multiple': {'company_type': 'stable_profitable', 'basis': 'forward_pe', 'next_year_figure': 10.0,
                          'source': 'company guidance midpoint plus its usual beat (synthetic)', 'median': 24.8,
                          'p25': 23.6, 'p75': 26.6, 'months': 60, 'value': 248.0, 'low': 236.0, 'high': 266.0},
        'beta_mature': {'value': 1.0, 'source': 'mature payments network (synthetic)'},
        'roic_terminal': {'value': 0.4, 'source': 'average ROIC of the last five years (synthetic)'},
        'market': {'rf': 0.0494, 'erp': 0.042, 'source': 'research/market.json', 'reason_if_different': ''},
    }
    for k, v in over.items():
        block[k] = dict(block[k], **v) if isinstance(v, dict) else v
    return block


def params(**over):
    p = {'as_of': '2026-11-13', 'rf': 0.0494, 'erp': 0.042, 'beta': 1.13, 'beta_mature': 1.0, 'roic_terminal': 0.4,
         'scenarios': {'bear': {'pe': 20}, 'base': {'pe': 25}, 'bull': {'pe': 28}}}
    p.update(over)
    return p


MARKET = {'in_force': [{'from': '2026-11-04', 'rf': {'value': 0.0494}, 'erp': {'value': 0.042}},
                       {'from': '2026-12-01', 'rf': {'value': 0.051}, 'erp': {'value': 0.041}}]}


class BetaTests(unittest.TestCase):
    def test_known_beta_and_adjustment(self):
        stock, spy = stock_and_spy(1.5)
        r = LR.beta_2y_weekly(stock, spy)
        self.assertAlmostEqual(r['raw'], 1.5, places=4)
        self.assertAlmostEqual(r['adjusted'], 0.67 * 1.5 + 0.33, places=4)
        self.assertEqual(r['weeks'], 104)
        self.assertEqual(r['window'], {'from': (FRIDAY - timedelta(days=7 * 104)).isoformat(), 'to': FRIDAY.isoformat()})
        self.assertEqual(r['correlation'], 1.0)
        self.assertFalse(r['short_history'])

    def test_only_the_last_104_weeks_count(self):
        rs = spy_returns(160)
        stock = weekly_series([(3.0 if i < 56 else 0.8) * r for i, r in enumerate(rs)])
        r = LR.beta_2y_weekly(stock, weekly_series(rs, start=500.0))
        self.assertAlmostEqual(r['raw'], 0.8, places=4)
        self.assertEqual(r['weeks'], 104)
        self.assertGreater(LR.beta_2y_weekly(stock, weekly_series(rs, start=500.0), weeks=160)['raw'], 1.0)

    def test_partial_week_is_dropped_with_as_of(self):
        stock, spy = stock_and_spy(1.2, n=110)
        # a wild partial week after the last Friday: Tuesday closes only
        tue = (FRIDAY + timedelta(days=4)).isoformat()
        stock[tue], spy[tue] = stock[FRIDAY.isoformat()] * 1.4, spy[FRIDAY.isoformat()] * 0.97
        with_partial = LR.beta_2y_weekly(stock, spy)
        clean = LR.beta_2y_weekly(stock, spy, as_of=FRIDAY + timedelta(days=5))     # Wednesday: the week is not over
        self.assertAlmostEqual(clean['raw'], 1.2, places=4)
        self.assertEqual(clean['window']['to'], FRIDAY.isoformat())
        self.assertNotAlmostEqual(with_partial['raw'], 1.2, places=2)
        # on the following Friday the week is complete and counts
        self.assertEqual(LR.beta_2y_weekly(stock, spy, as_of=FRIDAY + timedelta(days=7))['window']['to'], tue)

    def test_daily_and_weekly_series_agree_and_formats_mix(self):
        stock, spy = stock_and_spy(0.9, n=60)
        daily = dict(stock)
        for d, c in stock.items():                       # earlier closes inside each week must not matter
            daily[(date.fromisoformat(d) - timedelta(days=2)).isoformat()] = c * 1.07
        pairs = [(date.fromisoformat(d), c) for d, c in spy.items()]
        pairs.append((FRIDAY - timedelta(days=700), None))   # a missing bar is ignored
        a, b = LR.beta_2y_weekly(daily, pairs), LR.beta_2y_weekly(stock, spy)
        self.assertEqual(a['raw'], b['raw'])
        self.assertEqual(a['weeks'], 60)

    def test_short_history(self):
        stock, spy = stock_and_spy(2.0, n=30)
        r = LR.beta_2y_weekly(stock, spy)
        self.assertTrue(r['short_history'])
        self.assertEqual(r['weeks'], 30)
        stock, spy = stock_and_spy(2.0, n=10)
        with self.assertRaises(ValueError):
            LR.beta_2y_weekly(stock, spy)

    def test_beta_block(self):
        stock, spy = stock_and_spy(1.16)
        block = LR.beta_block(LR.beta_2y_weekly(stock, spy))
        self.assertEqual(block['used'], 1.11)            # 0.67 x 1.16 + 0.33 = 1.1072
        self.assertEqual(block['reason_if_different'], '')
        self.assertEqual(LR.beta_block(LR.beta_2y_weekly(stock, spy), used=2.0, reason='country premium')['used'], 2.0)


class MultipleTests(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual([LR.percentile([5, 1, 4, 2, 3], q) for q in (0.25, 0.5, 0.75)], [2, 3, 4])
        self.assertEqual([LR.percentile([1, 2, 3, 4], q) for q in (0.25, 0.5, 0.75)], [1.75, 2.5, 3.25])
        with self.assertRaises(ValueError):
            LR.percentile([], 0.5)

    def test_known_quartiles(self):
        months = [f'2026-{m:02d}' for m in range(1, 10)]
        prices = {m: 10.0 * (i + 1) for i, m in enumerate(months)}       # 10..90 over EPS 2 -> P/E 5..45
        h = LR.multiple_history(prices, {m: 2.0 for m in months})
        self.assertEqual((h['p25'], h['median'], h['p75'], h['min'], h['max']), (15.0, 25.0, 35.0, 5.0, 45.0))
        self.assertEqual((h['months'], h['basis'], h['latest']), (9, 'forward_pe', 45.0))
        self.assertEqual(h['window'], {'from': '2026-01', 'to': '2026-09'})

    def test_window_dates_and_nonpositive_months(self):
        prices = {date(2020 + i // 12, i % 12 + 1, 28): 100.0 + i for i in range(72)}
        eps = {f'{2020 + i // 12}-{i % 12 + 1:02d}': (-1.0 if 20 <= i < 24 else 5.0) for i in range(72)}
        h = LR.multiple_history(prices, eps)
        self.assertEqual(h['window'], {'from': '2021-01', 'to': '2025-12'})     # the last 60 month-ends
        self.assertEqual((h['months'], h['skipped_nonpositive']), (56, 4))      # four loss months are not a multiple
        self.assertEqual(LR.multiple_history(prices, eps, months=None)['months'], 68)
        self.assertEqual(LR.multiple_history(prices, eps, as_of='2024-06-30')['window']['to'], '2024-06')
        with self.assertRaises(ValueError):
            LR.multiple_history(prices, {k: 0.0 for k in eps})
        with self.assertRaises(ValueError):
            LR.multiple_history(prices, eps, basis='trailing_pe')

    def test_saved_bny_history_is_reproduced(self):
        path = os.path.join(ROOT, 'research', 'companies', 'BNY', '2026-11-11', 'hist_multiple.json')
        if not os.path.exists(path):
            self.skipTest('no saved BNY history')
        with open(path, encoding='utf-8') as f:
            saved = json.load(f)
        prices = {r['month']: r['price'] for r in saved['monthly']}
        pe = LR.multiple_history(prices, {r['month']: r['fwd_eps'] for r in saved['monthly']})
        tbv = LR.multiple_history(prices, {r['month']: r['tbvps'] for r in saved['monthly']}, basis='p_tbv')
        self.assertEqual(pe['months'], saved['summary']['fwd_pe']['n'])
        for k in ('median', 'p25', 'p75'):               # the file's inputs are rounded to cents
            self.assertAlmostEqual(pe[k], saved['summary']['fwd_pe'][k], delta=0.02)
            self.assertAlmostEqual(tbv[k], saved['summary']['p_tbv'][k], delta=0.02)

    def test_exit_pe_block(self):
        h = {'basis': 'forward_pe', 'median': 48.0, 'p25': 41.0, 'p75': 53.0, 'months': 57}
        self.assertEqual(LR.exit_pe_block(h, ratio_to_model_eps=1.53)['used'], 73)
        self.assertEqual(LR.exit_pe_block(h, used=63, ratio_to_model_eps=1.53, reason='growth slows')['used'], 63)
        with self.assertRaises(ValueError):
            LR.exit_pe_block(dict(h, basis='ev_sales'))


class HistMultipleValueTests(unittest.TestCase):
    PE = {'basis': 'forward_pe', 'median': 20.0, 'p25': 16.0, 'p75': 25.0, 'months': 60}
    EVS = {'basis': 'ev_sales', 'median': 8.0, 'p25': 6.0, 'p75': 11.0, 'months': 40}
    PTBV = {'basis': 'p_tbv', 'median': 2.4, 'p25': 2.0, 'p75': 3.5, 'months': 60}

    def test_forward_pe_types(self):
        v = LR.hist_multiple_value('stable_profitable', 5.0, self.PE)
        self.assertEqual((v['value'], v['low'], v['high']), (100.0, 80.0, 125.0))
        self.assertEqual(v['label_he'], 'שווי לפי המכפיל ההיסטורי')
        self.assertEqual(v['notes'], [])
        g = LR.hist_multiple_value('grower_growth_changed', 5.0, self.PE, peer_multiple=14.0)
        self.assertEqual((g['value'], g['peer_value']), (100.0, 70.0))
        self.assertIn('different growth rate', g['notes'][0])
        c = LR.hist_multiple_value('cyclical', 4.0, self.PE)
        self.assertEqual(c['value'], 80.0)
        self.assertIn('mid-cycle', c['anchor'])

    def test_ev_sales_adds_net_cash(self):
        v = LR.hist_multiple_value('low_profit', 10.0, self.EVS, net_cash_per_share=3.5)
        self.assertEqual((v['value'], v['low'], v['high']), (83.5, 63.5, 113.5))
        self.assertTrue(any('40 months' in n for n in v['notes']))
        with self.assertRaises(ValueError):              # cash is already in a share price multiple
            LR.hist_multiple_value('stable_profitable', 5.0, self.PE, net_cash_per_share=3.5)

    def test_bank_and_custom(self):
        b = LR.hist_multiple_value('bank', 32.0, self.PTBV)
        self.assertEqual((b['value'], b['low'], b['high']), (76.8, 64.0, 112.0))
        self.assertIn('ROE', b['notes'][0])
        x = LR.hist_multiple_value('levered_complex', None, None)
        self.assertIsNone(x['value'])
        self.assertIn('custom model', x['notes'][0])

    def test_wrong_inputs(self):
        with self.assertRaises(ValueError):
            LR.hist_multiple_value('bank', 32.0, self.PE)            # a bank is not read on P/E here
        with self.assertRaises(ValueError):
            LR.hist_multiple_value('low_profit', 10.0, self.PE)
        with self.assertRaises(ValueError):
            LR.hist_multiple_value('stable_profitable', -1.0, self.PE)
        with self.assertRaises(ValueError):
            LR.hist_multiple_value('utility', 5.0, self.PE)
        with self.assertRaises(ValueError):
            LR.hist_multiple_value('stable_profitable', 5.0, dict(self.PE, p25=30.0))


class JudgementTests(unittest.TestCase):
    def test_block_that_follows_the_rules(self):
        v = LR.validate_judgement(judgement(), params(), MARKET)
        self.assertTrue(v['ok'], v['errors'])
        self.assertEqual(v['rule']['beta'], 1.134)
        self.assertEqual(v['rule']['exit_pe'], 24.8)
        self.assertEqual((v['rule']['rf'], v['rule']['erp']), (0.0494, 0.042))
        self.assertEqual(v['rule']['hist_multiple'], {'value': 248.0, 'low': 236.0, 'high': 266.0})

    def test_beta_differs_without_reason_is_rejected(self):
        v = LR.validate_judgement(judgement(beta={'used': 2.0}))
        self.assertFalse(v['ok'])
        self.assertTrue(any(e.startswith('beta: used 2.0 differs from the rule 1.134') for e in v['errors']))
        ok = LR.validate_judgement(judgement(beta={'used': 2.0, 'reason_if_different': 'implicit country premium'}))
        self.assertTrue(ok['ok'], ok['errors'])
        blank = LR.validate_judgement(judgement(beta={'used': 2.0, 'reason_if_different': '   '}))
        self.assertFalse(blank['ok'])

    def test_exit_pe_differs_without_reason_is_rejected(self):
        v = LR.validate_judgement(judgement(exit_pe={'own_median': 9.1, 'used': 11}))
        self.assertFalse(v['ok'])
        self.assertTrue(any(e.startswith('exit_pe: used 11 differs from the rule 9.1') for e in v['errors']))
        ok = LR.validate_judgement(judgement(exit_pe={'own_median': 9.1, 'used': 11,
                                                      'reason_if_different': 'the mix moved to faster-growing servers'}))
        self.assertTrue(ok['ok'], ok['errors'])
        # the basis ratio is part of the rule: 48 x 1.53 = 73.4
        ratio = judgement(exit_pe={'own_median': 48.0, 'ratio_to_model_eps': 1.53, 'used': 73})
        self.assertTrue(LR.validate_judgement(ratio)['ok'])
        self.assertFalse(LR.validate_judgement(judgement(exit_pe={'own_median': 48.0, 'ratio_to_model_eps': 1.53,
                                                                  'used': 63}))['ok'])

    def test_no_measurement_needs_a_reason(self):
        no_pe = judgement(exit_pe={'own_median': None, 'used': 16})
        self.assertFalse(LR.validate_judgement(no_pe)['ok'])
        no_pe['exit_pe']['reason_if_different'] = 'no own P/E history: losses since listing'
        self.assertTrue(LR.validate_judgement(no_pe)['ok'])
        self.assertFalse(LR.validate_judgement(judgement(beta={'raw': None, 'used': 1.3}))['ok'])

    def test_working_is_required_and_checked(self):
        self.assertFalse(LR.validate_judgement(judgement(beta={'adjusted': 1.2}))['ok'])       # not 0.67 x 1.2 + 0.33
        self.assertFalse(LR.validate_judgement(judgement(beta={'weeks': None}))['ok'])
        self.assertFalse(LR.validate_judgement(judgement(beta_mature={'source': ''}))['ok'])
        self.assertFalse(LR.validate_judgement(judgement(roic_terminal=None))['ok'])
        self.assertFalse(LR.validate_judgement(judgement(hist_multiple={'value': 300.0}))['ok'])
        self.assertFalse(LR.validate_judgement(judgement(hist_multiple={'source': ''}))['ok'])
        self.assertFalse(LR.validate_judgement(None)['ok'])
        short = LR.validate_judgement(judgement(beta={'weeks': 40}, exit_pe={'months': 34}))
        self.assertTrue(short['ok'])
        self.assertEqual(len(short['warnings']), 2)
        no_report = judgement()
        del no_report['hist_multiple']
        self.assertTrue(LR.validate_judgement(no_report)['ok'])

    def test_block_must_describe_the_params(self):
        for over in ({'beta': 1.2}, {'beta_mature': 1.1}, {'roic_terminal': 0.3},
                     {'scenarios': {'base': {'pe': 22}}}):
            v = LR.validate_judgement(judgement(), params(**over), MARKET)
            self.assertFalse(v['ok'], over)

    def test_rf_and_erp_come_from_the_market_file(self):
        v = LR.validate_judgement(judgement(market={'rf': 0.049}), params(rf=0.049), MARKET)
        self.assertFalse(v['ok'])
        self.assertTrue(any(e.startswith('market: rf 0.049 differs from research/market.json 0.0494') for e in v['errors']))
        ok = LR.validate_judgement(judgement(market={'rf': 0.049, 'reason_if_different': 'kept from the saved baseline'}),
                                   params(rf=0.049), MARKET)
        self.assertTrue(ok['ok'], ok['errors'])
        later = LR.validate_judgement(judgement(), params(as_of='2026-12-02'), MARKET)        # a newer entry is in force
        self.assertEqual(len([e for e in later['errors'] if e.startswith('market:')]), 2)


class MarketTests(unittest.TestCase):
    def test_entry_in_force(self):
        self.assertIsNone(LR.market_in_force(MARKET, '2026-11-03'))
        self.assertEqual(LR.market_in_force(MARKET, '2026-11-30')['rf'], 0.0494)
        self.assertEqual(LR.market_in_force(MARKET, '2026-12-01'),
                         {'from': '2026-12-01', 'rf': 0.051, 'erp': 0.041, 'index_return': 0.092})
        self.assertEqual(LR.check_market(params(), MARKET), [])
        self.assertEqual(LR.check_market(params(rf=0.049), MARKET),
                         [{'key': 'rf', 'used': 0.049, 'rule': 0.0494, 'from': '2026-11-04'}])

    def test_tracked_market_file(self):
        market = LR.load_market()
        entry = LR.market_in_force(market, '2026-11-13')
        self.assertEqual((entry['rf'], entry['erp'], entry['index_return']), (0.0494, 0.042, 0.0914))
        for e in market['in_force']:
            for k in ('rf', 'erp'):
                self.assertTrue(e[k]['as_of'] <= e['from'])
                self.assertTrue(e[k]['title'])
        froms = [e['from'] for e in market['in_force']]
        self.assertEqual(froms, sorted(set(froms)))
        self.assertTrue(market['update_rule'])


class ScriptTests(unittest.TestCase):
    def test_weekly_closes_keep_the_exchange_day_across_dst(self):
        import io
        from datetime import datetime, timezone
        import locked_rules as script
        try:
            from zoneinfo import ZoneInfo
            ny = ZoneInfo('America/New_York')
        except Exception:
            self.skipTest('no time zone database')
        mondays = [date(2026, 3, 2), date(2026, 3, 9), date(2026, 10, 26), date(2026, 11, 2)]   # both sides of DST
        stamps = [int(datetime(d.year, d.month, d.day, tzinfo=ny).timestamp()) for d in mondays]
        payload = {'chart': {'result': [{
            'meta': {'exchangeTimezoneName': 'America/New_York', 'gmtoffset': -18000},
            'timestamp': stamps, 'indicators': {'quote': [{'close': [10.0, None, 12.0, 13.0]}]}}]}}

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        seen = []

        def opener(req, timeout=None):
            seen.append((req.full_url, req.get_header('User-agent')))
            return Reply(json.dumps(payload).encode())

        got = script.weekly_closes('TEST', date(2026, 3, 1), date(2026, 11, 6), opener)
        self.assertEqual(got, {'2026-03-02': 10.0, '2026-10-26': 12.0, '2026-11-02': 13.0})
        self.assertIn('interval=1wk', seen[0][0])
        self.assertIn('period1=', seen[0][0])
        self.assertEqual(seen[0][1], 'Mozilla/5.0')


if __name__ == '__main__':
    unittest.main()
