"""Lean two-method valuation for the 5-minute ticker check (owner preference 04.11.2026).

Method 1: DCF of free cash flow after SBC. The cost of equity is rf + beta x ERP and fades to a mature beta over
10 years. Growth fades to terminal growth = rf over 2033-2041, with reinvestment = g / terminal ROIC. Cash is added.
Method 2: exit multiple. The end-of-horizon value is P/E x next-year operating earnings (ex cash), plus the PV of the
FCF to the horizon, plus cash today.
Value = average of the two. Year-end values are conditional values on each path, not price forecasts.

Usage: python scripts/lean_model.py params.json [--out result.json] [--summary]
--summary prints a short plain table (values, 2-year and 5-year ranges, returns, ladder, buy test, top sensitivities,
warnings) instead of the JSON; --out still writes the full JSON.
The params file has keys: ticker, as_of, price, rf, erp, beta, beta_mature, shares_m, net_cash_m (end of the base year;
negative = net debt), rev0_m (base-year revenue), base_year, tax, cash_yield, debt_rate, roic_terminal,
days_to_base_year_end, years_to_horizon, buy_return, probabilities {bear, base, bull}, and scenarios
{bear|base|bull: {g[6], m[6] (operating margin after SBC), conv[6] (FCF / NOPAT), pe (exit P/E on next-year
operating EPS), charge {year index: $M after tax}, optional shares_m}}.
Converts, puts, warrants, leases and patent cliffs are NOT modelled here. Put them into the inputs (net cash, shares,
growth/margin paths, charges) from the filings fine-print lane, or use a custom model.

Two lenses (owner preference 05.11.2026, CLAUDE.md): the result also carries a calibrated lens of the base case. It keeps
the base's revenue, conversion and charges but holds today's forward P/E (price / first forecast year's operating EPS) as
the exit multiple and does not let the margin fall below the first forecast year. Reason: in the 2016-2025 price-path
walk-forward (docs/PRICE_PATH_V2_RESULTS_2026-10-25.md) fading the multiple and cutting margins were the two largest
sources of a low bias. Optional params key "calibrated": {"pe": exit P/E to use when today's multiple is not credible
(e.g. a GAAP P/E inflated by temporarily depressed earnings), "m": [6 margins] when a margin cut is hard and evidenced
(e.g. a quantified depreciation wave), "why": text}. The two lenses are not averaged: both historical analogues erred low
in the same direction (3-year median bias x0.73 and x0.89), so an average cannot remove that shared bias. A buy, sell or
trim call is made only where both lenses fall in the same band ("bands": buy/hold/lagging thresholds in % a year).

Reporting keys (15.11.2026; they read the model above and change none of its numbers):
- price_ladder: the price today at which the base 5-year return equals each hurdle, for both lenses, always as
  year-end value at the horizon / (1 + hurdle) ^ years_to_horizon. Every rung carries its hurdle (buy test, the
  margin-of-safety return = buy_return, the index = rf + erp, hold floor, sell line), so two hurdles cannot be mixed.
- buy_test: the owner's rule as numbers (base >= 12% a year in both lenses, bull >= 17%, bear >= -10%), each leg with
  its return, threshold, pass and highest passing price; max_price_passing is the lowest of the four, with the leg named.
  buy_test.rerun: the calibrated leg holds today's multiple, so at a lower price a rerun holds a lower one; rerun gives
  the price a rerun confirms (absent when calibrated.pe is set).
- ranges: bear/base/bull value today, at the 2-year and at the 5-year year-end. The 1-2 year number is the noisiest.
- calibrated_diagnostics: whether the margin floor binds, the held multiple against the base exit multiple, how much of
  the gap between the lenses comes from the multiple and how much from the margin, and a warning when the held multiple
  looks inflated (above 1.5x the base exit multiple, or a positive first-year operating EPS under 2% of the price).
- funding: the model's own net-cash path by year in each scenario, its low point, and a flag when net debt at the low
  point is above 3x that year's after-tax operating profit or the path borrows more than today's balance sheet holds.
  equity_wiped marks a scenario whose value today is zero or less (the value is floored at 0.01, never negative).
- sensitivity: the change in the base value today and in the base 5-year return for one input changed at a time.
  It is the model's own arithmetic, not a forecast. sensitivity_base (older, wider steps) is kept as it was.
- reverse_reading: the growth the price needs on the base margin path (= market_implied_growth_pct) and at a margin
  held flat at the first forecast year, so the reading is not dominated by the margin path.
- warnings: every flag above in one list.
"""
import argparse
import json


def _ke_path(p, n):
    k0 = p['rf'] + p['beta'] * p['erp']
    km = p['rf'] + p['beta_mature'] * p['erp']
    return [k0 + (km - k0) * min(i, 10) / 10 for i in range(n)]


