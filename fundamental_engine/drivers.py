"""Ratio-driven operating specs expanded into an auditable valuation_version=1 case.

Recurring-revenue businesses are usually underwritten as growth rates and costs in
percent of revenue. Hand-converting those ratios into absolute period inputs is
error-prone (the ServiceNow run of 2026-09-28 needed a 200-line script). This module
does that conversion deterministically and records the spec hash in the case.

It adds NO economic judgement: every ratio, rate and balance is still an analyst
input that needs sources, rationale and falsifiers in the ordinary assumption ledger.
The generated case is then executed and audited by value_company exactly as before.

Ratio keys with a default (zero SBC, interest, depreciation, capex, working capital, payout;
80% prepaid share) are economic assumptions, not neutral blanks. Each default actually
used is listed in generated_from.defaulted_keys with a warning; spec['strict_drivers']=True
refuses to default any ratio key.
"""
from copy import deepcopy
from hashlib import sha256
import json
from .finance import number
from .valuation import value_company, years

PASSTHROUGH = ('valuation_version', 'is_demo', 'as_of', 'company_id', 'ticker', 'currency', 'unit', 'archetype',
               'method', 'quote', 'sources', 'assumptions', 'opening', 'report_dates', 'underwriting')
RATIO_KEYS = {
    # key: (default, low, high)
    'cash_cost_ratio': (None, 0, 2),
    'sbc_pct': (0, 0, 1),
    'depreciation_pct': (0, 0, 1),
    'maintenance_capex_pct': (0, 0, 5),   # capital-heavy builders can spend more than revenue in a year
    'growth_capex_pct': (0, 0, 5),
    'tax_rate': (None, 0, 1),
    'interest_rate': (0, 0, 1),
    'deferred_revenue_ratio': (0, 0, 5),
    'working_capital_ratio': (0, -2, 2),
    'prepaid_share': (.8, 0, 1),
    'nol_usage_limit': (0, 0, 1),
    'payout_fraction': (0, 0, 1),
}
ABSOLUTE_KEYS = ('amortization', 'minimum_cash', 'investment_income_after_tax', 'debt_draw', 'debt_repayment', 'fixed_cash_costs',
                 'equity_proceeds', 'investment_liquidation', 'sbc_share_settlement')


def _series(spec_row, key, count, default=None, low=None, high=None):
    value = spec_row.get(key, default)
    if value is None:
        raise ValueError(f'drivers: {key} is required')
    values = value if isinstance(value, list) else [value] * count
    if len(values) != count:
        raise ValueError(f'drivers: {key} needs {count} values (one per period) or a single value')
    return [number(v, key, low, high) for v in values]


