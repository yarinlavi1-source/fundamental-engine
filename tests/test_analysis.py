"""analysis.analyze: financial-analysis stage on the real ADBE FactBook and on synthetic, clearly labelled companies.

Synthetic FactBooks below are invented numbers built only to exercise company types the fixture does not cover
(loss-making grower, acquirer, split, missing statements, quarterly inflection); they are not real issuers.
"""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from fundamental_engine import facts as F
from fundamental_engine.analysis import analyze, STATUS_ORDER
from fundamental_engine.history import history as check_rows

ROOT = Path(__file__).resolve().parents[1]
ADBE = json.loads((ROOT / 'tests/fixtures/adbe_factbook_2026-10-19.json').read_text())


def adbe_qa(**over):
    """Hand-built dataqa result shaped like the contract (dataqa.py is written separately)."""
    qa = {'status': 'limited',
          'fields': {'shares_outstanding': {'field': 'shares_outstanding', 'status': 'single_source', 'as_of': '2026-08-28',
                                            'sources': ['fmp:shares_float']},
                     'revenue_ttm': {'field': 'revenue_ttm', 'status': 'reported', 'as_of': '2025-11-28'},
                     'debt': {'field': 'debt', 'status': 'reported', 'as_of': '2025-11-28'},
                     'cash_and_liquid_investments': {'field': 'cash_and_liquid_investments', 'status': 'reported', 'as_of': '2025-11-28'}},
          'resolved': {'shares_outstanding': {'value': 389.2e6, 'as_of': '2026-08-28', 'basis': 'vendor_float'},
                       'cash': {'value': 5431e6, 'as_of': '2025-11-28'},
                       'short_term_investments': {'value': 1164e6, 'as_of': '2025-11-28'},
                       'debt': {'value': 6210e6, 'as_of': '2025-11-28', 'method': 'tagged_total'},
                       'net_cash': {'value': 385e6, 'as_of': '2025-11-28'},
                       'deferred_revenue': {'value': 7030e6, 'as_of': '2025-11-28'}},
          'blocking': [], 'limited': ['shares_outstanding']}
    for k, v in over.items():
        qa['fields'][k] = v
    return qa


def synth(annual, quarters=None, as_of='2026-10-19', ticker='SYN'):
    """SYNTHETIC FactBook: {year: {metric: value}} with Dec-31 fiscal years filed mid-February."""
    fb = F.new({'ticker': ticker, 'name': 'Synthetic test company (not real)', 'currency': 'USD'}, as_of)
    F.add_source(fb, 'syn', 'synthetic', 'synthetic statements for tests', 'synthetic', '2026-10-19T00:00:00Z')
    for y, row in annual.items():
        end, avail = f'{y}-12-31', f'{y + 1}-02-15'
        for m, v in row.items():
            period = 'I@' + end if m in F.INSTANT_TAGS else f'FY{y}'
            F.add_fact(fb, m, period, end, v, 'syn', 'as_reported', avail)
    for (label, end), row in (quarters or {}).items():
        for m, v in row.items():
            F.add_fact(fb, m, label, end, v, 'syn', 'as_reported', _plus(end, 40))
    return fb


def _plus(end, days):
    from datetime import date, timedelta
    return (date.fromisoformat(end) + timedelta(days=days)).isoformat()


def flags(an):
    return {f['code']: f for f in an['flags']}


class AdobeRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.an = analyze(ADBE, adbe_qa(), archetype='software')

    def test_annual_table_matches_10k(self):
        a = self.an['annual']['2025-11-28']
        self.assertEqual(self.an['years'][0], '2021-12-03')
        self.assertEqual(len(self.an['years']), 5)
        self.assertEqual(a['revenue'], 23.769e9)
        self.assertEqual(a['fcf'], 9.852e9)
        self.assertEqual(a['fcf_after_sbc'], 9.852e9 - 1.942e9)
        self.assertEqual(a['investments'], 1.164e9)                  # STI only; no LTI line -> zero, listed in assumptions
        self.assertEqual(a['deferred_revenue'], 7.030e9)
        self.assertEqual(a['invested_capital'], 11623e6 + 6210e6 - 5431e6 - 1164e6)
        self.assertEqual(a['nwc'], 10163e6 - 5431e6 - 1164e6 - 10200e6)
        self.assertEqual(a['invested_capital_asset_side'], a['nwc'] + 1873e6 + 12857e6 + 495e6)
        self.assertEqual(a['dividends'], 0.0)
        for pe, row in self.an['annual'].items():
            for k in ('revenue', 'operating_income', 'cfo', 'capex', 'fcf', 'sbc', 'shares_outstanding', 'debt', 'invested_capital', 'nwc'):
                self.assertIsNotNone(row[k], (pe, k))

    def test_debt_scope_follows_roll_forward(self):
        a = self.an['annual']
        self.assertEqual((a['2022-12-02']['debt'], a['2022-12-02']['debt_method']), (4129e6, 'tagged_plus_current'))
        self.assertEqual((a['2023-12-01']['debt'], a['2023-12-01']['debt_method']), (3634e6, 'tagged_total'))
        self.assertEqual((a['2024-11-29']['debt'], a['2024-11-29']['debt_method']), (5629e6, 'tagged_plus_current'))
        self.assertEqual((a['2025-11-28']['debt'], a['2025-11-28']['debt_method']), (6210e6, 'tagged_total'))
        scope = [x for x in self.an['assumptions'] if x['code'] == 'debt_scope']
        self.assertEqual({x['period_end'] for x in scope}, {'2022-12-02', '2024-11-29'})
        self.assertEqual(scope[-1]['roll_forward_expected'], 3634e6 + 1997e6 - 0)

    def test_key_metrics(self):
        m = self.an['metrics']
        self.assertAlmostEqual(m['operating_margin']['value'], 8706 / 23769, places=6)
        self.assertAlmostEqual(m['operating_margin']['value'], .366, places=3)
        self.assertAlmostEqual(m['sbc_pct_revenue']['value'], .0817, places=4)
        self.assertAlmostEqual(m['revenue_cagr_3y']['value'], (23769 / 17606) ** (1 / 3) - 1, places=9)
        self.assertAlmostEqual(m['revenue_cagr_3y']['value'], .105, places=3)
        self.assertEqual(m['revenue_cagr_3y']['period'], 'FY2022..FY2025')
        self.assertIsNone(m['revenue_cagr_5y']['value'])
        self.assertIn('5 years', m['revenue_cagr_5y']['reason'])
        self.assertAlmostEqual(m['fcf_margin']['value'], 9852 / 23769, places=6)
        self.assertAlmostEqual(m['operating_margin_trend_3y']['value'], 8706 / 23769 - 6098 / 17606, places=9)
        self.assertEqual(len(m['revenue_growth_series']['value']), 4)
        self.assertLess(m['growth_volatility']['value'], .01)
        self.assertAlmostEqual(m['capex_to_da']['value'], 179 / 818, places=6)
        self.assertAlmostEqual(m['cfo_to_net_income']['value'], 10031 / 7130, places=6)
        self.assertAlmostEqual(m['accruals_ratio']['value'], (7130 - 10031) / ((30230 + 29496) / 2), places=9)
        self.assertAlmostEqual(m['interest_coverage']['value'], 8706 / 263, places=6)
        self.assertAlmostEqual(m['buybacks_pct_fcf_3y']['value'], (4400 + 9500 + 11281) / (6942 + 7873 + 9852), places=9)

    def test_share_count_uses_point_in_time_counts(self):
        m = self.an['metrics']
        s = m['share_count_cagr_3y']
        self.assertAlmostEqual(s['value'], (413 / 462) ** (1 / 3) - 1, places=9)
        self.assertTrue(-.045 < s['value'] < -.025)
        self.assertEqual([i['metric'] for i in s['inputs']], ['shares_outstanding'] * 4)
        self.assertNotIn('dilution', flags(self.an))
        o = m['sbc_offset_by_buybacks']
        self.assertAlmostEqual(o['value'], (4400 + 9500 + 11281) / (1718 + 1833 + 1942), places=9)
        self.assertLess(o['net_dilution_after_buybacks_3y'], 0)
        self.assertIn('share count fell', o['reading'])

    def test_roic_and_shrinking_invested_capital(self):
        m = self.an['metrics']
        nopat = 8706e6 * (1 - 1604 / 8734)
        self.assertAlmostEqual(m['roic']['nopat'], nopat, places=0)
        self.assertAlmostEqual(m['roic']['value'], nopat / ((11848e6 + 11238e6) / 2), places=9)
        self.assertEqual(m['roic']['basis'], 'financing_side')
        self.assertIsNotNone(m['roic']['roic_asset_side'])
        series = self.an['forensics']['roic']['series']               # one ROIC computation: scores.roic on the same definition
        self.assertAlmostEqual(series[-1]['roic'], m['roic']['value'], places=12)
        self.assertAlmostEqual(series[-1]['nopat'], m['roic']['nopat'], places=3)
        for name in ('incremental_roic_3y', 'sales_to_capital_incremental_3y'):
            self.assertIsNone(m[name]['value'])
            self.assertIn('invested capital fell', m[name]['reason'])
            self.assertEqual(m[name]['status'], 'reported')
            self.assertLess(m[name]['delta_invested_capital'], 0)
        self.assertIsNone(m['roic_ex_goodwill']['value'])
        self.assertIn('not positive', m['roic_ex_goodwill']['reason'])

    def test_balance_from_dataqa_and_market_cap(self):
        m = self.an['metrics']
        self.assertEqual(m['net_cash']['value'], 385e6)
        self.assertEqual(m['net_cash']['detail']['cash'], 5431e6)            # spendable cash shown apart from investments
        self.assertEqual(m['net_cash']['detail']['short_term_investments'], 1164e6)
        self.assertLess(m['net_debt_to_ebitda']['value'], 0)
        self.assertEqual(self.an['ttm']['shares_outstanding'], 389.2e6)
        self.assertEqual(self.an['ttm']['balance_as_of']['shares_outstanding'], '2026-08-28')
        self.assertEqual(self.an['ttm']['method'], 'latest_fiscal_year')
        self.assertAlmostEqual(self.an['market_cap']['value'], 246.87 * 389.2e6, places=0)
        self.assertIn('dataqa-resolved shares', self.an['market_cap']['basis'])
        self.assertFalse([k for k, v in self.an['forensics'].items() if isinstance(v, dict) and 'status' not in v])

    def test_forensics_reuse_scores(self):
        fz = self.an['forensics']
        self.assertEqual(fz['status'], 'computed')
        self.assertEqual(fz['piotroski']['status'], 'computed')
        self.assertEqual(fz['beneish']['status'], 'computed')
        self.assertEqual(fz['rule_of_40']['status'], 'computed')
        self.assertEqual(fz['altman']['status'], 'computed')           # retained earnings mapped since facts v0.11
        self.assertNotEqual(fz['altman'].get('zone'), 'distress')
        rows = self.an['history_rows']
        self.assertEqual(len(rows), 5)
        check_rows({'history': rows, 'as_of': ADBE['as_of']})
        self.assertEqual(rows[-1]['cash'], 5431e6)                      # history rows keep cash and investments apart
        self.assertTrue(all(r['source_ids'] for r in rows))

    def test_contract_shape_status_and_json(self):
        json.dumps(self.an, allow_nan=False)
        for name in ('revenue_cagr_3y', 'revenue_cagr_5y', 'revenue_growth_last', 'revenue_growth_series', 'growth_volatility',
                     'acquisition_spend_pct_revenue_3y', 'gross_margin', 'gross_margin_trend_3y', 'operating_margin',
                     'operating_margin_trend_3y', 'operating_margin_best_3y', 'fcf_margin', 'fcf_after_sbc_margin',
                     'sbc_pct_revenue', 'cfo_to_net_income', 'accruals_ratio', 'capex_pct_revenue', 'capex_to_da', 'roic',
                     'roic_ex_goodwill', 'incremental_roic_3y', 'sales_to_capital_incremental_3y', 'nwc_pct_revenue',
                     'deferred_revenue_pct_revenue', 'net_cash', 'net_debt_to_ebitda', 'interest_coverage',
                     'share_count_cagr_3y', 'buybacks_pct_fcf_3y', 'sbc_offset_by_buybacks'):
            m = self.an['metrics'][name]
            for k in ('value', 'unit', 'formula', 'inputs', 'period', 'status'):
                self.assertIn(k, m, (name, k))
            self.assertIn(m['status'], STATUS_ORDER, name)
            self.assertTrue(m['formula'], name)
            if m['value'] is None:
                self.assertTrue(m['reason'], name)
            else:
                self.assertTrue(m['inputs'], name)
                for ref in m['inputs']:
                    self.assertIn('source_id', ref)
        self.assertEqual(self.an['metrics']['revenue_cagr_3y']['status'], 'reported')
        self.assertEqual(self.an['metrics']['quarterly']['status'], 'unavailable')
        self.assertIn('quarters', self.an['metrics']['quarterly']['reason'])

    def test_flags_are_observations(self):
        f = flags(self.an)
        self.assertEqual(f['deferred_revenue_funded']['kind'], 'positive')
        self.assertEqual(f['deferred_revenue_funded']['severity'], 'low')
        for code in ('negative_fcf', 'sbc_heavy', 'dilution', 'leverage_high', 'margin_compression', 'cash_conversion_weak',
                     'acquisition_affected_growth', 'possible_split'):
            self.assertNotIn(code, f)
        acq = self.an['acquisition_years']                             # FY2021 Workfront/Frame.io spend, but no growth step in table
        self.assertEqual([a['fiscal_period'] for a in acq], ['FY2021'])
        self.assertFalse(acq[0]['growth_step_in_table'])
        self.assertEqual(self.an['metrics']['revenue_cagr_3y']['detail']['excluded_acquisition_years'], [])

    def test_no_look_ahead(self):
        fb = deepcopy(ADBE)
        fb['as_of'] = '2025-06-01'                                     # before the FY2025 10-K (estimated availability 2026-02-11)
        qa = {'resolved': {'shares_outstanding': {'value': 441e6, 'as_of': '2024-11-29', 'basis': 'balance_sheet'}}}
        an = analyze(fb, qa)
        self.assertEqual(an['years'][-1], '2024-11-29')
        self.assertNotIn('2025-11-28', an['annual'])
        self.assertAlmostEqual(an['metrics']['revenue_cagr_3y']['value'], (21505 / 15785) ** (1 / 3) - 1, places=9)
        self.assertNotIn('2025', json.dumps(an['metrics']['revenue_cagr_3y']['inputs']))
        self.assertIsNone(an['market_cap']['value'])                   # the 2026 quote is never used for a 2025 cutoff
        self.assertEqual(an['metrics']['net_cash']['basis'], 'latest_fiscal_year')
        self.assertEqual(an['metrics']['net_cash']['value'], 7613e6 + 273e6 - 5629e6)

    def test_qa_field_status_propagates(self):
        qa = adbe_qa(revenue_ttm={'field': 'revenue_ttm', 'status': 'conflict', 'as_of': '2025-11-28'},
                     cfo_ttm={'field': 'cfo_ttm', 'status': 'verified', 'as_of': '2025-11-28'})
        m = analyze(ADBE, qa)['metrics']
        self.assertEqual(m['operating_margin']['status'], 'conflict')
        self.assertEqual(m['revenue_cagr_3y']['status'], 'conflict')
        self.assertEqual(m['capex_to_da']['status'], 'reported')        # revenue not an input
        self.assertEqual(m['incremental_roic_3y']['status'], 'reported')
        self.assertEqual(m['sales_to_capital_incremental_3y']['status'], 'conflict')
        self.assertEqual(m['cfo_to_net_income']['status'], 'reported')  # verified cfo, reported net income -> weakest
        self.assertEqual(m['net_cash']['status'], 'reported')
        old = analyze(ADBE, qa)['metrics']['revenue_growth_series']
        self.assertEqual(old['status'], 'conflict')                     # the series includes the conflicted year
        qa2 = adbe_qa(shares_outstanding={'field': 'shares_outstanding', 'status': 'stale'})   # no as_of: latest year only
        s = analyze(ADBE, qa2)['metrics']['share_count_cagr_3y']
        self.assertEqual(s['status'], 'stale')


    def test_missing_status_from_an_input_does_not_crash(self):
        # a dataqa 'missing' status on an input leaves a value without a reason (Ferguson 2026-10: KeyError 'reason')
        m = analyze(ADBE, adbe_qa(revenue_ttm={'field': 'revenue_ttm', 'status': 'missing', 'as_of': '2025-11-28'}))['metrics']
        self.assertEqual(m['operating_margin_trend_3y']['status'], 'missing')
        self.assertIn('operating_margin', m['operating_margin_trend_3y']['reason'])

    def test_net_cash_refused_by_dataqa_is_not_rederived(self):
        qa = adbe_qa(debt={'field': 'debt', 'status': 'conflict', 'as_of': '2025-11-28'})
        qa['resolved']['net_cash'] = {'value': None, 'as_of': None, 'status': 'conflict'}
        m = analyze(ADBE, qa)['metrics']
        self.assertIsNone(m['net_cash']['value'])
        self.assertEqual(m['net_cash']['status'], 'conflict')
        self.assertIsNone(m['net_debt_to_ebitda']['value'])
        self.assertEqual(m['roic']['status'], 'conflict')                # FY2025 debt feeds invested capital


