"""One machine record per valuation, and the published files projected from it (15.11.2026).

Why: the thesis table, the index row and the handoff were typed by hand from the model output, so they could disagree
with the model and with each other (UBER's margin-of-safety price 95 next to 99.14, NBIS text inside numeric fields,
NVDA three different base values). The record research/companies/<T>/<date>/valuation.json holds every published
number once; the handoff, the index row and the thesis numbers block are written from it and checked against it.

Parts:
- validate(record)                 the schema: numbers are numbers or null (the reason goes in a note field);
- build_from_lean(params, result)  the record of a lean_model valuation (result = lean_model.run(params));
- adapt_bank / adapt_segment / adapt_three_lane   the custom models kept in Git (BNY, SPCX, META); NU's file is lean
                                   format remapped to net income and goes through build_from_lean;
- record_for_folder(folder, root)  picks the builder by the folder's files (lean_freeze.classify);
- latest_valuation(root, ticker)   the newest dated folder of any kind, with its record when one can be built;
- project_handoff(record)          research/technical/<T>/<date>/fundamental.json: every v1 key, plus schema_version 2;
- index_row(record), numbers_block(record), replace_block(text, block)   the index row and the thesis numbers block;
- check_tree(research_root, run)   the consistency check of scripts/publish_valuation.py --check.

Judgement that no model computes (the verdict, the scenario conditions, typed conditions and falsifiers, the next
report, the historical-multiple inputs) comes from a thesis-inputs dict (THESIS_INPUTS_KEYS); inputs_from_handoff()
reads the same fields from an existing v1 handoff. Nothing here fetches data, and nothing writes: the command does.
Company research only: a record never holds holdings, costs or amounts.
"""
import calendar
import glob
import hashlib
import json
import math
import os
import re
from datetime import date, timedelta

from . import lean_freeze, locked_rules

SCHEMA = 'valuation_record/1'
HANDOFF_SCHEMA_VERSION = 2
STORE = os.path.join('companies')
TECH = os.path.join('technical')
INDEX = 'THESES_he.md'
RECORD_NAME = 'valuation.json'
WORLDS_NAME = 'worlds.json'   # weighted worlds beside lean_params.json (scripts/lean_model.py --worlds)
DATED = re.compile(r'^\d{4}-\d{2}-\d{2}$')
SCEN = ('bear', 'base', 'bull')
KINDS = ('exit', 'trim', 'add', 'too_low')
STATES = ('open', 'met', 'not_met', 'hit', 'unknown')
VERDICTS = ('buy', 'hold', 'not_buy', 'sell', None)
MODEL_KINDS = ('lean', 'lean_remapped', 'bank', 'segment', 'three_lane')
V1_KEYS = ('ticker', 'as_of', 'price', 'price_time_utc', 'verdict_he', 'buy_test_price', 'margin_of_safety_price',
           'values', 'scenarios', 'annual_return_pct', 'conditions', 'next_report', 'thesis_link', 'notes_he')
THESIS_INPUTS_KEYS = ('company', 'price_time_utc', 'price_source', 'currency', 'share_basis', 'known_split', 'scenarios',
                      'verdict', 'conditions', 'falsifiers', 'next_report', 'last_fresh_pull', 'notes_he',
                      'historical_multiple', 'entry_price', 'index')
BLOCK_START = '<!-- numbers:start (written by scripts/publish_valuation.py from valuation.json; do not edit by hand) -->'
BLOCK_END = '<!-- numbers:end -->'
BLOCK_RE = re.compile(r'<!-- numbers:start[^>]*-->.*?<!-- numbers:end -->', re.S)
NEAR_YEAR_END_DAYS = 92   # 'in one year' shows the fiscal year-end when it is this close to as_of + 1 year, else that day
LABELS_HE = {'buy_test': 'מחיר מבחן הקנייה', 'margin_of_safety': 'מחיר כניסה עם מרווח ביטחון',
             'hist': locked_rules.LABEL_HE}
# keys of a handoff that hold text by design; every other leaf under the numeric keys must be a number or null
HANDOFF_NUMERIC_KEYS = ('price', 'buy_test_price', 'margin_of_safety_price', 'values', 'annual_return_pct')
TEXT_LEAF = re.compile(r'(note|basis|label|source|^to$|_he$|^he$|text|method|why|rule|date)')
RECORD_TEXT = re.compile(r'(note|basis|label|source|band|date|rule|_he$|^id$|leg$|rung|meaning|company_type|anchor|fiscal_year_end|base_year_end|moves_most|^at_add_price$|leg_results)')
NUMERIC_CONTAINERS = re.compile(r'^(rungs|dated_values|at_add_price)$')   # their names match a text key, their leaves are numbers
LEAN_TOP = ('price', 'rf', 'erp', 'beta', 'beta_mature', 'shares_m', 'net_cash_m', 'rev0_m', 'base_year',
            'days_to_base_year_end', 'years_to_horizon', 'buy_return', 'probabilities')


# ------------------------------------------------------------------ small helpers