def run_scenario(p, d, detail=False):
    """One scenario. Revenue index 0 = base year; g/m/conv index i = base year + 1 + i (6 years)."""
    tax, sh = p['tax'], d.get('shares_m', p['shares_m'])  # per-scenario shares (e.g. warrants vesting with the deals)
    rev = [p['rev0_m']]
    opi, eps_op, fcf = [], [], []
    charge = {int(k): v for k, v in (d.get('charge') or {}).items()}
    for i, g in enumerate(d['g']):
        r = rev[-1] * (1 + g)
        rev.append(r)
        oi = r * d['m'][i]
        opi.append(oi)
        nopat = oi * (1 - tax)
        eps_op.append(nopat / sh)
        fcf.append(nopat * d['conv'][i] - charge.get(i, 0.0))
    tg, roic = p['rf'], p['roic_terminal']
    g_last, n_last = d['g'][-1], opi[-1] * (1 - tax)
    for j in range(1, 10):
        g = g_last + (tg - g_last) * j / 9
        n_last *= 1 + g
        fcf.append(n_last * (1 - g / roic))
    ke = _ke_path(p, len(fcf) + 1)
    tv = n_last * (1 + tg) * (1 - tg / roic) / (ke[len(fcf)] - tg)
    cash = [p['net_cash_m']]
    for i in range(5):
        c = cash[-1]
        rate = p['cash_yield'] if c >= 0 else p.get('debt_rate', p['cash_yield'])
        cash.append(c + fcf[i] + c * rate * (1 - tax))

    def dcf_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, len(fcf)):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return (v + tv / disc + cash[t]) / sh

    exit_op = d['pe'] * eps_op[5] * sh

    def mult_at(t):
        v, disc = 0.0, 1.0
        for i in range(t, 5):
            disc *= 1 + ke[i]
            v += fcf[i] / disc
        return (v + exit_op / disc + cash[t]) / sh

    dcf = [dcf_at(t) for t in range(6)]
    mul = [mult_at(t) for t in range(6)]
    val = [max((a + b) / 2, 0.01) for a, b in zip(dcf, mul)]   # equity cannot go below zero: a negative path means the scenario needs a restructuring the model does not show
    today = val[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365)
    ret = (val[5] / p['price']) ** (1 / p['years_to_horizon']) - 1
    y0 = p['base_year']
    res = {
        'revenue_m': {y0 + i: round(x) for i, x in enumerate(rev)},
        'op_margin': {y0 + 1 + i: m for i, m in enumerate(d['m'])},
        'eps_operating': {y0 + 1 + i: round(x, 2) for i, x in enumerate(eps_op)},
        'eps_with_interest': {y0 + 1 + i: round((opi[i] + cash[i] * (p['cash_yield'] if cash[i] >= 0 else p.get('debt_rate', p['cash_yield']))) * (1 - tax) / sh, 2) for i in range(6)},
        'dcf_today': round(dcf[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365), 2),
        'multiple_today': round(mul[0] / (1 + ke[0]) ** (p['days_to_base_year_end'] / 365), 2),
        'value_today': round(today, 2),
        'year_end_values': {y0 + t: round(val[t], 2) for t in range(1, 6)},
        'annual_return_pct': round(ret * 100, 1),
        'ke0_pct': round(ke[0] * 100, 2),
    }
    if detail:   # unrounded internals for the reporting keys; the result above is unchanged
        return res, {'shares_m': sh, 'nopat': [x * (1 - tax) for x in opi], 'fcf': fcf[:5], 'cash': cash,
                     'val_raw': [(a + b) / 2 for a, b in zip(dcf, mul)], 'val': val, 'today': today, 'ret': ret}
    return res


BANDS = {'buy': 12.0, 'hold': 6.0, 'lagging': 3.0}   # owner's bands on the base-case yearly return (CLAUDE.md, 05.11.2026)


def band(ret_pct, bands=None):
    b = bands or BANDS
    if ret_pct >= b['buy']:
        return 'buy'
    if ret_pct >= b['hold']:
        return 'hold'
    if ret_pct >= b['lagging']:
        return 'lagging'
    return 'sell'


def _calibrated_inputs(p, d):
    cal = p.get('calibrated') or {}
    first = run_scenario(p, d)
    eps0 = first['eps_operating'][min(first['eps_operating'])]
    pe_today = p['price'] / eps0 if eps0 > 0 else None
    pe = cal.get('pe', pe_today)
    m = cal.get('m') or [max(d['m'][0], x) for x in d['m']]
    return cal, eps0, pe_today, pe, m