try:
    from fundamental_engine import dataqa as _dataqa
except ImportError:                                                     # dataqa.py is built in parallel
    _dataqa = None


@unittest.skipUnless(_dataqa, 'dataqa.py not present')
class WithRealDataqa(unittest.TestCase):
    def test_contract_compatibility_on_adbe(self):
        qa = _dataqa.validate(ADBE)
        an = analyze(ADBE, qa, archetype='software')
        json.dumps(an, allow_nan=False)
        m = an['metrics']
        self.assertAlmostEqual(m['revenue_cagr_3y']['value'], .105, places=3)
        res = qa['resolved']
        if (res.get('net_cash') or {}).get('value') is not None:
            self.assertEqual(m['net_cash']['value'], res['net_cash']['value'])
        if (res.get('shares_outstanding') or {}).get('value'):
            self.assertAlmostEqual(an['market_cap']['value'], 246.87 * res['shares_outstanding']['value'], places=0)
        for name, x in m.items():
            self.assertIn(x['status'], STATUS_ORDER + ('unavailable',), name)


# ------------------------------------------------------------------ synthetic company types (invented numbers)

def grower():
    """SYNTHETIC loss-making grower: revenue +50%/yr, operating losses, negative FCF, SBC ~20% of revenue, 10%/yr dilution."""
    rev = [100e6, 150e6, 225e6, 337.5e6]
    rows = {}
    for i, y in enumerate(range(2022, 2026)):
        rows[y] = dict(revenue=rev[i], gross_profit=rev[i] * .7, operating_income=-60e6 + 10e6 * i, net_income=-55e6 + 10e6 * i,
                       pretax_income=-55e6 + 10e6 * i, income_tax=0.0, cfo=-40e6 + 5e6 * i, capex=10e6 + 5e6 * i,
                       sbc=rev[i] * .2, d_and_a=5e6 + 2e6 * i, shares_diluted_wavg=95e6 * 1.1 ** i,
                       shares_outstanding=100e6 * 1.1 ** i, cash=400e6 - 60e6 * i, equity=420e6 - 50e6 * i,
                       total_assets=500e6 - 40e6 * i + 20e6 * i, current_assets=420e6 - 50e6 * i, current_liabilities=60e6 + 10e6 * i,
                       total_liabilities=80e6 + 10e6 * i, ppe=40e6 + 10e6 * i)
    return synth(rows)


class LossMakingGrower(unittest.TestCase):
    def test_flags_and_meaningless_ratios(self):
        an = analyze(grower(), None, archetype='software')
        m, f = an['metrics'], flags(an)
        self.assertAlmostEqual(m['revenue_cagr_3y']['value'], .5, places=9)
        self.assertLess(m['fcf_margin']['value'], 0)
        self.assertAlmostEqual(m['sbc_pct_revenue']['value'], .2, places=9)
        self.assertAlmostEqual(m['share_count_cagr_3y']['value'], .1, places=9)
        self.assertIsNone(m['cfo_to_net_income']['value'])
        self.assertIn('net income not positive', m['cfo_to_net_income']['reason'])
        self.assertLess(m['roic']['value'], 0)
        self.assertIsNone(m['incremental_roic_3y']['value'])            # losses at both ends: no "return" to report
        self.assertIn('loss reduction', m['incremental_roic_3y']['reason'])
        self.assertGreater(m['sales_to_capital_incremental_3y']['value'], 0)
        self.assertIsNone(m['buybacks_pct_fcf_3y']['value'])            # three years of negative FCF
        self.assertIn('not positive', m['buybacks_pct_fcf_3y']['reason'])
        self.assertIsNone(m['interest_coverage']['value'])
        self.assertIn('no interest expense', m['interest_coverage']['reason'])
        self.assertEqual(an['annual']['2025-12-31']['debt_method'], 'none_reported')
        self.assertEqual(f['negative_fcf']['severity'], 'high')         # negative in each of the last 3 years
        self.assertIn('years at this burn', f['negative_fcf']['message'])
        self.assertEqual(f['sbc_heavy']['severity'], 'medium')
        self.assertEqual(f['dilution']['severity'], 'high')
        self.assertNotIn('leverage_high', f)
        self.assertNotIn('cash_conversion_weak', f)                     # not computed on losses
        self.assertIn('sbc_offset_by_buybacks', m)
        self.assertIn('no buybacks and the share count rose', m['sbc_offset_by_buybacks']['reading'])
        json.dumps(an, allow_nan=False)


