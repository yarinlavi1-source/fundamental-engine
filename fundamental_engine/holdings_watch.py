"""Light quarterly update of the owner's holdings against their frozen theses (no revaluation, no orders).

Why: after a quarterly report the fast ticker procedure reruns every agent for every holding. Most quarters only
confirm the frozen forecast. `update` compares each holding's latest frozen thesis (the forecast ledger) with a fresh
FactBook through `monitor.check` and sorts the holdings into:
- new_holding: no thesis in the ledger -> needs a full valuation (fast ticker procedure);
- data_unavailable: no FactBook (no CIK, foreign filer, another reporting currency, no data) -> the reason; the frozen
  value against today's price is still shown when a thesis exists;
- no_new_report: nothing filed after the thesis cutoff -> frozen base value vs today's price and the return at that price;
- light_update: new filings, no high-severity alert and no falsifier tripped -> the thesis stands; changes listed;
- full_rerun_needed: a high-severity alert or a tripped falsifier -> the fast ticker procedure for that holding.
Tickers with a thesis but not in the positions are listed as removed (monitoring stops). Positions are a READ-ONLY copy
of the broker's positions; this module never places, suggests or sizes orders: conclusions stay the valuation's.

Expected return at today's price: the monitor's implied IRR of the frozen base-case flows when the casefile that made the
thesis is available (the ledger stores the record without its flows); otherwise a rough proxy, (5-year base value /
price)^(1/years) - 1 without distributions, labelled as such.
"""
import glob
import json
import os
from datetime import datetime, timezone

from . import facts as F
from . import monitor as M
from .ledger import Ledger, day, parse
from .research_store import STORE, saved_folder

STATUSES = ('new_holding', 'data_unavailable', 'no_new_report', 'light_update', 'full_rerun_needed')
STOCK_TYPES = {'STK', 'STOCK', 'COMMON', 'EQUITY'}
ACTION_HE = {'new_holding': 'הרצה מלאה', 'full_rerun_needed': 'הרצה מלאה', 'light_update': 'עדכון קל',
             'no_new_report': 'אין', 'data_unavailable': 'אין (חסרים נתונים)'}
STATUS_HE = {'new_holding': 'מניה חדשה בתיק, אין הערכה שמורה', 'data_unavailable': 'אין נתונים עדכניים',
             'no_new_report': 'אין דוח חדש', 'light_update': 'דוח חדש, בתוך התחזית',
             'full_rerun_needed': 'דוח חדש, סוטה מהתחזית'}
METRIC_HE = {'revenue': 'הכנסות', 'ebit': 'רווח תפעולי', 'operating_margin': 'שיעור רווח תפעולי', 'fcf': 'תזרים חופשי',
             'fcf_margin': 'שיעור תזרים חופשי', 'fcf_after_sbc': 'תזרים חופשי אחרי תגמול במניות',
             'fcf_after_sbc_margin': 'שיעור תזרים אחרי תגמול במניות', 'shares_outstanding': 'מספר מניות',
             'revenue_growth': 'צמיחת הכנסות', 'gross_margin': 'שיעור רווח גולמי', 'net_debt': 'חוב נטו'}
ALERT_HE = {'revenue_below_bear': 'הכנסות מתחת לתרחיש הדובי', 'operating_margin_below_bear': 'רווחיות מתחת לתרחיש הדובי',
            'fcf_margin_drop': 'ירידה חדה בתזרים', 'dilution_above_assumption': 'דילול מעל ההנחה',
            'net_debt_increase': 'עלייה בחוב נטו', 'guidance_change': 'ההנהלה שינתה תחזית', 'financing_event': 'גיוס חוב או הון',
            'above_bull_case': 'מעל התרחיש השורי', 'price_outside_value_band': 'המחיר מחוץ לטווח השווי',
            'revenue_ttm_growth_below_bear': 'צמיחה ב-12 החודשים האחרונים מתחת לדובי', 'assumption_breached': 'הנחה נשברה'}
BUSINESS = ('revenue', 'ebit', 'operating_margin', 'fcf', 'fcf_margin', 'fcf_after_sbc', 'fcf_after_sbc_margin')


class Unavailable(Exception):
    """A FactBook cannot be built for this holding (the message is the reason shown to the owner)."""


# ------------------------------------------------------------------ inputs

