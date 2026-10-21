"""Quarterly report review: the fast read a good analyst does on results day,
made deterministic and auditable.

Lessons taken from comparing human earnings-day write-ups with the engine:
- remove one-off gains AND add back one-off charges symmetrically; recurring
  costs stay; no default tax rate on pre-tax items;
- opex growth versus revenue growth, reported and ex-unusual items;
- restricted cash, insurance reserves and customer funds are not spendable liquidity;
- cumulative KPIs ("products ever opened") are not activity evidence;
- price reacts to results versus expectations, so guidance is compared with prior
  guidance and an attributed consensus, never judged on its own;
- a peak-cycle quarter times four is a run rate, not normalized earnings;
- a dual-listed line (ADR) can trade at a premium to the same economic claim;
- banks: price / tangible book only means something against ROTE and cost of equity;
- EPS x exit multiple is a dated sensitivity with an implied annual return, not value.

Inputs are absolute currency units in one stated currency. Nothing is fetched.
Output is research indication, not a trade instruction or a price forecast.
"""
from datetime import date
from hashlib import sha256
import json
from .finance import number
from . import __version__

LINES = ('revenue', 'gross_profit', 'operating_income', 'adjusted_ebitda', 'net_income',
         'operating_expenses', 'operating_cash_flow', 'capex', 'diluted_shares', 'sbc', 'buybacks')
KPI_BASES = {'active', 'flow', 'balance', 'cumulative'}
SEVERITY = {'high': 0, 'medium': 1, 'info': 2}


def _num(value, label, minimum=None):
    return None if value is None else number(value, label, minimum)


def _growth(a, b):
    return a / b - 1 if a is not None and b is not None and b > 0 else None


def _margin(a, rev):
    return a / rev if a is not None and rev is not None and rev > 0 else None


def _sources(item, label):
    ids = item.get('source_ids')
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise ValueError(f'{label}: source_ids required')
    return ids


def _lines(block, label):
    if block is None:
        return {}
    if not isinstance(block, dict) or set(block) - set(LINES):
        raise ValueError(f'{label}: unknown line; allowed: ' + ', '.join(LINES))
    out = {k: _num(v, f'{label}.{k}') for k, v in block.items()}
    for k in ('revenue', 'diluted_shares', 'capex', 'operating_expenses', 'sbc', 'buybacks'):
        if out.get(k) is not None and out[k] < 0:
            raise ValueError(f'{label}.{k}: expected non-negative (capex/buybacks as positive outflows)')
    return out


def _normalize(cur, one_offs, unusual, flags):
    """Symmetric after-tax normalization of net income. Recurring items are kept.
    Non-recurring unusual opex removed for operating leverage is added back here too,
    so a charge cannot be ignored in one view and kept in the other."""
    one_offs = list(one_offs) + [{**u, 'kind': 'charge', 'pretax': True, 'from_unusual_opex': True}
                                 for u in unusual if not u.get('recurring')]
    removed, kept = [], []
    adjustment = 0.0
    for i, o in enumerate(one_offs):
        label = f'one_offs[{i}]'
        _sources(o, label)
        if o.get('kind') not in {'gain', 'charge'}:
            raise ValueError(f'{label}.kind must be gain or charge')
        amount = number(o.get('amount'), label + '.amount', 0)
        if o.get('pretax', True):
            if o.get('tax_rate') is None:
                raise ValueError(f"{'unusual_opex item ' + repr(o.get('label')) if o.get('from_unusual_opex') else label}: "
                                 'pre-tax item needs an explicit tax_rate (no default)')
            after = amount * (1 - number(o['tax_rate'], label + '.tax_rate', 0, 1))
        else:
            after = amount
        row = {'label': o.get('label', label), 'kind': o['kind'], 'after_tax': after,
               'non_cash': bool(o.get('non_cash'))}
        if o.get('recurring'):
            kept.append({**row, 'why': 'Recurring or repeated items stay in earnings; never erase a recurring cost or income.'})
            continue
        adjustment += -after if o['kind'] == 'gain' else after
        removed.append(row)
    ni = cur.get('net_income')
    if ni is None:
        if removed:
            raise ValueError('one_offs need current.net_income to normalize')
        return None
    normalized = ni + adjustment
    shares = cur.get('diluted_shares')
    out = {'reported_net_income': ni, 'normalized_net_income': normalized, 'adjustment': adjustment,
           'removed': removed, 'kept': kept,
           'reported_eps': ni / shares if shares else None,
           'normalized_eps': normalized / shares if shares else None,
           'note': 'Gains removed and charges added back after tax, symmetrically. Normalization is not cash and not a forecast.'}
    if ni and abs(adjustment) > .25 * abs(ni):
        flags.append(('headline_distorted', 'high',
                      f'כ-{abs(adjustment) / abs(ni):.0%} מהרווח הנקי המדווח הוא אירוע חד-פעמי; השורה התחתונה המדווחת לא מייצגת.'))
    return out


