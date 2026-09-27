"""Established forensic and quality scorecards over reported history.

- Piotroski (2000) F-score: 9 binary signals of improving financial strength.
- Altman (1968) Z for public manufacturers; Altman Z'' (1995) for non-manufacturers.
- Beneish (1999) 8-variable M-score for earnings-manipulation risk.
- ROIC and incremental ROIC versus a cost-of-capital reference (Mauboussin/McKinsey:
  returns tend to fade toward the cost of capital; high ROIC persists more than growth).
- Rule of 40 for software/platform (revenue growth + free-cash margin).
- Base-rate check of forecast growth (McKinsey: >20% real growers typically slow to
  ~8% within five years; most "supergrowers" cannot sustain it).

Missing inputs make a signal 'unavailable', never zero or guessed. Scores are
screens that direct research; they are not verdicts, verified facts or return odds.
"""
from .history import cagr, span_years
from .finance import number

COST_OF_CAPITAL_REFERENCE = .08


def _get(row, *keys):
    vals = [row.get(k) for k in keys]
    return None if any(v is None for v in vals) else vals


def _debt(row):
    if row.get('total_debt') is not None:
        return row['total_debt']
    return row.get('long_term_debt')


def piotroski(rows):
    if len(rows) < 2:
        return {'status': 'unavailable', 'reason': 'needs two fiscal years'}
    cur, prev = rows[-1], rows[-2]
    base = rows[-3] if len(rows) >= 3 else None
    signals = {}
    def roa(r, assets):
        return r['net_income'] / assets if r.get('net_income') is not None and assets else None
    ta_begin = prev.get('total_assets')
    ta_prev_begin = base.get('total_assets') if base else None
    roa_cur = roa(cur, ta_begin)
    roa_prev = roa(prev, ta_prev_begin)
    approx = False
    if roa_prev is None and prev.get('total_assets'):
        roa_prev, approx = roa(prev, prev['total_assets']), True
    signals['positive_net_income_roa'] = None if roa_cur is None else roa_cur > 0
    signals['positive_operating_cash_flow'] = None if cur.get('operating_cash_flow') is None else cur['operating_cash_flow'] > 0
    signals['improving_roa'] = None if roa_cur is None or roa_prev is None else roa_cur > roa_prev
    signals['cash_flow_exceeds_net_income'] = None if _get(cur, 'operating_cash_flow', 'net_income') is None else cur['operating_cash_flow'] > cur['net_income']
    lev = lambda r: _debt(r) / r['total_assets'] if _debt(r) is not None and r.get('total_assets') else None
    signals['lower_leverage'] = None if lev(cur) is None or lev(prev) is None else lev(cur) <= lev(prev)
    cr = lambda r: r['current_assets'] / r['current_liabilities'] if _get(r, 'current_assets', 'current_liabilities') and r['current_liabilities'] > 0 else None
    signals['higher_current_ratio'] = None if cr(cur) is None or cr(prev) is None else cr(cur) > cr(prev)
    signals['no_new_shares'] = None if _get(cur, 'shares_diluted') is None or _get(prev, 'shares_diluted') is None else cur['shares_diluted'] <= prev['shares_diluted'] * 1.005
    gm = lambda r: r['gross_profit'] / r['revenue'] if r.get('gross_profit') is not None and r['revenue'] > 0 else None
    signals['higher_gross_margin'] = None if gm(cur) is None or gm(prev) is None else gm(cur) > gm(prev)
    turn = lambda r, a: r['revenue'] / a if a else None
    t_cur, t_prev = turn(cur, ta_begin), turn(prev, ta_prev_begin or prev.get('total_assets'))
    signals['higher_asset_turnover'] = None if t_cur is None or t_prev is None else t_cur > t_prev
    known = [v for v in signals.values() if v is not None]
    if len(known) < 5:
        return {'status': 'unavailable', 'reason': 'fewer than 5 of 9 signals computable', 'signals': signals}
    score = sum(known)
    share = score / len(known)
    zone = 'strong' if share >= .78 else ('weak' if share <= .33 else 'mixed')
    return {'status': 'computed', 'score': score, 'out_of': len(known), 'zone': zone, 'signals': signals,
            'approximation': approx, 'reference': 'Piotroski 2000: 8–9 strong, 0–2 weak; developed on value (high book-to-market) stocks.'}