def latest_theses(ledger_paths):
    """{ticker: thesis record} with the latest made_at per ticker (the frozen base case in force), over the union of
    the ledgers (a path or a list: the runs/ ledger and the tracked research/forecast_ledger.jsonl; one thesis_id is one
    thesis wherever it is recorded)."""
    out = {}
    paths = [ledger_paths] if isinstance(ledger_paths, str) else list(ledger_paths)
    for r in (r for p in paths if p and os.path.exists(p) for r in Ledger(p).lines()):
        if r.get('type') != 'thesis' or not isinstance(r.get('record'), dict):
            continue
        t = str(r.get('entity') or r['record'].get('ticker') or '').upper()
        if t and (t not in out or parse(r['made_at'])[0] > parse(out[t]['_made_at'])[0]):
            out[t] = dict(r['record'], _made_at=r['made_at'])
    return out


def stock_rows(positions):
    """(stocks, skipped) from {as_of, rows:[{t, shares, price, value, weight, ...}]}; a row typed as something other than
    a stock (asset_class / sec_type / secType) is skipped."""
    stocks, skipped = [], []
    for r in positions.get('rows') or []:
        kind = str(r.get('asset_class') or r.get('sec_type') or r.get('secType') or 'STK').upper()
        t = str(r.get('t') or r.get('ticker') or r.get('symbol') or '').upper().strip()
        if not t:
            continue
        (stocks if kind in STOCK_TYPES else skipped).append(dict(r, t=t))
    return stocks, skipped


# ------------------------------------------------------------------ helpers

def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _proxy_return(thesis, price, at):
    """Rough expected return: (5-year base value / price)^(1/years) - 1, distributions excluded."""
    five = (thesis.get('values') or {}).get('5y') or {}
    v, d = _num(five.get('base')), five.get('date')
    if not (v and price and d) or v <= 0:
        return None
    yrs = (day(d) - day(at)).days / 365.25
    return (v / price) ** (1 / yrs) - 1 if yrs >= .5 else None


def _view(thesis, pv, pd):
    """Frozen value vs today's price: the monitor's price view plus the expected return used in the table."""
    th = thesis if 'irr_basis' in thesis else dict(thesis, irr_basis={})
    pi = M._price_view(th, pv, pd, M.DEFAULTS)
    base = (pi.get('value_band') or {}).get('base')
    irr = (pi.get('implied_irr') or {}).get('base')
    ret = ({'value': irr, 'kind': 'implied_irr', 'meaning': 'IRR of the frozen base-case flows at today\'s price'} if irr is not None
           else {'value': _proxy_return(thesis, pv, pd), 'kind': 'proxy',
                 'meaning': '(5-year base value / price)^(1/years) - 1, distributions excluded: a rough proxy'})
    return {'price': pv, 'price_as_of': pd, 'base_value_today': base,
            'discount_to_base': (1 - pv / base) if base else None, 'expected_return': ret,
            'expected_return_at_analysis': (thesis.get('expected_return') or {}).get('base'),
            'outside_band': pi.get('outside_band'), 'monitor_price_view': pi}


def _value_signal(changes):
    """'up' / 'down' / 'flat' from business actuals vs the frozen base (a direction signal, not a revaluation)."""
    s = []
    for c in changes:
        if c.get('metric') not in BUSINESS or not c.get('deviation'):
            continue
        d = c['deviation']
        x = d.get('vs_base_pp') if 'vs_base_pp' in d else d.get('vs_base_rel')
        lim = .005 if 'vs_base_pp' in d else .01
        if x is not None:
            s.append(1 if x > lim else -1 if x < -lim else 0)
    if not s:
        return None
    t = sum(s)
    return 'up' if t > 0 else 'down' if t < 0 else 'flat'


def _fmt(v, unit):
    if v is None:
        return '?'
    if unit == 'ratio':
        return f'{v:.1%}'
    if unit == 'shares':
        return f'{v / 1e6:,.0f}M'
    return f'{v / 1e9:,.2f}B' if abs(v) >= 1e8 else f'{v:,.2f}'


def _compact(chk, thesis):
    """Compact changes: actual vs forecast per metric, falsifiers tripped / confirmed, alerts."""
    fals = {a['id']: a.get('falsifier') for a in thesis.get('assumptions') or []}
    tripped, seen = [], set()
    for a in chk['alerts']:
        for aid in a.get('assumption_ids') or []:
            if aid not in seen:
                seen.add(aid)
                tripped.append({'assumption_id': aid, 'falsifier': fals.get(aid), 'alert': a['code'], 'period': a['period']})
    rows = [{'metric': c['metric'], 'period': c.get('period'), 'actual': c.get('actual'), 'base': c.get('base'),
             'range': c.get('expected_range'), 'position': c.get('position'), 'unit': c.get('unit')}
            for c in chk['changes']]
    return {'changes': rows, 'falsifiers_tripped': tripped, 'confirmations': list(chk['confirmations']),
            'alerts': [{'code': a['code'], 'period': a['period'], 'severity': a['severity'], 'message': a['message']}
                       for a in chk['alerts']],
            'to_check': [x for x in chk['not_checked'] if 'falsifier' in str(x.get('reason'))],
            'rereview_reasons': chk.get('rereview_reasons') or []}


