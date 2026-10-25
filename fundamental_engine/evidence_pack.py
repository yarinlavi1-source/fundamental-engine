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
  drivers {lever: {bear|base|bull|all: value, source_tier (one tier, or one per scenario), evidence_ids, reason, competitors,
          adjust_by_track_record, period (growth_anchor / growth: the next fiscal year 'FY<year>'), deviation}}, and
          drivers.current_year_revenue {source_tier guidance, evidence_ids, reason, adjust_by_track_record, remainder}: the
          fiscal year in progress (forecast inputs.current_year_revenue)
  scenario_events {bear|base|bull: [{event_he, evidence_ids | hypothesis: true}]}  (all three scenarios)
  company_path   inputs.company_path (contract: next-fiscal-year EPS x forward P/E per scenario, today and years 1-5)
  key_risk {reading_he, evidence_ids}; falsifiers [{id, scenario, metric, threshold, direction below|above, by,
  evidence_ids}]; assessments {model_card dimension: {assessment, evidence_ids, note}}; market {risk_free,
  equity_risk_premium: {value, as_of, source_id}}; forecast_options {horizon_years, financing, cyclical}; analyst; notes

Rules enforced: every evidence id resolves to a pack source or item; consensus is never evidence of a fact (facts, KPIs,
segments, customers and the guidance record may not cite a consensus source) and counts only for tier consensus;
guidance comes from issuer documents; a source tier must be backed by a source of a matching kind (guidance/contracted:
issuer documents, and tier guidance a guidance item; consensus: a consensus source; own_history: issuer documents or
structured data; market: price/macro/structured data; competitor: a source of the named competitor in the pack); no
source dated after as_of; a cited source must be citable at as_of (a document published at least facts.LAG_DAYS before;
a market feed, kind price or macro, on its date; an item when all its sources are and, for guidance, its issue date).
Guidance and its track record: the ratio actual / guided midpoint is always recorded. The fiscal year in progress
(current_year_revenue) is its guided midpoint, the ratio applied to the unreported remainder only: midpoint + (ratio - 1) x
the remainder's guided midpoint. growth_anchor is the growth of the NEXT fiscal year (its period); a guidance- or
consensus-tier value sits inside the cited item's range for that year unless the deviation is stated; a guidance-tier
growth driver states adjust_by_track_record (true = every guidance-tier scenario it sets, or a list, or false), and where
it applies the guided year's growth is adjusted explicitly: (1 + g) x ratio - 1 (recorded before and after).
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
GROWTH_LEVERS = ('growth_anchor', 'growth')          # their first value is the growth of the fiscal year AFTER the one in progress
CURRENT_YEAR = 'current_year_revenue'                 # the fiscal year in progress (forecast inputs.current_year_revenue)
FORECAST_OPTIONS = ('horizon_years', 'financing', 'cyclical')

TOP = {'pack_version', 'ticker', 'as_of', 'identity', 'sources', 'facts', 'guidance', 'guidance_track', 'consensus',
       'kpis', 'segments', 'customers', 'competitors', 'bottlenecks', 'potential', 'drivers', 'scenario_events',
       'company_path', 'key_risk', 'falsifiers', 'assessments', 'market', 'forecast_options', 'analyst', 'notes'}
