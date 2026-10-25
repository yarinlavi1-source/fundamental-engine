"""Evidence pack: everything the ticker workflow learned about ONE company, in one file, turned into pipeline inputs.

The saved ticker workflow (.claude/workflows/ticker.js; skeleton brain/protocols/master_process.md) gathers the company's
evidence with the user's connectors and writes evidence_pack.json. `to_inputs(pack)` turns it into the pipeline
inputs.json and `to_bundle(pack)` into the evidence bundle (facts.add_qualitative_bundle) that registers the pack's
sources and items in the FactBook, so every evidence id the inputs cite resolves there. Nothing here is fetched,
estimated or filled in: a lever the pack does not set stays on the engine's own rule (a visible gap), never another
company's number.

The pack (pack_version 1; unknown keys are rejected at every level):
  ticker, as_of (YYYY-MM-DD), identity {name, currency, exchange, security, fiscal_year_end, cik, type, evidence_ids}
  sources [{id, kind (SOURCE_KINDS), title, url ('connector:<tool>' for a connector payload), date, independent (bool),
            publisher, retrieval, syndicated_from}]: a syndicated copy names the source it copies and is never independent
  facts [{id, metric, period, value, unit, source_ids, period_end, basis reported|derived, statement, factbook (bool)}]
  guidance [{id, metric, period, low, high, unit, issued, source_id}]  the company's own guidance (issuer documents only)
  guidance_track [{period, metric, guided_mid, actual, source_ids}]   what the company guided and then delivered
  consensus [{id, metric, period, value, n, low, high, source_id}]    attributed estimates (consensus sources only)
  kpis [{id, name, period, value, unit, source_ids}], segments [{id, name, period, revenue, operating_income, unit,
  source_ids}], customers [{id, name, share, period, source_ids}]
  competitors [{ticker, name, why, metrics, source_ids}]              named direct competitors and why each one is
  bottlenecks [{id, reading, evidence_ids | hypothesis: true}]
  potential {market_direction, bottleneck_potential, share_trajectory, competitive_position, threats}: each
            {reading, evidence_ids} or {reading, hypothesis: true}
  drivers {lever: {bear|base|bull|all: value, source_tier, evidence_ids, reason, competitors, adjust_by_track_record}}
  scenario_events {bear|base|bull: [{event_he, evidence_ids | hypothesis: true}]}  (all three scenarios)
  company_path   inputs.company_path (contract: next-fiscal-year EPS x forward P/E per scenario, today and years 1-5)
  key_risk {reading_he, evidence_ids}; falsifiers [{id, scenario, metric, threshold, direction below|above, by,
  evidence_ids}]; assessments {model_card dimension: {assessment, evidence_ids, note}}; market {risk_free,
  equity_risk_premium: {value, as_of, source_id}}; forecast_options {horizon_years, financing, cyclical}; analyst; notes

Rules enforced: every evidence id resolves to a pack source or item; consensus is never evidence of a fact (facts, KPIs,
segments, customers and the guidance record may not cite a consensus source); guidance comes from issuer documents; a
source tier must be backed by a source of a matching kind (guidance/contracted: issuer documents; consensus: a
consensus source; own_history: issuer documents or structured data; market: price/macro/structured data; competitor:
named competitors in the pack); no source dated after as_of; a cited source must be citable at as_of (published at
least facts.LAG_DAYS before). Guidance and its track record: the ratio actual / guided midpoint is always recorded; a
guidance-tier growth driver states adjust_by_track_record (true = every scenario it sets, or a list of scenarios, or
false), and where it applies the guided year's growth is adjusted explicitly: (1 + g) x ratio - 1 (recorded before and
after).
"""
from copy import deepcopy
from datetime import date, timedelta
import json
import math

PACK_VERSION = 1
SCENARIOS = ('bear', 'base', 'bull')
TIERS = ('guidance', 'contracted', 'consensus', 'own_history', 'competitor', 'market', 'analyst')
SOURCE_KINDS = ('sec_filing', 'release', 'transcript', 'structured_data', 'consensus', 'price', 'macro', 'company_ir',
                'industry', 'press')
ISSUER_KINDS = frozenset({'sec_filing', 'release', 'transcript', 'company_ir'})
# what kind of source each tier needs at least one of ('analyst' is the analyst's own judgement: any evidenced source)
TIER_KINDS = {'guidance': ISSUER_KINDS, 'contracted': ISSUER_KINDS, 'consensus': frozenset({'consensus'}),
              'own_history': ISSUER_KINDS | {'structured_data'}, 'market': frozenset({'price', 'macro', 'structured_data'}),
              'competitor': None, 'analyst': None}
