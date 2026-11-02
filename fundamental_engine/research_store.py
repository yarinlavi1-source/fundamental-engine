"""Tracked research store: the persistent record of every full valuation (research/, in Git).

Why: runs/ is gitignored and a cloud container is ephemeral, so a finished run and the forecast ledger in
runs/tickers/ vanish between sessions and the next research on the company starts from nothing. `save` copies the
compact essentials of a finished fast-ticker run into research/companies/<T>/<as_of>/ and the frozen thesis rows into
research/forecast_ledger.jsonl; `load` returns the newest saved valuation, the starting point for the next update.

Company research only: never portfolio positions, balances or anything private (the copy list below is explicit).
The tracked ledger is a COPY of the runs/ ledger's rows for each saved thesis (thesis row plus its forecasts, verbatim
with their original hashes), append-only and deduplicated by thesis_id / forecast id; the hash chain is verified on the
source ledger, not on this subset.
"""
import json
import os
import shutil

from .ledger import Ledger, parse

STORE = os.path.join('research', 'companies')
LEDGER = os.path.join('research', 'forecast_ledger.jsonl')
RUN_LEDGER = os.path.join('runs', 'tickers', 'forecast_ledger.jsonl')
# run-relative files copied as they are ({T}: the ticker); review/*.json and position parts are listed below. The gather
# fragments, identity.json and understand/business.json make a saved valuation a reuse source for the next run
# (scripts/ticker_prefetch.py --reuse research/companies/<T>/<as_of>), so a fresh container can start from it (0.14.3)
COPY = ('companies/{T}/inputs.json', 'evidence_pack.json', 'status.json', 'companies/{T}/out/table_he.md',
        'companies/{T}/out/scenarios_he.md', 'companies/{T}/out/paragraph.txt', 'identity.json', 'understand/business.json',
        'gather/numbers.json', 'gather/documents.json', 'gather/field.json', 'gather/macro.json')
POSITION_PARTS = ('core', 'field', 'path')
CORE = 'casefile_core.json'


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)


def parse_run(run):
    """(ticker, as_of) from a run folder named <TICKER>_<YYYY-MM-DD>."""
    name = os.path.basename(os.path.normpath(run))
    t, _, d = name.rpartition('_')
    if not t or len(d) != 10:
        raise ValueError(f'{run}: run folder must be named <TICKER>_<YYYY-MM-DD>')
    parse(d)
    return t.upper(), d


def casefile_summary(cf):
    """The decision essentials of a casefile: verdict, expected return, fair values by date, assumption ids and values, hashes."""
    val, dc = cf.get('val') or {}, cf.get('dc') or {}
    res = val.get('result') if isinstance(val.get('result'), dict) else {}
    return {'company': cf.get('company'), 'as_of': cf.get('as_of'), 'engine_version': cf.get('engine_version'),
            'casefile_sha256': cf.get('casefile_sha256'), 'factbook_sha256': cf.get('factbook_sha256'),
            'inputs_sha256': cf.get('inputs_sha256'), 'decision': {k: dc.get(k) for k in ('verdict', 'confidence', 'expected_return')},
            'valuation_status': val.get('status'), 'annual_values': res.get('annual_values'),
            'assumptions': [{k: a.get(k) for k in ('id', 'value') if k in a} for a in (cf.get('fc') or {}).get('assumptions') or []
                            if isinstance(a, dict)],
            'review_status': (cf.get('rv') or {}).get('status')}


