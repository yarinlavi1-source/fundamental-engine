"""Walk-forward test of the discovery lanes (fundamental_engine.backtest) on a collect_backtest.py folder.

    python scripts/run_backtest.py runs/backtest --from 2012 --to 2025 [--month 07]
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import backtest as B  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--from', dest='start', type=int, default=2012)
    ap.add_argument('--to', dest='end', type=int, default=2025)
    ap.add_argument('--month', default='07')
    ap.add_argument('--sectors', help='universe raw folder with nasdaq__screener_stocks.json for sector labels')
    a = ap.parse_args(argv)
    raw = os.path.join(a.folder, 'raw')
    merged = B.load_merged(raw)
    with open(os.path.join(raw, 'sec__company_tickers_exchange.json')) as f:
        t = json.load(f)
    ticker_of = {}
    for cik, name, tk, ex in t['data']:
        if ex in ('Nasdaq', 'NYSE'):
            ticker_of.setdefault(int(cik), tk.upper())
    prices = {}
    for p in glob.glob(os.path.join(raw, 'prices', '*.json')):
        x = B.load_prices(p)
        if x:
            prices[os.path.basename(p)[:-5]] = x
    sectors, caps = {}, {}
    if a.sectors:
        with open(os.path.join(a.sectors, 'nasdaq__screener_stocks.json')) as f:
            for r in json.load(f)['data']['rows']:
                sectors[r['symbol'].upper()] = {'sector': r.get('sector') or None, 'industry': r.get('industry') or None}
                try:
                    caps[r['symbol'].upper()] = float(r.get('marketCap') or 0) or None
                except ValueError:
                    pass
    bad = B.share_basis_check(merged, ticker_of, prices, caps)
    print(json.dumps({'excluded_share_basis': len(bad), 'examples': sorted(bad)[:15]}))
    forms = [f'{y}-{a.month}-01' for y in range(a.start, a.end + 1)]
    res = B.run(merged, sorted(merged), forms, ticker_of, prices, sectors, exclude=set(bad))
    out = os.path.join(a.folder, f'backtest_{a.start}_{a.end}_m{a.month}.json')
    with open(out, 'w') as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: round(v['cagr'], 4) for k, v in res['summary'].items() if v and v.get('cagr') is not None}))
    for k, v in res['summary'].items():
        if v and 'excess_vs_universe_cw' in v:
            e, b = v['excess_vs_universe_cw'], v['excess_vs_benchmark']
            print(k, 'cagr', round(v['cagr'], 4), 'vs CW universe', round(e['mean'], 4), 't', e['t'] and round(e['t'], 2),
                  '| vs SPY', round(b['mean'], 4), 't', b['t'] and round(b['t'], 2), 'hit', round(b['share_positive'], 2))
    for k, v in res['summary'].items():
        if v and 'excess_vs_universe_ew' in v:
            e, b = v['excess_vs_universe_ew'], v['excess_vs_benchmark']
            print(k, 'vs EW', round(e['mean'], 4), 't', e['t'] and round(e['t'], 2), 'hit', round(e['share_positive'], 2),
                  '| vs SPY', round(b['mean'], 4), 't', b['t'] and round(b['t'], 2))


if __name__ == '__main__':
    main()