def _leverage(cur, py, unusual, flags):
    rg = _growth(cur.get('revenue'), py.get('revenue'))
    og = _growth(cur.get('operating_expenses'), py.get('operating_expenses'))
    if rg is None or og is None:
        return None
    one_off = sum(number(u.get('amount'), f'unusual_opex[{i}].amount', 0)
                  for i, u in enumerate(unusual) if _sources(u, f'unusual_opex[{i}]') and not u.get('recurring'))
    clean = _growth(cur['operating_expenses'] - one_off, py['operating_expenses'])
    out = {'revenue_growth': rg, 'opex_growth_reported': og, 'opex_growth_ex_unusual': clean,
           'spread_reported': rg - og, 'spread_ex_unusual': rg - clean, 'unusual_removed': one_off}
    oi, poi = cur.get('operating_income'), py.get('operating_income')
    if oi is not None and poi is not None and cur['revenue'] != py['revenue']:
        out['incremental_operating_margin'] = (oi - poi) / (cur['revenue'] - py['revenue'])
    if rg - clean < -.02:
        flags.append(('negative_operating_leverage', 'medium',
                      'ההוצאות צומחות מהר מההכנסות גם אחרי ניקוי סעיפים חריגים; הצמיחה נקנית ביוקר.'))
    elif rg - clean > .05:
        flags.append(('operating_leverage', 'info', 'ההכנסות צומחות מהר מההוצאות (אחרי ניקוי חריגים): מינוף תפעולי.'))
    return out


def _cash(cur, norm, flags):
    ocf, capex = cur.get('operating_cash_flow'), cur.get('capex')
    if ocf is None or capex is None:
        return None
    fcf = ocf - capex
    out = {'operating_cash_flow': ocf, 'capex': capex, 'free_cash_flow': fcf,
           'fcf_margin': _margin(fcf, cur.get('revenue')), 'capex_to_ocf': capex / ocf if ocf > 0 else None,
           'definition': 'operating cash flow minus capex as supplied; check issuer FCF definition separately'}
    base = norm['normalized_net_income'] if norm else cur.get('net_income')
    if base and base > 0:
        out['fcf_to_normalized_net_income'] = fcf / base
        if fcf / base < .6:
            flags.append(('weak_cash_conversion', 'medium', 'התזרים החופשי נמוך בהרבה מהרווח המנורמל ברבעון; לבדוק הון חוזר והשקעות.'))
    if ocf > 0 and capex / ocf > .5:
        flags.append(('investment_phase', 'info',
                      'יותר ממחצית התזרים התפעולי הולך להשקעות; תזרים חופשי נמוך זמנית אינו בהכרח חולשה, אבל הוא תלוי בתשואה על ההשקעה.'))
    return out


