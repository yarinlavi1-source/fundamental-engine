"""Walk-forward test of the valuation engine itself: does ranking by the engine's implied return beat the index?

The discovery backtest (backtest.py) tests the numeric screen. This tests the full decision path: at each formation date
the complete pipeline (dataqa, analysis, model card, forecast, valuation, risk, review, decision) runs on each company's
saved SEC companyfacts with as_of = the day before formation, so only facts filed by then are used (facts.usable), with the
price as traded on that day (split-adjusted monthly close x the splits after it) and the 10-year Treasury yield of the month
before (FRED GS10, published by then) as the risk-free rate. Documents filed later (the saved 10-K / 10-Q) are skipped by
pipeline._business. Each company's base-case implied return (IRR) is ranked; the engine market return is the cap-weighted
IRR of the valued names that date (calibration.market_implied's rule), so the edge is like for like.

Portfolios (equal weight, held 12 months, total return with dividends from the adjusted close, minus a yearly cost):
top and bottom quintile by engine IRR, the names with a positive edge, and the same excluding data-blocked casefiles.
Controls on the same names and dates: top quintile by trailing earnings yield and by trailing FCF yield (does the engine
add anything over a naive cheapness rank?), the equal- and cap-weighted universe and SPY. The rank correlation between
IRR and the forward return (information coefficient) is reported per year.

Bias, stated with every result: the universe is today's large caps (survivors), which inflates every stock portfolio and
especially value tilts (cheap companies that later failed are absent); SIC codes and listing labels are today's; the
engine's rules were written in 2026 (in-sample design). The forward ledger is the out-of-sample test.
"""
import json
import math
import os
from datetime import date, timedelta

from . import facts as F
from .backtest import _at, _factor, forward, load_prices, _stats, _cagr

CLIP = (-.10, .40)              # calibration.market_implied's clip for the cap-weighted engine market return


def treasury(path):
    """FRED monthly CSV (observation_date, GS10) -> {'yyyy-mm': yield as a fraction}."""
    out = {}
    with open(path, encoding='utf-8') as f:
        next(f)
        for line in f:
            d, v = line.strip().split(',')[:2]
            if v and v != '.':
                out[d[:7]] = float(v) / 100
    return out


def formation_inputs(formation, rates):
    """(as_of, risk-free spec) for a formation date 'yyyy-mm-01': as_of is the last day of the prior month; the rate is that
    month's average (published at its end)."""
    f = date.fromisoformat(formation)
    as_of = (f - timedelta(days=1)).isoformat()
    rf = rates.get(as_of[:7])
    return as_of, rf


def traded_price(p, as_of):
    """Close of the as_of month as quoted then (split-adjusted close x the splits after as_of), or None."""
    at = _at(p, as_of[:7])
    return at[0] * _factor(p, as_of) if at else None


def company_row(folder, as_of, price, rf):
    """Run the whole pipeline for one company folder at as_of with the given price and risk-free rate -> summary row or
    None when nothing usable was filed by then."""
    from . import pipeline
    with open(os.path.join(folder, 'company.json'), encoding='utf-8') as f:
        company = json.load(f)
    sub = os.path.join(folder, 'raw', 'sec__submissions.json')
    if os.path.exists(sub) and not company.get('sic'):
        with open(sub, encoding='utf-8') as f:
            d = json.load(f)
        if str(d.get('sic') or '').isdigit():
            company = {**company, 'sic': int(d['sic']), 'sic_description': d.get('sicDescription')}
    fb = pipeline.factbook_from_run_dir(os.path.join(folder, 'raw'), company, as_of)
    if not F.series(fb, 'revenue'):
        return None
    F.add_source(fb, 'backtest:price', 'yahoo', 'monthly close as traded', 'market_feed', as_of)
    F.set_price(fb, price, as_of, 'backtest:price', kind='close')
    inputs = None
    if rf is not None:
        F.add_source(fb, 'fred:GS10', 'fred', 'GS10 monthly average 10-year Treasury yield', 'market_feed', as_of,
                     url='https://fred.stlouisfed.org/series/GS10')
        inputs = {'forecast': {'risk_free': {'value': rf, 'as_of': as_of, 'source_id': 'fred:GS10'}}}
    cf = pipeline.run(fb, inputs)
    s = pipeline.summary(cf)
    ttm = (cf.get('an') or {}).get('ttm') or {}
    sh = (((cf.get('qa') or {}).get('resolved') or {}).get('shares_outstanding') or {}).get('value')
    cap = price * sh if sh else None
    ey = ttm.get('net_income') / cap if cap and ttm.get('net_income') is not None else None
    fy = ttm.get('fcf') / cap if cap and ttm.get('fcf') is not None else None
    return {'symbol': company['ticker'], 'as_of': as_of, 'price': price, 'market_cap': cap, 'base_irr': s.get('base_irr'),
            'bear_irr': s.get('bear_irr'), 'base_value': s.get('base_value'), 'bear_value': s.get('bear_value'),
            'verdict': s.get('verdict'), 'confidence': s.get('confidence'), 'quality': s.get('quality'),
            'review_status': s.get('review_status'), 'qa_status': (cf.get('qa') or {}).get('status'),
            'stage': ((cf.get('card') or {}).get('stage') or {}).get('value'),
            'type': ((cf.get('card') or {}).get('business_model') or {}).get('primary'),
            'earnings_yield': ey, 'fcf_yield': fy, 'errors': len(cf.get('errors') or [])}


