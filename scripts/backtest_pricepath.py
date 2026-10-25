"""Walk-forward test of the expected price path lane (fundamental_engine.pricepath; docs/prereg_price_path.md section 6, and
docs/prereg_price_path_v2.md section 6 with --spec 2).

    python scripts/backtest_pricepath.py runs/universe_2026-10-21 --name large500 --from 2016 --to 2025 --workers 2 \
        --engine-rows runs/backtest/engine/rows.jsonl --out runs/backtest/pricepath
    python scripts/backtest_pricepath.py ... --spec 2 --v1-rows <spec-1 out>/rows.jsonl --out runs/backtest/pricepath_v2

Company folders come from companies/QUEUE_<name>.json (saved SEC companyfacts), monthly prices from runs/backtest/raw/prices
(Yahoo, split-adjusted), the 10-year Treasury from runs/backtest/raw/macro/fred_GS10.csv, the ERP from
engine_backtest.IMPLIED_ERP and the CPI from pricepath.CPI_MAY (all point in time). Per (symbol, formation) the first three
pipeline stages run at as_of = the day before formation with the price as traded then; the lane's anchors, three paths and
flags are appended to <out>/rows.jsonl (resumable). --engine-rows names the engine walk-forward rows
(scripts/backtest_engine.py) that supply base_irr for the engine baseline; --evaluate-only rebuilds summary.json and
REPORT.md from the saved rows.

--spec 2 runs two passes (v2 section 6.1). Pass 1 appends every company's spec-2 anchors per formation to <out>/anchors.jsonl
(resumable; the pipeline stages, the slow part). Pass 2, per formation, builds the point-in-time cross-section table of v2
section 3.4 from every company with usable anchors at that as_of, then the paths, and rewrites <out>/rows.jsonl and
<out>/tables.json (deterministic: sorted by formation and symbol). --v1-rows names spec-1 rows of the same universe (a
--spec 1 run of the unchanged code) for the lane_v1 baseline; the evaluation of v2 sections 6-7 needs them.
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
ROW_KEYS_V2 = ('eps_ttm', 'multiple_today', 'cost_of_equity', 'return_on_capital', 'cross_section', 'cross_section_today',
               'premium_log_raw', 'premium_log', 'gordon', 'margin_history', 'size_class', 'scenarios', 'flags', 'years_basis',
               'ordering_checked', 'input_sha256', 'output_sha256')


def _init(prices_dir, rates, comp, spec=1):
    CTX.update(prices_dir=prices_dir, rates=rates, comp=comp, row=lane_row if spec == 1 else anchor_row)


def _stages(folder, as_of, price):
    """The first three pipeline stages at as_of with the traded price (both specs read them identically).
    -> (company, fb, qa, an, card), or None when no revenue was filed by as_of."""
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
    return company, fb, qa, an, card


def lane_row(folder, as_of, price, rf, erp, cpi):
    """Spec 1: one company folder at as_of -> lane row (status ok / excluded) or None when nothing usable was filed by then."""
    st = _stages(folder, as_of, price)
    if st is None:
        return None
    company, fb, qa, an, card = st
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


def anchor_row(folder, as_of, price, rf, erp, cpi):
    """Spec 2, pass 1: one company folder at as_of -> anchors row (status ok with the full v2 anchors / excluded) or None when
    nothing usable was filed by then. No path yet: the path needs the formation's cross-section table (pass 2)."""
    st = _stages(folder, as_of, price)
    if st is None:
        return None
    company, fb, qa, an, card = st
    a = PP.anchors(fb, an, qa, card, price, rf, erp, cpi, spec_version=PP.SPEC_VERSION_2)
    base = {'symbol': company['ticker'], 'as_of': as_of, 'price': price, 'risk_free': rf, 'erp': erp, 'cpi': cpi,
            'stage': ((card.get('stage') or {}).get('value')), 'cyclical': bool((card.get('cyclical') or {}).get('value')),
            'qa_status': qa.get('status'), 'eps_cagr_3y': PP.eps_cagr_3y(fb, qa)}
    if a['status'] != 'ok':
        return dict(base, status='excluded', reasons=a['reasons'])
    return dict(base, status='ok', dividend_yield=a.get('dividend_yield'), anchors=a)


