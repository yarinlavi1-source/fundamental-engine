"""Walk-forward test of the expected price path lane (fundamental_engine.pricepath; docs/prereg_price_path.md section 6).

    python scripts/backtest_pricepath.py runs/universe_2026-10-21 --name large500 --from 2016 --to 2025 --workers 2 \
        --engine-rows runs/backtest/engine/rows.jsonl --out runs/backtest/pricepath

Company folders come from companies/QUEUE_<name>.json (saved SEC companyfacts), monthly prices from runs/backtest/raw/prices
(Yahoo, split-adjusted), the 10-year Treasury from runs/backtest/raw/macro/fred_GS10.csv, the ERP from
engine_backtest.IMPLIED_ERP and the CPI from pricepath.CPI_MAY (all point in time). Per (symbol, formation) the first three
pipeline stages run at as_of = the day before formation with the price as traded then; the lane's anchors, three paths and
flags are appended to <out>/rows.jsonl (resumable). --engine-rows names the engine walk-forward rows
(scripts/backtest_engine.py) that supply base_irr for the engine baseline; --evaluate-only rebuilds summary.json and
REPORT.md from the saved rows.
"""
import argparse
import json
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import engine_backtest as E  # noqa: E402
from fundamental_engine import facts as F  # noqa: E402
from fundamental_engine import pricepath as PP  # noqa: E402
from fundamental_engine.backtest import load_prices  # noqa: E402

CTX = {}
ROW_KEYS = ('cost_of_equity', 'return_on_capital', 'eps_normalized', 'multiple_today', 'justified_today', 'justified_today_raw',
            'premium_today', 'size_class', 'scenarios', 'flags', 'eps_cagr_5y_base', 'payout_1', 'pe_regression_diagnostic',
            'input_sha256', 'output_sha256')


def _init(prices_dir, rates, comp):
    CTX.update(prices_dir=prices_dir, rates=rates, comp=comp)


def lane_row(folder, as_of, price, rf, erp, cpi):
    """One company folder at as_of -> lane row (status ok / excluded) or None when nothing usable was filed by then."""
    from fundamental_engine import pipeline, dataqa, analysis, model_card
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
    qa = dataqa.validate(fb, fb.get('capital_structure'))
    an = analysis.analyze(fb, qa)
    card = model_card.build(fb, an, qa, None)
    a = PP.anchors(fb, an, qa, card, price, rf, erp, cpi)
    base = {'symbol': company['ticker'], 'as_of': as_of, 'price': price, 'risk_free': rf, 'erp': erp, 'cpi': cpi,
            'stage': ((card.get('stage') or {}).get('value')), 'cyclical': bool((card.get('cyclical') or {}).get('value')),
            'qa_status': qa.get('status'), 'eps_cagr_3y': PP.eps_cagr_3y(fb, qa)}
    if a['status'] != 'ok':
        return dict(base, status='excluded', reasons=a['reasons'])
    out = PP.price_path(a)
    anch = {k: v for k, v in a.items() if k not in ('references', 'status')}
    return dict(base, status='ok', dividend_yield=a.get('dividend_yield'), anchors=anch, references=a['references'],
                **{k: out[k] for k in ROW_KEYS})


def _work(job):
    sym, formations = job
    p_path = os.path.join(CTX['prices_dir'], f'{sym}.json')
    p = load_prices(p_path) if os.path.exists(p_path) else None
    out = []
    for fm in formations:
        as_of, rf, erp = E.formation_inputs(fm, CTX['rates'])
        px = E.traded_price(p, as_of) if p else None
        if not px:
            out.append({'symbol': sym, 'formation': fm, 'status': 'skipped', 'reasons': ['no price']})
            continue
        cpi = PP.CPI_MAY.get(int(as_of[:4]))
        try:
            r = lane_row(os.path.join(CTX['comp'], sym), as_of, px, rf, erp, cpi)
        except Exception as e:                              # recorded, never hidden: a crash is a finding
            r = {'symbol': sym, 'as_of': as_of, 'status': 'error', 'reasons': [f'{type(e).__name__}: {e}'[:300]]}
        out.append(dict(r or {'symbol': sym, 'status': 'skipped', 'reasons': ['nothing filed by as_of']}, formation=fm))
    return out


# ---------------------------------------------------------------- report

def _f(x, k=3):
    return 'n/a' if x is None else f'{x:.{k}f}'


def _p(x):
    return 'n/a' if x is None else f'{x * 100:.0f}%'