class AcquisitionYear(unittest.TestCase):
    def test_growth_reported_and_with_acquisition_year_excluded(self):
        rev = {2021: 1000e6, 2022: 1050e6, 2023: 1500e6, 2024: 1580e6}
        gw = {2021: 200e6, 2022: 200e6, 2023: 700e6, 2024: 700e6}
        acq = {2023: 600e6}
        rows = {y: dict(revenue=r, operating_income=r * .15, cfo=r * .18, capex=r * .03, goodwill=gw[y], total_assets=3000e6,
                        equity=2000e6, cash=300e6, **({'acquisitions': acq[y]} if y in acq else {})) for y, r in rev.items()}
        an = analyze(synth(rows))
        m, f = an['metrics'], flags(an)
        self.assertEqual(an['annual']['2023-12-31']['revenue'], 1500e6)  # reported revenue never adjusted
        self.assertEqual(an['annual']['2022-12-31']['acquisitions'], 0.0)
        d = m['revenue_cagr_3y']['detail']
        self.assertAlmostEqual(d['reported'], (1580 / 1000) ** (1 / 3) - 1, places=9)
        self.assertAlmostEqual(d['ex_acquisition_years'], (1.05 * 1580 / 1500) ** .5 - 1, places=9)
        self.assertEqual(d['excluded_acquisition_years'], ['FY2023'])
        self.assertIn('acquisition_affected_growth', f)
        self.assertEqual(f['acquisition_affected_growth']['evidence']['years'], ['FY2023'])
        a = an['acquisition_years'][0]
        self.assertAlmostEqual(a['acquisitions_pct_revenue'], .4, places=9)
        self.assertAlmostEqual(a['goodwill_increase_pct_prior_revenue'], 500 / 1050, places=9)
        self.assertTrue([s for s in m['revenue_growth_series']['value'] if s['acquisition_year']])
        self.assertAlmostEqual(m['acquisition_spend_pct_revenue_3y']['value'], 600 / (1050 + 1500 + 1580), places=9)


class SplitCase(unittest.TestCase):
    def rows(self, sh, eps=None, wavg=False):
        return {y: dict(revenue=1000e6 + 50e6 * i, shares_outstanding=s, cfo=100e6, capex=10e6, total_assets=1e9, equity=8e8, cash=1e8,
                        **({'eps_diluted': eps[y]} if eps else {}), **({'shares_diluted_wavg': s} if wavg else {}))
                for i, (y, s) in enumerate(sh.items())}

    def test_dataqa_split_divided_out_and_flagged(self):
        # weighted-average shares restated by the full ratio: dataqa's per-share evidence for a split
        fb = synth(self.rows({2021: 100e6, 2022: 102e6, 2023: 208e6, 2024: 210e6}, wavg=True))
        an = analyze(fb, _dataqa.validate(fb))
        s = an['metrics']['share_count_cagr_3y']
        self.assertAlmostEqual(s['value'], (210 / 100 / 2) ** (1 / 3) - 1, places=9)   # issuance in the split year kept
        self.assertEqual((s['steps_used'], s['split_basis']), (3, 'dataqa'))
        self.assertEqual(s['excluded_split_pairs'][0]['factor'], 2)
        f = flags(an)
        self.assertEqual(f['possible_split']['kind'], 'data')
        self.assertIn('2-for-1', f['possible_split']['message'])
        self.assertNotIn('dilution', f)

    def test_reverse_split(self):
        fb = synth({y: dict(revenue=100e6, shares_outstanding=s) for y, s in {2022: 300e6, 2023: 30.5e6, 2024: 31e6, 2025: 31.5e6}.items()})
        fb['market']['splits'] = [{'date': '2023-06-15', 'factor': .1, 'source_id': 'syn'}]          # the dated 1:10 action
        an = analyze(fb, _dataqa.validate(fb))
        self.assertEqual(an['metrics']['share_count_cagr_3y']['excluded_split_pairs'][0]['factor'], -10)
        self.assertIn('reverse', flags(an)['possible_split']['message'])
        del fb['market']['splits']                                    # counts alone: a 90% consolidation is not established
        s = analyze(fb, _dataqa.validate(fb))['metrics']['share_count_cagr_3y']
        self.assertEqual((s['excluded_split_pairs'], s['unverified_split_like'][0]['factor']), ([], -10))

    def test_issuance_with_flat_price_is_dilution_not_a_split(self):
        """SYNTHETIC: count doubles, profit flat (EPS halves), sparse closes of unknown basis: nothing establishes a split."""
        sh = {2022: 50e6, 2023: 51e6, 2024: 102e6, 2025: 104e6}
        fb = synth(self.rows(sh, eps={2022: 1.0, 2023: 1.0, 2024: .5, 2025: .5}))
        fb['market']['price_history'] = [{'date': f'{y}-12-30', 'close': 20.0} for y in sh]
        qa = _dataqa.validate(fb)
        self.assertEqual(qa['splits'], [])
        an = analyze(fb, qa)
        s = an['metrics']['share_count_cagr_3y']
        self.assertAlmostEqual(s['value'], (104 / 50) ** (1 / 3) - 1, places=9)        # was 2.0%: the issuance was hidden
        self.assertEqual(s['excluded_split_pairs'], [])
        self.assertEqual(flags(an)['dilution']['severity'], 'high')

    def test_three_for_two_split_from_dataqa(self):
        sh = {2022: 100e6, 2023: 100e6, 2024: 150e6, 2025: 150e6}
        fb = synth(self.rows(sh, wavg=True))
        fb['market']['price_history'] = [{'date': f'{y}-12-30', 'close': 30.0 if y < 2024 else 20.0} for y in sh]
        qa = _dataqa.validate(fb)
        self.assertEqual([x['factor'] for x in qa['splits']], [1.5])
        an = analyze(fb, qa)
        self.assertAlmostEqual(an['metrics']['share_count_cagr_3y']['value'], 0.0, places=12)   # was 14.5% "dilution"
        self.assertNotIn('dilution', flags(an))
        self.assertIn('1.5-for-1', flags(an)['possible_split']['message'])

    def test_without_dataqa_an_unexplained_jump_counts_as_issuance(self):
        an = analyze(synth(self.rows({2021: 100e6, 2022: 102e6, 2023: 208e6, 2024: 210e6})))
        s = an['metrics']['share_count_cagr_3y']
        self.assertAlmostEqual(s['value'], (210 / 100) ** (1 / 3) - 1, places=9)
        self.assertEqual(s['excluded_split_pairs'], [])
        self.assertEqual(s['unverified_split_like'][0]['factor'], 2)
        f = flags(an)
        self.assertIn('no price or per-share evidence', f['possible_split']['message'])
        self.assertEqual(f['dilution']['severity'], 'high')

    def test_without_dataqa_per_share_evidence_supports_the_split(self):
        eps = {2021: 2.0, 2022: 2.1, 2023: 1.1, 2024: 1.2}
        sh = {2021: 100e6, 2022: 102e6, 2023: 208e6, 2024: 210e6}
        s = analyze(synth(self.rows(sh, eps=eps, wavg=True)))['metrics']['share_count_cagr_3y']
        self.assertAlmostEqual(s['value'], (210 / 100 / 2) ** (1 / 3) - 1, places=9)
        self.assertEqual(s['excluded_split_pairs'][0]['basis'], 'factbook_evidence')
        eps_only = analyze(synth(self.rows(sh, eps=eps)))                 # EPS alone cannot tell a split from an issuance
        s2 = eps_only['metrics']['share_count_cagr_3y']
        self.assertAlmostEqual(s2['value'], (210 / 100) ** (1 / 3) - 1, places=9)
        self.assertTrue(s2['unverified_split_like'][0]['unverified'])
        self.assertTrue(flags(eps_only)['dilution']['evidence']['unverified'])

    def test_equity_raise_is_not_hidden_as_a_split(self):
        """SYNTHETIC loss-maker: a March raise doubles the count; weighted shares are time-weighted (issuance signature)."""
        sh = {2022: 98e6, 2023: 99e6, 2024: 100e6, 2025: 200e6}
        wavg = {2022: 97e6, 2023: 98.5e6, 2024: 99.5e6, 2025: 180e6}
        rows = {y: dict(revenue=100e6, shares_outstanding=s, shares_diluted_wavg=wavg[y], net_income=-50e6, eps_diluted=-50e6 / wavg[y],
                        total_assets=1e9) for y, s in sh.items()}
        fb = synth(rows)
        an = analyze(fb, _dataqa.validate(fb))
        s = an['metrics']['share_count_cagr_3y']
        self.assertAlmostEqual(s['value'], (200 / 98) ** (1 / 3) - 1, places=9)       # was 0.7%/yr: the raise divided out
        self.assertEqual(flags(an)['dilution']['severity'], 'high')
        bare = synth({y: dict(revenue=100e6, shares_outstanding=s, total_assets=1e9) for y, s in sh.items()})
        an2 = analyze(bare, _dataqa.validate(bare))                       # counts only: unresolved, kept as issuance
        self.assertAlmostEqual(an2['metrics']['share_count_cagr_3y']['value'], (200 / 98) ** (1 / 3) - 1, places=9)
        self.assertTrue(flags(an2)['dilution']['evidence']['unverified'])

    def test_split_plus_issuance_divides_only_the_split(self):
        """SYNTHETIC: a 2:1 split and an 8% raise in the same fiscal year; weighted shares restated (x2.1)."""
        sh = {2022: 100e6, 2023: 100e6, 2024: 100e6, 2025: 216e6}
        wavg = {2022: 100e6, 2023: 100e6, 2024: 100e6, 2025: 210e6}
        fb = synth({y: dict(revenue=1e9, shares_outstanding=s, shares_diluted_wavg=wavg[y], total_assets=5e9) for y, s in sh.items()})
        an = analyze(fb, _dataqa.validate(fb))
        self.assertAlmostEqual(an['metrics']['share_count_cagr_3y']['value'], 1.08 ** (1 / 3) - 1, places=9)   # was 29.3%/yr
        self.assertEqual(flags(an)['dilution']['severity'], 'medium')


