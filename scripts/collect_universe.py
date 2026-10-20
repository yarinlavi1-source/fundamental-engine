"""Collect a broad US equity universe for discovery: SEC XBRL frames + SEC ticker map + Nasdaq listings.

Network script (the engine modules stay pure). Writes raw payloads verbatim with a MANIFEST (retrieval time, URL,
SHA-256) so every scan can be reproduced from the saved files:

    SEC_USER_AGENT="app-name contact@example.com" python scripts/collect_universe.py runs/universe_2026-10-20 --years 2022 2023 2024 2025

SEC requires a User-Agent with a contact e-mail; it is read from the environment and never written to disk.
Sources: data.sec.gov frames (one concept for every filer and period), www.sec.gov company_tickers_exchange.json
(CIK, ticker, exchange), api.nasdaq.com screener (last price, market cap, sector, industry, country for NYSE, NASDAQ
and AMEX listings). The Nasdaq endpoint is an unofficial public API: treat its prices as a market feed snapshot.
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fundamental_engine.sec_frames import frame_urls  # noqa: E402

NASDAQ = 'https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true'
TICKERS = 'https://www.sec.gov/files/company_tickers_exchange.json'


def fetch(url, headers, attempts=3):
    last = None
    for i in range(attempts):
        try:
            with urlopen(Request(url, headers=headers), timeout=60) as r:
                body = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                return 200, body
        except HTTPError as e:
            if e.code == 404:
                return 404, b''
            last = e
        except URLError as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f'{url}: {last}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--years', nargs='+', type=int, required=True, help='calendar years of annual frames, oldest first')
    a = ap.parse_args(argv)
    ua = os.environ.get('SEC_USER_AGENT', '')
    if '@' not in ua:
        raise SystemExit('set SEC_USER_AGENT to "application contact-email" (SEC fair-access policy)')
    raw = os.path.join(a.out, 'raw')
    os.makedirs(os.path.join(raw, 'frames'), exist_ok=True)
    calls = []

    def save(name, url, headers, status_ok=True):
        status, body = fetch(url, headers)
        rec = {'file': name, 'url': url, 'status': status, 'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        if status == 200:
            with open(os.path.join(raw, name), 'wb') as f:
                f.write(body)
            rec['sha256'] = hashlib.sha256(body).hexdigest()
            rec['bytes'] = len(body)
        calls.append(rec)
        return status
    sec_h = {'User-Agent': ua, 'Accept-Encoding': 'gzip'}
    save('sec__company_tickers_exchange.json', TICKERS, sec_h)
    save('nasdaq__screener_stocks.json', NASDAQ, {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    for year in a.years:
        for u in frame_urls(year):
            name = f"frames/{u['metric']}__{u['tag']}__{u['period']}.json"
            st = save(name, u['url'], sec_h)
            calls[-1].update(metric=u['metric'], tag=u['tag'], period=u['period'], year=year)
            if st != 200:
                calls[-1]['note'] = 'no frame for this concept and period'
            time.sleep(0.12)                 # SEC fair access: well under 10 requests per second
    with open(os.path.join(raw, 'MANIFEST.json'), 'w') as f:
        json.dump({'collected_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'years': a.years,
                   'calls': calls, 'user_agent': 'redacted (contains a contact e-mail)'}, f, indent=1)
    ok = sum(1 for c in calls if c['status'] == 200)
    print(json.dumps({'out': a.out, 'calls': len(calls), 'ok': ok, 'missing': len(calls) - ok}))


if __name__ == '__main__':
    main()
