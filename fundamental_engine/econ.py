"""Business economics layer (econ): read each business through its own economic identity.

Why: the engine classified companies by a sector label and valued them with one consolidated growth and margin path
(docs/ANALYST_COMPARISON_2026-10.md). A human analyst reads a broker as accounts x revenue per account, a memory maker
as bits x ASP, a marketplace as bookings x take rate, and splits growth into what is built (structural), what moves
with the market (cyclical), what rates set (macro) and what composition changes (mix). This module makes that read
deterministic: a JSON registry of economic models (brain/economics.json) validated on load, with the KPIs each model
needs, the identities that reconcile them to reported lines, the tag of every factor, the checks, the falsifiers and
a reading plan that says which document carries which KPI.

WP1: registry access and validation, required KPIs, factor tags, falsifiers, reading plan. WP2: KPI intake
(facts.add_kpi stores a KPI named by registry id with its basis, scope and currency basis; kpi_table reads those facts
point in time). Later work packages add identities and the growth split, model selection, overlays, the review and the
Hebrew text. Nothing here is called by the pipeline, so every existing output stays byte-identical (no-input invariance).

Boundaries: fetches nothing; the registry holds no company data; thresholds are uncalibrated screens frozen in
docs/prereg_econ.md; a result of this layer is never a valuation, a moat rating or a trade instruction.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from . import facts as F

REGISTRY_PATH = Path(__file__).parent / 'brain' / 'economics.json'
ECONOMICS_VERSION = 1
BASES = ('balance', 'average', 'flow', 'cumulative', 'rate')                     # KPI bases: quarter.KPI_BASES's balance/flow/
#   cumulative, 'average' (a period mean: DARTs, average margin loans; quarter.py says 'active') and 'rate' (a ratio)
TAGS = ('structural', 'cyclical', 'macro', 'mix', 'reclassification')           # growth-split tags of every factor
IDENTITY_TYPES = ('per_unit', 'ratio', 'product', 'share')
STATUSES = ('computed', 'consistent', 'partial', 'inconsistent', 'basis_mismatch', 'scope_mismatch', 'missing')
CHECK_KINDS = ('flag', 'reading', 'measure', 'status')
SEVERITIES = ('high', 'medium', 'info')                                           # quarter.SEVERITY's levels
# the twelve screens frozen in docs/prereg_econ.md section 1; the registry carries them under rules with `_status`
FROZEN_RULES = ('identity_tolerance', 'growth_identity_pp', 'partial_below', 'firm_exposure_high', 'cfo_to_ni_min',
                'near_threshold', 'receivables_gap_pp', 'minor_line_share', 'flat_units_abs', 'segment_material',
                'leverage_share_min', 'econ_view_min_pp')
TOP_KEYS = ('economics_version', 'rules', 'bases', 'tags', 'statuses', 'identity_types', 'lines', 'checks',
            'reading_common', 'models', 'overlays', 'new_keys')
MODEL_KEYS = ('label_he', 'description', 'price_cyclical', 'select', 'kpis', 'identities', 'growth_split', 'checks',
              'falsifier_kpis', 'overlays', 'reading', 'questions')
OVERLAY_KEYS = ('label_he', 'applies_to', 'inputs', 'computes', 'checks', 'rules_used', 'boundary')
# what each identity type must reference (a KPI, a line or another identity of the same model)
IDENTITY_REFS = {'per_unit': ('line', 'unit_kpi'), 'ratio': ('num', 'den'), 'product': ('line', 'factors'), 'share': ('part', 'total')}
UNCLASSIFIED = 'unclassified'
BOUNDARY = 'a reading plan: which document carries which KPI; it fetches nothing and asserts nothing about the company'
TABLE_BOUNDARY = ('the KPI facts usable at as_of, one row per period, scope and basis; it computes no identity and '
                  'asserts nothing about the company')

_CACHE = {}        # file sha256 -> validated registry (reloaded when the file changes)


# ---------------------------------------------------------------- load and validate

def _read():
    raw = REGISTRY_PATH.read_bytes()
    return json.loads(raw.decode('utf-8')), sha256(raw).hexdigest()


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _strs(x):
    return isinstance(x, list) and all(isinstance(s, str) and s for s in x)


def identity_refs(identity):
    """Every KPI / line / identity id an identity references, in declaration order."""
    refs = []
    for key in IDENTITY_REFS.get(identity.get('type'), ()):
        v = identity.get(key)
        refs.extend(v if isinstance(v, list) else [v])
    return refs


def problems(reg):
    """Why a registry is not usable; an empty list means it is. Every message names its path."""
    out = []
    if not isinstance(reg, dict):
        return ['registry: not an object']
    for k in TOP_KEYS:
        if k not in reg:
            out.append(f'{k}: missing')
    if out:
        return out
    if reg['economics_version'] != ECONOMICS_VERSION:
        out.append(f'economics_version: {reg["economics_version"]!r} is not {ECONOMICS_VERSION}')
    rules = reg['rules']
    if not isinstance(rules, dict) or not isinstance(rules.get('_status'), str) or not rules.get('_status'):
        out.append('rules._status: missing (thresholds are uncalibrated screens and must say so)')
    else:
        for k in FROZEN_RULES:
            if not _is_num(rules.get(k)):
                out.append(f'rules.{k}: missing or not numeric')
    for key, vocab in (('bases', BASES), ('tags', TAGS), ('statuses', STATUSES), ('identity_types', IDENTITY_TYPES)):
        if not isinstance(reg[key], dict) or set(reg[key]) != set(vocab):
            out.append(f'{key}: must describe exactly {sorted(vocab)}')
    if isinstance(reg['tags'], dict):
        for t, spec in reg['tags'].items():
            if not isinstance(spec, dict) or not spec.get('label_he'):
                out.append(f'tags.{t}.label_he: missing')
    lines = reg['lines']
    if not isinstance(lines, dict) or not lines:
        out.append('lines: missing')
        lines = {}
    for lid, spec in lines.items():
        if not isinstance(spec, dict) or not spec.get('unit') or spec.get('basis') not in BASES:
            out.append(f'lines.{lid}: needs unit and a basis from {BASES}')
    checks = reg['checks']
    if not isinstance(checks, dict) or not checks:
        out.append('checks: missing')
        checks = {}
    for cid, spec in checks.items():
        if not isinstance(spec, dict) or spec.get('kind') not in CHECK_KINDS or not spec.get('rule'):
            out.append(f'checks.{cid}: needs kind from {CHECK_KINDS} and a rule')
        elif spec['kind'] == 'flag' and (spec.get('severity') not in SEVERITIES or not isinstance(spec.get('fixed'), bool)):
            out.append(f'checks.{cid}: a flag needs severity from {SEVERITIES} and fixed (bool)')
    common = reg['reading_common']
    for step in ('mechanism', 'kpis', 'notes', 'transcript'):
        if not isinstance(common, dict) or not isinstance(common.get(step), dict) or not common[step].get('doc'):
            out.append(f'reading_common.{step}: missing doc')
    overlays = reg['overlays'] if isinstance(reg['overlays'], dict) else {}
    models = reg['models']
    if not isinstance(models, dict) or not models:
        out.append('models: missing')
        models = {}
    units = {}
    for mid, m in models.items():
        p = f'models.{mid}'
        if not isinstance(m, dict):
            out.append(f'{p}: not an object')
            continue
        for k in MODEL_KEYS:
            if k not in m:
                out.append(f'{p}.{k}: missing')
        if not isinstance(m.get('label_he'), str) or not m.get('label_he'):
            out.append(f'{p}.label_he: missing')
        if not isinstance(m.get('price_cyclical'), bool):
            out.append(f'{p}.price_cyclical: must be true or false')
        kpis = m.get('kpis') if isinstance(m.get('kpis'), dict) else {}
        if not kpis:
            out.append(f'{p}.kpis: empty')
        for kid, spec in kpis.items():
            q = f'{p}.kpis.{kid}'
            if not isinstance(spec, dict):
                out.append(f'{q}: not an object')
                continue
            if not isinstance(spec.get('unit'), str) or not spec.get('unit'):
                out.append(f'{q}.unit: missing')
            if not _strs(spec.get('basis')) or not spec['basis'] or set(spec['basis']) - set(BASES):
                out.append(f'{q}.basis: must be a non-empty subset of {BASES}')
            if spec.get('tag') not in TAGS:
                out.append(f'{q}.tag: must be one of {TAGS}')
            if 'core' in spec and not isinstance(spec['core'], bool):
                out.append(f'{q}.core: must be true or false')
            if units.setdefault(kid, spec.get('unit')) != spec.get('unit'):
                out.append(f'{q}.unit: {spec.get("unit")!r} differs from {units[kid]!r} elsewhere (one unit per KPI id: facts.add_kpi reads it)')
        idents = m.get('identities') if isinstance(m.get('identities'), list) else []
        ids = [i.get('id') for i in idents if isinstance(i, dict)]
        known = set(kpis) | set(lines) | set(ids)
        if len(ids) != len(set(ids)):
            out.append(f'{p}.identities: duplicate ids')
        if not any(isinstance(i, dict) and i.get('core') is True for i in idents):
            out.append(f'{p}.identities: no core identity')
        for i in idents:
            q = f'{p}.identities.{i.get("id") if isinstance(i, dict) else "?"}'
            if not isinstance(i, dict) or not i.get('id') or i.get('type') not in IDENTITY_TYPES:
                out.append(f'{q}: needs id and a type from {IDENTITY_TYPES}')
                continue
            refs = identity_refs(i)
            if not refs or any(r is None for r in refs) or (i['type'] == 'product' and len(i.get('factors') or []) < 2):
                out.append(f'{q}: {i["type"]} needs {IDENTITY_REFS[i["type"]]}')
            for r in refs:
                if r is not None and r not in known:
                    out.append(f'{q}: references {r!r}, not a declared KPI, line or identity')
            if i['id'] in refs:
                out.append(f'{q}: references itself')
            if 'tag' in i and i['tag'] not in TAGS:
                out.append(f'{q}.tag: must be one of {TAGS}')
            if 'check' in i and i['check'] not in checks:
                out.append(f'{q}.check: {i["check"]!r} is not a declared check')
            if 'core' in i and not isinstance(i['core'], bool):
                out.append(f'{q}.core: must be true or false')
        gs = m.get('growth_split')
        if not isinstance(gs, dict) or gs.get('line') not in known:
            out.append(f'{p}.growth_split: needs a declared line')
        elif gs.get('mode') == 'additive':
            if gs.get('part') not in known:
                out.append(f'{p}.growth_split.part: not declared')
        elif not _strs(gs.get('factors')) or not gs['factors'] or any(f not in known for f in gs['factors']):
            out.append(f'{p}.growth_split.factors: every factor must be a declared KPI or identity')
        for c in (m.get('checks') if isinstance(m.get('checks'), list) else ['?']):
            if c not in checks:
                out.append(f'{p}.checks: {c!r} is not a declared check')
        fals = m.get('falsifier_kpis')
        if not _strs(fals) or not fals or any(f not in set(kpis) | set(ids) for f in fals):
            out.append(f'{p}.falsifier_kpis: non-empty, each a declared KPI or identity')
        for o in (m.get('overlays') if isinstance(m.get('overlays'), list) else ['?']):
            if o not in overlays:
                out.append(f'{p}.overlays: {o!r} is not a declared overlay')
        sel = m.get('select')
        if not isinstance(sel, dict) or not _strs(sel.get('card_types')) or not _strs(sel.get('text')) or not _strs(sel.get('kpi_any')):
            out.append(f'{p}.select: needs card_types, text and kpi_any lists')
        elif any(k not in kpis for k in sel['kpi_any']):
            out.append(f'{p}.select.kpi_any: every id must be a KPI of the model')
        reading = m.get('reading')
        if not isinstance(reading, list) or not reading or any(not isinstance(r, dict) or not r.get('doc') or not _strs(r.get('find')) or not r['find'] for r in reading):
            out.append(f'{p}.reading: non-empty list of {{doc, find[]}}')
        if not _strs(m.get('questions')) or not m['questions']:
            out.append(f'{p}.questions: non-empty')
    if not overlays:
        out.append('overlays: missing')
    for oid, o in overlays.items():
        p = f'overlays.{oid}'
        if not isinstance(o, dict):
            out.append(f'{p}: not an object')
            continue
        for k in OVERLAY_KEYS:
            if k not in o:
                out.append(f'{p}.{k}: missing')
        if not o.get('label_he'):
            out.append(f'{p}.label_he: missing')
        for a in (o.get('applies_to') if _strs(o.get('applies_to')) else ['?']):
            if a != '*' and a not in models:
                out.append(f'{p}.applies_to: {a!r} is not a model')
        for c in (o.get('checks') if isinstance(o.get('checks'), list) else ['?']):
            if c not in checks:
                out.append(f'{p}.checks: {c!r} is not a declared check')
        for r in (o.get('rules_used') if isinstance(o.get('rules_used'), list) else ['?']):
            if r not in rules:
                out.append(f'{p}.rules_used: {r!r} is not a rule')
    all_kpis = {k for m in models.values() if isinstance(m, dict) for k in (m.get('kpis') or {})}
    nk = reg['new_keys']
    if not _strs(nk) or nk != sorted(set(nk)) or any(k not in all_kpis for k in nk):
        out.append('new_keys: sorted unique KPI ids of this registry')
    return out


def validate(reg):
    """The registry when it is usable; ValueError naming every problem otherwise."""
    bad = problems(reg)
    if bad:
        raise ValueError('economics.json: ' + '; '.join(bad))
    return reg


def registry():
    """The validated registry (a copy; the file is re-read and re-validated when its bytes change)."""
    doc, sha = _read()
    if sha not in _CACHE:
        _CACHE.clear()
        _CACHE[sha] = validate(doc)
    return deepcopy(_CACHE[sha])


def registry_sha256():
    return _read()[1]


def rules():
    return registry()['rules']


# ---------------------------------------------------------------- lookups

def models():
    return list(registry()['models'])


def overlays():
    return list(registry()['overlays'])


def model(model_id):
    reg = registry()
    if model_id not in reg['models']:
        raise ValueError(f'unknown economic model {model_id!r}; models: {", ".join(reg["models"])}')
    return reg['models'][model_id]


def overlay(overlay_id):
    reg = registry()
    if overlay_id not in reg['overlays']:
        raise ValueError(f'unknown overlay {overlay_id!r}; overlays: {", ".join(reg["overlays"])}')
    return reg['overlays'][overlay_id]


def kpi_ids(model_id=None):
    """KPI ids of one model (core first, then declaration order) or of the whole registry (sorted)."""
    if model_id is None:
        return sorted({k for m in registry()['models'].values() for k in m['kpis']})
    kpis = model(model_id)['kpis']
    return [k for k, v in kpis.items() if v.get('core')] + [k for k, v in kpis.items() if not v.get('core')]


def kpi(kpi_id, model_id=None):
    """A KPI's spec. Across models: one unit (validated), the union of bases and the tag each model gives it."""
    if model_id is not None:
        kpis = model(model_id)['kpis']
        if kpi_id not in kpis:
            raise ValueError(f'{kpi_id!r} is not a KPI of {model_id}; KPIs: {", ".join(kpis)}')
        return {'id': kpi_id, 'model': model_id, **kpis[kpi_id]}
    out = None
    for mid, m in registry()['models'].items():
        spec = m['kpis'].get(kpi_id)
        if spec is None:
            continue
        if out is None:
            out = {'id': kpi_id, 'unit': spec['unit'], 'basis': list(spec['basis']), 'models': {}}
        out['basis'] += [b for b in spec['basis'] if b not in out['basis']]
        out['models'][mid] = spec['tag']
    if out is None:
        raise ValueError(f'unknown KPI id {kpi_id!r}; the registry (brain/economics.json) names the accepted ids')
    return out


