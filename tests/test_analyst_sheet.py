"""analyst_sheet.py: the owner's six questions read from a casefile. Casefile pieces below are SYNTHETIC except the real
ADBE FactBook fixture used for one end-to-end run."""
import json
import unittest
from pathlib import Path

from fundamental_engine import analyst_sheet as A, pipeline
from fundamental_engine.render import markdown

ADBE = json.loads((Path(__file__).resolve().parent / 'fixtures/adbe_factbook_2026-10-19.json').read_text())


def fb(**extra):
    out = {'company': {'ticker': 'SYN', 'currency': 'USD', 'country': 'Taiwan'}, 'sources': {'s1': {}},
           'qualitative': [], 'business_map': {
               'tables': {'geography': {'revenue': {'US': {'2025-12-31': 70.}, 'CN': {'2025-12-31': 20.}, 'JP': {'2025-12-31': 10.}},
                                        'total_revenue': {'2025-12-31': 100.}, 'reconciliation': {'year': '2025-12-31'}}},
               'assets_by_geography': {'as_of': '2025-12-31', 'shares': {'Country Of Domicile': .8, 'US': .2}}}}
    out.update(extra)
    return out


def cf(price=100., bear=70., base=130., bull=200.):
    return {'dc': {'valuation_view': {'price': price, 'bear': bear, 'base': base, 'bull': bull},
                   'assessments': {'business_quality': {'level': 'strong', 'reasons': ['competitive_position: unknown ([])']}},
                   'expected_return': {'base_irr': .12}, 'verdict': {'reason_he': 'נימוק לדוגמה.'}},
            'rk': {'downside': {'loss_to_permanent': .5}, 'register': []},
            'val': {'what_must_be_true': [{'lever': 'growth', 'status': 'solved', 'required': .02, 'anchor': .08}]},
            'card': {'dimensions': {}}, 'an': {'metrics': {'revenue_cagr_3y': {'value': .15}}}, 'fc': {'warnings': []}}


class SheetTests(unittest.TestCase):
    def test_six_questions_read_from_the_casefile(self):
        s = A.build(fb(), cf())
        q = {x['key']: x for x in s['questions']}
        self.assertEqual([x['key'] for x in s['questions']], [k for k, _ in A.QUESTIONS])
        self.assertAlmostEqual(q['mispricing']['numbers']['gap_to_base'], .3)
        self.assertIn('2.0%', q['mispricing']['reading_he'])                        # what the price needs, from what_must_be_true
        self.assertAlmostEqual(q['geopolitics']['numbers']['sensitive_revenue']['סין'], .2)
        self.assertAlmostEqual(q['geopolitics']['numbers']['sensitive_assets']['טייוואן'], .8)   # domicile resolved to the country
        self.assertAlmostEqual(q['asymmetry']['numbers']['gain_to_bull'], 1.)
        self.assertAlmostEqual(q['asymmetry']['numbers']['loss_to_bear'], .3)
        self.assertIn('אסימטריה חיובית', q['asymmetry']['reading_he'])
        self.assertTrue(all(x['status'] != 'evidenced' for x in s['questions']))   # numbers are not evidence
        self.assertIn('טייוואן', s['thesis_he'])
        self.assertIn('דף אנליסט', A.render_he(s))

    def test_analyst_readings_need_evidence_to_close_a_question(self):
        inputs = {'analyst_sheet': {'geopolitics': {'reading_he': 'הייצור מרוכז באי אחד.', 'scenario_he': 'חסימה ימית לשנה',
                                                    'value_impact': .4, 'evidence_ids': ['s1']},
                                    'bottleneck': {'reading_he': 'דעה בלי מקור.'}}}
        s = A.build(fb(), cf(), inputs)
        q = {x['key']: x for x in s['questions']}
        self.assertEqual(q['geopolitics']['status'], 'evidenced')
        self.assertAlmostEqual(q['geopolitics']['numbers']['scenario_value'], 130. * .6)
        self.assertTrue(q['bottleneck']['analyst']['opinion'])
        self.assertNotEqual(q['bottleneck']['status'], 'evidenced')

    def test_a_quality_business_far_above_the_model_is_a_question_not_a_verdict(self):
        s = A.build(fb(), cf(price=300., bear=90., base=150., bull=220.))
        q = {x['key']: x for x in s['questions']}
        self.assertIn('לא מסקנה שהמניה יקרה', q['mispricing']['reading_he'])
        self.assertIn('אין אסימטריה לטובתנו', q['asymmetry']['reading_he'])


class PipelineSheetTests(unittest.TestCase):
    def test_pipeline_writes_the_sheet_and_the_moat_conditional_value(self):
        book = json.loads(json.dumps(ADBE))
        res = pipeline.run(book)
        sheet = res['analyst_sheet']
        self.assertEqual(res['stages'].get('analyst_sheet'), 'ok')
        self.assertEqual(len(sheet['questions']), 6)
        cond = next(q for q in sheet['questions'] if q['key'] == 'bottleneck')['numbers']['value_if_moat_confirmed']
        if cond:                                                  # ADBE's card may already carry an analyst moat rating
            self.assertIsNotNone(cond['base'])
        self.assertIn('דף אנליסט', markdown(res))


class AssetsByGeographyTests(unittest.TestCase):
    def test_latest_long_lived_assets_by_region_including_ifrs_and_extensions(self):
        from fundamental_engine import business
        ctx = lambda cid, d, axis, m: (f'<xbrli:context id="{cid}"><xbrli:segment><xbrldi:explicitMember dimension="{axis}">{m}'
                                       f'</xbrldi:explicitMember></xbrli:segment><xbrli:period><xbrli:instant>{d}</xbrli:instant>'
                                       '</xbrli:period></xbrli:context>')
        fact = lambda c, cid, v: f'<ix:nonFraction name="{c}" contextRef="{cid}" unitRef="usd" decimals="0">{v}</ix:nonFraction>'
        html = (ctx('a', '2025-12-31', 'ifrs-full:GeographicalAreasAxis', 'syn:CountryOfDomicileMember')
                + ctx('b', '2025-12-31', 'ifrs-full:GeographicalAreasAxis', 'country:US')
                + ctx('c', '2024-12-31', 'ifrs-full:GeographicalAreasAxis', 'country:US')
                + fact('syn:PropertyPlantAndEquipmentAndRightOfUse', 'a', '800') + fact('syn:PropertyPlantAndEquipmentAndRightOfUse', 'b', '200')
                + fact('syn:PropertyPlantAndEquipmentAndRightOfUse', 'c', '999'))           # SYNTHETIC filing fragment
        a = business.assets_by_geography(business.parse_ixbrl(html))
        self.assertEqual(a['as_of'], '2025-12-31')
        self.assertAlmostEqual(sum(a['shares'].values()), 1.)
        self.assertAlmostEqual(max(a['shares'].values()), .8)


if __name__ == '__main__':
    unittest.main()
