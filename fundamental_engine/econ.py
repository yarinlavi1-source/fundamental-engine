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
point in time). WP3: the core math on an econ case: evaluate_identity (per unit, ratio, product, share; status
computed / consistent / partial / inconsistent / basis_mismatch / scope_mismatch / missing / not_computed, the residual
named), growth_split (ln(1+g) = sum ln(1+g_i) + residual, every factor tagged) and review (every identity at every
scope, the split, the model's checks, falsifiers, open questions, input_sha256). WP4: model selection (select: the
card type, the industry / SIC / profile text, the segment names and the KPI facts, two agreeing signals, KPI facts
before labels, unclassified instead of a forced label, an analyst override only with a reason and evidence, the rule
result shown beside it; select_case and select_factbook read those inputs point in time). Later work packages add the
overlays, the quarter-review link and the Hebrew text. Nothing here is called by the pipeline, so every existing output
stays byte-identical (no-input invariance).

Boundaries: fetches nothing; the registry holds no company data; thresholds are uncalibrated screens frozen in
docs/prereg_econ.md; a result of this layer is never a valuation, a moat rating or a trade instruction.
"""
from copy import deepcopy
from hashlib import sha256
import json
from math import log
from pathlib import Path
import re
from . import facts as F
from . import __version__
from .finance import number

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
    as_of, view = _cutoff(fb, as_of, 'kpi_table')
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


# ---------------------------------------------------------------- WP3: identities, growth split, review (pure arithmetic)
#
# The econ case (the econ_fixture_v1 shape of tests/fixtures/econ, without `expected`): company, period_end, as_of,
# currency, period ('quarter', 'year', ...), sources {id: {kind, available_at}}, lines {line: {value, growth, prior, ...}},
# kpis [{id, value, growth, prior, kpi_basis, scope, currency_basis, window, source_ids}], segments [{name, revenue,
# profit, profit_definition, operating_income | operating_margin, <KPI id>, source_ids}], and the optional blocks read
# here: sensitivities, disclosure_changes, reclassification_evidence, cycle and market (memory_cycle only). Every other
# block is listed under not_read: later work packages read it (funding links, bank capital, contracts, SBC tranches).
# A growth rate is read in its comparison window: `growth`/`prior` in the item's `window` ('y/y' unless it says 'q/q'),
# `growth_qoq`/`prior_quarter` and `prior_year` always in theirs; 'TTM', 'quarter', 'year' in `window` or `period` are
# measurement windows. Nothing is fetched; nothing is filled in.

COMPARISONS = ('y/y', 'q/q')                     # the window a growth rate or a prior level compares against
MONTHS = {'month': 1, 'quarter': 3, 'half': 6, 'year': 12, 'TTM': 12}   # measurement windows; TTM and year are both
#   twelve months but are different windows (a trailing year is not the fiscal year), so they never match each other
START = {'quarter': 'q/q', 'year': 'y/y'}        # the comparison whose prior level is the balance at the period start
POINT_BASES = ('balance', 'cumulative')          # read at a date: an end value and, with the start, an average
UNIT_CLASSES = ('count', 'bits', 'GW')           # registry unit classes that count units (core_units_flat, per_unit_vs_units)
# KPIs stated as a rate per year (interest earned or paid over a year). The registry's unit class 'rate' also covers
# fractions (utilization, churn, shares), so the annual ones are named here; a unit ending in '_year' is annual by name.
# A product with an annual factor is put on the line's window (x months / 12); without a line it is a value per year.
ANNUAL_RATE_KPIS = ('margin_rate', 'net_interest_margin', 'deposit_cost_advantage')
IDENTITY_STATUSES = STATUSES + ('not_computed',)   # not_computed: refused by a status check (capex_per_unit_lag)
# source kinds that evidence a business-model change (the reclassification tag): issuer documents only; an analyst
# post, a consensus, a journal or an assumption never completes it (decision.py's rule for upgrade evidence)
EVIDENCE_KINDS = ('issuer_release', 'issuer_filing', 'issuer_transcript', 'issuer_document', 'primary_document', 'filing_derived')
DISCLOSURE_KINDS = ('reported', 'guidance')      # a disclosure change counts when the issuer reported or guided it
# segment fields read as lines at the segment's scope: (field, registry line, field of the year-ago level)
SEGMENT_LINES = (('revenue', 'revenue', 'prior_revenue'), ('revenue', 'segment_revenue', 'prior_revenue'),
                 ('profit', 'segment_profit', 'prior_profit'), ('operating_income', 'operating_income', None),
                 ('gross_profit', 'gross_profit', None), ('adjusted_ebitda', 'adjusted_ebitda', None),
                 ('research_and_development', 'research_and_development', None), ('capex', 'capex', None), ('sbc', 'sbc', None))
SEGMENT_MARGINS = {'operating_income': 'operating_margin', 'gross_profit': 'gross_margin', 'adjusted_ebitda': 'adjusted_ebitda_margin'}
CASE_META = ('_schema', '_provenance', 'company', 'name', 'period_end', 'period', 'as_of', 'as_of_note', 'currency',
             'reconciled_to_filings', 'sources', 'model', 'model_hint')
# the registry checks this version computes; every other declared check of a selected model is listed under
# checks_not_run (overlays, process evidence and the quarter-review peak exception arrive with later work packages)
CHECKS_RUN = ('per_unit_vs_units', 'take_rate_shift', 'thesis_on_minor_line', 'core_units_flat', 'disclosure_dropped',
              'cyclical_deceleration', 'expansion_led', 'cumulative_kpi_not_activity', 'rate_sensitivity_share',
              'margin_only_normalized', 'segment_reconciliation', 'basis_mismatch', 'scope_mismatch', 'capex_per_unit_lag')
CORE_ORDER = ('consistent', 'computed', 'partial', 'inconsistent', 'basis_mismatch', 'scope_mismatch', 'not_computed', 'missing')
# a core identity's summary row among the scopes that computed something: the worst check first (_core_row)
SURFACE_ORDER = ('inconsistent', 'partial', 'consistent', 'computed')
# expansion_led's "per-unit share of log growth above one half" (the registry rule's words; not a rules.* number).
# take_rate_shift reads rules.growth_identity_pp. Neither has a dedicated frozen rule: docs/prereg_econ.md is to record
# both before the held-out run (WP8 notes).
EXPANSION_LOG_SHARE = 0.5
# Which comparison window a check reads. An identity's status folds the growth check of every window it computes (y/y
# and q/q; the worst one wins). The flags and readings built on an identity's change (per_unit_vs_units, take_rate_shift,
# identity falsifiers, thesis_on_minor_line's incremental share) read its first window: y/y when every input has it, else
# q/q; a q/q move beside a y/y one is shown in the identity's changes but raises no second flag (seasonal quarters are
# not read as a shift). A KPI's own checks (core_units_flat, KPI falsifiers) read the window its source compares
# (`window`), the other one shown beside; the growth split reads the first window the line shares with its factors.
CHECK_WINDOW = ('flags on an identity read its first comparison window (y/y when every input has it, else q/q); its '
                'status folds the growth check of every window; KPI checks read the window the source compares')
_BP = re.compile(r'([+-]?\d+(?:\.\d+)?)\s*bp\b')
_QUARTER = re.compile(r'^(\d{4})-Q([1-4])$')
REVIEW_BOUNDARY = ('a deterministic read of caller-supplied, sourced KPIs and lines through each economic model\'s identities: '
                   'arithmetic, tags, residuals, flags and open questions. Not source verification, not a valuation, not a '
                   'moat rating and not a trade instruction; thresholds are uncalibrated screens (docs/prereg_econ.md).')


def _num(x, label, minimum=None):
    return None if x is None else number(x, label, minimum)


def _name(ref):
    return ref.replace('_', ' ')


def _a_basis(bases):
    """'a balance basis', 'an average basis', 'a balance/average basis'."""
    b = '/'.join(bases)
    return ('an ' if b[:1] in 'aeiou' else 'a ') + b + ' basis'


def _scope(x, label):
    if x is None:
        return 'total'
    if not isinstance(x, str) or not x.strip():
        raise ValueError(f'{label}.scope: name what the figure covers (total, a segment, a geography)')
    return x.strip().lower()


def _source_ids(case, item, label):
    ids = item.get('source_ids') if isinstance(item, dict) else None
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise ValueError(f'{label}: source_ids required')
    unknown = sorted(set(ids) - set(case['sources']))
    if unknown:
        raise ValueError(f'{label}: unknown source ids {unknown}')
    return list(ids)


def _visible(case, ids):
    """Point in time: an item is read when at least one of its sources was available at as_of."""
    return any(F._d(case['sources'][i]['available_at']) <= F._d(case['as_of']) for i in ids)


def _unavailable(ix, ref):
    """List an item none of whose sources was available at as_of (once, however many models read it)."""
    if ref not in ix['unavailable']:
        ix['unavailable'].append(ref)


def _check_case(case):
    if not isinstance(case, dict):
        raise ValueError('econ case: not an object')
    for k in ('company', 'period_end', 'as_of', 'currency', 'period'):
        if not case.get(k):
            raise ValueError(f'econ case: {k} required')
    if F._d(case['period_end']) > F._d(case['as_of']):
        raise ValueError('econ case: period_end after as_of')
    if case['period'] not in MONTHS:
        raise ValueError(f'econ case: period must be one of {tuple(MONTHS)}')
    if not isinstance(case['currency'], str) or not F.MONETARY.match(case['currency']):
        raise ValueError('econ case: currency must be an ISO code')
    src = case.get('sources')
    if not isinstance(src, dict) or not src:
        raise ValueError('econ case: sources {id: {kind, available_at}} required')
    for sid, s in src.items():
        if not isinstance(s, dict) or not s.get('kind') or not s.get('available_at'):
            raise ValueError(f'econ case: sources.{sid} needs kind and available_at')
        F._d(s['available_at'])


def _cmp(spec, label, value):
    """{window: {stated, prior, levels}} for each comparison window the item states (see the case notes above)."""
    w = spec.get('window')
    if w is not None and w not in COMPARISONS and w not in MONTHS:
        raise ValueError(f'{label}.window: {w!r} is neither a comparison {COMPARISONS} nor a measurement window {tuple(MONTHS)}')
    home = w if w in COMPARISONS else 'y/y'
    out = {}
    for c in COMPARISONS:
        g = spec.get('growth_qoq') if c == 'q/q' else None
        if g is None and c == home:
            g = spec.get('growth')
        p = spec.get('prior_quarter') if c == 'q/q' else spec.get('prior_year')
        if p is None and c == home:
            p = spec.get('prior')
        g, p = _num(g, f'{label}.growth ({c})'), _num(p, f'{label}.prior ({c})')
        if g is not None and g <= -1:
            raise ValueError(f'{label}.growth ({c}): {g} is not a growth rate a log can read; state the levels')
        if g is None and p is None:
            continue
        lv = value / p - 1 if value is not None and p is not None and value > 0 and p > 0 else None
        out[c] = {'stated': g, 'prior': p, 'levels': lv}
    return out


def _measure(spec, basis, period, label):
    if basis in POINT_BASES:
        return None
    w = spec.get('window')
    m = spec.get('period') or (w if w in MONTHS else None) or period
    m = 'year' if m == 'annual' else m
    if m not in MONTHS:
        raise ValueError(f'{label}.period: {m!r} is not a measurement window {tuple(MONTHS)}')
    return m


def _currency_code(unit, default, label):
    if unit is None:
        return default
    code = str(unit).split('/')[0].split('_per_')[0]
    if not F.MONETARY.match(code):
        raise ValueError(f'{label}.unit: {unit!r} must start with the ISO code of the currency it is stated in')
    return code


def _line_id(key, spec, lines):
    """The registry line a case line names: `line`, its key, or '<scope>_<line>' (us_commercial_revenue); else None."""
    if spec.get('line') is not None:
        if spec['line'] not in lines:
            raise ValueError(f'lines.{key}.line: {spec["line"]!r} is not a registry line')
        return spec['line']
    if key in lines:
        return key
    scope = spec.get('scope')
    if isinstance(scope, str) and key.startswith(scope + '_') and key[len(scope) + 1:] in lines:
        return key[len(scope) + 1:]
    return None


def _index(case, mids):
    """The case's lines, KPIs and segments as items keyed by (id, scope), validated and point in time."""
    reg = registry()
    lines = reg['lines']
    spec = {}
    for mid in mids:
        m = reg['models'][mid]
        spec[mid] = {'kpis': m['kpis'], 'ids': {i['id']: i for i in m['identities']}, 'tags': factor_tags(mid), 'model': m}
    kspec = {}
    for mid in mids:
        for kid, s in spec[mid]['kpis'].items():
            kspec.setdefault(kid, {})[mid] = s
    idents = {i for s in spec.values() for i in s['ids']}
    ix = {'case': case, 'models': list(mids), 'spec': spec, 'lines': lines, 'rules': reg['rules'], 'checks': reg['checks'], 'period': case['period'],
          'currency': case['currency'], 'line': {}, 'kpi': {}, 'stated': {}, 'not_read': [], 'unavailable': [],
          'read': set(), 'cache': {}, 'busy': set(), 'reclassified': {}, 'questions': []}
    rows = case.get('kpis') or []
    if not isinstance(rows, list):
        raise ValueError('kpis: a list of rows')
    seen = set()
    for i, row in enumerate(rows):
        label = f'kpis[{i}]'
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            raise ValueError(f'{label}: needs an id')
        ids = _source_ids(case, row, label)
        ix['read'].add('kpis')
        kid, scope, basis = row['id'], _scope(row.get('scope'), label), row.get('kpi_basis')
        if basis not in BASES:
            raise ValueError(f'{label}.kpi_basis: must be one of {BASES}')
        money = kid in kspec and F.CURRENCY_TOKEN in kpi(kid)['unit']
        cb = row.get('currency_basis', 'reported') if money else None
        if money and cb not in F.CURRENCY_BASES:
            raise ValueError(f'{label}.currency_basis: must be one of {F.CURRENCY_BASES}')
        ref = f'kpis[{kid}|{scope}|{basis}' + ('|constant_currency' if cb == 'constant_currency' else '') + ']'
        if ref in seen:
            raise ValueError(f'{label}: a second {ref}; state one figure per KPI, scope, basis and currency basis')
        seen.add(ref)
        value = _num(row.get('value'), f'{label}.value')
        cmp = _cmp(row, label, value)
        if kid not in kspec:
            if kid in idents and not _visible(case, ids):
                ix['unavailable'].append(ref)
            elif kid in idents:
                ix['stated'].setdefault((kid, scope), []).append(
                    {'ref': ref, 'kpi_basis': basis, 'value': value, 'prior': (cmp.get('y/y') or {}).get('prior'), 'source_ids': ids,
                     'unit': row.get('unit')})
            else:
                ix['not_read'].append({'ref': ref, 'reason': 'not a KPI of the selected models (brain/economics.json)'})
            continue
        if not _visible(case, ids):
            ix['unavailable'].append(ref)
            continue
        if value is None and not cmp:
            ix['not_read'].append({'ref': ref, 'reason': 'neither a level nor a growth rate'})
            continue
        ix['kpi'].setdefault((kid, scope), []).append(
            {'kind': 'kpi', 'id': kid, 'scope': scope, 'basis': basis, 'unit': kpi(kid)['unit'], 'money': money,
             'currency': _currency_code(row.get('unit'), case['currency'], label) if money else None, 'currency_basis': cb,
             'measure': _measure(row, basis, case['period'], label), 'value': value, 'cmp': cmp, 'ref': ref, 'source_ids': ids,
             'window': row.get('window') if row.get('window') in COMPARISONS else 'y/y'})
    block = case.get('lines') or {}
    if not isinstance(block, dict):
        raise ValueError('lines: an object {line: {...}}')
    for key in sorted(block):
        s, label = block[key], f'lines.{key}'
        if not isinstance(s, dict):
            raise ValueError(f'{label}: not an object')
        ix['read'].add('lines')
        lid = _line_id(key, s, lines)
        if lid is None:
            ix['not_read'].append({'ref': label, 'reason': 'not a reported line of the registry'})
            continue
        ids = _source_ids(case, s, label)
        if not _visible(case, ids):
            ix['unavailable'].append(label)
            continue
        cb = s.get('currency_basis', 'reported')
        if cb not in F.CURRENCY_BASES:
            raise ValueError(f'{label}.currency_basis: must be one of {F.CURRENCY_BASES}')
        value, basis = _num(s.get('value'), f'{label}.value'), lines[lid]['basis']
        it = {'kind': 'line', 'id': lid, 'scope': _scope(s.get('scope'), label), 'basis': basis, 'unit': lines[lid]['unit'],
              'money': True, 'currency': case['currency'], 'currency_basis': cb, 'measure': _measure(s, basis, case['period'], label),
              'value': value, 'cmp': _cmp(s, label, value), 'ref': label, 'source_ids': ids,
              'window': s.get('window') if s.get('window') in COMPARISONS else 'y/y', 'role': s.get('role'),
              'margin': _num(s.get('margin'), f'{label}.margin'), 'share_of_revenue': _num(s.get('share_of_revenue'), f'{label}.share_of_revenue', 0),
              'pass_through': _num(s.get('pass_through'), f'{label}.pass_through', 0),
              'prior_pass_through': _num(s.get('prior_pass_through'), f'{label}.prior_pass_through', 0)}
        if it['value'] is None and not it['cmp'] and it['margin'] is None and it['share_of_revenue'] is None:
            ix['not_read'].append({'ref': label, 'reason': 'neither a level nor a growth rate'})
            continue
        ix['line'].setdefault((lid, it['scope']), []).append(it)
    segs = case.get('segments') or []
    if not isinstance(segs, list):
        raise ValueError('segments: a list')
    names = set()
    for i, s in enumerate(segs):
        label = f'segments[{i}]'
        if not isinstance(s, dict) or not isinstance(s.get('name'), str) or not s['name'].strip():
            raise ValueError(f'{label}: needs a name')
        scope = _scope(s['name'], label)
        if scope in names:
            raise ValueError(f'{label}: a second segment named {s["name"]!r}')
        names.add(scope)
        ids = _source_ids(case, s, label)
        ix['read'].add('segments')
        if not _visible(case, ids):
            ix['unavailable'].append(f'segments[{s["name"]}]')
            continue
        for field, lid, prior_key in SEGMENT_LINES:
            ref = f'segments[{s["name"]}].{field}'
            value, derived = _num(s.get(field), ref), None
            mk = SEGMENT_MARGINS.get(field)
            if value is None and mk and s.get(mk) is not None and s.get('revenue') is not None:
                value = number(s['revenue'], f'{label}.revenue') * number(s[mk], f'{label}.{mk}')
                derived = f'segments[{s["name"]}].revenue x {mk}'
                ref = f'segments[{s["name"]}].{mk}'
            prior = s.get(prior_key) if prior_key else None
            if value is None and prior is None:
                continue
            cmp = _cmp({'value': value, 'prior': prior, 'growth': s.get('growth') if field == 'revenue' else None}, ref, value)
            ix['line'].setdefault((lid, scope), []).append(
                {'kind': 'line', 'id': lid, 'scope': scope, 'basis': lines[lid]['basis'], 'unit': lines[lid]['unit'], 'money': True,
                 'currency': case['currency'], 'currency_basis': 'reported', 'measure': case['period'], 'value': value, 'cmp': cmp,
                 'ref': ref, 'source_ids': ids, 'window': 'y/y', 'role': None, 'margin': None, 'share_of_revenue': None,
                 'pass_through': None, 'prior_pass_through': None, 'derived_from': derived,
                 'profit_definition': s.get('profit_definition') if lid == 'segment_profit' else None})
        for k in sorted(s):
            if k not in kspec or s[k] is None:
                continue
            ref = f'segments[{s["name"]}].{k}'
            declared = sorted({b for v in kspec[k].values() for b in v['basis']})
            if len(declared) != 1:
                ix['not_read'].append({'ref': ref, 'reason': f'basis not stated and the registry declares {declared}'})
                continue
            money = F.CURRENCY_TOKEN in kpi(k)['unit']
            value = number(s[k], ref)
            ix['kpi'].setdefault((k, scope), []).append(
                {'kind': 'kpi', 'id': k, 'scope': scope, 'basis': declared[0], 'unit': kpi(k)['unit'], 'money': money,
                 'currency': case['currency'] if money else None, 'currency_basis': 'reported' if money else None,
                 'measure': _measure({}, declared[0], case['period'], ref), 'value': value, 'cmp': {}, 'ref': ref,
                 'source_ids': ids, 'window': 'y/y', 'basis_from': 'the only basis the registry declares'})
    for items in list(ix['kpi'].values()) + list(ix['line'].values()):     # content order: the input order never matters
        items.sort(key=lambda it: (it['basis'], it['currency_basis'] not in (None, 'reported'), it['ref']))
    for items in ix['stated'].values():
        items.sort(key=lambda st: (st['kpi_basis'], st['ref']))
    for (lid, scope), items in ix['line'].items():          # a line stated as a margin or a share of revenue
        for it in items:
            if it['value'] is not None or (it['margin'] is None and it['share_of_revenue'] is None):
                continue
            rev = next((r for r in ix['line'].get(('revenue', scope), []) if r['value'] is not None), None)
            if rev is not None:
                field = 'margin' if it['margin'] is not None else 'share_of_revenue'
                it['value'] = it[field] * rev['value']
                it['derived_from'] = f'{it["ref"]}.{field} x {rev["ref"]}.value'
    ev = case.get('reclassification_evidence')
    if ev is not None:
        ix['read'].add('reclassification_evidence')
        for j, e in enumerate(ev if isinstance(ev, list) else [ev]):
            label = f'reclassification_evidence[{j}]'
            if not isinstance(e, dict) or not e.get('identity') or not e.get('reason'):
                raise ValueError(f'{label}: needs identity, reason and evidence_ids')
            ids = _source_ids(case, {'source_ids': e.get('evidence_ids')}, label)
            kinds = sorted({case['sources'][s]['kind'] for s in ids})
            issuer = [s for s in ids if case['sources'][s]['kind'] in EVIDENCE_KINDS and _visible(case, [s])]
            ix['reclassified'][e['identity']] = {'status': 'evidenced' if issuer else 'unproven', 'evidence_ids': issuer or ids,
                                                 'reason': e['reason'], 'source_kinds': kinds}
    return ix


