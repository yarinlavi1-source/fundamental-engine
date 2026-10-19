"""Stage case suite: what the engine said at the time vs what happened (point in time, no hindsight inputs).

Groups chosen to avoid learning only success stories (user requirement, 19.10.2026):
early growth that worked, turned profitable, promising-then-failed, quality priced too high, steady compounders.
The as-of month for each case is the month named below; every feature comes from data filed before it.

usage: python scripts/case_suite.py gap_panel.pkl panel.pkl out.json
"""
import json, sys
import numpy as np, pandas as pd

CASES = {
    'early_growth_worked': [('CRWD', '2020-06'), ('DDOG', '2020-06'), ('ZS', '2019-06'), ('TTD', '2017-06'), ('ENPH', '2019-06'),
                            ('AXON', '2019-06'), ('MDB', '2019-06'), ('NET', '2020-12'), ('TSLA', '2019-06'), ('APP', '2023-06')],
    'turned_profitable': [('UBER', '2023-06'), ('ABNB', '2022-03'), ('CELH', '2021-03'), ('HOOD', '2024-06'), ('SOFI', '2024-03'),
                          ('DKNG', '2024-03'), ('PLTR', '2023-06'), ('SHAK', '2015-06')],
    'promising_then_failed': [('PTON', '2021-03'), ('BYND', '2020-06'), ('FSLY', '2020-09'), ('TDOC', '2021-03'), ('OPEN', '2021-06'),
                              ('UPST', '2021-09'), ('LMND', '2021-03'), ('AFRM', '2021-09'), ('PLUG', '2021-03'), ('DOCU', '2021-09'),
                              ('CVNA', '2021-09'), ('RIVN', '2022-06'), ('W', '2021-03'), ('CHGG', '2021-03')],
    'quality_priced_too_high': [('ZM', '2020-12'), ('SNOW', '2021-11'), ('NVDA', '2021-11'), ('NKE', '2021-11'), ('CRM', '2021-11'),
                                ('PYPL', '2021-07'), ('ADBE', '2021-11'), ('SHOP', '2021-11'), ('NFLX', '2021-11'), ('MRNA', '2021-09')],
    'steady_compounders': [('COST', '2016-06'), ('MSFT', '2016-06'), ('V', '2016-06'), ('AAPL', '2016-06'), ('ODFL', '2016-06'),
                           ('CPRT', '2016-06'), ('TJX', '2016-06'), ('INTU', '2016-06')],
}

def _plus(m, k):
    y, mo = map(int, m.split('-')); n = y * 12 + mo - 1 + k
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


def _diff(a, b):
    ya, ma = map(int, a.split('-')); yb, mb = map(int, b.split('-'))
    return (yb - ya) * 12 + mb - ma


gp_path, panel_path, out = sys.argv[1:4]
G = pd.read_pickle(gp_path).set_index(['tk', 'm'])
P = pd.read_pickle(panel_path).set_index(['tk', 'm']).sort_index()
spy_r36 = P.xs('SPY', level=0).r36 if 'SPY' in P.index.get_level_values(0) else None
rows = []
for grp, cs in CASES.items():
    for tk, m in cs:
        r = {'group': grp, 'tk': tk, 'm_named': m}
        # Rule fixed 19.10.2026 before looking at shifted outcomes: if the named month is not eligible
        # (too few filed quarters after an IPO, size filter), use the FIRST eligible month within 24 months after it.
        months = sorted(x for x in (P.loc[tk].index if tk in P.index.get_level_values(0) else []) if m <= x <= _plus(m, 24))
        if not months:
            r['status'] = 'not_in_panel within 24 months of the named date (no XBRL quarters, no price, or size filter)'
            rows.append(r); continue
        m = months[0]; key = (tk, m)
        r['m'] = m; r['shifted_months'] = _diff(r['m_named'], m)
        p = P.loc[key]
        r.update({k: (None if pd.isna(p[k]) else (float(p[k]) if isinstance(p[k], (int, float, np.floating)) else p[k]))
                  for k in ('rev_ttm', 'g_ttm', 'accel_2q', 'accel_streak', 'op_margin', 'op_margin_trend', 'incremental_op_margin',
                            'fcf_margin', 'sbc_to_revenue', 'dilution_yoy', 'stage', 'growth_character', 'mcap', 'r12', 'r36')})
        if key in G.index:
            g = G.loc[key]
            r.update({'g_impl': float(g.g_impl), 'g_status': g.g_status, 'p_beat': None if pd.isna(g.p_beat) else float(g.p_beat),
                      'realized_3y_rev_cagr': None if pd.isna(g.real3) else float(g.real3)})
        rows.append(r)
json.dump(rows, open(out, 'w'), indent=1, default=str)
df = pd.DataFrame(rows)
cols = [c for c in ('group', 'tk', 'm', 'shifted_months', 'stage', 'g_ttm', 'op_margin', 'g_impl', 'p_beat', 'realized_3y_rev_cagr', 'r36', 'status') if c in df]
pd.set_option('display.width', 250)
print(df[cols].round(2).to_string(index=False))
print('\nby group (median):')
print(df.groupby('group')[[c for c in ('g_impl', 'p_beat', 'realized_3y_rev_cagr', 'r36') if c in df]].median().round(3).to_string())
