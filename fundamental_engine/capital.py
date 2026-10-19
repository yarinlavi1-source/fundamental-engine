"""Instrument-level capital structure and the enterprise-value -> per-share bridge.

Why this module exists (docs/GAP_ANALYSIS_2026-10-19.md, weakness #1): net cash and
share counts were typed by hand in the live brain and produced real errors (ASTS net
cash double-counted, NBIS share count stale, convertible accretion ignored). Every
claim on the enterprise is now an explicit instrument with a source, a date and a
deterministic rule for how it shares in value.

Conventions
- Amounts are absolute currency units; dates are ISO strings.
- Convertible notes: conversion shares are based on ORIGINAL principal; the debt claim
  is the ACCRETED principal at the valuation date (redemption / fundamental-change
  amount). Accretion follows an explicit schedule when supplied, otherwise geometric
  interpolation from issue to maturity (documented approximation).
- A holder converts when conversion value exceeds the accreted claim. The bridge solves
  the fixed point (conversion depends on the per-share value it produces).
- Options/warrants with a strike use the treasury method at the solved value.
  Pre-funded warrants (strike ~ 0) are economic shares.
- Restricted cash and haircut investments are reported separately and are NOT spendable
  unless the instrument says so. Customer prepayments are reported as a burden on future
  cash (revenue already collected), not netted as debt, unless treatment='financing'.
"""
from datetime import date
from .finance import number

CASH_KINDS = {'unrestricted', 'restricted', 'short_term_investments', 'long_term_investments'}
SHARE_KINDS = {'common', 'prefunded_warrant', 'warrant', 'option', 'rsu', 'psu'}
DEBT_KINDS = {'term', 'secured', 'revolver', 'bond', 'convertible', 'lease', 'other'}
QUASI_KINDS = {'customer_prepayment', 'preferred', 'minority_interest', 'pension', 'other_claim'}


def _d(s, label):
    try:
        return date.fromisoformat(s)
    except Exception as e:
        raise ValueError(f'{label}: invalid ISO date') from e


def _years(a, b):
    return (b - a).days / 365.25


def accreted_value(inst, as_of):
    """Claim of a debt instrument at as_of (accreted principal for accreting notes)."""
    principal = number(inst['principal'], inst['id'] + '.principal', 0)
    ratio = number(inst.get('accretion_to', 1.0), inst['id'] + '.accretion_to', 1.0, 3.0)
    if ratio == 1.0:
        return principal
    if inst.get('accreted_value') is not None:            # value read from a filing schedule wins
        return number(inst['accreted_value'], inst['id'] + '.accreted_value', principal)
    t0, t1 = _d(inst['issue_date'], inst['id'] + '.issue_date'), _d(inst['maturity'], inst['id'] + '.maturity')
    t = _d(as_of, 'as_of')
    if t1 <= t0:
        raise ValueError(f"{inst['id']}: maturity before issue")
    f = min(max(_years(t0, t) / _years(t0, t1), 0.0), 1.0)
    return principal * ratio ** f


def conversion_shares(inst):
    if inst.get('kind') != 'convertible':
        return 0.0
    cp = number(inst['conversion_price'], inst['id'] + '.conversion_price', 1e-9)
    return number(inst['principal'], inst['id'] + '.principal', 0) / cp


