"""Forecast tracker: score the FROZEN base cases (research/ledger.jsonl) against SEC-reported results, manual scorecards and
the latest close. Measurement only (fundamental_engine/forecast_tracker.py holds the logic and the rules of comparison).

Usage: python scripts/forecast_tracker.py [--as-of YYYY-MM-DD] [--offline | --refresh] [--out research] [--no-ledger]
Reads the lean_freeze thesis and forecast rows of research/ledger.jsonl (numbers from the ledger, never a rerun), checks
that the params and result files still hold the frozen sha256 (a changed file is reported and only the ledger rows are
used), lists research/companies/<T>/<date>/lean_params.json folders that were never frozen as 'descriptive only', reads
research/companies/<T>/<date>/scorecard*.json (manual outcome form for custom models, banks and foreign filers), writes
one outcome row per resolved forecast to the ledger through the Ledger API (--no-ledger: none), and writes
research/FORECAST_TRACKER_he.md and research/forecast_tracker.json (company research only: no holdings).
Network: SEC companyfacts and the SEC filing index (8-K item 2.02, 10-Q, 10-K, 6-K, 20-F, 40-F), both with
SEC_USER_AGENT from the environment (never printed or saved); Yahoo daily closes. Caches live under runs/ (gitignored):
runs/sec_cache and runs/forecast_tracker.
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
from collect_universe import TICKERS, fetch  # noqa: E402
from fundamental_engine import forecast_tracker as FT  # noqa: E402
from fundamental_engine import holdings_watch as HW  # noqa: E402
from fundamental_engine import lean_freeze as LF  # noqa: E402
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


def _sha(path):
    return LF.sha256_file(path) if os.path.isfile(path) else None


def _known_split(folder):
    path = os.path.join(folder, 'valuation.json')
    if not os.path.isfile(path):
        return None
    return ((_load(path).get('share_basis') or {}).get('known_split')) or None


def valuations(root, theses):
    """({ticker: [candidate]}, custom): the frozen theses of research/ledger.jsonl and the lean folders that were never
    frozen (descriptive only). A candidate holds the frozen path (ledger_path) or, for an unfrozen folder, the path of
    its saved result (model_path, no rerun). Tickers with neither are listed as custom."""
    out, custom, frozen_results = {}, [], set()
    for t, ts in theses.items():
        for th in ts:
            rec = th['record']
            files = rec.get('files') or {}
            rpath = os.path.join(root, files['result']['path'])
            ppath = os.path.join(root, files['params']['path']) if files.get('params') else None
            frozen_results.add(files['result']['path'])
            vals = None
            if os.path.isfile(rpath):
                try:
                    sc = (_load(rpath).get('scenarios') or {})
                    vals = {k: (sc.get(k) or {}).get('value_today', (sc.get(k) or {}).get('today')) for k in ('bear', 'base', 'bull') if k in sc}
                except (ValueError, OSError):
                    vals = None
            check = FT.file_check(rec, {'params_sha256': _sha(ppath) if ppath else None, 'result_sha256': _sha(rpath),
                                        'result_values_today': vals})
            params = _load(ppath) if ppath and check['params'] == 'same' else None
            path = FT.ledger_path(th, params)
            ks = _known_split(os.path.join(root, rec['folder']))
            if ks:
                path['known_split'] = ks
            out.setdefault(t, []).append({'thesis_id': th['thesis_id'], 'as_of': th['as_of'], 'recorded_at': th['recorded_at'],
                                          'kind': rec.get('kind'), 'model': rec.get('model'), 'path': path, 'file_check': check,
                                          'params_path': files.get('params', {}).get('path'), 'on_time': rec.get('on_time')})
    for folder in sorted(glob.glob(os.path.join(root, STORE, '*'))):
        t = os.path.basename(folder)
        if not os.path.isdir(folder):
            continue
        for dated in sorted(glob.glob(os.path.join(folder, '*'))):
            pf, rf = os.path.join(dated, 'lean_params.json'), os.path.join(dated, 'lean_result.json')
            rel = os.path.relpath(rf, root).replace(os.sep, '/')
            if not (os.path.isfile(pf) and os.path.isfile(rf)) or rel in frozen_results:
                continue
            params, result = _load(pf), _load(rf)
            ok, _ = FT.lean_format(params)
            try:
                path = FT.model_path(params, result) if ok else None
            except (KeyError, TypeError, ValueError):
                path = None
            out.setdefault(t, []).append({'thesis_id': None, 'as_of': str(params.get('as_of') or os.path.basename(dated))[:10],
                                          'recorded_at': None, 'kind': 'lean' if path else 'custom', 'path': path, 'file_check': None,
                                          'params_path': os.path.relpath(pf, root)})
        if t not in out:
            custom.append({'ticker': t, 'reason': 'no frozen thesis and no lean valuation files (heavy-engine or gather-only folders)'})
    return out, custom


def scorecards(root, by_id):
    """{ticker: [scorecard row]} from research/companies/<T>/<date>/scorecard*.json."""
    out = {}
    for p in sorted(glob.glob(os.path.join(root, STORE, '*', '*', 'scorecard*.json'))):
        try:
            sc = _load(p)
        except ValueError as e:
            sc = {'ticker': os.path.basename(os.path.dirname(os.path.dirname(p))), 'problems': [f'not JSON: {e}']}
        sc['_path'] = os.path.relpath(p, root)
        row = FT.score_scorecard(sc, by_id)
        out.setdefault(row['ticker'] or os.path.basename(os.path.dirname(os.path.dirname(p))), []).append(row)
    return out


def write_outcomes(ledger_path, cands):
    """One outcome row per forecast through the Ledger API; an outcome on file is never rewritten."""
    L = LF.open_ledger(ledger_path)
    have = {r['forecast_id'] for r in L.lines() if r.get('type') == 'outcome'}
    res = {'written': [], 'existing': [], 'refused': []}
    for c in cands:
        if c['forecast_id'] in have:
            res['existing'].append(c['forecast_id'])
            continue
        try:
            L.outcome(forecast_id=c['forecast_id'], actual=c['actual'], source_id=c['source_id'], unit=c['unit'],
                      currency=c['currency'], basis=c['basis'], observed_at=c['observed_at'])
            res['written'].append(c['forecast_id'])
            have.add(c['forecast_id'])
        except ValueError as e:
            res['refused'].append({'forecast_id': c['forecast_id'], 'reason': str(e)})
    return res


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


def submissions(cik, cache, ua, today, offline, refresh=False):
    """filings.recent of the SEC filing index ({'form', 'filingDate', 'items'}), cached per day (--refresh refetches);
    None when not available."""
    path = os.path.join(cache, f'submissions_CIK{cik:010d}.json')
    if (refresh or not _fresh(path, today)) and not offline and '@' in ua:
        try:
            time.sleep(.2)
            status, body = fetch(SUBS.format(cik), {'User-Agent': ua, 'Accept-Encoding': 'gzip'})
            if status == 200:
                r = json.loads(body).get('filings', {}).get('recent', {})
                _save(path, {'form': r.get('form'), 'filingDate': r.get('filingDate'), 'items': r.get('items')})
        except Exception:                                          # noqa: BLE001
            pass
    return _load(path) if os.path.exists(path) else None


def release_dates(recent):
    """Filing dates of results releases (8-K item 2.02); None without the filing index."""
    if recent is None:
        return None
    return sorted(d for f, d, it in zip(recent.get('form') or [], recent.get('filingDate') or [], recent.get('items') or [])
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
    ap.add_argument('--ledger', default=LF.LEDGER, help='the lean ledger (default research/ledger.jsonl)')
    ap.add_argument('--offline', action='store_true', help='cached data only')
    ap.add_argument('--refresh', action='store_true', help='refetch companyfacts and the filing index even when today\'s copy is cached')
    ap.add_argument('--no-ledger', action='store_true', help='write no outcome rows')
    a = ap.parse_args(argv)
    ua = os.environ.get('SEC_USER_AGENT', '')
    sec_cache = os.path.join(ROOT, 'runs', 'sec_cache')
    cache = os.path.join(ROOT, 'runs', 'forecast_tracker')
    ledger_file = os.path.join(ROOT, a.ledger)
    L = LF.open_ledger(ledger_file)
    lines = L.lines()
    verify = L.verify()
    theses = FT.frozen_theses(lines)
    by_id = {t['thesis_id']: t for ts in theses.values() for t in ts}
    cands, custom = valuations(ROOT, theses)
    cards = scorecards(ROOT, by_id)
    tmap, rows, gaps = None, [], []
    for t in sorted(set(cands) | set(cards)):
        cs = cands.get(t, [])
        cik = HW.find_cik(ROOT, t)
        if cik is None:
            tmap = ticker_map(sec_cache, ua, a.offline) if tmap is None else tmap
            cik = tmap.get(t)
        payload, note = (None, 'CIK not found') if cik is None else companyfacts(cik, sec_cache, ua, a.as_of, a.offline, a.refresh)
        recent = None if cik is None else submissions(cik, cache, ua, a.as_of, a.offline, a.refresh)
        newest = max(cs, key=lambda c: c['as_of']) if cs else None
        px = None
        if newest and newest['as_of'] <= a.as_of:
            px = last_close(t, cache, a.as_of, a.offline)
            if px and px.get('currency') not in (None, 'USD'):
                gaps.append(f"{t}: the close is in {px['currency']}, not scored")
                px = None
        earliest = min((c['as_of'] for c in cs), default=a.as_of)
        filings = FT.filing_events(recent, earliest, a.as_of) if recent else []
        got = FT.company_rows(t, cs, payload, a.as_of, price=px, release_dates=release_dates(recent), filings=filings,
                              scorecards=cards.get(t, [])) if cs else list(cards.get(t, []))
        for r in got:
            r.update(cik=cik, data_note=note)
        rows += got
        if payload is None:
            gaps.append(f'{t}: {note}')
        if recent is None:
            gaps.append(f'{t}: no SEC filing index (new reports from 6-K / 20-F / 40-F cannot be seen)')
        if cs and px is None:
            gaps.append(f'{t}: no close')
    oc_cands = FT.outcome_candidates(rows, by_id)
    outcomes = {'candidates': len(oc_cands), 'written': [], 'existing': [], 'refused': []}
    if not a.no_ledger and oc_cands:
        outcomes.update(write_outcomes(ledger_file, oc_cands))
    elif a.no_ledger:
        outcomes['note'] = '--no-ledger: nothing written'
    review = FT.review_changes(list(by_id.values()))
    doc = FT.build(rows, custom, a.as_of, review=review, outcomes=outcomes,
                   ledger={'path': a.ledger, 'verify': verify, 'theses': len(by_id),
                           'outcomes_on_file': sum(r.get('type') == 'outcome' for r in LF.open_ledger(ledger_file).lines())},
                   sources={'model': 'research/ledger.jsonl thesis and forecast rows (lean_freeze); params read only while their sha256 '
                                     'equals the frozen one; unfrozen folders from their saved lean_result.json (no rerun)',
                            'actuals': 'SEC companyfacts (data.sec.gov); reports from the SEC filing index (8-K item 2.02, 6-K, 20-F, 40-F)',
                            'scorecards': 'research/companies/<T>/<date>/scorecard*.json (manual, primary sources linked)',
                            'prices': 'Yahoo daily close'})
    doc['gaps'] = sorted(set(gaps))
    os.makedirs(os.path.join(ROOT, a.out), exist_ok=True)
    jp, mp = os.path.join(ROOT, a.out, 'forecast_tracker.json'), os.path.join(ROOT, a.out, 'FORECAST_TRACKER_he.md')
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(FT.render_he(doc))
    print(json.dumps({'rows': len(rows), 'summary': {k: v for k, v in doc['summary'].items() if k != 'items'},
                      'outcomes': {k: (len(v) if isinstance(v, list) else v) for k, v in outcomes.items()},
                      'review_bias': {k: review[k] for k in ('n', 'companies', 'median_change_pct', 'conclusion')},
                      'ledger': doc['ledger'], 'gaps': doc['gaps'], 'json': os.path.relpath(jp, ROOT), 'markdown': os.path.relpath(mp, ROOT)},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
