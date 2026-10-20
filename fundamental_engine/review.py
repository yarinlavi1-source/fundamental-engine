"""Independent review between valuation and decision (pipeline stage 'review').

Three layers; none of them proves that a source is true:
1. mechanical: dataqa status, the valuation underwriting audit and the red-team. The red-team
   package is DERIVED from executed facts: capital structure from the FactBook (or, when absent, a
   minimal capital.py structure built from dataqa's resolved balances), reported balances from
   dataqa, model flags from the executed driver spec and valuation rows. Flags a forecast declares
   about itself are never read: a model whose rows show a funding gap fails the funding check even
   if its author wrote 'financing modeled'. When the structure is built from dataqa, the cash, debt
   and share reconciliations pass by construction and are labelled so; the substantive opening test
   is then the consistency check 'opening_vs_validated_data'.
2. consistency: scenario order today, terminal ROIC versus demonstrated returns, terminal growth
   versus the risk-free rate, year-1 revenue versus consensus, margins versus best history, the
   share-count path versus history, and the valuation opening versus validated balances.
3. reviewer: an optional signed, independent review input. It is validated and recorded, never
   fabricated. It is ACCEPTED only when valid, independent and bound to this valuation (valuation_sha256
   equals the executed input_sha256; reviewed_at between the valuation cutoff and today) AND to the analyst's
   inputs (inputs_sha256 equals inputs_digest: the executed valuation plus inputs.decision, inputs.card and
   inputs.forecast, so an analyst override added after the review voids it). A reviewer whose normalized name
   is an author-side signer (a case assumption 'reviewer', a forecast 'reviews' signer or the analyst named
   in the inputs) is a self-review, never independent. Its assumption
   sign-offs are applied by RE-RUNNING the underwriting audit on a copy of the executed case in which every
   author-side review status is reset first: 'reviewed' statuses written in the forecast spec (or its
   'reviews' input) are author notes, never sign-offs, so no price conclusion exists without an accepted
   independent reviewer, whatever the valuation status. A sign-off cannot close a gap that is not a review
   gap; a gap that even a full sign-off leaves open (e.g. no primary source behind the opening balance) is
   reported as such ('primary_evidence_missing'), never as a missing review.

The price conclusion needs every gate; `reasons` explains each one that is closed. Demo cases never
pass. Evidenced going-concern doubt, material weakness or restatement (risk register withhold_price)
withholds the conclusion until the accepted reviewer documents an override (risk_overrides) citing at least one
FactBook filing or issuer document other than the flag's own evidence, and nothing weaker than filing evidence
(market feeds, consensus, vendor, synthetic, secondary and analyst-written case sources are rejected). A business
not yet understood (model card completeness insufficient) withholds the conclusion as an
evidence gap ('understanding_insufficient', status unreviewed_draft), never as a disqualifying judgement.
needs_explanation / needs_evidence items are open questions for the reviewer, not vetoes.
"""
from copy import deepcopy
from datetime import date
import re
from . import redteam
from .facts import digest
from .valuation import years
from .valuation_audit import audit_valuation

REVIEW_ONLY = {'unreviewed_assumption', 'evidence_missing'}
UNDERWRITTEN = ('conditional_valuation', 'underwriting_required', 'unreviewed')
AUTHOR_NOTES = ("review statuses written by the forecast author (spec review_status / forecast 'reviews') are notes; "
                'only an accepted independent reviewer signs an assumption off')
SEVERITIES = {'critical', 'major', 'minor'}
SBC_MIN_SHARE = .5      # modelled SBC share of revenue below half of history is a token charge unless a fade is evidenced
HARD = {'dataqa_missing', 'data_blocked', 'risk_not_assessed', 'disqualifying_risk', 'redteam_not_usable',
        'consistency_failure', 'critical_finding_unresolved'}
BOUND_INPUTS = ('decision', 'card', 'forecast')           # analyst inputs the reviewer's inputs_sha256 covers
RELEASE_CLASSES = {'filing_document', 'filing_dataset'}   # decision._evidence_class values a red-flag release may cite
RELEASE_DOCS = 'issuer_document, primary_document or document'


def num(x):
    if isinstance(x, dict):
        x = x.get('value')
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float('inf'), float('-inf')):
        return None
    return float(x)


def metric(an, name):
    return num(((an or {}).get('metrics') or {}).get(name))


def scen(obj, name):
    return next((s for s in (obj or {}).get('scenarios') or [] if s.get('name') == name), None)


def resolved(qa, key):
    return num(((qa or {}).get('resolved') or {}).get(key))


def valuation_parts(val):
    """(value_company result, executed case, today's row, quote) from the pipeline bundle; Nones when not executed."""
    result = val.get('result') if isinstance(val, dict) else None
    if not isinstance(result, dict) or not result.get('annual_values'):
        return None, None, {}, None
    case = result.get('reproduction_input') or val.get('case')
    today = result['annual_values'][0]
    return result, case, today, num(today.get('current_quote')) or num(case['quote']['price'])


def annual_revenue(row):
    d = years(row['start'], row['end'])
    return row['revenue'] / d if d > 0 else None


# ---------------------------------------------------------------- red-team derived from execution

