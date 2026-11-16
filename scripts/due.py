"""Which theses are due: reports in the next N days, reports that passed without a valuation update, held names whose
valuation is older than their last report, and open conditions (engine upgrade P9, 15.11.2026).

Usage: python scripts/due.py [--days 30] [--today D] [--positions runs/portfolio_<D>/positions.json] [--out DIR]
                             [--refresh]
The next report comes from the saved valuation record, else the handoff (research/technical/<T>/<date>/fundamental.json),
else the lean ledger (research/ledger.jsonl). No network: the SEC filing index is read from the tracker's cached copies
(runs/forecast_tracker/submissions_CIK*.json) when they exist; --refresh refetches it (SEC_USER_AGENT needed). With
--positions the held names are marked; that list is private, so --out must then be under runs/ (default: print only).
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fundamental_engine import forecast_tracker as FT  # noqa: E402
from fundamental_engine import holdings_watch as HW  # noqa: E402
from fundamental_engine import portfolio_health as PH  # noqa: E402

SUBS = 'https://data.sec.gov/submissions/CIK{:010d}.json'
CACHE = os.path.join(ROOT, 'runs', 'forecast_tracker')
TICKER_MAPS = (os.path.join(ROOT, 'runs', 'sec_cache', 'company_tickers_exchange.json'),
               os.path.join(ROOT, 'runs', 'forecast_tracker', 'company_tickers_exchange.json'))


def _ticker_map():
    for p in TICKER_MAPS:
        if os.path.isfile(p):
            m = json.load(open(p, encoding='utf-8'))
            i_c, i_t = m['fields'].index('cik'), m['fields'].index('ticker')
            return {str(r[i_t]).upper(): int(r[i_c]) for r in m['data']}
    return {}


def _fetch(cik, ua):
    req = urllib.request.Request(SUBS.format(cik), headers={'User-Agent': ua, 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        if r.headers.get('Content-Encoding') == 'gzip':
            body = gzip.decompress(body)
    rec = json.loads(body).get('filings', {}).get('recent', {})
    return {'form': rec.get('form'), 'filingDate': rec.get('filingDate'), 'items': rec.get('items')}


def filing_index(tickers, today, refresh=False):
    """{ticker: {'events', 'source'}} from the cached filing index (refetched with --refresh); tickers without a CIK
    or a cached copy are left out (the table shows a dash)."""
    ua = os.environ.get('SEC_USER_AGENT', '')
    tmap, out = None, {}
    for t in tickers:
        cik = HW.find_cik(ROOT, t)
        if cik is None:
            tmap = _ticker_map() if tmap is None else tmap
            cik = tmap.get(t)
        if cik is None:
            continue
        path = os.path.join(CACHE, f'submissions_CIK{cik:010d}.json')
        if refresh:
            if '@' not in ua:
                print('due: --refresh needs SEC_USER_AGENT; the cached copies are used', file=sys.stderr)
                refresh = False
            else:
                try:
                    time.sleep(.2)
                    rec = _fetch(cik, ua)
                    os.makedirs(CACHE, exist_ok=True)
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(rec, f)
                except Exception as e:                                   # noqa: BLE001 (a failed fetch keeps the cache)
                    print(f'due: {t}: filing index not fetched ({type(e).__name__})', file=sys.stderr)
        if not os.path.isfile(path):
            continue
        recent = json.load(open(path, encoding='utf-8'))
        got = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).date().isoformat()
        out[t] = {'events': FT.filing_events(recent, '0000-00-00', today),
                  'source': f'SEC filing index, copy of {got} ({os.path.relpath(path, ROOT)})'}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--today', default=date.today().isoformat())
    ap.add_argument('--positions', help='READ-ONLY positions copy: marks the held names (private: --out must be under runs/)')
    ap.add_argument('--out', help='folder for due.json and due_he.md')
    ap.add_argument('--refresh', action='store_true', help='refetch the SEC filing index (SEC_USER_AGENT); default: cached copies only')
    ap.add_argument('--json', action='store_true', help='print the JSON instead of the Hebrew table')
    a = ap.parse_args(argv)
    held = ()
    if a.positions:
        stocks, _, _ = HW.position_rows(json.load(open(a.positions, encoding='utf-8')))
        held = [r['t'] for r in stocks]
    tickers = sorted(set(PH.thesis_tickers(ROOT)) | set(held))
    doc = PH.due(ROOT, a.today, a.days, held, filing_index(tickers, a.today, a.refresh), tickers)
    if a.out:
        if a.positions:
            PH._private_dir(a.out, ROOT)
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, 'due.json'), 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        with open(os.path.join(a.out, 'due_he.md'), 'w', encoding='utf-8') as f:
            f.write(PH.render_due_he(doc))
    print(json.dumps(doc, ensure_ascii=False, indent=1) if a.json else PH.render_due_he(doc))


if __name__ == '__main__':
    main()
