import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.profile import classify_company
from fundamental_engine.scores import piotroski, altman, beneish, roic, rule_of_40, forecast_base_rate
from fundamental_engine.connectors import from_alpha_vantage
from fundamental_engine.history import acquisition_years, organic_cagr
from fundamental_engine.plain import plain_verdict
from fundamental_engine.valuation import value_company
from fundamental_engine.mcp import Server
ROOT=Path(__file__).resolve().parents[1]
def load(p):return json.loads((ROOT/p).read_text())
def row(year,rev,**kw):
    r={'fiscal_year':f'FY{year}','period_end':f'{year}-12-31','revenue':rev,'source_ids':['s']};r.update(kw);return r
def case(rows,**kw):
    c={'plain_version':1,'company':'T','ticker':'T','as_of':'2026-09-27','currency':'USD','history':rows};c.update(kw);return c


class TypeTests(unittest.TestCase):
    def test_broadcom_real_data_is_mature_compounder_not_fast_grower(self):
        r=classify_company(load('examples/real/avgo_plain_2025.json'))
        self.assertEqual((r['stage'],r['lynch']),('mature_growth','stalwart'))
        self.assertEqual(r['evidence']['acquisition_years'],['FY2024'])
        self.assertGreater(r['evidence']['revenue_cagr'],.2)
        self.assertLess(r['evidence']['growth_used_for_type'],.2)
        self.assertIn('acquisitions',[f['key'] for f in r['flags']])

    def test_life_cycle_ladder(self):
        grow=lambda g,om:[row(2022+i,100*(1+g)**i,operating_income=100*(1+g)**i*om) for i in range(4)]
        cases={'young_growth':grow(.4,-.2),'high_growth':grow(.3,.1),'mature_growth':grow(.12,.15),
               'mature_stable':grow(.03,.15),'start_up':[row(2024,1,operating_income=-50),row(2025,3,operating_income=-40)]}
        for stage,rows in cases.items():
            self.assertEqual(classify_company(case(rows))['stage'],stage,stage)
        decline=[row(2022+i,100*.9**i,operating_income=100*.9**i*(.1-.03*i)) for i in range(4)]
        self.assertEqual(classify_company(case(decline))['stage'],'decline')

    def test_lynch_categories(self):
        rows=[row(2022,100,operating_income=10),row(2023,70,operating_income=-5),row(2024,110,operating_income=25),row(2025,120,operating_income=30)]
        r=classify_company(case(rows,archetype='commodity'))
        self.assertEqual(r['lynch'],'cyclical');self.assertIn('cycle_peak',[f['key'] for f in r['flags']])
        self.assertIn('Cyclical',r['valuation_fit'])
        turn=[row(2023,100,operating_income=10),row(2024,95,operating_income=5),row(2025,96,operating_income=-8)]
        self.assertEqual(classify_company(case(turn))['lynch'],'turnaround')
        slow=[row(2023,100,operating_income=10),row(2024,102,operating_income=10),row(2025,104,operating_income=10)]
        self.assertEqual(classify_company(case(slow))['lynch'],'slow_grower')
        asset=case(slow,market_cap=100,balance={'as_of':'2025-12-31','cash':80,'debt':0})
        self.assertEqual(classify_company(asset)['lynch'],'asset_play')

    def test_override_needs_reason(self):
        c=load('examples/real/avgo_plain_2025.json');c['type_override']={'stage':'mature_stable'}
        with self.assertRaisesRegex(ValueError,'reason'):classify_company(c)
        c['type_override']['reason']='Analyst: AI growth judged temporary';r=classify_company(c)
        self.assertEqual(r['stage'],'mature_stable');self.assertEqual(r['computed']['stage'],'mature_growth')

    def test_acquired_revenue_supplied_explicitly(self):
        rows=[row(2023,100),row(2024,110),row(2025,200,acquired_revenue=80)]
        self.assertEqual(acquisition_years(rows),['FY2025'])
        self.assertAlmostEqual(organic_cagr(rows),((1.1)*(120/110))**.5-1)