REQUIRED = ('ticker', 'as_of', 'identity', 'sources', 'potential', 'scenario_events', 'company_path')
KEYS = {
    'identity': ({'name', 'currency', 'exchange', 'security', 'fiscal_year_end', 'cik', 'type', 'evidence_ids'},
                 ('name', 'currency', 'fiscal_year_end')),
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
                'adjust_by_track_record', 'period', 'deviation', 'remainder'}, ('source_tier', 'evidence_ids', 'reason')),
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
        self.fye = None
        if pack.get('identity') is not None and not isinstance(pack['identity'], dict):
            c.err('identity', 'must be an object {name, currency, fiscal_year_end, ...}')
        elif isinstance(pack.get('identity'), dict) and c.obj(pack['identity'], 'identity', 'identity'):
            self.ids(pack['identity'].get('evidence_ids') or [], 'identity', allow_empty=True)
            fye = str(pack['identity']['fiscal_year_end'])
            try:
                self.fye = (int(fye[:2]), int(fye[3:5]))
                date(2000, *self.fye)
                assert len(fye) == 5 and fye[2] == '-'
            except (ValueError, AssertionError):
                self.fye = None
                c.err('identity.fiscal_year_end', 'must be MM-DD (the fiscal year end month and day)')

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
        """A source is citable at as_of when published at least facts.LAG_DAYS before (the bundle registers only those); a market
        feed (price, macro, or retrieved as market_feed) quotes its own date and is citable on it (facts.MARKET_FEED_KINDS)."""
        from .facts import LAG_DAYS, MARKET_FEED_KINDS
        s = self.sources[sid]
        d = _day(s['date'])
        lag = 0 if s['kind'] in MARKET_FEED_KINDS or s.get('retrieval') == 'market_feed' else LAG_DAYS
        return bool(d and self.as_of and d + timedelta(days=lag) <= self.as_of)

    def item_citable(self, iid):
        """An item is citable when every source it cites is (the bundle dates it by the latest one) and, for guidance, its issue
        date is at least facts.LAG_DAYS before as_of."""
        from .facts import LAG_DAYS
        key, x = self.items[iid]
        srcs = list(x.get('source_ids') or ([x['source_id']] if x.get('source_id') else []))
        if not srcs or any(s not in self.sources or not self.citable(s) for s in srcs):
            return False
        if key == 'guidance':
            d = _day(x.get('issued'))
            return bool(d and self.as_of and d + timedelta(days=LAG_DAYS) <= self.as_of)
        return True

    def fiscal_year(self, offset=0):
        """(label, end date) of the fiscal year in progress at as_of (offset 0: its end is the first fiscal year end after
        as_of, as forecast._calendar dates it) or offset years later; None without a fiscal_year_end."""
        if not self.fye or not self.as_of:
            return None
        m, d = self.fye

        def end(y):
            try:
                return date(y, m, d)
            except ValueError:
                return date(y, m, 28)
        y = self.as_of.year - 1
        while end(y) <= self.as_of:
            y += 1
        return f'FY{y + offset}', end(y + offset)

    def is_consensus(self, e):
        return e in self.consensus_rows or (e in self.sources and self.sources[e]['kind'] == 'consensus')

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
            if any(k in x and not _num(x[k]) for k in ('low', 'high')) or (_num(x.get('low')) and _num(x.get('high'))
                                                                           and x['low'] > x['high']):
                c.err(where, 'low and high must be numbers with low <= high')
            if 'n' in x and (isinstance(x['n'], bool) or not isinstance(x['n'], int) or x['n'] < 1):
                c.err(where, 'n (the number of estimates) must be a positive integer')
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
                if not self.item_citable(e):
                    self.c.err(where, f'item {e} is not citable at as_of (a source it cites, or its issue date, is less than '
                                      'facts.LAG_DAYS before)')
                out.append(e)
            elif e in self.consensus_rows:
                sid = self.consensus_rows[e]['source_id']
                if sid in self.sources and not self.citable(sid):
                    self.c.err(where, f'consensus {e}: source {sid} is not citable at as_of')
                out.append('web:' + sid)
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

    def independent(self, ids, consensus=True):
        """Independent sources behind evidence ids: syndicated copies collapse into the source they copy; a source marked not
        independent (e.g. structured data re-keying the filing) adds none. consensus=False leaves consensus out: an expectation is
        never an independent source for another tier."""
        roots = set()
        for e in ids or []:
            if not consensus and self.is_consensus(e):
                continue
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
        """Check a source tier against its evidence; competitors only for tier competitor and named in the pack. Consensus counts
        only for tier consensus (an expectation, never evidence for another tier); tier guidance needs a guidance item (the
        company's own guided number); tier competitor needs a source of a named competitor."""
        if tier not in TIERS:
            self.c.err(where, f'source_tier must be one of {", ".join(TIERS)}')
            return
        ids = [e for e in ids or [] if isinstance(e, str)]
        own = ids if tier == 'consensus' else [e for e in ids if not self.is_consensus(e)]
        if ids and not own:
            self.c.err(where, f'tier {tier} cannot rest on consensus alone (an expectation, not evidence for this tier)')
        need = TIER_KINDS[tier]
        if need is not None and own and not (self.kinds(own) & need):
            self.c.err(where, f'tier {tier} needs evidence from a source of kind {", ".join(sorted(need))}')
        if tier == 'guidance' and ids and not any(e in self.items and self.items[e][0] == 'guidance' for e in ids):
            self.c.err(where, 'tier guidance needs a guidance item (guidance[] id: the company\'s own guided number)')
        if tier == 'competitor':
            if not competitors:
                self.c.err(where, 'tier competitor needs the named competitors (tickers)')
            for t in competitors or []:
                if str(t).upper() not in self.competitors:
                    self.c.err(where, f'{t} is not a named competitor in the pack (competitors[] with why)')
            theirs = {s for t in competitors or [] for s in (self.competitors.get(str(t).upper()) or {}).get('source_ids') or []}
            if competitors and ids and not set(ids) & theirs:
                self.c.err(where, 'tier competitor needs a source of the named competitor(s) (competitors[].source_ids)')
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

