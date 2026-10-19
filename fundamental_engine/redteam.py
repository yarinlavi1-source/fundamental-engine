"""Independent accounting / valuation red-team with hard verdicts.

Each check returns PASSED, LIMITED (passed with limitations), FAILED or UNVERIFIABLE.
Verdict precedence (most severe wins):
  1. any critical FAILED            -> failed            (not usable)
  2. any critical UNVERIFIABLE      -> unable_to_verify  (not usable; a major failure never
                                       upgrades an unverifiable critical check to usable)
  3. any major FAILED, any non-critical UNVERIFIABLE, or any LIMITED
                                    -> passed_with_limitations (usable, limitations listed)
  4. otherwise                      -> passed
A major failure is a known, bounded modelling defect (e.g. accretion at par, incoherent unit
economics) that is reported beside the number; it is not silently dropped. A model-level
check whose declaration is absent becomes an UNVERIFIABLE row, so a package that declares
nothing cannot pass. Nothing here overrides a result silently; the thesis and this critique
are stored side by side.

Built from real errors (docs/GAP_ANALYSIS_2026-10-19.md): cash double counting, missing
converts, stale share counts after financing, ARR treated as revenue, backlog treated as
guaranteed, customer prepayments treated as profit, SBC excluded without dilution, valuation
of capital-heavy growth without linked reinvestment, and unfunded plans.
"""
from datetime import date
from .capital import summarize, market_view, accreted_value
from .finance import number

PASSED, LIMITED, FAILED, UNVERIFIABLE = 'passed', 'passed_with_limitations', 'failed', 'unable_to_verify'
# Reported balances are exact filing figures; the tolerance only absorbs rounding. Debt keeps
# 2% in aggregate (principal vs carrying-amount ambiguity) and is checked per instrument at 1%.
CASH_TOL, DEBT_TOL, INSTRUMENT_TOL, SHARE_TOL = .005, .02, .01, .005


def _check(rows, code, severity, status, message, evidence=None):
    rows.append({'code': code, 'severity': severity, 'status': status, 'message': message, 'evidence': evidence or {}})


def _close(a, b, tol):
    return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)


def _events(package, rows, as_of):
    """Post-statement events inside (statement date, as_of]; others are flagged and ignored.

    An event after as_of is look-ahead; one on/before the statement date is already in the
    reported balance and would be counted twice."""
    stmt = package.get('reported', {}).get('statement_as_of')
    stmt_d = date.fromisoformat(stmt) if stmt else None
    kept, ignored = [], []
    for e in package.get('events_after_statement', []):
        d = date.fromisoformat(e['date']) if e.get('date') else None
        why = ('undated' if d is None else 'after_as_of_lookahead' if d > as_of
               else 'on_or_before_statement_date' if stmt_d and d <= stmt_d else None)
        if why:
            ignored.append({'date': e.get('date'), 'type': e.get('type'), 'source_id': e.get('source_id'), 'reason': why})
        else:
            kept.append(e)
    if ignored:
        _check(rows, 'events_window', 'major', LIMITED, 'Events outside (statement date, as_of] were ignored in reconciliation.',
               {'statement_as_of': stmt, 'as_of': as_of.isoformat(), 'ignored': ignored})
    elif kept and not stmt_d:
        _check(rows, 'events_window', 'major', LIMITED,
               'reported.statement_as_of not supplied: events cannot be checked against double counting with the statement.',
               {'events_used': len(kept)})
    return kept