class MissingData(unittest.TestCase):
    def test_income_statement_only(self):
        rows = {y: dict(revenue=100e6 * 1.1 ** i, operating_income=10e6, net_income=8e6) for i, y in enumerate(range(2020, 2026))}
        an = analyze(synth(rows), {})
        m = an['metrics']
        self.assertAlmostEqual(m['revenue_cagr_5y']['value'], .1, places=9)
        for name in ('fcf_margin', 'fcf_after_sbc_margin', 'cfo_to_net_income', 'capex_to_da', 'roic', 'net_cash', 'buybacks_pct_fcf_3y',
                     'share_count_cagr_3y', 'incremental_roic_3y', 'nwc_pct_revenue', 'accruals_ratio', 'net_debt_to_ebitda',
                     'sbc_offset_by_buybacks', 'acquisition_spend_pct_revenue_3y'):
            self.assertIsNone(m[name]['value'], name)
            self.assertEqual(m[name]['status'], 'missing', name)
            self.assertTrue(m[name]['reason'], name)
        self.assertIn('no cfo, capex', m['fcf_margin']['reason'])
        rows[2025].update(cash=50e6, short_term_investments=20e6, long_term_investments=100e6, long_term_debt=30e6)
        nc = analyze(synth(rows), {})['metrics']['net_cash']
        self.assertEqual(nc['value'], 50e6 + 20e6 - 30e6)               # long-term investments shown, not counted as cash
        self.assertEqual(nc['detail']['long_term_investments'], 100e6)
        a = an['annual']['2025-12-31']
        self.assertIsNone(a['buybacks'])                               # no cash-flow statement: absent lines are NOT zero-filled
        self.assertIsNone(a['debt'])
        self.assertEqual(an['forensics']['piotroski']['status'], 'unavailable')
        self.assertEqual(an['ttm']['method'], 'latest_fiscal_year')
        json.dumps(an, allow_nan=False)

    def test_empty_factbook(self):
        fb = F.new({'ticker': 'NONE', 'currency': 'USD'}, '2026-10-19')
        an = analyze(fb, None)
        self.assertEqual(an['years'], [])
        self.assertEqual(an['forensics']['status'], 'unavailable')
        self.assertIsNone(an['metrics']['revenue_cagr_3y']['value'])
        self.assertIsNone(an['ttm']['period_end'])
        json.dumps(an, allow_nan=False)

    def test_negative_value_in_nonnegative_history_field_does_not_kill_forensics(self):
        rows = {y: dict(revenue=100e6, net_income=10e6, cfo=12e6, capex=2e6, total_assets=200e6, interest_expense=-1e6)
                for y in range(2022, 2026)}
        an = analyze(synth(rows))
        self.assertEqual(an['forensics']['status'], 'computed')
        self.assertTrue([x for x in an['assumptions'] if x['code'] == 'history_field_dropped'])


