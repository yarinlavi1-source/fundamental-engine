"""Quality + business-momentum screen over US-listed companies above a market value floor (network + CLI).

    SEC_USER_AGENT="app-name contact@example.com" python scripts/quality_momentum_screen.py runs/screens/2026-11-25 --as-of 2026-11-25 [--min-cap 5e9] [--reuse]

Sources: the Nasdaq listing feed (symbol, market value, sector; an unofficial public API), the SEC ticker map, the SEC
companyfacts API read per company (data.sec.gov/api/xbrl/companyfacts/CIK##########.json, one request per company above
the floor; not the XBRL frames API) and its submissions file (SIC code). For the names with at most one failed quality
gate, and for the thesis book: three Nasdaq analyst pages per company (earnings surprises, the EPS forecast table with
its 4-week revisions, the consensus now against a month ago; Nasdaq names Zacks Investment Research as its vendor; an
unofficial public API) and Yahoo daily closes. The Nasdaq earnings calendar is read for the 10 days before the as-of
date and the 14 after it.
Version 2 (25.11.2026): price is information, never a gate. The business-momentum gates (analysts are not cutting,
the last two quarters met the consensus) replaced the three price gates; see fundamental_engine/quality_momentum.py.
Without SPY bars the run goes on: relative strength is empty and the last trading day is the most common last bar.
Files under the output folder: raw/ (listing, ticker map), facts/CIK*.json (the extracted series, not the raw
companyfacts), prices/<SYMBOL>.json, estimates/<SYMBOL>.json (the raw analyst rows and any error), calendar/<day>.json,
screen.json and screen_he.md. Beside the output folder: _estimates_history/<SYMBOL>.jsonl, our own record of the
consensus per fiscal year (one line per fiscal year and day the pages were read). It lengthens the revision window on
later runs only while that folder is kept: it is under runs/, which is not in Git and is lost with a cloud container.
The analyst pages are a snapshot of the day they are read: a run for an older as-of date cannot rebuild them, and the
report names the day they were read when it is not the as-of date.
A share class without an analyst record is read from another class of the same company (the listed other classes, the
A/B sister, the root symbol). When Nasdaq stops answering (20 companies in a row fail on the network) the analyst step
stops asking and the rest are recorded as gaps.
--reuse reads the saved files and fetches only what is missing; a saved estimates or calendar file that cannot be read
is fetched again. The SEC User-Agent is read from the environment and never written to disk; the Nasdaq and Yahoo
requests carry a generic browser header.
Saved valuations (15.11.2026): every listed name and every thesis-book name gets 'saved_valuation' (the buy test of its
newest research/companies valuation or research/rejected.jsonl row at the screen's close); a failing name is marked in
screen_he.md ('הערכה שמורה'), never dropped. --research points at another research folder (tests).
The logic is in fundamental_engine/quality_momentum.py. The result is not a valuation and not a buy signal.
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_universe import NASDAQ, TICKERS, fetch  # noqa: E402
from fundamental_engine import quality_momentum as qm  # noqa: E402

FACTS = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{:010d}.json'
SUBS = 'https://data.sec.gov/submissions/CIK{:010d}.json'
YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d'
BROWSER = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
# the Nasdaq analyst pages answer only a browser-like request (an unofficial public API; Nasdaq's vendor is Zacks)
NASDAQ_SITE = dict(BROWSER, Origin='https://www.nasdaq.com', Referer='https://www.nasdaq.com/')
EST_PAGES = (('surprise', 'https://api.nasdaq.com/api/company/{}/earnings-surprise'),
             ('forecast', 'https://api.nasdaq.com/api/analyst/{}/earnings-forecast'),
             ('momentum', 'https://api.nasdaq.com/api/analyst/{}/estimate-momentum'))
CALENDAR = 'https://api.nasdaq.com/api/calendar/earnings?date={}'
HISTORY_DIR = '_estimates_history'              # beside the output folder (under runs/: private, not in Git)
# One Nasdaq page takes 1-3 seconds to answer (measured 25.11.2026), so three workers reach about 1.5 requests a second.
# Eight workers reach the throttle's limit instead; the throttle, not the worker count, keeps the rate at about 4 a second.
NASDAQ_GAP, NASDAQ_WORKERS = 0.25, 8
NASDAQ_TIMEOUT = 15                             # seconds per request (the SEC and Yahoo requests keep fetch's 60)
NASDAQ_GIVE_UP = 20                             # companies in a row that failed on the network: stop asking Nasdaq


class Throttle:
    """At most one request per `gap` seconds across threads."""

    def __init__(self, gap):
        self.gap, self.lock, self.next = gap, threading.Lock(), 0.

    def wait(self):
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next)
            self.next = start + self.gap
        time.sleep(max(0., start - now))


class Breaker:
    """Stops asking a host that stopped answering: open after `limit` companies in a row failed on the network."""

    def __init__(self, limit):
        self.limit, self.lock, self.run, self.skipped = limit, threading.Lock(), 0, 0

    def record(self, failed):
        with self.lock:
            self.run = self.run + 1 if failed else 0

    @property
    def open(self):
        return self.run >= self.limit


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _load_saved(path, key):
    """A saved record that holds `key`, or None: a file that is missing, cut off mid-write or of another shape is
    treated as not saved, so the caller fetches it again."""
    try:
        rec = _load(path)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and isinstance(rec.get(key), (dict, list)) else None


def _save(path, obj):
    """Written through a temporary file, so a run killed mid-write leaves the old file or none, never half of one."""
    tmp = f'{path}.{os.getpid()}.{threading.get_ident()}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def get_listing(out, reuse):
    raw = os.path.join(out, 'raw')
    os.makedirs(raw, exist_ok=True)
    paths = {'nasdaq': os.path.join(raw, 'nasdaq__screener_stocks.json'), 'sec': os.path.join(raw, 'sec__company_tickers_exchange.json')}
    if not (reuse and os.path.exists(paths['nasdaq'])):
        _, body = fetch(NASDAQ, BROWSER)
        with open(paths['nasdaq'], 'wb') as f:
            f.write(body)
    if not (reuse and os.path.exists(paths['sec'])):
        _, body = fetch(TICKERS, {'User-Agent': os.environ['SEC_USER_AGENT'], 'Accept-Encoding': 'gzip'})
        with open(paths['sec'], 'wb') as f:
            f.write(body)
    listing = _load(paths['nasdaq'])['data']['rows']
    t = _load(paths['sec'])
    i_cik, i_tic = t['fields'].index('cik'), t['fields'].index('ticker')
    ciks = {}
    for r in t['data']:
        ciks.setdefault(qm.norm_symbol(r[i_tic]), r[i_cik])
    return listing, ciks


def _keep(path, rec):
    """Saves a fetched record unless the fetch itself failed (a failed company is tried again on the next run)."""
    if not any(x.endswith('Error') or 'RuntimeError' in x for x in rec['errors']):
        _save(path, rec)
    return rec


FACTS_V = 4      # 4: four completed fiscal years of history (the three-year growth gate)


def get_facts(cik, out, reuse, throttle, since, as_of):
    """Extracted series + SIC for one CIK, cached as facts/CIK<10 digits>.json (older extraction versions are refetched)."""
    path = os.path.join(out, 'facts', f'CIK{cik:010d}.json')
    old_sub = None
    if reuse and os.path.exists(path):
        rec = _load(path)
        if rec.get('v', 1) >= FACTS_V and rec.get('as_of') == as_of:
            return rec
        if rec.get('as_of') == as_of:
            old_sub = rec.get('sub')                               # an older extraction: only the facts are read again
    h = {'User-Agent': os.environ['SEC_USER_AGENT'], 'Accept-Encoding': 'gzip'}
    rec = {'cik': cik, 'v': FACTS_V, 'as_of': as_of, 'retrieved_at_utc': _now(), 'series': None, 'sub': old_sub, 'errors': []}
    try:
        throttle.wait()
        status, body = fetch(FACTS.format(cik), h)
        if status == 200:
            rec['series'] = qm.extract_series(json.loads(body), since=since, as_of=as_of)
        else:
            rec['errors'].append(f'companyfacts {status}')
    except Exception as e:                                         # noqa: BLE001 (a failed company is a recorded gap)
        rec['errors'].append(f'companyfacts {type(e).__name__}')
    try:
        if old_sub is not None:
            return _keep(path, rec)
        throttle.wait()
        status, body = fetch(SUBS.format(cik), h)
        if status == 200:
            s = json.loads(body)
            fr = (s.get('filings') or {}).get('recent') or {}
            periodic = [{'form': fm, 'filed': fd, 'period': pd} for fm, fd, pd in
                        zip(fr.get('form') or [], fr.get('filingDate') or [], fr.get('reportDate') or [])
                        if fm in ('10-Q', '10-K', '10-QT', '10-KT', '20-F', '40-F') and fd <= as_of]
            rec['sub'] = {'sic': s.get('sic'), 'sic_description': s.get('sicDescription'), 'fiscal_year_end': s.get('fiscalYearEnd'),
                          'name': s.get('name'), 'latest_periodic': periodic[0] if periodic else None}
        else:
            rec['errors'].append(f'submissions {status}')
    except Exception as e:                                         # noqa: BLE001
        rec['errors'].append(f'submissions {type(e).__name__}')
    return _keep(path, rec)


def get_prices(symbol, out, reuse, throttle, as_of):
    """Daily bars up to as_of, cached as prices/<symbol>.json: dates, adjclose (splits and dividends), close and high
    (splits only). A file saved without close/high is refetched; when that fails the saved file stands."""
    path = os.path.join(out, 'prices', symbol.replace('=', '_') + '.json')
    old = _load(path) if reuse and os.path.exists(path) else None
    if old and (old.get('close') or old.get('error')):
        return old
    end = date.fromisoformat(as_of)
    p1 = int(datetime(*(end - timedelta(days=600)).timetuple()[:3], tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(*(end + timedelta(days=1)).timetuple()[:3], tzinfo=timezone.utc).timestamp())
    rec = {'symbol': symbol, 'retrieved_at_utc': _now(), 'dates': [], 'adjclose': [], 'close': [], 'high': [], 'error': None}
    try:
        throttle.wait()
        status, body = fetch(YAHOO.format(symbol, p1, p2), BROWSER)
        res = (json.loads(body).get('chart', {}).get('result') or [None])[0] if status == 200 else None
        if not res or not res.get('timestamp'):
            rec['error'] = f'no bars (status {status})'
        else:
            quote = res['indicators']['quote'][0]
            adj = (res['indicators'].get('adjclose') or [{}])[0].get('adjclose') or quote['close']
            off = res.get('meta', {}).get('gmtoffset') or 0
            for ts, c, px, hi in zip(res['timestamp'], adj, quote['close'], quote.get('high') or quote['close']):
                d = datetime.fromtimestamp(ts + off, timezone.utc).date().isoformat()
                if c is not None and px is not None and d <= as_of:
                    rec['dates'].append(d)
                    rec['adjclose'].append(round(c, 4))
                    rec['close'].append(round(px, 4))
                    rec['high'].append(round(hi if hi is not None else px, 4))
            rec['currency'] = res.get('meta', {}).get('currency')
    except Exception as e:                                         # noqa: BLE001
        rec['error'] = type(e).__name__
    if rec['error'] is not None and old:
        return old
    if rec['error'] is None or rec['error'].startswith('no bars'):
        _save(path, rec)
    return rec


def _nasdaq_page(url, throttle):
    """One Nasdaq API page -> (state, data, message). state 'ok' (data: the 'data' node, which may be empty),
    'no_symbol' (the page does not know the symbol), 'network' (no answer: one retry on a network error or a read
    timeout, fetch attempts=2) or 'error' (an answer that is not usable)."""
    throttle.wait()
    try:
        status, body = fetch(url, NASDAQ_SITE, attempts=2, timeout=NASDAQ_TIMEOUT)
    except Exception as e:                                         # noqa: BLE001 (a failed page is a recorded gap)
        return 'network', None, type(e).__name__
    if status != 200:
        return 'no_symbol', None, f'status {status}'
    try:
        j = json.loads(body)
    except ValueError:
        return 'error', None, 'not JSON'
    st = j.get('status') or {} if isinstance(j, dict) else {}
    codes = [m for m in st.get('bCodeMessage') or [] if isinstance(m, dict)]
    text = '; '.join(str(m.get('errorMessage')) for m in codes) or None
    if st.get('rCode') not in (None, 200) and any(m.get('code') == 1001 for m in codes):
        return 'no_symbol', None, text
    if st.get('rCode') not in (None, 200):
        return 'error', None, text or f"rCode {st.get('rCode')}"
    return 'ok', j.get('data') if isinstance(j, dict) else None, text


def _has_rows(raw):
    """True when the pages hold something to read: a reported quarter or a yearly forecast row."""
    def rows(page, table):
        node = (raw.get(page) or {}).get(table) if isinstance(raw.get(page), dict) else None
        return node.get('rows') if isinstance(node, dict) else None
    return bool(rows('surprise', 'earningsSurpriseTable') or rows('forecast', 'yearlyForecast'))


def _estimate_pages(symbol, throttle):
    """The three pages for one symbol, tried in each spelling (quality_momentum.symbol_variants) until the first page
    knows one -> (spelling or None, raw, errors, network_failed). After a network failure the symbol's remaining pages
    are not asked: the record is not saved anyway, and a host that is down is not asked three times per company."""
    all_errors = []
    for variant in qm.symbol_variants(symbol):
        raw, errors, known = {}, [], False
        for key, url in EST_PAGES:
            state, data, msg = _nasdaq_page(url.format(variant), throttle)
            if state == 'network':
                return (variant if known else None), raw, all_errors + errors + [f'{key}: {msg} ({variant})'], True
            if state == 'no_symbol' and not known:
                errors.append(f'{key}: {msg or "symbol not found"} ({variant})')
                break                                              # the first page does not know this spelling: try the next
            known = True
            raw[key] = data
            if state != 'ok' or (msg and not data):
                errors.append(f'{key}: {msg or state} ({variant})')
        all_errors += errors
        if known:
            return variant, raw, all_errors, False
    return None, {}, all_errors, False


def get_estimates(symbol, out, reuse, throttle, siblings=(), breaker=None):
    """The three Nasdaq analyst pages of one company, saved as estimates/<SYMBOL>.json: {symbol, queried_as, read_from,
    retrieved_at_utc, raw {surprise, forecast, momentum} (the 'data' node of each page), errors}. A share-class symbol is
    tried as listed, then in its dotted and hyphen forms. When that class has no analyst record (no reported quarter and
    no yearly row), the siblings are tried the same way (other classes of the same company: the estimates are per share
    of the company, and the gates read percentages) and read_from names the class that answered. A page that answers
    'no record' is a saved gap; a network error is not saved, so the next run tries again. With --reuse a saved file
    that cannot be read is fetched again. breaker: when it is open, Nasdaq is not asked at all. Never raises."""
    path = os.path.join(out, 'estimates', symbol.replace('/', '_') + '.json')
    old = _load_saved(path, 'raw') if reuse and os.path.exists(path) else None
    if old is not None:
        return old
    rec = {'symbol': symbol, 'queried_as': None, 'read_from': None, 'retrieved_at_utc': _now(),
           'raw': {k: None for k, _ in EST_PAGES}, 'errors': []}
    if breaker is not None and breaker.open:
        breaker.skipped += 1
        rec['errors'].append('not asked: Nasdaq stopped answering earlier in this run')
        rec['fetch_failed'] = True
        return rec
    failed = False
    for cls in [symbol] + [x for x in siblings if x != symbol]:
        variant, raw, errors, failed = _estimate_pages(cls, throttle)
        rec['errors'] += errors
        if failed:
            break
        if variant and (rec['queried_as'] is None or _has_rows(raw)):
            rec['queried_as'] = variant
            rec['raw'] = dict({k: None for k, _ in EST_PAGES}, **raw)
            rec['read_from'] = cls if cls != symbol and _has_rows(raw) else None
        if _has_rows(rec['raw']):
            break
    if breaker is not None:
        breaker.record(failed)
    if not failed:
        _save(path, rec)
    rec['fetch_failed'] = failed
    return rec


def read_history(folder, symbol):
    """Our own earlier readings of one company's consensus: the lines of _estimates_history/<SYMBOL>.jsonl."""
    path = os.path.join(folder, symbol.replace('/', '_') + '.jsonl')
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:                                     # a broken line is skipped, never a crash
                continue
    return [r for r in rows if isinstance(r, dict)]


