"""Walk-forward test of the full valuation engine against the S&P 500 (fundamental_engine.engine_backtest).

    python scripts/backtest_engine.py runs/universe_2026-10-20 --name large500 --from 2016 --to 2025 --workers 4

Company folders come from companies/QUEUE_<name>.json (saved SEC companyfacts), monthly prices from
runs/backtest/raw/prices (collect_backtest.py), the 10-year Treasury from runs/backtest/raw/macro/fred_GS10.csv. Rows are
appended to <out>/rows.jsonl (resumable); the evaluation is written to <out>/summary.json.
"""
import argparse
import glob
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import engine_backtest as E  # noqa: E402
from fundamental_engine.backtest import load_prices  # noqa: E402

CTX = {}


def _init(prices_dir, rates, comp):
    CTX.update(prices_dir=prices_dir, rates=rates, comp=comp)


def _work(job):
    sym, formations = job
    p_path = os.path.join(CTX['prices_dir'], f'{sym}.json')
    p = load_prices(p_path) if os.path.exists(p_path) else None
    out = []
    for fm in formations:
        as_of, rf = E.formation_inputs(fm, CTX['rates'])
        px = E.traded_price(p, as_of) if p else None
        if not px:
            out.append({'symbol': sym, 'formation': fm, 'skipped': 'no price'})
            continue
        try:
            r = E.company_row(os.path.join(CTX['comp'], sym), as_of, px, rf)
        except Exception as e:                              # recorded, never hidden: a crash is a finding
            r = {'symbol': sym, 'as_of': as_of, 'error': f'{type(e).__name__}: {e}'[:300]}
        out.append(dict(r or {'symbol': sym, 'skipped': 'nothing filed by as_of'}, formation=fm))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--name', default='large500')
    ap.add_argument('--from', dest='start', type=int, default=2016)
    ap.add_argument('--to', dest='end', type=int, default=2025)
    ap.add_argument('--month', default='07')
    ap.add_argument('--prices', default='runs/backtest/raw/prices')
    ap.add_argument('--rates', default='runs/backtest/raw/macro/fred_GS10.csv')
    ap.add_argument('--out', default='runs/backtest/engine')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--symbols', nargs='*')
    ap.add_argument('--evaluate-only', action='store_true')
    a = ap.parse_args(argv)
    comp = os.path.join(a.folder, 'companies')
    os.makedirs(a.out, exist_ok=True)
    rows_path = os.path.join(a.out, 'rows.jsonl')
    done = set()
    if os.path.exists(rows_path):
        with open(rows_path, encoding='utf-8') as f:
            for line in f:
                r = json.loads(line)
                done.add((r['symbol'], r['formation']))
    formations = [f'{y}-{a.month}-01' for y in range(a.start, a.end + 1)]
    syms = a.symbols or [e['symbol'] for e in json.load(open(os.path.join(comp, f'QUEUE_{a.name}.json')))['queue'] if e.get('symbol')]
    syms = [s for s in syms if os.path.exists(os.path.join(comp, s, 'company.json'))]
    jobs = [(s, [f for f in formations if (s, f) not in done]) for s in syms]
    jobs = [j for j in jobs if j[1]]
    if jobs and not a.evaluate_only:
        rates = E.treasury(a.rates)
        with Pool(a.workers, initializer=_init, initargs=(a.prices, rates, comp)) as pool, open(rows_path, 'a', encoding='utf-8') as f:
            for k, rows in enumerate(pool.imap_unordered(_work, jobs), 1):
                for r in rows:
                    f.write(json.dumps(r, default=str) + '\n')
                f.flush()
                if k % 25 == 0:
                    print(f'{k}/{len(jobs)} companies', flush=True)
    by_date = {}
    with open(rows_path, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            if 'base_irr' in r and r['formation'] in formations and (not a.symbols or r['symbol'] in syms):
                by_date.setdefault(r['formation'], []).append(r)
    needed = {r['symbol'] for rs in by_date.values() for r in rs} | {'SPY'}
    prices = {s: load_prices(os.path.join(a.prices, f'{s}.json')) for s in needed if os.path.exists(os.path.join(a.prices, f'{s}.json'))}
    res = E.evaluate(by_date, prices)
    with open(os.path.join(a.out, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=1, default=str)
    pct = lambda x: 'n/a' if x is None else f'{x * 100:.1f}%'
    for k, v in res['summary'].items():
        if 'excess_vs_benchmark' in v:
            e, b = v['excess_vs_universe_ew'], v['excess_vs_benchmark']
            print(f"{k:32s} CAGR {pct(v['cagr'])}  names {v['avg_names']:.0f}  vs EW {pct(e.get('mean'))} (t {e.get('t') or 0:.1f})  "
                  f"vs SPY {pct(b.get('mean'))} (t {b.get('t') or 0:.1f})")
        elif 'cagr' in v:
            print(f"{k:32s} CAGR {pct(v['cagr'])}")
    ic = res['summary']['information_coefficient']
    print(f"information coefficient mean {ic.get('mean') or 0:.3f} (t {ic.get('t') or 0:.1f}, {ic.get('n')} years)")


if __name__ == '__main__':
    main()