def _mid(x):
    return (x['low'] + x['high']) / 2


def _guidance_items(p, ids, period):
    return [p.items[e][1] for e in ids if e in p.items and p.items[e][0] == 'guidance'
            and p.items[e][1].get('metric', 'revenue') == 'revenue' and p.items[e][1]['period'] == period]


def _current_year(p, track, adjustments):
    """drivers.current_year_revenue -> forecast inputs.current_year_revenue: the fiscal year in progress from the company's own
    guidance (its midpoint), the track-record ratio applied ONLY to the guided remainder not yet reported:
    value = guided fiscal-year midpoint + (ratio - 1) x the remainder's guided midpoint. None when the pack does not set it."""
    d = (p.raw.get('drivers') or {}).get(CURRENT_YEAR) if isinstance(p.raw.get('drivers'), dict) else None
    if d is None:
        return None
    c, where = p.c, f'drivers.{CURRENT_YEAR}'
    if not c.obj(d, 'driver', where):
        return None
    fy = p.fiscal_year(0)
    if any(k in d for k in SCENARIOS + ('all', 'period', 'competitors', 'deviation')):
        c.err(where, 'the fiscal year in progress has one revenue for every scenario, computed from the guidance: give source_tier, '
                     'evidence_ids, reason, adjust_by_track_record and the remainder item (no scenario values, period or competitors)')
        return None
    if d['source_tier'] != 'guidance':
        c.err(where, 'source_tier guidance (the company\'s guided fiscal year); consensus for the year in progress reaches the engine '
                     'through its own anchor')
        return None
    if fy is None:
        c.err(where, 'needs identity.fiscal_year_end')
        return None
    ev = p.ids(d['evidence_ids'], where)
    p.tier('guidance', d['evidence_ids'], None, where)
    g = _guidance_items(p, d['evidence_ids'], fy[0])
    if len(g) != 1:
        c.err(where, f'cite exactly one revenue guidance item for the fiscal year in progress ({fy[0]})')
        return None
    before, value, rem = _mid(g[0]), _mid(g[0]), None
    adjust = d.get('adjust_by_track_record')
    if not isinstance(adjust, bool):
        c.err(where, 'states adjust_by_track_record (true or false)')
        return None
    if adjust:
        rid = d.get('remainder')
        r = p.items.get(rid) if isinstance(rid, str) else None
        if not r or r[0] != 'guidance' or r[1].get('metric', 'revenue') != 'revenue' or r[1]['period'] == fy[0] \
                or fy[0][2:] not in str(r[1]['period']) or rid not in d['evidence_ids']:
            c.err(where, f'adjust_by_track_record applies to the unreported remainder only: remainder must be a cited revenue '
                         f'guidance item for the rest of {fy[0]} (e.g. its last quarter)')
            return None
        if not track:
            c.err(where, 'adjust_by_track_record needs guidance_track rows (guided midpoint and actual)')
            return None
        rem = _mid(r[1])
        if not 0 < rem < before:
            c.err(where, 'the remainder guidance must be a positive part of the fiscal-year guidance')
            return None
        value = round(before + (track['ratio'] - 1) * rem, 2)
        adjustments.append({'lever': CURRENT_YEAR, 'scenario': 'all', 'before': before, 'after': value, 'ratio': track['ratio'],
                            'remainder': rem, 'rule': 'guided fiscal-year midpoint + (track-record ratio - 1) x the guided midpoint of '
                                                      'the remainder not yet reported (the reported quarters are facts, not adjusted)'})
    basis = (f"{fy[0]} revenue: company guidance midpoint {before:,.0f} ({g[0]['low']:,.0f}-{g[0]['high']:,.0f}, issued {g[0]['issued']})"
             + (f" + (track-record ratio {track['ratio']:.4f} - 1) x the {r[1]['period']} guided midpoint {rem:,.0f} = {value:,.0f}"
                if rem else '') + f"; {d['reason']}")
    return {'value': value, 'fiscal_year_end': fy[1].isoformat(), 'basis': basis, 'evidence_ids': ev, 'source_tier': 'guidance'}


