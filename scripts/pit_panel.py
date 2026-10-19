"""Monthly point-in-time panel: features, market values, implied growth inputs and forward outcomes.

usage: python scripts/pit_panel.py pit.pkl px_dir out_panel.pkl [max_companies]
Only facts usable at month-end t (filed + 2 days) enter features. Outcomes (forward returns,
realized 3y revenue) are stored in separate columns and are never used to compute features.
"""
import json, os, pickle, sys, time
from datetime import date, timedelta
import numpy as np, pandas as pd
from fundamental_engine.inflection import features
from fundamental_engine.pit import latest_at, ttm_at, available, shares_at

pit_path, px_dir, out = sys.argv[1:4]
limit = int(sys.argv[4]) if len(sys.argv) > 4 else None
store = pickle.load(open(pit_path, 'rb'))


def month_end(y, m):
    return (date(y + (m // 12), m % 12 + 1, 1) - timedelta(days=1))


MONTHS = [month_end(y, m) for y in range(2010, 2026) for m in range(1, 13) if month_end(y, m) <= date(2025, 9, 30)]
MKEY = [d.strftime('%Y-%m') for d in MONTHS]


def load_px(tk):
    p = f'{px_dir}/{tk}.json'
    if not os.path.exists(p):
        return None
    j = json.load(open(p))
    if not j or not j.get('t') or not j.get('a'):
        return None
    idx = [date.fromtimestamp(x).strftime('%Y-%m') for x in j['t']]
    df = pd.DataFrame({'c': j['c'], 'a': j['a']}, index=idx)
    df = df[~df.index.duplicated(keep='last')].dropna()
    df = df[(df.a > 0) & (df.c > 0)]          # non-positive closes are vendor errors
    splits = sorted((date.fromtimestamp(int(k)), n / d) for k, (n, d) in (j.get('splits') or {}).items() if d)
    return df, splits


def split_factor_after(splits, d):
    f = 1.0
    for when, ratio in splits:
        if when > d:
            f *= ratio
    return f


rows, t0, nco = [], time.time(), 0
items = list(store.items())[:limit] if limit else list(store.items())
for cik, rec in items:
    tks = [t for t, ex in rec.get('tickers', []) if ex in ('Nasdaq', 'NYSE')]
    px = None
    for tk in tks:
        px = load_px(tk)
        if px is not None:
            break
    if px is None:
        continue
    px_tk = tk          # the ticker whose prices are used (bug fix 19.10.2026: rows were labelled tks[0])
    nco += 1
    df, splits = px
    a = df['a']; c = df['c']
    for t, mk in zip(MONTHS, MKEY):
        if mk not in df.index:
            continue
        f = features(rec, t)
        if f is None:
            continue
        raw = float(c[mk]) * split_factor_after(splits, t)
        sd, sh, sh_src = shares_at(rec, t)
        if sh is None:
            continue
        sh_date = date.fromisoformat(sd)
        # cover-page shares are as of sh_date: apply splits that happened between sh_date and t
        sh *= split_factor_after(splits, sh_date) / split_factor_after(splits, t)
        mcap = sh * raw
        cash = (latest_at(rec['cash'], t, max_age_days=200) or 0) + (latest_at(rec['sti'], t, max_age_days=200) or 0)
        debt = (latest_at(rec['ltd'], t, max_age_days=200) or 0) + (latest_at(rec['ltd_cur'], t, max_age_days=200) or 0)
        best_m = None
        for off in range(0, 9):
            oi, _ = ttm_at(rec['oi'], t, offset=off); rv, _ = ttm_at(rec['rev'], t, offset=off)
            if oi is not None and rv:
                best_m = oi / rv if best_m is None else max(best_m, oi / rv)
        i = MKEY.index(mk)
        def fwd(k):
            j = i + k
            if j < len(MKEY) and MKEY[j] in a.index:
                return float(a[MKEY[j]] / a[mk] - 1)
            return np.nan
        win = [MKEY[j] for j in range(max(0, i - 11), i + 1) if MKEY[j] in c.index]
        hi12 = float(c[win].max()) if win else np.nan
        # surprise inputs: newest revenue / NI quarter and whether it became available this month
        rq = sorted(available(rec['rev'], t)); nq = sorted(available(rec['ni'], t))
        new_this_month = bool(rq) and (t - timedelta(days=31)) < date.fromisoformat(rec['rev'][rq[-1][0]][1]) + timedelta(days=2) <= t
        def surprise(q):
            if len(q) < 12: return np.nan
            v = [x for _, x in q]
            d = [v[k] - v[k - 4] for k in range(len(v) - 9, len(v))]
            sd = np.std(d[:-1], ddof=1)
            return d[-1] / sd if sd > 0 else np.nan
        # realized revenue TTM 36 months later (outcome only)
        t36 = MONTHS[i + 36] if i + 36 < len(MONTHS) else None
        rv36 = ttm_at(rec['rev'], t36)[0] if t36 else None
        rows.append(dict(cik=cik, tk=px_tk, m=mk, fin=rec['financial'], sh_src=sh_src, mcap=mcap, cash=cash, debt=debt, best_m=best_m,
                         r12=fwd(12), r36=fwd(36), r1=fwd(1), r3=fwd(3), px=float(c[mk]), hi12=hi12, near_high=float(c[mk]) / hi12 if hi12 else np.nan,
                         new_q=new_this_month, surge=surprise(rq), sue=surprise(nq), rev36=rv36,
                         **{k: f.get(k) for k in ('rev_ttm', 'g_ttm', 'yoy_q', 'accel_2q', 'accel_streak', 'op_margin', 'op_margin_1y', 'op_margin_trend',
                                                 'incremental_op_margin', 'gross_margin', 'fcf_margin', 'fcf_margin_1y', 'capex_to_revenue',
                                                 'sbc_to_revenue', 'dilution_yoy', 'acquisition_driven_risk', 'goodwill_jump_to_rev', 'stage',
                                                 'growth_character', 'period_end')}))
    if nco % 250 == 0:
        print(nco, len(rows), round(time.time() - t0), flush=True)
panel = pd.DataFrame(rows)
panel.to_pickle(out)
print('companies with prices', nco, 'rows', len(panel), 'seconds', round(time.time() - t0), flush=True)
