"""Ratio-driven operating specs expanded into an auditable valuation_version=1 case.

Recurring-revenue businesses are usually underwritten as growth rates and costs in
percent of revenue. Hand-converting those ratios into absolute period inputs is
error-prone (the ServiceNow run of 2026-09-28 needed a 200-line script). This module
does that conversion deterministically and records the spec hash in the case.

It adds NO economic judgement: every ratio, rate and balance is still an analyst
input that needs sources, rationale and falsifiers in the ordinary assumption ledger.
The generated case is then executed and audited by value_company exactly as before.
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
    'maintenance_capex_pct': (0, 0, 1),
    'growth_capex_pct': (0, 0, 1),
    'tax_rate': (None, 0, 1),
    'interest_rate': (0, 0, 1),
    'deferred_revenue_ratio': (0, 0, 5),
    'working_capital_ratio': (0, -2, 2),
    'prepaid_share': (.8, 0, 1),
    'nol_usage_limit': (0, 0, 1),
    'payout_fraction': (0, 0, 1),
}
ABSOLUTE_KEYS = ('amortization', 'minimum_cash', 'investment_income_after_tax', 'debt_draw', 'debt_repayment', 'fixed_cash_costs')


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
    for sc in spec['scenarios']:
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
                'equity_proceeds': 0, 'equity_issue_price': case['quote']['price'], 'equity_fee_rate': 0,
                'financing_status': sc.get('financing_status', 'committed' if debt_draw else 'none'),
            })
            rate_prev = rate
        if sc.get('sbc_policy', 'cash_equivalent') != 'cash_equivalent':
            raise ValueError('Driver specs charge SBC as a cash-equivalent cost; model explicit share issuance in a full case')
        case['scenarios'].append({'name': sc['name'], 'thesis': sc['thesis'], 'assumption_ids': sc['assumption_ids'],
                                  'cost_of_equity': sc['cost_of_equity'], 'sbc_policy': 'cash_equivalent',
                                  'periods': periods, 'terminal': deepcopy(sc['terminal'])})
    case['prepayment_price_linkage'] = 'scaled'
    case['generated_from'] = {'tool': 'drivers.build_case', 'driver_version': 1,
                              'spec_sha256': sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    return case


def implied_growth_shift(spec, scenario='base', low=-.15, high=.30):
    """Uniform growth shift (every non-stub period) at which the scenario value equals the quote.

    Margins, SBC, capex ratios, financing and terminal assumptions stay fixed. The
    answer is a sensitivity of THIS spec, not the market's expectation, and must be
    re-underwritten for demand, capacity and competition before it is used.
    """
    base = next((s for s in spec['scenarios'] if s['name'] == scenario), None)
    if base is None:
        raise ValueError('Unknown scenario')
    original = base['growth'] if isinstance(base['growth'], list) else None
    target = spec['quote']['price']

    def calc(shift):
        s2 = deepcopy(spec)
        sc = next(s for s in s2['scenarios'] if s['name'] == scenario)
        g = sc['growth']
        sc['growth'] = [x + shift for x in g] if isinstance(g, list) else g + shift
        try:
            r = value_company(build_case(s2))
        except ValueError:
            return None, None
        final = next(x for x in r['scenarios'] if x['name'] == scenario)['forecast'][-1]
        return r['annual_values'][0][scenario], final['revenue'] / years(final['start'], final['end'])

    note = 'Uniform growth shift with all ratios fixed; a sensitivity of this spec, not market consensus. Re-underwrite feasibility.'
    lo_v, _ = calc(low)
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
            'implied_growth_path': [g + shift for g in original] if original else None,
            'final_period_annual_revenue': final_rate, 'reconstructed_value': value, 'limitation': note}