def calibrated_lens(p, d):
    """The base case with today's forward P/E held and the margin floored at the first forecast year (see the docstring)."""
    cal, eps0, pe_today, pe, m = _calibrated_inputs(p, d)
    out = {'pe_today': round(pe_today, 1) if pe_today else None, 'pe_used': round(pe, 1) if pe else None,
           'pe_overridden': 'pe' in cal, 'margin_used': [round(x, 4) for x in m], 'margin_overridden': bool(cal.get('m')),
           'why': cal.get('why', '')}
    if pe is None:
        out['note'] = 'no positive first-year EPS: the calibrated lens needs "calibrated.pe"'
        return out
    r = run_scenario(p, dict(d, pe=pe, m=m))
    out.update(value_today=r['value_today'], year_end_values=r['year_end_values'], annual_return_pct=r['annual_return_pct'])
    if pe_today and pe_today > 40 and 'pe' not in cal:
        out['note'] = 'today\'s multiple is above 40: holding it for five years is not credible; set "calibrated.pe"'
    return out


# ---- reporting (15.11.2026): reads the model above, changes none of its numbers ----

BUY_TEST = {'bull': 17.0, 'bear': -10.0}   # with the 'buy' band for the base in both lenses (owner's sleeve rule, 05.11.2026)
NOISE_NOTE = ('the 1-2 year value is the noisiest number here: over one or two years the price follows the multiple and '
              'the news far more than the value; the 5-year value rests on the business path')


def _price_at(end_value, hurdle_pct, years, factor=None):
    """The one formula for every entry price: year-end value at the horizon / (1 + hurdle) ^ years.

    factor: 1 + return written exactly as buy_price / index_parity_price write it, so those rungs equal those keys."""
    return round(end_value / (factor or 1 + hurdle_pct / 100) ** years, 2)


def _end_values(p, out, cal):
    last = p['base_year'] + 5
    ends = {k: out[k]['year_end_values'][last] for k in out}
    ends['base_calibrated'] = cal['year_end_values'][last] if 'year_end_values' in cal else None
    return ends


def _repriced(p, base, hurdle_pct):
    """Calibrated entry price when the multiple held is the one of that price (what a rerun at that price shows).

    The end value is linear in the held multiple, so two runs give the line and one more checks it.
    None: no such price (holding the price's own multiple, the lens is above or below the hurdle at every price)."""
    cal, eps0, pe_today, pe, m = _calibrated_inputs(p, base)
    if 'pe' in cal or not pe_today:
        return None

    def end(price):
        return run_scenario(p, dict(base, pe=price / eps0, m=m), detail=True)[1]['val_raw'][5]

    k = (1 + hurdle_pct / 100) ** p['years_to_horizon']
    p1, p2 = p['price'], p['price'] / 2
    slope = (end(p1) - end(p2)) / (p1 - p2)
    if k - slope <= 1e-9:
        return None
    x = (end(p1) - slope * p1) / (k - slope)
    if x <= 0 or abs(end(x) - k * x) > 0.005 * k * x:
        return None
    return round(x, 2)


def price_ladder(p, out, cal, bands=None):
    b = bands or BANDS
    years, ends = p['years_to_horizon'], _end_values(p, out, cal)
    idx = round((p['rf'] + p['erp']) * 100, 2)   # the label; the rung price uses the unrounded return
    mos = round(p['buy_return'] * 100, 2)
    spec = [('buy_test', f'buy test: base return {b["buy"]:g}% a year', b['buy'], None),
            ('margin_of_safety', f'entry with a margin of safety: buy_return {mos:g}% a year', mos, 'buy_price'),
            ('index', f'index return (rf + erp) {idx:g}% a year', idx, 'index_parity_price'),
            ('hold_floor', f'hold floor: {b["hold"]:g}% a year', b['hold'], None),
            ('sell_line', f'sell line: {b["lagging"]:g}% a year', b['lagging'], None)]
    rungs = {}
    for key, label, h, same in sorted(spec, key=lambda s: -s[2]):
        f = {'margin_of_safety': 1 + p['buy_return'], 'index': 1 + p['rf'] + p['erp']}.get(key)
        rung = {'label': label, 'return_pct': h, 'ours': _price_at(ends['base'], h, years, f),
                'calibrated': _price_at(ends['base_calibrated'], h, years, f) if ends['base_calibrated'] is not None else None}
        if same:
            rung['same_as'] = same
        rungs[key] = rung
    return {'method': 'price today = year-end value at the horizon / (1 + return) ^ years_to_horizon; one return per rung',
            'horizon_year': p['base_year'] + 5, 'years_to_horizon': years, 'price': p['price'],
            'end_value': {'ours': ends['base'], 'calibrated': ends['base_calibrated']},
            'rungs': rungs,
            'calibrated_note': ('the calibrated rungs hold the multiple of today\'s price (or calibrated.pe) to the horizon; '
                                'after a price move a rerun holds the new multiple (see buy_test.rerun)')}


