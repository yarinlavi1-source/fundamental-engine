"""Collect data for the walk-forward discovery test: SEC annual frames for many years + monthly prices.

    SEC_USER_AGENT="app contact@example.com" python scripts/collect_backtest.py runs/backtest --years 2009 ... 2025

Writes raw/ (frames, SEC ticker map, MANIFEST.json) like collect_universe.py, then raw/prices/<TICKER>.json: Yahoo
monthly chart payloads (split-adjusted close, dividend-adjusted close, split events) for every NYSE/Nasdaq ticker in
today's SEC map plus benchmark funds. Known bias, stated in every result: only CURRENTLY listed tickers have prices,
so companies that were delisted, acquired or went bankrupt are missing (survivorship bias: absolute returns are
overstated for every portfolio, including the equal-weight universe benchmark built from the same tickers).
Existing files are skipped, so the script can be re-run after an interruption.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
from collect_universe import fetch, TICKERS  # noqa: E402
from fundamental_engine.sec_frames import frame_urls  # noqa: E402

YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=max&interval=1mo&events=split%2Cdiv'
BENCHMARKS = ['SPY', 'RSP', 'QQQ', 'IWM', 'VTI']


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--years', nargs='+', type=int, required=True)
    ap.add_argument('--skip-prices', action='store_true')
    a = ap.parse_args(argv)
    ua = os.environ.get('SEC_USER_AGENT', '')
    if '@' not in ua:
        raise SystemExit('set SEC_USER_AGENT to "application contact-email" (SEC fair-access policy)')
    raw = os.path.join(a.out, 'raw')
    os.makedirs(os.path.join(raw, 'frames'), exist_ok=True)
    os.makedirs(os.path.join(raw, 'prices'), exist_ok=True)
    man_path = os.path.join(raw, 'MANIFEST.json')
    calls = json.load(open(man_path))['calls'] if os.path.exists(man_path) else []
    done = {c['file'] for c in calls if c['status'] in (200, 404)}
    sec_h = {'User-Agent': ua, 'Accept-Encoding': 'gzip'}

    def save(name, url, headers, extra=None):
        if name in done:
            return
        status, body = fetch(url, headers)
        rec = {'file': name, 'url': url, 'status': status, 'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
               **(extra or {})}
        if status == 200:
            with open(os.path.join(raw, name), 'wb') as f:
                f.write(body)
            rec['sha256'] = hashlib.sha256(body).hexdigest()
        calls.append(rec)
        done.add(name)

    def flush():
        with open(man_path, 'w') as f:
            json.dump({'calls': calls, 'user_agent': 'redacted (contains a contact e-mail)', 'survivorship':
                       'prices exist only for currently listed tickers'}, f, indent=1)
    save('sec__company_tickers_exchange.json', TICKERS, sec_h)
    for year in a.years:
        for u in frame_urls(year):
            save(f"frames/{u['metric']}__{u['tag']}__{u['period']}.json", u['url'], sec_h,
                 {'metric': u['metric'], 'tag': u['tag'], 'period': u['period'], 'year': year})
            time.sleep(0.12)
        flush()
        print(json.dumps({'frames_year': year}), flush=True)
    if a.skip_prices:
        return
    with open(os.path.join(raw, 'sec__company_tickers_exchange.json')) as f:
        t = json.load(f)
    # common shares only: warrants, units, rights and preferred lines (-WT, -U, -R, -P...) cannot be universe members
    tickers = sorted({r[2].upper() for r in t['data'] if r[3] in ('Nasdaq', 'NYSE') and not re.search(r'-(W|WT|WS|U|UN|R|RT|P[A-Z]?)$', r[2].upper())}
                     | set(BENCHMARKS))
    yh = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    lock = threading.Lock()
    have = set(os.listdir(os.path.join(raw, 'prices')))

    def one(tk):
        name = f"prices/{tk.replace('/', '-')}.json"
        if name in done or name.split('/')[1] in have:
            return
        url = YAHOO.format(t=tk.replace('.', '-'))
        try:
            status, body = fetch(url, yh)
        except RuntimeError as e:
            with lock:
                calls.append({'file': name, 'status': 'error', 'error': str(e)[:200], 'ticker': tk})
            return
        rec = {'file': name, 'url': url, 'status': status, 'ticker': tk, 'provider': 'yahoo',
               'retrieved_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        if status == 200:
            with open(os.path.join(raw, name), 'wb') as f:
                f.write(body)
            rec['sha256'] = hashlib.sha256(body).hexdigest()
        with lock:
            calls.append(rec)
            done.add(name)
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, _ in enumerate(ex.map(one, tickers)):
            if i % 500 == 0:
                with lock:
                    flush()
                print(json.dumps({'prices': i, 'of': len(tickers)}), flush=True)
    flush()
    print(json.dumps({'done': True, 'calls': len(calls)}), flush=True)


if __name__ == '__main__':
    main()