def table_source(name, as_of):
    """The cross-section's name in every v2 row (part of its hash, so free of machine paths)."""
    return f'universe QUEUE_{name}: spec-2 anchors of every company at {as_of} (pass 1 of scripts/backtest_pricepath.py --spec 2)'


def formation_paths(arows, source):
    """Spec 2, pass 2 for ONE formation: the v2 3.4 table from every company with usable anchors at that as_of, then each
    company's paths on it. Anchor rows that are not ok pass through; a path that raises (e.g. pricepath.OrderingError) is an
    error row, recorded, never hidden. -> (v2 rows sorted by symbol, table or None)."""
    ok = [r for r in arows if r.get('status') == 'ok']
    table, table_error = None, None
    if ok:
        as_ofs = sorted({r['as_of'] for r in ok})
        if len(as_ofs) != 1:
            raise ValueError(f'one formation, one as_of: got {as_ofs}')
        try:
            table = PP.cross_section_table([PP.cross_section_entry(r['anchors'], r['symbol']) for r in ok], as_ofs[0], source(as_ofs[0]))
        except ValueError as e:
            table_error = f'{type(e).__name__}: {e}'[:300]
    out = []
    for r in sorted(arows, key=lambda r: r['symbol']):
        if r.get('status') != 'ok':
            out.append(r)
            continue
        base = {k: v for k, v in r.items() if k != 'anchors'}
        try:
            if table_error:
                raise ValueError(table_error)
            p = PP.price_path(r['anchors'], spec_version=PP.SPEC_VERSION_2, cross_section=table)
        except Exception as e:                              # recorded, never hidden: a crash (or an ordering violation) is a finding
            out.append(dict(base, status='error', reasons=[f'{type(e).__name__}: {e}'[:300]]))
            continue
        anch = {k: v for k, v in r['anchors'].items() if k not in ('references', 'status')}
        out.append(dict(base, anchors=anch, references=r['anchors']['references'], **{k: p[k] for k in ROW_KEYS_V2}))
    return out, table


def _pass2(job):
    arows, name = job
    return formation_paths(arows, lambda as_of: table_source(name, as_of))


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
            r = CTX['row'](os.path.join(CTX['comp'], sym), as_of, px, rf, erp, cpi)
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


def _terms(x):
    return ' | '.join(f"{_f((x.get(k) or {}).get('median'))} ({_f((x.get(k) or {}).get('p10'), 2)} / {_f((x.get(k) or {}).get('p90'), 2)})"
                      for k in PP.ATTR_TERMS)


