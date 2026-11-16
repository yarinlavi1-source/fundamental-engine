"""Portfolio health from the saved valuations: the owner's rules computed, not rebuilt by hand (15.11.2026, upgrade P9).

Why: the health check of 15.11.2026 was rebuilt by hand: the second lens read as 'not reliable' or 'split' from memory,
the bands and caps added up in prose, and the age and next report of each valuation looked up one by one. This module
reads the saved valuations (valuation_record.latest_valuation over research/), the READ-ONLY positions copy and the
private rules file (runs/portfolio_rules.json), and states every rule result per holding:

- reprice: a holding with lean params is rerun with lean_model at the position's price and date (lean_model.reprice;
  the dates move from base_year_end, derived in memory from as_of + days_to_base_year_end when the file has none; named
  variant worlds (worlds.json) are run and weighted the same way). A custom model (META's three lanes, a bank or a
  segment model) is not rerun: its saved horizon values are read against the position's price and date. A frozen value
  is never revalued: a reprice changes the price and the dates only;
- band per lens (buy >= 12%, hold 6-12%, lagging 3-6%, sell < 3% of the base 5-year yearly return) and whether the two
  lenses agree; calibrated_diagnostics decides when the second lens is not a second opinion (an inflated held multiple):
  then no agreement is claimed and our lens is read alone, said so;
- the buy-test legs (base >= 12% in both lenses, bull >= 17%, bear >= -10%), the highest passing price and the rerun price;
- the index share of all the money (index funds plus the study fund) against 60-70%, the stock part against 30-40%;
- sizes against 15% (no new money above it) and 20% (back to 15%), 5% for a small bet, and the core minimum;
- linked exposure: the summed weight of the holdings that share a role tag (e.g. the AI capex chain);
- the age of each valuation, its next report (passed or not) and the state of its typed conditions and falsifiers.

Rows state rule results in simple Hebrew ("מעל התקרה של 15%"), never orders. The two 05.11 rules differ on a core name
in the lagging band (cap at 6% of wealth, or hold with no new money): the rules file's lagging_core_rule switch picks one
(default: hold, no new money, re-check after the next report, the later, revised sleeve rule) and the output names the
reading used. Output goes only under runs/ (private): health() never writes, write() refuses a folder outside runs/.

due(): which theses are due: reports in the next N days (from the records', the handoffs' and the lean ledger's next
report), reports that passed without a valuation update, held names whose valuation is older than their last report,
and open conditions. No network: a filing index is read only when the caller passes one (scripts/due.py --refresh, or
its cached copy).
"""
import copy
import glob
import json
import os
import shutil
import tempfile
from datetime import date

from . import holdings_watch as HW
from . import lean_freeze as LF
from . import valuation_record as VR

RULES_PATH = os.path.join('runs', 'portfolio_rules.json')
DRAFT = 'draft: owner to confirm'
# The owner's rules as numbers (CLAUDE.md, 05.11.2026); the rules file overrides each key it holds.
DEFAULT_RULES = {
    'bands_pct': {'buy': 12.0, 'hold': 6.0, 'lagging': 3.0},
    'buy_test_pct': {'base_both_lenses': 12.0, 'bull': 17.0, 'bear': -10.0},
    'index_target': {'min': 0.60, 'max': 0.70},
    'stock_part': {'min': 0.30, 'max': 0.40},
    'caps': {'no_new_money_above': 0.15, 'trim_above': 0.20, 'trim_to': 0.15, 'small_bet_max': 0.05, 'core_min': 0.06,
             'core_lagging_cap': 0.06},
    'lagging_core_rule': {'value': 'hold_no_new_money'},
    'noise_pts': 3.0,
    'price_scale_limit': HW.PRICE_SCALE_LIMIT,
    'index_funds': {'tickers': []},
    'study_fund': {'amount': 0.0, 'currency': 'USD'},
    'holdings': {},
    'linked_exposure': {},
}
LAGGING_RULES = {
    'hold_no_new_money': {'he': 'מחזיקים בלי כסף חדש, ובודקים שוב אחרי הדוח הבא',
                          'source': 'CLAUDE.md, the sleeve rule of 05.11.2026 as revised the same day (core held through the '
                                    'hold and lagging bands)'},
    'cap_6pct': {'he': 'מניית ליבה בתחום הזה מוגבלת ל-6% מכל הכסף, ובודקים שוב אחרי הדוח הבא',
                 'source': 'CLAUDE.md, the portfolio rule of 05.11.2026 (lagging 3-6%: core quality names cap at 6% of wealth)'},
}
BAND_HE = {'buy': 'קנייה', 'hold': 'החזקה', 'lagging': 'מפגר', 'sell': 'מכירה'}
ROLE_HE = {'core': 'ליבה', 'small_bet': 'הימור קטן', 'leaving': 'יוצאת', 'index': 'מדד', None: 'לא הוגדר'}
LEG_HE = {'base_ours': 'בסיסי שלנו', 'base_calibrated': 'בסיסי בעדשה השנייה', 'bull': 'שורי', 'bear': 'דובי'}
RULE_KEYS = tuple(DEFAULT_RULES)


# ------------------------------------------------------------------ inputs

def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _val(x):
    """A rules value that may be wrapped as {'value': ..., 'draft': ...}."""
    return x.get('value') if isinstance(x, dict) and 'value' in x else x


