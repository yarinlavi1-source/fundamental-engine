"""Stage 1 data validation: may a price conclusion rest on these numbers?

Why: the engine's costliest errors were data errors, not model errors. NBIS used a stale share count
and missed converts and pre-funded warrants; ASTS counted net cash twice; NOW's 1:5 split showed up as
394% "dilution"; APH debt existed only as a fair-value tag; MSFT/META net cash read +36.5B in one
source and -52B in another because one side counted leases and long-term investments. Definitions
therefore matter as much as numbers. Every critical field is resolved here with an explicit
definition, a dated source, the checks that confirm it and a status:

  verified       two independent confirmations: independent source families agree within tolerance, or a
                 filing-derived value passes an identity / continuity check that involves it. A source family is the
                 underlying document set: every copy of the issuer's own reports (SEC XBRL, FMP as-reported, the
                 issuer's releases and filings read directly, the vendor float that reads the filing cover) is one
                 family; each independently normalizing vendor and each primary non-issuer document provider is its
                 own; one document typed twice confirms nothing. Only a 'reported' value is ever upgraded
  reported       one filing-derived or issuer-document value, not yet confirmed
  single_source  vendor-normalized or secondary (press, analysis) only, or an unresolved definitional choice;
                 a secondary source never confirms nor contradicts any value (recorded as a note)
  conflict       sources disagree beyond tolerance (two transcriptions of one filing included), or a like-for-like
                 identity fails. A release revised by the later filing is an 'issuer_revision' check that limits the field
  missing        not available (or an instrument exists without its terms; a TTM older than twice its age limit)
  stale          too old for the as_of date (newer reports exist but are absent)

conflict / missing on shares, cash, debt, revenue_ttm or cfo_ttm blocks a price conclusion; the rest
limit confidence. Nothing is fetched and no fact published after fb['as_of'] is read: every lookup
goes through facts.candidates. docs/MODULE_CONTRACTS.md (dataqa.validate) is the output contract.
"""
from datetime import date, timedelta
from math import log
import re

from . import capital
from . import facts as F

TOL = {'shares_outstanding': .005, 'shares_outstanding_cover': .005, 'shares_outstanding_vendor': .005,
       'cash': .01, 'short_term_investments': .01, 'cash_incl_restricted': .01, 'restricted_cash': .01,
       'long_term_investments': .02, 'debt': .01, 'long_term_debt': .01, 'long_term_debt_tagged': .01,
       'current_portion_ltd': .01, 'short_term_borrowings': .01, 'debt_total_vendor': .01,
       'revenue': .005, 'operating_income': .005, 'cfo': .01, 'capex': .01, 'sbc': .02}
DEFAULT_TOL = .01
IDENTITY_TOL = .005           # statement identities are exact up to rounding
ROLLFORWARD_TOL = .025        # debt also moves with discount amortization, hedge fair value and FX: wider than a source tolerance
MAX_AGE = {'shares': 120, 'balance_sheet': 200, 'ttm': 200, 'annual': 456}
SPLIT_FACTORS = (1.5, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50)
SPLIT_BAND = .05
TTM_FIELDS = (('revenue_ttm', 'revenue'), ('operating_income_ttm', 'operating_income'), ('cfo_ttm', 'cfo'),
              ('capex_ttm', 'capex'), ('sbc_ttm', 'sbc'))
CRITICAL = ('shares_outstanding', 'cash_and_liquid_investments', 'investments', 'debt', 'convertibles',
            'post_period_events') + tuple(n for n, _ in TTM_FIELDS)
BLOCKING = {'shares_outstanding', 'cash_and_liquid_investments', 'debt', 'revenue_ttm', 'cfo_ttm'}
RANK = {'conflict': 0, 'missing': 1, 'stale': 2, 'single_source': 3, 'reported': 4, 'verified': 5}
FILING_KINDS = F.REPORTED_KINDS                  # filing_derived, issuer_document (legacy 'document')
BS_TAGS = ('total_assets', 'total_liabilities', 'liabilities_and_equity', 'current_assets', 'equity')
DEBT_PARTS = ('long_term_debt', 'long_term_debt_tagged', 'current_portion_ltd', 'short_term_borrowings')
BS_LINES = BS_TAGS + ('cash', 'cash_incl_restricted', 'short_term_investments', 'long_term_investments',
                      'deferred_revenue_current', 'deferred_revenue_noncurrent') + DEBT_PARTS
HYPOTHESES = ('noncurrent+current', 'tagged_total', 'tagged_plus_current')
SHARE_ROUTES = (('shares_outstanding_cover', 'cover'), ('shares_outstanding', 'balance_sheet'),
                ('shares_outstanding_vendor', 'vendor_float'))
ROUTE_PREF = {'cover': 0, 'balance_sheet': 1, 'capital_structure': 2, 'vendor_float': 3}
POST_TOPICS = {'post_period_event', 'post_period_events', 'subsequent_event', 'subsequent_events'}
SPLIT_TOPICS = {'stock_split', 'split', 'reverse_split', 'share_split'}
SPLIT_ISSUANCE_BAND = (.8, 1.25)  # a split identified by evidence: the residual count change (issuance/buybacks) kept
WAVG_SPLIT_TOL = .04              # weighted-average shares restated by the full count ratio (a split is retroactive): within 4%
SPLIT_ALT_WINDOW_DAYS = 45        # ...counts as split evidence only when an issuance would have to fall this early in the year
PRICE_JUMP_TOL = .15              # an as-traded close-to-close move within 15% (log) of 1/factor marks the split bar
PRICE_BAR_MAX_GAP = 40            # closes further apart than this cannot locate a split bar
SPLIT_RESTATED_DAYS = 92          # a split this soon after a count filed after the split: that count is already restated
CONVERT_TOPICS = {'convertible', 'convertibles', 'convertible_notes'}
WARRANT_TOPICS = {'warrant', 'warrants', 'prefunded_warrant', 'pre_funded_warrant', 'prefunded_warrants'}
DEFINITIONS = {
    'shares_outstanding': 'latest point-in-time common share count usable at as_of (cover page, balance sheet, vendor or '
                          'capital-structure common lines); never a weighted average',
    'cash': 'cash and cash equivalents at carrying value; restricted cash excluded and shown separately',
    'short_term_investments': 'current marketable securities; spendable, added to cash once',
    'long_term_investments': 'noncurrent investments: not spendable cash, shown separately and excluded from net_cash',
    'debt': 'borrowings at carrying value: long-term debt including current maturities plus short-term borrowings; '
            'operating leases excluded (lease_liabilities)',
    'net_cash': 'cash + short_term_investments - debt (leases, restricted cash and long-term investments excluded; '
                'net_cash_incl_long_term_investments shown separately)',
    'deferred_revenue': 'contract liabilities, current + noncurrent (cash already collected for future service)',
}


# Analyst resolutions of a cross-source disagreement (options['resolutions'], pipeline inputs.dataqa_resolutions), applied only
# to the fact-level comparison (_cross) of the field and metric named, recorded on the field, in the checks and in
# out['resolutions'], and only with FILING evidence; the field stays 'limited' (it rests on a resolution). Without one, or when
# one is rejected or matches nothing, the conflict and its block stay. NVIDIA 2026-07-26: short-term investments 34.1B (SEC)
# against 76.9B (Alpha Vantage), which per the 10-Q is debt securities 34.1B + equity securities 42.8B: a label difference.
#   label_difference  source_id is the source whose value measures something else (another definition or scope): its value is
#                     recorded as an explained definitional difference, not a conflict; the chosen value is unchanged
#   use_source        source_id is the source the filing confirms for that period (period_end required): it is chosen for
#                     the metric at that date, and the values it disagrees with are recorded as explained by the resolution
RESOLUTIONS = ('label_difference', 'use_source')
RESOLUTION_KEYS = ('field', 'metric', 'period_end', 'resolution', 'source_id', 'reason', 'evidence_ids')
RESOLUTION_METRICS = {
    'shares_outstanding': ('shares_outstanding', 'shares_outstanding_cover', 'shares_outstanding_vendor'),
    'cash_and_liquid_investments': ('cash', 'cash_incl_restricted', 'short_term_investments'),
    'investments': ('long_term_investments',),
    'debt': ('long_term_debt', 'long_term_debt_tagged', 'current_portion_ltd', 'short_term_borrowings', 'debt_total_vendor'),
    'revenue_ttm': ('revenue',), 'operating_income_ttm': ('operating_income',), 'cfo_ttm': ('cfo',), 'capex_ttm': ('capex',),
    'sbc_ttm': ('sbc',)}


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _days(a, b):
    return (_d(b) - _d(a)).days


def _rel(a, b, base=None):
    m = max(abs(a), abs(b), abs(base or 0.0))
    return 0.0 if m == 0 else abs(a - b) / m


def _fmt(v):
    return 'n/a' if v is None else f'{v:,.0f}'


def _weakest(sts):
    return min(sts, key=RANK.__getitem__)


def _ref(f):
    return {'metric': f['metric'], 'period': f['period'], 'source_id': f['source_id'], 'value': f['value']}


def _kind(f):
    p = f['period']
    if p.startswith('I@'):
        return 'instant'
    if p.startswith(('FY', 'AVFY@')):
        return 'annual'
    if p.startswith('Q@') or p[-2:] in ('Q1', 'Q2', 'Q3', 'Q4'):
        return 'quarter'
    return 'other'


def _span_kind(a, b):
    n = _days(a, b)
    return 'annual' if 330 <= n <= 400 else 'quarter' if 80 <= n <= 120 else None


def _val(metric, f):
    return F._fv(metric, f)          # payments (capex, buybacks, ...) by magnitude, as facts.ttm sums them


def _split_factor(r):
    """Nearest split factor (k or 1/k) within SPLIT_BAND of a share-count ratio, else None."""
    if not r or r <= 0:
        return None
    for k in SPLIT_FACTORS:
        for x in (k, 1.0 / k):
            if abs(r / x - 1) <= SPLIT_BAND:
                return x
    return None


class _QA:
    """Run state: the FactBook, cached point-in-time candidates, the check log and identity results."""

    def __init__(self, fb, options):
        o = options or {}
        self.fb, self.as_of = fb, fb['as_of']
        self.tol = {**TOL, **(o.get('tolerances') or {})}
        self.max_age = {**MAX_AGE, **(o.get('max_age') or {})}
        self.ccy = fb['company'].get('currency', 'USD')
        self.checks, self.ident, self.cross, self._c = [], [], {}, {}
        self.resolutions = _resolutions(fb, o.get('resolutions'))

    def resolution(self, kind, metric, f, source_id):
        """The accepted resolution of this kind for metric and source at f's period, else None."""
        return next((r for r in self.resolutions if r['status'] != 'rejected' and r['resolution'] == kind and r['metric'] == metric
                     and r['source_id'] == source_id and (not r.get('period_end') or abs(_days(r['period_end'], f['period_end'])) <= 7)),
                    None)

    def add(self, code, status, severity, message, detail=None, period=None, fields=()):
        c = {'code': code, 'status': status, 'severity': severity, 'message': message, 'detail': detail or {}}
        if period:
            c['period'] = period
        if fields:
            c['fields'] = list(fields)
        self.checks.append(c)
        return c

    def cands(self, metric):
        if metric not in self._c:
            c = F.candidates(self.fb, metric)                    # look-ahead filter + best-first order
            if any(r['resolution'] == 'use_source' and r['metric'] == metric and r['status'] != 'rejected' for r in self.resolutions):
                # a use_source resolution puts the confirmed source first at its period (stable: the rest keeps its order)
                c = sorted(c, key=lambda f: self.resolution('use_source', metric, f, f['source_id']) is None)
            self._c[metric] = c
        return self._c[metric]

    def near(self, metric, end, kind, days=7):
        """Facts of one period, wherever providers date it (52/53-week ends vs month ends differ by days)."""
        return [f for f in self.cands(metric) if _kind(f) == kind and abs(_days(f['period_end'], end)) <= days]

    def best(self, metric, end, kind='instant'):
        c = self.near(metric, end, kind)
        return c[0] if c else None

    def v(self, metric, end, kind='instant'):
        f = self.best(metric, end, kind)
        return f['value'] if f else None

    def timeline(self, metric, kind):
        """[(period_end, best fact)] ascending, one per period (ends within 7 days are the same period)."""
        keep = []
        for f in self.cands(metric):
            if _kind(f) == kind and not any(abs(_days(e, f['period_end'])) <= 7 for e, _ in keep):
                keep.append((f['period_end'], f))
        return sorted(keep, key=lambda x: x[0])

    def src(self, f):
        return self.fb['sources'].get(f['source_id'], {})

    def provider(self, f):
        return self.src(f).get('provider') or f['source_id'].split(':')[0]

    def family(self, f):
        """Underlying document family: every copy of the issuer's own reports (filing-derived XBRL through any vendor,
        issuer documents, the vendor float that reads the filing cover) is 'issuer'; each primary non-issuer document
        provider and each independently normalizing vendor is its own; each secondary source is its own."""
        k = self.src(f).get('kind')
        if k == 'secondary':
            return 'secondary:' + f['source_id']
        if k == 'primary_document':
            return 'primary:' + self.provider(f)
        if k in FILING_KINDS or f['metric'] == 'shares_outstanding_vendor':
            return 'issuer'
        return 'vendor:' + self.provider(f)

    def release(self, f):
        return self.src(f).get('dataset') in F.RELEASE_DATASETS

    def grade(self, f):
        return 'reported' if self.src(f).get('kind') in FILING_KINDS and f['basis'] != 'normalized' else 'single_source'

    def secondary(self, f):
        return self.src(f).get('kind') == 'secondary'

    def has_bs(self, end):
        """A full balance sheet (totals) at the date: absent lines on it read as zero."""
        return any(self.best(m, end) for m in BS_TAGS)

    def age(self, end):
        return _days(end, self.as_of)

    def latest_bs(self, until=None):
        """Latest full balance sheet (on or before `until`)."""
        ends = [e for m in BS_TAGS for e, _ in self.timeline(m, 'instant') if until is None or _days(e, until) >= -7]
        return max(ends) if ends else None

    def latest_instant(self):
        """Latest balance-sheet date with any usable line, partial quarter-end data from evidence bundles included:
        the date facts.latest_instant (and the valuation's opening balance) reads."""
        ends = [tl[-1][0] for tl in (self.timeline(m, 'instant') for m in BS_LINES) if tl]
        return max(ends) if ends else None

    def idents(self, metric, end):
        return [x for x in self.ident if x['metric'] == metric and abs(_days(x['end'], end)) <= 7]

    def price(self):
        p = (self.fb.get('market') or {}).get('price') or {}
        return p['value'] if p.get('value') and p.get('as_of') and _d(p['as_of']) <= _d(self.as_of) else None

    def bars(self):
        """[(date the close was known, close)] ascending, nothing known after as_of. With market['price_history_bar_date']
        == 'start' (bars stamped at their start, as IB monthly bars are) a close is known when the next bar starts, so
        each close moves to the next bar's date and the last bar is dropped."""
        m = self.fb.get('market') or {}
        rows = sorted((str(r['date'])[:10], float(r['close'])) for r in m.get('price_history') or [] if r.get('close') and r.get('date'))
        if m.get('price_history_bar_date') == 'start':
            rows = [(nxt[0], c) for (_, c), nxt in zip(rows, rows[1:])]
        return [(d, c) for d, c in rows if _d(d) <= _d(self.as_of)]

    def price_basis(self):
        """'as_traded', 'split_adjusted' or None (unknown): market['price_history_basis'], else every row's 'basis' or
        'adjusted' flag. Only an as-traded series can tell a split from an issuance (an adjusted one also carries
        splits announced after its earlier dates)."""
        m = self.fb.get('market') or {}
        if m.get('price_history_basis') in ('as_traded', 'split_adjusted'):
            return m['price_history_basis']
        tags = {r.get('basis') or {True: 'split_adjusted', False: 'as_traded'}.get(r.get('adjusted')) for r in m.get('price_history') or []}
        return next(iter(tags)) if len(tags) == 1 and tags <= {'as_traded', 'split_adjusted'} else None

    def closes(self, start, end):
        return [c for d, c in self.bars() if _d(start) < _d(d) <= _d(end)]

    def dated(self, x, missing=False):
        """Qualitative item usable at as_of: available_at, else its as_of (facts.item_usable, the facts.usable rule);
        undated items count only where that is the conservative reading."""
        return F.item_usable(self.fb, x, missing)