# pack kind -> evidence-bundle kind (facts.SOURCE_KIND makes filings and releases issuer documents; the rest secondary)
BUNDLE_KIND = {'sec_filing': 'filing', 'release': 'earnings_release', 'company_ir': 'issuer_release'}
RETRIEVALS = ('primary_document', 'provider_normalized', 'search_excerpt', 'market_feed')
POTENTIAL = ('market_direction', 'bottleneck_potential', 'share_trajectory', 'competitive_position', 'threats')
TRACK_LEVERS = ('growth_anchor', 'growth')
FORECAST_OPTIONS = ('horizon_years', 'financing', 'cyclical')

TOP = {'pack_version', 'ticker', 'as_of', 'identity', 'sources', 'facts', 'guidance', 'guidance_track', 'consensus',
       'kpis', 'segments', 'customers', 'competitors', 'bottlenecks', 'potential', 'drivers', 'scenario_events',
       'company_path', 'key_risk', 'falsifiers', 'assessments', 'market', 'forecast_options', 'analyst', 'notes'}
REQUIRED = ('ticker', 'as_of', 'identity', 'sources', 'potential', 'scenario_events')
KEYS = {
    'identity': ({'name', 'currency', 'exchange', 'security', 'fiscal_year_end', 'cik', 'type', 'evidence_ids'},
                 ('name', 'currency')),
    'source': ({'id', 'kind', 'title', 'url', 'date', 'independent', 'publisher', 'retrieval', 'syndicated_from'},
               ('id', 'kind', 'title', 'url', 'date', 'independent')),
    'fact': ({'id', 'metric', 'period', 'value', 'unit', 'source_ids', 'period_end', 'basis', 'statement', 'factbook'},
             ('id', 'metric', 'period', 'value', 'unit', 'source_ids')),
    'guidance': ({'id', 'metric', 'period', 'low', 'high', 'unit', 'issued', 'source_id'},
                 ('id', 'metric', 'period', 'low', 'high', 'issued', 'source_id')),
    'guidance_track': ({'period', 'metric', 'guided_mid', 'actual', 'source_ids'},
                       ('period', 'guided_mid', 'actual', 'source_ids')),
    'consensus': ({'id', 'metric', 'period', 'value', 'n', 'low', 'high', 'source_id'},
                  ('id', 'metric', 'period', 'value', 'source_id')),
    'kpi': ({'id', 'name', 'period', 'value', 'unit', 'source_ids'}, ('id', 'name', 'period', 'value', 'source_ids')),
    'segment': ({'id', 'name', 'period', 'revenue', 'operating_income', 'unit', 'source_ids'},
                ('id', 'name', 'period', 'revenue', 'source_ids')),
    'customer': ({'id', 'name', 'share', 'period', 'source_ids'}, ('id', 'share', 'period', 'source_ids')),
    'competitor': ({'ticker', 'name', 'why', 'metrics', 'source_ids'}, ('ticker', 'why', 'source_ids')),
    'bottleneck': ({'id', 'reading', 'evidence_ids', 'hypothesis'}, ('id', 'reading')),
    'reading': ({'reading', 'evidence_ids', 'hypothesis'}, ('reading',)),
    'driver': ({'bear', 'base', 'bull', 'all', 'source_tier', 'competitors', 'evidence_ids', 'reason',
                'adjust_by_track_record'}, ('source_tier', 'evidence_ids', 'reason')),
    'event': ({'event_he', 'evidence_ids', 'hypothesis'}, ('event_he',)),
    'key_risk': ({'reading_he', 'evidence_ids'}, ('reading_he', 'evidence_ids')),
    'falsifier': ({'id', 'scenario', 'metric', 'threshold', 'direction', 'by', 'evidence_ids'},
                  ('id', 'scenario', 'metric', 'threshold', 'direction', 'by', 'evidence_ids')),
    'rate': ({'value', 'as_of', 'source_id'}, ('value', 'as_of', 'source_id')),
    'company_path': ({'metric', 'multiple', 'scenarios', 'own_history', 'peers'}, ('metric', 'multiple', 'scenarios')),
    'path_scenario': ({'multiple_path', 'justification', 'reason_he', 'source_tier', 'competitors', 'evidence_ids'},
                      ('multiple_path', 'justification', 'reason_he', 'source_tier', 'evidence_ids')),
    'own_history': ({'low', 'median', 'high', 'years', 'evidence_ids'}, ('low', 'median', 'high', 'years', 'evidence_ids')),
    'peer': ({'ticker', 'forward_pe', 'growth', 'evidence_ids'}, ('ticker', 'forward_pe', 'growth', 'evidence_ids')),
}
JUSTIFICATIONS = ('justified_high', 'peak', 'unproven')
PATH_YEARS = 5


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _day(s):
    try:
        return date.fromisoformat(str(s)) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None


