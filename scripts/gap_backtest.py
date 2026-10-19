"""Pre-registered test of the expectation gap (docs/prereg_expectation_gap.md). Do not tune after seeing holdout.

usage: python scripts/gap_backtest.py panel.pkl px_dir out_dir
"""
import json, os, sys
from datetime import date
import numpy as np, pandas as pd
from fundamental_engine.expectations_vec import solve_growth

panel_path, px_dir, out_dir = sys.argv[1:4]
os.makedirs(out_dir, exist_ok=True)
P = pd.read_pickle(panel_path)
P = P[(P.rev_ttm >= 50e6) & (P.mcap >= 300e6)].copy()
P['ev'] = P.mcap + P.debt.fillna(0) - P.cash.fillna(0)
P['mdate'] = pd.to_datetime(P.m + '-01')
nonfin = ~P.fin.astype(bool)

# ---------- implied growth (uniform assumptions, fixed in the prereg) ----------
G = P[nonfin & (P.ev > 0) & P.op_margin.notna()].copy()
m1 = G.op_margin.clip(-0.5, 0.5).values
mm = G.best_m.fillna(G.op_margin).clip(0.08, 0.35).values
g, ok, f_lo, f_hi = solve_growth(G.ev.values, G.rev_ttm.values, m1, mm)
status = np.where(ok, 'solved', np.where(f_lo > 0, 'below_-50pct', 'above_150pct'))
g = np.where(status == 'below_-50pct', -0.5, np.where(status == 'above_150pct', 1.5, g))
G['g_impl'] = g; G['g_status'] = status
G['real3'] = np.where(G.rev36.notna() & (G.rev36 > 0), (G.rev36 / G.rev_ttm) ** (1 / 3) - 1, np.nan)
G['beat'] = np.where(G.real3.notna(), (G.real3 >= G.g_impl).astype(float), np.nan)

SIZE = [0, 250e6, 1e9, 5e9, 25e9, np.inf]
GROW = [-np.inf, 0, .10, .20, .35, .60, np.inf]
G['b_size'] = pd.cut(G.rev_ttm, SIZE, labels=False)
G['b_grow'] = pd.cut(G.g_ttm, GROW, labels=False)
G['b_acc'] = (G.accel_2q.fillna(0) > 0).astype(int)
G['b_mt'] = (G.op_margin_trend.fillna(0) > 0).astype(int)
G['b_full'] = G.b_size.astype(str) + '_' + G.b_grow.astype(str) + '_' + G.b_acc.astype(str) + '_' + G.b_mt.astype(str)
G['b_coarse'] = G.b_size.astype(str) + '_' + G.b_grow.astype(str)

# ---------- rolling (strictly point-in-time) empirical base rate ----------
months = sorted(G.m.unique())
G['p_beat'] = np.nan
train_all = G[G.real3.notna()]
for m in months:
    md = pd.Timestamp(m + '-01')
    cut = md - pd.DateOffset(months=36)
    tr = train_all[train_all.mdate <= cut]
    if len(tr) < 2000:
        continue
    cur = G.index[G.m == m]
    full = {k: np.sort(v.values) for k, v in tr.groupby('b_full').real3}
    coarse = {k: np.sort(v.values) for k, v in tr.groupby('b_coarse').real3}
    out = []
    for i in cur:
        arr = full.get(G.at[i, 'b_full'])
        if arr is None or len(arr) < 30:
            arr = coarse.get(G.at[i, 'b_coarse'])
        if arr is None or len(arr) < 30:
            out.append(np.nan); continue
        out.append(1 - np.searchsorted(arr, G.at[i, 'g_impl'], side='left') / len(arr))
    G.loc[cur, 'p_beat'] = out

G['window'] = np.where(G.m < '2013-01', 'pre', np.where(G.m <= '2017-12', 'design', 'holdout'))
G['q'] = G.groupby('m').p_beat.transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False) if s.notna().sum() >= 50 else np.nan)

res = {'n_obs_gap_model': int(len(G)), 'implied_status': G.g_status.value_counts().to_dict()}

