import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from fundamental_engine.finance import dcf, reverse_dcf, funding_path, ttm, standalone_ytd, number
from fundamental_engine.engine import analyze
from fundamental_engine.sec import select_facts, fetch_companyfacts
from fundamental_engine.store import Store
from fundamental_engine.report import render

ROOT = Path(__file__).resolve().parents[1]


def demo():
    return json.loads((ROOT/'examples/emerging_demo.json').read_text())


def scenario():
    return {'name':'flat','rationale':'test','wacc':.1,'terminal_growth':0,'terminal_roic':.1,
            'years':[{'growth':0,'operating_margin':.2,'cash_tax_rate':.25,'reinvestment':0}]}


class FinanceTests(unittest.TestCase):
    def test_perpetuity_known_answer(self):
        # 15/year forever at 10% = 150 operations, plus 20 cash minus 30 debt.
        r=dcf(100,10,20,30,0,scenario())
        self.assertAlmostEqual(r['operating_value'],150)
        self.assertAlmostEqual(r['value_per_share'],14)

    def test_terminal_growth_includes_reinvestment(self):
        a=scenario(); b={**a,'terminal_growth':.03}
        # First terminal year's growth also changes its reinvestment requirement.
        r=dcf(100,10,0,0,0,b)
        expected=(15+15*1.03*(1-.03/.1)/(.1-.03))/1.1/10
        self.assertAlmostEqual(r['value_per_share'],expected)

    def test_no_automatic_tax_refund_on_loss(self):
        s=scenario(); s['years'].insert(0,{'growth':0,'operating_margin':-.2,'cash_tax_rate':.25,'reinvestment':0})
        self.assertEqual(dcf(100,10,0,0,0,s)['forecast'][0]['nopat'],-20)

    def test_invalid_terminal(self):
        for changes in [{'terminal_growth':.1},{'terminal_roic':.01,'terminal_growth':.03}]:
            with self.assertRaises(ValueError): dcf(100,10,0,0,0,{**scenario(),**changes})

    def test_negative_terminal_earnings(self):
        s=scenario(); s['years'][0]['operating_margin']=-.1
        with self.assertRaises(ValueError): dcf(100,10,0,0,0,s)

    def test_reverse_round_trip(self):
        s=scenario(); s['years']*=5
        target=dcf(100,10,0,0,0,{**s,'years':[{**y,'growth':.17} for y in s['years']]})['value_per_share']
        r=reverse_dcf(target,100,10,0,0,0,s)
        self.assertAlmostEqual(r['growth'],.17)
        self.assertAlmostEqual(r['reconstructed_price'],target)

    def test_reverse_out_of_bounds(self):
        self.assertEqual(reverse_dcf(1e9,100,10,0,0,0,scenario())['status'],'no_bracket')

    def test_funding_gap(self):
        p={'operating_cash_flow':-8,'capex':10,'debt_repayment':0,'committed_financing':0}
        r=funding_path(50,[p]*4)
        self.assertEqual(r['first_gap_period'],3)
        self.assertEqual(r['maximum_shortfall'],22)

    def test_nonfinite_and_bool_rejected(self):
        for v in [math.nan,math.inf,True,'3']:
            with self.assertRaises(ValueError): number(v,'test')

    def quarters(self):
        return [dict(metric='revenue',currency='USD',unit='absolute',basis='GAAP',scope='group',kind='duration',start=s,end=e,value=10)
                for s,e in [('2025-01-01','2025-03-31'),('2025-04-01','2025-06-30'),('2025-07-01','2025-09-30'),('2025-10-01','2025-12-31')]]

    def test_ttm(self): self.assertEqual(ttm(self.quarters()),40)

    def test_ttm_rejects_overlap_and_balance_sheet(self):
        for key,value in [('start','2025-06-30'),('kind','instant')]:
            qs=self.quarters(); qs[2][key]=value
            with self.assertRaises(ValueError): ttm(qs)

    def test_ytd_derivation_and_currency(self):
        a=self.quarters()[0]; b={**a,'end':'2025-06-30','value':30}
        self.assertEqual(standalone_ytd(b,a)['value'],20)
        self.assertEqual(standalone_ytd(b,a)['start'],'2025-04-01')
        with self.assertRaises(ValueError): standalone_ytd({**b,'currency':'EUR'},a)


