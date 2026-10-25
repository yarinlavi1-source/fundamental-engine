"""Measure the market's own country-risk add-on on a large-cap sweep (market_context.COUNTRY_RISK_MARKET_RATE).

    python scripts/measure_country_risk_market.py runs/universe_2026-10-21 --name large500 --as-of 2026-10-21 \
        --out runs/macro/country_risk_market_2026-10-21.json

Why: the engine's ERP is Damodaran's S&P 500 implied premium and its index return is the cap-weighted implied return of the
largest US companies. Both are prices of companies that already sell abroad (about 40% of S&P 500 revenue), so they already
carry the market's average country exposure. A company's full add-on on top of them counts that average twice, and breaks
the invariant of forecast.MARKET_NEUTRAL_ADJUSTMENT (the value-weighted average company is discounted at risk-free + ERP).

What it measures, with the engine's own method (market_context.country_risk on each company's FactBook, no overrides): for
every sweep company with a country-risk row (status applied or partial), its add-on a_c and the share of the chosen channel
mapped to a country or region m_c (unmapped aggregates and the part not itemized are excluded from m_c), weighted by market
value w_c:

    rate = sum_c w_c a_c / sum_c w_c m_c          (add-on per unit of mapped exposure, value-weighted)

Weights: the listing feed's market value saved with each company (raw/listing__price-<symbol>__current.json); the universe
rows' value (rows.json, calibration.market_implied's weights) is reported next to it: on 2026-10-21 it counts Alphabet at
2.3x its listing value (both share classes priced at one class), and Alphabet alone carries about half of the weighted add-on.

The forecast then nets -rate x m_c next to each company's add-on (its own row, country_risk_market_neutral), so on this sweep
the value-weighted net row is exactly zero: an average-exposure company is discounted at risk-free + ERP as before, a
domestic one below it, a more exposed one above it, and an unmapped share counts as average (neither added nor netted).
Companies without a readable split get no row at all (no add-on, no netting). The coverage is reported: the annual reports
saved for the sweep cover part of its value only, so the rate is a sample estimate.
"""
import argparse
import json
import os
import sys
from statistics import median

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine import market_context as M, pipeline  # noqa: E402


def measure(folder, name, as_of):
    comp = os.path.join(folder, 'companies')
    with open(os.path.join(comp, f'QUEUE_{name}.json'), encoding='utf-8') as f:
        symbols = [e['symbol'] for e in json.load(f)['queue'] if e.get('symbol')]
    with open(os.path.join(folder, 'rows.json'), encoding='utf-8') as f:
        rows_caps = {r['symbol']: (r.get('metrics') or {}).get('market_cap') or r.get('market_cap') for r in json.load(f)}

    def listing_cap(s):
        p = os.path.join(comp, s, 'raw', f'listing__price-{s}__current.json')
        if not os.path.exists(p):
            return None
        with open(p, encoding='utf-8') as f:
            return json.load(f).get('market_cap')
    caps = {s: listing_cap(s) for s in symbols}
    rows, status = [], {}
    for s in symbols:
        raw = os.path.join(comp, s, 'raw')
        if not os.path.isdir(raw):
            status['no_raw'] = status.get('no_raw', 0) + 1
            continue
        with open(os.path.join(comp, s, 'company.json'), encoding='utf-8') as f:
            company = json.load(f)
        try:
            fb = pipeline.factbook_from_run_dir(raw, company, as_of)
            cr = M.country_risk(fb, {})
        except Exception as e:                       # a FactBook that does not build is counted, not guessed
            status['error'] = status.get('error', 0) + 1
            rows.append({'symbol': s, 'status': 'error', 'error': f'{type(e).__name__}: {e}'[:160], 'cap': caps.get(s),
                         'rows_cap': rows_caps.get(s)})
            continue
        status[cr['status']] = status.get(cr['status'], 0) + 1
        ch = cr.get('channel')
        rows.append({'symbol': s, 'status': cr['status'], 'cap': caps.get(s), 'rows_cap': rows_caps.get(s), 'channel': ch,
                     'add_on': cr.get('add_on'),
                     'mapped_share': (cr.get(ch) or {}).get('mapped_share') if ch else None,
                     'annual_report': os.path.exists(os.path.join(raw, 'sec__annual_report.htm'))})
    live = [r for r in rows if r['status'] in ('applied', 'partial')]

    def rate(sel, key='cap'):
        sel = [r for r in sel if r.get(key)]
        wm = sum(r[key] * r['mapped_share'] for r in sel)
        return sum(r[key] * r['add_on'] for r in sel) / wm if wm else None

    def weighted(sel, field, key='cap'):
        sel = [r for r in sel if r.get(key)]
        w = sum(r[key] for r in sel)
        return sum(r[key] * r[field] for r in sel) / w if w else None
    top = max((r for r in live if r['cap']), key=lambda r: r['cap'] * r['add_on'], default=None)
    total = sum(caps.get(s) or 0 for s in symbols)
    full = [r for r in live if r['status'] == 'applied']
    out = {'as_of': as_of, 'universe': os.path.basename(os.path.normpath(folder)), 'queue': name, 'n_sweep': len(symbols),
           'status_counts': status, 'n_with_row': len(live), 'n_applied': len(full), 'n_partial': len(live) - len(full),
           'coverage_by_value': sum(r['cap'] or 0 for r in live) / total if total else None,
           'rate': rate(live), 'rate_rows_json_weights': rate(live, 'rows_cap'),
           'rate_without_largest_contributor': rate([r for r in live if r is not top]),
           'largest_contributor': {'symbol': top['symbol'], 'share_of_weighted_add_on':
                                   top['cap'] * top['add_on'] / sum(r['cap'] * r['add_on'] for r in live if r['cap'])} if top else None,
           'rate_applied_only': rate(full),
           'value_weighted_add_on': weighted(live, 'add_on'), 'value_weighted_mapped_share': weighted(live, 'mapped_share'),
           'equal_weighted_add_on': sum(r['add_on'] for r in live) / len(live) if live else None,
           'median_add_on': median(r['add_on'] for r in live) if live else None,
           'table': {k: M.country_table()[k] for k in ('title', 'url', 'as_of')},
           'formula': 'rate = sum(w x add_on) / sum(w x mapped_share) over sweep companies with a country-risk row; w = listing market '
                      'value; net row = -rate x mapped_share',
           'rows': sorted(rows, key=lambda r: -(r['cap'] or 0))}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--name', default='large500')
    ap.add_argument('--as-of', required=True)
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    out = measure(a.folder, a.name, a.as_of)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != 'rows'}, indent=1))


if __name__ == '__main__':
    main()
