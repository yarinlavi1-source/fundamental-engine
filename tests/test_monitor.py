"""monitor.py: thesis records, re-check triggers, ledger freezing and forecast-vs-actual attribution.

Fixtures: the real ADBE FactBook (FY2021-FY2025) anchors the analysis block; the driver spec, later fiscal
years, guidance items, prices and other tickers are SYNTHETIC test data, not Adobe results or forecasts.
"""
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine import facts as F
from fundamental_engine import monitor as M
from fundamental_engine.drivers import build_case
from fundamental_engine.ledger import Ledger, _h
from fundamental_engine.valuation import value_company

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())
A = '2026-10-19'
FY26, FY27, FY28 = '2026-11-27', '2027-11-26', '2028-11-24'
ENDS = [FY26, FY27, FY28, '2029-11-23', '2030-11-22', '2031-11-21']   # synthetic 52-week fiscal year ends
INSTANT = {'shares_outstanding', 'cash', 'long_term_debt_tagged', 'long_term_debt', 'current_portion_ltd'}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def spec(ticker='ADBE', as_of=A, growth=None, ccr=None, payout=1.0, min_cash=3e9, cash=6.0e9):
    s = json.loads((ROOT / 'examples/valuation/software_drivers.json').read_text())
    s.update({'as_of': as_of, 'company_id': ticker.lower() + '-synthetic-spec', 'ticker': ticker,
              'quote': {'price': 246.87, 'currency': 'USD', 'as_of': as_of, 'source_ids': ['synthetic']}})
    s['sources'][0].update(published_at=as_of, available_at=as_of)
    s['assumptions'][0]['as_of'] = as_of
    s['opening'] = {'as_of': as_of, 'assumption_ids': ['demo'], 'unrestricted_cash': cash, 'debt': 6.2e9, 'shares': 389.2e6,
                    'deferred_revenue': 7.0e9, 'tax_loss_carryforward': 0, 'marketable_investments': 0.5e9}
    s['report_dates'] = [as_of] + ENDS
    s['boundaries'] = list(ENDS)
    s['stub_annual_revenue_rate'] = s['last_full_year_revenue'] = 25.6e9
    growth = growth or {'bear': [.02] * 5, 'base': [.08, .07, .06, .05, .04], 'bull': [.12, .11, .10, .08, .07]}
    ccr = ccr or {'bear': .60, 'base': .53, 'bull': .50}
    tm = {n: max(.05, 1 - ccr[n] - .11) for n in ccr}
    for x in s['scenarios']:
        n = x['name']
        x.update(growth=growth[n], cash_cost_ratio=ccr[n], sbc_pct=.08, depreciation_pct=.03, maintenance_capex_pct=.015,
                 growth_capex_pct=.005, tax_rate=.19, interest_rate=.045, deferred_revenue_ratio=.27, working_capital_ratio=.05,
                 prepaid_share=.8, payout_fraction=payout, minimum_cash=min_cash, investment_income_after_tax=0, cost_of_equity=.10)
        x['terminal'].update(growth=.025, wacc=.095, roic=.3, operating_margin=tm[n])
    return s


def analysis(fb=ADBE):
    """Analysis block from the real ADBE fiscal years (what the casefile knew at analysis)."""
    out = {}
    for end, rev, _ in F.series(fb, 'revenue', 'annual'):
        out[end] = {m: F.value(fb, m, period_end=end) for m in ('revenue', 'operating_income', 'cfo', 'capex', 'sbc', 'shares_outstanding')}
    return {'annual': out}


ASSUMPTIONS = [
    {'id': 'revenue_growth', 'topic': 'growth', 'kind': 'analyst', 'values': {'bear': [.02] * 5, 'base': [.08, .07, .06, .05, .04],
     'bull': [.12, .11, .10, .08, .07]}, 'rationale': 'synthetic', 'falsifier': 'net new ARR turns negative for two quarters', 'source_ids': []},
    {'id': 'cash_cost_ratio', 'topic': 'cash_cost', 'kind': 'analyst', 'values': {'bear': .60, 'base': .53, 'bull': .50},
     'rationale': 'synthetic', 'falsifier': 'AI pricing pressure forces discounting', 'source_ids': []},
    {'id': 'cost_of_equity', 'topic': 'cost_of_equity', 'kind': 'model', 'values': {'bear': .10, 'base': .10, 'bull': .10},
     'rationale': 'synthetic', 'falsifier': None, 'source_ids': []},
    {'id': 'tax_rate', 'topic': 'tax', 'kind': 'analyst', 'values': {'bear': .21, 'base': .19, 'bull': .18},
     'rationale': 'synthetic', 'falsifier': 'minimum tax regime change', 'source_ids': []},
]