def altman(rows, archetype, market_cap=None):
    r = rows[-1]
    need = _get(r, 'total_assets', 'current_assets', 'current_liabilities', 'retained_earnings', 'operating_income', 'total_liabilities')
    if need is None or r['total_assets'] <= 0 or r['total_liabilities'] <= 0:
        return {'status': 'unavailable', 'reason': 'needs total/current assets and liabilities, retained earnings, operating income'}
    ta = r['total_assets']
    x1 = (r['current_assets'] - r['current_liabilities']) / ta
    x2 = r['retained_earnings'] / ta
    x3 = r['operating_income'] / ta
    manufacturer = archetype in {'industrial', 'consumer', 'commodity'}
    if manufacturer and market_cap:
        z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + .6 * market_cap / r['total_liabilities'] + 1.0 * r['revenue'] / ta
        model, safe, distress = 'Z (1968, public manufacturers)', 2.99, 1.81
    else:
        if r.get('equity') is None:
            return {'status': 'unavailable', 'reason': "Z'' needs book equity"}
        z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * r['equity'] / r['total_liabilities']
        model, safe, distress = "Z'' (1995, non-manufacturers/emerging markets)", 2.6, 1.1
    zone = 'safe' if z > safe else ('distress' if z < distress else 'grey')
    return {'status': 'computed', 'z': z, 'model': model, 'zone': zone, 'safe_above': safe, 'distress_below': distress,
            'reference': 'Altman distress screen; weak for banks, very young firms and asset-light firms with large buybacks (negative retained earnings).'}


def beneish(rows):
    if len(rows) < 2:
        return {'status': 'unavailable', 'reason': 'needs two fiscal years'}
    c, p = rows[-1], rows[-2]
    keys = ('receivables', 'gross_profit', 'current_assets', 'ppe', 'total_assets', 'depreciation', 'sga',
            'current_liabilities', 'net_income', 'operating_cash_flow')
    if any(c.get(k) is None or p.get(k) is None for k in keys) or _debt(c) is None or _debt(p) is None:
        return {'status': 'unavailable', 'reason': 'needs receivables, gross profit, current assets, PP&E, total assets, depreciation, SG&A, current liabilities, debt, net income, operating cash flow for two years'}
    try:
        dsri = (c['receivables'] / c['revenue']) / (p['receivables'] / p['revenue'])
        gmi = (p['gross_profit'] / p['revenue']) / (c['gross_profit'] / c['revenue'])
        aqi = (1 - (c['current_assets'] + c['ppe']) / c['total_assets']) / (1 - (p['current_assets'] + p['ppe']) / p['total_assets'])
        sgi = c['revenue'] / p['revenue']
        depi = (p['depreciation'] / (p['depreciation'] + p['ppe'])) / (c['depreciation'] / (c['depreciation'] + c['ppe']))
        sgai = (c['sga'] / c['revenue']) / (p['sga'] / p['revenue'])
        lvgi = ((c['current_liabilities'] + _debt(c)) / c['total_assets']) / ((p['current_liabilities'] + _debt(p)) / p['total_assets'])
        tata = (c['net_income'] - c['operating_cash_flow']) / c['total_assets']
    except ZeroDivisionError:
        return {'status': 'unavailable', 'reason': 'a ratio denominator is zero'}
    m = (-4.84 + .92 * dsri + .528 * gmi + .404 * aqi + .892 * sgi + .115 * depi - .172 * sgai + 4.679 * tata - .327 * lvgi)
    zone = 'likely_manipulator' if m > -1.78 else ('watch' if m > -2.22 else 'unlikely')
    return {'status': 'computed', 'm': m, 'zone': zone,
            'components': {'DSRI': dsri, 'GMI': gmi, 'AQI': aqi, 'SGI': sgi, 'DEPI': depi, 'SGAI': sgai, 'LVGI': lvgi, 'TATA': tata},
            'reference': 'Beneish 1999: above −1.78 flags manipulation risk (−2.22 is a common stricter screen). Fast growers score high on SGI by construction — investigate, do not accuse.'}


