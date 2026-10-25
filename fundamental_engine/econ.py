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
result shown beside it; select_case and select_factbook read those inputs point in time). WP5: the overlays, each on
its own sourced case blocks and only when they are supplied: vendor_financing (the funding ledger as stock-to-flow shares
of annualized revenue, the contingent ceiling kept apart, statement checks on matched windows only, the level),
chain (own share via the funded chain x the upstream exposed share, the upstream review linked by sha256),
acquisition_sbc (the ongoing ratio, the tranche schedule and its mismatch), bank_capital (quarter._bank at both ends of
the target range, ROTE if all excess were returned, the price-implied ROTE, nco_basis), contract_coverage (lta_unproven)
and segments (segment_unexplained, EBITDA - capex); review() runs them and merges their flags. WP6: process and
expansion evidence, to cite and never a rating: process_evidence (category renewal, new categories, innovation
intensity against growth, the machine reading working / failing / unproven / unknown with every condition shown;
cash_conversion beside it) and expansion (customers x revenue per customer with NDR, the top-N figures compared only on
their own scope and window: top_n_inconsistent, scope_mismatch); review() reads the case's categories, history, cash
and ttm_revenue blocks for them. WP7: quarter_review takes this case's subset as case['economics'] (the review comes
back as its `economics`, the flags merge as econ.<key>, the falsifiers feed its falsifiers, and the model decides the
identity-based peak exception in quarter._cycle). A later work package adds the Hebrew text. Nothing here is called by
the pipeline, so every existing output stays byte-identical (no-input invariance).

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
# here: sensitivities, disclosure_changes, reclassification_evidence, cycle and market (memory_cycle only), funding (the
# stated annual deposit benefit, read as the line deposit_funding_benefit). The overlay blocks are read by WP5 (see the
# overlays section); categories, history, cash and ttm_revenue by WP6 (process and expansion evidence) for the models
# that declare those checks; every other block is listed under not_read.
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
# the registry checks the review computes itself; an overlay's checks (registry overlays.<id>.checks) count as run when
# the overlay ran (WP5); every other declared check of a selected model is listed under checks_not_run with the reason
# (an overlay whose inputs the case does not supply; a check quarter_review owns, QUARTER_CHECKS). The WP6 checks
# (process_evidence, cash_conversion, top_n_expansion, top_n_inconsistent) name their own reason when their inputs are
# missing.
CHECKS_RUN = ('per_unit_vs_units', 'take_rate_shift', 'thesis_on_minor_line', 'core_units_flat', 'disclosure_dropped',
              'cyclical_deceleration', 'expansion_led', 'cumulative_kpi_not_activity', 'rate_sensitivity_share',
              'margin_only_normalized', 'segment_reconciliation', 'basis_mismatch', 'scope_mismatch', 'capex_per_unit_lag',
              'process_evidence', 'cash_conversion', 'top_n_expansion', 'top_n_inconsistent')
# WP7: the checks quarter._cycle owns (the peak flag and its identity-based exception); quarter_review runs them with this
# review's model when the econ case comes in as case['economics'] (its checks_run_by_quarter), never econ.review alone
QUARTER_CHECKS = {c: 'owned by quarter_review (_cycle): run when this econ case is case.economics of a quarter_review'
                  for c in ('peak_margin_run_rate', 'operating_leverage_or_peak')}
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


def _num(x, label, minimum=None, maximum=None):
    return None if x is None else number(x, label, minimum, maximum)


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
        n = row.get('n')                       # the N of a top-N row (WP6); read by the top-N measure only
        if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
            raise ValueError(f'{label}.n: the number of customers a top-N figure covers, a positive integer')
        ix['kpi'].setdefault((kid, scope), []).append(
            {'kind': 'kpi', 'id': kid, 'scope': scope, 'basis': basis, 'unit': kpi(kid)['unit'], 'money': money,
             'currency': _currency_code(row.get('unit'), case['currency'], label) if money else None, 'currency_basis': cb,
             'measure': _measure(row, basis, case['period'], label), 'value': value, 'cmp': cmp, 'ref': ref, 'source_ids': ids,
             'window': row.get('window') if row.get('window') in COMPARISONS else 'y/y',
             'measure_stated': row.get('period') is not None or row.get('window') in MONTHS, 'n': n})
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
    _funding_line(ix)
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


# The `funding` block states the annual benefit of deposits against alternative funding (SOFI's post: 712m a year). It is
# the registry line deposit_funding_benefit measured over a year, so the digital_bank product identity deposits x deposit
# cost advantage is checked against it like any product against its line (WP5). Its deposits and spread are the KPI rows'
# figures: read from kpis[], never a second time from this block.
FUNDING_BENEFIT_KEYS = ('annual_benefit', 'post_annual_benefit')


def _funding_line(ix):
    case = ix['case']
    f = case.get('funding')
    if f is None:
        return
    if not isinstance(f, dict):
        raise ValueError('funding: an object {annual_benefit, deposits?, spread_vs_alternative?, source_ids}')
    ix['read'].add('funding')
    ids = _source_ids(case, f, 'funding')
    keys = [k for k in FUNDING_BENEFIT_KEYS if f.get(k) is not None]
    if len(keys) > 1:
        raise ValueError(f'funding: state the annual benefit once ({" or ".join(FUNDING_BENEFIT_KEYS)})')
    for k, kid in (('deposits', 'deposits'), ('spread_vs_alternative', 'deposit_cost_advantage')):
        if f.get(k) is not None:
            ix['not_read'].append({'ref': f'funding.{k}', 'reason': f'the KPI row kpis[{kid}] carries this figure; supply it there'})
    if not keys:
        return
    if ('deposit_funding_benefit', 'total') in ix['line']:
        raise ValueError('funding: the annual benefit is stated twice (lines.deposit_funding_benefit and the funding block)')
    if not _visible(case, ids):
        _unavailable(ix, 'funding')
        return
    ref = f'funding.{keys[0]}'
    ix['line'][('deposit_funding_benefit', 'total')] = [
        {'kind': 'line', 'id': 'deposit_funding_benefit', 'scope': 'total', 'basis': 'flow', 'unit': ix['lines']['deposit_funding_benefit']['unit'],
         'money': True, 'currency': case['currency'], 'currency_basis': 'reported', 'measure': 'year', 'value': number(f[keys[0]], ref),
         'cmp': {}, 'ref': ref, 'source_ids': ids, 'window': 'y/y', 'role': None, 'margin': None, 'share_of_revenue': None,
         'pass_through': None, 'prior_pass_through': None}]


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
            'measure_stated': end is not None or (per or avg).get('measure_stated', True),
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
    # an annual rate whose averaging window is not stated is a rate per year at the period: it is put on the line's
    # window (_variant), so it sets no window of its own; a stated averaging window (a TTM margin rate) must match
    measures = {r: op['measure'] for r, op in present.items() if not op['point'] and op['measure']
                and not (op['annual'] and not op.get('measure_stated', True))}
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
    for s in splits:
        if s['model'] != mid:
            continue
        rows = ix['kpi'].get(('net_revenue_retention', s['scope'])) or ix['kpi'].get(('net_revenue_retention', 'total')) or []
        ndr = next((it for it in rows if it['value'] is not None), None)
        if ndr is not None:
            ndr = {'value': ndr['value'], 'prior': (ndr['cmp'].get('y/y') or {}).get('prior'), 'scope': ndr['scope'], 'ref': ndr['ref']}
        r, qs = _expansion_led(mid, s, ndr, ix['rules'])
        if r is not None:
            readings.append(r)
            ix['questions'] += [{'model': mid, **q} for q in qs]


def _expansion_led(mid, s, ndr, rules):
    """expansion_led on one growth split of a model: (reading, questions), (None, []) when the split did not read the
    model's core per-unit factor consistently. `ndr` is {value, prior, scope, ref} or None."""
    per = next((i['id'] for i in model(mid)['identities'] if i['type'] == 'per_unit' and i.get('core')), None)
    fac = next((f for f in s.get('factors') or [] if f['id'] == per), None) if per else None
    if fac is None or fac['share'] is None or s['status'] not in ('consistent',):
        return None, []
    units = next((f for f in s['factors'] if f['id'] == identity(mid, per)['unit_kpi']), None)
    r = {'check': 'expansion_led', 'kind': 'reading', 'model': mid, 'scope': s['scope'], 'window': s['window'], 'per_unit': per,
         'per_unit_log_share': fac['share'], 'expansion_ndr_min': rules['expansion_ndr_min'],
         'rule': 'the per-unit share of log growth above one half (EXPANSION_LOG_SHARE) and NDR above expansion_ndr_min; NDR '
                 'at the split\'s scope, else the total\'s NDR read beside it (a cross-scope reading, named in ndr_scope)',
         'derived_from': f'growth_splits[{mid}@{s["scope"]}] factor {per}', 'per_unit_growth': fac['growth']}
    if units is not None:              # customers x revenue per customer: both growths shown beside the share (WP6)
        r.update(units=units['id'], units_growth=units['growth'])
    qs = []
    if ndr is None:
        r['value'] = None
        r['reason'] = 'net revenue retention not supplied'
        qs.append({'about': 'expansion_led', 'scope': s['scope'], 'question': 'supply net revenue retention'})
    else:
        r.update(value=fac['share'] > EXPANSION_LOG_SHARE and ndr['value'] > rules['expansion_ndr_min'], ndr=ndr['value'],
                 ndr_scope=ndr['scope'], ndr_ref=ndr['ref'])
        if ndr.get('prior') is not None:
            r['ndr_prior'], r['ndr_change'] = ndr['prior'], ndr['value'] - ndr['prior']
        if ndr['scope'] != s['scope']:
            # the scope rule (identities, splits) forbids mixing scopes; this reading puts two figures side by side and
            # computes nothing from them, so it is allowed but named, and the same-scope NDR is asked for
            r['scope_note'] = (f'cross-scope: NDR is reported for {ndr["scope"]}, not for {s["scope"]}; the reading puts '
                               f'the {ndr["scope"]} NDR beside the {s["scope"]} per-unit share')
            qs.append({'about': 'expansion_led', 'scope': s['scope'],
                       'question': f'supply net revenue retention for {s["scope"]} (reported for {ndr["scope"]} only)'})
    return r, qs


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


def _identity_rows(ix, mids):
    """Every identity of the selected models at every scope its inputs are reported for, as the review shows them."""
    identities = []
    for mid in mids:
        for ident in ix['spec'][mid]['model']['identities']:
            rows = [_evaluate(ix, mid, ident, scope) for scope in _scopes(ix, mid, ident)]
            read = any(r.get('value') is not None or r.get('changes') for r, _ in rows)
            # a non-core identity read at a narrower scope needs no row saying it was not read at the total
            identities += [r for r, meta in rows if _keep(r, meta)
                           and not (read and not r['core'] and r['status'] in ('missing', 'scope_mismatch'))]
    return identities