def _roles(ident):
    t = ident['type']
    if t == 'per_unit':
        return [('line', ident['line']), ('units', ident['unit_kpi'])]
    if t == 'ratio':
        return [('num', ident['num']), ('den', ident['den'])]
    if t == 'share':
        return [('part', ident['part']), ('total', ident['total'])]
    return [('line', ident['line'])] + [('factor', f) for f in ident['factors']]


def _elsewhere(ix, mid, ref, scope):
    """Other scopes at which `ref` (a KPI or a line) exists: a scope_mismatch names them."""
    pool = ix['kpi'] if ref in ix['spec'][mid]['kpis'] else ix['line']
    return sorted({s for (k, s) in pool if k == ref and s != scope})


def _operand(ix, mid, ref, scope):
    """What an identity reads for `ref` at `scope`: a KPI's rows (end, average or period row), a reported line or another
    identity of the same model; None when nothing is there, {'absent': status} when a nested identity computed nothing."""
    sp = ix['spec'][mid]
    if ref in sp['ids']:
        res, meta = _evaluate(ix, mid, sp['ids'][ref], scope)
        if res.get('value') is None and not res.get('changes'):
            return {'id': ref, 'kind': 'identity', 'absent': res['status'], 'found_at': res.get('found_at') or [],
                    'missing': res.get('missing') or []}
        cmp = {}
        for ch in res.get('changes') or []:
            cmp.setdefault(ch['window'], {'stated': ch['value'] if ch['footing'] == 'stated' else None,
                                          'levels': ch['value'] if ch['footing'] == 'levels' else None, 'prior': None})
        row = {'value': res.get('value'), 'cmp': cmp, 'ref': f'identities[{ref}@{scope}]', 'basis': 'flow' if meta['measure'] else 'balance',
               'measure': meta['measure'], 'derived_from': f'identities[{ref}@{scope}].value'}
        return {'id': ref, 'kind': 'identity', 'point': False, 'rows': {'period': row}, 'money': meta['money'],
                'currency': meta['currency'], 'currency_basis': meta['currency_basis'], 'measure': meta['measure'],
                'unit': None, 'annual': False, 'undeclared': [], 'flow': False, 'cumulative': meta['cumulative']}
    if ref in sp['kpis']:
        items, declared = ix['kpi'].get((ref, scope), []), sp['kpis'][ref]['basis']
    else:
        items, declared = ix['line'].get((ref, scope), []), [ix['lines'][ref]['basis']]
    if not items:
        return None
    ok = [it for it in items if it['basis'] in declared]
    # the reported currency basis first, then a row measured over the case's own window (a quarter's bookings before a TTM)
    rows = sorted(ok or items, key=lambda it: (it['currency_basis'] not in (None, 'reported'), it['measure'] not in (None, ix['period'])))
    end = next((it for it in rows if it['basis'] in POINT_BASES), None)
    avg = next((it for it in rows if it['basis'] == 'average'), None)
    per = next((it for it in rows if it['basis'] in ('flow', 'rate')), None)
    head = end or per or avg
    return {'id': ref, 'kind': head['kind'], 'point': end is not None,
            'rows': {'end': end, 'average': avg} if end is not None else {'period': per or avg},
            'money': head['money'], 'currency': head['currency'], 'currency_basis': head['currency_basis'],
            'measure': None if end is not None else (per or avg)['measure'], 'unit': head['unit'],
            'annual': ref in ANNUAL_RATE_KPIS or head['unit'].endswith('_year'),
            'flow': end is None and (per or avg)['basis'] == 'flow', 'cumulative': any(it['basis'] == 'cumulative' for it in rows),
            'undeclared': [] if ok else [{'ref': it['ref'], 'basis': it['basis'], 'declared': list(declared)} for it in items]}


def _level(op, variant, measure):
    """(value, derived_from) of an operand in a variant ('end', 'average' or 'period'); (None, None) when it has none.
    The average of a balance is the stated average row, else (start + end) / 2 with the start the balance at the
    beginning of the measurement window (its prior quarter for a quarter, its prior year for a year)."""
    if not op['point']:
        r = op['rows']['period']
        return (r['value'], r.get('derived_from') or f'{r["ref"]}.value') if r['value'] is not None else (None, None)
    e = op['rows']['end']
    if variant == 'end':
        return (e['value'], e.get('derived_from') or f'{e["ref"]}.value') if e['value'] is not None else (None, None)
    a = op['rows']['average']
    if a is not None and a['value'] is not None:
        return a['value'], f'{a["ref"]}.value'
    c = e['cmp'].get(START.get(measure)) if measure else None
    if e['value'] is None or not c:
        return None, None
    if c['prior'] is not None:
        return (c['prior'] + e['value']) / 2, f'({e["ref"]} prior {START[measure]} + value) / 2'
    if c['stated'] is not None:
        return (e['value'] / (1 + c['stated']) + e['value']) / 2, f'({e["ref"]} value / (1 + growth {START[measure]}) + value) / 2'
    return None, None


def _growth_row(op, variant):
    if not op['point']:
        return op['rows']['period']
    return op['rows']['average'] if variant == 'average' else op['rows']['end']


def _any_growth(c):
    return c is not None and (c['stated'] is not None or c['levels'] is not None)


def _pick(c, footing):
    """(growth, 'stated'|'levels') on a footing; the other source when the preferred one is absent."""
    first, second = ('levels', 'stated') if footing == 'levels' else ('stated', 'levels')
    return (c[first], first) if c[first] is not None else (c[second], second)


def _footing(rows, window):
    """One footing for an identity's inputs: every growth from its levels when every input has both levels (exact),
    otherwise every growth as the source stated it (the same rounding on every input; levels only where no growth is
    stated). The line a product is checked against is read on the same footing, from its other source when it lacks
    that one (trips known only as levels; margin interest income only as a stated growth)."""
    return 'levels' if rows and all(r['cmp'][window]['levels'] is not None for r in rows) else 'stated'


def _growth_inputs(rows, window, footing):
    out = []
    for ref, r in rows:
        g, frm = _pick(r['cmp'][window], footing)
        out.append({'id': ref, 'growth': g, 'from': frm,
                    'derived_from': f'{r["ref"]} {window} ' + ('growth as stated' if frm == 'stated' else 'value / prior - 1')})
    return out