def load_rules(path=None, obj=None):
    """The rules: DEFAULT_RULES with every key of the file (or obj) laid over it; 'drafts' lists the fields still marked
    draft; 'problems' lists what cannot be used (an unknown lagging reading, a band order that is not buy > hold > lagging)."""
    raw = obj if obj is not None else {}
    if obj is None and path and os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
    rules = copy.deepcopy(DEFAULT_RULES)
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(rules.get(k), dict) and k not in ('holdings', 'linked_exposure'):
            rules[k] = dict(rules[k], **v)
        else:
            rules[k] = v
    problems, drafts = [], []

    def walk(x, where):
        if isinstance(x, dict):
            if any(str(v).startswith('draft') for k, v in x.items() if k == 'draft'):
                drafts.append(where or '.')
            for k, v in x.items():
                walk(v, f'{where}.{k}' if where else k)
    walk(raw, '')
    b = {k: _num(_val(v)) for k, v in rules['bands_pct'].items() if k in ('buy', 'hold', 'lagging')}
    if None in b.values() or not b['buy'] > b['hold'] > b['lagging']:
        problems.append(f'bands_pct must be numbers with buy > hold > lagging (got {rules["bands_pct"]})')
    reading = _val(rules['lagging_core_rule'])
    if reading not in LAGGING_RULES:
        problems.append(f'lagging_core_rule {reading!r} is not one of {sorted(LAGGING_RULES)}')
    rules['_bands'] = b
    rules['_lagging_reading'] = reading if reading in LAGGING_RULES else 'hold_no_new_money'
    rules['_drafts'] = sorted(set(drafts))
    rules['_problems'] = problems
    rules['_source'] = path if obj is None else 'object'
    return rules


def band(ret, bands):
    if ret is None:
        return None
    if ret >= bands['buy']:
        return 'buy'
    if ret >= bands['hold']:
        return 'hold'
    if ret >= bands['lagging']:
        return 'lagging'
    return 'sell'


_LM = {}