def _prev_revenue(p, cur):
    """Revenue of the fiscal year in progress, to turn a guided or consensus revenue for the next one into growth: the
    current_year_revenue result, else that year's revenue guidance midpoint, else a revenue fact for it; None when unknown."""
    if cur:
        return cur['value']
    fy = p.fiscal_year(0)
    if fy is None:
        return None
    for key, x in p.items.values():
        if key == 'guidance' and x.get('metric', 'revenue') == 'revenue' and x['period'] == fy[0]:
            return _mid(x)
    for key, x in p.items.values():
        if key == 'facts' and x['metric'] == 'revenue' and x['period'] == fy[0] and _num(x['value']):
            return x['value']
    return None


def _range_check(p, d, s, tier, value, prev, where):
    """A guidance- or consensus-tier growth value against the cited item for its fiscal year (d['period']): within the guided
    low..high (or the consensus low..high, else its mean +-1pp) unless the pack states the deviation."""
    if tier not in ('guidance', 'consensus'):
        return
    period = d.get('period')
    if tier == 'guidance':
        items = _guidance_items(p, d['evidence_ids'], period)
        what = f'a revenue guidance item for {period}'
    else:
        items = [p.consensus_rows[e] for e in d['evidence_ids'] if e in p.consensus_rows
                 and p.consensus_rows[e]['metric'] == 'revenue' and p.consensus_rows[e]['period'] == period]
        what = f'a revenue consensus row for {period}'
    if not items:
        p.c.err(where, f'tier {tier} growth for {period} needs {what} among its evidence_ids')
        return
    if not prev:
        p.c.err(where, f'tier {tier} growth: the revenue of the fiscal year in progress is unknown (drivers.{CURRENT_YEAR}, its '
                       'guidance or a revenue fact), so the cited range cannot be checked')
        return
    x = items[0]
    if _num(x.get('low')) and _num(x.get('high')):
        lo, hi = x['low'] / prev - 1 - .005, x['high'] / prev - 1 + .005
    else:
        lo, hi = x['value'] / prev - 1 - .01, x['value'] / prev - 1 + .01
    if not lo <= value <= hi and not (isinstance(d.get('deviation'), str) and d['deviation'].strip()):
        p.c.err(where, f'{s} growth {value:.4f} is outside the cited {tier} range {lo:.4f}..{hi:.4f} for {period} (over the '
                       f'fiscal year in progress {prev:,.0f}); state why in deviation, or use another tier')