def derive_model(an, fc, result, case, today):
    """Red-team model flags computed from the executed spec/rows; provenance says how each was obtained."""
    model, prov = {}, {}

    def put(k, v, how):
        if v is not None:
            model[k] = v
        prov[k] = {'value': v, 'derived_from': how}
    spec = (fc or {}).get('spec') or {}
    start = num(spec.get('last_full_year_revenue'))
    recognized = [(e, num((row or {}).get('revenue'))) for e, row in sorted(((an or {}).get('annual') or {}).items())]
    if num(((an or {}).get('ttm') or {}).get('revenue')) is not None:
        recognized.append(('ttm', num(an['ttm']['revenue'])))
    # recognized revenue less evidenced one-offs (inputs.forecast.nonrecurring) is still a recognized basis
    adj = ((fc or {}).get('anchors') or {}).get('revenue_adjusted_for_one_offs') or {}
    recognized += [(f'{e} less evidenced one-offs', num(v)) for e, v in sorted(adj.items()) if num(v)]
    tie = next(((e, v) for e, v in reversed(recognized) if v and start and abs(start / v - 1) <= .01), None)
    how = f'spec.last_full_year_revenue {start:g} ties to recognized revenue {tie[1]:g} ({tie[0]}) within 1%' if tie else None
    for a in (fc or {}).get('assumptions') or [] if start and not tie else []:
        v = a.get('values') if isinstance(a.get('values'), dict) else {}
        rv, g = num(v.get('revenue')), num(v.get('growth'))
        if rv is None or abs(rv / start - 1) > .01:
            continue
        if a.get('kind') == 'guidance' and a.get('source_ids'):
            tie, how = a['id'], f"start revenue {start:g} is dated guidance (assumption {a['id']}, sources {a['source_ids']})"
            break
        bridge = next(((e, r) for e, r in reversed(recognized) if g is not None and r and abs(r * (1 + g) / start - 1) <= .01), None)
        if bridge:
            tie, how = a['id'], (f"start revenue {start:g} = recognized {bridge[1]:g} ({bridge[0]}) x (1 {g:+.2%}) per documented assumption "
                                 f"{a['id']}: recognized basis with a one-step current-year bridge (the growth is an assumption under review)")
            break
    put('revenue_basis', 'recognized' if tie else None, how or
        ('no driver spec: start revenue cannot be traced' if start is None else
         f'spec.last_full_year_revenue {start:g} does not tie to recognized annual/TTM revenue, dated guidance or a documented one-step bridge: undeclared'))
    if spec:
        put('backlog_treatment', 'not_used', 'drivers.build_case has no backlog/RPO input; revenue = growth on recognized revenue')
    if not result or not case:
        return model, prov
    method = case.get('method')
    base_r, base_c = scen(result, 'base'), scen(case, 'base')
    put('valuation_basis', 'fcff' if method == 'operating' else method,
        f'valuation.{method}: explicit cash flows after capex, terminal FCFF = NOPAT x (1 - g/ROIC)' if method == 'operating'
        else f'valuation method {method} as executed')
    put('terminal_share', num((base_r or {}).get('terminal_share_of_present_value')),
        'valuation result base.terminal_share_of_present_value (PV of terminal equity / value today)')
    vals = {k: num(today.get(k)) for k in ('bear', 'base', 'bull')}
    put('scenario_values', vals if None not in vals.values() else None,
        'valuation annual_values[0] (today)' if None not in vals.values() else 'a scenario value is withheld (funding gap) or missing')
    if method != 'operating' or not base_r or not base_c:
        if method == 'residual_income' and base_r:
            gap = sum(r.get('capital_shortfall', 0) for r in base_r['forecast'])
            put('funding_need', gap, 'residual_income base capital_shortfall rows')
            put('financing_modeled', gap <= 1e-7, 'no regulatory capital shortfall in base rows' if gap <= 1e-7 else 'capital shortfall not recapitalized')
        return model, prov
    put('prepayments_treatment', 'working_capital_with_reversal',
        'valuation.operating carries a deferred-revenue ledger: prepayments add cash and reverse as revenue is recognized')
    hist = metric(an, 'sbc_pct_revenue')
    fade = sorted(a['id'] for a in (fc or {}).get('assumptions') or [] if a.get('kind') in ('reported', 'guidance') and a.get('source_ids')
                  and re.search(r'\bsbc\b|stock[- ]based|share[- ]based|equity award', f"{a.get('id', '')} {a.get('topic', '')}".lower()))
    per = []
    for s, r in zip(case['scenarios'], result['scenarios']):
        rev = sum(x['revenue'] for x in r['forecast'])
        sbc = sum(num(p.get('sbc_expense')) or 0 for p in s['periods'])
        per.append((s['name'], s.get('sbc_policy'), sbc / rev if rev else None, sum(num(p.get('sbc_shares')) or 0 for p in s['periods'])))
    shown = {n: pct for n, _, pct, _ in per}
    # a token charge is not expensing: each scenario must carry at least half the historical SBC share unless a fade is evidenced
    token = [n for n, _, pct, _ in per if hist is not None and hist > .01 and (pct or 0) < SBC_MIN_SHARE * hist]
    if all(pol == 'cash_equivalent' and pct and pct > 0 for _, pol, pct, _ in per) and (not token or fade):
        put('sbc_treatment', 'expensed', f'every scenario charges SBC as a cash-equivalent cost (share of revenue {shown}); terminal margin includes SBC'
            + (f'; {token} below {SBC_MIN_SHARE:.0%} of the {hist:.1%} history on the evidenced fade {fade}' if token else ''))
        put('margin_basis', 'ebit_after_sbc', 'EBIT in the executed rows is after SBC expense')
    elif all(pol == 'explicit_shares' and sh > 0 for _, pol, _, sh in per):
        put('sbc_treatment', 'diluted', 'every scenario settles SBC in explicit new shares')
        put('margin_basis', 'ebit_after_sbc', 'EBIT in the executed rows is after SBC expense')
    elif hist is not None and hist > .01:
        put('margin_basis', 'adjusted_ebitda', f'a scenario charges no SBC or less than {SBC_MIN_SHARE:.0%} of the {hist:.1%} history '
            f'(share of revenue {shown}) without an evidenced fade (a reported/guidance SBC assumption with sources)')
        put('sbc_treatment', None, 'historical SBC is excluded (or reduced to a token charge) in at least one scenario and not modeled as dilution')
    elif hist is not None:
        put('sbc_treatment', 'expensed', f'historical SBC immaterial ({hist:.1%} of revenue); nothing material to expense')
    else:
        put('sbc_treatment', None, 'SBC history unavailable and the model charges none')
    ci = metric(an, 'capex_pct_revenue')
    put('capex_to_revenue_recent', ci, 'analysis.metrics.capex_pct_revenue' if ci is not None else 'capex/revenue history unavailable')
    rows = base_r['forecast']
    growing = [i for i in range(1, len(rows)) if (annual_revenue(rows[i]) or 0) > (annual_revenue(rows[i - 1]) or 0)]
    t = base_c['terminal']
    linked = not growing or any(rows[i]['growth_capex'] > 0 for i in growing)
    put('capex_linked_to_growth', linked,
        f"base rows: growth capex in {sum(rows[i]['growth_capex'] > 0 for i in growing)} of {len(growing)} growing periods; "
        f"terminal reinvestment rate g/ROIC = {(num(t.get('growth')) or 0) / num(t['roic']):.3f}")
    needs = {}
    for s, r in zip(case['scenarios'], result['scenarios']):
        ext = sum((num(p.get('debt_draw')) or 0) + (num(p.get('equity_proceeds')) or 0) for p in s['periods'])
        gap = sum(x['funding_gap'] for x in r['forecast'])
        needs[s['name']] = {'external_financing': ext, 'funding_gap': gap,
                            'uncommitted': [p['end'] for p in s['periods'] if p.get('financing_status') == 'assumed'
                                            and ((num(p.get('debt_draw')) or 0) or (num(p.get('equity_proceeds')) or 0))]}
    b = needs['base']
    put('funding_need', b['external_financing'] + b['funding_gap'],
        f"base plan: explicit external financing {b['external_financing']:g} + unfunded gaps {b['funding_gap']:g}; all scenarios: {needs}")
    put('financing_modeled', b['funding_gap'] <= 1e-7,
        'base rows have no funding gap: every need is financed by explicit draws/equity at stated prices' if b['funding_gap'] <= 1e-7
        else 'base rows show an unfunded cash requirement')
    return model, prov