def casefile(s=None, ticker='ADBE', verdict='attractive_at_price', stage='mature', valuation=True):
    s = s or spec(ticker)
    val = {'status': 'not_computed', 'reason': 'test: no executed valuation'}
    if valuation:
        case = build_case(s)
        r = value_company(case)
        val = {'status': r['status'], 'case': case, 'result': r}
    return {'casefile_version': 1, 'engine_version': 'test', 'company': dict(ADBE['company'], ticker=ticker), 'as_of': s['as_of'],
            'factbook_sha256': F.fingerprint(ADBE), 'qa': {'status': 'ok'}, 'an': analysis(),
            'card': {'stage': {'value': stage}, 'business_model': {'primary': 'subscription_software'}, 'valuation': {'method': 'fcff_annual_path'}},
            'fc': {'spec': s, 'assumptions': ASSUMPTIONS}, 'val': val, 'rk': {}, 'rv': {'status': 'unreviewed_draft'},
            'dc': {'verdict': {'code': verdict, 'reason': 'synthetic test verdict'}, 'expected_return': {}, 'confidence': {'level': 'medium'}}}


def fc(th, metric, end):
    return next(f for f in th['forecasts'] if f['metric'] == metric and f['period_end'] == end)


def fy27(th, rev_mult=1.01, margin=None, fcf_margin=None, **over):
    """Synthetic FY2027 actuals expressed against the thesis base case."""
    rev = fc(th, 'revenue', FY27)['base'] * rev_mult
    margin = fc(th, 'operating_margin', FY27)['base'] if margin is None else margin
    fm = fc(th, 'fcf_after_sbc_margin', FY27)['base'] if fcf_margin is None else fcf_margin
    v = {'revenue': rev, 'operating_income': margin * rev, 'sbc': .08 * rev, 'capex': .02 * rev, 'd_and_a': .03 * rev,
         'cfo': fm * rev + .02 * rev + .08 * rev, 'shares_outstanding': 375e6, 'cash': 7e9, 'long_term_debt_tagged': 6.2e9}
    v.update(over)
    return v


FY26_VALUES = {'revenue': 25.5e9, 'operating_income': 9.2e9, 'sbc': 2.0e9, 'd_and_a': .8e9, 'capex': .4e9, 'cfo': 10.2e9,
               'pretax_income': 9.0e9, 'income_tax': 1.7e9, 'shares_outstanding': 382e6, 'cash': 6.5e9, 'long_term_debt_tagged': 6.2e9}


def later_fb(as_of='2028-02-20', fy26=FY26_VALUES, fy27_values=None, avail27='2028-01-25', qualitative=(), capital_structure=None, price=None):
    fb = deepcopy(ADBE)
    fb['as_of'] = as_of
    F.add_source(fb, 'synthetic:test', 'synthetic', 'test actuals', 'synthetic', as_of, note='synthetic test data, not Adobe results')
    for end, avail, vals in ((FY26, '2027-01-20', fy26), (FY27, avail27, fy27_values)):
        for m, v in (vals or {}).items():
            F.add_fact(fb, m, ('I@' + end) if m in INSTANT else 'FY' + end[:4], end, v, 'synthetic:test', 'as_reported', avail)
    for q in qualitative:
        F.add_qualitative(fb, *q)
    if capital_structure:
        fb['capital_structure'] = capital_structure
    if price:
        fb['market']['price'] = {'value': price, 'currency': 'USD', 'as_of': as_of, 'source_id': 'synthetic:test', 'kind': 'close'}
    else:
        fb['market'] = {}
    return fb


def thesis(**kw):
    return M.thesis_from_casefile(casefile(**kw), clock=Clock('2026-10-19T14:00:00+00:00'))


class ThesisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.th = thesis()

    def test_record_fields(self):
        th = self.th
        self.assertTrue(th['thesis_id'].startswith('ADBE:2026-10-19:'))
        self.assertEqual(th['created_at'], '2026-10-19T14:00:00+00:00')
        self.assertEqual(th['factbook_sha256'], F.fingerprint(ADBE))
        self.assertEqual(len(th['casefile_sha256']), 64)
        self.assertEqual(th['verdict'], 'attractive_at_price')
        self.assertEqual(th['price_at_analysis']['value'], 246.87)
        self.assertEqual(th['values']['5y']['date'], '2031-11-21')
        self.assertTrue(th['values']['five_year_reached'])
        t = th['values']['today']
        self.assertTrue(t['bear'] < 246.87 < t['bull'])
        json.dumps(th, allow_nan=False)

    def test_forecasts_per_fiscal_year(self):
        fy = [f for f in self.th['forecasts'] if f['period_kind'] == 'fiscal_year']
        self.assertEqual(sorted({f['period_end'] for f in fy}), ENDS[1:])          # stub FY2026 is not a fiscal-year forecast
        self.assertEqual({f['metric'] for f in fy}, {'revenue', 'ebit', 'operating_margin', 'shares_outstanding', 'fcf_after_sbc', 'fcf_after_sbc_margin'})
        self.assertEqual(self.th['skipped_periods'][0]['period_end'], FY26)
        for f in fy:
            self.assertTrue(f['low'] <= f['base'] <= f['high'])
            self.assertIn(f['unit'], ('USD', 'ratio', 'shares'))
            self.assertEqual(f['currency'], 'USD' if f['unit'] == 'USD' else None)
        rev = fc(self.th, 'revenue', FY27)
        self.assertEqual(rev['basis'], 'reported_revenue')
        self.assertAlmostEqual(rev['growth']['base'], (rev['base'] / F.value(ADBE, 'revenue', 'FY2025')) ** (1 / M._years('2025-11-28', FY27)) - 1)
        self.assertEqual(fc(self.th, 'shares_outstanding', FY27)['sided'], 'upper')
        ret = next(f for f in self.th['forecasts'] if f['metric'] == M.RETURN_METRIC)
        self.assertEqual(ret['period_end'], '2031-11-21')
        self.assertEqual(ret['price'], 246.87)

    def test_irr_uses_the_same_arithmetic_as_the_valuation(self):
        th = self.th
        today = th['values']['today']
        # at a price equal to the rolled-back value the IRR is the scenario cost of equity
        self.assertAlmostEqual(M._irr(th['irr_basis']['base'], today['base'], A), .10, places=6)
        self.assertGreater(th['expected_return']['base'], .10)                   # price below base value
        self.assertTrue(th['expected_return']['bear'] < th['expected_return']['base'] < th['expected_return']['bull'])
        self.assertIn('monitor', th['expected_return']['source'])

    def test_triggers_from_casefile(self):
        ids = {t['id'] for t in self.th['triggers']}
        for k in (f'revenue_below_bear@{FY27}', f'operating_margin_below_bear@{FY27}', f'fcf_margin_drop@{FY27}', 'dilution_above_assumption',
                  'net_debt_increase', 'revenue_ttm_growth_below_bear', 'guidance_change', 'financing_event', 'price_outside_value_band'):
            self.assertIn(k, ids)
        t = {t['id']: t for t in self.th['triggers']}
        self.assertEqual(t[f'revenue_below_bear@{FY27}']['threshold'], fc(self.th, 'revenue', FY27)['bear'])
        anchor = self.th['anchors']['fcf_after_sbc_margin']
        self.assertAlmostEqual(t[f'fcf_margin_drop@{FY27}']['threshold'], min(anchor - .05, fc(self.th, 'fcf_after_sbc_margin', FY27)['bear']))
        self.assertTrue(all(x.get('why') for x in self.th['triggers']))

    def test_assumptions_carry_falsifiers_and_observable_triggers(self):
        a = {x['id']: x for x in self.th['assumptions']}
        g = a['revenue_growth']['trigger']
        self.assertEqual((g['metric'], g['op']), ('revenue_growth', '<'))
        self.assertEqual([e for e, _ in g['threshold']], ENDS[1:])               # per-year list aligned to fiscal years
        self.assertEqual(a['cash_cost_ratio']['trigger']['op'], '>')
        self.assertEqual(a['tax_rate']['trigger'], {'metric': 'tax_rate', 'op': '>', 'threshold': .21, 'tolerance': .01, 'period_kind': 'fiscal_year'})
        self.assertIsNone(a['cost_of_equity']['trigger'])
        self.assertEqual(a['cost_of_equity']['monitor'], 'valuation_review')
        self.assertEqual(a['revenue_growth']['falsifier'], 'net new ARR turns negative for two quarters')
        self.assertTrue(any(x.startswith('revenue_below_bear') for x in a['revenue_growth']['linked_triggers']))

    def test_created_before_cutoff_rejected(self):
        with self.assertRaises(ValueError):
            M.thesis_from_casefile(casefile(), clock=Clock('2026-10-18T00:00:00+00:00'))

    def test_no_valuation_still_monitors_events_and_dilution(self):
        th = thesis(valuation=False)
        self.assertEqual(th['forecasts'], [])
        self.assertTrue(any('no executed valuation' in x for x in th['limitations']))
        ids = {t['id'] for t in th['triggers']}
        self.assertTrue({'guidance_change', 'financing_event', 'dilution_above_assumption'} <= ids)
        fb = later_fb(as_of='2027-03-01', qualitative=[('q1', 'convertible_notes', '$2B convertible notes due 2032 priced', 'reported',
                                                        ['synthetic:test'], '2027-02-15')])
        r = M.check(th, fb)
        self.assertEqual([a['code'] for a in r['alerts']], ['financing_event'])

    def test_loss_making_grower(self):
        s = spec('GROW', growth={'bear': [.15] * 5, 'base': [.35, .3, .25, .2, .15], 'bull': [.5, .4, .3, .25, .2]},
                 ccr={'bear': 1.05, 'base': .98, 'bull': .92}, payout=0.0, min_cash=0, cash=20e9)
        th = M.thesis_from_casefile(casefile(s, ticker='GROW', stage='growth'), clock=Clock('2026-10-20T00:00:00+00:00'))
        m = fc(th, 'operating_margin', FY27)
        self.assertLess(m['bear'], 0)
        self.assertIn(f'operating_margin_below_bear@{FY27}', {t['id'] for t in th['triggers']})
        json.dumps(th, allow_nan=False)


class CheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.th = thesis()

    def test_on_track_small_price_move_is_price_only(self):
        r = M.check(self.th, later_fb(fy27_values=fy27(self.th)), price={'value': 252.0, 'as_of': '2028-02-20'})
        self.assertEqual(r['alerts'], [])
        rev = next(c for c in r['changes'] if c['metric'] == 'revenue')
        self.assertEqual((rev['period'], rev['position']), (FY27, 'within'))
        self.assertTrue(r['impact']['price_only'])
        self.assertEqual(r['impact']['business_quality'], [])
        self.assertEqual(r['impact']['value'], [])
        self.assertIn(r['price']['irr_direction'], ('lower', 'higher', 'unchanged'))
        self.assertTrue(any('buybacks' in c for c in r['confirmations']))          # fewer shares than modeled is not a miss
        self.assertEqual(r['rereview_reasons'], [f"scheduled review date {self.th['review_by']} reached"])

    def test_small_price_moves_never_alert_before_review_date(self):
        fb = later_fb(as_of='2027-03-01')
        for p in (240.0, 255.0, 230.0):
            r = M.check(self.th, fb, price=p)
            self.assertEqual(r['alerts'], [], p)
            self.assertFalse(r['needs_rereview'])
            self.assertTrue(r['impact']['price_only'])

    def test_price_outside_band_alerts_as_expected_return(self):
        r = M.check(self.th, later_fb(as_of='2027-03-01'), price=150.0)
        self.assertEqual([a['code'] for a in r['alerts']], ['price_outside_value_band'])
        self.assertTrue(r['impact']['expected_return'])
        self.assertEqual(r['impact']['price_only'], [])
        self.assertEqual(r['price']['irr_direction'], 'higher')
        self.assertTrue(r['needs_rereview'])

    def test_business_miss_separated_from_price(self):
        v = fy27(self.th, rev_mult=.88, margin=.25, fcf_margin=.18)
        r = M.check(self.th, later_fb(fy27_values=v), price={'value': 235.0, 'as_of': '2028-02-20'})
        codes = {a['code'] for a in r['alerts']}
        self.assertTrue({'revenue_below_bear', 'operating_margin_below_bear', 'fcf_margin_drop'} <= codes)
        self.assertTrue(r['impact']['business_quality'])
        self.assertTrue(r['impact']['value'])
        self.assertEqual(r['impact']['price_only'], [])
        self.assertTrue(any('recompute' in x for x in r['impact']['expected_return']))
        margin = next(a for a in r['alerts'] if a['code'] == 'operating_margin_below_bear')
        self.assertIn('cash_cost_ratio', margin['assumption_ids'])                # assumption breach folded into the same alert
        rev = next(a for a in r['alerts'] if a['code'] == 'revenue_below_bear')
        self.assertIn('revenue_growth', rev['assumption_ids'])
        self.assertTrue(all(a['why_it_matters'] for a in r['alerts']))
        self.assertEqual(sum(a['code'] == 'assumption_breached' for a in r['alerts']), 0)

    def test_dilution_and_net_debt(self):
        v = fy27(self.th, shares_outstanding=420e6, cash=2e9, long_term_debt_tagged=20e9)
        r = M.check(self.th, later_fb(fy27_values=v))
        codes = {a['code'] for a in r['alerts']}
        self.assertTrue({'dilution_above_assumption', 'net_debt_increase'} <= codes)
        self.assertIsNone(r['price'])

    def test_interim_dilution_between_annual_reports(self):
        fb = later_fb(as_of='2027-06-01')
        F.add_fact(fb, 'shares_outstanding_cover', 'I@2027-05-20', '2027-05-20', 430e6, 'synthetic:test', 'as_reported', '2027-05-25')
        r = M.check(self.th, fb)
        self.assertEqual([a['code'] for a in r['alerts']], ['dilution_above_assumption'])

    def test_guidance_and_financing_events(self):
        q = [('g_old', 'guidance', 'FY26 outlook', 'guidance', ['synthetic:test'], '2026-09-10'),
             ('g1', 'guidance', 'FY27 revenue outlook lowered', 'guidance', ['synthetic:test'], '2027-03-15')]
        cs = {'as_of': '2027-04-01', 'cash': [], 'shares': [{'id': 'common', 'kind': 'common', 'count': 389e6}],
              'debt': [{'id': 'cvt2032', 'kind': 'convertible', 'principal': 3e9, 'issue_date': '2027-03-20', 'maturity': '2032-03-20'},
                       {'id': 'old_notes', 'kind': 'bond', 'principal': 6.2e9, 'issue_date': '2020-02-01', 'maturity': '2030-02-01'}]}
        r = M.check(self.th, later_fb(as_of='2027-04-01', qualitative=q, capital_structure=cs))
        codes = [a['code'] for a in r['alerts']]
        self.assertEqual(sorted(codes), ['financing_event', 'guidance_change'])
        g = next(a for a in r['alerts'] if a['code'] == 'guidance_change')
        self.assertEqual(g['evidence']['item_id'], 'g1')
        q2 = [('g2', 'guidance', 'FY27 outlook reaffirmed', 'guidance', ['synthetic:test'], '2027-03-15')]
        fb = later_fb(as_of='2027-04-01', qualitative=q2)
        fb['qualitative'][-1]['direction'] = 'reaffirmed'
        r = M.check(self.th, fb)
        self.assertEqual(r['alerts'], [])
        self.assertTrue(any('reaffirmed' in c for c in r['confirmations']))

    def test_no_lookahead(self):
        with self.assertRaises(ValueError):
            M.check(self.th, later_fb(as_of='2026-10-01'))
        fb = later_fb(as_of='2028-02-20', fy27_values=fy27(self.th, rev_mult=.8), avail27='2028-03-01')
        r = M.check(self.th, fb)
        self.assertFalse(any(c['period'] == FY27 for c in r['changes']))          # filed after the check date: invisible
        self.assertGreater(r['new_data']['ignored_not_yet_available'], 0)
        self.assertNotIn('revenue_below_bear', {a['code'] for a in r['alerts']})

    def test_other_company_or_currency_refused(self):
        fb = later_fb(as_of='2027-03-01')
        fb['company'] = dict(fb['company'], currency='EUR')
        with self.assertRaisesRegex(ValueError, 'currency'):
            M.check(self.th, fb)
        fb['company'] = dict(fb['company'], ticker='MSFT', currency='USD')
        with self.assertRaisesRegex(ValueError, 'another company'):
            M.check(self.th, fb)

    def test_analysis_overlay_used_for_resolved_values(self):
        v = fy27(self.th)
        an = {'annual': {FY27: {'debt': 25e9, 'cash': 1e9, 'investments': 0, 'shares_outstanding': 376e6}}}
        r = M.check(self.th, later_fb(fy27_values=v), new_an=an)
        self.assertIn('net_debt_increase', {a['code'] for a in r['alerts']})


