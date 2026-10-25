"""Collect the public market-context series: FRED daily readings for the regime line, optionally Damodaran's country risk page.

Network script (the engine modules stay pure). Writes raw payloads verbatim with a MANIFEST (retrieval time, URL, SHA-256)
under runs/macro/ (gitignored), so the regime line can be rebuilt from the saved files:

    python scripts/collect_market_context.py                       # runs/macro/fred_<today>/: DGS10, BAA10Y, VIXCLS, T10Y2Y
    python scripts/collect_market_context.py --start 2024-01-01    # a longer window (default: 400 days before today)
    python scripts/collect_market_context.py --country-risk        # also runs/macro/ctryprem_<today>/ (Damodaran page + xlsx)

Then pass the folder to the engine: `python -m fundamental_engine deep <universe> --as-of <date> --macro runs/macro/fred_<date>`
(or `pipeline ... --macro ...`): market_context.read_regime reads the latest reading on or before as_of. The regime line is
context only; no value reads it. A refreshed country risk table is NOT applied automatically: review the saved page, then
rebuild the packaged table with `python -I scripts/build_country_risk.py runs/macro/ctryprem_<date> fundamental_engine/brain/country_risk.json`
(needs openpyxl), run the tests and record the change in docs/CHANGELOG.md.
Sources: fred.stlouisfed.org/graph/fredgraph.csv (public, no key); pages.stern.nyu.edu/~adamodar (Damodaran Online).
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine.market_context import REGIME_SERIES, FRED_URL  # noqa: E402

CTRYPREM = ('https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ctryprem.html',
            'https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctryprem.xlsx')
# fred.stlouisfed.org's CDN closes the connection for most non-curl user agents (tested 2026-10-25: a descriptive agent and
# 'Mozilla/5.0' were refused, a curl-style agent with a comment accepted); the comment names this collector honestly
HEADERS = {'User-Agent': 'curl/8.5.0 (fundamental-engine market-context collector; public data)'}


def fetch(url, attempts=3):
    last = None
    for i in range(attempts):
        try:
            with urlopen(Request(url, headers=HEADERS), timeout=90) as r:
                return r.read()
        except (HTTPError, URLError) as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f'{url}: {last}')


def save(folder, name, url, calls, **extra):
    body = fetch(url)
    with open(os.path.join(folder, name), 'wb') as f:
        f.write(body)
    calls.append({'file': name, 'url': url, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body),
                  'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), **extra})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='runs/macro', help='parent folder (gitignored)')
    ap.add_argument('--start', help='first observation date (default: 400 days before today)')
    ap.add_argument('--country-risk', action='store_true', help="also save Damodaran's ctryprem page and xlsx")
    a = ap.parse_args(argv)
    today = date.today().isoformat()
    start = a.start or (date.today() - timedelta(days=400)).isoformat()
    folder = os.path.join(a.out, f'fred_{today}')
    os.makedirs(folder, exist_ok=True)
    calls = []
    for s in REGIME_SERIES:
        save(folder, f'{s}.csv', FRED_URL.format(s) + f'&cosd={start}', calls, series=s, title=REGIME_SERIES[s])
    man = {'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'source': 'FRED, Federal Reserve Bank of St. Louis',
           'start': start, 'calls': calls}
    with open(os.path.join(folder, 'MANIFEST.json'), 'w', encoding='utf-8') as f:
        json.dump(man, f, indent=1)
    out = {'fred': folder, 'series': [c['series'] for c in calls]}
    if a.country_risk:
        cr = os.path.join(a.out, f'ctryprem_{today}')
        os.makedirs(cr, exist_ok=True)
        cc = []
        for url in CTRYPREM:
            save(cr, url.rsplit('/', 1)[-1], url, cc)
        with open(os.path.join(cr, 'MANIFEST.json'), 'w', encoding='utf-8') as f:
            json.dump({'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'calls': cc}, f, indent=1)
        out['country_risk'] = cr
        out['next'] = f'python -I scripts/build_country_risk.py {cr} fundamental_engine/brain/country_risk.json (review first)'
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