def _filing_evidence(fb, eid):
    """Is this evidence id a filing (a filing-derived source, or an issuer document of the filing dataset), or a dated item that
    cites one, usable at as_of?"""
    def filing(sid):
        x = fb['sources'].get(sid) or {}
        ok = x.get('kind') == 'filing_derived' or (x.get('kind') == 'issuer_document' and x.get('dataset') == 'filing')
        return ok and not (x.get('published_at') and _d(x['published_at']) > _d(fb['as_of']))
    if eid in fb['sources']:
        return filing(eid)
    item = next((x for x in fb.get('qualitative') or [] if x.get('id') == eid), None)
    return bool(item) and F.item_usable(fb, item, False) and any(filing(sid) for sid in item.get('source_ids') or [])


def _resolutions(fb, rows):
    """options['resolutions'] -> checked rows with status 'pending' (applied later), or 'rejected' with why (recorded, never
    applied: the conflict stays). A malformed list is an input error."""
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError('dataqa resolutions: a list of {field, metric, period_end, resolution, source_id, reason, evidence_ids}')
    out = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or set(r) - set(RESOLUTION_KEYS):
            raise ValueError(f'dataqa resolutions[{i}]: an object with {", ".join(RESOLUTION_KEYS)}')
        x = {k: r.get(k) for k in RESOLUTION_KEYS if k in r}
        x.update(evidence_ids=list(r.get('evidence_ids') or []) if isinstance(r.get('evidence_ids'), list) else [], status='pending',
                 applied=[])
        why = []
        if r.get('resolution') not in RESOLUTIONS:
            why.append(f"resolution must be one of {', '.join(RESOLUTIONS)}")
        if r.get('field') not in RESOLUTION_METRICS:
            why.append(f"field must be one of {', '.join(RESOLUTION_METRICS)}")
        elif r.get('metric') not in RESOLUTION_METRICS[r['field']]:
            why.append(f"metric of {r['field']} must be one of {', '.join(RESOLUTION_METRICS[r['field']])}")
        if r.get('source_id') not in fb['sources']:
            why.append(f"source_id {r.get('source_id')!r} is not a FactBook source")
        elif (fb['sources'][r['source_id']].get('kind') == 'secondary') and r.get('resolution') == 'use_source':
            why.append('a secondary source is never the value used')
        if not isinstance(r.get('reason'), str) or not r['reason'].strip():
            why.append('a written reason is required')
        if r.get('period_end') is not None or r.get('resolution') == 'use_source':
            try:
                _d(r.get('period_end'))
            except (TypeError, ValueError):
                why.append('period_end must be the date of the disagreeing period (required for use_source)')
        if not x['evidence_ids'] or not any(_filing_evidence(fb, e) for e in x['evidence_ids']):
            why.append('filing evidence required: evidence_ids must include a filing (filing-derived source, a filing document, or '
                       'an item citing one) usable at as_of')
        if why:
            x.update(status='rejected', why='; '.join(why))
        out.append(x)
    return out


def _apply_resolutions(q, fields, limited):
    """Record each resolution on its field and in the checks; a field resting on an applied one is limited, never silently ok."""
    out = []
    for r in q.resolutions:
        if r['status'] == 'pending':
            r['status'] = 'applied' if r['applied'] else 'unused'
        label = f"{r.get('resolution')} {r.get('field')}.{r.get('metric')} {r.get('source_id')}"
        if r['status'] == 'applied':
            fld = fields.get(r['field'])
            if fld is not None:
                fld.setdefault('analyst_resolutions', []).append({k: r[k] for k in ('resolution', 'metric', 'source_id', 'reason',
                                                                                     'evidence_ids', 'applied')})
                fld['note'] = '; '.join(x for x in (fld.get('note'), f"rests on an analyst resolution ({label}): {r['reason']}") if x)
                if not fld.get('blocking') and r['field'] not in limited:
                    limited.append(r['field'])
            q.add('analyst_resolution', 'applied', 'major', f"{label} applied to {len(r['applied'])} comparison(s): {r['reason']} "
                  f"(filing evidence {', '.join(r['evidence_ids'])})", detail={k: r[k] for k in r if k != 'status'},
                  fields=(r['field'],))
        else:
            q.add('analyst_resolution', r['status'], 'major',
                  f"{label} {'rejected: ' + r['why'] if r['status'] == 'rejected' else 'matched no cross-source disagreement'}: "
                  'nothing changed, the field keeps its status', detail={k: r[k] for k in r if k != 'status'},
                  fields=(r['field'],) if isinstance(r.get('field'), str) else ())
        out.append(r)
    return out


def _field(q, name, status, value, unit, as_of, facts=(), checks=(), notes=(), **extra):
    return {'field': name, 'status': status, 'value': value, 'unit': unit, 'as_of': as_of,
            'sources': sorted({f['source_id'] for f in facts}), 'facts': [_ref(f) for f in facts],
            'checks': sorted(set(checks)), 'note': '; '.join(n for n in notes if n), **extra}


def _stale(q, fld, kind, what):
    """Downgrade a field to stale when its date is older than the age limit (keeps the pre-age status)."""
    if fld['as_of'] is None or fld['status'] in ('conflict', 'missing'):
        return fld
    age = q.age(fld['as_of'])
    fld['age_days'] = age
    if kind == 'ttm' and age > 2 * q.max_age[kind]:
        fld['source_status'], fld['status'] = fld['status'], 'missing'
        fld['note'] = '; '.join(x for x in (fld['note'], f'{what} dated {fld["as_of"]} is {age} days old at {q.as_of}, more than '
                                            f'twice the {q.max_age[kind]}-day limit: no current figure, treated as missing') if x)
        fld['checks'] = sorted(set(fld['checks']) | {'staleness'})
        q.add('staleness', 'fail', 'critical', f"{fld['field']}: {what} is {age} days old (treated as missing)", period=fld['as_of'],
              detail={'as_of': fld['as_of'], 'age_days': age, 'limit_days': q.max_age[kind]}, fields=(fld['field'],))
    elif age > q.max_age[kind]:
        fld['source_status'], fld['status'] = fld['status'], 'stale'
        fld['note'] = '; '.join(x for x in (fld['note'], f'{what} dated {fld["as_of"]} is {age} days old at {q.as_of} '
                                            f'(limit {q.max_age[kind]}): newer reports exist but are absent') if x)
        fld['checks'] = sorted(set(fld['checks']) | {'staleness'})
        q.add('staleness', 'fail', 'major', f"{fld['field']}: {what} is {age} days old", period=fld['as_of'],
              detail={'as_of': fld['as_of'], 'age_days': age, 'limit_days': q.max_age[kind]}, fields=(fld['field'],))
    return fld


# ---------------------------------------------------------------- cross-source and identities

def _explain(q, metric, f, b):
    """Known definitional reasons two providers differ for the same period (not a conflict, but recorded)."""
    a, end, tol = f['value'], f['period_end'], q.tol.get(metric, DEFAULT_TOL)
    if metric == 'cash':
        sti, rc, incl = q.v('short_term_investments', end), q.v('restricted_cash', end), q.v('cash_incl_restricted', end)
        if sti and _rel(a + sti, b) <= tol:
            return 'other source includes short-term investments in cash (adding them again would double count)'
        if (incl and _rel(incl, b) <= tol) or (rc and _rel(a + rc, b) <= tol):
            return 'other source includes restricted cash'
    if metric.startswith('shares_outstanding') and a and _split_factor(b / a):
        return f'differs by a split factor ({_split_factor(b / a):g}): split-adjusted versus unadjusted count'
    return None


def _cross(q, metric, f):
    """Compare a chosen fact with the same period's values from other source families (one value per family).

    Inside the chosen fact's family: another transcription of the same filing (SEC XBRL vs FMP as-reported, XBRL vs the
    10-Q text) agreeing confirms only the transcription; disagreeing it is a disagreement like any other (one copy is
    wrong). A release against a filing (or another release) is a revision: an 'issuer_revision' check that limits the
    field. Secondary sources are recorded as notes, never confirmations or conflicts."""
    key = (metric, f['period_end'], _kind(f), f['source_id'])
    if key in q.cross:
        return q.cross[key]
    mine, others, copies, revs, sec = q.family(f), {}, [], [], []
    for g in q.near(metric, f['period_end'], _kind(f)):
        if g['source_id'] == f['source_id']:
            continue
        if q.secondary(g) or q.secondary(f):          # press/analysis never confirm nor contradict anything
            sec.append(g)
        elif q.family(g) == mine and (q.release(f) or q.release(g)):
            revs.append(g)
        elif q.family(g) == mine:
            copies.append(g)
        else:
            others.setdefault(q.family(g), g)
    a, tol = _val(metric, f), q.tol.get(metric, DEFAULT_TOL)
    base = None
    if _kind(f) in ('annual', 'quarter') and metric != 'revenue':
        rev = q.best('revenue', f['period_end'], _kind(f))
        base = .2 * abs(rev['value']) if rev else None     # near-zero margins compare on a revenue-scaled base
    out = {'agree': [], 'disagree': [], 'explained': [], 'same_family': [], 'revisions': [], 'compared': bool(others or copies),
           'secondary': [{'source_id': g['source_id'], 'value': _val(metric, g),
                          'rel_diff': round(_rel(a, _val(metric, g)), 6)} for g in sec]}
    rows = [(g, 'same filing, another transcription') for g in copies] + [(g, None) for _, g in sorted(others.items())]
    for g, copy in rows:
        b = _val(metric, g)
        r = _rel(a, b, base)
        row = {'provider': q.provider(g), 'family': q.family(g), 'source_id': g['source_id'], 'value': b, 'rel_diff': round(r, 6)}
        if copy:
            row['note'] = copy
        if r <= tol:
            (out['same_family'] if copy else out['agree']).append(row)
            continue
        why = _explain(q, metric, f, b)
        res = q.resolution('label_difference', metric, g, g['source_id']) or q.resolution('use_source', metric, f, f['source_id'])
        if not why and res and not copy:
            why = (f"analyst resolution ({res['resolution'].replace('_', ' ')}"
                   + (f", use {res['source_id']}" if res['resolution'] == 'use_source' else '')
                   + f"): {res['reason']} (filing evidence {', '.join(res['evidence_ids'])})")
            res['applied'].append({'metric': metric, 'period': f['period'], 'chosen_source': f['source_id'], 'chosen_value': a,
                                   'other_source': g['source_id'], 'other_value': b, 'rel_diff': round(r, 6)})
        if why:
            out['explained'].append({**row, 'definition': why})
        else:
            if q.grade(g) == 'reported' and _d(g['available_at']) > _d(f['available_at']):
                row['note'] = 'later filing-derived value: possible restatement (first-filed value kept for point-in-time use)'
            out['disagree'].append(row)
    for g in revs:
        r = _rel(a, _val(metric, g), base)
        row = {'provider': q.provider(g), 'source_id': g['source_id'], 'value': _val(metric, g), 'rel_diff': round(r, 6),
               'available_at': g['available_at'], 'release': q.release(g)}
        out['same_family'].append(row)
        if r > tol and not _explain(q, metric, f, _val(metric, g)):
            out['revisions'].append(row)
    if out['revisions']:
        q.add('issuer_revision', 'fail', 'major',
              f"{metric} {f['period']}: {f['source_id']} {_fmt(a)} vs " + ', '.join(f"{r['source_id']} {_fmt(r['value'])}" for r in out['revisions'])
              + " - the issuer's own documents disagree: a release revised later (a filing ranks first once public)",
              detail={'metric': metric, 'value': a, 'source_id': f['source_id'], 'available_at': f['available_at'],
                      'revisions': out['revisions'], 'tolerance': tol}, period=f['period'])
    if out['compared']:
        bad = bool(out['disagree'])
        q.add('cross_source', 'fail' if bad else 'pass', 'major' if bad else 'minor',
              f"{metric} {f['period']}: {q.provider(f)} {_fmt(a)} vs " + ', '.join(f"{r['provider']} {_fmt(r['value'])}"
                                                                                for k in ('agree', 'same_family', 'explained', 'disagree')
                                                                                for r in out[k] if 'release' not in r)
              + (' (beyond tolerance)' if bad else ' (agree or definitional difference)')
              + ('' if out['agree'] else '; only copies of the same filing agree: not an independent confirmation'
                 if any('release' not in r for r in out['same_family']) else ''),
              detail={'metric': metric, 'value': a, 'provider': q.provider(f), 'family': mine, 'source_id': f['source_id'],
                      'tolerance': tol, **{k: out[k] for k in ('agree', 'explained', 'disagree', 'same_family')}}, period=f['period'])
    spread = [r for r in out['secondary'] if r['rel_diff'] > tol]
    if spread and q.secondary(f):
        q.add('secondary_spread', 'fail', 'minor', f"{metric} {f['period']}: secondary sources disagree - {f['source_id']} {_fmt(a)} vs "
              + ', '.join(f"{r['source_id']} {_fmt(r['value'])}" for r in spread) + ' (recorded; neither is a filing value)',
              detail={'metric': metric, 'value': a, 'source_id': f['source_id'], 'others': spread}, period=f['period'])
    q.cross[key] = out
    return out


