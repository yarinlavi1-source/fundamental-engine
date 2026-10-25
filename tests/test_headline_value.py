"""Headline value, buy-below price, market comparator and expected price path (owner fix F7, 25.10.2026).

The headline 'fair value' was the value at the hurdle = max(index + premiums, Ke): 14-33% below the value at the company's
own cost of equity on the diagnosis profiles, a required-return choice shown as the company's worth. The default comparator
was risk-free + ERP although an engine-consistent calibration existed. And no row said where the price itself is likely to
go. These tests pin: fair value = executed base value at the model cost of equity (the hurdle value is the buy-below row),
the comparator is named (engine-consistent / generic / user), the calibration file is picked without look-ahead, and the
price path is price x (1 + r)^h with r halfway between the base IRR and the market return, shown with its accuracy band.
Fixtures are the FICTSAAS stand-ins of test_decision_render; no private evidence is used.
"""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_decision_render import parts, casefile                                                                     # noqa: E402

from fundamental_engine import calibration                                                                            # noqa: E402
from fundamental_engine.cli import index_input_for, with_index                                                        # noqa: E402
from fundamental_engine.decision import decide, PRICE_PATH_HIT, PRICE_PATH_PULL                                       # noqa: E402
from fundamental_engine.render import hebrew_table, hebrew_paragraph, markdown                                        # noqa: E402

FC = {'assumptions': [], 'cost_of_equity': {'base': .095, 'components': {'risk_free': .04, 'equity_risk_premium': .05}}}
HIGH_INDEX = {'value': .095, 'basis': 'index assumption above the model rate (test)', 'source': 'test input'}
CALIBRATION = {'value': .0696, 'low': .0596, 'high': .0796, 'rate_shift': .0233, 'market_cost_of_equity': .0929,
               'basis': 'engine-consistent market return (test copy of a sweep)',
               'source': 'fundamental_engine.calibration.market_implied over the large-cap sweep casefiles'}


def _row(table, label):
    return next(line for line in table.splitlines() if line.startswith(f'| {label}'))


class FairValueHeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = parts(35)
        cls.d = decide(cls.p, {'index': HIGH_INDEX})                      # required return 10.5% > the model's own 10%
        cls.er, cls.view = cls.d['expected_return'], cls.d['valuation_view']
        cls.table = hebrew_table(casefile(cls.p, cls.d))

    def test_fair_value_is_the_executed_value_at_the_cost_of_equity(self):
        fv, res = self.er['fair_value'], self.p['val']['result']
        self.assertEqual(fv['value'], res['annual_values'][0]['base'])
        self.assertEqual((fv['bear'], fv['bull']), (res['annual_values'][0]['bear'], res['annual_values'][0]['bull']))
        self.assertEqual(fv['cost_of_equity'], self.view['model_cost_of_equity'])
        self.assertAlmostEqual(fv['change_from_price'], fv['value'] / 35 - 1)

    def test_buy_below_price_is_the_value_at_the_required_return(self):
        bb = self.er['buy_below_price']
        self.assertEqual(bb['value'], self.er['value_at_required_return'])
        self.assertEqual(bb['required_return'], self.er['hurdle'])
        self.assertGreater(self.er['hurdle'], self.view['model_cost_of_equity'])
        self.assertLess(bb['value'], self.er['fair_value']['value'])     # a stricter return pays less: never the headline

    def test_table_headline_is_the_fair_value_and_the_hurdle_value_is_the_buy_below_row(self):
        base, pfh = self.view['base'], self.er['value_at_required_return']
        self.assertIn(f'{base:,.1f}$', _row(self.table, 'שווי הוגן (תרחיש בסיסי)'))
        self.assertNotIn(f'{pfh:,.1f}', _row(self.table, 'שווי הוגן (תרחיש בסיסי)'))
        self.assertIn(f'זולה בכ־{1 - 35 / base:.0%}', _row(self.table, 'המחיר מול השווי'))
        buy = _row(self.table, 'מחיר קנייה מרבי לתשואה שאתה דורש (10.5% בשנה)')
        self.assertIn(f'{pfh:,.1f}$', buy)
        self.assertIn(f'נמוך ממנו בכ־{1 - 35 / pfh:.0%}', buy)
        self.assertIn(f"{self.view['bear']:,.1f}$ / {self.view['bull']:,.1f}$", _row(self.table, 'דובי / שורי'))
        self.assertIn('זו דרישה שלך, לא שווי החברה', self.table)

    def test_yearly_table_and_five_year_row_are_at_the_fair_value_rate(self):
        yr = self.er['yearly_at_model_cost_of_equity']
        res = self.p['val']['result']
        exec_rows = {r['date']: r for r in res['annual_values']}
        for r in yr['rows']:
            self.assertEqual(r['base'], exec_rows[r['date']]['base'])    # read from the executed result, nothing revalued
        self.assertIn(f"| היום | {self.view['bear']:,.1f}$ | {self.view['base']:,.1f}$ | {self.view['bull']:,.1f}$ |", self.table)
        f5 = self.view['five_year']
        self.assertIn(f"{f5['base_value']:,.1f}$", _row(self.table, 'שווי משוער בעוד 5 שנים'))

    def test_paragraph_and_report_lead_with_the_fair_value(self):
        text = hebrew_paragraph(casefile(self.p, self.d))
        self.assertIn(f"השווי ההוגן (בשיעור ההיוון של החברה, כ־10.0%", text)
        self.assertIn(f"הוא כ־{self.view['base']:,.1f} USD", text)
        self.assertIn(f"כ־{self.er['value_at_required_return']:,.1f}", text)
        md = markdown(casefile(self.p, self.d))
        self.assertLess(md.index('> Fair value: base value at the company'), md.index('> Buy-below price for your required return'))

    def test_paragraph_stays_within_the_owner_length_with_both_values(self):
        for price in (30, 35, 40, 44, 50, 70):
            for inputs in (None, {'index': HIGH_INDEX}, {'index': CALIBRATION}):
                p = parts(price)
                d = decide(p, inputs)
                text = hebrew_paragraph(casefile(p, d))
                self.assertTrue(80 <= len(text.split()) <= 140, (price, inputs, len(text.split())))
                self.assertIn(f"{d['valuation_view']['base']:,.1f} USD", text)               # the fair value is always stated
                self.assertIn(f"כ־{d['expected_return']['value_at_required_return']:,.1f}", text)   # and the buy-below price

    def test_no_valuation_number_moves(self):
        # relabel only: the hurdle, the value at it, the verdict and the executed values are what they were
        res = self.p['val']['result']
        self.assertEqual(self.view['base'], res['annual_values'][0]['base'])
        self.assertAlmostEqual(self.er['hurdle'], .105)
        self.assertEqual(self.d['verdict']['value_at_required_return'], self.er['value_at_required_return'])
        self.assertEqual(self.d['verdict']['code'], 'attractive_at_price')


