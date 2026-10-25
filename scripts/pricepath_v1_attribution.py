"""Post-run diagnostic of the spec-1 expected price path lane (docs/PRICE_PATH_2026-10.md; docs/prereg_price_path.md section 9).
Read-only on the saved lane rows (scripts/backtest_pricepath.py output, run 21.10.2026), no fundamental_engine imports,
deterministic. Not part of the preregistered evaluation; it changes nothing in spec 1.

Ex-post attribution of the lane's base-path error into revenue, margin, share-count and multiple terms, using the lane's
OWN rows at the later formation (t + h) as the realized fundamentals (TTM net income, revenue, cover share count and the traded
price at 30 June of year t + h). No engine imports; own Yahoo-JSON parsing as recompute.py. Diagnostic for the v2
preregistration; it changes nothing in spec 1. Usage: scripts/pricepath_v1_attribution.py <rows.jsonl> <engine rows.jsonl> <prices dir>
Identity (t-share units): log(P_lane / P_actual) = e_rev + e_margin + e_shares + e_multiple, where
  e_rev = log(Rev_lane_h / Rev_real_h), e_margin = log(m_lane_h / m_real_h), e_shares = log(S_real_h / S_lane_h),
  e_multiple = log(M_lane_h / M_real_h), M_real_h = market cap(t+h) / TTM NI(t+h); the split factor between t and t+h is
  f = R x P_0 / P_row(t+h) (R = split-adjusted realized ratio), so S_real_h in t units = S_row(t+h) / f."""
import json, math, os, statistics, sys
from datetime import datetime, timezone
ROWS, ENG, PR = sys.argv[1:4]
FY = {1: (2016, 2025), 3: (2016, 2023), 5: (2016, 2021)}

def months(sym):
    path = os.path.join(PR, sym + '.json')
    if not os.path.exists(path):
        return None
    d = json.load(open(path, encoding='utf-8'))
    r = ((d.get('chart') or {}).get('result') or [None])[0]
    if not r or not r.get('timestamp'):
        return None
    q = (r.get('indicators') or {}).get('quote', [{}])[0]
    return {datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m'): c for ts, c in zip(r['timestamp'], q.get('close') or []) if c is not None}

def at(m, ym):
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
ok = {(r['symbol'], int(r['formation'][:4])): r for r in rows if r.get('status') == 'ok'}
irr = {}
for l in open(ENG, encoding='utf-8'):
    r = json.loads(l)
    if r.get('base_irr') is not None:
        irr[(r['symbol'], r['formation'])] = r['base_irr']
cache = {}
med = statistics.median
def q(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(p * len(xs)))]
BUCK = ((10, '<10x'), (15, '10-15x'), (20, '15-20x'), (30, '20-30x'), (60, '30-60x'), (1e9, '>60x'))
def bucket(m):
    for lim, lab in BUCK:
        if m < lim: return lab
GB = ((0, '<0%'), (.05, '0-5%'), (.10, '5-10%'), (.20, '10-20%'), (.30, '20-30%'), (9, '>30%'))
def gbucket(g):
    for lim, lab in GB:
        if g < lim: return lab