def _evaluate(ix, mid, ident, scope):
    key = (mid, ident['id'], scope)
    if key in ix['cache']:
        return ix['cache'][key]
    if key in ix['busy']:
        raise ValueError(f'economics.json: identity cycle at {mid}.{ident["id"]}')
    ix['busy'].add(key)
    try:
        out = _compute(ix, mid, ident, scope)
    finally:
        ix['busy'].discard(key)
    ix['cache'][key] = out
    return out


def _status_rank(s):
    return {'inconsistent': 2, 'partial': 1, 'consistent': 0}[s]


def _compute(ix, mid, ident, scope):
    """One identity at one scope: (public result, meta for identities that read it)."""
    rules, t = ix['rules'], ident['type']
    roles = _roles(ident)
    tag = ix['spec'][mid]['tags'].get(ident['id'])
    res = {'model': mid, 'id': ident['id'], 'type': t, 'tag': tag, 'core': bool(ident.get('core')), 'scope': scope,
           'inputs': {r: ref for r, ref in roles if r != 'factor'}}
    if t == 'product':
        res['inputs']['factors'] = list(ident['factors'])
    meta = {'money': False, 'currency': None, 'currency_basis': None, 'measure': None, 'present': [], 'cumulative': False}
    rc = ix['reclassified'].get(ident['id'])
    if rc and rc['status'] == 'evidenced':
        res['tag'], res['registry_tag'] = 'reclassification', tag
    if ident.get('lagged'):
        res.update(status='not_computed', check=ident.get('check'),
                   reason='construction lag: one period of capex over the same period of capacity additions is refused')
        return res, meta
    at = {}
    if t == 'share' and ident.get('part_scope'):          # logged-in DAU of total DAU: one KPI read at two scopes
        tot = scope if ident.get('total_scope', 'total') == 'total' else ident['total_scope']
        at = {'part': _part_scope(ident, scope), 'total': tot}
        res['read_at'] = dict(at)
        if ident['part'] == ident['total']:
            roles = [(r, f'{ref}@{at[r]}') for r, ref in roles]
    ops = {}
    for r, ref in roles:
        base = ref.split('@')[0]
        ops[ref] = _operand(ix, mid, base, at.get(r, scope))
    need = [ref for r, ref in roles if not (t == 'product' and r == 'line')]
    meta['present'] = [r for r in need if ops[r] is not None and not ops[r].get('absent')]
    absent = [r for r in need if ops[r] is None or ops[r].get('absent')]
    if absent:
        nested = {ops[r]['absent'] for r in absent if ops[r] is not None}
        found = {r: (ops[r]['found_at'] if ops[r] is not None else _elsewhere(ix, mid, r.split('@')[0], scope)) for r in absent}
        if 'basis_mismatch' in nested:
            status = 'basis_mismatch'
        elif all(found.values()) or 'scope_mismatch' in nested and all(found[r] or (ops[r] or {}).get('absent') == 'scope_mismatch' for r in absent):
            status = 'scope_mismatch'
        else:
            status = 'missing'
        res.update(status=status, missing=absent)
        if status == 'scope_mismatch':
            res['found_at'] = sorted({s for v in found.values() for s in v})
            res['reason'] = 'inputs are reported for another scope: ' + '; '.join(f'{r} at {", ".join(found[r]) or "a nested scope"}' for r in absent)
        return res, meta
    present = {r: op for r, op in ops.items() if op is not None and not op.get('absent')}
    meta['cumulative'] = any(op['cumulative'] for op in present.values())
    defs = sorted({it['profit_definition'] for op in present.values() for it in op['rows'].values() if it and it.get('profit_definition')})
    if defs:
        res['profit_definition'] = defs[0] if len(defs) == 1 else defs
    money = [(r, op) for r, op in present.items() if op['money']]
    problems = [{'id': r, 'problem': 'kpi_basis', 'stated': u['basis'], 'declared': u['declared']}
                for r, op in present.items() for u in op['undeclared']]
    if len({(op['currency'], op['currency_basis']) for _, op in money}) > 1:
        problems += [{'id': r, 'problem': 'currency' if op['currency'] != ix['currency'] else 'currency_basis',
                      'stated': op['currency'] if op['currency'] != ix['currency'] else op['currency_basis']}
                     for r, op in money if op['currency_basis'] != 'reported' or op['currency'] != ix['currency']]
    if problems:
        side = []
        for r, op in present.items():
            row = _growth_row(op, 'end')
            for w in COMPARISONS:
                c = row['cmp'].get(w) if row else None
                if _any_growth(c):
                    g, frm = _pick(c, 'stated')
                    side.append({'id': r, 'currency_basis': op['currency_basis'], 'window': w, 'growth': g, 'from': frm,
                                 'derived_from': f'{row["ref"]} {w} ' + ('growth as stated' if frm == 'stated' else 'value / prior - 1')})
                    break
        qs = [f'supply reported {_name(p["id"])} growth' if p['problem'] == 'currency_basis' else
              f'supply {_name(p["id"])} in {ix["currency"]}' if p['problem'] == 'currency' else
              f'supply {_name(p["id"])} on {_a_basis(p["declared"])} (stated {p["stated"]})' for p in problems]
        res.update(status='basis_mismatch', problems=problems, side_by_side=side, questions=sorted(set(qs)),
                   reason='inputs on different bases are never divided')
        return res, meta
    if money:
        meta.update(money=_is_money(t, roles, present), currency=money[0][1]['currency'], currency_basis=money[0][1]['currency_basis'])
    measures = {r: op['measure'] for r, op in present.items() if not op['point'] and op['measure']}
    if len(set(measures.values())) > 1:
        res.update(status='scope_mismatch', windows=dict(sorted(measures.items())),
                   reason='inputs measured over different windows (' + ', '.join(f'{r} {m}' for r, m in sorted(measures.items())) + ')')
        return res, meta
    measure = next(iter(measures.values()), None) or ix['period']
    meta['measure'] = measure if measures else None
    names = ('average', 'end') if any(op['point'] for op in present.values()) else ('period',)
    variants = {}
    for v in names:
        vals = {r: _level(op, v, measure) for r, op in present.items()}
        out = _variant(ix, ident, t, roles, present, vals, measure)
        if out is not None:
            variants[v] = out
    primary = next((v for v in names if v in variants), None)
    res['variant'] = primary
    res['value'] = variants[primary]['value'] if primary else None
    if variants:
        res['variants'] = variants
    changes, why = _changes(ix, t, roles, present, [primary] + [v for v in names if v != primary] if primary else list(names), res)
    res['changes'] = changes
    if why:
        res['change_reason'] = why
    if meta['cumulative']:
        res['label'] = 'cumulative, not active'
    checks = []
    if t == 'product':
        lc = _line_check(ix, ident, roles, present, variants.get(primary) if primary else None, measure)
        if lc:
            res['line_check'] = lc
            checks.append(lc['status'])
            if lc['status'] == 'partial':
                res['residual'] = lc['residual']
        for ch in changes:             # every comparison window's growth check (y/y and q/q): the worst one wins
            if ch.get('check'):
                checks.append(ch['check']['status'])
    stated = ix['stated'].get((ident['id'], scope))
    if stated and t in ('per_unit', 'ratio', 'share'):       # each stated figure on its own basis, in content order
        res['stated'] = [_stated_check(rules, st, names, variants, changes, meta, ix['currency']) for st in stated]
        checks += [c['status'] for c in res['stated'] if c['status'] != 'not_compared']
    if checks:
        res['status'] = max(checks, key=_status_rank)
    else:
        res['status'] = 'computed' if res['value'] is not None or changes else 'missing'
        if res['status'] == 'missing':
            res['missing'] = [f'{r} level' for r in need]
    res['derived_from'] = f'{ident["type"]} identity {mid}.{ident["id"]} over ' + ', '.join(f'{r}={ref}' for r, ref in roles)
    return res, meta


def _is_money(t, roles, present):
    """Whether an identity's value carries a currency: per unit and product when an input does; a ratio when exactly one
    side does (revenue / gross bookings cancels); a share never."""
    if t == 'share':
        return False
    if t == 'ratio':
        return sum(1 for _, ref in roles if present[ref]['money']) == 1
    return any(op['money'] for op in present.values())


def _annualizer(ident, op, measure):
    """Per year for a flow operand of an annualized identity: 12 / months of its window (x4 for a quarter)."""
    if not ident.get('annualize') or not op['flow']:
        return 1
    return 12 / MONTHS[op['measure'] or measure]


def _variant(ix, ident, t, roles, present, vals, measure):
    """The identity's level in one variant, or None when an input has no level in it."""
    v = {r: x for r, (x, _) in vals.items()}
    src = {r: d for r, (_, d) in vals.items()}
    if t == 'product':
        fs = ident['factors']
        if any(v.get(f) is None for f in fs):
            return None
        prod, notes = 1.0, []
        for f in fs:
            prod *= v[f]
        target = present.get(ident['line'])
        window = (target['measure'] if target and target['measure'] else None) or ix['period']
        if ident.get('months_per_period'):
            prod *= MONTHS[window]
            notes.append(f'x {MONTHS[window]} months of the {window}')
        annual = [f for f in fs if present[f]['annual']]
        if annual:
            if target is not None and v.get(ident['line']) is not None:
                prod *= MONTHS[window] / 12
                notes.append(f'x {MONTHS[window]}/12: {", ".join(annual)} per year, the line per {window}')
            else:
                window = 'year'
                notes.append(f'per year: {", ".join(annual)} per year and no line to put it on')
        return {'value': prod, 'window': window, 'derived_from': ' x '.join(src[f] for f in fs) + ('; ' + '; '.join(notes) if notes else '')}
    a, b = (r for _, r in roles)
    if t == 'share':
        if v.get(a) is not None and v.get(b) is not None and v[b] != 0:
            return {'value': v[a] / v[b], 'derived_from': f'{src[a]} / {src[b]}'}
        part = present[a]['rows'].get('period') or {}
        if b == 'revenue' and part.get('share_of_revenue') is not None:
            return {'value': part['share_of_revenue'], 'derived_from': f'{part["ref"]}.share_of_revenue as stated'}
        return None
    if v.get(a) is None or v.get(b) is None or v[b] == 0:
        return None
    ka, kb = _annualizer(ident, present[a], measure), _annualizer(ident, present[b], measure)
    out = {'value': v[a] * ka / (v[b] * kb), 'derived_from': f'{src[a]}' + (f' x {ka:g}' if ka != 1 else '') + f' / ({src[b]}' + (f' x {kb:g}' if kb != 1 else '') + ')'}
    if ka != 1 or kb != 1:
        out['annualized_by'] = ka if ka != 1 else kb
    return out


def _changes(ix, t, roles, present, order, res):
    """The identity's change on every comparison window its inputs share (y/y first), from the primary variant's rows
    and else the end rows; ([], reason) when no window is shared."""
    product = t == 'product'
    need = [ref for r, ref in roles if not (product and r == 'line')]
    for variant in order:
        rows = {ref: _growth_row(present[ref], variant) for ref in need}
        if any(r is None for r in rows.values()):
            continue
        windows = [w for w in COMPARISONS if all(_any_growth(r['cmp'].get(w)) for r in rows.values())]
        if windows:
            break
    else:
        have = {ref: sorted(w for w in COMPARISONS if _any_growth((_growth_row(op, order[0] if order else 'end') or {'cmp': {}})['cmp'].get(w)))
                for ref, op in present.items() if ref in need}
        if not any(have.values()):
            return [], None
        lacking = sorted(r for r, w in have.items() if not w)
        if lacking:
            return [], 'missing: no growth rate for ' + ', '.join(lacking)
        return [], 'scope_mismatch: no comparison window shared by every input (' + '; '.join(f'{r} {"/".join(w)}' for r, w in sorted(have.items())) + ')'
    out = []
    for w in windows:
        grp = [(ref, rows[ref]) for ref in need]
        footing = _footing([r for _, r in grp], w)
        ins = _growth_inputs(grp, w, footing)
        g = {i['id']: i['growth'] for i in ins}
        ch = {'window': w, 'variant': variant, 'footing': footing, 'inputs': ins}
        if t == 'product':
            f = 1.0
            for i in ins:
                f *= 1 + i['growth']
            ch.update(value=f - 1, factor=f, derived_from='product of (1 + g) over ' + ', '.join(i['id'] for i in ins))
            line = present.get(roles[0][1])
            lr = _growth_row(line, variant) if line else None
            if lr is not None and _any_growth(lr['cmp'].get(w)):
                lg, lfrom = _pick(lr['cmp'][w], footing)            # the line on the factors' footing
                tol = ix['rules']['growth_identity_pp']
                ch['check'] = {'line': roles[0][1], 'line_growth': lg, 'line_factor': 1 + lg, 'line_from': lfrom, 'product': f,
                               'residual_pp': (1 + lg) - f, 'residual_log': log(1 + lg) - sum(log(1 + i['growth']) for i in ins),
                               'status': 'consistent' if abs((1 + lg) - f) <= tol else 'inconsistent', 'growth_identity_pp': tol,
                               'derived_from': f'(1 + g_{roles[0][1]}) - product of factors; log residual ln(1 + g) - sum ln(1 + g_i)'}
        else:
            a, b = (ref for _, ref in roles)
            factor = (1 + g[a]) / (1 + g[b])
            ch.update(value=factor - 1, factor=factor, derived_from=f'(1 + g_{a}) / (1 + g_{b}) - 1')
            lvl = (res.get('variants') or {}).get(variant)
            if lvl is not None and t != 'share':
                ch['prior'] = lvl['value'] / factor
                ch['derived_from'] += '; prior = value / (1 + change)'
            if t == 'share':
                share = lvl['value'] if lvl is not None else None
                ch['share_change'] = ch['value']
                if share is not None and g[b] != 0:
                    ch['incremental_share'] = share * (1 - 1 / (1 + g[a])) / (1 - 1 / (1 + g[b]))
                    ch['derived_from'] += f'; incremental share = share x (1 - 1/(1 + g_{a})) / (1 - 1/(1 + g_{b})) = d{a} / d{b}'
        out.append(ch)
    return out, None