class DebtScope(unittest.TestCase):
    def test_tagged_scope_follows_roll_forward_else_conservative(self):
        base = dict(revenue=500e6, cfo=60e6, capex=10e6, total_assets=1e9, equity=5e8, cash=1e8)
        # the tag excludes the current portion: under 'plus current' 1200 - 100 repaid = 1100 rolls forward, the tag alone does not
        rows = {2023: dict(base, long_term_debt_tagged=1000e6, current_portion_ltd=200e6),
                2024: dict(base, long_term_debt_tagged=1000e6, current_portion_ltd=100e6, debt_repaid=100e6),
                2026: dict(base, long_term_debt=700e6, current_portion_ltd=100e6)}
        an = analyze(synth(rows, as_of='2027-06-30'))
        a = an['annual']
        self.assertEqual((a['2023-12-31']['debt'], a['2023-12-31']['debt_method']), (1200e6, 'tagged_plus_current'))
        self.assertEqual((a['2024-12-31']['debt'], a['2024-12-31']['debt_method']), (1100e6, 'tagged_plus_current'))
        self.assertEqual((a['2026-12-31']['debt'], a['2026-12-31']['debt_method']), (800e6, 'noncurrent+current'))
        notes = {x['period_end']: x for x in an['assumptions'] if x['code'] == 'debt_scope'}
        self.assertIn('per-hypothesis roll-forward supports tagged_plus_current', notes['2023-12-31']['decision'])
        self.assertEqual(notes['2024-12-31']['roll_forward_expected'], 1100e6)
        lone = analyze(synth({2025: dict(base, long_term_debt_tagged=1000e6, current_portion_ltd=200e6)}))
        self.assertEqual(lone['annual']['2025-12-31']['debt_method'], 'tagged_plus_current')
        self.assertIn('no roll-forward distinguishes', [x for x in lone['assumptions'] if x['code'] == 'debt_scope'][0]['decision'])
        # nwc removes the current portion from current liabilities whatever the debt scope
        rows[2026].update(current_assets=300e6, current_liabilities=250e6)
        self.assertEqual(analyze(synth(rows, as_of='2027-06-30'))['annual']['2026-12-31']['nwc'], 300e6 - 1e8 - 0 - (250e6 - 100e6))

    def test_historical_scope_follows_dataqa_per_hypothesis_rollforward(self):
        """SYNTHETIC: LongTermDebt is the TOTAL every year; the current portion varies (100/300/50/400)."""
        yrs = {2022: (1000, 100, 0, 0), 2023: (1100, 300, 200, 100), 2024: (1100, 50, 300, 300), 2025: (1200, 400, 400, 300)}
        rows = {y: dict(long_term_debt_tagged=t * 1e6, current_portion_ltd=c * 1e6, debt_issued=i * 1e6, debt_repaid=r * 1e6,
                        total_assets=5000e6, total_liabilities=3000e6, equity=2000e6, current_assets=1500e6, cash=500e6, revenue=4000e6,
                        operating_income=600e6, cfo=700e6, pretax_income=550e6, income_tax=110e6) for y, (t, c, i, r) in yrs.items()}
        fb = synth(rows)
        qa = _dataqa.validate(fb)
        self.assertEqual({c['period']: c['detail']['passing'] for c in qa['checks'] if c['code'] == 'debt_rollforward'},
                         {f'{y - 1}-12-31..{y}-12-31': ['tagged_total'] for y in (2023, 2024, 2025)})
        an = analyze(fb, qa)
        self.assertEqual([(an['annual'][f'{y}-12-31']['debt'], an['annual'][f'{y}-12-31']['debt_method']) for y in yrs],
                         [(t * 1e6, 'tagged_total') for t, _, _, _ in yrs.values()])           # was 1100 / 1400 / 1150 / 1200
        self.assertAlmostEqual(an['metrics']['roic']['average_invested_capital'], 2650e6)

    def test_debt_current_total_already_contains_commercial_paper(self):
        rows = {2025: dict(revenue=10e9, long_term_debt=40e9, current_portion_ltd=3e9, short_term_borrowings=2e9, cash=8e9, equity=20e9,
                           total_assets=90e9, current_assets=20e9, current_liabilities=15e9)}
        fb = synth(rows)
        for f in fb['facts']:
            f['concept'] = {'current_portion_ltd': 'debtcurrent', 'short_term_borrowings': 'commercialpaper'}.get(f['metric'])
        a = analyze(fb)['annual']['2025-12-31']
        self.assertEqual(a['debt'], 43e9)                                         # was 45bn
        self.assertEqual(a['nwc'], 20e9 - 8e9 - (15e9 - 3e9))

    def test_unresolved_scope_default_matches_dataqa(self):
        """SYNTHETIC (review finding 9): no debt flows to test the scope - both modules add the current portion."""
        rows = {y: dict(revenue=2000e6, operating_income=300e6, long_term_debt_tagged=t, current_portion_ltd=c, cash=200e6,
                        equity=1500e6, total_assets=4000e6) for y, t, c in ((2024, 1000e6, 500e6), (2025, 900e6, 400e6))}
        fb = synth(rows)
        qa = _dataqa.validate(fb)
        self.assertEqual(qa['resolved']['debt']['method'], 'tagged_plus_current')
        a = analyze(fb, qa)['annual']
        self.assertEqual((a['2024-12-31']['debt'], a['2024-12-31']['debt_method']), (1500e6, 'tagged_plus_current'))   # was 1000
        self.assertEqual(a['2025-12-31']['debt'], qa['resolved']['debt']['value'])

    def test_leverage_flag(self):
        rows = {y: dict(revenue=1000e6, operating_income=80e6, d_and_a=20e6, interest_expense=50e6, long_term_debt=600e6,
                        cash=50e6, equity=300e6, total_assets=1200e6, cfo=90e6, capex=40e6) for y in range(2022, 2026)}
        an = analyze(synth(rows))
        m, f = an['metrics'], flags(an)
        self.assertAlmostEqual(m['net_debt_to_ebitda']['value'], 550 / 100, places=9)
        self.assertEqual(f['leverage_high']['severity'], 'high')
        self.assertAlmostEqual(m['interest_coverage']['value'], 1.6, places=9)
        self.assertEqual(f['interest_coverage_low']['severity'], 'medium')


class EarningsQuality(unittest.TestCase):
    def test_weak_cash_conversion_and_margin_compression(self):
        """SYNTHETIC: profits reported but not collected, margins shrinking 20% -> 12%."""
        om = {2021: .20, 2022: .18, 2023: .15, 2024: .12}
        rows = {y: dict(revenue=1000e6, operating_income=1000e6 * m, net_income=800e6 * m, cfo=800e6 * m * (.6 if y >= 2023 else 1.1),
                        capex=20e6, total_assets=1500e6) for y, m in om.items()}
        an = analyze(synth(rows))
        f = flags(an)
        self.assertEqual(len(f['cash_conversion_weak']['evidence']['years']), 2)
        self.assertAlmostEqual(an['metrics']['operating_margin_trend_3y']['value'], -.08, places=9)
        self.assertIn('8.0 percentage points', f['margin_compression']['message'])
        self.assertAlmostEqual(an['metrics']['operating_margin_best_3y']['value'], .18, places=9)
        self.assertEqual(an['metrics']['operating_margin_best_3y']['best_year'], 'FY2022')


