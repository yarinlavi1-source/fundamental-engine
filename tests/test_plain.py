import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.plain import plain_verdict, fraction_words, multiple_words, price_vs_value_words, years_words
from fundamental_engine.supervisor import plan, packet
from fundamental_engine.mcp import Server
ROOT=Path(__file__).resolve().parents[1]
def demo():return json.loads((ROOT/'examples/plain_demo.json').read_text())
def valuation():return json.loads((ROOT/'examples/valuation/infrastructure.json').read_text())
def item(r,key):return next(i for i in r['items'] if i['key']==key)
def row(year,rev,**kw):
    r={'fiscal_year':str(year),'period_end':f'{year}-12-31','revenue':rev,'source_ids':['s']};r.update(kw);return r


class PlainTests(unittest.TestCase):
    def test_demo_labels_every_metric_and_explains_in_words(self):
        r=plain_verdict(demo())
        self.assertEqual({i['key'] for i in r['items']},{'growth','gross_margin','operating_margin','free_cash_flow','earnings_quality','balance_sheet','dilution'})
        for i in r['items']:
            self.assertIn(i['label'],{'מצוין','טוב','בינוני','חלש','מדאיג'})
            self.assertTrue(i['headline'] and i['explain'])
        self.assertEqual(item(r,'growth')['label'],'מצוין')
        self.assertEqual(item(r,'operating_margin')['trend'],'improving')
        self.assertIsNone(r['price'])
        self.assertIn('המחיר עוד לא נבדק',r['bottom_line']['call'])
        self.assertIn('אין עדיין הערכת שווי מאושרת',r['text'])

    def test_valuation_is_executed_and_translated(self):
        c=demo();c['valuation_case']=valuation();r=plain_verdict(c)
        self.assertTrue(r['valuation']['executed']);self.assertEqual(len(r['valuation']['annual_values']),6)
        self.assertEqual(r['price']['bucket'],'cheap')
        self.assertEqual(r['bottom_line']['call'],'מעניין מאוד')
        self.assertIn('2030',r['detailed_text']);self.assertIn('לא תחזית למחיר',r['detailed_text'])
        self.assertIn('הדגמה בלבד', r['text'])
        self.assertIn('זו דוגמה מומצאת, לא חברה אמיתית',r['confidence']['reasons'])
        self.assertEqual(r['confidence']['level'],'נמוך')

    def test_price_buckets_follow_scenarios(self):
        c=demo();v=valuation();c['valuation_case']=v
        for price,bucket in ((10,'cheap_even_bear'),(44,'fair'),(80,'expensive'),(150,'very_expensive')):
            v['quote']['price']=price;r=plain_verdict(deepcopy(c))
            self.assertEqual(r['price']['bucket'],bucket,price)
        v['quote']['price']=80;self.assertEqual(plain_verdict(c)['bottom_line']['call'],'עסק טוב אבל יקר')

    def test_funding_block_is_warning_not_value(self):
        c=demo();v=valuation();v['scenarios'][1]['periods'][0]['growth_capex']=10e9;c['valuation_case']=v
        r=plain_verdict(c)
        self.assertEqual(r['price']['bucket'],'blocked');self.assertEqual(r['bottom_line']['light'],'🔴')

    def test_declining_weak_business(self):
        c=demo();c.pop('balance')
        c['history']=[row(2023,1000,gross_profit=150,operating_income=20,net_income=10,operating_cash_flow=5,capex=30,shares_diluted=100),
                      row(2024,900,gross_profit=120,operating_income=-10,net_income=-20,operating_cash_flow=-5,capex=30,shares_diluted=115)]
        r=plain_verdict(c)
        self.assertEqual(item(r,'growth')['label'],'מדאיג');self.assertEqual(item(r,'free_cash_flow')['label'],'מדאיג')
        self.assertEqual(item(r,'dilution')['label'],'מדאיג');self.assertEqual(item(r,'operating_margin')['trend'],'worsening')
        self.assertNotIn('earnings_quality',{i['key'] for i in r['items']})

    def test_emerging_losses_graded_by_stage_and_runway(self):
        c=demo();c['stage']='emerging';c['archetype']='software';c['balance']={'as_of':'2026-06-30','cash':300,'debt':0}
        c['history']=[row(2024,100,gross_profit=70,operating_income=-80,operating_cash_flow=-60,capex=10),
                      row(2025,200,gross_profit=150,operating_income=-40,operating_cash_flow=-40,capex=10)]
        r=plain_verdict(c)
        self.assertEqual(item(r,'operating_margin')['points'],3)
        self.assertEqual(item(r,'balance_sheet')['label'],'מצוין')
        self.assertEqual(item(r,'runway')['figure']['runway_years'],6)
        mature=deepcopy(c);mature['stage']='mature'
        self.assertEqual(item(plain_verdict(mature),'operating_margin')['points'],1)

    def test_sector_bars_differ(self):
        c=demo();c['history']=[row(2025,100,gross_profit=40)]
        self.assertEqual(item(plain_verdict(c),'gross_margin')['label'],'טוב')
        c['archetype']='software';self.assertEqual(item(plain_verdict(c),'gross_margin')['label'],'חלש')

    def test_input_guards(self):
        for mutate,msg in ((lambda c:c.update(archetype='financials'),'dedicated'),
                           (lambda c:c['history'][0].update(source_ids=[]),'source_ids'),
                           (lambda c:c['history'].reverse(),'oldest first'),
                           (lambda c:c['history'][-1].update(period_end='2027-12-31'),'after as_of'),
                           (lambda c:c.update(stage='hype'),'stage'),
                           (lambda c:c['history'][0].update(revenue=float('nan')),'')):
            c=demo();mutate(c)
            with self.assertRaisesRegex(ValueError,msg):plain_verdict(c)
        c=demo();v=valuation();v['ticker']='OTHER';c['valuation_case']=v
        with self.assertRaisesRegex(ValueError,'match'):plain_verdict(c)

    def test_word_helpers(self):
        self.assertEqual(fraction_words(.5),'בערך חצי');self.assertEqual(fraction_words(.45),'קצת פחות מחצי')
        self.assertEqual(multiple_words(2.05),'בערך פי 2');self.assertEqual(multiple_words(1.0),'בערך אותו דבר')
        self.assertEqual(price_vs_value_words(20,40),'המחיר הוא בערך חצי מהשווי')
        self.assertEqual(price_vs_value_words(40,20),'המחיר גבוה מהשווי בערך פי 2')
        self.assertEqual(years_words(.8),'פחות משנה')

    def test_research_status_raises_confidence(self):
        c=demo();c['research_status']='ready_for_conditional_synthesis'
        c['history'][0].update(gross_profit=None)
        self.assertEqual(plain_verdict(c)['confidence']['level'],'גבוה')

    def test_packet_plan_and_mcp(self):
        self.assertIn('plain_verdict',packet('plain_language')['content'])
        r=plan({'ticker':'X','as_of':'2026-09-27','question':'value'})
        self.assertIn('plain_language',[p['id'] for p in r['packets']])
        s=Server(':memory:');s.initialized=True
        out=s.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'plain_verdict','arguments':{'case':demo()}}})
        self.assertFalse(out['result']['isError'])
        self.assertIn('הדגמה בלבד',json.loads(out['result']['content'][0]['text'])['text'])


if __name__=='__main__':
    unittest.main()