def _line_he(row):
    """One simple Hebrew line: what changed, the direction of the value signal and whether the conclusion stands."""
    st = row['status']
    if st == 'new_holding':
        return 'אין תחזית מוקפאת: צריך הערכה מלאה'
    if st == 'data_unavailable':
        return f"לא נבנו נתונים חדשים ({row.get('reason_he') or row.get('reason')}); המסקנה הקודמת לא נבדקה"
    v = row.get('view') or {}
    tail = []
    if v.get('outside_band'):
        tail.append('המחיר מחוץ לטווח דובי–שורי')
    if st == 'no_new_report':
        return '; '.join([f"אין דוח חדש מאז {row['thesis_as_of']}; המסקנה בתוקף"] + tail)
    d = row['detail']
    bits = []
    for c in d['changes'][:2]:
        pos = {'within': 'בתוך הטווח', 'below': 'מתחת לטווח', 'above': 'מעל הטווח', 'beyond_bear': 'מעבר לדובי'}.get(c['position'], c['position'])
        bits.append(f"{METRIC_HE.get(c['metric'], c['metric'])} {str(c['period'])[:4]}: {_fmt(c['actual'], c['unit'])} ({pos})")
    for a in d['alerts']:
        if a['severity'] == 'high' or a['code'] not in ('price_outside_value_band',):
            bits.append(ALERT_HE.get(a['code'], a['code']))
    if d['falsifiers_tripped']:
        bits.append('נשבר מפריך: ' + ', '.join(x['assumption_id'] for x in d['falsifiers_tripped']))
    if not bits and d['confirmations']:
        bits.append('אישור: ' + d['confirmations'][0][:60])
    sig = {'up': 'העסק מעל הבסיס (לחץ לשווי גבוה יותר)', 'down': 'העסק מתחת לבסיס (לחץ לשווי נמוך יותר)',
           'flat': 'העסק כמו הבסיס'}.get(row.get('value_signal'))
    if sig:
        bits.append(sig)
    bits.append('המסקנה בתוקף' if st == 'light_update' else 'המסקנה עלולה להשתנות')
    return '; '.join(list(dict.fromkeys(bits)) + tail)


# ------------------------------------------------------------------ the update

