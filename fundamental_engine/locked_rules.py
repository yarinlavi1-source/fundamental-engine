"""The owner's locked judgement rules as pure functions (no network, no file writes).

Why: beta, the exit P/E, the historical-multiple report and the risk-free rate / ERP were computed by hand in every
run and recorded only as free text in the params notes, so the same rule gave different numbers from run to run (103,
104 or 105 weeks; a partial week kept or dropped; rf 0.049 next to 0.0494 on the same day). These functions compute
them one way and return their working, so it can be stored in the params `judgement` block and checked.

The rules (CLAUDE.md, 'stable valuations' 05.11.2026 and 'two separate reports per stock' 06.11.2026):
- beta = the 2-year weekly beta vs SPY, adjusted: 0.67 x raw + 0.33. Here: the last 104 weekly returns of complete
  weeks, simple returns, slope of the stock's return on SPY's (sample covariance / sample variance);
- exit P/E = the company's own long-run forward P/E median unless a stated fact argues otherwise;
- historical-multiple report: the next-year figure times the company's own 5-year median multiple, the p25-p75
  range as the fair-value range, with the anchor chosen by company type (COMPANY_TYPES);
- one risk-free rate and one ERP per day for the lean path: research/market.json (market_in_force).

Series come from the session's connectors (broker or Yahoo closes, release or consensus EPS); the functions only align
them and do the arithmetic. They never judge: a deviation from a rule is allowed when the block states its reason
(validate_judgement), and no saved params file is changed by anything here.

The params `judgement` block (all parts optional except where validate_judgement says otherwise):
  beta          {raw, adjusted, weeks, window {from, to}, used, reason_if_different}
  exit_pe       {own_median, p25, p75, months, basis, ratio_to_model_eps, used, reason_if_different}
                ratio_to_model_eps = EPS of the history's basis / EPS of the model's basis for the same year (e.g.
                non-GAAP EPS / after-SBC operating EPS = 1.53): the rule multiple on the model's EPS is
                own_median x ratio_to_model_eps. 1.0 when the bases are the same.
  hist_multiple {company_type, basis, next_year_figure, source, median, p25, p75, months, net_cash_per_share, value,
                low, high}
  beta_mature   {value, source}
  roic_terminal {value, source}
  market        {rf, erp, source: 'research/market.json', reason_if_different}
"""
import json
import os
import statistics
from datetime import date, datetime, timedelta

BETA_WEIGHT = 0.67          # adjusted beta = BETA_WEIGHT x raw + BETA_PRIOR
BETA_PRIOR = 0.33
BETA_WEEKS = 104            # two years of weekly returns
BETA_MIN_WEEKS = 20         # below this no beta is computed
BETA_SHORT_WEEKS = 52       # below this the result is flagged as short history
BETA_TOLERANCE = 0.005      # 'used' may differ from the rule by rounding to two decimals
MULTIPLE_MONTHS = 60        # five years of month-ends
MULTIPLE_TOLERANCE = 0.5    # 'used' may differ from the rule by rounding to a whole multiple
RATE_TOLERANCE = 1e-9

LABEL_HE = 'שווי לפי המכפיל ההיסטורי'

BASES = ('forward_pe', 'ev_sales', 'p_tbv')

# The anchors of the historical-multiple report by company type. `figure` says what next_year_figure must be.
COMPANY_TYPES = {
    'stable_profitable': {
        'basis': 'forward_pe',
        'figure': 'next-year EPS: company guidance plus its usual beat, else attributed consensus',
        'note': None},
    'grower_growth_changed': {
        'basis': 'forward_pe',
        'figure': 'next-year EPS: company guidance plus its usual beat, else attributed consensus',
        'note': 'the old multiple belonged to a different growth rate: read it next to direct peers at today\'s growth'},
    'low_profit': {
        'basis': 'ev_sales',
        'figure': 'next-year revenue per share, on a share count that includes SBC dilution',
        'note': 'own median EV/sales; net cash per share is added'},
    'bank': {
        'basis': 'p_tbv',
        'figure': 'tangible book value per share',
        'note': 'P/TBV is read against ROE: a multiple earned at a different ROE is not a fair value'},
    'cyclical': {
        'basis': 'forward_pe',
        'figure': 'mid-cycle EPS (not next year\'s peak or trough EPS)',
        'note': 'mid-cycle earnings times the mid-cycle multiple (the median over a full cycle)'},
    'levered_complex': {
        'basis': None,
        'figure': None,
        'note': 'project debt, converts with puts: the custom model only, no historical-multiple value'},
}