def casefile_core(cf):
    """The part of a casefile monitor.thesis_from_casefile reads (holdings_watch rebuilds thesis_id and irr_basis from it; checked
    equal to the full casefile on META and MA 2026-11-01), about 40-50 KB instead of 1.4-1.7 MB. The full casefile is rebuilt
    from the saved inputs.json with the same engine version when anything else is needed."""
    val = cf.get('val') or {}
    case = val.get('case') if isinstance(val.get('case'), dict) else {}
    res = val.get('result') if isinstance(val.get('result'), dict) else {}
    from .facts import digest           # monitor.thesis_from_casefile's own fallback when a casefile carries no hash
    sha = cf.get('casefile_sha256') or digest(json.loads(json.dumps(cf, default=str)))
    return {**{k: cf.get(k) for k in ('factbook_sha256', 'engine_version', 'company', 'as_of')}, 'casefile_sha256': sha,
            'val': {'case': {k: case.get(k) for k in ('quote', 'currency', 'opening', 'ticker')},
                    'result': {'annual_values': res.get('annual_values'),
                               'scenarios': [{k: x.get(k) for k in ('name', 'status', 'forecast', 'terminal')}
                                             for x in res.get('scenarios') or [] if isinstance(x, dict)]}},
            'meaning': 'casefile extract for thesis_from_casefile (research_store.casefile_core); not the full casefile'}


def thesis_rows(ledger_path, ticker, as_of):
    """Rows of the latest thesis frozen for ticker at as_of (thesis row first, then its forecasts); [] when none."""
    rows = Ledger(ledger_path).lines() if ledger_path and os.path.exists(ledger_path) else []
    th = [r for r in rows if r.get('type') == 'thesis' and str(r.get('entity')).upper() == ticker
          and (r.get('record') or {}).get('as_of') == as_of]
    if not th:
        return []
    top = max(th, key=lambda r: parse(r['made_at'])[0])
    return [top] + [r for r in rows if r.get('type') == 'forecast' and r.get('thesis_id') == top['thesis_id']]


def append_ledger(rows, path):
    """Append rows not yet in the tracked ledger (thesis by thesis_id, others by id). -> number appended."""
    have = Ledger(path).lines() if os.path.exists(path) else []
    keys = {(r.get('type'), r.get('thesis_id') if r.get('type') == 'thesis' else r.get('id')) for r in have}
    new = [r for r in rows if (r.get('type'), r.get('thesis_id') if r.get('type') == 'thesis' else r.get('id')) not in keys]
    if new:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')
    return len(new)


def save(run, root='.', ledger_path=None):
    """Copy a finished run into research/companies/<T>/<as_of>/, append its frozen thesis to research/forecast_ledger.jsonl
    and point research/companies/<T>/latest.json at it (when it is the newest)."""
    t, d = parse_run(run)
    dest = os.path.join(root, STORE, t, d)
    os.makedirs(dest, exist_ok=True)
    copied, missing = [], []
    rels = [p.format(T=t) for p in COPY] + [f'position/{p}.json' for p in POSITION_PARTS]
    rdir = os.path.join(run, 'review')
    rels += sorted(f'review/{n}' for n in (os.listdir(rdir) if os.path.isdir(rdir) else []) if n.endswith('.json'))
    for rel in rels:
        src = os.path.join(run, rel)
        if not os.path.isfile(src):
            missing.append(rel)
            continue
        out = os.path.join(dest, rel.replace(f'companies/{t}/out/', '').replace(f'companies/{t}/', ''))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(src, out)
        copied.append(os.path.relpath(out, dest))
    cfp = os.path.join(run, 'companies', t, 'out', 'casefile.json')
    summary = None
    if os.path.isfile(cfp):
        cf = _load(cfp)
        summary = casefile_summary(cf)
        _dump(os.path.join(dest, 'casefile_summary.json'), summary)
        copied.append('casefile_summary.json')
        with open(os.path.join(dest, CORE), 'w', encoding='utf-8') as f:
            json.dump(casefile_core(cf), f, ensure_ascii=False, separators=(',', ':'), default=str)
        copied.append(CORE)
    else:
        missing.append(f'companies/{t}/out/casefile.json')
    lp = ledger_path or os.path.join(root, RUN_LEDGER)
    rows = thesis_rows(lp, t, d)
    appended = append_ledger(rows, os.path.join(root, LEDGER))
    status = _load(os.path.join(run, 'status.json')) if os.path.isfile(os.path.join(run, 'status.json')) else {}
    entry = {'ticker': t, 'as_of': d, 'folder': os.path.relpath(dest, root), 'thesis_id': rows[0]['thesis_id'] if rows else None,
             'status': ('verified' if status.get('verified') else 'draft') if 'verified' in status else status.get('status'),
             'verdict': (((summary or {}).get('decision') or {}).get('verdict') or {}).get('code'),
             'files': sorted(copied)}
    latest_p = os.path.join(root, STORE, t, 'latest.json')
    prev = _load(latest_p) if os.path.isfile(latest_p) else None
    if not prev or prev.get('as_of', '') <= d:
        _dump(latest_p, entry)
    return {**entry, 'missing': missing, 'ledger_rows_appended': appended, 'ledger': os.path.join(root, LEDGER),
            'latest_updated': not prev or prev.get('as_of', '') <= d,
            'note': None if rows else f'no frozen thesis for {t} at {d} in {lp}: freeze it, then save again'}