def kpi_unit(kpi_id):
    return kpi(kpi_id)['unit']


def identity(model_id, identity_id):
    for i in model(model_id)['identities']:
        if i['id'] == identity_id:
            return i
    raise ValueError(f'unknown identity {identity_id!r} of {model_id}')


def required_kpis(model_id):
    """KPIs a model cannot be read without: those marked core plus every KPI a core identity reaches, through other
    identities, in declaration order."""
    m = model(model_id)
    idents = {i['id']: i for i in m['identities']}
    out = [k for k, v in m['kpis'].items() if v.get('core')]
    todo = [i for i in m['identities'] if i.get('core')]
    seen = set()
    while todo:
        i = todo.pop(0)
        if i['id'] in seen:
            continue
        seen.add(i['id'])
        for r in identity_refs(i):
            if r in m['kpis'] and r not in out:
                out.append(r)
            elif r in idents:
                todo.append(idents[r])
    return out


def factor_tags(model_id):
    """{factor id: tag} for every tagged KPI and identity of a model (the growth split carries these)."""
    m = model(model_id)
    tags = {k: v['tag'] for k, v in m['kpis'].items()}
    tags.update({i['id']: i['tag'] for i in m['identities'] if i.get('tag')})
    return tags


def falsifiers(model_id):
    """The model's numeric falsifiers: a KPI or a computed identity, with its tag when it has one."""
    m = model(model_id)
    tags = factor_tags(model_id)
    return [{'id': f, 'kind': 'kpi' if f in m['kpis'] else 'identity', 'tag': tags.get(f)} for f in m['falsifier_kpis']]


