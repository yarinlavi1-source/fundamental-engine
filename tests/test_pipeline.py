"""Pipeline loader, summary and review binding: later-dated information is skipped (never relabelled), drafts never read as
conclusions, a review covers the valuation and the analyst inputs it saw. Data here is SYNTHETIC except the real ADBE FactBook
fixture (10-K values) used for the end-to-end binding runs."""
import json
import os
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from fundamental_engine import pipeline, portfolio, review
from fundamental_engine import facts as F

ADBE = json.loads((Path(__file__).resolve().parent / 'fixtures/adbe_factbook_2026-10-19.json').read_text())

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

    def test_summary_carries_margin_hurdle_and_value_at_required_return(self):
        cf = self.cf(status='draft', price_claim_withheld=True, margin='marginal', value_at_required_return=278.5,
                     margin_detail={'status': 'marginal', 'flips': []})
        cf['dc'].update(decision_version=3, expected_return={'hurdle': .0975, 'required_return': .0975, 'value_at_required_return': 278.5,
                                                             'base_irr': .106})
        s = pipeline.summary(cf)
        self.assertEqual((s['decision_version'], s['margin'], s['hurdle'], s['value_at_required_return']), (3, 'marginal', .0975, 278.5))
        old = pipeline.summary(self.cf())                                           # an older decision contract: unknown, never invented
        self.assertEqual((old['margin'], old['hurdle'], old['value_at_required_return']), (None, None, None))


class ReviewBinding(unittest.TestCase):
    """The reviewer signs the executed valuation AND the analyst inputs (decision, card, forecast) the pipeline ran with."""

    @classmethod
    def setUpClass(cls):
        cls.base = pipeline.run(deepcopy(ADBE), {'analyst': 'Yarin Analyst'})
        if not ((cls.base.get('val') or {}).get('result') and cls.base.get('rv')):
            raise unittest.SkipTest('pipeline produced no executed valuation/review: ' + json.dumps(cls.base.get('errors'))[:200])

    def reviewer(self):
        b = self.base['rv']['binding']
        return {'reviewer': 'Second Reader', 'reviewed_at': ADBE['as_of'], 'independent': True, 'assumption_reviews': {}, 'findings': [],
                'statement': 'Read the casefile, the sources and the analyst inputs (test double).',
                'valuation_sha256': b['valuation_sha256'], 'inputs_sha256': b['inputs_sha256']}

    def test_pipeline_passes_the_inputs_hash_and_an_override_after_the_review_voids_it(self):
        b = self.base['rv']['binding']
        self.assertEqual(b['supplied_by'], 'pipeline')
        self.assertEqual(b['valuation_sha256'], self.base['val']['result']['input_sha256'])
        self.assertEqual(b['inputs_sha256'], review.inputs_digest(self.base['val'], {}))       # no decision/card/forecast inputs
        cf = pipeline.run(deepcopy(ADBE), {'analyst': 'Yarin Analyst', 'reviewer': self.reviewer()})
        rc = cf['rv']['reviewer_check']
        self.assertEqual((rc['bound_to_valuation'], rc['bound_to_inputs'], rc['self_review']), (True, True, []))
        self.assertFalse([e for e in rc['errors'] if 'sha256' in e or 'author-side' in e], rc['errors'])
        later = {'overrides': [{'target': 'business_quality', 'value': 'weak', 'evidence_ids': ['fmp:as_reported_income_annual'],
                                'justification': 'Analyst downgrade added after the review was signed.'}]}
        cf = pipeline.run(deepcopy(ADBE), {'analyst': 'Yarin Analyst', 'reviewer': self.reviewer(), 'decision': later})
        rc = cf['rv']['reviewer_check']
        self.assertEqual((rc['bound_to_valuation'], rc['bound_to_inputs'], rc['accepted']), (True, False, False))
        self.assertTrue(any('inputs_sha256 does not match' in e for e in rc['errors']), rc['errors'])
        self.assertNotEqual(cf['rv']['binding']['inputs_sha256'], self.base['rv']['binding']['inputs_sha256'])
        self.assertFalse(cf['dc']['gates']['reviewer_present'])
        self.assertTrue(pipeline.summary(cf)['price_claim_withheld'])

    def test_the_analyst_named_in_the_inputs_cannot_review(self):
        cf = pipeline.run(deepcopy(ADBE), {'analyst': ['Second  Reader'], 'reviewer': self.reviewer()})
        rc = cf['rv']['reviewer_check']
        self.assertEqual((rc['independent'], rc['accepted'], rc['self_review']), (False, False, ['Second  Reader']))
        self.assertTrue(rc['bound_to_inputs'])                                      # the analyst name is not part of the signed hash

    def test_summary_reads_the_decision_contract(self):
        s, dc = pipeline.summary(self.base), self.base['dc']
        self.assertEqual(s['decision_version'], 3)
        self.assertEqual((s['margin'], s['hurdle'], s['value_at_required_return']),
                         (dc['verdict'].get('margin'), dc['expected_return']['hurdle'], dc['verdict'].get('value_at_required_return')))
        self.assertIsNotNone(s['hurdle'])


