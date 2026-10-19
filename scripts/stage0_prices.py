"""Stage 0 price-source audit (no business logic).

For each frozen golden-set company this finds the price that was actually quoted on the as-of date (the
"as-traded" close of the last trading day <= as-of, before any later split adjustment) and checks it against
every second source that can see that date.

Sources, as saved by scripts/stage0_extract.py and the earlier bulk download:
  primary  Alpha Vantage TIME_SERIES_MONTHLY_ADJUSTED   columns close (as traded) and adjusted close
  check    Interactive Brokers get_price_history         monthly (5 years max) or daily (1 week) bars
  check    Yahoo chart v8 monthly file                   'c' is split-adjusted; undone with its 'splits' list

Status per company:
  VERIFIED_IB      AV close and IB close agree within 1%
  VERIFIED_YAHOO   IB cannot see the date; AV close and Yahoo close (split adjustment undone) agree within 1%
  UNVERIFIED       no second source could see the date, or the sources disagree by more than 1%
  MISSING          the primary source has no bar for the as-of month

usage: python scripts/stage0_prices.py RAW_DIR YAHOO_PX_DIR OUT_JSON
"""
import csv, datetime as dt, json, os, sys

TOL = 0.01
raw, ypx, out = sys.argv[1:4]
here = os.path.dirname(os.path.abspath(__file__))
golden = json.load(open(os.path.join(here, '..', 'stage0', 'golden_set.json')))
manifest = {m['file']: m for m in json.load(open(os.path.join(raw, 'extract_manifest.json')))}

# conid -> ticker from the saved IB contract searches (first STK result whose symbol matches)
conid = {}
for f in os.listdir(os.path.join(raw, 'prices', 'ibkr')):
    if f.startswith('search_'):
        tk = f[7:-5]
        for r in json.load(open(os.path.join(raw, 'prices', 'ibkr', f)))['results']:
            if r['symbol'] == tk:
                conid[tk] = r['underlying_contract_id']
                break


def av_rows(tk):
    p = os.path.join(raw, 'prices', 'alphavantage', f'{tk}_TIME_SERIES_MONTHLY_ADJUSTED.csv')
    if not os.path.exists(p):
        return None, None
    rows = [dict(date=r['timestamp'], close=float(r['close']), adj=float(r['adjusted close']),
                 div=float(r['dividend amount'])) for r in csv.DictReader(open(p))]
    return sorted(rows, key=lambda r: r['date']), 'prices/alphavantage/' + os.path.basename(p)


def inferred_splits(rows):
    """A split shows up as a jump in close/adjusted-close between consecutive monthly bars.
    Dividends move that ratio by a few percent at most; a factor outside [0.8, 1.25] is treated as a split."""
    ev = []
    for a, b in zip(rows, rows[1:]):
        ra, rb = a['close'] / a['adj'], b['close'] / b['adj']
        f = ra / rb
        if f > 1.25 or f < 0.8:
            ev.append(dict(between=[a['date'], b['date']], factor=round(f, 4)))
    return ev


def ib_close(tk, bar_date):
    if tk not in conid:
        return None, 'no IB contract searched'
    d = os.path.join(raw, 'prices', 'ibkr')
    for f in sorted(os.listdir(d)):
        if not f.startswith(f'{conid[tk]}_'):
            continue
        j = json.load(open(os.path.join(d, f)))
        times = [t[:10] for t in j['time']]
        if 'ONE_DAY' in f and bar_date in times:
            return dict(close=j['close'][times.index(bar_date)], bar=bar_date, file='prices/ibkr/' + f, step='day'), None
        if 'ONE_MONTH' in f:
            ym = bar_date[:7]
            hits = [i for i, t in enumerate(times) if t[:7] == ym]
            if hits and j['chart_start'][:10] <= bar_date:
                i = hits[0]
                return dict(close=j['close'][i], bar=j['time'][i][:10], file='prices/ibkr/' + f, step='month',
                            corp_actions_in_file=bool(j.get('corp_actions'))), None
            if not hits:
                return None, f"IB monthly history starts {j['chart_start'][:10]} (5-year limit)"
    return None, 'IB history for this date not retrieved (5-year limit for monthly; daily only for the last week)'