class Quarterly(unittest.TestCase):
    def quarters(self, revs, ois):
        ends = ['2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31', '2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31']
        out = {}
        for e, r, o in zip(ends[-len(revs):], revs, ois):
            y, mth = int(e[:4]), int(e[5:7])
            out[(f'{y}Q{(mth - 1) // 3 + 1}', e)] = {'revenue': r, 'operating_income': o, 'gross_profit': r * .6}
        return out

    def test_acceleration_and_incremental_margin(self):
        revs = [100, 102, 104, 106, 110, 115, 122, 131]
        ois = [10, 10, 10, 11, 12, 14, 17, 21]
        q = self.quarters([x * 1e6 for x in revs], [x * 1e6 for x in ois])
        fb = synth({2024: {'revenue': 412e6}, 2025: {'revenue': 478e6}}, quarters=q, as_of='2026-03-01')
        an = analyze(fb)
        qm = an['metrics']['quarterly']
        self.assertEqual(qm['period'], '2025-12-31')
        self.assertAlmostEqual(qm['value'], 131 / 106 - 1, places=9)
        self.assertTrue(qm['accelerating_two_quarters'])
        self.assertAlmostEqual(qm['acceleration_2q'], (131 / 106 - 1) - (115 / 102 - 1), places=9)
        self.assertAlmostEqual(qm['incremental_operating_margin_yoy'], (21 - 11) / (131 - 106), places=9)
        self.assertAlmostEqual(qm['ttm_growth'], 478 / 412 - 1, places=9)
        self.assertEqual(qm['status'], 'reported')
        self.assertEqual(an['ttm']['method'], 'quarters')
        self.assertEqual(an['ttm']['label'], 'TTM@2025-12-31')

    def test_five_quarters_minimum_and_spacing(self):
        q = self.quarters([100e6, 101e6, 102e6, 103e6], [1e6] * 4)
        qm = analyze(synth({2025: {'revenue': 406e6}}, quarters=q, as_of='2026-03-01'))['metrics']['quarterly']
        self.assertEqual(qm['status'], 'unavailable')
        self.assertIn('5 standalone quarters', qm['reason'])
        q = self.quarters([100e6, 101e6, 102e6, 103e6, 104e6, 105e6, 106e6], [1e6] * 7)
        q.pop(('2025Q1', '2025-03-31'))                                 # a missing quarter: "4 back" is no longer a year earlier
        qm = analyze(synth({2025: {'revenue': 406e6}}, quarters=q, as_of='2026-03-01'))['metrics']['quarterly']
        self.assertEqual(qm['status'], 'unavailable')
        self.assertIn('340-390 days', qm['reason'])


class SourcesAndPeriods(unittest.TestCase):
    def test_ttm_flows_are_never_mixed_with_annual(self):
        q = Quarterly().quarters([100e6, 102e6, 104e6, 106e6, 110e6, 115e6, 122e6, 131e6], [10e6] * 8)
        fb = synth({2024: {'revenue': 412e6, 'operating_income': 40e6, 'd_and_a': 8e6},
                    2025: {'revenue': 478e6, 'operating_income': 80e6, 'd_and_a': 10e6, 'cash': 50e6, 'long_term_debt': 250e6,
                           'total_assets': 900e6}}, quarters=q, as_of='2026-03-01')
        an = analyze(fb)
        self.assertEqual(an['ttm']['method'], 'quarters')
        self.assertIn('d_and_a', an['ttm']['excluded'])               # annual-only D&A is not added to quarterly operating income
        nd = an['metrics']['net_debt_to_ebitda']
        self.assertEqual(nd['period'], 'FY2025')                        # falls back to one consistent fiscal year
        self.assertAlmostEqual(nd['value'], 200 / 90, places=9)

    def test_vendor_normalized_values_are_single_source_and_same_year_merges(self):
        fb = synth({2023: {'revenue': 90e6, 'operating_income': 9e6}, 2024: {'revenue': 100e6, 'operating_income': 10e6}})
        F.add_source(fb, 'av:income', 'alpha_vantage', 'income', 'vendor_normalized', '2026-10-19T00:00:00Z')
        F.add_fact(fb, 'revenue', 'AVFY@2025-12-29', '2025-12-29', 120e6, 'av:income', 'normalized', '2026-03-15', estimated=True)
        F.add_fact(fb, 'operating_income', 'AVFY@2025-12-29', '2025-12-29', 15e6, 'av:income', 'normalized', '2026-03-15', estimated=True)
        F.add_fact(fb, 'revenue', 'AVFY@2024-12-29', '2024-12-29', 101e6, 'av:income', 'normalized', '2025-03-15', estimated=True)
        an = analyze(fb)
        self.assertEqual(an['years'], ['2023-12-31', '2024-12-31', '2025-12-29'])   # 2024 from two sources stays one year
        self.assertEqual(an['annual']['2024-12-31']['revenue'], 100e6)               # filing-derived preferred
        m = an['metrics']
        self.assertEqual(m['operating_margin']['status'], 'single_source')
        self.assertEqual(m['operating_margin']['period'], 'FY@2025-12-29')
        self.assertAlmostEqual(m['revenue_growth_last']['value'], .2, places=9)
        self.assertEqual(m['revenue_growth_last']['status'], 'single_source')


class FiscalCalendar(unittest.TestCase):
    def test_missing_year_is_not_bridged(self):
        rows = {y: {'revenue': r} for y, r in ((2020, 100e6), (2021, 110e6), (2023, 130e6), (2024, 140e6))}
        an = analyze(synth(rows))
        m = an['metrics']
        self.assertAlmostEqual(m['revenue_cagr_3y']['value'], (140 / 110) ** (1 / 3) - 1, places=9)   # endpoints 3 years apart
        self.assertEqual([s['fiscal_period'] for s in m['revenue_growth_series']['value']], ['FY2021', 'FY2024'])
        self.assertIn('fiscal_calendar_gap', flags(an))
        self.assertIsNone(m['revenue_cagr_full']['value'])



# ---------------------------------------------------------------- review 2026-10-19: TTM and current-year M&A (SYNTHETIC bundles)

def adbe_with_quarters(acq_ytd=None):
    """ADBE fixture + a SYNTHETIC evidence bundle of FY2025/FY2026 quarters (round numbers, not Adobe's)."""
    fb = deepcopy(ADBE)
    src = [{'id': 'q', 'kind': 'issuer_release', 'published_at': '2026-09-20'}, {'id': 'k', 'kind': 'filing', 'published_at': '2026-09-25'}]
    qf = [{'metric': 'revenue', 'period': p, 'period_end': e, 'value': v, 'source_ids': ['q']}
          for p, e, v in (('FY2025Q1', '2025-02-28', 5.7e9), ('FY2025Q2', '2025-05-30', 5.9e9), ('FY2025Q3', '2025-08-29', 6.0e9),
                          ('FY2026Q1', '2026-02-27', 6.4e9), ('FY2026Q2', '2026-05-29', 6.6e9), ('FY2026Q3', '2026-08-28', 6.8e9))]
    if acq_ytd is not None:
        qf.append({'metric': 'acquisitions_ytd', 'period': 'FY2026Q3YTD', 'period_end': '2026-08-28', 'value': acq_ytd, 'source_ids': ['k']})
    F.add_qualitative_bundle(fb, {'as_of': '2026-10-19', 'sources': src, 'quarterly_facts': qf})
    return fb


class TtmConsistency(unittest.TestCase):
    def test_dataqa_analysis_and_facts_agree(self):
        fb = adbe_with_quarters()
        qa = _dataqa.validate(fb)
        an = analyze(fb, qa)
        t = F.ttm(fb, 'revenue')
        want = 23.769e9 + (6.4 + 6.6 + 6.8 - 5.7 - 5.9 - 6.0) * 1e9
        self.assertEqual(t['method'], 'annual_plus_ytd')
        self.assertAlmostEqual(t['value'], want, places=0)
        self.assertEqual(qa['fields']['revenue_ttm']['value'], t['value'])     # was FY + 2026 quarters + 2025 quarters
        self.assertEqual(an['ttm']['revenue'], t['value'])
        self.assertNotIn('dataqa_mismatch', an['ttm'])
        self.assertNotIn('ttm_inconsistent', flags(an))

    def test_mismatch_is_flagged(self):
        fb = adbe_with_quarters()
        qa = _dataqa.validate(fb)
        qa['fields']['revenue_ttm'] = dict(qa['fields']['revenue_ttm'], value=61.1e9)
        an = analyze(fb, qa)
        self.assertEqual(an['ttm']['dataqa_mismatch'][0]['field'], 'revenue_ttm')
        self.assertEqual(flags(an)['ttm_inconsistent']['severity'], 'high')


