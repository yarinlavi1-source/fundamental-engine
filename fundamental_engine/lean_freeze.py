"""Freeze published lean valuations into the tracked, chained ledger research/ledger.jsonl.

Why: the lean path (scripts/lean_model.py -> thesis book) never wrote to a ledger, and its params files can be edited
after publication, so a later score could not prove it measured a forecast. `freeze_folder` records one valuation folder
(research/companies/<T>/<date>/) through the existing Ledger API (hash chain, system clock, supersedes, outcomes):

- a thesis row: ticker, as_of, price, sha256 of the params and of the saved result, the lean_model file hash,
  bear/base/bull value today, the dated year-end values, the calibrated base, the buy test, the pre-review and
  post-review base when both files exist, the named variants, and on_time (recorded before the company's next report);
- forecast rows: base revenue, operating margin and operating EPS per model year with the bear-bull envelope.

Numbers are read from the SAVED result (what was published); lean_model is rerun only to say whether it still
reproduces that result, for the buy test of results saved before that key existed, and for the pre-review base.
Custom models (a bank model, a segment model, a three-lane model, a lean file whose revenue line is not sales) get a
thesis row with the published values of their saved result and the note 'custom model'; forecast rows only where the
saved result carries a per-year path this module knows how to read.

Time: the ledger is opened with the recording-lag refusal off, so a valuation published days ago can still be put on
file; the Ledger's own rule then flags it late (recorded more than 7 days after made_at). made_at is the params as_of.
The Ledger refuses a made_at earlier than one already on file, so freezing runs in as_of order; a valuation frozen out
of order, or frozen again because its inputs changed, takes the recording day as made_at and says so (the system clock
is the only time the ledger can prove). on_time is separate from that flag: it says whether the row was recorded before
the next report, which is what makes a later score a test of a forecast.

Idempotent: a folder whose inputs and published numbers are unchanged adds nothing; changed inputs add a new thesis that
supersedes the old one (both stay on file). A newer valuation of the same company supersedes the previous one the same way.

The first row of a new ledger is a note holding the sha256 of the legacy research/forecast_ledger.jsonl (heavy-engine
theses to 02.11.2026; a deduplicated copy whose chain cannot be verified or appended to): that file is never touched.

Weighted worlds (16.11.2026): when the main params carry a "worlds" key or a worlds.json sits beside lean_params.json,
the published values are the weighted worlds (fundamental_engine/valuation_record.py), so the thesis row holds them:
bear/base/bull today, the dated year-end values, the calibrated base, the buy test and the entry prices of
lean_model.run(params, worlds=...), with each world's weight, values and file sha256 and the sha256 of worlds.json. The
forecast rows hold the weighted base path (weight x each world's base revenue, operating margin and operating EPS; a
simple weighted mean, also for the margin) and, as bear and bull, the envelope across the worlds and their scenarios
(each world's own paths come from a lean_model rerun of its file; the saved results are the main file's only). A folder
frozen before without its worlds gets a superseding thesis row and new forecast rows; a second run adds nothing.
"""
import calendar
import glob
import hashlib
import json
import os
import re
from datetime import date, timedelta

from .forecast_tracker import lean_format
from .ledger import LATE_RECORDING_DAYS, Ledger, day
from .monitor import BASES

FROZEN_BY = 'lean_freeze/1'
LEDGER = os.path.join('research', 'ledger.jsonl')
LEGACY = os.path.join('research', 'forecast_ledger.jsonl')
STORE = os.path.join('research', 'companies')
GENESIS_ID = 'legacy-ledger'
SCEN = ('bear', 'base', 'bull')
EPS_BASIS = 'operating_income_after_sbc_after_model_tax/model_shares'
BUY_RULE = {'base_ours': 12.0, 'base_calibrated': 12.0, 'bull': 17.0, 'bear': -10.0}   # the owner's buy test (05.11.2026)
FY_MATCH_DAYS = 12        # 52/53-week fiscal years end on different calendar days
APPROX_DAYS = 3           # '~date' in a handoff: read as that many days either side
CUSTOM_NOTE = 'custom model'
SAVED_BUY_KEYS = ('buy_test', 'buy_test_today', 'buy_test_at_fundamental_add_price', 'entry_prices', 'entry_price_all_tests',
                  'price_for_12pct', 'combined')
MONTHS = {m[:3].lower(): i for i, m in enumerate(calendar.month_name) if m}
WORLDS_NAME = 'worlds.json'      # beside lean_params.json (valuation_record.WORLDS_NAME; scripts/lean_model.py --worlds)
CALIBRATED_RULE_NAME = 'calibrated_rule.json'   # beside lean_params.json (scripts/lean_model.py --calibrated-rule)
WORLDS_LABEL = 'subjective (judgement, not engine odds)'
WORLDS_PATH_NOTE = ("weighted path over the worlds ({weights}; judgement, not engine odds): base = sum of weight x each "
                    "world's base path; bear and bull = the envelope (lowest and highest) across the worlds and their scenarios")
ROUND = {'revenue': 0, 'operating_margin': 6, 'eps_operating': 4}


# ------------------------------------------------------------------ files and hashes