def _q(rows, key, top=True, frac=.2):
    xs = sorted((r for r in rows if r.get(key) is not None), key=lambda r: r[key], reverse=top)
    return xs[:max(1, int(len(xs) * frac))] if xs else []


def _spearman(a, b):
    n = len(a)
    if n < 5:
        return None
    rank = lambda xs: {i: k for k, i in enumerate(sorted(range(n), key=lambda i: xs[i]))}
    ra, rb = rank(a), rank(b)
    d2 = sum((ra[i] - rb[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1))


def evaluate(rows_by_date, prices, cost=.002, benchmark='SPY'):
    """rows_by_date: {formation: [company_row]} -> per-period portfolio returns and summary statistics."""
    periods = []
    for formation in sorted(rows_by_date):
        rows = [dict(r) for r in rows_by_date[formation]]
        as_of = rows[0]['as_of'] if rows else None
        for r in rows:
            r['fwd'] = forward(prices, r['symbol'], as_of)
        rows = [r for r in rows if r['fwd'] is not None]
        bm = forward(prices, benchmark, as_of)
        if not rows or bm is None:
            continue
        valued = [r for r in rows if r['base_irr'] is not None and r.get('market_cap')]
        w = sum(r['market_cap'] for r in valued)
        mkt = sum(min(max(r['base_irr'], CLIP[0]), CLIP[1]) * r['market_cap'] for r in valued) / w if w else None
        ok = [r for r in valued if r['qa_status'] != 'blocked']
        ew = lambda rs: (sum(r['fwd'] for r in rs) / len(rs) - cost) if rs else None
        caps = [(r['market_cap'], r['fwd']) for r in rows if r.get('market_cap')]
        books = {
            'engine_top_quintile': _q(valued, 'base_irr'),
            'engine_bottom_quintile': _q(valued, 'base_irr', top=False),
            'engine_positive_edge': [r for r in valued if mkt is not None and r['base_irr'] > mkt],
            'engine_top_quintile_unblocked': _q(ok, 'base_irr'),
            'engine_edge_and_bear_positive': [r for r in ok if mkt is not None and r['base_irr'] > mkt
                                              and (r['bear_irr'] or -1) > 0],
            'earnings_yield_top_quintile': _q(rows, 'earnings_yield'),
            'fcf_yield_top_quintile': _q(rows, 'fcf_yield'),
        }
        rec = {'formation': formation, 'as_of': as_of, 'n': len(rows), 'n_valued': len(valued), 'engine_market_return': mkt,
               'benchmark': bm, 'universe_ew': sum(r['fwd'] for r in rows) / len(rows),
               'universe_cw': sum(c * x for c, x in caps) / sum(c for c, _ in caps) if caps else None,
               'ic': _spearman([r['base_irr'] for r in valued], [r['fwd'] for r in valued]),
               'books': {k: {'n': len(v), 'ret': ew(v), 'members': sorted(r['symbol'] for r in v)} for k, v in books.items()}}
        periods.append(rec)
    summary = {}
    for k in (periods[0]['books'] if periods else {}):
        pairs = [(p['books'][k]['ret'], p['universe_ew'], p['benchmark']) for p in periods if p['books'][k]['ret'] is not None]
        if not pairs:
            continue
        summary[k] = {'years': len(pairs), 'cagr': _cagr([a for a, _, _ in pairs]),
                      'avg_names': sum(p['books'][k]['n'] for p in periods) / len(periods),
                      'excess_vs_universe_ew': _stats([a - u for a, u, _ in pairs]),
                      'excess_vs_benchmark': _stats([a - b for a, _, b in pairs])}
    for k in ('universe_ew', 'universe_cw', 'benchmark'):
        rs = [p[k] for p in periods if p[k] is not None]
        summary[k] = {'cagr': _cagr(rs), 'years': len(rs)}
    ics = [p['ic'] for p in periods if p['ic'] is not None]
    summary['information_coefficient'] = _stats(ics)
    return {'periods': periods, 'summary': summary, 'cost_per_year': cost, 'benchmark': benchmark,
            'caveats': ["survivorship: the universe is today's large caps; every stock portfolio and especially cheapness tilts "
                        'are flattered (companies that looked cheap and then failed are absent); compare the engine with the '
                        'earnings- and FCF-yield controls, which share the bias',
                        "today's SIC codes and listing labels; engine rules written in 2026 (in-sample design)",
                        'one formation a year: t-statistics below about 2 are noise',
                        'no consensus history: the forecast uses reported history only, as in an automatic run']}