# ---------------------------------------------------------------------------------------------------------------------
# series helpers
# ---------------------------------------------------------------------------------------------------------------------
def _day(x):
    """A date from a date, a datetime or an ISO string ('YYYY-MM-DD', longer strings are cut; 'YYYY-MM' = day 1)."""
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    if isinstance(x, str):
        s = x.strip()
        if len(s) == 7:
            s += '-01'
        return date.fromisoformat(s[:10])
    raise ValueError(f'not a date: {x!r}')


def _pairs(series):
    """[(date, value)] sorted by date from a {date: value} mapping or a list of (date, value) pairs. Entries without
    a positive finite price are dropped (a missing bar is not a zero price)."""
    items = series.items() if isinstance(series, dict) else series
    out = []
    for d, v in items:
        if v is None or isinstance(v, bool):
            continue
        v = float(v)
        if v != v or v <= 0:
            continue
        out.append((_day(d), v))
    out.sort(key=lambda t: t[0])
    return out


def _weekly(series, as_of=None):
    """{Monday of the week: (date of the last close in that week, close)}; with as_of, only weeks whose Friday is on
    or before as_of (complete weeks). Daily and weekly series give the same result."""
    limit = _day(as_of) if as_of is not None else None
    weeks = {}
    for d, v in _pairs(series):
        monday = d - timedelta(days=d.weekday())
        if limit is not None and monday + timedelta(days=4) > limit:
            continue
        weeks[monday] = (d, v)          # sorted input: the last close of the week wins
    return weeks


def percentile(values, q):
    """The q-quantile (0..1) with linear interpolation between the closest ranks (rank = q x (n - 1))."""
    xs = sorted(float(v) for v in values)
    if not xs:
        raise ValueError('no values')
    if not 0 <= q <= 1:
        raise ValueError('q must be between 0 and 1')
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ---------------------------------------------------------------------------------------------------------------------
# beta
# ---------------------------------------------------------------------------------------------------------------------
def adjusted_beta(raw):
    """The locked adjustment: 0.67 x raw + 0.33."""
    return BETA_WEIGHT * raw + BETA_PRIOR


def beta_2y_weekly(prices, spy_prices, as_of=None, weeks=BETA_WEEKS):
    """The 2-year weekly beta vs SPY and its adjusted value.

    prices, spy_prices: {date: close} or [(date, close)], weekly or daily closes of the stock and of SPY in any
    order. The two are aligned by calendar week (the last close of each week); with as_of, a week counts only when
    its Friday is on or before as_of, so a partial current week is never used. The last `weeks` weekly returns
    (simple returns) are taken; raw = sample covariance / sample variance of SPY's returns.

    Returns {raw, adjusted, weeks, window {from, to}, correlation, short_history, rule}. `weeks` is the number of
    weekly returns used; window is the first and last close date of the stock that entered the returns.
    Raises ValueError when fewer than BETA_MIN_WEEKS common weekly returns exist."""
    a, b = _weekly(prices, as_of), _weekly(spy_prices, as_of)
    common = sorted(set(a) & set(b))
    if len(common) - 1 < BETA_MIN_WEEKS:
        raise ValueError(f'only {max(len(common) - 1, 0)} common weekly returns: at least {BETA_MIN_WEEKS} are needed')
    common = common[-(weeks + 1):]
    ra = [a[common[i]][1] / a[common[i - 1]][1] - 1 for i in range(1, len(common))]
    rb = [b[common[i]][1] / b[common[i - 1]][1] - 1 for i in range(1, len(common))]
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    n = len(ra)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / (n - 1)
    var_a, var_b = statistics.variance(ra), statistics.variance(rb)
    if var_b <= 0:
        raise ValueError('SPY returns have no variance')
    raw = cov / var_b
    corr = cov / (var_a * var_b) ** 0.5 if var_a > 0 else None
    return {
        'raw': round(raw, 4),
        'adjusted': round(adjusted_beta(raw), 4),
        'weeks': n,
        'window': {'from': a[common[0]][0].isoformat(), 'to': a[common[-1]][0].isoformat()},
        'correlation': None if corr is None else round(corr, 3),
        'short_history': n < BETA_SHORT_WEEKS,
        'rule': f'{n} weekly returns of complete weeks vs SPY, adjusted = {BETA_WEIGHT} x raw + {BETA_PRIOR}',
    }