def digest(obj):
    from hashlib import sha256
    return sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class _Check:
    """Collects every problem so one pass lists them all."""

    def __init__(self):
        self.errors = []

    def err(self, where, msg):
        self.errors.append(f'{where}: {msg}')

    def obj(self, x, kind, where):
        allowed, required = KEYS[kind]
        if not isinstance(x, dict):
            self.err(where, 'must be an object')
            return False
        extra = sorted(set(x) - allowed)
        if extra:
            self.err(where, f'unknown keys {extra} (allowed: {", ".join(sorted(allowed))})')
        missing = [k for k in required if x.get(k) in (None, '', [])]
        if missing:
            self.err(where, f'missing {missing}')
        return not extra and not missing

    def lst(self, pack, key):
        v = pack.get(key, [])
        if not isinstance(v, list):
            self.err(key, 'must be a list')
            return []
        return v


class Pack:
    """A validated pack: lookups for ids, kinds and the evidence-id mapping into FactBook ids."""

    def __init__(self, pack):
        self.raw = pack
        c = self.c = _Check()
        if not isinstance(pack, dict):
            raise ValueError('evidence pack: must be an object')
        extra = sorted(set(pack) - TOP)
        if extra:
            c.err('pack', f'unknown keys {extra}')
        for k in REQUIRED:
            if pack.get(k) in (None, '', [], {}):
                c.err('pack', f'{k} required')
        if pack.get('pack_version', PACK_VERSION) != PACK_VERSION:
            c.err('pack_version', f'must be {PACK_VERSION}')
        self.ticker = str(pack.get('ticker') or '').upper()
        self.as_of = _day(pack.get('as_of'))
        if pack.get('as_of') and not self.as_of:
            c.err('as_of', 'must be YYYY-MM-DD')
        self.sources, self.items, self.consensus_rows = {}, {}, {}
        self._sources()
        self._items()
        self.competitors = {}
        for i, x in enumerate(c.lst(pack, 'competitors')):
            if c.obj(x, 'competitor', f'competitors[{i}]'):
                t = str(x['ticker']).upper()
                if t == self.ticker:
                    c.err(f'competitors[{i}]', 'the company itself is not its competitor')
                self.facts_ids(x['source_ids'], f'competitors[{i}]')
                self.competitors[t] = x
        if isinstance(pack.get('identity'), dict) and c.obj(pack['identity'], 'identity', 'identity'):
            self.ids(pack['identity'].get('evidence_ids') or [], 'identity', allow_empty=True)

    # ---------------------------------------------------------------- sources and items

    def _sources(self):
        c = self.c
        for i, s in enumerate(c.lst(self.raw, 'sources')):
            where = f'sources[{i}]'
            if not c.obj(s, 'source', where):
                continue
            sid = str(s['id'])
            if sid in self.sources:
                c.err(where, f'duplicate id {sid}')
            if s['kind'] not in SOURCE_KINDS:
                c.err(where, f'kind must be one of {", ".join(SOURCE_KINDS)}')
            if not isinstance(s['independent'], bool):
                c.err(where, 'independent must be true or false')
            if s.get('syndicated_from') and s['independent']:
                c.err(where, 'a syndicated copy is not independent evidence (independent must be false)')
            if s.get('retrieval') and s['retrieval'] not in RETRIEVALS:
                c.err(where, f'retrieval must be one of {", ".join(RETRIEVALS)}')
            d = _day(s['date'])
            if not d:
                c.err(where, 'date must be YYYY-MM-DD')
            elif self.as_of and d > self.as_of:
                c.err(where, f'dated {s["date"]}, after as_of (look-ahead)')
            self.sources[sid] = s
        for sid, s in self.sources.items():
            if s.get('syndicated_from') and s['syndicated_from'] not in self.sources:
                c.err(f'source {sid}', f'syndicated_from {s["syndicated_from"]} is not a pack source')

    def citable(self, sid):
        """A source is citable at as_of when published at least facts.LAG_DAYS before (the bundle registers only those)."""
        from .facts import LAG_DAYS
        d = _day(self.sources[sid]['date'])
        return bool(d and self.as_of and d + timedelta(days=LAG_DAYS) <= self.as_of)

    def _items(self):
        c = self.c
        for key, kind in (('facts', 'fact'), ('kpis', 'kpi'), ('segments', 'segment'), ('customers', 'customer')):
            for i, x in enumerate(c.lst(self.raw, key)):
                where = f'{key}[{i}]'
                if not c.obj(x, kind, where):
                    continue
                self._new_item(x['id'], key, x, where)
                self.facts_ids(x['source_ids'], where)
                val = x.get('value', x.get('revenue', x.get('share')))
                if not _num(val):
                    c.err(where, 'value must be a finite number')
                if key == 'facts' and x.get('basis', 'reported') not in ('reported', 'derived'):
                    c.err(where, 'basis must be reported or derived')
                if key == 'facts' and x.get('factbook'):
                    from .facts import BUNDLE_METRICS
                    m = x['metric'][:-4] if x['metric'].endswith('_ytd') else x['metric']
                    if m not in BUNDLE_METRICS or not _day(x.get('period_end')):
                        c.err(where, 'factbook facts need a facts.BUNDLE_METRICS metric and a period_end')
        for i, g in enumerate(c.lst(self.raw, 'guidance')):
            where = f'guidance[{i}]'
            if not c.obj(g, 'guidance', where):
                continue
            self._new_item(g['id'], 'guidance', g, where)
            if not (_num(g['low']) and _num(g['high']) and g['low'] <= g['high']):
                c.err(where, 'low and high must be numbers with low <= high')
            if not _day(g['issued']) or (self.as_of and _day(g['issued']) > self.as_of):
                c.err(where, 'issued must be a date on or before as_of')
            s = self.sources.get(g['source_id'])
            if s is None:
                c.err(where, f'unknown source {g["source_id"]}')
            elif s['kind'] not in ISSUER_KINDS:
                c.err(where, f'guidance is the company\'s own statement: source kind {s["kind"]} is not an issuer document')
        for i, x in enumerate(c.lst(self.raw, 'consensus')):
            where = f'consensus[{i}]'
            if not c.obj(x, 'consensus', where):
                continue
            if x['id'] in self.items or x['id'] in self.consensus_rows or x['id'] in self.sources:
                c.err(where, f'duplicate id {x["id"]}')
            s = self.sources.get(x['source_id'])
            if s is None or s['kind'] != 'consensus':
                c.err(where, 'consensus rows cite a source of kind consensus (attributed estimates)')
            if not _num(x['value']):
                c.err(where, 'value must be a finite number')
            self.consensus_rows[x['id']] = x
        for i, t in enumerate(c.lst(self.raw, 'guidance_track')):
            where = f'guidance_track[{i}]'
            if c.obj(t, 'guidance_track', where):
                self.facts_ids(t['source_ids'], where)
                if not (_num(t['guided_mid']) and t['guided_mid'] > 0 and _num(t['actual'])):
                    c.err(where, 'guided_mid must be a positive number and actual a number')

    def _new_item(self, iid, key, x, where):
        if iid in self.items or iid in self.sources:
            self.c.err(where, f'duplicate id {iid}')
        self.items[iid] = (key, x)

    def facts_ids(self, ids, where):
        """Source ids for a fact-like row: pack sources only, never a consensus source."""
        if not isinstance(ids, list) or not ids:
            self.c.err(where, 'source_ids required')
            return
        for s in ids:
            if s not in self.sources:
                self.c.err(where, f'unknown source {s}')
            elif self.sources[s]['kind'] == 'consensus':
                self.c.err(where, f'consensus ({s}) is an expectation, never evidence of a fact')

    def ids(self, ids, where, allow_empty=False):
        """Evidence ids (sources, items or consensus rows) -> FactBook ids; problems are recorded."""
        if not isinstance(ids, list) or (not ids and not allow_empty):
            self.c.err(where, 'evidence_ids required (a non-empty list)')
            return []
        out = []
        for e in ids:
            if e in self.sources:
                if not self.citable(e):
                    self.c.err(where, f'source {e} is not citable at as_of (published less than facts.LAG_DAYS before)')
                out.append('web:' + e)
            elif e in self.items:
                out.append(e)
            elif e in self.consensus_rows:
                out.append('web:' + self.consensus_rows[e]['source_id'])
            else:
                self.c.err(where, f'unknown evidence id {e}')
        return list(dict.fromkeys(out))

    def kinds(self, ids):
        """Source kinds behind evidence ids (an item counts through its cited sources)."""
        out = set()
        for e in ids or []:
            if e in self.sources:
                out.add(self.sources[e]['kind'])
            elif e in self.items:
                key, x = self.items[e]
                for s in x.get('source_ids') or ([x['source_id']] if x.get('source_id') else []):
                    if s in self.sources:
                        out.add(self.sources[s]['kind'])
            elif e in self.consensus_rows:
                out.add('consensus')
        return out

    def independent(self, ids):
        """Independent sources behind evidence ids: syndicated copies collapse into the source they copy; a source marked not
        independent (e.g. structured data re-keying the filing) adds none."""
        roots = set()
        for e in ids or []:
            srcs = [e] if e in self.sources else (
                list(self.items[e][1].get('source_ids') or [self.items[e][1].get('source_id')]) if e in self.items else
                [self.consensus_rows[e]['source_id']] if e in self.consensus_rows else [])
            for s in srcs:
                while s in self.sources and self.sources[s].get('syndicated_from'):
                    s = self.sources[s]['syndicated_from']
                if s in self.sources and self.sources[s]['independent']:
                    roots.add(s)                        # a non-independent source without an original adds none
        return sorted(roots)

    def tier(self, tier, ids, competitors, where):
        """Check a source tier against its evidence; competitors only for tier competitor and named in the pack."""
        if tier not in TIERS:
            self.c.err(where, f'source_tier must be one of {", ".join(TIERS)}')
            return
        need = TIER_KINDS[tier]
        if need is not None and ids and not (self.kinds(ids) & need):
            self.c.err(where, f'tier {tier} needs evidence from a source of kind {", ".join(sorted(need))}')
        if tier == 'competitor':
            if not competitors:
                self.c.err(where, 'tier competitor needs the named competitors (tickers)')
            for t in competitors or []:
                if str(t).upper() not in self.competitors:
                    self.c.err(where, f'{t} is not a named competitor in the pack (competitors[] with why)')
        elif competitors:
            self.c.err(where, 'competitors belong to tier competitor only')

    def reading(self, x, where):
        if not self.c.obj(x, 'reading', where):
            return None
        ev, hyp = x.get('evidence_ids') or [], x.get('hypothesis') is True
        if bool(ev) == hyp:
            self.c.err(where, 'give evidence_ids, or label the reading hypothesis: true (exactly one)')
        return {'reading': x['reading'], 'evidence_ids': self.ids(ev, where, allow_empty=True), 'hypothesis': hyp}

    def done(self):
        if self.c.errors:
            raise ValueError('evidence pack invalid:\n- ' + '\n- '.join(self.c.errors))


