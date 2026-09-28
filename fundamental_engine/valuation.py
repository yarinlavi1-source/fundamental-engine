"""Dated, current-holder valuation paths; pure calculations, never price forecasts.

Operating models project cash, debt and shares, then discount distributions and
terminal equity PER SHARE. This avoids subtracting future funding twice. All
financing prices are external assumptions; there is no circular auto-funding.
"""
from copy import deepcopy
from datetime import date
import json
from .finance import number
from . import __version__

OPERATING = {'software', 'platform', 'industrial', 'consumer', 'infrastructure',
             'commodity', 'nonfinancial', 'turnaround'}
# How the analyst actually obtained a source's content. Only primary_document means
# the filing/release itself was read; vendor normalization and search excerpts can
# be consistent yet still differ from the original definitions or footnotes.
RETRIEVAL = {'primary_document', 'provider_normalized', 'search_excerpt', 'market_feed'}
VALUATION_PRIMARY_KINDS = {'filing', 'earnings_release', 'issuer_release'}
ROUTES = {**{k: 'operating' for k in OPERATING}, 'financials': 'residual_income',
          'reit': 'nav', 'asset_holding': 'nav', 'biotech': 'rnpv', 'conglomerate': 'sotp'}


def text(v, label):
    if not isinstance(v, str) or not v.strip():
        raise ValueError(label + ': nonempty text required')
    return v


def dt(s):
    return date.fromisoformat(s)


def years(a, b):
    return (dt(b) - dt(a)).days / 365.25


def n(row, key, low=None, high=None):
    return number(row[key], key, low, high)


def validate(case):
    if case.get('valuation_version') != 1:
        raise ValueError('valuation_version must be 1')
    # No NaN, infinity, or object-specific numeric tricks in nested inputs.
    json.dumps(case, allow_nan=False)
    cutoff = dt(case['as_of'])
    text(case['company_id'], 'company_id'); text(case['ticker'], 'ticker'); text(case['currency'], 'currency')
    if case['unit'] != 'absolute':
        raise ValueError('Use absolute currency and share units')
    if case.get('prepayment_price_linkage', 'fixed') not in {'fixed', 'scaled'}:
        raise ValueError('prepayment_price_linkage must be fixed or scaled')
    if case['archetype'] not in ROUTES or case['method'] != ROUTES[case['archetype']]:
        raise ValueError('Model/archetype mismatch; use the dedicated economic model')
    sources = {}
    for s in case['sources']:
        sid = text(s['id'], 'source.id')
        if sid in sources: raise ValueError('Duplicate source id')
        for k in ('title', 'url', 'kind'): text(s[k], 'source.' + k)
        if not dt(s['published_at']) <= dt(s['available_at']) <= cutoff:
            raise ValueError('Source unavailable at valuation cutoff')
        if s['kind'] == 'synthetic' and not case.get('is_demo'):
            raise ValueError('Synthetic inputs require is_demo=true')
        if s.get('retrieval') is not None and s['retrieval'] not in RETRIEVAL:
            raise ValueError('source.retrieval must be one of ' + ', '.join(sorted(RETRIEVAL)))
        sources[sid] = s
    ledger = {}
    for a in case['assumptions']:
        aid = text(a['id'], 'assumption.id')
        if aid in ledger: raise ValueError('Duplicate assumption id')
        if a['kind'] not in {'reported', 'guidance', 'analyst'}:
            raise ValueError('Unknown assumption kind')
        for k in ('rationale', 'falsifier'): text(a[k], k)
        if dt(a['as_of']) > cutoff: raise ValueError('Future assumption')
        if a['review_status'] not in {'reviewed', 'unreviewed'}:
            raise ValueError('Invalid review status')
        if not a['source_ids'] or any(s not in sources for s in a['source_ids']):
            raise ValueError('Missing assumption evidence references')
        ledger[aid] = a
    def audit(node):
        if not node.get('assumption_ids') or any(x not in ledger for x in node['assumption_ids']):
            raise ValueError('Missing/unknown assumption_ids')
    audit(case['opening'])
    if dt(case['opening']['as_of']) != cutoff:
        raise ValueError('Opening balance must be bridged to as_of, not stale financial-period end')
    q = case['quote']
    if q['currency'] != case['currency'] or dt(q['as_of']) > cutoff:
        raise ValueError('Quote currency/date mismatch')
    n(q, 'price', 1e-12)
    if not q['source_ids'] or any(s not in sources for s in q['source_ids']):
        raise ValueError('Quote evidence missing')
    dates = case['report_dates']
    if not dates or dates[0] != case['as_of'] or dates != sorted(set(dates)):
        raise ValueError('report_dates must start at as_of and be distinct/ascending')
    if len(dates) > 41 or any(dt(d) < cutoff for d in dates):
        raise ValueError('Invalid report horizon')
    names = set()
    for s in case['scenarios']:
        if s['name'] not in {'bear', 'base', 'bull', 'tail'} or s['name'] in names:
            raise ValueError('Use unique bear/base/bull (and optional tail) scenarios')
        names.add(s['name']); text(s['thesis'], 'thesis'); audit(s)
        n(s, 'cost_of_equity', .000001, 1)
        rows = s['periods']
        if not 1 <= len(rows) <= 120: raise ValueError('Use 1..120 periods')
        prior = case['as_of']
        for row in rows:
            audit(row)
            if row['start'] != prior or not 0 < years(prior, row['end']) <= 1.01:
                raise ValueError('Periods must be contiguous, forward, at most annual; start is exclusive')
            prior = row['end']
        if any(d not in [case['as_of']] + [r['end'] for r in rows] for d in dates):
            raise ValueError('Every report date must be an explicit model boundary')
        if case['method'] in {'operating', 'residual_income'}: audit(s['terminal'])
        if s.get('opening_snapshot'):
            audit(s['opening_snapshot'])
            if s['opening_snapshot']['as_of']!=case['as_of']: raise ValueError('Snapshot opening date mismatch')
    if not {'bear', 'base', 'bull'} <= names: raise ValueError('Require bear, base and bull')
    return ledger


