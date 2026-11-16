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

Two more row kinds (15.11.2026):
- index_fund: a fund is not a company and is never valued. It is recognised by an explicit type on the position row
  (asset_class / sec_type / secType / kind = ETF, FUND or INDEX) or, for an untyped or stock-typed row (brokers type
  ETFs as STK), by the named set INDEX_FUNDS; never by its name;
- valuation_only: no frozen thesis, but a saved fundamental picture exists: its values are shown, no filing check.

Which value is compared with today's price (`value_comparison`, with its source and date in the row): the latest saved
fundamental picture (research/technical/<T>/<date>/fundamental.json) when it is newer than the ledger thesis, else the
ledger thesis. A value is compared only when it is in the position's currency and share unit: another currency, a
stated unit that differs, or a price that differs from the source's own price by more than PRICE_SCALE_LIMIT (ADR
ratio, split, stale source) gives the reason instead of a number. The position currency is the row's `currency`, else
the file's, else USD (stated in the row as assumed). The filing check (monitor.check on the ledger thesis) is unchanged.

Expected return at today's price: the monitor's implied IRR of the frozen base-case flows when the casefile that made the
thesis is available (the ledger stores the record without its flows); otherwise a rough proxy, (5-year base value /
price)^(1/years) - 1 without distributions, labelled as such.