# ---------------------------------------------------------------- guidance and its track record

def track_record(pack_obj):
    """Ratio actual / guided midpoint per period and their mean (revenue rows, or rows without a metric); None when empty."""
    rows = [t for t in pack_obj.raw.get('guidance_track') or [] if isinstance(t, dict) and t.get('metric', 'revenue') == 'revenue'
            and _num(t.get('guided_mid')) and t['guided_mid'] > 0 and _num(t.get('actual'))]
    if not rows:
        return None
    per = [{'period': t['period'], 'guided_mid': t['guided_mid'], 'actual': t['actual'],
            'ratio': round(t['actual'] / t['guided_mid'], 6)} for t in rows]
    ratio = sum(p['ratio'] for p in per) / len(per)
    return {'metric': 'revenue', 'ratio': round(ratio, 6), 'n': len(per), 'beats': sum(p['ratio'] > 1 for p in per),
            'misses': sum(p['ratio'] < 1 for p in per), 'periods': per,
            'rule': 'mean of actual / guided midpoint over the recorded periods; applied only where a driver says so'}


# ---------------------------------------------------------------- conversion

def _overrides(p, track):
    from .forecast import ECON_LEVERS, TERMINAL_KEYS
    from .drivers import RATIO_KEYS, ABSOLUTE_KEYS
    c, out, adjustments = p.c, [], []
    drivers = p.raw.get('drivers') or {}
    if not isinstance(drivers, dict):
        c.err('drivers', 'must be an object {lever: {...}}')
        return out, adjustments
    for lever, d in drivers.items():
        where = f'drivers.{lever}'
        if not c.obj(d, 'driver', where):
            continue
        raw = lever == 'growth' or lever in RATIO_KEYS or lever in ABSOLUTE_KEYS or (
            lever.startswith('terminal.') and lever.split('.', 1)[1] in TERMINAL_KEYS)
        if lever not in ECON_LEVERS and not raw:
            c.err(where, f'unknown lever; use {", ".join(ECON_LEVERS)}, a driver key or terminal.<key>')
            continue
        scen = [s for s in SCENARIOS + ('all',) if s in d]
        if not scen or ('all' in scen and len(scen) > 1):
            c.err(where, 'give bear/base/bull values, or one value for all')
            continue
        ev = p.ids(d['evidence_ids'], where)
        p.tier(d['source_tier'], d['evidence_ids'], d.get('competitors'), where)
        adjust = d.get('adjust_by_track_record')
        if d['source_tier'] == 'guidance' and lever in TRACK_LEVERS and not (
                isinstance(adjust, bool) or (isinstance(adjust, list) and set(adjust) <= set(scen))):
            c.err(where, 'a guidance-tier growth driver states adjust_by_track_record (true, false, or the scenarios to adjust)')
        if isinstance(adjust, list) and not set(adjust) <= set(scen):
            c.err(where, 'adjust_by_track_record lists scenarios this driver does not set')
        if adjust and (d['source_tier'] != 'guidance' or lever not in TRACK_LEVERS):
            c.err(where, 'adjust_by_track_record applies to guidance-tier growth_anchor or growth only')
        if adjust and not track:
            c.err(where, 'adjust_by_track_record needs guidance_track rows (guided midpoint and actual)')
        for s in scen:
            v = deepcopy(d[s])
            vals = v if isinstance(v, list) else [v]
            if not vals or not all(_num(x) for x in vals):
                c.err(f'{where}.{s}', 'value must be a finite number or a list of numbers')
                continue
            if isinstance(v, list) and lever in ECON_LEVERS:
                c.err(f'{where}.{s}', 'economic levers take one number')
                continue
            reason = d['reason']
            if track and (adjust is True or (isinstance(adjust, list) and s in adjust)):
                before = v[0] if isinstance(v, list) else v
                after = round((1 + before) * track['ratio'] - 1, 6)
                if isinstance(v, list):
                    v[0] = after
                else:
                    v = after
                adjustments.append({'lever': lever, 'scenario': s, 'before': before, 'after': after, 'ratio': track['ratio'],
                                    'rule': '(1 + guided growth) x track-record ratio - 1, guided year only'})
                reason = f'{reason} [guided growth {before:.4f} adjusted by the track-record ratio {track["ratio"]:.4f} -> {after:.4f}]'
            o = {'path': lever, 'scenario': s, 'value': v, 'reason': reason, 'evidence_ids': ev, 'source_tier': d['source_tier']}
            if d.get('competitors'):
                o['competitors'] = [str(t).upper() for t in d['competitors']]
            out.append(o)
    return out, adjustments


