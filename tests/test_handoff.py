"""handoff.py: identity check before analysis, lane backfill order, listing-price fallback, pipeline run from a company
folder and the cross-company comparison. Payloads are SYNTHETIC except the real ADBE FactBook fixture used for one
end-to-end casefile."""
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from fundamental_engine import handoff as H, pipeline
from fundamental_engine import facts as F

ADBE = json.loads((Path(__file__).resolve().parent / 'fixtures/adbe_factbook_2026-10-19.json').read_text())


def facts(units, shares=None):
    """SYNTHETIC companyfacts: {unit: count of monetary facts}, optional dei share count."""
    gaap = {f'Concept{u}{i}': {'units': {u: [{'end': '2025-12-31', 'val': 1.0}]}} for u, n in units.items() for i in range(n)}
    out = {'cik': 1, 'facts': {'us-gaap': gaap}}
    if shares:
        out['facts']['dei'] = {'EntityCommonStockSharesOutstanding': {'units': {'shares': [{'end': '2026-03-31', 'val': shares}]}}}
    return out


class IdentityTests(unittest.TestCase):
    def test_usd_reporter_with_ordinary_shares_passes(self):
        chk = H.identity_check(facts({'USD': 50}), {'name': 'Synthetic Co Ordinary Shares', 'lastsale': '$40', 'marketCap': '4e9'})
        self.assertEqual((chk['status'], chk['reporting_currency'], chk['depositary_shares']), ('ok', 'USD', False))

    def test_foreign_currency_reporter_is_blocked(self):
        chk = H.identity_check(facts({'CNY': 60, 'USD': 30}))
        self.assertEqual(chk['status'], 'blocked')
        self.assertIn('reporting currency CNY', chk['reasons'][0])

    def test_latest_annual_report_decides_the_currency(self):
        def rows(unit, n, filed, accn):
            return {f'C{unit}{filed}{i}': {'units': {unit: [{'end': filed, 'val': 1.0, 'form': '10-K', 'filed': filed, 'accn': accn}]}}
                    for i in range(n)}
        gaap = {**rows('RUB', 80, '2021-03-01', 'old'), **rows('USD', 30, '2026-02-20', 'new')}     # SYNTHETIC: history in RUB
        chk = H.identity_check({'facts': {'us-gaap': gaap}})
        self.assertEqual((chk['status'], chk['reporting_currency']), ('ok', 'USD'))
        self.assertIn('new', chk['currency_basis'])

    def test_depositary_ratio_must_be_one_or_modelled(self):
        listing = {'name': 'Synthetic Holdings American Depositary Shares', 'lastsale': '$7.00', 'marketCap': '700000000'}
        five = H.identity_check(facts({'USD': 50}, shares=500e6), listing)                 # 100m ADS, 500m shares
        self.assertEqual(five['status'], 'blocked')
        self.assertAlmostEqual(five['implied_shares_per_ads'], 5.0)
        one = H.identity_check(facts({'USD': 50}, shares=102e6), listing)                  # 1:1 within dated share counts
        self.assertEqual(one['status'], 'ok')

    def test_foreign_private_issuer_is_noted(self):
        chk = H.identity_check(facts({'USD': 50}), None, {'annual_form': '20-F'})
        self.assertEqual(chk['status'], 'ok')
        self.assertIn('annual reports only', chk['note'])