def _instruments(rows, rep, cs, events):
    """Every reported debt/convertible instrument must be in the structure (per id, 1%)."""
    listed = {}
    for x in rep.get('debt_instruments', []) + rep.get('convertibles', []):
        p = number(x['principal'], str(x.get('id')) + '.principal', 0)
        if x['id'] in listed and not _close(listed[x['id']], p, 1e-9):
            raise ValueError(f"reported instrument {x['id']} listed twice with different principal")
        listed[x['id']] = p
    if not listed:
        return
    for e in events:            # an exchange/repayment after the statement may name its instrument
        if e.get('instrument_id') in listed:
            listed[e['instrument_id']] += number(e.get('debt_principal_delta', 0), 'debt_principal_delta')
    model = {x['id']: number(x['principal'], x['id'], 0) for x in cs.get('debt', []) if x['kind'] != 'lease'}
    missing = [i for i, p in listed.items() if i not in model and p > 0]
    mismatched = [{'id': i, 'expected': p, 'model': model[i]} for i, p in listed.items()
                  if i in model and not _close(model[i], p, INSTRUMENT_TOL)]
    ok = not missing and not mismatched
    _check(rows, 'debt_instruments', 'critical', PASSED if ok else FAILED,
           'Every reported debt instrument is in the structure at its principal.' if ok else
           'Reported debt instrument missing from the structure or carried at a different principal.',
           {'missing': missing, 'mismatched': mismatched, 'reported_count': len(listed)})