def checks(model_id):
    """The model's checks with their declared kind and severity."""
    reg = registry()
    return {c: reg['checks'][c] for c in reg['models'][model_id]['checks']} if model_id in reg['models'] else model(model_id)


# ---------------------------------------------------------------- reading plan

def _have(fb, kpi_id):
    """The best usable fact named by a registry id at fb['as_of'] (facts.candidates: point in time), else None."""
    if not fb:
        return None
    c = F.candidates(fb, kpi_id)
    if not c:
        return None
    return {'id': kpi_id, 'period': c[0]['period'], 'period_end': c[0]['period_end'], 'source_ids': [c[0]['source_id']],
            'facts': len(c)}


def reading_plan(model_id, fb=None):
    """Ordered reading steps for a model: mechanism (10-K Item 1), the KPI document with what the FactBook already holds
    as point-in-time facts (`have`) and what is missing, the model's other documents, the notes and the transcript with
    the model's questions. Fetches nothing; an fb is only consulted through facts.candidates."""
    reg = registry()
    m = model(model_id)
    common = reg['reading_common']
    ids = kpi_ids(model_id)
    have = [h for h in (_have(fb, k) for k in ids) if h]
    present = {h['id'] for h in have}
    kpi_docs = [r for r in m['reading'] if set(r['find']) & set(m['kpis'])]
    extra_docs, notes_extra, transcript_extra = [], [], []
    for r in m['reading']:
        if kpi_docs and r is kpi_docs[0]:
            continue
        if r['doc'] == 'transcript':
            transcript_extra += [f for f in r['find'] if f not in transcript_extra]
        elif 'notes' in r['doc']:
            notes_extra += [f for f in r['find'] if f not in notes_extra]
        else:
            extra_docs.append({'doc': r['doc'], 'find': list(r['find'])})
    kpi_step = {**common['kpis'], 'find': ids, 'have': [h['id'] for h in have], 'missing': [k for k in ids if k not in present]}
    if kpi_docs:
        kpi_step['doc'] = kpi_docs[0]['doc']
        if kpi_docs[0].get('state'):
            kpi_step['state_notes'] = list(kpi_docs[0]['state'])
    notes = {**common['notes'], 'find': common['notes']['find'] + [f for f in notes_extra if f not in common['notes']['find']]}
    transcript = {**common['transcript'], 'find': common['transcript']['find'] + transcript_extra, 'questions': list(m['questions'])}
    steps = [dict(common['mechanism']), kpi_step, *extra_docs, notes, transcript]
    for n, s in enumerate(steps, 1):
        s['step'] = n
    return {'model': model_id, 'label_he': m['label_he'], 'economics_version': reg['economics_version'],
            'as_of': fb.get('as_of') if fb else None, 'steps': steps, 'have': have,
            'missing': kpi_step['missing'], 'required': required_kpis(model_id), 'falsifiers': falsifiers(model_id),
            'fetches': 'nothing', 'boundary': BOUNDARY}


