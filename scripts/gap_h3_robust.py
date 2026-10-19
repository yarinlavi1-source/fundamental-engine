"""EXPLORATORY robustness of H3 (NOT pre-registered; cannot upgrade or downgrade the pre-registered verdict).

Question: is the holdout gain of "momentum + near high + top-two P(beat) quintiles" more than what a random
subset of the same momentum sleeve, with the same number of names each month, would give?
Also: paired monthly difference t-stat on common months, and the same numbers per window.

usage: python scripts/gap_h3_robust.py panel.pkl gap_panel.pkl px_dir out.json [n_draws]
"""
import json, os, sys
from datetime import date
import numpy as np, pandas as pd

panel_path, gp_path, px_dir, out = sys.argv[1:5]
N = int(sys.argv[5]) if len(sys.argv) > 5 else 500
rng = np.random.default_rng(20261019)
P = pd.read_pickle(panel_path)
P = P[(P.rev_ttm >= 50e6) & (P.mcap >= 300e6)].copy()
G = pd.read_pickle(gp_path)
P['surge_rank'] = P[P.new_q.astype(bool)].groupby('m').surge.rank(pct=True)
P['sue_rank'] = P[P.new_q.astype(bool)].groupby('m').sue.rank(pct=True)
mom = P[(P.surge_rank >= 0.9) & (P.sue_rank >= 0.5) & (P.near_high >= 0.85)].copy()
qq = G.set_index(['tk', 'm']).q
mom['q'] = [qq.get((a, b), np.nan) for a, b in zip(mom.tk, mom.m)]
mom['in_gap_model'] = mom.q.notna()


def load_px(tk):
    p = f'{px_dir}/{tk}.json'
    j = json.load(open(p)) if os.path.exists(p) else {}
    if not j or not j.get('a'):
        return None
    idx = [date.fromtimestamp(x).strftime('%Y-%m') for x in j['t']]
    s = pd.Series(j['a'], index=idx)
    s = s[~s.index.duplicated(keep='last')].dropna()
    return s[s > 0]          # non-positive adjusted closes are vendor errors (e.g. NFE), dropped


tks = sorted(set(mom.tk))
R1 = pd.DataFrame({t: s.pct_change() for t in tks if (s := load_px(t)) is not None})
allm = sorted(R1.index); R1 = R1.reindex(allm).shift(-1)
pos = {m: i for i, m in enumerate(allm)}


def port(sel, hold=3):
    rets = {}
    for m in allm:
        vals = []
        for c, names in sel.items():
            if c in pos and 0 <= pos[m] - pos[c] < hold and names:
                vals.append(R1.loc[m, [n for n in names if n in R1.columns]].fillna(0).mean())
        if vals:
            rets[m] = float(np.mean(vals))
    return pd.Series(rets).sort_index()


def cagr(r):
    return float((1 + r).prod() ** (12 / len(r)) - 1) if len(r) >= 12 else None


res = {}
for w, (a, b) in {'design': ('2013-01', '2017-12'), 'holdout': ('2018-01', '2025-08')}.items():
    x = mom[(mom.m >= a) & (mom.m <= b)]
    base = {m: list(g.tk) for m, g in x.groupby('m')}
    # fair base: only names the gap model could score (non-financial, EV>0, margin known)
    base_scored = {m: list(g[g.in_gap_model].tk) for m, g in x.groupby('m')}
    filt = {m: list(g[g.q >= 3].tk) for m, g in x.groupby('m')}
    rb, rs, rf = port(base), port(base_scored), port(filt)
    rb, rs, rf = (s[(s.index >= a) & (s.index <= b)] for s in (rb, rs, rf))
    common = rf.index.intersection(rs.index)
    d = rf[common] - rs[common]
    draws = []
    for _ in range(N):
        sel = {}
        for m, names in filt.items():
            pool = base_scored.get(m, [])
            k = len(names)
            sel[m] = list(rng.choice(pool, size=k, replace=False)) if k and len(pool) >= k else []
        r = port(sel); r = r[(r.index >= a) & (r.index <= b)]
        draws.append(cagr(r[r.index.isin(common)]))
    draws = np.array([v for v in draws if v is not None])
    f_c = cagr(rf[common])
    res[w] = {'base_all_cagr': cagr(rb), 'base_scored_cagr': cagr(rs), 'filtered_cagr': f_c,
              'common_months': int(len(common)), 'paired_mean_diff_per_yr': float(d.mean() * 12),
              'paired_t': float(d.mean() / d.std(ddof=1) * np.sqrt(len(d))) if len(d) > 12 else None,
              'random_subset_median_cagr': float(np.median(draws)), 'random_subset_p95': float(np.percentile(draws, 95)),
              'share_random_at_or_above_filtered': float((draws >= f_c).mean()), 'draws': int(len(draws)),
              'n_names_filtered': int(sum(len(v) for v in filt.values())), 'n_names_scored': int(sum(len(v) for v in base_scored.values()))}
json.dump(res, open(out, 'w'), indent=1)
print(json.dumps(res, indent=1))