class ScoreTests(unittest.TestCase):
    def full(self,year,**kw):
        base=dict(revenue=1000,gross_profit=400,operating_income=150,net_income=100,operating_cash_flow=130,capex=30,
                  shares_diluted=100,total_assets=2000,current_assets=600,current_liabilities=300,total_liabilities=900,
                  retained_earnings=500,receivables=150,sga=200,depreciation=50,ppe=500,long_term_debt=400,total_debt=400,
                  cash=200,equity=1100,pretax_income=125,income_tax=25)
        base.update(kw);rev=base.pop('revenue');return row(year,rev,**base)

    def test_piotroski_counts_signals(self):
        rows=[self.full(2023),self.full(2024),self.full(2025,net_income=120,gross_profit=430,revenue=1050,total_debt=350,long_term_debt=350,current_assets=650)]
        r=piotroski(rows)
        self.assertEqual(r['out_of'],9)
        s=r['signals']
        self.assertTrue(s['positive_net_income_roa'] and s['improving_roa'] and s['lower_leverage'] and s['higher_current_ratio'])
        self.assertTrue(s['cash_flow_exceeds_net_income'])
        self.assertTrue(s['no_new_shares'] and s['higher_gross_margin'] and s['higher_asset_turnover'])
        self.assertEqual(r['score'],9);self.assertEqual(r['zone'],'strong')
        self.assertEqual(piotroski([row(2024,1),row(2025,2)])['status'],'unavailable')

    def test_altman_formulas(self):
        r=altman([self.full(2025)],'software')
        x1,x2,x3,x4=300/2000,500/2000,150/2000,1100/900
        self.assertAlmostEqual(r['z'],6.56*x1+3.26*x2+6.72*x3+1.05*x4);self.assertEqual(r['zone'],'safe')
        m=altman([self.full(2025)],'industrial',market_cap=3000)
        self.assertAlmostEqual(m['z'],1.2*x1+1.4*x2+3.3*x3+.6*3000/900+1000/2000)
        self.assertIn('1968',m['model'])

    def test_beneish_neutral_and_aggressive(self):
        r=beneish([self.full(2024),self.full(2025)])
        tata=(100-130)/2000
        self.assertAlmostEqual(r['m'],-4.84+.92+.528+.404+.892+.115-.172+4.679*tata-.327)
        self.assertEqual(r['zone'],'unlikely')
        bad=beneish([self.full(2024),self.full(2025,revenue=1500,receivables=500,net_income=300,operating_cash_flow=20,gross_profit=450)])
        self.assertEqual(bad['zone'],'likely_manipulator')

    def test_roic_and_rule_of_40(self):
        r=roic([self.full(2024),self.full(2025,operating_income=200,equity=1200)])
        self.assertAlmostEqual(r['series'][0]['roic'],150*.8/1300)
        self.assertAlmostEqual(r['roic_last'],200*.8/1350)
        self.assertAlmostEqual(r['incremental_roic'],(160-120)/100)
        self.assertEqual(rule_of_40([self.full(2025)],'industrial')['status'],'not_applicable')
        r40=rule_of_40([self.full(2024),self.full(2025,revenue=1300)],'software')
        self.assertAlmostEqual(r40['score'],.3+100/1300)

    def test_forecast_base_rate_flags_rare_growth(self):
        v=value_company(load('examples/valuation/infrastructure.json'))
        small=[row(2023,50e6),row(2024,60e6),row(2025,70e6)]
        r=forecast_base_rate(small,v)
        self.assertGreater(r['implied']['implied_revenue_cagr'],.2)
        self.assertEqual({f['key'] for f in r['flags']},{'rare_sustained_growth','above_own_history'})
        self.assertIsNone(forecast_base_rate(small,None))