def update(positions, ledger_path, as_of, factbook_for, price_for=None, casefile_for=None, root=None):
    """ledger_path: one ledger or a list (union by thesis_id). root: the repository root, to name the saved valuation
    folder an update starts from (research/companies/<T>/<as_of>, else runs/tickers/<T>_<as_of>).
    positions: {as_of, rows:[{t, shares, price, value, weight, avg_cost}]} (a READ-ONLY broker copy).
    factbook_for(ticker, as_of, thesis) -> FactBook, or raises (Unavailable or any error: data_unavailable with the reason).
    price_for(ticker) -> price or None (default: the position's price). casefile_for(ticker, thesis) -> the casefile that
    made the thesis or None (only to recompute the implied IRR at today's price)."""
    day(as_of)
    theses = latest_theses(ledger_path)
    stocks, skipped = stock_rows(positions)
    p_as_of = positions.get('as_of') or as_of
    out_rows = []
    for pos in stocks:
        t = pos['t']
        row = {'ticker': t, 'weight': _num(pos.get('weight')), 'shares': _num(pos.get('shares')), 'status': None}
        pv = _num(price_for(t)) if price_for else None
        pv = pv if pv else _num(pos.get('price'))
        th = theses.get(t)
        if th is None:
            row.update(status='new_holding', action='full_valuation',
                       reason='needs a full valuation: fast ticker procedure (no frozen thesis in the ledger)')
            out_rows.append(row)
            continue
        row.update(thesis_id=th['thesis_id'], thesis_as_of=th['as_of'], thesis_made_at=th['_made_at'], verdict=th.get('verdict'),
                   currency=th.get('currency'))
        thesis = {k: v for k, v in th.items() if k != '_made_at'}
        if casefile_for:
            try:
                cf = casefile_for(t, thesis)
                if cf:
                    rebuilt = M.thesis_from_casefile(cf, clock=lambda: th['created_at'])
                    if rebuilt['thesis_id'] == th['thesis_id']:
                        thesis['irr_basis'] = rebuilt['irr_basis']
            except (ValueError, KeyError, TypeError, OSError):
                pass
        thesis.setdefault('irr_basis', {})      # the ledger record has no IRR flows: implied IRR not computable
        pd = p_as_of if day(p_as_of) >= day(th['as_of']) else as_of
        if pv and day(pd) >= day(th['as_of']):
            row['view'] = _view(thesis, pv, pd)
        try:
            if day(as_of) < day(th['as_of']):
                raise Unavailable(f"as_of {as_of} is before the thesis cutoff {th['as_of']}")
            fb = factbook_for(t, as_of, thesis)
            if not fb:
                raise Unavailable('no FactBook')
            chk = M.check(thesis, fb, price={'value': pv, 'as_of': pd} if pv and day(pd) >= day(th['as_of']) else None)
        except Exception as e:      # any failure to get or read the data is a visible gap, never fatal for the others
            row.update(status='data_unavailable', action='none', reason=str(e)[:300])
            out_rows.append(row)
            continue
        new = chk['new_data']
        if not new['periods'] and not new['qualitative_ids']:
            row.update(status='no_new_report', action='none',
                       notes=[r for r in chk['rereview_reasons'] if 'scheduled review' in r or 'falsifier' in r])
            out_rows.append(row)
            continue
        d = _compact(chk, thesis)
        high = [a for a in d['alerts'] if a['severity'] == 'high']
        full = bool(high or d['falsifiers_tripped'])
        row.update(status='full_rerun_needed' if full else 'light_update', action='full_rerun' if full else 'light_update',
                   new_periods=new['periods'], detail=d, value_signal=_value_signal(chk['changes']),
                   conclusion='may_change' if full else 'stands')
        if root is not None:
            row['start_from'] = saved_folder(root, t, th['as_of'])
        out_rows.append(row)
    held = {r['ticker'] for r in out_rows}
    removed = [{'ticker': t, 'thesis_id': th['thesis_id'], 'thesis_as_of': th['as_of'], 'note': 'not in the positions: monitoring stops'}
               for t, th in sorted(theses.items()) if t not in held]
    for r in out_rows:
        r['line_he'] = _line_he(r)
    counts = {s: sum(r['status'] == s for r in out_rows) for s in STATUSES}
    return {'holdings_update_version': 1, 'as_of': as_of, 'positions_as_of': positions.get('as_of'),
            'positions_source': positions.get('source'),
            'ledgers': [ledger_path] if isinstance(ledger_path, str) else list(ledger_path), 'rows': out_rows, 'removed': removed,
            'skipped_non_stock': [r['t'] for r in skipped], 'counts': counts,
            'full_rerun': [r['ticker'] for r in out_rows if r['status'] in ('new_holding', 'full_rerun_needed')],
            'meaning': 'Frozen theses compared with filings after their cutoff (monitor.check). No revaluation and no orders: '
                       'light_update keeps the frozen values; full_rerun / new_holding go to the fast ticker procedure.'}


# ------------------------------------------------------------------ Hebrew table

def _pct(v):
    return f'{v:.0%}' if v is not None else '—'


def render_he(res):
    head = ('| מניה | משקל | מצב | מה השתנה | שווי בסיסי מול מחיר | תשואה צפויה במחיר של היום | פעולה |\n'
            '|---|---|---|---|---|---|---|')
    lines = [f"# עדכון רבעוני לתיק ({res['as_of']})", '', head]
    for r in res['rows']:
        v = r.get('view') or {}
        if v.get('base_value_today'):
            disc = v.get('discount_to_base')
            rel = f"{abs(disc):.0%} {'מתחת לשווי' if disc >= 0 else 'מעל השווי'}" if disc is not None else ''
            val = f"{v['base_value_today']:,.0f} מול {v['price']:,.2f} ({rel})"
        else:
            val = '—'
        er = v.get('expected_return') or {}
        ret = (f"{er['value']:.1%} לשנה" + (' (הערכה גסה)' if er.get('kind') == 'proxy' else '')) if er.get('value') is not None else '—'
        lines.append(f"| {r['ticker']} | {_pct(r.get('weight'))} | {STATUS_HE[r['status']]} | {r['line_he']} | {val} | {ret} | "
                     f"{ACTION_HE[r['status']]} |")
    c = res['counts']
    lines += ['', f"- הרצה מלאה: {', '.join(res['full_rerun']) or 'אף מניה'}; עדכון קל: {c['light_update']}; "
                  f"בלי דוח חדש: {c['no_new_report']}; חסרים נתונים: {c['data_unavailable']}."]
    if res['removed']:
        lines.append(f"- לא בתיק, המעקב נעצר: {', '.join(r['ticker'] for r in res['removed'])}.")
    lines.append('- השווי הוא התרחיש הבסיסי המוקפא, לא תחזית מחיר; עדכון קל לא משנה אותו. אין כאן הוראות קנייה או מכירה.')
    return '\n'.join(lines) + '\n'