class QueueAndPriceTests(unittest.TestCase):
    def test_lane_backfill_follows_shortlist_then_rank_and_skips_no(self):
        cs = {'cards': [
            {'symbol': 'A', 'lanes': [{'lens': 'growth_lane', 'rank': 1}], 'shortlist_position': 3, 'full_analysis': {'decision': 'yes'}},
            {'symbol': 'B', 'lanes': [{'lens': 'other', 'rank': 1}, {'lens': 'growth_lane', 'rank': 4}], 'full_analysis': {'decision': 'maybe'}},
            {'symbol': 'C', 'lanes': [{'lens': 'growth_lane', 'rank': 2}], 'full_analysis': {'decision': 'maybe'}},
            {'symbol': 'D', 'lanes': [{'lens': 'growth_lane', 'rank': 3}], 'full_analysis': {'decision': 'no'}}]}
        self.assertEqual(H.lane_candidates(cs, 'growth_lane'), ['A', 'C', 'B'])

    def test_session_date_before_the_open_is_the_prior_close(self):
        self.assertEqual(H.session_date('2026-10-20T08:01:41+00:00'), ('2026-10-19', True))
        self.assertEqual(H.session_date('2026-10-19T08:00:00Z'), ('2026-10-16', True))       # Monday morning -> Friday
        self.assertEqual(H.session_date('2026-10-20T15:00:00Z'), ('2026-10-20', False))      # intraday last sale

    def test_listing_price_feeds_the_factbook_when_no_broker_quote(self):
        with tempfile.TemporaryDirectory() as d:
            lp = {'symbol': 'SYNT', 'price': 41.5, 'date': '2026-10-19', 'provider': 'nasdaq', 'dataset': 'screener stocks (last sale)',
                  'retrieved_at_utc': '2026-10-20T08:01:41+00:00', 'note': 'SYNTHETIC'}
            with open(os.path.join(d, 'listing__price-SYNT__current.json'), 'w') as f:
                json.dump(lp, f)
            fb = pipeline.factbook_from_run_dir(d, {'ticker': 'SYNT', 'currency': 'USD'}, '2026-10-20')
            self.assertEqual((fb['market']['price']['value'], fb['market']['price']['source_id']), (41.5, 'listing:price'))
            self.assertEqual(fb['sources']['listing:price']['kind'], 'market_feed')
            self.assertTrue(any('not a broker quote' in n for n in fb['collection_notes']))
            later = pipeline.factbook_from_run_dir(d, {'ticker': 'SYNT', 'currency': 'USD'}, '2026-10-18')
            self.assertNotIn('price', later['market'])                                       # never relabelled to the cutoff


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cf = json.loads(json.dumps(pipeline.run(deepcopy(ADBE), {'analyst': 'Synthetic Analyst'}), default=str))

    def test_rows_keep_dimensions_separate_and_drafts_visible(self):
        card = {'symbol': 'ADBE', 'lanes': [{'lens': 'quality_fair_price', 'rank': 1}], 'why_found': 'test',
                'risks': ['r1'], 'falsifiers': ['f1']}
        comp = H.compare([self.cf], {'cards': [card]})
        r = comp['rows'][0]
        self.assertEqual((r['symbol'], r['group']), ('ADBE', 'established_quality'))
        self.assertEqual(set(r['levels']), set(H.DIMS))
        self.assertIn('ADBE', comp['drafts'])                       # no independent review: a draft, said on the row
        self.assertTrue(r['price_claim_withheld'])
        self.assertNotIn('score', json.dumps(comp).lower().replace('no composite score', ''))
        self.assertIn('research comparison, not a recommendation', comp['meaning'])
        txt = H.render_he(comp)
        for label in ('איכות עסק', 'תשואה שנתית משתמעת', 'הפסד עד התרחיש הדובי', 'טיוטה'):
            self.assertIn(label, txt)

    def test_run_company_reads_inputs_from_the_folder(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'raw'))
            with open(os.path.join(d, 'company.json'), 'w') as f:
                json.dump({'ticker': 'SYNT', 'currency': 'USD', 'name': 'Synthetic Co'}, f)
            with open(os.path.join(d, 'inputs.json'), 'w') as f:
                json.dump({'analyst': 'Synthetic Analyst'}, f)
            fb, cf = H.run_company(d, '2026-10-20')
            self.assertEqual(cf['company']['ticker'], 'SYNT')
            self.assertEqual(cf['inputs_sha256'], F.digest({'analyst': 'Synthetic Analyst'}))


if __name__ == '__main__':
    unittest.main()