Engine upgrade P9 (15.11.2026):
- the valuation record: the newest dated folder of research/companies/<T>/ (valuation_record.latest_valuation: the saved
  valuation.json, else a record built from the lean or custom model files) is read before the handoff when it is at
  least as new; its dated values, both lenses and typed conditions go into value_comparison (source valuation_record).
  A record that holds only the main file of several weighted variants (NVDA's worlds) gives way to the handoff of the
  same date, whose published values are the weighted ones. Judgement the record lacks (verdict, conditions, next
  report) is read from the handoff (valuation_record.inputs_from_handoff). The handoff is read under any dated key shape
  (end_2031, end_fy2031_jan_2032, june_2032, calibrated_end_2031, ...), with its second lens;
- the filing check is labelled for what it is: monitor.check against the heavy (old) engine's frozen thesis in the
  forecast ledger (filing_check.basis = old_engine_ledger_thesis), not against the current valuation. Lean-ledger rows
  (research/ledger.jsonl, lean_freeze) are not monitor theses and are skipped;
- cannot_tell: a filer without quarterly XBRL facts (a foreign private issuer: 20-F / 6-K) whose companyfacts show no new
  period: its quarterly results arrive as a 6-K that companyfacts does not carry, so "no new report" cannot be said.
"""
import calendar
import glob
import json
import os
import re
from datetime import datetime, timezone

from . import facts as F
from . import monitor as M
from . import valuation_record as VR
from .lean_freeze import MONTHS
from .ledger import Ledger, day, parse
from .research_store import STORE, saved_folder

STATUSES = ('new_holding', 'data_unavailable', 'no_new_report', 'light_update', 'full_rerun_needed', 'index_fund',
            'valuation_only', 'cannot_tell')
STOCK_TYPES = {'STK', 'STOCK', 'COMMON', 'EQUITY'}
FUND_TYPES = {'ETF', 'FUND', 'INDEX'}
# Broad index funds recognised when the row carries no fund type (the only place this list lives).
INDEX_FUNDS = frozenset({'CSPX', 'VOO', 'SPY', 'IVV', 'VTI', 'VUAA', 'SXR8', 'QQQ', 'VWRA', 'IWDA'})
DEFAULT_POSITION_CURRENCY = 'USD'
PRICE_SCALE_LIMIT = 1.8     # today's price vs the source's own price: beyond this factor the share unit is in doubt
TECHNICAL = os.path.join('research', 'technical')
ACTION_HE = {'new_holding': 'הרצה מלאה', 'full_rerun_needed': 'הרצה מלאה', 'light_update': 'עדכון קל',
             'no_new_report': 'אין', 'data_unavailable': 'אין (חסרים נתונים)', 'index_fund': 'אין',
             'valuation_only': 'אין', 'cannot_tell': 'אין (לבדוק את ה-6-K)'}
STATUS_HE = {'new_holding': 'מניה חדשה בתיק, אין הערכה שמורה', 'data_unavailable': 'אין נתונים עדכניים',
             'no_new_report': 'אין דוח חדש', 'light_update': 'דוח חדש, בתוך התחזית',
             'full_rerun_needed': 'דוח חדש, סוטה מהתחזית', 'index_fund': 'קרן מדד',
             'valuation_only': 'יש הערכה שמורה, אין תזה מוקפאת', 'cannot_tell': 'אי אפשר לדעת אם יצא דוח'}
SOURCE_HE = {'fundamental_picture': 'הערכה שמורה', 'ledger_thesis': 'תזה מוקפאת', 'valuation_record': 'רשומת הערכה'}
VALUE_SOURCES = ('fundamental_picture', 'valuation_record')     # saved valuations newer than the old engine's thesis
OLD_ENGINE_HE = 'המנוע הישן'
LENS_PREFIX = {'calibrated_': 'calibrated'}     # dated handoff keys of the second lens; no prefix = our lens
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
        if str(r['record'].get('frozen_by') or '').startswith('lean_freeze'):
            continue                    # a lean-ledger row (research/ledger.jsonl): not a monitor thesis
        t = str(r.get('entity') or r['record'].get('ticker') or '').upper()
        if t and (t not in out or parse(r['made_at'])[0] > parse(out[t]['_made_at'])[0]):
            out[t] = dict(r['record'], _made_at=r['made_at'])
    return out


def position_rows(positions):
    """(stocks, funds, skipped) from {as_of, rows:[{t, shares, price, value, weight, ...}]}. The type is the row's
    asset_class / sec_type / secType / kind. A fund type, or a named index fund on an untyped or stock-typed row, is a
    fund; any other non-stock type is skipped."""
    stocks, funds, skipped = [], [], []
    for r in positions.get('rows') or []:
        typed = r.get('asset_class') or r.get('sec_type') or r.get('secType') or r.get('kind')
        kind = str(typed or 'STK').upper().strip()
        t = str(r.get('t') or r.get('ticker') or r.get('symbol') or '').upper().strip()
        if not t:
            continue
        if kind in FUND_TYPES:
            funds.append(dict(r, t=t, _fund_by=f'type {kind}'))
        elif kind in STOCK_TYPES and t in INDEX_FUNDS:
            funds.append(dict(r, t=t, _fund_by='named index fund (INDEX_FUNDS)'))
        else:
            (stocks if kind in STOCK_TYPES else skipped).append(dict(r, t=t))
    return stocks, funds, skipped


def stock_rows(positions):
    """(stocks, skipped): the company rows and the rows of another type; funds are in neither (see position_rows)."""
    stocks, _, skipped = position_rows(positions)
    return stocks, skipped


def _month_end(year, month):
    return f'{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}'


def _month(word):
    w = (word or '').lower()
    m = MONTHS.get(w[:3])
    return m if m and len(w) >= 3 and calendar.month_name[m].lower().startswith(w) else None


def value_key_date(key, fy_dates=None):
    """(lens, ISO date, fiscal year or None) of a dated value key of a handoff, or None for any other key.

    lens: 'ours', or 'calibrated' for the calibrated_ prefix. Shapes read: end_2031 (31 Dec), end_fy2031_jan_2032 (end
    of that month), june_2032 / jan_2028 (end of that month), end_fy2031 (the date of our lens's key of the same fiscal
    year in fy_dates, else 31 Dec). Other prefixes (weighted_, depwave_, tail_, central_world_, ...) are not a lens."""
    lens = 'ours'
    for pre, name in LENS_PREFIX.items():
        if key.startswith(pre):
            lens, key = name, key[len(pre):]
    m = re.fullmatch(r'end_(fy)?(\d{4})(?:_([a-z]+)_(\d{4}))?', key)
    if m:
        fy = int(m.group(2))
        if m.group(3) and _month(m.group(3)):
            return lens, _month_end(int(m.group(4)), _month(m.group(3))), fy
        if m.group(3):
            return None
        if m.group(1) and fy_dates and fy in fy_dates:
            return lens, fy_dates[fy], fy
        return lens, f'{fy}-12-31', fy
    m = re.fullmatch(r'([a-z]+)_(\d{4})', key)
    if m and _month(m.group(1)):
        return lens, _month_end(int(m.group(2)), _month(m.group(1))), None
    return None


def dated_handoff_values(vals):
    """{'ours': [{date, bear, base, bull}], 'calibrated': [{date, value}], 'other_keys': [...]} from a handoff's values."""
    out, fy_dates, later = {'ours': [], 'calibrated': [], 'other_keys': []}, {}, []
    for k, v in vals.items():
        if k == 'today' or k.startswith('calibrated_today'):
            continue
        got = value_key_date(k)
        if got is None:
            out['other_keys'].append(k)
            continue
        lens, d, fy = got
        if lens == 'ours' and isinstance(v, dict):
            if fy is not None:
                fy_dates[fy] = d
            out['ours'].append({'date': d, **{s: _num(v.get(s)) for s in ('bear', 'base', 'bull')}})
        else:
            later.append((k, v))
    for k, v in later:                       # calibrated keys: a bare fiscal year takes our lens's date of that year
        lens, d, _ = value_key_date(k, fy_dates)
        x = _num(v) if not isinstance(v, dict) else _num(v.get('base') if 'base' in v else v.get('value'))
        if x is not None:
            out['calibrated'].append({'date': d, 'value': x})
    out['ours'].sort(key=lambda r: r['date'])
    out['calibrated'].sort(key=lambda r: r['date'])
    return out


def _judgement(raw):
    """Verdict, typed conditions and the next report of a handoff (valuation_record.inputs_from_handoff)."""
    try:
        inp = VR.inputs_from_handoff(raw)
    except (TypeError, ValueError, AttributeError):
        return {'conditions': [], 'falsifiers': [], 'next_report': raw.get('next_report')}
    return {'conditions': inp.get('conditions') or [], 'falsifiers': inp.get('falsifiers') or [],
            'next_report': raw.get('next_report') if isinstance(raw.get('next_report'), dict) else None}


def fundamental_picture(root, ticker, as_of=None):
    """The latest saved fundamental picture of a ticker (research/technical/<T>/<date>/fundamental.json, the folder
    name is its date; with as_of, not one dated after it), reduced to the keys the table needs; None when there is
    none. The files are hand-written, so every key is optional: a missing base value is a visible gap, never a guess.
    Dated values are read under any key shape (value_key_date), with the second lens (calibrated_today and
    calibrated_<date> keys) and the typed conditions and next report."""
    if root is None:
        return None
    best = None
    for p in glob.glob(os.path.join(root, TECHNICAL, ticker, '*', 'fundamental.json')):
        d = os.path.basename(os.path.dirname(p))
        try:
            if day(d) > day(as_of or d):
                continue
        except (ValueError, TypeError):
            continue
        if best is None or d > best[0]:
            best = (d, p)
    if best is None:
        return None
    try:
        with open(best[1], encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    vals = raw.get('values') if isinstance(raw.get('values'), dict) else {}
    today = vals.get('today') if isinstance(vals.get('today'), dict) else {}
    base, basis = _num(today.get('base')), 'values.today.base'
    bear, bull = _num(today.get('bear')), _num(today.get('bull'))
    if base is None and _num(vals.get('weighted_base_today')) is not None:
        base, basis = _num(vals['weighted_base_today']), 'values.weighted_base_today (weighted over the picture\'s scenarios)'
    dated = dated_handoff_values(vals)
    five = None
    for r in dated['ours']:
        if r['base'] and (five is None or r['date'] > five['date']):
            five = dict(r)
    ret = raw.get('annual_return_pct') if isinstance(raw.get('annual_return_pct'), dict) else {}
    cal_today = vals.get('calibrated_today')
    cal_today = _num(cal_today if not isinstance(cal_today, dict) else cal_today.get('base'))
    second = None
    if cal_today is not None or dated['calibrated']:
        second = {'value_today': cal_today, 'last_year': dated['calibrated'][-1] if dated['calibrated'] else None,
                  'annual_return_pct_at_its_price': _num(ret.get('calibrated'))}
    return {'source': 'fundamental_picture', 'date': best[0], 'path': os.path.relpath(best[1], root),
            'as_of_text': raw.get('as_of'),
            'price': _num(raw.get('price')), 'currency': raw.get('currency'), 'unit': raw.get('unit') or raw.get('share_unit'),
            'share_basis': raw.get('share_basis'), 'base': base, 'base_basis': basis if base is not None else None,
            'bear': bear, 'bull': bull, 'last_year': five, 'dated': dated, 'second_lens': second,
            'buy_test_price': raw.get('buy_test_price'),
            'margin_of_safety_price': raw.get('margin_of_safety_price'), 'verdict_he': raw.get('verdict_he'),
            'return_pct_at_its_price': _num(ret.get('base')), **_judgement(raw)}


def record_picture(root, ticker, as_of=None):
    """The valuation record of the newest dated folder (valuation_record.latest_valuation over research/), in the shape
    of fundamental_picture; None when there is no folder, no record (a heavy-engine or gather-only folder), or the folder
    is dated after as_of (an older folder is not looked for: the newest one is the valuation in force)."""
    if root is None or not os.path.isdir(os.path.join(root, STORE, ticker)):
        return None
    try:
        lv = VR.latest_valuation(os.path.join(root, 'research'), ticker)
    except Exception:                   # noqa: BLE001 (a record that cannot be built is a gap, the handoff still stands)
        return None
    if not lv or not lv.get('record') or (as_of and lv['as_of'] > as_of):
        return None
    rec = lv['record']
    sc = rec.get('scenarios') or {}

    def last(rows):
        rows = [r for r in rows or [] if r.get('date')]
        return max(rows, key=lambda r: r['date']) if rows else None

    def today_of(k):
        return _num((sc.get(k) or {}).get('value_today'))
    ends = {k: last((sc.get(k) or {}).get('dated_values')) for k in ('bear', 'base', 'bull')}
    ly = None
    if ends['base']:
        ly = {'date': ends['base']['date'], **{k: _num((ends[k] or {}).get('value')) for k in ('bear', 'base', 'bull')}}
    lens = rec.get('second_lens') or {}
    diag = lens.get('diagnostics') or {}
    hur = rec.get('hurdles') or {}
    verdict = rec.get('verdict') or {}
    return {'source': 'valuation_record', 'date': lv['as_of'], 'path': os.path.relpath(lv['folder'], os.path.dirname(os.path.join(root, 'research'))),
            'record_source': lv.get('record_source'), 'kind': (rec.get('model') or {}).get('kind'),
            'price': _num((rec.get('price') or {}).get('value')), 'currency': rec.get('currency'), 'unit': None,
            'share_basis': (rec.get('share_basis') or {}).get('basis'), 'base': today_of('base'),
            'base_basis': 'record scenarios.base.value_today' if today_of('base') is not None else None,
            'bear': today_of('bear'), 'bull': today_of('bull'), 'last_year': ly,
            'second_lens': {'value_today': _num(lens.get('value_today')), 'last_year': last(lens.get('dated_values')),
                            'annual_return_pct_at_its_price': _num(lens.get('annual_return_pct')), 'band_at_its_price': lens.get('band'),
                            'not_second_opinion': bool(diag.get('inflated_multiple_warning')),
                            'reading': lens.get('diagnostics_note')} if lens else None,
            'buy_test_price': hur.get('buy_test_price'), 'margin_of_safety_price': hur.get('margin_of_safety_price'),
            'verdict_he': verdict.get('text_he') or None, 'conditions': rec.get('conditions') or [],
            'falsifiers': rec.get('falsifiers') or [],
            'next_report': rec.get('next_report') if (rec.get('next_report') or {}).get('date') or (rec.get('next_report') or {}).get('window') else None,
            'return_pct_at_its_price': _num((sc.get('base') or {}).get('annual_return_pct')),
            'variants': rec.get('variants'), 'record_warnings': rec.get('warnings') or []}


def saved_picture(root, ticker, as_of=None):
    """The saved valuation compared with today's price: the record when it is at least as new as the handoff (with the
    handoff's verdict, conditions and next report when the record has none), else the handoff. A record of the main file
    of several variants (no weighted value) gives way to a handoff of the same date, which publishes the weighted one."""
    pic = fundamental_picture(root, ticker, as_of)
    rec = record_picture(root, ticker, as_of)
    if rec is None or rec['base'] is None:
        return pic
    if pic is not None and pic['date'] > rec['date']:
        return pic
    if pic is not None and rec.get('variants') and pic['date'] == rec['date'] and pic['base'] is not None:
        return dict(pic, record_note=f"the record of {rec['date']} holds the main file of {len(rec['variants'])} variant file(s) "
                                     'without a weighted value: the handoff\'s published values are shown')
    if pic is not None:
        if not rec['verdict_he']:
            rec['verdict_he'] = pic.get('verdict_he')
        if not (rec['conditions'] or rec['falsifiers']):
            rec['conditions'], rec['falsifiers'] = pic.get('conditions') or [], pic.get('falsifiers') or []
        if not rec['next_report']:
            rec['next_report'] = pic.get('next_report')
        rec['judgement_from'] = pic['path']
    return rec


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


def newer_saved_run(root, ticker, after):
    """The newest dated valuation folder of a ticker (research/companies/<T>/<date>, runs/tickers/<T>_<date>) later
    than `after`, or None: work this table does not read, so the value shown may be older than the latest one."""
    if root is None:
        return None
    found = [(os.path.basename(p), p) for p in glob.glob(os.path.join(root, STORE, ticker, '*')) if os.path.isdir(p)]
    found += [(os.path.basename(p)[len(ticker) + 1:], p) for p in glob.glob(os.path.join(root, 'runs', 'tickers', f'{ticker}_*'))
              if os.path.isdir(p)]
    dated = [(d, p) for d, p in found if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d) and d > after]
    if not dated:
        return None
    d, p = max(dated)
    return {'date': d, 'path': os.path.relpath(p, root)}


def _mismatch(pos_cur, pos_unit, pv, src_cur, src_unit, src_price, what):
    """Why a source's values cannot be compared with the position's price, or None. what: the source in words."""
    if src_cur and str(src_cur).upper() != pos_cur:
        return {'code': 'currency', 'reason': f'{what} is in {str(src_cur).upper()}, the position price in {pos_cur}',
                'reason_he': f'ההערכה ב-{str(src_cur).upper()} והמחיר בתיק ב-{pos_cur}'}
    if src_unit and pos_unit and str(src_unit).upper() != str(pos_unit).upper():
        return {'code': 'unit', 'reason': f'{what} is per {src_unit}, the position per {pos_unit}',
                'reason_he': f'ההערכה ליחידה אחרת ({src_unit}) מהיחידה בתיק ({pos_unit})'}
    if pv and src_price and not 1 / PRICE_SCALE_LIMIT <= pv / src_price <= PRICE_SCALE_LIMIT:
        return {'code': 'price_scale',
                'reason': f"today's price {pv:,.2f} is {pv / src_price:.2f}x the price {what} was built on ({src_price:,.2f}): "
                          'another share unit (ADR ratio, split) or a stale valuation',
                'reason_he': f'המחיר היום ({pv:,.2f}) רחוק מהמחיר שעליו נבנתה ההערכה ({src_price:,.2f}): יחידת מניה אחרת, פיצול או הערכה ישנה'}
    return None


def _proxy_from(end, end_date, pv, pd):
    """(end value / price)^(1/years) - 1 to a dated end value, or None (no value, not positive, under half a year)."""
    if not (end and pv and end_date) or end <= 0:
        return None
    yrs = (day(end_date) - day(pd)).days / 365.25
    return (end / pv) ** (1 / yrs) - 1 if yrs >= .5 else None


def conditions_summary(conditions, falsifiers):
    """Counts of the typed conditions and falsifiers by state, the ones fired and the exit texts (for the table)."""
    rows = list(conditions or []) + list(falsifiers or [])
    typed = [r for r in rows if r.get('metric') and r.get('op') and r.get('threshold') is not None]
    return {'count': len(rows), 'typed': len(typed), 'untyped': len(rows) - len(typed),
            'by_state': {st: sum((r.get('state') or 'open') == st for r in rows) for st in VR.STATES},
            'hit': [r.get('text_he') or r.get('id') for r in rows if r.get('state') == 'hit'],
            'exit_texts': [r.get('text_he') for r in falsifiers or [] if r.get('text_he')]}


def _picture_comparison(pic, pv, pd):
    """The saved valuation (the record or the handoff) against today's price; the return is a rough proxy from its last
    year-end value, for our lens and for the second lens when the source has one."""
    base, ly = pic['base'], pic.get('last_year')
    ret = _proxy_from(ly and ly['base'], ly and ly['date'], pv, pd)
    lo, hi = pic.get('bear'), pic.get('bull')
    out = {'source': pic.get('source') or 'fundamental_picture', 'date': pic['date'], 'path': pic['path'], 'price': pv,
           'price_as_of': pd, 'base_value_today': base, 'base_basis': pic.get('base_basis'),
           'base_is_weighted': 'weighted' in (pic.get('base_basis') or ''), 'bear_value_today': lo, 'bull_value_today': hi,
           'discount_to_base': (1 - pv / base) if base and pv else None,
           'outside_band': bool(pv and lo is not None and hi is not None and not min(lo, hi) <= pv <= max(lo, hi)),
           'last_year': ly, 'buy_test_price': pic.get('buy_test_price'),
           'margin_of_safety_price': pic.get('margin_of_safety_price'), 'verdict_he': pic.get('verdict_he'),
           'share_basis': pic.get('share_basis'),
           'expected_return': {'value': ret, 'kind': 'proxy',
                               'meaning': '(last year-end base value of the picture / price)^(1/years) - 1, distributions '
                                          'excluded: a rough proxy'},
           'return_pct_at_its_price': pic.get('return_pct_at_its_price'), 'price_at_valuation': pic.get('price')}
    sl = pic.get('second_lens')
    if sl:
        e = sl.get('last_year') or {}
        out['second_lens'] = {'value_today': sl.get('value_today'), 'last_year': sl.get('last_year'),
                              'expected_return': {'value': _proxy_from(e.get('value'), e.get('date'), pv, pd), 'kind': 'proxy'},
                              'annual_return_pct_at_its_price': sl.get('annual_return_pct_at_its_price'),
                              'not_second_opinion': sl.get('not_second_opinion'), 'reading': sl.get('reading')}
    if pic.get('conditions') or pic.get('falsifiers'):
        out['conditions'] = conditions_summary(pic.get('conditions'), pic.get('falsifiers'))
    if pic.get('next_report'):
        out['next_report'] = pic['next_report']
    for k in ('kind', 'record_source', 'judgement_from', 'record_note'):
        if pic.get(k):
            out[k] = pic[k]
    return out


def _thesis_comparison(th, view):
    band = (view.get('monitor_price_view') or {}).get('value_band') or {}
    return {'source': 'ledger_thesis', 'engine': 'old', 'date': th['as_of'], 'thesis_id': th['thesis_id'], 'price': view['price'],
            'price_as_of': view['price_as_of'], 'base_value_today': view['base_value_today'],
            'bear_value_today': band.get('bear'), 'bull_value_today': band.get('bull'),
            'discount_to_base': view['discount_to_base'], 'outside_band': view.get('outside_band'),
            'expected_return': view['expected_return'], 'verdict': th.get('verdict'),
            'price_at_valuation': (th.get('price_at_analysis') or {}).get('value')}


def _no_comparison(skipped):
    """No value is printed: the reasons, newest source first."""
    return {'source': None, 'base_value_today': None, 'skipped_sources': skipped,
            'reason': '; '.join(x['reason'] for x in skipped) or 'no price for the position',
            'reason_he': '; '.join(x['reason_he'] for x in skipped) or 'אין מחיר לפוזיציה'}


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
    if st == 'index_fund':
        return 'קרן מדד, לא חברה: לא מוערכת'
    vc = row.get('value_comparison') or {}
    newer = vc.get('source') in VALUE_SOURCES and not vc.get('older_than_thesis')
    old = f"בדיקת הדוחות מול התזה של {OLD_ENGINE_HE} מ-{row.get('thesis_as_of')}, לא מול ההערכה הנוכחית" if newer else None
    if st == 'valuation_only':
        return f"הערכה שמורה מ-{vc.get('date') or row.get('picture_date')}; אין תזה מוקפאת, לכן אין בדיקת דוחות"
    if st == 'data_unavailable':
        return f"לא נבנו נתונים חדשים ({row.get('reason_he') or row.get('reason')}); המסקנה הקודמת לא נבדקה"
    tail = []
    if vc.get('outside_band'):
        tail.append('המחיר מחוץ לטווח דובי–שורי')
    if newer:
        tail.append(f"יש הערכה חדשה יותר מ-{vc['date']} (ראו למטה)")
    elif vc.get('older_than_thesis'):
        tail.append(f"השווי בטבלה מהערכה שמורה מ-{vc['date']}, ישנה מהתזה המוקפאת (את התזה אי אפשר להשוות למחיר)")
    if row.get('newer_saved_run'):
        tail.append(f"יש הרצה חדשה יותר מ-{row['newer_saved_run']['date']} שלא נקראת כאן: "
                    + ('הערך בטבלה ישן' if vc.get('source') else 'לבדוק אותה'))
    if st == 'no_new_report':
        return '; '.join([f"אין דוח חדש ב-SEC מאז {row['thesis_as_of']}" + ('' if newer else '; המסקנה בתוקף')] + tail
                         + ([old] if old else []))
    if st == 'cannot_tell':
        return '; '.join([f"חברה זרה בלי דוחות רבעוניים ב-XBRL (20-F / 6-K): מכאן אי אפשר לדעת אם יצא דוח מאז "
                          f"{row['thesis_as_of']}; לבדוק את ה-6-K"] + tail + ([old] if old else []))
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
    bits.append(('התזה המוקפאת עומדת' if newer else 'המסקנה בתוקף') if st == 'light_update' else 'המסקנה עלולה להשתנות')
    return '; '.join(list(dict.fromkeys(bits)) + tail + ([old] if old else []))


# ------------------------------------------------------------------ the update

def update(positions, ledger_path, as_of, factbook_for, price_for=None, casefile_for=None, root=None):
    """ledger_path: one ledger or a list (union by thesis_id). root: the repository root, to name the saved valuation
    folder an update starts from (research/companies/<T>/<as_of>, else runs/tickers/<T>_<as_of>).
    positions: {as_of, rows:[{t, shares, price, value, weight, avg_cost}]} (a READ-ONLY broker copy).
    factbook_for(ticker, as_of, thesis) -> FactBook, or raises (Unavailable or any error: data_unavailable with the reason).
    price_for(ticker) -> price or None (default: the position's price). casefile_for(ticker, thesis) -> the casefile that
    made the thesis or None (only to recompute the implied IRR at today's price).
    With root, the latest saved fundamental picture (research/technical/<T>/<date>/fundamental.json) is read too."""
    day(as_of)
    theses = latest_theses(ledger_path)
    stocks, funds, skipped = position_rows(positions)
    p_as_of = positions.get('as_of') or as_of
    out_rows = []
    for pos in funds:
        out_rows.append({'ticker': pos['t'], 'weight': _num(pos.get('weight')), 'shares': _num(pos.get('shares')),
                         'status': 'index_fund', 'action': 'none',
                         'reason': f"index fund, not a company: not valued (recognised by {pos['_fund_by']})"})
    for pos in stocks:
        t = pos['t']
        row = {'ticker': t, 'weight': _num(pos.get('weight')), 'shares': _num(pos.get('shares')), 'status': None}
        pv = _num(price_for(t)) if price_for else None
        pv = pv if pv else _num(pos.get('price'))
        cur_given = pos.get('currency') or positions.get('currency')
        pos_cur = str(cur_given or DEFAULT_POSITION_CURRENCY).upper()
        pos_unit = pos.get('unit') or pos.get('share_unit')
        row.update(position_currency=pos_cur, position_currency_assumed=not cur_given)
        pic = saved_picture(root, t, as_of)
        skipped_src, pic_cmp = [], None
        if pic is not None:
            row['picture_date'] = pic['date']
            what = ('the valuation record' if pic.get('source') == 'valuation_record' else 'the saved fundamental picture') + f" of {pic['date']}"
            bad = _mismatch(pos_cur, pos_unit, pv, pic.get('currency'), pic.get('unit'), pic.get('price'), what)
            if bad is None and pic['base'] is None:
                bad = {'code': 'no_base_value', 'reason': f"{what} has no base value today "
                                                          'in its standard keys', 'reason_he': f"בהערכה מ-{pic['date']} אין שווי בסיסי להיום במבנה הרגיל"}
            if bad is None and pv:
                pic_cmp = _picture_comparison(pic, pv, p_as_of if day(p_as_of) >= day(pic['date']) else as_of)
            elif bad is not None:
                skipped_src.append(dict(bad, source=pic.get('source') or 'fundamental_picture', date=pic['date']))
        th = theses.get(t)
        if th is None:
            if pic is None:
                row.update(status='new_holding', action='full_valuation',
                           reason='needs a full valuation: fast ticker procedure (no frozen thesis in the ledger)')
            else:
                row.update(status='valuation_only', action='none',
                           reason='a saved fundamental picture exists but no frozen thesis in the ledger: values shown, no filing check',
                           value_comparison=pic_cmp or _no_comparison(skipped_src))
            out_rows.append(row)
            continue
        row.update(thesis_id=th['thesis_id'], thesis_as_of=th['as_of'], thesis_made_at=th['_made_at'], verdict=th.get('verdict'),
                   currency=th.get('currency'),
                   filing_check={'basis': 'old_engine_ledger_thesis', 'thesis_id': th['thesis_id'], 'thesis_as_of': th['as_of'],
                                 'meaning': 'monitor.check of SEC companyfacts against the heavy (old) engine\'s frozen thesis in the '
                                            'forecast ledger, not against the current valuation',
                                 'label_he': f"בדיקת הדוחות מול התזה המוקפאת של {OLD_ENGINE_HE} מ-{th['as_of']}"})
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
        priced = bool(pv and day(pd) >= day(th['as_of']))
        bad = _mismatch(pos_cur, pos_unit, pv, th.get('currency'), th.get('unit') or (th.get('price_at_analysis') or {}).get('unit'),
                        _num((th.get('price_at_analysis') or {}).get('value')), f"the frozen thesis of {th['as_of']}")
        if bad is not None:
            skipped_src.append(dict(bad, source='ledger_thesis', date=th['as_of']))
            priced = False              # the thesis price view would compare two different units
        if priced:
            row['view'] = _view(thesis, pv, pd)
        if pic_cmp is not None and pic['date'] > th['as_of']:
            row['value_comparison'] = pic_cmp
        elif priced and row['view']['base_value_today']:
            row['value_comparison'] = _thesis_comparison(th, row['view'])
        elif pic_cmp is not None:       # an older picture, but the only comparable value
            row['value_comparison'] = dict(pic_cmp, older_than_thesis=True)
        else:
            row['value_comparison'] = _no_comparison(skipped_src)
        if skipped_src and row['value_comparison'].get('source'):
            row['value_comparison']['skipped_sources'] = skipped_src
        later = newer_saved_run(root, t, row['value_comparison'].get('date') or th['as_of'])
        if later:
            row['newer_saved_run'] = later
        try:
            if day(as_of) < day(th['as_of']):
                raise Unavailable(f"as_of {as_of} is before the thesis cutoff {th['as_of']}")
            fb = factbook_for(t, as_of, thesis)
            if not fb:
                raise Unavailable('no FactBook')
            chk = M.check(thesis, fb, price={'value': pv, 'as_of': pd} if priced else None)
        except Exception as e:      # any failure to get or read the data is a visible gap, never fatal for the others
            row.update(status='data_unavailable', action='none', reason=str(e)[:300])
            out_rows.append(row)
            continue
        new = chk['new_data']
        if not new['periods'] and not new['qualitative_ids'] and (fb.get('company') or {}).get('quarterly_xbrl') is False:
            row.update(status='cannot_tell', action='none',
                       reason='no new period in companyfacts, but this filer has no quarterly XBRL facts (a foreign private '
                              'issuer: 20-F / 6-K): its quarterly results arrive as a 6-K that companyfacts does not carry, so '
                              'whether a report came out cannot be told here; check the 6-K filings')
            out_rows.append(row)
            continue
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
            'skipped_non_stock': [r['t'] for r in skipped], 'index_funds': [r['t'] for r in funds], 'counts': counts,
            'full_rerun': [r['ticker'] for r in out_rows if r['status'] in ('new_holding', 'full_rerun_needed')],
            'meaning': 'Frozen theses compared with filings after their cutoff (monitor.check). No revaluation and no orders: '
                       'light_update keeps the frozen values; full_rerun / new_holding go to the fast ticker procedure. '
                       'value_comparison names the source and date of the value shown against today\'s price (the newer '
                       'saved valuation: the valuation record or the fundamental picture, else the ledger thesis) or the '
                       'reason no value is comparable; index funds are listed and never valued. The filing check is '
                       'against the old (heavy) engine\'s frozen thesis (filing_check); cannot_tell: a filer without '
                       'quarterly XBRL facts, whose 6-K reports companyfacts does not show.'}