def buy_test(p, out, cal, base, fund, bands=None):
    b = bands or BANDS
    last = p['base_year'] + 5
    years, ends = p['years_to_horizon'], _end_values(p, out, cal)
    spec = [('base_ours', 'base', out.get('base', {}).get('annual_return_pct'), b['buy']),
            ('base_calibrated', 'base_calibrated', cal.get('annual_return_pct'), b['buy']),
            ('bull', 'bull', out.get('bull', {}).get('annual_return_pct'), BUY_TEST['bull']),
            ('bear', 'bear', out.get('bear', {}).get('annual_return_pct'), BUY_TEST['bear'])]
    legs = {}
    for key, end_key, ret, thr in spec:
        end = ends.get(end_key)
        legs[key] = {'return_pct': ret, 'threshold_pct': thr, 'pass': None if ret is None else ret >= thr,
                     'max_price': None if end is None else _price_at(end, thr, years)}
        if last in fund['scenarios'].get(end_key, {}).get('equity_wiped_year_ends', []):
            legs[key].update(max_price=None, equity_wiped_at_horizon=True)   # no price passes a wiped scenario
    res = {'rule': (f'base >= {b["buy"]:g}% a year in both lenses, bull >= {BUY_TEST["bull"]:g}%, bear >= '
                    f'{BUY_TEST["bear"]:g}% (yearly returns from today\'s price to the horizon year-end)'),
           'price': p['price'], 'legs': legs}
    res['complete'] = all(v['pass'] is not None for v in legs.values())
    res['pass'] = res['complete'] and all(v['pass'] for v in legs.values())
    priced = {k: v['max_price'] for k, v in legs.items() if v['max_price'] is not None}
    wiped = [k for k, v in legs.items() if v.get('equity_wiped_at_horizon')]
    if wiped:
        res.update(max_price_passing=None, binding_leg=wiped[0],
                   no_price_passes=f'the {wiped[0]} case wipes the equity at the horizon: no price passes that leg')
    elif priced:
        res['binding_leg'] = min(priced, key=priced.get)
        res['max_price_passing'] = priced[res['binding_leg']]
        res['max_price_vs_price_pct'] = round((res['max_price_passing'] / p['price'] - 1) * 100, 1)
    if not wiped and legs['base_calibrated']['max_price'] is not None and 'pe' not in (p.get('calibrated') or {}):
        # the calibrated leg holds today's multiple; at another price a rerun holds that price's multiple
        rp = _repriced(p, base, b['buy'])
        rerun = dict(priced, base_calibrated=rp) if rp is not None else {k: v for k, v in priced.items() if k != 'base_calibrated'}
        lead = min(rerun, key=rerun.get)
        res['rerun'] = {'calibrated_max_price': rp, 'max_price_passing': rerun[lead], 'binding_leg': lead,
                        'lower_than_stated': rerun[lead] < res['max_price_passing'] * 0.99,
                        'note': ('the calibrated leg with the multiple of its own price held, which is what a rerun at that '
                                 'price shows; calibrated_max_price null = holding the price\'s own multiple the lens never '
                                 'crosses the hurdle, so the leg is left out')}
    if not res['complete']:
        res['note'] = 'a leg has no value (no calibrated lens or a missing scenario): the test cannot pass'
    return res


def ranges(p, out, cal):
    y0, price = p['base_year'], p['price']

    def row(t, years):
        r = {'year': y0 + t, 'years_from_today': round(years, 2)}
        vals = {k: out[k]['year_end_values'][y0 + t] for k in out}
        if 'year_end_values' in cal:
            vals['base_calibrated'] = cal['year_end_values'][y0 + t]
        r.update(vals)
        if t == 5:
            r['return_pct'] = {k: (out[k] if k in out else cal)['annual_return_pct'] for k in vals}
        elif years > 0:
            r['return_pct'] = {k: round(((v / price) ** (1 / years) - 1) * 100, 1) for k, v in vals.items()}
        return r

    today = {k: out[k]['value_today'] for k in out}
    if 'value_today' in cal:
        today['base_calibrated'] = cal['value_today']
    return {'today': today, 'two_year': row(2, p['years_to_horizon'] - 3), 'five_year': row(5, p['years_to_horizon']),
            'note': NOISE_NOTE}