class ComparatorTests(unittest.TestCase):
    def test_default_index_is_named_the_generic_assumption(self):
        for fc in (None, FC):
            p = parts(35, fc=fc) if fc else parts(35)
            d = decide(p)
            self.assertEqual(d['expected_return']['index_expected_return']['comparator'], 'generic_assumption')
            t = hebrew_table(casefile(p, d))
            self.assertIn('הנחת שוק כללית: כ־', _row(t, 'תשואה שנתית צפויה במחיר של היום'))
            self.assertIn('ריבית חסרת סיכון ועוד פרמיית סיכון למניות', t)

    def test_calibration_index_is_the_engine_consistent_market(self):
        p = parts(70)
        d = decide(p, {'index': CALIBRATION})
        idx = d['expected_return']['index_expected_return']
        self.assertEqual(idx['comparator'], 'engine_consistent')
        no_shift = {k: v for k, v in CALIBRATION.items() if k not in ('rate_shift', 'market_cost_of_equity')}
        self.assertEqual(decide(p, {'index': no_shift})['expected_return']['index_expected_return']['comparator'], 'engine_consistent')
        t = hebrew_table(casefile(p, d))
        self.assertIn('השוק כולו, באותו מנוע: כ־7.0%', _row(t, 'תשואה שנתית צפויה במחיר של היום'))
        self.assertNotIn('הנחת שוק כללית', t)

    def test_other_user_index_is_named_a_supplied_assumption(self):
        d = decide(parts(35), {'index': HIGH_INDEX})
        self.assertEqual(d['expected_return']['index_expected_return']['comparator'], 'user_assumption')

    def test_calibration_pick_never_looks_ahead_and_skips_stale_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            def cal(name, as_of, status='computed'):
                # a sweep of this engine version (calibration.pick skips another version's: CalibrationEngineVersionTests)
                f = Path(tmp) / name
                f.write_text(json.dumps({'status': status, 'as_of': as_of, 'engine_version': calibration.ENGINE_VERSION,
                                         'index_input': {**CALIBRATION, 'basis': as_of}}))
                return f
            files = [cal('a.json', '2026-07-01'), cal('b.json', '2026-10-20'), cal('c.json', '2026-11-30'),
                     cal('d.json', '2026-10-24', status='not_computed'), Path(tmp) / 'missing.json']
            hit = calibration.pick('2026-10-25', files)
            self.assertEqual((hit['as_of'], hit['age_days'], hit['index_input']['basis']), ('2026-10-20', 5, '2026-10-20'))
            self.assertIsNone(calibration.pick('2026-10-19', files[1:]))           # only a later file: no look-ahead
            self.assertIsNone(calibration.pick('2027-03-01', files[:2]))           # older than MAX_AGE_DAYS
            self.assertEqual(calibration.pick('2026-07-02', files)['as_of'], '2026-07-01')
            # the CLI: explicit file, 'none', automatic pick; an automatic pick never replaces the inputs' own index
            got, used = index_input_for(None, '2026-10-25', files)
            self.assertEqual((got['basis'], 'b.json' in used), ('2026-10-20', True))
            self.assertEqual(index_input_for('none', '2026-10-25', files)[0], None)
            self.assertEqual(index_input_for(str(files[0]), '2026-10-25', files)[0]['basis'], '2026-07-01')
            self.assertIsNone(index_input_for(None, '2026-10-19', files[1:3])[0])
            own = {'decision': {'index': HIGH_INDEX}}
            self.assertEqual(with_index(own, got, explicit=False), own)
            self.assertEqual(with_index(own, got, explicit=True)['decision']['index'], got)
            self.assertEqual(with_index({}, got, explicit=False)['decision']['index'], got)
            self.assertEqual(with_index(None, None, explicit=False), {})


class CalibrationEngineVersionTests(unittest.TestCase):
    """Integration review of eb58e73: the automatic pick used any sweep dated within 92 days, so after the 0.13.0 calibration
    fixes (values and IRRs up) the default index of every deep/pipeline run became 0.12.0's 2026-10-20 sweep (7.0%), labelled
    the engine-consistent market of 'the same engine'. The bias that cancels in stock IRR - market IRR is one engine's."""

    def test_market_implied_records_its_engine_version(self):
        out = calibration.market_implied([{'symbol': 'A', 'base_irr': .08, 'cost_of_equity': .09},
                                          {'symbol': 'B', 'base_irr': .06, 'cost_of_equity': .09}], {'A': 3e11, 'B': 1e11}, '2026-10-25')
        self.assertEqual(out['engine_version'], calibration.ENGINE_VERSION)                                        # OLD
        self.assertEqual(out['index_input']['engine_version'], calibration.ENGINE_VERSION)                         # OLD

    def test_pick_skips_a_sweep_of_another_engine_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            def cal(name, as_of, **ver):
                f = Path(tmp) / name
                f.write_text(json.dumps({'status': 'computed', 'as_of': as_of, **ver, 'index_input': {**CALIBRATION, 'basis': name}}))
                return f
            unversioned = cal('old.json', '2026-10-20')                               # as the saved 0.12.0 sweep
            other = cal('other.json', '2026-10-22', engine_version='0.0.1')
            self.assertIsNone(calibration.pick('2026-10-25', [unversioned, other]))                                  # OLD
            got, used = index_input_for(None, '2026-10-25', [unversioned, other])
            self.assertIsNone(got)                                                                                    # OLD
            self.assertIn(f'engine {calibration.ENGINE_VERSION}', used)
            same = cal('same.json', '2026-10-21', engine_version=calibration.ENGINE_VERSION)
            hit = calibration.pick('2026-10-25', [unversioned, other, same])          # the newest of THIS engine, not the newest file
            self.assertEqual((hit['index_input']['basis'], hit['engine_version']), ('same.json', calibration.ENGINE_VERSION))
            self.assertEqual(hit['index_input']['engine_version'], calibration.ENGINE_VERSION)
            # an explicit --index still applies the file the owner names
            self.assertEqual(index_input_for(str(unversioned), '2026-10-25', [])[0]['basis'], 'old.json')

    def test_index_of_another_engine_version_is_a_supplied_assumption(self):
        p = parts(70)
        idx = decide(p, {'index': {**CALIBRATION, 'engine_version': '0.0.1'}})['expected_return']['index_expected_return']
        self.assertEqual(idx['comparator'], 'user_assumption')                                                       # OLD
        self.assertIn('not this engine', idx['basis'])
        same = decide(p, {'index': {**CALIBRATION, 'engine_version': calibration.ENGINE_VERSION}})
        self.assertEqual(same['expected_return']['index_expected_return']['comparator'], 'engine_consistent')
        self.assertEqual(decide(p, {'index': CALIBRATION})['expected_return']['index_expected_return']['comparator'],
                         'engine_consistent')                                     # no version stated: as before (explicit input)


