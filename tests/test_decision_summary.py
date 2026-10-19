import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine.decision_summary import decision_summary
from fundamental_engine.plain import plain_verdict
ROOT = Path(__file__).resolve().parents[1]


def inputs():
    # Renderer test doubles; not externally verified valuations.
    case = {'ticker': 'TEST', 'as_of': '2026-09-28', 'is_demo': False}
    report = {'ticker': 'TEST', 'as_of': case['as_of'], 'currency': 'USD',
        'stage': 'growth', 'items': [], 'confidence': {'level': 'בינוני'},
        'price': {'bucket': 'cheap', 'quote': 80, 'bear': 60, 'base': 100, 'bull': 140},
        'valuation': {'priced_conclusion_ready': True, 'annual_values': [
            {'date': '2026-09-28', 'base': 100},
            {'date': '2030-12-31', 'base': 180},
            {'date': '2031-12-31', 'base': 200}]}}
    return report, case


class DecisionSummaryTests(unittest.TestCase):
    def test_discount_is_not_upside_and_future_is_dated(self):
        r, c = inputs(); out = decision_summary(r, c)
        self.assertAlmostEqual(out['metrics']['discount_to_base_value'], .2)
        self.assertAlmostEqual(out['metrics']['upside_to_base_value'], .25)
        f = out['metrics']['five_year']
        self.assertEqual(f['date'], '2031-12-31')
        self.assertEqual(f['conditional_base_value'], 200)
        self.assertAlmostEqual(f['price_change_excluding_dividends'], 1.5)
        self.assertIn('אינה הבטחת מחיר', out['text'])
        self.assertNotIn('\n', out['text'])
        self.assertNotIn('2030', out['text'])

    def test_four_years_is_not_five_and_no_extrapolation(self):
        r, c = inputs(); r['valuation']['annual_values'].pop()
        out = decision_summary(r, c)
        self.assertIsNone(out['metrics']['five_year'])
        self.assertIn('חמש שנים מלאות', out['text'])

    def test_demo_unreviewed_blocked_never_publish_discount(self):
        for mode in ('demo', 'unreviewed', 'blocked'):
            r, c = inputs()
            if mode == 'demo': c['is_demo'] = True
            if mode == 'unreviewed': r['valuation']['priced_conclusion_ready'] = False
            if mode == 'blocked': r['price']['bucket'] = 'blocked'
            out = decision_summary(r, c)
            self.assertFalse(out['price_conclusion_eligible'])
            self.assertIsNone(out['metrics']['discount_to_base_value'])
            self.assertIsNone(out['metrics']['five_year'])
            self.assertNotIn('100.0', out['text'])

    def test_confidence_names_its_main_reason(self):
        r, c = inputs()
        self.assertIn('רמת הביטחון בהערכה: בינוני.', decision_summary(r, c)['text'])
        r['confidence'] = {'level': 'בינוני', 'reasons': ['נתוני המאזן לא נקראו ישירות מהדוח עצמו', 'סיבה שנייה']}
        text = decision_summary(r, c)['text']
        self.assertIn('בינוני — נתוני המאזן לא נקראו ישירות מהדוח עצמו.', text)
        self.assertNotIn('סיבה שנייה', text)

    def test_zero_value_and_premium(self):
        r, c = inputs(); r['price'].update(bucket='expensive', quote=120)
        out = decision_summary(r, c)
        self.assertIn('גבוה בכ־20%', out['text'])
        r['price']['base'] = 0
        self.assertFalse(decision_summary(r, c)['price_conclusion_eligible'])

    def test_evidenced_potential_does_not_unlock_price(self):
        r, c = inputs(); r['valuation']['priced_conclusion_ready'] = False
        c['research_dossier'] = {'identity': {'ticker': 'TEST'}, 'as_of': c['as_of'],
            'sources': [{'id': 's', 'available_at': '2026-09-01'}],
            'observations': [{'id': 'o', 'source_id': 's', 'reviewed': True,
                'review_note': 'Checked scope', 'reviewed_at': '2026-09-20', 'observed_at': '2026-08-31'}]}
        c['decision_notes'] = {'potential': {'text': 'התרחבות בתשלום אצל לקוחות קיימים', 'observation_ids': ['o']}}
        out = decision_summary(r, c)
        self.assertIn('התרחבות בתשלום', out['text'])
        self.assertFalse(out['price_conclusion_eligible'])
        for change in ('unknown', 'future', 'unreviewed', 'identity'):
            bad = deepcopy(c)
            if change == 'unknown': bad['decision_notes']['potential']['observation_ids'] = ['fake']
            if change == 'future': bad['research_dossier']['sources'][0]['available_at'] = '2027-01-01'
            if change == 'unreviewed': bad['research_dossier']['observations'][0]['reviewed'] = False
            if change == 'identity': bad['research_dossier']['identity']['ticker'] = 'OTHER'
            with self.assertRaises(ValueError): decision_summary(r, bad)

    def test_default_integration_and_explicit_detail(self):
        c = json.loads((ROOT/'examples/plain_demo.json').read_text())
        out = plain_verdict(c)
        self.assertNotIn('\n', out['text'])
        self.assertEqual(out['text'], out['decision_summary']['text'])
        self.assertIn('##', out['detailed_text'])
        c['response_style'] = 'detailed'
        self.assertEqual(plain_verdict(c)['text'], out['detailed_text'])
        c['response_style'] = 'invent'
        with self.assertRaises(ValueError): plain_verdict(c)

    def test_financials_are_not_relabelled_to_force_generic_model(self):
        c = json.loads((ROOT/'examples/plain_demo.json').read_text()); c['archetype'] = 'financials'
        with self.assertRaisesRegex(ValueError, 'dedicated'): plain_verdict(c)

    def test_accounting_warning_survives_compression(self):
        r, c = inputs(); r['items'] = [{'key': 'accounting_risk', 'points': 1}]
        self.assertIn('דגל אדום', decision_summary(r, c)['text'])

    def test_leap_anniversary_and_late_endpoint(self):
        r, c = inputs(); r['as_of'] = c['as_of'] = '2024-02-29'
        r['valuation']['annual_values'] = [ {'date': c['as_of'], 'base': 100}, {'date': '2029-02-28', 'base': 120}]
        self.assertEqual(decision_summary(r,c)['metrics']['five_year']['date'], '2029-02-28')
        r['valuation']['annual_values'][1]['date'] = '2029-12-31'
        self.assertIsNone(decision_summary(r,c)['metrics']['five_year'])