def roic(rows):
    def nopat(r):
        if r.get('operating_income') is None:
            return None
        rate = .21
        if _get(r, 'income_tax', 'pretax_income') and r['pretax_income'] > 0:
            rate = min(max(r['income_tax'] / r['pretax_income'], 0), .35)
        return r['operating_income'] * (1 - rate)
    def invested(r):
        if r.get('equity') is None or _debt(r) is None or r.get('cash') is None:
            return None
        return r['equity'] + _debt(r) - r['cash']
    series = []
    for i, r in enumerate(rows):
        ic_end, ic_begin = invested(r), invested(rows[i - 1]) if i else None
        ic = (ic_end + ic_begin) / 2 if ic_end is not None and ic_begin is not None else ic_end
        n = nopat(r)
        series.append({'fiscal_year': r['fiscal_year'], 'nopat': n, 'invested_capital': ic_end,
                       'roic': n / ic if n is not None and ic and ic > 0 else None})
    known = [s for s in series if s['roic'] is not None]
    if not known:
        return {'status': 'unavailable', 'reason': 'needs operating income, equity, debt and cash per year'}
    incremental = None
    first = next((s for s in series if s['nopat'] is not None and s['invested_capital'] is not None), None)
    last = series[-1]
    if first and last is not first and last['nopat'] is not None and last['invested_capital'] is not None:
        dic = last['invested_capital'] - first['invested_capital']
        if dic > 0:
            incremental = (last['nopat'] - first['nopat']) / dic
    return {'status': 'computed', 'roic_last': known[-1]['roic'], 'roic_average': sum(s['roic'] for s in known) / len(known),
            'incremental_roic': incremental, 'series': series, 'cost_of_capital_reference': COST_OF_CAPITAL_REFERENCE,
            'reference': 'ROIC = after-tax operating income / (equity + debt − cash). Returns tend to fade toward the cost of capital; persistent excess returns need a moat. Negative or tiny invested capital makes ROIC not meaningful.'}


def rule_of_40(rows, archetype):
    if archetype not in {'software', 'platform'}:
        return {'status': 'not_applicable'}
    if len(rows) < 2 or rows[-2]['revenue'] <= 0:
        return {'status': 'unavailable', 'reason': 'needs two years of revenue'}
    r = rows[-1]
    if _get(r, 'operating_cash_flow', 'capex') is None or r['revenue'] <= 0:
        return {'status': 'unavailable', 'reason': 'needs operating cash flow and capex'}
    growth = r['revenue'] / rows[-2]['revenue'] - 1
    fcf_margin = (r['operating_cash_flow'] - r['capex']) / r['revenue']
    score = growth + fcf_margin
    return {'status': 'computed', 'score': score, 'growth': growth, 'fcf_margin': fcf_margin,
            'reference': 'Revenue growth + FCF margin ≥ 40% is the classic bar; recent public-SaaS medians sit near 25–30% and only about a quarter clear 40%.'}


def forecast_base_rate(rows, valuation_result):
    """Does the base scenario ask for growth that history and base rates rarely deliver?"""
    if not valuation_result or not rows:
        return None
    base = next((s for s in valuation_result.get('scenarios', []) if s['name'] == 'base'), None)
    if not base or not base.get('forecast') or 'revenue' not in base['forecast'][0]:
        return None
    last = rows[-1]
    start_rev = last['revenue']
    out = []
    from datetime import date
    for row in base['forecast']:
        yrs = (date.fromisoformat(row['end']) - date.fromisoformat(last['period_end'])).days / 365.25
        dur = (date.fromisoformat(row['end']) - date.fromisoformat(row['start'])).days / 365.25
        if yrs >= 4.5 and dur > .9:
            g = cagr(start_rev, row['revenue'] / dur, yrs)
            out.append({'through': row['end'], 'implied_revenue_cagr': g, 'years': yrs})
            break
    if not out:
        return None
    g = out[0]['implied_revenue_cagr']
    hist = cagr(rows[0]['revenue'], start_rev, span_years(rows)) if len(rows) > 1 else None
    flags = []
    if g is not None and g > .2:
        flags.append({'key': 'rare_sustained_growth', 'he': 'התרחיש הבסיסי מניח צמיחה של יותר מ-20% בשנה לחמש שנים. זה נדיר מאוד: רוב החברות שצומחות כך מאטות תוך כמה שנים. זה צריך הוכחה חזקה.'})
    if g is not None and hist is not None and g > hist + .1:
        flags.append({'key': 'above_own_history', 'he': 'התרחיש הבסיסי מניח צמיחה מהירה בהרבה ממה שהחברה עשתה בעבר. צריך סיבה מוכחת — חוזים, קיבולת חדשה, מוצר חדש.'})
    return {'implied': out[0], 'historical_cagr': hist, 'flags': flags,
            'reference': 'McKinsey (Koller et al.): companies growing >20% real typically slow to ~8% within five years and ~5% within ten; ~85% of supergrowers fail to sustain growth.'}


def scorecards(rows, archetype, market_cap=None):
    if market_cap is not None:
        number(market_cap, 'market_cap', 0)
    return {'piotroski': piotroski(rows), 'altman': altman(rows, archetype, market_cap), 'beneish': beneish(rows),
            'roic': roic(rows), 'rule_of_40': rule_of_40(rows, archetype)}