def _company_path(p):
    cp = p.raw.get('company_path')
    if cp is None:
        return None
    c = p.c
    if not c.obj(cp, 'company_path', 'company_path'):
        return None
    if cp['metric'] != 'eps' or cp['multiple'] != 'forward_pe':
        c.err('company_path', "metric must be 'eps' and multiple 'forward_pe'")
    out = {'metric': 'eps', 'multiple': 'forward_pe', 'scenarios': {}}
    sc = cp['scenarios'] if isinstance(cp['scenarios'], dict) else {}
    bad = sorted(set(sc) - set(SCENARIOS))
    if bad or not sc:
        c.err('company_path.scenarios', 'an object with bear, base and/or bull')
    for s in SCENARIOS:
        if s not in sc:
            continue
        x, where = sc[s], f'company_path.{s}'
        if not c.obj(x, 'path_scenario', where):
            continue
        m = x['multiple_path']
        if not isinstance(m, list) or len(m) != PATH_YEARS + 1 or not all(_num(v) and v > 0 for v in m):
            c.err(where, f'multiple_path needs {PATH_YEARS + 1} positive numbers (today and years 1-{PATH_YEARS})')
        if x['justification'] not in JUSTIFICATIONS:
            c.err(where, f'justification must be one of {", ".join(JUSTIFICATIONS)}')
        p.tier(x['source_tier'], x['evidence_ids'], x.get('competitors'), where)
        row = {k: deepcopy(x[k]) for k in ('multiple_path', 'justification', 'reason_he', 'source_tier')}
        row['evidence_ids'] = p.ids(x['evidence_ids'], where)
        if x.get('competitors'):
            row['competitors'] = [str(t).upper() for t in x['competitors']]
        out['scenarios'][s] = row
    if cp.get('own_history') is not None and c.obj(cp['own_history'], 'own_history', 'company_path.own_history'):
        h = cp['own_history']
        if not (all(_num(h[k]) for k in ('low', 'median', 'high')) and 0 < h['low'] <= h['median'] <= h['high']):
            c.err('company_path.own_history', '0 < low <= median <= high required')
        out['own_history'] = {**{k: h[k] for k in ('low', 'median', 'high', 'years')},
                              'evidence_ids': p.ids(h['evidence_ids'], 'company_path.own_history')}
    if cp.get('peers') is not None:
        peers = cp['peers'] if isinstance(cp['peers'], list) else []
        out['peers'] = []
        for i, x in enumerate(peers):
            where = f'company_path.peers[{i}]'
            if not c.obj(x, 'peer', where):
                continue
            t = str(x['ticker']).upper()
            if t not in p.competitors:
                c.err(where, f'{t} is not a named direct competitor in the pack (no other-company defaults)')
            if not (_num(x['forward_pe']) and x['forward_pe'] > 0 and _num(x['growth'])):
                c.err(where, 'a positive forward_pe and a growth number required')
            out['peers'].append({'ticker': t, 'forward_pe': x['forward_pe'], 'growth': x['growth'],
                                 'evidence_ids': p.ids(x['evidence_ids'], where)})
    return out