def _status_of(q, metric, f, corroborated=False):
    """Status of one fact: grade by source, upgraded (only from 'reported') by an independent family or an identity,
    conflict on an unresolved disagreement, limited when the issuer's release and filing disagree."""
    grade = q.grade(f)
    cx = _cross(q, metric, f)
    ids = q.idents(metric, f['period_end'])
    codes = [x['code'] for x in ids] + (['cross_source'] if cx['compared'] else []) + (['issuer_revision'] if cx['revisions'] else [])
    notes = [f"{r['provider']} reports {_fmt(r['value'])}: {r['definition']}" for r in cx['explained']]
    notes += [f"secondary source {r['source_id']} gives {_fmt(r['value'])} ({r['rel_diff']:.2%} apart): recorded, neither a "
              f"confirmation nor a conflict" for r in cx['secondary'] if r['rel_diff'] > q.tol.get(metric, DEFAULT_TOL)]
    notes += [f"{r['source_id']} (the issuer's {'release' if r['release'] else 'filing'}, available {r['available_at']}) gives "
              f"{_fmt(r['value'])} ({r['rel_diff']:.2%} apart): revised figure - confirm which one the latest filing states"
              for r in cx['revisions']]
    failed = sorted({x['code'] for x in ids if x['status'] == 'fail' and x['lfl']})
    if failed:
        return 'conflict', codes, notes + [f"{metric} {f['period']} fails {', '.join(failed)}"]
    if q.secondary(f):
        return grade, codes, notes + [f"{metric} {f['period']} from a secondary source only ({f['source_id']}): single source"]
    id_ok = grade == 'reported' and (corroborated or any(x['status'] == 'pass' for x in ids))
    if cx['disagree']:
        who = ', '.join(f"{r['provider']} {_fmt(r['value'])}" for r in cx['disagree'])
        if (cx['agree'] or id_ok) and not cx['revisions']:
            return 'verified' if grade == 'reported' else grade, codes, notes + [
                f"{metric} {f['period']}: outlier source(s) {who} recorded; chosen value corroborated by "
                f"{'another source family' if cx['agree'] else 'an accounting check'}"]
        return 'conflict', codes, notes + [f"{metric} {f['period']}: {q.provider(f)} {_fmt(f['value'])} vs {who} beyond tolerance"]
    if cx['revisions']:
        return _weakest([grade, 'single_source']), codes, notes
    if (cx['agree'] or id_ok) and grade == 'reported':
        return 'verified', codes, notes
    return grade, codes, notes


def _identity(q, metric, end, code, status, lfl):
    q.ident.append({'metric': metric, 'end': end, 'code': code, 'status': status, 'lfl': lfl})


def _identities(q):
    """Accounting identities that confirm (or contradict) the reported numbers they involve."""
    for end, ta in q.timeline('total_assets', 'instant')[-6:]:
        le, tl, eq = (q.best(m, end) for m in ('liabilities_and_equity', 'total_liabilities', 'equity'))
        if le:
            other, how = le['value'], 'liabilities_and_equity'
        elif tl and eq:
            other, how = tl['value'] + eq['value'], 'total_liabilities + equity'
        else:
            continue
        ok = _rel(ta['value'], other) <= IDENTITY_TOL
        d = {'total_assets': ta['value'], how: other}
        if le and tl and eq:
            d['noncontrolling_or_mezzanine'] = le['value'] - tl['value'] - eq['value']
        q.add('balance_sheet_identity', 'pass' if ok else 'fail', 'major' if le or ok else 'minor',
              f'assets {_fmt(ta["value"])} vs {how} {_fmt(other)}' + ('' if ok or le else ' (gap may be noncontrolling/mezzanine equity)'),
              detail=d, period=ta['period'])
    for kind, n in (('annual', 5), ('quarter', 4)):
        for end, rev in q.timeline('revenue', kind)[-n:]:
            cor, gp = q.best('cost_of_revenue', end, kind), q.best('gross_profit', end, kind)
            if not (cor and gp):
                continue
            diff = rev['value'] - cor['value'] - gp['value']
            ok = abs(diff) <= IDENTITY_TOL * abs(rev['value'])
            lfl = len({rev['source_id'], cor['source_id'], gp['source_id']}) == 1
            q.add('gross_profit_identity', 'pass' if ok else 'fail', 'major',
                  f"revenue - cost of revenue - gross profit = {_fmt(diff)}", period=rev['period'], fields=('revenue_ttm',),
                  detail={'revenue': rev['value'], 'cost_of_revenue': cor['value'], 'gross_profit': gp['value'], 'difference': diff,
                          'same_source': lfl})
            for x in (rev, cor, gp):
                _identity(q, x['metric'], end, 'gross_profit_identity', 'pass' if ok else 'fail', lfl)
        for end, cfo in q.timeline('cfo', kind)[-n:]:
            cfi, cff, chg, fx = (q.best(m, end, kind) for m in ('cfi', 'cff', 'change_in_cash', 'fx_effect'))
            if not (cfi and cff and chg):
                continue
            s = cfo['value'] + cfi['value'] + cff['value'] + (fx['value'] if fx else 0.0)
            base = max(abs(cfo['value']), abs(cfi['value']), abs(cff['value']), abs(chg['value']))
            ok = abs(s - chg['value']) <= IDENTITY_TOL * base
            lfl = fx is not None or ok          # without an FX line a small gap may be the missing FX effect
            q.add('cash_flow_tie', 'pass' if ok else 'fail', 'major' if lfl else 'minor',
                  f"cfo + cfi + cff{' + fx' if fx else ''} = {_fmt(s)} vs change in cash {_fmt(chg['value'])}",
                  period=cfo['period'], fields=('cfo_ttm',),
                  detail={'cfo': cfo['value'], 'cfi': cfi['value'], 'cff': cff['value'], 'fx_effect': fx['value'] if fx else None,
                          'change_in_cash': chg['value'], 'difference': s - chg['value']})
            for m in ('cfo', 'change_in_cash'):
                _identity(q, m, end, 'cash_flow_tie', 'pass' if ok else 'fail', lfl)
    tl = q.timeline('cash', 'instant')
    for (pe, p), (ce, c) in list(zip(tl, tl[1:]))[-5:]:
        kind = _span_kind(pe, ce)
        chg = q.best('change_in_cash', ce, kind) if kind else None
        if not chg:
            continue
        incl = 'restricted' in (chg.get('concept') or '').lower()          # SEC concepts are CamelCase
        lfl = not incl
        bp, bc, basis = p['value'], c['value'], 'cash'
        if incl:
            ip, ic = q.v('cash_incl_restricted', pe), q.v('cash_incl_restricted', ce)
            rp, rc = q.v('restricted_cash', pe), q.v('restricted_cash', ce)
            if ip is not None and ic is not None:
                bp, bc, basis, lfl = ip, ic, 'cash_incl_restricted', True
            elif rp is not None and rc is not None:
                bp, bc, basis, lfl = bp + rp, bc + rc, 'cash + restricted_cash', True
        delta = bc - bp
        ok = abs(delta - chg['value']) <= q.tol['cash'] * max(abs(bp), abs(bc))
        q.add('cash_rollforward', 'pass' if ok else 'fail', 'major' if lfl else 'minor',
              f'balance-sheet {basis} moved {_fmt(delta)}; cash-flow statement change {_fmt(chg["value"])}'
              + ('' if lfl else ' (statement change includes restricted cash, balance sheet basis may not)'),
              period=f'{pe}..{ce}', fields=('cash_and_liquid_investments',),
              detail={'prior': bp, 'current': bc, 'balance_change': delta, 'cash_flow_change': chg['value'], 'basis': basis,
                      'like_for_like': lfl})
        for e in (pe, ce):
            _identity(q, 'cash', e, 'cash_rollforward', 'pass' if ok else 'fail', lfl)
    for end, ni in q.timeline('net_income', 'annual')[-5:]:
        by = {}
        for g in q.near('net_income', end, 'annual'):
            by.setdefault(q.provider(g), {}).setdefault(g['source_id'], g)
        for prov, rows in by.items():
            if len(rows) < 2:
                continue
            vals = {s: g['value'] for s, g in rows.items()}
            ok = max(vals.values()) - min(vals.values()) <= IDENTITY_TOL * max(abs(x) for x in vals.values())
            q.add('net_income_is_vs_cf', 'pass' if ok else 'fail', 'minor',
                  f'net income across {prov} statements: ' + ', '.join(f'{s} {_fmt(x)}' for s, x in vals.items())
                  + ('' if ok else ' (cash-flow statement may start from profit including noncontrolling interests)'),
                  period=ni['period'], detail={'values': vals})
    for m in ('revenue', 'operating_income', 'cfo', 'capex', 'sbc'):
        qs = q.timeline(m, 'quarter')
        if len(qs) < 4:
            continue
        for end, a in q.timeline(m, 'annual')[-3:]:
            start = a.get('period_start') or (_d(end) - timedelta(days=364)).isoformat()
            inq = [(e, f) for e, f in qs if _days(start, e) > 0 and _days(e, end) >= -7]
            if len(inq) != 4:
                continue
            s = sum(_val(m, f) for _, f in inq)
            rev = q.best('revenue', end, 'annual')
            ok = _rel(s, _val(m, a), .2 * abs(rev['value']) if rev and m != 'revenue' else None) <= q.tol.get(m, DEFAULT_TOL)
            lfl = len({q.provider(f) for _, f in inq} | {q.provider(a)}) == 1
            derived = any(f['basis'] == 'derived' or f.get('derived_from') for _, f in inq)
            q.add('quarters_sum_to_annual', 'pass' if ok else 'fail', 'major',
                  f'{m}: four quarters sum to {_fmt(s)} vs fiscal year {_fmt(_val(m, a))}'
                  + (' (a quarter is derived from the annual: tie is not independent)' if derived else ''),
                  period=a['period'], detail={'metric': m, 'quarters': {e: _val(m, f) for e, f in inq}, 'annual': _val(m, a)})
            if not derived or not ok:
                for e in [end] + [e for e, _ in inq]:
                    _identity(q, m, e, 'quarters_sum_to_annual', 'pass' if ok else 'fail', lfl)


# ---------------------------------------------------------------- capital structure

def _line_date(x, cs):
    return x.get('as_of') or x.get('issue_date') or cs['as_of']


def _line_latest(x, cs):
    """The latest date a line carries (its as_of, its issue_date, the structure's as_of): known only once all have passed."""
    return max((str(d)[:10] for d in (x.get('as_of'), x.get('issue_date'), cs['as_of']) if d), key=_d)


def _capital(q, cs):
    """capital.py structure -> amounts by kind, statement-dated subset and post-period lines (validated, never trusted blind).

    A line's date is its as_of, else its issue_date, else the structure's as_of (undated lines read as post-period).
    A line dated (as_of or issue_date) after the FactBook as_of is not knowable then: it is dropped before anything
    reads the structure, with a 'capital_structure_lookahead' check."""
    if not cs:
        return None
    fb = q.fb
    try:
        capital.validate(cs)
    except (ValueError, KeyError, TypeError) as e:
        q.add('capital_structure_valid', 'fail', 'critical', f'capital structure rejected: {e}', fields=('convertibles',))
        return None
    if _d(cs['as_of']) > _d(q.as_of):
        q.add('capital_structure_lookahead', 'fail', 'critical', f"capital structure dated {cs['as_of']} after as_of {q.as_of}: ignored")
        return None
    groups = ('cash', 'shares', 'debt', 'quasi_debt')
    late = [(g, x['id'], _line_latest(x, cs)) for g in groups for x in cs.get(g, []) if _d(_line_latest(x, cs)) > _d(q.as_of)]
    if late:
        cs = {**cs, **{g: [x for x in cs[g] if _d(_line_latest(x, cs)) <= _d(q.as_of)] for g in groups if g in cs}}
        q.add('capital_structure_lookahead', 'fail', 'major',
              f"{len(late)} capital-structure line(s) dated after as_of {q.as_of} excluded: " + ', '.join(f'{g}:{i} ({d})' for g, i, d in late),
              detail={'excluded': [{'group': g, 'id': i, 'date': d} for g, i, d in late]},
              fields=('shares_outstanding', 'cash_and_liquid_investments', 'debt', 'convertibles'))
        try:
            capital.validate(cs)
        except (ValueError, KeyError, TypeError) as e:
            q.add('capital_structure_valid', 'fail', 'critical', f'capital structure rejected once lines after as_of are excluded: {e}',
                  fields=('convertibles',))
            return None
    try:
        summ, err = capital.summarize(cs), None
    except (ValueError, KeyError, TypeError) as e:
        summ, err = None, f'{type(e).__name__}: {e}'
    q.add('capital_structure_valid', 'fail' if err else 'pass', 'major' if err else 'minor',
          f'capital structure as of {cs["as_of"]} ' + (f'could not be summarized ({err})' if err else 'validated and summarized'),
          detail={'summary': summ} if summ else {'error': err})

    def grade(lines):
        ids = [x['source_id'] for x in lines]
        return 'reported' if ids and all(fb['sources'].get(s, {}).get('kind') in FILING_KINDS for s in ids) else 'single_source'

    def lines(group, kinds, until=None):
        return [x for x in cs.get(group, []) if x['kind'] in kinds and (until is None or _d(_line_date(x, cs)) <= _d(until))]

    def amount(xs, key='amount'):
        return sum(float(x.get(key) or 0.0) for x in xs)

    def debt_amount(xs):
        return sum(float(x.get('carrying_value', x.get('principal')) or 0.0) for x in xs)
    return {'raw': cs, 'as_of': cs['as_of'], 'summary': summ, 'error': err, 'lines': lines, 'grade': grade,
            'amount': amount, 'debt_amount': debt_amount, 'source_ids': sorted({x['source_id'] for g in ('cash', 'shares', 'debt', 'quasi_debt')
                                                                                 for x in cs.get(g, [])}),
            'converts': [x for x in cs.get('debt', []) if x['kind'] == 'convertible'],
            'share_kinds': {s['kind'] for s in cs.get('shares', [])}}


