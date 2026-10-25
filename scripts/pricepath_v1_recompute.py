"""Post-run diagnostic of the spec-1 expected price path lane (docs/PRICE_PATH_2026-10.md; docs/prereg_price_path.md section 9).
Read-only on the saved lane rows (scripts/backtest_pricepath.py output, run 21.10.2026), no fundamental_engine imports,
deterministic. Not part of the preregistered evaluation; it changes nothing in spec 1.

Independent recomputation of the preregistered headline metrics (section 6.5) from the lane rows, written without
importing fundamental_engine: own Yahoo-JSON parsing (split-adjusted 'close', no dividends), the common-sample rule of 6.3,
the predictors of 6.4. Usage: scripts/pricepath_v1_recompute.py <rows.jsonl> <engine rows.jsonl> <prices dir>"""
import json, math, os, statistics, sys
from datetime import datetime, timezone
ROWS, ENG, PR = sys.argv[1:4]
WITHIN = math.log(1.25)
FY = {1: (2016, 2025), 3: (2016, 2023), 5: (2016, 2021)}
NAMES = ('lane_base', 'random_walk', 'drift_7', 'analyst_const', 'engine_irr', 'lane_bear', 'lane_bull')

def months(sym):
    path = os.path.join(PR, sym + '.json')
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding='utf-8'))
    r = ((d.get('chart') or {}).get('result') or [None])[0]
    if not r or not r.get('timestamp'):
        return None
    q = (r.get('indicators') or {}).get('quote', [{}])[0]
    out = {}
    for ts, c in zip(r['timestamp'], q.get('close') or []):
        if c is None:
            continue
        out[datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m')] = c
    return out

def at(m, ym):
    """close for month ym, else the latest earlier month within two months (backtest._at's rule)."""
    y, mo = int(ym[:4]), int(ym[5:7])
    for k in range(3):
        yy, mm = y, mo - k
        while mm <= 0:
            mm += 12; yy -= 1
        key = f'{yy:04d}-{mm:02d}'
        if key in m:
            return m[key]
    return None

rows = [json.loads(l) for l in open(ROWS, encoding='utf-8')]
ok = [r for r in rows if r.get('status') == 'ok']
badM = [r['symbol'] for r in ok if not r['multiple_today'] or r['multiple_today'] <= 0 or not r['eps_normalized']]
print('ok rows with M_0 or EPS_0 <= 0 (left out of the ratio medians only):', len(badM), sorted(set(badM)))
print(f'rows {len(rows)}  status ok {len(ok)}  excluded {sum(1 for r in rows if r.get("status")=="excluded")}  skipped {sum(1 for r in rows if r.get("status")=="skipped")}')
irr = {}
for l in open(ENG, encoding='utf-8'):
    r = json.loads(l)
    if r.get('base_irr') is not None:
        irr[(r['symbol'], r['formation'])] = r['base_irr']
cache = {}
def stats(es):
    n = len(es)
    return (n, statistics.median(es), sum(es) / n, statistics.median(abs(e) for e in es),
            sum(1 for e in es if abs(e) <= WITHIN) / n, sum(1 for e in es if e > 0) / n)
for h in (1, 3, 5):
    y0, y1 = FY[h]
    recs, nlane, Rlane = [], 0, []
    for r in ok:
        if not (y0 <= int(r['formation'][:4]) <= y1):
            continue
        s = r['symbol']
        if s not in cache:
            cache[s] = months(s)
        m = cache[s]
        if not m:
            continue
        a = at(m, r['as_of'][:7])
        b = at(m, f"{int(r['as_of'][:4]) + h:04d}-{r['as_of'][5:7]}")
        if not a or not b or a <= 0 or b <= 0:
            continue
        R = b / a
        nlane += 1; Rlane.append(R)
        key = (s, r['formation'])
        if key not in irr or r.get('eps_cagr_3y') is None:
            continue
        p0 = r['price']; g = max(-.10, min(.30, r['eps_cagr_3y'])); dy = r.get('dividend_yield') or 0.
        sc = r['scenarios']
        preds = {'lane_base': sc['base']['years'][h - 1]['price'], 'lane_bear': sc['bear']['years'][h - 1]['price'],
                 'lane_bull': sc['bull']['years'][h - 1]['price'], 'random_walk': p0, 'drift_7': p0 * 1.07 ** h,
                 'analyst_const': p0 * (1 + g) ** h, 'engine_irr': p0 * max(0., 1 + irr[key] - dy) ** h}
        recs.append((preds, p0 * R, R, r['formation'][:4]))
    print(f'\n== h={h}  lane-only n={nlane} (median realized ratio {statistics.median(Rlane):.3f})  common n={len(recs)} '
          f'(median realized ratio {statistics.median(R for _,_,R,_ in recs):.3f})')
    print(f"{'predictor':14s} {'n':>5s} {'med e':>7s} {'mean e':>7s} {'MdALE':>6s} {'w25':>5s} {'above':>5s}")
    E = {}
    for nm in NAMES:
        es = [math.log(p[nm] / a) for p, a, _, _ in recs if p[nm] > 0 and a > 0]
        E[nm] = es
        n, md, mn, mdale, w25, ab = stats(es)
        print(f'{nm:14s} {n:5d} {md:+7.3f} {mn:+7.3f} {mdale:6.3f} {w25*100:4.0f}% {ab*100:4.0f}%')
    n = len(recs)
    inb = sum(1 for p, a, _, _ in recs if p['lane_bear'] <= a <= p['lane_bull']) / n
    below = sum(1 for p, a, _, _ in recs if a < p['lane_bear']) / n
    above = sum(1 for p, a, _, _ in recs if a > p['lane_bull']) / n
    print(f'band: inside {inb*100:.1f}%  below bear {below*100:.1f}%  above bull {above*100:.1f}%')
    h2h = {b: sum(1 for x, y in zip(E['lane_base'], E[b]) if abs(x) < abs(y)) / n for b in ('random_walk', 'drift_7', 'analyst_const', 'engine_irr')}
    print('head-to-head (lane |e| smaller): ' + ', '.join(f'{k} {v*100:.0f}%' for k, v in h2h.items()))
    if h == 3:
        print('by formation year (median e, lane_base / random_walk / drift_7 / analyst_const / engine_irr; n):')
        for yr in sorted({f for _, _, _, f in recs}):
            sub = [(p, a) for p, a, _, f in recs if f == yr]
            vals = [statistics.median(math.log(p[nm] / a) for p, a in sub) for nm in ('lane_base', 'random_walk', 'drift_7', 'analyst_const', 'engine_irr')]
            print(f'  {yr}: ' + ' / '.join(f'{v:+.2f}' for v in vals) + f'  (n {len(sub)})')
ok = [r for r in ok if r['multiple_today'] and r['multiple_today'] > 0 and r['eps_normalized']]
print('\n== decomposition: median base path over all ok rows (ratio to today), n =', len(ok))
print(f"{'year':4s} {'growth':>7s} {'margin':>7s} {'EPS_h/EPS_0':>11s} {'justified':>9s} {'premium':>8s} {'multiple':>8s} {'M_h/M_0':>8s} {'P_h/P_0':>8s}")
for y in range(1, 6):
    b = [r['scenarios']['base']['years'][y - 1] for r in ok]
    g = statistics.median(x['growth'] for x in b); mg = statistics.median(x['margin'] for x in b)
    eps = statistics.median(x['eps'] / r['eps_normalized'] for x, r in zip(b, ok))
    just = statistics.median(x['justified_multiple'] for x in b)
    prem = statistics.median(x['multiple'] - x['justified_multiple'] for x in b)
    mult = statistics.median(x['multiple'] for x in b)
    mr = statistics.median(x['multiple'] / r['multiple_today'] for x, r in zip(b, ok))
    pr = statistics.median(x['price'] / r['price'] for x, r in zip(b, ok))
    print(f'{y:4d} {g*100:+6.1f}% {mg*100:6.1f}% {eps:11.3f} {just:8.1f}x {prem:7.1f}x {mult:7.1f}x {mr:8.3f} {pr:8.3f}')
bull = [r['scenarios']['bull']['years'][4] for r in ok]
print('bull year 5 medians: EPS ratio %.2f, multiple %.1fx, P5/P0 %.2f' % (
    statistics.median(x['eps'] / r['eps_normalized'] for x, r in zip(bull, ok)), statistics.median(x['multiple'] for x in bull),
    statistics.median(x['price'] / r['price'] for x, r in zip(bull, ok))))
print('today: median M_0 %.1fx, median justified %.1fx, median premium/M_0 %.2f' % (
    statistics.median(r['multiple_today'] for r in ok), statistics.median(r['justified_today'] for r in ok),
    statistics.median(r['premium_today'] / r['multiple_today'] for r in ok)))
print('rows with M_0 < 1x:', sum(1 for r in ok if r['multiple_today'] < 1), sorted({r['symbol'] for r in ok if r['multiple_today'] < 1}))