def _line_check(ix, ident, roles, present, var, measure):
    """A product's level against the reported line: consistent within rules.identity_tolerance, partial below
    rules.partial_below (the residual named, an open question), inconsistent otherwise."""
    line = present.get(roles[0][1])
    if var is None or line is None:
        return None
    lv, src = _level(line, 'end', measure)
    if lv is None or lv <= 0:
        return None
    rules = ix['rules']
    explained = var['value'] / lv
    status = ('consistent' if abs(explained - 1) <= rules['identity_tolerance'] else
              'partial' if 0 <= explained < rules['partial_below'] else 'inconsistent')
    out = {'line': roles[0][1], 'line_value': lv, 'computed': var['value'], 'explained': explained, 'diff': explained - 1,
           'status': status, 'identity_tolerance': rules['identity_tolerance'], 'partial_below': rules['partial_below'],
           'derived_from': f'computed / {src}'}
    if status == 'partial':
        fs = ' x '.join(_name(f) for f in ident['factors'])
        out['residual'] = {'name': f'{_name(roles[0][1])} not in {fs}', 'value': lv - var['value'], 'share': 1 - explained,
                           'status': 'open', 'question': f'what makes up the {_name(roles[0][1])} that {fs} does not explain?',
                           'derived_from': f'{src} - computed'}
    return out


def _stated_variant(basis, names, variants):
    """The variant a stated figure is compared with, on its own basis: the end value for a balance or cumulative figure,
    the average for an average one (the period value when every input is a period row: `names` is ('period',)), the
    period value for a flow or rate figure. (wanted, None) when that variant was not computed: a figure is never
    compared across bases."""
    if basis in POINT_BASES:
        want = 'end'
    elif basis == 'average':
        want = 'period' if names == ('period',) else 'average'
    else:
        want = 'period'
    return want, (want if want in variants else None)


def _stated_check(rules, st, names, variants, changes, meta, currency):
    """A source's own figure for the identity (revenue per account as the post states it) against the computed one, on
    the matching basis (end, average or period) and in the same currency; never compared across bases or currencies."""
    want, v = _stated_variant(st['kpi_basis'], names, variants)
    out = {'ref': st['ref'], 'kpi_basis': st['kpi_basis'], 'source_ids': st['source_ids'], 'variant': v, 'stated': st['value'],
           'stated_prior': st['prior']}
    code = str(st.get('unit') or currency).split('/')[0]
    if meta['money'] and F.MONETARY.match(code) and code != meta['currency']:
        out.update(status='not_compared', reason=f'stated in {code}, computed in {meta["currency"]}')
        return out
    if v is None:
        out.update(status='not_compared', reason=f'stated on {_a_basis([st["kpi_basis"]])}; no {want} value was computed to compare it with')
        return out
    diffs = []
    if st['value'] is not None and st['value'] != 0:
        out['computed'] = variants[v]['value']
        out['diff'] = variants[v]['value'] / st['value'] - 1
        diffs.append(out['diff'])
    ch = next((c for c in changes if c['window'] == 'y/y' and c.get('prior') is not None and c['variant'] == v), None)
    if ch and st['prior'] is not None and st['prior'] != 0:
        out['computed_prior'] = ch['prior']
        out['prior_diff'] = ch['prior'] / st['prior'] - 1
        diffs.append(out['prior_diff'])
    out['status'] = ('not_compared' if not diffs else
                     'consistent' if all(abs(d) <= rules['identity_tolerance'] for d in diffs) else 'inconsistent')
    out['identity_tolerance'] = rules['identity_tolerance']
    out['derived_from'] = 'computed / stated - 1'
    return out


def _base_refs(sp, ident, seen=None):
    """(KPI ids, line ids) an identity reaches, nested identities expanded."""
    seen = set() if seen is None else seen
    kp, ln = set(), set()
    for ref in identity_refs(ident):
        if ref in sp['ids']:
            if ref not in seen:
                seen.add(ref)
                k, l = _base_refs(sp, sp['ids'][ref], seen)
                kp |= k
                ln |= l
        elif ref in sp['kpis']:
            kp.add(ref)
        else:
            ln.add(ref)
    return kp, ln


def _scopes(ix, mid, ident):
    """Where an identity is read: the total, every scope a KPI it reaches is reported for (a segment, a geography, a
    customer group) and, for an identity of lines only, every scope those lines exist at. A by-segment identity has no
    total. A share across two named scopes (logged-in of total) is read once, at the part's scope."""
    if ident.get('part_scope'):
        pool = ix['kpi'] if ident['total'] in ix['spec'][mid]['kpis'] else ix['line']
        found = {s for (k, s) in pool if k == ident['total'] and (ident['part'], _part_scope(ident, s)) in pool} | {'total'}
        return sorted(found, key=lambda s: (s != 'total', s))
    kp, ln = _base_refs(ix['spec'][mid], ident)
    pool, ids = (ix['kpi'], kp) if kp else (ix['line'], ln)
    found = {s for (k, s) in pool if k in ids} | {'total'}
    if ident.get('by') == 'segment':
        found.discard('total')
    return sorted(found, key=lambda s: (s != 'total', s))


def _part_scope(ident, scope):
    """The part's scope of a share across two scopes of one KPI (logged-in of total): part_scope itself at the total,
    '<scope>_<part_scope>' at a narrower scope (us_logged_in of us)."""
    return ident['part_scope'] if scope == 'total' else f'{scope}_{ident["part_scope"]}'


def _keep(res, meta):
    """Rows shown: the total row of a core identity always, and any total row that computed something or names a
    mismatch; at a narrower scope (a segment, a geography) a row that computed something, and a core identity's missing
    or mismatched row only when one of its inputs is reported there (it says what else is missing)."""
    if res['scope'] == 'total':
        return res['core'] or res['status'] != 'missing'
    if res['status'] not in ('missing', 'scope_mismatch'):
        return True
    return res['core'] and bool(meta['present'])


def evaluate_identity(case, model_id, identity_id, scope='total'):
    """One identity of a model on an econ case at one scope (registry lookup: identity(model_id, identity_id))."""
    _check_case(case)
    model(model_id)
    ix = _index(case, [model_id])
    return deepcopy(_evaluate(ix, model_id, identity(model_id, identity_id), _scope(scope, 'scope'))[0])


def _split(ix, mid, scope):
    """ln(1 + g) = sum ln(1 + g_i) + residual for the model's growth split at a scope, every factor with its tag; the
    residual is always shown. A missing factor leaves the split incomplete: the known factors and the unassigned rest
    (which includes the missing ones, named) are shown, never a filled-in factor."""
    sp = ix['spec'][mid]
    gs, rules = sp['model']['growth_split'], ix['rules']
    out = {'model': mid, 'line': gs['line'], 'scope': scope}
    line = _operand(ix, mid, gs['line'], scope)
    if gs.get('mode') == 'additive':
        out.update(mode='additive', part=gs['part'], status='missing', missing=[gs['part']],
                   reason='an additive split reads each part as its own KPI row; none at this scope')
        parts = sorted(s for (k, s) in ix['kpi'] if k == gs['part'])
        if parts:
            out.update(status='missing', reason='additive parts are read by category (process evidence, a later work package)', parts=parts)
        return out
    tags = sp['tags']
    facs = []
    for f in gs['factors']:
        op = _operand(ix, mid, f, scope)
        facs.append((f, op))
    absent = [f for f, op in facs if op is None or op.get('absent')]
    if line is None:
        absent = [gs['line']] + absent
    present = [(f, op) for f, op in facs if op is not None and not op.get('absent')]
    nested = {op['absent'] for _, op in facs if op is not None and op.get('absent')}
    money = [(r, op) for r, op in ([(gs['line'], line)] if line else []) + present if op['money']]
    undeclared = [{'id': f, 'problem': 'kpi_basis', 'stated': u['basis'], 'declared': u['declared']} for f, op in present for u in op['undeclared']]
    mismatch = 'basis_mismatch' in nested or bool(undeclared) or len({(op['currency'], op['currency_basis']) for _, op in money}) > 1
    if undeclared:            # its growth is never read as the factor's: the split names it and asks for the declared basis
        out['problems'] = undeclared
        for u in undeclared:
            ix['questions'].append({'model': mid, 'about': f'growth_split.{u["id"]}', 'scope': scope,
                                    'question': f'supply {_name(u["id"])} on {_a_basis(u["declared"])} (stated {u["stated"]})'})
    rows = {f: None if op['undeclared'] else _growth_row(op, 'end') for f, op in present}    # an undeclared basis is not read
    lrow = _growth_row(line, 'end') if line else None
    window = next((w for w in COMPARISONS if lrow is not None and _any_growth(lrow['cmp'].get(w))
                   and all(r is not None and _any_growth(r['cmp'].get(w)) for r in rows.values())), None)
    if window is None and lrow is not None:
        window = next((w for w in COMPARISONS if _any_growth(lrow['cmp'].get(w))), None)
    out['window'] = window
    known = []
    if window is not None and lrow is not None:
        kpi_rows = [r for f, r in rows.items() if f in sp['kpis'] and r is not None and r['cmp'].get(window)]
        footing = _footing(kpi_rows, window)
        lg, lfrom = _pick(lrow['cmp'][window], footing)           # the line on the factors' footing
        out['line_growth'] = {'growth': lg, 'factor': 1 + lg, 'log': log(1 + lg), 'from': lfrom,
                              'derived_from': f'{lrow["ref"]} {window} ' + ('growth as stated' if lfrom == 'stated' else 'value / prior - 1')}
        for f, op in present:
            r = rows[f]
            if r is None or not _any_growth(r['cmp'].get(window)):
                continue
            g, frm = _pick(r['cmp'][window], footing if f in sp['kpis'] else 'stated')
            tag = 'reclassification' if (ix['reclassified'].get(f) or {}).get('status') == 'evidenced' else tags.get(f)
            known.append({'id': f, 'kind': op['kind'], 'tag': tag, 'growth': g, 'factor': 1 + g, 'log': log(1 + g), 'from': frm,
                          'share': log(1 + g) / log(1 + lg) if lg != 0 else None,
                          'derived_from': f'{r["ref"]} {window} ' + ('growth as stated' if frm == 'stated' else 'value / prior - 1')
                          + '; share = ln(1 + g_i) / ln(1 + g)'})
    out['factors'] = known
    lacking = absent + [f for f, _ in present if f not in {k['id'] for k in known}]
    if mismatch:
        out['status'] = 'basis_mismatch'
    elif lacking:
        other_window = {f for f, op in present if rows[f] is not None and any(_any_growth(rows[f]['cmp'].get(w)) for w in COMPARISONS)}
        found = [f for f in lacking if f in other_window or (f in sp['kpis'] or f in ix['lines']) and _elsewhere(ix, mid, f, scope)
                 or f in sp['ids'] and (_operand(ix, mid, f, scope) or {}).get('absent') == 'scope_mismatch']
        out['status'] = 'scope_mismatch' if found and len(found) == len(lacking) else 'missing'
        if other_window & set(lacking):
            out['reason'] = 'no comparison window shared with the line: ' + ', '.join(sorted(other_window & set(lacking)))
    elif window is None:
        out['status'] = 'missing'
    if lacking or mismatch:
        out['missing'] = lacking
    if 'line_growth' in out and known:
        total = out['line_growth']['log']
        known_log = sum(k['log'] for k in known)
        by_tag = {}
        for k in known:
            by_tag[k['tag'] or 'untagged'] = by_tag.get(k['tag'] or 'untagged', 0.0) + k['log']
        out['by_tag'] = [{'tag': k, 'log': v, 'share': v / total if total != 0 else None,
                          'derived_from': f'sum of ln(1 + g_i) over the {k} factors; share = that / ln(1 + g)'} for k, v in sorted(by_tag.items())]
        if 'status' not in out:
            prod = 1.0
            for k in known:
                prod *= k['factor']
            out['product'] = prod
            out['residual'] = {'log': total - known_log, 'pp': out['line_growth']['factor'] - prod,
                               'share': (total - known_log) / total if total != 0 else None,
                               'derived_from': 'ln(1 + g) - sum ln(1 + g_i); (1 + g) - product of (1 + g_i)'}
            out['status'] = 'consistent' if abs(out['residual']['pp']) <= rules['growth_identity_pp'] else 'inconsistent'
            out['growth_identity_pp'] = rules['growth_identity_pp']
        else:
            out['unassigned'] = {'log': total - known_log, 'includes': lacking,
                                 'derived_from': 'ln(1 + g) - sum ln(1 + g_i) of the factors read; it includes the factors not read'}
    out['derived_from'] = f'growth split of {gs["line"]} over ' + ', '.join(gs['factors'])
    return out


def growth_split(case, model_id, scope='total'):
    """The model's growth split on an econ case at one scope (see _split)."""
    _check_case(case)
    model(model_id)
    return deepcopy(_split(_index(case, [model_id]), model_id, _scope(scope, 'scope')))


# ---------------------------------------------------------------- WP3: review

def _selection(sel):
    """[model ids] and the selection shown in the review: a model id, or {primary, secondary[], source} (econ.select's
    shape, WP4). None or 'unclassified' is not assessed: no rows, no effect."""
    if sel is None:
        return [], None
    if isinstance(sel, str):
        sel = {'primary': sel}
    if not isinstance(sel, dict):
        raise ValueError('model: a model id or {primary, secondary[]}')
    p, sec = sel.get('primary'), sel.get('secondary') or []
    source = sel.get('source', 'caller')
    if p in (None, UNCLASSIFIED):
        return [], {'primary': UNCLASSIFIED, 'secondary': [], 'source': source}
    if not isinstance(sec, list):
        raise ValueError('model.secondary: a list of model ids')
    mids = []
    for m in [p] + sec:
        model(m)
        if m not in mids:
            mids.append(m)
    return mids, {'primary': p, 'secondary': mids[1:], 'source': source}