def review(case, model=None, prior=None, *, upstream=None):
    """Read an econ case through its economic model(s): every identity at every scope its inputs are reported for
    (status, levels on the end and average bases, changes per comparison window, residual named), the growth split by
    tag, the model's checks (flags, readings, measures), the overlays whose inputs the case supplies (WP5: who funds the
    demand, the supplier chain, acquisition SBC, bank capital, contract coverage, segments; `overlays`, their flags merged
    into `flags` with the overlay named), the falsifiers and the open questions. `model` is a model id or
    {primary, secondary[]} (case['model'] when omitted); none or 'unclassified' is not assessed. `prior` is the review of
    an earlier period of the same company (cyclical_deceleration compares the two); `upstream` the econ review of the
    funder up the supply chain (funded_customer_chain links it by its input_sha256). Pure and deterministic: the same
    case gives the same output and input_sha256; nothing is fetched, filled in or carried over from the registry as data."""
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
    empty = dict(models=[], kpis=[], lines=[], identities=[], growth_splits=[], flags=[], readings=[], measures=[], overlays={},
                 falsifiers=[], open_questions=[], checks_not_run=[], unavailable=[])
    if not mids:
        out.update(status='not_assessed', reason='no economic model selected (unclassified): no rows, no effect', **empty,
                   not_read=[{'ref': k, 'reason': 'not assessed'} for k in sorted(case) if k not in CASE_META])
        return _finish(out, case)
    ix = _index(case, mids)
    identities = _identity_rows(ix, mids)
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
    ran = _run_overlays(ix, mids, identities, upstream)
    owner = _overlay_checks()
    for res in ran.values():                      # the review shows each flag and question once, at the top level
        flags += res.pop('flags')
        ix['questions'] += res.pop('questions')
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
        if 'process_evidence' in declared:                       # WP6: process and expansion evidence
            readings.append(_process(ix, mid))
        if 'cash_conversion' in declared:
            m, fl, why = _cash_conversion(ix, mid)
            measures += [m] if m else []
            flags += fl
            not_run += [{'check': 'cash_conversion', 'model': mid, 'reason': why}] if why else []
        if {'top_n_expansion', 'top_n_inconsistent'} & set(declared):
            ms, fl, why = _top_n(ix, mid, [s for s in splits if s['model'] == mid and s.get('factors')])
            measures += ms
            flags += fl
            not_run += [{'check': c, 'model': mid, 'reason': r} for c, r in why if c in declared]
        for r in rows:
            if r.get('label') == 'cumulative, not active':
                readings.append({'check': 'cumulative_kpi_not_activity', 'kind': 'reading', 'model': mid, 'scope': r['scope'],
                                 'identity': r['id'], 'label': r['label'],
                                 'rule': 'a cumulative KPI (products ever opened) is never read as activity'})
        fals += _falsifiers(ix, mid, identities, drops, flags)
        not_run += [{'check': c, 'model': mid, 'reason': _not_run_reason(ix, c, owner, ran)}
                    for c in declared if c not in CHECKS_RUN and owner.get(c) not in ran]
    _segment_reconciliation(ix, measures)
    out.update(status='assessed', check_window=CHECK_WINDOW,
               models=[_summary(ix, mid, identities, 'primary' if n == 0 else 'secondary') for n, mid in enumerate(mids)],
               kpis=kpis, lines=lines, identities=identities, growth_splits=splits,
               flags=sorted(flags, key=lambda f: (SEVERITIES.index(f['severity']), f['key'], f['model'] or '', f['scope'] or '',
                                                  f.get('overlay') or '', f.get('segment') or '')),
               readings=readings, measures=measures, overlays=ran, falsifiers=fals, open_questions=_questions(ix, identities),
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


# ---------------------------------------------------------------- WP5: overlays
#
# Six overlays read blocks of the econ case beside the identities, each point in time (a block or link none of whose
# sources was available at as_of is listed under `unavailable` and not read) and each only when the case supplies its
# inputs; in review() also only when it applies to a selected model (registry overlays.<id>.applies_to; '*' = every
# model). Supplied but not applicable, its blocks are listed under not_read with the reason.
#
#   vendor_financing       funding_links [{id, kind, status, amount, amount_is_maximum?, direction?, label?, source_ids}]
#                          (an empty list = the caller looked and found none), revenue_basis {value, period, annualize?,
#                          source_ids} (else lines.revenue), exposed_share {value, horizon, kind, source_ids}, one flow
#                          window (statements {period, operating_cash_flow, net_income, non_cash_gains, receivables_build,
#                          related_party_revenue?, revenue?, source_ids} or the econ_fixture_v1 half_year block; else the
#                          case lines on the case period), balance {receivables_start, receivables_end,
#                          receivables_window_months, deferred_revenue_start + deferred_revenue_window_months or
#                          deferred_revenue_start_of_year, deferred_revenue, a spendable-cash field, marketable_equities}
#                          and receivables_windows [{label, months, receivables_start, receivables_end, revenue_growth,
#                          source_ids}] (earlier matched windows, e.g. the prior fiscal year).
#   funded_customer_chain  chain {upstream_ticker, upstream_review_sha256?, share_of_revenue_via_chain (a number, or
#                          {value, kind?, source_ids}), source_ids} and the upstream econ review given by the caller.
#   acquisition_sbc        acquisition_sbc {deal, stock_to_employees, vesting_years, schedule (per-year amounts) +
#                          schedule_year | null, observed_tranche_in_period, period, source_ids} with lines.sbc and revenue.
#   bank_capital           bank {quarter._bank's inputs: tangible_book_per_share (or tangible_common_equity and
#                          diluted_shares), rote or net_income_annual, cost_of_equity, growth, capital_ratio,
#                          target_ratio_range [low, high] or target_ratio, minimum_ratio, risk_weighted_assets,
#                          price_to_tangible_book?; source_ids}, market {price, source_ids}, credit {loan_sales_disclosed,
#                          nco_headline, nco_normalized: {value, kind, source_ids} | null, source_ids}.
#   contract_coverage      contracts {share_of_next_year_volume, price_mechanism, deposits, tenor_years, customers?,
#                          source_ids} (an object; a list of contracts is another block).
#   segments               two or more segments (SEGMENTS_MIN), read by WP3 at their own scope.
#
# Every number an overlay prints is an input, a frozen rule beside the value it judges, or a derived field with its
# derived_from. A ratio near its threshold (within rules.near_threshold) carries the threshold beside it. No default
# haircut, probability, schedule, cost of equity or growth is ever supplied.

OVERLAY_BOUNDARY = ('an overlay of the econ review: ratios and checks on caller-supplied, sourced figures; exposure is a '
                    'stock-to-flow scale ratio, never a probability or revenue at risk; not a valuation and not a trade '
                    'instruction; thresholds are uncalibrated screens (docs/prereg_econ.md)')
LINK_KINDS = ('equity_stake', 'guarantee', 'take_or_pay', 'loan', 'lease_guarantee', 'prepayment')
LINK_STATUSES = ('funded', 'committed', 'contingent')
LINK_DIRECTIONS = ('company_funds_customer', 'company_buys_from_customer', 'customer_funds_company')
# ledger groups: firm exposure = funded + committed + take-or-pay (a committed purchase from a customer is firm too);
# a contingent link is a ceiling kept apart; a customer funding the company (a prepayment) is no vendor financing at all
FIRM_GROUPS = ('funded', 'committed', 'take_or_pay')
LEDGER_GROUPS = FIRM_GROUPS + ('contingent_ceiling', 'customer_funded')
SCALE_LABEL = 'stock-to-flow scale ratio: amounts over annualized revenue, not revenue at risk'
CEILING_LABEL = 'ceiling: the maximum if called in full; no probability; never summed into firm exposure or a screen'
EXPOSED_KINDS = ('reported', 'analyst_estimate')
VF_BLOCKS = ('funding_links', 'revenue_basis', 'exposed_share', 'statements', 'half_year', 'receivables_windows')
VF_BALANCE_KEYS = ('receivables_start', 'receivables_end', 'deferred_revenue', 'deferred_revenue_start', 'deferred_revenue_start_of_year')
FLOW_BLOCKS = (('statements', None), ('half_year', 'half'))       # (block, its window when the block does not state one)
# flow fields: (name, accepted spellings: the registry's, then the econ_fixture_v1 one); two spellings at once are refused
FLOW_FIELDS = (('operating_cash_flow', ('operating_cash_flow', 'cfo')), ('net_income', ('net_income',)),
               ('non_cash_gains', ('non_cash_gains', 'non_cash_investment_gains')),
               ('receivables_build', ('receivables_build', 'receivables_build_cash_flow_statement')),
               ('related_party_revenue', ('related_party_revenue',)), ('revenue', ('revenue',)))
SPENDABLE_CASH_KEYS = ('spendable_cash', 'cash_and_bonds', 'cash_and_securities')
INVESTMENT_ASSET_KEYS = ('marketable_equities',)      # investment assets: shown beside spendable cash, never added to it
GROWTH_WINDOW_MONTHS = {12: 'y/y', 3: 'q/q'}          # a balance window matched to the revenue line's growth window
COVERAGE_ITEMS = ('share_of_next_year_volume', 'price_mechanism', 'deposits', 'tenor_years')
COVERAGE_ASK = {'share_of_next_year_volume': 'the share of next-year volume covered', 'price_mechanism': 'the price floor or formula',
                'deposits': 'the customer deposits', 'tenor_years': 'the tenor'}
PRICE_MECHANISMS = ('floor', 'formula')
# a material segment is explained by an identity of its model at its scope that reconciles (consistent, or partial with
# the residual named) or computed a value from at least one operating KPI of the model reported there (Mobility's take
# rate from its gross bookings); lines alone (R&D / revenue) explain nothing about how the segment earns
SEGMENT_RECONCILED = ('consistent', 'partial')
SBC_DILUTION = ('counted as real: the stock is a cost to current holders; the tranche is shown apart to read the ongoing '
                'ratio, never added back')


def _line_item(ix, lid, scope='total', measure=None):
    """The first reported item of a line at a scope with a level (and on a measurement window, when given)."""
    return next((it for it in ix['line'].get((lid, scope), []) if it['value'] is not None and it['currency_basis'] == 'reported'
                 and (measure is None or it['measure'] == measure)), None)


def _block(ix, key, label=None, kind=dict):
    """A case block read point in time: (block, source_ids) when visible, (block, None) when none of its sources was
    available at as_of (listed under unavailable), (None, None) when absent."""
    case = ix['case']
    b = case.get(key)
    if b is None:
        return None, None
    if not isinstance(b, kind):
        raise ValueError(f'{label or key}: {"an object" if kind is dict else "a list"}')
    ix['read'].add(key)
    ids = _source_ids(case, b, label or key)
    if not _visible(case, ids):
        _unavailable(ix, label or key)
        return b, None
    return b, ids


def _near(value, threshold, rules):
    """A value within rules.near_threshold of its threshold prints with the threshold beside it."""
    if value is None or abs(value - threshold) > rules['near_threshold'] + 1e-12:
        return None
    return {'threshold': threshold, 'near_threshold': rules['near_threshold'], 'distance': value - threshold,
            'display': f'{value:.3f} near_threshold ({threshold:.2f})', 'derived_from': 'value - threshold; within near_threshold'}


def _q(qs, about, question, scope='total'):
    qs.append({'model': None, 'about': about, 'scope': scope, 'question': question})


def _oflag(ix, key, overlay_id, scope='total', **data):
    return {**_flag(ix, key, None, scope, **data), 'overlay': overlay_id}


def _kinds(case, ids):
    return sorted({case['sources'][i]['kind'] for i in ids})


# ------------------------------------------------ vendor financing

def _links(ix, qs):
    """The funding links point in time: one row per deal id (the same deal reported several times counts once, its
    sources merged; different terms under one id are refused), each in its ledger group."""
    case = ix['case']
    raw = case.get('funding_links')
    if raw is None:
        return None, []
    if not isinstance(raw, list):
        raise ValueError('funding_links: a list of links (an empty list: none found)')
    ix['read'].add('funding_links')
    deals = {}
    for i, l in enumerate(raw):
        label = f'funding_links[{i}]'
        if not isinstance(l, dict) or not isinstance(l.get('id'), str) or not l['id'].strip():
            raise ValueError(f'{label}: needs an id (one deal keeps one id across the outlets that report it)')
        ids = _source_ids(case, l, label)
        if l.get('kind') not in LINK_KINDS:
            raise ValueError(f'{label}.kind: one of {LINK_KINDS}')
        if l.get('status') not in LINK_STATUSES:
            raise ValueError(f'{label}.status: one of {LINK_STATUSES}')
        if l.get('direction') is not None and l['direction'] not in LINK_DIRECTIONS:
            raise ValueError(f'{label}.direction: one of {LINK_DIRECTIONS}')
        mx = l.get('amount_is_maximum', False)
        if not isinstance(mx, bool):
            raise ValueError(f'{label}.amount_is_maximum: true or false')
        terms = (l['kind'], l['status'], number(l.get('amount'), f'{label}.amount', 0), l.get('direction'), mx)
        deals.setdefault(l['id'].strip(), []).append((terms, ids, l))
    links, merged = [], []
    for lid in sorted(deals):
        entries = deals[lid]
        if len({t for t, _, _ in entries}) > 1:
            raise ValueError(f'funding_links: {lid} is stated {len(entries)} times with different terms; state one figure per deal')
        (kind, status, amount, d, mx), _, first = entries[0]
        ids = sorted({s for _, ids, _ in entries for s in ids})
        if not _visible(case, ids):
            _unavailable(ix, f'funding_links[{lid}]')
            continue
        group = ('customer_funded' if d == 'customer_funds_company' else 'contingent_ceiling' if status == 'contingent'
                 else 'take_or_pay' if kind == 'take_or_pay' else status)
        row = {'id': lid, 'kind': kind, 'status': status, 'direction': d, 'amount': amount, 'amount_is_maximum': mx, 'group': group,
               'source_ids': ids}
        if isinstance(first.get('label'), str):
            row['label'] = first['label']
        links.append(row)
        if len(entries) > 1:
            merged.append({'id': lid, 'source_ids': ids, 'rule': 'the same deal reported several times counts once'})
    seen = {}
    for l in links:                     # different ids with identical terms: maybe one deal twice; never merged silently
        seen.setdefault((l['group'], l['kind'], l['direction'], l['amount']), []).append(l['id'])
    for ids in seen.values():
        if len(ids) > 1:
            _q(qs, 'vendor_financing.funding_links', f'are {" and ".join(ids)} one deal reported twice? they count separately '
                                                     'until the same id is given to both')
    return links, merged


def _annual_revenue(ix):
    rb, ids = _block(ix, 'revenue_basis')
    if rb is not None and ids is not None:
        per = rb.get('period')
        if per not in MONTHS:
            raise ValueError(f'revenue_basis.period: one of {tuple(MONTHS)}')
        k = 12 / MONTHS[per]
        if rb.get('annualize') is not None and abs(number(rb['annualize'], 'revenue_basis.annualize', 0) - k) > 1e-9:
            raise ValueError(f'revenue_basis.annualize: {rb["annualize"]} does not turn a {per} into a year (x{k:g})')
        v = number(rb.get('value'), 'revenue_basis.value', 0)
        return {'value': v * k, 'revenue': v, 'period': per, 'annualized_by': k, 'source_ids': ids,
                'derived_from': f'revenue_basis.value x {k:g} (12 months / the {per})'}
    rev = _line_item(ix, 'revenue')
    if rev is None or rev['measure'] not in MONTHS:
        return None
    k = 12 / MONTHS[rev['measure']]
    return {'value': rev['value'] * k, 'revenue': rev['value'], 'period': rev['measure'], 'annualized_by': k,
            'source_ids': rev['source_ids'], 'derived_from': f'{rev["ref"]}.value x {k:g} (12 months / the {rev["measure"]})'}


def _ledger(links, annual):
    out = {}
    for g in LEDGER_GROUPS + ('firm',):
        ls = [l for l in links if (l['group'] in FIRM_GROUPS if g == 'firm' else l['group'] == g)]
        if g == 'customer_funded' and not ls:
            continue
        amount = sum(l['amount'] for l in ls)
        row = {'amount': amount, 'links': [l['id'] for l in ls],
               'derived_from': (' + '.join(f'funding_links[{l["id"]}].amount' for l in ls) or 'no link in this group')}
        if g == 'firm':
            row['includes'] = list(FIRM_GROUPS)
        if any(l['amount_is_maximum'] for l in ls):
            row['includes_maximum'] = [l['id'] for l in ls if l['amount_is_maximum']]
        if annual is not None and annual['value'] > 0:
            row['share'] = amount / annual['value']
            row['derived_from'] += '; share = amount / annualized revenue'
        if g == 'contingent_ceiling':
            row['label'] = CEILING_LABEL
        elif g == 'customer_funded':
            row['label'] = 'customers funding the company (prepayments): financing from customers, not vendor financing'
        out[g] = row
    return out


def _flow(ix):
    """The one window the cash-flow checks read: a statements / half_year block, else the case lines on the case period."""
    case = ix['case']
    present = [(k, w) for k, w in FLOW_BLOCKS if case.get(k) is not None]
    if len(present) > 1:
        raise ValueError('statements and half_year: state one flow window')
    if present:
        key, default = present[0]
        b, ids = _block(ix, key)
        per = b.get('period') if default is None else default
        if per not in MONTHS:
            raise ValueError(f'{key}.period: one of {tuple(MONTHS)}')
        out = {'from': key, 'period': per, 'source_ids': ids, 'fields': {}}
        if default is not None and isinstance(b.get('period'), str):
            out['label'] = b['period']
        elif isinstance(b.get('period_label'), str):
            out['label'] = b['period_label']
        if ids is None:
            out['unavailable'] = True
            return out
        for name, keys in FLOW_FIELDS:
            given = [k for k in keys if b.get(k) is not None]
            if len(given) > 1:
                raise ValueError(f'{key}: {" and ".join(given)} are one figure; state it once')
            if given:
                out['fields'][name] = (number(b[given[0]], f'{key}.{given[0]}'), f'{key}.{given[0]}')
        return out
    out = {'from': 'lines', 'period': ix['period'], 'fields': {}}
    for name, lid in (('operating_cash_flow', 'operating_cash_flow'), ('net_income', 'net_income'),
                      ('related_party_revenue', 'related_party_revenue'), ('revenue', 'revenue')):
        it = _line_item(ix, lid, measure=ix['period'])
        if it is not None:
            out['fields'][name] = (it['value'], it['ref'])
    return out


def _cfo_check(flow, rules, qs):
    f = flow['fields']
    out = {'check': 'cfo_to_ni', 'kind': 'measure', 'window': flow['period'], 'cfo_to_ni_min': rules['cfo_to_ni_min']}
    cfo, ni, gains = f.get('operating_cash_flow'), f.get('net_income'), f.get('non_cash_gains')
    if cfo is None or ni is None:
        out.update(status='not_computed', reason='needs operating cash flow and net income on one window')
        _q(qs, 'vendor_financing.cfo_to_ni', 'supply operating cash flow and net income for the same window')
        return out
    out.update(operating_cash_flow=cfo[0], net_income=ni[0])
    if ni[0] <= 0:
        out.update(status='not_computed', reason=f'{ni[1]} is not positive: CFO / NI is not read')
        return out
    out['cfo_to_ni'] = {'value': cfo[0] / ni[0], 'derived_from': f'{cfo[1]} / {ni[1]}'}
    if gains is None:
        out.update(status='undetermined', reason='non-cash gains in net income not supplied (0 when there are none): CFO / '
                                                 '(NI - non-cash gains), the ratio the level reads, is not computed')
        _q(qs, 'vendor_financing.cfo_to_ni', 'supply the non-cash gains in net income (gains on equity stakes; 0 if none)')
        return out
    out['non_cash_gains'] = gains[0]
    base = ni[0] - gains[0]
    if base <= 0:
        out.update(status='not_computed', reason='net income less non-cash gains is not positive: the ratio is not read')
        return out
    v = cfo[0] / base
    out['ex_gains'] = {'value': v, 'derived_from': f'{cfo[1]} / ({ni[1]} - {gains[1]})'}
    near = _near(v, rules['cfo_to_ni_min'], rules)
    if near:
        out['ex_gains']['near'] = near
    out['status'] = 'fails' if v < rules['cfo_to_ni_min'] else 'passes'
    return out


def _revenue_growth(ix, months):
    """The revenue line's growth over a window of `months` (12: y/y, 3: q/q on a quarter), or None: never re-scaled."""
    w = GROWTH_WINDOW_MONTHS.get(months)
    if w is None or (w == 'q/q' and ix['period'] != 'quarter'):
        return None
    rev = _line_item(ix, 'revenue', measure=ix['period'])
    c = rev['cmp'].get(w) if rev else None
    if not _any_growth(c):
        return None
    g, frm = _pick(c, 'stated')
    return {'value': g, 'window': w, 'derived_from': f'{rev["ref"]} {w} ' + ('growth as stated' if frm == 'stated' else 'value / prior - 1')}


def _receivables(ix, bal, qs):
    rules, case = ix['rules'], ix['case']
    windows = []
    if bal is not None and (bal.get('receivables_start') is not None or bal.get('receivables_end') is not None):
        s = number(bal.get('receivables_start'), 'balance.receivables_start', 0)
        e = number(bal.get('receivables_end'), 'balance.receivables_end', 0)
        m = number(bal.get('receivables_window_months'), 'balance.receivables_window_months', 1)
        windows.append({'label': 'balance', 'months': m, 'receivables_start': s, 'receivables_end': e, 'ref': 'balance',
                        'revenue_growth': _revenue_growth(ix, m)})
    rw = case.get('receivables_windows')
    if rw is not None:
        if not isinstance(rw, list):
            raise ValueError('receivables_windows: a list of matched windows')
        ix['read'].add('receivables_windows')
        for i, w in enumerate(rw):
            label = f'receivables_windows[{i}]'
            if not isinstance(w, dict):
                raise ValueError(f'{label}: an object')
            ids = _source_ids(case, w, label)
            if not _visible(case, ids):
                _unavailable(ix, label)
                continue
            m = number(w.get('months'), f'{label}.months', 1)
            g = _num(w.get('revenue_growth'), f'{label}.revenue_growth')
            windows.append({'label': w.get('label') or label, 'months': m, 'ref': label,
                            'receivables_start': number(w.get('receivables_start'), f'{label}.receivables_start', 0),
                            'receivables_end': number(w.get('receivables_end'), f'{label}.receivables_end', 0),
                            'revenue_growth': None if g is None else {'value': g, 'derived_from': f'{label}.revenue_growth as stated'}})
    out = {'check': 'receivables_vs_revenue', 'kind': 'measure', 'receivables_gap_pp': rules['receivables_gap_pp'],
           'receivables_windows': rules['receivables_windows'], 'windows': []}
    failing, matched = [], []
    for w in windows:
        row = {'label': w['label'], 'months': w['months'], 'receivables_start': w['receivables_start'], 'receivables_end': w['receivables_end']}
        if w['receivables_start'] > 0:
            row['receivables_growth'] = {'value': w['receivables_end'] / w['receivables_start'] - 1,
                                         'derived_from': f'{w["ref"]}.receivables_end / receivables_start - 1'}
        if w['revenue_growth'] is None or 'receivables_growth' not in row:
            row.update(status='not_computed', reason=f'window_mismatch: no revenue growth over the same {w["months"]:g} months'
                       if w['revenue_growth'] is None else 'receivables at the start of the window are zero')
        else:
            gap = row['receivables_growth']['value'] - w['revenue_growth']['value']
            row['revenue_growth'] = w['revenue_growth']
            row['gap'] = {'value': gap, 'derived_from': 'receivables growth - revenue growth over the same window'}
            near = _near(gap, rules['receivables_gap_pp'], rules)
            if near:
                row['gap']['near'] = near
            row['status'] = 'fails' if gap > rules['receivables_gap_pp'] else 'passes'
            matched.append(w['label'])
            if row['status'] == 'fails':
                failing.append(w['label'])
        out['windows'].append(row)
    out.update(matched=matched, failing=failing)
    need = rules['receivables_windows']
    if len(failing) >= need:
        out['status'] = 'fails'
    elif len(matched) >= need:
        out['status'] = 'passes'
    else:
        out['status'] = 'not_computed'
        out['reason'] = ('window_mismatch: ' if any(r['status'] == 'not_computed' for r in out['windows']) else '') + \
            f'{len(matched)} matched window(s); the check reads {need:g}'
        if windows:
            _q(qs, 'vendor_financing.receivables_vs_revenue', 'supply receivables and revenue growth over matched windows '
                                                              f'(the same months; {need:g} windows, e.g. two fiscal years)')
    return out


def _build_share(flow, bal):
    f = flow['fields']
    out = {'check': 'receivables_build_share', 'kind': 'measure', 'window': flow['period']}
    change = None
    if bal is not None and bal.get('receivables_start') is not None and bal.get('receivables_end') is not None:
        m = number(bal.get('receivables_window_months'), 'balance.receivables_window_months', 1)
        change = {'value': number(bal['receivables_end'], 'balance.receivables_end') - number(bal['receivables_start'], 'balance.receivables_start'),
                  'months': m, 'same_window': m == MONTHS[flow['period']],
                  'derived_from': 'balance.receivables_end - balance.receivables_start'}
    build, basis = f.get('receivables_build'), 'cash-flow statement'
    if build is not None:
        build = (build[0], f'{build[1]} as stated')
    elif change is not None and change['same_window']:
        build, basis = (change['value'], change['derived_from']), 'balance change'
    ni = f.get('net_income')
    if build is None or ni is None or ni[0] <= 0:
        out.update(status='not_computed', reason='needs the receivables build and a positive net income on one window')
    else:
        out.update(status='computed', build={'value': build[0], 'basis': basis, 'derived_from': build[1]}, net_income=ni[0],
                   share={'value': build[0] / ni[0], 'derived_from': f'build / {ni[1]}'})
    if change is not None and basis != 'balance change':
        out['balance_change'] = change           # the two receivables figures are kept, each labelled (design correction 4)
    return out


def _prepayments(ix, bal, qs, flags):
    if bal is None or bal.get('deferred_revenue') is None:
        return None
    end = number(bal['deferred_revenue'], 'balance.deferred_revenue', 0)
    if bal.get('deferred_revenue_start') is not None and bal.get('deferred_revenue_start_of_year') is not None:
        raise ValueError('balance: deferred_revenue_start and deferred_revenue_start_of_year are one figure; state one')
    out = {'check': 'customer_prepayment_build', 'kind': 'flag', 'deferred_revenue': end,
           'reading': 'financing from customers: read with the funding ledger'}
    if bal.get('deferred_revenue_start') is not None:
        start, ref = number(bal['deferred_revenue_start'], 'balance.deferred_revenue_start', 0), 'balance.deferred_revenue_start'
        months = number(bal.get('deferred_revenue_window_months'), 'balance.deferred_revenue_window_months', 1)
        out['window_months'] = months
    elif bal.get('deferred_revenue_start_of_year') is not None:
        start, ref, months = number(bal['deferred_revenue_start_of_year'], 'balance.deferred_revenue_start_of_year', 0), \
            'balance.deferred_revenue_start_of_year', None
        out['window'] = 'year to date'
    else:
        out.update(status='not_computed', reason='needs deferred revenue at the start of a stated window')
        return out
    out['deferred_revenue_start'] = start
    out['build'] = {'value': end - start, 'derived_from': f'balance.deferred_revenue - {ref}'}
    if start > 0:
        out['growth'] = {'value': end / start - 1, 'derived_from': f'balance.deferred_revenue / {ref} - 1'}
    rg = _revenue_growth(ix, months) if months is not None else None
    if rg is not None and 'growth' in out:
        out['revenue_growth'] = rg
        out['comparison'] = 'faster than revenue' if out['growth']['value'] > rg['value'] else 'not faster than revenue'
    else:
        out['comparison'] = 'not_computed: window_mismatch (no revenue growth over the same window)'
    fires = out['build']['value'] > 0 and out['comparison'] != 'not faster than revenue'
    out['status'] = 'flag' if fires else 'no_flag'
    if fires:
        flags.append(_oflag(ix, 'customer_prepayment_build', 'vendor_financing', build=out['build']['value'],
                            growth=(out.get('growth') or {}).get('value'), comparison=out['comparison'],
                            derived_from=out['build']['derived_from']))
        if rg is None:
            _q(qs, 'vendor_financing.customer_prepayment_build', 'supply revenue over the same window as the deferred-revenue '
                                                                 'build (does it build faster than revenue?)')
    return out


def _related_party(flow):
    f = flow['fields']
    out = {'check': 'related_party_share', 'kind': 'measure', 'window': flow['period']}
    rp, rev = f.get('related_party_revenue'), f.get('revenue')
    if rp is None:
        out.update(status='not_supplied', reason='related-party revenue not supplied')
    elif rev is None or rev[0] <= 0:
        out.update(status='not_computed', reason='needs revenue on the same window')
    else:
        out.update(status='computed', share={'value': rp[0] / rev[0], 'derived_from': f'{rp[1]} / {rev[1]}'})
    return out


def _exposed(ix):
    es, ids = _block(ix, 'exposed_share')
    if es is None or ids is None:
        return None
    if es.get('kind') not in EXPOSED_KINDS:
        raise ValueError(f'exposed_share.kind: one of {EXPOSED_KINDS}')
    kinds = _kinds(ix['case'], ids)
    issuer = es['kind'] == 'reported' and all(k in EVIDENCE_KINDS for k in kinds)
    return {'value': number(es.get('value'), 'exposed_share.value', 0, 1), 'horizon': es.get('horizon'), 'kind': es['kind'],
            'source_ids': ids, 'source_kinds': kinds, 'attributed': not issuer, 'sets_level': False,
            'label': ('attributed ' + es['kind'].replace('_', ' ') if not issuer else 'reported by the issuer')
            + '; shown beside the ledger; never sets the level'}


def _guarantee(ledger, bal, ix, qs):
    c = ledger.get('contingent_ceiling')
    if not c or not c['links']:
        return None
    out = {'check': 'guarantee_ceiling', 'kind': 'measure', 'contingent': c['amount'], 'links': c['links'],
           'label': 'if called in full, against spendable cash; no probability; never summed into a screen'}
    keys = [k for k in SPENDABLE_CASH_KEYS if bal is not None and bal.get(k) is not None]
    if not keys:
        out.update(status='not_computed', reason='spendable cash not supplied')
        _q(qs, 'vendor_financing.guarantee_ceiling', 'supply spendable cash (cash and bonds, without investment assets or restricted cash)')
        return out
    if len(keys) > 1:
        raise ValueError(f'balance: {" and ".join(keys)}: state spendable cash once')
    cash = number(bal[keys[0]], f'balance.{keys[0]}', 0)
    out.update(status='computed', spendable_cash=cash, spendable_cash_ref=f'balance.{keys[0]}')
    if cash > 0:
        out['multiple_of_spendable_cash'] = {'value': c['amount'] / cash, 'derived_from': f'contingent ceiling / balance.{keys[0]}'}
    beside = {k: number(bal[k], f'balance.{k}', 0) for k in INVESTMENT_ASSET_KEYS if bal.get(k) is not None}
    if beside:
        out['investment_assets_beside'] = {**beside, 'note': 'investment assets, not spendable cash: shown beside, never added'}
    return out


def _funding_level(firm, statement, rules, failing, undetermined):
    """high = firm exposure at or above rules.firm_exposure_high and a statement check fails; elevated = one of the two;
    none = neither. A condition that cannot be decided is named; the value is the certain part, could_be the rest."""
    out = {'check': 'funding_level', 'kind': 'reading', 'firm_exposure_high': rules['firm_exposure_high'],
           'cfo_to_ni_min': rules['cfo_to_ni_min'], 'receivables_gap_pp': rules['receivables_gap_pp'],
           'firm': firm, 'statement': statement, 'failing': failing, 'undetermined': undetermined,
           'rule': 'high = firm exposure >= firm_exposure_high and a statement check fails (CFO / (NI - non-cash gains) < '
                   'cfo_to_ni_min, or receivables outgrow revenue by more than receivables_gap_pp in receivables_windows '
                   'matched windows); elevated = one of the two; an exposed share never sets it'}
    if firm is None and statement is None:
        out['value'] = 'not_assessed'
        return out
    certain = int(firm is True) + int(statement is True)
    open_ = int(firm is None) + int(statement is None)
    out['value'] = ('high', 'elevated', 'none')[2 - certain] if certain else 'none'
    if open_:
        out['could_be'] = ('none', 'elevated', 'high')[certain + open_]
    return out


def _vendor_financing(ix, mids, identities, upstream):
    case, rules = ix['case'], ix['rules']
    qs, flags = [], []
    out = {'overlay': 'vendor_financing', 'label_he': overlay('vendor_financing')['label_he'], 'status': 'computed', 'label': SCALE_LABEL}
    links, merged = _links(ix, qs)
    bal, bal_ids = _block(ix, 'balance')
    bal = bal if bal_ids is not None else None
    annual = _annual_revenue(ix)
    if links is None:
        out['links'] = None
        if annual is not None:
            out['annualized_revenue'] = annual
        out['ledger'] = {'status': 'not_supplied', 'reason': 'no funding_links block (an empty list states that none were found)'}
        _q(qs, 'vendor_financing.funding_links', 'list the customer-financing links (equity stakes, guarantees, take-or-pay, '
                                                 'loans) from the notes, or state that there are none (an empty list)')
        firm = None
    else:
        out['links'] = links
        if merged:
            out['merged_reports'] = merged
        out['annualized_revenue'] = annual
        out['ledger'] = _ledger(links, annual)
        share = out['ledger']['firm'].get('share')
        if share is None:
            firm = None
            _q(qs, 'vendor_financing.revenue_basis', 'supply the revenue the ledger is scaled by (revenue_basis or lines.revenue)')
        else:
            firm = share >= rules['firm_exposure_high']
            near = _near(share, rules['firm_exposure_high'], rules)
            if near:
                out['ledger']['firm']['near'] = near
    flow = _flow(ix)
    checks = {'window': {k: flow[k] for k in ('from', 'period', 'label', 'source_ids') if flow.get(k) is not None}}
    if flow.get('unavailable'):
        flow['fields'] = {}
        checks['window']['unavailable'] = True
    checks['cfo_to_ni'] = _cfo_check(flow, rules, qs)
    checks['receivables_vs_revenue'] = _receivables(ix, bal, qs)
    checks['receivables_build_share'] = _build_share(flow, bal)
    pre = _prepayments(ix, bal, qs, flags)
    if pre is not None:
        checks['customer_prepayment_build'] = pre
    checks['related_party_share'] = _related_party(flow)
    out['statement_checks'] = checks
    if links is not None:
        g = _guarantee(out['ledger'], bal, ix, qs)
        if g is not None:
            out['guarantee_ceiling'] = g
    es = _exposed(ix)
    if es is not None:
        out['exposed_share'] = es
    gate = ('cfo_to_ni', 'receivables_vs_revenue')     # the statement checks the level reads
    failing = [c for c in gate if checks[c]['status'] == 'fails']
    undetermined = [c for c in gate if checks[c]['status'] not in ('fails', 'passes')]
    statement = True if failing else (None if undetermined else False)
    out['level'] = _funding_level(firm, statement, rules, failing,
                                  (['firm_exposure'] if firm is None else []) + (undetermined if statement is None else []))
    out['flags'], out['questions'] = flags, qs
    return out


# ------------------------------------------------ supplier chain

def _chain(ix, mids, identities, upstream):
    case = ix['case']
    qs = []
    out = {'overlay': 'funded_customer_chain', 'label_he': overlay('funded_customer_chain')['label_he'],
           'boundary_note': 'the link is a citation by sha256, not evidence of a dependence; upstream numbers are never copied in as facts'}
    ch, ids = _block(ix, 'chain')
    if ch is None:
        out.update(status='not_supplied')
        out['flags'], out['questions'] = [], qs
        return out
    ticker = ch.get('upstream_ticker')
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError('chain.upstream_ticker: the funder up the chain')
    if ids is None:
        out.update(status='unavailable', reason='no source of the chain block was available at as_of')
        out['flags'], out['questions'] = [], qs
        return out
    own = ch.get('share_of_revenue_via_chain')
    own_row = None
    if own is not None:
        oids = _source_ids(case, own, 'chain.share_of_revenue_via_chain') if isinstance(own, dict) else ids
        v = number(own.get('value') if isinstance(own, dict) else own, 'chain.share_of_revenue_via_chain', 0, 1)
        if _visible(case, oids):
            own_row = {'value': v, 'source_ids': oids, 'source_kinds': _kinds(case, oids)}
            if isinstance(own, dict) and own.get('kind'):
                own_row['kind'] = own['kind']
        else:
            _unavailable(ix, 'chain.share_of_revenue_via_chain')
    stated = ch.get('upstream_review_sha256')
    up = {'ticker': ticker, 'review_sha256': stated, 'linked': False}
    up_share = None
    if upstream is not None:
        if not isinstance(upstream, dict) or not upstream.get('input_sha256') or not isinstance(upstream.get('overlays'), dict):
            raise ValueError('upstream: the econ review of the upstream company (econ.review output)')
        if upstream.get('company') != ticker:
            raise ValueError(f'upstream: a review of {upstream.get("company")!r}; the chain names {ticker!r}')
        if stated is not None and stated != upstream['input_sha256']:
            raise ValueError('upstream: not the review the case cites (chain.upstream_review_sha256 differs from its input_sha256)')
        if F._d(upstream['as_of']) > F._d(case['as_of']):
            up['reason'] = f'the upstream review is as of {upstream["as_of"]}, after this case: not linked (point in time)'
        else:
            sha = upstream['input_sha256']
            up.update(linked=True, review_sha256=sha, as_of=upstream['as_of'], period_end=upstream['period_end'],
                      linked_by='the sha256 the case cites' if stated else 'the review passed at run time (its input_sha256)')
            vf = upstream['overlays'].get('vendor_financing') or {}
            if vf.get('level'):
                up['funding_level'] = vf['level'].get('value')
            es = vf.get('exposed_share')
            if es and es.get('value') is not None:
                up_share = {'value': es['value'], 'kind': es.get('kind'), 'attributed': es.get('attributed'), 'horizon': es.get('horizon'),
                            'source_ids': es.get('source_ids'),
                            'derived_from': f'upstream review {sha[:12]}: overlays.vendor_financing.exposed_share.value (cited, not copied as a fact)'}
                up['exposed_share'] = up_share
    out['upstream'] = up
    out['own_share'] = own_row
    missing = []
    if own_row is None:
        missing.append(f'{case["company"]} revenue share via {ticker}')
        _q(qs, 'funded_customer_chain', f'what share of {case["company"]} revenue flows through customers {ticker} funds?')
    if up_share is None:
        missing.append(f'{ticker} exposed share (' + ('not in its econ review' if up['linked'] else 'its econ review') + ')')
        _q(qs, 'funded_customer_chain', f'run the {ticker} econ review with its exposed share and pass it as upstream')
    if missing:
        out.update(status='unquantified', missing=missing, exposure=None)
    else:
        out['status'] = 'computed'
        out['exposure'] = {'check': 'chain_exposure', 'value': own_row['value'] * up_share['value'],
                           'labelled_with': [f'{case["company"]} share via {ticker}: ' + ', '.join(f'{i} ({case["sources"][i]["kind"]})' for i in own_row['source_ids']),
                                             f'{ticker} exposed share: {up_share["kind"]}, review {up["review_sha256"][:12]}'],
                           'derived_from': 'own share of revenue via the chain x upstream exposed share'}
    out['flags'], out['questions'] = [], qs
    return out


# ------------------------------------------------ acquisition SBC

def _acquisition_sbc(ix, mids, identities, upstream):
    rules = ix['rules']
    qs, flags = [], []
    out = {'overlay': 'acquisition_sbc', 'label_he': overlay('acquisition_sbc')['label_he'], 'dilution': SBC_DILUTION}
    a, ids = _block(ix, 'acquisition_sbc')
    if a is None or ids is None:
        out['status'] = 'not_supplied' if a is None else 'unavailable'
        out['flags'], out['questions'] = flags, qs
        return out
    per = a.get('period') or ix['period']
    if per not in MONTHS:
        raise ValueError(f'acquisition_sbc.period: one of {tuple(MONTHS)}')
    deal = a.get('deal')
    total = _num(a.get('stock_to_employees'), 'acquisition_sbc.stock_to_employees', 0)
    years = _num(a.get('vesting_years'), 'acquisition_sbc.vesting_years', 0)
    tranche = _num(a.get('observed_tranche_in_period'), 'acquisition_sbc.observed_tranche_in_period', 0)
    out.update(status='computed', deal=deal, period=per, stock_to_employees=total, vesting_years=years, tranche_in_period=tranche,
               source_ids=ids)
    sbc, rev = _line_item(ix, 'sbc', measure=per), _line_item(ix, 'revenue', measure=per)
    if sbc is None or rev is None or tranche is None or rev['value'] <= 0:
        out['ratios'] = {'status': 'not_computed', 'reason': f'needs lines.sbc, lines.revenue and the tranche on the same {per}'}
        _q(qs, 'acquisition_sbc', f'supply total SBC and revenue for the {per} the tranche was expensed in')
    elif tranche > sbc['value']:
        out['ratios'] = {'status': 'not_computed', 'reason': 'the tranche exceeds total SBC in the period'}
    else:
        out['ratios'] = {'status': 'computed',
                         'headline': {'value': sbc['value'] / rev['value'], 'derived_from': f'{sbc["ref"]} / {rev["ref"]}'},
                         'ongoing': {'value': (sbc['value'] - tranche) / rev['value'],
                                     'derived_from': f'({sbc["ref"]} - acquisition_sbc.observed_tranche_in_period) / {rev["ref"]}'}}
    sched = a.get('schedule')
    per_year = None
    if sched is None:
        if total is not None and years:
            per_year = {'value': total / years, 'derived_from': 'acquisition_sbc.stock_to_employees / vesting_years'}
            out['schedule'] = {'basis': 'straight_line_assumed', 'per_year': per_year,
                               'label': 'an assumption: the vesting schedule is not disclosed; straight line over the term'}
            out['readings'] = [{'check': 'straight_line_assumed', 'kind': 'reading', 'per_year': per_year['value'],
                                'derived_from': per_year['derived_from'],
                                'rule': 'a schedule given only as a total and a number of years is straight-line, labelled an assumption'}]
        else:
            out['schedule'] = {'basis': 'missing', 'reason': 'neither a schedule nor a total and a term'}
            _q(qs, 'acquisition_sbc', f'supply the {deal} stock consideration to employees and its vesting term or schedule')
    else:
        if not isinstance(sched, list) or not sched:
            raise ValueError('acquisition_sbc.schedule: the evidenced amount for each vesting year, in order')
        amounts = [number(x, f'acquisition_sbc.schedule[{i}]', 0) for i, x in enumerate(sched)]
        y = a.get('schedule_year')
        out['schedule'] = {'basis': 'evidenced', 'amounts': amounts}
        if total is not None:
            out['schedule']['sum'] = {'value': sum(amounts), 'derived_from': 'sum of acquisition_sbc.schedule'}
            out['schedule']['sum_vs_total'] = {'value': sum(amounts) / total - 1 if total else None,
                                               'derived_from': 'schedule sum / stock_to_employees - 1'}
        if not isinstance(y, int) or isinstance(y, bool) or not 1 <= y <= len(amounts):
            out['schedule']['reason'] = 'schedule_year (1-based, the vesting year the period falls in) not supplied'
            _q(qs, 'acquisition_sbc', f'which vesting year of the {deal} schedule does the {per} fall in?')
        else:
            per_year = {'value': amounts[y - 1], 'derived_from': f'acquisition_sbc.schedule[{y - 1}] (vesting year {y})'}
            out['schedule'].update(year=y, per_year=per_year)
    if tranche is not None:
        k = 12 / MONTHS[per]
        obs = {'value': tranche * k, 'annualized_by': k, 'derived_from': f'acquisition_sbc.observed_tranche_in_period x {k:g}'}
        out['observed_annualized'] = obs
        if per_year is not None and per_year['value'] > 0:
            diff = obs['value'] / per_year['value'] - 1
            mismatch = abs(diff) > rules['tranche_mismatch']
            out['vs_schedule'] = {'value': diff, 'tranche_mismatch': rules['tranche_mismatch'],
                                  'status': 'mismatch' if mismatch else 'consistent', 'schedule_basis': out['schedule']['basis'],
                                  'derived_from': 'observed annualized tranche / schedule per year - 1'}
            if mismatch:
                flags.append(_oflag(ix, 'tranche_schedule_mismatch', 'acquisition_sbc', deal=deal, observed_annualized=obs['value'],
                                    schedule_per_year=per_year['value'], schedule_basis=out['schedule']['basis'], change=diff,
                                    threshold=rules['tranche_mismatch'], derived_from='acquisition_sbc.vs_schedule'))
                _q(qs, 'acquisition_sbc', f'the {deal} tranche expensed this {per} annualizes {abs(diff):.0%} '
                                          f'{"below" if diff < 0 else "above"} the {out["schedule"]["basis"].replace("_", " ")} schedule: '
                                          'what is the vesting schedule (front- or back-loaded, performance conditions)?')
    out['tranche_ends_after_years'] = years
    out['flags'], out['questions'] = flags, qs
    return out


# ------------------------------------------------ bank capital

def _bank_capital(ix, mids, identities, upstream):
    from . import quarter as Q                        # lazy: quarter_review will read econ (WP7)
    case, rules = ix['case'], ix['rules']
    qs, flags = [], []
    out = {'overlay': 'bank_capital', 'label_he': overlay('bank_capital')['label_he'], 'reuses': 'quarter._bank (never duplicated)'}
    b, ids = _block(ix, 'bank')
    cr, cr_ids = _block(ix, 'credit')
    if b is None and cr is None:
        out['status'] = 'not_supplied'
        out['flags'], out['questions'] = flags, qs
        return out
    out['status'] = 'computed'
    if b is not None and ids is not None:
        out.update(_capital(ix, Q, b, ids, qs, flags))
    elif b is not None:
        out['capital'] = {'status': 'unavailable'}
    if cr is not None:
        out['credit'] = _credit(ix, cr, cr_ids, qs, flags)
    out['flags'], out['questions'] = flags, qs
    return out


def _capital(ix, Q, b, ids, qs, flags):
    case, rules = ix['case'], ix['rules']
    num = lambda k, lo=None, hi=None: None if b.get(k) is None else number(b[k], f'bank.{k}', lo, hi)
    tbv, shares, tce_in = num('tangible_book_per_share', 1e-12), num('diluted_shares', 1e-12), num('tangible_common_equity', 1e-12)
    res = {'source_ids': ids, 'source_kinds': _kinds(case, ids)}
    if tbv is None and tce_in is not None and shares is not None:
        tbv = tce_in / shares
        res['tangible_book_per_share'] = {'value': tbv, 'derived_from': 'bank.tangible_common_equity / bank.diluted_shares'}
    if tce_in is not None:
        tce = {'value': tce_in, 'derived_from': 'bank.tangible_common_equity as stated'}
    elif tbv is not None and shares is not None:
        tce = {'value': tbv * shares, 'derived_from': 'bank.tangible_book_per_share x bank.diluted_shares'}
    else:
        tce = None
    res['tangible_common_equity'] = tce
    if tbv is None:
        res['capital'] = {'status': 'missing', 'missing': ['tangible_book_per_share']}
        _q(qs, 'bank_capital', 'supply tangible book per share (or tangible common equity and diluted shares)')
        return res
    rote, ni_in = num('rote', -1, 1), num('net_income_annual')
    ni = None
    if rote is not None:
        res['rote'] = {'value': rote, 'from': 'bank.rote as stated'}
        if tce is not None:
            ni = {'value': rote * tce['value'], 'derived_from': 'bank.rote x tangible common equity'}
    elif ni_in is not None and tce is not None:
        ni = {'value': ni_in, 'derived_from': 'bank.net_income_annual as stated'}
        rote = ni_in / tce['value']
        res['rote'] = {'value': rote, 'derived_from': 'bank.net_income_annual / tangible common equity'}
    res['net_income_annual'] = ni
    rng = b.get('target_ratio_range')
    if rng is not None:
        if not isinstance(rng, list) or len(rng) != 2:
            raise ValueError('bank.target_ratio_range: [low, high]')
        lo, hi = (number(x, 'bank.target_ratio_range', 0, 1) for x in rng)
        if lo > hi:
            raise ValueError('bank.target_ratio_range: low above high')
    elif b.get('target_ratio') is not None:
        lo = hi = number(b['target_ratio'], 'bank.target_ratio', 0, 1)
    else:
        lo = hi = None
    # the price basis of quarter._bank: a stated P/TBV is an input of the overlay (registry); the market price is read
    # beside it; without either, _bank still gives the capital fields and its price fields are not read
    mkt, mkt_ids = _block(ix, 'market')
    price = number(mkt['price'], 'market.price', 1e-12) if mkt is not None and mkt_ids is not None and mkt.get('price') is not None else None
    ptbv = num('price_to_tangible_book', 1e-12)
    basis = ('stated', ptbv * tbv) if ptbv is not None else ('market', price) if price is not None else (None, None)
    keep = ('cost_of_equity', 'growth', 'capital_ratio', 'risk_weighted_assets', 'minimum_ratio', 'fair_value_marks', 'equity')
    qb = {'source_ids': ids, 'tangible_book_per_share': tbv, **{k: b[k] for k in keep if b.get(k) is not None}}
    if rote is not None:
        qb['rote'] = rote
    quarter_flags = []
    ends = []
    for end, t in (('high', hi), ('low', lo)) if hi is not None else ((None, None),):
        got = Q._bank({**qb, **({'target_ratio': t} if t is not None else {})}, basis[1] or tbv, quarter_flags if not ends else [])
        ends.append((end, t, got))
    _, _, head = ends[0]
    if basis[0] is not None:
        res['quarter_flags'] = [{'key': k, 'severity': s} for k, s, _ in quarter_flags]
        res['quarter_flags_note'] = "quarter_review's own flags from quarter._bank: shown here, never merged into econ flags"
    # excess capital at both ends of the target range (never the midpoint) and ROTE if all of it were returned
    rows = []
    for end, t, got in ends if hi is not None else ():
        row = {'end': f'{end} target', 'target_ratio': t}
        if 'excess_capital_vs_target' in got:
            ex = got['excess_capital_vs_target']
            row['excess'] = {'value': ex, 'derived_from': 'quarter._bank: (capital_ratio - target_ratio) x risk_weighted_assets'}
            if ex <= 0:
                row['rote_if_returned'] = {'status': 'not_computed', 'reason': 'at or below this end of the target range: nothing to return'}
            elif ni is None or tce is None or tce['value'] - ex <= 0:
                row['rote_if_returned'] = {'status': 'not_computed', 'reason': 'needs ROTE or annual net income and tangible common equity above the excess'}
            else:
                row['rote_if_returned'] = {'value': ni['value'] / (tce['value'] - ex),
                                           'derived_from': 'annual net income / (tangible common equity - excess)'}
        rows.append(row)
    res['excess_capital'] = rows
    if hi is None or not all('excess' in r for r in rows):
        _q(qs, 'bank_capital', 'supply the capital ratio, the evidenced target range and risk-weighted assets')
    else:
        xs = [r['excess']['value'] for r in rows]
        res['excess_capital_range'] = {'min': min(xs), 'max': max(xs), 'midpoint': 'never used',
                                       'derived_from': 'the excess at both ends of the target range'}
        bounds = [r['rote_if_returned']['value'] for r in rows if 'value' in r['rote_if_returned']]
        if bounds:
            res['rote_if_all_excess_returned'] = {'min': min(bounds), 'max': max(bounds),
                                                  'label': 'upper bound: ignores the yield the excess earns',
                                                  'derived_from': 'annual net income / (tangible common equity - excess) at each end'}
    if 'headroom_to_minimum' in head:
        res['headroom_to_minimum'] = {'value': head['headroom_to_minimum'], 'minimum_ratio': b.get('minimum_ratio'),
                                      'derived_from': 'quarter._bank: capital_ratio - minimum_ratio'}
    # the ROTE the price implies, from quarter._bank at the price basis (COE and growth are explicit inputs, never defaults)
    if basis[0] is None:
        res['price_implied_rote'] = {'status': 'not_computed', 'reason': 'no market price and no stated P/TBV'}
    elif 'rote_implied_by_price' not in head:
        res['price_implied_rote'] = {'status': 'not_computed', 'reason': 'needs bank.rote (or net income), cost_of_equity and growth, each stated'}
        _q(qs, 'bank_capital', 'supply the cost of equity and the long-run growth as explicit inputs (no defaults)')
    else:
        pir = {'value': head['rote_implied_by_price'], 'price_to_tangible_book': ptbv if basis[0] == 'stated' else head['price_to_tangible_book'],
               'ptbv_from': 'bank.price_to_tangible_book as stated' if basis[0] == 'stated' else 'market.price / tangible book per share',
               'cost_of_equity': b.get('cost_of_equity'), 'growth': b.get('growth'),
               'assumption_kinds': _kinds(case, ids), 'derived_from': 'quarter._bank: growth + P/TBV x (cost_of_equity - growth)'}
        if basis[0] == 'stated' and price is not None:
            at = Q._bank(qb, price, [])
            pir['at_market_price'] = {'price': price, 'price_to_tangible_book': at['price_to_tangible_book'], 'value': at['rote_implied_by_price'],
                                      'stated_vs_market_ptbv': ptbv / at['price_to_tangible_book'] - 1,
                                      'derived_from': 'quarter._bank at market.price; stated / market P/TBV - 1'}
        res['price_implied_rote'] = pir
        if 'rote_if_all_excess_returned' in res:
            bound = res['rote_if_all_excess_returned']
            gaps = [pir['value'] - r['rote_if_returned']['value'] for r in rows if 'value' in r['rote_if_returned']]
            lever = {'check': 'capital_lever_insufficient', 'insufficient': bound['max'] < pir['value'],
                     'gap': {'min': min(gaps), 'max': max(gaps), 'derived_from': 'price-implied ROTE - ROTE if all excess returned, at each end'},
                     'reading': 'the part of the price-implied ROTE that must come from earnings, not from returning capital'}
            near = _near(bound['max'], pir['value'], rules)
            if near:
                lever['near'] = near
            res['capital_lever'] = lever
            if lever['insufficient']:
                flags.append(_oflag(ix, 'capital_lever_insufficient', 'bank_capital', rote_bound_max=bound['max'],
                                    price_implied_rote=pir['value'], gap_min=lever['gap']['min'], gap_max=lever['gap']['max'],
                                    derived_from='bank_capital.capital_lever'))
    return res


def _credit(ix, cr, ids, qs, flags):
    case = ix['case']
    if ids is None:
        return {'status': 'unavailable'}
    sold = cr.get('loan_sales_disclosed')
    if sold is not None and not isinstance(sold, bool):
        raise ValueError('credit.loan_sales_disclosed: true or false')
    head = _num(cr.get('nco_headline'), 'credit.nco_headline', 0)
    norm = cr.get('nco_normalized')
    out = {'loan_sales_disclosed': sold, 'nco_headline': head, 'source_ids': ids}
    if norm is not None:
        nids = _source_ids(case, norm, 'credit.nco_normalized') if isinstance(norm, dict) else ids
        v = number(norm.get('value') if isinstance(norm, dict) else norm, 'credit.nco_normalized', 0)
        if _visible(case, nids):
            kind = norm.get('kind') if isinstance(norm, dict) else None
            out.update(status='normalized', nco_used=v, nco_source_ids=nids,
                       label='normalized' + (f' ({kind.replace("_", " ")})' if kind else '') + ': loans sold put back; used and labelled')
            return out
        _unavailable(ix, 'credit.nco_normalized')
    if sold and head is not None:
        out.update(status='headline_only', nco_used=None,
                   reason='loan sales are disclosed and only the headline NCO is supplied: it excludes the losses on loans sold')
        flags.append(_oflag(ix, 'nco_basis', 'bank_capital', nco_headline=head, loan_sales_disclosed=True))
        _q(qs, 'bank_capital.nco_basis', 'supply the NCO rate with the loans sold put back (a normalized NCO, sourced)')
    elif head is not None:
        out.update(status='headline', nco_used=head, label='headline (no loan sales disclosed)')
    else:
        out.update(status='missing', nco_used=None)
    return out


# ------------------------------------------------ contract coverage

def _contract_coverage(ix, mids, identities, upstream):
    case = ix['case']
    qs, flags = [], []
    out = {'overlay': 'contract_coverage', 'label_he': overlay('contract_coverage')['label_he'],
           'boundary_note': 'a tenor and a customer count alone prove nothing about price'}
    c, ids = _block(ix, 'contracts')
    if c is None or ids is None:
        out['status'] = 'not_supplied' if c is None else 'unavailable'
        out['flags'], out['questions'] = flags, qs
        return out
    items = {k: c.get(k) for k in COVERAGE_ITEMS}
    share = _num(items['share_of_next_year_volume'], 'contracts.share_of_next_year_volume', 0, 1)
    ten = items['tenor_years']
    if ten is not None:
        ten = [number(x, 'contracts.tenor_years', 0) for x in (ten if isinstance(ten, list) else [ten])]
    missing = [k for k, ok in (('share_of_next_year_volume', share is not None and share > 0),
                               ('price_mechanism', items['price_mechanism'] in PRICE_MECHANISMS),
                               ('deposits', items['deposits'] is True), ('tenor_years', bool(ten) and all(t > 0 for t in ten)))
               if not ok]
    kinds = _kinds(case, ids)
    issuer = any(k in EVIDENCE_KINDS for k in kinds)
    out.update(items=items, missing=missing, source_ids=ids,
               evidence={'issuer_document': issuer, 'source_kinds': kinds,
                         'rule': 'cycle damping needs all four items from an issuer document (decision.py\'s rule for upgrade evidence)'})
    if c.get('customers') is not None:
        out['customers'] = c['customers']
    if missing or not issuer:
        out.update(status='lta_unproven', cycle_damping='none')
        flags.append(_oflag(ix, 'lta_unproven', 'contract_coverage', missing=missing, issuer_document=issuer))
        if missing:
            _q(qs, 'contract_coverage', 'supply ' + ', '.join(COVERAGE_ASK[m] for m in missing) + ' of the long-term agreements')
        if not issuer:
            _q(qs, 'contract_coverage', 'cite the issuer document that states the contract terms')
    else:
        out.update(status='evidenced', cycle_damping='eligible (applied by the forecast, WP13; the bear never changes)')
    out['flags'], out['questions'] = flags, qs
    return out


# ------------------------------------------------ segments

def _segments(ix, mids, identities, upstream):
    case, rules = ix['case'], ix['rules']
    qs, flags = [], []
    out = {'overlay': 'segments', 'label_he': overlay('segments')['label_he'], 'segment_material': rules['segment_material'],
           'boundary_note': 'one consolidated margin is never applied to segments with very different margins'}
    segs = [s for i, s in enumerate(case.get('segments') or []) if _visible(case, _source_ids(case, s, f'segments[{i}]'))]
    if len(segs) < SEGMENTS_MIN:
        out['status'] = 'not_supplied'
        out['flags'], out['questions'] = flags, qs
        return out
    seg_line = lambda lid, scope: next((it for it in ix['line'].get((lid, scope), []) if it['ref'].startswith('segments[')
                                        and it['value'] is not None), None)
    scopes = [_scope(s['name'], 'segments') for s in segs]
    revs = {sc: seg_line('revenue', sc) for sc in scopes}
    rev_sum = sum(r['value'] for r in revs.values() if r) if all(revs.values()) else None
    total = _line_item(ix, 'revenue', measure=ix['period'])
    measure = next((lid for lid in ('operating_income', 'segment_profit') if all(seg_line(lid, sc) for sc in scopes)), None)
    prof_sum = sum(seg_line(measure, sc)['value'] for sc in scopes) if measure else None
    out.update(status='computed', profit_measure=measure)
    rows = []
    for s, sc in zip(segs, scopes):
        named = s.get('model')
        if named is not None and named not in registry()['models']:
            raise ValueError(f'segments[{s["name"]}].model: {named!r} is not a model of the registry')
        read_under = [named] if named in mids else ([] if named else list(mids))
        row = {'name': s['name'], 'scope': sc, 'model': named, 'model_from': 'named by the segment' if named else 'the selected models',
               'read_under': read_under}
        if named and named not in mids:
            row['note'] = f'{named} is not among the selected models: the segment is not read under it'
        r = revs[sc]
        if r is not None:
            row['revenue'] = r['value']
            if rev_sum:
                row['share_of_revenue'] = {'value': r['value'] / rev_sum, 'of': 'the sum of the segments',
                                           'derived_from': f'{r["ref"]} / sum of segment revenue'}
            if total and total['value'] > 0:
                row['share_of_reported_revenue'] = {'value': r['value'] / total['value'], 'derived_from': f'{r["ref"]} / {total["ref"]}'}
        p = seg_line(measure, sc) if measure else None
        if p is not None:
            row['profit'] = {'value': p['value'], 'measure': measure, 'derived_from': p.get('derived_from') or f'{p["ref"]}.value'}
            if prof_sum and prof_sum > 0:
                row['share_of_profit'] = {'value': p['value'] / prof_sum,
                                          'derived_from': f'{p["ref"]} / sum of segment {_name(measure).replace("segment ", "")}'}
        if s.get('research_and_development') is not None and s.get('revenue'):
            row['rnd_to_revenue'] = {'value': number(s['research_and_development'], 'segment R&D') / number(s['revenue'], 'segment revenue'),
                                     'derived_from': f'segments[{s["name"]}].research_and_development / revenue'}
        ebitda_key = next((k for k in ('ebitda', 'adjusted_ebitda') if s.get(k) is not None), None)
        if ebitda_key and s.get('capex') is not None:
            row['ebitda_minus_capex'] = {'check': 'ebitda_minus_capex', 'value': number(s[ebitda_key], ebitda_key) - number(s['capex'], 'capex'),
                                         'ebitda_field': ebitda_key, 'basis': 'the issuer definitions of both',
                                         'derived_from': f'segments[{s["name"]}].{ebitda_key} - capex'}
        mine = [x for x in identities if x['scope'] == sc and x['model'] in read_under]
        explaining = [x for x in mine if x['status'] in SEGMENT_RECONCILED or (
            x['status'] == 'computed' and (x.get('value') is not None or x.get('changes'))
            and _base_refs(ix['spec'][x['model']], ix['spec'][x['model']]['ids'][x['id']])[0])]
        row['identities'] = [{'model': x['model'], 'id': x['id'], 'status': x['status'], 'core': x['core']} for x in mine]
        row['explained_by'] = [f'{x["model"]}.{x["id"]} ({x["status"]})' for x in explaining]
        row['explained'] = bool(explaining) if read_under else None
        shares = [row[k]['value'] for k in ('share_of_revenue', 'share_of_profit') if k in row]
        row['material'] = any(v >= rules['segment_material'] for v in shares)
        not_disclosed = [k for k, v in s.items() if v is None]
        if not_disclosed:
            row['not_disclosed'] = not_disclosed
        ident_missing = {x['id']: x['missing'] for x in mine if x['core'] and x.get('missing')}
        if ident_missing:
            row['identity_missing'] = ident_missing
        unread = [m for m in read_under if not any(x['model'] == m for x in mine)]   # no row of the model here at all
        if unread:
            row['core_kpis_not_reported_here'] = {m: required_kpis(m) for m in unread}
        if row['material'] and row['explained'] is False:
            missing = list(dict.fromkeys(not_disclosed + [m for ms in ident_missing.values() for m in ms]
                                         + [k for m in unread for k in required_kpis(m)]))
            row['missing'] = missing
            flags.append(_oflag(ix, 'segment_unexplained', 'segments', sc, segment=s['name'], model=named,
                                share_of_revenue=(row.get('share_of_revenue') or {}).get('value'),
                                share_of_profit=(row.get('share_of_profit') or {}).get('value'), threshold=rules['segment_material'],
                                missing=missing, derived_from=f'overlays.segments.segments[{s["name"]}]'))
            _q(qs, 'segments.segment_unexplained', f'how does {s["name"]} earn? supply ' + (', '.join(_name(m) for m in missing) or
                                                   'the operating KPIs of its model') + f' ({s["name"]})', sc)
        rows.append(row)
    out['segments'] = rows
    out['flags'], out['questions'] = flags, qs
    return out


# ------------------------------------------------ running the overlays

OVERLAY_RUNNERS = {'vendor_financing': _vendor_financing, 'funded_customer_chain': _chain, 'acquisition_sbc': _acquisition_sbc,
                   'segments': _segments, 'contract_coverage': _contract_coverage, 'bank_capital': _bank_capital}


def _supplied(case, oid):
    """The case blocks that supply an overlay's inputs ([] when it supplies none)."""
    if oid == 'vendor_financing':
        bal = case.get('balance')
        return [k for k in VF_BLOCKS if case.get(k) is not None] + (
            ['balance'] if isinstance(bal, dict) and any(bal.get(k) is not None for k in VF_BALANCE_KEYS) else [])
    if oid == 'funded_customer_chain':
        return ['chain'] if case.get('chain') is not None else []
    if oid == 'acquisition_sbc':
        return ['acquisition_sbc'] if case.get('acquisition_sbc') is not None else []
    if oid == 'bank_capital':
        return [k for k in ('bank', 'credit') if case.get(k) is not None]
    if oid == 'contract_coverage':
        return ['contracts'] if isinstance(case.get('contracts'), dict) else []
    segs = case.get('segments') or []
    return ['segments'] if isinstance(segs, list) and len(segs) >= SEGMENTS_MIN else []


def _run_overlays(ix, mids, identities, upstream):
    """{overlay id: result} in registry order for every overlay whose inputs the case supplies and that applies to a
    selected model; supplied but not applicable, its blocks go to not_read with the reason."""
    reg = registry()
    case, out = ix['case'], {}
    for oid in reg['overlays']:
        blocks = _supplied(case, oid)
        if not blocks:
            continue
        applies = reg['overlays'][oid]['applies_to']
        if '*' not in applies and not set(applies) & set(mids):
            for blk in blocks:
                if blk not in ix['read']:
                    ix['not_read'].append({'ref': blk, 'reason': f'input of the {oid} overlay, which applies to {", ".join(applies)}; '
                                                                 'none of them is selected'})
                    ix['read'].add(blk)
            continue
        out[oid] = OVERLAY_RUNNERS[oid](ix, mids, identities, upstream)
    if upstream is not None and 'funded_customer_chain' not in out:
        raise ValueError('upstream: given, but the case supplies no chain block for a model the chain overlay applies to')
    return out


def _overlay_checks():
    return {c: oid for oid, o in registry()['overlays'].items() for c in o['checks']}


def _not_run_reason(ix, check, owner, ran):
    oid = owner.get(check)
    if check in QUARTER_CHECKS:
        return QUARTER_CHECKS[check]
    if oid is None:
        return 'not computed by this version of the econ review'
    if _supplied(ix['case'], oid):
        return f'overlay {oid}: supplied, but it applies to none of the selected models'
    return f'overlay {oid}: its inputs are not supplied in the case'


def _overlay_call(case, oid, model=None, upstream=None):
    """One overlay on an econ case, as the review computes it (it runs even when no selected model declares it: the
    caller asked for it). `model` selects the models segments are read under (case['model'] when omitted)."""
    _check_case(case)
    mids, selection = _selection(model if model is not None else case.get('model'))
    ix = _index(case, mids)
    res = OVERLAY_RUNNERS[oid](ix, mids, _identity_rows(ix, mids) if oid == 'segments' else [], upstream)
    res['flags'] = sorted(res['flags'], key=lambda f: (SEVERITIES.index(f['severity']), f['key'], f['scope'] or '', f.get('segment') or ''))
    res['questions'] = sorted(res['questions'], key=lambda q: (q['about'], q['scope'] or '', q['question']))
    res.update(model=selection, unavailable=sorted(set(ix['unavailable'])), as_of=case['as_of'], company=case['company'],
               economics_version=ECONOMICS_VERSION, registry_sha256=registry_sha256())
    return _finish_overlay(res, case)


def _finish_overlay(res, case):
    res['input_sha256'] = F.digest(case)
    res['engine_version'] = __version__
    res['boundary'] = OVERLAY_BOUNDARY
    return deepcopy(res)


def vendor_financing(case):
    """Who funds the demand: the funding ledger (funded, committed, take-or-pay and the contingent ceiling as stock-to-flow
    shares of annualized revenue, never revenue at risk), the statement checks (CFO / NI and CFO / (NI - non-cash gains),
    receivables against revenue on matched windows only, the receivables build, the customer-prepayment build, the
    related-party share), the guarantee ceiling against spendable cash, the attributed exposed share and the level
    (high / elevated / none; an exposed share never sets it)."""
    return _overlay_call(case, 'vendor_financing')


def chain(case, upstream=None):
    """The supplier-chain exposure: own revenue share via the funded chain x the upstream exposed share, computed only
    when both exist (otherwise unquantified, the missing numbers named). `upstream` is the funder's econ review, linked by
    its input_sha256; its numbers are cited, never copied in as facts."""
    return _overlay_call(case, 'funded_customer_chain', upstream=upstream)


def acquisition_sbc(case):
    """Acquisition SBC: the ongoing ratio (SBC - the period's tranche) / revenue beside the headline ratio, the tranche
    schedule (evidenced, or straight_line_assumed from a total and a term), the observed tranche annualized against it
    (tranche_schedule_mismatch beyond rules.tranche_mismatch, an open question), the dilution counted as real."""
    return _overlay_call(case, 'acquisition_sbc')


def bank_capital(case):
    """Bank capital through quarter._bank: excess capital at both ends of the evidenced target range (never the midpoint),
    ROTE if all of it were returned (an upper bound), the ROTE the price implies (stated P/TBV, else market price; COE and
    growth explicit), capital_lever_insufficient, headroom to the minimum, and nco_basis when loans are sold."""
    return _overlay_call(case, 'bank_capital')


def contract_coverage(case):
    """Long-term agreements: lta_unproven unless the share of next-year volume, a price floor or formula, deposits and a
    tenor are all evidenced by an issuer document; no cycle damping otherwise."""
    return _overlay_call(case, 'contract_coverage')


def segments(case, model=None):
    """Segment economics: each segment's share of revenue and profit, its identities under its own model (named by the
    segment) or the selected models, EBITDA - capex and R&D / revenue where given, and segment_unexplained for a segment
    with at least rules.segment_material of revenue or profit that no identity of its model explains."""
    return _overlay_call(case, 'segments', model=model)


# ---------------------------------------------------------------- WP6: process and expansion evidence

# Moat and expansion become evidence Claude can cite (design goal 4); neither ever becomes a rating. Nothing here sets
# card.dimensions.moat or feeds forecast._moat_proxy: neither model_card nor forecast imports econ, and nothing in the
# engine calls these functions (only facts.add_kpi reads the registry).
#
# process_evidence (consumer_innovator) reads the moat as a repeatable process from what the caller states with sources:
#   categories    [{name, kind?, revenue {year: value}, growth? {year: g}, source_ids}]: revenue by product category
#   history       {revenue | research_and_development | advertising: {year: value, source_ids}}: the `intensity` argument
#   gross_margin  history.gross_margin {start, end, years?, source_ids} or {year: margin, source_ids}
# Years are the source's labels ('2021', 'FY2021') read in calendar order. A year without a figure is listed
# (missing_years) and never interpolated; a trend whose window start or end has no figure is `unknown`. A renewal is read
# on the years stated, so a gap is named in a caveat (it could hold a higher peak or a lower trough). Rules (frozen in
# docs/prereg_econ.md section 1):
#   renewed  a fall of at least rules.renewal_drawdown from the running peak, then a new high above that peak at most
#            rules.renewal_window_years after the peak year (inclusive: 2021 -> 2025 counts)
#   machine  working  = at least one renewed category, every intensity with a known trend flat (within
#                       +-rules.intensity_flat_pp) or falling, R&D's trend known, and revenue CAGR >= rules.machine_growth_min
#            failing  = an intensity rising by at least rules.intensity_rising_pp while revenue growth slows (the latest
#                       window's annual growth below the one before; two stated windows of equal length)
#            unproven = otherwise; unknown = no condition could be read (no inputs). Each condition shows True/False/None.
# Innovation intensity is R&D / revenue (the registry's rd_intensity); advertising / revenue is read beside it: a known
# advertising trend counts in both rules, an unknown one is named and asked for but does not block the reading.
#
# cash_conversion: FCF / net income below rules.cfo_to_ni_min. The check has no dedicated frozen rule; it reads the
# threshold of the funding screen's CFO / NI statement check (recorded for docs/prereg_econ.md's deviations log, WP8).
# From `cash` {fcf_to_net_income | free_cash_flow and net_income, fcf_basis, period?, source_ids}, else the lines
# (operating cash flow - capex) / net income on the case period. The FCF basis is always labelled; an unstated one is
# named and asked for.
#
# Expansion (data_platform_software): customers x revenue per customer is the growth split (expansion_led adds NDR and
# both growths); the top-N measure (top_n_expansion) reads the top_n_average_revenue / top_n_share / top_n_total_revenue
# rows with their N and window: the average's growth (the set is re-ranked at each date, so it is not same-customer
# growth), the total (stated, or N x the average), the revenue the share implies (total / share) and the share trend
# (unknown without a prior share). It is compared only with revenue on the same scope and measurement window
# (`ttm_revenue` {value, growth?, scope?, source_ids}, else the revenue line when it is on that window):
# top_n_inconsistent beyond rules.top_n_tolerance. A revenue line or a growth split on another scope or window is listed
# under not_compared as scope_mismatch and nothing is computed from it (PLTR: top-20 TTM over all customers against the
# US commercial q/q split, design correction 5).
INTENSITY_SERIES = ('research_and_development', 'advertising')
HISTORY_SERIES = ('revenue',) + INTENSITY_SERIES
MACHINE_STATES = ('working', 'failing', 'unproven', 'unknown')
PROCESS_RULES = ('renewal_drawdown', 'renewal_window_years', 'intensity_flat_pp', 'intensity_rising_pp', 'machine_growth_min')
SERIES_META = ('source_ids', 'note')
GROSS_MARGIN_META = SERIES_META + ('start', 'end', 'years', 'start_year', 'end_year')
CATEGORY_KEYS = ('name', 'kind', 'revenue', 'growth', 'missing_years', 'source_ids', 'note')
CASH_KEYS = ('fcf_to_net_income', 'free_cash_flow', 'net_income', 'fcf_basis', 'period', 'cash_to_debt', 'source_ids', 'note')
TOP_N_KPIS = ('top_n_average_revenue', 'top_n_share', 'top_n_total_revenue')
TOP_N_FIELDS = ('n', 'scope', 'window', 'comparison', 'average', 'prior_average', 'average_growth', 'total', 'prior_total',
                'share', 'prior_share', 'revenue', 'refs', 'source_ids', 'model')
TTM = 'TTM'
_YEAR = re.compile(r'^(FY)?(\d{4})$')
TOP_N_NOTE = ('the top-N set is re-ranked at each date: the growth of its average is not same-customer expansion, and it is '
              'compared only with revenue on the same scope and window')
PROCESS_BOUNDARY = ('evidence for Claude to cite: arithmetic on stated category and intensity figures under frozen screens; '
                    'never a moat rating; it never sets card.dimensions.moat and never feeds forecast._moat_proxy')
EXPANSION_BOUNDARY = ('evidence for Claude to cite: customers x revenue per customer, NDR and the top-N figures, each on its own '
                      'scope and window; never compared across scopes or windows; never a moat or quality rating')
_EPS = 1e-12


def _pp(x):
    """A change in percentage points as text ('+11pp', '-0.06pp'): printed as a change, never as 'stable'."""
    s = f'{round(100 * x, 2):+.2f}'.rstrip('0').rstrip('.')
    return s + 'pp'


def _fy(label, where):
    m = _YEAR.match(str(label))
    if not m:
        raise ValueError(f'{where}: {label!r} is not a fiscal-year label (2021 or FY2021)')
    return int(m.group(2))


def _series(spec, where, minimum=None, meta=SERIES_META):
    """A {year: value} series as ([(year, label, value)] in calendar order, [labels stated without a figure])."""
    if not isinstance(spec, dict):
        raise ValueError(f'{where}: an object {{year: value}}')
    pts, empty, seen = [], [], {}
    for k in spec:
        if k in meta:
            continue
        y = _fy(k, where)
        if y in seen:
            raise ValueError(f'{where}: {k!r} and {seen[y]!r} are the same year')
        seen[y] = k
        if spec[k] is None:
            empty.append((y, str(k)))
        else:
            pts.append((y, str(k), number(spec[k], f'{where}.{k}', minimum)))
    return sorted(pts), [lab for _, lab in sorted(empty)]


def _gaps(pts, empty=()):
    """Years between the first and the last figure that have none (never filled), plus years stated without a figure."""
    if not pts:
        return sorted(empty)
    have = {y for y, _, _ in pts}
    fy = 'FY' if pts[0][1].startswith('FY') else ''
    gaps = {f'{fy}{y}' for y in range(pts[0][0] + 1, pts[-1][0]) if y not in have}
    return sorted(gaps | set(empty), key=lambda lab: _fy(lab, 'year'))


def _episode(peak, trough, high, last, rules, where):
    win = rules['renewal_window_years']
    e = {'peak_year': peak[1], 'peak': peak[2], 'trough_year': trough[1], 'trough': trough[2], 'drawdown': trough[2] / peak[2] - 1}
    if high is None:
        still = last[0] - peak[0] < win
        e.update(high_year=None, renewed=None if still else False,
                 reason=f'no new high by {last[1]}; the window runs to {peak[0] + win}' if still
                 else f'no new high within {win} years of the {peak[1]} peak',
                 derived_from=f'{where}: trough / peak - 1')
        return e
    years = high[0] - peak[0]
    e.update(high_year=high[1], high=high[2], recovery=high[2] / trough[2] - 1 if trough[2] > 0 else None,
             new_high_over_prior_peak=high[2] / peak[2] - 1, years_to_new_high=years, renewed=years <= win,
             derived_from=f'{where}: trough / peak - 1; high / trough - 1; high / peak - 1; high year - peak year')
    if years > win:
        e['reason'] = f'the new high came {years} years after the peak (window {win} years)'
    return e


def _renewals(pts, rules, where):
    """Every fall of at least rules.renewal_drawdown from the running peak, with the new high that ended it (or none)."""
    dd = rules['renewal_drawdown']

    def fell(t, p):
        return t is not None and p[2] > 0 and t[2] / p[2] - 1 <= -dd + _EPS
    out, peak, trough = [], pts[0], None
    for p in pts[1:]:
        if p[2] > peak[2]:
            if fell(trough, peak):
                out.append(_episode(peak, trough, p, None, rules, where))
            peak, trough = p, None
        elif trough is None or p[2] < trough[2]:
            trough = p
    if fell(trough, peak):
        out.append(_episode(peak, trough, None, pts[-1], rules, where))
    return out


def _category(c, rules, names):
    if not isinstance(c, dict) or not isinstance(c.get('name'), str) or not c['name'].strip():
        raise ValueError('categories[]: each category needs a name')
    name = c['name'].strip()
    if name.lower() in names:
        raise ValueError(f'categories: a second category named {name!r}')
    names.add(name.lower())
    where = f'categories[{name}]'
    unknown = sorted(set(c) - set(CATEGORY_KEYS))
    if unknown:
        raise ValueError(f'{where}: unknown fields {unknown}; a category states {CATEGORY_KEYS}')
    if c.get('kind') is not None and not isinstance(c['kind'], str):
        raise ValueError(f'{where}.kind: the source\'s label (mature, new), as text')
    out = {'name': name, 'kind': c.get('kind')}
    if c.get('revenue') is None:
        out.update(renewed=None, reason='no revenue by year: growth rates alone cannot show a fall from a peak')
    else:
        pts, empty = _series(c['revenue'], f'{where}.revenue', 0)
        out.update(years=[lab for _, lab, _ in pts], missing_years=_gaps(pts, empty))
        if len(pts) < 2:
            out.update(renewed=None, reason='needs revenue in two or more years')
        else:
            eps = _renewals(pts, rules, f'{where}.revenue')
            out['episodes'] = eps
            if any(e['renewed'] for e in eps):
                out['renewed'] = True
            elif any(e['renewed'] is None for e in eps):
                out.update(renewed=None, reason='a fall without a new high yet; the window is still open')
            else:
                out.update(renewed=False, reason='no new high within the window after a fall' if eps else
                           f'no fall of at least {rules["renewal_drawdown"]:.0%} from a peak on the stated years')
            gaps = [y for y in out['missing_years'] if pts[0][0] < _fy(y, where) < pts[-1][0]]
            if gaps:
                out['caveat'] = (f'read on the stated years only: {", ".join(gaps)} {"has" if len(gaps) == 1 else "have"} no '
                                 f'figure and could hold a higher peak or a lower trough')
    if c.get('growth') is not None:
        pts, _ = _series(c['growth'], f'{where}.growth', -1)
        out['growth'] = {lab: g for _, lab, g in pts}
    if isinstance(c.get('source_ids'), list):
        out['source_ids'] = list(c['source_ids'])
    return out


def _slowdown(pts):
    """Does revenue growth slow? The latest window's annual growth against the window before, both stated, equal length."""
    if len(pts) < 3:
        return {'value': None, 'reason': 'needs revenue in three or more years (two windows)'}
    (y0, l0, v0), (y1, l1, v1), (y2, l2, v2) = pts[-3:]
    if y1 - y0 != y2 - y1:
        return {'value': None, 'reason': f'the last two windows differ in length ({l0}-{l1}, {l1}-{l2}); a year is never filled in'}
    if v0 <= 0 or v1 <= 0:
        return {'value': None, 'reason': 'a revenue level is not positive'}
    g0, g1 = (v1 / v0) ** (1 / (y1 - y0)) - 1, (v2 / v1) ** (1 / (y2 - y1)) - 1
    return {'value': g1 < g0, 'windows': [[l0, l1], [l1, l2]], 'earlier_growth': g0, 'latest_growth': g1,
            'derived_from': f'history.revenue: annual growth {l0}-{l1} and {l1}-{l2}'}


def _intensity(intensity, rules):
    unknown = sorted(set(intensity) - set(HISTORY_SERIES))
    if unknown:
        raise ValueError(f'intensity: unknown series {unknown}; process evidence reads {HISTORY_SERIES}')
    flat, rising = rules['intensity_flat_pp'], rules['intensity_rising_pp']
    rev, rev_empty = _series(intensity['revenue'], 'history.revenue', 0) if intensity.get('revenue') is not None else ([], [])
    out = {'window': None, 'revenue': None, 'series': [], 'incremental_revenue_per_incremental_rd': None}
    if len(rev) >= 2 and rev[0][2] > 0:
        a, b = rev[0], rev[-1]
        span = b[0] - a[0]
        out['window'] = [a[1], b[1]]
        out['revenue'] = {'start_year': a[1], 'start': a[2], 'end_year': b[1], 'end': b[2], 'span_years': span,
                          'cagr': (b[2] / a[2]) ** (1 / span) - 1, 'missing_years': _gaps(rev, rev_empty),
                          'growth_trend': _slowdown(rev), 'derived_from': f'history.revenue: (end / start) ^ (1 / span_years) - 1'}
    elif rev:
        out['revenue'] = {'status': 'not_computed', 'reason': 'needs positive revenue in two or more years'}
    by_year = {y: v for y, _, v in rev}
    for sid in INTENSITY_SERIES:
        if intensity.get(sid) is None:
            continue
        pts, empty = _series(intensity[sid], f'history.{sid}', 0)
        row = {'id': sid, 'stated': [{'year': lab, 'value': v, 'revenue': by_year[y], 'intensity': v / by_year[y],
                                      'derived_from': f'history.{sid}[{lab}] / history.revenue[{lab}]'}
                                     for y, lab, v in pts if by_year.get(y)],
               'missing_years': _gaps(pts, empty)}
        no_rev = [lab for y, lab, _ in pts if not by_year.get(y)]
        if no_rev:
            row['no_revenue_for'] = no_rev
        if out['window'] is None:
            row.update(trend='unknown', reason='no revenue window (two or more years of revenue)')
        else:
            at = {s['year']: s for s in row['stated']}
            missing = [lab for lab in out['window'] if lab not in at]
            if missing:
                row.update(trend='unknown', missing=missing, reason=f'no figure for {", ".join(missing)}: never interpolated')
            else:
                s0, s1 = at[out['window'][0]], at[out['window'][1]]
                ch = s1['intensity'] - s0['intensity']
                row.update(start=s0['intensity'], end=s1['intensity'], change=ch, display=_pp(ch),
                           trend='flat' if abs(ch) <= flat + _EPS else 'falling' if ch < 0 else 'rising',
                           rising_by_rule=ch >= rising - _EPS,
                           cagr=(s1['value'] / s0['value']) ** (1 / out['revenue']['span_years']) - 1 if s0['value'] > 0 else None,
                           derived_from=f'history.{sid} / history.revenue at {out["window"][0]} and {out["window"][1]}; '
                                        f'change = end - start; cagr = (end value / start value) ^ (1 / span_years) - 1')
                if sid == 'research_and_development':
                    d_rd = s1['value'] - s0['value']
                    out['incremental_revenue_per_incremental_rd'] = (
                        {'value': (out['revenue']['end'] - out['revenue']['start']) / d_rd,
                         'derived_from': f'(history.revenue[{out["window"][1]}] - [{out["window"][0]}]) / '
                                         f'(history.research_and_development[{out["window"][1]}] - [{out["window"][0]}])'}
                        if d_rd > 0 else {'value': None, 'reason': 'R&D did not rise over the window'})
        out['series'].append(row)
    return out


def _gross_margin(gm):
    if gm is None:
        return None
    if not isinstance(gm, dict):
        raise ValueError('gross_margin: {start, end} or {year: margin}')
    where = 'history.gross_margin'
    if gm.get('start') is not None or gm.get('end') is not None:
        start, end = number(gm.get('start'), f'{where}.start', -1, 1), number(gm.get('end'), f'{where}.end', -1, 1)
        extra = [k for k in gm if k not in GROSS_MARGIN_META]
        if extra:
            raise ValueError(f'{where}: state {{start, end}} or {{year: margin}}, not both ({extra})')
        out = {'start': start, 'end': end}
        for k in ('start_year', 'end_year', 'years'):
            if gm.get(k) is not None:
                out[k] = str(gm[k])
    else:
        pts, _ = _series(gm, where, -1)
        if len(pts) < 2:
            return {'status': 'not_computed', 'reason': 'needs the gross margin at two dates'}
        out = {'start_year': pts[0][1], 'start': pts[0][2], 'end_year': pts[-1][1], 'end': pts[-1][2]}
    out.update(change=out['end'] - out['start'], display=_pp(out['end'] - out['start']),
               derived_from=f'{where}: end - start, printed as a change in pp')
    return out


def _machine(cats, inten, rules):
    """The process reading from its conditions; each condition True / False / None (could not be read)."""
    ren = [c['renewed'] for c in cats]
    renewal = True if True in ren else (False if ren and all(r is False for r in ren) else None)
    series = inten['series']
    known = [s for s in series if s['trend'] != 'unknown']
    rd_known = any(s['id'] == 'research_and_development' for s in known)
    if any(s['trend'] == 'rising' for s in known):
        flat_or_falling = False
    else:
        flat_or_falling = True if rd_known else None
    if any(s['rising_by_rule'] for s in known):
        rising = True
    else:
        rising = False if rd_known and len(known) == len(series) else None
    rev = inten['revenue'] or {}
    cagr = rev.get('cagr')
    growth_min = None if cagr is None else cagr >= rules['machine_growth_min'] - _EPS
    slows = (rev.get('growth_trend') or {}).get('value')
    cond = {'renewal': renewal, 'intensity_flat_or_falling': flat_or_falling, 'revenue_cagr_at_least_min': growth_min,
            'intensity_rising_by_rule': rising, 'growth_slows': slows}
    if all(v is None for v in cond.values()):
        value = 'unknown'
    elif renewal and flat_or_falling and growth_min:
        value = 'working'
    elif rising and slows:
        value = 'failing'
    else:
        value = 'unproven'
    why = {'renewal': 'no renewed category', 'intensity_flat_or_falling': 'innovation intensity not flat or falling',
           'revenue_cagr_at_least_min': 'revenue CAGR below rules.machine_growth_min'}
    reasons = []
    if value in ('unproven', 'unknown'):
        reasons = [why[k] if cond[k] is False else f'{_name(k)}: could not be read' for k in why if cond[k] is not True]
    return value, cond, reasons


def _process_questions(cats, inten, cond):
    qs = []
    if cond['renewal'] is None:
        qs.append('supply revenue by fiscal year for each product category (five years when available; a missing year '
                  'stays missing)')
    if inten['window'] is None:
        qs.append('supply revenue, R&D and advertising for the same fiscal years (two or more)')
    for s in inten['series']:
        if s['trend'] == 'unknown' and s.get('missing'):
            qs.append(f'supply {_name(s["id"])} for {", ".join(s["missing"])} to read its intensity trend')
    if inten['window'] is not None and not any(s['id'] == 'research_and_development' for s in inten['series']):
        qs.append(f'supply R&D for {" and ".join(inten["window"])} (innovation intensity)')
    return [{'about': 'process_evidence', 'scope': 'total', 'question': q} for q in qs]


def _process_core(categories, intensity, gross_margin, rules):
    if categories is not None and not isinstance(categories, list):
        raise ValueError('categories: a list of categories')
    if intensity is not None and not isinstance(intensity, dict):
        raise ValueError('intensity: {revenue|research_and_development|advertising: {year: value}}')
    names = set()
    cats = [_category(c, rules, names) for c in categories or []]
    inten = _intensity(intensity or {}, rules)
    value, cond, reasons = _machine(cats, inten, rules)
    out = {'check': 'process_evidence', 'kind': 'reading', 'machine': value, 'conditions': cond}
    if reasons:
        out['reasons'] = reasons
    out.update(categories=cats, renewed_categories=[c['name'] for c in cats if c['renewed']],
               new_categories=[c['name'] for c in cats if c['kind'] == 'new'], revenue=inten['revenue'],
               intensity={'window': inten['window'], 'series': inten['series']},
               incremental_revenue_per_incremental_rd=inten['incremental_revenue_per_incremental_rd'],
               gross_margin=_gross_margin(gross_margin), rules={k: rules[k] for k in PROCESS_RULES},
               rule=registry()['checks']['process_evidence']['rule'], boundary=PROCESS_BOUNDARY)
    return out, _process_questions(cats, inten, cond)


def process_evidence(categories, intensity, gross_margin=None):
    """The moat read as a repeatable process, as evidence to cite (never a rating): each category's falls from a peak and
    new highs (renewed within rules.renewal_window_years), innovation intensity (R&D and advertising over revenue in the
    same year) and its trend over the revenue window, revenue and R&D CAGR, incremental revenue per incremental R&D, the
    gross-margin change in pp, and the machine reading working / failing / unproven / unknown with every condition shown.
    `categories` is [{name, kind?, revenue {year: value}, growth? {year: g}, source_ids?}], `intensity`
    {revenue, research_and_development?, advertising?: {year: value}} (an econ case's `history`), `gross_margin`
    {start, end, years?} or {year: margin}. A missing year is listed and never interpolated. Pure and deterministic."""
    out, qs = _process_core(categories, intensity, gross_margin, rules())
    out['questions'] = qs
    out['input_sha256'] = F.digest({'categories': categories, 'intensity': intensity, 'gross_margin': gross_margin})
    out['engine_version'] = __version__
    return deepcopy(out)


def _process(ix, mid):
    """process_evidence on the case's categories and history blocks, point in time (a block none of whose sources was
    available at as_of is listed under unavailable)."""
    case = ix['case']
    cats = []
    raw = case.get('categories')
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError('categories: a list of categories')
        ix['read'].add('categories')
        for i, c in enumerate(raw):
            label = f'categories[{c.get("name") if isinstance(c, dict) and c.get("name") else i}]'
            if _visible(case, _source_ids(case, c, label)):
                cats.append(c)
            else:
                _unavailable(ix, label)
    inten, gm = {}, None
    hist = case.get('history')
    if hist is not None:
        if not isinstance(hist, dict):
            raise ValueError('history: an object {series: {year: value, source_ids}}')
        ix['read'].add('history')
        for k in sorted(hist):
            label = f'history.{k}'
            if k not in HISTORY_SERIES and k != 'gross_margin':
                if all(x['ref'] != label for x in ix['not_read']):
                    ix['not_read'].append({'ref': label, 'reason': 'not a series process evidence reads '
                                                                   f'({", ".join(HISTORY_SERIES)}, gross_margin)'})
                continue
            if not _visible(case, _source_ids(case, hist[k], label)):
                _unavailable(ix, label)
            elif k == 'gross_margin':
                gm = hist[k]
            else:
                inten[k] = hist[k]
    out, qs = _process_core(cats, inten, gm, ix['rules'])
    ix['questions'] += [{'model': mid, **q} for q in qs]
    return {**out, 'model': mid}


def _cash_conversion(ix, mid):
    """(measure or None, flags, the reason the check did not run or None)."""
    rules = ix['rules']
    thr = rules['cfo_to_ni_min']
    case = ix['case']
    m = {'check': 'cash_conversion', 'kind': 'measure', 'model': mid, 'scope': 'total', 'threshold': thr,
         'threshold_rule': 'cfo_to_ni_min (no dedicated frozen rule; the funding screen\'s CFO / NI threshold)'}
    if case.get('cash') is not None:
        b, ids = _block(ix, 'cash')
        if ids is None:
            return None, [], 'cash: none of its sources was available at as_of'
        for k in sorted(b):
            if k not in CASH_KEYS:
                raise ValueError(f'cash.{k}: unknown field; the block states {CASH_KEYS}')
        if b.get('cash_to_debt') is not None and all(x['ref'] != 'cash.cash_to_debt' for x in ix['not_read']):
            ix['not_read'].append({'ref': 'cash.cash_to_debt', 'reason': 'not read by cash_conversion (a balance-sheet ratio)'})
        stated = b.get('fcf_to_net_income')
        if stated is not None and (b.get('free_cash_flow') is not None or b.get('net_income') is not None):
            raise ValueError('cash: state fcf_to_net_income, or free_cash_flow and net_income; not both')
        if stated is not None:
            m.update(value=number(stated, 'cash.fcf_to_net_income'), derived_from='cash.fcf_to_net_income as stated')
        elif b.get('free_cash_flow') is not None and b.get('net_income') is not None:
            fcf, ni = number(b['free_cash_flow'], 'cash.free_cash_flow'), number(b['net_income'], 'cash.net_income')
            if ni <= 0:
                return {**m, 'status': 'not_computed', 'reason': 'cash.net_income is not positive: FCF / NI is not read',
                        'source_ids': ids}, [], None
            m.update(free_cash_flow=fcf, net_income=ni, value=fcf / ni, derived_from='cash.free_cash_flow / cash.net_income')
        else:
            return None, [], 'cash: needs fcf_to_net_income, or free_cash_flow and net_income'
        m.update(window=str(b['period']) if b.get('period') is not None else 'not stated', source_ids=ids)
        basis = b.get('fcf_basis')
    else:
        per = ix['period']
        got = {lid: _line_item(ix, lid, measure=per) for lid in ('operating_cash_flow', 'capex', 'net_income')}
        if any(v is None for v in got.values()):
            return None, [], ('needs a cash block (FCF / net income and its FCF basis) or the lines operating cash flow, capex '
                              f'and net income on the case period ({per}); missing {", ".join(k for k, v in got.items() if v is None)}')
        ocf, capex, ni = got['operating_cash_flow'], got['capex'], got['net_income']
        ids = sorted({i for it in got.values() for i in it['source_ids']})
        if ni['value'] <= 0:
            return {**m, 'status': 'not_computed', 'reason': f'{ni["ref"]} is not positive: FCF / NI is not read',
                    'source_ids': ids}, [], None
        fcf = ocf['value'] - capex['value']
        m.update(free_cash_flow=fcf, net_income=ni['value'], value=fcf / ni['value'], window=per, source_ids=ids,
                 derived_from=f'({ocf["ref"]} - {capex["ref"]}) / {ni["ref"]}')
        basis = 'operating cash flow - capex (case lines)'
    if isinstance(basis, str) and basis.strip():
        m['fcf_basis'] = basis.strip()
    else:
        m['fcf_basis'] = 'not stated'
        ix['questions'].append({'model': mid, 'about': 'cash_conversion', 'scope': 'total',
                                'question': 'state the FCF definition behind FCF / net income (operating cash flow - capex, '
                                            'or the issuer definition)'})
    near = _near(m['value'], thr, rules)
    if near:
        m['near'] = near
    m['status'] = 'fails' if m['value'] < thr else 'passes'
    flags = []
    if m['status'] == 'fails':
        f = _flag(ix, 'cash_conversion', mid, 'total', fcf_to_net_income=m['value'], threshold=thr, threshold_rule='cfo_to_ni_min',
                  fcf_basis=m['fcf_basis'], window=m['window'], source_ids=m['source_ids'], derived_from=m['derived_from'])
        if near:
            f['near'] = near
        flags.append(f)
    return m, flags, None


def _top_n_plain(t):
    """A top-N input validated: {n, scope, window, average, prior_average, average_growth, total, prior_total, share,
    prior_share, revenue {value, growth, scope, window, ref}, refs, source_ids}."""
    if not isinstance(t, dict):
        raise ValueError('top_n: an object')
    extra = sorted(set(t) - set(TOP_N_FIELDS))
    if extra:
        raise ValueError(f'top_n: unknown fields {extra}; it states {TOP_N_FIELDS}')
    n = t.get('n')
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
        raise ValueError('top_n.n: a positive integer')
    w = t.get('window')
    if w not in MONTHS:
        raise ValueError(f'top_n.window: the measurement window of the top-N figures, one of {tuple(MONTHS)}')
    out = {'n': n, 'scope': _scope(t.get('scope'), 'top_n'), 'window': w, 'comparison': t.get('comparison') or 'y/y',
           'refs': dict(t.get('refs') or {}), 'source_ids': list(t.get('source_ids') or []), 'model': t.get('model')}
    if out['comparison'] not in COMPARISONS:
        raise ValueError(f'top_n.comparison: one of {COMPARISONS}')
    for k in ('average', 'prior_average', 'total', 'prior_total'):
        out[k] = _num(t.get(k), f'top_n.{k}', 0)
    out['average_growth'] = _num(t.get('average_growth'), 'top_n.average_growth', -1)
    for k in ('share', 'prior_share'):
        out[k] = _num(t.get(k), f'top_n.{k}', 0, 1)
        if out[k] == 0:
            raise ValueError(f'top_n.{k}: a share above zero')
    rv = t.get('revenue')
    out['revenue'] = []
    for i, x in enumerate([] if rv is None else rv if isinstance(rv, list) else [rv]):
        if not isinstance(x, dict) or x.get('window') not in MONTHS:
            raise ValueError(f'top_n.revenue: {{value, growth?, scope, window}} (or a list of them) with a window from {tuple(MONTHS)}')
        out['revenue'].append({'value': _num(x.get('value'), 'top_n.revenue.value', 0), 'growth': _num(x.get('growth'), 'top_n.revenue.growth', -1),
                               'scope': _scope(x.get('scope'), 'top_n.revenue'), 'window': x['window'],
                               'ref': x.get('ref') or ('top_n.revenue' if not isinstance(rv, list) else f'top_n.revenue[{i}]')})
    return out


def _top_n_core(t, splits, rules):
    """(top_n_expansion measure, flags, questions, top_n_inconsistent not-run reason or None) for one validated top-N input."""
    tol = rules['top_n_tolerance']
    ref = lambda k: t['refs'].get(k) or f'top_n.{k}'       # noqa: E731  (the case row a figure came from)
    scope, w, n, mid = t['scope'], t['window'], t['n'], t['model']
    out = {'check': 'top_n_expansion', 'kind': 'measure', 'model': mid, 'scope': scope, 'window': w, 'comparison': t['comparison'],
           'n': n, 'note': TOP_N_NOTE}
    qs, flags = [], []
    avg, pavg, g = t['average'], t['prior_average'], t['average_growth']
    if avg is not None or pavg is not None or g is not None:
        a = {'value': avg, 'prior': pavg}
        if g is not None:
            a.update(growth=g, growth_from='stated')
        elif avg is not None and pavg:
            a.update(growth=avg / pavg - 1, growth_from='levels')
        if g is not None and avg is not None and pavg:
            a['levels_growth'] = avg / pavg - 1
        a['derived_from'] = f'{ref("average")} {t["comparison"]}: growth ' + ('as stated' if g is not None else 'value / prior - 1')
        out['average'] = a
    total = {}
    if t['total'] is not None:
        total = {'value': t['total'], 'from': 'stated'}
    elif n is not None and avg is not None:
        total = {'value': n * avg, 'from': 'n x average'}
    if t['prior_total'] is not None:
        total.update(prior=t['prior_total'], prior_from='stated')
    elif n is not None and pavg is not None:
        total.update(prior=n * pavg, prior_from='n x prior average')
    if total.get('value') is not None and total.get('prior'):
        total['growth'] = total['value'] / total['prior'] - 1
    elif total.get('value') is not None and g is not None and total['from'] == 'n x average':
        total['growth'] = g                                       # the same N at both dates: the total grows as the average
    if total.get('value') is not None:
        total['derived_from'] = (f'{ref("total")} as stated' if total['from'] == 'stated' else f'n ({n}) x {ref("average")}') + \
            ('; growth = value / prior - 1' if total.get('prior') else '; growth = the average\'s (same N)' if 'growth' in total else '')
        out['total'] = total
    else:
        qs.append({'about': 'top_n_expansion', 'scope': scope, 'question': 'supply N with the top-N average revenue, or the top-N total revenue'})
    sh, psh = t['share'], t['prior_share']
    if sh is not None:
        s = {'value': sh, 'prior': psh}
        if psh is None:
            s.update(trend='unknown', reason='prior share not supplied')
            qs.append({'about': 'top_n_expansion', 'scope': scope,
                       'question': f'supply the prior top-{n or "N"} share of revenue (same N and window) to read the share trend'})
        else:
            s.update(change=sh - psh, trend='rising' if sh > psh else 'falling' if sh < psh else 'unchanged',
                     derived_from=f'{ref("share")}: value - prior')
        out['share'] = s
    implied = None
    if total.get('value') is not None and sh:
        implied = total['value'] / sh
        out['implied_revenue'] = {'value': implied, 'window': w, 'scope': scope, 'derived_from': f'top-N total / {ref("share")}'}
    not_compared, why = [], None
    rv = next((x for x in t['revenue'] if x['scope'] == scope and x['window'] == w), None)     # the first on the same
    for x in t['revenue']:                                                                    # scope and window
        if x['scope'] != scope or x['window'] != w:
            not_compared.append({'with': x['ref'], 'status': 'scope_mismatch', 'scope': x['scope'], 'window': x['window'],
                                 'reason': f'the top-N figures are {w}, {scope}; {x["ref"]} is {x["window"]}, {x["scope"]}: '
                                           'nothing is compared'})
    if implied is None:
        why = 'the top-N total and share are needed for the implied revenue'
    elif rv is None or rv['value'] is None:
        why = f'no revenue on the top-N scope and window ({scope}, {w}) is supplied'
        out['revenue_check'] = {'status': 'not_computed', 'reason': why}
        qs.append({'about': 'top_n_inconsistent', 'scope': scope,
                   'question': f'supply {w} revenue ({scope}) to check the top-N figures (ttm_revenue for TTM)'})
    else:
        diff = rv['value'] / implied - 1
        rc = {'status': 'inconsistent' if abs(diff) > tol + _EPS else 'consistent', 'supplied': rv['value'], 'implied': implied,
              'diff': diff, 'top_n_tolerance': tol, 'derived_from': f'{rv["ref"]} / implied revenue - 1'}
        near = _near(abs(diff), tol, rules)
        if near:
            rc['near'] = near
        out['revenue_check'] = rc
        if rc['status'] == 'inconsistent':
            f = _flag({'checks': registry()['checks']}, 'top_n_inconsistent', mid, scope, window=w, n=n, implied_revenue=implied,
                      supplied=rv['value'], diff=diff, threshold=tol, derived_from=f'{rv["ref"]} / (top-N total / {ref("share")}) - 1')
            if near:
                f['near'] = near
            flags.append(f)
            qs.append({'about': 'top_n_inconsistent', 'scope': scope,
                       'question': 'do the top-N average, N and share use the same customer set and window as the revenue?'})
    rg, rg_ref = (rv['growth'], rv['ref']) if rv is not None and rv['growth'] is not None else (None, None)
    for s in splits:
        same = s['scope'] == scope and s.get('measure') == w and s['window'] == t['comparison']
        if not same:
            not_compared.append({'with': f'growth_splits[{s["model"]}@{s["scope"]}]', 'status': 'scope_mismatch', 'scope': s['scope'],
                                 'window': f'{s.get("measure") or "unstated"} {s["window"]}',
                                 'reason': f'the top-N figures are {w} {t["comparison"]}, {scope}; the split is '
                                           f'{s.get("measure") or "unstated"} {s["window"]}, {s["scope"]}: nothing is compared'})
        elif rg is None and (s.get('line_growth') or {}).get('growth') is not None:
            rg, rg_ref = s['line_growth']['growth'], f'growth_splits[{s["model"]}@{s["scope"]}].line_growth'
    tg = total.get('growth')
    if tg is not None and rg is not None:
        out['vs_revenue_growth'] = {'top_n_growth': tg, 'revenue_growth': rg, 'gap_pp': tg - rg,
                                    'reading': 'top_n_grew_faster' if tg > rg else 'top_n_grew_slower' if tg < rg else 'same',
                                    'derived_from': f'top-N total growth - {rg_ref} growth, both {w} {t["comparison"]}, {scope}'}
    elif tg is not None:
        out['vs_revenue_growth'] = {'status': 'not_computed',
                                    'reason': f'no revenue growth on the top-N scope and window ({scope}, {w} {t["comparison"]})'}
    if not_compared:
        out['not_compared'] = not_compared
    if t['source_ids']:
        out['source_ids'] = sorted(set(t['source_ids']))
    out['top_n_tolerance'] = tol
    return out, flags, qs, why


def expansion(split=None, top_n=None, ndr=None):
    """Software expansion as evidence to cite (never a rating): customers x revenue per customer from a growth split
    (econ.growth_split; expansion_led with NDR, both growths shown), NDR and its change, and the top-N figures (average
    growth, total, implied revenue at the stated share, share trend, top_n_inconsistent against revenue on the same
    scope and window). `split` may carry `measure` (the measurement window of its line); a split or revenue on another
    scope or window is listed under not_compared (scope_mismatch) and nothing is computed from it. `ndr` is {value,
    prior?, scope?, window?, ref?}; `top_n` is {n, scope, window, average, prior_average?, average_growth?, total?,
    prior_total?, share, prior_share?, revenue? {value, growth?, scope, window} or a list of them (the first on the
    top-N scope and window is compared), refs?, source_ids?}. Pure and deterministic."""
    r = rules()
    out = {'check': 'expansion', 'expansion_led': None, 'ndr': None, 'top_n': None, 'flags': [], 'questions': [],
           'checks_not_run': []}
    if ndr is not None:
        if not isinstance(ndr, dict):
            raise ValueError('ndr: {value, prior?, scope?, window?}')
        ndr = {'value': number(ndr.get('value'), 'ndr.value', 0), 'prior': _num(ndr.get('prior'), 'ndr.prior', 0),
               'scope': _scope(ndr.get('scope'), 'ndr'), 'window': ndr.get('window'), 'ref': ndr.get('ref') or 'ndr'}
        out['ndr'] = dict(ndr)
        if ndr['prior'] is not None:
            out['ndr'].update(change=ndr['value'] - ndr['prior'], derived_from=f'{ndr["ref"]}: value - prior')
    mid = None
    if split is not None:
        if not isinstance(split, dict) or not split.get('model') or not split.get('scope') or 'factors' not in split:
            raise ValueError('split: a growth split (econ.growth_split)')
        mid = split['model']
        rd, qs = _expansion_led(mid, split, ndr, r)
        out['expansion_led'] = rd
        if rd is None:
            out['checks_not_run'].append({'check': 'expansion_led', 'reason': 'the split did not read units x revenue per unit consistently'})
        out['questions'] += [{'model': mid, **q} for q in qs]
    if top_n is not None:
        t = _top_n_plain(top_n)
        t['model'] = t['model'] or mid
        m, fl, qs, why = _top_n_core(t, [split] if split is not None else [], r)
        out['top_n'] = m
        out['flags'] += fl
        out['questions'] += [{'model': t['model'], **q} for q in qs]
        if why:
            out['checks_not_run'].append({'check': 'top_n_inconsistent', 'reason': why})
    out['boundary'] = EXPANSION_BOUNDARY
    out['input_sha256'] = F.digest({'split': split, 'top_n': top_n, 'ndr': ndr})
    out['engine_version'] = __version__
    return deepcopy(out)


def _ttm_revenue(ix):
    """The `ttm_revenue` block as a comparison revenue for the top-N figures (window TTM), or None."""
    case = ix['case']
    b = case.get('ttm_revenue')
    if b is None:
        return None
    if not isinstance(b, dict):
        raise ValueError('ttm_revenue: an object {value, growth?, scope?, source_ids}')
    if b.get('window', TTM) != TTM:
        raise ValueError('ttm_revenue.window: TTM (the block is trailing-twelve-month revenue)')
    if b.get('value') is None and b.get('growth') is None:
        ix['read'].add('ttm_revenue')
        if all(x['ref'] != 'ttm_revenue' for x in ix['not_read']):
            ix['not_read'].append({'ref': 'ttm_revenue', 'reason': 'neither a level nor a growth rate'})
        return None
    b, ids = _block(ix, 'ttm_revenue')
    if ids is None:
        return None
    return {'value': _num(b.get('value'), 'ttm_revenue.value', 0), 'growth': _num(b.get('growth'), 'ttm_revenue.growth', -1),
            'scope': _scope(b.get('scope'), 'ttm_revenue'), 'window': TTM, 'ref': 'ttm_revenue', 'source_ids': ids}


def _top_n_input(ix, mid, scope, ttm):
    """The top-N rows of one scope as a top_n input, or (None, reason) when they cannot be combined."""
    spec = ix['spec'][mid]['kpis']
    got = {}
    for k in TOP_N_KPIS:
        if k not in spec:
            continue
        rows = [it for it in ix['kpi'].get((k, scope), []) if it['value'] is not None]
        for it in rows:
            if it['basis'] not in spec[k]['basis']:
                ix['not_read'].append({'ref': it['ref'], 'reason': f'top-N figure on {_a_basis([it["basis"]])}; the registry '
                                                                   f'declares {_a_basis(spec[k]["basis"])}'})
            elif it['n'] is None:
                ix['not_read'].append({'ref': it['ref'], 'reason': 'the N the top-N figure covers is not stated'})
                ix['questions'].append({'model': mid, 'about': 'top_n_expansion', 'scope': scope,
                                        'question': f'state N for {it["ref"]}'})
            elif k not in got:
                got[k] = it
    if not got:
        return None, f'no top-N figure with its N at {scope}'
    ns, ws, cs = ({it[f] for it in got.values()} for f in ('n', 'measure', 'window'))
    if len(ns) > 1 or len(ws) > 1 or len(cs) > 1:
        return None, ('the top-N figures differ in N, window or comparison (' +
                      '; '.join(f'{it["ref"]}: N {it["n"]}, {it["measure"]} {it["window"]}' for it in got.values()) + '): scope_mismatch')
    avg, sh, tot = (got.get(k) for k in TOP_N_KPIS)

    def home(it):                      # a row's comparison in the window its source compares
        return (it['cmp'].get(it['window']) or {}) if it else {}
    rev = [ttm] if ttm is not None else []          # comparison revenues: the ttm_revenue block, then the revenue line at
    it = _line_item(ix, 'revenue', scope)            # this scope; one on another scope or window is shown as a mismatch
    if it is not None:
        c = it['cmp'].get('y/y') or {}
        rev.append({'value': it['value'], 'growth': _pick(c, 'stated')[0] if _any_growth(c) else None, 'scope': scope,
                    'window': it['measure'], 'ref': it['ref']})
    t = {'n': next(iter(ns)), 'scope': scope, 'window': next(iter(ws)), 'comparison': next(iter(cs)), 'model': mid,
         'average': avg['value'] if avg else None, 'prior_average': home(avg).get('prior'),
         'average_growth': home(avg).get('stated'), 'total': tot['value'] if tot else None, 'prior_total': home(tot).get('prior'),
         'share': sh['value'] if sh else None, 'prior_share': home(sh).get('prior'),
         'refs': {'average': avg['ref'] if avg else None, 'total': tot['ref'] if tot else None, 'share': sh['ref'] if sh else None},
         'source_ids': sorted({i for it in got.values() for i in it['source_ids']})}
    t['revenue'] = [{k: x[k] for k in ('value', 'growth', 'scope', 'window', 'ref')} for x in rev]
    return t, None


def _top_n(ix, mid, splits):
    """(measures, flags, [(check, reason)] not run) for the top-N figures of a model at each scope they are reported for."""
    scopes = sorted({s for (k, s) in ix['kpi'] if k in TOP_N_KPIS}, key=lambda s: (s != 'total', s))
    if not scopes:
        why = 'no top-N figures supplied (top_n_average_revenue, top_n_share, top_n_total_revenue with N)'
        return [], [], [('top_n_expansion', why), ('top_n_inconsistent', why)]
    ttm = _ttm_revenue(ix)
    measures, flags, not_run = [], [], []
    for scope in scopes:
        t, why = _top_n_input(ix, mid, scope, ttm)
        if t is None:
            not_run += [('top_n_expansion', why), ('top_n_inconsistent', why)]
            continue
        ms = []
        for s in splits:
            it = _line_item(ix, s['line'], s['scope'])
            ms.append({**s, 'measure': it['measure'] if it else None})
        m, fl, qs, why = _top_n_core(_top_n_plain(t), ms, ix['rules'])
        measures.append(m)
        flags += fl
        ix['questions'] += [{'model': mid, **q} for q in qs]
        if why:
            not_run.append(('top_n_inconsistent', why))
    return measures, flags, not_run