def _structure_from_qa(fb, qa):
    """Minimal capital.py structure from dataqa's resolved balances (source ids carried, never invented)."""
    res = (qa or {}).get('resolved') or {}
    shares = resolved(qa, 'shares_outstanding')
    if not shares or shares <= 0:
        return None, 'no validated share count: cannot build a capital structure'

    def src(key, field):
        r = res.get(key) if isinstance(res.get(key), dict) else {}
        ids = r.get('source_ids') or r.get('sources') or []
        f = ((qa or {}).get('fields') or {}).get(field) or {}
        return r.get('source_id') or (ids[0] if ids else None) or (f.get('sources') or [None])[0] or f'dataqa:resolved.{key}'

    def when(key):
        r = res.get(key) if isinstance(res.get(key), dict) else {}
        return r.get('as_of') or fb['as_of']
    cs = {'as_of': fb['as_of'], 'currency': fb['company']['currency'], 'cash': [], 'debt': [],
          'shares': [{'id': 'dataqa_common', 'kind': 'common', 'count': shares, 'source_id': src('shares_outstanding', 'shares_outstanding'),
                      'as_of': when('shares_outstanding')}]}
    for key, kind, field in (('cash', 'unrestricted', 'cash_and_liquid_investments'),
                             ('short_term_investments', 'short_term_investments', 'cash_and_liquid_investments'),
                             ('long_term_investments', 'long_term_investments', 'investments')):
        v = resolved(qa, key)
        if v:
            cs['cash'].append({'id': 'dataqa_' + key, 'kind': kind, 'amount': v, 'source_id': src(key, field), 'as_of': when(key)})
    d = resolved(qa, 'debt')
    if d:
        cs['debt'].append({'id': 'dataqa_debt', 'kind': 'other', 'principal': d, 'source_id': src('debt', 'debt'), 'as_of': when('debt')})
    return cs, None


def _reported(qa):
    res, rep = (qa or {}).get('resolved') or {}, {}
    cash = resolved(qa, 'cash')
    if cash is not None:
        rep.update(cash_and_equivalents=cash, short_term_investments=resolved(qa, 'short_term_investments') or 0)
        if isinstance(res.get('cash'), dict) and res['cash'].get('as_of'):
            rep['statement_as_of'] = res['cash']['as_of']
    if resolved(qa, 'debt') is not None:
        rep['debt_principal_outstanding'] = resolved(qa, 'debt')
    if resolved(qa, 'shares_outstanding') is not None:
        rep['shares_count'] = resolved(qa, 'shares_outstanding')
        rep['shares_count_date'] = (res.get('shares_outstanding') or {}).get('as_of')
        if not rep['shares_count_date']:
            del rep['shares_count'], rep['shares_count_date']
    return rep


def redteam_check(fb, qa, an, fc, val):
    """Run redteam.audit on a package derived from execution, then read it conservatively:
    any critical FAILED or critical UNVERIFIABLE check makes the valuation unusable for price."""
    result, case, today, _ = valuation_parts(val)
    model, prov = derive_model(an, fc, result, case, today)
    cs, source, by_construction, err = (fb or {}).get('capital_structure'), 'factbook', [], None
    if cs is None:
        cs, err = _structure_from_qa(fb or {}, qa)
        source = 'dataqa_resolved' if cs else 'unavailable'
        by_construction = ['cash_reconciles', 'debt_complete', 'shares_current'] if cs else []
    else:
        res = (qa or {}).get('resolved') or {}
        by_construction = [code for key, code in (('cash', 'cash_reconciles'), ('debt', 'debt_complete'), ('shares_outstanding', 'shares_current'))
                           if isinstance(res.get(key), dict) and 'capital_structure' in (res[key].get('basis'), res[key].get('method'))]
    reported = _reported(qa)
    out = {'inputs_derived': True, 'capital_structure_source': source, 'by_construction': by_construction,
           'derived_inputs': prov, 'reported': reported, 'result': None, 'error': err}
    if cs is not None:
        try:
            if date.fromisoformat(cs['as_of']) > date.fromisoformat(fb['as_of']):
                raise ValueError('capital structure dated after the FactBook as_of (look-ahead)')
            out['result'] = redteam.audit({'as_of': fb['as_of'], 'capital_structure': cs, 'reported': reported, 'model': model})
        except (ValueError, KeyError, TypeError) as e:
            out['error'] = f'{type(e).__name__}: {e}'
    checks = (out['result'] or {}).get('checks', [])
    out['critical_failed'] = out['critical_failures'] = [c['code'] for c in checks if c['severity'] == 'critical' and c['status'] == redteam.FAILED]
    out['critical_unverifiable'] = [c['code'] for c in checks if c['severity'] == 'critical' and c['status'] == redteam.UNVERIFIABLE]
    out['verdict'] = (out['result'] or {}).get('verdict', redteam.UNVERIFIABLE)
    out['redteam_usable'] = bool((out['result'] or {}).get('valuation_usable'))
    out['usable'] = out['redteam_usable'] and not out['critical_failed'] and not out['critical_unverifiable']
    out['meaning'] = ('Inputs derived from executed facts, not self-declared flags. Checks listed in by_construction compare a '
                      'structure with the balances it was built from and are not independent evidence.')
    return out


# ---------------------------------------------------------------- reviewer input

