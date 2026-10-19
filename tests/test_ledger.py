import json
import os
import tempfile
import unittest
from fundamental_engine.ledger import Ledger, _h


class Clock:
    """Injectable system clock (the ledger stamps recorded_at from it)."""
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


REV = dict(unit='USD', currency='USD', basis='reported_revenue')


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'ledger.jsonl')
        self.clock = Clock('2026-10-19T12:00:00+00:00')
        self.L = Ledger(self.path, clock=self.clock)
        self.L.forecast(id='nbis-rev-2027', entity='NBIS', made_at='2026-10-19', metric='revenue_usd', period_end='2027-12-31',
                        scenarios={'bear': 9.0e9, 'base': 11.0e9, 'bull': 14.3e9}, price=173.39, benchmark='SPY', model_version='0.10.0', **REV)

    def test_chain_and_score(self):
        self.clock.t = '2028-02-16T09:00:00+00:00'
        # legacy keyword name: recorded_at= is read as the date the actual became public
        o = self.L.outcome(forecast_id='nbis-rev-2027', recorded_at='2028-02-15', actual=12.0e9, source_id='6k_q4_2027',
                           benchmark_return=0.10, price_now=200.0, **REV)
        self.assertEqual(o['observed_at'], '2028-02-15')
        self.assertEqual(o['recorded_at'], '2028-02-16T09:00:00+00:00')
        self.assertTrue(self.L.verify()['ok'])
        s = self.L.score()
        self.assertAlmostEqual(s['items'][0]['base_error'], 1 / 11, places=6)
        self.assertTrue(s['items'][0]['inside_bear_bull'])
        self.assertFalse(s['items'][0]['legacy'])
        self.assertAlmostEqual(s['items'][0]['excess_return'], 200 / 173.39 - 1 - 0.10)

    def test_recorded_at_is_system_time(self):
        row = self.L.lines()[0]
        self.assertEqual(row['recorded_at'], '2026-10-19T12:00:00+00:00')
        self.assertEqual(row['recording_lag_days'], 0)
        self.assertEqual(row['ledger_version'], 2)

    def test_tamper_detected_and_blocks_append(self):
        with open(self.path) as f:
            rows = f.read().splitlines()
        row = json.loads(rows[0]); row['scenarios']['base'] = 13e9
        with open(self.path, 'w') as f:
            f.write(json.dumps(row) + '\n')
        self.assertFalse(self.L.verify()['ok'])
        with self.assertRaises(ValueError):
            self.L.forecast(id='x', entity='X', made_at='2026-10-19', metric='m', period_end='2027-01-01',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)

    def test_no_lookahead_outcome(self):
        self.clock.t = '2028-03-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2027-06-01', actual=1, source_id='s', **REV)
        # the wall clock decides whether the period has ended, not the caller's date
        self.clock.t = '2026-11-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'has not ended|future'):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=1, source_id='s', **REV)

    def test_no_silent_rewrite(self):
        with self.assertRaises(ValueError):
            self.L.forecast(id='nbis-rev-2027', entity='NBIS', made_at='2026-11-11', metric='revenue_usd', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)

    def test_made_at_after_recorded_at_rejected(self):
        with self.assertRaisesRegex(ValueError, 'later than the system recording time'):
            self.L.forecast(id='f2', entity='X', made_at='2026-10-20', metric='revenue_usd', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)

    def test_backdating_rejected(self):
        with self.assertRaisesRegex(ValueError, 'backdating'):
            self.L.forecast(id='f2', entity='X', made_at='2026-10-18', metric='revenue_usd', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)
        # same day, with or without a time, is not backdating
        self.L.forecast(id='f3', entity='X', made_at='2026-10-19T08:00:00+00:00', metric='revenue_usd', period_end='2027-12-31',
                        scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)
        with self.assertRaisesRegex(ValueError, 'backdating'):
            self.L.forecast(id='f4', entity='X', made_at='2026-10-19T07:00:00+00:00', metric='revenue_usd', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)

    def test_cannot_forecast_past_period(self):
        with self.assertRaisesRegex(ValueError, 'cannot forecast the past'):
            self.L.forecast(id='f2', entity='X', made_at='2026-10-19', metric='revenue_usd', period_end='2026-09-30',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)
        # a period ending today has not ended yet
        self.L.forecast(id='f3', entity='X', made_at='2026-10-19', metric='revenue_usd', period_end='2026-10-19',
                        scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)

    def test_clock_running_backwards_refused(self):
        self.clock.t = '2026-10-18T12:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'clock'):
            self.L.event(id='e', entity='NBIS', kind='note', occurred_at='2026-10-18', description='x')

    def test_duplicate_outcome_rejected_and_correction_linked(self):
        self.clock.t = '2028-02-16T09:00:00+00:00'
        first = self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=12.0e9, source_id='6k', **REV)
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=12.5e9, source_id='6k', **REV)
        with self.assertRaisesRegex(ValueError, 'in force'):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=12.5e9, source_id='6k', corrects='0' * 64,
                           correction_reason='typo', **REV)
        with self.assertRaisesRegex(ValueError, 'correction_reason'):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=12.5e9, source_id='6k', corrects=first['hash'], **REV)
        self.clock.t = '2028-03-02T09:00:00+00:00'
        fix = self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-03-01', actual=10.0e9, source_id='20f',
                             corrects=first['hash'], correction_reason='6-K figure included a disposed segment', **REV)
        self.assertEqual(fix['corrects'], first['hash'])
        self.assertEqual(sum(r['type'] == 'outcome' for r in self.L.lines()), 2)   # the original stays on file
        item = self.L.score()['items'][0]
        self.assertEqual(item['actual'], 10.0e9)
        self.assertTrue(item['corrected'])
        with self.assertRaises(ValueError):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-03-01', actual=11e9, source_id='x', corrects=first['hash'],
                           correction_reason='stale link', **REV)

    def test_definitions_required_and_mismatch_refused(self):
        with self.assertRaisesRegex(ValueError, 'unit required'):
            self.L.forecast(id='f2', entity='X', made_at='2026-10-19', metric='revenue_usd', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v')
        with self.assertRaisesRegex(ValueError, 'currency'):
            self.L.forecast(id='f2', entity='X', made_at='2026-10-19', metric='revenue_usd', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', unit='USD', basis='reported_revenue')
        self.L.forecast(id='sh', entity='X', made_at='2026-10-19', metric='shares', period_end='2027-12-31', scenario_order='descending',
                        scenarios={'bear': 120, 'base': 110, 'bull': 105}, model_version='v', unit='shares', currency=None,
                        basis='period_end_shares_outstanding')
        self.clock.t = '2028-02-16T09:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'basis differs'):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=12e9, source_id='s',
                           unit='USD', currency='USD', basis='non_gaap_revenue')
        with self.assertRaisesRegex(ValueError, 'currency differs'):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=12e9, source_id='s',
                           unit='USD', currency='EUR', basis='reported_revenue')
        with self.assertRaisesRegex(ValueError, 'unit required'):
            self.L.outcome(forecast_id='nbis-rev-2027', observed_at='2028-02-15', actual=12e9, source_id='s')

    def test_scenario_orders(self):
        with self.assertRaisesRegex(ValueError, 'bear <= base <= bull'):
            self.L.forecast(id='a', entity='X', made_at='2026-10-19', metric='m', period_end='2027-12-31',
                            scenarios={'bear': 3, 'base': 2, 'bull': 1}, model_version='v', **REV)
        with self.assertRaisesRegex(ValueError, 'note'):
            self.L.forecast(id='b', entity='X', made_at='2026-10-19', metric='m', period_end='2027-12-31', scenario_order='crossing',
                            scenarios={'bear': 2, 'base': 3, 'bull': 1}, model_version='v', **REV)
        self.L.forecast(id='c', entity='X', made_at='2026-10-19', metric='m', period_end='2027-12-31', scenario_order='crossing',
                        note='bull invests more capex early', scenarios={'bear': 2, 'base': 3, 'bull': 1}, model_version='v', **REV)
        self.clock.t = '2028-02-16T09:00:00+00:00'
        self.L.outcome(forecast_id='c', observed_at='2028-02-15', actual=1.5, source_id='s', **REV)
        item = next(i for i in self.L.score()['items'] if i['id'] == 'c')
        self.assertTrue(item['inside_bear_bull'])            # envelope 1..3

    def test_supersedes_keeps_original(self):
        self.clock.t = '2026-12-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            self.L.forecast(id='nbis-rev-2027-v2', entity='NBIS', made_at='2026-12-01', metric='ebit', period_end='2027-12-31',
                            scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', supersedes='nbis-rev-2027', **REV)
        row = self.L.forecast(id='nbis-rev-2027-v2', entity='NBIS', made_at='2026-12-01', metric='revenue_usd', period_end='2027-12-31',
                              scenarios={'bear': 8e9, 'base': 10e9, 'bull': 12e9}, model_version='v', supersedes='nbis-rev-2027', **REV)
        self.assertEqual(row['supersedes'], 'nbis-rev-2027')
        self.assertEqual(len([r for r in self.L.lines() if r['type'] == 'forecast']), 2)

    def test_external_event_requires_evidence(self):
        self.clock.t = '2027-04-01T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'evidence'):
            self.L.event(id='e1', entity='NBIS', kind='external_shock', occurred_at='2027-03-15', description='export ban')
        with self.assertRaisesRegex(ValueError, 'future'):
            self.L.event(id='e1', entity='NBIS', kind='external_shock', occurred_at='2027-05-15', description='export ban',
                         evidence_ids=['8k'], why_unforeseeable='new rule')
        e = self.L.event(id='e1', entity='NBIS', kind='external_shock', occurred_at='2027-03-15', description='export ban',
                         evidence_ids=['8k_2027-03-16'], why_unforeseeable='rule announced without consultation', affects=['revenue_usd'])
        self.assertEqual(e['recorded_at'], '2027-04-01T00:00:00+00:00')

    def test_legacy_entries_readable(self):
        path = os.path.join(tempfile.mkdtemp(), 'legacy.jsonl')
        rows, prev = [], None
        for body in ({'type': 'forecast', 'id': 'old-rev', 'entity': 'OLD', 'made_at': '2025-01-10', 'metric': 'revenue_usd',
                      'period_end': '2025-12-31', 'scenarios': {'bear': 90, 'base': 100, 'bull': 120}, 'price': 10.0,
                      'benchmark': 'SPY', 'model_version': '0.10.0', 'inputs_hash': None, 'thesis_breakers': [], 'note': ''},
                     {'type': 'outcome', 'forecast_id': 'old-rev', 'recorded_at': '2026-02-20', 'actual': 95.0,
                      'source_id': '10k', 'benchmark_return': None, 'price_now': 12.0}):
            row = dict(body, prev=prev); row['hash'] = _h(row); prev = row['hash']; rows.append(row)
        with open(path, 'w') as f:
            f.write(''.join(json.dumps(r) + '\n' for r in rows))
        L = Ledger(path, clock=Clock('2026-10-19T00:00:00+00:00'))
        v = L.verify()
        self.assertTrue(v['ok']); self.assertEqual(v['legacy_entries'], 2)
        self.assertTrue(all(r['legacy'] for r in L.entries()))
        item = L.score()['items'][0]
        self.assertTrue(item['legacy']); self.assertTrue(item['basis_unverified']); self.assertTrue(item['inside_bear_bull'])
        self.assertAlmostEqual(item['stock_return'], .2)
        # new entries chain onto legacy ones
        L.forecast(id='new', entity='OLD', made_at='2026-10-19', metric='revenue_usd', period_end='2027-12-31',
                   scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)
        self.assertTrue(L.verify()['ok'])


if __name__ == '__main__':
    unittest.main()