def ledger(clock):
    return Ledger(os.path.join(tempfile.mkdtemp(), 'ledger.jsonl'), clock=clock)


class FreezeTests(unittest.TestCase):
    def test_freeze_writes_with_system_time_and_is_idempotent(self):
        th = thesis()
        clock = Clock('2026-10-19T15:00:00+00:00')
        L = ledger(clock)
        r = M.freeze(L, th)
        n = len(th['forecasts'])
        self.assertEqual(len(r['written']), n + 1)
        self.assertEqual(r['rejected'], [])
        self.assertTrue(r['ledger']['ok'])
        rows = L.lines()
        self.assertEqual(rows[0]['type'], 'thesis')
        self.assertTrue(all(x['recorded_at'] == '2026-10-19T15:00:00+00:00' for x in rows))
        self.assertTrue(all(x['made_at'] == th['created_at'] for x in rows))
        rev = next(x for x in rows if x.get('metric') == 'revenue')
        self.assertEqual(rev['thesis_breakers'], ['net new ARR turns negative for two quarters'])
        clock.t = '2026-10-20T15:00:00+00:00'
        again = M.freeze(L, th)
        self.assertEqual(again['written'], [])
        self.assertEqual(len(again['skipped']), n + 1)
        self.assertEqual(len(L.lines()), n + 1)

    def test_freeze_after_a_period_ended_refuses_those_forecasts(self):
        th = thesis()
        L = ledger(Clock('2027-12-15T00:00:00+00:00'))
        r = M.freeze(L, th)
        rej = {x['id'].split(':')[-1] for x in r['rejected']}
        self.assertEqual(rej, {FY27})
        self.assertTrue(all('cannot forecast the past' in x['reason'] for x in r['rejected']))
        self.assertTrue(any(i.endswith(FY28) for i in r['written']))

    def test_revision_links_to_original(self):
        clock = Clock('2026-10-19T15:00:00+00:00')
        L = ledger(clock)
        th1 = thesis()
        M.freeze(L, th1)
        clock.t = '2026-12-02T10:00:00+00:00'
        s2 = spec(as_of='2026-12-01')                       # after FY2026 closed: no stub, five full years
        s2['boundaries'] = ENDS[1:]
        s2['report_dates'] = ['2026-12-01'] + ENDS[1:]
        s2.pop('stub_annual_revenue_rate')
        th2 = M.thesis_from_casefile(casefile(s2), clock=Clock('2026-12-02T09:00:00+00:00'))
        r = M.freeze(L, th2)
        self.assertEqual(r['rejected'], [])
        rows = {x['id']: x for x in L.lines() if x.get('type') == 'forecast'}
        new = rows[f"{th2['thesis_id']}:revenue:{FY27}"]
        self.assertEqual(new['supersedes'], f"{th1['thesis_id']}:revenue:{FY27}")
        self.assertEqual(new['context']['vintage'], 'revision')
        self.assertIn(f"{th1['thesis_id']}:revenue:{FY27}", rows)          # the original stays and is still scored


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock('2026-10-19T15:00:00+00:00')
        self.L = ledger(self.clock)
        self.th = {t: thesis(ticker=t) for t in ('ADBE', 'SHOK', 'VALU', 'OKAY')}
        for th in self.th.values():
            M.freeze(self.L, th)

    def out(self, ticker, metric, actual, observed='2028-01-25'):
        th = self.th[ticker]
        f = fc(th, metric, FY27)
        self.L.outcome(forecast_id=f['id'], actual=actual, source_id='10k_fy2027_synthetic', unit=f['unit'], currency=f['currency'],
                       basis=f['basis'], observed_at=observed)

    def test_attribution_cases(self):
        self.clock.t = '2027-03-01T00:00:00+00:00'
        # a shock dated BEFORE the forecast was made cannot excuse a miss; one after it (with evidence) can
        self.L.event(id='old', entity='ADBE', kind='external_shock', occurred_at='2026-09-01', description='known before analysis',
                     evidence_ids=['news_2026-09-01'], why_unforeseeable='test', affects=['revenue'])
        self.L.event(id='ban', entity='SHOK', kind='external_shock', occurred_at='2027-02-10', description='export ban on core product',
                     evidence_ids=['8k_2027-02-11'], why_unforeseeable='rule issued without consultation', affects=['revenue'])
        self.clock.t = '2028-02-01T00:00:00+00:00'
        for t in ('ADBE', 'SHOK'):
            self.out(t, 'revenue', fc(self.th[t], 'revenue', FY27)['bear'] * .9)
        for t in ('VALU', 'OKAY'):
            self.out(t, 'revenue', fc(self.th[t], 'revenue', FY27)['base'])
            self.out(t, 'operating_margin', fc(self.th[t], 'operating_margin', FY27)['base'] - .005)
        prices = [{'entity': 'VALU', 'date': '2028-02-01', 'price': 110.0}, {'entity': 'OKAY', 'date': '2028-02-01', 'price': 280.0}]
        s = M.score(self.L, {'prices': prices})
        bt = {v['entity']: v for v in s['by_thesis'].values()}
        self.assertEqual(bt['ADBE']['attribution'], 'analysis_error')
        self.assertEqual(bt['SHOK']['attribution'], 'external_shock')
        self.assertEqual(bt['VALU']['attribution'], 'valuation_error')
        self.assertTrue(bt['VALU']['provisional'])
        self.assertEqual(bt['OKAY']['attribution'], 'on_track')
        shock = next(i for i in s['items'] if i['entity'] == 'SHOK')
        self.assertEqual(shock['external_events'], ['ban'])
        self.assertEqual(shock['events_recorded_after_outcome'], [])          # recorded 2027-03-01, before the 2028-01-25 result
        m = next(i for i in s['items'] if i['entity'] == 'OKAY' and i['metric'] == 'operating_margin')
        self.assertEqual(m['error_kind'], 'pp')
        self.assertAlmostEqual(m['error'], -.005)
        self.assertTrue(all(i['recorded_before_outcome'] and i['eligible_for_learning'] for i in s['items']))

    def test_factbook_actuals_and_definition_guard(self):
        th = self.th['ADBE']
        fb = later_fb(fy27_values=fy27(th))
        s = M.score(self.L, {'factbooks': [fb]})
        mine = [i for i in s['items'] if i['entity'] == 'ADBE']
        self.assertEqual({i['metric'] for i in mine}, {'revenue', 'ebit', 'operating_margin', 'shares_outstanding', 'fcf_after_sbc', 'fcf_after_sbc_margin'})
        self.assertTrue(all(i['outcome_source'] == 'factbook' and i['attribution'] == 'within_range' for i in mine))
        eur = deepcopy(fb); eur['company'] = dict(eur['company'], currency='EUR')
        s2 = M.score(self.L, eur)
        self.assertTrue(any('currency differs' in x['reason'] for x in s2['not_scored']))
        self.assertTrue(any(u['entity'] == 'SHOK' for u in s['unresolved']))

    def test_external_shock_never_inferred(self):
        self.clock.t = '2028-02-01T00:00:00+00:00'
        self.out('SHOK', 'revenue', 1e9)                     # catastrophic miss, but no event on file
        s = M.score(self.L)
        self.assertEqual(next(i for i in s['items'] if i['entity'] == 'SHOK')['attribution'], 'analysis_error')