def _cs_compare(q, cs, field, label, stmt_value, stmt_end, kinds_group, explain=None, stmt_sources=()):
    """Cross-check the capital structure against the statements at the statement date.

    Returns (use_cs, tie, conflict, note); tie is 'independent', 'same_source' or None. A structure dated at the statement
    date must tie; a newer one (a bridge with post-period lines) is compared on its statement-dated lines and then used as
    the newer source. A tie confirms (verifies) only when every line cites a filing-grade source, none of the statement
    facts' source ids and none of their providers. Lines citing press, analysis or vendors are a note - neither a
    confirmation nor a conflict - and their structure is graded by those sources."""
    group, kinds = kinds_group
    newer = _days(stmt_end, cs['as_of']) > 7 if stmt_end else True
    older = _days(cs['as_of'], stmt_end) > 7 if stmt_end else False
    if older:
        return False, None, False, f'capital structure ({cs["as_of"]}) older than the statements: not used for {label}'
    xs = cs['lines'](group, kinds, until=(_d(stmt_end) + timedelta(days=7)).isoformat() if stmt_end else None)
    if stmt_value is None or stmt_end is None:
        return True, None, False, f'{label} from the capital structure only (no statement value to compare)'
    cs_v = cs['debt_amount'](xs) if group == 'debt' else cs['amount'](xs, 'count' if group == 'shares' else 'amount')
    tol = q.tol.get('debt' if group == 'debt' else 'shares_outstanding' if group == 'shares' else 'cash', DEFAULT_TOL)
    r = _rel(stmt_value, cs_v)
    why = explain(stmt_value, cs_v) if explain and r > tol else None
    ok = r <= tol or bool(why)
    post = [x['id'] for x in cs.get('raw', {}).get(group, []) if x['kind'] in kinds and _d(_line_date(x, cs)) > _d(stmt_end) + timedelta(days=7)]
    srcs = q.fb['sources']
    line_ids = {x['source_id'] for x in xs}
    non_filing = sorted(x for x in line_ids if srcs.get(x, {}).get('kind') not in FILING_KINDS)
    prov = lambda x: srcs.get(x, {}).get('provider') or x.split(':')[0]
    q.add('capital_structure_crosscheck', 'pass' if ok else 'fail',
          'minor' if ok or non_filing else ('major' if newer else 'critical'),
          f'{label}: statements {_fmt(stmt_value)} at {stmt_end} vs capital-structure lines dated by then {_fmt(cs_v)}'
          + (f' ({why})' if why else '') + (f'; post-period lines {post}' if post else '')
          + (f'; lines cite non-filing sources ({", ".join(non_filing)}): a note only' if non_filing else ''),
          period=stmt_end, fields=(field,),
          detail={'statement': stmt_value, 'capital_structure': cs_v, 'rel_diff': round(r, 6), 'tolerance': tol,
                  'explanation': why, 'post_period_lines': post, 'capital_structure_as_of': cs['as_of'],
                  'non_filing_sources': non_filing})
    if non_filing:      # press, analysis or vendor lines neither confirm nor contradict the statements
        return newer, None, False, (
            f'capital structure {label} {_fmt(cs_v)} ' + ('ties to' if ok else 'does not tie to') + f' the statements at {stmt_end} '
            f'({_fmt(stmt_value)}), but its lines cite non-filing sources ({", ".join(non_filing)}): recorded, neither a '
            'confirmation nor a conflict' + ('; newer capital structure used, graded by its sources' if newer else ''))
    if ok:
        shared = sorted(line_ids & set(stmt_sources))
        same_prov = sorted({prov(x) for x in line_ids} & {prov(x) for x in stmt_sources})
        why_not = (f'it cites the same source ({", ".join(shared)})' if shared else
                   f'its lines and the statements come from the same provider ({", ".join(same_prov)})' if same_prov else None)
        note = (f'capital structure ties to the statements at {stmt_end}' + (f' ({why})' if why else '')
                + (f'; {why_not}: not an independent confirmation' if why_not else '')
                + ('; newer capital structure used' if newer else ''))
        return newer, 'same_source' if why_not else 'independent', False, note
    if newer:   # exchanges/repayments after the statement date can change pre-existing lines: limited, not a conflict
        return True, None, False, (f'capital structure lines dated by {stmt_end} ({_fmt(cs_v)}) do not tie to the statements '
                                    f'({_fmt(stmt_value)}); may reflect post-period exchanges or repayments: confirm')
    return False, None, True, (f'capital structure {label} {_fmt(cs_v)} does not tie to the statements {_fmt(stmt_value)} '
                                f'at {stmt_end}: an instrument may be missing or a balance-sheet scope is wrong')


# ---------------------------------------------------------------- shares

def _split_candidate(r):
    """(k, in_band) for a share-count ratio r: k is the split factor (k or 1/k) within SPLIT_BAND of r (in_band), else the
    factor whose residual r/k stays inside SPLIT_ISSUANCE_BAND and is nearest 1 (a split plus issuance or buybacks in the
    same step); (None, False) when no factor fits."""
    if not r or r <= 0:
        return None, False
    k = _split_factor(r)
    if k:
        return k, True
    best = None
    for f in SPLIT_FACTORS:
        for x in (f, 1.0 / f):
            res = r / x
            if SPLIT_ISSUANCE_BAND[0] <= res <= SPLIT_ISSUANCE_BAND[1] and abs(log(res)) < abs(log(r)) \
                    and (best is None or abs(log(res)) < abs(log(r / best))):
                best = x
    return best, False


def _wavg_at(q, end):
    """(weighted-average shares of the period ending at `end`, its kind, its length in days): annual, else quarter;
    reported, else net income / EPS. Vendor-normalized and secondary figures are not used (vendors restate history)."""
    ok = lambda g: g and g['value'] and not q.secondary(g) and q.src(g).get('kind') != 'vendor_normalized'
    for kind, days in (('annual', 365), ('quarter', 91)):
        for m in ('shares_diluted_wavg', 'shares_basic_wavg'):
            w = q.best(m, end, kind)
            if ok(w):
                return w['value'], kind, days
        ni, eps = q.best('net_income', end, kind), q.best('eps_diluted', end, kind)
        if ok(ni) and ok(eps) and ni['value'] / eps['value'] > 0:
            return ni['value'] / eps['value'], kind, days
    return None, None, None


def _split_events(q, a, b):
    """Dated splits between two counts: market['splits'] [{date, factor (shares after / before), source_id}] and
    qualitative items with a split topic (announced up to 30 days before the first count). A split dated up to
    SPLIT_RESTATED_DAYS after the later count also belongs here when that count was filed after the split: filings restate
    the balance-sheet count retroactively (Powell's 3-for-1 of 2 April 2026 is already in its 31 March 2026 count)."""
    out, avail = [], ((b.get('fact') or {}).get('available_at'))
    restated = lambda when: bool(avail) and _d(avail) > _d(when) and 0 < _days(b['as_of'], when) <= SPLIT_RESTATED_DAYS
    for x in (q.fb.get('market') or {}).get('splits') or []:
        if x.get('date') and _d(x['date']) <= _d(q.as_of) and _days(a['as_of'], x['date']) > 0 \
                and (_days(x['date'], b['as_of']) >= 0 or restated(x['date'])):
            out.append({'date': str(x['date'])[:10], 'factor': x.get('factor'), 'source_id': x.get('source_id')})
    for x in q.fb.get('qualitative', []):
        when = x.get('as_of')
        if x.get('topic') in SPLIT_TOPICS and q.dated(x) and when and _days(a['as_of'], when) > -30 \
                and (_days(when, b['as_of']) >= 0 or restated(when)):
            out.append({'date': when, 'factor': x.get('factor'), 'item': x['id']})
    return out


def _split_decision(q, a, b, k):
    """Split or issuance between two dated counts a -> b: {'decision': split|issuance|unresolved, 'decided_by', 'factor',
    'residual_ratio', 'evidence'}. Only positive evidence makes a split:

    - corporate_action: a dated split between the counts (market['splits'], a qualitative split item); its factor, when
      given, is the factor and the rest of the change is issuance or buybacks. Decisive.
    - price: an as-traded close series (market['price_history_basis'] == 'as_traded', or every row flagged) dense enough
      to locate the split bar (closes at most 40 days apart): a close-to-close move of about 1/k between the counts
      supports, its absence contradicts. A split-adjusted or unknown series, or a sparse one, is not used: an adjusted
      series also carries splits made after its earlier dates, and a year-long price ratio mostly measures performance.
    - per_share: weighted-average shares restated by the full count ratio (a split is applied retroactively to the whole
      year, an issuance is weighted by the time outstanding) supports, but only when an issuance would have had to fall
      in the first SPLIT_ALT_WINDOW_DAYS of the year to look the same; weighted shares moving materially less than any
      split reading allows contradict. EPS alone cannot tell a split from an issuance at flat profit: recorded only.
    Agreeing evidence decides; disagreeing or no evidence leaves the step 'unresolved' (counted as issuance, unverified)."""
    r = b['value'] / a['value']
    ev, tests = {'count_ratio': r}, {}
    events = _split_events(q, a, b)
    if events:
        ev['split_events'] = events
        fs = [float(e['factor']) for e in events if e.get('factor')]
        if fs:
            k = 1.0
            for f in fs:
                k *= f
            if k > 0 and r > 0 and abs(log(r * k)) < abs(log(r / k)):
                k = 1 / k                 # a tagged ratio of 10 for a 1-for-10 reverse split: the counts tell the direction
            if not (k > 0 and r > 0 and abs(log(r / k)) < abs(log(r))):
                # the split does not explain this step (both counts already restated, or neither): it belongs to another pair
                ev['split_events_not_this_step'] = ev.pop('split_events')
                k = _split_candidate(r)[0] if r > 0 else None
                if not k:
                    return None
        tests['corporate_action'] = 'supports' if 'split_events' in ev else 'untested'
    if not k:
        return None
    ev['candidate_factor'] = k
    basis = q.price_basis()
    ev['price_basis'] = basis or 'unknown'
    tests['price'] = 'untested'
    if basis == 'as_traded':
        pts = [(d, c) for d, c in q.bars() if _days(d, a['as_of']) <= PRICE_BAR_MAX_GAP and _days(d, b['as_of']) >= 0]
        moves = [(d0, d1, c1 / c0) for (d0, c0), (d1, c1) in zip(pts, pts[1:])
                 if c0 > 0 and _days(d0, d1) <= PRICE_BAR_MAX_GAP and _days(a['as_of'], d1) > 0]
        hit = [m for m in moves if abs(log(m[2] * k)) <= log(1 + PRICE_JUMP_TOL)]
        dense = len(pts) >= 2 and _days(pts[0][0], a['as_of']) >= 0 and _days(pts[-1][0], b['as_of']) <= PRICE_BAR_MAX_GAP \
            and all(_days(d0, d1) <= PRICE_BAR_MAX_GAP for (d0, _), (d1, _) in zip(pts, pts[1:]))
        if hit:
            tests['price'], ev['split_bar'] = 'supports', {'from': hit[0][0], 'to': hit[0][1], 'move': hit[0][2]}
        elif dense:
            tests['price'], ev['price_moves_checked'] = 'contradicts', len(moves)
        else:
            ev['price_note'] = f'as-traded closes too sparse to locate a split bar (need closes at most {PRICE_BAR_MAX_GAP} days apart)'
    else:
        ev['price_note'] = 'close history split-adjusted or of unknown basis: it cannot tell a split from an issuance'
    eps0, eps1 = q.best('eps_diluted', a['as_of'], 'annual'), q.best('eps_diluted', b['as_of'], 'annual')
    if eps0 and eps1 and eps0['value'] and eps0['value'] * eps1['value'] > 0:
        er = eps1['value'] / eps0['value']
        ev['eps_ratio'], ev['eps_supports_split'] = er, abs(log(er * k)) < abs(log(er))      # informational only
    wa, ka, days = _wavg_at(q, a['as_of'])
    wb, kb, _ = _wavg_at(q, b['as_of'])
    tests['per_share'] = 'untested'
    if wa and wb and ka == kb:
        w, rho = wb / wa, r / k
        window = days * WAVG_SPLIT_TOL * r / abs(r - 1) if r != 1 else float('inf')
        ev.update(wavg_ratio=w, wavg_to_count=w / r, issuance_lookalike_window_days=round(window, 1))
        lo, hi = min(1.0, rho), max(1.0, rho)
        if abs(log(w / r)) <= log(1 + WAVG_SPLIT_TOL) and abs(log(w / k)) < abs(log(w)):
            tests['per_share'] = 'supports' if window <= SPLIT_ALT_WINDOW_DAYS else 'ambiguous'
            if window > SPLIT_ALT_WINDOW_DAYS:
                ev['per_share_note'] = (f'weighted shares moved with the count, but an issuance in the first {window:.0f} days of '
                                        'the period would look the same')
        elif lo / (1 + WAVG_SPLIT_TOL) <= w / k <= hi * (1 + WAVG_SPLIT_TOL):
            tests['per_share'] = 'ambiguous'          # a split with issuance during the year, or an issuance: both fit
        else:
            tests['per_share'] = 'contradicts'
        ev['wavg_supports_split'] = tests['per_share'] == 'supports'
    ev['tests'] = tests
    sup = [t for t, x in tests.items() if x == 'supports']
    con = [t for t, x in tests.items() if x == 'contradicts']
    if tests.get('corporate_action') == 'supports':
        decision, by = 'split', 'corporate_action'
    elif sup and not con:
        decision, by = 'split', '+'.join(sup)
    elif con and not sup:
        decision, by = 'issuance', '+'.join(con)
    elif sup:
        decision, by = 'unresolved', 'conflicting'
    else:
        decision, by = 'unresolved', 'ambiguous' if 'ambiguous' in tests.values() else 'untested'
    ev['decided_by'] = by
    return {'decision': decision, 'decided_by': by, 'factor': k, 'residual_ratio': r / k, 'evidence': ev}


def _continuity(q, a, b):
    """Share count change between two dated counts versus the cash spent on buybacks over the same span."""
    kind = _span_kind(a['as_of'], b['as_of'])
    chg = b['value'] - a['value']
    d = {'from': a['as_of'], 'to': b['as_of'], 'routes': [a['basis'], b['basis']], 'prior': a['value'], 'current': b['value'],
         'change': chg, 'change_pct': chg / a['value'] if a['value'] else None}
    bb = q.best('buybacks', b['as_of'], kind) if kind else None
    status, sev, msg = 'skip', 'minor', f"shares {_fmt(a['value'])} ({a['as_of']}) -> {_fmt(b['value'])} ({b['as_of']})"
    if bb:
        d['buybacks'] = abs(bb['value'])
    if chg < 0 and bb and abs(bb['value']) > 0:
        implied = abs(bb['value']) / -chg
        d['implied_avg_repurchase_price'] = implied
        closes = q.closes(a['as_of'], b['as_of'])
        if closes:
            lo, hi = min(closes), max(closes)
            d['price_range'] = [lo, hi]
            if implied < .67 * lo:
                status, sev = 'fail', 'major'
                msg += (f': reduction implies buying at {implied:,.2f}, far below the period low {lo:,.2f} - count may be stale, '
                        f'split-affected or wrongly scoped')
            else:
                status = 'pass'
                msg += f': implied repurchase price {implied:,.2f} vs period range {lo:,.2f}-{hi:,.2f}' + (
                    ' (issuance from SBC/conversions offset part of the buyback)' if implied > 1.5 * hi else '')
        else:
            msg += f': implied repurchase price {implied:,.2f} recorded (no price history to test it)'
    elif chg < 0:
        msg += ': reduction without recorded buybacks over this span (recorded)'
    else:
        msg += f": {'+' if chg >= 0 else ''}{(d['change_pct'] or 0):.1%} (issuance net of buybacks, recorded)"
    q.add('share_continuity', status, sev, msg, detail=d, period=f"{a['as_of']}..{b['as_of']}", fields=('shares_outstanding',))
    return status


def _route_secondary(q, r):
    """A route that never confirms, contradicts or displaces another: a secondary fact, or a capital structure whose
    common lines cite any non-filing source (press, analysis, vendor)."""
    if r['fact'] is not None:
        return q.secondary(r['fact'])
    return any(q.fb['sources'].get(x, {}).get('kind') not in FILING_KINDS for x in r.get('source_ids') or [])


def _route_family(q, r):
    if r['fact'] is not None:
        return q.family(r['fact'])
    return 'capital_structure:' + ','.join(r.get('providers') or [])


