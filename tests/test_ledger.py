import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from fundamental_engine.ledger import Ledger, _h
from fundamental_engine.mcp import Server


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


def _chain(path, bodies):
    """Write already-hashed rows (as an older engine version would have) to a ledger file."""
    rows, prev = [], None
    for body in bodies:
        row = dict(body, prev=prev); row['hash'] = _h(row); prev = row['hash']; rows.append(row)
    with open(path, 'w') as f:
        f.write(''.join(json.dumps(r) + '\n' for r in rows))


class RecordingLagTests(unittest.TestCase):
    """Finding 5: a forecast is recorded when it is made; late recordings never count as skill."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.clock = Clock('2026-10-19T12:00:00+00:00')

    def test_backdated_made_at_refused_on_fresh_ledger(self):
        L = Ledger(os.path.join(self.dir, 'fresh.jsonl'), clock=self.clock)
        with self.assertRaisesRegex(ValueError, 'days before the system recording time'):
            L.forecast(id='syn-rev', entity='SYN', made_at='2024-01-02', metric='revenue', period_end='2026-12-31',
                       scenarios={'bear': 90, 'base': 100, 'bull': 110}, model_version='x', price=50.0, **REV)
        with self.assertRaisesRegex(ValueError, 'days before the system recording time'):
            L.thesis(thesis_id='T', entity='SYN', made_at='2026-10-11', record={}, model_version='x')
        # up to seven days of lag is a recording delay, not a backdate
        row = L.forecast(id='syn-rev', entity='SYN', made_at='2026-10-12', metric='revenue', period_end='2026-12-31',
                         scenarios={'bear': 90, 'base': 100, 'bull': 110}, model_version='x', **REV)
        self.assertEqual(row['recording_lag_days'], 7)

    def test_configured_lag_is_allowed_but_late_rows_never_count(self):
        L = Ledger(os.path.join(self.dir, 'cfg.jsonl'), clock=self.clock, max_recording_lag_days=400)
        L.forecast(id='late', entity='SYN', made_at='2026-01-02', metric='revenue', period_end='2026-12-31',
                   scenarios={'bear': 90, 'base': 100, 'bull': 110}, model_version='x', price=50.0, **REV)
        L.forecast(id='ontime', entity='OTH', made_at='2026-10-19', metric='revenue', period_end='2026-12-31',
                   scenarios={'bear': 90, 'base': 100, 'bull': 110}, model_version='x', price=50.0, **REV)
        self.clock.t = '2027-02-15T12:00:00+00:00'
        L.outcome(forecast_id='late', actual=104, source_id='10k', observed_at='2027-02-10', price_now=150.0, benchmark_return=.3, **REV)
        L.outcome(forecast_id='ontime', actual=80, source_id='10k', observed_at='2027-02-10', price_now=45.0, benchmark_return=.0, **REV)
        s = L.score()
        items = {i['id']: i for i in s['items']}
        self.assertTrue(items['late']['late_recording'])
        self.assertFalse(items['ontime']['late_recording'])
        self.assertEqual(s['resolved'], 2)
        self.assertEqual(s['excluded_late_recordings'], 1)
        self.assertEqual(s['bear_bull_coverage'], 0.0)                       # only the on-time miss counts
        self.assertAlmostEqual(s['mean_excess_return'], 45 / 50 - 1)         # the hindsight pick return is excluded
        self.assertAlmostEqual(s['median_abs_base_error'], .2)

    def test_late_rows_already_on_file_are_excluded(self):
        path = os.path.join(self.dir, 'old.jsonl')
        _chain(path, [
            {'type': 'forecast', 'ledger_version': 2, 'id': 'SYN:rev:FY26', 'entity': 'SYN', 'made_at': '2024-01-02',
             'recorded_at': '2026-10-19T12:00:00+00:00', 'recording_lag_days': 1021, 'metric': 'revenue', 'period_end': '2026-12-31',
             'scenarios': {'bear': 90, 'base': 100, 'bull': 110}, 'scenario_order': 'ascending', **REV, 'price': 50.0, 'benchmark': None,
             'model_version': 'x', 'inputs_hash': None, 'thesis_breakers': [], 'note': '', 'thesis_id': None, 'supersedes': None, 'context': {}},
            {'type': 'outcome', 'ledger_version': 2, 'forecast_id': 'SYN:rev:FY26', 'observed_at': '2027-02-10',
             'recorded_at': '2027-02-15T12:00:00+00:00', 'actual': 104.0, **REV, 'source_id': '10-K', 'benchmark_return': .3,
             'price_now': 150.0, 'corrects': None, 'correction_reason': None}])
        L = Ledger(path, clock=Clock('2027-03-01T00:00:00+00:00'))
        self.assertTrue(L.verify()['ok'])
        s = L.score()
        self.assertTrue(s['items'][0]['late_recording'])
        self.assertEqual(s['excluded_late_recordings'], 1)
        self.assertIsNone(s['mean_excess_return'])
        self.assertIsNone(s['bear_bull_coverage'])
        self.assertIsNone(s['median_abs_base_error'])


def _today():
    return datetime.now(timezone.utc).date()


class LedgerFileTests(unittest.TestCase):
    """Finding 7: the file appears on the first successful append; MCP names stay inside the corpus directory."""
    def setUp(self):
        self.cwd = os.getcwd()
        self.sandbox = tempfile.mkdtemp()           # relative paths that escaped would land here, not in the repository
        self.corpus = os.path.join(self.sandbox, 'corpus')
        self.outside = os.path.join(self.sandbox, 'outside')
        os.makedirs(self.corpus); os.makedirs(self.outside)
        os.chdir(self.sandbox)

    def tearDown(self):
        os.chdir(self.cwd)

    def entry(self, **over):
        t = _today()
        e = dict(id='mcp-rev', entity='SYN', made_at=t.isoformat(), metric='revenue', period_end=(t + timedelta(days=400)).isoformat(),
                 scenarios={'bear': 90, 'base': 100, 'bull': 110}, model_version='x', **REV)
        e.update(over)
        return e

    def test_failing_calls_create_no_file(self):
        path = os.path.join(self.corpus, 'new.jsonl')
        L = Ledger(path, clock=Clock('2026-10-19T12:00:00+00:00'))
        self.assertFalse(os.path.exists(path))
        with self.assertRaisesRegex(ValueError, 'unit required'):
            L.forecast(id='a', entity='X', made_at='2026-10-19', metric='m', period_end='2027-01-01',
                       scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v')
        self.assertFalse(os.path.exists(path))
        self.assertEqual(L.verify(), {'ok': True, 'entries': 0, 'legacy_entries': 0})
        self.assertEqual(L.score()['resolved'], 0)
        self.assertEqual(L.lines(), [])
        self.assertFalse(os.path.exists(path))
        L.forecast(id='a', entity='X', made_at='2026-10-19', metric='m', period_end='2027-01-01',
                   scenarios={'bear': 1, 'base': 2, 'bull': 3}, model_version='v', **REV)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(L.verify()['ok'])

    def test_mcp_ledger_names_resolve_under_the_corpus_directory(self):
        srv = Server(os.path.join(self.corpus, 'sources.sqlite'))
        escape = os.path.join(self.outside, 'x.jsonl')
        for bad in (escape, '../outside/x.jsonl', 'sub/../../outside/x.jsonl', '..', '', 'sources.sqlite', '~/x.jsonl'):
            with self.assertRaises(ValueError, msg=bad):
                srv.call('forecast_ledger', {'path': bad, 'action': 'forecast', 'entry': self.entry()})
        os.symlink(escape, os.path.join(self.corpus, 'link.jsonl'))            # a link inside that points outside
        with self.assertRaises(ValueError):
            srv.call('forecast_ledger', {'path': 'link.jsonl', 'action': 'forecast', 'entry': self.entry()})
        self.assertEqual(os.listdir(self.outside), [])
        self.assertFalse(os.path.exists(os.path.join(self.sandbox, 'x.jsonl')))
        # failing calls leave nothing behind
        with self.assertRaises(ValueError):
            srv.call('forecast_ledger', {'path': 'mine.jsonl', 'action': 'bogus'})
        with self.assertRaises(ValueError):
            srv.call('forecast_ledger', {'path': 'mine.jsonl', 'action': 'forecast', 'entry': self.entry(unit=None)})
        with self.assertRaises(ValueError):
            srv.call('forecast_ledger', {'path': 'mine.jsonl', 'action': 'verify'})       # nothing there yet: say so
        self.assertFalse(os.path.exists(os.path.join(self.corpus, 'mine.jsonl')))
        # the first successful append creates the file inside the corpus directory
        row = srv.call('forecast_ledger', {'path': 'mine.jsonl', 'action': 'forecast', 'entry': self.entry()})
        self.assertEqual(row['id'], 'mcp-rev')
        self.assertTrue(os.path.exists(os.path.join(self.corpus, 'mine.jsonl')))
        self.assertEqual(srv.call('forecast_ledger', {'path': 'mine.jsonl', 'action': 'verify'})['entries'], 1)
        self.assertEqual(sorted(os.listdir(self.corpus)), ['link.jsonl', 'mine.jsonl'])


if __name__ == '__main__':
    unittest.main()