def _liquidity(bal, flags):
    if not bal:
        return None
    allowed = {'cash', 'short_term_investments', 'restricted_cash', 'insurance_reserves',
               'customer_funds', 'debt', 'convertible_debt', 'source_ids'}
    if set(bal) - allowed:
        raise ValueError('balance: unknown field')
    _sources(bal, 'balance')
    v = {k: _num(bal.get(k), 'balance.' + k, 0) or 0.0 for k in allowed - {'source_ids'}}
    split = 'convertible_debt' in bal
    spendable = v['cash'] + v['short_term_investments']
    tied = v['restricted_cash'] + v['insurance_reserves'] + v['customer_funds']
    debt = v['debt'] + v['convertible_debt']
    if tied:
        flags.append(('restricted_not_spendable', 'info',
                      'מזומן מוגבל, עתודות ביטוח וכספי לקוחות מוצגים בנפרד: הם מגבים התחייבויות ואינם נזילות פנויה.'))
    return {'spendable_liquidity': spendable, 'restricted_or_held_for_others': tied, 'total_debt': debt,
            'net_cash': spendable - debt, 'convertible_share_of_debt': v['convertible_debt'] / debt if debt and split else None}


def _capital_return(cur, py, market, flags):
    out = {}
    if cur.get('diluted_shares') and py.get('diluted_shares'):
        out['diluted_share_change_yoy'] = cur['diluted_shares'] / py['diluted_shares'] - 1
        if out['diluted_share_change_yoy'] > .03:
            flags.append(('dilution', 'medium', f"מספר המניות המדולל עלה {out['diluted_share_change_yoy']:.1%} בשנה; חלק מהצמיחה לא שייך למחזיק הקיים."))
    if cur.get('buybacks') is not None and cur.get('sbc') is not None:
        out['buybacks_minus_sbc'] = cur['buybacks'] - cur['sbc']
    cap = market.get('market_cap') if market else None
    if cap and cur.get('buybacks') is not None:
        out['quarter_buyback_to_market_cap'] = cur['buybacks'] / cap
        out['annualized_buyback_yield_run_rate'] = 4 * cur['buybacks'] / cap
    return out or None


def _segments(segs, cur, flags):
    rows = []
    total = sum(number(s['profit'], 'segment profit') for s in segs if s.get('profit') is not None and s['profit'] > 0)
    for i, s in enumerate(segs):
        _sources(s, f'segments[{i}]')
        rev, prev = _num(s.get('revenue'), 'segment revenue', 0), _num(s.get('prior_revenue'), 'segment prior revenue', 0)
        p, pp = _num(s.get('profit'), 'segment profit'), _num(s.get('prior_profit'), 'segment prior profit')
        m, pm = _margin(p, rev), _margin(pp, prev)
        row = {'name': s['name'], 'revenue_growth': _growth(rev, prev), 'profit_growth': _growth(p, pp),
               'margin': m, 'margin_change': m - pm if m is not None and pm is not None else None,
               'share_of_positive_profit': p / total if p is not None and p > 0 and total else None,
               'profit_definition': s.get('profit_definition', 'unspecified')}
        if row['margin_change'] is not None and row['margin_change'] < -.03:
            flags.append(('segment_margin_compression', 'medium', f"שולי הרווח של {s['name']} נשחקו ב-{-100 * row['margin_change']:.0f} נקודות אחוז."))
        if row['revenue_growth'] is not None and row['revenue_growth'] < 0:
            flags.append(('segment_shrinking', 'medium', f"ההכנסות של {s['name']} מתכווצות."))
        rows.append(row)
    top = max((r for r in rows if r['share_of_positive_profit']), key=lambda r: r['share_of_positive_profit'], default=None)
    if top and top['share_of_positive_profit'] > .7 and len(rows) > 1:
        flags.append(('profit_concentration', 'medium', f"{top['name']} מייצר יותר מ-70% מהרווח המגזרי; הסיפור הרחב נשען בפועל על מגזר אחד."))
    return rows