def _events(p):
    raw = p.raw.get('scenario_events')
    out = {}
    if not isinstance(raw, dict):
        p.c.err('scenario_events', 'an object {bear, base, bull: [events]}')
        return out
    bad = sorted(set(raw) - set(SCENARIOS))
    if bad:
        p.c.err('scenario_events', f'unknown scenarios {bad}')
    for s in SCENARIOS:
        rows = raw.get(s)
        if not isinstance(rows, list) or not rows:
            p.c.err(f'scenario_events.{s}', 'at least one event (what has to happen for this price; for the bear what breaks)')
            continue
        out[s] = []
        for i, e in enumerate(rows):
            where = f'scenario_events.{s}[{i}]'
            if not p.c.obj(e, 'event', where):
                continue
            ev, hyp = e.get('evidence_ids') or [], e.get('hypothesis') is True
            if bool(ev) == hyp:
                p.c.err(where, 'give evidence_ids, or label the event hypothesis: true (exactly one)')
            out[s].append({'event_he': e['event_he'], 'evidence_ids': p.ids(ev, where, allow_empty=True)})
    return out


def to_inputs(pack):
    """Evidence pack -> pipeline inputs.json (raises ValueError listing every problem). The FactBook must also load
    to_bundle(pack) (facts.add_qualitative_bundle) so the cited ids resolve: sources become 'web:<id>', items keep their id."""
    p = Pack(pack)
    c = p.c
    track = track_record(p)
    overrides, adjustments = _overrides(p, track)
    inputs = {}
    fc = {}
    if overrides:
        fc['overrides'] = overrides
    market = p.raw.get('market') or {}
    if not isinstance(market, dict) or set(market) - {'risk_free', 'equity_risk_premium'}:
        c.err('market', 'an object with risk_free and/or equity_risk_premium')
        market = {}
    for k, spec in market.items():
        if not c.obj(spec, 'rate', f'market.{k}'):
            continue
        s = p.sources.get(spec['source_id'])
        if s is None or s['kind'] not in TIER_KINDS['market']:
            c.err(f'market.{k}', 'source_id must be a pack source of kind price, macro or structured_data')
            continue
        p.ids([spec['source_id']], f'market.{k}')
        fc[k] = {'value': spec['value'], 'as_of': spec['as_of'], 'source_id': 'web:' + spec['source_id'],
                 'title': s['title'], 'url': s['url']}
    opts = p.raw.get('forecast_options') or {}
    if not isinstance(opts, dict) or set(opts) - set(FORECAST_OPTIONS):
        c.err('forecast_options', f'only {", ".join(FORECAST_OPTIONS)}')
    else:
        fc.update(deepcopy(opts))
    if fc:
        inputs['forecast'] = fc
    cp = _company_path(p)
    if cp:
        inputs['company_path'] = cp
    sheet = {'scenario_events': _events(p)}
    kr = p.raw.get('key_risk')
    if kr is not None and c.obj(kr, 'key_risk', 'key_risk'):
        sheet['key_risk'] = {'reading_he': kr['reading_he'], 'evidence_ids': p.ids(kr['evidence_ids'], 'key_risk')}
    pot_raw = p.raw.get('potential') if isinstance(p.raw.get('potential'), dict) else {}
    extra = sorted(set(pot_raw) - set(POTENTIAL))
    if extra:
        c.err('potential', f'unknown keys {extra}')
    potential = {}
    for k in POTENTIAL:
        if k not in pot_raw:
            c.err('potential', f'{k} required (evidenced, or labelled hypothesis)')
            continue
        potential[k] = p.reading(pot_raw[k], f'potential.{k}')
    for q, k in (('bottleneck', 'bottleneck_potential'), ('future', 'market_direction')):
        r = potential.get(k)
        if r and not r['hypothesis']:
            sheet[q] = {'reading_he': r['reading'], 'evidence_ids': r['evidence_ids']}
    inputs['analyst_sheet'] = sheet
    asm = p.raw.get('assessments')
    if asm is not None:
        from .model_card import DIMENSIONS, LEVELS
        if not isinstance(asm, dict):
            c.err('assessments', 'an object {dimension: {assessment, evidence_ids, note}}')
        else:
            card = {}
            for dim, a in asm.items():
                where = f'assessments.{dim}'
                if dim not in DIMENSIONS:
                    c.err(where, f'unknown dimension (one of {", ".join(DIMENSIONS)})')
                elif not isinstance(a, dict) or set(a) - {'assessment', 'evidence_ids', 'note'} or a.get('assessment') not in LEVELS:
                    c.err(where, f'{{assessment: {"|".join(LEVELS)}, evidence_ids, note}}')
                else:
                    card[dim] = {'assessment': a['assessment'], 'evidence_ids': p.ids(a.get('evidence_ids'), where),
                                 'note': a.get('note')}
            inputs['card'] = {'assessments': card}
    if p.raw.get('analyst') is not None:
        if not isinstance(p.raw['analyst'], str) or not p.raw['analyst'].strip():
            c.err('analyst', 'a name')
        else:
            inputs['analyst'] = p.raw['analyst']
    falsifiers = []
    for i, f in enumerate(c.lst(p.raw, 'falsifiers')):
        where = f'falsifiers[{i}]'
        if not c.obj(f, 'falsifier', where):
            continue
        if f['scenario'] not in SCENARIOS or f['direction'] not in ('below', 'above') or not _num(f['threshold']):
            c.err(where, 'scenario bear|base|bull, direction below|above and a numeric threshold')
        if not _day(f['by']) or (p.as_of and _day(f['by']) <= p.as_of):
            c.err(where, 'by must be a future date (when the threshold is checked)')
        falsifiers.append({**{k: f[k] for k in ('id', 'scenario', 'metric', 'threshold', 'direction', 'by')},
                           'evidence_ids': p.ids(f['evidence_ids'], where)})
    bottlenecks = []
    for i, b in enumerate(c.lst(p.raw, 'bottlenecks')):
        r = p.reading({k: v for k, v in b.items() if k != 'id'} if isinstance(b, dict) else b, f'bottlenecks[{i}]')
        if r and isinstance(b, dict):
            bottlenecks.append({'id': b.get('id'), **r})
    notes = [n for n in c.lst(p.raw, 'notes') if isinstance(n, str)]
    for o in overrides:
        if len(p.independent([e[4:] if e.startswith('web:') else e for e in o['evidence_ids']])) < 2 and o['source_tier'] != 'analyst':
            notes.append(f"driver {o['path']} ({o['scenario']}) rests on one independent source")
    if track is None and any(o['source_tier'] == 'guidance' for o in overrides):
        notes.append('guidance used without a recorded track record (guidance_track): its reliability is unknown')
    p.done()
    inputs['evidence_pack'] = {
        'pack_version': PACK_VERSION, 'pack_sha256': digest(pack), 'ticker': p.ticker, 'as_of': pack['as_of'],
        'identity': deepcopy(pack['identity']), 'sources': len(p.sources),
        'independent_sources': len(p.independent(list(p.sources))),
        'guidance_track_record': track, 'guidance_adjustments': adjustments,
        'competitors': [{'ticker': t, 'name': x.get('name'), 'why': x['why']} for t, x in p.competitors.items()],
        'consensus': [deepcopy(x) for x in p.consensus_rows.values()],
        'potential': potential, 'bottlenecks': bottlenecks, 'falsifiers': falsifiers, 'notes': notes,
        'meaning': 'provenance of these inputs (evidence_pack.to_inputs); evidence ids web:<id> are pack sources registered '
                   'by the evidence bundle, other ids are pack items'}
    return inputs