class CurrentYearAcquisitions(unittest.TestCase):
    def test_year_to_date_acquisitions_enter_the_window(self):
        an = analyze(adbe_with_quarters(acq_ytd=1.5e9), _dataqa.validate(adbe_with_quarters(acq_ytd=1.5e9)))
        m = an['metrics']['acquisition_spend_pct_revenue_3y']
        self.assertEqual(m['window_kind'], 'fiscal_3y_plus_ytd')
        self.assertEqual(m['window'], 'FY2023..FY2025 + year to date 2026-08-28')
        self.assertAlmostEqual(m['value'], (17e6 + 1.5e9) / (19409e6 + 21505e6 + 23769e6 + 19.8e9), places=9)   # was 0.03%
        self.assertEqual(m['year_to_date']['acquisitions'], 1.5e9)
        f = flags(an)['acquisition_current_year']
        self.assertEqual(f['severity'], 'medium')
        self.assertIn('YTD@2026-08-28', [a['fiscal_period'] for a in an['acquisition_years']])

    def test_newer_revenue_without_acquisition_data_says_so(self):
        m = analyze(adbe_with_quarters(), adbe_qa())['metrics']['acquisition_spend_pct_revenue_3y']
        self.assertEqual((m['window_kind'], m['window']), ('fiscal_3y', 'FY2023..FY2025'))
        self.assertIn('not counted', m['note'])


class RestatedHistoryTests(unittest.TestCase):
    """SYNTHETIC: a divestiture in 2023 (first-filed 2022 revenue 150 with the sold business, 95 as restated in the 2023
    report). Growth is like for like; the first-filed CAGR (-10%) would call a growing business a decliner (FIS 2026)."""

    def test_cagr_chains_like_for_like_steps(self):
        fb = synth({y: dict(revenue=r, operating_income=r * .15) for y, r in {2021: 140., 2022: 150., 2023: 100., 2024: 104., 2025: 108.}.items()})
        F.add_fact(fb, 'revenue_prior_comparable', 'CMP@2023-12-31', '2023-12-31', 95., 'syn', 'as_reported', '2024-02-15')
        m = analyze(fb)['metrics']
        self.assertAlmostEqual(m['revenue_cagr_3y']['value'], (108 / 95) ** (1 / 3) - 1, places=9)
        self.assertAlmostEqual(m['revenue_cagr_3y']['detail']['first_filed'], (108 / 150) ** (1 / 3) - 1, places=9)
        self.assertEqual(m['revenue_cagr_3y']['detail']['restated_bases'], ['FY2023'])
        steps = {s['fiscal_period']: s for s in m['revenue_growth_series']['value']}
        self.assertAlmostEqual(steps['FY2023']['growth'], 100 / 95 - 1)
        self.assertEqual(steps['FY2023']['restated_base'], 95.)
        self.assertNotIn('restated_base', steps['FY2024'])

    def test_rounding_differences_keep_first_filed_revenue(self):
        fb = synth({y: dict(revenue=r) for y, r in {2022: 100., 2023: 110., 2024: 121., 2025: 133.1}.items()})
        F.add_fact(fb, 'revenue_prior_comparable', 'CMP@2023-12-31', '2023-12-31', 100.5, 'syn', 'as_reported', '2024-02-15')
        m = analyze(fb)['metrics']
        self.assertAlmostEqual(m['revenue_cagr_3y']['value'], .1, places=9)
        self.assertNotIn('restated_bases', m['revenue_cagr_3y']['detail'])


if __name__ == '__main__':
    unittest.main()


# ---------------------------------------------------------------- review 2026-10-20 (TTM payments, current-year acquisitions; SYNTHETIC)

class TtmPayments(unittest.TestCase):
    def test_mixed_sign_payment_lines_sum_by_magnitude(self):
        fb = synth({2025: dict(revenue=4000e6, capex=500e6, buybacks=2000e6, cfo=900e6, total_assets=9e9, cash=1e9)}, as_of='2026-11-20')
        qf = []
        for y, cap, bb in ((2025, (100, 120, 130), (400, 500, 600)), (2026, (130, 140, 150), (700, 800, 900))):
            for i, end in enumerate((f'{y}-03-31', f'{y}-06-30', f'{y}-09-30')):
                qf += [{'metric': m, 'period': f'FY{y}Q{i + 1}', 'period_end': end, 'value': v, 'source_ids': ['r']}
                       for m, v in (('capex', -cap[i] * 1e6), ('buybacks', -bb[i] * 1e6), ('revenue', 1000e6), ('cfo', 220e6))]
        F.add_qualitative_bundle(fb, {'as_of': '2026-11-20', 'sources': [{'id': 'r', 'kind': 'issuer_release', 'published_at': '2026-11-01'}],
                                      'quarterly_facts': qf})
        an = analyze(fb, _dataqa.validate(fb))
        t = an['ttm']
        self.assertEqual((t['capex'], t['buybacks']), (570e6, 2900e6))           # were 430m and 1,100m
        self.assertEqual(t['fcf'], 900e6 - 570e6)
        self.assertNotIn('ttm_inconsistent', flags(an))


class CurrentYearAcquisitionRows(unittest.TestCase):
    def test_deal_quarter_row_counts_as_a_lower_bound(self):
        rows = {y: dict(revenue=r * 1e6, acquisitions=a * 1e6, cfo=9000e6, total_assets=30e9) for y, r, a in ((2023, 19400, 0), (2024, 21500, 0),
                                                                                                          (2025, 23800, 17))}
        fb = synth(rows)
        qf = [{'metric': 'revenue', 'period': f'FY2026Q{i}', 'period_end': e, 'value': v * 1e6, 'source_ids': ['r']}
              for i, (e, v) in enumerate((('2026-03-31', 6400), ('2026-06-30', 6600), ('2026-09-30', 6750)), 1)]
        qf.append({'metric': 'acquisitions', 'period': 'FY2026Q2', 'period_end': '2026-06-30', 'value': 1870e6, 'source_ids': ['r']})
        F.add_qualitative_bundle(fb, {'as_of': '2026-10-19', 'sources': [{'id': 'r', 'kind': 'filing', 'published_at': '2026-10-05'}],
                                      'quarterly_facts': qf})
        an = analyze(fb)
        m = an['metrics']['acquisition_spend_pct_revenue_3y']
        self.assertAlmostEqual(m['value'], (17 + 1870) / (19400 + 21500 + 23800 + 6400 + 6600), places=12)   # the deal was dropped
        self.assertEqual((m['window_kind'], m['year_to_date']['lower_bound']), ('fiscal_3y_plus_ytd', True))
        self.assertIn('lower bound', m['note'])
        f = flags(an)['acquisition_current_year']
        self.assertTrue(f['evidence']['lower_bound'])
        self.assertIn('at least', f['message'])
