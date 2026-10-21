"""Residual-income forecast for balance-sheet businesses (banks, insurers, broker-dealers, managed care routed by SIC).

Deposits, float and policy reserves are operating funding, not corporate debt, so a free-cash-flow model with an EV bridge
misprices these businesses. Value = common book equity + the present value of future residual income (profit above the
cost of equity on book), executed by valuation.residual_income; book grows by retained profit (clean surplus).

Evidence, all from the FactBook at as_of: common equity (stockholders' equity less preferred stock) at each balance sheet,
profit to common holders (NetIncomeLossAvailableToCommonStockholdersBasic, else net income less preferred dividends), the
distribution history (dividends to common + buybacks) and the point-in-time share count. Return on equity (ROE) = profit
to common / average common equity, per fiscal year and trailing twelve months.

Rules (documented assumptions, never tuned to a price): base ROE moves from today's to the company's own median of up to
ten years by year 5 and on to a terminal ROE that keeps half of the median's excess over the cost of equity (excess returns
are competed down; a business below its cost of equity is not assumed to recover); the bear starts at the company's own
lower-quartile ROE (a credit or underwriting downturn) and ends at the lower of its median and the cost of equity less one
point; the bull holds the better of today's and the median and keeps three quarters of the excess. Distributions follow the
three-year history (buybacks treated like dividends at a constant share count, valuation.residual_income's convention).
Terminal growth is the lower of the engine's cap, the risk-free rate and the sustainable rate ROE x retention.

Not modelled, stated as open items: regulatory capital (risk-weighted assets and CET1 are not in companyfacts), credit
quality, reserve adequacy and interest-rate sensitivity; an analyst supplies them with evidence and a reviewer signs.
"""
import json
from datetime import timedelta
from statistics import median

from . import facts

BANK_KE_ADDON = .005      # balance-sheet leverage and the credit / underwriting cycle (a documented add-on, not a measured beta)
EXCESS_KEPT = {'base': .5, 'bull': .75}
# persistence proxy (the operating moat proxy's counterpart): ROE above the cost of equity in at least 80% of seven or more
# fiscal years keeps more of the excess (base .75, bull .9); a franchise that has earned it through a cycle is not assumed
# to lose half of it
PERSISTENT = {'min_years': 7, 'share_above': .8, 'kept': {'base': .75, 'bull': .9}}
BOOK_GROWTH_CAP = .08     # retained profit beyond what book growth of at most this needs is paid out (no 30%-a-year compounding)
BOOK_GROWTH_FLOOR = .03   # the five-year book growth used is at least this (the former terminal cap's number, kept as its own
                          # constant: terminal growth now follows the risk-free rate, forecast.TERMINAL_BEAR_GAP comment)
ROE_FLOOR = .005          # the terminal formula needs a positive ROE; a lower result is raised and flagged
RI_IRRELEVANT_FIELDS = ('debt', 'cfo_ttm', 'capex_ttm', 'revenue_ttm', 'operating_income_ttm', 'cash_and_liquid_investments',
                        'investments', 'sbc_ttm')


def _near(series, end, days=20):
    return next((v for e, v in series if abs((facts._d(e) - facts._d(end)).days) <= days), None)


def _common_equity(fb):
    """[(date, common equity)] ascending: stockholders' equity less the latest preferred stock dated within 400 days and less
    accumulated other comprehensive income at the same date (unrealized gains and losses on securities held to maturity
    pull to par; MetLife's bond losses had halved its book and doubled its ROE)."""
    eq = [(e, v) for e, v, _ in facts.series(fb, 'equity', 'instant')]
    pref = [(e, v) for e, v, _ in facts.series(fb, 'preferred_equity', 'instant')]
    aoci = [(e, v) for e, v, _ in facts.series(fb, 'aoci', 'instant')]
    out = []
    for e, v in eq:
        p = [x for d, x in pref if 0 <= (facts._d(e) - facts._d(d)).days <= 400]
        out.append((e, v - (p[-1] if p else 0.) - (_near(aoci, e, 10) or 0.)))
    return out


