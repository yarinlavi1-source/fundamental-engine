"""Forecast tracker: score every published lean base case in research/companies against SEC-reported results and the
latest close. Measurement only (fundamental_engine/forecast_tracker.py holds the logic and the rules of comparison).

Usage: python scripts/forecast_tracker.py [--as-of YYYY-MM-DD] [--offline | --refresh] [--out research]
Reads research/companies/<T>/<date>/lean_params*.json (the main file is lean_params.json; variants are listed, not
scored), runs scripts/lean_model.py on each, and writes research/FORECAST_TRACKER_he.md and research/forecast_tracker.json
(company research only: no holdings). Network: SEC companyfacts and, for a company with a new period, its filing index
(results releases, 8-K item 2.02), both with SEC_USER_AGENT from the environment (never printed or saved); Yahoo daily
closes. Caches live under runs/ (gitignored): runs/sec_cache and runs/forecast_tracker.
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import lean_model  # noqa: E402
from collect_universe import TICKERS, fetch  # noqa: E402
from fundamental_engine import forecast_tracker as FT  # noqa: E402
from fundamental_engine import holdings_watch as HW  # noqa: E402
from fundamental_engine.sec import fetch_companyfacts  # noqa: E402

SUBS = 'https://data.sec.gov/submissions/CIK{:010d}.json'
YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d'
BROWSER = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
STORE = os.path.join('research', 'companies')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def _fresh(path, today):
    return os.path.exists(path) and datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).date().isoformat() >= today


def valuations(root):
    """(entries, custom): one entry per company and dated folder with lean params; companies with none are custom."""
    entries, custom = [], []
    for folder in sorted(glob.glob(os.path.join(root, STORE, '*'))):
        t = os.path.basename(folder)
        if not os.path.isdir(folder):
            continue
        found = False
        for dated in sorted(glob.glob(os.path.join(folder, '*'))):
            files = sorted(glob.glob(os.path.join(dated, 'lean_params*.json')))
            if not files:
                continue
            found = True
            main = os.path.join(dated, 'lean_params.json')
            main = main if main in files else files[0]
            entries.append({'ticker': t, 'folder_date': os.path.basename(dated), 'path': os.path.relpath(main, root),
                            'variants_not_scored': [os.path.relpath(f, root) for f in files if f != main]})
        if not found:
            custom.append({'ticker': t, 'reason': 'custom model: scored by hand (no lean params file)'})
    return entries, custom


def ticker_map(cache, ua, offline):
    path = os.path.join(cache, 'company_tickers_exchange.json')
    if not os.path.exists(path):
        if offline or '@' not in ua:
            return {}
        _, body = fetch(TICKERS, {'User-Agent': ua, 'Accept-Encoding': 'gzip'})
        os.makedirs(cache, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(body)
    m = _load(path)
    i_cik, i_tic = m['fields'].index('cik'), m['fields'].index('ticker')
    out = {}
    for r in m['data']:
        out.setdefault(str(r[i_tic]).upper(), int(r[i_cik]))
    return out


def companyfacts(cik, cache, ua, today, offline, refresh):
    """(payload or None, note). The cached copy of today stands; otherwise it is refetched (unless --offline)."""
    path = os.path.join(cache, f'CIK{cik:010d}.json')
    try:
        if offline or (_fresh(path, today) and not refresh):
            return (_load(path), 'cached copy') if os.path.exists(path) else (None, 'no cached companyfacts (offline)')
        if '@' not in ua:
            return (_load(path), 'cached copy (SEC_USER_AGENT not set)') if os.path.exists(path) else (None, 'SEC_USER_AGENT not set')
        return fetch_companyfacts(cik, ua, cache, refresh=True), 'fetched'
    except Exception as e:                                         # noqa: BLE001 (a failed company is a recorded gap)
        if os.path.exists(path):
            return _load(path), f'fetch failed ({type(e).__name__}): cached copy'
        return None, f'companyfacts {type(e).__name__}'


def release_dates(cik, cache, ua, today, offline):
    """Filing dates of results releases (8-K item 2.02) from the SEC filing index; None when not available."""
    path = os.path.join(cache, f'submissions_CIK{cik:010d}.json')
    if not _fresh(path, today) and not offline and '@' in ua:
        try:
            time.sleep(.2)
            status, body = fetch(SUBS.format(cik), {'User-Agent': ua, 'Accept-Encoding': 'gzip'})
            if status == 200:
                r = json.loads(body).get('filings', {}).get('recent', {})
                _save(path, {'form': r.get('form'), 'filingDate': r.get('filingDate'), 'items': r.get('items')})
        except Exception:                                          # noqa: BLE001
            pass
    if not os.path.exists(path):
        return None
    r = _load(path)
    return sorted(d for f, d, it in zip(r.get('form') or [], r.get('filingDate') or [], r.get('items') or [])
                  if str(f).startswith('8-K') and '2.02' in str(it))


def last_close(symbol, cache, today, offline):
    """{'value', 'date'} of the latest daily close up to today (Yahoo), or None."""
    path = os.path.join(cache, 'prices', f'{symbol}_{today}.json')
    if os.path.exists(path):
        return _load(path)
    if offline:
        return None
    end = date.fromisoformat(today)
    p1 = int(datetime(*(end - timedelta(days=12)).timetuple()[:3], tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(*(end + timedelta(days=1)).timetuple()[:3], tzinfo=timezone.utc).timestamp())
    try:
        time.sleep(.3)
        status, body = fetch(YAHOO.format(symbol, p1, p2), BROWSER)
        res = (json.loads(body).get('chart', {}).get('result') or [None])[0] if status == 200 else None
        if not res or not res.get('timestamp'):
            return None
        off = res.get('meta', {}).get('gmtoffset') or 0
        bars = [(datetime.fromtimestamp(ts + off, timezone.utc).date().isoformat(), c)
                for ts, c in zip(res['timestamp'], res['indicators']['quote'][0]['close']) if c is not None]
        bars = [b for b in bars if b[0] <= today]
        if not bars:
            return None
        out = {'value': round(bars[-1][1], 4), 'date': bars[-1][0], 'currency': res.get('meta', {}).get('currency'), 'source': 'Yahoo daily close'}
        _save(path, out)
        return out
    except Exception:                                              # noqa: BLE001
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--as-of', default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument('--out', default='research', help='folder for FORECAST_TRACKER_he.md and forecast_tracker.json')
    ap.add_argument('--offline', action='store_true', help='cached data only')
    ap.add_argument('--refresh', action='store_true', help='refetch companyfacts even when today\'s copy is cached')
    a = ap.parse_args(argv)
    ua = os.environ.get('SEC_USER_AGENT', '')
    sec_cache = os.path.join(ROOT, 'runs', 'sec_cache')
    cache = os.path.join(ROOT, 'runs', 'forecast_tracker')
    entries, custom = valuations(ROOT)
    tmap, rows, gaps = None, [], []
    for e in entries:
        t = e['ticker']
        params = _load(os.path.join(ROOT, e['path']))
        ok, why = FT.lean_format(params)
        if not ok:
            row = FT.score(t, params, None, None, a.as_of, params_path=e['path'])
            rows.append(row)
            continue
        result = lean_model.run(params)
        cik = HW.find_cik(ROOT, t)
        if cik is None:
            tmap = ticker_map(sec_cache, ua, a.offline) if tmap is None else tmap
            cik = tmap.get(t)
        payload, note = (None, 'CIK not found') if cik is None else companyfacts(cik, sec_cache, ua, a.as_of, a.offline, a.refresh)
        px = None if params.get('as_of', '')[:10] > a.as_of else last_close(str(params.get('ticker') or t).upper(), cache, a.as_of, a.offline)
        if px and px.get('currency') not in (None, 'USD'):
            gaps.append(f"{t}: the close is in {px['currency']}, not scored")
            px = None
        row = FT.score(t, params, result, payload, a.as_of, price=px, params_path=e['path'])
        if row['status'] == 'scored' and cik is not None:          # a new period: is it after the results release?
            rel = release_dates(cik, cache, ua, a.as_of, a.offline)
            if rel is not None:
                row = FT.score(t, params, result, payload, a.as_of, price=px, release_dates=rel, params_path=e['path'])
        row.update(cik=cik, variants_not_scored=e['variants_not_scored'], data_note=note)
        if payload is None:
            gaps.append(f'{t}: {note}')
        if px is None:
            gaps.append(f'{t}: no close')
        rows.append(row)
    doc = FT.build(rows, custom, a.as_of, sources={
        'actuals': 'SEC companyfacts (data.sec.gov), results release dates from the SEC filing index (8-K item 2.02)',
        'prices': 'Yahoo daily close', 'model': 'scripts/lean_model.py run() on each lean_params.json (base scenario)'})
    doc['gaps'] = sorted(set(gaps))
    os.makedirs(os.path.join(ROOT, a.out), exist_ok=True)
    jp, mp = os.path.join(ROOT, a.out, 'forecast_tracker.json'), os.path.join(ROOT, a.out, 'FORECAST_TRACKER_he.md')
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(FT.render_he(doc))
    print(json.dumps({'rows': len(rows), 'summary': {k: v for k, v in doc['summary'].items() if k != 'items'}, 'gaps': doc['gaps'],
                      'json': os.path.relpath(jp, ROOT), 'markdown': os.path.relpath(mp, ROOT)}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