def calibrated_diagnostics(p, base, out, cal_out):
    cal, eps0, pe_today, pe, m = _calibrated_inputs(p, base)
    y0 = p['base_year']
    changed = [y0 + 1 + i for i, (a, b) in enumerate(zip(m, base['m'])) if abs(a - b) > 1e-12]
    res = {'margin_changed': bool(changed), 'margin_years_changed': changed,
           'margin_floor_binds': bool(changed) and not cal.get('m'),
           'differs_from_base_only_by_multiple': not changed,
           'pe_held': round(pe, 2) if pe else None, 'pe_base_exit': base['pe'],
           'pe_held_to_base_exit': round(pe / base['pe'], 2) if pe and base['pe'] else None,
           'first_year_eps_operating': eps0,
           'first_year_eps_to_price_pct': round(eps0 / p['price'] * 100, 2)}
    reasons = []
    if res['pe_held_to_base_exit'] is not None and pe / base['pe'] > 1.5:
        reasons.append(f'the held multiple {pe:.1f} is {pe / base["pe"]:.2f}x the base exit multiple {base["pe"]:g} (above 1.5x)')
    if 'pe' not in cal and 0 < eps0 < 0.02 * p['price']:   # no positive EPS: no multiple to hold (the lens says so in its note)
        reasons.append(f'first-year operating EPS {eps0:g} is {eps0 / p["price"] * 100:.1f}% of the price (under 2%): '
                       'today\'s multiple may be inflated by depressed earnings')
    res['inflated_multiple_warning'] = bool(reasons)
    res['warning_reasons'] = reasons
    if 'value_today' in cal_out:
        ours = out['base']
        only_pe, d_pe = run_scenario(p, dict(base, pe=pe), detail=True)
        _, d_ours = run_scenario(p, base, detail=True)
        _, d_cal = run_scenario(p, dict(base, pe=pe, m=m), detail=True)
        res['gap_to_base'] = {
            'value_today': round(cal_out['value_today'] - ours['value_today'], 2),
            'value_today_from_multiple': round(d_pe['today'] - d_ours['today'], 2),
            'value_today_from_margin': round(d_cal['today'] - d_pe['today'], 2),
            'return_pts': round(cal_out['annual_return_pct'] - ours['annual_return_pct'], 1),
            'return_pts_from_multiple': round((d_pe['ret'] - d_ours['ret']) * 100, 1),
            'return_pts_from_margin': round((d_cal['ret'] - d_pe['ret']) * 100, 1)}
    if pe is None:
        res['reading'] = 'no positive first-year EPS: there is no multiple to hold and no calibrated lens; set "calibrated.pe"'
    elif reasons:
        res['reading'] = 'the held multiple looks inflated: the calibrated lens is not a second opinion here; set "calibrated.pe"'
    elif not changed:
        res['reading'] = 'the margin floor does not bind: the two lenses differ only by the exit multiple'
    else:
        res['reading'] = 'the lenses differ by the exit multiple and by the margin'
    return res


def funding(p, scenarios):
    y0, res = p['base_year'], {}
    for k, d in scenarios.items():
        _, det = run_scenario(p, d, detail=True)
        cash, nopat = det['cash'], det['nopat']
        i = min(range(len(cash)), key=lambda j: cash[j])
        low = cash[i]
        profit = nopat[max(i - 1, 0)]   # the low-point year's after-tax operating profit (base year-end: the first forecast year)
        net_debt = max(-low, 0.0)
        outside = max(min(cash[0], 0.0) - low, 0.0)   # borrowing beyond today's balance sheet
        reasons = []
        if net_debt > 0 and (profit <= 0 or net_debt > 3 * profit):
            reasons.append(f'net debt {net_debt:,.0f}M at the low point ({y0 + i}) is above 3x that year\'s after-tax operating '
                           f'profit ({profit:,.0f}M)')
        if outside > 0.5:
            size = f' ({outside / profit:.1f}x that year\'s after-tax operating profit)' if profit > 0 else ''
            reasons.append(f'the path needs {outside:,.0f}M of outside money by {y0 + i}{size} that is not in shares or net '
                           'cash: the model carries it as debt at debt_rate and does not check that it can be raised')
        row = {'net_cash_m': {y0 + j: round(c) for j, c in enumerate(cash)},
               'fcf_m': {y0 + 1 + j: round(x) for j, x in enumerate(det['fcf'])},
               'low_point_m': round(low), 'low_point_year': y0 + i,
               'low_year_nopat_m': round(profit), 'low_year_nopat_year': y0 + max(i, 1),
               'net_debt_to_nopat_at_low': round(net_debt / profit, 2) if net_debt > 0 and profit > 0 else None,
               'outside_money_m': round(outside),
               'outside_money_to_nopat': round(outside / profit, 2) if outside > 0.5 and profit > 0 else None,
               'shares_m': det['shares_m'],
               'flag': bool(reasons), 'reasons': reasons}
        if det['val_raw'][0] <= 0:
            row['equity_wiped'] = True   # value today is zero or less: say 'equity wiped', never a negative price
        wiped = [y0 + t for t in range(1, 6) if det['val_raw'][t] <= 0]
        if wiped:
            row['equity_wiped_year_ends'] = wiped
        res[k] = row
    return {'note': ('the model\'s own path: net cash at face at the base year-end, plus each year\'s free cash flow and the '
                     'after-tax interest on the balance; shares are fixed in each scenario'),
            'scenarios': res}