def _profit_common(fb):
    """({fiscal year end: profit to common}, basis) from the annual facts."""
    common = {e: v for e, v, _ in facts.series(fb, 'net_income_common')}
    if common:
        return common, 'net income available to common stockholders (as reported)'
    ni = {e: v for e, v, _ in facts.series(fb, 'net_income')}
    pref = {e: v for e, v, _ in facts.series(fb, 'preferred_dividends')}
    return {e: v - abs(pref.get(e, 0.)) for e, v in ni.items()}, ('net income less preferred dividends' if pref else
                                                                    'net income (no preferred dividends reported)')


def _ttm_profit(fb):
    t = facts.ttm(fb, 'net_income_common')
    if t:
        return t['value'], t['period_end'], 'net income available to common, trailing twelve months'
    t = facts.ttm(fb, 'net_income')
    if not t:
        return None, None, None
    p = facts.ttm(fb, 'preferred_dividends')
    return t['value'] - abs(p['value'] if p and p['period_end'] == t['period_end'] else 0.), t['period_end'], \
        'net income less preferred dividends, trailing twelve months'


def _path(r0, r5, rT, t):
    """ROE at t years: linear from r0 to r5 at year 5, then to rT at year 10, constant after."""
    if t <= 5:
        return r0 + (r5 - r0) * t / 5
    return r5 + (rT - r5) * min(t - 5, 5) / 5


