"""leads.py and channels.py on SYNTHETIC payloads shaped like the real ones (EDGAR form.idx, SEC submissions, SEC frames,
Alpha Vantage NEWS_SENTIMENT / EARNINGS_ESTIMATES / EARNINGS, LunarCrush tables). Companies and numbers are invented."""
import json
import os
import tempfile
import unittest

from fundamental_engine import candidates, channels, leads as L

IDX = """Description:           Master Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    October 19, 2026

Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
NT 10-K          Late Filer Inc                                                1003        2026-10-05  edgar/data/1003/0000001003-26-000009.txt
SCHEDULE 13D     Target Co                                                     1001        2026-10-08  edgar/data/1001/0001104659-26-114703.txt
SCHEDULE 13D     Activist Fund LP                                              9001        2026-10-08  edgar/data/9001/0001104659-26-114703.txt
SCHEDULE 13D     Old Target                                                    1002        2026-01-08  edgar/data/1002/0001104659-26-000001.txt
10-12B           NewCo Spin Inc                                                7001        2026-10-02  edgar/data/7001/0001193125-26-000002.txt
"""


def subs(cik, rows):
    return {'cik': str(cik), 'filings': {'recent': {'form': [r[0] for r in rows], 'filingDate': [r[1] for r in rows],
                                                    'items': [r[2] for r in rows], 'accessionNumber': [f'0000{cik}-26-00000{i}' for i in range(len(rows))],
                                                    'primaryDocument': ['d.htm'] * len(rows)}}}


class SecChannelTests(unittest.TestCase):
    def test_form_index_targets_red_flags_and_prelisting(self):
        rows = L.parse_form_index(IDX)
        self.assertEqual(rows[1]['form'], 'SCHEDULE 13D')
        got, pre = L.from_form_index(rows, {1001: 'TGT', 1002: 'OLD', 1003: 'LATE'}, '2026-10-20')
        kinds = {(x['symbol'], x['kind'], x['role']) for x in got}
        self.assertIn(('TGT', 'stake_13d', 'lead'), kinds)                         # the listed side of the 13D is the target
        self.assertIn(('LATE', 'late_annual_report', 'red_flag'), kinds)
        self.assertNotIn('OLD', {x['symbol'] for x in got})                         # outside the 92-day window
        self.assertEqual([p['company'] for p in pre], ['NewCo Spin Inc'])

    def test_8k_items(self):
        s = subs(1001, [('8-K', '2026-09-28', '1.01,9.01'), ('8-K', '2026-08-01', '4.02'), ('10-Q', '2026-08-01', ''),
                        ('8-K', '2025-01-01', '1.01')])
        got = L.from_8k_items(s, 'TGT', '2026-10-20')
        self.assertEqual(sorted((x['kind'], x['role']) for x in got), [('material_agreement', 'lead'), ('non_reliance', 'red_flag')])

    def test_revenue_acceleration_from_quarterly_frames(self):
        def frame(vals):
            return {'data': [{'cik': c, 'val': v} for c, v in vals.items()]}
        q = {'CY2025Q2': {1: 100, 2: 100}, 'CY2025Q3': {1: 100, 2: 100}, 'CY2025Q4': {1: 100, 2: 100}, 'CY2026Q1': {1: 104, 2: 100},
             'CY2026Q2': {1: 130, 2: 80}, 'CY2025Q1': {1: 100, 2: 100}, 'CY2024Q4': {1: 100, 2: 100}, 'CY2024Q3': {1: 100, 2: 100},
             'CY2024Q2': {1: 100, 2: 100}}
        series = L.quarterly_series({k: frame({c: v * 1e6 for c, v in d.items()}) for k, d in q.items()})
        got = {x['symbol']: x for x in L.from_revenue_acceleration(series, {1: 'UP', 2: 'DOWN'}, '2026-10-20', 'CY2026Q2')}
        self.assertEqual((got['UP']['kind'], got['UP']['direction']), ('revenue_acceleration', 'positive'))
        self.assertAlmostEqual(got['UP']['strength']['yoy_latest'], .30)
        self.assertEqual(got['DOWN']['kind'], 'revenue_deceleration')