def segment_revenue(seg, duration):
    """Average active units, never year-end capacity masquerading as annual revenue."""
    if seg['driver'] not in {'capacity', 'subscribers', 'units', 'commodity'}:
        raise ValueError('Unknown revenue driver')
    text(seg['name'], 'segment name')
    units = n(seg, 'average_units', 0)
    price = n(seg, 'annual_revenue_per_unit', 0)
    utilization = n(seg, 'utilization', 0, 1)
    return units * price * utilization * duration


def _roll_back(case, s, rows, terminal_per_share):
    """Ex-distribution value at each boundary; dividends are not counted twice."""
    values = {rows[-1]['end']: terminal_per_share}
    v = terminal_per_share
    for row in reversed(rows):
        v = (v + row['dividend_per_share']) / (1 + s['cost_of_equity']) ** years(row['start'], row['end'])
        values[row['start']] = v
    out = []
    for d in case['report_dates']:
        elapsed = years(case['as_of'], d)
        paid = sum(r['dividend_per_share'] for r in rows if r['end'] <= d)
        # Annualized endpoint wealth assumes distributions held as cash, no reinvestment.
        wealth = values[d] + paid
        out.append({'date': d, 'value_per_share': values[d],
                    'gap_vs_current_quote': values[d] / case['quote']['price'] - 1,
                    'discounted_price_component_today': values[d] / (1 + s['cost_of_equity']) ** elapsed,
                    'cumulative_dividend_per_share': paid,
                    'annualized_endpoint_return': (wealth / case['quote']['price']) ** (1 / elapsed) - 1 if elapsed > 0 else None})
    return out


