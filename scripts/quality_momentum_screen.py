"""Quality + trend screen over US-listed companies above a market value floor (network + CLI).

    SEC_USER_AGENT="app-name contact@example.com" python scripts/quality_momentum_screen.py runs/screens/2026-11-15 --as-of 2026-11-15 [--min-cap 5e9] [--reuse]

Sources: the Nasdaq listing feed (symbol, market value, sector; an unofficial public API), the SEC ticker map, the SEC
companyfacts API read per company (data.sec.gov/api/xbrl/companyfacts/CIK##########.json, one request per company above
the floor; not the XBRL frames API) and its submissions file (SIC code), Yahoo daily adjusted closes for the names that
reach the trend gates. Only the extracted series are kept (facts/CIK*.json), not the raw companyfacts. --reuse reads the
saved files and fetches only what is missing. The SEC User-Agent is read from the environment and never written to disk.
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


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _save(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


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
    for sub in ('raw', 'facts', 'prices'):
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
    if not spy:
        raise SystemExit('SPY bars missing: the trend gates cannot be computed')
    last_day = spy_bars['dates'][-1]
    want = [r for r in rows if r['basis'] and (qm.needs_trend(r['quality']) or r['symbol'] in book)]
    with ThreadPoolExecutor(3) as ex:
        bars = list(ex.map(lambda r: get_prices(r['symbol'], a.out, a.reuse, yh, a.as_of), want))
    fx = {}
    for ccy in sorted({r['currency'] for r in want if r.get('currency') not in (None, 'USD')}):
        b = get_prices(f'{ccy}=X', a.out, a.reuse, yh, a.as_of)
        fx[ccy] = b['adjclose'][-1] if b['adjclose'] else None
    print(json.dumps({'step': 'prices', 'n': len(want), 'seconds': round(time.time() - t0)}), flush=True)
    price_gaps = []
    for r, b in zip(want, bars):
        stale = bool(b['dates']) and b['dates'][-1] != last_day
        tm = None if stale else qm.trend_measures(b['adjclose'], b.get('close'), b.get('high'))
        r['trend_measures'] = tm
        r['trend'] = qm.trend_gates(tm, spy)
        if tm is None:
            why = b.get('error') or ('last bar %s, not %s' % (b['dates'][-1], last_day) if stale else 'under 253 daily bars (%d)' % len(b['dates']))
            r['gaps'].append('prices: ' + why)
            price_gaps.append({'symbol': r['symbol'], 'why': why})
        if tm and r['price'] > 0 and 0.5 < tm['last'] / r['price'] < 2:     # the listing feed can be a trading day behind
            r['market_cap_feed'], r['price_feed'] = r['market_cap'], r['price']
            r['market_cap'], r['price'] = r['market_cap'] * tm['last'] / r['price'], tm['last']
        rate = 1. if r.get('currency') == 'USD' else fx.get(r.get('currency'))
        r.update(qm.price_columns(r, r['market_cap'], rate))
        if rate is None:
            r['gaps'].append('no exchange rate for %s: price multiples not computed' % r.get('currency'))
    for r in rows:
        r['status'] = qm.classify(r['quality'], r.get('trend'))
        gates = r['quality'] + (r.get('trend') or [])
        r['failed'], r['missing'] = qm.failed(gates), qm.missing(gates)
        if r.get('trend_measures'):
            r['rel_strength'] = r['trend_measures']['ret_12_1'] - spy['ret_12_1']
        if r.get('behind_latest_filing'):
            r['status'], r['note'] = 'incomplete', 'הדוח האחרון לא נקרא בנתוני ה-SEC'      # an older report is not a result

    inu = [r for r in rows if r['in_universe']]
    std = [r for r in inu if r['route'] == 'standard']
    passers = qm.rank([r for r in std if r['status'] == 'pass'])
    near = sorted([r for r in std if r['status'] == 'near_miss'], key=lambda r: -r['market_cap'])
    fins = sorted([r for r in inu if r['route'] == 'financial' and r['status'] == 'pass'], key=lambda r: -(r.get('roe') or 0))
    fin_near = sorted([r for r in inu if r['route'] == 'financial' and r['status'] == 'near_miss'], key=lambda r: -r['market_cap'])
    unread = sorted([r for r in inu if r['status'] == 'incomplete' and r['basis'] and not r['failed'] and r.get('trend')
                     and not r.get('behind_latest_filing')],
                    key=lambda r: -r['market_cap'])
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
        'trend_fetched': len(want), 'passers': len(passers), 'near_misses': len(near),
        'financial_pass': len(fins), 'financial_near_miss': len(fin_near), 'no_gate_failed_but_incomplete': len(unread),
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
        'method': {'gates': qm.GATES, 'rank_keys': list(qm.RANK_KEYS), 'rank': 'plain sum of percentile ranks among the passers, no weights',
                   'trend_prices': 'Yahoo daily adjusted closes', 'market_cap': 'Nasdaq listing feed snapshot',
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
                      ['נבדקה מגמת מחיר', counts['trend_fetched']], ['עברו הכול', counts['passers']],
                      ['כמעט עברו', counts['near_misses']], ['חסר נתון (לא נספרו כעוברות)', counts['standard_incomplete']]],
        'gaps': {'no_cik': no_cik, 'incomplete': incomplete, 'prices': price_gaps,
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
                    'שווי השוק מרשימת Nasdaq, שיכולה לפגר ביום מסחר: סף השווי נבדק לפיה, ולחברות שנבדקה להן מגמה השווי והמכפילים '
                    f'עודכנו למחיר הסגירה של {last_day}. סוג המניה נבחר לפי מחזור המסחר של יום אחד.',
                    'הוצאות הון: שורת הסך בדוח התזרים, או סכום שורות הרכיבים (בארות נפט, ציוד אחר) כשהוא גדול יותר.',
                    'תזרים חופשי = תזרים מפעילות פחות רכישת רכוש קבוע; פיתוח תוכנה מהוון ותגמול במניות לא נוכו.',
                    'צמיחה כוללת רכישות; לא נעשתה התאמה לצמיחה אורגנית.'],
        'passers': passers, 'near_misses': near, 'commodity': commodity, 'financials': fins, 'financial_near_misses': fin_near, 'unread': unread,
        'annual_only': ann, 'thesis_book': book_rows, 'saved_valuations': saved_list,
        'rows': rows,
    }
    _save(os.path.join(a.out, 'screen.json'), res)
    with open(os.path.join(a.out, 'screen_he.md'), 'w', encoding='utf-8') as f:
        f.write(qm.render_he(res))
    print(json.dumps({'out': a.out, 'seconds': round(time.time() - t0), **{k: v for k, v in counts.items()}}))


if __name__ == '__main__':
    main()
