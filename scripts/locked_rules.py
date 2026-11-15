"""Locked rules, beta: the 2-year weekly beta of a ticker vs SPY, printed as the params `judgement.beta` block (JSON).

Usage: python scripts/locked_rules.py TICKER [--as-of YYYY-MM-DD] [--weeks 104] [--save FILE]
Fetches weekly closes of TICKER and SPY from the Yahoo chart API (interval=1wk with period1/period2) and passes them
to fundamental_engine.locked_rules.beta_2y_weekly (the arithmetic and the rule live there; this script only fetches).
Only complete weeks up to --as-of (default: today, UTC) are used, so a rerun on the same day gives the same number.
--save writes the closes that were used next to the result (e.g. under runs/, gitignored), so the beta can be recomputed
without the network. Nothing else is written; no saved params file is read or changed.
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fundamental_engine import locked_rules as LR  # noqa: E402

YAHOO = 'https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1wk'
BROWSER = {'User-Agent': 'Mozilla/5.0'}
BENCHMARK = 'SPY'


def weekly_closes(symbol, start, end, opener=None):
    """{ISO date: close} of the weekly bars of symbol between two dates (Yahoo chart API). Bars without a close are
    left out. A bar's date is its exchange-local day: Yahoo stamps a weekly bar at local midnight of the week's Monday
    and adds a last bar stamped at the latest trade; the exchange's own time zone is used so that a daylight-saving
    hour cannot move a bar into the previous week."""
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
    req = urllib.request.Request(YAHOO.format(symbol, p1, p2), headers=BROWSER)
    with (opener or urllib.request.urlopen)(req, timeout=30) as r:
        res = json.load(r)['chart']['result'][0]
    meta = res.get('meta', {})
    try:
        zone = ZoneInfo(meta['exchangeTimezoneName'])
    except Exception:                                    # no zone name or no tz database: today's offset, plus 3 hours
        zone = timezone(timedelta(seconds=(meta.get('gmtoffset') or 0) + 3 * 3600))   # so a DST hour cannot move the day
    closes = res['indicators']['quote'][0]['close']
    out = {}
    for ts, c in zip(res.get('timestamp') or [], closes):
        if c is not None:
            out[datetime.fromtimestamp(ts, tz=zone).date().isoformat()] = round(float(c), 4)
    return out


def beta_for(ticker, as_of, weeks=LR.BETA_WEEKS, opener=None):
    """(judgement.beta block with source and correlation, {ticker: closes, SPY: closes})."""
    start = as_of - timedelta(days=7 * (weeks + 6))       # a few spare weeks for holidays and listing gaps
    series = {s: weekly_closes(s, start, as_of, opener) for s in (ticker, BENCHMARK)}
    res = LR.beta_2y_weekly(series[ticker], series[BENCHMARK], as_of=as_of, weeks=weeks)
    block = LR.beta_block(res)
    block.update({'ticker': ticker, 'as_of': as_of.isoformat(), 'correlation': res['correlation'],
                  'short_history': res['short_history'], 'rule': res['rule'],
                  'source': 'Yahoo chart API weekly closes (interval=1wk), price returns, vs ' + BENCHMARK})
    return block, series


def main(argv=None):
    ap = argparse.ArgumentParser(description='2-year weekly beta vs SPY, adjusted 0.67 x raw + 0.33')
    ap.add_argument('ticker')
    ap.add_argument('--as-of', default=None, help='YYYY-MM-DD; default today (UTC)')
    ap.add_argument('--weeks', type=int, default=LR.BETA_WEEKS)
    ap.add_argument('--save', default=None, help='write the block and the closes used to this JSON file')
    a = ap.parse_args(argv)
    as_of = date.fromisoformat(a.as_of) if a.as_of else datetime.now(timezone.utc).date()
    block, series = beta_for(a.ticker.upper(), as_of, a.weeks)
    if a.save:
        os.makedirs(os.path.dirname(os.path.abspath(a.save)), exist_ok=True)
        with open(a.save, 'w', encoding='utf-8') as f:
            json.dump({'beta': block, 'closes': series}, f, ensure_ascii=False, indent=1)
    print(json.dumps(block, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