# ------------------------------------------------------------------ Hebrew table

def _pct(v):
    return f'{v:.0%}' if v is not None else '—'


def _money(v):
    if isinstance(v, dict):
        return ', '.join(f'{k}: {_money(x)}' for k, x in v.items() if x is not None and k != 'note_he') or '—'
    x = _num(v)
    return '—' if x is None else f'{x:,.2f}' if abs(x) < 100 else f'{x:,.0f}'


def _value_cell(r):
    vc = r.get('value_comparison')
    if vc is None:                                   # rows of older results (before value_comparison)
        v = r.get('view') or {}
        vc = dict(v, source='ledger_thesis', date=r.get('thesis_as_of')) if v.get('base_value_today') else {}
    if vc.get('base_value_today'):
        disc = vc.get('discount_to_base')
        rel = f" ({abs(disc):.0%} {'מתחת לשווי' if disc >= 0 else 'מעל השווי'})" if disc is not None else ''
        w = ' (משוקלל בין תרחישים)' if vc.get('base_is_weighted') else ''
        eng = f' ({OLD_ENGINE_HE})' if vc['source'] == 'ledger_thesis' else ''
        return f"{_money(vc['base_value_today'])}{w} מול {vc['price']:,.2f}{rel}", f"{SOURCE_HE[vc['source']]} {vc['date']}{eng}", vc
    if vc.get('reason_he'):
        return f"אין השוואה: {vc['reason_he']}", '—', vc
    return '—', '—', vc