def report(res, name):
    L = [f'# Expected price path walk-forward ({name})', '',
         f"Verdict (preregistered section 6.7): **{res['verdict']['verdict']}** -- {res['verdict']['meaning']}.",
         '' if not res['verdict']['failed'] else 'Failed checks: ' + ', '.join(res['verdict']['failed']), '']
    for h, z in sorted(res['horizons'].items()):
        c, lo = z.get('common') or {}, z.get('lane_only') or {}
        L += [f"## Horizon {h} year(s): formations {z['formations'][0]}-{z['formations'][1]}", '',
              f"Rows in range {z['n_in_range']}; excluded (independent counts): " + ', '.join(f'{k} {v}' for k, v in z['excluded'].items())
              + f"; common sample n = {c.get('n', 0)}, lane-only sample n = {lo.get('n', 0)}.",
              'Exclusion reasons (no lane path): ' + ', '.join(f'{k} {v}' for k, v in sorted(z['exclusion_reasons'].items(), key=lambda kv: -kv[1])), '']
        if c.get('metrics'):
            L += ['| predictor (common sample) | n | median log error (bias) | mean log error | median abs log error | within +-25% | share above actual |', '|---|---|---|---|---|---|---|']
            for n, m in c['metrics'].items():
                if m.get('n'):
                    L.append(f"| {n} | {m['n']} | {_f(m['bias_median'])} | {_f(m['bias_mean'])} | {_f(m['mdale'])} | {_p(m['within25'])} | {_p(m['share_above'])} |")
            b = c['band']
            L += ['', f"Band: realized within [bear, bull] {_p(b['within_band'])}, below bear {_p(b['below_bear'])}, above bull {_p(b['above_bull'])} (n {b['n']}).",
                  'Head to head (share of rows where the lane\'s |log error| is smaller): ' + ', '.join(f"vs {k} {_p(v['share_lane_closer'])}" for k, v in c['head_to_head'].items()),
                  'Yearly Spearman of pred / P_0 vs realized ratio (mean, t over formation years): ' + ', '.join(
                      f"{k} {_f(v.get('mean'))} (t {_f(v.get('t'), 1)}, {v.get('n')} yrs)" for k, v in c['rank'].items()), '']
            d = c['deflated']
            L += [f"Survivorship-deflated (6.8; per-window rescale to SPY, n {d['n']}): " + '; '.join(
                f"{n} bias {_f(m.get('bias_median'))} MdALE {_f(m.get('mdale'))} within25 {_p(m.get('within25'))}" for n, m in d['metrics'].items() if m.get('n'))
                + f"; band within {_p(d['band'].get('within_band'))} below {_p(d['band'].get('below_bear'))} above {_p(d['band'].get('above_bull'))}", '']
            for key, label in (('formation_year', 'formation year'), ('stage', 'stage'), ('cyclical', 'cyclical flag'), ('multiple_bucket', 'multiple today'), ('size_class', 'size class')):
                L += [f'By {label} (lane_base bias / MdALE / within25; then the baselines\' MdALE):', '',
                      '| group | n | lane bias | lane MdALE | lane within25 | random_walk | drift_7 | analyst_const | engine_irr |', '|---|---|---|---|---|---|---|---|---|']
                for g, ms in c['by'][key].items():
                    m = ms['lane_base']
                    L.append(f"| {g} | {m.get('n')} | {_f(m.get('bias_median'))} | {_f(m.get('mdale'))} | {_p(m.get('within25'))} | "
                             + ' | '.join(_f(ms[b].get('mdale')) for b in PP.BASELINES) + ' |')
                L.append('')
        if lo.get('metrics'):
            L += ['Lane-only sample (lane rows with a realized price; no engine or EPS requirement):', '',
                  '| predictor | n | bias | MdALE | within25 |', '|---|---|---|---|---|']
            for n, m in lo['metrics'].items():
                if m.get('n'):
                    L.append(f"| {n} | {m['n']} | {_f(m['bias_median'])} | {_f(m['mdale'])} | {_p(m['within25'])} |")
            lb = lo['band']
            L += ['', f"Lane-only band: within {_p(lb.get('within_band'))}, below bear {_p(lb.get('below_bear'))}, above bull {_p(lb.get('above_bull'))}; "
                  + 'head to head ' + ', '.join(f"vs {k} {_p(v['share_lane_closer'])}" for k, v in lo['head_to_head'].items()), '']
    dg = res['diagnostics']
    L += ['## Diagnostics (6.6; no effect on the price)', '', f"Lane rows ok {dg['n_rows_ok']} of {dg['n_rows']}.", '']
    if dg.get('by_size_class'):
        L += ['| size class | n | median lambda (base) | median 5-year base-rate share (base) | share of rows shrunk |', '|---|---|---|---|---|']
        for k, v in dg['by_size_class'].items():
            L.append(f"| {k} | {v['n']} | {_f(v['median_lambda_base'])} | {_f(v['median_share_5y_base'])} | {_p(v['share_lambda_shrunk_base'])} |")
        pm = dg['premium_over_multiple']
        L += ['', f"Premium today / multiple today (D_0 / M_0): p10 {_f(pm['p10'])}, p25 {_f(pm['p25'])}, median {_f(pm['median'])}, p75 {_f(pm['p75'])}, p90 {_f(pm['p90'])} (n {pm['n']}).",
              f"Multiple today median {_f(dg['multiple_today']['median'], 1)}x; justified today median {_f(dg['multiple_today']['median_justified'], 1)}x.",
              f"Justified multiple today / Damodaran Jan-2026 regression PE: p25 {_f(dg['justified_over_damodaran_regression']['p25'])}, median "
              f"{_f(dg['justified_over_damodaran_regression']['median'])}, p75 {_f(dg['justified_over_damodaran_regression']['p75'])} ({dg['justified_over_damodaran_regression']['note']}).",
              'Flag shares: ' + ', '.join(f'{k} {_p(v)}' for k, v in dg['flag_shares'].items()), '']
    L += ['## Checks (6.7)', '']
    for k, v in res['verdict']['checks'].items():
        L.append(f"- {k}: {'pass' if v.get('pass') else ('FAIL' if v.get('pass') is False else 'not testable')} -- "
                 + ', '.join(f'{a} {_f(b) if isinstance(b, float) else b}' for a, b in v.items() if a != 'pass'))
    L += ['', '## Caveats', ''] + [f'- {c}' for c in res['caveats']]
    return '\n'.join(L) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--name', default='large500')
    ap.add_argument('--from', dest='start', type=int, default=2016)
    ap.add_argument('--to', dest='end', type=int, default=2025)
    ap.add_argument('--month', default='07')
    ap.add_argument('--prices', default='runs/backtest/raw/prices')
    ap.add_argument('--rates', default='runs/backtest/raw/macro/fred_GS10.csv')
    ap.add_argument('--engine-rows', default=None, help='scripts/backtest_engine.py rows.jsonl (base_irr for the engine baseline)')
    ap.add_argument('--out', default='runs/backtest/pricepath')
    ap.add_argument('--workers', type=int, default=2)
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
    rows = []
    with open(rows_path, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            if r['formation'] in formations and (not a.symbols or r['symbol'] in syms) and r.get('status') != 'skipped':
                rows.append(r)
    engine = []
    if a.engine_rows and os.path.exists(a.engine_rows):
        with open(a.engine_rows, encoding='utf-8') as f:
            engine = [json.loads(line) for line in f]
        engine = [r for r in engine if r.get('base_irr') is not None]
    needed = {r['symbol'] for r in rows} | {'SPY'}
    prices = {s: load_prices(os.path.join(a.prices, f'{s}.json')) for s in needed if os.path.exists(os.path.join(a.prices, f'{s}.json'))}
    res = PP.evaluate(rows, prices, engine)
    res['run'] = {'universe': a.folder, 'name': a.name, 'formations': formations, 'engine_rows': a.engine_rows, 'n_engine_rows': len(engine),
                  'n_rows': len(rows), 'status_counts': {k: sum(1 for r in rows if r.get('status') == k) for k in ('ok', 'excluded', 'error')}}
    with open(os.path.join(a.out, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=1, default=str)
    with open(os.path.join(a.out, 'REPORT.md'), 'w', encoding='utf-8') as f:
        f.write(report(res, a.name))
    print(f"verdict {res['verdict']['verdict']}; failed: {res['verdict']['failed']}")
    for h in PP.VERDICT_HORIZONS:
        c = (res['horizons'].get(h) or {}).get('common') or {}
        if c.get('metrics'):
            print(f'h{h} n={c["n"]}: ' + '; '.join(f"{n} bias {_f(m['bias_median'])} MdALE {_f(m['mdale'])} w25 {_p(m['within25'])}"
                                                    for n, m in c['metrics'].items() if m.get('n') and n in ('lane_base', 'random_walk', 'drift_7', 'analyst_const', 'engine_irr'))
                  + f"; band {_p(c['band']['within_band'])} / below {_p(c['band']['below_bear'])} / above {_p(c['band']['above_bull'])}")


if __name__ == '__main__':
    main()