def build(fb, an, card, qa, inputs, rates, reviews, warn, warnings, empty):
    """-> forecast dict (status built | insufficient_data) with a valuation_version 1 residual_income case as 'spec'."""
    from .forecast import (FORECAST_VERSION, RULES, STAGE, TERMINAL_HIGH_RF, _calendar, _case_sources, _pct, _r, _sha)
    from .valuation import value_company, years, TERMINAL_GROWTH_MAX
    rf, erp, rf_src, erp_src, rf_note, erp_note = rates
    method = 'residual_income'
    as_of = fb['as_of']
    if inputs.get('overrides'):
        warn('overrides_not_applied', 'forecast overrides address operating drivers; the residual-income model takes none (state ROE '
                                      'or payout views as analyst evidence instead)', 'medium')
    ce = [(e, v) for e, v in _common_equity(fb) if facts._d(e) <= facts._d(as_of)]
    if not ce or ce[-1][1] <= 0:
        return empty('insufficient_data', 'no positive common book equity at as_of: residual income needs book equity', method)
    stmt, book = ce[-1]
    if (facts._d(as_of) - facts._d(stmt)).days > 150:
        warn('stale_balance_sheet', f'book equity dated {stmt} is more than 150 days before as_of', 'high')
    profit, p_basis = _profit_common(fb)
    roe_hist = []
    for e in sorted(profit):
        if facts._d(e) > facts._d(as_of):
            continue
        b1 = _near(ce, e)
        b0 = _near(ce, (facts._d(e) - timedelta(days=365)).isoformat())
        if b1 and b0 and b0 > 0 and b1 > 0:
            roe_hist.append((e, profit[e] / ((b0 + b1) / 2)))
    roe_hist = roe_hist[-10:]
    t_val, t_end, t_basis = _ttm_profit(fb)
    r_ttm = None
    if t_val is not None:
        b1, b0 = _near(ce, t_end), _near(ce, (facts._d(t_end) - timedelta(days=365)).isoformat(), 45)
        if b1 and b0 and b0 > 0 and b1 > 0:
            r_ttm = t_val / ((b0 + b1) / 2)
    if r_ttm is None and roe_hist:
        r_ttm, t_basis = roe_hist[-1][1], 'latest fiscal year (no trailing-twelve-month profit with matching book equity)'
    if r_ttm is None:
        return empty('insufficient_data', 'no profit to common with matching book equity: ROE cannot be measured', method)
    res = qa.get('resolved') or {}
    shares = (res.get('shares_outstanding') or {}).get('value')
    price = (fb.get('market') or {}).get('price') or {}
    if not shares:
        return empty('insufficient_data', 'dataqa did not resolve the point-in-time share count', method)
    if price.get('value') is None or price.get('currency', fb['company']['currency']) != fb['company']['currency']:
        return empty('insufficient_data', 'no quote in the company currency at as_of', method)
    vals = sorted(x for _, x in roe_hist) or [r_ttm]
    r_med = median(vals)
    r_low = vals[len(vals) // 4] if len(vals) >= 4 else min(vals + [r_ttm])
    # robust start: the median of the trailing twelve months and the last three fiscal years (one exceptional year, such as an
    # insurer's year without catastrophes, does not set the path)
    r0 = median([r_ttm] + [x for _, x in roe_hist[-3:]])
    if len(roe_hist) < 5:
        warn('short_roe_history', f'only {len(roe_hist)} fiscal years of ROE: the median and lower quartile may not span a credit cycle', 'high')
    # payout: dividends to common + buybacks over profit to common, last three fiscal years
    div = {e: abs(v) for e, v, _ in facts.series(fb, 'dividends_paid')}
    bb = {e: abs(v) for e, v, _ in facts.series(fb, 'buybacks')}
    pdiv = {e: abs(v) for e, v, _ in facts.series(fb, 'preferred_dividends')}
    pays = []
    for e in sorted(profit)[-3:]:
        if profit[e] > 0 and (e in div or e in bb):
            pays.append((max(0., div.get(e, 0.) - pdiv.get(e, 0.)) + bb.get(e, 0.)) / profit[e])
    if pays:
        payout = min(max(sum(pays) / len(pays), 0.), 1.)
        pay_basis = f'mean of {len(pays)} fiscal years of (dividends to common + buybacks) / profit to common, bounded 0-100%'
    else:
        payout, pay_basis = .5, 'no distribution history: 50% assumed'
        warn('payout_unknown', 'no dividend or buyback history: payout set to 50% (model assumption)', 'medium')
    stage = (card.get('stage') or {}).get('value') or 'mature'
    st_add = (STAGE.get(stage) or STAGE['mature'])['ke']
    ke = min(max(rf + erp + st_add + BANK_KE_ADDON, rf + .02), .20)
    adj = [{'factor': 'stage:' + stage, 'value': st_add, 'reason': f'business-risk add-on for the {stage} stage'},
           {'factor': 'balance_sheet_business', 'value': BANK_KE_ADDON,
            'reason': 'balance-sheet leverage and the credit / underwriting cycle (documented add-on, not a measured beta)'}]
    # the operating forecast's component shape (forecast._ke_parts), without a relevering row: deposits and float are operating funding
    ke_parts = {'risk_free': rf, 'equity_risk_premium': erp, 'notes': [rf_note, erp_note],
                **{'adjustment_' + a['factor'].replace(':', '_'): a['value'] for a in adj}, 'adjustments': adj, 'table_rate': ke,
                'unlevered': ke, 'asset_unlevered': None, 'leverage': None,
                'formula': 'Ke = risk-free + equity risk premium + stage add-on + balance-sheet add-on; no relevering'}
    above = sum(1 for _, x in roe_hist if x > ke)
    persistent = len(roe_hist) >= PERSISTENT['min_years'] and above >= PERSISTENT['share_above'] * len(roe_hist)
    kept = PERSISTENT['kept'] if persistent else EXCESS_KEPT
    excess = lambda r, k: ke + k * (r - ke) if r > ke else r
    hi = max(r_med, (r0 + r_med) / 2)
    targets = {'base': (r0, r_med, excess(r_med, kept['base'])),
               'bear': (min(r0, r_low), min(r_med, ke), min(r_med, ke) - .01),
               'bull': (max(r0, r_med), hi, excess(hi, kept['bull']))}
    # book growth the business can use: its own five-year common-equity growth, bounded to [BOOK_GROWTH_FLOOR, BOOK_GROWTH_CAP]
    old = _near(ce, (facts._d(stmt) - timedelta(days=round(5 * 365.25))).isoformat(), 60)
    g_book = (book / old) ** (1 / 5) - 1 if old and old > 0 else BOOK_GROWTH_FLOOR
    g_book = min(max(g_book, BOOK_GROWTH_FLOOR), BOOK_GROWTH_CAP)
    cal = _calendar(fb, {'last_end': sorted(profit)[-1] if profit else stmt}, 10)
    periods, prev = [], as_of
    for b in cal['periods']:
        periods.append((prev, b))
        prev = b
    scen, paths, terms, gs = [], {}, {}, {}
    for s in ('bear', 'base', 'bull'):
        s0, s5, rT = targets[s]
        if rT < ROE_FLOOR:
            warn('roe_floor', f'{s} terminal ROE {_pct(rT)} raised to {_pct(ROE_FLOOR)} so a terminal value exists', 'high')
            rT = ROE_FLOOR
        # perpetual growth at the risk-free rate, the implied ERP's own convention (forecast.TERMINAL_BEAR_GAP comment), within the
        # valuation contract's bound, never above what retained profit funds at the terminal ROE, the ROE itself less 0.1pp, nor
        # within half a point of the cost of equity
        g = gs[s] = max(0., min(rf, TERMINAL_GROWTH_MAX, rT * (1 - payout), rT - .001, ke - .005))
        rows = []
        for a, b in periods:
            mid = (years(as_of, a) + years(as_of, b)) / 2
            roe = _path(s0, s5, rT, mid)
            gb = g + (g_book + {'bear': -.01, 'base': 0., 'bull': .01}[s] - g) * max(0., 1 - mid / 10)   # fades to terminal growth by year 10
            pay = min(1., max(payout, 1 - max(gb, 0.) / roe)) if roe > 0 else payout
            rows.append({'start': a, 'end': b, 'assumption_ids': ['roe_' + s, 'payout', 'regulatory_capital'], 'roe': _r(roe),
                         'payout_fraction': _r(pay),
                         # capital requirement not modelled (assumption regulatory_capital): zero, never a guessed ratio
                         'risk_weighted_assets': 0., 'required_capital_ratio': 0.})
        paths[s] = [{'end': r['end'], 'roe': r['roe'], 'payout_fraction': r['payout_fraction']} for r in rows]
        terms[s] = {'roe': _r(rT), 'growth': _r(g)}
        scen.append({'name': s, 'assumption_ids': ['roe_' + s, 'payout', 'cost_of_equity'], 'cost_of_equity': _r(ke),
                     'thesis': {'base': 'ROE moves to its own ten-year median, then keeps half of its excess over the cost of equity',
                                'bear': 'a credit or underwriting downturn at its own lower-quartile ROE, then no excess return',
                                'bull': 'the better of today\'s and the median ROE holds, three quarters of the excess kept'}[s],
                     'periods': rows, 'terminal': {'assumption_ids': ['terminal'], 'growth': _r(g), 'roe': _r(rT)}})
    # flags read the growth actually used: the contract bound binding below the rate, or a high nominal perpetual growth
    capped = [s for s, g in gs.items() if rf > TERMINAL_GROWTH_MAX and g == TERMINAL_GROWTH_MAX]
    if capped:
        warn('terminal_growth_capped', f'risk-free {rf:.2%} is above the valuation contract\'s {TERMINAL_GROWTH_MAX:.0%} bound on nominal '
                                       f'perpetual growth: terminal growth held at {TERMINAL_GROWTH_MAX:.0%} in {capped}, below the rate, '
                                       'so the ERP\'s growth convention no longer holds there and the terminal value is lower than it '
                                       'would give (check the rate and its currency; value in a currency with a lower rate)', 'high')
    high = {s: g for s, g in gs.items() if g > TERMINAL_HIGH_RF and s not in capped}
    if high:
        warn('terminal_growth_high', f'terminal growth {", ".join(f"{s} {_pct(g)}" for s, g in high.items())} above {TERMINAL_HIGH_RF:.0%} '
                                     f'(risk-free {rf:.2%}): a nominal perpetual growth this high only holds with matching inflation in '
                                     'the reporting currency forever (check the rate and its currency)', 'medium')
    eq_ids = sorted({f['source_id'] for f in facts.candidates(fb, 'equity') + facts.candidates(fb, 'preferred_equity')})
    ni_ids = sorted({f['source_id'] for m in ('net_income_common', 'net_income', 'preferred_dividends') for f in facts.candidates(fb, m)})
    pay_ids = sorted({f['source_id'] for m in ('dividends_paid', 'buybacks') for f in facts.candidates(fb, m)})
    sh_ids = [x for x in [(res.get('shares_outstanding') or {}).get('source_id')] if x]
    extra = {RULES: {'id': RULES, 'title': 'fundamental_engine.forecast_financials documented residual-income rules (ROE fade to the '
                     'own median, excess kept, payout history, cost-of-equity table)', 'url': 'engine://fundamental_engine/forecast_financials/rules',
                     'kind': 'model_rules', 'published_at': as_of, 'available_at': as_of}}
    for src in (rf_src, erp_src):
        if src and 'case' in src:
            extra[src['case']['id']] = src['case']
    rate_ids = [x['fb'] if 'fb' in x else x['case']['id'] for x in (rf_src, erp_src) if x] or [RULES]
    rows = []

    def add(aid, topic, kind, values, rationale, falsifier, source_ids):
        ids = sorted(set(source_ids) & (set(fb['sources']) | set(extra))) or [RULES]
        rows.append({'id': aid, 'topic': topic, 'kind': kind, 'provenance': 'reported' if kind == 'reported' else 'model_assumption',
                     'values': values, 'rationale': rationale, 'falsifier': falsifier, 'source_ids': ids})
    add('open_balance', 'opening book equity', 'reported', {'common_book_equity': book, 'statement_as_of': stmt},
        f'common book equity {book:,.0f} at {stmt} (stockholders\' equity less preferred stock); deposits, float and reserves are '
        'operating funding inside it, not debt', 'A primary balance sheet at or after the statement date shows common equity more than 2% '
        'different.', eq_ids)
    add('shares_point', 'point-in-time shares', 'reported', {'shares': shares, 'as_of': (res.get('shares_outstanding') or {}).get('as_of')},
        f'point-in-time common shares {shares:,.0f}; constant over the forecast (buybacks are treated like dividends)',
        'The share count at as_of differs by more than 1%.', sh_ids or eq_ids)
    add('roe_history', 'return on equity history', 'reported',
        {'ttm': r_ttm, 'median': r_med, 'lower_quartile': r_low, 'by_year': {e: _r(x) for e, x in roe_hist}},
        f'ROE = profit to common / average common equity ({p_basis}); trailing {_pct(r_ttm)} ({t_basis}), median {_pct(r_med)} and '
        f'lower quartile {_pct(r_low)} over {len(roe_hist)} fiscal years; the path starts at {_pct(r0)} (median of the trailing '
        f'twelve months and the last three fiscal years); ROE above the {_pct(ke)} cost of equity in {above} of {len(roe_hist)} years'
        + (' (persistent: more of the excess kept)' if persistent else ''), 'Reported profit or book equity for these years differs.',
        ni_ids + eq_ids)
    for s in ('bear', 'base', 'bull'):
        s0, s5, _ = targets[s]
        add('roe_' + s, f'{s} ROE path', 'model', {'start': s0, 'year_5': s5, 'terminal': terms[s]['roe']},
            {'base': f'from {_pct(s0)} to the own median {_pct(s5)} by year 5, then to {_pct(terms[s]["roe"])} ({kept["base"]:.0%} of the '
                     f'excess over the {_pct(ke)} cost of equity kept)',
             'bear': f'starts at {_pct(s0)} (lower quartile: a credit or underwriting downturn), {_pct(s5)} by year 5, {_pct(terms[s]["roe"])} '
                     'terminal (no excess return)',
             'bull': f'{_pct(s0)} to {_pct(s5)} by year 5, {_pct(terms[s]["roe"])} terminal ({kept["bull"]:.0%} of the excess kept)'}[s],
            f'ROE over the next two fiscal years differs from the {s} path by more than 3 points.', ni_ids + eq_ids + [RULES])
    add('payout', 'distributions', 'model', {'payout_fraction_history': payout, 'history': [_r(x) for x in pays], 'book_growth': g_book},
        pay_basis + f'; book grows by retained profit (clean surplus), at most the growth the business can use: its own five-year '
        f'common-equity growth {_pct(g_book)} (bounded {_pct(BOOK_GROWTH_FLOOR)}-{_pct(BOOK_GROWTH_CAP)}) fading to terminal growth, '
        'so profit beyond that need is paid out (payout = max(history, 1 - book growth / ROE))',
        'Distributions over the next two years differ from this share of profit by more than 20 points.', pay_ids + [RULES])
    add('terminal', 'terminal growth and ROE', 'model', terms, f'terminal growth = min(risk-free, {TERMINAL_GROWTH_MAX:.0%} (the valuation '
        'contract\'s bound), ROE x retention, ROE - 0.1pp, Ke - 0.5pp), at least 0 (perpetual growth at the risk-free rate: the '
        'convention of the implied equity risk premium); terminal value '
        '= book x (ROE - g) / (Ke - g)', 'The terminal ROE is not sustainable at the business\'s regulatory capital and competitive position.',
        rate_ids + [RULES])
    add('cost_of_equity', 'cost of equity', 'model', {'value': ke, 'components': ke_parts},
        'risk-free + equity risk premium + stage add-on + balance-sheet add-on; no relevering (deposits and float are operating funding)',
        'A dated market-implied cost of equity for comparable banks or insurers differs by more than 1.5 points.', rate_ids + [RULES])
    add('regulatory_capital', 'regulatory capital', 'model', {'checked': False},
        'risk-weighted assets and capital ratios are not in companyfacts: the plan is not checked against capital requirements',
        'CET1 (or solvency) ratios fall below requirements plus buffers on this payout.', [RULES])
    warn('regulatory_capital_not_checked', 'regulatory capital (risk-weighted assets, CET1 or solvency ratio) is not checked: an analyst '
                                           'confirms the payout is permitted with evidence', 'medium')
    case_assumptions = []
    for r in rows:
        rv = reviews.get(r['id'], {})
        a = {'id': r['id'], 'kind': r['kind'], 'as_of': as_of, 'review_status': 'reviewed' if rv.get('status') == 'reviewed' else 'unreviewed',
             'source_ids': r['source_ids'], 'rationale': r['rationale'], 'falsifier': r['falsifier']}
        if rv.get('status') == 'reviewed':
            a['reviewer'], a['review_note'] = rv['reviewer'], rv.get('note', '')
        r['review_status'] = a['review_status']
        case_assumptions.append(a)
    used = {x for r in rows for x in r['source_ids']} | {price['source_id']}
    case = {'valuation_version': 1, 'is_demo': any(fb['sources'][x]['kind'] == 'synthetic' for x in used if x in fb['sources']),
            'as_of': as_of, 'company_id': str(fb['company'].get('cik') or fb['company']['ticker']), 'ticker': fb['company']['ticker'],
            'currency': fb['company']['currency'], 'unit': 'absolute', 'archetype': 'financials', 'method': method,
            'quote': {'price': float(price['value']), 'currency': price.get('currency', fb['company']['currency']), 'as_of': price['as_of'],
                      'source_ids': [price['source_id']]},
            'opening': {'as_of': as_of, 'assumption_ids': ['open_balance', 'shares_point'], 'book_equity': float(book), 'shares': float(shares)},
            'report_dates': cal['report_dates'], 'scenarios': scen, 'sources': _case_sources(fb, used, extra),
            'assumptions': case_assumptions}
    json.dumps(case, allow_nan=False)
    try:
        result = value_company(case)
    except ValueError as e:
        return dict(empty('invalid_spec', str(e), method), spec=case, assumptions=rows)
    today = result['annual_values'][0]
    if not today.get('bear', 0) <= today.get('base', 0) <= today.get('bull', 0):
        warn('scenario_values_cross', 'today: bear <= base <= bull does not hold; labels were not re-sorted', 'high')
    pb = {s: (r['terminal'] or {}).get('implied_price_to_book') for s, r in zip(('bear', 'base', 'bull'), result['scenarios'])}
    return {'forecast_version': FORECAST_VERSION, 'status': 'built', 'reason': None, 'method': method, 'spec': case,
            'spec_sha256': _sha(case), 'assumptions': rows,
            'anchors': {'book_equity': book, 'statement_as_of': stmt, 'shares': shares, 'roe': {'ttm': r_ttm, 'median': r_med,
                        'lower_quartile': r_low, 'history': dict(roe_hist), 'basis': p_basis}, 'payout': payout,
                        'price_to_book': float(price['value']) * shares / book, 'terminal_price_to_book': pb,
                        'calendar': {k: cal[k] for k in ('boundaries', 'periods', 'interim', 'fifth_anniversary', 'report_dates')}},
            'paths': paths, 'cost_of_equity': {'bear': ke, 'base': ke, 'bull': ke, 'components': ke_parts},
            'funding': {}, 'overrides': [], 'warnings': warnings,
            'preview': {'status': result['status'], 'today': {k: today.get(k) for k in ('bear', 'base', 'bull')}, 'quote': today['current_quote']},
            'moat_proxy': None, '_external': False, '_external_assumed': False}