SENSITIVITY_STEPS = (
    ('operating margin', '1 point every year', 'm', 0.01),
    ('revenue growth', '1 point every year', 'g', 0.01),
    ('exit P/E', '2 turns', 'pe', 2.0),
    ('beta', '0.1', 'beta', 0.1),
    ('free-cash-flow conversion', '5 points every year', 'conv', 0.05),
)


def sensitivity(p, base):
    r0, d0 = run_scenario(p, base, detail=True)
    floored = d0['val_raw'][0] <= 0

    def side(key, step):
        if key == 'beta':
            r, det = run_scenario(dict(p, beta=p['beta'] + step), base, detail=True)
        elif key == 'pe':
            r, det = run_scenario(p, dict(base, pe=base['pe'] + step), detail=True)
        else:
            r, det = run_scenario(p, dict(base, **{key: [x + step for x in base[key]]}), detail=True)
        dv = det['today'] - d0['today']
        return {'value_today': r['value_today'], 'd_value': round(dv, 2),
                'd_value_pct': None if floored else round(dv / d0['today'] * 100, 1),
                'annual_return_pct': r['annual_return_pct'], 'd_return_pts': round((det['ret'] - d0['ret']) * 100, 2)}

    rows = []
    for name, change, key, step in SENSITIVITY_STEPS:
        down, up = side(key, -step), side(key, step)
        rows.append({'input': name, 'change': '+/- ' + change, 'down': down, 'up': up,
                     'abs_effect_value': max(abs(down['d_value']), abs(up['d_value']))})
    rows.sort(key=lambda r: -r['abs_effect_value'])
    return {'note': ('one input changed at a time on the base case, the others held: this is the model\'s own arithmetic '
                     '(what moves the value most), not a forecast and not a probability'),
            'base': {'value_today': r0['value_today'], 'annual_return_pct': r0['annual_return_pct']},
            'rows': rows, 'moves_most': rows[0]['input']}


def reverse_reading(p, base, res):
    """What the price needs: on the base margin path (the existing keys) and at a margin held flat at the first year."""
    y0, price = p['base_year'], p['price']
    tol = max(0.02, 0.005 * price)
    flat = dict(base, m=[base['m'][0]] * len(base['m']))
    d = _reverse_growth(p, flat)
    r = run_scenario(p, d)
    as_modelled = _reverse_growth(p, base)
    return {
        'base_growth_pct': [round(g * 100, 1) for g in base['g']],
        'base_end_revenue_m': run_scenario(p, base)['revenue_m'][y0 + 5],
        'base_margin_path': {'growth_pct': res['market_implied_growth_pct'],
                             'end_revenue_m': res['market_implied_end_revenue_m'],
                             'solved': abs(run_scenario(p, as_modelled)['value_today'] - price) <= tol},
        'flat_margin': {'margin_held': base['m'][0], 'growth_pct': [round(g * 100, 1) for g in d['g']],
                        'end_revenue_m': r['revenue_m'][y0 + 5], 'solved': abs(r['value_today'] - price) <= tol,
                        'value_today_at_base_growth': run_scenario(p, flat)['value_today']},
        'note': ('the first (guided) year is held and the later years are scaled until the value today equals the price; '
                 'solved false = no scaling between -1x and 4x reaches the price. A reading of the price, not a forecast'),
    }


def collect_warnings(p, res):
    w = []
    lens = res['two_lens']
    if lens.get('agree') is False:
        w.append(f'lenses disagree (ours {lens["ours"]["band"]}, calibrated {lens["calibrated"]["band"]}): hold, or stage '
                 'the trade around the next report')
    if lens['calibrated'].get('note'):
        w.append('calibrated lens: ' + lens['calibrated']['note'])
    w += ['calibrated lens: ' + x for x in res['calibrated_diagnostics']['warning_reasons']]
    for k, f in res['funding']['scenarios'].items():
        if f.get('equity_wiped'):
            w.append(f'{k}: equity wiped (value today is zero or less; the scenario needs a restructuring the model does not show)')
        elif f.get('equity_wiped_year_ends'):
            w.append(f'{k}: equity wiped at the year-ends {f["equity_wiped_year_ends"]}')
        w += [f'{k} funding: {x}' for x in f['reasons']]
    rr = res['buy_test'].get('rerun')
    if rr and rr['lower_than_stated']:
        w.append(f'buy test: at the stated highest passing price {res["buy_test"]["max_price_passing"]:,.2f} a rerun would hold a '
                 f'lower multiple; with the multiple of its own price held the highest passing price is '
                 f'{rr["max_price_passing"]:,.2f} (binding leg: {rr["binding_leg"]})')
    for key, label in (('base_margin_path', 'on the base margin path'), ('flat_margin', 'at a flat margin')):
        if not res['reverse_reading'][key]['solved']:
            why = ' (the first-year margin is not positive)' if key == 'flat_margin' and p['scenarios']['base']['m'][0] <= 0 else ''
            w.append(f'reverse reading {label}: no growth path between -1x and 4x of the base reaches the price{why}')
    return w


