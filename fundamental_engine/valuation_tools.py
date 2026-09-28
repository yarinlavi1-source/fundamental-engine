"""Auditable supporting tools: replacement cycles, stresses and expectations."""
from copy import deepcopy
from datetime import date
from .finance import number
from .valuation import value_company, years, dt


def asset_schedule(cohorts, boundaries):
    """Straight-line asset cohorts. Replacements occur on service anniversaries.

    Returns maintenance cash/depreciation to explicitly copy into forecasts.
    Initial acquisition cash belongs in the separate growth-capex schedule.
    A fully depreciated asset need not be physically obsolete: replacement life
    and accounting life are distinct analyst inputs, not inferred from earnings.
    """
    if len(boundaries) < 2 or boundaries != sorted(set(boundaries)) or len(boundaries) > 121:
        raise ValueError('Use 2..121 ascending date boundaries')
    dates = [dt(d) for d in boundaries]
    result = [{'start': a.isoformat(), 'end': b.isoformat(), 'depreciation': 0., 'replacement_capex': 0.} for a, b in zip(dates, dates[1:])]
    seen = set()
    for c in cohorts:
        if c['id'] in seen: raise ValueError('Duplicate asset cohort')
        seen.add(c['id'])
        start = dt(c['in_service'])
        cost = number(c['cost'], 'cost', 0)
        salvage = number(c['salvage_fraction'], 'salvage_fraction', 0, 1)
        life = c['depreciation_years']; replacement = c['replacement_years']
        for k in (life, replacement):
            if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
                raise ValueError('Asset lives must be integer years 1..100')
        inflation = number(c['replacement_cost_growth'], 'replacement_cost_growth', -.5, 1)
        def anniversary(d, y):
            try: return d.replace(year=d.year + y)
            except ValueError: return d.replace(year=d.year + y, day=28)
        cycle = 0
        while start < dates[-1]:
            if cycle > 150: raise ValueError('Asset history too long')
            repl_date = anniversary(start, replacement)
            dep_end = min(anniversary(start, life), repl_date)
            depreciable = cost * (1 - salvage)
            lifetime_days = (anniversary(start, life) - start).days
            for row, a, b in zip(result, dates, dates[1:]):
                overlap = max(0, (min(b, dep_end) - max(a, start)).days)
                row['depreciation'] += depreciable * overlap / lifetime_days
                if a < repl_date <= b:
                    row['replacement_capex'] += cost * (1 + inflation) ** replacement - cost * salvage
            cost *= (1 + inflation) ** replacement
            start = repl_date; cycle += 1
    return result


def stress_test(case):
    if case['method'] != 'operating': raise ValueError('Operating stresses require operating model')
    shocks = ('prices_down_20pct', 'cash_costs_up_15pct', 'capex_up_25pct',
              'equity_issue_price_down_30pct', 'uncommitted_funding_unavailable')
    original = value_company(case)
    out = []
    for shock in shocks:
        c = deepcopy(case)
        for s in c['scenarios']:
            for p in s['periods']:
                for seg in p['segments']:
                    if shock == 'prices_down_20pct': seg['annual_revenue_per_unit'] *= .8
                    if shock == 'cash_costs_up_15pct': seg['cash_cost_ratio'] *= 1.15
                if shock == 'prices_down_20pct': _scale_prepayments(c, p, .8)
                if shock == 'cash_costs_up_15pct': p['fixed_cash_costs'] *= 1.15
                if shock == 'capex_up_25pct':
                    p['growth_capex'] *= 1.25; p['maintenance_capex'] *= 1.25
                if shock == 'equity_issue_price_down_30pct': p['equity_issue_price'] *= .7
                if shock == 'uncommitted_funding_unavailable' and p['financing_status'] == 'assumed':
                    p['debt_draw'] = p['equity_proceeds'] = p['debt_fees'] = 0
                    p['financing_status'] = 'none'
            # Keep terminal operating margin linked to price/cost shock, not reset to optimistic default.
            if shock == 'prices_down_20pct': s['terminal']['operating_margin'] *= .8
            if shock == 'cash_costs_up_15pct': s['terminal']['operating_margin'] *= .85
        try:
            r = value_company(c)
            out.append({'shock': shock, 'status': r['status'], 'annual_values': r['annual_values'],
                        'first_funding_gaps': {x['name']: next((p['end'] for p in x['forecast'] if p['funding_gap'] > 1e-7), None) for x in r['scenarios']}})
        except ValueError as e:
            out.append({'shock': shock, 'status': 'invalid_stressed_input', 'reason': str(e)})
    return {'baseline': original['annual_values'], 'stresses': out,
            'interpretation': 'Fixed analyst shocks, not calibrated probabilities. Cost-ratio shocks approximate cash costs; do not claim causal precision. Financing repricing holds gross proceeds fixed; unavailable funding is tested separately.'}


