"""WP4 of the econ layer: model selection (econ.select, select_case, select_factbook, kpi_fact_ids).

The rules are the design's (docs/prereg_econ.md, brain/economics.json `select` blocks): the card type, the industry /
SIC / profile text, the segment names and the KPI facts; two agreeing signals select a model, KPI facts outrank labels,
no match is `unclassified` (not assessed, no effect) and an analyst override wins only with a reason and evidence, with
the rule result shown beside it. Cases are the WP0 fixtures without their `expected` block; a fixture's `model_hint` is
the expected answer, never an engine input (its card type and text are what a caller would pass). Every behaviour test
fails without WP4 (no econ.select); the no-wiring guard passes on the base by design.
"""
import json
import random
import unittest
from pathlib import Path

from fundamental_engine import econ, facts as F

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests' / 'fixtures' / 'econ'
UBER_LABEL = {'industry': 'Software - Application', 'sector': 'Technology'}     # the FMP label the comparison found
# uber_2025q3.json segments[Mobility].gross_bookings, analyst_post_uber_2025q3 available 2025-11-04
UBER_MOBILITY_GB, UBER_Q3_25, UBER_DAY = 25.1e9, ('Q@2025-09-30', '2025-09-30'), '2025-11-04'


def fixture(name):
    return json.loads((FIX / f'{name}.json').read_text(encoding='utf-8'))


def case(name):
    d = fixture(name)
    d.pop('expected')
    return d


def hinted(name, **kw):
    """select_case with the card type and text a caller would pass for the fixture (its model_hint's inputs only)."""
    h = fixture(name)['model_hint']
    text = {'profile': h['profile_text']} if h.get('profile_text') else {}
    if h.get('provider_label'):
        text['industry'] = h['provider_label']
    return econ.select_case(case(name), h.get('card_type'), text or None, **kw)


def outcome(sel, model_id):
    return next(c['outcome'] for c in sel['candidates'] if c['model'] == model_id)