def report_v2(res, name):
    """REPORT.md of a --spec 2 run: v2 sections 6.3-6.8 and the section 7 verdict in its words."""
    v = res['verdict']
    L = [f'# Expected price path walk-forward, spec 2 ({name})', '',
         f"Verdict (docs/prereg_price_path_v2.md section 7): **{v['verdict']}** -- {v['meaning']}.",
         f"S1 accuracy {v['S1_accuracy']}, S2 bias {v['S2_bias']}, S3 band {v['S3_band']}, S4 attribution {v['S4_attribution']}.",
         '' if not v['failed'] else 'Failed checks: ' + ', '.join(v['failed']), '']
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
            for key, label in (('deflated', 'Survivorship-deflated (6.8; per-window rescale to SPY)'), ('defect_rows_removed', 'Data-defect rows removed (6.8)')):
                d = c[key]
                L += [f"{label}, n {d['n']}: " + '; '.join(f"{n} bias {_f(m.get('bias_median'))} MdALE {_f(m.get('mdale'))} within25 {_p(m.get('within25'))}"
                                                        for n, m in d['metrics'].items() if m.get('n'))
                      + f"; band within {_p(d['band'].get('within_band'))} below {_p(d['band'].get('below_bear'))} above {_p(d['band'].get('above_bull'))}"
                      + (f"; removed ({d['rule']}): {', '.join(d['removed']) or 'none'}" if key == 'defect_rows_removed' else ''), '']
            for key, label in (('formation_year', 'formation year'), ('stage', 'stage'), ('cyclical', 'cyclical flag'), ('multiple_bucket', 'raw multiple today'),
                               ('size_class', 'size class'), ('cross_section_cell', 'cross-section cell at formation (growth x margin)')):
                L += [f'By {label} (lane_base bias / MdALE / within25; then the baselines\' MdALE):', '',
                      '| group | n | lane bias | lane MdALE | lane within25 | ' + ' | '.join(PP.BASELINES_V2) + ' |', '|---|---|---|---|---|' + '---|' * len(PP.BASELINES_V2)]
                for g, ms in c['by'][key].items():
                    m = ms['lane_base']
                    L.append(f"| {g} | {m.get('n')} | {_f(m.get('bias_median'))} | {_f(m.get('mdale'))} | {_p(m.get('within25'))} | "
                             + ' | '.join(_f(ms[b].get('mdale')) for b in PP.BASELINES_V2) + ' |')
                L.append('')
        a = (res.get('attribution') or {}).get(h)
        if a:
            L += [f"Attribution (6.6; attributed subsample n {a['n']} of {a['n_common']} common rows; no usable v2 row at t + h {a['n_no_usable_row_at_t_plus_h']}, "
                  f"terms undefined {a['n_terms_undefined']}). Median (p10 / p90) of " + ' | '.join(PP.ATTR_TERMS) + ':', '',
                  '| lane | n | ' + ' | '.join(PP.ATTR_TERMS) + ' | max abs identity residual |', '|---|---|' + '---|' * (len(PP.ATTR_TERMS) + 1)]
            for lane in ('lane_base', 'lane_v1'):
                x = a[lane]
                if x.get('n'):
                    L.append(f"| {lane} | {x['n']} | " + _terms(x) + f" | {x['residual_max_abs']:.1e} |")
            L.append('')
            for key, grp in a['by'].items():
                L += [f'Attribution by {key} (median (p10 / p90)):', '', '| group | lane | n | ' + ' | '.join(PP.ATTR_TERMS) + ' |', '|---|---|---|' + '---|' * len(PP.ATTR_TERMS)]
                for g, both in grp.items():
                    for lane in ('lane_base', 'lane_v1'):
                        x = both[lane]
                        if x.get('n'):
                            L.append(f"| {g} | {lane} | {x['n']} | " + _terms(x) + ' |')
                L.append('')
        if lo.get('metrics'):
            L += ['Lane-only sample (lane rows with a realized price; no engine, EPS or lane_v1 requirement):', '',
                  '| predictor | n | bias | MdALE | within25 |', '|---|---|---|---|---|']
            for n, m in lo['metrics'].items():
                if m.get('n'):
                    L.append(f"| {n} | {m['n']} | {_f(m['bias_median'])} | {_f(m['mdale'])} | {_p(m['within25'])} |")
            lb = lo['band']
            L += ['', f"Lane-only band: within {_p(lb.get('within_band'))}, below bear {_p(lb.get('below_bear'))}, above bull {_p(lb.get('above_bull'))}; "
                  + 'head to head ' + ', '.join(f"vs {k} {_p(v['share_lane_closer'])}" for k, v in lo['head_to_head'].items()), '']
    dg = res['diagnostics']
    L += ['## Diagnostics (6.7; no effect on the price)', '',
          f"Rows {dg['n_rows']}: " + ', '.join(f'{k} {v}' for k, v in dg['status_counts'].items())
          + f". Ordering assertions (must be 0): **{dg['ordering_assertions']}**. Errors: " + (', '.join(f'{k} {v}' for k, v in dg['errors'].items()) or 'none') + '.', '']
    if dg.get('flag_shares'):
        pl, go = dg['premium_log'], dg['gordon_over_cross_section']
        dist = lambda d: ', '.join(f"{k} {_f(d.get(k))}" for k in ('p10', 'p25', 'median', 'p75', 'p90')) + f" (n {d.get('n')})"
        L += [f"L_0 (log premium over the class median, clipped at +-ln 3): {dist(pl)}; clipped in {_p(pl['share_clipped'])} of rows; before the clip: {dist(pl['raw'])}.",
              f"Spec 1 Gordon multiple / cross-section median, today: {dist(go['today'])}; year 5 (base): {dist(go['year_5_base'])}.",
              'Cell usage (share of lookups served by a cell / the growth row / the whole cross-section): '
              + '; '.join(f"{k}: " + ', '.join(f'{lv} {_p(x)}' for lv, x in v.items()) for k, v in dg['cell_usage'].items()) + '.',
              'Margin targets set by: ' + '; '.join(f"{s}: " + ', '.join(f'{b} {_p(x)}' for b, x in v.items()) for s, v in dg['margin_target_basis'].items()) + '.',
              f"Raw multiple today: {dist(dg['multiple_today'])}.",
              'Flag shares: ' + ', '.join(f'{k} {_p(v)}' for k, v in dg['flag_shares'].items()), '']
        L += ['| size class | n | median lambda (base) | median 5-year base-rate share (base) | share of rows shrunk |', '|---|---|---|---|---|']
        for k, x in dg['by_size_class'].items():
            L.append(f"| {k} | {x['n']} | {_f(x['median_lambda_base'])} | {_f(x['median_share_5y_base'])} | {_p(x['share_lambda_shrunk_base'])} |")
        L.append('')
    if dg.get('tables'):
        L += ['Cross-section tables (raw trailing P/E within [1, 500]) per formation:', '',
              '| formation | n | outside range | p25 | median | p75 | cells with >= CELL_MIN / cells | median by growth bucket (n) |', '|---|---|---|---|---|---|---|---|']
        for fm, t in dg['tables'].items():
            L.append(f"| {fm} | {t['n']} | {t['n_outside_pe_range']} | {_f(t['p25'], 1)} | {_f(t['median'], 1)} | {_f(t['p75'], 1)} | {t['cells_with_cell_min']} / {t['cells']} | "
                     + ', '.join(f"{g} {_f(x['median'], 1)} ({x['n']})" for g, x in t['by_growth'].items()) + ' |')
        L.append('')
    L += ['## Checks (section 7)', '']
    for k, x in v['checks'].items():
        L.append(f"- {k}: {'pass' if x.get('pass') else ('FAIL' if x.get('pass') is False else 'not testable')} -- "
                 + ', '.join(f'{a} {_f(b) if isinstance(b, float) else b}' for a, b in x.items() if a != 'pass'))
    L += ['', '## Caveats', ''] + [f'- {c}' for c in res['caveats']]
    return '\n'.join(L) + '\n'