def _statement(key, x):
    if key == 'facts':
        s = f"{x['metric']} {x['period']}: {x['value']} {x['unit']}" + (' (derived)' if x.get('basis') == 'derived' else '')
        return s + (f" — {x['statement']}" if x.get('statement') else '')
    if key == 'kpis':
        return f"KPI {x['name']} {x['period']}: {x['value']} {x.get('unit') or ''}".strip()
    if key == 'segments':
        s = f"segment {x['name']} {x['period']}: revenue {x['revenue']} {x.get('unit') or ''}".strip()
        return s + (f", operating income {x['operating_income']}" if x.get('operating_income') is not None else '')
    if key == 'customers':
        return f"customer {x.get('name') or '(unnamed)'} {x['period']}: {x['share']:.1%} of revenue"
    return f"guidance {x['metric']} {x['period']}: {x['low']} to {x['high']} {x.get('unit') or ''} (issued {x['issued']})".strip()


def to_bundle(pack):
    """Evidence pack -> evidence bundle for facts.add_qualitative_bundle: the pack sources (registered as 'web:<id>'),
    facts / KPIs / segments / customers as reported items, guidance as guidance items and dated guidance rows, and the facts
    marked factbook as numeric bundle rows. Validates the pack first (to_inputs)."""
    to_inputs(pack)
    p = Pack(pack)
    srcs = []
    for sid, s in p.sources.items():
        row = {'id': sid, 'kind': BUNDLE_KIND.get(s['kind'], s['kind']), 'title': s['title'], 'url': s['url'],
               'published_at': s['date'], 'retrieval': s.get('retrieval') or (
                   'provider_normalized' if s['kind'] in ('structured_data', 'consensus') else
                   'market_feed' if s['kind'] == 'price' else 'search_excerpt')}
        if s.get('publisher'):
            row['publisher'] = s['publisher']
        srcs.append(row)
    items, guidance, rows = [], [], []
    for iid, (key, x) in p.items.items():
        ids = list(x.get('source_ids') or [x['source_id']])
        when = x['issued'] if key == 'guidance' else max(p.sources[s]['date'] for s in ids)
        items.append({'id': iid, 'topic': {'facts': 'fact', 'kpis': 'kpi', 'segments': 'segment', 'customers': 'customer',
                                           'guidance': 'guidance'}[key] + ':' + str(x.get('metric') or x.get('name') or ''),
                      'statement': _statement(key, x), 'kind': 'guidance' if key == 'guidance' else 'reported',
                      'source_ids': ids, 'as_of': when})
        if key == 'guidance':
            guidance.append({'metric': x['metric'], 'period': x['period'], 'low': x['low'], 'high': x['high'],
                             'issued_at': x['issued'], 'source_ids': ids})
        if key == 'facts' and x.get('factbook'):
            rows.append({k: x[k] for k in ('metric', 'period', 'period_end', 'value') if k in x} | {'source_ids': ids})
    return {'retrieved_at': pack['as_of'], 'as_of': pack['as_of'], 'sources': srcs, 'items': items, 'guidance': guidance,
            'quarterly_facts': rows}