def reverse_price(case, low=.25, high=4.):
    """Solve a uniform unit-price multiplier for the base case, with explicit caveats.

    Units, capex, financing and utilization held fixed. Reject gaps/nonmonotonicity
    rather than pretending an economically infeasible solution is market consensus.
    """
    if case['method'] != 'operating': raise ValueError('Reverse price requires operating model')
    number(low, 'low', .000001); number(high, 'high', .000001)
    if high <= low: raise ValueError('Invalid reverse bounds')
    target = case['quote']['price']
    def calc(x):
        c = deepcopy(case)
        s = next(s for s in c['scenarios'] if s['name'] == 'base')
        for p in s['periods']:
            for seg in p['segments']: seg['annual_revenue_per_unit'] *= x
            _scale_prepayments(c, p, x)
        r = value_company(c)
        return r['annual_values'][0]['base']
    samples = [(low + (high - low) * i / 20) for i in range(21)]
    try:
        values = [calc(x) for x in samples]
    except ValueError as e:
        return {'status':'invalid_search_domain','multiplier':None,'limitation':str(e)}
    note = ('Uniform unit-price sensitivity, NOT inferred market expectations. Fixed capacity, capex, financing, terminal margin and cost ratios. '
            'Absolute SBC, capex and fixed costs do not scale, so low multipliers can create artificial funding gaps. '
            'Must re-underwrite feasibility. Also see implied_cost_of_equity and, for driver specs, implied_growth_shift.')
    domain_note = None
    if any(v is None for v in values):
        # Low multipliers can starve cash because absolute costs do not scale. Search
        # only the contiguous funded upper part of the domain and say so explicitly.
        first = next((i for i, v in enumerate(values) if v is not None), None)
        if first is None or any(v is None for v in values[first:]):
            return {'status': 'funding_gap_in_search_domain', 'multiplier': None, 'limitation': note}
        low, samples, values = samples[first], samples[first:], values[first:]
        domain_note = f'Multipliers below {low:.3f} create a funding gap; searched the funded domain only.'
    if any(b < a - 1e-8 for a, b in zip(values, values[1:])):
        return {'status': 'nonmonotonic', 'multiplier': None, 'limitation': note}
    if not values[0] <= target <= values[-1] or abs(values[-1] - values[0]) < 1e-10:
        return {'status': 'not_bracketed', 'multiplier': None, 'limitation': note}
    for _ in range(60):
        mid = (low + high) / 2
        v = calc(mid)
        if v is None or v < target: low = mid
        else: high = mid
    out = {'status': 'solved', 'multiplier': (low + high) / 2,
           'reconstructed_price': calc((low + high) / 2), 'limitation': note}
    if domain_note: out['search_domain'] = domain_note
    return out


def _scale_prepayments(case, period, factor):
    """Apply a price shock to the deferred-revenue ledger.

    prepayment_price_linkage='scaled' (subscription billing, set by driver specs):
    billings and recognition move with price. Default 'fixed' (contracted advances):
    amounts stay, but recognition can never exceed the shocked period revenue.
    """
    if case.get('prepayment_price_linkage', 'fixed') == 'scaled':
        for key in ('customer_prepayments', 'revenue_from_prepayments'):
            if key in period:
                period[key] *= factor
    elif 'revenue_from_prepayments' in period:
        revenue = sum(seg['average_units'] * seg['annual_revenue_per_unit'] * seg['utilization'] for seg in period['segments']) * years(period['start'], period['end'])
        period['revenue_from_prepayments'] = min(period['revenue_from_prepayments'], revenue)


def _shifted_value(case, scenario, shift):
    c = deepcopy(case)
    s = next((x for x in c['scenarios'] if x['name'] == scenario), None)
    if s is None: raise ValueError('Unknown scenario')
    s['cost_of_equity'] += shift
    if c['method'] == 'operating': s['terminal']['wacc'] += shift
    try:
        return value_company(c)['annual_values'][0][scenario]
    except ValueError:
        return None