for h in (1, 3, 5):
    y0, y1 = FY[h]
    recs, n_common, n_no_later = [], 0, 0
    for (sym, y), r in ok.items():
        if not (y0 <= y <= y1):
            continue
        if (sym, r['formation']) not in irr or r.get('eps_cagr_3y') is None:
            continue
        if sym not in cache:
            cache[sym] = months(sym)
        m = cache[sym]
        if not m:
            continue
        a = at(m, r['as_of'][:7]); b = at(m, f"{y + h:04d}-{r['as_of'][5:7]}")
        if not a or not b or a <= 0 or b <= 0:
            continue
        n_common += 1
        R = b / a
        r2 = ok.get((sym, y + h))
        if not r2:
            n_no_later += 1
            continue
        an2 = r2['anchors']
        f = R * r['price'] / r2['price']
        NI, S, Rev, P2 = an2['net_income'], an2['shares'], an2['revenue'], r2['price']
        if NI <= 0 or S <= 0 or Rev <= 0 or P2 <= 0 or r['multiple_today'] <= 0:
            continue
        bb = r['scenarios']['base']['years'][h - 1]
        an = r['anchors']
        actual = r['price'] * R
        e_tot = math.log(bb['price'] / actual)
        e_rev = math.log(bb['revenue'] / Rev)
        e_m = math.log(bb['margin'] / (NI / Rev))
        e_sh = math.log((S / f) / bb['shares'])
        M_real = P2 * S / NI
        e_mult = math.log(bb['multiple'] / M_real)
        M0_raw = r['price'] * an['shares'] / an['net_income']       # raw trailing P/E on reported TTM NI
        recs.append({'sym': sym, 'y': y, 'e_tot': e_tot, 'e_rev': e_rev, 'e_m': e_m, 'e_sh': e_sh, 'e_mult': e_mult,
                     'resid': e_tot - (e_rev + e_m + e_sh + e_mult), 'R': R,
                     'rev_real': Rev / an['revenue'], 'rev_lane': bb['revenue'] / an['revenue'],
                     'ni_real': NI / an['net_income'], 'ni_lane': bb['net_income'] / (an['revenue'] * an['m_anchor']),
                     'm_real': NI / Rev, 'm_lane': bb['margin'], 'm_ttm0': an['m_ttm'], 'm_anchor': an['m_anchor'],
                     'M_real': M_real, 'M_lane': bb['multiple'], 'M0_raw': M0_raw, 'M0_norm': r['multiple_today'], 'just_h': bb['justified_multiple'],
                     'bucket': bucket(r['multiple_today']), 'gb': gbucket(an['g_run']), 'cyc': r['cyclical'], 'stage': r['stage'],
                     'bear_e': math.log(r['scenarios']['bear']['years'][h - 1]['price'] / actual),
                     'bull_e': math.log(r['scenarios']['bull']['years'][h - 1]['price'] / actual)})
    n = len(recs)
    print(f'\n== h={h}: common {n_common}; no ok lane row at t+h (NI<=0 / no shares / not covered) {n_no_later}; attributed n={n} '
          f'(lane bias on this subsample {med(x["e_tot"] for x in recs):+.3f}; identity residual max {max(abs(x["resid"]) for x in recs):.1e})')
    print('median terms: ' + '  '.join(f'{k} {med(x[k] for x in recs):+.3f}' for k in ('e_tot', 'e_rev', 'e_m', 'e_sh', 'e_mult')))
    print('mean terms:   ' + '  '.join(f'{k} {sum(x[k] for x in recs)/n:+.3f}' for k in ('e_tot', 'e_rev', 'e_m', 'e_sh', 'e_mult')))
    print(f"median realized: revenue x{med(x['rev_real'] for x in recs):.3f} (lane x{med(x['rev_lane'] for x in recs):.3f}); "
          f"net income x{med(x['ni_real'] for x in recs):.3f} (lane, on normalized NI_0, x{med(x['ni_lane'] for x in recs):.3f}); "
          f"net margin {med(x['m_real'] for x in recs):.3f} vs TTM_0 {med(x['m_ttm0'] for x in recs):.3f}, anchor {med(x['m_anchor'] for x in recs):.3f}, lane year h {med(x['m_lane'] for x in recs):.3f}; "
          f"P/E raw t+h {med(x['M_real'] for x in recs):.1f}x vs raw t {med(x['M0_raw'] for x in recs):.1f}x (lane M_h {med(x['M_lane'] for x in recs):.1f}x, justified {med(x['just_h'] for x in recs):.1f}x); "
          f"median log(M_real_h / M0_raw) {med(math.log(x['M_real']/x['M0_raw']) for x in recs):+.3f}, log(M_lane_h / M0_norm) {med(math.log(x['M_lane']/x['M0_norm']) for x in recs):+.3f}")
    print('share of rows where |e_mult| > |e_rev + e_m + e_sh|: %.0f%%' % (100 * sum(1 for x in recs if abs(x['e_mult']) > abs(x['e_rev'] + x['e_m'] + x['e_sh'])) / n))
    print('by M_0 (normalized) bucket: n | median e_tot | e_rev | e_m | e_sh | e_mult | realized log(M_h/M0_raw) | raw M0 | raw M_h')
    for lab in [b for _, b in BUCK]:
        sub = [x for x in recs if x['bucket'] == lab]
        if len(sub) < 10: continue
        print(f"  {lab:>7s} {len(sub):5d} | {med(x['e_tot'] for x in sub):+.2f} | {med(x['e_rev'] for x in sub):+.2f} | {med(x['e_m'] for x in sub):+.2f} | "
              f"{med(x['e_sh'] for x in sub):+.2f} | {med(x['e_mult'] for x in sub):+.2f} | {med(math.log(x['M_real']/x['M0_raw']) for x in sub):+.2f} | "
              f"{med(x['M0_raw'] for x in sub):.1f}x | {med(x['M_real'] for x in sub):.1f}x")
    print('by run-rate growth bucket: n | e_tot | e_rev | e_m | e_mult | realized rev ratio | lane rev ratio | raw M0 | raw M_h')
    for lab in [b for _, b in GB]:
        sub = [x for x in recs if x['gb'] == lab]
        if len(sub) < 10: continue
        print(f"  {lab:>7s} {len(sub):5d} | {med(x['e_tot'] for x in sub):+.2f} | {med(x['e_rev'] for x in sub):+.2f} | {med(x['e_m'] for x in sub):+.2f} | "
              f"{med(x['e_mult'] for x in sub):+.2f} | x{med(x['rev_real'] for x in sub):.2f} | x{med(x['rev_lane'] for x in sub):.2f} | {med(x['M0_raw'] for x in sub):.1f}x | {med(x['M_real'] for x in sub):.1f}x")
    for key, lab in (('cyc', 'cyclical'), ('stage', 'stage')):
        print(f'by {lab}: ' + '; '.join(f"{g}: n {len(s)} e_tot {med(x['e_tot'] for x in s):+.2f} rev {med(x['e_rev'] for x in s):+.2f} m {med(x['e_m'] for x in s):+.2f} mult {med(x['e_mult'] for x in s):+.2f}"
                                       for g, s in sorted(((g, [x for x in recs if x[key] == g]) for g in {x[key] for x in recs}), key=lambda t: str(t[0])) if len(s) >= 10))
    print('spread of terms (p10 / p90): ' + '  '.join(f"{k} {q([x[k] for x in recs], .1):+.2f}/{q([x[k] for x in recs], .9):+.2f}" for k in ('e_rev', 'e_m', 'e_sh', 'e_mult')))
    print(f"realized distribution of log price ratio: p10 {q([math.log(x['R']) for x in recs], .1):+.2f} p25 {q([math.log(x['R']) for x in recs], .25):+.2f} "
          f"median {q([math.log(x['R']) for x in recs], .5):+.2f} p75 {q([math.log(x['R']) for x in recs], .75):+.2f} p90 {q([math.log(x['R']) for x in recs], .9):+.2f}; "
          f"realized revenue ratio p10 {q([x['rev_real'] for x in recs], .1):.2f} p25 {q([x['rev_real'] for x in recs], .25):.2f} p75 {q([x['rev_real'] for x in recs], .75):.2f}; "
          f"realized NI ratio p10 {q([x['ni_real'] for x in recs], .1):.2f} p25 {q([x['ni_real'] for x in recs], .25):.2f} p75 {q([x['ni_real'] for x in recs], .75):.2f}; "
          f"realized raw P/E p10 {q([x['M_real'] for x in recs], .1):.1f} p25 {q([x['M_real'] for x in recs], .25):.1f} p75 {q([x['M_real'] for x in recs], .75):.1f} p90 {q([x['M_real'] for x in recs], .9):.1f}")
    print(f"bear / bull median e on this subsample: {med(x['bear_e'] for x in recs):+.3f} / {med(x['bull_e'] for x in recs):+.3f}; "
          f"share of rows with realized NI ratio below the lane's bear NI path: n/a (bear NI not stored per row here)")