class ConnectorTests(unittest.TestCase):
    def test_news_counts_syndicated_copies_once(self):
        art = lambda t, src, s: {'title': t, 'time_published': '20261015T120000', 'source_domain': src,
                                 'ticker_sentiment': [{'ticker': 'ABC', 'relevance_score': '0.8', 'ticker_sentiment_score': str(s)}]}
        p = {'feed': [art('ABC wins big contract!', 'a.com', .5), art('ABC Wins Big Contract', 'b.com', .5), art('ABC margins', 'c.com', -.1)]}
        x = L.from_news_sentiment(p, '2026-10-20')[0]
        self.assertEqual((x['role'], x['strength']['articles'], x['strength']['sources']), ('attention', 2, 3))

    def test_estimate_revisions_and_surprises(self):
        est = {'symbol': 'ABC', 'estimates': [{'date': '2026-12-31', 'horizon': 'fiscal year', 'eps_estimate_average': '2.20',
                                               'eps_estimate_average_90_days_ago': '2.00', 'eps_estimate_revision_up_trailing_30_days': '5',
                                               'eps_estimate_revision_down_trailing_30_days': '1'}]}
        x = L.from_estimate_revisions(est, '2026-10-20')[0]
        self.assertEqual((x['kind'], x['direction']), ('estimate_revision_up', 'positive'))
        ear = {'symbol': 'ABC', 'quarterlyEarnings': [{'reportedDate': '2026-07-30', 'surprisePercentage': '8.1'},
                                                      {'reportedDate': '2026-04-30', 'surprisePercentage': '6.0'},
                                                      {'reportedDate': '2026-11-05', 'surprisePercentage': '-20'}]}   # after as_of: unused
        self.assertEqual(L.from_earnings_surprises(ear, '2026-10-20')[0]['kind'], 'earnings_beats')

    def test_lunarcrush_table(self):
        t = ("| Stock | AltRank | Galaxy Score | Price | Price Change 24h | Engagements | Mentions | Creators | Sentiment | Social Dominance |"
             " Market Cap | Market Dominance | Trading Volume | TopicRank |\n| ----- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
             "| [Tesla, Inc. (TSLA)](/topic/tesla) | 20 | 52.40 | $368.10 | 3.80% | 491,751,655 | 113,010 | 69,733 | 73% | 11.20% | $1 | 1.5% | $1 | 57 |\n"
             "| [Quiet Co (QCO)](/topic/q) | 900 | 40 | $10 | 0% | 1,000 | 12 | 9 | 60% | 0.01% | $1 | 0% | $1 | 999 |")
        rows = L.parse_lunarcrush_table(t)
        self.assertEqual([(r['symbol'], r['social_dominance']) for r in rows], [('TSLA', 11.2), ('QCO', .01)])
        got = {x['symbol']: x['kind'] for x in L.from_social(rows, '2026-10-20')}
        self.assertEqual(got, {'TSLA': 'crowded', 'QCO': 'social_mentions'})


class MergeTests(unittest.TestCase):
    def test_merge_dedupes_and_cards_keep_red_flags_apart(self):
        a = L.lead('ABC', 'sec_8k_items', 'signal', 'material_agreement', '2026-10-01', 'contract', 'sec:1', 'positive', independence_key='k1')
        b = dict(a, channel='news_sentiment')                                            # same story again: one piece of evidence
        c = L.lead('ABC', 'sec_form_index', 'red_flag', 'late_annual_report', '2026-10-02', 'late', 'sec:2', 'negative')
        m = L.merge([a, b, c])['ABC']
        self.assertEqual((m['positive_signals'], m['red_flags']), (1, ['late_annual_report']))
        sig = L.card_signals({'ABC': m})
        scan = {'lenses': {ln: [] for ln in candidates.LANES_HE}, 'shortlist': [], 'dimensions': {'ABC': {}}, 'excluded': {}}
        self.assertEqual(len(sig['ABC']), 2)
        # a red flag alone never opens a card
        self.assertEqual(candidates.cards(scan, signals={'ABC': [x for x in sig['ABC'] if x['kind'] == 'red_flag']})['cards'], [])

    def test_channels_folder(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'edgar'))
            open(os.path.join(d, 'edgar', 'form_2026_QTR4.idx'), 'w').write(IDX)
            json.dump([{'symbol': 'TGT', 'channel': 'owner_channels', 'role': 'lead', 'kind': 'thesis', 'date': '2026-10-10',
                        'statement': 'long thesis on X', 'source': 'https://x.com/example/1'}], open(os.path.join(d, 'leads_x.json'), 'w'))
            rows = [{'symbol': 'TGT', 'source': {'cik': 1001}, 'metrics': {}}, {'symbol': 'LATE', 'source': {'cik': 1003}, 'metrics': {}}]
            got = channels.collect(d, rows, '2026-10-20')
            self.assertEqual(got['channels']['sec_form_index'], 2)
            self.assertEqual(got['channels']['normalized_leads'], 1)
            self.assertIn('news_sentiment', got['missing'])
            self.assertIn('ספין-אוף', channels.summary_he(L.merge(got['leads']), got['prelisting']))


if __name__ == '__main__':
    unittest.main()