def validate(cs):
    for k in ('as_of', 'currency'):
        if not cs.get(k):
            raise ValueError(f'capital structure needs {k}')
    _d(cs['as_of'], 'as_of')
    seen = set()
    for group, kinds in (('cash', CASH_KINDS), ('shares', SHARE_KINDS), ('debt', DEBT_KINDS), ('quasi_debt', QUASI_KINDS)):
        for x in cs.get(group, []):
            if not x.get('id') or x['id'] in seen:
                raise ValueError(f'{group}: every instrument needs a unique id')
            seen.add(x['id'])
            if x.get('kind') not in kinds:
                raise ValueError(f"{x['id']}: kind must be one of {sorted(kinds)}")
            if x.get('currency', cs['currency']) != cs['currency']:
                raise ValueError(f"{x['id']}: currency differs from structure currency")
            if not x.get('source_id'):
                raise ValueError(f"{x['id']}: source_id required (no unsourced claims)")
            if x.get('as_of') and _d(x['as_of'], x['id'] + '.as_of') > _d(cs['as_of'], 'as_of'):
                raise ValueError(f"{x['id']}: dated after the structure as_of (look-ahead)")
    if not any(s['kind'] == 'common' for s in cs.get('shares', [])):
        raise ValueError('at least one common share line is required')
    return cs


def summarize(cs):
    """Static view at as_of: spendable cash, restricted, investments, claims, basic shares."""
    validate(cs)
    a = cs['as_of']
    spendable = sum(number(x['amount'], x['id'], 0) for x in cs.get('cash', []) if x['kind'] == 'unrestricted')
    sti = sum(number(x['amount'], x['id'], 0) * (1 - number(x.get('haircut', 0), x['id'] + '.haircut', 0, 1))
              for x in cs.get('cash', []) if x['kind'] == 'short_term_investments')
    lti = sum(number(x['amount'], x['id'], 0) * (1 - number(x.get('haircut', 0.25), x['id'] + '.haircut', 0, 1))
              for x in cs.get('cash', []) if x['kind'] == 'long_term_investments')
    restricted = sum(number(x['amount'], x['id'], 0) for x in cs.get('cash', []) if x['kind'] == 'restricted')
    debt_rows = []
    for x in cs.get('debt', []):
        if x['kind'] == 'lease' and not x.get('include_in_net_debt', False):
            continue
        debt_rows.append({'id': x['id'], 'kind': x['kind'], 'claim': accreted_value(x, a),
                          'principal': x['principal'], 'conversion_shares': conversion_shares(x)})
    quasi_fin = sum(number(q['amount'], q['id'], 0) for q in cs.get('quasi_debt', []) if q.get('treatment', 'operating') == 'financing'
                    or q['kind'] in {'preferred', 'minority_interest'})
    prepay = sum(number(q['amount'], q['id'], 0) for q in cs.get('quasi_debt', []) if q['kind'] == 'customer_prepayment'
                 and q.get('treatment', 'operating') == 'operating')
    basic = sum(number(s['count'], s['id'], 0) for s in cs['shares'] if s['kind'] in {'common', 'prefunded_warrant'}
                or (s['kind'] in {'warrant', 'option'} and number(s.get('strike', 0), s['id'] + '.strike', 0) < 0.01))
    rsu = sum(number(s['count'], s['id'], 0) for s in cs['shares'] if s['kind'] in {'rsu', 'psu'})
    return {'as_of': a, 'spendable_cash': spendable + sti, 'restricted_cash': restricted, 'investments_after_haircut': lti,
            'debt': debt_rows, 'debt_claims': sum(r['claim'] for r in debt_rows), 'quasi_financing_claims': quasi_fin,
            'customer_prepayments_operating': prepay, 'basic_economic_shares': basic, 'rsu_shares': rsu}