def sha256_file(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def canonical_sha256(obj):
    """Hash of the content, not of the bytes: a reformatted file with the same values has the same hash."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _rel(path, root):
    return os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, '/')


def _d(s):
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def _shift(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:       # 29 February
        return d.replace(year=d.year + years, day=28)


def open_ledger(path, clock=None):
    """The lean ledger: the Ledger API with the recording-lag refusal off (late rows are flagged, not refused)."""
    return Ledger(path, clock=clock, max_recording_lag_days=None)


def genesis(L, legacy_path=None):
    """First row of a new ledger: a note with the sha256 of the legacy ledger file. No-op on a ledger with rows."""
    if L.lines():
        return None
    if legacy_path and os.path.isfile(legacy_path):
        with open(legacy_path, 'rb') as f:
            body = f.read()
        digest, name = hashlib.sha256(body).hexdigest(), os.path.basename(legacy_path)
        text = (f'Start of the lean-path ledger. The legacy file {name} (heavy-engine theses, a deduplicated copy whose hash '
                f'chain cannot be verified or appended to) stays as it is; its sha256 at this point is {digest} '
                f'({body.count(10)} lines, {len(body)} bytes).')
        ids = ['sha256:' + digest, 'file:research/' + name]
    else:
        text, ids = 'Start of the lean-path ledger. No legacy ledger file was found at this point.', []
    return L.event(id=GENESIS_ID, entity='*', kind='note', occurred_at=L.now(), description=text, evidence_ids=ids)


# ------------------------------------------------------------------ what a folder holds

def classify(folder):
    """{'kind': 'lean' | 'custom' | None, ...} for one dated research folder; file names are relative to it."""
    def has(name):
        return os.path.isfile(os.path.join(folder, name))

    def found(pattern):
        return sorted(_rel(f, folder) for f in glob.glob(os.path.join(folder, pattern)) if os.path.isfile(f))

    if has('lean_params.json'):
        if not has('lean_result.json'):
            return {'kind': None, 'reason': 'lean_params.json without lean_result.json: not finished'}
        ok, why = lean_format(_load(os.path.join(folder, 'lean_params.json')))
        if ok:
            return {'kind': 'lean', 'model': 'lean_model', 'params': 'lean_params.json', 'result': 'lean_result.json'}
        base = (_load(os.path.join(folder, 'lean_result.json')).get('scenarios') or {}).get('base') or {}
        model = 'remapped_lean' if 'revenue_m' in base else 'bank' if 'tbvps' in base else 'other'
        return {'kind': 'custom', 'model': model, 'params': 'lean_params.json', 'result': 'lean_result.json',
                'inputs': found('*_model.py'), 'why': why}
    if has('params.json') and has('results.json'):
        return {'kind': 'custom', 'model': 'segment', 'params': 'params.json', 'result': 'results.json', 'inputs': found('model.py'),
                'why': 'its own model file (params.json, results.json)'}
    if has('model_result.json'):
        return {'kind': 'custom', 'model': 'three_lane', 'params': None, 'result': 'model_result.json',
                'inputs': found('finish_result.json') + found('model/*_params.json') + found('model/*.py'),
                'why': 'its own model code (model/)'}
    return {'kind': None, 'reason': 'no lean or custom valuation files (a heavy-engine or gather-only folder)'}


def published_folders(root, skip=()):
    """(folders to freeze in as_of order, folders left out with the reason). skip: tickers not to freeze."""
    todo, left = [], []
    for folder in sorted(glob.glob(os.path.join(root, STORE, '*', '*'))):
        if not os.path.isdir(folder):
            continue
        t, d = os.path.basename(os.path.dirname(folder)), os.path.basename(folder)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', d):
            continue
        if t in skip:
            left.append({'folder': _rel(folder, root), 'reason': 'skipped on request'})
            continue
        spec = classify(folder)
        if spec['kind'] is None:
            left.append({'folder': _rel(folder, root), 'reason': spec['reason']})
        else:
            todo.append((d, t, folder))
    return [f for _, _, f in sorted(todo)], left


# ------------------------------------------------------------------ the next report

def report_window(nr):
    """(earliest, latest, text) days the next report may fall on, or None when no date can be read.

    nr: a date string or a handoff next_report {'date': ...}. An ISO date is that day; '~2027-02-04' is APPROX_DAYS
    either side; 'late January 2027' or 'Feb-Mar 2027' is the whole of the named months."""
    text = (nr.get('date') if isinstance(nr, dict) else nr)
    if not text:
        return None
    text = str(text).strip()
    m = re.search(r'(~?)\s*(\d{4}-\d{2}-\d{2})', text)
    if m:
        d = date.fromisoformat(m.group(2))
        pad = timedelta(days=APPROX_DAYS if m.group(1) else 0)
        return d - pad, d + pad, text
    prev = 0
    for y in re.finditer(r'\b(20\d{2})\b', text):       # the first year that has month names before it
        words = re.findall(r'[A-Za-z]{3,}', text[prev:y.start()])
        names = [MONTHS[w[:3].lower()] for w in words if w[:3].lower() in MONTHS
                 and calendar.month_name[MONTHS[w[:3].lower()]].lower().startswith(w.lower())]
        if names:
            year = int(y.group(1))
            return date(year, names[0], 1), date(year, names[-1], calendar.monthrange(year, names[-1])[1]), text
        prev = y.end()
    return None


def on_time(recorded, nr):
    """True: recorded before the next report; False: after it; None: no date, or recorded inside the window."""
    w = report_window(nr)
    if w is None:
        return None
    rec = _d(recorded)
    return True if rec < w[0] else False if rec > w[1] else None


def _next_report(nr, source):
    if not nr:
        return None
    w = report_window(nr)
    out = {'text': str(nr.get('date') if isinstance(nr, dict) else nr), 'source': (nr.get('source') if isinstance(nr, dict) else None) or source,
           'confirmed': bool(nr.get('confirmed')) if isinstance(nr, dict) else False,
           'earliest': w[0].isoformat() if w else None, 'latest': w[1].isoformat() if w else None}
    return out


def handoff_next_report(root, ticker, as_of):
    """next_report of the fundamental handoff written for this valuation, when there is one."""
    path = os.path.join(root, 'research', 'technical', ticker, as_of, 'fundamental.json')
    if not os.path.isfile(path):
        return None
    nr = _load(path).get('next_report')
    return dict(nr, source=_rel(path, root)) if isinstance(nr, dict) and nr.get('date') else None


# ------------------------------------------------------------------ the published numbers

def _scenarios(result):
    out = {}
    for k in SCEN:
        s = (result.get('scenarios') or {}).get(k)
        if isinstance(s, dict):
            ye = s.get('year_end_values', s.get('ye')) or {}
            out[k] = {'value_today': s.get('value_today', s.get('today')), 'year_end': {int(y): v for y, v in ye.items()},
                      'annual_return_pct': s.get('annual_return_pct', s.get('return_pct'))}
    return out


def _calibrated(result):
    lens = (result.get('two_lens') or {}).get('calibrated')
    full = result.get('calibrated') if isinstance(result.get('calibrated'), dict) else {}
    c = dict(full, **lens) if isinstance(lens, dict) else full or (result.get('lenses') or {}).get('calibrated') or {}
    if not c:
        return None
    ye = {int(y): v for y, v in (c.get('year_end_values') or {}).items()}
    if not ye and c.get('end_2031') is not None:
        ye = {2031: c['end_2031']}
    return {'value_today': c.get('value_today'), 'year_end': ye, 'annual_return_pct': c.get('annual_return_pct', c.get('return_pct')),
            'band': c.get('band'), 'pe_used': c.get('pe_used'), 'note': c.get('note')}


def _buy_test(result, scen, cal, rerun=None):
    """The buy test as published; for results saved before lean_model wrote it, the rerun's; else the legs from the returns."""
    saved = result.get('buy_test') if isinstance(result.get('buy_test'), dict) else None
    bt, source = (saved, 'saved result') if saved and 'legs' in saved else (rerun, 'rerun at freeze: the saved result has no '
                                                                             'lean_model buy_test key') if rerun else (None, None)
    if bt:
        out = {'source': source, 'rule': bt.get('rule'), 'pass': bt.get('pass'), 'complete': bt.get('complete'),
               'legs': {k: {x: v.get(x) for x in ('return_pct', 'threshold_pct', 'pass', 'max_price')} for k, v in bt['legs'].items()},
               'binding_leg': bt.get('binding_leg'), 'max_price_passing': bt.get('max_price_passing'),
               'on_the_line': bool(bt.get('on_the_line')), 'no_price_passes': bt.get('no_price_passes'),
               'rerun_max_price_passing': (bt.get('rerun') or {}).get('max_price_passing')}
    else:
        rets = {'base_ours': (scen.get('base') or {}).get('annual_return_pct'), 'base_calibrated': (cal or {}).get('annual_return_pct'),
                'bull': (scen.get('bull') or {}).get('annual_return_pct'), 'bear': (scen.get('bear') or {}).get('annual_return_pct')}
        legs = {k: {'return_pct': r, 'threshold_pct': BUY_RULE[k], 'pass': None if r is None else r >= BUY_RULE[k]} for k, r in rets.items()}
        done = all(v['pass'] is not None for v in legs.values())
        out = {'source': 'computed at freeze from the saved returns (base >= 12% a year in both lenses, bull >= 17%, bear >= -10%)',
               'pass': done and all(v['pass'] for v in legs.values()), 'complete': done, 'legs': legs}
    extra = {k: result[k] for k in SAVED_BUY_KEYS if k in result and not (k == 'buy_test' and bt is saved and bt)}
    if extra:
        out['saved_keys'] = extra
    return out


def _differences(saved, new, path='', out=None):
    """Keys of the saved result the rerun does not reproduce (the rerun may hold more keys)."""
    out = [] if out is None else out
    if isinstance(saved, dict) and isinstance(new, dict):
        for k, v in saved.items():
            if k not in new:
                out.append(path + str(k) + ' (missing)')
            else:
                _differences(v, new[k], path + str(k) + '.', out)
    elif saved != new:
        out.append(path.rstrip('.'))
    return out


def _apply_calibrated_rule(params, rule):
    """The params with a calibrated-rule sidecar applied, as scripts/lean_model.py apply_calibrated_rule does (a fixed
    calibrated.pe is dropped from the copy and kept as replaced_pe; the saved file is never edited)."""
    q = json.loads(json.dumps(params))
    cal = dict(q.get('calibrated') or {})
    if 'pe' in cal:
        cal['replaced_pe'] = cal.pop('pe')
        cal['replaced_why'] = cal.get('why', '')
    cal['pe_rule'] = rule.get('pe_rule')
    cal['why'] = rule.get('why') or cal.get('why', '')
    q['calibrated'] = cal
    return q


def _run(run, params, **kw):
    """lean_model.run on a copy, with JSON key types (years as strings), or (None, reason). kw: worlds, base_dir."""
    try:
        return json.loads(json.dumps(run(json.loads(json.dumps(params)), **kw))), None
    except Exception as e:   # a params file the current model cannot run is a fact to record, not a crash
        return None, f'{type(e).__name__}: {e}'


def _series(result, key, scale=1.0):
    sc = result.get('scenarios') or {}
    if not all(isinstance((sc.get(k) or {}).get(key), dict) for k in SCEN):
        return None
    return {k: {int(y): v * scale for y, v in sc[k][key].items()} for k in SCEN}


def _paths(model, result, cur):
    """[(metric, unit, currency, basis, {scenario: {fiscal year: value}})] this module can read from the saved result."""
    if model == 'lean_model':
        spec = [('revenue', cur, cur, BASES['revenue'], _series(result, 'revenue_m', 1e6)),
                ('operating_margin', 'ratio', None, BASES['operating_margin'], _series(result, 'op_margin')),
                ('eps_operating', cur, cur, EPS_BASIS, _series(result, 'eps_operating'))]
    elif model == 'bank':
        spec = [('eps', cur, cur, 'custom:bank_model eps (net income to common / diluted average shares)', _series(result, 'eps')),
                ('net_income_common', cur, cur, 'custom:bank_model net income to common', _series(result, 'ni_common_m', 1e6))]
    elif model == 'segment':
        sc = result.get('scenarios') or {}
        ok = all(isinstance((sc.get(k) or {}).get('path'), dict) for k in SCEN)

        def col(name, scale=1.0):
            if not ok or not all(name in row for k in SCEN for row in sc[k]['path'].values()):
                return None
            return {k: {int(y): row[name] * scale for y, row in sc[k]['path'].items()} for k in SCEN}

        spec = [('revenue', cur, cur, 'custom:segment model group revenue', col('rev', 1e9)),
                ('operating_margin', 'ratio', None, 'custom:segment model group EBIT / revenue', col('ebit_m'))]
    else:
        spec = []
    return [s for s in spec if s[4]]


def _order(b, m, u):
    return 'ascending' if b <= m <= u else 'descending' if b >= m >= u else 'crossing'


# ------------------------------------------------------------------ weighted worlds

def worlds_spec(folder, spec, params):
    """(worlds spec, worlds.json path or None) of a lean valuation: the params' own "worlds" key, else a worlds.json
    beside the main lean_params.json (the same rule as valuation_record.worlds_spec); (None, None) without worlds."""
    if spec.get('kind') != 'lean':
        return None, None
    if isinstance(params, dict) and params.get('worlds') is not None:
        return params['worlds'], None
    path = os.path.join(folder, WORLDS_NAME)
    if spec.get('params') == 'lean_params.json' and os.path.isfile(path):
        return _load(path), path
    return None, None


def _weighted_paths(runs, cur):
    """([(metric, unit, currency, basis, {bear: low, base: weighted, bull: high})], {metric: {year: detail}}) from the
    single runs of the worlds [(name, weight, result)]: base = sum of weight x each world's base; bear / bull = the
    envelope across every world and scenario. A metric a world lacks is left out."""
    specs = (('revenue', cur, cur, BASES['revenue'], 'revenue_m', 1e6),
             ('operating_margin', 'ratio', None, BASES['operating_margin'], 'op_margin', 1.0),
             ('eps_operating', cur, cur, EPS_BASIS, 'eps_operating', 1.0))
    out, extra = [], {}
    for metric, unit, c, basis, key, scale in specs:
        per = [(name, wt, _series(res, key, scale)) for name, wt, res in runs]
        if any(s is None for _, _, s in per):
            continue
        series, detail = {k: {} for k in SCEN}, {}
        for y in sorted(set.intersection(*[set(s['base']) for _, _, s in per])):
            if any(s[k].get(y) is None for _, _, s in per for k in SCEN):
                continue
            vals = [s[k][y] for _, _, s in per for k in SCEN]
            lo, hi = min(vals), max(vals)
            wb = min(max(round(sum(wt * s['base'][y] for _, wt, s in per), ROUND[metric]), lo), hi)
            series['bear'][y], series['base'][y], series['bull'][y] = lo, wb, hi
            detail[y] = {'base_by_world': {name: s['base'][y] for name, _, s in per},
                         'weighted_scenarios': {k: round(sum(wt * s[k][y] for _, wt, s in per), ROUND[metric]) for k in SCEN},
                         'envelope': {'low': lo, 'high': hi, 'across': 'every world x bear / base / bull'}}
        if series['base']:
            out.append((metric, unit, c, basis, series))
            extra[metric] = detail
    return out, extra


def _worlds(run, new, folder, root, wfile, cur, saved_scen):
    """The weighted-worlds part of a thesis: (block, scen, cal, buy test, entry prices, two_lens, paths, path detail).
    new: lean_model.run(params, worlds=...) as JSON. Each world file is rerun alone for its yearly paths."""
    wd = new['worlds']
    w = wd['weighted']
    keys = [k for k in SCEN if k in w['value_today']]
    scen = {k: {'value_today': w['value_today'][k], 'year_end': {int(y): v[k] for y, v in w['year_end_values'].items()},
                'annual_return_pct': w['annual_return_pct'][k]} for k in keys}
    tl = w.get('two_lens') or {}
    cal = None
    if 'base_calibrated' in w['value_today']:
        cal = {'value_today': w['value_today']['base_calibrated'],
               'year_end': {int(y): v['base_calibrated'] for y, v in w['year_end_values'].items()},
               'annual_return_pct': w['annual_return_pct']['base_calibrated'], 'band': tl.get('calibrated'), 'pe_used': None,
               'note': "weighted worlds: each world's second lens holds its own multiple"}
    rows, runs = [], []
    for x in wd['worlds']:
        path = x['file'] if os.path.isabs(x['file']) else os.path.join(folder, x['file'])
        wp = _load(path)
        res, err = _run(run, {k: v for k, v in wp.items() if k != 'worlds'})
        if res is None:
            raise ValueError(f"world {x['name']!r} ({x['file']}) cannot be run: {err}")
        runs.append((x['name'], x['weight'], res))
        bt = x.get('buy_test') or {}
        rows.append({'name': x['name'], 'label_he': x.get('label_he'), 'weight': x['weight'],
                     'params': {'path': _rel(path, root), 'sha256': sha256_file(path), 'canonical_sha256': canonical_sha256(wp)},
                     'value_today': x.get('value_today'), 'base_value_today': (x.get('value_today') or {}).get('base'),
                     'annual_return_pct': x.get('annual_return_pct'), 'base_band': x.get('base_band'),
                     'buy_test': {'pass': bt.get('pass'), 'max_price_passing': bt.get('max_price_passing'),
                                  'rerun_max_price_passing': bt.get('rerun_max_price_passing')},
                     'margin_of_safety_13': x.get('margin_of_safety_13')})
    block = {'source': _rel(wfile, root) if wfile else 'the params key "worlds"',
             'file': {'path': _rel(wfile, root), 'sha256': sha256_file(wfile), 'canonical_sha256': canonical_sha256(_load(wfile))}
             if wfile else None,
             'weights_label': WORLDS_LABEL, 'weights': wd['weights'], 'worlds': rows,
             'probability_weighted_value_today': w.get('probability_weighted_value_today'),
             'main_file_values_today': {k: v['value_today'] for k, v in saved_scen.items()},
             'warnings': wd.get('warnings') or [],
             'note': ('values, second lens, buy test and entry prices = the weighted worlds of lean_model.run(params, worlds=...) '
                      '(weight x value of each world, per scenario and date); main_file_values_today = the saved lean_result.json '
                      '(the main file alone); the forecast rows are the weighted path')}
    bt = _buy_test({'buy_test': w['buy_test']}, scen, cal)
    bt['source'] = 'weighted worlds: lean_model.run(params, worlds=...) at freeze'
    rungs = (w.get('price_ladder') or {}).get('rungs') or {}
    entry = {'buy_price': (rungs.get('margin_of_safety') or {}).get('ours'), 'index_parity_price': (rungs.get('index') or {}).get('ours'),
             'index_return_pct': (rungs.get('index') or {}).get('return_pct'),
             'margin_of_safety_13': (rungs.get('margin_of_safety_13') or {}).get('ours'),
             'buy_test_rung': (rungs.get('buy_test') or {}).get('ours'), 'source': 'the weighted price ladder'}
    two = {'agree': tl.get('agree'), 'rule': None if tl.get('agree') is None else
           'both lenses in the same band: act on it' if tl['agree'] else 'lenses disagree: hold, or stage the trade around the next report'}
    paths, detail = _weighted_paths(runs, cur)
    return block, scen, cal, bt, entry, two, paths, detail


def _forecasts(paths, base_year, base_end, as_of, today, detail=None):
    """(forecast specs, [fiscal years that are not forecasts any more]). detail: {metric: {year: ...}} of a weighted path."""
    specs, past = [], []
    for metric, unit, cur, basis, series in paths:
        for y in sorted(series['base']):
            end = _shift(base_end, y - base_year)
            vals = {k: series[k].get(y) for k in SCEN}
            if any(v is None for v in vals.values()):
                continue
            if end < today:
                past.append({'metric': metric, 'fiscal_year': y, 'period_end': end.isoformat(),
                             'reason': 'the fiscal year ended before the freeze: not a forecast'})
                continue
            specs.append({'metric': metric, 'fiscal_year': y, 'period_end': end.isoformat(), 'unit': unit, 'currency': cur,
                          'basis': basis, 'scenarios': vals, 'scenario_order': _order(*[vals[k] for k in SCEN]),
                          'horizon_years': round((end - as_of).days / 365.25, 2),
                          'weighted': ((detail or {}).get(metric) or {}).get(y)})
    return specs, past


# ------------------------------------------------------------------ the thesis record

def build_record(folder, root, spec=None, run=None, lean_model_path=None, falsifiers=None):
    """(record, forecast paths meta) for one valuation folder, read from its saved files. Nothing is written."""
    spec = spec or classify(folder)
    if spec['kind'] is None:
        raise ValueError(spec['reason'])
    lean = spec['kind'] == 'lean'
    ticker = os.path.basename(os.path.dirname(os.path.abspath(folder)))
    folder_date = os.path.basename(os.path.abspath(folder))
    result_path = os.path.join(folder, spec['result'])
    result = _load(result_path)
    params_path = os.path.join(folder, spec['params']) if spec.get('params') else None
    params = _load(params_path) if params_path else {}
    cfg = result.get('config') if isinstance(result.get('config'), dict) else {}
    as_of = str(params.get('as_of') or folder_date)[:10] if spec['model'] != 'three_lane' else folder_date
    price = params.get('price') if spec['model'] != 'three_lane' else cfg.get('price', result.get('price'))
    cur = params.get('currency') or 'USD'
    notes = []
    if as_of != folder_date:
        notes.append(f'the params as_of {as_of} differs from the folder date {folder_date}')

    files = {'result': {'path': _rel(result_path, root), 'sha256': sha256_file(result_path)}}
    if params_path:
        files['params'] = {'path': _rel(params_path, root), 'sha256': sha256_file(params_path), 'canonical_sha256': canonical_sha256(params)}
    inputs = {name: sha256_file(os.path.join(folder, name)) for name in spec.get('inputs', [])}
    if inputs:
        files['inputs'] = inputs
    lm_sha = sha256_file(lean_model_path) if lean_model_path and os.path.isfile(lean_model_path) else None
    files['lean_model'] = {'path': 'scripts/lean_model.py', 'sha256': lm_sha}

    scen, cal = _scenarios(result), _calibrated(result)
    if 'base' not in scen:
        raise ValueError('the saved result has no base scenario')
    wspec, wfile = worlds_spec(folder, spec, params)
    worlds, new_w = None, None
    if wspec is not None:
        # the published values are the weighted worlds: they are frozen, never the main file alone
        if not run:
            raise ValueError('a valuation with weighted worlds needs lean_model.run: its published values are the weighted ones')
        new_w, err = _run(run, params, worlds=wspec, base_dir=folder)
        if new_w is None or 'worlds' not in new_w:
            raise ValueError(f'the weighted worlds cannot be run: {err}')
        worlds, scen, cal, w_bt, w_entry, w_two, w_paths, w_detail = _worlds(run, new_w, folder, root, wfile, cur, scen)
        files['worlds'] = worlds['file'] or {'path': None, 'note': 'the params key "worlds"'}
    # a calibrated-rule sidecar (calibrated_rule.json beside lean_params.json) is part of the published valuation: the
    # second lens and the buy test are the ones with the rule applied (valuation_record.record_for_folder does the same)
    crule, rule_new = None, None
    rule_path = os.path.join(folder, CALIBRATED_RULE_NAME)
    if wspec is None and spec.get('params') == 'lean_params.json' and os.path.isfile(rule_path):
        if not run:
            raise ValueError('a valuation with a calibrated-rule sidecar needs lean_model.run: its published second lens is '
                             'the one with the rule applied')
        rule = _load(rule_path)
        rule_new, err = _run(run, _apply_calibrated_rule(params, rule))
        if rule_new is None:
            raise ValueError(f'the calibrated rule cannot be run: {err}')
        crule = {'path': _rel(rule_path, root), 'sha256': sha256_file(rule_path), 'canonical_sha256': canonical_sha256(rule),
                 'pe_rule': rule.get('pe_rule')}
        files['calibrated_rule'] = crule
        cal = _calibrated(rule_new)
    # dates of the year-ends: the base fiscal year ends days_to_base_year_end after the date the params were built on
    years = sorted(scen['base']['year_end'])
    dates_from = params
    if spec['model'] == 'three_lane':
        lane = [os.path.join(folder, n) for n in spec.get('inputs', []) if n.endswith('dcf_params.json')]
        dates_from = _load(lane[0]) if lane else {}
    base_end = (_d(dates_from['as_of']) + timedelta(days=dates_from['days_to_base_year_end'])
                if dates_from.get('as_of') and dates_from.get('days_to_base_year_end') is not None else None)
    # a custom result has no base_year key: its year labels are read as the calendar year the fiscal year ends in
    base_year = int(params['base_year']) if 'base_year' in params else base_end.year if base_end else None
    year_end_dates = {y: _shift(base_end, y - base_year).isoformat() for y in years} if base_end and base_year is not None else {}
    if not year_end_dates and years:
        notes.append('the year-end dates could not be derived (no as_of and days_to_base_year_end in the inputs)')

    rerun, rerun_bt, review = None, None, None
    if run and (lean or spec['model'] == 'remapped_lean'):
        new, err = (new_w, None) if new_w is not None else _run(run, params)
        diff = None if new is None else _differences(result, new)
        rerun = {'reproduces_saved_result': None if new is None else not diff, 'differing_keys': (diff or [])[:12], 'error': err}
        rerun_bt = new.get('buy_test') if new else None
        if rerun['reproduces_saved_result'] is False:
            notes.append('the current lean_model does not reproduce the saved result: the frozen numbers are the saved ones')
    pre_path = os.path.join(folder, 'lean_params_pre_review.json')
    if os.path.isfile(pre_path):
        pre = _load(pre_path)
        review = {'pre_review': {'path': _rel(pre_path, root), 'sha256': sha256_file(pre_path), 'price': pre.get('price')},
                  'post_review_base_today': scen['base']['value_today']}
        new, err = _run(run, pre) if run else (None, 'no model supplied')
        if new:
            b = new['scenarios']['base']
            review['pre_review'].update(base_today=b['value_today'], base_return_pct=b['annual_return_pct'],
                                        source='lean_model rerun of the pre-review params at freeze')
            review['base_change'] = round(scen['base']['value_today'] - b['value_today'], 2)
        else:
            review['pre_review']['error'] = err
    rf_path = os.path.join(folder, 'review_findings.json')
    if os.path.isfile(rf_path):
        rf = _load(rf_path)
        review = review or {'post_review_base_today': scen['base']['value_today']}
        review['review_findings'] = {'path': _rel(rf_path, root), 'sha256': sha256_file(rf_path),
                                     'pre_review_base_today': (rf.get('pre_review') or {}).get('base_today'),
                                     'final_base_today': (rf.get('final') or {}).get('base_today'),
                                     'critical_findings': rf.get('critical_findings')}

    variants = []
    for vp in sorted(glob.glob(os.path.join(folder, 'lean_params_*.json'))):
        name = os.path.basename(vp)[len('lean_params_'):-len('.json')]
        vr = os.path.join(folder, f'lean_result_{name}.json')
        if name == 'pre_review' or not os.path.isfile(vr):
            continue
        vs = _scenarios(_load(vr))
        variants.append({'name': name, 'params_sha256': sha256_file(vp), 'result_sha256': sha256_file(vr),
                         'value_today': {k: v['value_today'] for k, v in vs.items()},
                         'base_return_pct': (vs.get('base') or {}).get('annual_return_pct')})
    if variants and worlds:
        weight_of = {os.path.basename(x['params']['path']): x['weight'] for x in worlds['worlds']}
        for v in variants:
            v['weight'] = weight_of.get(f"lean_params_{v['name']}.json", 0.0)
        notes.append('the named variants are frozen by hash and value; weight = their weight in the worlds (0: saved next to '
                     'the main file, not one of the worlds in force)')
    elif variants:
        notes.append('the named variants are frozen by hash and value; a weighting across them is not in any file')
    if worlds:
        notes.append(f"the values are the weighted worlds of {worlds['source']} ({len(worlds['worlds'])} worlds, weights "
                     f"{', '.join(f'{k} {v:g}' for k, v in worlds['weights'].items())}: judgement); the forecast rows are the "
                     'weighted path')

    values = {'today': {k: v['value_today'] for k, v in scen.items()}, 'weighted_today': result.get('weighted_value_today',
              (result.get('probability_weighted') or {}).get('today')),
              'year_end_dates': year_end_dates, 'year_end': {k: v['year_end'] for k, v in scen.items()},
              'annual_return_pct': {k: v['annual_return_pct'] for k, v in scen.items()},
              'probabilities': {k: v for k, v in (params.get('probabilities') or {}).items() if k in SCEN} or None}
    if worlds:
        values['weighted_today'] = worlds['probability_weighted_value_today']
    record = {'frozen_by': FROZEN_BY, 'kind': spec['kind'], 'model': spec['model'], 'ticker': ticker, 'as_of': as_of, 'price': price,
              'currency': cur, 'folder': _rel(folder, root), 'files': files, 'values': values, 'calibrated': cal,
              'two_lens': {k: (result.get('two_lens') or {}).get(k) for k in ('agree', 'rule')} if result.get('two_lens') else None,
              'buy_test': _buy_test(result, scen, cal, rerun_bt),
              'entry_prices': {k: result.get(k) for k in ('buy_price', 'index_parity_price', 'index_return_pct') if k in result} or None,
              'buy_return': params.get('buy_return'), 'review': review, 'variants': variants or None, 'rerun': rerun,
              'falsifiers': list(falsifiers) if falsifiers else None}
    if worlds:
        record.update(two_lens=w_two, buy_test=w_bt, entry_prices=w_entry, worlds=worlds)
    if crule:
        record.update(buy_test=_buy_test(rule_new, scen, cal), calibrated_rule=crule,
                      two_lens={k: (rule_new.get('two_lens') or {}).get(k) for k in ('agree', 'rule')} if rule_new.get('two_lens')
                      else record['two_lens'])
        notes.append(f"the second lens and the buy test use the calibrated rule {crule['pe_rule']} ({crule['path']}); the "
                     'scenario values are the saved result')
    if not lean:
        record['note'] = f"{CUSTOM_NOTE}: {spec.get('why')}; values from the saved result file"
    paths, detail = (w_paths, w_detail) if worlds else (_paths(spec['model'], result, cur), None)
    if not lean and not paths:
        notes.append('no forecast rows: the saved result has no per-year path this module reads')
    record['notes'] = notes
    fp = {'params': files.get('params', {}).get('canonical_sha256'), 'inputs': inputs, 'values': values, 'calibrated': cal,
          'paths': [[m, b, s] for m, _, _, b, s in paths], 'falsifiers': record['falsifiers']}
    if crule:            # only here, as for the worlds: other folders keep the fingerprint they always had
        fp['calibrated_rule'] = crule['canonical_sha256']
    if worlds:           # only here: the fingerprint of a valuation without worlds is what it always was
        fp['worlds'] = {'file': (worlds['file'] or {}).get('canonical_sha256'), 'weights': worlds['weights'],
                        'files': {x['name']: x['params']['canonical_sha256'] for x in worlds['worlds']}}
    record['fingerprint'] = canonical_sha256(fp)
    return record, {'paths': paths, 'base_year': base_year, 'base_end': base_end, 'detail': detail,
                    'weighted_note': WORLDS_PATH_NOTE.format(weights=', '.join(f'{k} {v:g}' for k, v in worlds['weights'].items()))
                    if worlds else None}


# ------------------------------------------------------------------ freeze

def freeze_folder(folder, ledger, *, root=None, run=None, lean_model_path=None, next_report=None, falsifiers=None,
                  legacy_path=None, spec=None):
    """Record one valuation folder. Returns what was written; nothing is forced past a ledger refusal.

    ledger: path or a Ledger opened with open_ledger. run: lean_model.run (optional; see the module docstring).
    next_report: a date string or {'date', 'confirmed', 'source'}; default: the handoff written for this valuation."""
    root = root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(folder)))))
    L = ledger if isinstance(ledger, Ledger) else open_ledger(ledger)
    first = genesis(L, legacy_path if legacy_path is not None else os.path.join(root, LEGACY))
    spec = spec or classify(folder)
    if spec['kind'] is None:
        raise ValueError(spec['reason'])
    rows = L.lines()
    mine = [r for r in rows if r.get('type') == 'thesis' and (r.get('record') or {}).get('frozen_by', '').startswith('lean_freeze')]
    result_file = _rel(os.path.join(folder, spec['result']), root)     # one thesis line per saved result file
    same = [r for r in mine if r['record']['files']['result']['path'] == result_file]
    if falsifiers is None and same:
        falsifiers = same[-1]['record'].get('falsifiers')              # not supplied: the ones on file stay
    record, meta = build_record(folder, root, spec=spec, run=run, lean_model_path=lean_model_path, falsifiers=falsifiers)
    ticker, as_of = record['ticker'], record['as_of']
    out = {'ticker': ticker, 'as_of': as_of, 'folder': record['folder'], 'kind': record['kind'], 'model': record['model'],
           'base_value_today': record['values']['today'].get('base'), 'genesis_written': bool(first), 'rows_written': int(bool(first)),
           'forecasts_written': 0, 'rejected': [], 'not_forecast': []}
    today = day(L.now())
    if same and same[-1]['record'].get('fingerprint') == record['fingerprint']:
        have = same[-1]
        tid, made, status = have['thesis_id'], have['made_at'], 'unchanged'
        record = have['record']
        if have['record']['files']['result']['sha256'] != sha256_file(os.path.join(root, have['record']['files']['result']['path'])):
            out['note'] = 'the result file was rewritten since the freeze; the frozen numbers are unchanged'
    else:
        name = os.path.basename(result_file)
        prev = same[-1] if same else next((r for r in reversed(mine) if r['entity'] == ticker and r['record']['as_of'] <= as_of
                                           and (r['record']['kind'] != 'lean' or record['kind'] != 'lean'
                                                or os.path.basename(r['record']['files']['result']['path']) == name)), None)
        latest = max((r['made_at'] for r in rows if r.get('made_at')), key=day, default=None)
        no_worlds_before = bool(same and record.get('worlds') and not same[-1]['record'].get('worlds'))
        no_rule_before = bool(same and record.get('calibrated_rule') and not same[-1]['record'].get('calibrated_rule'))
        if no_worlds_before:
            made, basis = today.isoformat(), ('recording day: the frozen row of this valuation was made without the weighted '
                                              'worlds it publishes')
        elif same:
            made, basis = today.isoformat(), 'recording day: the inputs of a frozen valuation changed after its freeze'
        elif latest and day(latest) > _d(as_of):
            made, basis = today.isoformat(), f'recording day: as_of {as_of} is earlier than an entry already on file ({latest})'
        else:
            made, basis = as_of, 'params as_of'
        nr = next_report or handoff_next_report(root, ticker, as_of)
        lag = (today - _d(made)).days
        record.update(made_at_basis=basis, next_report=_next_report(nr, 'supplied to the freeze'), on_time=on_time(today, nr),
                      recording_lag_days=lag, late_recording=lag > LATE_RECORDING_DAYS,
                      supersedes=prev['thesis_id'] if prev else None,
                      supersede_reason=None if not prev else
                      (f"the frozen row was made without the weighted worlds this valuation publishes ({record['worlds']['source']}): "
                       'the values and the forecast path are now the weighted ones') if no_worlds_before
                      else (f"the frozen row was made without the calibrated rule this valuation publishes "
                            f"({record['calibrated_rule']['path']}): the second lens and the buy test are now the rule's")
                      if no_rule_before
                      else 'the inputs of this valuation changed after its freeze' if same
                      else 'a newer valuation of the same company')
        tid = f"lean:{ticker}:{as_of}:{record['fingerprint'][:12]}"
        n = sum(r['thesis_id'].startswith(tid) for r in mine)
        tid += f':r{n + 1}' if n else ''
        version = f"lean_model/{(record['files']['lean_model']['sha256'] or 'unknown')[:12]}" if record['kind'] == 'lean' else f"custom/{record['model']}"
        L.thesis(thesis_id=tid, entity=ticker, made_at=made, model_version=version, record=record)
        out['rows_written'] += 1
        status = 'superseded an earlier freeze' if same else 'frozen'
    specs, past = (_forecasts(meta['paths'], meta['base_year'], meta['base_end'], _d(as_of), today, meta.get('detail'))
                   if meta['paths'] and meta['base_end'] else ([], []))
    rows = L.lines()
    ids = {r.get('id') for r in rows}
    old = [r for r in rows if r.get('type') == 'forecast' and r.get('thesis_id') == record.get('supersedes')] if record.get('supersedes') else []
    version = next(r['model_version'] for r in rows if r.get('type') == 'thesis' and r['thesis_id'] == tid)
    for f in specs:
        fid = f"{tid}:{f['metric']}:{f['fiscal_year']}"
        if fid in ids:
            continue
        prior = [r for r in old if r['metric'] == f['metric'] and r.get('basis') == f['basis']
                 and abs((day(r['period_end']) - _d(f['period_end'])).days) <= FY_MATCH_DAYS]
        ctx = {'thesis_id': tid, 'frozen_by': FROZEN_BY, 'fiscal_year': f['fiscal_year'], 'period_kind': 'fiscal_year',
               'horizon_years': f['horizon_years'], 'sided': 'two', 'vintage': 'revision' if prior else 'original',
               'on_time': record.get('on_time')}
        note = 'scenario paths cross for this metric' if f['scenario_order'] == 'crossing' else ''
        if f.get('weighted'):
            ctx.update(path='weighted_worlds', weights=(record.get('worlds') or {}).get('weights'), weighted_worlds=f['weighted'])
            note = '; '.join(x for x in (meta.get('weighted_note'), note) if x)
        try:
            L.forecast(id=fid, entity=ticker, made_at=made, metric=f['metric'], period_end=f['period_end'], scenarios=f['scenarios'],
                       unit=f['unit'], currency=f['currency'], basis=f['basis'], model_version=version, price=record['price'],
                       inputs_hash=(record['files'].get('params') or record['files']['result'])['sha256'], thesis_id=tid,
                       supersedes=prior[-1]['id'] if prior else None, scenario_order=f['scenario_order'], context=ctx,
                       note=note)
            out['forecasts_written'] += 1
        except ValueError as e:
            out['rejected'].append({'id': fid, 'reason': str(e)})
    out['rows_written'] += out['forecasts_written']
    out.update(status=status if status != 'unchanged' or not out['forecasts_written'] else 'completed missing forecast rows',
               thesis_id=tid, made_at=made, on_time=record.get('on_time'), late_recording=record.get('late_recording'),
               supersedes=record.get('supersedes'), not_forecast=past, notes=record.get('notes') or [],
               worlds=(record.get('worlds') or {}).get('weights'))
    return out