class DesignTests(unittest.TestCase):
    def test_uber_gross_bookings_select_marketplace_over_the_software_label(self):
        for name in ('uber_2025q3', 'uber_2026q2'):
            s = hinted(name)
            self.assertEqual((s['primary'], s['secondary'], s['source'], s['status']), ('marketplace', [], 'rule', 'selected'), name)
            self.assertEqual(outcome(s, 'data_platform_software'), 'overridden', name)
            over = [r for r in s['reasons'] if r.startswith('data_platform_software:')]
            self.assertEqual(len(over), 1)
            for word in ("industry 'Software - Application'", 'overridden', 'gross_bookings'):
                self.assertIn(word, over[0], name)
            self.assertIn('KPI facts gross_bookings', s['reasons'][0])
            self.assertEqual(s['primary'], fixture(name)['model_hint']['primary'])
        # the KPI fact decides: the same label without it reads as a software vendor (labels only)
        bare = econ.select('subscription_software', UBER_LABEL, [])
        self.assertEqual((bare['primary'], bare['status']), ('data_platform_software', 'selected'))
        self.assertIn('labels only: no KPI fact of this model is available', bare['reasons'][0])

    def test_ibkr_is_a_broker_not_the_loans_deposits_tree(self):
        for name in ('ibkr_2026q2', 'ibkr_2026q3'):
            s = hinted(name)
            self.assertEqual((s['primary'], s['secondary']), ('broker', []), name)
            self.assertEqual(outcome(s, 'digital_bank'), 'one_signal')
            self.assertEqual(s['candidates'][0]['signals']['kpis'], ['margin_loans', 'client_equity', 'darts'])
            self.assertEqual(s['candidates'][0]['count'], 3)
        # SYNTHETIC text that also says bank: the bank model has two label signals and still loses to the broker's KPI facts
        s = econ.select_case(case('ibkr_2026q3'), 'financials', {'industry': 'Financial - Capital Markets',
                                                                 'profile': 'online brokerage with a bank deposit sweep'})
        self.assertEqual((s['primary'], s['secondary'], outcome(s, 'digital_bank')), ('broker', [], 'overridden'))
        self.assertNotIn('bank_capital', s['overlays'])
        r, base = econ.review(case('ibkr_2026q3'), s), econ.review(case('ibkr_2026q3'), 'broker')
        self.assertEqual(r['model'], {'primary': 'broker', 'secondary': [], 'source': 'rule'})
        self.assertEqual([m['model'] for m in r['models']], ['broker'])
        self.assertEqual(r['identities'], base['identities'])

    def test_skhy_reads_as_memory_cycle(self):
        s = hinted('skhy_2026q2')
        self.assertEqual((s['primary'], s['secondary'], s['overlays']),
                         ('memory_cycle', [], ['contract_coverage', 'funded_customer_chain']))
        self.assertEqual({t['keyword'] for t in s['candidates'][0]['signals']['text']}, {'memory', 'dram', 'nand'})
        self.assertEqual(s['candidates'][0]['signals']['kpis'], ['asp'])        # bits: a row with no level or growth
        self.assertEqual(outcome(s, 'gpu_vendor'), 'one_signal')
        # one coarse card type never selects: 'semiconductor' covers memory makers and GPU vendors alike
        s = econ.select('semiconductor', None, [])
        self.assertEqual((s['primary'], s['status']), ('unclassified', 'not_assessed'))
        self.assertEqual({c['model'] for c in s['candidates']}, {'memory_cycle', 'gpu_vendor'})

    def test_an_industrial_without_kpis_is_unclassified_with_no_effect(self):
        s = econ.select('industrial', {'industry': 'Industrial - Machinery', 'sector': 'Industrials'}, [])
        self.assertEqual((s['primary'], s['secondary'], s['overlays'], s['status'], s['source'], s['candidates']),
                         ('unclassified', [], [], 'not_assessed', 'rule', []))
        self.assertIn('never relabelled', s['reasons'][-1])
        r = econ.review(case('ibkr_2026q3'), s)
        self.assertEqual((r['status'], r['identities'], r['flags'], r['models']), ('not_assessed', [], [], []))
        self.assertEqual(econ.select(None, None, None)['primary'], 'unclassified')
        # one word never forces a label: a senior-living operator's 'memory care' is not a memory maker
        s = econ.select('healthcare_services', {'profile': 'senior living with assisted living and memory care'}, [])
        self.assertEqual((s['primary'], outcome(s, 'memory_cycle')), ('unclassified', 'one_signal'))

    def test_an_analyst_override_wins_and_both_results_are_shown(self):
        ov = {'primary': 'digital_bank', 'reason': 'SYNTHETIC: test of precedence only', 'evidence_ids': ['analyst_post_ibkr_2026q3']}
        s = econ.select_case(case('ibkr_2026q3'), 'financials', 'investment brokerage; capital markets', override=ov)
        self.assertEqual((s['primary'], s['source'], s['status'], s['overlays']), ('digital_bank', 'analyst', 'selected', ['bank_capital', 'segments']))
        self.assertEqual((s['rule']['primary'], s['rule']['status']), ('broker', 'selected'))
        self.assertEqual(s['override']['agrees_with_rule'], False)
        self.assertEqual((s['override']['evidence_checked'], s['override']['evidence_kinds']), (True, ['analyst_post']))
        self.assertTrue(s['reasons'][0].startswith('analyst override: digital_bank (SYNTHETIC: test of precedence only; '
                                                   'evidence analyst_post_ibkr_2026q3)'))
        self.assertIn("rule result shown beside under 'rule': broker; the analyst choice wins", s['reasons'][1])
        self.assertIn('broker:', s['rule']['reasons'][0])
        # a hybrid the rules leave unclassified (the fixture hint gives no card type or text) is set by the analyst
        ov = {'primary': 'subscriber_network', 'secondary': ['compute_leasing'], 'reason': 'Starlink subscribers and leased compute',
              'evidence_ids': ['analyst_post_spcx_2026q3']}
        s = econ.select_case(case('spcx_2026q3'), override=ov)
        self.assertEqual((s['primary'], s['secondary'], s['rule']['primary']), ('subscriber_network', ['compute_leasing'], 'unclassified'))
        self.assertEqual(s['overlays'], ['segments', 'contract_coverage', 'vendor_financing'])
        r = econ.review(case('spcx_2026q3'), s)
        self.assertEqual((r['model']['source'], [m['model'] for m in r['models']]), ('analyst', ['subscriber_network', 'compute_leasing']))
        # an override that agrees with the rule says so; one without known ids says they were not checked
        s = econ.select('financials', 'brokerage', ['accounts', 'darts'],
                        {'primary': 'broker', 'reason': 'accounts x revenue per account', 'evidence_ids': ['10k_item1']})
        self.assertEqual((s['override']['agrees_with_rule'], s['override']['evidence_checked']), (True, False))
        self.assertIn('ids not checked', s['reasons'][0])
        self.assertIn('the analyst agrees with it', s['reasons'][1])
        s = econ.select('financials', None, [], {'primary': 'unclassified', 'reason': 'insurer: no model fits', 'evidence_ids': ['x']})
        self.assertEqual((s['primary'], s['status'], s['overlays']), ('unclassified', 'not_assessed', []))

    def test_an_override_without_a_reason_or_usable_evidence_is_refused(self):
        c = case('spcx_2026q3')
        c['sources']['late'] = {'kind': 'issuer_filing', 'available_at': '2026-10-22'}      # SYNTHETIC: after as_of
        good = {'primary': 'subscriber_network', 'reason': 'subscribers x ARPU', 'evidence_ids': ['analyst_post_spcx_2026q3']}
        econ.select_case(c, override=good)
        bad = [{'reason': ''}, {'reason': None}, {'evidence_ids': []}, {'evidence_ids': None},
               {'evidence_ids': ['nope']},                                   # unknown
               {'evidence_ids': ['late']},                                   # not yet available at as_of
               {'evidence_ids': ['analyst_post_spcx_2026q3', 'test_assumption_flat_revenue']},   # an assumption
               {'primary': 'bank_tree'}, {'primary': None}, {'secondary': ['bank_tree']}, {'secondary': [{}]},
               {'primary': 'unclassified', 'secondary': ['broker']}, {'overlays': ['bank_capital']},
               {'overlays': ['nope']}, {'weight': 1}]
        for change in bad:
            with self.assertRaises(ValueError, msg=change):
                econ.select_case(c, override={**good, **change})
        with self.assertRaises(ValueError):
            econ.select('financials', None, [], 'broker')                   # an override is an object, never a bare label
        for args in (('bank', None, []), ('financials', 7, []), ('financials', None, ['acounts']), ('financials', None, 'darts'),
                     ('financials', None, [], None, None)):
            with self.assertRaises(ValueError, msg=args):
                econ.select(*args[:4], segments=args[4] if len(args) > 4 else None, evidence=7 if len(args) > 4 else None)

    def test_fixture_hints_reproduce_where_the_hint_carries_the_inputs(self):
        got = {n: hinted(n)['primary'] for n in ('uber_2025q3', 'uber_2026q2', 'ibkr_2026q2', 'ibkr_2026q3', 'pltr_2026q2',
                                                  'rddt_2026q2', 'skhy_2026q2', 'sofi_2026q2', 'nvda_2026q2', 'sn_2025', 'spcx_2026q3')}
        expected = {n: fixture(n)['model_hint']['primary'] for n in got}
        # NVDA and SN give only a card type (no text, no KPI rows of their model) and SPCX neither card nor text: one signal
        # at most, so unclassified until the caller supplies the industry text or an analyst override
        for n in ('nvda_2026q2', 'sn_2025', 'spcx_2026q3'):
            expected[n] = 'unclassified'
        self.assertEqual(got, expected)
        sofi = hinted('sofi_2026q2')
        self.assertIn("segment 'Lending' matches 'lending'", sofi['reasons'][0])
        self.assertEqual(sofi['overlays'], ['bank_capital', 'segments'])