def operating(case, s):
    op = case['opening']
    cash, debt, shares = n(op, 'unrestricted_cash', 0), n(op, 'debt', 0), n(op, 'shares', 1e-12)
    investments = number(op.get('marketable_investments', 0), 'marketable_investments', 0)
    deferred, nol = n(op, 'deferred_revenue', 0), n(op, 'tax_loss_carryforward', 0)
    rows, issues = [], ['Cash is checked at period boundaries only; use monthly periods for construction or near-term refinancing risk.']
    blocked = False
    for p in s['periods']:
        duration = years(p['start'], p['end'])
        detail = [{'name': seg['name'], 'revenue': segment_revenue(seg, duration)} for seg in p['segments']]
        if not detail or len({x['name'] for x in detail}) != len(detail):
            raise ValueError('Require distinct operating segments')
        revenue = sum(x['revenue'] for x in detail)
        variable = sum(x['revenue'] * n(seg, 'cash_cost_ratio', 0, 2) for x, seg in zip(detail, p['segments']))
        ebitda_before_sbc = revenue - variable - n(p, 'fixed_cash_costs', 0)
        sbc = n(p, 'sbc_expense', 0)
        sbc_shares = n(p, 'sbc_shares', 0)
        policy = s['sbc_policy']
        if policy not in {'cash_equivalent', 'explicit_shares'}:
            raise ValueError('Choose SBC cash_equivalent or explicit_shares')
        if policy == 'cash_equivalent' and sbc_shares:
            raise ValueError('Do not expense SBC as cash and dilute for the same future awards')
        if policy == 'explicit_shares' and sbc and not sbc_shares:
            raise ValueError('Explicit SBC expense needs share settlement')
        dep = n(p, 'depreciation', 0)
        ebit = ebitda_before_sbc - sbc - dep
        draws, repay = n(p, 'debt_draw', 0), n(p, 'debt_repayment', 0)
        if repay > debt + draws + 1e-7: raise ValueError('Debt repayment exceeds balance')
        # Debt draw occurs at start, repayment at end. Explicit convention, conservative interest.
        interest = (debt + draws) * n(p, 'interest_rate', 0, 1) * duration
        taxable = ebit - interest
        used = min(nol, max(taxable, 0) * n(p, 'nol_usage_limit', 0, 1))
        taxes = max(taxable - used, 0) * n(p, 'tax_rate', 0, 1)
        nol = nol - used + max(-taxable, 0)
        received = n(p, 'customer_prepayments', 0)
        recognized = n(p, 'revenue_from_prepayments', 0)
        if recognized > min(revenue, deferred + received) + 1e-7:
            raise ValueError('Prepayment recognition exceeds revenue or liability balance')
        deferred += received - recognized
        delta_wc = n(p, 'change_working_capital_excluding_deferred')
        cfo = ebitda_before_sbc - (sbc if policy == 'cash_equivalent' else 0) - interest - taxes - delta_wc + received - recognized
        maintenance, growth = n(p, 'maintenance_capex', 0), n(p, 'growth_capex', 0)
        equity = n(p, 'equity_proceeds', 0)
        issue_price = n(p, 'equity_issue_price', 1e-12)
        fees = equity * n(p, 'equity_fee_rate', 0, .99) + n(p, 'debt_fees', 0)
        issued = equity / issue_price
        shares += issued + sbc_shares
        if p['financing_status'] not in {'committed', 'assumed', 'none'}:
            raise ValueError('Unknown financing_status')
        if p['financing_status'] == 'none' and (draws or equity):
            raise ValueError('Funding marked none but cash financing supplied')
        if p['financing_status'] == 'assumed' and (draws or equity):
            issues.append(p['end'] + ': financing is assumed, not committed')
        cash_before = cash
        debt += draws - repay
        liquidation = number(p.get('investment_liquidation', 0), 'investment_liquidation', 0)
        if liquidation > investments + 1e-7:
            raise ValueError('Investment liquidation exceeds remaining investments')
        investments -= liquidation
        # Net cash investment income and minority distributions need explicit attribution/tax assumptions.
        investment_income = number(p.get('investment_income_after_tax', 0), 'investment_income_after_tax', 0)
        minority_paid = number(p.get('noncontrolling_distributions', 0), 'noncontrolling_distributions', 0)
        cash += cfo - maintenance - growth + draws - repay + equity - fees + liquidation + investment_income - minority_paid
        min_cash = n(p, 'minimum_cash', 0)
        gap = max(0, min_cash - cash)
        if gap > 1e-7: blocked = True
        dividend = max(0, cash - min_cash) * n(p, 'payout_fraction', 0, 1)
        cash -= dividend
        rows.append({'start': p['start'], 'end': p['end'], 'segments': detail,
                     'revenue': revenue, 'ebitda_before_sbc': ebitda_before_sbc, 'ebit': ebit,
                     'interest': interest, 'cash_taxes': taxes, 'nol_remaining': nol,
                     'cfo': cfo, 'maintenance_capex': maintenance, 'growth_capex': growth,
                     'fcfe_before_new_equity': cfo - maintenance - growth + draws - repay - n(p, 'debt_fees', 0) + investment_income - minority_paid,
                     'cash_open': cash_before, 'cash': cash, 'debt': debt, 'shares': shares,
                     'marketable_investments': investments, 'investment_liquidation': liquidation,
                     'investment_income_after_tax': investment_income, 'noncontrolling_distributions': minority_paid,
                     'new_financing_shares': issued, 'sbc_shares': sbc_shares,
                     'original_ownership': op['shares'] / shares, 'deferred_revenue': deferred,
                     'minimum_cash': min_cash, 'funding_gap': gap,
                     'dividends': dividend, 'dividend_per_share': dividend / shares,
                     'financing_status': p['financing_status']})
    t = s['terminal']
    g, wacc, roic = n(t, 'growth', 0, .08), n(t, 'wacc', .000001, 1), n(t, 'roic', .000001, 1)
    if g >= wacc or g > roic: raise ValueError('Terminal growth must be below WACC and no greater than ROIC')
    if t['sbc_in_margin'] is not True: raise ValueError('Terminal margin must include economic SBC cost')
    terminal_rev = rows[-1]['revenue'] / years(rows[-1]['start'], rows[-1]['end']) * (1 + g)
    nopat = terminal_rev * n(t, 'operating_margin', .000001, 1) * (1 - n(t, 'tax_rate', 0, 1))
    terminal_fcff = nopat * (1 - g / roic)
    ev = terminal_fcff / (wacc - g)
    # Any terminal working-capital/deferred runoff must be separately quantified.
    runoff = n(t, 'net_runoff_obligation', 0)
    text(t['working_capital_rationale'], 'terminal working capital rationale')
    excess_cash = max(0, cash - rows[-1]['minimum_cash'])
    raw_equity = ev + excess_cash + investments - debt - n(t, 'other_claims', 0) - runoff
    terminal_price = max(0, raw_equity) / shares
    bridge = {'enterprise_value': ev, 'next_year_revenue': terminal_rev, 'next_year_nopat': nopat,
              'next_year_fcff': terminal_fcff, 'reinvestment_rate': g / roic,
              'excess_cash': excess_cash, 'marketable_investments': investments, 'debt': debt, 'other_claims': t['other_claims'],
              'net_runoff_obligation': runoff, 'raw_equity_value': raw_equity,
              'shares': shares, 'value_per_share': terminal_price}
    if blocked: issues.append('Unfunded cash requirement: going-concern values withheld; supply executable funding or a recovery scenario')
    if not maintenance and case['archetype'] in {'infrastructure', 'industrial', 'commodity'}:
        issues.append('Final explicit maintenance capex is zero; check asset replacement schedule')
    last_margin = rows[-1]['ebit'] / rows[-1]['revenue'] if rows[-1]['revenue'] else 0
    if abs(t['operating_margin'] - last_margin) > .05:
        issues.append('Terminal margin differs by more than 5 percentage points; extend transition forecast')
    if years(rows[-1]['start'], rows[-1]['end']) < .9:
        issues.append('Terminal revenue annualized from a short period; seasonality review required')
    if deferred:
        issues.append('Terminal deferred revenue remains; verify renewal versus runoff treatment')
    timeline = [] if blocked else _roll_back(case, s, rows, terminal_price)
    today = timeline[0]['value_per_share'] if timeline else 0
    terminal_pv = terminal_price / (1 + s['cost_of_equity']) ** years(case['as_of'], rows[-1]['end'])
    if today > 0 and terminal_pv / today > .8:
        issues.append('More than 80% of present value comes from terminal equity; prioritize terminal assumptions and extend explicit forecast if needed')
    return {'name': s['name'], 'status': 'funding_blocked' if blocked else 'conditional',
            'issues': issues, 'timeline': timeline, 'forecast': rows, 'terminal': bridge,
            'terminal_share_of_present_value': terminal_pv / today if today > 0 else None}


