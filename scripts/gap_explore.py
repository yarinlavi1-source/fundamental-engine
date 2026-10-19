"""EXPLORATORY (not pre-registered) follow-ups to gap_backtest. Results here cannot confirm anything;
anything promising must be pre-registered and tested forward through the ledger.

usage: python scripts/gap_explore.py out_dir/gap_panel.pkl px_dir out_dir
"""
import json, os, sys
from datetime import date
import numpy as np, pandas as pd

gp, px_dir, out_dir = sys.argv[1:4]
G = pd.read_pickle(gp)


def load_px(tk):
    p = f'{px_dir}/{tk}.json'
    j = json.load(open(p)) if os.path.exists(p) else {}
    if not j or not j.get('a'):
        return None
    idx = [date.fromtimestamp(x).strftime('%Y-%m') for x in j['t']]
    s = pd.Series(j['a'], index=idx)
    s = s[~s.index.duplicated(keep='last')].dropna()
    return s[s > 0]          # non-positive adjusted closes are vendor errors (e.g. NFE), dropped


# bucket median realized growth, rolling point-in-time (same rule as p_beat)
G['exp_g'] = np.nan
train = G[G.real3.notna()]
for m in sorted(G.m.unique()):
    cut = pd.Timestamp(m + '-01') - pd.DateOffset(months=36)
    tr = train[train.mdate <= cut]
    if len(tr) < 2000: continue
    med_f = tr.groupby('b_full').real3.agg(['median', 'size']); med_c = tr.groupby('b_coarse').real3.median()
    idx = G.index[G.m == m]
    bf = G.loc[idx, 'b_full'].map(lambda b: med_f.loc[b, 'median'] if b in med_f.index and med_f.loc[b, 'size'] >= 30 else np.nan)
    G.loc[idx, 'exp_g'] = bf.fillna(G.loc[idx, 'b_coarse'].map(med_c))
G['gap'] = G.exp_g - G.g_impl
G['gq'] = G.groupby('m').gap.transform(lambda s: pd.qcut(s.rank(method='first'), 5, labels=False) if s.notna().sum() >= 50 else np.nan)

PX = {t: load_px(t) for t in G.tk.unique()}
R12 = {}
for (t, m) in zip(G.tk, G.m):
    s = PX.get(t)
    if s is None or m not in s.index: continue
    i = s.index.get_loc(m)
    R12[(t, m)] = s.iloc[i + 12] / s.iloc[i] - 1 if i + 12 < len(s) else np.nan
G['fr12'] = [R12.get((t, m), np.nan) for t, m in zip(G.tk, G.m)]
G['window'] = np.where(G.m <= '2017-12', 'design', 'holdout')
out = {}
for w in ('design', 'holdout'):
    x = G[(G.window == w) & G.gq.notna()]
    mean_by_month = x.groupby(['m', 'gq']).fr12.mean().unstack()
    tab = pd.DataFrame({'mean_fwd12_avg_of_months': mean_by_month.mean(), 'beat_rate': x.groupby('gq').beat.mean(),
                        'median_gap': x.groupby('gq').gap.median(), 'pct_halved': x.groupby('gq').fr12.apply(lambda s: (s <= -0.5).mean())})
    spread = (mean_by_month[4] - mean_by_month[0]).dropna()
    out[w] = {'by_gap_quintile': tab.round(4).reset_index().to_dict('records'),
              'top_minus_bottom_mean_12m': float(spread.mean()), 'months': int(len(spread)),
              'share_months_top_beats_bottom': float((spread > 0).mean())}
    char = x[x.gq == 4].groupby('growth_character').fr12.agg(['mean', 'median', 'size'])
    out[w]['top_gap_by_growth_character'] = char.round(4).reset_index().to_dict('records')
json.dump(out, open(f'{out_dir}/gap_explore.json', 'w'), indent=1, default=float)
for w in out:
    print('\n', w, 'top-bottom 12m', round(out[w]['top_minus_bottom_mean_12m'], 4), 'share months', round(out[w]['share_months_top_beats_bottom'], 2))
    print(pd.DataFrame(out[w]['by_gap_quintile']).to_string(index=False))
    print(pd.DataFrame(out[w]['top_gap_by_growth_character']).to_string(index=False))