def load(ticker, root='.'):
    """The newest saved valuation of a ticker: latest.json plus its casefile summary and thesis headline; None when none."""
    t = str(ticker).upper().strip()
    p = os.path.join(root, STORE, t, 'latest.json')
    if not os.path.isfile(p):
        return unsaved_run(t, root)
    entry = _load(p)
    folder = os.path.join(root, entry['folder'])
    sp, tp = os.path.join(folder, 'casefile_summary.json'), os.path.join(folder, 'thesis.json')
    s = _load(sp) if os.path.isfile(sp) else {}
    if os.path.isfile(tp):                         # saves before 0.14.3 kept the thesis row beside the casefile
        th = _load(tp).get('record') or {}
    else:                                          # the tracked ledger holds it (by thesis_id)
        lp = os.path.join(root, LEDGER)
        th = next((r.get('record') or {} for r in (Ledger(lp).lines() if os.path.isfile(lp) else [])
                   if r.get('type') == 'thesis' and r.get('thesis_id') == entry.get('thesis_id')), {})
    return {**entry, 'path': folder, 'verdict_reason_he': ((s.get('decision') or {}).get('verdict') or {}).get('reason_he'),
            'expected_return': (s.get('decision') or {}).get('expected_return'),
            'values': {k: (th.get('values') or {}).get(k) for k in ('today', '5y')} if th else None,
            'price_at_analysis': th.get('price_at_analysis'), 'review_by': th.get('review_by'),
            'saved': sorted(n for n in os.listdir(os.path.join(root, STORE, t)) if n != 'latest.json'),
            'next': f'start from this valuation: python scripts/ticker_prefetch.py {t} <as_of> --reuse {entry["folder"]}; update only '
                    'what happened since its as_of'}


def unsaved_run(ticker, root='.'):
    """No tracked save: the newest runs/tickers/<T>_<date> that holds a casefile, labelled unsaved; None when none."""
    base = os.path.join(root, 'runs', 'tickers')
    best = None
    for name in os.listdir(base) if os.path.isdir(base) else []:
        try:
            t, d = parse_run(name)
        except ValueError:
            continue
        cfp = os.path.join(base, name, 'companies', t, 'out', 'casefile.json')
        if t == ticker and os.path.isfile(cfp) and (best is None or d > best[0]):
            best = (d, name)
    if not best:
        return None
    run = os.path.join(base, best[1])
    st = _load(os.path.join(run, 'status.json')) if os.path.isfile(os.path.join(run, 'status.json')) else {}
    return {'ticker': ticker, 'as_of': best[0], 'folder': os.path.relpath(run, root), 'path': run, 'saved': False,
            'status': ('verified' if st.get('verified') else 'draft') if 'verified' in st else None,
            'note': 'not in the tracked research store (research-save was not run): this runs/ folder is lost with the container',
            'next': f'start from it: python scripts/ticker_prefetch.py {ticker} <as_of> --reuse {os.path.relpath(run, root)}; '
                    f'and save it: python -m fundamental_engine research-save {os.path.relpath(run, root)}'}


def saved_folder(root, ticker, as_of):
    """The tracked folder of a saved valuation, else the runs/ folder that made it, else None."""
    for p in (os.path.join(root, STORE, ticker, as_of), os.path.join(root, 'runs', 'tickers', f'{ticker}_{as_of}')):
        if os.path.isdir(p):
            return os.path.relpath(p, root)
    return None