def build_case(spec):
    """Expand a driver spec (driver_version 1) into a valuation_version 1 case."""
    spec = deepcopy(spec)
    if spec.get('driver_version') != 1:
        raise ValueError('driver_version must be 1')
    json.dumps(spec, allow_nan=False)
    if spec.get('method', 'operating') != 'operating':
        raise ValueError('Driver specs build operating-route cases only')
    boundaries = spec['boundaries']
    if not boundaries or boundaries != sorted(set(boundaries)):
        raise ValueError('boundaries must be ascending and distinct')
    as_of = spec['as_of']
    # interim_boundaries: dates that split a fiscal year (e.g. the fifth anniversary of as_of) so it is an explicit
    # valuation date. The period starting there continues the same annual rate: no growth step, no working-capital step.
    interim = spec.get('interim_boundaries') or []
    if any(d not in boundaries[:-1] for d in interim):
        raise ValueError('interim_boundaries must be boundaries other than the last')
    starts = [as_of] + boundaries[:-1]
    durations = [years(a, b) for a, b in zip(starts, boundaries)]
    if any(not 0 < d <= 1.01 for d in durations):
        raise ValueError('Each period must be forward and at most one year')
    count = len(boundaries)
    stub = spec.get('stub_annual_revenue_rate')
    growth_count = count - 1 if stub is not None else count
    last_annual = number(spec['last_full_year_revenue'], 'last_full_year_revenue', 1e-9)
    opening_deferred = number(spec['opening'].get('deferred_revenue', 0), 'opening.deferred_revenue', 0)
    case = {k: deepcopy(spec[k]) for k in PASSTHROUGH if k in spec}
    case.setdefault('valuation_version', 1)
    case.setdefault('method', 'operating')
    case['scenarios'] = []
    segment_name = spec.get('segment_name', 'Revenue')
    driver = spec.get('segment_driver', 'subscribers')
    strict = spec.get('strict_drivers', False)
    if not isinstance(strict, bool):
        raise ValueError('strict_drivers must be boolean')
    defaulted = []
    for sc in spec['scenarios']:
        missing = [k for k in RATIO_KEYS if k not in sc]
        if strict and missing:
            raise ValueError(f"drivers: strict_drivers forbids defaults; scenario {sc['name']} is missing {', '.join(missing)}")
        defaulted += [{'scenario': sc['name'], 'key': k, 'default': RATIO_KEYS[k][0]} for k in missing if RATIO_KEYS[k][0] is not None]
        growth = _series(sc, 'growth', growth_count, None, -.9, 3)
        ratios = {k: _series(sc, k, count, *RATIO_KEYS[k]) for k in RATIO_KEYS}
        absolutes = {k: _series(sc, k, count, 0, 0) for k in ABSOLUTE_KEYS}
        rate_prev, deferred = last_annual, opening_deferred
        periods = []
        gi = 0
        for i, (start, end, dur) in enumerate(zip(starts, boundaries, durations)):
            if i == 0 and stub is not None:
                rate = number(stub, 'stub_annual_revenue_rate', 1e-9)
                wc_base = 0  # the stub continues the current fiscal year; no new annual step yet
            elif start in interim:
                rate, wc_base = rate_prev, 0  # continuation of a split fiscal year; its growth entry is ignored
                gi += 1
            else:
                rate = (last_annual if gi == 0 else rate_prev) * (1 + growth[gi])
                wc_base = rate - (last_annual if gi == 0 else rate_prev)
                gi += 1
            revenue = rate * dur
            target = ratios['deferred_revenue_ratio'][i] * rate
            recognized = ratios['prepaid_share'][i] * revenue
            received = recognized + target - deferred
            if received < 0:
                # A shrinking balance recognizes more of the existing liability than new billings.
                recognized, received = recognized - received, 0
                if recognized > min(revenue, deferred) + 1e-6:
                    raise ValueError(f'drivers: deferred revenue ratio falls faster than revenue can absorb in {end}')
            deferred = target
            debt_draw = absolutes['debt_draw'][i]
            periods.append({
                'start': start, 'end': end,
                'assumption_ids': sc.get('period_assumption_ids', sc['assumption_ids']),
                'segments': [{'name': segment_name, 'driver': driver, 'average_units': 1,
                              'annual_revenue_per_unit': rate, 'utilization': 1,
                              'cash_cost_ratio': ratios['cash_cost_ratio'][i]}],
                'fixed_cash_costs': absolutes['fixed_cash_costs'][i],
                'sbc_expense': ratios['sbc_pct'][i] * revenue, 'sbc_shares': 0,
                'depreciation': ratios['depreciation_pct'][i] * revenue + absolutes['amortization'][i],
                'debt_draw': debt_draw, 'debt_repayment': absolutes['debt_repayment'][i],
                'interest_rate': ratios['interest_rate'][i], 'debt_fees': 0,
                'tax_rate': ratios['tax_rate'][i], 'nol_usage_limit': ratios['nol_usage_limit'][i],
                'change_working_capital_excluding_deferred': ratios['working_capital_ratio'][i] * wc_base,
                'customer_prepayments': received, 'revenue_from_prepayments': recognized,
                'maintenance_capex': ratios['maintenance_capex_pct'][i] * revenue,
                'growth_capex': ratios['growth_capex_pct'][i] * revenue,
                'minimum_cash': absolutes['minimum_cash'][i], 'payout_fraction': ratios['payout_fraction'][i],
                'investment_income_after_tax': absolutes['investment_income_after_tax'][i] * dur,
                # equity raises dilute existing holders at an explicit issue price (default: today's quote)
                'equity_proceeds': absolutes['equity_proceeds'][i],
                'equity_issue_price': number((sc.get('equity_issue_price') or [case['quote']['price']] * count)[i]
                                             if isinstance(sc.get('equity_issue_price'), list) else
                                             (sc.get('equity_issue_price') or case['quote']['price']), 'equity_issue_price', 1e-9),
                'equity_fee_rate': number(sc.get('equity_fee_rate', 0), 'equity_fee_rate', 0, .2),
                'investment_liquidation': absolutes['investment_liquidation'][i],
                # part of the cash-equivalent SBC charge settled in new shares (at equity_issue_price) instead of cash
                'sbc_share_settlement': absolutes['sbc_share_settlement'][i],
                'financing_status': sc.get('financing_status', 'committed' if (debt_draw or absolutes['equity_proceeds'][i]) else 'none'),
            })
            rate_prev = rate
        if sc.get('sbc_policy', 'cash_equivalent') != 'cash_equivalent':
            raise ValueError('Driver specs charge SBC as a cash-equivalent cost; model explicit share issuance in a full case')
        case['scenarios'].append({'name': sc['name'], 'thesis': sc['thesis'], 'assumption_ids': sc['assumption_ids'],
                                  'cost_of_equity': sc['cost_of_equity'], 'sbc_policy': 'cash_equivalent',
                                  'periods': periods, 'terminal': deepcopy(sc['terminal'])})
    case['prepayment_price_linkage'] = 'scaled'
    case['generated_from'] = {'tool': 'drivers.build_case', 'driver_version': 1,
                              'spec_sha256': sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                              'strict_drivers': strict, 'defaulted_keys': defaulted, 'interim_boundaries': list(interim),
                              'warnings': ['Defaulted driver ratios are silent economic assumptions; declare them explicitly: '
                                           + ', '.join(sorted({f"{d['key']}={d['default']}" for d in defaulted}))] if defaulted else []}
    return case