def _today(today):
    return date.fromisoformat(today) if isinstance(today, str) else (today or date.today())


def inputs_digest(val, inputs=None):
    """The reviewer's inputs_sha256: the executed valuation (input_sha256) and the analyst's inputs.decision, inputs.card and
    inputs.forecast (empty = none). The pipeline computes it; an override added after the review changes it."""
    result = valuation_parts(val or {})[0]
    inputs = inputs if isinstance(inputs, dict) else {}
    return digest({'binding': 'review_inputs_v1', 'valuation_sha256': (result or {}).get('input_sha256'),
                   **{k: inputs.get(k) or None for k in BOUND_INPUTS}})


def input_authors(inputs):
    """Author-side names in the pipeline inputs: inputs.analyst (text or list) and every inputs.forecast.reviews signer."""
    inputs = inputs if isinstance(inputs, dict) else {}
    a = inputs.get('analyst')
    names = [a] if isinstance(a, str) else list(a) if isinstance(a, list) else []
    fc = inputs.get('forecast') if isinstance(inputs.get('forecast'), dict) else {}
    reviews = fc.get('reviews') if isinstance(fc.get('reviews'), dict) else {}
    names += [r.get('reviewer') for r in reviews.values() if isinstance(r, dict)]
    return sorted({n.strip() for n in names if isinstance(n, str) and n.strip()})


