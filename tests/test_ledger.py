import json
import os
import tempfile
import unittest
from fundamental_engine.ledger import Ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'ledger.jsonl')
        self.L = Ledger(self.path)
        self.L.forecast(id='nbis-rev-2027', entity='NBIS', made_at='2026-10-19', metric='revenue_usd', period_end='2027-12-31',
                        scenarios={'bear': 9.0e9, 'base': 11.0e9, 'bull': 14.3e9}, price=173.39, benchmark='SPY', model_version='0.10.0')

    def test_chain_and_score(self):
        self.L.outcome(forecast_id='nbis-rev-2027', recorded_at='2028-02-15', actual=12.0e9, source_id='6k_q4_2027',
                       benchmark_return=0.10, price_now=200.0)
        self.assertTrue(self.L.verify()['ok'])
        s = self.L.score()
        self.assertAlmostEqual(s['items'][0]['base_error'], 1 / 11, places=6)
        self.assertTrue(s['items'][0]['inside_bear_bull'])
        self.assertAlmostEqual(s['items'][0]['excess_return'], 200 / 173.39 - 1 - 0.10)

    def test_tamper_detected_and_blocks_append(self):
        rows = open(self.path).read().splitlines()
        row = json.loads(rows[0]); row['scenarios']['base'] = 13e9
        open(self.path, 'w').write(json.dumps(row) + '\n')
        self.assertFalse(self.L.verify()['ok'])
        with self.assertRaises(ValueError):
            self.L.forecast(id='x', entity='X', made_at='2026-10-20', metric='m', period_end='2027-01-01',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v')

    def test_no_lookahead_outcome(self):
        with self.assertRaises(ValueError):
            self.L.outcome(forecast_id='nbis-rev-2027', recorded_at='2027-06-01', actual=1, source_id='s')

    def test_no_silent_rewrite(self):
        with self.assertRaises(ValueError):
            self.L.forecast(id='nbis-rev-2027', entity='NBIS', made_at='2026-11-11', metric='revenue_usd', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v')


if __name__ == '__main__':
    unittest.main()