def per_share(cs, enterprise_value, include_rsu=True, include_investments=True):
    """Solve value per share for a given enterprise value, with rational conversion/exercise.

    Returns the per-share value, the instruments treated as converted/exercised and the bridge.
    """
    s = summarize(cs)
    ev = number(enterprise_value, 'enterprise_value')
    base_equity = ev + s['spendable_cash'] + (s['investments_after_haircut'] if include_investments else 0) - s['quasi_financing_claims']
    shares0 = s['basic_economic_shares'] + (s['rsu_shares'] if include_rsu else 0)
    converts = [r for r in s['debt'] if r['conversion_shares'] > 0]
    straight = sum(r['claim'] for r in s['debt'] if r['conversion_shares'] == 0)
    opts = [o for o in cs['shares'] if o['kind'] in {'warrant', 'option'} and number(o.get('strike', 0), o['id'], 0) >= 0.01]

    def value_given(conv_set, v_guess):
        eq = base_equity - straight - sum(r['claim'] for r in converts if r['id'] not in conv_set)
        sh = shares0 + sum(r['conversion_shares'] for r in converts if r['id'] in conv_set)
        exercised = []
        for o in opts:                                             # treasury method at the guessed value
            k = number(o['strike'], o['id'], 0); n = number(o['count'], o['id'], 0)
            if v_guess > k:
                sh += n * (1 - k / v_guess); exercised.append(o['id'])
        return eq / sh if sh > 0 else float('nan'), sh, eq, exercised

    # thresholds: convert iff per-share value > accreted claim / conversion shares
    order = sorted(converts, key=lambda r: r['claim'] / r['conversion_shares'])
    best = None
    for k in range(len(order) + 1):
        conv = {r['id'] for r in order[:k]}
        v = max(base_equity / max(shares0, 1e-9), 1e-9)
        for _ in range(50):                                        # fixed point for treasury-method options
            v_new, sh, eq, ex = value_given(conv, v)
            if abs(v_new - v) < 1e-9 * max(1, abs(v)):
                break
            v = v_new if v_new > 0 else 1e-9
        lo = order[k - 1]['claim'] / order[k - 1]['conversion_shares'] if k else -float('inf')
        hi = order[k]['claim'] / order[k]['conversion_shares'] if k < len(order) else float('inf')
        if lo <= v_new <= hi or (k == 0 and v_new <= hi):
            best = (v_new, sh, eq, ex, conv)
            break
    if best is None:                                               # degenerate: fall back to all converted
        conv = {r['id'] for r in order}
        v_new, sh, eq, ex = value_given(conv, base_equity / max(shares0, 1e-9))
        best = (v_new, sh, eq, ex, conv)
    v, sh, eq, ex, conv = best
    if eq <= 0:
        return {'status': 'equity_impaired', 'value_per_share': 0.0, 'equity_value': eq, 'shares': sh,
                'converted': sorted(conv), 'exercised': ex, 'bridge': s,
                'note': 'Claims exceed enterprise value at this EV; residual equity is option value only (not modeled here).'}
    return {'status': 'ok', 'value_per_share': v, 'equity_value': eq, 'shares': sh, 'converted': sorted(conv),
            'exercised': ex, 'bridge': s}


def market_view(cs, price):
    """Market capitalization, fully diluted equity and implied enterprise value at a quoted price."""
    s = summarize(cs)
    p = number(price, 'price', 1e-9)
    conv_now = [r for r in s['debt'] if r['conversion_shares'] > 0 and p > r['claim'] / r['conversion_shares']]
    straight = s['debt_claims'] - sum(r['claim'] for r in conv_now)
    opts = sum(number(o['count'], o['id'], 0) * (1 - number(o['strike'], o['id'], 0) / p)
               for o in cs['shares'] if o['kind'] in {'warrant', 'option'} and 0.01 <= number(o.get('strike', 0), o['id'], 0) < p)
    sh = s['basic_economic_shares'] + s['rsu_shares'] + sum(r['conversion_shares'] for r in conv_now) + opts
    equity = p * sh
    ev = equity - s['spendable_cash'] - s['investments_after_haircut'] + straight + s['quasi_financing_claims']
    return {'price': p, 'fully_diluted_shares': sh, 'equity_value': equity, 'enterprise_value': ev,
            'basic_economic_shares': s['basic_economic_shares'], 'in_the_money_converts': [r['id'] for r in conv_now],
            'straight_claims': straight, 'spendable_cash': s['spendable_cash'],
            'customer_prepayments_operating': s['customer_prepayments_operating'],
            'potential_conversion_shares_all': sum(r['conversion_shares'] for r in s['debt'])}