def add_reporting(p, res):
    """New keys only: every key that existed before keeps its value."""
    out, base, bands = res['scenarios'], p['scenarios']['base'], p.get('bands')
    cal = res['two_lens']['calibrated']
    res['price_ladder'] = price_ladder(p, out, cal, bands)
    fund = funding(p, p['scenarios'])
    res['buy_test'] = buy_test(p, out, cal, base, fund, bands)
    res['ranges'] = ranges(p, out, cal)
    res['calibrated_diagnostics'] = calibrated_diagnostics(p, base, out, cal)
    res['funding'] = fund
    res['sensitivity'] = sensitivity(p, base)
    res['reverse_reading'] = reverse_reading(p, base, res)
    res['warnings'] = collect_warnings(p, res)
    return res


def _fmt(x, width=10, dec=2):
    return f'{"n/a":>{width}}' if x is None else f'{x + 0.0:>{width},.{dec}f}'


def summary(p, res):
    """A short plain table of the result."""
    rg, lad, bt = res['ranges'], res['price_ladder'], res['buy_test']
    cols = [k for k in ('bear', 'base', 'bull', 'base_calibrated') if k in rg['today']]
    wiped = res['funding']['scenarios']

    def cell(k, v, today=False, year=None):
        f = wiped.get(k, {})
        if (today and f.get('equity_wiped')) or (year and year in f.get('equity_wiped_year_ends', [])):
            return f'{"wiped":>12}'
        return _fmt(v, 12)

    L = [f'{p.get("ticker", "?")}  as of {p.get("as_of", "?")}  price {p["price"]:,.2f}   '
         '(conditional values on each path, not price forecasts)', '',
         f'{"":<24}' + ''.join(f'{("base (cal.)" if k == "base_calibrated" else k):>12}' for k in cols),
         f'{"value today":<24}' + ''.join(cell(k, rg['today'][k], today=True) for k in cols)]
    for key in ('two_year', 'five_year'):
        r = rg[key]
        L.append(f'{"end " + str(r["year"]) + " (" + format(r["years_from_today"], ".1f") + "y)":<24}'
                 + ''.join(cell(k, r[k], year=r['year']) for k in cols))
        if 'return_pct' in r:
            L.append(f'{"  return % a year":<24}' + ''.join(_fmt(r['return_pct'][k], 12, 1) for k in cols))
    L += [f'{"index % a year":<24}{res["index_return_pct"]:>12,.1f}   weighted return {res["weighted_return_pct"]:.1f}% a year '
          '(subjective probabilities)', 'Note: ' + rg['note'] + '.', '',
          f'Price ladder: price today at which the base return to end {lad["horizon_year"]} equals each hurdle',
          f'{"  hurdle":<58}{"ours":>10}{"calibrated":>12}']
    for rung in lad['rungs'].values():
        L.append(f'  {rung["label"]:<56}{_fmt(rung["ours"])}{_fmt(rung["calibrated"], 12)}')
    L += ['',
          f'Buy test: {"PASS" if bt["pass"] else "FAIL"}   ({bt["rule"]})',
          f'{"  leg":<20}{"return %":>10}{"threshold %":>13}{"pass":>7}{"max price":>12}']
    for k, leg in bt['legs'].items():
        L.append(f'  {k:<18}{_fmt(leg["return_pct"], 10, 1)}{leg["threshold_pct"]:>13,.1f}'
                 f'{("n/a" if leg["pass"] is None else "yes" if leg["pass"] else "no"):>7}{_fmt(leg["max_price"], 12)}')
    if bt.get('no_price_passes'):
        L.append('  ' + bt['no_price_passes'])
    elif 'max_price_passing' in bt:
        L.append(f'  highest price at which every leg passes: {bt["max_price_passing"]:,.2f} ({bt["max_price_vs_price_pct"]:+.1f}% '
                 f'vs today), binding leg: {bt["binding_leg"]}')
    if bt.get('rerun', {}).get('lower_than_stated'):
        L.append(f'  on a rerun at that price (its own multiple held): {bt["rerun"]["max_price_passing"]:,.2f}, binding leg: '
                 f'{bt["rerun"]["binding_leg"]}')
    L += ['', 'What moves the base value most (one input at a time; model arithmetic, not a forecast)']
    for i, r in enumerate(res['sensitivity']['rows'][:3], 1):
        dn, up = r['down'], r['up']
        pct = '' if dn['d_value_pct'] is None else f' ({dn["d_value_pct"]:+.1f}% / {up["d_value_pct"]:+.1f}%)'
        L.append(f'  {i}. {r["input"]} {r["change"]}: value today {dn["d_value"]:+,.2f} / {up["d_value"]:+,.2f}{pct}, '
                 f'5-year return {dn["d_return_pts"]:+.2f} / {up["d_return_pts"]:+.2f} points')
    rv, cd = res['reverse_reading'], res['calibrated_diagnostics']
    L += ['', f'Price needs growth % (first year held): {rv["base_margin_path"]["growth_pct"]} on the base margin path; '
          f'{rv["flat_margin"]["growth_pct"]} at a flat {rv["flat_margin"]["margin_held"] * 100:g}% margin; base '
          f'{rv["base_growth_pct"]}',
          f'Calibrated lens: multiple held {_fmt(cd["pe_held"], 0).strip()} vs base exit {cd["pe_base_exit"]:g} '
          f'({_fmt(cd["pe_held_to_base_exit"], 0).strip()}{"" if cd["pe_held_to_base_exit"] is None else "x"}); {cd["reading"]}',
          'Lowest net cash ($M): ' + '; '.join(f'{k} {f["low_point_m"]:,} in {f["low_point_year"]}'
                                              for k, f in res['funding']['scenarios'].items()),
          '', 'Warnings:' if res['warnings'] else 'Warnings: none']
    L += ['  - ' + x for x in res['warnings']]
    return '\n'.join(L)