def beta_block(result, used=None, reason=''):
    """The params judgement.beta block from a beta_2y_weekly result. used defaults to the adjusted beta rounded to
    two decimals (the rule)."""
    block = {k: result[k] for k in ('raw', 'adjusted', 'weeks', 'window')}
    block['used'] = round(result['adjusted'], 2) if used is None else used
    block['reason_if_different'] = reason
    return block


# ---------------------------------------------------------------------------------------------------------------------
# own multiple history
# ---------------------------------------------------------------------------------------------------------------------
def _monthly(series):
    """{'YYYY-MM': value} keeping the last dated entry of each month; None values are kept out."""
    items = series.items() if isinstance(series, dict) else series
    rows = sorted(((_day(d), v) for d, v in items if v is not None and not isinstance(v, bool)), key=lambda t: t[0])
    return {d.strftime('%Y-%m'): float(v) for d, v in rows}


def multiple_history(prices, figures, basis='forward_pe', months=MULTIPLE_MONTHS, as_of=None):
    """The company's own multiple by month: price / figure.

    prices: month-end prices {date or 'YYYY-MM': price} (for basis 'ev_sales': the enterprise value per share, or the
    enterprise value in the same unit as the sales). figures: by month, the forward (next-twelve-month) EPS for
    'forward_pe', the forward sales for 'ev_sales', the tangible book value per share for 'p_tbv'. A month enters
    only when both exist and the figure is positive: a P/E on zero or negative earnings is not a multiple (those
    months are counted in `skipped_nonpositive`). The window is the last `months` months up to as_of that have both
    series (None = all of them).

    Returns {basis, median, p25, p75, min, max, months, window {from, to}, latest, skipped_nonpositive}; `months` is
    the number of months used. Quartiles: linear interpolation (percentile). Raises ValueError with no usable month."""
    if basis not in BASES:
        raise ValueError(f'unknown basis {basis!r}: one of {BASES}')
    p, f = _monthly(prices), _monthly(figures)
    limit = _day(as_of).strftime('%Y-%m') if as_of is not None else None
    keys = [k for k in sorted(set(p) & set(f)) if limit is None or k <= limit]
    if months is not None:
        keys = keys[-months:]
    skipped = [k for k in keys if f[k] <= 0 or p[k] <= 0]
    keys = [k for k in keys if f[k] > 0 and p[k] > 0]
    if not keys:
        raise ValueError('no month with a price and a positive figure')
    xs = [p[k] / f[k] for k in keys]
    return {
        'basis': basis,
        'median': round(percentile(xs, 0.5), 2),
        'p25': round(percentile(xs, 0.25), 2),
        'p75': round(percentile(xs, 0.75), 2),
        'min': round(min(xs), 2),
        'max': round(max(xs), 2),
        'months': len(xs),
        'window': {'from': keys[0], 'to': keys[-1]},
        'latest': round(xs[-1], 2),
        'skipped_nonpositive': len(skipped),
    }