def render_he(res):
    head = ('| מניה | משקל | מצב | מה השתנה | שווי בסיסי מול מחיר | מקור השווי ותאריך | תשואה צפויה במחיר של היום | פעולה |\n'
            '|---|---|---|---|---|---|---|---|')
    lines = [f"# עדכון רבעוני לתיק ({res['as_of']})", '', head]
    notes = []
    for r in res['rows']:
        val, src, vc = _value_cell(r)
        er = vc.get('expected_return') or {}
        ret = (f"{er['value']:.1%} לשנה" + (' (הערכה גסה)' if er.get('kind') == 'proxy' else '')) if er.get('value') is not None else '—'
        lines.append(f"| {r['ticker']} | {_pct(r.get('weight'))} | {STATUS_HE[r['status']]} | {r['line_he']} | {val} | {src} | {ret} | "
                     f"{ACTION_HE[r['status']]} |")
        if vc.get('source') in VALUE_SOURCES:
            bits = [f"דובי {_money(vc.get('bear_value_today'))} / בסיסי {_money(vc['base_value_today'])} / שורי {_money(vc.get('bull_value_today'))}"]
            sl = vc.get('second_lens') or {}
            sr = (sl.get('expected_return') or {}).get('value')
            if sr is not None:
                bits.append(f"עדשה שנייה {sr:.1%} לשנה במחיר של היום (הערכה גסה)"
                            + (' — לא דעה שנייה כאן (המכפיל המוחזק מנופח)' if sl.get('not_second_opinion') else ''))
            cs = vc.get('conditions')
            if cs:
                bits.append(f"תנאים ומפריכים: {cs['count']} ({cs['untyped']} בלי מספר לבדיקה)"
                            + (f"; הופעל: {', '.join(cs['hit'])}" if cs['hit'] else ''))
            if vc.get('buy_test_price') is not None:
                bits.append(f"מחיר מבחן הקנייה {_money(vc['buy_test_price'])}")
            if vc.get('margin_of_safety_price') is not None:
                bits.append(f"מחיר כניסה עם מרווח ביטחון {_money(vc['margin_of_safety_price'])}")
            if vc.get('verdict_he'):
                bits.append(f"מסקנה: {vc['verdict_he']}")
            notes.append(f"- {r['ticker']} ({vc['date']}): " + '; '.join(bits) + '.')
    c = res['counts']
    lines += ['', f"- הרצה מלאה: {', '.join(res['full_rerun']) or 'אף מניה'}; עדכון קל: {c['light_update']}; "
                  f"בלי דוח חדש: {c['no_new_report']}; חסרים נתונים: {c['data_unavailable']}"
                  + (f"; אי אפשר לדעת (חברה זרה, 6-K): {c['cannot_tell']}" if c.get('cannot_tell') else '') + '.']
    if any(r.get('filing_check') for r in res['rows']):
        lines.append(f'- "מה השתנה" נבדק מול התזה המוקפאת של {OLD_ENGINE_HE} (הנתונים של SEC), לא מול ההערכה הנוכחית שבעמודת השווי.')
    if res.get('index_funds'):
        lines.append(f"- קרנות מדד (לא מוערכות): {', '.join(res['index_funds'])}.")
    if res['removed']:
        lines.append(f"- לא בתיק, המעקב נעצר: {', '.join(r['ticker'] for r in res['removed'])}.")
    lines.append('- השווי הוא התרחיש הבסיסי מהמקור שבעמודה (הערכה שמורה חדשה, אחרת התזה המוקפאת), לא תחזית מחיר; '
                 'עדכון קל לא משנה אותו. אין כאן הוראות קנייה או מכירה.')
    if notes:
        lines += ['', '## פירוט ההערכות השמורות שבטבלה', ''] + notes
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


def xbrl_forms(payload):
    """The filing forms the companyfacts rows come from ({'10-K', '10-Q', ...}; a foreign private issuer shows 20-F or
    40-F and no 10-Q, because its quarterly results are 6-K text without XBRL facts)."""
    return {str(r.get('form')) for tax in (payload.get('facts') or {}).values() for v in tax.values()
            for rs in (v.get('units') or {}).values() for r in rs if r.get('form')}


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
        forms = xbrl_forms(payload)
        fb['company'].update(xbrl_forms=sorted(forms), quarterly_xbrl=any(f.startswith('10-Q') for f in forms))
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
