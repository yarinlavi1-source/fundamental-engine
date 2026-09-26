"""Finite annual cash/ownership model with conditional financing and terminal sale.

All new financing occurs at start of year; dividends at year end. Within-year
liquidity is not inferred. End-of-horizon business sale uses a supplied terminal
capitalization rate. Its equity proceeds, plus dividends, are discounted at Ke.
Future SBC is an economic cash-equivalent expense in margins (no second dilution).
"""
from copy import deepcopy
from .finance import number


def owner_value(model, allow_hypothetical=True):
    revenue = number(model['base_revenue'], 'base_revenue', 0)
    cash = number(model['cash'], 'cash', 0)
    debt = number(model['debt'], 'debt', 0)
    shares = original_shares = number(model['shares'], 'shares', 1e-12)
    minimum_cash = number(model['minimum_cash'], 'minimum_cash', 0)
    ke = number(model['cost_equity'], 'cost_equity', .000001, 1)
    cap_rate = number(model['terminal_capitalization_rate'], 'terminal_capitalization_rate', .000001, 1)
    g = number(model['terminal_growth'], 'terminal_growth', 0, .1)
    roic = number(model['terminal_roic'], 'terminal_roic', .000001, 1)
    claims = number(model['other_claims'], 'other_claims', 0)
    if g >= cap_rate or g > roic:
        raise ValueError('Invalid terminal growth/capitalization/reinvestment relationship')
    if model['sbc_policy'] != 'cash_equivalent_expensed_no_extra_dilution':
        raise ValueError('Owner model requires explicit cash-equivalent SBC policy')
    if not 1 <= len(model['years']) <= 30:
        raise ValueError('Use 1..30 annual periods')
    rows, pv_dividends = [], 0
    hypothetical_used = False
    for i, year in enumerate(model['years'], 1):
        start_cash, start_debt, start_shares = cash, debt, shares
        financing = []
        for f in year.get('financing', []):
            if f['kind'] not in {'equity', 'debt'} or f['commitment'] not in {'committed', 'hypothetical'}:
                raise ValueError('Financing kind/commitment invalid')
            amount = number(f['gross_amount'], 'gross financing', 0)
            fee = number(f['fee_rate'], 'fee_rate', 0, .99)
            price = number(f['issue_price'], 'issue_price', 1e-12) if f['kind'] == 'equity' else None
            if f['commitment'] == 'hypothetical' and not allow_hypothetical:
                continue
            hypothetical_used |= f['commitment'] == 'hypothetical'
            cash += amount*(1-fee)
            if f['kind'] == 'equity':
                shares += amount/price
            else:
                debt += amount
            financing.append({**f, 'net_cash': amount*(1-fee)})
        growth = number(year['growth'], 'growth', -.99, 5)
        margin = number(year['operating_margin'], 'operating_margin', -5, 1)
        tax_rate = number(year['cash_tax_rate'], 'cash_tax_rate', 0, 1)
        capital_efficiency = number(year['sales_to_capital'], 'sales_to_capital', .000001)
        base_reinvestment = number(year['base_net_reinvestment'], 'base_net_reinvestment', 0)
        interest_rate = number(year['debt_interest_rate'], 'debt_interest_rate', 0, 1)
        repayment = number(year['debt_repayment'], 'debt_repayment', 0)
        dividend = number(year['dividend'], 'dividend', 0)
        if repayment > debt:
            raise ValueError('Debt repayment exceeds outstanding debt')
        new_revenue = revenue*(1+growth)
        reinvestment = max(new_revenue-revenue,0)/capital_efficiency + base_reinvestment
        ebit = new_revenue*margin
        interest = debt*interest_rate
        tax = max(ebit-interest,0)*tax_rate
        cash_to_equity_before_financing = ebit-interest-tax-reinvestment-repayment
        end_cash = cash+cash_to_equity_before_financing-dividend
        row = {'year': i, 'revenue': new_revenue, 'ebit': ebit, 'interest': interest, 'tax': tax,
               'net_reinvestment': reinvestment, 'cash_to_equity_before_financing': cash_to_equity_before_financing,
               'opening_cash': start_cash, 'cash_after_start_financing': cash, 'closing_cash': end_cash,
               'opening_debt': start_debt, 'closing_debt': debt-repayment, 'opening_shares': start_shares,
               'closing_shares': shares, 'original_ownership': original_shares/shares,
               'dividend_per_share': dividend/shares, 'financing': financing}
        rows.append(row)
        if cash < minimum_cash or end_cash < minimum_cash:
            return {'status': 'financing_gap', 'gap_year': i,
                    'required_cash': max(minimum_cash-cash, minimum_cash-end_cash),
                    'forecast': rows, 'value_per_initial_share': None,
                    'failure_value': None, 'note': 'Plan infeasible; liquidation/recovery not modeled. A funding gap is not automatically a zero valuation.'}
        pv_dividends += dividend/shares/(1+ke)**i
        revenue, cash, debt = new_revenue, end_cash, debt-repayment
    nopat = ebit-max(ebit,0)*tax_rate
    if nopat <= 0:
        return {'status':'terminal_unproven','forecast':rows,'value_per_initial_share':None,
                'note':'Extend the forecast or supply another terminal model; losses do not imply zero potential.'}
    terminal_fcff = nopat*(1+g)*(1-g/roic)
    terminal_operating_value = terminal_fcff/(cap_rate-g)
    terminal_equity = terminal_operating_value + cash-minimum_cash-debt-claims
    terminal_price = max(0,terminal_equity)/shares
    return {'status':'funded', 'conditional_on_hypothetical_financing':hypothetical_used,
            'value_per_initial_share':pv_dividends+terminal_price/(1+ke)**len(rows),
            'pv_dividends_per_initial_share':pv_dividends, 'terminal_equity':terminal_equity,
            'terminal_operating_value':terminal_operating_value, 'terminal_cash_excess':cash-minimum_cash,
            'terminal_debt':debt, 'terminal_share_count':shares, 'original_ownership':original_shares/shares,
            'forecast':rows, 'assumptions':{'cost_equity':ke,'terminal_capitalization_rate':cap_rate,
            'terminal_growth':g,'terminal_roic':roic},
            'note':'Conditional scenario, not fair issuance pricing. Financing at year start; annual liquidity checks only. Retained cash earns zero.'}


def financing_scenarios(model):
    result = owner_value(model)
    result['without_hypothetical_financing'] = owner_value(model, allow_hypothetical=False)
    sensitivity = []
    for factor in (.5, 1, 1.5):
        variant = deepcopy(model)
        for y in variant['years']:
            for f in y.get('financing', []):
                if f['kind'] == 'equity' and f['commitment'] == 'hypothetical':
                    f['issue_price'] *= factor
        r = owner_value(variant)
        sensitivity.append({'issue_price_factor':factor, 'status':r['status'],
                            'value_per_initial_share':r['value_per_initial_share']})
    result['issue_price_sensitivity'] = sensitivity
    return result