def _overrides(p, track, cur=None):
    from .forecast import ECON_LEVERS, TERMINAL_KEYS
    from .drivers import RATIO_KEYS, ABSOLUTE_KEYS
    c, out, adjustments = p.c, [], []
    drivers = p.raw.get('drivers') or {}
    if not isinstance(drivers, dict):
        c.err('drivers', 'must be an object {lever: {...}}')
        return out, adjustments
    prev = _prev_revenue(p, cur)
    for lever, d in drivers.items():
        where = f'drivers.{lever}'
        if lever == CURRENT_YEAR:
            continue                                # _current_year
        if not c.obj(d, 'driver', where):
            continue
        raw = lever == 'growth' or lever in RATIO_KEYS or lever in ABSOLUTE_KEYS or (
            lever.startswith('terminal.') and lever.split('.', 1)[1] in TERMINAL_KEYS)
        if lever not in ECON_LEVERS and not raw:
            c.err(where, f'unknown lever; use {", ".join(ECON_LEVERS)}, {CURRENT_YEAR}, a driver key or terminal.<key>')
            continue
        scen = [s for s in SCENARIOS + ('all',) if s in d]
        if not scen or ('all' in scen and len(scen) > 1):
            c.err(where, 'give bear/base/bull values, or one value for all')
            continue
        st = d['source_tier']
        if isinstance(st, dict):
            if set(st) != set(scen):
                c.err(where, f'source_tier per scenario must name exactly the scenarios this driver sets ({", ".join(scen)})')
                continue
            tiers = dict(st)
        else:
            tiers = {s: st for s in scen}
        ev = p.ids(d['evidence_ids'], where)
        for tr in dict.fromkeys(tiers.values()):
            p.tier(tr, d['evidence_ids'], d.get('competitors') if tr == 'competitor' else None, where)
        if d.get('competitors') and 'competitor' not in tiers.values():
            c.err(where, 'competitors belong to tier competitor only')
        if lever in GROWTH_LEVERS:
            nxt = p.fiscal_year(1)
            if not isinstance(d.get('period'), str):
                c.err(where, f'period required: the fiscal year its (first) growth is for ({nxt[0] if nxt else "FY<year>"}: the first full '
                             f'fiscal year after the one in progress; that one is drivers.{CURRENT_YEAR})')
            elif nxt and d['period'] != nxt[0]:
                cur_fy = p.fiscal_year(0)[0]
                c.err(where, f"period {d['period']}: {lever} is the growth of {nxt[0]}, the first full fiscal year after the one in "
                             f"progress ({cur_fy}); guidance for {cur_fy} sets drivers.{CURRENT_YEAR}, never the next year's growth")
        elif d.get('period') is not None:
            c.err(where, 'period applies to growth_anchor and growth only')
        guided = [s for s in scen if tiers[s] == 'guidance']
        adjust = d.get('adjust_by_track_record')
        if guided and lever in TRACK_LEVERS and not (
                isinstance(adjust, bool) or (isinstance(adjust, list) and set(adjust) <= set(scen))):
            c.err(where, 'a guidance-tier growth driver states adjust_by_track_record (true, false, or the scenarios to adjust)')
        if isinstance(adjust, list) and not set(adjust) <= set(scen):
            c.err(where, 'adjust_by_track_record lists scenarios this driver does not set')
        adj_s = [s for s in scen if adjust is True or (isinstance(adjust, list) and s in adjust)]
        if adj_s and (any(tiers[s] != 'guidance' for s in adj_s) or lever not in TRACK_LEVERS):
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
            if lever in GROWTH_LEVERS and isinstance(d.get('period'), str):
                _range_check(p, d, s, tiers[s], vals[0], prev, f'{where}.{s}')
            reason = d['reason'] + (f" [deviation from the cited range: {d['deviation']}]" if d.get('deviation') else '')
            if track and s in adj_s and tiers[s] == 'guidance':
                before = v[0] if isinstance(v, list) else v
                after = round((1 + before) * track['ratio'] - 1, 6)
                if isinstance(v, list):
                    v[0] = after
                else:
                    v = after
                adjustments.append({'lever': lever, 'scenario': s, 'before': before, 'after': after, 'ratio': track['ratio'],
                                    'rule': '(1 + guided growth) x track-record ratio - 1, the guided year only ('
                                            + str(d.get('period')) + ')'})
                reason = f'{reason} [guided growth {before:.4f} adjusted by the track-record ratio {track["ratio"]:.4f} -> {after:.4f}]'
            o = {'path': lever, 'scenario': s, 'value': v, 'reason': reason, 'evidence_ids': ev, 'source_tier': tiers[s]}
            if d.get('competitors') and tiers[s] == 'competitor':
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