def _comparisons(it):
    out = []
    for w in COMPARISONS:
        c = it['cmp'].get(w)
        if not c:
            continue
        g, frm = _pick(c, 'stated') if _any_growth(c) else (None, None)
        if c['prior'] is not None:
            prior, pfrom = c['prior'], 'stated'
        elif it['value'] is not None and c['stated'] is not None:
            prior, pfrom = it['value'] / (1 + c['stated']), 'derived'
        else:
            prior, pfrom = None, None
        row = {'window': w, 'growth': g, 'from': frm, 'prior': prior, 'prior_from': pfrom}
        if c['stated'] is not None and c['levels'] is not None:
            row['levels_growth'] = c['levels']
        row['derived_from'] = (f'{it["ref"]} {w}: growth ' + ('as stated' if frm == 'stated' else 'value / prior - 1' if frm else 'none')
                               + ('; prior = value / (1 + growth)' if pfrom == 'derived' else ''))
        out.append(row)
    return out


def _tables(ix):
    spec = ix['spec']
    kpis = []
    for (kid, scope) in sorted(ix['kpi']):
        for it in ix['kpi'][(kid, scope)]:
            row = {'id': kid, 'scope': scope, 'kpi_basis': it['basis'], 'currency_basis': it['currency_basis'], 'value': it['value'],
                   'measure': it['measure'], 'tags': {m: spec[m]['kpis'][kid]['tag'] for m in ix['models'] if kid in spec[m]['kpis']},
                   'core_in': [m for m in ix['models'] if spec[m]['kpis'].get(kid, {}).get('core')],
                   'basis_declared': any(it['basis'] in spec[m]['kpis'][kid]['basis'] for m in ix['models'] if kid in spec[m]['kpis']),
                   'comparisons': _comparisons(it), 'source_ids': it['source_ids'], 'ref': it['ref']}
            if it.get('basis_from'):
                row['basis_from'] = it['basis_from']
            kpis.append(row)
    lines = []
    for (lid, scope) in sorted(ix['line']):
        for it in ix['line'][(lid, scope)]:
            row = {'id': lid, 'scope': scope, 'value': it['value'], 'measure': it['measure'], 'currency_basis': it['currency_basis'],
                   'comparisons': _comparisons(it), 'source_ids': it['source_ids'], 'ref': it['ref']}
            for k in ('role', 'profit_definition'):
                if it.get(k):
                    row[k] = it[k]
            if it.get('derived_from'):
                row['derived_from'] = it['derived_from']
            if it['pass_through'] is not None:
                row['pass_through'] = _pass_through(ix, it)
            lines.append(row)
    return kpis, lines


def _pass_through(ix, it):
    """Growth with pass-through fees removed on both sides (registry line note: stated and removed before growth is read)."""
    w = it['window']
    c = it['cmp'].get(w) or {}
    prior = c.get('prior') if c.get('prior') is not None else (it['value'] / (1 + c['stated']) if it['value'] is not None and c.get('stated') is not None else None)
    out = {'window': w, 'pass_through': it['pass_through'], 'prior_pass_through': it['prior_pass_through']}
    if it['value'] is None or prior is None or it['prior_pass_through'] is None or prior - it['prior_pass_through'] <= 0:
        out['status'] = 'not_computed'
        out['reason'] = 'needs the level, the prior level and the prior period pass-through'
        ix['questions'].append({'model': None, 'about': it['ref'], 'scope': it['scope'],
                                'question': f'supply the prior period pass-through fees in {_name(it["id"])}'})
        return out
    out['growth_ex_pass_through'] = (it['value'] - it['pass_through']) / (prior - it['prior_pass_through']) - 1
    out['derived_from'] = f'({it["ref"]}.value - pass_through) / (prior - prior_pass_through) - 1; prior ' + ('as stated' if c.get('prior') is not None else '= value / (1 + growth)')
    return out


def _flag(ix, key, model_id, scope, **data):
    """One flag with the registry's severity (quarter.SEVERITY's levels); the numbers it carries come from the rows named
    in derived_from."""
    return {'key': key, 'severity': ix['checks'][key]['severity'], 'model': model_id, 'scope': scope, **data}


def _home_growth(it):
    """A KPI row's growth in the window its source compares (`window`), else the other one: (window, growth, from)."""
    for w in (it['window'],) + tuple(c for c in COMPARISONS if c != it['window']):
        c = it['cmp'].get(w)
        if _any_growth(c):
            g, frm = _pick(c, 'stated')
            return w, g, frm
    return None, None, None


def _check_per_unit(ix, mid, rows, flags):
    fall = ix['rules']['per_unit_fall']
    sp = ix['spec'][mid]
    for r in rows:
        ch = (r.get('changes') or [None])[0]
        if ch is None:
            continue
        if r['type'] == 'per_unit':
            units = next(i for i in ch['inputs'] if i['id'] == r['inputs']['units'])
            per, per_id = ch['value'], r['id']
        elif r['type'] == 'product' and r['core'] and len(ch['inputs']) == 2 and ch['inputs'][0]['id'] in sp['kpis'] \
                and sp['kpis'][ch['inputs'][0]['id']]['unit'] in UNIT_CLASSES:
            units, per, per_id = ch['inputs'][0], ch['inputs'][1]['growth'], ch['inputs'][1]['id']
        else:
            continue
        if units['growth'] > 0 and per < -fall:
            flags.append(_flag(ix, 'per_unit_vs_units', mid, r['scope'], identity=r['id'], window=ch['window'], units=units['id'],
                               units_growth=units['growth'], per_unit=per_id, per_unit_change=per, threshold=fall,
                               derived_from=f'identities[{r["id"]}@{r["scope"]}].changes[{ch["window"]}]'))


def _check_take_rate(ix, mid, rows, flags):
    tol = ix['rules']['growth_identity_pp']
    for r in rows:
        ident = ix['spec'][mid]['ids'].get(r['id']) or {}
        ch = (r.get('changes') or [None])[0]
        if ident.get('check') != 'take_rate_shift' or ch is None or abs(ch['value']) <= tol:
            continue
        rc = ix['reclassified'].get(r['id'])
        f = _flag(ix, 'take_rate_shift', mid, r['scope'], identity=r['id'], window=ch['window'], change=ch['value'], threshold=tol,
                  reclassification=rc['status'] if rc else 'unproven',
                  derived_from=f'identities[{r["id"]}@{r["scope"]}].changes[{ch["window"]}]; threshold rules.growth_identity_pp')
        if rc and rc['status'] == 'evidenced':
            f.update(tag='reclassification', evidence_ids=rc['evidence_ids'], read_on=r['inputs']['den'],
                     note=f'falsifiers and margins are read on the {_name(r["inputs"]["den"])} basis')
        else:
            f['tag'] = None
            ix['questions'].append({'model': mid, 'about': r['id'], 'scope': r['scope'],
                                    'question': 'is the take-rate change pricing, mix or a reclassification of what is booked as '
                                                'revenue? A reclassification needs an issuer document (evidence ids)'})
        flags.append(f)


def _check_thesis(ix, mid, flags):
    floor = ix['rules']['minor_line_share']
    sp = ix['spec'][mid]
    for (lid, scope), items in sorted(ix['line'].items()):
        if lid == 'revenue' or not any(it.get('role') == 'thesis_driver' for it in items):
            continue
        ident = next((i for i in sp['model']['identities'] if i['type'] == 'share' and i['part'] == lid and i['total'] == 'revenue'),
                     {'id': f'{lid}_share_of_revenue', 'type': 'share', 'part': lid, 'total': 'revenue'})
        res, _ = _evaluate(ix, mid, ident, scope)
        if res.get('value') is None:
            ix['questions'].append({'model': mid, 'about': 'thesis_on_minor_line', 'scope': scope,
                                    'question': f'supply {_name(lid)} and revenue on the same window to size the thesis line'})
            continue
        if res['value'] < floor:
            ch = (res.get('changes') or [{}])[0]
            flags.append(_flag(ix, 'thesis_on_minor_line', mid, scope, line=lid, share=res['value'], window=ch.get('window'),
                               incremental_share=ch.get('incremental_share'), threshold=floor,
                               derived_from=f'identities[{ident["id"]}@{scope}]: share of revenue and of incremental revenue'))


def _check_flat(ix, mid, flags):
    flat = ix['rules']['flat_units_abs']
    sp = ix['spec'][mid]
    for kid, s in sp['kpis'].items():
        if not s.get('core') or s['unit'] not in UNIT_CLASSES:
            continue
        for (k, scope), items in sorted(ix['kpi'].items()):
            if k != kid:
                continue
            for it in items:
                w, g, frm = _home_growth(it)
                if g is None or abs(g) >= flat:
                    continue
                f = _flag(ix, 'core_units_flat', mid, scope, kpi=kid, kpi_basis=it['basis'], window=w, growth=g, threshold=flat,
                          derived_from=f'{it["ref"]} {w} growth ' + ('as stated' if frm == 'stated' else 'value / prior - 1'))
                other = next((c for c in COMPARISONS if c != w and _any_growth(it['cmp'].get(c))), None)
                if other:
                    f['other_window'], f['other_growth'] = other, _pick(it['cmp'][other], 'stated')[0]
                flags.append(f)


def _next_period(label):
    m = _QUARTER.match(label)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        return f'{y + (q == 4)}-Q{q % 4 + 1}'
    m = re.match(r'^(FY)?(\d{4})$', label)
    return f'{m.group(1) or ""}{int(m.group(2)) + 1}' if m else None


def _disclosures(ix):
    case = ix['case']
    out = []
    for i, d in enumerate(case.get('disclosure_changes') or []):
        label = f'disclosure_changes[{i}]'
        ids = _source_ids(case, d, label)
        ix['read'].add('disclosure_changes')
        if not d.get('kpi') or not d.get('last_period') or not d.get('kind'):
            raise ValueError(f'{label}: needs kpi, last_period and kind')
        if not _visible(case, ids):
            _unavailable(ix, label)
        else:
            out.append({'kpi': d['kpi'], 'scope': _scope(d['scope'], label) if d.get('scope') is not None else None,
                        'last_period': str(d['last_period']), 'kind': d['kind'], 'source_ids': ids})
    return out


def _falsifiers(ix, mid, identities, drops, flags):
    out = []
    for f in falsifiers(mid):
        rows = []
        if f['kind'] == 'kpi':
            for (k, scope), items in sorted(ix['kpi'].items()):
                for it in items if k == f['id'] else []:
                    w, g, frm = _home_growth(it)
                    rows.append({'id': f['id'], 'kind': 'kpi', 'tag': f['tag'], 'scope': scope, 'kpi_basis': it['basis'],
                                 'value': it['value'], 'window': w, 'growth': g, 'status': 'tracked', 'source_ids': it['source_ids'],
                                 'derived_from': f'{it["ref"]}' + (f' {w} growth ' + ('as stated' if frm == 'stated' else 'value / prior - 1') if w else '')})
        else:
            for r in identities:
                if r['model'] == mid and r['id'] == f['id'] and (r.get('value') is not None or r.get('changes')):
                    ch = (r.get('changes') or [{}])[0]
                    rows.append({'id': f['id'], 'kind': 'identity', 'tag': r['tag'], 'scope': r['scope'], 'value': r.get('value'),
                                 'window': ch.get('window'), 'change': ch.get('value'), 'status': 'tracked',
                                 'derived_from': f'identities[{r["id"]}@{r["scope"]}]'})
                    rc = ix['reclassified'].get(f['id'])
                    if rc and rc['status'] == 'evidenced':
                        rows[-1]['read_on'] = r['inputs'].get('den')
        if not rows:
            rows.append({'id': f['id'], 'kind': f['kind'], 'tag': f['tag'], 'scope': None, 'status': 'missing'})
        for row in rows:
            for d in drops:
                if d['kpi'] == f['id'] and d['kind'] in DISCLOSURE_KINDS and (d['scope'] is None or d['scope'] == row['scope']):
                    row.update(status='unobservable', last_period=d['last_period'], from_period=_next_period(d['last_period']),
                               disclosure_source_ids=d['source_ids'])
                    flags.append(_flag(ix, 'disclosure_dropped', mid, row['scope'], kpi=f['id'], last_period=d['last_period'],
                                       from_period=row['from_period'], source_ids=d['source_ids']))
                    ix['questions'].append({'model': mid, 'about': f['id'], 'scope': row['scope'],
                                            'question': f'which reported figure replaces {_name(f["id"])} ({row["scope"]}) after {d["last_period"]}?'})
        out += rows
    return out


def _deceleration(ix, mid, kpis, prior):
    rules = ix['rules']
    out = {'check': 'cyclical_deceleration', 'kind': 'reading', 'model': mid, 'value': None,
           'cyclical_slowdown_pp': rules['cyclical_slowdown_pp'], 'structural_stable_pp': rules['structural_stable_pp']}
    if prior is None:
        out['reason'] = 'needs the prior review (review(case, model, prior=earlier_review))'
        return out
    yoy = lambda row: next((c['growth'] for c in row.get('comparisons') or [] if c['window'] == 'y/y' and c['growth'] is not None), None)
    before = {(r['id'], r['kpi_basis'], r['scope']): yoy(r) for r in prior.get('kpis') or []}
    tags = ix['spec'][mid]['kpis']
    cyc, struc = [], []
    for r in kpis:
        k = (r['id'], r['kpi_basis'], r['scope'])
        g0, g1 = before.get(k), yoy(r)
        if r['id'] not in tags or g0 is None or g1 is None:
            continue
        row = {'id': r['id'], 'kpi_basis': r['kpi_basis'], 'scope': r['scope'], 'prior_growth': g0, 'growth': g1, 'change': g1 - g0,
               'derived_from': f'y/y growth in this review minus y/y growth in the prior review ({prior["period_end"]})'}
        {'cyclical': cyc, 'structural': struc}.get(tags[r['id']]['tag'], []).append(row)
    out.update(cyclical=cyc, structural=struc, prior_period_end=prior['period_end'])
    if cyc and struc:
        out['value'] = all(c['change'] < -rules['cyclical_slowdown_pp'] for c in cyc) and \
            all(abs(s['change']) <= rules['structural_stable_pp'] for s in struc)
    else:
        out['reason'] = 'needs at least one cyclical and one structural KPI with y/y growth in both reviews'
    out['rule'] = 'every cyclical KPI slows by more than cyclical_slowdown_pp while every structural KPI stays within structural_stable_pp'
    return out