class SegmentViewTests(unittest.TestCase):
    def test_second_value_when_segments_disagree_with_the_consolidated_path(self):
        fb = deepcopy(ADBE)
        F.add_source(fb, 'sec:annual_report', 'sec', '10-K', 'filing_derived', '2026-01-20T00:00:00Z', note='test business map')
        fast, slow = 9e9, 15e9                                                  # SYNTHETIC segment table (not ADBE's)
        fb['business_map'] = {'source_id': 'sec:annual_report', 'source': {'form': '10-K'}, 'tables': {'segment': {
            'reconciliation': {'status': 'reconciles'},
            'derived': {'Fast': {'latest': fast, 'cagr': .55, 'share_of_total': .375, 'span': '2023-2025'},
                        'Slow': {'latest': slow, 'cagr': .0, 'share_of_total': .625, 'span': '2023-2025'}}}}}
        cf = pipeline.run(fb)
        sv = cf['segment_view']
        self.assertEqual(sv['status'], 'computed')
        self.assertGreater(sv['growth_segment_path'][0], sv['growth_consolidated'][0])
        self.assertGreater(sv['base_value_segment_path'], sv['base_value_consolidated'])
        base = pipeline.summary(cf)['base_value']
        self.assertAlmostEqual(base, sv['base_value_consolidated'])            # the base case itself is unchanged


class ScaleUpInCyclicalIndustryTests(unittest.TestCase):
    """Regression from the 2026-10-21 valuation-gap diagnosis: a SYNTHETIC Uber-like history ('travel' industry word, revenue up
    every year, margins -22% .. +11%) was read as a cyclical peak and normalized to its -2% start-up mean (value 0.08x price;
    0.49x with the engine's own inputs.cyclical structural treatment)."""

    @staticmethod
    def uber_like(price=95.):
        rev, om, sbc = [17.5e9, 31.9e9, 37.3e9, 44.0e9, 52.0e9], [-.22, -.06, .03, .06, .11], [.09, .06, .05, .043, .035]
        as_of = '2026-10-20'
        fb = F.new({'ticker': 'UBX', 'name': 'Synthetic travel platform', 'currency': 'USD', 'fiscal_year_end': '12-31',
                    'sector': 'Industrials', 'industry': 'Travel Services'}, as_of)
        F.add_source(fb, 'syn:fs', 'synthetic', 'test-statements', 'filing_derived', as_of + 'T00:00:00Z', note='SYNTHETIC')
        F.add_source(fb, 'syn:px', 'synthetic', 'test-price', 'market_feed', as_of + 'T00:00:00Z')
        for i, (r, m, s) in enumerate(zip(rev, om, sbc)):
            end = f'{2021 + i}-12-31'
            oi = r * m
            flows = {'revenue': r, 'gross_profit': r * .4, 'cost_of_revenue': r * .6, 'operating_income': oi, 'sbc': r * s, 'd_and_a': r * .015,
                     'capex': r * .005, 'cfo': oi + r * s + r * .015, 'pretax_income': oi, 'income_tax': max(oi, 0) * .21,
                     'net_income': oi - max(oi, 0) * .21, 'interest_expense': 5e8}
            for k, v in flows.items():
                F.add_fact(fb, k, 'FY' + end[:4], end, v, 'syn:fs', 'as_reported', f'{2022 + i}-02-20', period_start=f'{2021 + i}-01-01')
            c = 9e9 * r / rev[-1]
            inst = {'cash': c, 'equity': r * .6 + c - 10e9, 'long_term_debt': 10e9, 'total_assets': r * .9 + c, 'total_liabilities': r * .3 + 10e9,
                    'receivables': r * .15, 'payables': r * .05, 'shares_outstanding': 2.1e9, 'current_assets': c + r * .2,
                    'current_liabilities': r * .25, 'ppe': r * .05}
            for k, v in inst.items():
                F.add_fact(fb, k, 'I@' + end, end, v, 'syn:fs', 'as_reported', f'{2022 + i}-02-10')
        F.set_price(fb, price, as_of, 'syn:px')
        return fb

    def test_automatic_card_agrees_with_the_structural_treatment(self):
        market = {'risk_free': {'value': .0515, 'as_of': '2026-10-19', 'title': 'test', 'retrieval': 'provider_normalized'},
                  'equity_risk_premium': {'value': .042, 'as_of': '2026-10-01', 'title': 'test', 'retrieval': 'provider_normalized'}}
        cf = pipeline.run(self.uber_like(), {'forecast': market})
        cy, route = cf['card']['cyclical'], cf['fc']['anchors']['route']
        self.assertEqual((cy['value'], cy['normalize'], cy['history'], cy.get('position')), (True, False, 'scale_up', None))
        self.assertFalse(route['cycle_path'] or route['cycle_peak'])
        self.assertEqual(route['stage'], 'growth')                                   # not forced to 'mature' around a cycle
        base = next(s for s in cf['fc']['spec']['scenarios'] if s['name'] == 'base')
        self.assertGreater(base['terminal']['operating_margin'], .08)                 # not the 2% floor over a negative mean
        structural = {'treatment': 'structural', 'reason': 'scale-up from losses, not a cycle (TEST)', 'evidence_ids': ['syn:fs']}
        cf2 = pipeline.run(self.uber_like(), {'forecast': dict(market, cyclical=structural)})
        self.assertAlmostEqual(pipeline.summary(cf)['base_value'], pipeline.summary(cf2)['base_value'])
        self.assertGreater(pipeline.summary(cf)['base_value'] / 95., .3)              # was 0.08x price
        # an analyst who shows a cycle still gets the cycle path
        cyc = {'treatment': 'cyclical', 'reason': 'bookings fell 40% in the last downturn (TEST)', 'evidence_ids': ['syn:fs']}
        cf3 = pipeline.run(self.uber_like(), {'forecast': dict(market, cyclical=cyc)})
        self.assertTrue(cf3['fc']['anchors']['route']['cycle_path'])
        self.assertLess(pipeline.summary(cf3)['base_value'], pipeline.summary(cf)['base_value'])


if __name__ == '__main__':
    unittest.main()