def _kpis(kpis, flags):
    rows = []
    for i, k in enumerate(kpis):
        _sources(k, f'kpis[{i}]')
        if k.get('basis') not in KPI_BASES:
            raise ValueError(f'kpis[{i}].basis must be one of ' + ', '.join(sorted(KPI_BASES)))
        v, p = number(k['value'], 'kpi value'), _num(k.get('prior'), 'kpi prior')
        rows.append({'name': k['name'], 'value': v, 'growth': _growth(v, p), 'basis': k['basis']})
        if k['basis'] == 'cumulative':
            flags.append(('cumulative_kpi', 'medium',
                          f"המדד '{k['name']}' מצטבר (כולל מה שכבר נסגר/נפרע); הוא לא מוכיח פעילות נוכחית."))
    return rows


def _guidance(items, flags):
    rows = []
    for i, g in enumerate(items):
        label = f'guidance[{i}]'
        _sources(g, label)
        low, high = number(g['low'], label + '.low'), number(g['high'], label + '.high')
        if high < low:
            raise ValueError(label + ': high below low')
        mid = (low + high) / 2
        row = {'metric': g['metric'], 'period': g.get('period'), 'midpoint': mid}
        if g.get('prior_low') is not None and g.get('prior_high') is not None:
            prior = (number(g['prior_low'], label + '.prior_low') + number(g['prior_high'], label + '.prior_high')) / 2
            row['change_vs_prior_guidance'] = _growth(mid, prior) if prior > 0 else mid - prior
            row['direction'] = 'raised' if mid > prior * 1.005 else ('cut' if mid < prior * .995 else 'maintained')
        if g.get('comparable_actual') is not None:
            row['implied_growth'] = _growth(mid, number(g['comparable_actual'], label + '.comparable_actual'))
        if g.get('consensus') is not None:
            if not g.get('consensus_source_ids'):
                raise ValueError(label + ': consensus needs consensus_source_ids (attributed estimate)')
            c = number(g['consensus'], label + '.consensus')
            row['vs_consensus'] = _growth(mid, c) if c > 0 else mid - c
            if c > 0 and mid < c * .99:
                flags.append(('guide_below_consensus', 'medium',
                              f"תחזית {g['metric']} (אמצע הטווח) נמוכה מציפיות האנליסטים; מסביר ירידה גם אחרי רבעון טוב."))
        if row.get('direction') == 'maintained' and g.get('related_raised_metric'):
            flags.append(('reinvesting_upside', 'info',
                          f"{g['related_raised_metric']} הועלה אבל {g['metric']} נשאר: ההנהלה משקיעה את העודף ולא מעבירה אותו לשורה התחתונה."))
        rows.append(row)
    return rows


def _track(track, flags):
    if not track:
        return None
    surprises = []
    for i, t in enumerate(track):
        _sources(t, f'guidance_track[{i}]')
        if not t.get('same_basis', False):
            raise ValueError(f'guidance_track[{i}]: same_basis must confirm matching scope, accounting and period')
        mid = (number(t['low'], 'low') + number(t['high'], 'high')) / 2
        actual = number(t['actual'], 'actual')
        # A guided growth rate is compared in percentage points, an amount in relative terms.
        surprises.append(actual - mid if t.get('unit') == 'rate' else (_growth(actual, mid) if mid > 0 else None))
    s = [x for x in surprises if x is not None]
    beats = sum(1 for x in s if x > .005)
    out = {'periods': len(track), 'beats': beats, 'mean_surprise_vs_midpoint': sum(s) / len(s) if s else None,
           'pattern': 'conservative' if s and beats == len(s) else ('mixed' if beats else 'misses_or_in_line'),
           'small_sample': len(track) < 4}
    if out['pattern'] == 'conservative':
        flags.append(('conservative_guider', 'info',
                      f"ההנהלה עקפה את התחזית שלה ב-{beats}/{len(s)} תקופות" + (' (מדגם קטן)' if out['small_sample'] else '')
                      + '; התחזית מתנהגת כרצפה, אבל אין להניח עקיפה אוטומטית.'))
    return out


