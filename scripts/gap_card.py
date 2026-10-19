"""Expectation-gap card for one US filer, live: SEC companyfacts + Yahoo price -> features, scorecard,
uniform implied growth and the empirical P(beat) from the latest base-rate table.

usage: python scripts/gap_card.py TICKER base_rate_table.json [out.json]
The uniform model is the pre-registered screening model, NOT a company-specific valuation; use
expectation_gap with company drivers + capital_bridge for that. Foreign filers (20-F/6-K) are
usually missing quarterly XBRL and will report insufficient data.
"""
import json, sys, urllib.request, http.cookiejar
from datetime import date
import numpy as np
from fundamental_engine.pit import company, latest_at, ttm_at, available, shares_at
from fundamental_engine.inflection import features, scorecard
from fundamental_engine.expectations_vec import solve_growth

UA = {'User-Agent': 'Yarin Lavi research yarinlavi1@gmail.com'}
tk, table_path = sys.argv[1].upper(), sys.argv[2]
out = sys.argv[3] if len(sys.argv) > 3 else None
m = json.loads(urllib.request.urlopen(urllib.request.Request('https://www.sec.gov/files/company_tickers.json', headers=UA), timeout=60).read())
cik = next(v['cik_str'] for v in m.values() if v['ticker'] == tk)
facts = json.loads(urllib.request.urlopen(urllib.request.Request(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json', headers=UA), timeout=60).read())
rec = company(facts)
t = date.today()
f = features(rec, t)
cj = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj)); op.addheaders = [('User-Agent', 'Mozilla/5.0')]
q = json.loads(op.open(f'https://query2.finance.yahoo.com/v8/finance/chart/{tk}?range=1y&interval=1d', timeout=30).read())['chart']['result'][0]
closes = [c for c in q['indicators']['quote'][0]['close'] if c]
price, hi = closes[-1], max(closes)
card = {'ticker': tk, 'cik': cik, 'as_of': t.isoformat(), 'price': price, 'near_52w_high': price / hi}
if not f:
    card['status'] = 'insufficient point-in-time quarterly data'
else:
    sd, sh, sh_src = shares_at(rec, t)
    cash = (latest_at(rec['cash'], t) or 0) + (latest_at(rec['sti'], t) or 0)
    debt = (latest_at(rec['ltd'], t) or 0) + (latest_at(rec['ltd_cur'], t) or 0)
    best = max([oi / rv for off in range(9) for oi, rv in [(ttm_at(rec['oi'], t, offset=off)[0], ttm_at(rec['rev'], t, offset=off)[0])] if oi is not None and rv] or [f.get('op_margin') or 0])
    ev = sh * price + debt - cash if sh else None
    card.update({'features': {k: v for k, v in f.items() if not isinstance(v, list)}, 'scorecard': scorecard(f),
                 'shares': sh, 'shares_date': sd, 'shares_source': sh_src, 'ev_simple': ev,
                 'capital_structure_warning': 'Uniform EV uses cover-page shares, balance-sheet cash and term debt only. Converts/warrants/prepayments need capital_bridge.'})
    if ev and ev > 0 and f.get('op_margin') is not None:
        g, ok, flo, fhi = solve_growth(np.array([ev]), np.array([f['rev_ttm']]), np.array([np.clip(f['op_margin'], -0.5, 0.5)]),
                                       np.array([np.clip(best, 0.08, 0.35)]))
        card['implied_growth_5y'] = float(g[0]) if ok[0] else ('below -50%' if flo[0] > 0 else 'above 150%')
        T = json.load(open(table_path))
        SIZE = [0, 250e6, 1e9, 5e9, 25e9, float('inf')]; GROW = [-float('inf'), 0, .10, .20, .35, .60, float('inf')]
        si = next(i for i in range(5) if SIZE[i] <= f['rev_ttm'] < SIZE[i + 1]); gi = next(i for i in range(6) if GROW[i] <= f['g_ttm'] < GROW[i + 1])
        key_full = f"{si}_{gi}_{int((f.get('accel_2q') or 0) > 0)}_{int((f.get('op_margin_trend') or 0) > 0)}"
        arr = T['full'].get(key_full) if len(T['full'].get(key_full, [])) >= 30 else T['coarse'].get(f'{si}_{gi}')
        if arr and ok[0]:
            arr = sorted(arr)
            card['p_beat'] = 1 - np.searchsorted(arr, float(g[0])) / len(arr)
            card['base_rate_bucket'] = key_full if len(T['full'].get(key_full, [])) >= 30 else f'{si}_{gi}'
            card['bucket_n'] = len(arr)
            card['interpretation'] = ('P(beat) = share of comparable businesses that grew as fast as this price requires. '
                                      'Calibrated in 2018-2025 (docs/GAP_TEST_RESULTS.md) but NOT a return signal; low values '
                                      'carried ~3-4x the 12-month halving rate. For young capital-light growers the uniform model '
                                      'overstates required growth: use expectation_gap with company drivers before any conclusion.')
print(json.dumps(card, indent=1, default=str))
if out:
    json.dump(card, open(out, 'w'), indent=1, default=str)
