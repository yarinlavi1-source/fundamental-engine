"""Walk-forward test of the discovery lanes against the index: does the screen find stocks that beat it?

Formation every year on a fixed date (default 1 July). At formation F the screen sees annual SEC frames for the prior
calendar years only, and a frame value is used only if its period ended at least `filing_lag_days` before F (annual
reports are filed within 60-90 days), so no number is used before it could have been read. Market value at F is the
split-adjusted close times the reported share count, corrected for splits in between. Each lane's candidates are held
equal-weight for 12 months, total return (dividends included), minus a round-trip trading cost.

Benchmarks: the S&P 500 (SPY, total return) and the equal-weight universe of eligible companies built from the same data.
Survivorship bias: prices exist only for CURRENTLY listed tickers, so companies that were delisted, acquired or went
bankrupt are missing. This inflates every stock-based return (lanes and the equal-weight universe) but not SPY: the fair
comparison is lane vs equal-weight universe; lane vs SPY is shown with that warning. Hindsight returns are never presented
as proof of the method: the statistics state their uncertainty and the sample is small (one observation a year).
"""
from datetime import date, datetime, timezone
import json
import math
import os

from . import sec_frames, screen, universe

FILING_LAG_DAYS = 95


def load_prices(path):
    """Yahoo monthly chart payload -> {'months': [(yyyy-mm, close, adjclose)], 'splits': [(date, ratio)]}."""
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    r = ((d.get('chart') or {}).get('result') or [None])[0]
    if not r or not r.get('timestamp'):
        return None
    q = (r.get('indicators') or {}).get('quote', [{}])[0]
    adj = ((r.get('indicators') or {}).get('adjclose') or [{}])[0].get('adjclose') or [None] * len(r['timestamp'])
    months = []
    for ts, c, a in zip(r['timestamp'], q.get('close') or [], adj):
        if c is None or a is None:
            continue
        months.append((datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m'), c, a))
    splits = [(datetime.fromtimestamp(s['date'], timezone.utc).date().isoformat(), s['numerator'] / s['denominator'])
              for s in ((r.get('events') or {}).get('splits') or {}).values() if s.get('denominator')]
    return {'months': months, 'splits': sorted(splits)}


def _at(p, ym):
    """(close, adjclose) for the month ym, or the latest month before it within two months."""
    best = None
    for m, c, a in p['months']:
        if m <= ym:
            best = (m, c, a)
        else:
            break
    if not best:
        return None
    y0, m0 = map(int, best[0].split('-'))
    y1, m1 = map(int, ym.split('-'))
    return (best[1], best[2]) if (y1 - y0) * 12 + (m1 - m0) <= 2 else None


def _factor(p, after):
    """Product of split ratios with a date after `after` (split-adjusted price x factor = price as quoted then)."""
    f = 1.
    for d, r in p['splits']:
        if d > after:
            f *= r
    return f


def share_basis_check(merged, ticker_of, prices, listed_caps, tolerance=2.):
    """Symbols whose SEC share count and quoted price are on different bases (ADRs quoted per depositary share, units,
    class mismatches): today's implied value (latest frame shares x latest close x later splits) vs the listing's published
    market value outside 1/tolerance..tolerance. Excluded from every formation; the count is reported."""
    last = max(merged)
    bad = {}
    for cik, r in merged[last].items():
        sym = ticker_of.get(cik)
        p, sh, cap = prices.get(sym), r.get('shares_outstanding'), listed_caps.get(sym)
        if not (p and sh and cap and p['months']):
            continue
        implied = p['months'][-1][1] * sh['value'] * _factor(p, sh['end'])
        ratio = implied / cap
        if not 1 / tolerance <= ratio <= tolerance:
            bad[sym] = ratio
    return bad


def rows_at(merged, years, formation, ticker_of, prices, sectors, min_cap=1e9, lag=FILING_LAG_DAYS, exclude=None):
    """Screen rows as they could have been built at `formation` (yyyy-mm-dd). merged: {year: merge_frames output}."""
    f = date.fromisoformat(formation)
    usable_years = [y for y in years if y < f.year]
    if len(usable_years) < 2:
        return [], {}
    last = usable_years[-1]
    cut = (f - __import__('datetime').timedelta(days=lag)).isoformat()

    def fresh(rec):
        return {cik: {m: v for m, v in r.items() if m == 'name' or (isinstance(v, dict) and (v.get('end') or '') <= cut)}
                for cik, r in rec.items()}
    latest = fresh(merged[last])
    hist = [fresh(merged[y]) for y in usable_years[-4:-1]]
    ym = formation[:7]
    chosen, px = {}, {}
    for cik, r in latest.items():
        sym = ticker_of.get(cik)
        p = prices.get(sym) if sym and sym not in (exclude or ()) else None
        if not p or not (r.get('revenue') or {}).get('value') or not r.get('shares_outstanding'):
            continue
        at = _at(p, ym)
        if not at:
            continue
        sh = r['shares_outstanding']
        # split-adjusted close x reported shares x the splits after the share-count date (a 2:1 split after the count doubles the
        # shares; one after formation is already in the adjusted price). Dividing by the factor at formation inflated names with
        # later reverse splits by up to millions of times (found 20.10.2026: WHLR)
        cap = at[0] * sh['value'] * _factor(p, sh['end'])
        if cap < min_cap:
            continue
        chosen[cik] = sym
        px[sym] = {'market_cap': cap, 'price': at[0] * _factor(p, formation) / _factor(p, '9999')}
    cur = sec_frames.rows(latest, chosen, sectors)
    hst = [sec_frames.rows(h, chosen, sectors) for h in hist]
    rows = screen.rows_from_sec_frames(cur, prices=px, history=hst, as_of=formation)
    for r in rows:
        universe.sanity(r)
    return rows, chosen


def forward(prices, sym, formation, months=12):
    p = prices.get(sym)
    if not p:
        return None
    y, m = map(int, formation[:7].split('-'))
    y2, m2 = y + (m - 1 + months) // 12, (m - 1 + months) % 12 + 1
    a, b = _at(p, formation[:7]), _at(p, f'{y2:04d}-{m2:02d}')
    if not a or not b or a[1] <= 0:
        return None
    return b[1] / a[1] - 1


def _stats(xs):
    n = len(xs)
    if not n:
        return {'n': 0}
    mu = sum(xs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1)) if n > 1 else None
    return {'n': n, 'mean': mu, 'sd': sd, 't': mu / (sd / math.sqrt(n)) if sd else None, 'share_positive': sum(1 for x in xs if x > 0) / n}


def _cagr(rets):
    g = 1.
    for r in rets:
        g *= 1 + r
    return g ** (1 / len(rets)) - 1 if rets else None


def run(merged, years, formations, ticker_of, prices, sectors, options=None, cost=.002, benchmark='SPY', exclude=None):
    """Per formation: each lens's candidates, their forward returns, the equal-weight universe and the benchmark."""
    periods = []
    for fdate in formations:
        rows, chosen = rows_at(merged, years, fdate, ticker_of, prices, sectors, exclude=exclude)
        if not rows:
            continue
        sc = screen.scan(rows, {**(options or {}), 'as_of': fdate})
        uni = [forward(prices, r['symbol'], fdate) for r in rows]
        uni = [x for x in uni if x is not None]
        bm = forward(prices, benchmark, fdate)
        if bm is None or not uni:
            continue
        caps = {r['symbol']: r['metrics'].get('market_cap') or 0 for r in rows}
        ucw = [(caps[r['symbol']], forward(prices, r['symbol'], fdate)) for r in rows]
        ucw = [(w, x) for w, x in ucw if x is not None and w]
        rec = {'formation': fdate, 'universe_n': len(uni), 'universe_ew': sum(uni) / len(uni), 'benchmark': bm, 'lanes': {},
               'universe_cw': sum(w * x for w, x in ucw) / sum(w for w, _ in ucw)}

        def cw(rs):
            ws = [(caps.get(s) or 0, x) for s, x in rs]
            tot = sum(w for w, _ in ws)
            return sum(w * x for w, x in ws) / tot - cost if tot else None
        for ln, cands in sc['lenses'].items():
            rs = [(c['symbol'], forward(prices, c['symbol'], fdate)) for c in cands]
            rs = [(s, x) for s, x in rs if x is not None]
            rec['lanes'][ln] = {'n': len(rs), 'ew': (sum(x for _, x in rs) / len(rs) - cost) if rs else None, 'cw': cw(rs),
                                'members': [s for s, _ in rs]}
        short = [(e['symbol'], forward(prices, e['symbol'], fdate)) for e in sc['shortlist']]
        short = [(s, x) for s, x in short if x is not None]
        rec['lanes']['shortlist'] = {'n': len(short), 'ew': (sum(x for _, x in short) / len(short) - cost) if short else None,
                                     'cw': cw(short), 'members': [s for s, _ in short]}
        allc = {s for v in rec['lanes'].values() for s in v['members']}
        rs = [forward(prices, s, fdate) for s in allc]
        rs = [x for x in rs if x is not None]
        rec['lanes']['all_lanes'] = {'n': len(rs), 'ew': (sum(rs) / len(rs) - cost) if rs else None,
                                     'cw': cw([(s, forward(prices, s, fdate)) for s in allc if forward(prices, s, fdate) is not None]),
                                     'members': sorted(allc)}
        periods.append(rec)
    summary = {}
    names = sorted({ln for p in periods for ln in p['lanes']})
    for ln in names + ['universe_ew', 'universe_cw', 'benchmark']:
        if ln in ('universe_ew', 'universe_cw', 'benchmark'):
            rets = [p[ln] for p in periods]
            summary[ln] = {'cagr': _cagr(rets), 'years': len(rets)}
            continue
        pairs = [(p['lanes'][ln]['ew'], p['universe_ew'], p['benchmark']) for p in periods if p['lanes'].get(ln, {}).get('ew') is not None]
        if not pairs:
            continue
        lane = [a for a, _, _ in pairs]
        cwp = [(p['lanes'][ln]['cw'], p['universe_cw'], p['benchmark']) for p in periods if p['lanes'].get(ln, {}).get('cw') is not None]
        summary[ln + '_capweighted'] = {'years': len(cwp), 'cagr': _cagr([a for a, _, _ in cwp]),
                                        'excess_vs_universe_cw': _stats([a - u for a, u, _ in cwp]),
                                        'excess_vs_benchmark': _stats([a - b for a, _, b in cwp])} if cwp else None
        summary[ln] = {'years': len(pairs), 'cagr': _cagr(lane), 'avg_names': sum(p['lanes'][ln]['n'] for p in periods if p['lanes'].get(ln)) / len(pairs),
                       'excess_vs_universe_ew': _stats([a - u for a, u, _ in pairs]), 'excess_vs_benchmark': _stats([a - b for a, _, b in pairs]),
                       'cagr_universe_same_years': _cagr([u for _, u, _ in pairs]), 'cagr_benchmark_same_years': _cagr([b for _, _, b in pairs])}
    return {'periods': periods, 'summary': summary, 'cost_per_year': cost, 'benchmark': benchmark, 'excluded_share_basis': len(exclude or ()),
            'caveats': ['survivorship: only currently listed tickers have prices (inflates lanes and the equal-weight universe, not SPY)',
                        'one observation a year: a handful of years cannot prove an edge; t-statistics below ~2 are noise',
                        'sector labels are today\'s listing labels (minor look-ahead in the cyclical cue)',
                        'annual SEC frames only: no quarterly updates between formations',
                        'no capacity, tax or timing effects beyond the stated cost']}


def load_merged(raw_dir):
    """{year: merged frames} from a collect_backtest raw folder."""
    with open(os.path.join(raw_dir, 'MANIFEST.json'), encoding='utf-8') as f:
        man = json.load(f)
    frames = {}
    for c in man['calls']:
        if c.get('metric') and c['status'] == 200:
            with open(os.path.join(raw_dir, c['file']), encoding='utf-8') as f:
                frames.setdefault(c['year'], []).append({'metric': c['metric'], 'tag': c['tag'], 'payload': json.load(f)})
    return {y: sec_frames.merge_frames(v) for y, v in sorted(frames.items())}