def audit(package):
    rows = []
    cs = package['capital_structure']
    rep = package.get('reported', {})
    model = package.get('model', {})
    as_of = date.fromisoformat(package['as_of'])
    s = summarize(cs)
    events = _events(package, rows, as_of)

    # 1. Cash: spendable cash must reconcile to the balance sheet without double counting
    if 'cash_and_equivalents' in rep:
        expected = number(rep['cash_and_equivalents'], 'cash_and_equivalents', 0) + number(rep.get('short_term_investments', 0), 'sti', 0)
        expected += sum(number(e.get('cash_delta', 0), 'cash_delta') for e in events)
        if _close(s['spendable_cash'], expected, CASH_TOL):
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
        exp += sum(number(e.get('debt_principal_delta', 0), 'debt_principal_delta') for e in events)
        got = sum(number(x['principal'], x['id'], 0) for x in cs.get('debt', []) if x['kind'] != 'lease')
        if _close(got, exp, DEBT_TOL):
            _check(rows, 'debt_complete', 'critical', PASSED, 'Debt principal reconciles (incl. post-statement issues/exchanges).', {'model': got, 'expected': exp})
        else:
            _check(rows, 'debt_complete', 'critical', FAILED, 'Debt instruments missing or double counted versus filings.', {'model': got, 'expected': exp})
    else:
        _check(rows, 'debt_complete', 'critical', UNVERIFIABLE, 'No reported debt principal supplied.')
    _instruments(rows, rep, cs, events)

    accreting = [x for x in cs.get('debt', []) if x.get('accretion_to', 1.0) > 1.0]
    if accreting:
        _check(rows, 'accretion_modeled', 'major', PASSED if all(accreted_value(x, package['as_of']) > x['principal'] or
               date.fromisoformat(x['issue_date']) >= as_of for x in accreting) else FAILED,
               'Accreting convertible principal is carried at accreted value, not par.')

    # 3. Shares: count must post-date the last share-issuing event
    share_events = [e for e in events if e.get('shares_delta')]
    cover = rep.get('shares_count_date')
    if cover:
        cover_d = date.fromisoformat(cover)
        expected_sh = number(rep['shares_count'], 'shares_count', 0) + number(rep.get('prefunded_warrants', 0), 'prefunded_warrants', 0) + sum(number(e['shares_delta'], 'shares_delta') for e in share_events
                                                                            if date.fromisoformat(e['date']) > cover_d)
        if _close(s['basic_economic_shares'], expected_sh, SHARE_TOL):
            _check(rows, 'shares_current', 'critical', PASSED, 'Economic share count includes every later issuance/exchange/warrant.',
                   {'model': s['basic_economic_shares'], 'expected': expected_sh})
        else:
            _check(rows, 'shares_current', 'critical', FAILED, 'Share count is stale, inflated, or omits pre-funded warrants / exchanges.',
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
    else:
        _check(rows, 'backlog', 'major', UNVERIFIABLE, "Backlog treatment not declared (use 'not_used' when the model has no backlog).")

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
    if ci is None:
        _check(rows, 'reinvestment', 'critical', UNVERIFIABLE, 'Recent capex/revenue not declared; reinvestment linkage cannot be checked.')
    elif number(ci, 'capex_to_revenue_recent', 0) > 0.25:
        if not model.get('capex_linked_to_growth'):
            _check(rows, 'reinvestment', 'critical', FAILED, 'Capital-heavy business valued without linking capex to growth.', {'capex_to_revenue': ci})
        elif model.get('valuation_basis') == 'ev_ebitda_only':
            _check(rows, 'reinvestment', 'critical', FAILED, 'EV/EBITDA-only value ignores depreciation of short-lived assets.', {'capex_to_revenue': ci})
        else:
            _check(rows, 'reinvestment', 'major', PASSED, 'Reinvestment linked to growth; value not EBITDA-only.')
    else:
        _check(rows, 'reinvestment', 'major', PASSED, 'Declared capital-light (capex/revenue <= 25%).', {'capex_to_revenue': ci})

    # 8. Funding
    need = model.get('funding_need')
    if need is None:
        _check(rows, 'funding', 'critical', UNVERIFIABLE, 'External funding need not declared (declare 0 when the plan is self-funded).')
    elif number(need, 'funding_need') > 0:
        if model.get('financing_modeled'):
            _check(rows, 'funding', 'critical', PASSED, 'Funding need is financed explicitly (debt cost and/or dilution at a stated price).', {'need': need})
        else:
            _check(rows, 'funding', 'critical', FAILED, 'Plan needs external capital that is not modeled.', {'need': need})
    else:
        _check(rows, 'funding', 'critical', PASSED, 'Declared no external funding need.', {'need': need})

    # 9. Terminal dependence and scenario coherence
    tf = model.get('terminal_share')
    if tf is not None:
        _check(rows, 'terminal_share', 'major', LIMITED if tf > 0.8 else PASSED,
               'Terminal value is >80% of value: result depends mostly on maturity assumptions.' if tf > 0.8 else 'Terminal share acceptable.',
               {'terminal_share': tf})
    else:
        _check(rows, 'terminal_share', 'major', UNVERIFIABLE, 'Terminal share of value not declared.')
    sc = model.get('scenario_values')
    if sc:
        ok = sc.get('bear', 0) <= sc.get('base', 0) <= sc.get('bull', 0)
        _check(rows, 'scenario_order', 'critical', PASSED if ok else FAILED, 'Scenario values ordered bear <= base <= bull.' if ok else 'Scenario values cross.')
    else:
        _check(rows, 'scenario_order', 'major', UNVERIFIABLE, 'Scenario values not supplied; bear/base/bull coherence unchecked.')

    # 10. Unit-economics coherence for capacity models (only when the model declares them)
    ue = model.get('unit_economics')
    if ue:
        rev, units, rpu = ue['revenue'], ue['active_units'], ue['revenue_per_unit']
        _check(rows, 'unit_coherence', 'major', PASSED if _close(rev, units * rpu, 0.05) else FAILED,
               'Revenue equals active units x revenue per unit.' if _close(rev, units * rpu, 0.05) else 'Revenue inconsistent with capacity x price.',
               {'revenue': rev, 'units_x_price': units * rpu})

    crit_fail = [r for r in rows if r['status'] == FAILED and r['severity'] == 'critical']
    crit_unver = [r for r in rows if r['status'] == UNVERIFIABLE and r['severity'] == 'critical']
    other_fail = [r for r in rows if r['status'] == FAILED and r['severity'] != 'critical']
    other_unver = [r for r in rows if r['status'] == UNVERIFIABLE and r['severity'] != 'critical']
    if crit_fail:
        verdict = FAILED
    elif crit_unver:
        verdict = UNVERIFIABLE
    elif other_fail or other_unver or any(r['status'] == LIMITED for r in rows):
        verdict = LIMITED
    else:
        verdict = PASSED
    return {'verdict': verdict, 'valuation_usable': verdict in (PASSED, LIMITED), 'checks': rows,
            'critical_failures': [r['code'] for r in crit_fail],
            'major_failures': [r['code'] for r in other_fail],
            'unverified': [r['code'] for r in crit_unver + other_unver],
            'limitations': [r['code'] for r in rows if r['status'] in (LIMITED, UNVERIFIABLE) or (r['status'] == FAILED and r['severity'] != 'critical')],
            'meaning': 'Tests internal accounting relationships against supplied filing figures; it does not prove the filing figures true.'}
