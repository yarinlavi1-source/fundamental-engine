"""Fast ticker procedure: the deterministic steps between the agents (no LLM, no network; CLAUDE.md "On a ticker").

    ticker-check  <run> <file>   one gather fragment or one position part, validated in its run (the author runs it
                                 before replying, so the merged pack passes; it prints the guidance track record too)
    ticker-prep   <run>          after the four lanes: every fragment checked, understand/auto.json (the deterministic
                                 readings: fiscal years, track record, history ratios, ROIC, forensic flags, consensus),
                                 evidence_ids.json (every id the pack offers, one line each) and position/skeleton_<part>.json
    ticker-value  <run>          position/core.json + field.json + path.json -> position.json (and position/coherence.md),
                                 pack-merge, pack-to-inputs, deep for this one company -> value.json (ran, stages, table)
    ticker-trace  <run>          the pack's and the table's numbers traced to their sources and to the saved text (a
                                 reviewer's aid: a candidate match is checked by reading it, never taken as proof)
    ticker-status <run>          verified or draft, decided from THIS attempt's files only (newer than prefetch.json
                                 started_at_utc) together with the main loop's own list of agents that returned; stage times

The three analyst parts write disjoint pack keys (PARTS): `core` (the analyst: drivers, forecast_options, scenario_events,
key_risk, falsifiers, and dataqa_resolutions when a data conflict is resolved from the filing), `field` (potential, assessments) and `path` (company_path). Each part also carries thesis_he
{bear, base, bull}: one line per scenario of what that part assumes, shown side by side in position/coherence.md so the
reviewers can see that the three parts tell one story per scenario. Nothing here judges, estimates or fills in a number:
the evidence pack rules (evidence_pack.to_inputs) are the only gate, and the engine runs exactly as `deep` runs it.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from . import evidence_pack as EP

PARTS = {'core': ('drivers', 'forecast_options', 'scenario_events', 'key_risk', 'falsifiers', 'dataqa_resolutions'),
         'field': ('potential', 'assessments'),
         'path': ('company_path',)}
PART_STAGE = {'core': 'analyst', 'field': 'analyst_field', 'path': 'analyst_path'}
PART_EXTRA = ('thesis_he', 'reasons_he', 'gaps', 'notes', 'engine_options')      # kept in position.json, not pack keys
IDENTITY_KEYS = ('name', 'currency', 'exchange', 'security', 'fiscal_year_end', 'cik', 'type', 'evidence_ids')
FRAGMENT_META = ('lane', 'gaps', 'stopped')
LANE_PREFIX = {'numbers.json': 'nm_', 'documents.json': 'dc_', 'field.json': 'fd_', 'macro.json': 'mc_',
               'understand_core.json': 'uc_', 'understand_field.json': 'uf_', 'understand_path.json': 'up_',
               'understand.json': 'un_', 'repair.json': 'rf_', '_prefetch.json': 'pf_'}
MARKET_OWNERS = ('_prefetch.json', 'macro.json', 'repair.json')
REVIEWS = {'review_trace': 'review/trace.json', 'review_numbers': 'review/numbers.json', 'review_counter': 'review/counter.json'}
RUN_RE = re.compile(r'^([A-Z0-9.\-]{1,12})_(\d{4}-\d{2}-\d{2})$')
WHERE_RE = re.compile(r'^([a-z_]+)(?:\[(\d+)\])?')


# ---------------------------------------------------------------- run folder, files, pack

def run_info(run):
    """-> (run Path, ticker, as_of) for runs/tickers/<TICKER>_<as_of>."""
    p = Path(run).resolve()
    m = RUN_RE.match(p.name)
    if not m or not p.is_dir():
        raise ValueError(f'{run}: not a run folder runs/tickers/<TICKER>_<as_of>')
    return p, m.group(1), m.group(2)


def _load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False, default=str), encoding='utf-8')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def fragments(run):
    """[(file name, fragment or the load error)] for gather/*.json in pack-merge's order (sorted: _prefetch.json first)."""
    out = []
    for f in sorted((Path(run) / 'gather').glob('*.json')):
        try:
            out.append((f.name, json.loads(f.read_text(encoding='utf-8'))))
        except (OSError, ValueError) as e:
            out.append((f.name, e))
    return out


def identity(run):
    ident = _load(Path(run) / 'identity.json', {}) or {}
    return {k: ident[k] for k in IDENTITY_KEYS if ident.get(k) is not None}


def merge_pack(run, position=None, order=None):
    """The evidence pack exactly as `pack-merge` builds it (gather/*.json sorted, identity.json, position) -> (pack, report).
    order: the fragments to merge (default: every readable one in pack-merge's order)."""
    run, t, as_of = run_info(run)
    frs = order if order is not None else [fr for _, fr in fragments(run) if isinstance(fr, dict)]
    return EP.merge_fragments(t, as_of, identity(run), frs, position or {})


def _problems(pack):
    """Every problem evidence_pack.to_inputs lists (one line each); [] when the pack passes."""
    try:
        EP.to_inputs(pack)
        return []
    except ValueError as e:
        msg = str(e)
        if msg.startswith('evidence pack invalid:'):
            return [ln[2:] for ln in msg.split('\n')[1:] if ln.startswith('- ')]
        return [msg]
    except Exception as e:      # a malformed value the validator did not foresee: still a listed problem, never a crash
        return [f'validator error {type(e).__name__}: {e}']


def _where(line):
    """'facts[12]: ...' -> ('facts', 12); 'drivers.growth: ...' -> ('drivers', None); 'pack: potential required' -> ('pack', None)."""
    m = WHERE_RE.match(line)
    return (m.group(1), int(m.group(2)) if m.group(2) else None) if m else (None, None)


# ---------------------------------------------------------------- ticker-check

def check(run, file):
    """Validate one gather fragment (gather/<lane>.json) or one position part (position/<part>.json) in its run."""
    run, t, as_of = run_info(run)
    path = Path(file)
    path = path if path.is_absolute() else (run / path if (run / path).exists() else Path.cwd() / path)
    path = path.resolve()
    if path.parent == run / 'gather':
        return _check_fragment(run, path)
    if path.parent == run / 'position' and path.stem in PARTS:
        return _check_part(run, path)
    raise ValueError(f'{file}: not a gather fragment ({run}/gather/<lane>.json) or a position part '
                     f'({run}/position/{{{",".join(PARTS)}}}.json)')


def _check_fragment(run, path):
    out = {'file': str(path), 'kind': 'fragment', 'problems': [], 'warnings': []}
    frag = _load(path)
    if not isinstance(frag, dict):
        out['problems'].append('not a JSON object (or not readable)')
        out['ok'] = False
        return out
    name = path.name
    allowed = set(EP.LIST_KEYS) | {'market'} | set(FRAGMENT_META)
    extra = sorted(set(frag) - allowed)
    if extra:
        out['problems'].append(f'unknown top-level keys {extra}: a fragment holds {", ".join(sorted(allowed))}')
    if 'market' in frag and name not in MARKET_OWNERS:
        out['warnings'].append('market belongs to the prefetch and gather_macro: remove it from this fragment (at merge a later '
                               'file overrides a fresher rate)')
    prefix = LANE_PREFIX.get(name)
    if prefix:
        bad = [x.get('id') for k in EP.LIST_KEYS if k not in ('guidance_track', 'notes', 'competitors')
               for x in frag.get(k) or [] if isinstance(x, dict) and x.get('id') and not str(x['id']).startswith(prefix)]
        if bad:
            out['warnings'].append(f'ids without the lane prefix {prefix} (they can collide with another lane): {bad[:8]}')
    others = [fr for n, fr in fragments(run) if n != name and isinstance(fr, dict)]
    try:
        base, _ = merge_pack(run, order=others)
        pack, rep = merge_pack(run, order=others + [frag])
    except ValueError as e:
        out['problems'].append(str(e))
        out['ok'] = False
        return out
    n_before = {k: len(base.get(k) or []) for k in EP.LIST_KEYS}
    mine_src = {s.get('id') for s in frag.get('sources') or [] if isinstance(s, dict)}
    mine_comp = {str(c.get('ticker')).upper() for c in frag.get('competitors') or [] if isinstance(c, dict)}
    for line in _problems(pack):
        key, i = _where(line)
        if key in EP.POSITION_KEYS or (key == 'pack' and any(f'{k} required' in line for k in EP.POSITION_KEYS)):
            continue                                   # the position is not written yet
        if key == 'competitors' and i is not None:
            c = (pack.get('competitors') or [None] * (i + 1))[i]
            if isinstance(c, dict) and str(c.get('ticker')).upper() in mine_comp:
                out['problems'].append(line)
        elif key in EP.LIST_KEYS and i is not None:
            if i >= n_before[key]:
                x = (pack.get(key) or [])[i] if i < len(pack.get(key) or []) else None
                ident = f" (id {x.get('id')})" if isinstance(x, dict) and x.get('id') else ''
                out['problems'].append(line + ident)
        elif key == 'source' and any(f'source {s}:' in line for s in mine_src):
            out['problems'].append(line)
        elif key == 'market' and 'market' in frag:
            out['problems'].append(line)
        elif key == 'identity':
            out['warnings'].append(f'{line} (identity.json: gather_numbers completes it)')
    if rep.get('renamed_sources'):
        mine = {k: v for k, v in rep['renamed_sources'].items() if k in mine_src}
        if mine:
            out['warnings'].append(f'the same URL is registered by another fragment too ({mine}): pack-merge keeps one id and '
                                   'renames the other everywhere (nothing to fix; a pf_ id always wins)')
    out['counts'] = {k: len(frag.get(k) or []) for k in EP.LIST_KEYS if frag.get(k)}
    if frag.get('guidance_track'):
        p = EP.Pack(pack)
        out['track_record'] = EP.track_record(p)
    out['ok'] = not out['problems']
    return out


def _part_problems(part, data):
    keys, out = PARTS[part], []
    owner = {k: p for p, ks in PARTS.items() for k in ks}
    for k in data:
        if k.startswith('_') or k in keys or k in PART_EXTRA:
            continue
        out.append(f'{k}: belongs to part {owner[k]} ({PART_STAGE[owner[k]]})' if k in owner else
                   f'{k}: not a key of part {part} (allowed: {", ".join(keys + PART_EXTRA)})')
    th = data.get('thesis_he')
    if not (isinstance(th, dict) and all(isinstance(th.get(s), str) and th[s].strip() for s in EP.SCENARIOS)):
        out.append('thesis_he: one line per scenario {bear, base, bull} (what this part assumes; position/coherence.md shows '
                   'the three parts side by side)')
    return out


def _check_part(run, path):
    part = path.stem
    out = {'file': str(path), 'kind': 'part', 'part': part, 'stage': PART_STAGE[part], 'problems': [], 'warnings': []}
    data = _load(path)
    if not isinstance(data, dict):
        out['problems'].append('not a JSON object (or not readable)')
        out['ok'] = False
        return out
    out['problems'] += _part_problems(part, data)
    keys = PARTS[part]
    try:
        pack, _ = merge_pack(run, position={k: data[k] for k in keys if k in data})
    except ValueError as e:
        out['problems'].append(f'the gather fragments do not merge: {e}')
        out['ok'] = False
        return out
    for line in _problems(pack):
        key, _ = _where(line)
        if key in keys or (key == 'pack' and any(f'{k} required' in line for k in keys)):
            out['problems'].append(line)
    if part == 'core':
        for lever, d in (data.get('drivers') or {}).items():
            st = d.get('source_tier') if isinstance(d, dict) else None
            tiers = set(st.values()) if isinstance(st, dict) else {st}
            if tiers & {'analyst', 'market'}:
                out['warnings'].append(f'drivers.{lever}: tier analyst/market on a company lever stays a visible gap in the table')
    out['ok'] = not out['problems']
    return out


# ---------------------------------------------------------------- ticker-prep: deterministic readings and skeletons

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _scalars(d):
    return {k: v for k, v in (d or {}).items() if isinstance(v, (int, float, str, bool)) or v is None}


def _history(run):
    case = _load(Path(run) / 'prefetch' / 'history.json', {}) or {}
    rows, prev = [], None
    for r in case.get('history') or []:
        rev = r.get('revenue')
        row = {'fiscal_year': r.get('fiscal_year'), 'period_end': r.get('period_end'), 'revenue': rev,
               'source_ids': r.get('source_ids')}
        if _num(rev) and rev > 0:
            if prev:
                row['growth'] = round(rev / prev - 1, 4)
            for k, label in (('gross_profit', 'gross_margin'), ('operating_income', 'operating_margin'), ('net_income', 'net_margin')):
                if _num(r.get(k)):
                    row[label] = round(r[k] / rev, 4)
            if _num(r.get('operating_cash_flow')) and _num(r.get('capex')):
                row['fcf_margin'] = round((r['operating_cash_flow'] - r['capex']) / rev, 4)
            prev = rev
        rows.append(row)
    return rows


def _catalogue(pack):
    """Every id the merged pack offers, one compact line each (sources with kind, date and citability; items with what they
    say), so an analyst part cites ids without reading every fragment."""
    p = EP.Pack(pack)
    out = []
    for sid, s in p.sources.items():
        out.append({'id': sid, 'is': 'source', 'kind': s.get('kind'), 'date': s.get('date'), 'citable': p.citable(sid),
                    'label': str(s.get('title'))[:110]})
    for iid, (key, x) in p.items.items():
        val = x.get('value', x.get('revenue', x.get('share')))
        if key == 'guidance':
            val = f"{x.get('low')}..{x.get('high')}"
        out.append({'id': iid, 'is': key, 'citable': p.item_citable(iid),
                    'label': f"{x.get('metric') or x.get('name') or ''} {x.get('period') or ''} = {val} {x.get('unit') or ''}".strip(),
                    'cites': x.get('source_ids') or [x.get('source_id')]})
    for cid, x in p.consensus_rows.items():
        out.append({'id': cid, 'is': 'consensus', 'label': f"{x.get('metric')} {x.get('period')} = {x.get('value')} (n {x.get('n')})",
                    'cites': [x.get('source_id')]})
    for i, b in enumerate(pack.get('bottlenecks') or []):
        if isinstance(b, dict) and b.get('id'):
            out.append({'id': b['id'], 'is': 'bottleneck (not citable as evidence)', 'label': str(b.get('reading'))[:110]})
    for t, c in p.competitors.items():
        out.append({'id': t, 'is': 'competitor (tier competitor names it; cite its source_ids)', 'label': str(c.get('why'))[:110],
                    'cites': c.get('source_ids')})
    return out


def auto_readings(run, pack=None):
    """understand/auto.json: what can be computed without judgement from the prefetch and the fragments."""
    run, t, as_of = run_info(run)
    pack = pack if pack is not None else merge_pack(run)[0]
    p = EP.Pack(pack)
    cur, nxt = p.fiscal_year(0), p.fiscal_year(1)
    guid = [g for g in pack.get('guidance') or [] if isinstance(g, dict)]
    forensic = _load(run / 'prefetch' / 'forensic.json', {}) or {}
    typ = _load(run / 'prefetch' / 'classify.json', {}) or {}
    notes = [n for _, fr in fragments(run) if isinstance(fr, dict) for n in fr.get('notes') or []
             if isinstance(n, str) and 'OWN MULTIPLE HISTORY' in n.upper()]
    lanes = {n: {'gaps': fr.get('gaps') or [], 'stopped': fr.get('stopped') or []} for n, fr in fragments(run)
             if isinstance(fr, dict) and n != '_prefetch.json'}
    reviews = {k: str(f.relative_to(run)) for k, f in (('quarter', run / 'understand' / 'quarter_result.json'),
                                                        ('econ', run / 'understand' / 'econ_result.json')) if f.exists()}
    return {
        'ticker': t, 'as_of': as_of, 'built_by': 'ticker_run.auto_readings (deterministic; no judgement)',
        'fiscal_year_in_progress': {'label': cur[0], 'end': cur[1].isoformat()} if cur else None,
        'next_fiscal_year': ({'label': nxt[0], 'end': nxt[1].isoformat(),
                              'meaning': 'growth_anchor / growth state this period; the year in progress is '
                                         'drivers.current_year_revenue'} if nxt else None),
        'revenue_guidance_for_year_in_progress': [g['id'] for g in guid if cur and g.get('period') == cur[0]
                                                  and 'revenue' in str(g.get('metric')).lower()],
        'guidance_items': [{'id': g.get('id'), 'metric': g.get('metric'), 'period': g.get('period'), 'low': g.get('low'),
                            'high': g.get('high'), 'issued': g.get('issued'), 'citable': p.item_citable(g['id'])
                            if g.get('id') in p.items else False} for g in guid],
        'track_record': EP.track_record(p),
        'history': _history(run),
        'forensic': {k: _scalars(v) for k, v in forensic.items() if isinstance(v, dict)},
        'computed_type': {k: typ.get(k) for k in ('stage', 'lynch', 'reasons', 'flags') if k in typ} or None,
        'consensus': [{k: x.get(k) for k in ('id', 'metric', 'period', 'value', 'n', 'low', 'high')}
                      for x in pack.get('consensus') or [] if isinstance(x, dict)],
        'competitors': [{'ticker': x.get('ticker'), 'name': x.get('name'), 'why': x.get('why')}
                        for x in pack.get('competitors') or [] if isinstance(x, dict)],
        'peers_prefetched': _load(run / 'prefetch' / 'peers.json'),
        'own_multiple_history_notes': notes,
        'reviews_saved_by_gather_documents': reviews,
        'lanes': lanes,
    }


def _ids_hint(n=3):
    return [f'<evidence id {i + 1} from evidence_ids.json>' for i in range(n)]


def skeleton(part, run=None):
    """A position part with every required key, the allowed values and the period labels of this run filled in. Placeholders
    are '<...>' strings: a part that keeps one fails ticker-check."""
    if part not in PARTS:
        raise ValueError(f'unknown part {part!r}; parts: {", ".join(PARTS)}')
    cur = nxt = None
    as_of = '<as_of>'
    if run is not None:
        r, _, as_of = run_info(run)
        try:
            p = EP.Pack(merge_pack(r)[0])
            cur, nxt = p.fiscal_year(0), p.fiscal_year(1)
        except ValueError:
            pass
    cur_l, nxt_l = (cur[0] if cur else '<FY in progress>'), (nxt[0] if nxt else '<FY after the one in progress>')
    tiers = '|'.join(EP.TIERS)
    common = {'_how': f'copy to position/{part}.json, fill every <...>, remove what you cannot evidence, validate with '
                      f'`python -m fundamental_engine ticker-check <run> <run>/position/{part}.json` until ok. Top-level keys '
                      'starting with _ are ignored; every nested key is checked (unknown keys are rejected at every level)',
              'thesis_he': {s: f'<one plain Hebrew line: what this part assumes in the {s} scenario>' for s in EP.SCENARIOS}}
    if part == 'core':
        from .forecast import ECON_LEVERS, TERMINAL_KEYS, FINANCING_MODES, CYCLE_TREATMENTS
        from .drivers import RATIO_KEYS, ABSOLUTE_KEYS
        sc = {s: '<decimal>' for s in EP.SCENARIOS}
        return {**common,
                '_allowed': {'economic levers (one number per scenario)': list(ECON_LEVERS),
                             'driver keys (a number or a yearly list)': sorted(RATIO_KEYS) + list(ABSOLUTE_KEYS),
                             'terminal.<key>': list(TERMINAL_KEYS),
                             'growth': f'a yearly list per scenario; its first value is {nxt_l}',
                             'source_tier': f'one of {tiers}, or one per scenario {{bear, base, bull}}',
                             'competitors': 'only with tier competitor: "competitors": [named tickers of field.json]',
                             'forecast_options': {'horizon_years': '1-30 (optional)',
                                                  'financing': f'{"|".join(FINANCING_MODES)} (optional)',
                                                  'acquired_revenue': '{value, acquisition, closed, reason, evidence_ids} (optional): '
                                                                      f'revenue an acquisition brought into the step into {cur_l}, at its '
                                                                      'pre-deal size; kept out of the growth reinvestment (its price is in '
                                                                      'the opening balance)',
                                                  'debt_like_claims': '[{kind, amount, reason, evidence_ids}] (optional): claims ahead '
                                                                      'of the shares the debt lines miss (a preferred stock at its '
                                                                      'liquidation preference), citing the filing note; added to '
                                                                      'opening debt'},
                             'dataqa_resolutions': '[{field, metric, period_end, resolution label_difference|use_source, source_id, '
                                                   'reason, evidence_ids}] (optional, only for a cross-source conflict dataqa blocks on: '
                                                   'qa.fields.<field>.note names the sources; filing evidence required)',
                             'scenario_event': '{"event_he", "evidence_ids"} or {"event_he", "hypothesis": true}'},
                'drivers': {
                    'current_year_revenue': {
                        'source_tier': 'guidance',
                        'evidence_ids': [f'<the ONE revenue guidance item for {cur_l}>', '<the remainder guidance item>',
                                         '<the release that guided>'],
                        'remainder': '<guidance id of the part not yet reported (e.g. Q4), or remove>',
                        'adjust_by_track_record': '<true|false>', 'reason': '<why; the ratio applies to the remainder only>'},
                    'growth_anchor': {'period': nxt_l, **sc, 'source_tier': f'<{tiers}>', 'evidence_ids': _ids_hint(),
                                      'adjust_by_track_record': '<true|false|[scenarios]: required for a guidance tier>',
                                      'reason': '<why these values; a guidance/consensus value sits inside the cited range '
                                                'for that year, else state deviation>'},
                    'mature_margin': {**sc, 'source_tier': f'<{tiers}>', 'evidence_ids': _ids_hint(), 'reason': '<why>'},
                },
                'forecast_options': {'cyclical': {'treatment': f'<{"|".join(CYCLE_TREATMENTS)}>', 'reason': '<the evidence>',
                                                  'evidence_ids': _ids_hint(2)}},
                'scenario_events': {s: [{'event_he': f'<{s}: a specific event in plain Hebrew>', 'evidence_ids': _ids_hint(2)},
                                        {'event_he': '<an event without evidence>', 'hypothesis': True}]
                                    for s in EP.SCENARIOS},
                'key_risk': {'reading_he': '<the specific risk in plain Hebrew>', 'evidence_ids': _ids_hint(2)},
                'falsifiers': [{'id': 'fx_<short>', 'scenario': '<bear|base|bull>', 'metric': '<metric and period>',
                                'threshold': '<number>', 'direction': '<below|above>', 'by': f'<a date after {as_of}>',
                                'evidence_ids': _ids_hint(1)}],
                'reasons_he': {s: f'<why this {s} price: what must happen / what breaks / why most likely>' for s in EP.SCENARIOS},
                'engine_options': {'country_risk': '<true when the company discloses revenue by country, else false>',
                                   'reason': '<one line>'},
                'gaps': ['<a lever left on the engine rule, and why>']}
    if part == 'field':
        from .model_card import DIMENSIONS, LEVELS
        return {**common,
                '_allowed': {'potential reading': '{"reading", "evidence_ids"} or {"reading", "hypothesis": true}',
                             'assessment dimensions': list(DIMENSIONS), 'assessment levels': list(LEVELS)},
                'potential': {k: {'reading': f'<{k}: the reading, on numbers>', 'evidence_ids': _ids_hint(2)}
                              for k in EP.POTENTIAL},
                'assessments': {'moat': {'assessment': f'<{"|".join(LEVELS)}>', 'evidence_ids': _ids_hint(2),
                                         'note': '<against which named competitor, on what>'}},
                'gaps': []}
    return {**common,
            '_allowed': {'justification': list(EP.JUSTIFICATIONS), 'source_tier': list(EP.TIERS),
                         'multiple_from': "economics (preferred): the engine computes multiple_path from its own executed EPS "
                                          "path, cost of equity, terminal cost of equity and terminal growth with this "
                                          "scenario's roic; or remove multiple_from and roic and give \"multiple_path\": "
                                          "[today, y1, y2, y3, y4, y5] by hand",
                         'roic': "the scenario's incremental ROIC as a decimal (0.30 = 30%, 0.045 = 4.5%; at most 1.5), evidenced by the scenario's "
                                 'evidence_ids and source_tier (understand/auto.json: ROIC and incremental ROIC)',
                         'competitors': 'only with tier competitor: "competitors": [named tickers of field.json] in a scenario',
                         'peers': 'named competitors of field.json only (no other-company defaults); remove peers when none'},
            'company_path': {
                'metric': 'eps', 'multiple': 'forward_pe',
                'scenarios': {s: {'multiple_from': 'economics',
                                  'roic': f'<{s}: incremental ROIC as a decimal, from the evidence>',
                                  'justification': f'<{"|".join(EP.JUSTIFICATIONS)}>',
                                  'reason_he': '<why this return on capital and so this multiple, in plain Hebrew>',
                                  'source_tier': f'<{tiers}>', 'evidence_ids': _ids_hint(2)}
                              for s in EP.SCENARIOS},
                'own_history': {'low': '<number>', 'median': '<number>', 'high': '<number>', 'years': '<e.g. 2021-2026>',
                                'evidence_ids': _ids_hint(2)},
                'peers': [{'ticker': '<a named competitor in field.json>', 'forward_pe': '<number>', 'growth': '<decimal>',
                           'evidence_ids': _ids_hint(1)}]},
            'gaps': []}


def prep(run):
    """After the four lanes: check every fragment, write understand/auto.json, evidence_ids.json and the skeletons."""
    run, t, as_of = run_info(run)
    checks = {n: (_check_fragment(run, run / 'gather' / n) if isinstance(fr, dict) else
                  {'ok': False, 'problems': [f'not readable: {fr}'], 'warnings': []}) for n, fr in fragments(run)}
    try:
        pack, rep = merge_pack(run)
    except ValueError as e:
        out = {'ok': False, 'merge_error': str(e), 'fragments': {n: {k: c[k] for k in ('ok', 'problems', 'warnings')}
                                                                for n, c in checks.items()}}
        _dump(run / 'prep.json', out)
        return out
    auto = auto_readings(run, pack)
    auto['fragment_problems'] = {n: c['problems'] for n, c in checks.items() if c['problems']}
    _dump(run / 'understand' / 'auto.json', auto)
    _dump(run / 'evidence_ids.json', _catalogue(pack))
    for part in PARTS:
        _dump(run / 'position' / f'skeleton_{part}.json', skeleton(part, run))
    out = {'ok': all(c['ok'] for c in checks.values()), 'at_utc': _now(),
           'fragments': {n: {k: c.get(k) for k in ('ok', 'problems', 'warnings', 'counts')} for n, c in checks.items()},
           'renamed_sources': rep.get('renamed_sources'), 'auto': 'understand/auto.json', 'evidence_ids': 'evidence_ids.json',
           'skeletons': [f'position/skeleton_{p}.json' for p in PARTS],
           'next': 'start the analyst parts (analyst, analyst_field, analyst_path) and review_trace; a fragment problem goes '
                   'to the analyst (core) to fix first' if not all(c['ok'] for c in checks.values()) else
                   'start the analyst parts (analyst, analyst_field, analyst_path) and review_trace'}
    _dump(run / 'prep.json', out)
    return out


# ---------------------------------------------------------------- ticker-value

def _lever_value(d, s):
    v = d.get(s, d.get('all')) if isinstance(d, dict) else None
    return v[0] if isinstance(v, list) and v else v


def coherence_md(position):
    """position/coherence.md: per scenario, what each part assumes, the driver values, the multiple path, the events and the
    falsifiers side by side (for the reviewers; nothing is judged here)."""
    parts = position.get('parts') or {}
    lines = ['# Coherence of the three position parts', '',
             'One story per scenario: the drivers (core), the events (core), the potential readings (field) and the multiple '
             'path (path) must agree. A contradiction is a review finding.', '']
    drivers = position.get('drivers') or {}
    cp = ((position.get('company_path') or {}).get('scenarios') or {})
    for s in EP.SCENARIOS:
        lines.append(f'## {s}')
        for part in PARTS:
            th = ((parts.get(part) or {}).get('thesis_he') or {}).get(s)
            lines.append(f'- {part} ({PART_STAGE[part]}): {th if th else "(no thesis_he)"}')
        vals = [f'{k}={_lever_value(d, s)}' for k, d in drivers.items() if k != EP.CURRENT_YEAR and _lever_value(d, s) is not None]
        lines.append(f"- drivers: {', '.join(vals) if vals else '(none set: engine rules)'}")
        if s in cp and cp[s].get('multiple_from') == 'economics':
            lines.append(f"- multiple path: computed by the engine from its own EPS path and rates at roic {cp[s].get('roic')} "
                         f"({cp[s].get('justification')}, tier {cp[s].get('source_tier')})")
        elif s in cp:
            lines.append(f"- multiple path: {cp[s].get('multiple_path')} ({cp[s].get('justification')}, tier {cp[s].get('source_tier')})")
        for e in (position.get('scenario_events') or {}).get(s) or []:
            if isinstance(e, dict):
                lines.append(f"- event: {e.get('event_he')}" + (' (hypothesis)' if e.get('hypothesis') else ''))
        for f in position.get('falsifiers') or []:
            if isinstance(f, dict) and f.get('scenario') == s:
                lines.append(f"- falsifier: {f.get('metric')} {f.get('direction')} {f.get('threshold')} by {f.get('by')}")
        lines.append('')
    return '\n'.join(lines)


def merge_position(run):
    """position/core.json, field.json and path.json -> position.json and position/coherence.md. -> (position, problems)."""
    run, _, _ = run_info(run)
    pos, prov, problems, gaps = {}, {}, [], []
    for part, keys in PARTS.items():
        f = run / 'position' / f'{part}.json'
        data = _load(f)
        if not isinstance(data, dict):
            problems.append(f'position/{part}.json missing or unreadable ({PART_STAGE[part]} did not write it)')
            continue
        problems += [f'position/{part}.json: {x}' for x in _part_problems(part, data) if not x.startswith('thesis_he')]
        for k in keys:
            if k in data:
                pos[k] = data[k]
        prov[part] = {'file': f'position/{part}.json', 'sha256': _sha(f), 'stage': PART_STAGE[part],
                      'thesis_he': data.get('thesis_he')}
        if part == 'core':
            for k in ('reasons_he', 'engine_options'):
                if data.get(k) is not None:
                    pos[k] = data[k]
        gaps += [f'{part}: {g}' for g in data.get('gaps') or [] if isinstance(g, str)]
    pos['gaps'] = gaps
    pos['parts'] = prov
    _dump(run / 'position.json', pos)
    (run / 'position' / 'coherence.md').write_text(coherence_md(pos), encoding='utf-8')
    return pos, problems


def _compact_path(cf):
    cp = cf.get('company_path') or {}
    out = {'status': cp.get('status')}
    for s, x in (cp.get('scenarios') or {}).items():
        out[s] = [{k: r.get(k) for k in ('year', 'date', 'price', 'multiple', 'eps', 'status')} for r in x.get('rows') or []]
        e = x.get('economics')
        if isinstance(e, dict):           # multiple_from economics: the rates and ROIC the engine computed the path with
            out.setdefault('economics', {})[s] = {k: e[k] for k in ('status', 'reason', 'roic', 'cost_of_equity',
                                                                     'terminal_cost_of_equity', 'terminal_growth',
                                                                     'engine_terminal_roic', 'engine_value_today', 'roic_check')
                                                    if k in e}
    return out


def value(run, deep=True, timeout=900):
    """Merge the parts, build the pack, convert it and run the engine for this one company -> value.json."""
    run, t, as_of = run_info(run)
    co = run / 'companies' / t
    out = {'ticker': t, 'as_of': as_of, 'run': str(run), 'at_utc': _now(), 'ran': False, 'failed_at': None, 'problems': []}

    def done():
        _dump(run / 'value.json', out)
        return out
    pos, probs = merge_position(run)
    out['position_problems'] = probs
    try:
        pack, rep = merge_pack(run, position=pos)
    except ValueError as e:
        out.update(failed_at='pack-merge', problems=[str(e)])
        return done()
    _dump(run / 'evidence_pack.json', pack)
    out['renamed_sources'] = rep.get('renamed_sources')
    problems = _problems(pack)
    if problems:
        out.update(failed_at='pack-to-inputs', problems=problems)
        return done()
    inputs, bundle = EP.to_inputs(pack), EP.to_bundle(pack)
    _dump(co / 'inputs.json', inputs)
    _dump(co / 'evidence_bundle.json', bundle)
    meta = inputs['evidence_pack']
    out.update(pack='evidence_pack.json', inputs=f'companies/{t}/inputs.json', pack_sha256=meta['pack_sha256'],
               overrides=len((inputs.get('forecast') or {}).get('overrides') or []), pack_notes=meta['notes'],
               guidance_adjustments=meta['guidance_adjustments'], track_record=meta['guidance_track_record'])
    if not deep:
        out['failed_at'] = 'not run (--no-deep)'
        return done()
    root = run.parents[2]
    cmd = [sys.executable, '-m', 'fundamental_engine', 'deep', str(run), '--as-of', as_of, '--symbols', t]
    fred = ((_load(run / 'prefetch.json', {}) or {}).get('macro') or {}).get('fred_folder')
    if fred and Path(fred).is_dir():
        cmd += ['--macro', str(fred)]
    opts = pos.get('engine_options') if isinstance(pos.get('engine_options'), dict) else {}
    if opts.get('country_risk') is True:
        cmd.append('--country-risk')
    import os
    env = dict(os.environ)
    pkg_parent = str(Path(__file__).resolve().parents[1])         # this engine, whatever the working directory
    env['PYTHONPATH'] = pkg_parent + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        out.update(failed_at='deep', problems=[f'deep did not finish in {timeout} s'])
        return done()
    printed = {}
    try:
        printed = json.loads(proc.stdout)
    except ValueError:
        pass
    out['deep'] = {'cmd': ' '.join(cmd), 'returncode': proc.returncode, 'index': printed.get('index'),
                   'stderr_tail': proc.stderr[-1500:] if proc.returncode else ''}
    cf = _load(co / 'out' / 'casefile.json')
    if proc.returncode or not isinstance(cf, dict):
        out.update(failed_at='deep', problems=[proc.stderr[-1500:] or 'no casefile written'])
        return done()
    from .pipeline import summary
    stages = cf.get('stages') or {}
    need = ['forecast', 'valuation'] + (['company_path'] if inputs.get('company_path') else [])
    out['ran'] = all(stages.get(k) == 'ok' for k in need)
    out['failed_at'] = None if out['ran'] else 'engine stages: ' + ', '.join(f'{k}={stages.get(k)}' for k in need
                                                                             if stages.get(k) != 'ok')
    out.update(stages=stages, stage_errors=cf.get('errors'), review_status=(cf.get('rv') or {}).get('status'),
               summary=summary(cf), casefile=f'companies/{t}/out/casefile.json', table=f'companies/{t}/out/table_he.md',
               scenarios=f'companies/{t}/out/scenarios_he.md',
               annual_values=[{k: r.get(k) for k in ('date', 'bear', 'base', 'bull', 'current_quote')}
                              for r in ((cf.get('val') or {}).get('result') or {}).get('annual_values') or []],
               company_path=_compact_path(cf))
    return done()


# ---------------------------------------------------------------- ticker-trace

def _renderings(v):
    """How a number may be printed in a filing or a payload (thousands separators, millions, billions, percent)."""
    if not _num(v):
        return []
    a, out = abs(float(v)), set()
    if a >= 1e5:
        for div, nd in ((1e6, 0), (1e6, 1), (1e9, 1), (1e9, 2), (1e3, 0), (1, 0)):
            out.add(f'{a / div:,.{nd}f}')
        out.add(f'{a:.0f}')
    elif a < 2:
        out |= {f'{a * 100:.1f}%', f'{a * 100:.0f}%', f'{a * 100:.1f}', f'{a:.4f}'.rstrip('0').rstrip('.')}
    else:
        out |= {f'{a:,.2f}', f'{a:.2f}', f'{a:,.0f}', f'{a:g}'}
    return sorted((x for x in out if len(re.sub(r'[^0-9]', '', x)) >= 2), key=len, reverse=True)


def _texts(run, t):
    files = sorted((run / 'reading').glob('*.txt')) + sorted((run / 'companies' / t / 'raw').glob('*.json')) + \
        sorted((run / 'companies' / t / 'raw').glob('*.csv')) + sorted((run / 'gather').glob('raw_*/**/*.json')) + \
        sorted((run / 'gather').glob('raw_*/**/*.txt'))
    out = {}
    for f in files:
        try:
            if f.stat().st_size <= 25_000_000:
                out[str(f.relative_to(run))] = f.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
    return out


def _find(texts, v, sids=(), limit=2):
    """Candidate matches of a number: first in the saved text of the documents it cites (reading/ files whose header names
    the source id), then anywhere in the saved text and payloads."""
    cited = [n for n, txt in texts.items() if any(f'source {s} ' in txt[:600] or f'source {s}|' in txt[:600] for s in sids)]
    order = cited + [n for n in texts if n not in cited]
    for cand in _renderings(v):
        rx = re.compile(r'(?<![\d.,])' + re.escape(cand) + r'(?![\d])')
        hits = []
        for name in order:
            txt = texts[name]
            m = rx.search(txt)
            if m:
                s = max(0, m.start() - 70)
                hits.append({'file': name, 'cited_document': name in cited, 'as': cand,
                             'line': txt.count('\n', 0, m.start()) + 1, 'snippet': ' '.join(txt[s:m.end() + 70].split())})
                if len(hits) >= limit:
                    break
        if hits:
            return hits
    return []


def trace(run, n=30):
    """Numbers of the pack (and of the executed table when it exists) with their cited sources and candidate matches in the
    saved text and payloads. A candidate match is where to look, not proof: the reviewer reads it."""
    run, t, as_of = run_info(run)
    pack = _load(run / 'evidence_pack.json')
    latest = max([f.stat().st_mtime for f in (run / 'gather').glob('*.json')] or [0])
    if not isinstance(pack, dict) or (run / 'evidence_pack.json').stat().st_mtime < latest:
        pack = merge_pack(run, position=_load(run / 'position.json', {}) or {})[0]
    p = EP.Pack(pack)
    texts = _texts(run, t)

    def src(sid):
        s = p.sources.get(sid)
        if not s:
            return {'id': sid, 'missing': True}
        return {'id': sid, 'kind': s.get('kind'), 'date': s.get('date'), 'citable': p.citable(sid),
                'retrieval': s.get('retrieval'), 'independent': s.get('independent'), 'url': s.get('url')}
    rows = []

    def add(what, ident, value, label, sids):
        rows.append({'what': what, 'id': ident, 'value': value, 'label': label, 'sources': [src(s) for s in sids if s],
                     'found_in': _find(texts, value, [s for s in sids if s])})
    for g in (pack.get('guidance') or [])[:8]:
        if isinstance(g, dict):
            add('guidance', g.get('id'), g.get('low'), f"{g.get('metric')} {g.get('period')} low..high {g.get('low')}..{g.get('high')}",
                [g.get('source_id')])
    for x in (pack.get('consensus') or [])[:4]:
        if isinstance(x, dict):
            add('consensus', x.get('id'), x.get('value'), f"{x.get('metric')} {x.get('period')}", [x.get('source_id')])
    facts = [f for f in pack.get('facts') or [] if isinstance(f, dict)]
    lane = [f for f in facts if not str(f.get('id')).startswith('pf_')][:10]
    pf = [f for f in facts if str(f.get('id')).startswith('pf_')]
    fy = sorted({f['period'] for f in pf if str(f.get('period')).startswith('FY')})
    qs = sorted({f['period'] for f in pf if str(f.get('period')).startswith('Q@')})
    pick = [f for f in pf if (fy and f['period'] == fy[-1] and f['metric'] in ('revenue', 'operating_income', 'net_income'))
            or (qs and f['period'] == qs[-1] and f['metric'] == 'revenue')]
    for f in lane + pick:
        add('fact', f.get('id'), f.get('value'), f"{f.get('metric')} {f.get('period')} {f.get('unit')}", f.get('source_ids') or [])
    for k, r in (pack.get('market') or {}).items():
        if isinstance(r, dict):
            add('market', k, r.get('value'), f"{k} as of {r.get('as_of')}", [r.get('source_id')])
    for lever, d in (pack.get('drivers') or {}).items():
        if isinstance(d, dict) and lever != EP.CURRENT_YEAR:
            add('driver (base)', lever, _lever_value(d, 'base'), f"{lever} base, tier {d.get('source_tier')}",
                [e for e in d.get('evidence_ids') or [] if e in p.sources])
    inputs = _load(run / 'companies' / t / 'inputs.json', {}) or {}
    cy = (inputs.get('forecast') or {}).get(EP.CURRENT_YEAR)
    if isinstance(cy, dict):
        add('current_year_revenue', EP.CURRENT_YEAR, cy.get('value'), json.dumps({k: v for k, v in cy.items() if k != 'value'},
                                                                                 ensure_ascii=False, default=str)[:200], [])
    val = _load(run / 'value.json', {}) or {}
    price = (val.get('summary') or {}).get('price')
    if price is not None:
        add('price (casefile)', 'price', price, 'the quote the table uses', [s for s, x in p.sources.items() if x['kind'] == 'price'])
    lookahead = [s for s, x in p.sources.items() if str(x.get('date')) > as_of]
    not_citable = [s for s in p.sources if not p.citable(s)]
    return {'ticker': t, 'as_of': as_of, 'numbers': rows[:n], 'shown': min(n, len(rows)), 'total': len(rows),
            'sources_after_as_of': lookahead, 'sources_not_yet_citable': not_citable,
            'syndicated': {s: x.get('syndicated_from') for s, x in p.sources.items() if x.get('syndicated_from')},
            'meaning': 'found_in is a candidate match in the saved text or payloads (where to look, not proof; cited_document '
                       'true when it is in the text of a document the number cites); an empty found_in means the number was '
                       'not found locally: check it at the source URL'}


# ---------------------------------------------------------------- ticker-status

def _ts(path):
    return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)


def status(run, returned=None):
    """Verified or draft from THIS attempt's files (written after prefetch.json started_at_utc) and the main loop's own list
    of the agents that returned (`returned`); plus the stage times. Writes status.json."""
    run, t, as_of = run_info(run)
    pf = _load(run / 'prefetch.json', {}) or {}
    try:
        started = datetime.fromisoformat(str(pf.get('started_at_utc')).replace('Z', '+00:00'))
    except ValueError:
        started = None
    returned = None if returned is None else {x.strip() for x in returned if x and x.strip()}
    reasons = []

    def fresh(rel):
        f = run / rel
        return f.exists() and started is not None and _ts(f) >= started

    if started is None:
        reasons.append('no prefetch.json with started_at_utc: this attempt has no start mark (run scripts/ticker_prefetch.py)')
    val = _load(run / 'value.json', {}) or {}
    repair = _load(run / 'review' / 'repair.json', {}) if fresh('review/repair.json') else None
    ran = bool(fresh('value.json') and val.get('ran'))
    if not ran:
        reasons.append('the engine did not run in this attempt (value.json missing, older than this attempt, or ran false: '
                       f"{val.get('failed_at')})")
    reviews, critical, critical_at = {}, [], None
    for stage, rel in REVIEWS.items():
        data = _load(run / rel) if fresh(rel) else None
        ok_file = isinstance(data, dict) and isinstance(data.get('findings'), list)
        came = returned is not None and stage in returned
        reviews[stage] = {'file': rel, 'this_attempt': ok_file, 'returned': came}
        if not ok_file:
            reasons.append(f'{stage}: no findings file of this attempt ({rel})')
        elif returned is None:
            pass
        elif not came:
            reasons.append(f'{stage}: the main loop did not record it as returned (a file alone is not enough)')
        if ok_file:
            mine = [{**f, 'lens': data.get('lens') or stage} for f in data['findings']
                    if isinstance(f, dict) and f.get('severity') == 'critical']
            critical += mine
            if mine:
                critical_at = max(critical_at, _ts(run / rel)) if critical_at else _ts(run / rel)
    if returned is None:
        reasons.append('--returned not given: the main loop\'s own record of the agents that returned is required')
    open_critical = critical
    if critical and repair is not None and critical_at is not None and _ts(run / 'review' / 'repair.json') < critical_at:
        reasons.append('review/repair.json is older than the critical findings: it cannot close them')
        repair = None
    if critical and repair is not None:
        open_critical = [{'finding': x} for x in repair.get('open') or []]
        if not repair.get('ran'):
            reasons.append('the repair pass did not rerun the engine to the end (repair.json ran false)')
    if open_critical:
        reasons.append(f'{len(open_critical)} critical finding(s) open')
    times = {'started_at_utc': pf.get('started_at_utc'), 'prefetch_finished_at_utc': pf.get('finished_at_utc')}
    if started is not None:
        stamp = {}
        for rel in ([f'gather/{n}' for n, _ in fragments(run)] + [f'understand/{f.name}' for f in sorted((run / 'understand').glob('*.json'))]
                    + ['prep.json'] + [f'position/{p}.json' for p in PARTS] + ['value.json'] + list(REVIEWS.values())
                    + ['review/repair_value.json', 'review/repair.json', 'deliver/table_he.md']):
            if fresh(rel):
                stamp[rel] = round((_ts(run / rel) - started).total_seconds() / 60, 1)
        times['minutes_after_start'] = stamp
        times['elapsed_minutes'] = max(stamp.values()) if stamp else 0.0
    verified = not reasons
    out = {'ticker': t, 'as_of': as_of, 'at_utc': _now(), 'verified': verified,
           'label': 'verified' if verified else 'UNVERIFIED draft (טיוטה — ההערכה לא אומתה)', 'reasons': reasons,
           'engine_ran': ran, 'engine_review_status': val.get('review_status') if ran else None,
           'reviews': reviews, 'critical': critical, 'open_critical': open_critical,
           'repair': {k: repair.get(k) for k in ('fixed', 'open', 'ran')} if repair else None,
           'returned': sorted(returned) if returned is not None else None, 'times': times,
           'meaning': 'verified needs: the engine ran in this attempt, the three reviewers returned (files of this attempt AND '
                      'the main loop\'s record), no critical finding open after the one repair pass. The engine\'s own review '
                      'status is reported beside it, never replaced by it.'}
    _dump(run / 'status.json', out)
    return out