def _shares(q, cs):
    fb, routes = q.fb, []
    for metric, basis in SHARE_ROUTES:
        for end, f in q.timeline(metric, 'instant'):
            routes.append({'as_of': end, 'value': f['value'], 'basis': basis, 'fact': f})
    if cs:
        com = cs['lines']('shares', {'common'})
        if com:
            ids = sorted({x['source_id'] for x in com})
            routes.append({'as_of': max(_line_date(x, cs) for x in com), 'value': cs['amount'](com, 'count'),
                           'basis': 'capital_structure', 'fact': None, 'grade': cs['grade'](com), 'source_ids': ids,
                           'providers': sorted({fb['sources'].get(x, {}).get('provider') or x.split(':')[0] for x in ids})})
    if not routes:
        fld = _field(q, 'shares_outstanding', 'missing', None, 'shares', None,
                     notes=['no point-in-time share count (cover, balance sheet, vendor or capital structure); weighted averages are never used'])
        return fld, {'value': None, 'as_of': None, 'basis': None, 'status': 'missing'}, [], []
    merged = {}
    # one count per date cluster: a filing or vendor route always wins over a secondary one (a press count never displaces
    # a filing count), then filing routes before structure and vendor routes
    for r in sorted(routes, key=lambda r: (_route_secondary(q, r), ROUTE_PREF[r['basis']])):
        if not any(abs(_days(e, r['as_of'])) <= 7 for e in merged):
            merged[r['as_of']] = r
    tl = [merged[e] for e in sorted(merged)]
    pick, splits, unresolved, last_cont = tl[-1], [], [], None
    for a, b in zip(tl, tl[1:]):
        ratio = b['value'] / a['value'] if a['value'] else None
        k, in_band = _split_candidate(ratio) if ratio else (None, False)
        dec = _split_decision(q, a, b, k) if ratio and (k or _split_events(q, a, b)) else None
        if dec and (in_band or dec['decision'] == 'split'):
            k, ev = dec['factor'], dec['evidence']
            head = (f"share count x{ratio:.3f} between {a['as_of']} and {b['as_of']} is near a "
                    + (f'{k:g}:1 split' if k > 1 else f'1:{1 / k:g} reverse split'))
            detail = {'factor': k, 'ratio': ratio, 'residual_ratio': dec['residual_ratio'], 'evidence': ev,
                      'decided_by': dec['decided_by'], 'from': a['as_of'], 'to': b['as_of']}
            row = {'from': a['as_of'], 'to': b['as_of'], 'factor': k, 'residual_ratio': dec['residual_ratio'], 'evidence': ev,
                   'decided_by': dec['decided_by']}
            if dec['decision'] == 'split':
                q.add('possible_split', 'fail', 'major', head + f": treated as a split ({dec['decided_by']} evidence)"
                      + (f"; the remaining x{dec['residual_ratio']:.3f} is issuance/buybacks and stays in the share change"
                         if abs(dec['residual_ratio'] - 1) > SPLIT_BAND else '')
                      + ('; weighted-average shares moved by the full ratio (restated for the whole year, the accounting signature '
                         'of a split): confirm with the 8-K' if dec['decided_by'] == 'per_share' else '')
                      + ' - restate per-share history before comparing', period=f"{a['as_of']}..{b['as_of']}",
                      fields=('shares_outstanding',), detail=detail)
                splits.append(row)
                if b is pick:
                    last_cont = 'split'
                continue
            if dec['decision'] == 'unresolved':
                tested = {t: x for t, x in ev['tests'].items() if x != 'untested'}
                q.add('split_unresolved', 'fail', 'major', head + ': split or issuance not established ('
                      + ('no dated split, as-traded price or weighted-average evidence' if not tested
                         else 'the evidence does not decide: ' + ', '.join(f'{t} {x}' for t, x in tested.items()))
                      + f"); both readings: issuance x{ratio:.3f}, or a split leaving x{dec['residual_ratio']:.3f} - counted as "
                      'issuance (dilution unverified) until confirmed', period=f"{a['as_of']}..{b['as_of']}",
                      fields=('shares_outstanding',), detail=detail)
                unresolved.append(row)
                if b is pick:
                    last_cont = 'unresolved'
                continue
            q.add('large_share_change', 'fail', 'major', head + ': ' + {
                'price': 'the as-traded share price shows no move of about 1/factor between the counts',
                'per_share': 'weighted-average shares moved materially less than the count (an issuance is weighted by the time '
                             'outstanding; a split is restated for the whole year)'}.get(
                dec['decided_by'], f"{dec['decided_by']} evidence contradicts a split")
                  + ': treated as real issuance/consolidation - verify', period=f"{a['as_of']}..{b['as_of']}",
                  fields=('shares_outstanding',), detail=detail)
        st = _continuity(q, a, b)
        if b is pick:
            last_cont = st
    f, checks = pick['fact'], ['share_continuity'] if len(tl) > 1 else []
    notes = [f"{pick['basis']} count dated {pick['as_of']}" + (': vendor-normalized only, confirm against the filing cover page'
                                                               if pick['basis'] == 'vendor_float' else '')]
    if f:
        status, codes, n = _status_of(q, f['metric'], f, corroborated=last_cont == 'pass')
        checks += codes
        notes += n
        facts = [f]
    else:
        status, facts = pick['grade'], []
        if last_cont == 'pass' and status == 'reported':
            status = 'verified'
    # other routes dated within a week of the pick: another source family confirms or contradicts; a copy of the same
    # filing (vendor float vs cover, FMP vs SEC) or a structure citing the same source or provider only ties; a secondary
    # route (press count, structure lines citing press or vendors) is a note
    agree, disagree = [], []
    pick_ids = {f['source_id']} if f else set(pick.get('source_ids') or [])
    pick_fam, pick_sec = _route_family(q, pick), _route_secondary(q, pick)
    pick_prov = {q.provider(f)} if f else set(pick.get('providers') or [])
    for r in routes:
        if r is pick or abs(_days(r['as_of'], pick['as_of'])) > 7 or not r['value']:
            continue
        rr = _rel(pick['value'], r['value'])
        if pick_sec or _route_secondary(q, r):
            notes.append(f"{'secondary ' if _route_secondary(q, r) else ''}{r['basis']} count {_fmt(r['value'])} ({r['as_of']}, "
                         f"{rr:.2%} apart): recorded, neither a confirmation nor a conflict")
            continue
        r_ids = {r['fact']['source_id']} if r['fact'] else set(r.get('source_ids') or [])
        r_prov = {q.provider(r['fact'])} if r['fact'] else set(r.get('providers') or [])
        indep = not (r_ids & pick_ids) and _route_family(q, r) != pick_fam and not (r_prov & pick_prov)
        row = {'basis': r['basis'], 'as_of': r['as_of'], 'value': r['value'], 'rel_diff': round(rr, 6), 'independent': indep,
               'family': _route_family(q, r)}
        if rr <= q.tol['shares_outstanding']:
            agree.append(row)
        elif _split_factor(r['value'] / pick['value']):
            notes.append(f"{r['basis']} count {_fmt(r['value'])} differs by a split factor (split-adjusted vs unadjusted)")
        else:
            disagree.append(row)
    if agree or disagree:
        checks.append('share_routes')
        q.add('share_routes', 'fail' if disagree else 'pass', 'major' if disagree else 'minor',
              f"{pick['basis']} {_fmt(pick['value'])} ({pick['as_of']}) vs " + ', '.join(f"{r['basis']} {_fmt(r['value'])}" for r in agree + disagree),
              detail={'pick': {k: pick[k] for k in ('basis', 'as_of', 'value')}, 'agree': agree, 'disagree': disagree},
              period=pick['as_of'], fields=('shares_outstanding',))
    if any(r['independent'] for r in agree) and status == 'reported':
        status = 'verified'
    if disagree and status != 'conflict':
        if any(r['independent'] for r in agree) or last_cont == 'pass':
            notes.append('a route dated within a week disagrees: recorded, pick corroborated')
        else:
            status = 'conflict'
            notes.append('routes dated within a week disagree beyond 0.5%: ' + ', '.join(f"{r['basis']} {_fmt(r['value'])}" for r in disagree))
    # weighted-average counts must never stand in for a point-in-time count
    for m in ('shares_basic_wavg', 'shares_diluted_wavg'):
        for kind in ('annual', 'quarter'):
            w = q.best(m, pick['as_of'], kind)
            if w and _rel(w['value'], pick['value']) < 1e-9:
                q.add('shares_not_wavg', 'fail', 'major', f'point-in-time count equals the {m} figure for {w["period"]}: '
                      'a weighted average may have been stored as the outstanding count', period=pick['as_of'], fields=('shares_outstanding',))
                checks.append('shares_not_wavg')
                status = _weakest([status, 'single_source'])
    if last_cont == 'fail':
        status = _weakest([status, 'single_source'])
        notes.append('share continuity versus buybacks failed: count may be stale or split-affected')
    # pre-funded / penny warrants are economic shares (NBIS): evidence without structure lines limits the count
    ev = [x['id'] for x in fb.get('qualitative', []) if x.get('topic') in WARRANT_TOPICS and q.dated(x, missing=True)]
    ev += [f"{g['metric']}:{g.get('concept')}" for g in fb['facts'] if F.usable(fb, g) and 'warrant' in (g['metric'] + (g.get('concept') or '')).lower()]
    incomplete = bool(ev) and not (cs and cs['share_kinds'] & {'prefunded_warrant', 'warrant'})
    if ev:
        q.add('dilutive_instruments_without_terms', 'fail' if incomplete else 'pass', 'major' if incomplete else 'minor',
              f"warrant evidence ({', '.join(ev[:4])}) " + ('without warrant lines in a capital structure: economic share count may be understated'
                                                            if incomplete else 'is modelled in the capital structure'),
              fields=('shares_outstanding',), detail={'evidence': ev})
        checks.append('dilutive_instruments_without_terms')
        if incomplete:
            notes.append('warrants/pre-funded warrants evidenced but not modelled: economic share count incomplete')
    fld = _field(q, 'shares_outstanding', status, pick['value'], 'shares', pick['as_of'], facts, checks, notes,
                 basis=pick['basis'], source_ids=pick.get('source_ids') or [x['source_id'] for x in facts])
    if incomplete:
        fld['incomplete'] = True
    if splits:
        fld['note'] = '; '.join([fld['note']] + [f"split x{x['factor']:g} between {x['from']} and {x['to']} ({x['decided_by']} evidence): "
                                                  f"per-share history must be restated before comparing" for x in splits])
        fld['checks'] = sorted(set(fld['checks']) | {'possible_split'})
    if unresolved:
        fld['note'] = '; '.join([fld['note']] + [f"split-like change x{x['evidence']['count_ratio']:.3f} between {x['from']} and "
                                                  f"{x['to']} not established as a split: counted as issuance, unverified" for x in unresolved])
        fld['checks'] = sorted(set(fld['checks']) | {'split_unresolved'})
    # a split dated after the latest count and by the valuation date, effective after that count was published: the count
    # predates it (Amphenol's 2-for-1 of 3.9.2026 after its July count had valued each post-split share at twice its worth).
    # A count published after the split is already restated (Powell) and is not multiplied again.
    published = (f or {}).get('available_at') or pick['as_of']
    post = [{'date': str(x['date'])[:10], 'factor': float(x['factor']), 'source_id': x.get('source_id')}
            for x in (fb.get('market') or {}).get('splits') or []
            if x.get('date') and x.get('factor') and float(x['factor']) > 0 and abs(float(x['factor']) - 1) > .01
            and _days(pick['as_of'], x['date']) > 0 and _d(x['date']) <= _d(q.as_of) and _d(published) < _d(x['date'])]
    value = pick['value']
    for x in post:
        value *= x['factor']
    if post:
        msg = (f"count {_fmt(pick['value'])} dated {pick['as_of']} (published {published}) predates "
               + ', '.join(f"a x{x['factor']:g} split on {x['date']} ({x['source_id']})" for x in post) + f": restated to {_fmt(value)}")
        q.add('split_after_count', 'pass', 'major', msg, period=pick['as_of'], fields=('shares_outstanding',),
              detail={'splits': post, 'count': pick['value'], 'restated': value})
        fld['value'] = value
        fld['note'] = '; '.join([fld['note'], msg])
        fld['checks'] = sorted(set(fld['checks']) | {'split_after_count'})
    # cross-check against the listing's market cap / last sale (the shares a quote vendor counts today): off by a split factor
    # means a split or reverse split after the count that no dated action explains (Monster's 3-for-1, Amphenol's 2-for-1
    # before its IBKR action was saved); the per-share value would be off by that factor, so the count is a conflict
    lm = (fb.get('market') or {}).get('listing_market_cap') or {}
    if lm.get('value') and lm.get('price') and value:
        implied = lm['value'] / lm['price']
        # a depositary issuer's listing market cap and price are in depositary shares, not ordinary shares: the implied count
        # is a count of depositary shares, so multiply by the ordinary-shares-per-depositary-share ratio before comparing to
        # the ordinary count (SK hynix: 711m ordinary x 10 ADS = 7.1bn ADS, not a 10x split)
        ratio = ((fb.get('market') or {}).get('price') or {}).get('conversion', {}).get('shares_per_depositary_share')
        if ratio:
            implied *= ratio
        k = _split_factor(implied / value)
        if k and abs(k - 1) > .01:
            msg = (f"listing market cap {_fmt(lm['value'])} at {lm['price']:g} ({lm.get('date')}) implies {_fmt(implied)} shares, "
                   f"{implied / value:.2f}x the count {_fmt(value)}: a {k:g}x split after the count without a dated corporate action "
                   "(save IBKR corporate actions or read the 8-K)")
            q.add('implied_shares_split_factor', 'fail', 'critical', msg, period=pick['as_of'], fields=('shares_outstanding',),
                  detail={'implied': implied, 'count': value, 'factor': k, 'listing': lm})
            fld['status'] = 'conflict'
            fld['note'] = '; '.join([fld['note'], msg])
            fld['checks'] = sorted(set(fld['checks']) | {'implied_shares_split_factor'})
    _stale(q, fld, 'shares', 'share count')
    res = {'value': value, 'as_of': pick['as_of'], 'basis': pick['basis'], 'status': fld['status'],
           'source_id': f['source_id'] if f else (pick.get('source_ids') or [None])[0], 'definition': DEFINITIONS['shares_outstanding']}
    if post:
        res['splits_after_count'] = post
    if cs and cs['summary']:
        res['basic_economic_shares'] = cs['summary']['basic_economic_shares']     # common + pre-funded + zero-strike
        res['rsu_shares'], res['economic_shares_as_of'] = cs['summary']['rsu_shares'], cs['as_of']
    return fld, res, splits, unresolved


# ---------------------------------------------------------------- cash and investments

