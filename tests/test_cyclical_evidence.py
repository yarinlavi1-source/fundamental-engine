"""Cyclicality is charged in the rates only when it is evidenced (25.10.2026 calibration fix F4).

Regression from the 2026-10-21 valuation-gap diagnosis: the +0.5pp cyclical row of the cost of equity (forecast.KE_FACTORS) and
the 1pp cyclicality component of the decision hurdle (decision.PREMIUM) were charged from an industry word alone (MKT, 'industrial
machinery', no cycle in its reported history and not normalized: +0.5pp on Ke, about 9% of value, and 1pp on the hurdle). Now
both need evidence (model_card.cyclical_evidence): the reported history shows a cycle, an analyst confirmed one, or the type is
cyclical by economics; a word-only flag stays a visible question. Data here is SYNTHETIC."""
import unittest
from copy import deepcopy
from fundamental_engine import pipeline, decision
from fundamental_engine import facts as F
from fundamental_engine import model_card as M
from test_decision_render import parts, card_saas

MARKET = {'risk_free': {'value': .0515, 'as_of': '2026-10-19', 'title': 'test', 'retrieval': 'provider_normalized'},
          'equity_risk_premium': {'value': .042, 'as_of': '2026-10-01', 'title': 'test', 'retrieval': 'provider_normalized'}}
WORD_ONLY = {'value': True, 'normalize': False, 'basis': 'industry_flag', 'industry_flag': True, 'data_shows_cycle': False,
             'industry_word': 'machinery', 'history': None, 'peak_question': None}


def machinery_like(industry='Industrial Machinery', price=60.):
    """SYNTHETIC equipment maker: revenue +6% a year with no dip, operating margin 17-18.5% (no swing, no peak)."""
    rev, om = [8.0e9, 8.5e9, 9.05e9, 9.6e9, 10.2e9], [.17, .18, .175, .18, .185]
    as_of = '2026-10-20'
    fb = F.new({'ticker': 'MCHX', 'name': 'Synthetic machinery maker', 'currency': 'USD', 'fiscal_year_end': '12-31',
                'sector': 'Industrials', 'industry': industry}, as_of)
    F.add_source(fb, 'syn:fs', 'synthetic', 'test-statements', 'filing_derived', as_of + 'T00:00:00Z', note='SYNTHETIC')
    F.add_source(fb, 'syn:px', 'synthetic', 'test-price', 'market_feed', as_of + 'T00:00:00Z')
    for i, (r, m) in enumerate(zip(rev, om)):
        end = f'{2021 + i}-12-31'
        oi = r * m
        flows = {'revenue': r, 'gross_profit': r * .35, 'cost_of_revenue': r * .65, 'operating_income': oi, 'sbc': r * .01,
                 'd_and_a': r * .03, 'capex': r * .035, 'cfo': oi * .79 + r * .04, 'pretax_income': oi - 1e8, 'income_tax': (oi - 1e8) * .21,
                 'net_income': (oi - 1e8) * .79, 'interest_expense': 1e8}
        for k, v in flows.items():
            F.add_fact(fb, k, 'FY' + end[:4], end, v, 'syn:fs', 'as_reported', f'{2022 + i}-02-20', period_start=f'{2021 + i}-01-01')
        inst = {'cash': r * .12, 'equity': r * .7, 'long_term_debt': 2e9, 'total_assets': r * 1.3, 'total_liabilities': r * .6,
                'receivables': r * .18, 'inventory': r * .15, 'payables': r * .1, 'shares_outstanding': 1e9, 'current_assets': r * .5,
                'current_liabilities': r * .3, 'ppe': r * .3}
        for k, v in inst.items():
            F.add_fact(fb, k, 'I@' + end, end, v, 'syn:fs', 'as_reported', f'{2022 + i}-02-10')
    F.set_price(fb, price, as_of, 'syn:px')
    return fb


def factors(cf):
    return {a['factor']: a['value'] for a in cf['fc']['cost_of_equity']['components']['adjustments']}


def premium(cf):
    return {c['component']: c for c in cf['dc']['expected_return']['premium_components']}


class IndustryWordIsNotEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.word = pipeline.run(machinery_like(), {'forecast': MARKET})
        cls.confirmed = pipeline.run(machinery_like(), {'forecast': MARKET, 'card': {'cyclical': {
            'value': True, 'reason': 'orders fell 30% in the last downturn per the 10-K backlog table (TEST)'}}})
        cls.treated = pipeline.run(machinery_like(), {'forecast': dict(MARKET, cyclical={
            'treatment': 'cyclical', 'reason': 'orders fell 30% in the last downturn (TEST)', 'evidence_ids': ['syn:fs']})})
        cls.plain = pipeline.run(machinery_like('Industrial Products'), {'forecast': MARKET})

    def test_card_reads_a_word_only_flag(self):
        cy = self.word['card']['cyclical']
        self.assertEqual((cy['value'], cy['basis'], cy['data_shows_cycle'], cy['normalize'], cy['industry_word']),
                         (True, 'industry_flag', False, False, 'machinery'))
        ev = M.cyclical_evidence(self.word['card'])
        self.assertEqual((ev['evidenced'], ev['basis']), (False, 'industry_word'))
        self.assertIn("'machinery'", ev['question'])
        self.assertIn('inputs.cyclical', ev['question'])
        self.assertFalse(self.plain['card']['cyclical']['value'])                        # the same history without the word

    def test_word_only_flag_charges_no_cost_of_equity_row_and_stays_a_question(self):
        fc = self.word['fc']
        self.assertNotIn('cyclical', factors(self.word))                               # was +0.5pp from the word alone
        comp = fc['cost_of_equity']['components']
        self.assertEqual((comp['cyclical_evidence']['evidenced'], comp['cyclical_evidence']['basis']), (False, 'industry_word'))
        self.assertIn('machinery', comp['cyclical_evidence']['question'])
        self.assertEqual(fc['anchors']['route']['cyclical_evidence']['evidenced'], False)
        w = {x['code']: x for x in fc['warnings']}
        self.assertEqual(w['cyclical_unevidenced']['severity'], 'low')
        # the explicit table is the same as for the identical history without the word (same stage, intensity, dimensions)
        self.assertAlmostEqual(comp['table_rate'], self.plain['fc']['cost_of_equity']['components']['table_rate'], places=12)
        self.assertNotIn('cyclical_evidence', self.plain['fc']['cost_of_equity']['components'])

    def test_word_only_flag_charges_no_hurdle_premium(self):
        c = premium(self.word)['cyclicality']
        self.assertEqual(c['value'], 0.)                                                 # was 1pp from the word alone
        self.assertIn('not evidenced', c['basis'])
        self.assertIn('machinery', c['basis'])
        self.assertEqual(premium(self.plain)['cyclicality']['value'], 0.)

    def test_an_analyst_confirmed_cycle_is_charged_in_both(self):
        for cf, how in ((self.confirmed, 'card inputs.cyclical'), (self.treated, 'forecast inputs.cyclical treatment')):
            self.assertEqual(factors(cf).get('cyclical'), .005, how)
            ev = cf['fc']['cost_of_equity']['components']['cyclical_evidence']
            self.assertEqual((ev['evidenced'], ev['basis']), (True, 'analyst'), how)
            self.assertNotIn('cyclical_unevidenced', {x['code'] for x in cf['fc']['warnings']}, how)
            self.assertEqual(premium(cf)['cyclicality']['value'], .01, how)
            self.assertIn('analyst', premium(cf)['cyclicality']['basis'], how)


class EvidenceRuleTests(unittest.TestCase):
    """model_card.cyclical_evidence on engine cards: data, type and analyst evidence; a word is a question."""

    def test_engine_cards(self):
        from test_model_card import steady_fb, card, synth_fb, series_years
        # type: a semiconductor with steady margins (Broadcom-like): no cycle in the data, cyclical by economics
        semi = card(steady_fb('Information Technology', 'Semiconductors', [2.74e10, 3.32e10, 3.58e10, 3.95e10, 4.35e10],
                              [.40, .42, .41, .42, .43], .70, .02))
        self.assertEqual(semi['cyclical']['data_shows_cycle'], False)
        self.assertEqual({k: M.cyclical_evidence(semi)[k] for k in ('evidenced', 'basis')}, {'evidenced': True, 'basis': 'type'})
        # data: a chemicals maker whose revenue fell and recovered with swinging margins
        rev, om = [1e10, 1.25e10, .95e10, 1.1e10, 1.3e10], [.12, .2, .04, .1, .16]
        chem = card(synth_fb({'sector': 'Materials', 'industry': 'Specialty Chemicals'}, series_years(
            5, revenue=rev, operating_income=[r * m for r, m in zip(rev, om)], cfo=[r * (m + .05) for r, m in zip(rev, om)],
            capex=[r * .06 for r in rev], d_and_a=[r * .05 for r in rev], cash=[1e9] * 5, equity=[8e9] * 5)))
        self.assertTrue(chem['cyclical']['data_shows_cycle'])
        self.assertEqual(M.cyclical_evidence(chem)['basis'], 'data')
        self.assertTrue(M.cyclical_evidence(chem)['evidenced'])
        # an analyst who rejects the cycle settles it, even for a cyclical type
        rej = card(steady_fb('Information Technology', 'Semiconductors', [2.74e10, 3.32e10, 3.58e10, 3.95e10, 4.35e10],
                             [.40, .42, .41, .42, .43], .70, .02), {'cyclical': {'value': False, 'reason': 'custom silicon under contract (TEST)'}})
        self.assertEqual({k: M.cyclical_evidence(rej)[k] for k in ('evidenced', 'basis')}, {'evidenced': False, 'basis': 'analyst'})
        # a word at a margin peak of a history with no cycle: valued around the cycle (the conservative reading, an unresolved
        # material issue), but the word is still not evidence for a rate charge
        rev = [9e9, 9.5e9, 10e9, 10.6e9, 11.2e9]
        up = card(steady_fb('Industrials', 'Industrial Machinery', rev, [.10, .11, .12, .13, .20], .3, .04))
        self.assertEqual((up['cyclical']['basis'], up['valuation']['method']), ('industry_flag+peak', 'normalized_cycle_fcff'))
        ev = M.cyclical_evidence(up)
        self.assertEqual((ev['evidenced'], ev['basis']), (False, 'industry_word'))
        self.assertIn('possible peak', ev['question'])
        # the same path labelled steel is a commodity producer: cyclical by type
        steel = card(steady_fb('Materials', 'Steel', rev, [.10, .11, .12, .13, .20], .25, .06))
        self.assertEqual((steel['business_model']['primary'], M.cyclical_evidence(steel)['basis']), ('commodity', 'type'))

    def test_cards_without_a_recorded_basis_keep_their_value(self):
        self.assertEqual(M.cyclical_evidence({'cyclical': {'value': True}})['evidenced'], True)
        self.assertEqual(M.cyclical_evidence({'cyclical': {'value': False}})['evidenced'], False)
        self.assertEqual(M.cyclical_evidence({'cyclical': WORD_ONLY}, 'cyclical')['basis'], 'analyst')