def _forecast_options(p, opts):
    """forecast_options checked against forecast.build's own rules (so a passing pack never fails the forecast stage): horizon an
    integer 1-30, financing one of forecast.FINANCING_MODES, cyclical {treatment, reason, evidence_ids} with its ids mapped
    (Pack.ids) like every other block."""
    from .forecast import FINANCING_MODES, CYCLE_TREATMENTS
    c, out = p.c, {}
    if 'horizon_years' in opts:
        h = opts['horizon_years']
        if isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= 30:
            c.err('forecast_options.horizon_years', 'an integer from 1 to 30')
        else:
            out['horizon_years'] = h
    if 'financing' in opts:
        if opts['financing'] not in FINANCING_MODES:
            c.err('forecast_options.financing', f'one of {", ".join(FINANCING_MODES)}')
        else:
            out['financing'] = opts['financing']
    if 'cyclical' in opts:
        cy = opts['cyclical']
        if not isinstance(cy, dict) or set(cy) - {'treatment', 'reason', 'evidence_ids'}:
            c.err('forecast_options.cyclical', 'an object {treatment, reason, evidence_ids}')
        elif cy.get('treatment') not in CYCLE_TREATMENTS or not isinstance(cy.get('reason'), str) or not cy['reason'].strip():
            c.err('forecast_options.cyclical', f'treatment one of {", ".join(CYCLE_TREATMENTS)} and a written reason')
        else:
            out['cyclical'] = {'treatment': cy['treatment'], 'reason': cy['reason'],
                               'evidence_ids': p.ids(cy.get('evidence_ids'), 'forecast_options.cyclical')}
    return out


def to_inputs(pack):
    """Evidence pack -> pipeline inputs.json (raises ValueError listing every problem). The FactBook must also load
    to_bundle(pack) (facts.add_qualitative_bundle) so the cited ids resolve: sources become 'web:<id>', items keep their id."""
    p = Pack(pack)
    c = p.c
    track = track_record(p)
    adjustments = []
    cur = _current_year(p, track, adjustments)
    overrides, adj = _overrides(p, track, cur)
    adjustments += adj
    inputs = {}
    fc = {}
    if overrides:
        fc['overrides'] = overrides
    if cur:
        fc[CURRENT_YEAR] = cur
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
        if not (_num(spec['value']) and -.05 <= spec['value'] <= .3):
            c.err(f'market.{k}', 'value must be a rate between -0.05 and 0.30 (a decimal, e.g. 0.042)')
            continue
        if not _day(spec['as_of']) or (p.as_of and _day(spec['as_of']) > p.as_of):
            c.err(f'market.{k}', 'as_of must be a date on or before the pack as_of (no look-ahead)')
            continue
        fc[k] = {'value': spec['value'], 'as_of': spec['as_of'], 'source_id': 'web:' + spec['source_id'],
                 'title': s['title'], 'url': s['url']}
    opts = p.raw.get('forecast_options') or {}
    if not isinstance(opts, dict) or set(opts) - set(FORECAST_OPTIONS):
        c.err('forecast_options', f'only {", ".join(FORECAST_OPTIONS)}')
    else:
        fc.update(_forecast_options(p, opts))
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
        if not c.obj(b, 'bottleneck', f'bottlenecks[{i}]'):
            continue
        r = p.reading({k: v for k, v in b.items() if k != 'id'}, f'bottlenecks[{i}]')
        if r:
            bottlenecks.append({'id': b['id'], **r})
    notes = []
    for i, n in enumerate(c.lst(p.raw, 'notes')):
        if isinstance(n, str) and n.strip():
            notes.append(n)
        else:
            c.err(f'notes[{i}]', 'a note is a non-empty text')
    raw_ids = {(d_lever, s): d.get('evidence_ids') or [] for d_lever, d in (p.raw.get('drivers') or {}).items()
               if isinstance(d, dict) for s in SCENARIOS + ('all',)} if isinstance(p.raw.get('drivers'), dict) else {}
    for o in overrides:
        ids = raw_ids.get((o['path'], o['scenario'])) or []
        if o['source_tier'] != 'analyst' and len(p.independent(ids, consensus=o['source_tier'] == 'consensus')) < 2:
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