def _cash(q, cs):
    fb, ccy, res = q.fb, q.ccy, {}
    tl = q.timeline('cash', 'instant')
    bs_end = q.latest_instant()
    notes, cash_f, fallback = [], None, False
    if tl:
        end, cash_f = tl[-1]
    else:
        inc = q.timeline('cash_incl_restricted', 'instant')
        if inc:
            end, cash_f, fallback = inc[-1][0], inc[-1][1], True
            notes.append('cash taken from the cash-flow total that includes restricted cash (no cash-equivalents line)')
        else:
            end = None
    if cash_f and bs_end and _days(end, bs_end) > 7:
        notes.append(f'cash not tagged on the latest balance sheet ({bs_end}); using {end}')
    parts, st, checks, facts, comp_src = {}, [], [], [], {}
    if cash_f:
        s, c, n = _status_of(q, cash_f['metric'], cash_f)
        st.append(s if not fallback and not (bs_end and _days(end, bs_end) > 7) else _weakest([s, 'single_source']))
        checks += c
        notes += n
        facts.append(cash_f)
        parts['cash'], comp_src['cash'] = cash_f['value'], [cash_f['source_id']]
        bs = q.has_bs(end)
        for m in ('short_term_investments', 'long_term_investments', 'restricted_cash'):
            g = q.best(m, end)
            if g:
                parts[m], comp_src[m] = g['value'], [g['source_id']]
                if m == 'short_term_investments':
                    s, c, n = _status_of(q, m, g)
                    st.append(s)
                    checks += c
                    notes += n
                    facts.append(g)
            else:
                parts[m] = 0.0 if bs else None
                if bs and m != 'restricted_cash':
                    # a line that vanishes from one balance sheet to the next is usually a tag change (NVIDIA, fiscal 2027:
                    # MarketableSecuritiesCurrent -> DebtSecuritiesCurrent), not a sale of every security: never a silent zero
                    prior = [f for e, f in q.timeline(m, 'instant') if 0 < _days(e, end) <= 400 and (f['value'] or 0) > 0]
                    if prior:
                        last = prior[-1]
                        q.add('investment_line_continuity', 'fail', 'major',
                              f"{m} not tagged on the balance sheet at {end} but {_fmt(last['value'])} at {last['period_end']}: "
                              'a tag change can hide investments; read the balance sheet before using zero', period=end,
                              fields=('cash_and_liquid_investments',), detail={'metric': m, 'last_value': last['value'],
                                                                              'last_period_end': last['period_end']})
                        checks.append('investment_line_continuity')
                        st.append('single_source')
                        notes.append(f"{m} untagged at {end} (last {_fmt(last['value'])} at {last['period_end']}): taken as zero only "
                                     'provisionally; confirm on the balance sheet')
        if parts['short_term_investments'] is None:
            st.append('single_source')
            notes.append('no balance sheet context at the cash date: short-term investments unknown, cash only')
        # double-count guard (ASTS): liquid assets cannot exceed current assets, and one amount is not both cash and STI
        ca = q.best('current_assets', end)
        liquid = parts['cash'] + (parts['short_term_investments'] or 0.0)
        dup = bool(parts.get('short_term_investments')) and _rel(parts['cash'], parts['short_term_investments']) < 1e-9
        if ca or dup:
            bad = dup or (ca is not None and liquid > ca['value'] * (1 + IDENTITY_TOL))
            q.add('liquid_assets_within_current_assets', 'fail' if bad else 'pass', 'critical' if bad else 'minor',
                  ('the same amount is tagged as cash and as short-term investments' if dup else
                   f'cash + short-term investments {_fmt(liquid)} vs current assets {_fmt(ca["value"])}')
                  + (': double count suspected' if bad else ''), period=end, fields=('cash_and_liquid_investments',),
                  detail={'cash': parts['cash'], 'short_term_investments': parts['short_term_investments'],
                          'current_assets': ca['value'] if ca else None})
            checks.append('liquid_assets_within_current_assets')
            if bad:
                st.append('conflict')
                notes.append('liquid assets double counted or mis-scoped')
    value = parts['cash'] + (parts.get('short_term_investments') or 0.0) if cash_f else None
    basis, as_of, src_ids = 'balance_sheet', end, sorted({x['source_id'] for x in facts})
    if cs:
        def expl(sv, cv):
            return ('capital structure counts restricted cash as unrestricted' if parts.get('restricted_cash')
                    and _rel(sv + parts['restricted_cash'], cv) <= q.tol['cash'] else None)
        use, corr, conf, note = _cs_compare(q, cs, 'cash_and_liquid_investments', 'cash + short-term investments', value, end,
                                            ('cash', {'unrestricted', 'short_term_investments'}), expl,
                                            {x['source_id'] for x in facts})
        notes.append(note)
        checks.append('capital_structure_crosscheck')
        if conf:
            st.append('conflict')
        if corr == 'independent':
            st = ['verified' if x == 'reported' else x for x in st]
        if use:
            un, sti_l, lti_l, rc_l = (cs['lines']('cash', {k}) for k in ('unrestricted', 'short_term_investments',
                                                                          'long_term_investments', 'restricted'))
            stmt = dict(parts)
            parts = {'cash': cs['amount'](un), 'short_term_investments': cs['amount'](sti_l),
                     'long_term_investments': cs['amount'](lti_l), 'restricted_cash': cs['amount'](rc_l)}
            value, basis, as_of = parts['cash'] + parts['short_term_investments'], 'capital_structure', cs['as_of']
            src_ids = sorted({x['source_id'] for x in un + sti_l})
            comp_src = {k: sorted({x['source_id'] for x in xs}) for k, xs in
                        (('cash', un), ('short_term_investments', sti_l), ('long_term_investments', lti_l), ('restricted_cash', rc_l))}
            g = cs['grade'](un + sti_l) if un + sti_l else 'missing'
            st = [g] + ([] if corr else ['single_source'] if stmt.get('cash') is not None else [])
            notes.append(f'statement values at {end}: {stmt}' if end else 'no statement cash: capital structure only')
    neg = {k: parts[k] for k in ('cash', 'short_term_investments') if value is not None and (parts.get(k) or 0.) < 0}
    if neg:
        # cash at carrying value cannot be negative (an overdraft is a borrowing): a data error, never an opening balance
        q.add('liquid_assets_nonnegative', 'fail', 'critical', f"negative {', '.join(f'{k} {_fmt(v)}' for k, v in neg.items())} at {as_of}: "
              'impossible at carrying value (an overdraft is a borrowing); rejected as a data error', period=as_of,
              fields=('cash_and_liquid_investments',), detail={**neg, 'basis': basis})
        checks.append('liquid_assets_nonnegative')
        fld = _field(q, 'cash_and_liquid_investments', 'conflict', None, ccy, as_of, facts, checks,
                     notes + [f"negative {', '.join(neg)} reported ({_fmt(value)} in total): rejected, no usable cash figure"],
                     basis=basis, rejected_value=value, components={'cash': parts.get('cash'), 'short_term_investments': parts.get('short_term_investments')},
                     source_ids=src_ids)
        value = None
    elif value is None:
        fld = _field(q, 'cash_and_liquid_investments', 'missing', None, ccy, None, checks=checks,
                     notes=notes + ['no cash and cash-equivalents line usable at as_of'])
    else:
        fld = _field(q, 'cash_and_liquid_investments', _weakest(st), value, ccy, as_of, facts, checks, notes,
                     basis=basis, components={'cash': parts.get('cash'), 'short_term_investments': parts.get('short_term_investments')},
                     restricted_cash_excluded=parts.get('restricted_cash'), source_ids=src_ids)
        _stale(q, fld, 'balance_sheet', 'balance sheet')
    # long-term investments: separate from spendable cash
    lti_f = q.best('long_term_investments', end) if end else None
    if basis == 'capital_structure':
        lti_lines = cs['lines']('cash', {'long_term_investments'})
        inv = _field(q, 'investments', cs['grade'](lti_lines) if lti_lines else 'reported', parts['long_term_investments'], ccy, as_of,
                     notes=['from the capital structure' + ('' if lti_lines else ': no long-term investment lines (none)')],
                     basis='capital_structure', haircut_value=(cs['summary'] or {}).get('investments_after_haircut'))
    elif lti_f:
        s, c, n = _status_of(q, 'long_term_investments', lti_f)
        inv = _field(q, 'investments', s, lti_f['value'], ccy, end, [lti_f], c, n + ['long-term investments: not spendable cash, excluded from net cash'],
                     basis='balance_sheet')
    elif end and q.has_bs(end):
        inv = _field(q, 'investments', 'reported', 0.0, ccy, end,
                     notes=[f'no long-term investment line on the balance sheet dated {end}: taken as none (conservative)'],
                     basis='balance_sheet', assumed_zero=True)
    elif end and q.latest_bs(end):
        full = q.latest_bs(end)            # partial quarter-end lines (evidence bundle): read the latest full balance sheet
        g, part = q.best('long_term_investments', full), f'balance data at {end} are partial (no long-term investment line)'
        if g:
            s, c, n = _status_of(q, 'long_term_investments', g)
            inv = _field(q, 'investments', s, g['value'], ccy, full, [g], c,
                         n + [f'{part}: latest full balance sheet ({full}) used; not spendable cash, excluded from net cash'],
                         basis='balance_sheet')
        else:
            inv = _field(q, 'investments', 'reported', 0.0, ccy, full,
                         notes=[f'{part}; no long-term investment line on the latest full balance sheet ({full}): taken as none'],
                         basis='balance_sheet', assumed_zero=True)
    else:
        inv = _field(q, 'investments', 'missing', None, ccy, None, notes=['no balance sheet usable at as_of'])
    if (inv['value'] or 0.) < 0:
        q.add('liquid_assets_nonnegative', 'fail', 'critical', f"negative long-term investments {_fmt(inv['value'])} at {inv['as_of']}: "
              'impossible at carrying value; rejected as a data error', period=inv['as_of'], fields=('investments',),
              detail={'long_term_investments': inv['value'], 'basis': inv.get('basis')})
        inv.update(status='conflict', rejected_value=inv['value'], value=None, checks=sorted(set(inv['checks']) | {'liquid_assets_nonnegative'}),
                   note='; '.join(x for x in (inv['note'], 'negative long-term investments rejected: no usable figure') if x))
        parts['long_term_investments'] = None
    if inv['value'] is not None:
        _stale(q, inv, 'balance_sheet', 'balance sheet')
    for k in ('cash', 'short_term_investments', 'long_term_investments', 'restricted_cash'):
        res[k] = {'value': parts.get(k) if value is not None or k == 'long_term_investments' else None, 'as_of': as_of,
                  'basis': basis, 'status': (inv if k == 'long_term_investments' else fld)['status'],
                  'source_ids': comp_src.get(k, []), 'definition': DEFINITIONS.get(k, 'restricted cash: not spendable, excluded from net cash')}
    if res['long_term_investments']['value'] is None:
        res['long_term_investments']['value'] = inv['value']
    # listed equity stakes at fair value: an asset at market value, not spendable cash and not net cash; valued once
    eq_end = end if basis == 'balance_sheet' else None
    eq = q.best('equity_securities_fv', eq_end) if eq_end else None
    res['equity_securities_fv'] = {'value': eq['value'] if eq and eq['value'] > 0 else 0.0, 'as_of': eq_end,
                                   'basis': 'balance_sheet' if eq_end else basis, 'status': fld['status'] if eq else 'reported',
                                   'source_ids': [eq['source_id']] if eq else [],
                                   'definition': 'equity securities with a readily determinable fair value (listed stakes): an asset '
                                                 'at market value, outside net cash; private stakes excluded'}
    dr_end = end or bs_end
    if dr_end:
        drc, drn = q.best('deferred_revenue_current', dr_end), q.best('deferred_revenue_noncurrent', dr_end)
        res['deferred_revenue'] = {'value': (drc['value'] if drc else 0.0) + (drn['value'] if drn else 0.0) if drc or drn else None,
                                   'as_of': dr_end, 'components': {'current': drc['value'] if drc else None, 'noncurrent': drn['value'] if drn else None},
                                   'source_ids': sorted({x['source_id'] for x in (drc, drn) if x}), 'definition': DEFINITIONS['deferred_revenue']}
    else:
        res['deferred_revenue'] = {'value': None, 'as_of': None}
    return fld, inv, res


# ---------------------------------------------------------------- debt

def _current_debt(cur_f, stb_f):
    """(current debt, short-term borrowings to add to it). us-gaap DebtCurrent is the current-debt TOTAL (current
    maturities, short-term borrowings and commercial paper): borrowings tagged beside it are inside it, so only an
    excess over it is added. LongTermDebtCurrent (and vendor current-portion lines) exclude them: added in full."""
    cur = cur_f['value'] if cur_f else 0.0
    stb = stb_f['value'] if stb_f else 0.0
    if cur_f and (cur_f.get('concept') or '').lower() == 'debtcurrent':
        return cur, max(0.0, stb - cur)
    return cur, stb


def _debt_totals(q, end):
    """Candidate total debt at a date under each scope hypothesis; absent lines on a reported balance sheet are zero."""
    p = {m: q.best(m, end) for m in DEBT_PARTS}
    bs = q.has_bs(end)
    cur, stb = _current_debt(p['current_portion_ltd'], p['short_term_borrowings'])
    out = {}
    if p['long_term_debt']:
        out['noncurrent+current'] = p['long_term_debt']['value'] + cur + stb
    if p['long_term_debt_tagged']:
        out['tagged_total'] = p['long_term_debt_tagged']['value'] + stb
        out['tagged_plus_current'] = p['long_term_debt_tagged']['value'] + cur + stb
    if not out and (p['current_portion_ltd'] or p['short_term_borrowings']):
        out['noncurrent+current'] = cur + stb
    if not out and bs:
        out = {h: 0.0 for h in HYPOTHESES}               # balance sheet reported without any debt line
    return out, p


def _rollforward(q, pe, ce):
    kind = _span_kind(pe, ce)
    if not kind:
        return None
    tp, _ = _debt_totals(q, pe)
    tc, _ = _debt_totals(q, ce)
    iss, rep = q.best('debt_issued', ce, kind), q.best('debt_repaid', ce, kind)
    if not (iss or rep or any(q.best(m, ce, kind) for m in ('cfo', 'cff'))):
        return None                                       # no cash-flow statement for the span: cannot test
    flows = (abs(iss['value']) if iss else 0.0) - (abs(rep['value']) if rep else 0.0)
    ta = q.best('total_assets', ce)
    hyp = {}
    for h in HYPOTHESES:
        if h in tp and h in tc:
            exp = tp[h] + flows
            tol = max(ROLLFORWARD_TOL * max(abs(tp[h]), abs(tc[h])), .002 * abs(ta['value']) if ta else 0.0)
            hyp[h] = {'prior': tp[h], 'current': tc[h], 'expected': exp, 'residual': tc[h] - exp, 'tolerance': tol,
                      'pass': abs(tc[h] - exp) <= tol}
    if not hyp:
        return None
    passing = [h for h, r in hyp.items() if r['pass']]
    dist = 'tagged_total' in hyp and any(abs(hyp['tagged_plus_current'][k] - hyp['tagged_total'][k]) > hyp['tagged_total']['tolerance']
                                         for k in ('prior', 'current'))
    rec = {'prior': pe, 'current': ce, 'kind': kind, 'issued': abs(iss['value']) if iss else None,
           'repaid': abs(rep['value']) if rep else None, 'flows_absent_taken_as_zero': not (iss or rep),
           'hypotheses': hyp, 'passing': passing, 'distinguishable': dist}
    q.add('debt_rollforward', 'pass' if passing else 'fail', 'major',
          f'debt {pe} -> {ce}: prior + issued {_fmt(rec["issued"] or 0)} - repaid {_fmt(rec["repaid"] or 0)}; '
          + ', '.join(f"{h} residual {_fmt(r['residual'])} ({'pass' if r['pass'] else 'fail'})" for h, r in hyp.items()),
          detail=rec, period=f'{pe}..{ce}', fields=('debt',))
    return rec


def _tagged_winner(pr):
    w = [h for h in pr['passing'] if h in ('tagged_total', 'tagged_plus_current')]
    return w[0] if pr['distinguishable'] and len(w) == 1 else None