def add_history(folder, symbol, lines, history):
    """Appends today's readings once per as_of (a second run of the same day adds nothing)."""
    if not lines or any(h.get('as_of') == lines[0]['as_of'] for h in history):
        return False
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, symbol.replace('/', '_') + '.jsonl'), 'a', encoding='utf-8') as f:
        f.write(''.join(json.dumps(x, separators=(',', ':')) + '\n' for x in lines))
    return True


def get_calendar(day, out, reuse, throttle):
    """One day of the Nasdaq earnings calendar, saved as calendar/<day>.json: {date, retrieved_at_utc, rows, error}.
    A day that failed is not saved (the next run tries again); with --reuse a saved file that cannot be read is fetched
    again. Never raises."""
    path = os.path.join(out, 'calendar', day + '.json')
    old = _load_saved(path, 'rows') if reuse and os.path.exists(path) else None
    if old is not None:
        return old
    state, data, msg = _nasdaq_page(CALENDAR.format(day), throttle)
    rec = {'date': day, 'retrieved_at_utc': _now(), 'rows': (data or {}).get('rows') or [] if isinstance(data, dict) else [],
           'error': None if state == 'ok' else (msg or state)}
    if rec['error'] is None:
        _save(path, rec)
    return rec


def thesis_book(research=None):
    d = os.path.join(research or os.path.join(ROOT, 'research'), 'companies')
    return sorted(x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))) if os.path.isdir(d) else []


