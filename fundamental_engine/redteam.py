"""Independent accounting / valuation red-team with hard verdicts.

Each check returns PASSED, LIMITED (passed with limitations), FAILED or UNVERIFIABLE.
Any FAILED check with severity 'critical' marks the valuation UNVERIFIED: the number may be
shown for inspection but must not be used for a price conclusion. Nothing here overrides a
result silently; the thesis and this critique are stored side by side.

Built from real errors (docs/GAP_ANALYSIS_2026-10-19.md): cash double counting, missing
converts, stale share counts after financing, ARR treated as revenue, backlog treated as
guaranteed, customer prepayments treated as profit, SBC excluded without dilution, valuation
of capital-heavy growth without linked reinvestment, and unfunded plans.
"""
from datetime import date
from .capital import summarize, market_view, accreted_value
from .finance import number

PASSED, LIMITED, FAILED, UNVERIFIABLE = 'passed', 'passed_with_limitations', 'failed', 'unable_to_verify'


def _check(rows, code, severity, status, message, evidence=None):
    rows.append({'code': code, 'severity': severity, 'status': status, 'message': message, 'evidence': evidence or {}})


def _close(a, b, tol):
    return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)


def audit(package):
    rows = []
    cs = package['capital_structure']
    rep = package.get('reported', {})
    model = package.get('model', {})
    as_of = date.fromisoformat(package['as_of'])
    s = summarize(cs)

    # 1. Cash: spendable cash must reconcile to the balance sheet without double counting
    if 'cash_and_equivalents' in rep:
        expected = number(rep['cash_and_equivalents'], 'cash_and_equivalents', 0) + number(rep.get('short_term_investments', 0), 'sti', 0)
        expected += sum(number(e.get('cash_delta', 0), 'cash_delta') for e in package.get('events_after_statement', []))
        if _close(s['spendable_cash'], expected, 0.02):
            _check(rows, 'cash_reconciles', 'critical', PASSED, 'Spendable cash reconciles to statement + dated events.',
                   {'model': s['spendable_cash'], 'expected': expected})
        else:
            _check(rows, 'cash_reconciles', 'critical', FAILED,
                   'Spendable cash does not reconcile (double count, restricted cash included, or missing event).',
                   {'model': s['spendable_cash'], 'expected': expected})
    else:
        _check(rows, 'cash_reconciles', 'critical', UNVERIFIABLE, 'No reported cash figure supplied for reconciliation.')

    # 2. Debt: every reported debt instrument must be in the structure (converts included)
    if 'debt_principal_outstanding' in rep:
        exp = number(rep['debt_principal_outstanding'], 'debt_principal_outstanding', 0)
        exp += sum(number(e.get('debt_principal_delta', 0), 'debt_principal_delta') for e in package.get('events_after_statement', []))
        got = sum(number(x['principal'], x['id'], 0) for x in cs.get('debt', []) if x['kind'] != 'lease')
        if _close(got, exp, 0.02):
            _check(rows, 'debt_complete', 'critical', PASSED, 'Debt principal reconciles (incl. post-statement issues/exchanges).', {'model': got, 'expected': exp})
        else:
            _check(rows, 'debt_complete', 'critical', FAILED, 'Debt instruments missing or double counted versus filings.', {'model': got, 'expected': exp})
    else:
        _check(rows, 'debt_complete', 'critical', UNVERIFIABLE, 'No reported debt principal supplied.')

    accreting = [x for x in cs.get('debt', []) if x.get('accretion_to', 1.0) > 1.0]
    if accreting:
        _check(rows, 'accretion_modeled', 'major', PASSED if all(accreted_value(x, package['as_of']) > x['principal'] or
               date.fromisoformat(x['issue_date']) >= as_of for x in accreting) else FAILED,
               'Accreting convertible principal is carried at accreted value, not par.')

    # 3. Shares: count must post-date the last share-issuing event
    events = package.get('events_after_statement', [])
    share_events = [e for e in events if e.get('shares_delta')]
    cover = rep.get('shares_count_date')
    if cover:
        cover_d = date.fromisoformat(cover)
        expected_sh = number(rep['shares_count'], 'shares_count', 0) + number(rep.get('prefunded_warrants', 0), 'prefunded_warrants', 0) + sum(number(e['shares_delta'], 'shares_delta') for e in share_events
                                                                            if date.fromisoformat(e['date']) > cover_d)
        if _close(s['basic_economic_shares'], expected_sh, 0.01):
            _check(rows, 'shares_current', 'critical', PASSED, 'Economic share count includes every later issuance/exchange/warrant.',
                   {'model': s['basic_economic_shares'], 'expected': expected_sh})
        else:
            _check(rows, 'shares_current', 'critical', FAILED, 'Share count is stale or omits pre-funded warrants / exchanges.',
                   {'model': s['basic_economic_shares'], 'expected': expected_sh})
        if (as_of - cover_d).days > 120:
            _check(rows, 'shares_age', 'major', LIMITED, 'Share count older than 120 days; dilution since then is unknown.')
    else:
        _check(rows, 'shares_current', 'critical', UNVERIFIABLE, 'No dated share count supplied.')

    # 4. Revenue basis
    basis = model.get('revenue_basis')
    if basis in ('arr', 'run_rate', 'bookings', 'backlog'):
        if model.get('start_revenue_from') == 'recognized_or_guidance':
            _check(rows, 'revenue_basis', 'major', LIMITED, f'{basis} used only as a forward indicator; start revenue is recognized/guided.')
        else:
            _check(rows, 'revenue_basis', 'critical', FAILED, f'{basis} treated as recognized revenue.')
    elif basis == 'recognized':
        _check(rows, 'revenue_basis', 'critical', PASSED, 'Model starts from recognized revenue or dated guidance.')
    else:
        _check(rows, 'revenue_basis', 'critical', UNVERIFIABLE, 'Revenue basis not declared.')

    bt = model.get('backlog_treatment')
    if bt == 'guaranteed':
        _check(rows, 'backlog', 'critical', FAILED, 'Backlog/RPO/optional contract value treated as guaranteed revenue.')
    elif bt in ('probability_weighted', 'not_used', 'firm_only_with_cancellation_terms'):
        _check(rows, 'backlog', 'major', PASSED, f'Backlog treatment: {bt}.')

    # 5. Customer prepayments
    pp = model.get('prepayments_treatment')
    if s['customer_prepayments_operating'] > 0:
        if pp == 'profit' or pp == 'net_cash':
            _check(rows, 'prepayments', 'critical', FAILED, 'Customer prepayments counted as profit or as free net cash.')
        elif pp in ('working_capital_with_reversal', 'liability'):
            _check(rows, 'prepayments', 'major', PASSED, 'Prepayments modeled as working capital that reverses as revenue is recognized.')
        else:
            _check(rows, 'prepayments', 'major', UNVERIFIABLE, 'Material customer prepayments; treatment not declared.')

    # 6. SBC
    mb, sbc = model.get('margin_basis'), model.get('sbc_treatment')
    if mb == 'adjusted_ebitda' and sbc not in ('expensed', 'diluted'):
        _check(rows, 'sbc', 'critical', FAILED, 'Adjusted margin excludes SBC and SBC is neither expensed nor modeled as dilution.')
    elif sbc in ('expensed', 'diluted'):
        _check(rows, 'sbc', 'major', PASSED, f'SBC treated as {sbc}.')
    else:
        _check(rows, 'sbc', 'major', UNVERIFIABLE, 'SBC treatment not declared.')

    # 7. Capital intensity: growth must carry reinvestment; capital-heavy firms cannot be valued on EBITDA alone
    ci = model.get('capex_to_revenue_recent')
    if ci is not None and ci > 0.25:
        if not model.get('capex_linked_to_growth'):
            _check(rows, 'reinvestment', 'critical', FAILED, 'Capital-heavy business valued without linking capex to growth.', {'capex_to_revenue': ci})
        elif model.get('valuation_basis') == 'ev_ebitda_only':
            _check(rows, 'reinvestment', 'critical', FAILED, 'EV/EBITDA-only value ignores depreciation of short-lived assets.', {'capex_to_revenue': ci})
        else:
            _check(rows, 'reinvestment', 'major', PASSED, 'Reinvestment linked to growth; value not EBITDA-only.')

    # 8. Funding
    need = model.get('funding_need')
    if need is not None and need > 0:
        if model.get('financing_modeled'):
            _check(rows, 'funding', 'critical', PASSED, 'Funding need is financed explicitly (debt cost and/or dilution at a stated price).', {'need': need})
        else:
            _check(rows, 'funding', 'critical', FAILED, 'Plan needs external capital that is not modeled.', {'need': need})

    # 9. Terminal dependence and scenario coherence
    tf = model.get('terminal_share')
    if tf is not None:
        _check(rows, 'terminal_share', 'major', LIMITED if tf > 0.8 else PASSED,
               'Terminal value is >80% of value: result depends mostly on maturity assumptions.' if tf > 0.8 else 'Terminal share acceptable.',
               {'terminal_share': tf})
    sc = model.get('scenario_values')
    if sc:
        ok = sc.get('bear', 0) <= sc.get('base', 0) <= sc.get('bull', 0)
        _check(rows, 'scenario_order', 'critical', PASSED if ok else FAILED, 'Scenario values ordered bear <= base <= bull.' if ok else 'Scenario values cross.')

    # 10. Unit-economics coherence for capacity models
    ue = model.get('unit_economics')
    if ue:
        rev, units, rpu = ue['revenue'], ue['active_units'], ue['revenue_per_unit']
        _check(rows, 'unit_coherence', 'major', PASSED if _close(rev, units * rpu, 0.05) else FAILED,
               'Revenue equals active units x revenue per unit.' if _close(rev, units * rpu, 0.05) else 'Revenue inconsistent with capacity x price.',
               {'revenue': rev, 'units_x_price': units * rpu})

    crit_fail = [r for r in rows if r['status'] == FAILED and r['severity'] == 'critical']
    any_fail = [r for r in rows if r['status'] == FAILED]
    unver = [r for r in rows if r['status'] == UNVERIFIABLE and r['severity'] == 'critical']
    if crit_fail:
        verdict = FAILED
    elif any_fail or unver:
        verdict = UNVERIFIABLE if unver and not any_fail else LIMITED
    elif any(r['status'] == LIMITED for r in rows):
        verdict = LIMITED
    else:
        verdict = PASSED
    return {'verdict': verdict, 'valuation_usable': verdict in (PASSED, LIMITED), 'checks': rows,
            'critical_failures': [r['code'] for r in crit_fail],
            'meaning': 'Tests internal accounting relationships against supplied filing figures; it does not prove the filing figures true.'}