RELEASE_FLOOR = .5        # a declining business keeps replacing at least half of its maintenance capex (forecast reinvestment rule)


def reinvest(fy_rate, rates, dur, capint, wc, dr, maint, release, d_rev0=None):
    """Per fiscal year: (growth capex %, maintenance capex %, net reinvestment need) from the identity
    growth capex + change in working capital - change in deferred revenue = change in revenue x capital per unit of revenue,
    on the bases drivers.build_case uses (year 0 = the stub: the current year's need pro rata, no working-capital step;
    year 1: deferred revenue moves from the stub rate). A negative need (a shrinking business releasing capital) is taken from
    maintenance capex (assets not replaced, never below half the maintenance level: a shrinking business still replaces part of
    what it keeps) when release is True, else growth capex is floored at zero."""
    gc, mc, need = [], [], []
    for t in range(len(rates)):
        if t == 0:
            d_rev = d_rev0
            nr, dwc, ddef = d_rev * capint[0] * dur[0], 0., 0.
        else:
            d_rev = fy_rate[t] - fy_rate[t - 1]
            nr, dwc = d_rev * capint[t], wc * d_rev
            ddef = dr * (rates[t] - (rates[0] if t == 1 else rates[t - 1]))
        need.append(nr)
        x = (nr - dwc + ddef) / (rates[t] * dur[t])
        if x >= 0 or not release:
            gc.append(max(0., x))
            mc.append(maint)
        else:
            gc.append(0.)
            mc.append(max(RELEASE_FLOOR * maint, maint + x))
    return gc, mc, need


def capex_for_path(spec, sc, rule):
    """Per-period growth / maintenance capex % of revenue from the reinvestment rule on this scenario's growth path."""
    B, n = spec['boundaries'], len(spec['boundaries'])
    starts = [spec['as_of']] + B[:-1]
    interim = set(spec.get('interim_boundaries') or [])
    fy = []                                    # period indices per fiscal year; index 0 = the stub
    for i in range(n):
        if i and starts[i] in interim:
            fy[-1].append(i)
        else:
            fy.append([i])
    g = sc['growth']
    rates, prev = [spec['stub_annual_revenue_rate']], spec['last_full_year_revenue']
    for t in range(1, len(fy)):
        prev *= 1 + g[fy[t][0] - 1]
        rates.append(prev)
    fy_rate = [spec['last_full_year_revenue']] + rates[1:]
    dur = [years(starts[ix[0]], B[ix[-1]]) for ix in fy]
    first = lambda v: v[0] if isinstance(v, list) else v
    gc, mc, _ = reinvest(fy_rate, rates, dur, rule['capital_per_revenue'], first(sc['working_capital_ratio']), first(sc['deferred_revenue_ratio']),
                         rule['maintenance_capex_pct'], rule['release'], d_rev0=rule['stub_revenue_change'])
    pg, pm = [0.] * n, [0.] * n
    for t, ix in enumerate(fy):
        for i in ix:
            pg[i], pm[i] = round(gc[t], 6), round(mc[t], 6)
    return pg, pm