class ContractTests(unittest.TestCase):
    def test_order_never_matters_and_the_hash_covers_every_input(self):
        kpis = ['gross_bookings', 'mapcs', 'trips', 'frequency', 'customers']
        segs = ['Mobility', 'Delivery', 'Freight']
        base = econ.select(['subscription_software', 'platform_marketplace'], UBER_LABEL, kpis, segments=segs)
        rnd = random.Random(4)
        for _ in range(5):
            k, s = kpis[:], segs[:]
            rnd.shuffle(k)
            rnd.shuffle(s)
            prof = dict(reversed(list(UBER_LABEL.items())))
            self.assertEqual(econ.select(['subscription_software', 'platform_marketplace'], prof, k + k[:1], segments=s + s[:1]), base)
        self.assertEqual(len(base['input_sha256']), 64)
        self.assertEqual(base['inputs']['segments'], ['Delivery', 'Freight', 'Mobility'])
        ov = {'primary': 'marketplace', 'reason': 'gross bookings x take rate', 'evidence_ids': ['rel']}
        hashes = {base['input_sha256'],
                  econ.select(['subscription_software', 'platform_marketplace'], UBER_LABEL, kpis, segments=segs[:2])['input_sha256'],
                  econ.select(['subscription_software', 'platform_marketplace'], UBER_LABEL, kpis, ov, segments=segs)['input_sha256'],
                  econ.select(['subscription_software', 'platform_marketplace'], UBER_LABEL, kpis, ov, segments=segs,
                              evidence={'rel': {'kind': 'issuer_document'}})['input_sha256']}
        self.assertEqual(len(hashes), 4)
        # the card's business_model block and a list with its primary first are the same input
        self.assertEqual(econ.select({'primary': 'subscription_software', 'secondary': ['platform_marketplace']}, UBER_LABEL, kpis,
                                     segments=segs), base)
        self.assertEqual((base['primary'], base['secondary'], base['overlays']), ('marketplace', ['data_platform_software'], ['segments', 'acquisition_sbc']))
        ads = econ.select(['subscription_software', 'platform_marketplace'], None, ['dau'])
        self.assertEqual(ads['primary'], 'ads_platform')
        self.assertIn("card type 'platform_marketplace' (a secondary type of the card); KPI facts dau", ads['reasons'][0])

    def test_factbook_kpi_facts_and_evidence_are_read_point_in_time(self):
        def book(as_of):
            fb = F.new({'ticker': 'UBER', 'currency': 'USD', **UBER_LABEL}, as_of)
            F.add_source(fb, 'post', 'user', 'analyst_post', 'secondary', UBER_DAY, note='analyst_post_uber_2025q3')
            F.add_source(fb, 'cons', 'fmp', 'financial-estimates-annual', 'consensus', UBER_DAY)
            F.add_kpi(fb, 'gross_bookings', *UBER_Q3_25, UBER_MOBILITY_GB, 'post', UBER_DAY, kpi_basis='flow', scope='mobility')
            F.add_qualitative(fb, 'q_mech', 'business model', 'SYNTHETIC: riders pay per trip', 'reported', ['post'], UBER_DAY)
            F.add_qualitative(fb, 'q_guess', 'business model', 'SYNTHETIC: a guess', 'assumption', ['post'], UBER_DAY)
            return fb
        fb = book(UBER_DAY)
        self.assertEqual(econ.kpi_fact_ids(fb), ['gross_bookings'])
        self.assertEqual(econ.kpi_fact_ids(fb, '2025-11-03'), [])                # published 2025-11-04: invisible the day before
        with self.assertRaises(ValueError):
            econ.kpi_fact_ids(fb, '2025-11-05')                                   # never later than the FactBook cutoff
        s = econ.select_factbook(fb, 'subscription_software', segments=['Mobility', 'Delivery'])
        self.assertEqual((s['primary'], s['inputs']['profile'], s['inputs']['kpi_ids']),
                         ('marketplace', {'industry': 'Software - Application', 'sector': 'Technology'}, ['gross_bookings']))
        early = econ.select_factbook(fb, 'subscription_software', as_of='2025-11-03')
        self.assertEqual((early['primary'], outcome(early, 'marketplace')), ('data_platform_software', 'one_signal'))
        ov = {'primary': 'marketplace', 'reason': 'riders pay per trip', 'evidence_ids': ['q_mech', 'post']}
        s = econ.select_factbook(fb, 'subscription_software', ov)
        self.assertEqual((s['source'], s['override']['evidence_kinds']), ('analyst', ['reported', 'secondary']))
        for ids in (['q_guess'], ['cons'], ['nope']):                            # an assumption, a consensus, an unknown id
            with self.assertRaises(ValueError, msg=ids):
                econ.select_factbook(fb, 'subscription_software', {**ov, 'evidence_ids': ids})
        with self.assertRaises(ValueError):                                      # the post is not yet available on 11-03
            econ.select_factbook(fb, 'subscription_software', ov, as_of='2025-11-03')
        # a FactBook company record's SIC code and description are read as text; absent fields are left out
        self.assertEqual(econ.profile_fields({'sic': 6211, 'sic_description': 'Security Brokers, Dealers & Flotation Companies',
                                              'industry': ' ', 'sector': 'Financial Services'}),
                         {'sector': 'Financial Services', 'sic': 'SIC 6211 Security Brokers, Dealers & Flotation Companies'})
        self.assertEqual(econ.profile_fields({'sic': True}), {})

    def test_case_kpi_facts_are_point_in_time_and_exclude_stated_identity_figures(self):
        c = case('ibkr_2026q3')
        self.assertEqual(econ.case_kpi_ids(c), ['accounts', 'client_equity', 'darts', 'margin_loans', 'margin_rate', 'net_new_accounts'])
        c['sources']['late'] = {'kind': 'issuer_release', 'available_at': '2026-10-22'}       # SYNTHETIC: after as_of
        c['sources']['cons'] = {'kind': 'consensus', 'available_at': '2026-10-21'}            # SYNTHETIC: never a KPI fact
        for k in c['kpis']:
            if k['id'] in ('darts', 'margin_loans'):
                k['source_ids'] = ['late']
            elif k['id'] == 'client_equity':
                k['source_ids'] = ['cons']
        self.assertEqual(econ.case_kpi_ids(c), ['accounts', 'margin_rate', 'net_new_accounts'])
        s = econ.select_case(c, 'financials', 'investment brokerage; capital markets')
        self.assertEqual((s['primary'], s['candidates'][0]['signals']['kpis']), ('broker', []))   # labels only now
        self.assertIn('labels only', s['reasons'][0])
        spcx = econ.select_case(case('spcx_2026q3'))
        self.assertEqual(spcx['inputs']['segments'], ['Space', 'Starlink', 'SuperIntelligence'])

    def test_no_wiring_nothing_in_the_engine_calls_select(self):
        import fundamental_engine
        pkg = Path(fundamental_engine.__file__).parent
        callers = sorted(p.name for p in pkg.glob('*.py') if p.name != 'econ.py'
                         and any(s in p.read_text(encoding='utf-8') for s in ('econ.select', 'select_case(', 'select_factbook(',
                                                                                'kpi_fact_ids(')))
        # close-out of WP7: quarter_review checks the model that may lower the peak flag with select_case, only on a
        # supplied case['economics'] (no input, no call: test_quarter_econ test_no_economics_no_change); nothing else does
        self.assertEqual(callers, ['quarter.py'])
        from fundamental_engine import quarter
        src = Path(quarter.__file__).read_text(encoding='utf-8')
        self.assertEqual(src.count('select_case('), 2)                    # both in quarter._model_evidence
        self.assertLess(src.index('def _model_evidence'), src.index('select_case('))


if __name__ == '__main__':
    unittest.main()
