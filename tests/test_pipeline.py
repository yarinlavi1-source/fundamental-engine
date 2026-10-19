"""Pipeline loader and summary: later-dated information is skipped (never relabelled), drafts never read as conclusions.
All data here is SYNTHETIC."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from fundamental_engine import pipeline, portfolio
from fundamental_engine import facts as F

CO = {'ticker': 'SYNT', 'currency': 'USD', 'name': 'Synthetic Co'}
URL = 'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/synt-20260828.htm'


def raw_dir(tmp):
    ts = int(datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc).timestamp())
    files = {
        'fmp__shares-float__current.json': [{'symbol': 'SYNT', 'date': '2026-10-19 08:00:00', 'outstandingShares': 100e6, 'source': URL}],
        'fmp__financial-estimates__annual.json': [{'date': '2027-11-30', 'revenueAvg': 1e9, 'revenueLow': .9e9, 'revenueHigh': 1.1e9,
                                                    'numAnalystsRevenue': 10}],
        'ib__price-snapshot-SYNT__current.json': {'last': {'price': 50.0, 'ts': ts, 'is_close': False}},
    }
    calls = [{'file': k, 'retrieved_at_utc': '2026-10-19T22:10:00Z'} for k in files]
    files['MANIFEST.json'] = {'run_id': 'synthetic', 'calls': calls}
    for k, v in files.items():
        with open(os.path.join(tmp, k), 'w') as f:
            json.dump(v, f)
    return tmp


class LoaderLookAhead(unittest.TestCase):
    def test_later_items_skipped_not_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb = pipeline.factbook_from_run_dir(raw_dir(tmp), CO, '2026-08-01')
        self.assertNotIn('price', fb['market'])
        self.assertFalse([f for f in fb['facts'] if f['metric'] == 'shares_outstanding_vendor'])
        self.assertFalse(fb['estimates'])
        notes = ' '.join(fb['collection_notes'])
        for what in ('price snapshot', 'vendor share count', 'consensus estimates'):
            self.assertIn(what, notes)

    def test_same_day_items_kept_with_real_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb = pipeline.factbook_from_run_dir(raw_dir(tmp), CO, '2026-10-19')
        self.assertEqual(fb['market']['price']['as_of'], '2026-10-19')
        sh = [f for f in fb['facts'] if f['metric'] == 'shares_outstanding_vendor']
        self.assertEqual((sh[0]['period_end'], sh[0]['available_at']), ('2026-08-28', '2026-10-19'))
        self.assertTrue(fb['estimates'])
        self.assertEqual(fb['sources']['ib:snapshot']['retrieved_at'], '2026-10-19T22:10:00Z')   # retrieved_at_utc read


class SummaryDraft(unittest.TestCase):
    def cf(self, **verdict):
        return {'company': CO, 'as_of': '2026-10-19', 'dc': {'verdict': {'code': 'attractive_at_price', **verdict}},
                'val': {}, 'rk': {}, 'rv': {'status': 'unreviewed_draft'}}

    def test_withheld_verdict_does_not_support_price(self):
        s = pipeline.summary(self.cf(status='draft', price_claim_withheld=True))
        self.assertEqual((s['verdict_status'], s['price_claim_withheld']), ('draft', True))
        self.assertEqual(portfolio._casefile(s)['research_support'], 'draft_unreviewed')
        final = pipeline.summary(self.cf(status='final', price_claim_withheld=False))
        self.assertEqual(portfolio._casefile(final)['research_support'], 'supports_price')

    def test_missing_status_is_treated_as_withheld(self):
        self.assertTrue(pipeline.summary(self.cf())['price_claim_withheld'])


if __name__ == '__main__':
    unittest.main()