def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def sha256_file(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _text(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def dumps(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False) + '\n'


def _iso(x):
    try:
        return date.fromisoformat(str(x)) if isinstance(x, str) and DATED.match(x) else None
    except ValueError:
        return None


def _shift(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def file_ref(path, root):
    """{path relative to root, sha256} of a file, or None when it does not exist."""
    if not path or not os.path.isfile(path):
        return None
    return {'path': os.path.relpath(path, root).replace(os.sep, '/'), 'sha256': sha256_file(path)}


def _num(x, places=None):
    if not is_number(x):
        return None
    return round(x, places) if places is not None else x


def _interp(points, when):
    """Log-linear value on a dated path (forecast_tracker.value_at); None outside it or through a zero."""
    for (d0, v0), (d1, v1) in zip(points, points[1:]):
        if d0 <= when <= d1 and v0 > 0 and v1 > 0:
            w = (when - d0).days / max((d1 - d0).days, 1)
            return math.exp(math.log(v0) + w * (math.log(v1) - math.log(v0)))
    return None


def is_lean_params(p):
    """A lean_model params file by its keys (the same test as tests/test_saved_valuations.py)."""
    if not isinstance(p, dict) or not all(k in p for k in LEAN_TOP):
        return False
    sc = p.get('scenarios')
    return (isinstance(sc, dict) and 'base' in sc
            and all(isinstance(d, dict) and all(k in d for k in ('g', 'm', 'conv', 'pe')) for d in sc.values()))


def default_run():
    """lean_model.run from scripts/lean_model.py of this checkout, or None when the script is not there."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'lean_model.py')
    if not os.path.isfile(path):
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location('lean_model', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.run


REPORTING_KEYS = ('price_ladder', 'buy_test', 'dated_values', 'two_lens')
SAVED_VALUE_KEYS = ('scenarios', 'weighted_value_today', 'weighted_end_value', 'weighted_return_pct', 'index_return_pct', 'buy_price',
                    'index_parity_price', 'two_lens')


# ------------------------------------------------------------------ schema and validator

def _typed_item(item, where, problems):
    if not isinstance(item, dict):
        problems.append(f'{where}: must be an object')
        return
    for k in ('id', 'kind', 'metric', 'op', 'threshold', 'by_date', 'text_he', 'state'):
        if k not in item:
            problems.append(f'{where}: missing key {k}')
    if item.get('kind') not in KINDS:
        problems.append(f'{where}: kind {item.get("kind")!r} is not one of {"|".join(KINDS)}')
    if item.get('state') not in STATES:
        problems.append(f'{where}: state {item.get("state")!r} is not one of {"|".join(STATES)}')
    if item.get('threshold') is not None and not is_number(item.get('threshold')):
        problems.append(f'{where}: threshold must be a number or null (text goes in text_he), got {item.get("threshold")!r}')
    if item.get('by_date') is not None and _iso(item.get('by_date')) is None:
        problems.append(f'{where}: by_date must be an ISO date or null, got {item.get("by_date")!r}')
    if not isinstance(item.get('text_he'), str) or not item.get('text_he'):
        problems.append(f'{where}: text_he must be a non-empty text')


def _numbers_only(obj, where, problems, text_keys=TEXT_LEAF):
    """Every leaf must be a number, null or a bool, except keys that hold text by design."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if text_keys.search(str(k)) and not (isinstance(v, (dict, list)) and NUMERIC_CONTAINERS.match(str(k))):
                continue
            if isinstance(v, (dict, list)):
                _numbers_only(v, f'{where}/{k}', problems, text_keys)
            elif isinstance(v, str):
                problems.append(f'{where}/{k}: text in a numeric field ({v!r})')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                _numbers_only(v, f'{where}[{i}]', problems, text_keys)
            elif isinstance(v, str):
                problems.append(f'{where}[{i}]: text in a numeric field ({v!r})')


def rung_prices(record):
    """Every price of the record's ladder: {label: price} (both lenses, the buy-test legs, the rerun price)."""
    h = record.get('hurdles') or {}
    out = {}
    for r in h.get('rungs') or []:
        for lens, key in (('', 'price'), (' (second lens)', 'price_second_lens')):
            if is_number(r.get(key)):
                out[f'{r.get("id")}{lens}'] = r[key]
    bt = h.get('buy_test') or {}
    for key in ('price', 'stated_price', 'rerun_price'):
        if is_number(bt.get(key)):
            out[f'buy_test.{key}'] = bt[key]
    for leg, v in (bt.get('legs') or {}).items():
        if is_number((v or {}).get('max_price')):
            out[f'buy_test.leg.{leg}'] = v['max_price']
    return out


def validate(record):
    """Problems of a record as a list of texts (empty = valid)."""
    p = []
    if not isinstance(record, dict):
        return ['the record must be an object']
    if record.get('schema') != SCHEMA:
        p.append(f'schema must be {SCHEMA!r}, got {record.get("schema")!r}')
    if not isinstance(record.get('ticker'), str) or not record.get('ticker'):
        p.append('ticker must be a text')
    if _iso(record.get('as_of')) is None:
        p.append(f'as_of must be an ISO date (YYYY-MM-DD), got {record.get("as_of")!r}')
    price = record.get('price')
    if not isinstance(price, dict) or not is_number(price.get('value')) or price['value'] <= 0:
        p.append('price.value must be a positive number')
    if not isinstance(record.get('currency'), str):
        p.append('currency must be a text (e.g. USD)')
    sb = record.get('share_basis')
    if not isinstance(sb, dict) or 'known_split' not in sb:
        p.append('share_basis must be an object with known_split (null when none is known)')
    elif sb.get('model_shares_m') is not None and not is_number(sb['model_shares_m']):
        p.append(f'share_basis/model_shares_m: text in a numeric field ({sb["model_shares_m"]!r})')
    elif sb['known_split'] is not None:
        ks = sb['known_split']
        if not isinstance(ks, dict) or _iso(ks.get('effective')) is None or not is_number(ks.get('ratio')) or ks['ratio'] <= 0:
            p.append('share_basis.known_split needs an ISO effective date and a positive ratio (new shares per old share)')
    m = record.get('model')
    if not isinstance(m, dict) or m.get('kind') not in MODEL_KINDS:
        p.append(f'model.kind must be one of {"|".join(MODEL_KINDS)}')
    else:
        for k in ('params', 'result', 'code'):
            ref = m.get(k)
            if ref is not None and not (isinstance(ref, dict) and isinstance(ref.get('path'), str)
                                        and re.fullmatch(r'[0-9a-f]{64}', str(ref.get('sha256')))):
                p.append(f'model.{k} must be null or {{path, sha256}}')
        if m.get('result') is None:
            p.append('model.result is missing: a record needs the saved result it was built from')
    sc = record.get('scenarios')
    if not isinstance(sc, dict) or 'base' not in sc:
        p.append('scenarios must hold at least a base case')
    else:
        probs = [v.get('probability') for v in sc.values() if isinstance(v, dict)]
        if all(is_number(x) for x in probs) and abs(sum(probs) - 1) > 1e-6:
            p.append(f'the scenario probabilities sum to {sum(probs):g}, not 1')
        for name, v in sc.items():
            if not isinstance(v, dict):
                p.append(f'scenarios/{name}: must be an object')
                continue
            for k in ('probability', 'value_today', 'annual_return_pct'):
                if k not in v or (v[k] is not None and not is_number(v[k])):
                    p.append(f'scenarios/{name}/{k}: must be a number or null, got {v.get(k)!r}')
            if not isinstance(v.get('wiped'), bool):
                p.append(f'scenarios/{name}/wiped: must be true or false')
            if v.get('condition_he') is not None and not isinstance(v.get('condition_he'), str):
                p.append(f'scenarios/{name}/condition_he: must be a text or null')
            for row in v.get('dated_values') or []:
                if _iso(row.get('date')) is None or (row.get('value') is not None and not is_number(row.get('value'))):
                    p.append(f'scenarios/{name}/dated_values: each row needs an ISO date and a number or null')
            _numbers_only(v.get('at') or {}, f'scenarios/{name}/at', p, RECORD_TEXT)
        if not is_number((sc.get('base') or {}).get('value_today')):
            p.append('scenarios/base/value_today must be a number')
    for key in ('second_lens', 'base_path', 'weighted'):
        if not isinstance(record.get(key), dict):
            p.append(f'{key} must be an object (its numbers may be null, with a note)')
        else:
            _numbers_only(record[key], key, p, RECORD_TEXT)
    h = record.get('hurdles')
    if not isinstance(h, dict):
        p.append('hurdles must be an object')
    else:
        _numbers_only(h, 'hurdles', p, RECORD_TEXT)
        for k in ('buy_test_price', 'margin_of_safety_price', 'index_parity_price', 'hold_floor_price', 'sell_line_price'):
            if k not in h:
                p.append(f'hurdles/{k}: missing (null with a note when the model has no such rung)')
        ep = h.get('entry_price')
        if not isinstance(ep, dict) or 'value' not in ep:
            p.append('hurdles/entry_price must be {value, rung}')
        elif ep['value'] is not None and not is_number(ep['value']):
            p.append(f'hurdles/entry_price/value: text in a numeric field ({ep["value"]!r})')
        elif ep['value'] is not None:
            rungs = rung_prices(record)
            if not any(abs(ep['value'] - v) < 0.005 for v in rungs.values()):
                p.append(f'hurdles/entry_price {ep["value"]:g} is not a rung of the price ladder '
                         f'({", ".join(f"{k} {v:g}" for k, v in sorted(rungs.items()))})')
    hm = record.get('historical_multiple')
    if not isinstance(hm, dict):
        p.append('historical_multiple must be an object (value null with a note when not computed)')
    else:
        _numbers_only(hm, 'historical_multiple', p, RECORD_TEXT)
    for key in ('conditions', 'falsifiers'):
        if not isinstance(record.get(key), list):
            p.append(f'{key} must be a list')
        else:
            ids = [c.get('id') for c in record[key] if isinstance(c, dict)]
            if len(ids) != len(set(ids)):
                p.append(f'{key}: ids must be unique')
            for i, c in enumerate(record[key]):
                _typed_item(c, f'{key}[{i}]', p)
    nr = record.get('next_report')
    if not isinstance(nr, dict) or not isinstance(nr.get('confirmed'), bool):
        p.append('next_report must be an object with confirmed true/false')
    else:
        if nr.get('date') is not None and _iso(nr.get('date')) is None:
            p.append(f'next_report/date must be an ISO date or null (a window otherwise), got {nr.get("date")!r}')
        w = nr.get('window')
        if w is not None and not (isinstance(w, dict) and _iso(w.get('from')) and _iso(w.get('to'))):
            p.append('next_report/window must be null or {from, to} ISO dates')
    lf = record.get('last_fresh_pull')
    if not isinstance(lf, dict) or (lf.get('date') is not None and _iso(lf.get('date')) is None):
        p.append('last_fresh_pull must be {date: ISO or null, note}')
    v = record.get('verdict')
    if not isinstance(v, dict) or v.get('code') not in VERDICTS or not isinstance(v.get('draft'), bool):
        p.append(f'verdict must be {{code: {"|".join(str(x) for x in VERDICTS)}, text_he, draft}}')
    return p


# ------------------------------------------------------------------ judgement inputs

def _typed(items, default_kind, prefix):
    out = []
    for i, c in enumerate(items or []):
        c = dict(c) if isinstance(c, dict) else {'text_he': str(c)}
        row = {'id': c.get('id') or f'{prefix}{i + 1}', 'kind': c.get('kind') or default_kind, 'metric': c.get('metric'),
               'op': c.get('op'), 'threshold': c.get('threshold'), 'by_date': c.get('by_date'),
               'text_he': c.get('text_he') or c.get('text') or '', 'state': c.get('state') or 'open'}
        if c.get('note'):
            row['note'] = c['note']
        out.append(row)
    return out


def _kind_of(key):
    k = key.lower()
    if 'trim' in k:
        return 'trim'
    if any(w in k for w in ('exit', 'sell', 'worsen', 'falsif', 'watch')):
        return 'exit'
    if 'too_low' in k or 'raise_value' in k:
        return 'too_low'
    return 'add'


def inputs_from_handoff(h):
    """Thesis inputs read from a v1 handoff (the fields no model computes). Conditions come over as text only:
    metric, op and threshold stay null until someone types them."""
    out = {'imported_from_handoff': True}
    if isinstance(h.get('price_time_utc'), str):
        out['price_time_utc'] = h['price_time_utc']
    if h.get('currency'):
        out['currency'] = h['currency']
    if h.get('share_basis'):
        out['share_basis'] = h['share_basis'] if isinstance(h['share_basis'], str) else None
    sc = h.get('scenarios') or {}
    out['scenarios'] = {k: (v.get('he') or v.get('condition_he')) for k, v in sc.items() if isinstance(v, dict)}
    out['verdict'] = {'text_he': h.get('verdict_he') or '', 'code': None}
    conds, fals = [], []
    for key, items in (h.get('conditions') or {}).items():
        if not isinstance(items, list):
            continue
        kind = _kind_of(key)
        rows = [{'kind': kind, 'text_he': x if isinstance(x, str) else json.dumps(x, ensure_ascii=False),
                 'note': f'imported from the v1 handoff key conditions.{key}; not typed'} for x in items]
        (fals if kind == 'exit' else conds).extend(rows)
    out['conditions'], out['falsifiers'] = conds, fals
    hm = (h.get('values') or {}).get('historical_multiple') or (h.get('values') or {}).get('hist_multiple_today')
    if isinstance(hm, dict):
        val = next((hm[k] for k in ('value', 'base', 'median', 'mid') if is_number(hm.get(k))), None)
        rng = hm.get('range') if isinstance(hm.get('range'), list) and len(hm.get('range')) == 2 else [hm.get('low'), hm.get('high')]
        if val is not None:
            out['historical_multiple'] = {'value': val, 'low': rng[0] if is_number(rng[0]) else None, 'high': rng[1] if is_number(rng[1]) else None,
                                          'note': hm.get('basis') or hm.get('basis_he') or hm.get('note_he') or 'numbers as published in the v1 handoff'}
    out['next_report'] = h.get('next_report') or {}
    out['notes_he'] = [x for x in (h.get('notes_he') or []) if isinstance(x, str)]
    return out


def _next_report(nr):
    nr = nr if isinstance(nr, dict) else {'date': nr} if nr else {}
    text = nr.get('text') or (str(nr['date']) if nr.get('date') else None)
    out = {'date': None, 'window': None, 'confirmed': bool(nr.get('confirmed')), 'text': text,
           'check_he': nr.get('check_he') or nr.get('check')}
    w = lean_freeze.report_window(nr) if nr.get('date') else None
    if w:
        if w[0] == w[1]:
            out['date'] = w[0].isoformat()
        else:
            out['window'] = {'from': w[0].isoformat(), 'to': w[1].isoformat()}
    elif isinstance(nr.get('window'), dict):
        out['window'] = nr['window']
    if out['date'] is None and out['window'] is None:
        out['note'] = 'no date could be read' if text else 'no next report was given'
    return out


def _hist_multiple(hm):
    """The historical-multiple part: computed by locked_rules when the inputs are given, else the numbers as given."""
    out = {'label_he': LABELS_HE['hist'], 'company_type': None, 'basis': None, 'next_year_figure': None, 'median': None,
           'p25': None, 'p75': None, 'months': None, 'net_cash_per_share': None, 'value': None, 'low': None, 'high': None,
           'source': None, 'note': None}
    if not hm:
        out['note'] = 'not supplied: the historical-multiple report is not in this record'
        return out
    for k in ('company_type', 'basis', 'next_year_figure', 'median', 'p25', 'p75', 'months', 'net_cash_per_share', 'value',
              'low', 'high', 'source', 'note', 'note_he'):
        if hm.get(k) is not None:
            out[k] = hm[k]
    if hm.get('company_type') and all(is_number(hm.get(k)) for k in ('next_year_figure', 'median', 'p25', 'p75')):
        spec = locked_rules.COMPANY_TYPES.get(hm['company_type'])
        if spec and spec['basis']:
            res = locked_rules.hist_multiple_value(
                hm['company_type'], hm['next_year_figure'],
                {'basis': spec['basis'], 'median': hm['median'], 'p25': hm['p25'], 'p75': hm['p75'], 'months': hm.get('months')},
                net_cash_per_share=hm.get('net_cash_per_share') or 0)
            out.update(basis=res['basis'], value=res['value'], low=res['low'], high=res['high'], computed=True)
    elif out['value'] is not None:
        out['computed'] = False
        out['note'] = out['note'] or 'value as supplied (not recomputed from a multiple history)'
    return out


# ------------------------------------------------------------------ assembling a record

def _display_points(as_of, year_ends):
    """Which dated points the published 'in one year' and 'in five years' cells show (date, source)."""
    target = _shift(as_of, 1)
    near = [(abs((d - target).days), d) for d in year_ends]
    one = min(near)[1] if near and min(near)[0] <= NEAR_YEAR_END_DAYS else None
    return ({'date': (one or target).isoformat(), 'source': 'fiscal_year_end' if one else 'as_of_plus_one_year'},
            {'date': max(year_ends).isoformat(), 'source': 'horizon_year_end'} if year_ends else None)


def _assemble(core, inputs):
    """core: the model's numbers in one shape; inputs: the judgement (thesis inputs). Returns the record."""
    inputs = inputs or {}
    as_of = _iso(core['as_of'])
    sc_in = inputs.get('scenarios') or {}
    scenarios = {}
    for name, s in core['scenarios'].items():
        cond = sc_in.get(name)
        cond = cond.get('he') or cond.get('condition_he') if isinstance(cond, dict) else cond
        scenarios[name] = {'probability': _num(s.get('probability')), 'condition_he': cond or None,
                           'value_today': _num(s.get('value_today')), 'annual_return_pct': _num(s.get('annual_return_pct')),
                           'wiped': bool(s.get('wiped')), 'dated_values': s.get('dated_values') or [], 'at': s.get('at') or {}}
        if s.get('note'):
            scenarios[name]['note'] = s['note']
    ends = [_iso(r['date']) for r in scenarios['base']['dated_values'] if _iso(r.get('date'))]
    one, five = _display_points(as_of, ends) if as_of else (None, None)
    ks = inputs.get('known_split')
    sb = inputs.get('share_basis')
    v = inputs.get('verdict') or {}
    v = {'text_he': v} if isinstance(v, str) else v
    rec = {
        'schema': SCHEMA, 'ticker': core['ticker'], 'company': inputs.get('company') or core.get('company'),
        'as_of': core['as_of'],
        'price': {'value': core['price'], 'time_utc': inputs.get('price_time_utc'), 'source': inputs.get('price_source') or core.get('price_source')},
        'currency': inputs.get('currency') or core.get('currency') or 'USD',
        'share_basis': {'basis': sb if isinstance(sb, str) else (sb or {}).get('basis') if isinstance(sb, dict) else None,
                        'model_shares_m': _num(core.get('shares_m')), 'known_split': ks or None},
        'model': core['model'],
        'scenarios': scenarios,
        'probability_label': 'subjective',
        'weighted': core.get('weighted') or {'value_today': None, 'note': 'not computed'},
        'second_lens': core.get('second_lens') or {'value_today': None, 'note': 'no second lens in this model'},
        'base_path': core.get('base_path') or {'years': {}, 'note': 'this model saves no yearly path'},
        'hurdles': core['hurdles'],
        'display': {'one_year': one, 'five_year': five,
                    'rule': f'in one year = the fiscal year-end within {NEAR_YEAR_END_DAYS} days of as_of + 1 year, else that '
                            'exact day (log-linear between the dated values); in five years = the horizon year-end the '
                            'returns are computed to'},
        'historical_multiple': _hist_multiple(inputs.get('historical_multiple')),
        'conditions': _typed(inputs.get('conditions'), 'add', 'c'),
        'falsifiers': _typed(inputs.get('falsifiers'), 'exit', 'f'),
        'next_report': _next_report(inputs.get('next_report')),
        'last_fresh_pull': {'date': (inputs.get('last_fresh_pull') or {}).get('date') if isinstance(inputs.get('last_fresh_pull'), dict)
                            else inputs.get('last_fresh_pull'),
                            'note': (inputs.get('last_fresh_pull') or {}).get('note') if isinstance(inputs.get('last_fresh_pull'), dict) else None},
        'verdict': {'code': v.get('code'), 'text_he': v.get('text_he') or '', 'draft': bool(v.get('draft'))},
        'model_reading': core.get('model_reading') or {},
        'notes_he': list(inputs.get('notes_he') or []),
        'thesis_link': f'research/companies/{core["ticker"]}/thesis_he.md',
        'warnings': list(core.get('warnings') or []),
    }
    if rec['last_fresh_pull']['date'] is None:
        rec['last_fresh_pull']['note'] = rec['last_fresh_pull']['note'] or 'not recorded'
    if core.get('variants'):
        rec['variants'] = core['variants']
    if inputs.get('index'):
        rec['index_texts'] = inputs['index']
    if inputs.get('imported_from_handoff'):
        rec['judgement_source'] = 'imported from the v1 handoff of the same valuation (texts only)'
    # the entry price: a ladder rung by its id, the fixed 13% rung unless the inputs name another
    want = (inputs.get('entry_price') or {}).get('rung') or 'margin_of_safety_13'
    prices = rung_prices(rec)
    if want not in prices:
        want = next((k for k in ('margin_of_safety_13', 'buy_test.price', 'buy_test') if k in prices), None)
    rec['hurdles']['entry_price'] = {'value': prices.get(want), 'rung': want,
                                     'note': None if want else 'the model has no ladder: no entry price'}
    return rec


def _hurdles(rungs, buy_test, horizon_date, years, note=None):
    by = {r['id']: r for r in rungs}

    def price(rid):
        return (by.get(rid) or {}).get('price')
    h = {'horizon_date': horizon_date, 'years_to_horizon': years, 'rungs': rungs, 'buy_test': buy_test,
         'buy_test_price': buy_test.get('price'), 'base_12_price': price('buy_test'),
         'margin_of_safety_price': price('margin_of_safety_13'), 'index_parity_price': price('index'),
         'hold_floor_price': price('hold_floor'), 'sell_line_price': price('sell_line'),
         'labels_he': {'buy_test_price': LABELS_HE['buy_test'], 'margin_of_safety_price': LABELS_HE['margin_of_safety']}}
    if note:
        h['note'] = note
    return h


def _variants(folder, root, main='lean_params.json'):
    out = []
    for fp in sorted(glob.glob(os.path.join(folder, 'lean_params_*.json'))):
        name = os.path.basename(fp)
        res = os.path.join(folder, name.replace('lean_params', 'lean_result', 1))
        if name == main or name.endswith('_pre_review.json') or not os.path.isfile(res):
            continue
        base = ((load(res).get('scenarios') or {}).get('base') or {})
        out.append({'name': name[len('lean_params_'):-len('.json')], 'params': file_ref(fp, root), 'result': file_ref(res, root),
                    'base_value_today': _num(base.get('value_today')), 'base_annual_return_pct': _num(base.get('annual_return_pct')),
                    'weight': None, 'note': 'a named variant saved next to the main file; no weight is stored'})
    return out


def build_from_lean(params, result, inputs=None, files=None):
    """The record of a lean_model valuation. result must hold the reporting keys (lean_model.run of today's script)."""
    missing = [k for k in REPORTING_KEYS if k not in result]
    if missing:
        raise ValueError('the lean result has no ' + ', '.join(missing) + ': rerun the params with the current lean_model')
    worlds = result.get('worlds')
    if worlds:
        result = _weighted_view(result)
    dv, fund = result['dated_values'], (result.get('funding') or {}).get('scenarios') or {}
    if not dv.get('available'):
        raise ValueError('the lean result carries no dates (as_of missing or not an ISO date): a record needs them')
    probs = params.get('probabilities') or {}
    scen = {}
    for name, s in result['scenarios'].items():
        f = fund.get(name) or {}
        wiped = bool(f.get('equity_wiped')) or dv['today'].get(name) == 0
        scen[name] = {
            'probability': probs.get(name), 'wiped': wiped,
            'value_today': 0.0 if wiped else s['value_today'],
            'annual_return_pct': None if wiped or f.get('equity_wiped_year_ends') else s.get('annual_return_pct'),
            'dated_values': [{'fiscal_year': int(y), 'date': row['date'], 'value': row.get(name)} for y, row in sorted(dv['year_ends'].items())],
            'at': {k: {'date': row['date'], 'value': row.get(name), 'return_pct': (row.get('return_pct') or {}).get(name)}
                   for k, row in dv['at'].items()},
            'note': ('equity wiped in this scenario: value 0 (the model result floors it at 0.01), no return' if wiped else
                     f'equity wiped at the year-ends {f["equity_wiped_year_ends"]}: no return' if f.get('equity_wiped_year_ends') else None)}
    cal, diag = result['two_lens']['calibrated'], result.get('calibrated_diagnostics') or {}
    lens = {'value_today': _num(cal.get('value_today')), 'annual_return_pct': _num(cal.get('annual_return_pct')),
            'band': cal.get('band'), 'band_ours': result['two_lens']['ours'].get('band'), 'agree': result['two_lens'].get('agree'),
            'pe_held': _num(cal.get('pe_used')), 'margin_overridden': cal.get('margin_overridden'),
            'dated_values': [{'fiscal_year': int(y), 'date': row['date'], 'value': row.get('base_calibrated')}
                             for y, row in sorted(dv['year_ends'].items())],
            'at': {k: {'date': row['date'], 'value': row.get('base_calibrated'), 'return_pct': (row.get('return_pct') or {}).get('base_calibrated')}
                   for k, row in dv['at'].items()},
            'diagnostics': {k: diag.get(k) for k in ('margin_floor_binds', 'differs_from_base_only_by_multiple', 'pe_held',
                                                     'pe_base_exit', 'pe_held_to_base_exit', 'inflated_multiple_warning')},
            'diagnostics_note': '; '.join([diag.get('reading') or ''] + list(diag.get('warning_reasons') or [])).strip('; ') or None}
    base = result['scenarios']['base']
    years = {}
    for y in sorted(base.get('op_margin') or {}, key=int):
        end = (dv['year_ends'].get(str(y)) or dv['year_ends'].get(y) or {}).get('date')
        if end is None and dv.get('base_year_end'):
            end = _shift(_iso(dv['base_year_end']), int(y) - int(params['base_year'])).isoformat()
        years[str(y)] = {'fiscal_year_end': end, 'revenue_m': _num((base.get('revenue_m') or {}).get(y)),
                         'op_margin': _num(base['op_margin'].get(y)), 'eps_operating': _num((base.get('eps_operating') or {}).get(y))}
    remapped = 'earnings model' in str((params.get('notes') or {}).get('basis', '')).lower()
    path = {'basis': {'revenue_m': 'net income (remapped file)' if remapped else 'reported revenue',
                      'op_margin': 'operating income after SBC / revenue', 'eps_operating': lean_freeze.EPS_BASIS},
            'base_year': params['base_year'], 'base_year_end': dv.get('base_year_end'),
            'base_year_revenue_m': _num(params.get('rev0_m')), 'years': years}
    lad, bt = result['price_ladder'], result['buy_test']
    names = {'margin_of_safety_13': 'margin_of_safety_13', 'buy_test': 'buy_test', 'margin_of_safety': 'company_hurdle',
             'index': 'index', 'hold_floor': 'hold_floor', 'sell_line': 'sell_line'}
    rungs = [{'id': names.get(k, k), 'return_pct': _num(r.get('return_pct')), 'price': _num(r.get('ours')),
              'price_second_lens': _num(r.get('calibrated')), 'label': r.get('label')} for k, r in lad['rungs'].items()]
    rerun = bt.get('rerun') or {}
    warns = bool(rerun.get('lower_than_stated'))
    test = {'price': rerun.get('max_price_passing') if warns else bt.get('max_price_passing'),
            'stated_price': bt.get('max_price_passing'), 'rerun_price': rerun.get('max_price_passing'), 'rerun_used': warns,
            'binding_leg': rerun.get('binding_leg') if warns else bt.get('binding_leg'),
            'pass_at_price': bt.get('pass'), 'complete': bt.get('complete'),
            'legs': {k: {'return_pct': _num(v.get('return_pct')), 'threshold_pct': _num(v.get('threshold_pct')), 'pass': v.get('pass'),
                         'max_price': _num(v.get('max_price'))} for k, v in bt['legs'].items()},
            'rule': bt.get('rule'),
            'note': (bt.get('no_price_passes') or ('the model warns that a rerun at the stated price fails the second-lens leg: '
                                                   'the rerun price is used' if warns else None))}
    core = {
        'ticker': params['ticker'], 'as_of': params['as_of'], 'price': params['price'], 'shares_m': params.get('shares_m'),
        'model': {'kind': 'lean_remapped' if remapped else 'lean', 'reproducible': True, **(files or {})},
        'scenarios': scen,
        'weighted': {'value_today': _num(result.get('weighted_value_today')), 'end_value': _num(result.get('weighted_end_value')),
                     'annual_return_pct': _num(result.get('weighted_return_pct')), 'index_return_pct': _num(result.get('index_return_pct'))},
        'second_lens': lens, 'base_path': path,
        'hurdles': _hurdles(rungs, test, lad.get('horizon_year') and dv['year_ends'][str(lad['horizon_year'])]['date'],
                            _num(lad.get('years_to_horizon'))),
        'model_reading': {'band_ours': result['two_lens']['ours'].get('band'), 'band_second_lens': cal.get('band'),
                          'lenses_agree': result['two_lens'].get('agree'), 'buy_test_pass': bt.get('pass'),
                          'moves_most': (result.get('sensitivity') or {}).get('moves_most'),
                          'method_gap_warnings': len((result.get('method_gap') or {}).get('warnings') or [])},
        'warnings': result.get('warnings') or [],
    }
    rec = _assemble(core, inputs)
    if worlds:
        rec['worlds'] = _worlds_block(worlds)
        main = rec['worlds']['worlds'][0] if rec['worlds']['worlds'] else {}
        rec['base_path']['note'] = (f'the yearly path of the main file ({main.get("name")}, weight {main.get("weight")}): the weighted '
                                    'values have no single path')
        rec['model_reading']['worlds'] = len(rec['worlds']['worlds'])
    return rec


def worlds_spec(folder, params):
    """(worlds spec, folder its files are relative to) of a lean valuation: the params' own "worlds" key, else a
    worlds.json beside them; (None, None) when the valuation has no weighted worlds."""
    if isinstance(params, dict) and params.get('worlds') is not None:
        return params['worlds'], folder
    fp = os.path.join(folder, WORLDS_NAME)
    return (load(fp), folder) if os.path.isfile(fp) else (None, None)


def _weighted_view(result):
    """The lean result reshaped to its weighted worlds (result['worlds']['weighted']): the keys build_from_lean reads.
    The yearly path, sensitivity and funding stay the main file's (the weighted values have no single path)."""
    w, dv = result['worlds']['weighted'], result['dated_values']
    names = [k for k in w['value_today'] if k != 'base_calibrated']
    scen = {k: dict(result['scenarios'].get(k) or {}, value_today=w['value_today'][k], annual_return_pct=w['annual_return_pct'][k])
            for k in names}
    view_dv = dict(dv, today=dict({'date': dv['today'].get('date')}, **w['value_today']),
                   year_ends={y: dict({'date': dv['year_ends'][y]['date']}, **vals) for y, vals in w['year_end_values'].items()},
                   at={k: dict(v) for k, v in w['at'].items()})
    tl = w.get('two_lens') or {}
    lens = {'ours': {'value_today': w['value_today'].get('base'), 'annual_return_pct': w['annual_return_pct'].get('base'),
                     'band': tl.get('ours')},
            'calibrated': {'value_today': w['value_today'].get('base_calibrated'),
                           'annual_return_pct': w['annual_return_pct'].get('base_calibrated'), 'band': tl.get('calibrated'),
                           'pe_used': None, 'margin_overridden': None},
            'agree': tl.get('agree')}
    out = dict(result, scenarios=scen, dated_values=view_dv, two_lens=lens, price_ladder=w['price_ladder'], buy_test=w['buy_test'],
               weighted_value_today=w.get('probability_weighted_value_today'), weighted_end_value=None, weighted_return_pct=None,
               calibrated_diagnostics={'reading': 'per world: the second lens of each world holds its own multiple'},
               funding={'scenarios': {}},
               warnings=list(result.get('warnings') or []) + list(result['worlds'].get('warnings') or []))
    return out


def _worlds_block(worlds):
    """The record's worlds part: the weights (judgement, not engine odds) and each world's own numbers."""
    rows = []
    for x in worlds.get('worlds') or []:
        bt, m13 = x.get('buy_test') or {}, x.get('margin_of_safety_13') or {}
        rows.append({'name': x['name'], 'label_he': x.get('label_he'), 'weight': x.get('weight'),
                     'params': {'path': x.get('file'), 'sha256': x.get('sha256')},
                     'value_today': x.get('value_today'), 'annual_return_pct': x.get('annual_return_pct'),
                     'buy_test_price': bt.get('rerun_max_price_passing', bt.get('max_price_passing')),
                     'buy_test_pass': bt.get('pass'), 'margin_of_safety_price': m13.get('ours'),
                     'margin_of_safety_price_second_lens': m13.get('calibrated'), 'base_band': x.get('base_band'),
                     'two_lens_agree': x.get('two_lens_agree')})
    return {'source': worlds.get('source'), 'weights_label': 'subjective (judgement, not engine odds)',
            'note': worlds.get('note'), 'worlds': rows,
            'weighted_value_today': (worlds.get('weighted') or {}).get('value_today'),
            'weighted_annual_return_pct': (worlds.get('weighted') or {}).get('annual_return_pct')}


def _custom_dates(as_of, days, base_year):
    end0 = _iso(as_of) + timedelta(days=days)
    return lambda y: _shift(end0, int(y) - base_year).isoformat()


def _custom_scenario(as_of, price, prob, today, year_ends, ret, date_of):
    """A scenario from a custom model: year_ends {label: value}; the +1/+2/+5 values by the tracker's interpolation."""
    a = _iso(as_of)
    rows = [{'fiscal_year': int(y), 'date': date_of(y), 'value': _num(v)} for y, v in sorted(year_ends.items(), key=lambda kv: int(kv[0]))]
    pts = [(a, today)] + [(_iso(r['date']), r['value']) for r in rows]
    at = {}
    for key, n in (('one_year', 1), ('two_year', 2), ('five_year', 5)):
        when = _shift(a, n)
        val = _interp(pts, when)
        at[key] = {'date': when.isoformat(), 'value': None if val is None else round(val, 2),
                   'return_pct': None if val is None or not price else round(((val / price) ** (1 / n) - 1) * 100, 1)}
    wiped = not is_number(today) or today <= 0
    return {'probability': prob, 'value_today': 0.0 if wiped else today, 'annual_return_pct': None if wiped else ret,
            'wiped': wiped, 'dated_values': rows, 'at': at}


def _simple_rungs(pairs):
    """[(id, return_pct, price, second-lens price)] -> rung rows."""
    return [{'id': i, 'return_pct': r, 'price': _num(a), 'price_second_lens': _num(b), 'label': None} for i, r, a, b in pairs]


def adapt_bank(params, result, inputs=None, files=None):
    """BNY's bank model (bank_model.py: residual income on tangible equity + a P/E path)."""
    date_of = _custom_dates(params['as_of'], params['days_to_base_year_end'], 2026)
    pr = params['probabilities']
    scen = {k: _custom_scenario(params['as_of'], params['price'], pr.get(k), s['value_today'], s['year_end_values'],
                                s['annual_return_pct'], date_of) for k, s in result['scenarios'].items()}
    cal, ep = result['calibrated'], result.get('entry_prices') or {}
    base = result['scenarios']['base']
    rungs = _simple_rungs([('buy_test', 12.0, ep.get('base_12'), ep.get('calibrated_12'))])
    test = {'price': _num(result.get('entry_price_all_tests')), 'stated_price': _num(result.get('entry_price_all_tests')),
            'rerun_price': None, 'rerun_used': False,
            'binding_leg': min((k for k in ep if is_number(ep[k])), key=ep.get, default=None), 'pass_at_price': None, 'complete': True,
            'legs': {k: {'return_pct': None, 'threshold_pct': None, 'pass': None, 'max_price': _num(v)} for k, v in ep.items()},
            'rule': 'the bank model\'s entry_prices: the lowest of the four leg prices',
            'note': 'returns include dividends; the bank model saves no 13%, index, 6% or 3% rung'}
    core = {
        'ticker': params['ticker'], 'company': params.get('company'), 'as_of': params['as_of'], 'price': params['price'],
        'price_source': params.get('price_source'), 'shares_m': params.get('shares_q3_m'),
        'model': {'kind': 'bank', 'reproducible': True, **(files or {})},
        'scenarios': scen,
        'weighted': {'value_today': _num(result.get('weighted_value_today')), 'end_value': None, 'annual_return_pct': None,
                     'index_return_pct': _num(result.get('index_return_pct')), 'note': 'the bank model saves the weighted value today only'},
        'second_lens': {'value_today': _num(cal.get('value_today')), 'annual_return_pct': _num(cal.get('annual_return_pct')),
                        'band': cal.get('band'), 'band_ours': result['two_lens']['ours'].get('band'), 'agree': result['two_lens'].get('agree'),
                        'pe_held': _num(cal.get('pe_used')),
                        'dated_values': [{'fiscal_year': int(y), 'date': date_of(y), 'value': v} for y, v in sorted(cal['year_end_values'].items())],
                        'diagnostics_note': cal.get('why')},
        'base_path': {'basis': {'eps': 'EPS to common', 'tbvps': 'tangible book value per share', 'rotce': 'return on tangible common equity'},
                      'base_year': 2026, 'years': {str(y): {'fiscal_year_end': date_of(y), 'eps': _num(base['eps'].get(y)),
                                                              'tbvps': _num(base['tbvps'].get(y)), 'rotce': _num(base['rotce'].get(y)),
                                                              'revenue_m': None, 'op_margin': None, 'eps_operating': None}
                                                     for y in sorted(base['rotce'], key=int)},
                      'note': 'a bank has no revenue / operating-margin path: EPS, TBVPS and ROTCE instead'},
        'hurdles': _hurdles(rungs, test, date_of(2031), _num(params.get('years_to_horizon')),
                            'margin_of_safety, index, hold-floor and sell-line prices are null: the bank model does not compute them'),
        'model_reading': {'band_ours': result['two_lens']['ours'].get('band'), 'band_second_lens': cal.get('band'),
                          'lenses_agree': result['two_lens'].get('agree')},
    }
    inputs = dict(inputs or {})
    inputs.setdefault('entry_price', {'rung': 'buy_test.price'})
    return _assemble(core, inputs)


def adapt_segment(params, result, inputs=None, files=None):
    """SPCX's segment model (model.py: three segments, capex vintages)."""
    date_of = _custom_dates(params['as_of'], params['days_to_base_year_end'], 2026)
    pr = params['probabilities']
    scen = {k: _custom_scenario(params['as_of'], params['price'], pr.get(k), s['value_today'], s['year_end_values'],
                                s['annual_return_pct'], date_of) for k, s in result['scenarios'].items()}
    cal, base, bt = result['calibrated'], result['scenarios']['base'], result.get('buy_test') or {}
    rungs = _simple_rungs([('buy_test', 12.0, result.get('price_for_12pct'), None),
                           ('index', _num(result.get('index_return_pct')), result.get('index_parity_price'), None)])
    test = {'price': None, 'stated_price': None, 'rerun_price': None, 'rerun_used': False, 'binding_leg': None,
            'pass_at_price': all(bt.values()) if bt else None, 'complete': bool(bt), 'legs': {},
            'leg_results': bt, 'rule': 'base >= 12% in both lenses, bull >= 17%, bear >= -10%',
            'note': 'the segment model saves pass/fail per leg and the base 12% price only; no all-legs price'}
    core = {
        'ticker': params['ticker'], 'as_of': params['as_of'], 'price': params['price'], 'shares_m': params.get('shares_m'),
        'model': {'kind': 'segment', 'reproducible': True, **(files or {})},
        'scenarios': scen,
        'weighted': {'value_today': _num(result.get('weighted_value_today')), 'end_value': _num(result.get('weighted_end_2031')),
                     'annual_return_pct': _num(result.get('weighted_return_pct')), 'index_return_pct': _num(result.get('index_return_pct'))},
        'second_lens': {'value_today': _num(cal.get('value_today')), 'annual_return_pct': _num(cal.get('annual_return_pct')),
                        'band': result['two_lens']['calibrated'].get('band'), 'band_ours': result['two_lens']['ours'].get('band'),
                        'agree': result['two_lens'].get('agree'), 'pe_held': _num(cal.get('pe_used')),
                        'dated_values': [{'fiscal_year': int(y), 'date': date_of(y), 'value': v} for y, v in sorted(cal['year_end_values'].items())],
                        'diagnostics_note': cal.get('why')},
        'base_path': {'basis': {'revenue_m': 'group revenue (the model works in $bn; shown here in $M)', 'op_margin': 'EBIT / revenue',
                                'eps_operating': 'operating EPS as the model reports it'},
                      'base_year': 2026,
                      'years': {str(y): {'fiscal_year_end': date_of(y), 'revenue_m': _num(row.get('rev') * 1000 if is_number(row.get('rev')) else None, 1),
                                         'op_margin': _num(row.get('ebit_m')), 'eps_operating': _num(row.get('eps_operating') or row.get('eps'))}
                                for y, row in sorted(base.get('path', {}).items(), key=lambda kv: int(kv[0]))}},
        'hurdles': _hurdles(rungs, test, date_of(2031), _num(params.get('years_to_horizon')),
                            'margin_of_safety, hold-floor and sell-line prices are null: the segment model does not compute them'),
        'model_reading': {'band_ours': result['two_lens']['ours'].get('band'), 'band_second_lens': result['two_lens']['calibrated'].get('band'),
                          'lenses_agree': result['two_lens'].get('agree'), 'buy_test_pass': all(bt.values()) if bt else None},
    }
    inputs = dict(inputs or {})
    inputs.setdefault('entry_price', {'rung': 'buy_test'})
    return _assemble(core, inputs)


def adapt_three_lane(ticker, as_of, model_result, finish_result=None, inputs=None, files=None):
    """META's three-lane model (model/: DCF, P/E and reverse lanes averaged; ladder at whole dollars)."""
    cfg = model_result['config']
    days = round((cfg['T31'] - 5) * 365)
    date_of = _custom_dates(as_of, days, 2026)
    w = {'bear': 0.25, 'base': 0.5, 'bull': 0.25}
    pw = model_result.get('probability_weighted') or {}
    sc = model_result['scenarios']
    got = sum(w[k] * sc[k]['today'] for k in w)
    probs = w if abs(got - pw.get('today', got)) < 0.06 else {k: None for k in w}
    scen = {k: _custom_scenario(as_of, cfg['price'], probs.get(k), s['today'], s['ye'], s['return_pct'], date_of) for k, s in sc.items()}
    lenses = model_result['lenses']
    lad = lambda name: lenses[name]['ladder']   # noqa: E731
    f, c = lad('fundamental'), lad('calibrated')
    idx = next(k for k in f if k not in ('12%', '6%', '3%'))
    rungs = _simple_rungs([('buy_test', 12.0, f['12%'], c['12%']), ('index', float(idx.rstrip('%')), f[idx], c[idx]),
                           ('hold_floor', 6.0, f['6%'], c['6%']), ('sell_line', 3.0, f['3%'], c['3%'])])
    add = (model_result.get('combined') or {}).get('fundamental_and_calibrated', {}).get('add_both')
    test = {'price': _num(add), 'stated_price': _num(add), 'rerun_price': None, 'rerun_used': False, 'binding_leg': 'base_ours',
            'pass_at_price': False, 'complete': True,
            'legs': {'base_ours': {'return_pct': sc['base']['return_pct'], 'threshold_pct': 12.0, 'pass': sc['base']['return_pct'] >= 12, 'max_price': _num(f['12%'])},
                     'base_calibrated': {'return_pct': lenses['calibrated']['return_pct'], 'threshold_pct': 12.0,
                                         'pass': lenses['calibrated']['return_pct'] >= 12, 'max_price': _num(c['12%'])}},
            'at_add_price': model_result.get('buy_test_at_fundamental_add_price'),
            'rule': 'base >= 12% in both lenses (the add price); bull and bear tested at that price',
            'note': 'the ladder is saved at whole dollars; no 13% rung'}
    core = {
        'ticker': ticker, 'as_of': as_of, 'price': cfg['price'],
        'model': {'kind': 'three_lane', 'reproducible': True, **(files or {})},
        'scenarios': scen,
        'weighted': {'value_today': _num(pw.get('today')), 'end_value': _num(pw.get('end_2031')), 'annual_return_pct': _num(pw.get('return_pct')),
                     'index_return_pct': float(idx.rstrip('%'))},
        'second_lens': {'value_today': None, 'annual_return_pct': _num(lenses['calibrated']['return_pct']), 'band': lenses['calibrated']['band'],
                        'band_ours': lenses['fundamental']['band'], 'agree': lenses['calibrated']['band'] == lenses['fundamental']['band'],
                        'pe_held': _num(lenses.get('pe_today_held')),
                        'dated_values': [{'fiscal_year': 2031, 'date': date_of(2031), 'value': _num(lenses['calibrated']['end_2031'])}],
                        'depreciation_wave_line': {'end_value': _num(lenses['calibrated_dep_wave_line']['end_2031']),
                                                   'annual_return_pct': _num(lenses['calibrated_dep_wave_line']['return_pct'])},
                        'note': 'the model saves the second lens at the horizon only: no value today'},
        'base_path': {'basis': {'eps': 'EPS per lane (dcf / pe / rev), 2027 and 2032 only'}, 'base_year': 2026,
                      'years': {y: {'fiscal_year_end': date_of(int(y)), 'revenue_m': None, 'op_margin': None, 'eps_operating': None,
                                    'eps_by_lane': {ln: _num(model_result['lanes'][ln]['base'].get('eps' + y[2:])) for ln in model_result['lanes']}}
                                for y in ('2027', '2032')},
                      'note': 'no revenue or margin path is saved by this model'},
        'hurdles': _hurdles(rungs, test, date_of(2031), _num(cfg['T31']),
                            'no 13% rung in this model: margin_of_safety_price is null; prices are at whole dollars'),
        'model_reading': {'band_ours': lenses['fundamental']['band'], 'band_second_lens': lenses['calibrated']['band'],
                          'lenses_agree': lenses['calibrated']['band'] == lenses['fundamental']['band']},
    }
    if finish_result and finish_result.get('post_split'):
        core['model_reading']['post_split_ladder'] = finish_result['post_split'].get('rule')
    inputs = dict(inputs or {})
    inputs.setdefault('entry_price', {'rung': 'buy_test.price'})
    return _assemble(core, inputs)


# ------------------------------------------------------------------ folders

def _files(folder, root, params, result, code):
    repo = os.path.dirname(root)
    code_path = os.path.join(folder, code) if code and os.path.isfile(os.path.join(folder, code)) else os.path.join(repo, code) if code else None
    return {'params': file_ref(os.path.join(folder, params), repo) if params else None,
            'result': file_ref(os.path.join(folder, result), repo) if result else None,
            'code': file_ref(code_path, repo) if code_path else None}


def record_for_folder(folder, root, inputs=None, result=None, run=None):
    """(record, spec) built from a dated valuation folder; record None when the folder holds no lean or custom valuation.

    root: the research folder. result: a fresh lean_model.run result to use instead of the saved lean_result.json.
    run: lean_model.run; used only when the saved lean result was written before the reporting keys existed (the
    saved keys keep their values in a rerun: tests/test_saved_valuations.py)."""
    spec = lean_freeze.classify(folder)
    t, d = os.path.basename(os.path.dirname(folder)), os.path.basename(folder)
    if spec['kind'] is None:
        return None, spec
    if spec['kind'] == 'lean' or spec.get('model') == 'remapped_lean':
        params = load(os.path.join(folder, spec['params']))
        res = result if result is not None else load(os.path.join(folder, spec['result']))
        if run is not None and result is None and (any(k not in res for k in REPORTING_KEYS)
                                                   or 'margin_of_safety_13' not in res['price_ladder'].get('rungs', {})
                                                   or 'rerun' not in res['buy_test'] and 'no_price_passes' not in res['buy_test']):
            # a result saved before today's reporting keys: the rerun supplies them, the saved model values stay
            res = dict(json.loads(json.dumps(run(params))), **{k: res[k] for k in SAVED_VALUE_KEYS if k in res})
        wspec, wdir = worlds_spec(folder, params) if spec['kind'] == 'lean' else (None, None)
        if wspec is not None and 'worlds' not in res:
            # the weighted worlds are computed, never saved into lean_result.json (its keys are the single file's)
            res = dict(res, worlds=json.loads(json.dumps((run or default_run())(params, worlds=wspec, base_dir=wdir)))['worlds'])
        files = _files(folder, root, spec['params'], spec['result'], 'scripts/lean_model.py')
        if wspec is not None and 'worlds' not in params:
            files['worlds'] = file_ref(os.path.join(folder, WORLDS_NAME), os.path.dirname(root))
        rec = build_from_lean(params, res, inputs, files)
        var = _variants(folder, os.path.dirname(root))
        if var:
            rec['variants'] = var
            if wspec is not None:
                weights = {os.path.basename(x['params']['path'] or ''): x['weight'] for x in rec['worlds']['worlds']}
                for v in var:
                    name = os.path.basename(v['params']['path'])
                    v['weight'] = weights.get(name, 0.0)
                    v['note'] = ('a world of the weighted value (worlds.json)' if name in weights else
                                 'saved next to the main file, not one of the worlds in force (weight 0)')
                rec['warnings'].append(f'the values are the weighted worlds of {WORLDS_NAME} ({len(rec["worlds"]["worlds"])} worlds); '
                                       'the yearly path is the main file\'s')
            else:
                rec['warnings'].append(f'{len(var)} named variant file(s) next to the main file: the record holds the main file only, '
                                       'no weighted value over the variants is computed')
    elif spec['model'] == 'bank':
        rec = adapt_bank(load(os.path.join(folder, spec['params'])), load(os.path.join(folder, spec['result'])), inputs,
                         _files(folder, root, spec['params'], spec['result'], 'bank_model.py'))
    elif spec['model'] == 'segment':
        rec = adapt_segment(load(os.path.join(folder, spec['params'])), load(os.path.join(folder, spec['result'])), inputs,
                            _files(folder, root, spec['params'], spec['result'], 'model.py'))
    elif spec['model'] == 'three_lane':
        fin = os.path.join(folder, 'finish_result.json')
        rec = adapt_three_lane(t, d, load(os.path.join(folder, spec['result'])), load(fin) if os.path.isfile(fin) else None, inputs,
                               _files(folder, root, 'model/dcf_params.json', spec['result'], 'model/model13.py'))
    else:
        return None, dict(spec, kind=None, reason=f'a custom model without an adapter ({spec.get("model")})')
    return rec, spec


def dated_folders(root, ticker):
    base = os.path.join(root, STORE, ticker)
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, d) for d in os.listdir(base) if DATED.match(d) and os.path.isdir(os.path.join(base, d)))


def latest_valuation(root, ticker, run=None):
    """The newest dated folder of a ticker, of any kind: {ticker, as_of, folder, kind, model, record, record_source, note}.

    root: the research folder. record: the saved valuation.json when the folder has one, else a record built in memory
    from the folder's lean or custom files (judgement fields empty), else None (heavy-engine or gather-only folder).
    run: lean_model.run, needed for lean results saved before the reporting keys existed (default: this checkout's
    scripts/lean_model.py). None when the ticker has no dated folder."""
    run = run or default_run()
    folders = dated_folders(root, ticker)
    if not folders:
        return None
    folder = folders[-1]
    out = {'ticker': ticker, 'as_of': os.path.basename(folder), 'folder': folder, 'kind': None, 'model': None, 'record': None,
           'record_source': None, 'note': None, 'older_folders': [os.path.basename(f) for f in folders[:-1]]}
    spec = lean_freeze.classify(folder)
    out['kind'], out['model'] = spec['kind'], spec.get('model')
    saved = os.path.join(folder, RECORD_NAME)
    if os.path.isfile(saved):
        out['record'], out['record_source'] = load(saved), RECORD_NAME
        return out
    if spec['kind'] is None:
        out['kind'] = 'engine' if os.path.isfile(os.path.join(folder, 'casefile_summary.json')) else 'gather_only'
        out['note'] = spec['reason']
        return out
    try:
        rec, _ = record_for_folder(folder, root, run=run)
        out['record'], out['record_source'] = rec, 'built in memory from the saved files (no valuation.json yet)'
    except (ValueError, KeyError) as e:
        out['note'] = f'no record could be built from the saved files: {e}'
    return out


# ------------------------------------------------------------------ projections

def _money(x, places=None, like=None):
    if x is None:
        return 'אין'
    if places is None:
        places = 2 if abs(x) < 30 else 0      # `like` is kept for callers; the decimals follow the value itself
    return f'{x:,.{places}f}$'


def _dmy(iso):
    d = _iso(iso)
    return f'{d.day}.{d.month}.{d.year}' if d else str(iso)


def _pct(x):
    if x is None:
        return 'אין'
    x = 0.0 if abs(x) < 0.05 else x
    return '\u200e' + (f'{x:+.1f}%' if x else '0.0%') + '\u200e'


def _value_on(rows, at, point):
    """The value of a scenario at a display point {date, source}."""
    if point is None:
        return None
    for r in rows or []:
        if r.get('date') == point['date']:
            return r.get('value')
    for row in (at or {}).values():
        if row.get('date') == point['date']:
            return row.get('value')
    return None


def display_values(record):
    """{'one_year'|'five_year': {date, bear, base, bull, second_lens}} as published."""
    out = {}
    for key in ('one_year', 'five_year'):
        pt = (record.get('display') or {}).get(key)
        if pt is None:
            out[key] = None
            continue
        row = {'date': pt['date'], 'source': pt['source']}
        for name, s in record['scenarios'].items():
            row[name] = _value_on(s.get('dated_values'), s.get('at'), pt)
        lens = record.get('second_lens') or {}
        row['second_lens'] = _value_on(lens.get('dated_values'), lens.get('at'), pt)
        out[key] = row
    return out


def project_handoff(record, previous=None):
    """research/technical/<T>/<date>/fundamental.json from a record: every v1 key with its v1 meaning, then the v2
    keys. previous: the handoff already at that path; its keys that this function does not write are kept as they are
    (the drawing session may read them) and listed in carried_from_previous."""
    sc, h, lens = record['scenarios'], record['hurdles'], record.get('second_lens') or {}
    order = [k for k in SCEN if k in sc] + [k for k in sc if k not in SCEN]
    values = {'today': {k: sc[k]['value_today'] for k in order}}
    for row in sc['base']['dated_values']:
        y = row['fiscal_year']
        values[f'end_{y}'] = {k: next((r['value'] for r in sc[k]['dated_values'] if r['fiscal_year'] == y), None) for k in order}
    probs = [sc[k]['probability'] for k in order]
    w = record.get('weighted') or {}
    if all(is_number(x) for x in probs) and w.get('value_today') is not None:
        values['weighted_today_' + '_'.join(f'{round(x * 100):g}' for x in probs)] = w['value_today']
    values['calibrated_today'] = lens.get('value_today')
    nr = record['next_report']
    conds = {'add': [], 'proposed_trim': [], 'proposed_exit': [], 'too_low': []}
    for c in record['conditions'] + record['falsifiers']:
        conds[{'add': 'add', 'trim': 'proposed_trim', 'exit': 'proposed_exit', 'too_low': 'too_low'}[c['kind']]].append(c['text_he'])
    prev_conds = (previous or {}).get('conditions')
    if isinstance(prev_conds, dict):
        # a text the previous file holds under another key (would_raise_value, watch, ...) stays under that key only:
        # the v1 lists add / proposed_trim / proposed_exit keep their meaning and gain nothing from a guessed kind
        for key in conds:
            own = [x for x in prev_conds.get(key) or [] if isinstance(x, str)] if isinstance(prev_conds.get(key), list) else []
            other = {x for k, v in prev_conds.items() if k != key and isinstance(v, list) for x in v if isinstance(x, str)}
            conds[key] = [t for t in conds[key] if t in own or t not in other]
    if not conds['too_low']:
        del conds['too_low']
    out = {
        'ticker': record['ticker'], 'as_of': record['as_of'], 'price': record['price']['value'],
        'price_time_utc': record['price'].get('time_utc'),
        'verdict_he': record['verdict']['text_he'],
        'buy_test_price': h['buy_test'].get('stated_price') if h['buy_test'].get('stated_price') is not None else h.get('base_12_price'),
        'margin_of_safety_price': h.get('margin_of_safety_price'),
        'values': values,
        'scenarios': dict({k: {'prob': sc[k]['probability'], 'he': sc[k]['condition_he']} for k in order}, label='subjective'),
        'annual_return_pct': dict({k: sc[k]['annual_return_pct'] for k in order}, calibrated=lens.get('annual_return_pct'),
                                  weighted=w.get('annual_return_pct'), index=w.get('index_return_pct')),
        'conditions': conds,
        'next_report': {'date': nr.get('text') or nr.get('date'), 'confirmed': nr['confirmed'], 'check': nr.get('check_he')},
        'thesis_link': record['thesis_link'],
        'notes_he': record.get('notes_he') or [],
        # ---- v2 (added; a v1 reader ignores them): one new object, so no key an older file already uses changes shape
        'schema_version': HANDOFF_SCHEMA_VERSION,
    }
    out['v2'] = {
        'currency': record['currency'],
        'share_basis': record['share_basis'].get('basis'),
        'known_split': record['share_basis'].get('known_split'),
        'model_kind': record['model']['kind'],
        'record': {'path': f'research/companies/{record["ticker"]}/{record["as_of"]}/{RECORD_NAME}'},
        'buy_test': {'price_all_legs': h.get('buy_test_price'), 'stated_price': h['buy_test'].get('stated_price'),
                     'rerun_price': h['buy_test'].get('rerun_price'), 'base_12_price': h.get('base_12_price'), 'binding_leg': h['buy_test'].get('binding_leg'),
                     'pass_at_price': h['buy_test'].get('pass_at_price'), 'rerun_used': h['buy_test'].get('rerun_used'),
                     'legs': h['buy_test'].get('legs'), 'note': h['buy_test'].get('note')},
        'price_ladder': {'horizon_date': h.get('horizon_date'), 'years_to_horizon': h.get('years_to_horizon'),
                         'rungs': [{k: r.get(k) for k in ('id', 'return_pct', 'price', 'price_second_lens')} for r in h.get('rungs') or []]},
        'entry_price': h.get('entry_price'),
        'index_parity_price': h.get('index_parity_price'),
        'hold_floor_price': h.get('hold_floor_price'),
        'sell_line_price': h.get('sell_line_price'),
        'values_dated': {
            'today': dict({k: sc[k]['value_today'] for k in order}, date=record['as_of'], base_calibrated=lens.get('value_today')),
            'year_ends': [dict({k: next((r['value'] for r in sc[k]['dated_values'] if r['fiscal_year'] == row['fiscal_year']), None) for k in order},
                               fiscal_year=row['fiscal_year'], date=row['date'],
                               base_calibrated=next((r['value'] for r in lens.get('dated_values') or [] if r['fiscal_year'] == row['fiscal_year']), None))
                          for row in sc['base']['dated_values']],
            'at': {key: dict({k: (sc[k]['at'].get(key) or {}).get('value') for k in order}, date=(sc['base']['at'].get(key) or {}).get('date'),
                             base_calibrated=((lens.get('at') or {}).get(key) or {}).get('value'))
                   for key in ('one_year', 'two_year', 'five_year') if key in sc['base']['at']},
            'published': display_values(record)},
        'wiped': {k: sc[k]['wiped'] for k in order},
        'second_lens': {'value_today': lens.get('value_today'), 'annual_return_pct': lens.get('annual_return_pct'), 'band': lens.get('band'),
                        'band_ours': lens.get('band_ours'), 'agree': lens.get('agree'),
                        'inflated_multiple_warning': (lens.get('diagnostics') or {}).get('inflated_multiple_warning'),
                        'note': lens.get('diagnostics_note') or lens.get('note')},
        'historical_multiple': {k: record['historical_multiple'].get(k) for k in ('value', 'low', 'high', 'basis', 'company_type', 'note')},
        'conditions_typed': record['conditions'],
        'falsifiers': record['falsifiers'],
        'last_fresh_pull': record['last_fresh_pull'],
        'verdict': record['verdict'],
        'next_report': {'date': nr.get('date'), 'window': nr.get('window'), 'confirmed': nr['confirmed']},
    }
    wd = record.get('worlds')
    if wd:
        # weighted worlds: the v1 entry prices stay a dict per world (the v1 shape of a multi-world handoff); the one
        # weighted number of each is under v2.buy_test / v2.entry_price and v2.worlds
        rows = wd.get('worlds') or []
        out['buy_test_price'] = {x['name']: x.get('buy_test_price') for x in rows}
        out['margin_of_safety_price'] = {x['name']: x.get('margin_of_safety_price') for x in rows}
        values['today_base_by_world'] = {f'{x["name"]}_{round((x.get("weight") or 0) * 100):g}pct': (x.get('value_today') or {}).get('base')
                                         for x in rows}
        values['weighted_base_today'] = sc['base']['value_today']
        values['today_note'] = ('bear, base and bull = the weighted values over the worlds (weight x value, weights are judgement): '
                                'see today_base_by_world and v2.worlds for each world')
        ar = out['annual_return_pct']
        ar['at_price'], ar['weighted_base'] = record['price']['value'], sc['base']['annual_return_pct']
        prev_ar = (previous or {}).get('annual_return_pct') if isinstance((previous or {}).get('annual_return_pct'), dict) else {}
        for x in rows:
            r = x.get('annual_return_pct') or {}
            key = next((k for k, v in prev_ar.items() if isinstance(v, dict) and x['name'].startswith(k)), x['name'])
            ar[key] = {'bear': r.get('bear'), 'base': r.get('base'), 'bull': r.get('bull'), 'calibrated': r.get('base_calibrated')}
        out['v2']['worlds'] = {'weights_label': wd.get('weights_label'), 'source': wd.get('source'),
                               'weighted': {'value_today': sc['base']['value_today'], 'annual_return_pct': sc['base']['annual_return_pct'],
                                            'buy_test_price': h.get('buy_test_price'), 'margin_of_safety_price': h.get('margin_of_safety_price')},
                               'worlds': rows}
    if previous:
        carried = []
        for k, v in previous.items():
            if k not in out:
                out[k] = v
                carried.append(k)
            elif k in V1_KEYS and isinstance(v, dict) and isinstance(out[k], dict):
                _carry(out[k], v, k, carried)
            elif k in V1_KEYS and isinstance(v, (dict, list)) and not isinstance(out[k], (dict, list)):
                out[k + '_carried'] = v           # the old file held another shape under a v1 key: kept beside it
                carried.append(k + '_carried')
        if isinstance(previous.get('thesis_note'), str) and previous.get('thesis_link') is None and out.get('thesis_link'):
            out['thesis_note'] = f'the thesis is {out["thesis_link"]}'   # the old note said there was none
            carried = [k for k in carried if k != 'thesis_note']
        if (isinstance(previous.get('margin_of_safety_note'), str) and previous.get('margin_of_safety_price') is None
                and out.get('margin_of_safety_price') is not None):
            # the old note explained a null that is now filled: it would contradict the number beside it
            out['margin_of_safety_note'] = ('the fixed 13% rung of the price ladder (one definition for every company since '
                                            '15.11.2026); the company\'s own hurdle price stays in price_ladder.rungs')
            carried = [k for k in carried if k != 'margin_of_safety_note']
        if carried:
            out['carried_from_previous'] = sorted(carried)
    return out


def _carry(new, old, path, carried):
    """Keys of the old dict that the new one does not write, at any depth, are kept."""
    for k, v in old.items():
        if k not in new:
            new[k] = v
            carried.append(f'{path}.{k}')
        elif isinstance(v, dict) and isinstance(new[k], dict):
            _carry(new[k], v, f'{path}.{k}', carried)


def index_row(record, keep=None):
    """The company's single row of research/THESES_he.md (ten cells, the header's order). keep: the cells of the row
    being replaced; its judgement cells (the date note, the historical multiple, the verdict, the next check) stay
    when the record holds nothing to write there."""
    keep = keep or []
    sc, h, txt = record['scenarios'], record['hurdles'], record.get('index_texts') or {}
    base = sc['base']['value_today']
    dv = display_values(record)
    hm = record['historical_multiple']
    hist = txt.get('hist_he') or ((keep[6] if len(keep) > 6 else 'לא חושב') if hm.get('value') is None or (len(keep) > 6 and not hm.get('computed')) else
                                  f'כ-{_money(hm["value"], like=base)}' + (f' ({_money(hm["low"], like=base)[:-1]}-{_money(hm["high"], like=base)})'
                                                                             if hm.get('low') is not None and hm.get('high') is not None else ''))
    ep = h.get('entry_price') or {}
    if h.get('margin_of_safety_price') is not None and ep.get('rung') == 'margin_of_safety_13':
        entry = f'{_money(ep["value"], 2)} (13% בשנה)'
    elif h.get('margin_of_safety_price') is None:
        entry = 'אין מחיר ל-13% במודל הזה'
    else:
        entry = f'{_money(ep["value"], 2)} ({ep.get("rung")})'
    if record.get('worlds'):
        entry += ' (ממוצע משוקלל של העולמות)'
    if h.get('buy_test_price') is not None:
        entry += f'; {LABELS_HE["buy_test"]}: {_money(h["buy_test_price"], 2)}'
    else:
        entry += f'; {LABELS_HE["buy_test"]}: אין מחיר שעובר את כל המבחנים'
        if h.get('base_12_price') is not None:
            entry += f' (התרחיש הבסיסי לבדו נותן 12% עד {_money(h["base_12_price"], 2)})'
    nr = record['next_report']
    cells = [
        f'[{record["ticker"]}](companies/{record["ticker"]}/thesis_he.md)',
        (keep[1] if len(keep) > 1 and not txt.get('updated_note_he') and keep[1].startswith(_dmy(record['as_of']))
         else _dmy(record['as_of']) + (f' ({txt["updated_note_he"]})' if txt.get('updated_note_he') else '')),
        _money(record['price']['value'], 2),
        ' / '.join((f'**{_money(sc[k]["value_today"], like=base)}**' if k == 'base' else
                    '0$ (ההון נמחק)' if sc[k].get('wiped') else _money(sc[k]['value_today'], like=base))
                   for k in SCEN if k in sc),
        'אין' if not dv.get('one_year') else f'{_money(dv["one_year"].get("base"), like=base)} ({_dmy(dv["one_year"]["date"])})',
        'אין' if not dv.get('five_year') else f'{_money(dv["five_year"].get("base"), like=base)} ({_dmy(dv["five_year"]["date"])})',
        hist, entry,
        txt.get('verdict_he') or (keep[8] if len(keep) > 8 else None) or record['verdict']['text_he'] or 'אין מסקנה ברשומה',
        txt.get('next_check_he') or (keep[9] if len(keep) > 9 else None) or nr.get('text') or 'לא ידוע',
    ]
    return '| ' + ' | '.join(str(c).replace('|', '/').replace('\n', ' ') for c in cells) + ' |'


def numbers_block(record):
    """The generated numbers block of thesis_he.md, markers included. Prose outside it is never touched."""
    sc, h, lens = record['scenarios'], record['hurdles'], record.get('second_lens') or {}
    order = [k for k in SCEN if k in sc]
    base = sc['base']['value_today']
    he = {'bear': 'דובי', 'base': 'בסיסי', 'bull': 'שורי'}
    dv = display_values(record)

    def cells(get, bold=True):
        return ' | '.join((f'**{get(k)}**' if k == 'base' and bold else get(k)) for k in order)

    def val(k, v):
        return 'ההון נמחק (0$)' if sc[k]['wiped'] and not v else _money(v, like=base)
    lines = [BLOCK_START,
             f'**תאריך ההערכה:** {_dmy(record["as_of"])} | **מחיר:** {_money(record["price"]["value"], 2)}',
             '',
             '| | ' + ' | '.join(he[k] for k in order) + ' |', '|' + '---|' * (len(order) + 1),
             '| היום | ' + cells(lambda k: val(k, sc[k]['value_today'])) + ' |']
    for key, label in (('one_year', 'בעוד שנה'), ('five_year', 'בעוד 5 שנים')):
        if dv.get(key):
            lines.append(f'| {label} ({_dmy(dv[key]["date"])}) | ' + cells(lambda k: val(k, dv[key].get(k))) + ' |')
    lines.append(f'| תשואה שנתית עד {_dmy(h["horizon_date"]) if h.get("horizon_date") else "האופק"} מהמחיר של היום | '
                 + cells(lambda k: 'אין (ההון נמחק)' if sc[k]['annual_return_pct'] is None else _pct(sc[k]['annual_return_pct']), bold=False) + ' |')
    lines.append('')
    probs = [sc[k]['probability'] for k in order]
    if lens.get('annual_return_pct') is not None or lens.get('value_today') is not None:
        lines.append(f'- **עדשה שנייה** (המכפיל של היום נשמר): {_money(lens.get("value_today"), like=base)} היום, {_pct(lens.get("annual_return_pct"))} בשנה.')
    hm = record['historical_multiple']
    if hm.get('value') is not None:
        rng = f' (טווח {_money(hm["low"], like=base)[:-1]}-{_money(hm["high"], like=base)})' if hm.get('low') is not None and hm.get('high') is not None else ''
        lines.append(f'- **{LABELS_HE["hist"]}:** {_money(hm["value"], like=base)}{rng}.')
    bt = h['buy_test']
    wiped = [he[k] for k in order if sc[k]['wiped']]   # the reason in Hebrew: the model's English note stays in the record
    why = f' (בתרחיש ה{wiped[0]} ההון נמחק, ולכן אף מחיר לא עובר את החלק שלו במבחן)' if wiped else ''
    lines.append(f'- **{LABELS_HE["buy_test"]}:** ' + ('אין מחיר שעובר את כל ארבעת המבחנים' + why
                                                     if h.get('buy_test_price') is None else
                                                     f'{_money(h["buy_test_price"], 2)}' + (' (בהרצה חוזרת)' if bt.get('rerun_used') else '')) + '.')
    lines.append(f'- **{LABELS_HE["margin_of_safety"]}:** ' + ('אין (המודל לא מחשב מחיר ל-13%)' if h.get('margin_of_safety_price') is None
                                                             else f'{_money(h["margin_of_safety_price"], 2)} (13% בשנה)') + '.')
    rest = [(n, h.get(k)) for n, k in (('מחיר שבו התשואה שווה למדד', 'index_parity_price'), ('6% בשנה', 'hold_floor_price'), ('3% בשנה', 'sell_line_price'))
            if h.get(k) is not None]
    if rest:
        lines.append('- **שאר המדרגות:** ' + '; '.join(f'{n}: {_money(v, 2)}' for n, v in rest) + '.')
    wd = record.get('worlds')
    if wd:
        lines.append('- **עולמות** (שיקול דעת; השווי למעלה הוא הממוצע המשוקלל שלהם): ' + '; '.join(
            f'{x.get("label_he") or x["name"]} ({round((x.get("weight") or 0) * 100):g}%): בסיסי '
            f'{_money((x.get("value_today") or {}).get("base"), like=base)} היום, {_pct((x.get("annual_return_pct") or {}).get("base"))} בשנה, '
            f'{LABELS_HE["buy_test"]} ' + (_money(x.get('buy_test_price'), 2) if x.get('buy_test_price') is not None else 'אין')
            for x in wd.get('worlds') or []) + '.')
    elif record.get('variants'):
        lines.append('- **גרסאות שמורות לצד הקובץ הראשי** (לא בתוך השווי למעלה): ' + '; '.join(
            f'{v["name"]}: בסיסי {_money(v.get("base_value_today"), like=base)} היום, {_pct(v.get("base_annual_return_pct"))} בשנה'
            for v in record['variants']) + '.')
    if all(is_number(x) for x in probs):
        w = record.get('weighted') or {}
        lines.append('- **הסתברויות** (שיקול דעת): ' + ' / '.join(f'{he[k]} {round(sc[k]["probability"] * 100):g}%' for k in order)
                     + (f'. השווי המשוקלל היום: {_money(w["value_today"], like=base)}' if w.get('value_today') is not None else '') + '.')
    lines.append(BLOCK_END)
    return '\n'.join(lines)


def replace_block(text, block):
    """(new text, True) with the numbers block replaced, or (text, False) when the markers are not there."""
    if not BLOCK_RE.search(text):
        return text, False
    return BLOCK_RE.sub(lambda m: block, text, count=1), True


def index_rows(text):
    """{ticker: [(line number, line)]} of the index table."""
    out = {}
    for i, line in enumerate(text.splitlines()):
        m = re.match(r'\|\s*\[([A-Z0-9.]+)\]\(companies/', line)
        if m:
            out.setdefault(m.group(1), []).append((i, line))
    return out


def replace_index_row(text, ticker, row):
    """(new text, removed rows): the ticker's rows replaced by the one row, at the place of the first; appended when new."""
    lines = text.splitlines()
    mine = index_rows(text).get(ticker, [])
    if not mine:
        return '\n'.join(lines + [row]) + '\n', []
    first = mine[0][0]
    drop = {i for i, _ in mine}
    new = [row if i == first else ln for i, ln in enumerate(lines) if i == first or i not in drop]
    return '\n'.join(new) + '\n', [ln for _, ln in mine]


# ------------------------------------------------------------------ the check

_NUM = re.compile(r'(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)')


def _numbers(text):
    """[(value, decimals written)] of the numbers in a text, dates like 13.11.2026 and percentages left out."""
    text = re.sub(r'\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\.\d{4}|\b20\d{2}\b', ' ', text)
    out = []
    for m in _NUM.finditer(text):
        if text[m.end():m.end() + 1] == '%':
            continue
        s = m.group(1).replace(',', '')
        out.append((float(s), len(s.split('.')[1]) if '.' in s else 0))
    return out


def _same(written, places, truth):
    return truth is not None and abs(round(truth, places) - written) < 10 ** -places / 2 + 1e-9


def _written_places(x):
    s = repr(x)
    return len(s.split('.')[1]) if '.' in s and 'e' not in s else 0


def parse_index_row(line):
    cells = [c.strip() for c in line.strip().strip('|').split('|')]
    out = {'cells': cells, 'price': None, 'base': None, 'entry': []}
    if len(cells) >= 8:
        n = _numbers(cells[2])
        out['price'] = n[0] if n else None
        m = re.search(r'\*\*([^*]+)\*\*', cells[3])
        n = _numbers(m.group(1)) if m else []
        out['base'] = n[0] if n else None
        out['entry'] = _numbers(cells[7])
    return out


def parse_thesis(text):
    """{'block': the numbers block or None, 'price', 'base'}: from the block when present, else from the hand-written
    header and the first 'היום' table row."""
    m = BLOCK_RE.search(text)
    out = {'block': m.group(0) if m else None, 'price': None, 'base': None}
    src = out['block'] or text
    pm = re.search(r'\*\*מחיר(?: באותו יום)?:\*\*\s*([^|\n]*)', src)
    n = _numbers(pm.group(1)) if pm else []
    out['price'] = n[0] if n else None
    tm = re.search(r'^\|\s*היום\s*\|(.*)$', src, re.M)
    if tm:
        bm = re.search(r'\*\*([^*]+)\*\*', tm.group(1))
        n = _numbers(bm.group(1)) if bm else []
        out['base'] = n[0] if n else None
    return out


def check_handoff_numbers(h):
    """Text where a handoff must hold a number or null (price, the two entry prices, values, annual_return_pct), and
    an as_of that is not an ISO date."""
    bad = []
    for k in HANDOFF_NUMERIC_KEYS:
        if isinstance(h.get(k), str):
            bad.append(f'{k}: text in a numeric field ({h[k]!r})')
        else:
            _numbers_only(h.get(k), k, bad)
    if _iso(h.get('as_of')) is None:
        bad.append(f'as_of: not an ISO date ({h.get("as_of")!r})')
    return bad


def _handoffs(root):
    out = {}
    for fp in sorted(glob.glob(os.path.join(root, TECH, '*', '*', 'fundamental.json'))):
        d = os.path.basename(os.path.dirname(fp))
        if DATED.match(d):
            out.setdefault(os.path.basename(os.path.dirname(os.path.dirname(fp))), []).append((d, fp))
    return out


def _lean_rerun_problems(root, run, is_lean):
    out = []
    for fp in sorted(glob.glob(os.path.join(root, STORE, '*', '*', 'lean_params*.json'))):
        res = os.path.join(os.path.dirname(fp), os.path.basename(fp).replace('lean_params', 'lean_result', 1))
        if not DATED.match(os.path.basename(os.path.dirname(fp))) or not os.path.isfile(res):
            continue
        params = load(fp)
        if not is_lean(params):
            continue
        t = os.path.basename(os.path.dirname(os.path.dirname(fp)))
        rel = os.path.relpath(res, root).replace(os.sep, '/')
        try:
            rerun = json.loads(json.dumps(run(params)))
        except Exception as e:   # noqa: BLE001  (a params file the model refuses is a problem to list, not a crash)
            out.append(('rerun', t, f'{rel}: the params can no longer be run ({e})'))
            continue
        diffs = lean_freeze._differences(load(res), rerun)
        if diffs:
            first = diffs[0]
            out.append(('rerun', t, f'{rel}: the saved result no longer equals a rerun ({len(diffs)} key(s); first: {first})'))
    return out


def check_tree(root, run=None):
    """Problems of a research tree as [(kind, ticker, text)]. root: the research folder. run: lean_model.run, for the
    rerun check (left out when None). Reads only."""
    problems = []
    comp = os.path.join(root, STORE)
    theses = sorted(t for t in (os.listdir(comp) if os.path.isdir(comp) else []) if os.path.isfile(os.path.join(comp, t, 'thesis_he.md')))
    handoffs = _handoffs(root)
    index_path = os.path.join(root, INDEX)
    rows = index_rows(_text(index_path)) if os.path.isfile(index_path) else {}
    for t in theses:
        if t not in handoffs:
            problems.append(('pairing', t, 'a thesis without a handoff (research/technical/<T>/<date>/fundamental.json)'))
        if t not in rows:
            problems.append(('index', t, 'a thesis without an index row'))
    for t in sorted(handoffs):
        if t not in theses:
            problems.append(('pairing', t, f'a handoff ({handoffs[t][-1][0]}) without a thesis file'))
    for t, mine in sorted(rows.items()):
        if len(mine) > 1:
            problems.append(('index', t, f'{len(mine)} index rows (one row per company)'))
        if t not in theses:
            problems.append(('index', t, 'an index row without a thesis file'))
    for t in sorted(set(theses) | set(handoffs)):
        latest = latest_valuation(root, t, run)
        rec = latest and latest['record']
        if latest is None:
            problems.append(('record', t, 'no dated valuation folder under research/companies'))
            continue
        d = latest['as_of']
        if rec is None:
            problems.append(('record', t, f'the newest folder {d} holds no lean or custom valuation ({latest["note"]}): nothing to check against'))
            continue
        if latest['record_source'] == RECORD_NAME:
            invalid = validate(rec)
            for msg in invalid:
                problems.append(('entry' if 'entry_price' in msg else 'record', t, f'{d}/{RECORD_NAME}: {msg}'))
            try:
                fresh, _ = record_for_folder(latest['folder'], root, run=run or default_run())
            except (ValueError, KeyError) as e:
                fresh = None
                problems.append(('record', t, f'{d}: the saved files give no record ({e})'))
            if fresh:
                for label, a, b in (('price', rec['price']['value'], fresh['price']['value']),
                                    ('base value today', rec['scenarios']['base']['value_today'], fresh['scenarios']['base']['value_today']),
                                    ('result file hash', (rec['model'].get('result') or {}).get('sha256'), (fresh['model'].get('result') or {}).get('sha256')),
                                    ('params file hash', (rec['model'].get('params') or {}).get('sha256'), (fresh['model'].get('params') or {}).get('sha256'))):
                    if a != b:
                        problems.append(('mismatch', t, f'{d}: {label} in {RECORD_NAME} is {a}, the saved params/result give {b}'))
            if invalid:
                if not fresh:
                    continue
                rec = fresh       # an invalid saved record cannot be projected: the other files are compared with a fresh one
        elif t in theses:
            problems.append(('record', t, f'{d}: no {RECORD_NAME} (the comparisons below use a record built from the saved files)'))
        price, base = rec['price']['value'], rec['scenarios']['base']['value_today']
        rungs = rung_prices(rec)

        def not_rung(v, places):
            return rungs and not any(_same(v, places, r) for r in rungs.values())
        # the handoff
        for hd, fp in handoffs.get(t, [])[-1:]:
            rel = os.path.relpath(fp, root).replace(os.sep, '/')
            h = load(fp)
            if hd != d:
                problems.append(('mismatch', t, f'{rel}: the newest handoff is dated {hd}, the newest valuation {d}'))
                continue
            problems += [('text_in_number', t, f'{rel}: {b}') for b in check_handoff_numbers(h)]
            if is_number(h.get('price')) and h['price'] != price:
                problems.append(('mismatch', t, f'{rel}: price {h["price"]:g}, the valuation of {d} uses {price:g}'))
            hb = ((h.get('values') or {}).get('today') or {}).get('base') if isinstance((h.get('values') or {}).get('today'), dict) else None
            if is_number(hb) and not _same(hb, _written_places(hb), base):
                problems.append(('mismatch', t, f'{rel}: base value today {hb:g}, the valuation of {d} gives {base:g}'))
            elif hb is None:
                problems.append(('mismatch', t, f'{rel}: no values.today.base to compare with the valuation ({base:g})'))
            for k in ('buy_test_price', 'margin_of_safety_price'):
                v = h.get(k)
                if is_number(v) and not_rung(v, _written_places(v)):
                    problems.append(('entry', t, f'{rel}: {k} {v:g} is not a rung of the valuation\'s ladder'))
            if h.get('schema_version') == HANDOFF_SCHEMA_VERSION:
                want, v2 = project_handoff(rec), h.get('v2') or {}
                for k in ('buy_test_price', 'margin_of_safety_price'):
                    if h.get(k) != want.get(k):
                        problems.append(('mismatch', t, f'{rel}: {k} is {h.get(k)!r}, the record gives {want.get(k)!r}'))
                for k in ('entry_price', 'index_parity_price', 'hold_floor_price', 'sell_line_price', 'buy_test', 'price_ladder', 'values_dated'):
                    if v2.get(k) != want['v2'].get(k):
                        problems.append(('mismatch', t, f'{rel}: v2.{k} differs from the record'))
        # the index rows
        for _, line in rows.get(t, []):
            r = parse_index_row(line)
            tag = f'{INDEX} row "{r["cells"][1][:40]}"'
            if r['price'] and not _same(r['price'][0], r['price'][1], price):
                problems.append(('mismatch', t, f'{tag}: price {r["price"][0]:g}, the valuation of {d} uses {price:g}'))
            if r['base'] and not _same(r['base'][0], r['base'][1], base):
                problems.append(('mismatch', t, f'{tag}: base value today {r["base"][0]:g}, the valuation of {d} gives {base:g}'))
            for v, places in r['entry']:
                if v >= 1 and not_rung(v, places):
                    problems.append(('entry', t, f'{tag}: entry price {v:g} is not a rung of the valuation\'s ladder'))
        # the thesis
        tp = os.path.join(comp, t, 'thesis_he.md')
        if os.path.isfile(tp):
            th = parse_thesis(_text(tp))
            where = f'companies/{t}/thesis_he.md ' + ('numbers block' if th['block'] else '(hand-written table, no numbers block)')
            if th['block'] and th['block'] != numbers_block(rec):
                problems.append(('mismatch', t, f'{where}: the block differs from the one the record gives (edited by hand or stale)'))
            if th['price'] and not _same(th['price'][0], th['price'][1], price):
                problems.append(('mismatch', t, f'{where}: price {th["price"][0]:g}, the valuation of {d} uses {price:g}'))
            if th['base'] and not _same(th['base'][0], th['base'][1], base):
                problems.append(('mismatch', t, f'{where}: base value today {th["base"][0]:g}, the valuation of {d} gives {base:g}'))
    if run is not None:
        problems += _lean_rerun_problems(root, run, is_lean_params)
    return problems