def value_paused(spec, name='base'):
    """Scenario value today; a funding gap first pauses the earlier distributions (the company keeps cash to fund the path,
    value-neutral now that retained cash is worth face), and only a gap left after that makes the case unfunded (None)."""
    sc = next(x for x in spec['scenarios'] if x['name'] == name)
    for _ in range(len(sc['payout_fraction']) + 1):
        try:
            r = value_company(build_case(spec))
        except ValueError:
            return None
        rs = next(x for x in r['scenarios'] if x['name'] == name)
        if rs['status'] != 'funding_blocked':
            return r['annual_values'][0][name]
        j = next(i for i, row in enumerate(rs['forecast']) if row['funding_gap'] > 1e-6)
        paid = [k for k in range(j) if sc['payout_fraction'][k] > 0]
        if not paid:
            return None
        for k in paid:
            sc['payout_fraction'][k] = 0.
    return None


def implied_growth_shift(spec, scenario='base', low=-.15, high=.30):
    """Uniform growth shift (every non-stub period) at which the scenario value equals the quote.

    Margins, SBC, financing and terminal assumptions stay fixed. Capex ratios stay fixed too, unless the scenario carries
    the forecast's reinvestment_rule (capital per unit of new revenue): then growth capex is recomputed on the shifted path,
    so faster growth costs capital. The answer is a sensitivity of THIS spec, not the market's expectation, and must be
    re-underwritten for demand, capacity and competition before it is used.
    """
    base = next((s for s in spec['scenarios'] if s['name'] == scenario), None)
    if base is None:
        raise ValueError('Unknown scenario')
    original = base['growth'] if isinstance(base['growth'], list) else None
    target = spec['quote']['price']

    stub = 1 if spec.get('stub_annual_revenue_rate') is not None else 0
    interim = set(spec.get('interim_boundaries') or [])
    starts = [spec['as_of']] + spec['boundaries'][:-1]
    cont = [starts[i + stub] in interim for i in range(len(spec['boundaries']) - stub)]

    def calc(shift):
        s2 = deepcopy(spec)
        sc = next(s for s in s2['scenarios'] if s['name'] == scenario)
        g = sc['growth']
        sc['growth'] = [x + (0 if c else shift) for x, c in zip(g, cont)] if isinstance(g, list) else g + shift
        if sc.get('reinvestment_rule') and isinstance(sc['growth'], list) and stub:
            sc['growth_capex_pct'], sc['maintenance_capex_pct'] = capex_for_path(s2, sc, sc['reinvestment_rule'])
            v = value_paused(s2, scenario)       # the faster path keeps cash first (distributions paused, value-neutral)
            if v is None:
                return None, None
        try:
            r = value_company(build_case(s2))
        except ValueError:
            return None, None
        final = next(x for x in r['scenarios'] if x['name'] == scenario)['forecast'][-1]
        return r['annual_values'][0][scenario], final['revenue'] / years(final['start'], final['end'])

    note = ('Uniform growth shift with ' + ('growth capex recomputed from the reinvestment rule' if base.get('reinvestment_rule') else
                                           'all ratios fixed') + '; a sensitivity of this spec, not market consensus. Re-underwrite feasibility.')
    lo_v, _ = calc(low)
    hi_v, _ = calc(high)
    for h in (.2, .12, .08, .05, .03):            # above some shift the path is unfunded even with distributions paused
        if hi_v is not None or h >= high:
            break
        high = h
        hi_v, _ = calc(high)
    if lo_v is None or hi_v is None:
        return {'status': 'invalid_search_domain', 'growth_shift': None, 'limitation': note}
    if not lo_v <= target <= hi_v:
        return {'status': 'not_bracketed', 'growth_shift': None, 'range_values': [lo_v, hi_v], 'limitation': note}
    for _ in range(50):
        mid = (low + high) / 2
        v, _ = calc(mid)
        if v is None:
            return {'status': 'invalid_search_domain', 'growth_shift': None, 'limitation': note}
        if v < target: low = mid
        else: high = mid
    shift = (low + high) / 2
    value, final_rate = calc(shift)
    return {'status': 'solved', 'scenario': scenario, 'growth_shift': shift,
            'implied_growth_path': [g + (0 if c else shift) for g, c in zip(original, cont)] if original else None,
            'final_period_annual_revenue': final_rate, 'reconstructed_value': value, 'limitation': note}