def residual_income(case, s):
    """Clean-surplus equity model; deposits are NOT subtracted as corporate debt.

    Constant shares; recapitalizations/buybacks require an explicit separate model.
    Regulatory book must be reconciled to eligible capital by the analyst.
    """
    book = n(case['opening'], 'book_equity', .000001)
    shares = n(case['opening'], 'shares', 1e-12)
    rows, issues = [], []
    blocked = False
    ke = s['cost_of_equity']
    for p in s['periods']:
        dur = years(p['start'], p['end'])
        profit = book * n(p, 'roe', -2, 2) * dur
        dividend = max(0, profit) * n(p, 'payout_fraction', 0, 1)
        close = book + profit - dividend
        minimum = n(p, 'risk_weighted_assets', 0) * n(p, 'required_capital_ratio', 0, 1)
        if close < minimum or close <= 0: blocked = True
        residual = profit - book * ((1 + ke) ** dur - 1)
        rows.append({'start': p['start'], 'end': p['end'], 'book_open': book,
                     'book_equity': close, 'net_income': profit, 'residual_income': residual,
                     'required_capital': minimum, 'capital_shortfall': max(0, minimum - close),
                     'shares': shares, 'dividend_per_share': dividend / shares})
        book = close
    t = s['terminal']
    g, roe = n(t, 'growth', 0, .08), n(t, 'roe', .000001, 2)
    if g >= ke or g > roe: raise ValueError('Terminal growth must be below Ke and no greater than ROE')
    # B + (ROE-Ke)*B/(Ke-g) = B*(ROE-g)/(Ke-g)
    equity = book * (roe - g) / (ke - g)
    if blocked: issues.append('Regulatory capital shortfall or nonpositive book: recapitalization not modeled')
    return {'name': s['name'], 'status': 'funding_blocked' if blocked else 'conditional',
            'issues': issues, 'forecast': rows, 'timeline': [] if blocked else _roll_back(case, s, rows, max(0, equity) / shares),
            'terminal': {'equity_value': equity, 'book_equity': book, 'implied_price_to_book': (roe - g) / (ke - g)}}


