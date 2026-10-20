"""Collect the SEC discovery channels for every listed filer (fundamental_engine.channels reads them).

    SEC_USER_AGENT="app contact@example.com" python scripts/collect_channels.py runs/universe_2026-10-20 \
        --channels runs/discovery_sources --as-of 2026-10-20

Writes, as retrieved: <channels>/edgar/form_<YYYY>_QTR<n>.idx for the quarter of as_of and the one before;
<channels>/frames_q/<tag>_<CYyyyyQq>.json for the last eight calendar quarters of revenue; and the SEC submissions JSON
(8-K item lists) of every universe filer missing from <folder>/raw/submissions (--refresh re-fetches all). SEC asks for a
contact e-mail in the User-Agent; it is read from the environment and never written to disk. About 8 requests a second.
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import date

REV_TAGS = ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax')


def fetch(url, ua):
    req = urllib.request.Request(url, headers={'User-Agent': ua, 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return gzip.decompress(body) if r.headers.get('Content-Encoding') == 'gzip' else body


def quarters_back(d, n):
    q = (d.month - 1) // 3 + 1
    k = d.year * 4 + q - 1
    return [(x // 4, x % 4 + 1) for x in range(k - n + 1, k + 1)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--channels', required=True)
    ap.add_argument('--as-of', required=True)
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--skip-submissions', action='store_true')
    a = ap.parse_args(argv)
    ua = os.environ.get('SEC_USER_AGENT', '')
    if '@' not in ua:
        raise SystemExit('set SEC_USER_AGENT to "application contact-email" (SEC fair-access policy)')
    d = date.fromisoformat(a.as_of)
    log = []
    os.makedirs(os.path.join(a.channels, 'edgar'), exist_ok=True)
    for y, q in quarters_back(d, 2):
        p = os.path.join(a.channels, 'edgar', f'form_{y}_QTR{q}.idx')
        if os.path.exists(p) and not a.refresh and (y, q) != quarters_back(d, 1)[0]:
            continue
        body = fetch(f'https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx', ua)
        open(p, 'wb').write(body)
        log.append({'file': p, 'bytes': len(body)})
        time.sleep(.15)
    os.makedirs(os.path.join(a.channels, 'frames_q'), exist_ok=True)
    for y, q in quarters_back(d, 9)[:-1]:          # the current quarter is not reported yet
        for tag in REV_TAGS:
            p = os.path.join(a.channels, 'frames_q', f'{tag}_CY{y}Q{q}.json')
            if os.path.exists(p) and not a.refresh:
                continue
            try:
                body = fetch(f'https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/CY{y}Q{q}.json', ua)
                open(p, 'wb').write(body)
                log.append({'file': p, 'bytes': len(body)})
            except Exception as e:                    # a frame not published yet is recorded, not fatal
                log.append({'file': p, 'error': str(e)[:200]})
            time.sleep(.15)
    if not a.skip_submissions:
        rows = json.load(open(os.path.join(a.folder, 'rows.json')))
        sub = os.path.join(a.folder, 'raw', 'submissions')
        os.makedirs(sub, exist_ok=True)
        ciks = sorted({int(r['source']['cik']) for r in rows if (r.get('source') or {}).get('cik')})
        n = 0
        for cik in ciks:
            p = os.path.join(sub, f'CIK{cik:010d}.json')
            if os.path.exists(p) and not a.refresh:
                continue
            try:
                body = fetch(f'https://data.sec.gov/submissions/CIK{cik:010d}.json', ua)
                open(p, 'wb').write(body)
                n += 1
            except Exception as e:
                log.append({'cik': cik, 'error': str(e)[:200]})
            time.sleep(.125)
        log.append({'submissions_fetched': n, 'universe_filers': len(ciks)})
    with open(os.path.join(a.channels, f'COLLECT_{a.as_of}.json'), 'w') as f:
        json.dump(log, f, indent=1)
    print(json.dumps(log[-5:], indent=1))


if __name__ == '__main__':
    main()