class PipelineIntegrationTests(unittest.TestCase):
    """The real casefile from pipeline.run on the ADBE fixture (later fiscal years and prices are synthetic)."""
    @classmethod
    def setUpClass(cls):
        try:
            from fundamental_engine import pipeline
            cls.cf = pipeline.run(deepcopy(ADBE))
        except Exception as e:                                  # stages are owned by other modules
            raise unittest.SkipTest(f'pipeline unavailable: {e}')
        if (cls.cf.get('val') or {}).get('result') is None:
            raise unittest.SkipTest('pipeline produced no executed valuation: ' + json.dumps(cls.cf.get('errors'))[:200])
        cls.th = M.thesis_from_casefile(cls.cf, clock=Clock('2026-10-19T20:00:00+00:00'))

    def test_thesis_from_real_casefile(self):
        th = self.th
        self.assertEqual(th['casefile_sha256'], self.cf['casefile_sha256'])
        self.assertEqual(th['factbook_sha256'], F.fingerprint(ADBE))
        self.assertEqual(th['price_at_analysis']['value'], 246.87)
        fy = [f for f in th['forecasts'] if f['period_kind'] == 'fiscal_year']
        self.assertTrue(fy and all(f['low'] <= f['base'] <= f['high'] for f in fy))
        self.assertTrue(all(f['period_end'] > A for f in fy))
        trig = {a['id']: a['trigger'] for a in th['assumptions']}
        self.assertTrue(any(t and t['metric'] == 'revenue_growth' for t in trig.values()))     # forecast.py per-scenario rows merged
        json.dumps(th, allow_nan=False)

    def test_on_track_year_is_quiet_and_freezes(self):
        th = self.th
        e = min(f['period_end'] for f in th['forecasts'] if f['period_kind'] == 'fiscal_year')
        g = lambda m: next(f for f in th['forecasts'] if f['metric'] == m and f['period_end'] == e)['base']
        rev = g('revenue') * 1.01
        fm = g('fcf_after_sbc_margin')
        v = {'revenue': rev, 'operating_income': g('operating_margin') * rev, 'sbc': .08 * rev, 'capex': .02 * rev, 'd_and_a': .03 * rev,
             'cfo': (fm + .10) * rev, 'shares_outstanding': 375e6, 'cash': 7e9, 'long_term_debt_tagged': 6.2e9}
        fb = later_fb(fy27_values=v)
        r = M.check(th, fb, price={'value': 255.0, 'as_of': '2028-02-20'})
        self.assertEqual(r['alerts'], [], [a['message'] for a in r['alerts']])
        self.assertTrue(r['impact']['price_only'])
        L = ledger(Clock('2026-10-19T21:00:00+00:00'))
        out = M.freeze(L, th)
        self.assertEqual(out['rejected'], [])
        self.assertTrue(out['ledger']['ok'])