def _debt(q, cs, mcap):
    fb, ccy, notes, checks = q.fb, q.ccy, [], []
    bs_end = q.latest_bs()
    dates = []
    for m in DEBT_PARTS:
        for e, _ in q.timeline(m, 'instant'):
            if not any(abs(_days(x, e)) <= 7 for x in dates):
                dates.append(e)
    dates.sort()
    vanished = None
    if dates and bs_end and _days(dates[-1], bs_end) > 7:
        vanished = dates[-1]
        dates.append(bs_end)       # debt lines gone from the latest balance sheet: test "repaid" with the roll-forward
        notes.append(f'no debt line on the latest balance sheet ({bs_end}); roll-forward tests whether it was repaid')
    leases = None
    pairs = [r for r in (_rollforward(q, a, b) for a, b in zip(dates, dates[1:])) if r]
    if pairs:
        checks.append('debt_rollforward')
    value = method = end = None
    decisive, basis, facts, status = False, None, [], None
    scope = {}
    if dates:
        end = dates[-1]
        tot, parts = _debt_totals(q, end)
        latest = pairs[-1] if pairs and pairs[-1]['current'] == end else None
        nc, tg = parts['long_term_debt'], parts['long_term_debt_tagged']
        if nc:
            method, decisive, basis = 'noncurrent+current', True, 'unambiguous_noncurrent_tag'
            if tg:
                cur = parts['current_portion_ltd']['value'] if parts['current_portion_ltd'] else 0.0
                scope['direct'] = ('tagged_plus_current' if _rel(tg['value'], nc['value']) <= q.tol['debt'] else
                                   'tagged_total' if _rel(tg['value'], nc['value'] + cur) <= q.tol['debt'] else 'inconsistent')
                if scope['direct'] == 'inconsistent':
                    notes.append(f"LongTermDebt tag {_fmt(tg['value'])} matches neither the noncurrent line nor noncurrent + current")
        elif tg:
            gap = abs(tot['tagged_plus_current'] - tot['tagged_total'])
            hist = [w for w in (_tagged_winner(pr) for pr in pairs) if w]
            wit, cur_p = q.best('debt_noncurrent_witness', end), parts['current_portion_ltd']
            tot_w = q.best('debt_total_witness', end)
            win = None if not (tot_w and gap > 0) else (
                'tagged_total' if _rel(tot['tagged_total'], tot_w['value']) <= q.tol['debt'] else
                'tagged_plus_current' if _rel(tot['tagged_plus_current'], tot_w['value']) <= q.tol['debt'] else None)
            if win:
                # the filer's own total including current maturities says which scope the LongTermDebt-type tag has
                method, decisive, basis = win, True, 'total_witness'
                scope['total_witness'] = {'concept': tot_w.get('concept'), 'value': tot_w['value']}
            elif wit and cur_p and gap > 0 and _rel(tg['value'], wit['value'] + cur_p['value']) <= q.tol['debt']:
                # the face lines add up to the tag: noncurrent (another tag) + current = LongTermDebt, so the tag is the total
                method, decisive, basis = 'tagged_total', True, 'noncurrent_witness'
                scope['noncurrent_witness'] = {'concept': wit.get('concept'), 'value': wit['value']}
            elif latest and _tagged_winner(latest):
                method, decisive, basis = _tagged_winner(latest), True, 'latest_rollforward'
            elif len(hist) >= 2 and len(set(hist)) == 1:
                method, decisive, basis = hist[0], True, 'history'
            elif gap <= q.tol['debt'] * max(abs(tot['tagged_total']), abs(tot['tagged_plus_current']), 1.0):
                method, decisive, basis = (hist[-1] if hist else 'tagged_total'), True, 'no_ambiguity'
            else:
                method, decisive, basis = 'tagged_plus_current', False, 'unresolved_conservative'
            scope['gap_between_hypotheses'] = gap
            if not decisive:
                material = gap > q.tol['debt'] * max(tot.values()) and (mcap is None or gap > .005 * mcap)
                scope['material'] = material
                notes.append(f"scope of the LongTermDebt tag unresolved (tagged {_fmt(tot['tagged_total'])} vs tagged + current "
                             f"{_fmt(tot['tagged_plus_current'])}); conservative choice: tagged + current"
                             + (' - material' if material else ' - immaterial'))
        elif tot:
            method, decisive, basis = 'noncurrent+current', True, 'current_or_none_tagged'
        if method:
            value = tot[method]
            use = {'noncurrent+current': ('long_term_debt', 'current_portion_ltd', 'short_term_borrowings'),
                   'tagged_total': ('long_term_debt_tagged', 'short_term_borrowings'),
                   'tagged_plus_current': ('long_term_debt_tagged', 'current_portion_ltd', 'short_term_borrowings')}[method]
            if ((parts['current_portion_ltd'] or {}).get('concept') or '').lower() == 'debtcurrent' and parts['short_term_borrowings']:
                extra = _current_debt(parts['current_portion_ltd'], parts['short_term_borrowings'])[1]
                scope['current_concept'] = 'debtcurrent'
                notes.append(f"current debt is tagged DebtCurrent ({_fmt(parts['current_portion_ltd']['value'])}), which already includes "
                             f"short-term borrowings and commercial paper ({_fmt(parts['short_term_borrowings']['value'])}): "
                             + (f'only the excess {_fmt(extra)} added' if extra else 'not added again'))
                if not extra:
                    use = tuple(m for m in use if m != 'short_term_borrowings')
            facts = [parts[m] for m in use if parts[m]]
            rf_ok = bool(latest and method in latest['passing'])
            sts = []
            for g in facts:
                s, c, n = _status_of(q, g['metric'], g, corroborated=rf_ok)
                sts.append(s)
                checks += c
                notes += n
                if 'lease' in (g.get('concept') or ''):
                    notes.append(f"{g['metric']} {g['period']} concept {g['concept']} includes finance-lease obligations")
                if 'fairvalue' in (g.get('concept') or ''):
                    sts.append('single_source')
                    notes.append(f"{g['metric']} uses a fair-value concept ({g['concept']}), not principal/carrying value")
            ttm_i, rev_i = F.ttm(fb, 'interest_expense'), F.ttm(fb, 'revenue')
            paying = bool(ttm_i and ttm_i['value'] > 0 and (not rev_i or ttm_i['value'] > .002 * abs(rev_i['value'])))
            if not facts and vanished and not rf_ok and (_days(vanished, end) <= 400 or paying):
                # lines tagged until the prior balance sheet, none now, and no repayment in the cash flows: usually a tag the
                # map does not read (Harrow 2026-06-30: LongTermNotesPayable after LongTermDebt), never a silent zero
                sts.append('conflict')
                q.add('debt_line_continuity', 'fail', 'major',
                      f'debt lines tagged until {vanished} are absent at {end} and no roll-forward shows a repayment'
                      + (' while interest expense continues' if paying else '') + ': not taken as zero; read the balance sheet',
                      period=end, fields=('debt',), detail={'last_tagged': vanished, 'balance_sheet': end, 'interest_paid': paying})
                checks.append('debt_line_continuity')
                notes.append(f'debt lines tagged until {vanished} are absent at {end} without a repayment: unresolved (blocking)')
            elif not facts:
                sts.append('reported' if rf_ok else 'single_source')
                notes.append(f'no debt line on the balance sheet dated {end}: taken as zero'
                             + (' (consistent with the roll-forward)' if rf_ok else ''))
            status = _weakest(sts)
            if not decisive and scope.get('material'):
                status = _weakest([status, 'single_source'])
            # vendor total: definitional differences (leases, scope) are recorded, not conflicts
            vt = q.best('debt_total_vendor', end)
            lc, ln = q.v('operating_lease_current', end), q.v('operating_lease_noncurrent', end)
            if lc is not None or ln is not None:
                leases = (lc or 0.0) + (ln or 0.0)
            if vt:
                r = _rel(value, vt['value'])
                alt = {h: t for h, t in tot.items() if h != method and _rel(t, vt['value']) <= q.tol['debt']}
                why = ('vendor total includes operating lease liabilities' if leases and _rel(value + leases, vt['value']) <= q.tol['debt']
                       else f'vendor applies the {next(iter(alt))} scope; roll-forward supports {method}' if alt else None)
                ok = r <= q.tol['debt']
                q.add('cross_source', 'pass' if ok or why else 'fail', 'minor' if ok or why else 'major',
                      f"debt {end}: resolved {_fmt(value)} vs {q.provider(vt)} total {_fmt(vt['value'])}" + (f' ({why})' if why and not ok else ''),
                      period=end, fields=('debt',), detail={'resolved': value, 'vendor': vt['value'], 'rel_diff': round(r, 6), 'definition': why})
                checks.append('cross_source')
                indep = q.family(vt) not in {q.family(g) for g in facts} and vt['source_id'] not in {g['source_id'] for g in facts} \
                    and not q.secondary(vt)
                if ok and indep and status == 'reported' and decisive:      # a vendor total never lifts a non-filing value
                    status = 'verified'
                elif why and not ok:
                    notes.append(f"{q.provider(vt)} total {_fmt(vt['value'])}: {why}")
                elif not ok and not rf_ok:
                    status = 'conflict'
                    notes.append(f"vendor total {_fmt(vt['value'])} disagrees with {_fmt(value)} and no roll-forward corroborates either")
            q.add('debt_scope', 'pass' if decisive or not scope.get('material') else 'fail', 'major',
                  f'debt at {end}: {method} = {_fmt(value)} ({basis})', period=end, fields=('debt',),
                  detail={'method': method, 'decisive': decisive, 'basis': basis, 'candidates': tot,
                          'parts': {m: (parts[m]['value'] if parts[m] else None) for m in DEBT_PARTS},
                          'history': [_tagged_winner(pr) for pr in pairs], **scope})
            checks.append('debt_scope')
    if value is None:
        fv = q.timeline('debt_fair_value', 'instant')
        if fv:     # APH: debt only tagged at fair value -> a proxy, never silently principal
            end, g = fv[-1]
            value, method, basis, facts, status = g['value'], 'fair_value_proxy', 'fair_value_only', [g], 'single_source'
            notes.append('only a fair-value debt figure is available: used as a proxy for principal (limited)')
        else:
            bs_ok = bs_end is not None
            ttm_int = F.ttm(fb, 'interest_expense')
            rev = F.ttm(fb, 'revenue')
            sig = ttm_int and ttm_int['value'] > 0 and (not rev or ttm_int['value'] > .002 * abs(rev['value']))
            if bs_ok and not sig:
                value, method, basis, end, status = 0.0, 'none_tagged', 'none_tagged', bs_end, 'reported'
                notes.append(f'no debt line on the balance sheet dated {bs_end} and no material interest expense: taken as zero')
            else:
                status = 'missing'
                notes.append('no debt line usable at as_of' + (f"; interest expense {_fmt(ttm_int['value'])} indicates debt exists but is not captured"
                                                               if sig else ''))
    stmt_value, stmt_end = value, end
    if cs:
        def expl(sv, cv):
            if leases and _rel(sv, cv - leases) <= q.tol['debt']:
                return 'capital structure includes lease lines as debt'
            return 'carrying value below principal (unamortized discount/issuance costs)' if 0 < cv - sv <= .03 * cv else None
        use, corr, conf, note = _cs_compare(q, cs, 'debt', 'debt', stmt_value, stmt_end,
                                            ('debt', {k for k in capital.DEBT_KINDS if k != 'lease'}), expl,
                                            {g['source_id'] for g in facts})
        notes.append(note)
        checks.append('capital_structure_crosscheck')
        if conf:
            status = 'conflict'
        elif corr == 'independent' and status == 'reported':
            status = 'verified'
        if use:
            lines = cs['lines']('debt', {k for k in capital.DEBT_KINDS if k != 'lease'})
            notes.append(f'statement debt at {stmt_end}: {_fmt(stmt_value)} ({method})' if stmt_end else 'no statement debt: capital structure only')
            value, method, basis, end = cs['debt_amount'](lines), 'capital_structure', 'capital_structure', cs['as_of']
            status = cs['grade'](lines) if lines else 'reported'
            if not corr and stmt_value is not None:
                status = _weakest([status, 'single_source'])
            facts, decisive = [], True
    fld = _field(q, 'debt', status or 'missing', value, ccy, end, facts, checks, notes, method=method, decisive=decisive,
                 decisive_basis=basis, lease_liabilities_excluded=leases)
    if value is not None:
        _stale(q, fld, 'balance_sheet', 'balance sheet')
    res = {'value': value, 'as_of': end, 'method': method, 'decisive': decisive, 'decisive_basis': basis, 'status': fld['status'],
           'source_ids': fld['sources'] or (cs['source_ids'] if cs and method == 'capital_structure' else []),
           'definition': DEFINITIONS['debt']}
    if cs and cs['summary'] and method == 'capital_structure':
        res['claims_incl_accretion'] = cs['summary']['debt_claims']
    lease = {'value': leases, 'as_of': stmt_end if leases is not None else None, 'included_in_debt': False,
             'definition': 'operating lease liabilities (current + noncurrent); excluded from debt and net cash'}
    return fld, res, lease


# ---------------------------------------------------------------- convertibles and post-period events

CONVERT_MATERIAL = .02          # convertible amount / market value below which missing terms do not block


def _convert_share(fb, ev):
    """Largest recent convertible amount in the evidence / market value (price x latest point-in-time shares); None when
    either is unknown."""
    amounts = [e.get('value') for e in ev if e.get('kind') == 'fact' and e.get('value')]
    px = ((fb.get('market') or {}).get('price') or {}).get('value')
    sh = [f for f in fb['facts'] if f['metric'] in ('shares_outstanding_cover', 'shares_outstanding') and F.usable(fb, f)]
    if not amounts or not px or not sh:
        return None
    shares = max(sh, key=lambda f: f['period_end'])['value']
    return max(amounts) / (px * shares) if shares > 0 else None