# ------------------------------------------------------------------ default data access (CLI)

def _runs(root, ticker):
    return sorted(glob.glob(os.path.join(root, 'runs', 'tickers', f'{ticker}_*')))


def find_cik(root, ticker):
    """CIK from the newest earlier run (company.json / identity.json), else a saved SEC ticker map. None when unknown."""
    for run in reversed(_runs(root, ticker)):
        for p in (os.path.join(run, 'companies', ticker, 'company.json'), os.path.join(run, 'identity.json')):
            try:
                with open(p, encoding='utf-8') as f:
                    cik = json.load(f).get('cik')
            except (OSError, ValueError, AttributeError):
                continue
            if str(cik or '').isdigit():
                return int(cik)
    for p in sorted(glob.glob(os.path.join(root, 'runs', '*', 'raw', 'sec__company_tickers_exchange.json')), reverse=True):
        try:
            with open(p, encoding='utf-8') as f:
                m = json.load(f)
        except (OSError, ValueError):
            continue
        for row in (dict(zip(m.get('fields') or [], x)) for x in m.get('data') or []):
            if str(row.get('ticker', '')).upper() in (ticker, ticker.replace('.', '-')):
                return int(row['cik'])
    return None


def sec_factbook_for(root='.', refresh=False, ua=None, cache_dir=None):
    """factbook_for(ticker, as_of, thesis) from SEC companyfacts: --refresh refetches into cache_dir (SEC_USER_AGENT
    needed); otherwise the newest saved copy (cache_dir, or a prefetch's runs/tickers/<T>_*/companies/<T>/raw)."""
    ua = os.environ.get('SEC_USER_AGENT', '') if ua is None else ua
    cache_dir = cache_dir or os.path.join(root, 'runs', 'sec_cache')

    def build(ticker, as_of, thesis):
        from .sec import fetch_companyfacts
        cik = find_cik(root, ticker)
        if cik is None:
            raise Unavailable('CIK not found (not an SEC filer, or no earlier run): needs the fast ticker procedure')
        saved = [p for p in [os.path.join(cache_dir, f'CIK{cik:010d}.json')] +
                 [os.path.join(r, 'companies', ticker, 'raw', 'sec__companyfacts.json') for r in _runs(root, ticker)]
                 if os.path.exists(p)]
        if refresh or not saved:
            if '@' not in (ua or ''):
                raise Unavailable('SEC_USER_AGENT is not set: companyfacts cannot be fetched' +
                                  ('' if saved else ' and no saved copy exists'))
            payload = fetch_companyfacts(cik, ua, cache_dir, refresh=True)
            got = datetime.now(timezone.utc).date().isoformat()
        else:
            path = max(saved, key=os.path.getmtime)
            with open(path, encoding='utf-8') as f:
                payload = json.load(f)
            got = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).date().isoformat()
        cur = thesis.get('currency') or 'USD'
        rc = F.reporting_currency(payload)
        if rc and rc != cur:
            raise Unavailable(f'companyfacts report in {rc}, the thesis in {cur}: foreign filer, needs the fast ticker procedure')
        fb = F.new({'ticker': ticker, 'currency': cur, 'cik': str(cik)}, as_of)
        F.from_sec_companyfacts(fb, payload, min(got, as_of))
        if not F.series(fb, 'revenue', 'annual'):
            raise Unavailable('no annual revenue in SEC companyfacts (foreign filer or no XBRL facts)')
        return fb
    return build


def run_casefile_for(root='.'):
    """casefile_for(ticker, thesis): the casefile that made the thesis, from the tracked store
    (research/companies/<T>/<as_of>/casefile_core.json, the extract thesis_from_casefile reads) or the run (runs/tickers/<T>_<as_of>/companies/<T>/out/casefile.json)."""
    def find(ticker, thesis):
        for p in (os.path.join(root, STORE, ticker, thesis['as_of'], 'casefile_core.json'),
                  os.path.join(root, STORE, ticker, thesis['as_of'], 'casefile.json'),      # saves before 0.14.3
                  os.path.join(root, 'runs', 'tickers', f"{ticker}_{thesis['as_of']}", 'companies', ticker, 'out', 'casefile.json')):
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    return json.load(f)
        return None
    return find


def write(res, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    jp, mp = os.path.join(out_dir, 'holdings_update.json'), os.path.join(out_dir, 'holdings_update_he.md')
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(render_he(res))
    return {'json': jp, 'markdown': mp}