def nav(case, s):
    """Asset snapshots at explicit dates; no automatic financing of asset growth."""
    rows = []
    for p in s['periods']:
        assets = 0
        for a in p['properties']:
            assets += n(a, 'forward_noi', 0) / n(a, 'cap_rate', .000001, 1) * n(a, 'ownership', 0, 1)
        assets += n(p, 'other_asset_value', 0)
        gross = assets + n(p, 'unrestricted_cash', 0) - n(p, 'debt', 0) - n(p, 'other_claims', 0) - n(p, 'selling_costs_and_taxes', 0)
        equity = max(0, gross)
        shares = n(p, 'shares', 1e-12)
        rows.append({'date': p['end'], 'value_per_share': equity / shares,
                     'equity_value': equity, 'asset_value': assets,
                     'gap_vs_current_quote': equity / shares / case['quote']['price'] - 1})
    # Present NAV is supplied separately; future snapshots are conditional, not rolled back.
    p = s.get('opening_snapshot', case['opening'])
    equity = max(0, n(p, 'asset_value', 0) + n(p, 'unrestricted_cash', 0) - n(p, 'debt', 0) - n(p, 'other_claims', 0))
    price = equity / n(p, 'shares', 1e-12)
    timeline = [{'date': case['as_of'], 'value_per_share': price, 'gap_vs_current_quote': price / case['quote']['price'] - 1}] + rows
    return {'name': s['name'], 'status': 'conditional', 'forecast': rows,
            'timeline': [r for r in timeline if r['date'] in case['report_dates']],
            'issues': ['Future NAV inputs must reconcile acquisitions, disposals, development spending, financing and share count; snapshots do not certify funding.'], 'terminal': None}


def specialist(case, s):
    """Explicit rNPV / SOTP dated snapshots, no invented probabilities or multiples."""
    snapshots = [s.get('opening_snapshot', case['opening'])] + s['periods']
    out = []
    for p in snapshots:
        d = p.get('end', case['as_of'])
        total, seen = 0, set()
        for item in p['components']:
            ident = text(item['id'], 'component.id')
            if ident in seen: raise ValueError('Duplicate component; potential double count')
            seen.add(ident)
            if case['method'] == 'rnpv':
                val = 0
                for cf in item['cashflows']:
                    if dt(cf['date']) <= dt(d): raise ValueError('rNPV cashflows must follow snapshot date')
                    val += n(cf, 'cash_flow') * n(cf, 'probability', 0, 1) / (1 + s['cost_of_equity']) ** years(d, cf['date'])
                total += val
            else:
                val = n(item, 'value')
                if item['basis'] == 'enterprise':
                    val += n(item, 'cash', 0) - n(item, 'debt', 0) - n(item, 'other_claims', 0)
                elif item['basis'] != 'equity': raise ValueError('SOTP basis must be enterprise or equity')
                total += val * n(item, 'ownership', 0, 1)
        equity = max(0, total + n(p, 'unrestricted_cash', 0) - n(p, 'holding_debt', 0) - n(p, 'other_claims', 0) - n(p, 'corporate_costs_pv', 0))
        v = equity / n(p, 'shares', 1e-12)
        out.append({'date': d, 'value_per_share': v, 'component_value': total,
                    'gap_vs_current_quote': v / case['quote']['price'] - 1})
    return {'name': s['name'], 'status': 'conditional', 'forecast': out,
            'timeline': [r for r in out if r['date'] in case['report_dates']], 'terminal': None,
            'issues': ['Snapshot model: financing and component-level forecasts must be separately substantiated; do not interpret scenario probabilities as clinical or investment success rates.']}