def yahoo_close(tk, bar_date):
    p = os.path.join(ypx, f'{tk}.json')
    if not os.path.exists(p):
        return None, 'no Yahoo file'
    j = json.load(open(p))
    if not j or not j.get('t'):
        return None, 'Yahoo file empty'
    ym = bar_date[:7]
    for t, c in zip(j['t'], j['c']):
        d = dt.datetime.utcfromtimestamp(t)
        # Yahoo monthly timestamps are the month start (sometimes the day before, in exchange time)
        d2 = (d + dt.timedelta(hours=12)).strftime('%Y-%m')
        if d2 == ym and c is not None:
            undo, applied = 1.0, []
            for ts, (num, den) in j.get('splits', {}).items():
                if dt.datetime.utcfromtimestamp(int(ts)).strftime('%Y-%m-%d') > bar_date:
                    undo *= num / den
                    applied.append([dt.datetime.utcfromtimestamp(int(ts)).strftime('%Y-%m-%d'), num, den])
            return dict(close_split_adjusted=c, close_as_traded=c * undo, later_splits_undone=applied), None
    return None, 'no Yahoo bar for that month'


res = []
for co in golden['companies']:
    tk, asof = co['tk'], co['asof']
    rows, av_file = av_rows(tk)
    r = dict(tk=tk, asof=asof, group=co['group'])
    if not rows:
        r.update(status='MISSING', reason='no Alpha Vantage series saved')
        res.append(r)
        continue
    bars = [x for x in rows if x['date'] <= asof and x['date'][:7] == asof[:7]]
    r['av'] = dict(file=av_file, sha256=manifest[av_file]['sha256'], retrieved_utc=manifest[av_file]['retrieved_utc'],
                   first_bar=rows[0]['date'], last_bar=rows[-1]['date'], n_bars=len(rows),
                   inferred_splits=inferred_splits(rows),
                   splits_endpoint=None)
    sp = os.path.join(raw, 'prices', 'alphavantage', f'{tk}_SPLITS.json')
    if os.path.exists(sp):
        r['av']['splits_endpoint'] = open(sp).read()
    if not bars:
        r.update(status='MISSING', reason=f'no AV bar in {asof[:7]} on or before the as-of date')
        res.append(r)
        continue
    b = bars[-1]
    r['as_traded'] = dict(bar_date=b['date'], close=b['close'], adjusted_close=b['adj'],
                          later_adjustment_factor=round(b['close'] / b['adj'], 6))
    # an as-of mid-month: the monthly bar dated the as-of day is the month-to-date bar, i.e. that day's close,
    # provided the series was pulled after that day's close (20:00 UTC)
    if b['date'] == asof and asof[8:] not in ('28', '29', '30', '31'):
        r['as_traded']['note'] = ('month-to-date bar; pulled ' + r['av']['retrieved_utc'] +
                                  (' (after the 20:00 UTC close)' if r['av']['retrieved_utc'][:10] > asof or
                                   r['av']['retrieved_utc'][11:13] >= '20' else ' (BEFORE the close: intraday!)'))
    ib, ib_why = ib_close(tk, b['date'])
    y, y_why = yahoo_close(tk, b['date'])
    checks = {}
    if ib:
        ib['rel_diff'] = round(ib['close'] / b['close'] - 1, 5)
        checks['ib'] = ib
    else:
        checks['ib'] = dict(unavailable=ib_why)
    if y:
        y['rel_diff'] = round(y['close_as_traded'] / b['close'] - 1, 5)
        checks['yahoo'] = y
    else:
        checks['yahoo'] = dict(unavailable=y_why)
    r['checks'] = checks
    if ib and abs(ib['rel_diff']) <= TOL:
        r['status'] = 'VERIFIED_IB'
    elif ib:
        r['status'] = 'UNVERIFIED'
        r['reason'] = f"AV and IB disagree by {ib['rel_diff']:.2%}"
    elif y and abs(y['rel_diff']) <= TOL:
        r['status'] = 'VERIFIED_YAHOO'
    elif y:
        r['status'] = 'UNVERIFIED'
        r['reason'] = f"AV and Yahoo disagree by {y['rel_diff']:.2%}; IB: {ib_why}"
    else:
        r['status'] = 'UNVERIFIED'
        r['reason'] = f'no second source: IB: {ib_why}; Yahoo: {y_why}'
    res.append(r)

summary = {s: [r['tk'] for r in res if r['status'] == s] for s in ('VERIFIED_IB', 'VERIFIED_YAHOO', 'UNVERIFIED', 'MISSING')}
json.dump(dict(generated_utc=dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), tolerance=TOL, summary=summary,
               companies=res), open(out, 'w'), indent=1)
for r in res:
    c = r.get('checks', {})
    print(f"{r['tk']:5} {r['asof']} {r.get('as_traded', {}).get('bar_date', '-'):10} {r.get('as_traded', {}).get('close', 0):9.2f} "
          f"IB {c.get('ib', {}).get('rel_diff', '-')!s:9} Y {c.get('yahoo', {}).get('rel_diff', '-')!s:9} {r['status']:15} "
          f"splits {[(e['between'][1], e['factor']) for e in r.get('av', {}).get('inferred_splits', [])]}")
print(summary)