def exit_pe_block(history, used=None, ratio_to_model_eps=1.0, reason=''):
    """The params judgement.exit_pe block from a multiple_history result on basis 'forward_pe'. used defaults to the
    rule: own median x ratio_to_model_eps, rounded to a whole multiple."""
    if history.get('basis') != 'forward_pe':
        raise ValueError('the exit P/E rule needs a forward P/E history')
    rule = history['median'] * ratio_to_model_eps
    return {
        'own_median': history['median'], 'p25': history['p25'], 'p75': history['p75'], 'months': history['months'],
        'basis': 'forward_pe', 'ratio_to_model_eps': ratio_to_model_eps,
        'used': round(rule) if used is None else used,
        'reason_if_different': reason,
    }


# ---------------------------------------------------------------------------------------------------------------------
# historical-multiple report
# ---------------------------------------------------------------------------------------------------------------------
def hist_multiple_value(company_type, next_year_figure, history, net_cash_per_share=0, peer_multiple=None):
    """The value by the company's own historical multiple and its p25-p75 range.

    company_type: a key of COMPANY_TYPES (its `figure` says what next_year_figure must be, per share). history: a
    multiple_history result (or any {basis, median, p25, p75, months}) on the basis the type requires.
    net_cash_per_share is added only on basis 'ev_sales' (a P/E or P/TBV is on the share price, which already holds
    the cash). peer_multiple (optional, 'grower_growth_changed'): direct peers' multiple at today's growth, returned
    as peer_value next to the own-history value, never mixed into it.

    Returns {company_type, basis, anchor, next_year_figure, multiple {median, p25, p75, months}, net_cash_per_share,
    value, low, high, peer_value, label_he, notes}. For 'levered_complex' value, low and high are None."""
    if company_type not in COMPANY_TYPES:
        raise ValueError(f'unknown company type {company_type!r}: one of {sorted(COMPANY_TYPES)}')
    spec = COMPANY_TYPES[company_type]
    notes = [spec['note']] if spec['note'] else []
    out = {'company_type': company_type, 'basis': spec['basis'], 'anchor': spec['figure'],
           'next_year_figure': next_year_figure, 'multiple': None, 'net_cash_per_share': net_cash_per_share,
           'value': None, 'low': None, 'high': None, 'peer_value': None, 'label_he': LABEL_HE, 'notes': notes}
    if spec['basis'] is None:
        return out
    if not history or history.get('basis') != spec['basis']:
        raise ValueError(f"{company_type} needs a {spec['basis']} history, got {history.get('basis') if history else None!r}")
    if next_year_figure is None or next_year_figure <= 0:
        raise ValueError('next_year_figure must be positive (a company without it needs another anchor)')
    if net_cash_per_share and spec['basis'] != 'ev_sales':
        raise ValueError('net cash per share is added only to an EV/sales value')
    med, lo, hi = history['median'], history['p25'], history['p75']
    if not lo <= med <= hi:
        raise ValueError('history needs p25 <= median <= p75')
    out['multiple'] = {'median': med, 'p25': lo, 'p75': hi, 'months': history.get('months')}
    out['value'] = round(next_year_figure * med + net_cash_per_share, 2)
    out['low'] = round(next_year_figure * lo + net_cash_per_share, 2)
    out['high'] = round(next_year_figure * hi + net_cash_per_share, 2)
    if peer_multiple is not None:
        out['peer_value'] = round(next_year_figure * peer_multiple + net_cash_per_share, 2)
    if history.get('months') is not None and history['months'] < MULTIPLE_MONTHS:
        notes.append(f"history of {history['months']} months, shorter than {MULTIPLE_MONTHS}")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# market inputs of the lean path