def value_company(case):
    case = deepcopy(case)
    ledger = validate(case)
    fn = {'operating': operating, 'residual_income': residual_income, 'nav': nav,
          'rnpv': specialist, 'sotp': specialist}[case['method']]
    results = [fn(case, s) for s in case['scenarios']]
    warnings = []
    unreviewed = [k for k, a in ledger.items() if a['review_status'] != 'reviewed']
    if unreviewed: warnings.append('Unreviewed assumptions: ' + ', '.join(unreviewed))
    if (dt(case['as_of']) - dt(case['quote']['as_of'])).days > 7: warnings.append('Quote is more than 7 calendar days old')
    table = []
    for d in case['report_dates']:
        row = {'date': d, 'current_quote': case['quote']['price']}
        for result in results:
            found = next((r for r in result['timeline'] if r['date'] == d), None)
            row[result['name']] = found['value_per_share'] if found else None
            if result['name'] == 'base':
                row['base_gap_vs_quote'] = found['gap_vs_current_quote'] if found else None
        if all(row.get(k) is not None for k in ('bear', 'base', 'bull')) and not row['bear'] <= row['base'] <= row['bull']:
            warnings.append(d + ': scenario values cross; labels were not silently sorted')
        if row.get('tail') is not None and row.get('bull') is not None and row['tail'] < row['bull']:
            warnings.append(d + ': tail value below bull; a tail scenario must be the larger-outcome case')
        table.append(row)
    from hashlib import sha256
    fingerprint = sha256(json.dumps(case, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    output = {'input_sha256': fingerprint, 'version': __version__, 'ticker': case['ticker'], 'currency': case['currency'],
            'as_of': case['as_of'], 'company_id': case['company_id'], 'method': case['method'], 'is_demo': bool(case.get('is_demo')),
            'status': 'funding_blocked' if any(r['status'] == 'funding_blocked' for r in results) else ('unreviewed' if unreviewed else 'conditional_valuation'),
            'annual_values': table, 'scenarios': results, 'warnings': warnings,
            'assumptions': case['assumptions'], 'sources': case['sources'],
            'meaning': 'Values at each stated date, conditional on the scenario; not predicted market prices. Current price is a fixed comparison, not a forecast. No accuracy or investment win-rate claim.'}
    from .valuation_audit import audit_valuation
    output['underwriting_audit'] = audit_valuation(case, results)
    output['calculation_status'] = output['status']
    if not output['is_demo'] and output['status'] == 'conditional_valuation' and not output['underwriting_audit']['eligible_for_research_synthesis']:
        output['status'] = 'underwriting_required'
    output['reproduction_input'] = case
    output['annual_path_semantics'] = ('Conditional ex-distribution values rolled back from terminal equity; without dividends the path compounds at the assumed cost of equity. These are not independent annual market-price forecasts.'
                                     if case['method'] in {'operating', 'residual_income'} else 'Independently supplied conditional snapshots, not market-price forecasts.')
    json.dumps(output, allow_nan=False)
    return output


def sensitivity(case, scenario='base', rate_shifts=(-.02, 0, .02), growth_shifts=(-.01, 0, .01)):
    """Recalculate entire funding/ownership path; invalid terminals stay explicit."""
    if case['method'] not in {'operating', 'residual_income'}: raise ValueError('Sensitivity requires a cash-flow model')
    if len(rate_shifts) * len(growth_shifts) > 81: raise ValueError('Sensitivity grid too large')
    cells = []
    for dr in rate_shifts:
        number(dr, 'rate shift', -.5, .5)
        for dg in growth_shifts:
            number(dg, 'growth shift', -.1, .1)
            c = deepcopy(case)
            s = next((x for x in c['scenarios'] if x['name'] == scenario), None)
            if s is None: raise ValueError('Unknown scenario')
            s['cost_of_equity'] += dr
            if c['method'] == 'operating': s['terminal']['wacc'] += dr
            s['terminal']['growth'] += dg
            try:
                r = value_company(c)
                v = r['annual_values'][0][scenario]
                cells.append({'rate_shift': dr, 'growth_shift': dg, 'value_today': v, 'status': 'funding_blocked' if v is None else 'calculated'})
            except ValueError as e:
                cells.append({'rate_shift': dr, 'growth_shift': dg, 'value_today': None, 'status': str(e)})
    return cells