def _expansion(ix, mid, splits, readings):
    sp = ix['spec'][mid]
    per = next((i['id'] for i in sp['model']['identities'] if i['type'] == 'per_unit' and i.get('core')), None)
    for s in splits:
        if s['model'] != mid or per is None:
            continue
        fac = next((f for f in s.get('factors') or [] if f['id'] == per), None)
        if fac is None or fac['share'] is None or s['status'] not in ('consistent',):
            continue
        rows = ix['kpi'].get(('net_revenue_retention', s['scope'])) or ix['kpi'].get(('net_revenue_retention', 'total')) or []
        ndr = next((it for it in rows if it['value'] is not None), None)
        r = {'check': 'expansion_led', 'kind': 'reading', 'model': mid, 'scope': s['scope'], 'window': s['window'], 'per_unit': per,
             'per_unit_log_share': fac['share'], 'expansion_ndr_min': ix['rules']['expansion_ndr_min'],
             'rule': 'the per-unit share of log growth above one half (EXPANSION_LOG_SHARE) and NDR above expansion_ndr_min; NDR '
                     'at the split\'s scope, else the total\'s NDR read beside it (a cross-scope reading, named in ndr_scope)',
             'derived_from': f'growth_splits[{mid}@{s["scope"]}] factor {per}'}
        if ndr is None:
            r['value'] = None
            r['reason'] = 'net revenue retention not supplied'
            ix['questions'].append({'model': mid, 'about': 'expansion_led', 'scope': s['scope'], 'question': 'supply net revenue retention'})
        else:
            c = ndr['cmp'].get('y/y') or {}
            r.update(value=fac['share'] > EXPANSION_LOG_SHARE and ndr['value'] > ix['rules']['expansion_ndr_min'], ndr=ndr['value'],
                     ndr_scope=ndr['scope'], ndr_ref=ndr['ref'])
            if c.get('prior') is not None:
                r['ndr_prior'], r['ndr_change'] = c['prior'], ndr['value'] - c['prior']
            if ndr['scope'] != s['scope']:
                # the scope rule (identities, splits) forbids mixing scopes; this reading puts two figures side by side and
                # computes nothing from them, so it is allowed but named, and the same-scope NDR is asked for
                r['scope_note'] = (f'cross-scope: NDR is reported for {ndr["scope"]}, not for {s["scope"]}; the reading puts '
                                   f'the {ndr["scope"]} NDR beside the {s["scope"]} per-unit share')
                ix['questions'].append({'model': mid, 'about': 'expansion_led', 'scope': s['scope'],
                                        'question': f'supply net revenue retention for {s["scope"]} (reported for {ndr["scope"]} only)'})
        readings.append(r)


def _rate_sensitivity(ix, mid, measures):
    case = ix['case']
    nii = next((it for it in ix['line'].get(('net_interest_income', 'total'), []) if it['value'] is not None), None)
    rows = []
    for i, s in enumerate(case.get('sensitivities') or []):
        label = f'sensitivities[{i}]'
        ids = _source_ids(case, s, label)
        ix['read'].add('sensitivities')
        if not _visible(case, ids):
            _unavailable(ix, label)
            continue
        rows.append((str(s.get('driver') or ''), number(s.get('annual_impact'), f'{label}.annual_impact'), _num(s.get('base_annual'), f'{label}.base_annual'), ids, label))
    for (k, scope), items in sorted(ix['kpi'].items()):
        if k == 'rate_sensitivity_25bp':
            rows += [(f'25bp ({scope})', it['value'], None, it['source_ids'], it['ref']) for it in items if it['value'] is not None]
    for driver, impact, base, ids, ref in rows:
        bp = _BP.search(driver)
        m = {'check': 'rate_sensitivity_share', 'kind': 'measure', 'model': mid, 'driver': driver, 'annual_impact': impact,
             'source_ids': ids, 'ref': ref}
        if not bp or abs(float(bp.group(1))) != 25:
            m.update(status='not_computed', reason='not a 25bp move; a sensitivity is never rescaled')
        elif nii is None:
            m.update(status='not_computed', reason='needs the net_interest_income line')
        elif nii['value'] <= 0:
            m.update(status='not_computed', net_interest_income=nii['value'],
                     reason=f'{nii["ref"]} is not positive: a share of it is not read')
        else:
            k = 12 / MONTHS[nii['measure']]
            m.update(status='computed', net_interest_income=nii['value'], annualized_by=k, annual_net_interest_income=nii['value'] * k,
                     share=abs(impact) / (nii['value'] * k), derived_from=f'|annual_impact| / ({nii["ref"]}.value x {k:g})')
            if base is not None:
                m['stated_base'] = base
        measures.append(m)


def _margin_only(ix, mid, measures):
    case = ix['case']
    cyc, mkt = case.get('cycle'), case.get('market')
    m = {'check': 'margin_only_normalized', 'kind': 'measure', 'model': mid, 'label': 'margin_only',
         'note': 'a floor on the correction: revenue stays at the record run rate'}
    if not isinstance(cyc, dict) or not isinstance(mkt, dict):
        m.update(status='not_computed', reason='needs market {price, run_rate_eps} and cycle {cycle_margin, current_operating_margin}')
        measures.append(m)
        return
    ix['read'] |= {'cycle', 'market'}
    # point in time, as every other block: a block none of whose sources was available at as_of is not read (listed under
    # unavailable). cycle_margin with its own source_ids is its own item; a bare number is part of the cycle block.
    mkt_seen = _visible(case, _source_ids(case, mkt, 'market'))
    cyc_ids = _source_ids(case, cyc, 'cycle')
    cyc_seen = _visible(case, cyc_ids)
    cm = cyc.get('cycle_margin')
    cm_ids = _source_ids(case, cm, 'cycle.cycle_margin') if isinstance(cm, dict) else cyc_ids
    cm_seen = _visible(case, cm_ids)
    hidden = [ref for ref, seen in (('market', mkt_seen), ('cycle', cyc_seen)) if not seen]
    if isinstance(cm, dict) and not cm_seen:
        hidden.append('cycle.cycle_margin')
    for ref in hidden:
        _unavailable(ix, ref)
    if hidden:
        m['unavailable'] = hidden
    if not mkt_seen or not cm_seen:
        m.update(status='not_computed', reason='not available at as_of: ' + ', '.join(hidden))
        measures.append(m)
        return
    cm_v = _num(cm.get('value') if isinstance(cm, dict) else cm, 'cycle.cycle_margin', 0)
    price, eps = _num(mkt.get('price'), 'market.price', 0), _num(mkt.get('run_rate_eps'), 'market.run_rate_eps')
    oi = next((it for it in ix['line'].get(('operating_income', 'total'), []) if it['value'] is not None), None)
    rev = next((it for it in ix['line'].get(('revenue', 'total'), []) if it['value'] is not None), None)
    line_margin = oi['value'] / rev['value'] if oi and rev and rev['value'] > 0 else None
    stated = _num(cyc.get('current_operating_margin'), 'cycle.current_operating_margin') if cyc_seen else None
    current = stated if stated is not None else line_margin
    if not price or not eps or eps <= 0 or not cm_v or not current or current <= 0:
        m.update(status='not_computed', reason='needs a price, a positive run-rate EPS, an evidenced cycle margin and a positive '
                                               'current operating margin')
        measures.append(m)
        return
    kind = cm.get('kind') if isinstance(cm, dict) else None
    norm = eps * cm_v / current
    m.update(status='computed', price=price, run_rate_eps=eps, run_rate_basis=mkt.get('run_rate_basis'), run_rate_pe=price / eps,
             current_operating_margin=current, current_margin_from='stated' if stated is not None else 'lines',
             cycle_margin=cm_v, cycle_margin_kind=kind, cycle_margin_source_ids=cm_ids,
             attributed=kind not in (None, 'reported'), normalized_eps=norm, normalized_pe=price / norm,
             derived_from='price / run_rate_eps; normalized EPS = run_rate_eps x cycle_margin / current_operating_margin')
    if stated is not None and line_margin is not None and line_margin > 0:
        m['at_line_margin'] = {'operating_margin': line_margin, 'normalized_pe': price / (eps * cm_v / line_margin),
                               'derived_from': f'{oi["ref"]}.value / {rev["ref"]}.value; the same normalization at that margin'}
    elif stated is not None and line_margin is not None:
        m['at_line_margin'] = {'status': 'not_computed', 'operating_margin': line_margin,
                               'reason': 'the operating margin of the lines is not positive: no normalization at that margin',
                               'derived_from': f'{oi["ref"]}.value / {rev["ref"]}.value'}
    measures.append(m)


def _segment_reconciliation(ix, measures):
    segs = sorted({s for (lid, s), items in ix['line'].items() for it in items if it['ref'].startswith('segments[')})
    if not segs:
        return
    rules = ix['rules']

    def seg_value(lid, s):
        return next((it for it in ix['line'].get((lid, s), []) if it['ref'].startswith('segments[') and it['value'] is not None), None)

    def total(lid):
        return next((it for it in ix['line'].get((lid, 'total'), []) if it['value'] is not None), None)

    m = {'check': 'segment_reconciliation', 'kind': 'measure', 'segments': segs, 'identity_tolerance': rules['identity_tolerance'],
         'partial_below': rules['partial_below']}
    for lid, key in (('revenue', 'revenue'), ('operating_income', 'operating_income')):
        parts = {s: seg_value(lid, s) for s in segs}
        lacking = [s for s, it in parts.items() if it is None]
        if lacking:
            m[key] = {'status': 'missing', 'missing': lacking}
            continue
        sm = sum(it['value'] for it in parts.values())
        row = {'sum': sm, 'derived_from': ' + '.join(it['ref'] for it in parts.values())}
        tot = total(lid)
        if tot is None or tot['value'] <= 0:
            row['status'] = 'computed'
        else:
            e = sm / tot['value']
            row.update(consolidated=tot['value'], diff=e - 1,
                       status='consistent' if abs(e - 1) <= rules['identity_tolerance'] else
                       'partial' if 0 <= e < rules['partial_below'] else 'inconsistent')
            row['derived_from'] += f'; diff = sum / {tot["ref"]}.value - 1'
            if row['status'] == 'partial':
                row['residual'] = {'name': f'{_name(lid)} outside the reported segments', 'value': tot['value'] - sm, 'status': 'open',
                                   'derived_from': f'{tot["ref"]}.value - sum'}
        m[key] = row
    prof = {s: seg_value('segment_profit', s) for s in segs}
    if any(prof.values()):
        defs = sorted({it.get('profit_definition') or 'unspecified' for it in prof.values() if it})
        known = [it for it in prof.values() if it]
        m['segment_profit'] = {'sum': sum(it['value'] for it in known), 'definitions': defs, 'missing': [s for s, it in prof.items() if it is None],
                               'status': 'not_reconciled', 'derived_from': ' + '.join(it['ref'] for it in known),
                               'reason': 'a segment profit measure reconciles to the consolidated lines only through unallocated '
                                         'items (corporate costs, eliminations), which are not supplied'}
    measures.append(m)


def _core_row(rows):
    """The row that speaks for a core identity across its scopes. A row that computed something beats a missing or
    mismatched one (a segment's figure says more than a total that was never reported); among the rows that computed
    something the worst check surfaces (SURFACE_ORDER: an inconsistent or partial row at any scope is never hidden behind
    a consistent one at another); ties go to the total, then the scope name."""
    done = [r for r in rows if r['status'] in SURFACE_ORDER]
    if done:
        return min(done, key=lambda r: (SURFACE_ORDER.index(r['status']), r['scope'] != 'total', r['scope']))
    return min(rows, key=lambda r: (CORE_ORDER.index(r['status']), len(r.get('missing') or []), r['scope'] != 'total', r['scope']))


def _summary(ix, mid, identities, role):
    sp = ix['spec'][mid]
    core = []
    for i in sp['model']['identities']:
        if not i.get('core'):
            continue
        rows = [r for r in identities if r['model'] == mid and r['id'] == i['id']]
        best = _core_row(rows) if rows else None
        core.append({'id': i['id'], 'scope': best['scope'] if best else 'total', 'status': best['status'] if best else 'missing',
                     'missing': (best or {}).get('missing') or [],
                     'scopes': [{'scope': r['scope'], 'status': r['status']} for r in sorted(rows, key=lambda r: (r['scope'] != 'total', r['scope']))]})
    st = [c['status'] for c in core]
    if 'inconsistent' in st:
        status = 'inconsistent'
    elif all(s == 'consistent' for s in st):
        status = 'consistent'
    elif all(s in ('consistent', 'computed') for s in st):
        status = 'computed'
    elif any(s in ('consistent', 'computed', 'partial') for s in st):
        status = 'partial'
    else:
        status = next(s for s in ('basis_mismatch', 'scope_mismatch', 'not_computed', 'missing') if s in st)
    residuals = [{'identity': r['id'], 'scope': r['scope'], **r['residual']} for r in identities
                 if r['model'] == mid and r.get('residual')]
    return {'model': mid, 'role': role, 'label_he': sp['model']['label_he'], 'price_cyclical': sp['model']['price_cyclical'],
            'core_status': status, 'core': core, 'missing': sorted({x for c in core for x in c['missing']}), 'residuals': residuals}


def _questions(ix, identities):
    for r in identities:
        if r['status'] == 'basis_mismatch':
            for q in r.get('questions') or []:
                ix['questions'].append({'model': r['model'], 'about': r['id'], 'scope': r['scope'], 'question': q})
        elif r['status'] == 'scope_mismatch' and r['core'] and r.get('missing'):
            ix['questions'].append({'model': r['model'], 'about': r['id'], 'scope': r['scope'],
                                    'question': f'supply {", ".join(_name(x) for x in r["missing"])} for {r["scope"]} '
                                                f'(reported at {", ".join(r.get("found_at") or []) or "another scope"})'})
        elif r['status'] == 'missing' and r['core']:
            ix['questions'].append({'model': r['model'], 'about': r['id'], 'scope': r['scope'],
                                    'question': f'supply {", ".join(_name(x) for x in r.get("missing") or [])} ({r["scope"]})'})
        if r.get('residual'):
            ix['questions'].append({'model': r['model'], 'about': r['id'], 'scope': r['scope'], 'question': r['residual']['question']})
    seen, out = set(), []
    for q in ix['questions']:
        k = (q['model'] or '', q['about'], q['scope'] or '', q['question'])
        if k not in seen:
            seen.add(k)
            out.append(q)
    return sorted(out, key=lambda q: (q['model'] or '', q['about'], q['scope'] or '', q['question']))