# ---------- H1 calibration ----------
H1 = {}
for w in ('design', 'holdout'):
    x = G[(G.window == w) & G.beat.notna() & G.q.notna()]
    tab = x.groupby('q').agg(pred=('p_beat', 'mean'), realized=('beat', 'mean'), n=('beat', 'size')).reset_index()
    H1[w] = tab.to_dict('records')
res['H1'] = H1


# ---------- returns: overlapping 12m cohorts from monthly prices ----------
def load_px(tk):
    p = f'{px_dir}/{tk}.json'
    j = json.load(open(p)) if os.path.exists(p) else {}
    if not j or not j.get('a'):
        return None
    idx = [date.fromtimestamp(x).strftime('%Y-%m') for x in j['t']]
    s = pd.Series(j['a'], index=idx)
    s = s[~s.index.duplicated(keep='last')].dropna()
    return s[s > 0]          # non-positive adjusted closes are vendor errors (e.g. NFE), dropped


tks = sorted(set(P.tk))
PX = {t: load_px(t) for t in tks}
allm = sorted({m for s in PX.values() if s is not None for m in s.index})
R1 = pd.DataFrame({t: s.pct_change() for t, s in PX.items() if s is not None}).reindex(allm)
R1 = R1.shift(-1)          # return over the month AFTER the index month (no look-ahead)


def cohort_portfolio(sel_by_month, hold=12):
    """Monthly return of an equal-weight portfolio holding each month's selection for `hold` months.
    A delisted stock's missing months count as 0% after its last price (no survivorship gain, no recovery)."""
    ms = sorted(sel_by_month)
    rets = {}
    for i, m in enumerate(allm):
        live = [sel_by_month[c] for c in ms if c <= m and allm.index(m) - allm.index(c) < hold and c in allm]
        vals = []
        for names in live:
            r = R1.loc[m, [n for n in names if n in R1.columns]] if names else pd.Series(dtype=float)
            if len(r):
                vals.append(r.fillna(0).mean())
        if vals:
            rets[m] = float(np.mean(vals))
    return pd.Series(rets).sort_index()


def stats(r, start, end):
    r = r[(r.index >= start) & (r.index <= end)]
    if len(r) < 12:
        return None
    cagr = (1 + r).prod() ** (12 / len(r)) - 1
    eq = (1 + r).cumprod(); dd = float((eq / eq.cummax() - 1).min())
    return {'cagr': float(cagr), 'max_dd': dd, 'months': int(len(r))}


def excess_t(a, b, start, end):
    x = (a - b).dropna(); x = x[(x.index >= start) & (x.index <= end)]
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 12 else None, float(x.mean() * 12) if len(x) else None


top = {m: list(G[(G.m == m) & (G.q == 4)].tk) for m in G.m.unique()}
univ = {m: list(P[(P.m == m) & ~P.fin.astype(bool)].tk) for m in P.m.unique()}
r_top, r_univ = cohort_portfolio(top), cohort_portfolio(univ)
spy = PX.get('SPY') if 'SPY' in PX else load_px('SPY')
r_spy = spy.pct_change().shift(-1) if spy is not None else None
H2 = {}
for w, (a, b) in {'design': ('2013-01', '2017-12'), 'holdout': ('2018-01', '2025-08')}.items():
    t, ex = excess_t(r_top, r_univ, a, b)
    H2[w] = {'top_quintile': stats(r_top, a, b), 'universe': stats(r_univ, a, b),
             'spy': stats(r_spy, a, b) if r_spy is not None else None, 'excess_vs_universe_per_yr': ex, 't_stat': t}
res['H2'] = H2