class EngineTests(unittest.TestCase):
    def test_loss_does_not_erase_potential(self):
        r=analyze(demo())
        self.assertEqual(r['potential']['status'],'early_evidence')
        self.assertEqual(r['status'],'financing_required')
        self.assertEqual(len(r['valuations']),3)

    def test_future_data_is_excluded(self):
        d=demo(); d['as_of']='2025-12-31'
        r=analyze(d)
        self.assertEqual(r['financial_metrics'],[])
        self.assertEqual(r['valuations'],[])
        self.assertIsNone(r['quote'])
        self.assertEqual(r['potential']['status'],'hypothesis_only')

    def test_future_review_not_used(self):
        d=demo()
        for s in d['signals']: s['reviewed_at']='2026-10-01'
        self.assertEqual(analyze(d)['potential']['status'],'hypothesis_only')

    def test_missing_values_stay_missing(self):
        d=demo(); d['financials'][-1]['cfo']=None
        self.assertIsNone(analyze(d)['financial_metrics'][-1]['reported_fcf'])

    def test_unsupported_sector(self):
        d=demo(); d['company']['sector']='bank'
        self.assertEqual(analyze(d)['valuations'],[])

    def test_unknown_source_and_currency(self):
        for mutate in [lambda d:d['financials'][0].update(source_ids=['unknown']),lambda d:d['quote'].update(currency='EUR')]:
            d=demo(); mutate(d)
            with self.assertRaises(ValueError): analyze(d)

    def test_overlapping_annual_periods(self):
        d=demo(); d['financials'].append(copy.deepcopy(d['financials'][0]))
        with self.assertRaises(ValueError): analyze(d)

    def test_sbc_policy_required(self):
        d=demo(); d['valuation']['sbc_policy']='ignore'
        with self.assertRaises(ValueError): analyze(d)

    def test_synthetic_sources_require_demo_banner(self):
        d=demo(); d['is_demo']=False
        with self.assertRaises(ValueError): analyze(d)

    def test_disproven_negative_claim_is_not_negative_evidence(self):
        d=demo(); d['signals'][1].update(direction='negative',review_status='contradicted')
        self.assertEqual(analyze(d)['potential']['evidence']['adoption']['opposing'],[])

    def test_html_escapes_external_text(self):
        d=demo(); d['company']['name']='<script>alert(1)</script>'
        html=render(analyze(d))
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)

    def test_snapshots_immutable_and_diff(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp)/'test.sqlite')
            d=demo(); a=s.save(d,analyze(d)); d['quote']['price']=99
            b=s.save(d,analyze(d))
            self.assertEqual(s.report(a)['quote']['price'],12)
            self.assertTrue(any(x['path']=='quote.price' for x in s.compare(a,b)))
            self.assertEqual(len(s.history('demo-emerging')),2)
            s.close()


class SECTests(unittest.TestCase):
    def payload(self):
        return {'facts':{'us-gaap':{'Revenue':{'units':{'USD':[
            {'start':'2024-01-01','end':'2024-12-31','filed':'2025-03-01','val':100,'accn':'first'},
            {'start':'2024-01-01','end':'2024-12-31','filed':'2026-03-01','val':110,'accn':'restated'}]}}}}}

    def test_restatement_cutoff(self):
        p=self.payload()
        self.assertEqual(select_facts(p,'us-gaap','Revenue','USD','2025-12-31')[0]['value'],100)
        self.assertEqual(select_facts(p,'us-gaap','Revenue','USD','2026-12-31')[0]['value'],110)

    def test_same_day_conflict(self):
        p=self.payload(); p['facts']['us-gaap']['Revenue']['units']['USD'][1]['filed']='2025-03-01'
        with self.assertRaises(ValueError): select_facts(p,'us-gaap','Revenue','USD','2026-12-31')

    def test_transport_requires_contact_before_network(self):
        with self.assertRaises(ValueError): fetch_companyfacts('123','anonymous','unused')


if __name__ == '__main__': unittest.main()