def _read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def _pipeline_pass(a, comp, syms, formations, path, spec):
    """The slow pass (pipeline stages per symbol x formation) appended to path; resumable by (symbol, formation)."""
    done = {(r['symbol'], r['formation']) for r in _read_jsonl(path)} if os.path.exists(path) else set()
    jobs = [(s, [f for f in formations if (s, f) not in done]) for s in syms]
    jobs = [j for j in jobs if j[1]]
    if not jobs:
        return
    rates = E.treasury(a.rates)
    with Pool(a.workers, initializer=_init, initargs=(a.prices, rates, comp, spec)) as pool, open(path, 'a', encoding='utf-8') as f:
        for k, rows in enumerate(pool.imap_unordered(_work, jobs), 1):
            for r in rows:
                f.write(json.dumps(r, default=str) + '\n')
            f.flush()
            if k % 25 == 0:
                print(f'{k}/{len(jobs)} companies', flush=True)


def _paths_pass(a, formations, anchors_path, rows_path, tables_path):
    """Spec 2, pass 2: per formation the cross-section table, then the paths; rewrites rows.jsonl and tables.json."""
    by_fm = {}
    for r in _read_jsonl(anchors_path):
        if r['formation'] in formations:
            by_fm.setdefault(r['formation'], []).append(r)
    order = sorted(by_fm)
    jobs = [(by_fm[fm], a.name) for fm in order]
    if a.workers > 1 and len(jobs) > 1:
        with Pool(min(a.workers, len(jobs))) as pool:
            results = pool.map(_pass2, jobs)
    else:
        results = [_pass2(j) for j in jobs]
    tables = {}
    with open(rows_path, 'w', encoding='utf-8') as f:
        for fm, (rows, table) in zip(order, results):
            for r in rows:
                f.write(json.dumps(r, default=str) + '\n')
            if table:
                tables[fm] = table
    with open(tables_path, 'w', encoding='utf-8') as f:
        json.dump(tables, f, indent=1)
    print(f'pass 2: {sum(len(r) for r, _ in results)} rows over {len(order)} formations; tables ' + ', '.join(f"{fm[:4]} n {t['n']}" for fm, t in tables.items()), flush=True)


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
    ap.add_argument('--spec', type=int, choices=PP.SPEC_VERSIONS, default=PP.SPEC_VERSION,
                    help='1: docs/prereg_price_path.md (default, unchanged); 2: docs/prereg_price_path_v2.md (two passes)')
    ap.add_argument('--v1-rows', default=None, help='--spec 2: spec-1 rows.jsonl of the same universe (the lane_v1 baseline)')
    a = ap.parse_args(argv)
    if a.spec == PP.SPEC_VERSION_2 and not (a.v1_rows and os.path.exists(a.v1_rows)):
        ap.error('--spec 2 needs --v1-rows: spec-1 rows of the same universe (a --spec 1 run), the lane_v1 baseline of v2 section 6.4')
    comp = os.path.join(a.folder, 'companies')
    os.makedirs(a.out, exist_ok=True)
    rows_path = os.path.join(a.out, 'rows.jsonl')
    formations = [f'{y}-{a.month}-01' for y in range(a.start, a.end + 1)]
    syms = a.symbols or [e['symbol'] for e in json.load(open(os.path.join(comp, f'QUEUE_{a.name}.json')))['queue'] if e.get('symbol')]
    syms = [s for s in syms if os.path.exists(os.path.join(comp, s, 'company.json'))]
    tables_path = os.path.join(a.out, 'tables.json')
    if not a.evaluate_only:
        if a.spec == PP.SPEC_VERSION:
            _pipeline_pass(a, comp, syms, formations, rows_path, a.spec)
        else:
            anchors_path = os.path.join(a.out, 'anchors.jsonl')
            _pipeline_pass(a, comp, syms, formations, anchors_path, a.spec)
            _paths_pass(a, formations, anchors_path, rows_path, tables_path)
    rows = [r for r in _read_jsonl(rows_path)
            if r['formation'] in formations and (not a.symbols or r['symbol'] in syms) and r.get('status') != 'skipped']
    engine = []
    if a.engine_rows and os.path.exists(a.engine_rows):
        engine = [r for r in _read_jsonl(a.engine_rows) if r.get('base_irr') is not None]
    needed = {r['symbol'] for r in rows} | {'SPY'}
    prices = {s: load_prices(os.path.join(a.prices, f'{s}.json')) for s in needed if os.path.exists(os.path.join(a.prices, f'{s}.json'))}
    run = {'universe': a.folder, 'name': a.name, 'formations': formations, 'engine_rows': a.engine_rows, 'n_engine_rows': len(engine),
           'n_rows': len(rows), 'status_counts': {k: sum(1 for r in rows if r.get('status') == k) for k in ('ok', 'excluded', 'error')}}
    if a.spec == PP.SPEC_VERSION:
        res = PP.evaluate(rows, prices, engine)
        res['run'] = run
        text, show = report(res, a.name), ('lane_base', 'random_walk', 'drift_7', 'analyst_const', 'engine_irr')
    else:
        v1_rows = [r for r in _read_jsonl(a.v1_rows) if r.get('status') == 'ok' and r['formation'] in formations]
        with open(tables_path, encoding='utf-8') as f:
            tables = json.load(f)
        res = PP.evaluate_v2(rows, prices, engine, v1_rows, tables)
        res['run'] = dict(run, spec=a.spec, v1_rows=a.v1_rows, n_v1_rows_ok=len(v1_rows),
                          tables={fm: {'as_of': t['as_of'], 'n': t['n'], 'sha256': t['sha256'], 'source': t['source']} for fm, t in sorted(tables.items())})
        text, show = report_v2(res, a.name), ('lane_base', 'lane_v1', 'random_walk', 'drift_7', 'analyst_const', 'engine_irr')
    with open(os.path.join(a.out, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=1, default=str)
    with open(os.path.join(a.out, 'REPORT.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    print(f"verdict {res['verdict']['verdict']}; failed: {res['verdict']['failed']}")
    for h in PP.VERDICT_HORIZONS:
        c = (res['horizons'].get(h) or {}).get('common') or {}
        if c.get('metrics'):
            print(f'h{h} n={c["n"]}: ' + '; '.join(f"{n} bias {_f(m['bias_median'])} MdALE {_f(m['mdale'])} w25 {_p(m['within25'])}"
                                                    for n, m in c['metrics'].items() if m.get('n') and n in show)
                  + f"; band {_p(c['band']['within_band'])} / below {_p(c['band']['below_bear'])} / above {_p(c['band']['above_bull'])}")


if __name__ == '__main__':
    main()