def _legacy_rows():
    rows, prev = [], None
    for body in ({'type': 'forecast', 'id': 'legacy-1', 'entity': 'OLD', 'made_at': '2025-01-10', 'metric': 'revenue',
                  'period_end': '2025-12-31', 'scenarios': {'bear': 90, 'base': 100, 'bull': 110}, 'price': None, 'benchmark': None,
                  'model_version': '0.10.0', 'inputs_hash': None, 'thesis_breakers': [], 'note': ''},
                 {'type': 'outcome', 'forecast_id': 'legacy-1', 'recorded_at': '2026-02-20', 'actual': 80.0, 'source_id': '10k',
                  'benchmark_return': None, 'price_now': None}):
        row = dict(body, prev=prev); row['hash'] = _h(row); prev = row['hash']; rows.append(row)
    return rows


class RecurringErrorTests(unittest.TestCase):
    def build(self, stage_errors, legacy=True):
        path = os.path.join(tempfile.mkdtemp(), 'ledger.jsonl')
        if legacy:
            with open(path, 'w') as f:
                f.write(''.join(json.dumps(r) + '\n' for r in _legacy_rows()))
        clock = Clock('2026-10-19T12:00:00+00:00')
        L = Ledger(path, clock=clock)
        k = 0
        for stage, errs in stage_errors.items():
            for e in errs:
                k += 1
                L.forecast(id=f'f{k}', entity=f'C{k}', made_at='2026-10-19', metric='revenue', period_end='2027-06-30',
                           scenarios={'bear': 80, 'base': 100, 'bull': 120}, unit='USD', currency='USD', basis='reported_revenue',
                           model_version='t', context={'stage': stage, 'business_model': 'saas', 'horizon_years': .7})
        clock.t = '2027-09-01T00:00:00+00:00'
        k = 0
        for stage, errs in stage_errors.items():
            for e in errs:
                k += 1
                L.outcome(forecast_id=f'f{k}', actual=100 * (1 + e), source_id='s', unit='USD', currency='USD', basis='reported_revenue',
                          observed_at='2027-08-15')
        return L

    def test_systematic_growth_overforecast_by_stage(self):
        L = self.build({'growth': [-.10, -.12, -.08, -.15, -.09, -.11, -.30], 'mature': [.02, -.03, .01, -.02, .03, -.01]})
        r = M.score(L)['recurring_errors']
        g = {(x['group'].get('stage'), len(x['group'])): x for x in r['groups'] if x['group'].get('metric') == 'revenue'}
        self.assertEqual(g[('growth', 2)]['status'], 'recurring_over_forecast')
        self.assertIn('do not apply a mechanical haircut', g[('growth', 2)]['review_hint'])
        self.assertEqual(g[('mature', 2)]['status'], 'no_pattern')
        self.assertEqual(r['excluded'].get('legacy'), 1)                      # legacy timing unverified: excluded
        self.assertEqual(r['eligible'], 13)
        self.assertTrue(all('return' not in str(x['group']) for x in r['groups']))

    def test_too_few_companies_is_not_a_pattern(self):
        L = self.build({'growth': [-.2, -.2]}, legacy=False)
        r = M.score(L)['recurring_errors']
        self.assertEqual(r['recurring'], [])
        self.assertTrue(all(x['status'] == 'insufficient_sample' for x in r['groups']))

    def test_revisions_and_overdue_forecasts(self):
        path = os.path.join(tempfile.mkdtemp(), 'ledger.jsonl')
        clock = Clock('2026-10-19T12:00:00+00:00')
        L = Ledger(path, clock=clock)
        for i in range(4):
            L.forecast(id=f'o{i}', entity=f'D{i}', made_at='2026-10-19', metric='revenue', period_end='2026-12-31',
                       scenarios={'bear': 80, 'base': 100, 'bull': 120}, unit='USD', currency='USD', basis='reported_revenue', model_version='t')
        clock.t = '2026-11-01T12:00:00+00:00'
        L.forecast(id='r0', entity='D0', made_at='2026-11-01', metric='revenue', period_end='2026-12-31', supersedes='o0',
                   scenarios={'bear': 85, 'base': 95, 'bull': 105}, unit='USD', currency='USD', basis='reported_revenue', model_version='t')
        clock.t = '2027-03-01T12:00:00+00:00'
        for fid in ('o0', 'r0'):
            L.outcome(forecast_id=fid, actual=96, source_id='s', unit='USD', currency='USD', basis='reported_revenue', observed_at='2027-02-15')
        s = M.score(L, options={'as_of': '2027-12-31'})
        rev = next(i for i in s['items'] if i['forecast_id'] == 'r0')
        self.assertEqual(rev['learning_exclusion'], 'revision')
        self.assertTrue(next(i for i in s['items'] if i['forecast_id'] == 'o0')['eligible_for_learning'])
        self.assertEqual(sum(u['overdue'] for u in s['unresolved']), 3)
        self.assertTrue(s['recurring_errors']['warnings'])


if __name__ == '__main__':
    unittest.main()