class PricePathTests(unittest.TestCase):
    def test_path_compounds_today_price_at_the_irr_halfway_to_the_market(self):
        p = parts(35, fc=FC)
        d = decide(p)
        er = d['expected_return']
        pp = er['expected_price_path']
        irr, mkt = er['base_irr'], er['index_expected_return']['value']
        self.assertEqual(PRICE_PATH_PULL, .5)
        self.assertAlmostEqual(pp['rate'], (irr + mkt) / 2)
        self.assertEqual([r['years'] for r in pp['rows']], [1, 2, 3, 4, 5])
        for r in pp['rows']:
            self.assertAlmostEqual(r['price'], 35 * (1 + pp['rate']) ** r['years'])
        d0 = date.fromisoformat(d['price']['price_as_of'])
        self.assertEqual([r['date'] for r in pp['rows']], [d0.replace(year=d0.year + h).isoformat() for h in range(1, 6)])
        self.assertEqual(pp['hit_rate_within_band'], PRICE_PATH_HIT)
        self.assertEqual(PRICE_PATH_HIT, {1: .65, 3: .50, 5: .37})
        self.assertEqual(pp['comparator'], 'generic_assumption')

    def test_table_row_and_band_statement(self):
        p = parts(35, fc=FC)
        d = decide(p)
        pp = d['expected_return']['expected_price_path']
        t = hebrew_table(casefile(p, d))
        row = _row(t, 'מחיר צפוי למניה')
        self.assertIn(f"כ־{pp['rate'] * 100:.1f}% בשנה", row)
        for r in pp['rows']:
            self.assertIn(f"{r['date'][:7].replace('-', '/')}: {r['price']:,.1f}$", row)
        self.assertIn('±25%', t)
        self.assertIn('בכ־65% מהמקרים אחרי שנה, בכ־50% אחרי 3 שנים ובכ־37% אחרי 5 שנים', t)
        self.assertIn('לא הבטחה ולא יעד', t)
        self.assertIn('| מה המחיר של היום מניח |', hebrew_table({**casefile(p, d), 'price_implied_growth': {
            'status': 'solved', 'growth_shift': .03, 'horizon': 10, 'implied_real_cagr': .14, 'share_at_least': .18}}))
        self.assertIn('Expected price path', markdown(casefile(p, d)))
        # a JSON round trip (casefile on disk) keeps the band sentence
        cf = json.loads(json.dumps(casefile(p, d), default=str))
        self.assertIn('בכ־37% אחרי 5 שנים', hebrew_table(cf))

    def test_path_follows_the_named_market_and_is_unavailable_without_an_irr(self):
        p = parts(70)
        d = decide(p, {'index': CALIBRATION})
        pp = d['expected_return']['expected_price_path']
        self.assertAlmostEqual(pp['market_return'], .0696)
        self.assertEqual(pp['comparator'], 'engine_consistent')
        view = deepcopy(d['valuation_view'])
        from fundamental_engine.decision import _price_path
        self.assertEqual(_price_path(view, None, d['expected_return']['index_expected_return'], '2026-09-27')['status'], 'unavailable')
        self.assertEqual(_price_path({**view, 'price': None}, .1, {'value': .07}, '2026-09-27')['status'], 'unavailable')
        leap = _price_path({'price': 10., 'price_as_of': '2024-02-29'}, .1, {'value': .06}, None)
        self.assertEqual(leap['rows'][0]['date'], '2025-02-28')


if __name__ == '__main__':
    unittest.main()
