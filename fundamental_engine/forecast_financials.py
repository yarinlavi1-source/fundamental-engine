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

Analyst input (inputs.bank_drivers, 0.14.3; BANK_* constants below): a scenario it sets takes the analyst's evidenced ROE path
(start, year 5, terminal), start book growth (fading to terminal growth by year 10) and payout floor instead of the rules above;
opening replaces the FactBook's book (and shares) with a later filed balance; regulatory_capital fills risk-weighted assets and
the required ratio. Absent, the case is unchanged.

Not modelled, stated as open items: regulatory capital (risk-weighted assets and CET1 are not in companyfacts), credit
quality, reserve adequacy and interest-rate sensitivity; an analyst supplies them with evidence and a reviewer signs.
"""
import json
from datetime import timedelta
from statistics import median

from . import facts

PRE_BREAKEVEN_MIN_YEARS = 3  # consecutive profitable latest fiscal years after loss years: a scale-up past breakeven
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
# inputs['bank_drivers'] (0.14.3): the analyst's evidenced reading of THIS company's ROE, book growth and payout per scenario, with
# an optional later reported opening balance and regulatory capital. A scenario it sets replaces the own-history rules above
# for that scenario only; without it every line below is skipped and the case is byte-identical. Every value is bounded and
# evidenced (FactBook source or qualitative ids); a terminal ROE above the cost of equity needs a terminal_reason; book growth
# may exceed BOOK_GROWTH_CAP only as far as retained profit funds it (book_growth_start <= roe_start x (1 - payout)); the payout,
# when given, is the floor of distributions (book growth above it is paid out, as in the rules); nothing is defaulted silently.
BANK_SCENARIO_KEYS = ('roe_start', 'roe_year5', 'roe_terminal', 'book_growth_start', 'payout', 'terminal_reason', 'source_tier',
                      'evidence_ids', 'reason')
BANK_RANGES = {'roe_start': (-.3, .6), 'roe_year5': (-.3, .6), 'roe_terminal': (ROE_FLOOR, .4), 'book_growth_start': (-.1, .4),
               'payout': (0., 1.)}
BANK_TIERS = ('own_history', 'guidance', 'contracted')
BANK_OPENING_KEYS = ('book_equity', 'shares', 'as_of', 'reason', 'evidence_ids')
BANK_OPENING_BAND = (.5, 2.)    # a later reported book within this factor of the FactBook's last common book (a units guard)
BANK_CAPITAL_KEYS = ('risk_weighted_assets', 'required_capital_ratio', 'as_of', 'reason', 'evidence_ids')
BANK_CAPITAL_RATIO_MAX = .3
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


def check_bank_drivers(fb, bd):
    """Shape, bounds and evidence of inputs.bank_drivers (called by forecast.build); ValueError on anything it refuses. The rule
    that needs the cost of equity (terminal ROE above it) is checked in build."""
    from .forecast import _d, _filing_document_evidence, _num
    known = set(fb.get('sources', {})) | {q['id'] for q in fb.get('qualitative', [])}
    if not isinstance(bd, dict) or not bd or sorted(set(bd) - {'bear', 'base', 'bull', 'opening', 'regulatory_capital'}):
        raise ValueError('bank_drivers: an object {bear|base|bull: {...}, opening: {...}, regulatory_capital: {...}}')

    def evidence(x, where, filing=False):
        if not isinstance(x.get('reason'), str) or not x['reason'].strip():
            raise ValueError(f'{where}: a written reason is required')
        ev = x.get('evidence_ids')
        if not isinstance(ev, list) or not ev or any(e not in known for e in ev):
            raise ValueError(f'{where}: evidence_ids must be FactBook source or qualitative ids')
        if filing and not any(_filing_document_evidence(fb, e) for e in ev):
            raise ValueError(f'{where}: cite the filing document (the 6-K / 10-Q / 20-F read for this number, or an item citing it)')
        if filing:
            try:
                when = _d(x.get('as_of'))
            except (TypeError, ValueError):
                raise ValueError(f'{where}: as_of must be the date of the reported figure') from None
            if when > _d(fb['as_of']):
                raise ValueError(f'{where}: dated after as_of (look-ahead)')
    for s in ('bear', 'base', 'bull'):
        x = bd.get(s)
        if x is None:
            continue
        where = f'bank_drivers.{s}'
        if not isinstance(x, dict) or sorted(set(x) - set(BANK_SCENARIO_KEYS)):
            raise ValueError(f"{where}: an object with {', '.join(BANK_SCENARIO_KEYS)}")
        for k, (lo, hi) in BANK_RANGES.items():
            if k == 'payout' and x.get(k) is None:
                continue
            if _num(x.get(k)) is None or not lo <= x[k] <= hi:
                raise ValueError(f'{where}.{k} must be a decimal from {lo} to {hi}')
        if x.get('source_tier') not in BANK_TIERS:
            raise ValueError(f"{where}: source_tier one of {', '.join(BANK_TIERS)} (the company's own record, guidance or contracts)")
        evidence(x, where)
        if x['source_tier'] == 'guidance' and not any(q.get('id') in x['evidence_ids'] and q.get('kind') == 'guidance'
                                                      for q in fb.get('qualitative') or []):
            raise ValueError(f'{where}: tier guidance needs a guidance item among evidence_ids')
        retained = x['roe_start'] * (1 - (x.get('payout') or 0.))
        if x['book_growth_start'] > retained + 1e-9:
            raise ValueError(f"{where}: book_growth_start {x['book_growth_start']:.1%} is above what retained profit funds "
                             f"(roe_start x (1 - payout) = {retained:.1%}): lower the growth or the payout, or evidence a capital raise")
    op = bd.get('opening')
    if op is not None:
        if not isinstance(op, dict) or sorted(set(op) - set(BANK_OPENING_KEYS)):
            raise ValueError(f"bank_drivers.opening: an object with {', '.join(BANK_OPENING_KEYS)}")
        if _num(op.get('book_equity')) is None or op['book_equity'] <= 0 or \
                (op.get('shares') is not None and (_num(op['shares']) is None or op['shares'] <= 0)):
            raise ValueError('bank_drivers.opening: book_equity (common, on the FactBook basis) and shares (if given) positive numbers')
        evidence(op, 'bank_drivers.opening', filing=True)
    rc = bd.get('regulatory_capital')
    if rc is not None:
        if not isinstance(rc, dict) or sorted(set(rc) - set(BANK_CAPITAL_KEYS)):
            raise ValueError(f"bank_drivers.regulatory_capital: an object with {', '.join(BANK_CAPITAL_KEYS)}")
        if _num(rc.get('risk_weighted_assets')) is None or rc['risk_weighted_assets'] <= 0 or \
                _num(rc.get('required_capital_ratio')) is None or not 0 < rc['required_capital_ratio'] <= BANK_CAPITAL_RATIO_MAX:
            raise ValueError('bank_drivers.regulatory_capital: risk_weighted_assets positive and required_capital_ratio (the CET1 '
                             f'requirement with buffers) a decimal above 0 and at most {BANK_CAPITAL_RATIO_MAX}')
        evidence(rc, 'bank_drivers.regulatory_capital', filing=True)


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
    fb_stmt, fb_book = stmt, book
    bd = inputs.get('bank_drivers') or {}      # validated by forecast.build (check_bank_drivers); absent -> nothing below changes
    qual = {q['id']: q for q in fb.get('qualitative') or []}
    src_of = lambda ids: [y for e in ids for y in ([e] if e in fb['sources'] else (qual.get(e) or {}).get('source_ids') or [])]
    op = bd.get('opening')
    if op is not None:
        if facts._d(op['as_of']) <= facts._d(stmt):
            warn('bank_opening_not_applied', f"bank_drivers.opening dated {op['as_of']} is not after the FactBook's last balance "
                                             f'sheet {stmt}: the FactBook book equity is kept', 'medium')
            op = None
        elif not BANK_OPENING_BAND[0] <= op['book_equity'] / book <= BANK_OPENING_BAND[1]:
            raise ValueError(f"bank_drivers.opening: book_equity {op['book_equity']:,.0f} is outside {BANK_OPENING_BAND[0]}-"
                             f'{BANK_OPENING_BAND[1]}x the FactBook common book {book:,.0f} at {stmt} (check units and basis)')
        else:
            stmt, book = op['as_of'], float(op['book_equity'])
            warn('bank_opening_applied', f'opening common book {book:,.0f} at {stmt} from the analyst\'s filing evidence (later than the '
                                         f'FactBook balance {fb_book:,.0f} at {fb_stmt})', 'low')
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
    if op is not None and op.get('shares') is not None:
        shares = float(op['shares'])
    if not shares:
        return empty('insufficient_data', 'dataqa did not resolve the point-in-time share count', method)
    if price.get('value') is None or price.get('currency', fb['company']['currency']) != fb['company']['currency']:
        return empty('insufficient_data', 'no quote in the company currency at as_of', method)
    # a scale-up that turned profitable (the operating forecast's post-breakeven rule for a scale-up's terminal ROIC): when the
    # latest PRE_BREAKEVEN_MIN_YEARS or more fiscal years are all profitable after loss years, the loss years are the build-out
    # before scale, not a credit or underwriting downturn of the business as it now is: the median and lower quartile use the
    # post-breakeven years only (warned; a later loss year keeps the full history)
    run = 0
    for _, x in reversed(roe_hist):
        if x <= 0:
            break
        run += 1
    if run >= PRE_BREAKEVEN_MIN_YEARS and run < len(roe_hist):
        dropped = roe_hist[:-run]
        roe_hist = roe_hist[-run:]
        warn('pre_breakeven_roe_excluded', f"ROE of {len(dropped)} loss years before breakeven ({', '.join(e[:4] for e, _ in dropped)}) is left "
             f'out of the median and lower quartile: the last {run} fiscal years are all profitable (a scale-up past breakeven, '
             'not a credit cycle of the business as it is now)', 'medium')
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
        if any(s not in bd for s in ('bear', 'base', 'bull')):
            warn('payout_unknown', 'no dividend or buyback history: payout set to 50% (model assumption)', 'medium')
    # the distribution floor per scenario: the analyst's evidenced payout, else the history, else (an analyst scenario without
    # history) none: its book growth alone sets what is paid out (stated, never a silent default)
    floor = {}
    for s in ('bear', 'base', 'bull'):
        x = bd.get(s)
        floor[s] = float(x['payout']) if x and x.get('payout') is not None else 0. if x and not pays else payout
    no_floor = [s for s in ('bear', 'base', 'bull') if bd.get(s) and bd[s].get('payout') is None and not pays]
    if no_floor:
        warn('bank_payout_from_growth', f'no distribution history and no bank_drivers payout in {no_floor}: distributions are what '
                                        'the evidenced book growth leaves (payout = 1 - book growth / ROE)', 'medium')
    stage = (card.get('stage') or {}).get('value') or 'mature'
    st_add = (STAGE.get(stage) or STAGE['mature'])['ke']
    ke = min(max(rf + erp + st_add + BANK_KE_ADDON, rf + .02), .20)
    adj = [{'factor': 'stage:' + stage, 'value': st_add, 'reason': f'business-risk add-on for the {stage} stage'},
           {'factor': 'balance_sheet_business', 'value': BANK_KE_ADDON,
            'reason': 'balance-sheet leverage and the credit / underwriting cycle (documented add-on, not a measured beta)'}]
    cr = inputs.get('country_risk')            # forecast.COUNTRY_RISK_KEYS, validated by forecast.build; absent -> no rows
    if cr is not None:
        from .forecast import country_risk_rows    # the gross add-on and the market-average netting, as in the operating table
        rows_c = country_risk_rows(cr)
        ke = min(max(rf + erp + st_add + BANK_KE_ADDON + sum(a['value'] for a in rows_c), rf + .02), .20)
        adj += rows_c
    # the operating forecast's component shape (forecast._ke_parts), without a relevering row: deposits and float are operating funding
    ke_parts = {'risk_free': rf, 'equity_risk_premium': erp, 'notes': [rf_note, erp_note],
                **{'adjustment_' + a['factor'].replace(':', '_'): a['value'] for a in adj}, 'adjustments': adj, 'table_rate': ke,
                'unlevered': ke, 'asset_unlevered': None, 'leverage': None,
                'formula': 'Ke = risk-free + equity risk premium + stage add-on + balance-sheet add-on'
                           + (' + country risk - its market average' if cr else '')
                           + '; no relevering'}
    above = sum(1 for _, x in roe_hist if x > ke)
    persistent = len(roe_hist) >= PERSISTENT['min_years'] and above >= PERSISTENT['share_above'] * len(roe_hist)
    kept = PERSISTENT['kept'] if persistent else EXCESS_KEPT
    excess = lambda r, k: ke + k * (r - ke) if r > ke else r
    hi = max(r_med, (r0 + r_med) / 2)
    targets = {'base': (r0, r_med, excess(r_med, kept['base'])),
               'bear': (min(r0, r_low), min(r_med, ke), min(r_med, ke) - .01),
               'bull': (max(r0, r_med), hi, excess(hi, kept['bull']))}
    for s in ('bear', 'base', 'bull'):
        x = bd.get(s)
        if not x:
            continue
        if x['roe_terminal'] > ke + 1e-12 and not (isinstance(x.get('terminal_reason'), str) and x['terminal_reason'].strip()):
            raise ValueError(f"bank_drivers.{s}: roe_terminal {x['roe_terminal']:.1%} is above the {ke:.1%} cost of equity forever: "
                             'give terminal_reason (the evidenced moat that keeps the excess return)')
        targets[s] = (float(x['roe_start']), float(x['roe_year5']), float(x['roe_terminal']))
    # book growth the business can use: its own five-year common-equity growth, bounded to [BOOK_GROWTH_FLOOR, BOOK_GROWTH_CAP]
    old = _near(ce, (facts._d(fb_stmt) - timedelta(days=round(5 * 365.25))).isoformat(), 60)
    g_book = (fb_book / old) ** (1 / 5) - 1 if old and old > 0 else BOOK_GROWTH_FLOOR
    g_own = g_book
    g_book = min(max(g_book, BOOK_GROWTH_FLOOR), BOOK_GROWTH_CAP)
    g_text = (f'the engine floor {_pct(BOOK_GROWTH_FLOOR)} (no common equity five years before {fb_stmt})' if not (old and old > 0)
              else f'its own five-year common-equity growth {_pct(g_book)}' if g_book == g_own else
              f'the engine cap {_pct(BOOK_GROWTH_CAP)} (its own five-year common-equity growth {_pct(g_own)} is above it)'
              if g_own > BOOK_GROWTH_CAP else
              f'the engine floor {_pct(BOOK_GROWTH_FLOOR)} (its own five-year common-equity growth {_pct(g_own)} is below it)')
    rc = bd.get('regulatory_capital')
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
        g = gs[s] = max(0., min(rf, TERMINAL_GROWTH_MAX, rT * (1 - floor[s]), rT - .001, ke - .005))
        g0 = bd[s]['book_growth_start'] if bd.get(s) else g_book + {'bear': -.01, 'base': 0., 'bull': .01}[s]
        rows, rwa = [], (float(rc['risk_weighted_assets']) if rc else 0.)
        for a, b in periods:
            mid = (years(as_of, a) + years(as_of, b)) / 2
            roe = _path(s0, s5, rT, mid)
            gb = g + (g0 - g) * max(0., 1 - mid / 10)   # fades to terminal growth by year 10
            pay = min(1., max(floor[s], 1 - max(gb, 0.) / roe)) if roe > 0 else floor[s]
            if rc:          # risk-weighted assets grow with the balance sheet (the scenario's book growth path)
                rwa *= (1 + gb) ** (years(as_of, b) - years(as_of, a))
            rows.append({'start': a, 'end': b, 'assumption_ids': ['roe_' + s, 'payout', 'regulatory_capital'], 'roe': _r(roe),
                         'payout_fraction': _r(pay),
                         # capital requirement: zero unless bank_drivers.regulatory_capital evidences it, never a guessed ratio
                         'risk_weighted_assets': _r(rwa) if rc else 0.,
                         'required_capital_ratio': float(rc['required_capital_ratio']) if rc else 0.})
        paths[s] = [{'end': r['end'], 'roe': r['roe'], 'payout_fraction': r['payout_fraction']} for r in rows]
        terms[s] = {'roe': _r(rT), 'growth': _r(g)}
        scen.append({'name': s, 'assumption_ids': ['roe_' + s, 'payout', 'cost_of_equity'], 'cost_of_equity': _r(ke),
                     'thesis': f"evidenced ROE, book growth and payout path ({bd[s]['source_tier']}, inputs.bank_drivers)" if bd.get(s)
                     else {'base': 'ROE moves to its own ten-year median, then keeps half of its excess over the cost of equity',
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
    if op is not None:
        eq_ids = sorted(set(eq_ids) | set(src_of(op['evidence_ids'])))
        if op.get('shares') is not None:
            sh_ids = src_of(op['evidence_ids'])
    extra = {RULES: {'id': RULES, 'title': 'fundamental_engine.forecast_financials documented residual-income rules (ROE fade to the '
                     'own median, excess kept, payout history, cost-of-equity table)', 'url': 'engine://fundamental_engine/forecast_financials/rules',
                     'kind': 'model_rules', 'published_at': as_of, 'available_at': as_of}}
    for src in (rf_src, erp_src):
        if src and 'case' in src:
            extra[src['case']['id']] = src['case']
    rate_ids = [x['fb'] if 'fb' in x else x['case']['id'] for x in (rf_src, erp_src) if x] or [RULES]
    crp_ids = []
    if cr is not None:
        extra['input:country_risk'] = {'id': 'input:country_risk', 'title': cr['title'], 'url': cr['url'], 'kind': 'analyst_input',
                                       'published_at': str(cr['as_of'])[:10], 'available_at': str(cr['as_of'])[:10],
                                       'retrieval': 'provider_normalized'}
        crp_ids = ['input:country_risk'] + list(cr['evidence_ids'])
    rows = []

    def add(aid, topic, kind, values, rationale, falsifier, source_ids):
        ids = sorted(set(source_ids) & (set(fb['sources']) | set(extra))) or [RULES]
        rows.append({'id': aid, 'topic': topic, 'kind': kind, 'provenance': 'reported' if kind == 'reported' else 'model_assumption',
                     'values': values, 'rationale': rationale, 'falsifier': falsifier, 'source_ids': ids})
    add('open_balance', 'opening book equity', 'reported', {'common_book_equity': book, 'statement_as_of': stmt},
        f'common book equity {book:,.0f} at {stmt} (stockholders\' equity less preferred stock); deposits, float and reserves are '
        'operating funding inside it, not debt' + (f" (inputs.bank_drivers.opening, a later filing than the FactBook's {fb_stmt}: "
                                                   f"{op['reason']})" if op is not None else ''), 'A primary balance sheet at or after the statement date shows common equity more than 2% '
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
        x = bd.get(s)
        if x:
            add('roe_' + s, f'{s} ROE path', 'analyst', {'start': s0, 'year_5': s5, 'terminal': terms[s]['roe'],
                                                       'book_growth_start': x['book_growth_start'], 'payout_floor': floor[s],
                                                       'source_tier': x['source_tier']},
                f"inputs.bank_drivers ({x['source_tier']}): ROE {_pct(s0)} to {_pct(s5)} by year 5, {_pct(terms[s]['roe'])} terminal "
                f"(cost of equity {_pct(ke)}); book growth {_pct(x['book_growth_start'])} fading to terminal growth by year 10"
                + (f"; payout at least {_pct(x['payout'])}" if x.get('payout') is not None else '') + f": {x['reason']}"
                + (f"; terminal: {x['terminal_reason']}" if x.get('terminal_reason') else ''),
                f'ROE over the next two fiscal years differs from the {s} path by more than 3 points.', src_of(x['evidence_ids']))
            continue
        add('roe_' + s, f'{s} ROE path', 'model', {'start': s0, 'year_5': s5, 'terminal': terms[s]['roe']},
            {'base': f'from {_pct(s0)} to the own median {_pct(s5)} by year 5, then to {_pct(terms[s]["roe"])} ({kept["base"]:.0%} of the '
                     f'excess over the {_pct(ke)} cost of equity kept)',
             'bear': f'starts at {_pct(s0)} (lower quartile: a credit or underwriting downturn), {_pct(s5)} by year 5, {_pct(terms[s]["roe"])} '
                     'terminal (no excess return)',
             'bull': f'{_pct(s0)} to {_pct(s5)} by year 5, {_pct(terms[s]["roe"])} terminal ({kept["bull"]:.0%} of the excess kept)'}[s],
            f'ROE over the next two fiscal years differs from the {s} path by more than 3 points.', ni_ids + eq_ids + [RULES])
    add('payout', 'distributions', 'model', {'payout_fraction_history': payout, 'history': [_r(x) for x in pays], 'book_growth': g_book,
                                              **({'floor_by_scenario': floor} if bd else {})},
        pay_basis + f'; book grows by retained profit (clean surplus), at most the growth the business can use: {g_text} '
        f'(bounded {_pct(BOOK_GROWTH_FLOOR)}-{_pct(BOOK_GROWTH_CAP)}) fading to terminal growth, '
        'so profit beyond that need is paid out (payout = max(history, 1 - book growth / ROE))'
        + (f"; inputs.bank_drivers sets the start growth and the payout floor in {[s for s in ('bear', 'base', 'bull') if bd.get(s)]}"
           if any(bd.get(s) for s in ('bear', 'base', 'bull')) else ''),
        'Distributions over the next two years differ from this share of profit by more than 20 points.',
        pay_ids + [y for s in ('bear', 'base', 'bull') if bd.get(s) for y in src_of(bd[s]['evidence_ids'])] + [RULES])
    add('terminal', 'terminal growth and ROE', 'model', terms, f'terminal growth = min(risk-free, {TERMINAL_GROWTH_MAX:.0%} (the valuation '
        'contract\'s bound), ROE x retention, ROE - 0.1pp, Ke - 0.5pp), at least 0 (perpetual growth at the risk-free rate: the '
        'convention of the implied equity risk premium); terminal value '
        '= book x (ROE - g) / (Ke - g)', 'The terminal ROE is not sustainable at the business\'s regulatory capital and competitive position.',
        rate_ids + [RULES])
    add('cost_of_equity', 'cost of equity', 'model', {'value': ke, 'components': ke_parts},
        'risk-free + equity risk premium + stage add-on + balance-sheet add-on; no relevering (deposits and float are operating funding)',
        'A dated market-implied cost of equity for comparable banks or insurers differs by more than 1.5 points.', rate_ids + crp_ids + [RULES])
    if rc:
        need = float(rc['risk_weighted_assets']) * float(rc['required_capital_ratio'])
        add('regulatory_capital', 'regulatory capital', 'analyst',
            {'checked': True, 'risk_weighted_assets': rc['risk_weighted_assets'], 'required_capital_ratio': rc['required_capital_ratio'],
             'as_of': rc['as_of'], 'book_to_rwa': book / float(rc['risk_weighted_assets'])},
            f"inputs.bank_drivers.regulatory_capital: risk-weighted assets {rc['risk_weighted_assets']:,.0f} at {rc['as_of']}, growing "
            f"with each scenario's book growth path; common book must stay at or above {rc['required_capital_ratio']:.1%} of them "
            f"(common book is a proxy: CET1 deducts goodwill and intangibles): {rc['reason']}",
            'CET1 (or solvency) ratios fall below requirements plus buffers on this payout.', src_of(rc['evidence_ids']))
        if book < need:
            warn('capital_below_requirement', f'opening common book {book:,.0f} is below the required {need:,.0f} '
                                              f"({rc['required_capital_ratio']:.1%} of risk-weighted assets)", 'high')
    else:
        add('regulatory_capital', 'regulatory capital', 'model', {'checked': False},
            'risk-weighted assets and capital ratios are not in companyfacts: the plan is not checked against capital requirements',
            'CET1 (or solvency) ratios fall below requirements plus buffers on this payout.', [RULES])
        warn('regulatory_capital_not_checked', 'regulatory capital (risk-weighted assets, CET1 or solvency ratio) is not checked: an analyst '
                                               'confirms the payout is permitted with evidence', 'medium')
    applied = [s for s in ('bear', 'base', 'bull') if bd.get(s)]
    if applied:
        warn('bank_drivers_applied', 'inputs.bank_drivers replaced the own-history ROE / book-growth rules in ' + ', '.join(
            f"{s} (ROE {_pct(bd[s]['roe_start'])} -> {_pct(bd[s]['roe_year5'])} -> {_pct(bd[s]['roe_terminal'])}, book growth "
            f"{_pct(bd[s]['book_growth_start'])}, payout floor {_pct(floor[s])}, {bd[s]['source_tier']})" for s in applied), 'low')
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
    if None not in [today.get(k, 0) for k in ('bear', 'base', 'bull')] and not today.get('bear', 0) <= today.get('base', 0) <= today.get('bull', 0):
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