class ConnectorAndIntegrationTests(unittest.TestCase):
    def av(self,sym='X',cur='USD'):
        inc={'symbol':sym,'annualReports':[{'fiscalDateEnding':'2025-12-31','reportedCurrency':cur,'totalRevenue':'1000','grossProfit':'400','operatingIncome':'None','netIncome':'90'},
                                           {'fiscalDateEnding':'2024-12-31','reportedCurrency':cur,'totalRevenue':'800','grossProfit':'300','operatingIncome':'100','netIncome':'70'},
                                           {'fiscalDateEnding':'2027-12-31','reportedCurrency':cur,'totalRevenue':'9','grossProfit':'1','operatingIncome':'1','netIncome':'1'}]}
        cf={'symbol':sym,'annualReports':[{'fiscalDateEnding':'2025-12-31','reportedCurrency':cur,'operatingCashflow':'120','capitalExpenditures':'-30'}]}
        return inc,cf

    def test_alpha_vantage_mapping(self):
        inc,cf=self.av();r=from_alpha_vantage(inc,None,cf,as_of='2026-09-27')
        self.assertEqual([x['period_end'] for x in r['history']],['2024-12-31','2025-12-31'])
        last=r['history'][-1]
        self.assertNotIn('operating_income',last);self.assertEqual(last['capex'],30);self.assertEqual(len(last['source_ids']),2)
        with self.assertRaisesRegex(ValueError,'different symbols'):from_alpha_vantage(inc,None,self.av('Y')[1])
        with self.assertRaisesRegex(ValueError,'currencies'):from_alpha_vantage(inc,None,self.av(cur='EUR')[1])

    def test_plain_uses_type_and_scores(self):
        r=plain_verdict(load('examples/real/avgo_plain_2025.json'))
        self.assertEqual(r['company_type']['lynch'],'stalwart');self.assertEqual(r['stage'],'growth')
        self.assertIn('איזה סוג חברה זו',r['detailed_text']);self.assertIn('איתנה',r['detailed_text'])
        keys={i['key'] for i in r['items']}
        self.assertTrue({'roic','distress','fscore','rule_of_40'}<=keys)
        self.assertEqual(list(r['axis_weights']),[.45,.3,.25])

    def test_red_flag_reaches_bottom_line(self):
        full=ScoreTests.full
        rows=[full(None,2024),full(None,2025,revenue=1500,receivables=500,net_income=300,operating_cash_flow=20,gross_profit=450)]
        r=plain_verdict(case(rows))
        self.assertEqual(next(i for i in r['items'] if i['key']=='accounting_risk')['label'],'מדאיג')
        self.assertEqual(r['bottom_line']['light'],'🔴')
        self.assertTrue(any('בניש' in x for x in r['confidence']['reasons']))

    def test_mcp_tools(self):
        s=Server(':memory:');s.initialized=True
        call=lambda n,a:s.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':n,'arguments':a}})['result']
        avgo=load('examples/real/avgo_plain_2025.json')
        for name,args in (('classify_company',{'case':avgo}),('forensic_scores',{'case':avgo}),('import_statements',{'income':self.av()[0],'as_of':'2026-09-27'})):
            out=call(name,args);self.assertFalse(out['isError'],out)
        self.assertEqual(json.loads(call('classify_company',{'case':avgo})['content'][0]['text'])['lynch'],'stalwart')


if __name__=='__main__':
    unittest.main()


class InflectionTests(unittest.TestCase):
    def test_axti_quarterly_inflection_and_recent_dilution(self):
        c=load('examples/real/axti_plain_2026q2.json')
        k=classify_company(c)
        self.assertEqual((k['stage'],k['lynch']),('high_growth','turnaround'))
        self.assertIn('inflection',[f['key'] for f in k['flags']])
        self.assertGreater(k['evidence']['quarter_momentum']['latest_quarter_yoy'],1)
        r=plain_verdict(c)
        items={i['key']:i for i in r['items']}
        self.assertEqual(items['dilution']['label'],'מדאיג')
        self.assertEqual(items['recent_margin']['label'],'מצוין')
        self.assertEqual(items['growth']['label'],'טוב')
        no_q=deepcopy(c);no_q.pop('recent_quarters')
        self.assertEqual(classify_company(no_q)['stage'],'decline')

    def test_quarter_guards_and_slowdown(self):
        c=case([row(2024,100,operating_income=20),row(2025,130,operating_income=30)])
        q=lambda d,v:{'period_end':d,'revenue':v,'operating_income':v*.1,'source_ids':['s']}
        c['recent_quarters']=[q('2025-03-31',40),q('2025-06-30',40),q('2025-09-30',40),q('2025-12-31',40),q('2026-03-31',25)]
        self.assertIn('slowdown',[f['key'] for f in classify_company(c)['flags']])
        c['recent_quarters'].reverse()
        with self.assertRaisesRegex(ValueError,'oldest first'):classify_company(c)

    def test_price_above_bull_is_red_bottom_line(self):
        c=json.loads((ROOT/'examples/plain_demo.json').read_text());v=load('examples/valuation/infrastructure.json')
        v['quote']['price']=500;c['valuation_case']=v
        r=plain_verdict(c)
        self.assertEqual(r['price']['bucket'],'very_expensive');self.assertEqual(r['bottom_line']['light'],'🔴')
        self.assertIn('יקר מאוד',r['bottom_line']['call'])