# ---------------------------------------------------------------- merge (the ticker workflow's Value step)

LIST_KEYS = ('sources', 'facts', 'guidance', 'guidance_track', 'consensus', 'kpis', 'segments', 'customers', 'competitors',
             'bottlenecks', 'notes')
POSITION_KEYS = ('potential', 'scenario_events', 'drivers', 'company_path', 'key_risk', 'falsifiers', 'assessments',
                 'forecast_options', 'analyst')


def _remap(x, ids):
    """Rename source ids everywhere in a structure (source_id, source_ids, evidence_ids, syndicated_from, remainder)."""
    if isinstance(x, list):
        return [_remap(v, ids) for v in x]
    if not isinstance(x, dict):
        return x
    out = {}
    for k, v in x.items():
        if k in ('source_ids', 'evidence_ids') and isinstance(v, list):
            out[k] = list(dict.fromkeys(ids.get(e, e) if isinstance(e, str) else e for e in v))
        elif k in ('source_id', 'syndicated_from') and isinstance(v, str):
            out[k] = ids.get(v, v)
        else:
            out[k] = _remap(v, ids)
    return out


def merge_fragments(ticker, as_of, identity, fragments, position, market=None):
    """The ticker workflow's gather fragments and position file -> one evidence pack, deterministically (no judgement): sources
    deduplicated by URL (the first id kept; later ids renamed everywhere), the lists concatenated in fragment order, competitors
    merged by ticker (their source ids united), and the forward judgement (potential, scenario_events, drivers, company_path,
    key_risk, falsifiers, assessments, forecast_options) taken from position. A duplicate item id is an error, never resolved by
    guessing. -> (pack, report {'renamed_sources', 'duplicate_ids'}). to_inputs then validates the result."""
    by_url, ids = {}, {}
    for fr in fragments:
        for s in fr.get('sources') or []:
            if isinstance(s, dict) and s.get('url') and s.get('id'):
                first = by_url.setdefault(s['url'], s['id'])
                if first != s['id']:
                    ids[s['id']] = first
    pack = {'pack_version': PACK_VERSION, 'ticker': str(ticker).upper(), 'as_of': as_of, 'identity': deepcopy(identity)}
    for k in LIST_KEYS:
        pack[k] = []
    seen, dup, comp = set(), [], {}
    for fr in fragments:
        fr = _remap(fr, ids)
        for k in LIST_KEYS:
            for x in fr.get(k) or []:
                if k == 'sources' and isinstance(x, dict) and x.get('id') in ids:
                    continue                                    # a second copy of a document already registered
                if k == 'competitors' and isinstance(x, dict) and x.get('ticker'):
                    t = str(x['ticker']).upper()
                    if t in comp:
                        comp[t]['source_ids'] = list(dict.fromkeys((comp[t].get('source_ids') or []) + (x.get('source_ids') or [])))
                        for kk in ('name', 'why', 'metrics'):
                            comp[t].setdefault(kk, x.get(kk))
                        continue
                    comp[t] = deepcopy(x)
                    pack[k].append(comp[t])
                    continue
                if isinstance(x, dict) and x.get('id') and k != 'guidance_track':
                    if x['id'] in seen:
                        dup.append(x['id'])
                    seen.add(x['id'])
                pack[k].append(deepcopy(x))
        if fr.get('market'):
            market = {**(market or {}), **fr['market']}
    pos = _remap(position or {}, ids)
    for k in POSITION_KEYS:
        if pos.get(k) is not None:
            pack[k] = deepcopy(pos[k])
    if market:
        pack['market'] = market
    pack = {k: v for k, v in pack.items() if v not in ([], None)}
    if dup:
        raise ValueError(f'evidence pack merge: duplicate ids across fragments {sorted(set(dup))} (prefix ids by lane)')
    return pack, {'renamed_sources': ids, 'duplicate_ids': dup}