def saved_summary(lists, book_rows):
    """One row per listed or thesis-book name with a saved valuation: where it is listed and what the buy test says."""
    out, seen = [], set()
    for label, rows in lists + [('thesis_book', book_rows)]:
        for r in rows:
            sv = r.get('saved_valuation') or {}
            if sv.get('state') in (None, 'none') or (label == 'thesis_book' and r['symbol'] in seen):
                continue
            seen.add(r['symbol'])
            out.append({'symbol': r['symbol'], 'list': label, **{k: sv.get(k) for k in (
                'state', 'date', 'kind', 'age_days', 'base_value', 'price_then', 'price', 'buy_test_price', 'buy_test_price_saved',
                'binding_leg', 'basis', 'rejected')}})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--as-of', required=True)
    ap.add_argument('--min-cap', type=float, default=5e9)
    ap.add_argument('--reuse', action='store_true', help='use the saved raw data; fetch only what is missing')
    ap.add_argument('--research', default=os.path.join(ROOT, 'research'), help='the research folder (saved valuations, rejected.jsonl)')
    a = ap.parse_args(argv)
    if '@' not in os.environ.get('SEC_USER_AGENT', ''):
        raise SystemExit('set SEC_USER_AGENT to "application contact-email" (SEC fair-access policy)')
    for sub in ('raw', 'facts', 'prices', 'estimates', 'calendar'):
        os.makedirs(os.path.join(a.out, sub), exist_ok=True)
    t0 = time.time()
    book = thesis_book(a.research)
    listing, ciks = get_listing(a.out, a.reuse)
    universe, counts, no_cik = qm.build_universe(listing, ciks, a.min_cap, keep=book)
    print(json.dumps({'step': 'universe', **counts, 'no_cik': len(no_cik)}), flush=True)

    since = (date.fromisoformat(a.as_of) - timedelta(days=qm.HISTORY_DAYS)).isoformat()
    sec = Throttle(0.14)                                           # about 7 requests a second, under the SEC limit
    with ThreadPoolExecutor(4) as ex:
        facts = list(ex.map(lambda r: get_facts(r['cik'], a.out, a.reuse, sec, since, a.as_of), universe))
    print(json.dumps({'step': 'facts', 'n': len(facts), 'seconds': round(time.time() - t0)}), flush=True)

    rows = []
    for u, f in zip(universe, facts):
        sic = (f.get('sub') or {}).get('sic')
        r = qm.evaluate(u, f.get('series') or {}, sic, a.as_of)
        r['sic_description'] = (f.get('sub') or {}).get('sic_description')
        lp = (f.get('sub') or {}).get('latest_periodic')
        r['latest_filing'] = lp
        if lp and r.get('period_end') and lp.get('period') and (date.fromisoformat(lp['period']) - date.fromisoformat(r['period_end'])).days > 10:
            r['behind_latest_filing'] = True       # a newer report exists than the period the numbers come from
            r['gaps'].append('numbers end %s but a %s for %s was filed %s' % (r['period_end'], lp['form'], lp['period'], lp['filed']))
        r['gaps'] += f.get('errors') or []
        rows.append(r)

    yh = Throttle(0.25)
    spy_bars = get_prices('SPY', a.out, a.reuse, yh, a.as_of)
    spy = qm.trend_measures(spy_bars['adjclose'], spy_bars.get('close'), spy_bars.get('high'))
    # analyst pages and prices: the rows within one failed quality gate, and every thesis-book name (also one whose
    # SEC data gave no basis: its quality gates stay missing, the analyst and price columns are still shown)
    book_syms = {qm.norm_symbol(t) for t in book}
    want = [r for r in rows if (r['basis'] and qm.needs_business(r['quality'])) or r['symbol'] in book_syms]
    with ThreadPoolExecutor(3) as ex:
        bars = list(ex.map(lambda r: get_prices(r['symbol'], a.out, a.reuse, yh, a.as_of), want))
    if spy:
        last_day = spy_bars['dates'][-1]
    else:
        # Price decides nothing, so a missing SPY file does not stop the screen: relative strength stays empty and
        # the last trading day is the most common last bar among the companies read.
        days = [b['dates'][-1] for b in bars if b['dates']]
        last_day = max(sorted(set(days)), key=days.count) if days else None
        print(json.dumps({'warning': 'SPY bars missing: relative strength is empty', 'last_trading_day': last_day}), flush=True)
    fx = {}
    for ccy in sorted({r['currency'] for r in want if r.get('currency') not in (None, 'USD')}):
        b = get_prices(f'{ccy}=X', a.out, a.reuse, yh, a.as_of)
        fx[ccy] = b['adjclose'][-1] if b['adjclose'] else None
    print(json.dumps({'step': 'prices', 'n': len(want), 'seconds': round(time.time() - t0)}), flush=True)
    price_gaps = []
    for r, b in zip(want, bars):
        stale = bool(b['dates']) and last_day is not None and b['dates'][-1] != last_day
        tm = None if stale else qm.trend_measures(b['adjclose'], b.get('close'), b.get('high'))
        r['trend_measures'] = tm
        r['price_info'] = qm.price_info(tm, spy)                   # information only: no status reads it
        if tm is None:
            why = b.get('error') or ('last bar %s, not %s' % (b['dates'][-1], last_day) if stale else 'under 253 daily bars (%d)' % len(b['dates']))
            r['gaps'].append('prices: ' + why)
            price_gaps.append({'symbol': r['symbol'], 'why': why})
        if tm and r['price'] > 0 and 0.5 < tm['last'] / r['price'] < 2:     # the listing feed can be a trading day behind
            r['market_cap_feed'], r['price_feed'] = r['market_cap'], r['price']
            r['market_cap'], r['price'] = r['market_cap'] * tm['last'] / r['price'], tm['last']
        rate = 1. if r.get('currency') == 'USD' else fx.get(r.get('currency'))
        r.update(qm.price_columns(r, r['market_cap'], rate))
        if rate is None and r.get('currency'):
            r['gaps'].append('no exchange rate for %s: price multiples not computed' % r.get('currency'))

    # business momentum: three Nasdaq analyst pages per company, about 4 requests a second
    nq, breaker = Throttle(NASDAQ_GAP), Breaker(NASDAQ_GIVE_UP)
    with ThreadPoolExecutor(NASDAQ_WORKERS) as ex:
        ests = list(ex.map(lambda r: get_estimates(r['symbol'], a.out, a.reuse, nq,
                                                   qm.sibling_symbols(r['symbol'], r.get('other_classes')), breaker), want))
    hist_dir = os.path.join(os.path.dirname(os.path.abspath(a.out)), HISTORY_DIR)
    est_gaps, read_days, other_class = [], [], []
    for r, e in zip(want, ests):
        raw = e.get('raw') or {}
        est = qm.parse_estimates(raw.get('surprise'), raw.get('forecast'), raw.get('momentum'))
        # the day the pages were read: the as-of day in a normal run. Our own history is dated by it and its age is
        # measured from it, so a run over older saved pages never compares a reading with itself.
        read_on = (e.get('retrieved_at_utc') or a.as_of)[:10]
        if e.get('queried_as'):
            read_days.append(read_on)
        history = read_history(hist_dir, r['symbol'])
        r['business'] = qm.business_gates(est, history, a.as_of, read_on)
        r.update(qm.business_columns(r['business']))
        r['estimates'] = {'queried_as': e.get('queried_as'), 'read_from': e.get('read_from'), 'retrieved_at_utc': e.get('retrieved_at_utc'),
                          'years': est['years'], 'momentum': est['momentum'], 'surprises': est['surprises'][:4]}
        if e.get('read_from'):
            other_class.append(r['symbol'])
            r['gaps'].append('analyst data: read from %s, another share class of the same company' % e['read_from'])
        why = [g['gate'] + ': ' + (g.get('note') or 'missing') for g in qm.missing(r['business'])]
        if e.get('fetch_failed') or why:
            why = (['fetch failed'] if e.get('fetch_failed') else []) + why + (e.get('errors') or [])[:3]
            r['gaps'].append('analyst data: ' + '; '.join(why))
            est_gaps.append({'symbol': r['symbol'], 'why': why})
        notes = [g['note'] for g in r['business'] if g['pass'] is not None and g.get('note')] + [
            x for x in est['gaps'] if 'describes' in x or '(no value)' in x]
        r['gaps'] += ['analyst data: ' + x for x in notes]
        add_history(hist_dir, r['symbol'], qm.history_lines(est, read_on), history)
    analyst_read = {'first': min(read_days), 'last': max(read_days)} if read_days else None
    if analyst_read and (analyst_read['first'] != a.as_of or analyst_read['last'] != a.as_of):
        print(json.dumps({'warning': 'the analyst pages were not read on the as-of date', 'as_of': a.as_of, **analyst_read}), flush=True)
    if breaker.skipped:
        print(json.dumps({'warning': 'Nasdaq stopped answering: analyst pages not asked', 'companies': breaker.skipped}), flush=True)
    print(json.dumps({'step': 'estimates', 'n': len(want), 'gaps': len(est_gaps), 'seconds': round(time.time() - t0)}), flush=True)

    for r in rows:
        r['status'] = qm.classify(r['quality'], r.get('business'))
        gates = r['quality'] + (r.get('business') or [])
        r['failed'], r['missing'] = qm.failed(gates), qm.missing(gates)
        if r.get('price_info'):
            r['rel_strength'] = r['price_info']['rel_strength']
        if r.get('behind_latest_filing'):
            r['status'], r['note'] = 'incomplete', 'הדוח האחרון לא נקרא בנתוני ה-SEC'      # an older report is not a result
        r['divergence'] = qm.divergence(r.get('price_info'), r.get('business'), r['status'])     # passers and near misses only
        r['flags'] = [qm.DIVERGENCE] if r['divergence'] else []

    # earnings radar: who reported in the last 10 days, who reports in the next 14
    cal, cal_gaps = [], []
    for day in qm.calendar_days(a.as_of):
        rec = get_calendar(day, a.out, a.reuse, nq)
        cal += qm.parse_calendar(day, rec.get('rows') or [])
        if rec.get('error'):
            cal_gaps.append(day)
    radar = dict(qm.earnings_radar(cal, rows, book, a.as_of), gaps=cal_gaps)
    print(json.dumps({'step': 'calendar', 'rows': len(cal), 'seconds': round(time.time() - t0)}), flush=True)

    inu = [r for r in rows if r['in_universe']]
    std = [r for r in inu if r['route'] == 'standard']
    passers = qm.rank([r for r in std if r['status'] == 'pass'])
    near = sorted([r for r in std if r['status'] == 'near_miss'], key=lambda r: -r['market_cap'])
    fins = sorted([r for r in inu if r['route'] == 'financial' and r['status'] == 'pass'], key=lambda r: -(r.get('roe') or 0))
    fin_near = sorted([r for r in inu if r['route'] == 'financial' and r['status'] == 'near_miss'], key=lambda r: -r['market_cap'])
    # incomplete rows worth a manual look: no failed gate, or one failed condition with an analyst gate missing
    unread = sorted([r for r in inu if r['status'] == 'incomplete' and r['basis'] and r.get('business')
                     and not r.get('behind_latest_filing') and (not r['failed'] or qm.missing(r['business']))],
                    key=lambda r: (bool(r['failed']), -r['market_cap']))
    commodity = sorted([r for r in inu if r['route'] == 'commodity' and r['status'] in ('pass', 'near_miss')],
                       key=lambda r: (r['status'] != 'pass', -r['market_cap']))
    ann = sorted([r for r in inu if r['route'] == 'annual_only' and r['status'] in ('pass', 'near_miss')],
                 key=lambda r: (r['status'] != 'pass', -r['market_cap']))
    by_sym = {r['symbol']: r for r in rows}
    # saved valuations: marked on every listed and thesis-book name, read by no gate and no rank (the MSFT slip, 06.11)
    lean = qm.lean_module()
    saved = qm.saved_book(a.research)
    listed = {id(r): r for r in passers + near + fins + fin_near + commodity + ann + unread}
    listed.update({id(by_sym[qm.norm_symbol(t)]): by_sym[qm.norm_symbol(t)] for t in book if qm.norm_symbol(t) in by_sym})
    qm.attach_saved(list(listed.values()), saved, a.as_of, last_day, lean)
    # the DELL pattern: listed names 25% or more below the high whose analyst data shows an improvement (information,
    # no gate; commodity names keep the mark in their own table only)
    diverging = qm.divergence_rows([('passers', 'עברה', passers), ('near_misses', 'כמעט', near), ('financials', 'פיננסים', fins),
                                    ('financial_near_misses', 'פיננסים, כמעט', fin_near), ('annual_only', 'שנתי בלבד', ann)])
    book_rows = []
    for t in book:
        r = by_sym.get(qm.norm_symbol(t))
        if r is None:
            book_rows.append({'symbol': t, 'status': 'not_found', 'note': 'לא נמצאה ברשימת המניות או במפת ה-SEC',
                              'saved_valuation': qm.saved_valuation(saved, t, None, a.as_of, None, lean)})
        else:
            book_rows.append(dict(r, note=None if r['in_universe'] else 'שווי שוק מתחת לסף'))
    gate_fails = {}
    for r in std:
        for g in qm.failed(r['quality']):
            gate_fails[g['gate']] = gate_fails.get(g['gate'], 0) + 1
    counts.update({
        'no_cik': len(no_cik), 'facts_missing': sum(1 for r in inu if not r['basis']),
        'route_standard': len(std), 'route_commodity': sum(1 for r in inu if r['route'] == 'commodity'),
        'commodity_pass': sum(1 for r in commodity if r['status'] == 'pass'),
        'commodity_near_miss': sum(1 for r in commodity if r['status'] == 'near_miss'), 'route_financial': sum(1 for r in inu if r['route'] == 'financial'),
        'route_annual_only': sum(1 for r in inu if r['route'] == 'annual_only'),
        'standard_quality_all_pass': sum(1 for r in std if not qm.failed(r['quality']) and not qm.missing(r['quality'])),
        'standard_quality_one_fail': sum(1 for r in std if len(qm.failed(r['quality'])) == 1 and not qm.missing(r['quality'])),
        'standard_incomplete': sum(1 for r in std if r['status'] == 'incomplete'),
        'business_fetched': len(want), 'analyst_data_gaps': len(est_gaps), 'price_gaps': len(price_gaps),
        'divergence': len(diverging), 'radar_reported': len(radar['reported']), 'radar_upcoming': len(radar['upcoming']),
        'passers': len(passers), 'near_misses': len(near),
        'financial_pass': len(fins), 'financial_near_miss': len(fin_near),
        'no_gate_failed_but_incomplete': sum(1 for r in unread if not r['failed']),
        'one_failed_and_analyst_data_missing': sum(1 for r in unread if r['failed']),
        'analyst_data_not_readable': sum(1 for r in want if any(g.get('why') == 'erratic' for g in r.get('business') or [])),
        'analyst_data_other_class': len(other_class), 'analyst_pages_not_asked': breaker.skipped,
        'stale_period': sum(1 for r in inu if r.get('stale')), 'behind_latest_filing': sum(1 for r in inu if r.get('behind_latest_filing')),
        'annual_only_pass': sum(1 for r in ann if r['status'] == 'pass'),
        'annual_only_near_miss': sum(1 for r in ann if r['status'] == 'near_miss'),
        'standard_quality_gate_fails': gate_fails,
    })
    incomplete = [{'symbol': r['symbol'], 'route': r['route'], 'market_cap': r['market_cap'], 'missing': [g['gate'] for g in r['missing']],
                   'gaps': r['gaps']} for r in inu if r['status'] == 'incomplete']
    stale = sorted([r for r in inu if r['route'] == 'annual_only' and not r['basis']], key=lambda r: -r['market_cap'])
    counts['annual_only_stale'] = len(stale)
    for key, lst in (('passers', passers), ('near_misses', near)):
        for state in ('fail', 'pass', 'unknown'):
            counts[f'{key}_saved_{state}'] = sum(1 for r in lst if r['saved_valuation']['state'] == state)
    saved_list = saved_summary([('passers', passers), ('near_misses', near), ('financials', fins), ('financial_near_misses', fin_near),
                                ('commodity', commodity), ('annual_only', ann), ('unread', unread)], book_rows)
    res = {
        'as_of': a.as_of, 'last_trading_day': last_day, 'min_cap': a.min_cap, 'generated_at_utc': _now(),
        'analyst_read': analyst_read,
        'method': {'version': 2, 'gates': qm.GATES, 'rank_keys': list(qm.RANK_KEYS),
                   'rank': 'plain sum of percentile ranks among the passers, no weights; a passer without eps_revision_pct (a zero '
                           'or negative consensus has no percentage) gets the median of the others for that component',
                   'price': 'information only since 25.11.2026, never a gate: the three price gates (12-1 return above SPY, price '
                            'above a rising 200-day average, within 25% of the 52-week high) threw out DELL, 43% below its high, '
                            'just before its best quarter. trend_measures, price_info and rel_strength are shown; no status and '
                            'no rank reads them',
                   'prices': 'Yahoo daily closes (adjusted for splits; the 12-1 return also for dividends)',
                   'business_gates': {
                       'eps_revisions': qm.RULE_REVISIONS, 'earnings_beats': qm.RULE_BEATS,
                       'counted_as': 'ONE failed condition together: a missed quarter lowers the full-year consensus by itself, '
                                     'so the two gates read the same event. A row with clean quality gates that fails both is a '
                                     'near miss with both reasons',
                       'legs': 'a leg known to fail fails its gate; a gate is missing only when no known leg fails and one is unknown',
                       'not_readable': f'both gates are missing, never failed, when the last two EPS surprises are beyond '
                                       f'{qm.ERRATIC_SURPRISE_PCT:.0f}% in opposite directions (investment gains in EPS, or a base '
                                       'of a few cents)',
                       'what_they_stop': 'a business that stopped growing is stopped by the quality gates (PYPL failed four of them '
                                         'on 25.11.2026 and passed both analyst gates); the analyst gates only check that the last '
                                         'two quarters and the last month of estimates are not getting worse',
                       'source': 'Nasdaq analyst pages per company (api.nasdaq.com: company/<S>/earnings-surprise, '
                                 'analyst/<S>/earnings-forecast, analyst/<S>/estimate-momentum); Nasdaq names Zacks Investment '
                                 'Research as its vendor (the pages themselves carry no attribution); an unofficial public API, '
                                 'read as a snapshot (analyst_read gives the day)',
                       'revision_window': f'{qm.REVISION_MONTH_DAYS} days (Nasdaq: the consensus now against one month ago; the '
                                          'up/down count covers 4 weeks). When our own saved readings of the same fiscal year are '
                                          f'older than that, the oldest one within {qm.REVISION_HISTORY_DAYS} days, so the window '
                                          'can differ between rows (each row carries eps_revision_window_days); the -1% bar is the '
                                          'same for every window',
                       'history': f'{HISTORY_DIR}/<SYMBOL>.jsonl beside the output folder: one line per fiscal year and day read. '
                                  'It is under runs/ (not in Git): the window grows only while that folder is kept. A saved '
                                  'reading on another per-share scale (a split) is not compared',
                       'page_checks': f'{qm.MOMENTUM_NO_VALUE:g} on the estimate-momentum page means no value; its current '
                                      'consensus must match the forecast table to the cent or the month-ago number is not used',
                       'share_classes': 'a class without an analyst record is read from another class of the same company '
                                        "(estimates.read_from)",
                       'missing': 'a company without analyst data is incomplete, never a passer'},
                   'divergence': f"flag '{qm.DIVERGENCE}' (information, no gate), on passers and near misses only: the last close "
                                 f"is {qm.GATES['divergence_below_high'] * 100:.0f}% or more below the 52-week high and the analyst "
                                 'data shows an improvement (both gates pass, more analysts raised than cut, the consensus is '
                                 'higher than at the start of the window, both quarters at or above the consensus)',
                   'radar': f'Nasdaq earnings calendar (api.nasdaq.com/api/calendar/earnings), {qm.RADAR_BACK_DAYS} days back and '
                            f'{qm.RADAR_AHEAD_DAYS} ahead; reported: status pass or near_miss with an EPS surprise of '
                            f'+{qm.RADAR_MIN_SURPRISE_PCT:.0f}% or more, plus every thesis-book company (also one on a past day '
                            'without an EPS number yet); upcoming: passers, near misses and thesis-book companies; in_quality: '
                            'whether the quality numbers already cover the reported quarter',
                   'market_cap': 'Nasdaq listing feed snapshot',
                   'fundamentals': 'SEC XBRL companyfacts as reported (no adjustment for one-offs or acquisitions)',
                   'fundamentals_source': 'the companyfacts API read per company (one request per company above the floor) and its '
                                          'submissions file for the SIC code; not the XBRL frames API',
                   'saved_valuation': 'newest research/companies/<T>/<date> valuation (valuation_record.latest_valuation) or newer '
                                      'research/rejected.jsonl row; lean params repriced to the screen close (lean_model.reprice) and '
                                      'rerun; a custom model compared with its saved buy-test price; marked only, read by no gate '
                                      'or rank',
                   'durability': 'revenue did not fall in any of the last 3 completed fiscal years; 3-year growth rate >= 10% a year',
                   'commodity_sic': {str(k): v for k, v in sorted(qm.COMMODITY_SIC.items())},
                   'coarse_pass': 'none: companyfacts were read for every company above the floor',
                   'not': 'not a valuation, not a buy signal, not backtested'},
        'spy': spy, 'fx': fx, 'counts': counts,
        'counts_he': [['מניות ברשימת Nasdaq', counts['listed']], ['שווי שוק מעל הסף', counts['cap_ok']],
                      ['מניות רגילות (בלי בכורה, אופציות, יחידות, קרנות)', counts['common']], ['עם מספר CIK ב-SEC', counts['with_cik']],
                      ['חברות (סוג מניה אחד לחברה)', counts['companies']], ['מסלול רגיל', counts['route_standard']],
                      ['חברות סחורות (טבלה נפרדת)', counts['route_commodity']],
                      ['פיננסים', counts['route_financial']], ['זרות עם נתונים שנתיים בלבד', counts['route_annual_only']],
                      ['רגיל: עברו את כל תנאי האיכות', counts['standard_quality_all_pass']],
                      ['רגיל: נכשלו בתנאי איכות אחד', counts['standard_quality_one_fail']],
                      ['נקראו נתוני אנליסטים ומחיר', counts['business_fetched']],
                      ['מהן בלי נתוני אנליסטים מלאים', counts['analyst_data_gaps']], ['עברו הכול', counts['passers']],
                      ['כמעט עברו', counts['near_misses']], ['חסר נתון (לא נספרו כעוברות)', counts['standard_incomplete']],
                      ['נתוני אנליסטים לא קריאים (רווח למניה קופץ)', counts['analyst_data_not_readable']],
                      ['המחיר ירד, העסק משתפר', counts['divergence']]],
        'gaps': {'no_cik': no_cik, 'incomplete': incomplete, 'prices': price_gaps, 'estimates': est_gaps, 'calendar': cal_gaps,
                 'revenue_mixed_tags': [r['symbol'] for r in inu if r.get('revenue_mixed_tags')],
                 'stale_period': [{'symbol': r['symbol'], 'period_end': r.get('period_end'), 'status': r['status']} for r in inu if r.get('stale')],
                 'behind_latest_filing': [{'symbol': r['symbol'], 'period_end': r.get('period_end'), 'latest_filing': r.get('latest_filing')}
                                          for r in inu if r.get('behind_latest_filing')],
                 'stale_annual_filers': [{'symbol': r['symbol'], 'market_cap': r['market_cap'], 'gaps': r['gaps']} for r in stale]},
        'gaps_he': [f"{len(stale)} חברות זרות (20-F / 40-F) שהשנה האחרונה שלהן בנתוני ה-SEC ישנה מדי או חסרה, ולכן לא נבדקו: "
                    + ', '.join(r['symbol'] for r in stale[:40]) + (' ועוד' if len(stale) > 40 else ''),
                    *([f"{len(no_cik)} מניות מעל הסף בלי מספר CIK במפת ה-SEC (לא נבדקו): " + ', '.join(x['symbol'] for x in no_cik[:40])]
                      if no_cik else []),
                    f"{len(incomplete)} חברות עם נתון חסר (למשל בלי תג הוצאות הון או פחות משמונה רבעונים): לא נספרו כעוברות, רשומות ב-screen.json.",
                    'חברות שההכנסות שלהן מתויגות בדוחות תחת מושגים שונים בתקופות שונות (הצמיחה לא אמינה, לא נספרו כעוברות): '
                    + (', '.join(r['symbol'] for r in sorted(inu, key=lambda r: -r['market_cap']) if r.get('revenue_mixed_tags') and
                                 len(qm.failed(r['quality'])) <= 1) or 'אין'),
                    *([f"{sum(1 for r in inu if r.get('stale'))} חברות שהתקופה האחרונה שלהן בנתונים הסתיימה לפני יותר מחמישה חודשים (מסומנות \"ישן\"; מהן ברשימות: "
                       + (', '.join(r['symbol'] for r in passers + near + commodity + fins + ann if r.get('stale')) or 'אין') + ').'] if any(r.get('stale') for r in inu) else []),
                    *([f"{sum(1 for r in inu if r.get('behind_latest_filing'))} חברות שהגישו דוח חדש יותר מהתקופה שנמצאה בנתוני ה-SEC "
                       '(הדוח האחרון חסר שם או בלי שורת הכנסות): לא נבדקו ולא נספרו, לא כעוברות ולא כנכשלות: '
                       + ', '.join(r['symbol'] for r in sorted(inu, key=lambda r: -r['market_cap']) if r.get('behind_latest_filing'))]
                      if any(r.get('behind_latest_filing') for r in inu) else []),
                    *([f"{len(est_gaps)} חברות בלי נתוני אנליסטים מלאים בדפי Nasdaq (בלי תחזית, בלי שני דוחות, רווח למניה לא קריא, "
                       'או שהדף לא נקרא): לא נספרו כעוברות. מהן מופיעות ברשימת "חסר להן נתון": '
                       + (', '.join(r['symbol'] for r in unread if any(g['gate'] in qm.BUSINESS_GATES for g in r['missing'])) or 'אין')
                       + '. הרשימה המלאה ב-screen.json (gaps.estimates).'] if est_gaps else []),
                    *([f"{breaker.skipped} חברות שדפי האנליסטים שלהן לא נשאלו, כי Nasdaq הפסיקה לענות באמצע ההרצה. "
                       'להריץ שוב עם --reuse כדי להשלים.'] if breaker.skipped else []),
                    *([f"נתוני אנליסטים שנקראו מסוג מניה אחר של אותה חברה, כי לסוג שנסרק אין נתונים ({len(other_class)}): "
                       + ', '.join(f"{r['symbol']} (נקרא {r['estimates']['read_from']})" for r in want if (r.get('estimates') or {}).get('read_from'))
                       + '.'] if other_class else []),
                    *([f"{len(price_gaps)} חברות בלי מחירים יומיים (עמודות המחיר ריקות; זה לא משפיע על התנאים)."] if price_gaps else []),
                    *(['מחירי SPY לא נקראו: התשואה מול המדד ריקה, ויום המסחר האחרון נקבע לפי רוב החברות. זה לא משפיע על התנאים.']
                      if not spy else []),
                    'נתוני האנליסטים הם תמונת מצב של יום הקריאה'
                    + (f" ({qm._dmy(analyst_read['first'])}"
                       + ('' if analyst_read['first'] == analyst_read['last'] else f" עד {qm._dmy(analyst_read['last'])}") + ')'
                       if analyst_read else '')
                    + '. חברה שדיווחה בימים האחרונים: ההפתעה ברווח כבר מעודכנת, אבל מספרי האיכות עדיין מהרבעון הקודם, '
                    'עד שהדוח יופיע בנתוני ה-SEC. גם התחזית השנתית מתעדכנת באיחור של כמה ימים אחרי דוח, ולכן השינוי בתחזית '
                    'יכול להיראות קטן מיד אחרי הפתעה גדולה.',
                    *([f"דפי האנליסטים נקראו ב-{qm._dmy(analyst_read['last'])}, לא בתאריך החיפוש ({qm._dmy(a.as_of)}): תחזיות הרווח וספירת האנליסטים "
                       'הן של יום הקריאה. דוחות שפורסמו אחרי תאריך החיפוש לא נכנסו לתנאי העמידה בתחזית.']
                      if analyst_read and (analyst_read['first'] != a.as_of or analyst_read['last'] != a.as_of) else []),
                    'תחזיות הרווח והפתעות הרווח הן המספרים של ספק הנתונים של Nasdaq (Zacks) כפי שהם. הרווח למניה שם הוא לפי '
                    'ההגדרה שלו, ויכול לכלול רווחי השקעות או סעיף חד-פעמי אחרת ממה שהחברה מציגה: הפתעה חריגה מאוד כדאי לבדוק מול הדוח.',
                    'כיסוי דל: כשרק אנליסט אחד או שניים מכסים חברה, התחזית הממוצעת זזה גם בלי שמישהו שינה תחזית. '
                    'מספר התחזיות לכל חברה נמצא ב-screen.json (estimates.years).',
                    'שווי השוק מרשימת Nasdaq, שיכולה לפגר ביום מסחר: סף השווי נבדק לפיה, ולחברות שנקרא להן מחיר השווי והמכפילים '
                    f"עודכנו למחיר הסגירה של {last_day or 'היום האחרון שנקרא'}. סוג המניה נבחר לפי מחזור המסחר של יום אחד.",
                    'הוצאות הון: שורת הסך בדוח התזרים, או סכום שורות הרכיבים (בארות נפט, ציוד אחר) כשהוא גדול יותר.',
                    'תזרים חופשי = תזרים מפעילות פחות רכישת רכוש קבוע; פיתוח תוכנה מהוון ותגמול במניות לא נוכו.',
                    'צמיחה כוללת רכישות; לא נעשתה התאמה לצמיחה אורגנית.'],
        'passers': passers, 'near_misses': near, 'commodity': commodity, 'financials': fins, 'financial_near_misses': fin_near, 'unread': unread,
        'annual_only': ann, 'thesis_book': book_rows, 'saved_valuations': saved_list,
        'divergence': diverging, 'radar': radar,
        'rows': rows,
    }
    _save(os.path.join(a.out, 'screen.json'), res)
    with open(os.path.join(a.out, 'screen_he.md'), 'w', encoding='utf-8') as f:
        f.write(qm.render_he(res))
    print(json.dumps({'out': a.out, 'seconds': round(time.time() - t0), **{k: v for k, v in counts.items()}}))


if __name__ == '__main__':
    main()