def review(case, model=None, prior=None):
    """Read an econ case through its economic model(s): every identity at every scope its inputs are reported for
    (status, levels on the end and average bases, changes per comparison window, residual named), the growth split by
    tag, the model's checks (flags, readings, measures), the falsifiers and the open questions. `model` is a model id or
    {primary, secondary[]} (case['model'] when omitted); none or 'unclassified' is not assessed. `prior` is the review of
    an earlier period of the same company (cyclical_deceleration compares the two). Pure and deterministic: the same case
    gives the same output and input_sha256; nothing is fetched, filled in or carried over from the registry as data."""
    _check_case(case)
    mids, selection = _selection(model if model is not None else case.get('model'))
    if prior is not None:
        if not isinstance(prior, dict) or prior.get('company') != case['company'] or 'kpis' not in prior or not prior.get('input_sha256'):
            raise ValueError('prior: an earlier econ review of the same company')
        if F._d(prior['period_end']) >= F._d(case['period_end']):
            raise ValueError('prior: its period_end must precede this case')
    out = {'company': case['company'], 'period_end': case['period_end'], 'as_of': case['as_of'], 'currency': case['currency'],
           'period': case['period'], 'economics_version': ECONOMICS_VERSION, 'registry_sha256': registry_sha256(), 'model': selection,
           'prior_review': {'period_end': prior['period_end'], 'input_sha256': prior['input_sha256']} if prior is not None else None}
    empty = dict(models=[], kpis=[], lines=[], identities=[], growth_splits=[], flags=[], readings=[], measures=[], falsifiers=[],
                 open_questions=[], checks_not_run=[], unavailable=[])
    if not mids:
        out.update(status='not_assessed', reason='no economic model selected (unclassified): no rows, no effect', **empty,
                   not_read=[{'ref': k, 'reason': 'not assessed'} for k in sorted(case) if k not in CASE_META])
        return _finish(out, case)
    ix = _index(case, mids)
    identities = []
    for mid in mids:
        for ident in ix['spec'][mid]['model']['identities']:
            rows = [_evaluate(ix, mid, ident, scope) for scope in _scopes(ix, mid, ident)]
            read = any(r.get('value') is not None or r.get('changes') for r, _ in rows)
            # a non-core identity read at a narrower scope needs no row saying it was not read at the total
            identities += [r for r, meta in rows if _keep(r, meta)
                           and not (read and not r['core'] and r['status'] in ('missing', 'scope_mismatch'))]
    shown = {c['ref'] for r in identities for c in r.get('stated') or []}
    for (iid, scope), sts in sorted(ix['stated'].items()):      # a source's own identity figure is compared or named here
        product = any(i['id'] == iid and i['type'] == 'product' for m in mids for i in ix['spec'][m]['model']['identities'])
        ix['not_read'] += [{'ref': st['ref'], 'reason': 'a product identity is checked against its reported line, not a stated figure'
                            if product else f'no row of this identity at {scope} computed a value to compare it with'}
                           for st in sts if st['ref'] not in shown]
    splits = []
    for mid in mids:
        sp = ix['spec'][mid]
        gs = sp['model']['growth_split']
        kp = {f for f in gs.get('factors') or [] if f in sp['kpis']}
        for f in gs.get('factors') or []:
            if f in sp['ids']:
                kp |= _base_refs(sp, sp['ids'][f])[0]
        for scope in sorted({s for (k, s) in ix['kpi'] if k in kp} | {'total'}, key=lambda s: (s != 'total', s)):
            s = _split(ix, mid, scope)
            if scope == 'total' or s.get('factors') or s.get('problems'):   # a narrower scope when a factor was read or
                splits.append(s)                                               # one was refused for its basis
    kpis, lines = _tables(ix)
    flags, readings, measures, fals, not_run = [], [], [], [], []
    drops = _disclosures(ix)
    for mid in mids:
        sp = ix['spec'][mid]
        declared = sp['model']['checks']
        rows = [r for r in identities if r['model'] == mid]
        if 'per_unit_vs_units' in declared:
            _check_per_unit(ix, mid, rows, flags)
        _check_take_rate(ix, mid, rows, flags)
        if 'thesis_on_minor_line' in declared:
            _check_thesis(ix, mid, flags)
        if 'core_units_flat' in declared:
            _check_flat(ix, mid, flags)
        if 'cyclical_deceleration' in declared:
            readings.append(_deceleration(ix, mid, kpis, prior))
        if 'expansion_led' in declared:
            _expansion(ix, mid, splits, readings)
        if 'rate_sensitivity_share' in declared:
            _rate_sensitivity(ix, mid, measures)
        if 'margin_only_normalized' in declared:
            _margin_only(ix, mid, measures)
        for r in rows:
            if r.get('label') == 'cumulative, not active':
                readings.append({'check': 'cumulative_kpi_not_activity', 'kind': 'reading', 'model': mid, 'scope': r['scope'],
                                 'identity': r['id'], 'label': r['label'],
                                 'rule': 'a cumulative KPI (products ever opened) is never read as activity'})
        fals += _falsifiers(ix, mid, identities, drops, flags)
        not_run += [{'check': c, 'model': mid, 'reason': 'not computed by this version of the econ review'}
                    for c in declared if c not in CHECKS_RUN]
    _segment_reconciliation(ix, measures)
    out.update(status='assessed', check_window=CHECK_WINDOW,
               models=[_summary(ix, mid, identities, 'primary' if n == 0 else 'secondary') for n, mid in enumerate(mids)],
               kpis=kpis, lines=lines, identities=identities, growth_splits=splits,
               flags=sorted(flags, key=lambda f: (SEVERITIES.index(f['severity']), f['key'], f['model'] or '', f['scope'] or '')),
               readings=readings, measures=measures, falsifiers=fals, open_questions=_questions(ix, identities),
               checks_not_run=not_run, unavailable=sorted(set(ix['unavailable'])),
               not_read=sorted(ix['not_read'], key=lambda x: x['ref']) +
               [{'ref': k, 'reason': 'block not read by this version of the econ review'} for k in sorted(case)
                if k not in CASE_META and k not in ix['read']])
    return _finish(out, case)


def _finish(out, case):
    out['input_sha256'] = F.digest(case)
    out['engine_version'] = __version__
    out['boundary'] = REVIEW_BOUNDARY
    return deepcopy(out)


# ---------------------------------------------------------------- WP4: model selection

# select() reads three signals for every model of the registry (its `select` block): the card type (model_card's business
# model type is one of select.card_types), text (a select.text keyword in the industry, sector, SIC description or profile
# text, or in a segment name, matched as model_card matches labels: lower case, dashes as spaces, at a word start, so
# plurals and compounds match: 'telecom' in 'telecommunications') and KPI facts (a select.kpi_any id among the KPI facts
# available, facts.add_kpi rows or a case's sourced KPI rows).
SELECT_SIGNALS = ('card', 'text', 'kpis')
# Two of the three must agree before a model is selected; one signal never selects. One word ('memory' in a senior-living
# operator's 'memory care'), one generic KPI id ('members' of a warehouse club, 'customers' of a utility) or one coarse
# card type ('semiconductor' covers memory makers and GPU vendors; 'financials' brokers, banks and insurers) would force a
# label. A rule of evidence, not a calibrated cut: every reason shows it, and an analyst override with evidence replaces it.
MIN_SIGNALS = 2
# Economics before labels: a model with a KPI fact outranks every model without one; label-only candidates (card type and
# text) are then named as overridden and not read (the provider label 'Software - Application' does not make a company
# that reports gross bookings a software vendor). Among equals: more signals, more KPI facts, more distinct keywords, then
# the registry order; a tie on every count is named in the reasons.
SEGMENTS_OVERLAY = 'segments'
SEGMENTS_MIN = 2           # reported segments from which the segments overlay (it applies to every model) is listed
OVERRIDE_KEYS = ('primary', 'secondary', 'overlays', 'reason', 'evidence_ids')
# Evidence kinds an override may not cite: a consensus, a price or market feed and an assumption say nothing about how a
# business earns (facts.KPI_REFUSED_KINDS plus an econ case's market_data / test_assumption and a FactBook qualitative
# assumption). Every other kind is accepted and shown under override.evidence_kinds, so an analyst-post-only override
# stays visible as one.
OVERRIDE_REFUSED_KINDS = F.KPI_REFUSED_KINDS + ('market_data', 'assumption', 'test_assumption')
SELECT_OUTCOMES = ('primary', 'secondary', 'overridden', 'one_signal')
SELECTED, NOT_ASSESSED = 'selected', 'not_assessed'
SELECT_BOUNDARY = ('which economic model(s) to read the business through, chosen by visible rules from the card type, the '
                   'industry / SIC / profile text, the segment names and the KPI facts, or by an analyst override with evidence; '
                   'a reading choice, not a fact about the company, a valuation or a rating; unclassified is not assessed and '
                   'has no effect')


def profile_fields(company):
    """The text select() reads from a FactBook company record: industry, sector and the SEC SIC code with its description
    ('SIC 6211 Security Brokers, Dealers & Flotation Companies'); only the fields that are present."""
    company = company or {}
    out = {}
    for k in ('industry', 'sector'):
        v = company.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    sic = company.get('sic')
    if isinstance(sic, int) and not isinstance(sic, bool):
        out['sic'] = f"SIC {sic} {company.get('sic_description') or ''}".strip()
    return out


def _card_types(card_type, M):
    if card_type is None:
        return []
    if isinstance(card_type, dict):                     # model_card's business_model block: {primary, secondary[]}
        sec = card_type.get('secondary') or []
        card_type = [card_type.get('primary')] + (list(sec) if isinstance(sec, (list, tuple)) else [sec])
    types = [card_type] if isinstance(card_type, str) else card_type
    if not isinstance(types, (list, tuple)) or not types or not all(isinstance(t, str) for t in types):
        raise ValueError('card_type: a model_card business-model type, or a list of them (the primary first)')
    bad = [t for t in types if t not in M.TYPES and t != M.UNCLASSIFIED]
    if bad:
        raise ValueError(f'card_type: {bad} not model_card types ({", ".join(M.TYPES)})')
    return [t for t in dict.fromkeys(types) if t != M.UNCLASSIFIED]


def _profile(profile_text):
    if profile_text is None:
        return {}
    if isinstance(profile_text, str):
        profile_text = {'profile': profile_text}
    if not isinstance(profile_text, dict) or not all(isinstance(k, str) and k.strip() for k in profile_text):
        raise ValueError('profile_text: text, or {field: text} (industry, sector, sic, description: econ.profile_fields)')
    out = {}
    for k in sorted(profile_text):
        v = profile_text[k]
        if v is None:
            continue
        if not isinstance(v, str):
            raise ValueError(f'profile_text.{k}: text required')
        if v.strip():
            out[k.strip()] = v.strip()
    return out


def _segment_names(segments):
    if segments is None:
        return []
    if not isinstance(segments, (list, tuple)):
        raise ValueError('segments: a list of segment names (or segments with a name)')
    names = []
    for i, s in enumerate(segments):
        n = s.get('name') if isinstance(s, dict) else s
        if not isinstance(n, str) or not n.strip():
            raise ValueError(f'segments[{i}]: a name required')
        names.append(n.strip())
    return sorted(set(names), key=lambda n: (n.lower(), n))


def _kpi_facts(ids, reg):
    if ids is None:
        return []
    if isinstance(ids, (str, dict)) or not hasattr(ids, '__iter__'):
        raise ValueError('kpi_ids: a list of registry KPI ids (the KPI facts available)')
    ids = list(ids)
    known = {k for m in reg['models'].values() for k in m['kpis']}
    bad = sorted({repr(k) for k in ids if not isinstance(k, str) or k not in known})
    if bad:
        raise ValueError(f'kpi_ids: {", ".join(bad)} not KPI ids of the registry (brain/economics.json); facts.add_kpi refuses them too')
    return sorted(set(ids))


def _evidence_ids(evidence):
    """The ids an override may cite: a sources dict ({id: {kind, ...}}) or a list of ids; None when none were supplied."""
    if evidence is None:
        return None, {}
    if isinstance(evidence, str) or not hasattr(evidence, '__iter__') or not all(isinstance(e, str) and e for e in evidence):
        raise ValueError('evidence: the source / qualitative ids an override may cite (a sources dict or a list of ids)')
    if isinstance(evidence, dict):
        return set(evidence), {k: v.get('kind') for k, v in evidence.items() if isinstance(v, dict)}
    return set(evidence), {}


def _text_hits(keywords, fields, M):
    """Every (field, text, keyword) where the keyword starts a word of the text (model_card's label rule)."""
    out = []
    for field, text in fields:
        low = M.clean_text(text)
        for k in keywords:
            if re.search(r'(?<![a-z])' + re.escape(M.clean_text(k)), low):
                out.append({'field': field, 'text': text, 'keyword': k})
    return out


def _signal_text(c, cards):
    """The signals in the design's order: card type, then text, then KPI facts (the KPI facts rank first, _rank)."""
    s, parts = c['signals'], []
    if s['card']:
        parts.append(f"card type '{s['card']}'" + ('' if s['card'] == cards[0] else ' (a secondary type of the card)'))
    parts += [f"{t['field']} '{t['text']}' matches '{t['keyword']}'" for t in s['text']]
    if s['kpis']:
        parts.append('KPI facts ' + ', '.join(s['kpis']))
    return '; '.join(parts)


def _rank(c):
    s = c['signals']
    return (not s['kpis'], -c['count'], -len(s['kpis']), -len({t['keyword'] for t in s['text']}), c['order'])


def _overlays_for(mids, segments, reg):
    """The overlays the selected models declare (primary first), plus the segments overlay when segments are reported.
    Listed for the reading plan; each computes only when its own inputs are supplied."""
    out, why = [], []
    for mid in mids:
        for o in reg['models'][mid]['overlays']:
            if o not in out:
                out.append(o)
                why.append(f'{o} (declared by {mid})')
    seg = reg['overlays'].get(SEGMENTS_OVERLAY)
    if mids and seg and '*' in seg['applies_to'] and len(segments) >= SEGMENTS_MIN and SEGMENTS_OVERLAY not in out:
        out.append(SEGMENTS_OVERLAY)
        why.append(f'{SEGMENTS_OVERLAY} ({len(segments)} segments reported; it applies to every model)')
    return out, why