# ---------------------------------------------------------------- KPI table (point in time)

def _tiebreak(view, f):
    """facts.rank first, then every remaining field a row shows, so equal-rank facts never resolve by insertion order."""
    return (F.rank(view, f), f['value'], f['source_id'], f.get('period_start') or '', bool(f.get('available_at_estimated')))


def _row(view, fs, declared):
    """One table row for the facts of a (period, period_end, scope, kpi_basis, currency_basis, unit) group: the best
    candidate by _tiebreak, so the row does not depend on insertion order; `alternates` counts the facts it beat and
    `alternate_values` (only when some beaten fact disagrees) lists their other values, so a disagreement between
    sources stays visible."""
    best = min(fs, key=lambda f: _tiebreak(view, f))
    basis = best.get('kpi_basis')
    others = sorted({f['value'] for f in fs if f['value'] != best['value']})
    row = {'period': best['period'], 'period_end': best['period_end'], 'value': best['value'], 'unit': best['unit'],
           'kpi_basis': basis, 'scope': best.get('scope'), 'currency_basis': best.get('currency_basis'),
           'basis_declared': (basis in declared) if basis is not None else None,
           'source_id': best['source_id'], 'source_kind': view['sources'][best['source_id']]['kind'],
           'available_at': best['available_at'], 'available_at_estimated': bool(best.get('available_at_estimated')),
           'alternates': len(fs) - 1}
    if others:
        row['alternate_values'] = others
    if best.get('period_start'):
        row['period_start'] = best['period_start']
    return row