def _person(name):
    """Normalized person name: case-folded word tokens in sorted order, bracketed remarks dropped ('Lavi, Yarin (author)' = 'yarin lavi')."""
    if not isinstance(name, str):
        return ''
    s = name.casefold()
    t = re.findall(r'[^\W_]+', re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', s)) or re.findall(r'[^\W_]+', s)
    return ' '.join(sorted(t))


def _release_errors(rid, ev, own, evidence):
    """Why a risk override's evidence cannot release a withheld red flag ([] = it can). The flag's own evidence is ignored;
    every other id must be FactBook filing evidence and at least one a filing/issuer document."""
    from .decision import _evidence_class
    fresh = [x for x in ev if x not in own]
    cls = {x: _evidence_class(x, evidence or {}) for x in fresh}
    bad = [x for x in fresh if cls[x] not in RELEASE_CLASSES]
    if bad:
        return [f"risk_overrides.{rid}: evidence must be FactBook filing documents or filing datasets; not accepted: "
                + ', '.join(f'{x} ({cls[x]})' for x in bad)]
    if not any(cls[x] == 'filing_document' for x in fresh):
        cited = sorted(set(ev) & set(own))
        return [f"risk_overrides.{rid}: cite at least one FactBook filing or issuer document ({RELEASE_DOCS}) besides the flag's own evidence"
                + (f" (the flag's own evidence {', '.join(cited)} is ignored)" if cited else '')]
    return []


def check_reviewer(reviewer, case=None, result=None, today=None, known_ids=None, evidence=None, own_evidence=None, authors=(),
                   inputs_sha256=None):
    """Validate the shape of a signed review; record it, never invent one.

    accepted = valid AND independent AND bound to the executed valuation (valuation_sha256 is mandatory: an
    unbound review could be reused for any later revision that keeps the assumption ids) AND bound to the analyst
    inputs (inputs_sha256 is mandatory and must equal `inputs_sha256`, see inputs_digest). reviewed_at must lie
    between the valuation cutoff and `today` (injectable clock; default date.today()). A reviewer whose normalized
    name matches one of `authors` (author-side signers) is a self-review. risk_overrides {risk id: {justification,
    evidence_ids}} document why a withheld governance red flag does not preclude a price conclusion: ids must be known
    (`known_ids`) when supplied, the flag's own evidence (`own_evidence` {risk id: ids}) is ignored, and the rest must be
    FactBook filing evidence by decision._evidence_class over `evidence` (decision._evidence_index), at least one a
    filing/issuer document."""
    out = {'present': reviewer is not None, 'valid': False, 'independent': False, 'errors': [], 'reviewed': [], 'rejected': [],
           'unresolved_critical': [], 'unresolved_major': [], 'bound_to_valuation': False, 'bound_to_inputs': False, 'accepted': False,
           'risk_overrides': [], 'self_review': []}
    if reviewer is None:
        return out
    if not isinstance(reviewer, dict):
        out['errors'].append('reviewer input must be an object')
        return out
    e = out['errors']
    for k in ('reviewer', 'statement'):
        if not isinstance(reviewer.get(k), str) or not reviewer[k].strip():
            e.append(f'{k}: nonempty text required')
    try:
        when, now = date.fromisoformat(str(reviewer.get('reviewed_at'))), _today(today)
        if case and when < date.fromisoformat(case['as_of']):
            e.append('reviewed_at precedes the valuation cutoff: the review cannot cover this casefile')
        if when > now:
            e.append(f'reviewed_at {when.isoformat()} is after today {now.isoformat()}: a review cannot be dated in the future')
    except ValueError:
        e.append('reviewed_at: ISO date required')
    if reviewer.get('independent') is not True:
        e.append('independent must be true: a self-review is recorded but is not an independent review')
    me = _person(reviewer.get('reviewer'))
    out['self_review'] = sorted({n for n in authors or () if me and _person(n) == me})
    if out['self_review']:
        e.append(f"reviewer is an author-side signer ({', '.join(out['self_review'])}: the analyst, a case assumption reviewer or a forecast "
                 'reviews signer): a self-review is recorded but is not an independent review')
    known = {a['id'] for a in (case or {}).get('assumptions', [])}
    reviews = reviewer.get('assumption_reviews', {})
    if not isinstance(reviews, dict):
        e.append('assumption_reviews must map assumption id -> reviewed|rejected'); reviews = {}
    for aid, st in reviews.items():
        if st not in ('reviewed', 'rejected'):
            e.append(f'assumption_reviews.{aid}: reviewed or rejected')
        elif case and aid not in known:
            e.append(f'assumption_reviews.{aid}: unknown assumption id')
        else:
            out[st].append(aid)
    findings = reviewer.get('findings', [])
    for i, f in enumerate(findings if isinstance(findings, list) else []):
        if not isinstance(f, dict):
            e.append(f'findings[{i}]: object required'); continue
        if f.get('severity') not in SEVERITIES:
            e.append(f'findings[{i}].severity: critical|major|minor')
        if not isinstance(f.get('text'), str) or not f['text'].strip():
            e.append(f'findings[{i}].text: nonempty text required')
        if not isinstance(f.get('resolved'), bool):
            e.append(f'findings[{i}].resolved: boolean required')
        elif f['resolved'] and (not isinstance(f.get('response'), str) or not f['response'].strip()):
            e.append(f'findings[{i}]: a resolved finding needs a response')
        if f.get('resolved') is not True and f.get('severity') in ('critical', 'major'):
            out['unresolved_' + f['severity']].append(f.get('text'))
    if not isinstance(findings, list):
        e.append('findings must be a list')
    sha = reviewer.get('valuation_sha256')
    if not isinstance(sha, str) or not sha.strip():
        e.append('valuation_sha256: required (input_sha256 of the executed valuation the review covers); an unbound review '
                 'could be reused for a different revision')
    elif result and sha != result.get('input_sha256'):
        e.append('valuation_sha256 does not match the executed valuation: the review covers a different case')
    out['bound_to_valuation'] = bool(result and isinstance(sha, str) and sha == result.get('input_sha256'))
    ish = reviewer.get('inputs_sha256')
    if not isinstance(ish, str) or not ish.strip():
        e.append('inputs_sha256: required (review.inputs_digest: the executed valuation plus inputs.decision, inputs.card and inputs.forecast); '
                 'an unbound review would also cover analyst overrides added after it')
    elif inputs_sha256 is not None and ish != inputs_sha256:
        e.append('inputs_sha256 does not match: the valuation or the analyst inputs (decision, card or forecast) changed after the review')
    out['bound_to_inputs'] = bool(inputs_sha256 and ish == inputs_sha256)
    ro = reviewer.get('risk_overrides', {})
    if not isinstance(ro, dict):
        e.append('risk_overrides must map risk id -> {justification, evidence_ids}'); ro = {}
    for rid, o in ro.items():
        o = o if isinstance(o, dict) else {}
        ev = o.get('evidence_ids')
        if not isinstance(o.get('justification'), str) or len(o['justification'].split()) < 5:
            e.append(f'risk_overrides.{rid}: documented justification required (at least 5 words)')
        elif not isinstance(ev, list) or not ev or not all(isinstance(x, str) and x for x in ev):
            e.append(f'risk_overrides.{rid}: evidence_ids required (nonempty list of ids)')
        elif known_ids is not None and set(ev) - set(known_ids):
            e.append(f"risk_overrides.{rid}: unknown evidence ids {sorted(set(ev) - set(known_ids))}")
        else:
            why = _release_errors(rid, ev, set((own_evidence or {}).get(rid) or ()), evidence)
            e += why
            if not why:
                out['risk_overrides'].append(rid)
    out['independent'] = reviewer.get('independent') is True and not out['self_review']
    out['valid'] = not e
    out['accepted'] = out['valid'] and out['independent'] and out['bound_to_valuation'] and out['bound_to_inputs']
    return out


# ---------------------------------------------------------------- consistency

def _risk_free(fc):
    comp = ((fc or {}).get('cost_of_equity') or {}).get('components') or {}
    for k in ('risk_free', 'risk_free_rate', 'rf'):
        if num(comp.get(k)) is not None:
            return num(comp[k]), f'forecast.cost_of_equity.components.{k}'
    return None, None


def consistency(fb, qa, an, fc, val, rt=None, rc=None):
    result, case, today, price = valuation_parts(val)
    out = []
    reviewed = set((rc or {}).get('reviewed') or []) if (rc or {}).get('accepted') else set()     # only an accepted reviewer signs off

    def add(code, status, severity, message, **detail):
        out.append({'code': code, 'status': status, 'severity': severity, 'message': message, 'detail': detail})
    if not result:
        add('valuation', 'skip', 'minor', 'No executed valuation: consistency checks not applicable.')
        return out
    v = {k: num(today.get(k)) for k in ('bear', 'base', 'bull')}
    if None in v.values():
        add('scenario_order_today', 'skip', 'major', 'A scenario value is withheld or missing; order cannot be checked.', values=v)
    else:
        ok = v['bear'] <= v['base'] <= v['bull']
        add('scenario_order_today', 'pass' if ok else 'fail', 'critical', 'bear <= base <= bull today.' if ok else 'Scenario values cross today.', values=v)
    if case.get('method') != 'operating':
        add('operating_checks', 'skip', 'minor', f"Method {case.get('method')}: terminal ROIC/growth/margin checks apply to operating models only.")
        return out
    bc, br = scen(case, 'base'), scen(result, 'base')
    t = bc['terminal']
    terminal_ids = set(t.get('assumption_ids') or [])
    evidence = {k: metric(an, k) for k in ('roic', 'roic_ex_goodwill', 'incremental_roic_3y')}
    known = [x for x in evidence.values() if x is not None]
    if not known:
        add('terminal_roic_vs_evidence', 'needs_evidence', 'minor', 'No demonstrated ROIC in analysis to support the terminal ROIC.', terminal_roic=t['roic'])
    elif t['roic'] <= max(known) + .10:
        add('terminal_roic_vs_evidence', 'pass', 'major', 'Terminal ROIC within 10pp of the best demonstrated return measure.', terminal_roic=t['roic'], evidence=evidence)
    else:
        add('terminal_roic_vs_evidence', 'reviewed' if terminal_ids and terminal_ids <= reviewed else 'needs_evidence', 'major',
            f"Terminal ROIC {t['roic']:.1%} exceeds the best demonstrated measure {max(known):.1%} by more than 10pp: evidence of a durable advantage is required.",
            terminal_roic=t['roic'], evidence=evidence)
    rf, rf_src = _risk_free(fc)
    tg = {s['name']: s['terminal']['growth'] for s in case['scenarios']}
    if rf is None:
        add('terminal_growth_vs_risk_free', 'skip', 'major', 'Risk-free rate not supplied with the cost of equity; check not run (no default invented).', terminal_growth=tg)
    else:
        bad = {k: g for k, g in tg.items() if g > rf + 1e-12}
        add('terminal_growth_vs_risk_free', 'fail' if bad else 'pass', 'major',
            f'Terminal growth above the risk-free rate {rf:.2%} in {sorted(bad)}: perpetual outgrowth of the economy.' if bad else
            f'Terminal growth at or below the risk-free rate {rf:.2%}.', terminal_growth=tg, risk_free=rf, risk_free_source=rf_src)
    cutoff = date.fromisoformat(fb['as_of'])
    ests = sorted((e for e in (fb or {}).get('estimates', []) if e.get('metric') == 'revenue' and num(e.get('value'))
                   and date.fromisoformat(e['as_of']) <= cutoff and date.fromisoformat(e['period_end']) > cutoff), key=lambda e: e['period_end'])
    full = [r for r in br['forecast'] if years(r['start'], r['end']) > .9]
    match = next(((e, r) for e in ests for r in full if abs((date.fromisoformat(r['end']) - date.fromisoformat(e['period_end'])).days) <= 45), None)
    if not ests:
        add('revenue_vs_consensus', 'skip', 'minor', 'No consensus revenue available at the cutoff (later estimates are never used).')
    elif not match:
        add('revenue_vs_consensus', 'skip', 'minor', 'No full forecast year matches a consensus fiscal year within 45 days.')
    else:
        e, r = match
        diff = r['revenue'] / e['value'] - 1
        comps = ((case.get('underwriting') or {}).get('consensus_review') or {}).get('comparisons', [])
        explained = any(c.get('metric') == 'revenue' and c.get('difference_explanation') and
                        abs((date.fromisoformat(c['period_end']) - date.fromisoformat(r['end'])).days) <= 45 for c in comps)
        explained = explained or any('consensus' in str(a.get('topic', '')).lower() and a.get('rationale') for a in (fc or {}).get('assumptions', []))
        add('revenue_vs_consensus', 'pass' if abs(diff) <= .20 else ('explained' if explained else 'needs_explanation'), 'major',
            f"First full forecast year revenue differs from consensus by {diff:+.1%}" + ('' if abs(diff) <= .20 else '; a difference above 20% needs a written explanation.'),
            model_revenue=r['revenue'], period_end=r['end'], consensus=e['value'], consensus_period_end=e['period_end'],
            consensus_source=e.get('source_id'), provenance='vendor_estimate', relative_difference=diff)
    best = metric(an, 'operating_margin_best_3y')
    if best is None:
        hist = [num(x.get('operating_income')) / num(x['revenue']) for x in ((an or {}).get('annual') or {}).values()
                if num((x or {}).get('operating_income')) is not None and (num((x or {}).get('revenue')) or 0) > 0]
        best = max(hist) if hist else None
    rows_margin = [r['ebit'] / r['revenue'] for r in br['forecast'] if r['revenue'] > 0]
    peak = max(rows_margin + [t['operating_margin']])
    if best is None:
        add('margin_vs_history', 'skip', 'major', 'No operating-margin history to compare.', model_peak_margin=peak)
    elif peak <= best + .10:
        add('margin_vs_history', 'pass', 'major', f'Peak modeled operating margin {peak:.1%} within 10pp of best history {best:.1%}.', model_peak_margin=peak, best_history=best)
    else:
        margin_a = [a for a in (fc or {}).get('assumptions', []) if 'margin' in f"{a.get('id', '')} {a.get('topic', '')}".lower()]
        evidenced = any(a.get('kind') in ('reported', 'guidance') and a.get('source_ids') for a in margin_a)
        ids = {a['id'] for a in margin_a}          # margin-specific only: a signed terminal/generic id does not cover the margin
        status = 'reviewed' if ids and ids <= reviewed else ('evidenced' if evidenced else 'needs_evidence')
        add('margin_vs_history', status, 'major', f'Peak modeled operating margin {peak:.1%} exceeds best history {best:.1%} by more than 10pp: '
            'needs reported/guided evidence or an explicit reviewer sign-off of a margin-specific assumption.', model_peak_margin=peak,
            best_history=best, margin_assumption_ids=sorted(ids))
    hist_sh = metric(an, 'share_count_cagr_3y')
    rows = br['forecast']
    span = years(case['as_of'], rows[-1]['end'])
    model_sh = (rows[-1]['shares'] / case['opening']['shares']) ** (1 / span) - 1 if span > 0 else 0
    sbc_hist = metric(an, 'sbc_pct_revenue') or 0
    expensed = ((rt or {}).get('derived_inputs') or {}).get('sbc_treatment', {}).get('value') == 'expensed'
    if hist_sh is None:
        add('share_path_vs_history', 'skip', 'minor', 'No share-count history.', model_cagr=model_sh)
    elif hist_sh > model_sh + .02 and not (expensed and hist_sh <= sbc_hist):
        add('share_path_vs_history', 'needs_explanation', 'major', f'History shows {hist_sh:+.1%}/yr share growth; the model path is {model_sh:+.1%}/yr. '
            'Cash-equivalent SBC covers award dilution, not financing issuance.', history_cagr=hist_sh, model_cagr=model_sh)
    else:
        add('share_path_vs_history', 'pass', 'major', f'Model share path {model_sh:+.1%}/yr versus history {hist_sh:+.1%}/yr'
            + (' (buybacks are not credited unless modeled as payouts).' if hist_sh < model_sh - .02 else '.'), history_cagr=hist_sh, model_cagr=model_sh)
    out.append(_opening_check(qa, case, val))
    return out


def _opening_check(qa, case, val):
    """The valuation opening must not overstate cash or understate debt/shares versus validated balances."""
    op = case['opening']
    q = {k: resolved(qa, k) for k in ('cash', 'short_term_investments', 'long_term_investments', 'equity_securities_fv', 'debt',
                                       'shares_outstanding')}
    rec = (case.get('underwriting') or {}).get('opening_reconciliation') or {}
    fails, notes = [], []
    if q['shares_outstanding'] is not None and op['shares'] < q['shares_outstanding'] * .995:
        fails.append(f"opening shares {op['shares']:g} below validated outstanding {q['shares_outstanding']:g}")
    liquid_q = None if q['cash'] is None else (q['cash'] + (q['short_term_investments'] or 0) + (q['long_term_investments'] or 0)
                                               + (q['equity_securities_fv'] or 0))    # listed stakes enter the opening once (forecast)
    liquid_o = (num(op.get('unrestricted_cash')) or 0) + (num(op.get('marketable_investments')) or 0)
    bridged = bool(rec.get('bridge_events'))
    inv_adj = sum(num(a.get('amount')) or 0 for a in rec.get('investment_adjustments') or [])
    if liquid_q is not None and liquid_o > (liquid_q + inv_adj) * 1.02 + 1:
        (notes if bridged else fails).append(f'opening cash+investments {liquid_o:g} above validated {liquid_q:g}'
                                             + (f' adjusted by {inv_adj:+g}' if inv_adj else '') + (' (bridge events supplied)' if bridged else ''))
    elif inv_adj and liquid_q is not None:
        notes.append(f'opening cash+investments {liquid_o:g} = validated {liquid_q:g} + non-operating stakes {inv_adj:+g} at fair value less '
                     'haircut: analyst addition for the reviewer to accept')
    adj = rec.get('debt_adjustments') or []
    adj_sum = sum(num(a.get('amount')) or 0 for a in adj)
    if q['debt'] is not None and (num(op.get('debt')) or 0) < (q['debt'] + adj_sum) * .98 - 1:
        (notes if bridged else fails).append(f"opening debt {op.get('debt'):g} below validated {q['debt']:g}"
                                             + (f' adjusted by {adj_sum:+g}' if adj else '') + (' (bridge events supplied)' if bridged else ''))
    elif adj and q['debt'] is not None:
        # evidenced reclassifications (operating funding out, debt-like claims in): visible, accepted only by the reviewer
        notes.append(f"opening debt {op.get('debt'):g} = validated {q['debt']:g} " + ', '.join(f"{a['kind']} {num(a.get('amount')):+g}" for a in adj)
                     + ': analyst reclassification for the reviewer to accept')
    rc_cash = num(rec.get('cash_and_equivalents'))
    if q['cash'] is not None and rc_cash is not None and abs(rc_cash - q['cash']) > .01 * max(q['cash'], 1):
        notes.append('opening reconciliation statement cash differs from dataqa resolved cash (dates or definitions differ)')
    cap = (val or {}).get('capital_check') or {}
    if num(cap.get('difference')) is not None and cap['difference'] > .02:
        notes.append(f"fully diluted shares at the current price exceed opening shares by {cap['difference']:.1%} (in-the-money converts/options)")
    status = 'fail' if fails else ('needs_explanation' if notes else ('skip' if all(x is None for x in q.values()) else 'pass'))
    return {'code': 'opening_vs_validated_data', 'status': status, 'severity': 'critical' if fails else 'major',
            'message': '; '.join(fails + notes) or 'Valuation opening consistent with validated balances (no overstated cash, no understated debt/shares).',
            'detail': {'validated': q, 'opening': {k: op.get(k) for k in ('unrestricted_cash', 'marketable_investments', 'debt', 'shares')}}}


# ---------------------------------------------------------------- audit

def _reaudit(case, result, signed):
    """Underwriting audit re-run on a copy in which ONLY the ids in `signed` are reviewed: author-side review
    statuses are reset first, so they can never stand in for an independent sign-off."""
    c = deepcopy(case)
    for a in c['assumptions']:
        a['review_status'] = 'reviewed' if a['id'] in signed else 'unreviewed'
    return audit_valuation(c, result['scenarios'])


def _paths(issues):
    return ', '.join(sorted({i['code'] + '@' + (i['path'] or '-') for i in issues}))


def _underwriting_gate(case, result, rc, va, no):
    """Close the underwriting gate only through an accepted independent reviewer, in every valuation status."""
    ids = [a['id'] for a in case['assumptions']]
    author = sorted(a['id'] for a in case['assumptions'] if a.get('review_status') == 'reviewed')
    if author:
        va['author_review_notes'] = {'assumption_ids': author, 'meaning': AUTHOR_NOTES}
    blind, full = _reaudit(case, result, ()), _reaudit(case, result, set(ids))
    other = sorted({i['code'] for i in blind['issues']} - REVIEW_ONLY)
    primary = [i for i in full['issues'] if i['code'] == 'evidence_missing' and 'primary source' in i['message']]
    va['full_signoff_would_close'] = full['eligible_for_research_synthesis']
    if other:
        no('underwriting_incomplete', f"underwriting issues beyond review: {', '.join(other)}")
    elif primary:
        no('primary_evidence_missing', f'no primary source (filing, earnings or issuer release) behind {_paths(primary)}: a reviewer '
           'sign-off cannot close this; link the primary document to the opening assumptions')
    elif not full['eligible_for_research_synthesis']:
        no('underwriting_incomplete', f"evidence not linked to any assumption ({_paths(full['issues'])}): a reviewer sign-off cannot close this")
    elif not rc['accepted']:
        why = '; '.join(rc['errors']) or ('reviewer not independent' if not rc['independent'] else 'review not bound to this valuation and inputs')
        no('review_required', 'only review gaps remain; a valid, independent review bound to this valuation (valuation_sha256) and the analyst '
           'inputs (inputs_sha256) is required'
           + (f' (reviewer input not accepted: {why})' if rc['present'] else '') + (f"; author review notes on {', '.join(author)} do not count" if author else ''))
    elif rc['rejected']:
        no('assumption_rejected', f"reviewer rejected: {', '.join(rc['rejected'])}")
    else:
        missing = sorted(i for i in ids if i not in rc['reviewed'])
        re_ = _reaudit(case, result, set(rc['reviewed']))
        va['reaudit_with_reviewer'] = {'eligible': re_['eligible_for_research_synthesis'], 'issues': re_['issues']}
        if missing:
            no('review_incomplete', f"assumptions not signed off by the independent reviewer: {', '.join(missing)}")
        elif not re_['eligible_for_research_synthesis']:
            no('review_incomplete', f"gaps remain after sign-off: {_paths(re_['issues'])}")
        else:
            va['basis'] = 'accepted independent reviewer (bound to this valuation) signs off every assumption and closes every remaining underwriting gap'


def _known_ids(fb, case, rk):
    ids = set((fb or {}).get('sources') or {}) | {q.get('id') for q in (fb or {}).get('qualitative') or []}
    ids |= {s.get('id') for s in (case or {}).get('sources') or []}
    for r in (rk or {}).get('register') or []:
        ids |= set(r.get('evidence_ids') or [])
    return {i for i in ids if isinstance(i, str)}


def audit(fb, qa, an, card, fc, val, rk, reviewer=None, today=None, inputs_sha256=None, authors=()):
    """Review gate. `today` is the clock for reviewed_at (date or ISO string; default date.today()). `inputs_sha256` is the
    pipeline's inputs_digest(val, inputs) the reviewer must sign (default: inputs_digest(val), i.e. no analyst inputs);
    `authors` are author-side names from the inputs (input_authors), checked with every case/spec assumption reviewer."""
    from .decision import _evidence_index
    result, case, _, _ = valuation_parts(val)
    rt = redteam_check(fb, qa, an, fc, val)
    bind = inputs_sha256 or inputs_digest(val)
    signers = set(authors or ()) | {a['reviewer'] for a in list((case or {}).get('assumptions') or []) + list(((fc or {}).get('spec') or {}).get('assumptions') or [])
                                    if isinstance(a, dict) and isinstance(a.get('reviewer'), str)}
    own = {r.get('id'): list(r.get('evidence_ids') or []) for r in (rk or {}).get('register') or [] if isinstance(r, dict)}
    idx = _evidence_index({'fb': fb, 'card': card, 'fc': fc, 'val': val, 'rk': rk})
    rc = check_reviewer(reviewer, case, result, today, _known_ids(fb, case, rk), idx, own, sorted(signers), bind)
    cons = consistency(fb, qa, an, fc, val, rt, rc)
    ua = (result or {}).get('underwriting_audit') or {}
    va = {'valuation_status': (result or {}).get('status', (val or {}).get('status', 'not_computed')),
          'calculation_status': (result or {}).get('calculation_status'), 'is_demo': bool((result or {}).get('is_demo')),
          'status': ua.get('status'), 'eligible': bool(ua.get('eligible_for_research_synthesis')),
          'issues': ua.get('issues', []), 'issue_codes': sorted({i['code'] for i in ua.get('issues', [])}),
          'source_access': ua.get('source_access'), 'input_sha256': (result or {}).get('input_sha256'),
          'warnings': (result or {}).get('warnings', [])}
    reasons, codes = [], []

    def no(code, message):
        codes.append(code); reasons.append(f'{code}: {message}')
    status_qa = (qa or {}).get('status')
    if not qa:
        no('dataqa_missing', 'data validation did not run')
    elif status_qa == 'blocked':
        no('data_blocked', f"critical fields blocked: {', '.join(qa.get('blocking', [])) or 'see dataqa'}")
    if not result:
        no('valuation_not_executed', (val or {}).get('reason') or 'no executed valuation')
    elif va['is_demo']:
        no('is_demo', 'synthetic demo inputs never support a real price conclusion')
    elif va['valuation_status'] in UNDERWRITTEN:
        _underwriting_gate(case, result, rc, va, no)
    elif va['valuation_status'] == 'funding_blocked':
        no('funding_blocked', 'a scenario has an unfunded cash requirement; values are withheld until financing is modeled')
    else:
        no('valuation_status', f"valuation status {va['valuation_status']}")
    if rc['rejected'] and 'assumption_rejected' not in codes:
        no('assumption_rejected', f"reviewer rejected: {', '.join(rc['rejected'])}")
    comp = ((card or {}).get('completeness') or {})
    if not card or comp.get('status') == 'insufficient':
        no('understanding_insufficient', 'business not yet understood (model card ' + ('missing' if not card else 'completeness insufficient: '
           + (', '.join(map(str, comp.get('missing') or [])) or 'unspecified')) + '): an evidence gap, not a judgement on the company')
    if rk is None:
        no('risk_not_assessed', 'risk stage did not run')
    elif rk.get('disqualifying'):
        no('disqualifying_risk', ', '.join(rk['disqualifying']))
    held = [r for r in (rk or {}).get('register') or [] if r.get('withhold_price')]
    overridden = set(rc['risk_overrides']) if rc['accepted'] else set()
    open_flags = [r for r in held if r['id'] not in overridden]
    if open_flags:
        no('governance_red_flag', '; '.join(f"{r['id']} ({', '.join(r.get('evidence_ids') or [])})" for r in open_flags)
           + ': evidenced going-concern doubt, material weakness or restatement; the price conclusion waits for an accepted independent '
           'reviewer to document an override (risk_overrides with a justification and FactBook filing-document evidence other than the '
           "flag's own)")
    if not rt['usable']:
        why = rt['critical_failed'] + rt['critical_unverifiable'] or [rt['error'] or rt['verdict']]
        no('redteam_not_usable', f"critical red-team checks failed or unverifiable: {', '.join(map(str, why))}")
    crit = [c['code'] for c in cons if c['status'] == 'fail' and c['severity'] == 'critical']
    if crit:
        no('consistency_failure', ', '.join(crit))
    if rc['unresolved_critical']:
        no('critical_finding_unresolved', ' | '.join(map(str, rc['unresolved_critical'])))
    allowed = not reasons
    open_items = [{'code': c['code'], 'status': c['status'], 'message': c['message']} for c in cons
                  if c['status'] in ('needs_explanation', 'needs_evidence') or (c['status'] == 'fail' and c['severity'] != 'critical')]
    open_items += [{'code': 'reviewer_major_finding', 'status': 'unresolved', 'message': m} for m in rc['unresolved_major']]
    return {'mechanical': {'dataqa': status_qa, 'dataqa_blocking': (qa or {}).get('blocking', []), 'dataqa_limited': (qa or {}).get('limited', []),
                           'valuation_audit': va, 'redteam': rt, 'consistency': cons},
            'reviewer': deepcopy(reviewer) if reviewer is not None else None, 'reviewer_check': rc,
            'binding': {'valuation_sha256': (result or {}).get('input_sha256'), 'inputs_sha256': bind,
                        'covers': ['valuation input_sha256'] + [f'inputs.{k}' for k in BOUND_INPUTS],
                        'supplied_by': 'pipeline' if inputs_sha256 else 'review default: no analyst inputs (inputs_digest(val))',
                        'meaning': 'a reviewer signs valuation_sha256 and inputs_sha256; any later change to the valuation or to these '
                                   'analyst inputs voids the review'},
            'risk_overrides_applied': sorted(r['id'] for r in held if r['id'] in overridden),
            'price_conclusion_allowed': allowed,
            'status': 'reviewed' if allowed else ('blocked' if HARD & set(codes) else 'unreviewed_draft'),
            'reasons': reasons, 'reason_codes': codes, 'open_items': open_items,
            'meaning': 'Process and consistency audit of supplied evidence, not an independent fact check. A reviewed status '
                       'permits a dated, conditional price conclusion; it does not establish forecast accuracy or investment merit.'}