# ---------------------------------------------------------------------------------------------------------------------
def load_market(path=None):
    """research/market.json (the dated risk-free rate and ERP of the lean path)."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'research', 'market.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def market_in_force(market, as_of):
    """The entry of market['in_force'] that applies on as_of: the latest one whose `from` is on or before it.
    Returns {from, rf, erp, index_return} or None when as_of is before the first entry."""
    day = _day(as_of)
    best = None
    for e in market.get('in_force', []):
        if _day(e['from']) <= day and (best is None or _day(e['from']) > _day(best['from'])):
            best = e
    if best is None:
        return None
    rf, erp = best['rf']['value'], best['erp']['value']
    return {'from': best['from'], 'rf': rf, 'erp': erp, 'index_return': round(rf + erp, 6)}


def check_market(params, market):
    """Deviations of a params file's rf and erp from the entry in force on its as_of: a list of
    {key, used, rule, from} (empty when both follow research/market.json)."""
    entry = market_in_force(market, params.get('as_of')) if params.get('as_of') else None
    if entry is None:
        return [{'key': k, 'used': params.get(k), 'rule': None, 'from': None} for k in ('rf', 'erp')]
    return [{'key': k, 'used': params.get(k), 'rule': entry[k], 'from': entry['from']}
            for k in ('rf', 'erp')
            if params.get(k) is None or abs(params[k] - entry[k]) > RATE_TOLERANCE]


# ---------------------------------------------------------------------------------------------------------------------
# the judgement block
# ---------------------------------------------------------------------------------------------------------------------
def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def _reason(part):
    r = part.get('reason_if_different')
    return isinstance(r, str) and bool(r.strip())


def validate_judgement(block, params=None, market=None):
    """Check a params `judgement` block against the locked rules.

    A value may differ from its rule, but then reason_if_different must say why; a block where `used` differs and the
    reason is empty is rejected. With params, the block must also describe the numbers the model actually runs on
    (beta, base exit P/E, beta_mature, roic_terminal, rf, erp); with market (research/market.json), rf and erp must be
    the entry in force on the params date or carry a reason.

    Returns {ok, errors, warnings, rule}: rule holds what each rule gives (beta, exit_pe, rf, erp) for the record."""
    errors, warnings, rule = [], [], {}
    if not isinstance(block, dict):
        return {'ok': False, 'errors': ['judgement: missing or not an object'], 'warnings': [], 'rule': {}}

    # beta
    b = block.get('beta')
    if not isinstance(b, dict) or not _num(b.get('used')):
        errors.append('beta: block with a numeric `used` is required')
    else:
        if _num(b.get('raw')):
            rule['beta'] = round(adjusted_beta(b['raw']), 4)
            if _num(b.get('adjusted')) and abs(b['adjusted'] - rule['beta']) > BETA_TOLERANCE:
                errors.append(f"beta: adjusted {b['adjusted']} is not {BETA_WEIGHT} x raw {b['raw']} + {BETA_PRIOR} = {rule['beta']}")
            if not _num(b.get('weeks')) or not isinstance(b.get('window'), dict):
                errors.append('beta: weeks and window {from, to} of the measurement are required')
            elif b['weeks'] < BETA_SHORT_WEEKS:
                warnings.append(f"beta: measured on {b['weeks']} weeks, less than a year")
            if abs(b['used'] - rule['beta']) > BETA_TOLERANCE and not _reason(b):
                errors.append(f"beta: used {b['used']} differs from the rule {rule['beta']} and reason_if_different is empty")
        elif not _reason(b):
            errors.append('beta: no measured raw beta and reason_if_different is empty')
        if params is not None and _num(params.get('beta')) and abs(params['beta'] - b['used']) > 1e-9:
            errors.append(f"beta: used {b['used']} is not the params beta {params['beta']}")

    # exit P/E
    e = block.get('exit_pe')
    if not isinstance(e, dict) or not _num(e.get('used')):
        errors.append('exit_pe: block with a numeric `used` is required')
    else:
        if _num(e.get('own_median')):
            ratio = e.get('ratio_to_model_eps', 1.0)
            if not _num(ratio) or ratio <= 0:
                errors.append('exit_pe: ratio_to_model_eps must be a positive number')
            else:
                rule['exit_pe'] = round(e['own_median'] * ratio, 2)
                if abs(e['used'] - rule['exit_pe']) > MULTIPLE_TOLERANCE + 1e-9 and not _reason(e):
                    errors.append(f"exit_pe: used {e['used']} differs from the rule {rule['exit_pe']} "
                                  f"(own median {e['own_median']} x {ratio}) and reason_if_different is empty")
            if not _num(e.get('months')) or not e.get('basis'):
                errors.append('exit_pe: months and basis of the own-median history are required')
            elif e['months'] < MULTIPLE_MONTHS:
                warnings.append(f"exit_pe: own median over {e['months']} months, shorter than {MULTIPLE_MONTHS}")
        elif not _reason(e):
            errors.append('exit_pe: no own forward P/E median and reason_if_different is empty')
        base = (((params or {}).get('scenarios') or {}).get('base') or {}).get('pe')
        if _num(base) and abs(base - e['used']) > 1e-9:
            errors.append(f"exit_pe: used {e['used']} is not the params base exit P/E {base}")

    # hand-set terminal inputs: a one-line source each
    for key in ('beta_mature', 'roic_terminal'):
        part = block.get(key)
        if not isinstance(part, dict) or not _num(part.get('value')):
            errors.append(f'{key}: block with a numeric `value` is required')
            continue
        if not isinstance(part.get('source'), str) or not part['source'].strip():
            errors.append(f'{key}: a one-line source is required')
        if params is not None and _num(params.get(key)) and abs(params[key] - part['value']) > 1e-9:
            errors.append(f"{key}: value {part['value']} is not the params {key} {params[key]}")

    # historical-multiple report (optional; when present it must recompute)
    h = block.get('hist_multiple')
    if h is not None:
        if not isinstance(h, dict):
            errors.append('hist_multiple: not an object')
        else:
            try:
                got = hist_multiple_value(h.get('company_type'), h.get('next_year_figure'),
                                          {k: h.get(k) for k in ('basis', 'median', 'p25', 'p75', 'months')},
                                          h.get('net_cash_per_share') or 0)
            except (ValueError, TypeError) as ex:
                errors.append(f'hist_multiple: {ex}')
            else:
                rule['hist_multiple'] = {k: got[k] for k in ('value', 'low', 'high')}
                for k in ('value', 'low', 'high'):
                    if got[k] is not None and _num(h.get(k)) and abs(h[k] - got[k]) > 0.011:
                        errors.append(f'hist_multiple: {k} {h[k]} is not the computed {got[k]}')
                if got['value'] is not None and (not isinstance(h.get('source'), str) or not h['source'].strip()):
                    errors.append('hist_multiple: the source of next_year_figure is required')

    # rf and ERP from research/market.json
    m = block.get('market')
    if market is not None and params is not None:
        devs = check_market(params, market)
        entry = market_in_force(market, params['as_of']) if params.get('as_of') else None
        if entry:
            rule['rf'], rule['erp'] = entry['rf'], entry['erp']
        if devs and not (isinstance(m, dict) and _reason(m)):
            for d in devs:
                errors.append(f"market: {d['key']} {d['used']} differs from research/market.json {d['rule']} "
                              f"(in force from {d['from']}) and reason_if_different is empty")
    if isinstance(m, dict) and params is not None:
        for k in ('rf', 'erp'):
            if _num(m.get(k)) and _num(params.get(k)) and abs(m[k] - params[k]) > RATE_TOLERANCE:
                errors.append(f'market: {k} {m[k]} is not the params {k} {params[k]}')
    return {'ok': not errors, 'errors': errors, 'warnings': warnings, 'rule': rule}