def _overlay_reason(why):
    return 'overlays: ' + '; '.join(why) + ': each computes only when its own inputs are supplied'


def _rule(cards, profile, segments, kpis, reg, M):
    fields = list(profile.items()) + [('segment', n) for n in segments]
    allc = []
    for n, (mid, m) in enumerate(reg['models'].items()):
        sel = m['select']
        card = next((t for t in cards if t in sel['card_types']), None)
        sig = {'card': card, 'text': _text_hits(sel['text'], fields, M), 'kpis': [k for k in sel['kpi_any'] if k in kpis]}
        count = sum(bool(sig[k]) for k in SELECT_SIGNALS)
        if count:
            allc.append({'model': mid, 'label_he': m['label_he'], 'signals': sig, 'count': count, 'order': n,
                         'note': sel.get('note')})
    cands = sorted((c for c in allc if c['count'] >= MIN_SIGNALS), key=_rank)
    pool = [c for c in cands if c['signals']['kpis']] or cands
    reasons = []
    for c in pool:
        c['outcome'] = 'primary' if c is pool[0] else 'secondary'
        lead = f"{c['model']}: {_signal_text(c, cards)} -> {c['outcome']} ({c['count']} of {len(SELECT_SIGNALS)} signals agree; "
        reasons.append(lead + ('KPI facts are the business\'s own economics and outrank labels)' if c['signals']['kpis'] else
                               'labels only: no KPI fact of this model is available)')
                       + (f" [registry: {c['note']}]" if c is pool[0] and c['note'] else ''))
    if len(pool) > 1 and _rank(pool[0])[:-1] == _rank(pool[1])[:-1]:
        reasons.append(f"tie: {pool[0]['model']} and {pool[1]['model']} have the same signals and counts; the registry order decides "
                       'the primary and both are read; a KPI fact or an analyst override with evidence settles it')
    for c in cands:
        if c not in pool:
            c['outcome'] = 'overridden'
            reasons.append(f"{c['model']}: {_signal_text(c, cards)} -> overridden: labels only, while {pool[0]['model']} has KPI facts "
                           f"({', '.join(pool[0]['signals']['kpis'])}); the business's KPI facts decide over a provider's label")
    for c in sorted((c for c in allc if c['count'] < MIN_SIGNALS), key=_rank):
        c['outcome'] = 'one_signal'
        reasons.append(f"{c['model']}: {_signal_text(c, cards)} -> one signal only, not selected (two of card type, text and KPI "
                       'facts must agree)')
    mids = [c['model'] for c in pool]
    overlays, why = _overlays_for(mids, segments, reg)
    if mids:
        if why:
            reasons.append(_overlay_reason(why))
    else:
        reasons.append('no model has two agreeing signals (card type, text, KPI facts): unclassified, not assessed (no rows, no '
                       'effect); a sector is never relabelled to force a model: supply the model\'s KPI facts or an analyst '
                       'override with a reason and evidence ids')
    candidates = [{k: c[k] for k in ('model', 'label_he', 'signals', 'count', 'outcome')}
                  for c in sorted(allc, key=lambda c: (SELECT_OUTCOMES.index(c['outcome']), _rank(c)))]
    rule = {'primary': mids[0] if mids else UNCLASSIFIED, 'secondary': mids[1:], 'overlays': overlays,
            'status': SELECTED if mids else NOT_ASSESSED, 'reasons': reasons}
    return rule, candidates


def _override(ov, reg, segments, known, kinds):
    if not isinstance(ov, dict):
        raise ValueError('override: {primary, reason, evidence_ids[, secondary, overlays]}')
    extra = sorted(set(ov) - set(OVERRIDE_KEYS))
    if extra:
        raise ValueError(f'override: unknown keys {extra} ({", ".join(OVERRIDE_KEYS)})')
    p = ov.get('primary')
    if not isinstance(p, str) or (p != UNCLASSIFIED and p not in reg['models']):
        raise ValueError(f'override.primary: {p!r} is not a model ({", ".join(reg["models"])}) or {UNCLASSIFIED}')
    if not isinstance(ov.get('reason'), str) or not ov['reason'].strip():
        raise ValueError('override.reason: a written reason is required')
    ev = ov.get('evidence_ids')
    if not isinstance(ev, list) or not ev or not all(isinstance(e, str) and e for e in ev):
        raise ValueError('override.evidence_ids: required (the source or qualitative ids that show the economics)')
    if known is not None:
        bad = [e for e in ev if e not in known]
        if bad:
            raise ValueError(f'override: unknown or not yet available evidence ids {bad}')
    refused = [e for e in ev if kinds.get(e) in OVERRIDE_REFUSED_KINDS]
    if refused:
        raise ValueError(f'override.evidence_ids: {refused} are {sorted({kinds[e] for e in refused})}: a consensus, a market '
                         'feed or an assumption does not show how the business earns; cite an issuer document or a sourced reading')
    sec = ov.get('secondary') or []
    if not isinstance(sec, list) or any(not isinstance(m, str) or m not in reg['models'] for m in sec):
        raise ValueError('override.secondary: a list of model ids')
    if p == UNCLASSIFIED and sec:
        raise ValueError('override.secondary: an unclassified override selects no model')
    mids = [] if p == UNCLASSIFIED else [m for m in dict.fromkeys([p] + sec)]
    ovl = ov.get('overlays')
    if ovl is None:
        overlays, why = _overlays_for(mids, segments, reg)
    else:
        if not isinstance(ovl, list) or any(not isinstance(o, str) or o not in reg['overlays'] for o in ovl):
            raise ValueError(f'override.overlays: a list of overlay ids ({", ".join(reg["overlays"])})')
        for o in ovl:
            applies = reg['overlays'][o]['applies_to']
            if '*' not in applies and not set(applies) & set(mids):
                raise ValueError(f'override.overlays: {o} applies to {applies}, none of them selected')
        overlays, why = list(dict.fromkeys(ovl)), [f'{o} (set by the analyst)' for o in dict.fromkeys(ovl)]
    out = {'primary': p, 'secondary': mids[1:], 'overlays': overlays, 'overlays_given': ovl is not None, 'reason': ov['reason'].strip(),
           'evidence_ids': list(ev), 'evidence_checked': known is not None}
    if kinds:
        out['evidence_kinds'] = sorted({kinds.get(e) or 'unknown' for e in ev})
    return out, why


def select(card_type, profile_text, kpi_ids, override=None, *, segments=None, evidence=None):
    """The economic model(s) to read a business through: {primary, secondary[], overlays[], status, source: rule|analyst,
    reasons[], rule, override, candidates[], inputs, input_sha256}.

    `card_type` is model_card's business-model type (or a list, the card's primary first); `profile_text` the industry,
    sector, SIC description or a short profile (text, or {field: text}: profile_fields(fb['company'])); `kpi_ids` the
    registry ids of the KPI facts available (facts.add_kpi rows visible at as_of; case_kpi_ids for an econ case);
    `segments` the reported segment names. Rules, in the order the reasons show them: each model's signals are the card
    type, the text and the KPI facts of its registry `select` block; two must agree (MIN_SIGNALS), a model with KPI facts
    outranks label-only ones (economics before labels: those are named as overridden), and with none the answer is
    unclassified (status not_assessed: no rows, no effect), never a forced label.

    An analyst override wins: {primary (a model id or unclassified), reason, evidence_ids[, secondary, overlays]};
    without a reason or evidence ids it is refused, and with `evidence` (the known source / qualitative ids, or a sources
    dict {id: {kind}}) an id outside it, or one of a kind that cannot show the economics (OVERRIDE_REFUSED_KINDS), is
    refused. The rule result is always shown beside it under `rule`. Pure and deterministic: the order of the KPI ids,
    the segments, the profile fields or the evidence never changes the output, and input_sha256 covers every argument."""
    from . import model_card as M                       # lazy: the card may import econ when it shows the selection
    reg = registry()
    cards, profile = _card_types(card_type, M), _profile(profile_text)
    segs, kpis = _segment_names(segments), _kpi_facts(kpi_ids, reg)
    known, kinds = _evidence_ids(evidence)
    rule, candidates = _rule(cards, profile, segs, kpis, reg, M)
    ov = None
    if override is None:
        out = {k: deepcopy(rule[k]) for k in ('primary', 'secondary', 'overlays', 'status', 'reasons')}
        out['source'] = 'rule'
    else:
        ov, why = _override(override, reg, segs, known, kinds)
        ov['agrees_with_rule'] = (ov['primary'], ov['secondary']) == (rule['primary'], rule['secondary'])
        sel = ov['primary'] + (f" with {', '.join(ov['secondary'])}" if ov['secondary'] else '')
        reasons = [f"analyst override: {sel} ({ov['reason']}; evidence {', '.join(ov['evidence_ids'])}"
                   + ('' if ov['evidence_checked'] else '; ids not checked: no known evidence ids supplied') + ')',
                   f"rule result shown beside under 'rule': {rule['primary']}"
                   + (f" with {', '.join(rule['secondary'])}" if rule['secondary'] else '')
                   + ('; the analyst agrees with it' if ov['agrees_with_rule'] else '; the analyst choice wins')]
        if why:
            reasons.append(_overlay_reason(why))
        if ov['primary'] == UNCLASSIFIED:
            reasons.append('the analyst declines every model: unclassified, not assessed (no rows, no effect)')
        out = {'primary': ov['primary'], 'secondary': ov['secondary'], 'overlays': ov['overlays'],
               'status': NOT_ASSESSED if ov['primary'] == UNCLASSIFIED else SELECTED, 'reasons': reasons, 'source': 'analyst'}
    inputs = {'card_type': cards, 'profile': profile, 'segments': segs, 'kpi_ids': kpis}
    ev = None if known is None else {e: kinds.get(e) for e in sorted(known)}
    out.update(rule=rule, override=ov, candidates=candidates, inputs=inputs, min_signals=MIN_SIGNALS,
               economics_version=ECONOMICS_VERSION, registry_sha256=registry_sha256(),
               input_sha256=F.digest({**inputs, 'override': override, 'evidence': ev}), engine_version=__version__,
               boundary=SELECT_BOUNDARY)
    return deepcopy(out)


def _fact_sources(case, ids):
    """At least one source available at as_of that may carry a KPI fact (facts.KPI_REFUSED_KINDS never does)."""
    return any(_visible(case, [i]) and case['sources'][i]['kind'] not in F.KPI_REFUSED_KINDS for i in ids)


def case_kpi_ids(case):
    """The registry KPI ids an econ case carries as facts at its as_of: KPI rows with a level or a growth rate, and the KPI
    fields of its segments, each from a source available at as_of that is not a consensus or a market feed. A stated
    identity figure (revenue per account) is not a KPI fact and is not listed."""
    _check_case(case)
    known = set(kpi_ids())
    rows, segs = case.get('kpis') or [], case.get('segments') or []
    if not isinstance(rows, list) or not isinstance(segs, list):
        raise ValueError('econ case: kpis and segments are lists')
    out = set()
    for i, row in enumerate(rows):
        label = f'kpis[{i}]'
        if not isinstance(row, dict) or row.get('id') not in known or not _fact_sources(case, _source_ids(case, row, label)):
            continue
        value = _num(row.get('value'), f'{label}.value')
        if value is not None or _cmp(row, label, value):
            out.add(row['id'])
    for i, s in enumerate(segs):
        if isinstance(s, dict) and _fact_sources(case, _source_ids(case, s, f'segments[{i}]')):
            out |= {k for k in s if k in known and s[k] is not None}
    return sorted(out)


def select_case(case, card_type=None, profile_text=None, override=None):
    """select() on an econ case: its KPI facts (case_kpi_ids) and the names of its segments available at as_of; an
    override may cite only the case's sources available at as_of."""
    ids = case_kpi_ids(case)
    segs = [s.get('name') for i, s in enumerate(case.get('segments') or [])
            if isinstance(s, dict) and _visible(case, _source_ids(case, s, f'segments[{i}]'))]
    visible = {sid: s for sid, s in case['sources'].items() if F._d(s['available_at']) <= F._d(case['as_of'])}
    return select(card_type, profile_text, ids, override, segments=segs, evidence=visible)


def _cutoff(fb, as_of, label):
    """A FactBook view at `as_of` (fb['as_of'] by default); never later than the FactBook's own cutoff."""
    if as_of is None:
        as_of = fb['as_of']
    if F._d(as_of) > F._d(fb['as_of']):
        raise ValueError(f'{label}: as_of {as_of} is after the FactBook cutoff {fb["as_of"]}')
    return as_of, (fb if as_of == fb['as_of'] else {**fb, 'as_of': as_of})


def kpi_fact_ids(fb, as_of=None):
    """The registry KPI ids the FactBook holds as facts usable at `as_of` (facts.candidates: published and about a
    period ended by then; never later than the FactBook's cutoff), from a source that may carry a KPI (not a consensus
    or a market feed): the KPI facts select() reads. Sorted; nothing is fetched or derived."""
    view = _cutoff(fb, as_of, 'kpi_fact_ids')[1]
    src = fb.get('sources') or {}
    return [k for k in kpi_ids() if any((src.get(f['source_id']) or {}).get('kind') not in F.KPI_REFUSED_KINDS
                                        for f in F.candidates(view, k))]


def select_factbook(fb, card_type, override=None, *, as_of=None, segments=None):
    """select() on a FactBook at `as_of`: the text of its company record (profile_fields: industry, sector, SIC with its
    description), its KPI facts (kpi_fact_ids) and the reported segment names given; an override may cite only the
    sources and qualitative items usable at as_of (model_card's evidence rule), each with its kind (a source's kind, a
    qualitative item's reported / guidance / opinion / assumption)."""
    from . import model_card as M
    view = _cutoff(fb, as_of, 'select_factbook')[1]
    quals = {q['id']: q.get('kind') for q in fb.get('qualitative') or [] if isinstance(q, dict) and q.get('id')}
    src = fb.get('sources') or {}
    evidence = {i: {'kind': quals[i] if i in quals else (src.get(i) or {}).get('kind')} for i in M._valid_ids(view)}
    return select(card_type, profile_fields(fb.get('company')), kpi_fact_ids(fb, as_of), override, segments=segments,
                  evidence=evidence)