def kpi_table(fb, model_id, as_of=None):
    """The model's KPIs as the FactBook holds them at `as_of` (fb['as_of'] by default; never later than it, so the
    FactBook's cutoff is never widened): for every KPI with a usable fact, one row per (period, period_end, scope,
    kpi_basis, currency_basis, unit) group, best source first within the group (facts.rank), rows ascending by period
    end; values in different units or on different bases are separate rows, never alternates of each other.
    Rows carry the stated basis, scope and currency basis from facts.add_kpi and `basis_declared`, whether that basis
    is one the model declares for the KPI (None when the fact was stored without a stated basis: such ids are listed
    under `unstated` and are a question, not a basis). `have`, `missing` and `required_missing` follow kpi_ids /
    required_kpis order. Nothing is computed, derived or fetched; the same facts in any insertion order give an identical
    table and sha256."""
    m = model(model_id)
    version = registry()['economics_version']
    if as_of is None:
        as_of = fb['as_of']
    if F._d(as_of) > F._d(fb['as_of']):
        raise ValueError(f'kpi_table: as_of {as_of} is after the FactBook cutoff {fb["as_of"]}')
    view = fb if as_of == fb['as_of'] else {**fb, 'as_of': as_of}
    ids = kpi_ids(model_id)
    kpis, unstated = {}, []
    for k in ids:
        spec = m['kpis'][k]
        groups = {}
        for f in F.candidates(view, k):
            key = (f['period'], f['period_end'], f.get('scope'), f.get('kpi_basis'), f.get('currency_basis'), f['unit'])
            groups.setdefault(key, []).append(f)
        if not groups:
            continue
        rows = sorted((_row(view, fs, spec['basis']) for fs in groups.values()),
                      key=lambda r: (r['period_end'], r['period'], r['scope'] or '', r['kpi_basis'] or '', r['currency_basis'] or '',
                                     r['unit']))
        if any(r['kpi_basis'] is None for r in rows):
            unstated.append(k)
        kpis[k] = {'unit': spec['unit'], 'bases': list(spec['basis']), 'tag': spec['tag'], 'core': bool(spec.get('core')),
                   'period_ends': sorted({r['period_end'] for r in rows}), 'rows': rows}
    out = {'model': model_id, 'label_he': m['label_he'], 'economics_version': version, 'as_of': as_of,
           'currency': (fb.get('company') or {}).get('currency'), 'kpis': kpis, 'have': list(kpis),
           'missing': [k for k in ids if k not in kpis], 'required_missing': [k for k in required_kpis(model_id) if k not in kpis],
           'unstated': unstated, 'boundary': TABLE_BOUNDARY}
    out['sha256'] = F.digest({'model': model_id, 'as_of': as_of, 'economics_version': version, 'kpis': kpis})
    return out