# ---------- H3: interaction with earnings momentum + near high (approximation of the 10/12 signal) ----------
P['surge_rank'] = P[P.new_q.astype(bool)].groupby('m').surge.rank(pct=True)
P['sue_rank'] = P[P.new_q.astype(bool)].groupby('m').sue.rank(pct=True)
mom = P[(P.surge_rank >= 0.9) & (P.sue_rank >= 0.5) & (P.near_high >= 0.85)]
pb = G.set_index(['tk', 'm']).p_beat
qq = G.set_index(['tk', 'm']).q
mom = mom.assign(q=[qq.get((a, b), np.nan) for a, b in zip(mom.tk, mom.m)])
sel_a = {m: list(x.tk) for m, x in mom.groupby('m')}
sel_b = {m: list(x[x.q >= 3].tk) for m, x in mom.groupby('m')}
r_a, r_b = cohort_portfolio(sel_a, hold=3), cohort_portfolio(sel_b, hold=3)
H3 = {}
for w, (a, b) in {'design': ('2013-01', '2017-12'), 'holdout': ('2018-01', '2025-08')}.items():
    halved = lambda d: float((P.set_index(['tk', 'm']).r12.reindex(d).dropna() <= -0.5).mean()) if len(d) else None
    keys_a = [(t, m) for m, ts in sel_a.items() if a <= m <= b for t in ts]
    keys_b = [(t, m) for m, ts in sel_b.items() if a <= m <= b for t in ts]
    H3[w] = {'momentum_near_high': stats(r_a, a, b), 'plus_pbeat_top2': stats(r_b, a, b),
             'n_a': len(keys_a), 'n_b': len(keys_b), 'halved12_a': halved(keys_a), 'halved12_b': halved(keys_b)}
res['H3'] = H3

# ---------- stage tables (all eligible incl. financials for stage; gap fields where available) ----------
S = P.merge(G[['tk', 'm', 'g_impl', 'p_beat']], on=['tk', 'm'], how='left')
S['grp'] = S.stage
S.loc[(S.g_impl >= 0.30) & (S.p_beat < 0.15), 'grp'] = 'priced_for_perfection'
ST = {}
for w in ('design', 'holdout'):
    x = S[(S.m >= ('2013-01' if w == 'design' else '2018-01')) & (S.m <= ('2017-12' if w == 'design' else '2022-09'))]
    rows = {}
    for grp, y in x.groupby('grp'):
        rows[grp] = {'n': int(len(y)), 'median_r36': float(y.r36.median()) if y.r36.notna().any() else None,
                     'mean_r36': float(y.r36.mean()) if y.r36.notna().any() else None,
                     'pct_halved_12m': float((y.r12 <= -0.5).mean()), 'pct_tripled_36m': float((y.r36 >= 2.0).mean()),
                     'pct_r36_missing': float(y.r36.isna().mean())}
        yy = y.dropna(subset=['r36']).sort_values('r36')
        rows[grp]['worst'] = [f"{a} {b} {c:+.0%}" for a, b, c in yy[['tk', 'm', 'r36']].drop_duplicates('tk').head(5).values]
        rows[grp]['best'] = [f"{a} {b} {c:+.0%}" for a, b, c in yy[['tk', 'm', 'r36']].drop_duplicates('tk', keep='last').tail(5).values]
    ST[w] = rows
res['stages'] = ST
res['coverage'] = {'panel_rows_eligible': int(len(P)), 'companies': int(P.cik.nunique()),
                   'r36_missing_share_holdout_2018_2022': float(S[(S.m >= '2018-01') & (S.m <= '2022-09')].r36.isna().mean())}
json.dump(res, open(f'{out_dir}/gap_backtest_results.json', 'w'), indent=1, default=lambda o: o if not isinstance(o, (np.floating, np.integer)) else o.item())
G.to_pickle(f'{out_dir}/gap_panel.pkl')
# live base-rate table: every completed 3y outcome known today (legitimate for live use; not used in the backtest above)
done = G[G.real3.notna()]
json.dump({'as_of_outcomes_through': str(done.m.max()), 'n': int(len(done)),
           'full': {k: [float(x) for x in v.values] for k, v in done.groupby('b_full').real3},
           'coarse': {k: [float(x) for x in v.values] for k, v in done.groupby('b_coarse').real3}},
          open(f'{out_dir}/base_rate_table.json', 'w'))

print(json.dumps({k: res[k] for k in ('n_obs_gap_model', 'implied_status', 'coverage')}, indent=1, default=str))
for w in ('design', 'holdout'):
    print('\nH1', w); print(pd.DataFrame(H1[w]).round(3).to_string(index=False))
    print('H2', w, json.dumps(H2[w], default=str))
    print('H3', w, json.dumps(H3[w], default=str))