def lean_module():
    """scripts/lean_model.py of this checkout as a module (cached)."""
    if 'm' not in _LM:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'lean_model.py')
        spec = importlib.util.spec_from_file_location('lean_model_ph', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LM['m'] = mod
    return _LM['m']


# ------------------------------------------------------------------ reprice

def with_base_year_end(p):
    """(params, derived): the params with base_year_end = as_of + days_to_base_year_end when the file has none (in memory
    only, so a reprice can move the dates; the saved file is never changed)."""
    if p.get('base_year_end') or not (p.get('as_of') and isinstance(p.get('days_to_base_year_end'), (int, float))):
        return p, False
    q = dict(p)
    q['base_year_end'] = date.fromordinal(date.fromisoformat(str(p['as_of'])[:10]).toordinal()
                                          + int(p['days_to_base_year_end'])).isoformat()
    return q, True


def _lens_from(res, worlds):
    """(returns {bear, base, bull, base_calibrated}, buy_test, two_lens bands from the model, diagnostics) of a run."""
    diag = res.get('calibrated_diagnostics') or {}
    if worlds:
        w = res['worlds']['weighted']
        r = dict(w['annual_return_pct'])
        return r, w['buy_test'], diag
    r = {k: res['scenarios'][k]['annual_return_pct'] for k in res['scenarios']}
    r['base_calibrated'] = res['two_lens']['calibrated'].get('annual_return_pct')
    return r, res['buy_test'], diag


def reprice_lean(folder, spec, price, when, lm=None):
    """Rerun a lean valuation folder at another price and date. -> dict (returns per scenario and lens, buy test,
    diagnostics, dates, worlds) or raises ValueError with the reason."""
    lm = lm or lean_module()
    p = VR.load(os.path.join(folder, spec['params']))
    q, derived = with_base_year_end(p)
    as_of = max(when, str(p.get('as_of'))[:10])
    wpath = os.path.join(folder, 'worlds.json')
    tmp, worlds = None, None
    crule = (VR.calibrated_rule(folder)[0]
             if spec['params'] == 'lean_params.json' and not os.path.isfile(wpath) else None)
    try:
        if crule is not None:                        # the published second lens is the one with the rule (FIVE)
            q = lm.apply_calibrated_rule(q, crule)
        rq = lm.reprice(q, price=price, as_of=as_of)
        if os.path.isfile(wpath) and spec['params'] == 'lean_params.json':
            ws = VR.load(wpath)
            tmp = tempfile.mkdtemp(prefix='ph_worlds_')
            entries = []
            for e in ws.get('worlds') or []:
                wp, _ = with_base_year_end(VR.load(os.path.join(folder, e['file'])))
                with open(os.path.join(tmp, os.path.basename(e['file'])), 'w', encoding='utf-8') as f:
                    json.dump(wp, f)
                entries.append(dict(e, file=os.path.basename(e['file'])))
            worlds = {'worlds': entries}
            res = lm.run(rq, worlds=worlds, base_dir=tmp, source='worlds.json (repriced in memory)')
        else:
            res = lm.run(rq)
    except lm.ParamsError as e:
        raise ValueError('; '.join(e.problems)) from e
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    rets, bt, diag = _lens_from(res, worlds)
    inp = res.get('inputs') or {}
    return {'method': 'lean_model rerun at the position price and date' + (' over the weighted worlds' if worlds else ''),
            'price': price, 'as_of_used': rq.get('as_of'), 'dates_moved': (rq.get('repriced') or {}).get('dates_moved'),
            'base_year_end_derived': derived, 'years_to_horizon': inp.get('years_to_horizon') or rq.get('years_to_horizon'),
            'returns': rets, 'buy_test': bt, 'diagnostics': diag,
            'worlds': (res['worlds']['weights'] if worlds else None),
            'main_file_returns': ({k: res['scenarios'][k]['annual_return_pct'] for k in res['scenarios']}
                                  | {'base_calibrated': res['two_lens']['calibrated'].get('annual_return_pct')}) if worlds else None,
            'warnings': res.get('warnings') or []}


def _ret(end, price, years):
    if end is None or price is None or years is None or years <= 0 or end <= 0:
        return None
    return round(((end / price) ** (1 / years) - 1) * 100, 1)


def reprice_custom(record, price, when):
    """A custom model's saved returns read at another price and date (no rerun). The model's own end value, distributions
    included (META's three lanes add the cumulative dividend), is recovered from its return at its own price:
    end = price_then x (1 + return)^years_then; then return_now = (end / price_now)^(1 / years_now) - 1 with
    years_now = years_then - the days between the two dates / 365.25. -> the same shape as reprice_lean."""
    sc = record.get('scenarios') or {}
    lens = record.get('second_lens') or {}
    vprice = _num((record.get('price') or {}).get('value'))
    years_then = _num((record.get('hurdles') or {}).get('years_to_horizon'))
    if not (vprice and years_then):
        raise ValueError('the record has no price or no years to the horizon: its returns cannot be read at another price')
    years = round(years_then - (date.fromisoformat(when) - date.fromisoformat(record['as_of'])).days / 365.25, 4)
    rets_then = {k: _num((sc.get(k) or {}).get('annual_return_pct')) for k in ('bear', 'base', 'bull')}
    rets_then['base_calibrated'] = _num(lens.get('annual_return_pct'))
    if rets_then['base'] is None:
        raise ValueError('the record has no base return')
    ends = {k: (vprice * (1 + r / 100) ** years_then if r is not None else None) for k, r in rets_then.items()}
    return {'method': 'custom model: its saved returns read at the position price and date (no rerun; the end value with '
                      'distributions is recovered from the model\'s return at its own price; the second lens keeps the '
                      'multiple of the valuation\'s price)',
            'price': price, 'as_of_used': when, 'dates_moved': True, 'base_year_end_derived': False,
            'years_to_horizon': years, 'horizon_date': (record.get('hurdles') or {}).get('horizon_date'),
            'returns': {k: _ret(e, price, years) for k, e in ends.items()}, 'returns_at_valuation': rets_then,
            'end_values': ends, 'buy_test': None, 'diagnostics': lens.get('diagnostics') or {}, 'worlds': None,
            'main_file_returns': None, 'warnings': []}


def buy_test_legs(rp, rules):
    """The four legs at the reprice: return, threshold, pass, highest passing price (the model's, when the thresholds are
    the model's), and the rerun price."""
    t = rules['buy_test_pct']
    thr = {'base_ours': _num(_val(t['base_both_lenses'])), 'base_calibrated': _num(_val(t['base_both_lenses'])),
           'bull': _num(_val(t['bull'])), 'bear': _num(_val(t['bear']))}
    key = {'base_ours': 'base', 'base_calibrated': 'base_calibrated', 'bull': 'bull', 'bear': 'bear'}
    bt = rp.get('buy_test') or {}
    model_legs = bt.get('legs') or {}
    legs = {}
    for leg, k in key.items():
        r = rp['returns'].get(k)
        mp = (model_legs.get(leg) or {}).get('max_price')
        if mp is None and rp.get('end_values') and rp['end_values'].get(k) and thr[leg] is not None:
            mp = round(rp['end_values'][k] / (1 + thr[leg] / 100) ** rp['years_to_horizon'], 2)
        legs[leg] = {'return_pct': r, 'threshold_pct': thr[leg], 'pass': None if r is None else r >= thr[leg], 'max_price': mp}
    complete = all(v['pass'] is not None for v in legs.values())
    priced = {k: v['max_price'] for k, v in legs.items() if v['max_price'] is not None}
    lead = min(priced, key=priced.get) if priced else None
    return {'legs': legs, 'complete': complete, 'pass': complete and all(v['pass'] for v in legs.values()),
            'max_price_passing': bt.get('max_price_passing', priced.get(lead) if lead else None),
            'binding_leg': bt.get('binding_leg', lead), 'rerun_max_price_passing': (bt.get('rerun') or {}).get('max_price_passing'),
            'no_price_passes': bt.get('no_price_passes')}


# ------------------------------------------------------------------ one holding

def next_report_for(root, ticker, lv=None):
    """The next report of the valuation in force: the saved record's, else the handoff of the newest date (the
    valuation's own date first), else the lean ledger's thesis row. -> {text, earliest, latest, confirmed, source,
    source_date} or None. Dates are read with lean_freeze.report_window ('~2027-02-22' is 3 days either side, 'late
    January 2027' the whole month)."""
    rec = (lv or {}).get('record') or {}
    if (lv or {}).get('record_source') == VR.RECORD_NAME:
        nr = rec.get('next_report') or {}
        if nr.get('date') or nr.get('window'):
            lo = nr.get('date') or nr['window']['from']
            hi = nr.get('date') or nr['window']['to']
            return {'text': nr.get('text') or lo, 'earliest': lo, 'latest': hi, 'confirmed': bool(nr.get('confirmed')),
                    'check_he': nr.get('check_he'), 'source': os.path.relpath(os.path.join(lv['folder'], VR.RECORD_NAME), root),
                    'source_date': lv['as_of']}
    hand = sorted(glob.glob(os.path.join(root, HW.TECHNICAL, ticker, '*', 'fundamental.json')))
    if lv:
        own = [h for h in hand if os.path.basename(os.path.dirname(h)) == lv['as_of']]
        hand = own + [h for h in reversed(hand) if h not in own]
    else:
        hand = list(reversed(hand))
    for h in hand:
        try:
            nr = VR.load(h).get('next_report')
        except (OSError, ValueError, AttributeError):
            continue
        w = LF.report_window(nr) if nr else None
        if w:
            return {'text': w[2], 'earliest': w[0].isoformat(), 'latest': w[1].isoformat(),
                    'confirmed': bool(nr.get('confirmed')) if isinstance(nr, dict) else False,
                    'check_he': (nr.get('check') or nr.get('check_he')) if isinstance(nr, dict) else None,
                    'source': os.path.relpath(h, root), 'source_date': os.path.basename(os.path.dirname(h))}
    lp = os.path.join(root, LF.LEDGER)
    if os.path.isfile(lp):
        best = None
        with open(lp, encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            rec_l = r.get('record') or {}
            if r.get('type') == 'thesis' and r.get('entity') == ticker and (rec_l.get('next_report') or {}).get('earliest'):
                if best is None or str(rec_l.get('as_of')) >= str(best.get('as_of')):
                    best = rec_l
        if best:
            nr = best['next_report']
            return {'text': nr.get('text'), 'earliest': nr['earliest'], 'latest': nr['latest'], 'confirmed': nr.get('confirmed'),
                    'check_he': None, 'source': LF.LEDGER, 'source_date': best.get('as_of')}
    return None


def judgement_for(root, ticker, lv):
    """(conditions, falsifiers, verdict_he, source): typed ones from a saved record, else the handoff's (text only)."""
    rec = (lv or {}).get('record') or {}
    if (lv or {}).get('record_source') == VR.RECORD_NAME and (rec.get('conditions') or rec.get('falsifiers')):
        return rec.get('conditions') or [], rec.get('falsifiers') or [], (rec.get('verdict') or {}).get('text_he'), 'record'
    pic = HW.fundamental_picture(root, ticker)
    if pic:
        return pic.get('conditions') or [], pic.get('falsifiers') or [], pic.get('verdict_he'), pic['path']
    return [], [], None, None


def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def holding(root, pos, rules, when, lm=None):
    """The rule results of one stock holding (no weight-based results: those need the whole portfolio)."""
    t = pos['t']
    role_cfg = (rules['holdings'] or {}).get(t) or {}
    row = {'ticker': t, 'role': _val(role_cfg.get('role')), 'role_draft': str(role_cfg.get('draft', '')).startswith('draft'),
           'tags': list(_val(role_cfg.get('tags')) or []), 'price': _num(pos.get('price')), 'price_date': when, 'gaps': []}
    research = os.path.join(root, 'research')
    lv = VR.latest_valuation(research, t)
    if lv is None:
        row['gaps'].append('no dated valuation folder in research/companies: not repriced (a new holding needs a valuation)')
        return row
    row['valuation'] = {'as_of': lv['as_of'], 'folder': os.path.relpath(lv['folder'], root), 'kind': lv['kind'],
                        'model': lv['model'], 'record_source': lv['record_source'],
                        'age_days': _days(lv['as_of'], when) if lv['as_of'] <= when else 0,
                        'older_folders': lv['older_folders']}
    nr = next_report_for(root, t, lv)
    row['next_report'] = nr
    if nr:
        row['next_report_passed'] = nr['latest'] < when
        row['valuation_older_than_last_report'] = row['next_report_passed'] and lv['as_of'] < nr['earliest']
    conds, fals, verdict, jsrc = judgement_for(root, t, lv)
    row['conditions'] = HW.conditions_summary(conds, fals)
    row['conditions']['source'] = jsrc
    row['verdict_he'] = verdict
    rec = lv.get('record')
    if rec is None:
        row['gaps'].append(f"the newest folder ({lv['as_of']}) holds no lean or custom model ({lv['kind']}): not repriced")
        return row
    vprice = _num((rec.get('price') or {}).get('value'))
    cur = (rec.get('currency') or 'USD').upper()
    pcur = str(pos.get('currency') or 'USD').upper()
    if cur != pcur:
        row['gaps'].append(f'the valuation is in {cur}, the position in {pcur}: not repriced')
        return row
    lim = _num(_val(rules.get('price_scale_limit'))) or HW.PRICE_SCALE_LIMIT
    if row['price'] and vprice and not 1 / lim <= row['price'] / vprice <= lim:
        row['gaps'].append(f"the position price {row['price']:,.2f} is {row['price'] / vprice:.2f}x the valuation's {vprice:,.2f}: "
                           'another share unit (split, ADR ratio) or a stale valuation: not repriced')
        return row
    if not row['price']:
        row['gaps'].append('no position price: not repriced')
        return row
    spec = LF.classify(lv['folder'])
    try:
        if spec.get('kind') == 'lean' or spec.get('model') == 'remapped_lean':
            rp = reprice_lean(lv['folder'], spec, row['price'], when, lm)
        else:
            rp = reprice_custom(rec, row['price'], max(when, lv['as_of']))
    except (ValueError, KeyError, TypeError) as e:
        row['gaps'].append(f'reprice failed: {str(e)[:200]}')
        return row
    row['reprice'] = rp
    bands = rules['_bands']
    ours, cal = rp['returns'].get('base'), rp['returns'].get('base_calibrated')
    diag = rp.get('diagnostics') or {}
    not_second = bool(diag.get('inflated_multiple_warning'))
    lens = {'ours': {'return_pct': ours, 'band': band(ours, bands)},
            'second': {'return_pct': cal, 'band': band(cal, bands), 'not_second_opinion': not_second,
                       'reading': diag.get('reading'), 'reasons': diag.get('warning_reasons') or []}}
    if not_second or cal is None:
        lens['agree'] = None
        lens['decision_band'] = lens['ours']['band']
        lens['decision_basis'] = ('our lens alone: the second lens is not a second opinion here (calibrated_diagnostics)'
                                  if not_second else 'our lens alone: no second lens')
    else:
        lens['agree'] = lens['ours']['band'] == lens['second']['band']
        lens['decision_band'] = lens['ours']['band'] if lens['agree'] else None
        lens['decision_basis'] = 'both lenses in the same band' if lens['agree'] else 'the lenses disagree'
    edges = [bands['buy'], bands['hold'], bands['lagging']]
    lens['edge_distance_pts'] = round(min(abs(ours - e) for e in edges), 1) if ours is not None else None
    row['lenses'] = lens
    row['buy_test'] = buy_test_legs(rp, rules)
    return row


# ------------------------------------------------------------------ the portfolio

def _value(r):
    v = _num(r.get('value'))
    if v is None and _num(r.get('shares')) is not None and _num(r.get('price')) is not None:
        v = r['shares'] * r['price']
    return v


def _study_fund(rules):
    sf = rules.get('study_fund') or {}
    amt = _num(_val(sf.get('amount'))) or 0.0
    cur = str(_val(sf.get('currency')) or 'USD').upper()
    if cur != 'USD':
        rate = _num(_val(sf.get('usd_per_unit')))
        if rate is None:
            return None, f'study fund in {cur} without usd_per_unit: not counted'
        amt *= rate
    return amt, None


def _results_he(r, rules):
    """The rule results of one holding in simple Hebrew (statements of the rules, never orders)."""
    out = []
    caps, bands = rules['caps'], rules['_bands']
    role = r.get('role')
    w = r.get('weight')
    lens = r.get('lenses')
    bt = r.get('buy_test') or {}
    if r.get('gaps'):
        out += [f'חסר: {g}' for g in r['gaps']]
    if lens:
        db = lens['decision_band']
        if lens['agree'] is False:
            out.append('העדשות חלוקות: לפי הכלל, בלי פעולה בין דוחות')
        elif lens['second']['not_second_opinion']:
            out.append('העדשה השנייה אינה דעה שנייה כאן (המכפיל המוחזק מנופח): התחום לפי העדשה שלנו בלבד')
        if db == 'buy':
            out.append('בתחום הקנייה' + (' ועוברת את מבחן הקנייה במחיר הזה' if bt.get('pass') else '; מבחן הקנייה לא עובר במחיר הזה'))
        elif db == 'hold':
            out.append(f"בתחום ההחזקה ({bands['hold']:g}-{bands['buy']:g}%)")
        elif db == 'lagging':
            if role == 'core':
                out.append(f"בתחום המפגר ({bands['lagging']:g}-{bands['hold']:g}%); כלל הליבה שנבחר: {LAGGING_RULES[rules['_lagging_reading']]['he']}")
                if rules['_lagging_reading'] == 'cap_6pct' and w is not None and w > _num(_val(caps['core_lagging_cap'])):
                    out.append(f"מעל התקרה של {_num(_val(caps['core_lagging_cap'])):.0%} לליבה בתחום המפגר")
            else:
                out.append(f"בתחום המפגר ({bands['lagging']:g}-{bands['hold']:g}%); לפי הכלל, מניה שאינה ליבה בתחום הזה מסומנת ליציאה")
        elif db == 'sell':
            out.append(f"מתחת לקו המכירה ({bands['lagging']:g}%)" + (' בשתי העדשות' if lens['agree'] else '')
                       + ': לפי הכלל, מסומנת ליציאה')
        if lens.get('edge_distance_pts') is not None and lens['edge_distance_pts'] < 1:
            out.append('על גבול התחום (פחות מנקודה): הפרש כזה הוא רעש של המודל')
    hit = (r.get('conditions') or {}).get('hit')
    if hit:
        out.append('מפריך הופעל: ' + '; '.join(hit) + ' (לפי הכלל, מסומנת ליציאה)')
    if w is not None:
        if w > _num(_val(caps['trim_above'])):
            out.append(f"מעל התקרה של {_num(_val(caps['trim_above'])):.0%} (הכלל: חזרה ל-{_num(_val(caps['trim_to'])):.0%})")
        elif w > _num(_val(caps['no_new_money_above'])):
            out.append(f"מעל {_num(_val(caps['no_new_money_above'])):.0%}: לפי הכלל, בלי כסף חדש למניה הזו")
        if role == 'small_bet' and w > _num(_val(caps['small_bet_max'])):
            out.append(f"מעל {_num(_val(caps['small_bet_max'])):.0%} להימור קטן")
        if role == 'core' and w < _num(_val(caps['core_min'])):
            out.append(f"מתחת ל-{_num(_val(caps['core_min'])):.0%}: קטנה לליבה"
                       + ('; מבחן הקנייה לא עובר במחיר הזה, ולפי הכלל פוזיציה כזו עוברת למדד ולרשימת החזרה'
                          if bt and bt.get('complete') and not bt.get('pass') else ''))
    if role == 'leaving':
        out.append('סומנה "יוצאת" בקובץ הכללים')
    if r.get('next_report_passed'):
        out.append('הדוח הבא כבר עבר' + (' ואין הערכה מעודכנת אחריו' if r.get('valuation_older_than_last_report') else ''))
    return out


def health(positions, rules, root='.', as_of=None, holdings_update=None, lm=None):
    """The health document (nothing is written). positions: the READ-ONLY copy {as_of, rows, cash?, study_fund_usd?,
    total_wealth?}; rules: load_rules(); holdings_update: an optional holdings_update.json result of the same date, whose
    filing-check status is attached per row."""
    when = str(as_of or positions.get('as_of'))[:10]
    date.fromisoformat(when)
    stocks, funds, skipped = HW.position_rows(positions)
    idx_named = {str(x).upper() for x in (_val(rules['index_funds'].get('tickers')) or [])}
    roles = {t: _val((c or {}).get('role')) for t, c in (rules['holdings'] or {}).items()}
    moved = [s for s in stocks if s['t'] in idx_named or roles.get(s['t']) == 'index']
    funds = funds + moved
    stocks = [s for s in stocks if s not in moved]
    study, study_note = _study_fund(rules)
    cash = _num(positions.get('cash')) or 0.0
    vals = {r['t']: _value(r) for r in stocks + funds + skipped}
    missing_value = sorted(t for t, v in vals.items() if v is None)
    total = sum(v for v in vals.values() if v) + cash + (study or 0.0)
    rows = []
    for pos in stocks:
        r = holding(root, pos, rules, when, lm)
        r['value_known'] = vals.get(pos['t']) is not None
        r['weight'] = vals[pos['t']] / total if vals.get(pos['t']) and total else None
        hu = next((x for x in (holdings_update or {}).get('rows') or [] if x.get('ticker') == pos['t']), None)
        if hu:
            r['filing_check'] = {'status': hu.get('status'), 'line_he': hu.get('line_he'),
                                 'basis': (hu.get('filing_check') or {}).get('basis')}
        if not r['role']:
            r['gaps'].append('no role in the rules file (core / small_bet / leaving / index)')
        r['results_he'] = _results_he(r, rules)
        rows.append(r)
    index_value = sum(vals[f['t']] or 0 for f in funds) + (study or 0.0)
    stock_value = sum(vals[s['t']] or 0 for s in stocks)
    it, sp = rules['index_target'], rules['stock_part']
    idx_share = index_value / total if total else None
    stock_share = stock_value / total if total else None

    def where(x, lo, hi):
        return None if x is None else 'below' if x < lo else 'above' if x > hi else 'inside'
    tags = {}
    for r in rows:
        for tg in r['tags']:
            tags.setdefault(tg, []).append(r)
    linked = {}
    for tg, rs in sorted(tags.items()):
        w = sum(x['weight'] or 0 for x in rs)
        cfg = (rules.get('linked_exposure') or {}).get(tg) or {}
        linked[tg] = {'tickers': [x['ticker'] for x in rs], 'weight': w, 'share_of_stock_part': w / stock_share if stock_share else None,
                      'label_he': _val(cfg.get('label_he')) or tg, 'note_above': _num(_val(cfg.get('note_above')))}
    stated = _num(positions.get('total_wealth'))
    pf = {'total': total, 'cash': cash, 'study_fund': study, 'study_fund_note': study_note,
          'index_funds': sorted(f['t'] for f in funds), 'index_share': idx_share,
          'index_target': {'min': _num(_val(it['min'])), 'max': _num(_val(it['max']))},
          'index_position': where(idx_share, _num(_val(it['min'])), _num(_val(it['max']))),
          'stock_share': stock_share, 'stock_part': {'min': _num(_val(sp['min'])), 'max': _num(_val(sp['max']))},
          'stock_position': where(stock_share, _num(_val(sp['min'])), _num(_val(sp['max']))),
          'linked_exposure': linked, 'skipped_non_stock': [r['t'] for r in skipped], 'values_missing': missing_value,
          'stated_total': stated,
          'stated_total_differs': bool(stated and total and abs(stated / total - 1) > 0.005)}
    return {'portfolio_health_version': 1, 'as_of': when, 'positions_as_of': positions.get('as_of'),
            'positions_source': positions.get('source'), 'rules_source': rules.get('_source'),
            'lagging_core_reading': {'value': rules['_lagging_reading'], **LAGGING_RULES[rules['_lagging_reading']]},
            'rules_drafts': rules['_drafts'], 'rules_problems': rules['_problems'], 'bands_pct': rules['_bands'],
            'portfolio': pf, 'rows': rows,
            'meaning': 'Rule results computed from the saved valuations at the position prices (a reprice changes the price '
                       'and the dates only; no input is re-chosen), the READ-ONLY positions and the private rules file. '
                       'Rule results, not orders; the 1-2 year number is the noisiest and differences under about '
                       f"{_num(_val(rules['noise_pts'])):g} points are model noise."}


# ------------------------------------------------------------------ Hebrew

def _p(x, d=1):
    return '—' if x is None else f'{x * 100:.{d}f}%'


def _r(x):
    return '—' if x is None else f'{x:.1f}%'


def _money(x):
    return '—' if x is None else f'{x:,.2f}$' if abs(x) < 100 else f'{x:,.0f}$'


def render_he(doc):
    pf = doc['portfolio']
    lines = [f"# בדיקת בריאות לתיק ({doc['as_of']})", '',
             f"- המקור: הפוזיציות ({doc['positions_as_of']}, {doc['positions_source'] or 'לא צוין'}), ההערכות השמורות ב-research/ "
             f"וקובץ הכללים ({doc['rules_source']}). תוצאות של כללים, לא הוראות.",
             f"- קריאת הכלל למניית ליבה בתחום המפגר: {doc['lagging_core_reading']['he']} ({doc['lagging_core_reading']['value']}; "
             'האפשרות השנייה בקובץ הכללים: ' + ('תקרה של 6% מכל הכסף' if doc['lagging_core_reading']['value'] != 'cap_6pct'
                                               else 'מחזיקים בלי כסף חדש') + ').']
    if doc['rules_drafts']:
        lines.append(f"- שדות בקובץ הכללים שעדיין בטיוטה (ממתינים לאישור): {', '.join(doc['rules_drafts'])}.")
    if doc['rules_problems']:
        lines.append('- בעיות בקובץ הכללים: ' + '; '.join(doc['rules_problems']) + '.')
    pos_he = {'below': 'מתחת לטווח', 'above': 'מעל הטווח', 'inside': 'בתוך הטווח', None: '—'}
    it, sp = pf['index_target'], pf['stock_part']
    lines += ['', '## התיק', '',
              f"- המדד (קרנות מדד {', '.join(pf['index_funds']) or 'אין'} וקרן ההשתלמות): {_p(pf['index_share'])} מכל הכסף; "
              f"הטווח {it['min']:.0%}-{it['max']:.0%}: {pos_he[pf['index_position']]}.",
              f"- המניות הבודדות: {_p(pf['stock_share'])}; הטווח {sp['min']:.0%}-{sp['max']:.0%}: {pos_he[pf['stock_position']]}."]
    if pf['study_fund_note']:
        lines.append(f"- קרן ההשתלמות: {pf['study_fund_note']}.")
    for tg, x in pf['linked_exposure'].items():
        lines.append(f"- חשיפה משותפת ({x['label_he']}: {', '.join(x['tickers'])}): {_p(x['weight'])} מכל הכסף, "
                     f"{_p(x['share_of_stock_part'], 0)} מחלק המניות" + (' — הימור מתואם אחד' if x['note_above'] and x['weight'] > x['note_above'] else '') + '.')
    if pf['stated_total_differs']:
        lines.append(f"- הסכום שחושב ({_money(pf['total'])}) שונה מהסכום שבקובץ הפוזיציות ({_money(pf['stated_total'])}).")
    if pf['values_missing']:
        lines.append(f"- אין שווי לפוזיציה: {', '.join(pf['values_missing'])}.")
    lines += ['', '## לפי מניה', '',
              '| מניה | תפקיד | משקל | הערכה (גיל) | בסיסי שלנו | עדשה שנייה | העדשות | מבחן הקנייה | דוח הבא | תנאים | תוצאת הכללים |',
              '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in doc['rows']:
        v = r.get('valuation') or {}
        lens = r.get('lenses') or {}
        bt = r.get('buy_test') or {}
        o, s = lens.get('ours') or {}, lens.get('second') or {}
        agree = {True: 'מסכימות', False: 'חלוקות', None: 'אין דעה שנייה' if s.get('not_second_opinion') else '—'}[lens.get('agree')] if lens else '—'
        if bt:
            failed = [LEG_HE[k] for k, x in bt['legs'].items() if x['pass'] is False]
            btc = ('עובר' if bt['pass'] else 'לא עובר' + (f" ({', '.join(failed)})" if failed else '')) + \
                  (f"; עד {_money(bt['max_price_passing'])}" if bt.get('max_price_passing') else '') + \
                  (f"; בהרצה חוזרת עד {_money(bt['rerun_max_price_passing'])}" if bt.get('rerun_max_price_passing') else '')
        else:
            btc = '—'
        nr = r.get('next_report') or {}
        nrc = (nr.get('text') or '—') + (' (עבר)' if r.get('next_report_passed') else '') if nr else 'אין תאריך'
        cs = r.get('conditions') or {}
        csc = f"{cs.get('count', 0)} ({cs.get('untyped', 0)} בלי מספר)" + (f"; הופעל {len(cs['hit'])}" if cs.get('hit') else '') if cs else '—'
        role = ROLE_HE.get(r.get('role'), r.get('role')) + (' (טיוטה)' if r.get('role_draft') else '')
        val = f"{v.get('as_of')} ({v.get('age_days')} ימים)" if v else 'אין'
        lines.append(f"| {r['ticker']} | {role} | {_p(r.get('weight'))} | {val} | "
                     f"{_r(o.get('return_pct'))} ({BAND_HE.get(o.get('band'), '—')}) | {_r(s.get('return_pct'))} ({BAND_HE.get(s.get('band'), '—')}) | "
                     f"{agree} | {btc} | {nrc} | {csc} | {'; '.join(r['results_he']) or '—'} |")
    lines += ['', '## איך זה חושב', '',
              '- כל מניה עם קובץ מודל רזה הורצה מחדש במחיר ובתאריך של הפוזיציה (רק המחיר והתאריכים זזים; התחזית לא נבחרת מחדש). '
              'מודל מותאם (למשל שלושת המסלולים של META) לא רץ מחדש: הערכים השמורים לסוף התקופה נקראו מול המחיר של היום.',
              '- התחומים: קנייה 12% בשנה ומעלה, החזקה 6-12%, מפגר 3-6%, מכירה מתחת ל-3% (התשואה הבסיסית ל-5 שנים). '
              'מבחן הקנייה: בסיסי 12% ומעלה בשתי העדשות, שורי 17% ומעלה, דובי ‎-10%‎ ומעלה.',
              '- המשקל הוא מכל הכסף (כולל קרן ההשתלמות והמזומן). המספר לשנה-שנתיים הוא הרועש ביותר; הפרשים של פחות מכ-3 נקודות הם רעש.']
    notes = []
    for r in doc['rows']:
        rp = r.get('reprice') or {}
        bits = []
        if rp.get('worlds'):
            bits.append('ממוצע משוקלל של העולמות ' + ', '.join(f'{k} {w:.0%}' for k, w in rp['worlds'].items()))
        if rp.get('base_year_end_derived'):
            bits.append('תאריך סוף שנת הבסיס חושב בזיכרון מהקובץ (as_of + days_to_base_year_end)')
        if rp and rp.get('dates_moved') is False:
            bits.append('התאריכים לא זזו')
        if (r.get('lenses') or {}).get('second', {}).get('not_second_opinion'):
            bits.append('העדשה השנייה: ' + '; '.join(r['lenses']['second']['reasons']))
        if r.get('filing_check'):
            bits.append(f"בדיקת הדוחות (holdings-update): {r['filing_check']['line_he']}")
        if bits:
            notes.append(f"- {r['ticker']}: " + '; '.join(bits) + '.')
    if notes:
        lines += ['', '## הערות', ''] + notes
    return '\n'.join(lines) + '\n'


def write(doc, out_dir, root='.'):
    """Write portfolio_health.json and portfolio_health_he.md; refuses a folder outside <root>/runs/ (private data)."""
    _private_dir(out_dir, root)
    os.makedirs(out_dir, exist_ok=True)
    jp, mp = os.path.join(out_dir, 'portfolio_health.json'), os.path.join(out_dir, 'portfolio_health_he.md')
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, default=str)
    with open(mp, 'w', encoding='utf-8') as f:
        f.write(render_he(doc))
    return {'json': jp, 'markdown': mp}


def _private_dir(out_dir, root):
    runs = os.path.realpath(os.path.join(root, 'runs'))
    if not os.path.realpath(out_dir).startswith(runs + os.sep):
        raise ValueError(f'{out_dir}: portfolio output is private and goes only under {os.path.join(root, "runs")}/')


# ------------------------------------------------------------------ due

def thesis_tickers(root):
    """Every ticker with a dated folder in research/companies or research/technical."""
    out = set()
    for base in (os.path.join(root, 'research', 'companies'), os.path.join(root, HW.TECHNICAL)):
        for t in os.listdir(base) if os.path.isdir(base) else []:
            if glob.glob(os.path.join(base, t, '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*')):
                out.add(t)
    return sorted(out)


def due(root, today, days=30, held=(), filings=None, tickers=None):
    """Which theses are due. filings: {ticker: {'events': [{form, date, kind}], 'source': text}} from the SEC filing
    index (optional). held: tickers held (private: the caller writes the result under runs/ only)."""
    end = date.fromordinal(date.fromisoformat(today).toordinal() + days).isoformat()
    held = {str(t).upper() for t in held or ()}
    rows = []
    for t in tickers or thesis_tickers(root):
        lv = VR.latest_valuation(os.path.join(root, 'research'), t)
        pic = HW.fundamental_picture(root, t)
        v_date = max([d for d in ((lv or {}).get('as_of'), (pic or {}).get('date')) if d] or [None])
        nr = next_report_for(root, t, lv)
        r = {'ticker': t, 'held': t in held, 'valuation_as_of': v_date, 'valuation_kind': (lv or {}).get('kind'),
             'next_report': nr}
        if nr is None:
            r['status'] = 'no_date'
        elif nr['latest'] < today:
            r['status'] = 'passed_without_update' if v_date and v_date < nr['earliest'] else 'passed'
        elif nr['earliest'] > end:
            r['status'] = 'later'
        else:
            r['status'] = 'in_window' if nr['earliest'] <= today else 'upcoming'
        f = (filings or {}).get(t)
        if f is not None:
            ev = [e for e in f.get('events') or [] if v_date and e['date'] > v_date]
            r['filings_since_valuation'] = {'events': ev, 'source': f.get('source')}
        ev = (r.get('filings_since_valuation') or {}).get('events') or []
        r['valuation_older_than_last_report'] = r['status'] == 'passed_without_update' or any(
            not e['form'].startswith('6-K') for e in ev)
        r['six_k_since_valuation'] = sum(e['form'].startswith('6-K') for e in ev)   # results or other news: to check
        conds, fals, _, src = judgement_for(root, t, lv)
        cs = HW.conditions_summary(conds, fals)
        dated = [c for c in conds + fals if (c.get('state') or 'open') == 'open' and c.get('by_date') and c['by_date'] <= end]
        r['open_conditions'] = {'open': cs['by_state']['open'], 'untyped': cs['untyped'], 'hit': cs['hit'], 'source': src,
                                'due_by_date': [{'id': c.get('id'), 'by_date': c['by_date'], 'text_he': c.get('text_he')} for c in dated]}
        rows.append(r)
    return {'due_version': 1, 'today': today, 'days': days, 'until': end, 'rows': rows,
            'upcoming': [r['ticker'] for r in rows if r['status'] in ('upcoming', 'in_window')],
            'passed_without_update': [r['ticker'] for r in rows if r['status'] == 'passed_without_update'],
            'held_stale': [r['ticker'] for r in rows if r['held'] and r['valuation_older_than_last_report']],
            'no_date': [r['ticker'] for r in rows if r['status'] == 'no_date'],
            'held_6k_to_check': [r['ticker'] for r in rows if r['held'] and r.get('six_k_since_valuation')
                                 and not r['valuation_older_than_last_report']],
            'meaning': 'next report from the saved record, else the handoff (the valuation\'s own date first), else the lean '
                       'ledger; a report window that ended before today with no valuation dated on or after its start is '
                       '"passed without an update"; filings_since_valuation lists results-type filings of the SEC filing '
                       'index after the valuation; a 8-K 2.02, 10-Q, 10-K, 20-F or 40-F there makes a held valuation older '
                       'than the last report, a 6-K only (results or other news) is listed to check.'}


def render_due_he(doc):
    st_he = {'upcoming': 'בקרוב', 'in_window': 'בחלון עכשיו', 'passed_without_update': 'עבר בלי עדכון', 'passed': 'עבר',
             'later': 'מאוחר יותר', 'no_date': 'אין תאריך'}
    lines = [f"# מה עומד לדוח ({doc['today']}, {doc['days']} הימים הבאים עד {doc['until']})", '',
             f"- דוחות בחלון: {', '.join(doc['upcoming']) or 'אין'}.",
             f"- דוח שעבר בלי עדכון הערכה: {', '.join(doc['passed_without_update']) or 'אין'}.",
             f"- מניות בתיק שההערכה שלהן ישנה מהדוח האחרון: {', '.join(doc['held_stale']) or 'אין'}.",
             f"- בלי תאריך לדוח הבא: {', '.join(doc['no_date']) or 'אין'}.",
             f"- מניות בתיק עם 6-K אחרי ההערכה (דוח או חדשות אחרות, לבדוק): {', '.join(doc['held_6k_to_check']) or 'אין'}.", '',
             '| מניה | בתיק | הערכה | הדוח הבא | מצב | הגשות מאז ההערכה | תנאים פתוחים |', '|---|---|---|---|---|---|---|']
    order = {'in_window': 0, 'upcoming': 1, 'passed_without_update': 2, 'passed': 3, 'later': 4, 'no_date': 5}
    for r in sorted(doc['rows'], key=lambda r: (order[r['status']], (r['next_report'] or {}).get('earliest') or '9', r['ticker'])):
        nr = r['next_report'] or {}
        nrc = f"{nr.get('text')} ({nr.get('earliest')}" + (f" עד {nr['latest']}" if nr.get('latest') != nr.get('earliest') else '') + \
              (', מאושר' if nr.get('confirmed') else '') + ')' if nr else '—'
        fs = r.get('filings_since_valuation')
        fsc = ('—' if fs is None else ', '.join(f"{e['form']} {e['date']}" for e in fs['events'][-3:]) or 'אין')
        oc = r['open_conditions']
        occ = f"{oc['open']} ({oc['untyped']} בלי מספר)" + (f"; הופעל: {', '.join(oc['hit'])}" if oc['hit'] else '') + \
              ''.join(f"; עד {c['by_date']}: {c['text_he']}" for c in oc['due_by_date'])
        lines.append(f"| {r['ticker']} | {'כן' if r['held'] else ''} | {r['valuation_as_of'] or '—'} | {nrc} | {st_he[r['status']]} | {fsc} | {occ} |")
    lines += ['', '- התאריכים מההערכה השמורה, אחרת מקובץ ההעברה (fundamental.json), אחרת מהספר של המודל הרזה. '
                  'תאריך משוער ("~") הוא 3 ימים לכל כיוון; "סוף ינואר" הוא כל החודש.']
    return '\n'.join(lines) + '\n'