def _reverse_growth(p, base):
    lo, hi = -1.0, 4.0
    for _ in range(80):
        f = (lo + hi) / 2
        d = dict(base, g=[base['g'][0]] + [g * f for g in base['g'][1:]])
        if run_scenario(p, d)['value_today'] < p['price']:
            lo = f
        else:
            hi = f
    return dict(base, g=[base['g'][0]] + [g * f for g in base['g'][1:]])


def run(p):
    out = {k: run_scenario(p, d) for k, d in p['scenarios'].items()}
    pr = p['probabilities']
    last = str(p['base_year'] + 5)
    w_today = sum(pr[k] * out[k]['value_today'] for k in out)
    w_end = sum(pr[k] * out[k]['year_end_values'][int(last)] for k in out)
    base = p['scenarios']['base']
    res = {
        'scenarios': out,
        'weighted_value_today': round(w_today, 2),
        'weighted_end_value': round(w_end, 2),
        'weighted_return_pct': round(((w_end / p['price']) ** (1 / p['years_to_horizon']) - 1) * 100, 1),
        'index_return_pct': round((p['rf'] + p['erp']) * 100, 1),
        'buy_price': round(out['base']['year_end_values'][int(last)] / (1 + p['buy_return']) ** p['years_to_horizon'], 2),
        'index_parity_price': round(out['base']['year_end_values'][int(last)] / (1 + p['rf'] + p['erp']) ** p['years_to_horizon'], 2),
    }
    # reverse DCF: hold the first (guided) year, scale later growth until value today = price
    d = _reverse_growth(p, base)
    res['market_implied_growth_pct'] = [round(g * 100, 1) for g in d['g']]
    res['market_implied_end_revenue_m'] = run_scenario(p, d)['revenue_m'][p['base_year'] + 5]
    sens = {}
    for b in (p['beta'] - 0.3, p['beta'] + 0.3):
        sens[f'beta {b:.2f}'] = run_scenario(dict(p, beta=b), base)['value_today']
    for pe in (base['pe'] - 5, base['pe'] + 5):
        r = run_scenario(p, dict(base, pe=pe))
        sens[f'exit P/E {pe:g}'] = [r['value_today'], r['annual_return_pct']]
    res['sensitivity_base'] = sens
    cal = calibrated_lens(p, base)
    bands = p.get('bands')
    ours_ret = out['base']['annual_return_pct']
    lens = {'ours': {'value_today': out['base']['value_today'], 'annual_return_pct': ours_ret, 'band': band(ours_ret, bands),
                     'pe_exit': base['pe']}, 'calibrated': cal}
    if 'annual_return_pct' in cal:
        cal['band'] = band(cal['annual_return_pct'], bands)
        lens['agree'] = cal['band'] == lens['ours']['band']
        lens['rule'] = ('both lenses in the same band: act on it' if lens['agree'] else
                        'lenses disagree: hold, or stage the trade around the next report')
    res['two_lens'] = lens
    return add_reporting(p, res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('params')
    ap.add_argument('--out')
    ap.add_argument('--summary', action='store_true', help='print a short plain table instead of the JSON')
    a = ap.parse_args()
    p = json.load(open(a.params))
    res = run(p)
    txt = json.dumps(res, indent=1)
    if a.out:
        open(a.out, 'w').write(txt)
    print(summary(p, res) if a.summary else txt)


if __name__ == '__main__':
    main()