def _convertibles(q, cs):
    fb, ev = q.fb, []
    for f in fb['facts']:
        txt = (f['metric'] + ' ' + (f.get('concept') or '')).lower()
        if F.usable(fb, f) and (('convertible' in txt and f['value']) or f.get('conv')) and q.age(f['period_end']) <= q.max_age['annual']:
            ev.append({'kind': 'fact', **_ref(f)})
    ev += [{'kind': 'qualitative', 'id': x['id'], 'statement': x.get('statement')} for x in fb.get('qualitative', [])
           if x.get('topic') in CONVERT_TOPICS and q.dated(x, missing=True)]
    if fb['company'].get('conv') or fb['company'].get('has_convertibles'):
        ev.append({'kind': 'company_flag'})
    blocks, rows, gaps = False, [], []
    if cs:
        for x in cs['converts']:
            miss = [k for k in ('principal', 'conversion_price', 'maturity') if x.get(k) in (None, '')]
            if miss:
                gaps.append({'id': x['id'], 'missing_terms': miss})
                continue
            try:
                row = {'id': x['id'], 'principal': float(x['principal']), 'conversion_price': float(x['conversion_price']),
                       'maturity': x['maturity'], 'conversion_shares': capital.conversion_shares(x), 'source_id': x['source_id']}
                row['accreted_claim'] = capital.accreted_value(x, cs['as_of'])
            except (ValueError, KeyError, TypeError) as e:
                gaps.append({'id': x['id'], 'missing_terms': [f'usable terms ({e})']})
                continue
            rows.append(row)
        if gaps:
            status, blocks, note = 'missing', True, f'convertible instrument(s) without terms: {gaps}'
        elif rows:
            status, note = cs['grade']([x for x in cs['converts']]), f'{len(rows)} convertible instrument(s) with terms from the capital structure'
        elif ev:
            status, blocks, note = 'missing', True, f'evidence of convertibles ({len(ev)} item(s)) but the capital structure lists none'
        else:
            status, note = 'reported', f"capital structure as of {cs['as_of']} lists no convertible instruments"
    elif (fb.get('convertible_terms') or {}).get('instruments'):
        # terms read from the filings' inline XBRL by instrument (pipeline._business): complete instruments are used, an
        # instrument without a principal or conversion price still blocks (the footnote must be read)
        ct = fb['convertible_terms']
        for x in ct['instruments']:
            miss = [k for k in ('principal', 'conversion_price') if not x.get(k)]
            if miss:
                gaps.append({'id': x['id'], 'missing_terms': miss})
                continue
            src = next((sid for sid in x['source_ids'] if sid in fb['sources']), None)
            rows.append({'id': x['id'], 'principal': float(x['principal']), 'conversion_price': float(x['conversion_price']),
                         'maturity': x.get('maturity'), 'conversion_shares': float(x['principal']) / float(x['conversion_price']),
                         'accreted_claim': float(x['principal']), 'source_id': src, 'price_basis': x.get('price_basis'),
                         'maturity_basis': x.get('maturity_basis')})
        if gaps:
            status, blocks, note = 'missing', True, f'convertible instrument(s) in the filings without terms: {gaps}'
            share = _convert_share(fb, ev)
            if share is not None and share < CONVERT_MATERIAL:
                status, blocks = 'single_source', False
                note += f'; about {share:.1%} of market value (below {CONVERT_MATERIAL:.0%}): immaterial, not a block'
        else:
            status = 'reported' if all(fb['sources'].get(r['source_id'], {}).get('kind') in FILING_KINDS for r in rows) else 'single_source'
            note = (f"{len(rows)} convertible instrument(s) with terms from the filings' inline XBRL as of {ct['as_of']} "
                    '(principal at par: accreting notes need the footnote)')
    elif ev:
        status, blocks, note = 'missing', True, 'evidence of convertibles without instrument terms (supply a capital structure)'
        share = _convert_share(fb, ev)
        if share is not None and share < CONVERT_MATERIAL:
            # immaterial: terms cannot move the value much (ONDS $5m; ServiceNow's senior notes tagged as convertible). A noted
            # limitation, not a block: a disqualifier needs materiality
            status, blocks = 'single_source', False
            note = (f'convertible evidence without terms, about {share:.1%} of market value (below {CONVERT_MATERIAL:.0%}): immaterial '
                    'to value and dilution; terms not required (check the debt footnote if the tag is a mis-label)')
    else:
        status, note = 'missing', 'no capital structure and no convertible evidence in the FactBook: confirm from the latest debt footnote'
    q.add('convertible_terms', 'fail' if blocks else 'pass' if status != 'missing' else 'skip', 'critical' if blocks else 'minor',
          note, detail={'evidence': ev, 'instruments': rows, 'gaps': gaps}, fields=('convertibles',))
    value = sum(r['principal'] for r in rows) if rows else (0.0 if status == 'reported' else None)
    fld = _field(q, 'convertibles', status, value, q.ccy, cs['as_of'] if cs else (fb.get('convertible_terms') or {}).get('as_of'),
                 checks=['convertible_terms'], notes=[note],
                 instruments=rows, evidence=ev, conversion_shares=sum(r['conversion_shares'] for r in rows) if rows else None)
    fld['_blocks'] = blocks
    return fld


def _post_period(q, cs, bs_end):
    """Events after the latest balance sheet: post-period items dated after it (an older event reviews nothing since)
    and capital-structure lines dated after it (_capital already dropped every line dated after as_of)."""
    fb = q.fb
    items = [x for x in fb.get('qualitative', []) if x.get('topic') in POST_TOPICS and q.dated(x)
             and (not bs_end or not x.get('as_of') or _days(bs_end, x['as_of']) > -7)]
    later = []
    if cs and bs_end:
        for g in ('cash', 'shares', 'debt', 'quasi_debt'):
            later += [{'group': g, 'id': x['id'], 'date': _line_date(x, cs), 'source_id': x['source_id']}
                      for x in cs['raw'].get(g, []) if _days(bs_end, _line_date(x, cs)) > 7]
    if items or later:
        kinds = {fb['sources'].get(s, {}).get('kind') for x in items for s in x.get('source_ids', [])} | \
                {fb['sources'].get(x['source_id'], {}).get('kind') for x in later}
        status = 'reported' if kinds & FILING_KINDS else 'single_source'
        note = f'{len(items)} recorded post-period event(s)' + (f'; {len(later)} capital-structure line(s) dated after {bs_end}' if later else '')
    else:
        status = 'missing'
        note = f'no post-period review recorded after the latest balance sheet ({bs_end}): check 8-K/6-K filings for issuance, converts, M&A, buybacks'
    q.add('post_period_review', 'pass' if status != 'missing' else 'skip', 'minor', note, fields=('post_period_events',),
          detail={'events': [{'id': x['id'], 'statement': x.get('statement'), 'kind': x.get('kind'), 'as_of': x['as_of']} for x in items],
                  'capital_structure_lines': later})
    fld = _field(q, 'post_period_events', status, len(items) + len(later) if status != 'missing' else None, 'count', bs_end,
                 checks=['post_period_review'], notes=[note], events=[x['id'] for x in items], capital_structure_lines=later)
    fld['_blocks'] = False
    return fld


# ---------------------------------------------------------------- TTM flows

def _ttm(q, metric):
    """facts.ttm unchanged (value, period, method): the facts are re-pointed at the best fact of each period only for
    the status. The value is never re-summed - annual_plus_ytd subtracts the year-earlier quarters (facts.ttm signs);
    payments (capex, buybacks, ...) are summed by magnitude per fact there."""
    t = F.ttm(q.fb, metric)
    if not t:
        return None
    signs = t.get('signs') or [1] * len(t['facts'])
    value = t['value']
    fs = [q.best(metric, f['period_end'], _kind(f)) or f for f in t['facts']]
    return {**t, 'facts': fs, 'signs': signs, 'value': value,
            'repointed_value': sum(s * _val(metric, f) for s, f in zip(signs, fs))}


def _ttm_field(q, name, metric):
    t = _ttm(q, metric)
    if not t:
        return _field(q, name, 'missing', None, q.ccy, None, notes=[f'no {metric} usable at as_of'])
    sts, checks, notes = [], [], []
    for f in t['facts']:
        s, c, n = _status_of(q, metric, f)
        sts.append(s)
        checks += c
        notes += n
    ref = F.ttm(q.fb, metric)['value']
    ok = _rel(abs(t['value']), abs(ref)) <= 1e-9
    q.add('ttm_matches_facts', 'pass' if ok else 'fail', 'minor' if ok else 'critical',
          f"{name} {_fmt(t['value'])} vs facts.ttm {_fmt(ref)} ({t['method']}, {t['period_end']})", period=t['period_end'],
          fields=(name,), detail={'value': t['value'], 'facts_ttm': ref, 'method': t['method'], 'signs': t['signs']})
    checks.append('ttm_matches_facts')
    if not ok:
        sts.append('conflict')
    if _rel(t['repointed_value'], t['value']) > q.tol.get(metric, DEFAULT_TOL):
        notes.append(f"the best fact of each period gives {_fmt(t['repointed_value'])}: facts.ttm value kept, see cross_source")
    fld = _field(q, name, _weakest(sts), t['value'], q.ccy, t['period_end'], t['facts'], checks, notes,
                 method=t['method'], period_end=t['period_end'], signs=t['signs'])
    if t.get('ytd_rejected'):
        fld['note'] = '; '.join(x for x in (fld['note'], t['ytd_rejected']) if x)
    if t['method'] == 'latest_fiscal_year':
        fld['note'] = '; '.join(x for x in (fld['note'], f"TTM is the fiscal year ended {t['period_end']} (no four contiguous quarters)") if x)
    _stale(q, fld, 'ttm', 'trailing period' + (' (quarterly data missing)' if t['method'] == 'latest_fiscal_year' else ''))
    return fld


# ---------------------------------------------------------------- researcher conflicts (evidence bundle)

CONFLICT_FIELDS = (('revenue_ttm', ('revenue',)), ('operating_income_ttm', ('operating income',)),
                   ('cfo_ttm', ('operating cash flow', 'cash from operations', 'cfo')), ('capex_ttm', ('capex', 'capital expenditure')),
                   ('sbc_ttm', ('sbc', 'stock-based compensation', 'share-based compensation')),
                   ('shares_outstanding', ('shares outstanding', 'share count', 'cover-page count')),
                   ('cash_and_liquid_investments', ('cash and cash equivalents', 'short-term investments')),
                   ('debt', ('debt', 'borrowings', 'commercial paper')), ('convertibles', ('convertible',)))


def _research_conflicts(q, fields):
    """Unresolved conflicts the researcher recorded in the evidence bundle (fb['research_notes']['conflicts']) become
    'research_conflict' checks (major when they name a critical field, which also gets a note). Resolved ones
    (resolved: true, or a description starting 'RESOLVED') are skipped. The text is data, never instructions."""
    for c in (q.fb.get('research_notes') or {}).get('conflicts') or []:
        c = c if isinstance(c, dict) else {'description': str(c)}
        desc = str(c.get('description') or c.get('text') or '')
        if c.get('resolved') is True or desc.strip().upper().startswith('RESOLVED'):
            continue
        text = (desc + ' ' + str(c.get('metric') or '')).lower()
        hit = [n for n, words in CONFLICT_FIELDS if any(re.search(r'\b' + re.escape(w) + r'\b', text) for w in words)
               and not (n in ('revenue_ttm', 'operating_income_ttm') and 'non-gaap' in text)]   # the TTM fields are GAAP
        q.add('research_conflict', 'fail', 'major' if hit else 'minor', f"unresolved researcher conflict ({c.get('topic') or 'unspecified'}): {desc[:240]}",
              fields=hit, detail={'topic': c.get('topic'), 'description': desc, 'resolved': c.get('resolved'),
                                  'source_ids': [('web:' + s if 'web:' + s in q.fb['sources'] else s) for s in c.get('source_ids') or []]})
        for n in hit:
            fld = fields[n]
            fld['note'] = '; '.join(x for x in (fld['note'], f'unresolved researcher conflict: {desc[:160]}') if x)
            fld['checks'] = sorted(set(fld['checks']) | {'research_conflict'})


# ---------------------------------------------------------------- entry point

def validate(fb, capital_structure=None, options=None):
    """Validate the critical fields of a FactBook at fb['as_of'] (docs/MODULE_CONTRACTS.md, dataqa.validate).

    capital_structure: capital.py structure (defaults to fb['capital_structure']); its instruments are the source
    for convertibles and a cross-check for cash, debt and shares (or the source, when newer than the statements).
    options: {'tolerances': {metric: relative}, 'max_age': {'shares'|'balance_sheet'|'ttm'|'annual': days}, 'resolutions': [analyst
    resolutions of a cross-source disagreement (RESOLUTIONS; filing evidence required; recorded in out['resolutions'])]}."""
    q = _QA(fb, options)
    late = [f for f in fb['facts'] if not F.usable(fb, f)]
    est = sum(1 for f in fb['facts'] if F.usable(fb, f) and f.get('available_at_estimated'))
    q.add('lookahead_excluded', 'pass', 'minor', f'{len(late)} fact(s) published after {q.as_of} ignored; '
          f'{est} usable fact(s) carry estimated availability dates',
          detail={'excluded': len(late), 'excluded_metrics': sorted({f['metric'] for f in late}), 'estimated_availability': est})
    cs = _capital(q, fb.get('capital_structure') if capital_structure is None else capital_structure)
    _identities(q)
    fields, res = {}, {}
    fields['shares_outstanding'], res['shares_outstanding'], splits, unresolved = _shares(q, cs)
    price, sh = q.price(), res['shares_outstanding']['value']
    mcap = price * sh if price and sh else None
    fields['cash_and_liquid_investments'], fields['investments'], cash_res = _cash(q, cs)
    res.update(cash_res)
    fields['debt'], res['debt'], res['lease_liabilities'] = _debt(q, cs, mcap)
    fields['convertibles'] = _convertibles(q, cs)
    fields['post_period_events'] = _post_period(q, cs, q.latest_instant())
    for name, metric in TTM_FIELDS:
        fields[name] = _ttm_field(q, name, metric)
    _research_conflicts(q, fields)
    ends = [x[-1][0] for x in (q.timeline(m, 'annual') for m in ('revenue', 'cfo')) if x]
    if ends:
        age = q.age(max(ends))
        q.add('annual_recency', 'fail' if age > q.max_age['annual'] else 'pass', 'major' if age > q.max_age['annual'] else 'minor',
              f'latest fiscal year ended {max(ends)} ({age} days before {q.as_of})', detail={'latest_fiscal_year_end': max(ends), 'age_days': age})
    c, d = res['cash'], res['debt']
    if c['value'] is not None and d['value'] is not None:
        sti = res['short_term_investments']['value'] or 0.0
        lti = res['long_term_investments']['value'] or 0.0
        st = _weakest([fields['cash_and_liquid_investments']['status'], fields['debt']['status']])
        res['net_cash'] = {'value': c['value'] + sti - d['value'], 'as_of': c['as_of'], 'status': st,
                           'formula': f"cash {_fmt(c['value'])} + short_term_investments {_fmt(sti)} - debt {_fmt(d['value'])}",
                           'net_cash_incl_long_term_investments': c['value'] + sti + lti - d['value'],
                           'lease_liabilities_excluded': res['lease_liabilities']['value'],
                           'restricted_cash_excluded': res['restricted_cash']['value'], 'definition': DEFINITIONS['net_cash'],
                           'dates_differ': c['as_of'] != d['as_of'] and abs(_days(c['as_of'], d['as_of'])) > 7}
    else:
        res['net_cash'] = {'value': None, 'as_of': None, 'status': 'missing', 'definition': DEFINITIONS['net_cash']}
    res['definitions'] = dict(DEFINITIONS)
    blocking, limited = [], []
    for name in CRITICAL:
        fld = fields[name]
        blocks = fld.pop('_blocks', None)
        if blocks is None:
            blocks = name in BLOCKING and fld['status'] in ('conflict', 'missing')
        fld['blocking'] = blocks
        if blocks:
            blocking.append(name)
        elif fld['status'] in ('conflict', 'missing', 'stale', 'single_source') or fld.get('incomplete'):
            limited.append(name)
    applied = _apply_resolutions(q, fields, limited) if q.resolutions else None
    return {'status': 'blocked' if blocking else 'limited' if limited else 'ok', 'as_of': q.as_of,
            'fields': fields, 'checks': q.checks, 'resolved': res, 'blocking': blocking, 'limited': limited,
            'splits': splits, 'unresolved_splits': unresolved, **({'resolutions': applied} if applied is not None else {})}