def _cycle(cur, history_margins, flags):
    om = _margin(cur.get('operating_income'), cur.get('revenue'))
    if om is None or not history_margins:
        return None
    hist = sorted(number(m, 'history_operating_margins', -10, 1) for m in history_margins)
    median = hist[len(hist) // 2]
    peak = om > max(hist) or om > median + .15
    if peak:
        flags.append(('peak_margin_run_rate', 'high',
                      f'שולי רווח תפעולי של {om:.0%} מול חציון היסטורי של {median:.0%}: רווח רבעוני כפול 4 הוא רווח שיא, לא רווח מנורמל לאורך מחזור.'))
    return {'operating_margin': om, 'historical_median': median, 'historical_max': hist[-1], 'above_history': peak}


def _market(m, cur, norm, cycle, flags):
    if not m:
        return None
    _sources(m, 'market')
    date.fromisoformat(m['price_as_of'])
    price = number(m['price'], 'market.price', 1e-12)
    out = {'price': price, 'price_as_of': m['price_as_of']}
    shares = _num(m.get('shares_outstanding'), 'market.shares_outstanding', 1)
    if shares:
        out['market_cap'] = price * shares
    eps = norm['normalized_eps'] if norm else None
    if eps:
        out['run_rate_eps'] = 4 * eps
        out['pe_on_run_rate'] = price / (4 * eps) if eps > 0 else None
        out['run_rate_basis'] = 'peak_quarter_x4' if cycle and cycle['above_history'] else 'quarter_x4'
    fwd = []
    for i, f in enumerate(m.get('forward_eps') or []):
        if f.get('origin') not in {'consensus', 'own_model'}:
            raise ValueError(f'forward_eps[{i}].origin must be consensus or own_model')
        _sources(f, f'forward_eps[{i}]')
        e = number(f['value'], 'forward eps')
        row = {'year': f['year'], 'eps': e, 'origin': f['origin'], 'pe': price / e if e > 0 else None}
        if f.get('growth') is not None and e > 0 and number(f['growth'], 'growth') > 0:
            row['peg'] = row['pe'] / (100 * f['growth'])
        fwd.append(row)
    if fwd:
        out['forward'] = fwd
    dual = m.get('other_listing')
    if dual:
        _sources(dual, 'other_listing')
        equivalent = number(dual['price_local'], 'price_local', 1e-12) / number(dual['local_per_quote_currency'], 'fx', 1e-12) \
            * number(dual['local_shares_per_unit'], 'local_shares_per_unit', 1e-12)
        out['other_listing'] = {'equivalent_price': equivalent, 'premium': price / equivalent - 1, 'name': dual.get('name')}
        if abs(price / equivalent - 1) > .1:
            flags.append(('listing_premium', 'high' if price / equivalent - 1 > .2 else 'medium',
                          f"קו המסחר הזה נסחר ב-{price / equivalent - 1:+.0%} מול אותה מניה בבורסת הבית: משלמים מחיר שונה על אותה זכות כלכלית. הפער יכול להיסגר."))
    return out


def _scenarios(m, price, flags):
    """EPS x exit multiple tables are dated sensitivities. Show implied annual returns."""
    rows = []
    for i, s in enumerate(m.get('multiple_scenarios') or []):
        _sources(s, f'multiple_scenarios[{i}]')
        years = (date.fromisoformat(s['target_date']) - date.fromisoformat(m['price_as_of'])).days / 365.25
        if years <= 0:
            raise ValueError('multiple_scenarios target_date must follow price_as_of')
        eps = number(s['eps'], 'scenario eps', 1e-12)
        for pe in s['multiples']:
            val = eps * number(pe, 'multiple', 1e-12)
            row = {'name': s.get('name'), 'target_date': s['target_date'], 'eps': eps, 'multiple': pe, 'value_at_date': val,
                   'implied_annual_return': (val / price) ** (1 / years) - 1}
            if s.get('cost_of_equity') is not None:
                row['discounted_today'] = val / (1 + number(s['cost_of_equity'], 'cost_of_equity', 0, 1)) ** years
            rows.append(row)
    if rows and not any(s.get('multiple_basis_source_ids') for s in m.get('multiple_scenarios') or []):
        flags.append(('unanchored_multiples', 'medium',
                      'המכפילים בתרחישים לא מעוגנים בהיסטוריה/עמיתים מתועדים; זו רגישות, לא שווי.'))
    return rows


def _bank(b, price, flags):
    if not b:
        return None
    _sources(b, 'bank')
    tbv = number(b['tangible_book_per_share'], 'tangible_book_per_share', 1e-12)
    ptbv = price / tbv
    out = {'price_to_tangible_book': ptbv}
    if b.get('rote') is not None and b.get('cost_of_equity') is not None and b.get('growth') is not None:
        rote, ke, g = (number(b[k], 'bank.' + k, -1, 1) for k in ('rote', 'cost_of_equity', 'growth'))
        if ke <= g:
            raise ValueError('bank: cost_of_equity must exceed long-run growth')
        out['justified_price_to_tangible_book'] = (rote - g) / (ke - g)
        out['rote_implied_by_price'] = g + ptbv * (ke - g)
        out['justified_price'] = tbv * out['justified_price_to_tangible_book']
        out['note'] = 'Steady-state (ROTE-g)/(COE-g); a check on what the price requires, not a full bank valuation.'
        if out['rote_implied_by_price'] > rote + .03:
            flags.append(('bank_price_requires_higher_rote', 'high',
                          f"המחיר מגלם תשואה על הון מוחשי של כ-{out['rote_implied_by_price']:.0%} מול {rote:.1%} בפועל: השוק כבר משלם על היעד ולא על המצב."))
    if b.get('capital_ratio') is not None and b.get('target_ratio') is not None and b.get('risk_weighted_assets') is not None:
        excess = (number(b['capital_ratio'], 'capital_ratio', 0, 1) - number(b['target_ratio'], 'target_ratio', 0, 1)) \
            * number(b['risk_weighted_assets'], 'risk_weighted_assets', 0)
        out['excess_capital_vs_target'] = excess
        if b.get('minimum_ratio') is not None:
            out['headroom_to_minimum'] = b['capital_ratio'] - number(b['minimum_ratio'], 'minimum_ratio', 0, 1)
    if b.get('fair_value_marks') is not None and b.get('equity'):
        out['fair_value_marks_to_equity'] = number(b['fair_value_marks'], 'fair_value_marks') / number(b['equity'], 'equity', 1e-12)
        if out['fair_value_marks_to_equity'] > .15:
            flags.append(('equity_from_marks', 'medium',
                          f"כ-{out['fair_value_marks_to_equity']:.0%} מההון העצמי הוא שערוך שווי הוגן ולא מזומן; תלוי בהנחות."))
    return out


def _sensitivities(items):
    rows = []
    for i, s in enumerate(items):
        _sources(s, f'sensitivities[{i}]')
        impact, base = number(s['annual_impact'], 'annual_impact'), _num(s.get('base_annual'), 'base_annual', 1e-12)
        rows.append({'driver': s['driver'], 'annual_impact': impact, 'share_of_base': impact / base if base else None,
                     'offset_growth_needed': -impact / base if base and impact < 0 else None})
    return rows


def _falsifiers(items, flags):
    rows = []
    for i, f in enumerate(items):
        if f.get('direction') not in {'below', 'above'}:
            raise ValueError(f'falsifiers[{i}].direction must be below or above')
        cur, thr = number(f['current'], 'falsifier current'), number(f['threshold'], 'falsifier threshold')
        broken = cur < thr if f['direction'] == 'below' else cur > thr
        prior = _num(f.get('prior'), 'falsifier prior')
        moving_toward = prior is not None and ((cur < prior) if f['direction'] == 'below' else (cur > prior))
        status = 'broken' if broken else ('weakening' if moving_toward else 'intact')
        rows.append({'id': f['id'], 'metric': f['metric'], 'current': cur, 'threshold': thr, 'status': status})
        if status == 'broken':
            flags.append(('thesis_falsifier_broken', 'high', f"תנאי ההפרכה '{f['metric']}' נחצה: התזה דורשת בחינה מחדש, לא הסבר."))
        elif status == 'weakening':
            flags.append(('thesis_falsifier_weakening', 'medium', f"'{f['metric']}' זז לכיוון סף ההפרכה."))
    return rows


def _currency(case):
    rc, cur = case.get('reporting_currency', case['currency']), case['currency']
    if rc == cur:
        return None
    fx = case.get('fx')
    if not fx:
        raise ValueError('reporting_currency differs from currency: supply fx {rate, quote, as_of, source_ids}')
    _sources(fx, 'fx')
    date.fromisoformat(fx['as_of'])
    return {'reporting_currency': rc, 'presented_in': cur, 'rate': number(fx['rate'], 'fx.rate', 1e-12),
            'quote': fx.get('quote'), 'as_of': fx['as_of'],
            'note': 'Values converted by the caller at one dated rate; USD growth mixes business and FX moves.'}


def _text(case, r):
    """One short Hebrew paragraph in the user's preferred style."""
    parts = [f"{case['company']} ({case['period_end']}):"]
    g = r['growth'].get('revenue_yoy')
    if g is not None:
        parts.append(f"ההכנסות צמחו {g:.0%} בשנה.")
    n = r.get('normalization')
    if n and n['removed']:
        parts.append(f"אחרי ניקוי חד-פעמיים הרווח הנקי הוא {n['normalized_net_income'] / 1e9:,.2f} מיליארד ולא {n['reported_net_income'] / 1e9:,.2f}.")
    lev = r.get('operating_leverage')
    if lev:
        parts.append('ההוצאות צומחות לאט מההכנסות.' if lev['spread_ex_unusual'] > 0 else 'ההוצאות צומחות מהר מההכנסות.')
    for g in r.get('guidance', []):
        if g.get('direction'):
            parts.append(f"תחזית {g['metric']}: {dict(raised='הועלתה', cut='הורדה', maintained='נשארה')[g['direction']]}"
                         + (f", {g['vs_consensus']:+.0%} מול הקונצנזוס" if g.get('vs_consensus') is not None else '') + '.')
            break
    high = [f for f in r['flags'] if f['severity'] == 'high']
    if high:
        parts.append('נקודות מרכזיות: ' + ' '.join(f['he'] for f in high[:2]))
    parts.append('המחיר זז לפי הפער מול הציפיות ולא לפי איכות הדוח בלבד; זו סקירת דוח ולא הערכת שווי או המלצה.')
    return ' '.join(parts)


def quarter_review(case):
    for k in ('company', 'period_end', 'as_of', 'currency'):
        if not case.get(k):
            raise ValueError(f'{k} required')
    if date.fromisoformat(case['period_end']) > date.fromisoformat(case['as_of']):
        raise ValueError('period_end after as_of')
    _sources(case, 'case')
    cur, py, pq = _lines(case.get('current'), 'current'), _lines(case.get('prior_year'), 'prior_year'), _lines(case.get('prior_quarter'), 'prior_quarter')
    if cur.get('revenue') is None:
        raise ValueError('current.revenue required')
    raw = []
    growth = {}
    for k in ('revenue', 'gross_profit', 'operating_income', 'adjusted_ebitda', 'operating_cash_flow'):
        growth[k + '_yoy'] = _growth(cur.get(k), py.get(k))
        growth[k + '_qoq'] = _growth(cur.get(k), pq.get(k))
    margins = {k: _margin(cur.get(k), cur['revenue']) for k in ('gross_profit', 'operating_income', 'adjusted_ebitda', 'net_income')}
    margins_prior = {k: _margin(py.get(k), py.get('revenue')) for k in margins}
    norm = _normalize(cur, case.get('one_offs') or [], case.get('unusual_opex') or [], raw)
    eg, og = _growth(cur.get('adjusted_ebitda'), py.get('adjusted_ebitda')), _growth(cur.get('operating_income'), py.get('operating_income'))
    if eg is not None and og is not None and eg - og > .15:
        raw.append(('adjusted_vs_reported_gap', 'medium',
                    f'EBITDA מתואם צמח {eg:.0%} מול רווח תפעולי מדווח {og:.0%}: לבדוק מה מוחרג (SBC, פחת, משפטי) ואם זה חוזר.'))
    cycle = _cycle(cur, case.get('history_operating_margins'), raw)
    market = _market(case.get('market'), cur, norm, cycle, raw)
    result = {
        'company': case['company'], 'period_end': case['period_end'], 'as_of': case['as_of'], 'currency': case['currency'],
        'currency_conversion': _currency(case),
        'growth': {k: v for k, v in growth.items() if v is not None},
        'margins': {k: v for k, v in margins.items() if v is not None},
        'margin_change_yoy': {k: margins[k] - margins_prior[k] for k in margins if margins[k] is not None and margins_prior[k] is not None},
        'normalization': norm,
        'operating_leverage': _leverage(cur, py, case.get('unusual_opex') or [], raw),
        'cash': _cash(cur, norm, raw),
        'liquidity': _liquidity(case.get('balance'), raw),
        'cycle': cycle,
        'market': market,
        'capital_return': _capital_return(cur, py, market, raw),
        'segments': _segments(case.get('segments') or [], cur, raw),
        'kpis': _kpis(case.get('kpis') or [], raw),
        'guidance': _guidance(case.get('guidance') or [], raw),
        'guidance_track': _track(case.get('guidance_track') or [], raw),
        'bank': _bank(case.get('bank'), market['price'], raw) if case.get('bank') and market else None,
        'multiple_scenarios': _scenarios(case['market'], market['price'], raw) if market else [],
        'sensitivities': _sensitivities(case.get('sensitivities') or []),
        'falsifiers': _falsifiers(case.get('falsifiers') or [], raw),
    }
    if case.get('bank') and not market:
        raise ValueError('bank checks need market.price')
    for i, c in enumerate(case.get('concentration') or []):
        _sources(c, f'concentration[{i}]')
        share = number(c['share'], 'concentration share', 0, 1)
        if share >= .3:
            raw.append(('customer_concentration', 'medium', f"{c['group']} = {share:.0%} מההכנסות: כוח מיקוח של מעטים מול החברה."))
    if not (case.get('guidance') or []) or not any(g.get('consensus') is not None for g in case.get('guidance') or []):
        raw.append(('no_expectations_data', 'info', 'אין נתון ציפיות מתועד; אי אפשר לקבוע אם התגובה במניה מוצדקת או מוגזמת.'))
    result['flags'] = sorted(({'key': k, 'severity': s, 'he': he} for k, s, he in raw), key=lambda f: SEVERITY[f['severity']])
    result['next_quarter_watch'] = [f['he'] for f in result['flags'] if f['severity'] != 'info'][:5]
    result['text'] = _text(case, result)
    result['input_sha256'] = sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result['engine_version'] = __version__
    result['boundary'] = ('Deterministic quarter review from caller-supplied, sourced figures. Not source verification, '
                          'not a valuation (use value_company), not a price forecast or order.')
    return result