def freeze(params_path, result_path, falsifiers=None, *, ledger, **kw):
    """Freeze one lean valuation given its two files (the main pair of a dated folder, or a named pair in it)."""
    folder = os.path.dirname(os.path.abspath(params_path))
    if os.path.dirname(os.path.abspath(result_path)) != folder:
        raise ValueError('the params and the result must sit in the same valuation folder')
    ok, why = lean_format(_load(params_path))
    if not ok:
        raise ValueError(f'{why}: freeze the folder instead (custom models are read from their saved result)')
    spec = {'kind': 'lean', 'model': 'lean_model', 'params': os.path.basename(params_path), 'result': os.path.basename(result_path)}
    return freeze_folder(folder, ledger, falsifiers=falsifiers, spec=spec, **kw)


def freeze_all(root, ledger, *, run=None, lean_model_path=None, next_reports=None, skip=()):
    """Every published lean or custom valuation under research/companies, in as_of order.

    next_reports: {'T' or 'T/as_of': date string or {'date', 'confirmed', 'source'}} for valuations without a handoff.
    A date keyed by the ticker alone is the company's upcoming report, so it is given only to the latest valuation of
    that company: an older one may have a report between it and today, and reading the upcoming date for it would
    call a late freeze on time. An older valuation needs its own 'T/as_of' key or its handoff, else on_time is unknown."""
    L = ledger if isinstance(ledger, Ledger) else open_ledger(ledger)
    todo, left = published_folders(root, skip)
    latest = {os.path.basename(os.path.dirname(f)): os.path.basename(f) for f in todo}     # todo is in as_of order
    done = []
    for folder in todo:
        t, d = os.path.basename(os.path.dirname(folder)), os.path.basename(folder)
        nr = (next_reports or {}).get(f'{t}/{d}') or ((next_reports or {}).get(t) if latest[t] == d else None)
        try:
            done.append(freeze_folder(folder, L, root=root, run=run, lean_model_path=lean_model_path, next_report=nr))
        except (ValueError, KeyError, TypeError) as e:
            left.append({'folder': _rel(folder, root), 'reason': f'not frozen: {type(e).__name__}: {e}'})
    return {'frozen': done, 'left_out': left, 'ledger': L.verify()}


def status(ledger):
    """Verify result and the theses in force (the latest per company)."""
    L = ledger if isinstance(ledger, Ledger) else open_ledger(ledger)
    rows = L.lines()
    theses = [r for r in rows if r.get('type') == 'thesis']
    replaced = {r['record'].get('supersedes') for r in theses}
    return {'verify': L.verify(), 'rows': len(rows), 'theses': len(theses),
            'forecasts': sum(r.get('type') == 'forecast' for r in rows), 'outcomes': sum(r.get('type') == 'outcome' for r in rows),
            'in_force': [{'thesis_id': r['thesis_id'], 'ticker': r['entity'], 'as_of': r['record'].get('as_of'),
                          'base_value_today': r['record']['values']['today'].get('base'), 'on_time': r['record'].get('on_time'),
                          'late_recording': r['record'].get('late_recording'),
                          'worlds': (r['record'].get('worlds') or {}).get('weights')} for r in theses if r['thesis_id'] not in replaced]}