def discount_rate_band(case, scenario='base', shift=.01):
    """Value today at cost of equity (and terminal WACC) minus/plus one shift.

    Answers: does a modest, defensible change in required return flip the price
    conclusion? It is not a probability interval.
    """
    if case['method'] not in {'operating', 'residual_income'}: raise ValueError('Discount band requires a cash-flow model')
    number(shift, 'shift', .001, .05)
    low_rate, central, high_rate = (_shifted_value(case, scenario, x) for x in (-shift, 0, shift))
    quote = case['quote']['price']
    flips = None
    if None not in (low_rate, central, high_rate):
        flips = min(low_rate, high_rate) <= quote <= max(low_rate, high_rate)
    relative_width = (low_rate - high_rate) / central if None not in (low_rate, central, high_rate) and central > 0 else None
    return {'scenario': scenario, 'shift': shift, 'value_lower_rate': low_rate, 'value': central, 'value_higher_rate': high_rate,
            'relative_width': relative_width, 'quote_inside_band': flips,
            'meaning': 'Values with cost of equity and terminal WACC moved together by the shift. A quote inside the band means a one-step change in required return changes the cheap/expensive call.'}


def implied_cost_of_equity(case, scenario='base', low=-.08, high=.12):
    """Cost-of-equity shift (with terminal WACC) at which the scenario value equals the quote.

    Holds every cash flow fixed. It says what return the price implies IF these cash
    flows occur; it is not the market's forecast and not a recommended hurdle rate.
    """
    if case['method'] not in {'operating', 'residual_income'}: raise ValueError('Implied rate requires a cash-flow model')
    s = next((x for x in case['scenarios'] if x['name'] == scenario), None)
    if s is None: raise ValueError('Unknown scenario')
    target = case['quote']['price']
    ke = s['cost_of_equity']
    low = max(low, -ke + .005)
    if case['method'] == 'operating':
        # Terminal WACC must stay above terminal growth.
        low = max(low, s['terminal']['growth'] - s['terminal']['wacc'] + .0025)
    lo_v, hi_v = _shifted_value(case, scenario, low), _shifted_value(case, scenario, high)
    note = 'Cash flows held fixed; required return that equates scenario value and quote. Not a market forecast or hurdle recommendation.'
    if lo_v is None or hi_v is None:
        return {'status': 'invalid_search_domain', 'implied_cost_of_equity': None, 'limitation': note}
    if not hi_v <= target <= lo_v:
        return {'status': 'not_bracketed', 'implied_cost_of_equity': None, 'range_values': [hi_v, lo_v], 'limitation': note}
    for _ in range(60):
        mid = (low + high) / 2
        v = _shifted_value(case, scenario, mid)
        if v is None: return {'status': 'invalid_search_domain', 'implied_cost_of_equity': None, 'limitation': note}
        if v > target: low = mid
        else: high = mid
    shift = (low + high) / 2
    return {'status': 'solved', 'scenario': scenario, 'model_cost_of_equity': ke, 'implied_cost_of_equity': ke + shift,
            'shift': shift, 'limitation': note}


def forecast_score(observations):
    """Ex-post operating forecast evaluation; NOT a backtest of stock returns."""
    if not observations: raise ValueError('Require forecast/actual observations')
    definitions={(r['metric'],r['forecast_currency'],r['forecast_basis']) for r in observations}
    if len(definitions)!=1: raise ValueError('Evaluate one metric/currency/basis at a time')
    errors, covered, percentage, seen = [], [], [], set()
    for r in observations:
        key = (r['company_id'], r['metric'], r['target_date'])
        if key in seen: raise ValueError('Duplicate observation; select one frozen forecast vintage')
        seen.add(key)
        if not dt(r['forecast_as_of']) < dt(r['target_date']) <= dt(r['actual_available_at']):
            raise ValueError('Forecast must predate outcome; actual must be available after target')
        if r['forecast_currency'] != r['actual_currency'] or r['forecast_basis'] != r['actual_basis']:
            raise ValueError('Forecast and actual definitions must match')
        f = number(r['forecast'], 'forecast'); a = number(r['actual'], 'actual')
        lo = number(r['low'], 'low'); hi = number(r['high'], 'high')
        if lo > hi: raise ValueError('Inverted interval')
        errors.append(abs(f - a)); covered.append(lo <= a <= hi)
        if a: percentage.append(abs(f - a) / abs(a))
    return {'observations': len(errors), 'mean_absolute_error': sum(errors) / len(errors),
            'mean_absolute_percentage_error': sum(percentage) / len(percentage) if percentage else None,
            'percentage_error_observations': len(percentage), 'interval_coverage': sum(covered) / len(covered),
            'limitation': 'Descriptive sample only. Same metric/units needed for interpretable MAE; no investment win rate, no causal accuracy guarantee.'}