class HurdlePremiumTests(unittest.TestCase):
    """The decision reads the same evidence: the executed forecast's route when it ran, else the model card."""

    def comps(self, card, fc=None):
        p = parts(35, card=card)
        if fc is not None:
            p['fc'] = fc
        return {c['component']: c for c in decision.decide(p)['expected_return']['premium_components']}

    def test_card_evidence(self):
        self.assertEqual(self.comps(card_saas(cyclical=dict(WORD_ONLY)))['cyclicality']['value'], 0.)
        self.assertEqual(self.comps(card_saas(cyclical=dict(WORD_ONLY, data_shows_cycle=True, basis='data')))['cyclicality']['value'], .01)
        ov = {'value': True, 'reason': 'orders fell 30% in the last downturn (TEST)', 'evidence_ids': []}
        self.assertEqual(self.comps(card_saas(cyclical=dict(WORD_ONLY, basis='analyst', override=ov)))['cyclicality']['value'], .01)
        self.assertEqual(self.comps(card_saas(cyclical={'value': False}))['cyclicality']['value'], 0.)

    def test_forecast_route_evidence_wins(self):
        # the forecast's analyst treatment 'cyclical' is not on the card: the executed route carries it to the hurdle
        fc = {'assumptions': [], 'anchors': {'route': {'cyclical_evidence': M.cyclical_evidence({'cyclical': WORD_ONLY}, 'cyclical')}}}
        self.assertEqual(self.comps(card_saas(cyclical=dict(WORD_ONLY)), fc)['cyclicality']['value'], .01)

    def test_cyclicality_is_priced_once_not_netted(self):
        # documented netting check (decision CYCLICALITY note): hurdle = max(index + premium, Ke) never adds the Ke's cyclical row
        # to this component, so an evidenced cyclical's hurdle exceeds the identical non-cyclical's by at most the larger of the
        # two (1pp), whichever leg binds; and a binding Ke floor's top-up row is net of the component (the rows sum to Ke)
        dc = decision.decide(parts(35))
        A, index = dc['assessments'], dc['expected_return']['index_expected_return']
        g = {'funding': False, 'funding_conditional': [], 'red_flags': []}
        an = parts(35)['an']
        cyc, plain = card_saas(cyclical=dict(WORD_ONLY, basis='data', data_shows_cycle=True)), card_saas()
        for ke_plain in (.07, .09, .1135, .13):
            r1 = decision._required_return(A, index, cyc, g, None, an, {'value': ke_plain + .005}, M.cyclical_evidence(cyc))
            r0 = decision._required_return(A, index, plain, g, None, an, {'value': ke_plain}, M.cyclical_evidence(plain))
            self.assertLessEqual(r1['hurdle'] - r0['hurdle'], decision.PREMIUM['cyclicality'] + 1e-12, ke_plain)
            self.assertGreater(r1['hurdle'], r0['hurdle'], ke_plain)
            self.assertAlmostEqual(sum(c['value'] for c in r1['components']) + index['value'], r1['hurdle'], places=9)
            self.assertEqual([c['component'] for c in r1['components']].count('cyclicality'), 1)


if __name__ == '__main__':
    unittest.main()
