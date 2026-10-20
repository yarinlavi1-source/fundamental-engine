"""Portfolio construction against the index: which researched opportunities earn a place, and how large.

The index is the default holding. A company earns an active weight only where the research shows an edge over the index
after a haircut for valuation error and trading cost, and the weight grows with that edge and shrinks with the loss in
the bear case and with low confidence. Material disqualifiers get no weight; drafts get a PROVISIONAL weight that is shown
but not counted as a decision. Caps per name and per shared economic driver keep one bet from dominating.

Sizing rule (stated, not tuned): weight = kelly_fraction x edge / downside^2 x confidence factor, then capped. edge = base
expected return - index expected return - haircut; downside = loss to the bear value (floored). A quarter-Kelly fraction
is a conventional, deliberately small share of the theoretical optimum because the inputs are estimates.

Read-only and illustrative: no orders, no quantities, no tax lots. portfolio.compare covers tax and funding of a single
change; this module answers what the whole book would look like if it followed the research.
"""
from .finance import number

DEFAULTS = {'kelly_fraction': .25, 'single_name_cap': .10, 'driver_cap': .25, 'active_budget': .80, 'downside_floor': .25,
            'trading_cost': .002, 'error_haircut': {'low': .005, 'medium': .01, 'high': .02, None: .015},
            'confidence_factor': {'high': 1., 'medium': .6, 'low': .3, None: .3}, 'min_weight': .01, 'edge_reliability': 1.}


def _status(r):
    if r.get('material_disqualifier'):
        return 'excluded'
    if r.get('irr', {}).get('base') is None or r.get('index_expected_return') is None:
        return 'no_return_estimate'
    return 'provisional' if r.get('price_claim_withheld', True) or r.get('review_status') != 'passed' else 'decided'


def construct(rows, current=None, exposures=None, options=None):
    """rows: handoff.compare()['rows'] (candidates and holdings); current: {symbol: weight} of the read-only book;
    exposures: {symbol: [economic driver tags]}. -> target weights, index weight, expected return range, driver exposure."""
    o = {**DEFAULTS, **(options or {})}
    for k in ('kelly_fraction', 'single_name_cap', 'driver_cap', 'active_budget', 'downside_floor', 'trading_cost', 'min_weight',
              'edge_reliability'):
        o[k] = number(o[k], k, 0, 1)
    current, exposures = current or {}, exposures or {}
    lines = []
    for r in rows:
        st = _status(r)
        irr, idx = r.get('irr') or {}, r.get('index_expected_return')
        hair = o['error_haircut'].get(r.get('valuation_error_size'), o['error_haircut'][None]) + o['trading_cost']
        # edge_reliability: the share of a model edge that materialized historically (engine_backtest.edge_reliability); the
        # model's edge is shrunk by it before the haircut (1 = taken at face value, the default when nothing was measured)
        edge = (irr['base'] - idx) * o['edge_reliability'] - hair if st not in ('excluded', 'no_return_estimate') else None
        down = max(r.get('loss_to_bear') if r.get('loss_to_bear') is not None else 1., o['downside_floor'])
        conf = o['confidence_factor'].get(r.get('confidence'), o['confidence_factor'][None])
        raw = o['kelly_fraction'] * edge / down ** 2 * conf if edge is not None and edge > 0 else 0.
        why = ('material disqualifier: ' + str(r.get('judgement')) if st == 'excluded' else
               'no expected return (valuation not computed)' if st == 'no_return_estimate' else
               f"edge {edge * 100:+.1f} pp over the index after reliability x{o['edge_reliability']:.2f} and a {hair * 100:.1f} pp haircut; "
               f"bear loss {down:.0%}; confidence {r.get('confidence')}")
        lines.append({'symbol': r['symbol'], 'status': st, 'edge_after_haircut': edge, 'downside': down, 'confidence': r.get('confidence'),
                      'raw_weight': min(raw, o['single_name_cap']), 'capped': raw > o['single_name_cap'], 'why': why,
                      'drivers': list(exposures.get(r['symbol']) or []), 'current_weight': current.get(r['symbol'], 0.),
                      'irr': irr, 'index_expected_return': idx})
    # driver caps: scale down every name sharing an over-cap driver, largest driver first
    for _ in range(5):
        load = {}
        for ln in lines:
            for d in ln['drivers']:
                load[d] = load.get(d, 0.) + ln['raw_weight']
        over = {d: v for d, v in load.items() if v > o['driver_cap'] + 1e-12}
        if not over:
            break
        d, v = max(over.items(), key=lambda x: x[1])
        for ln in lines:
            if d in ln['drivers'] and ln['raw_weight']:
                ln['raw_weight'] *= o['driver_cap'] / v
                ln['driver_capped'] = d
    total = sum(ln['raw_weight'] for ln in lines)
    scale = min(1., o['active_budget'] / total) if total else 1.
    for ln in lines:
        w = ln['raw_weight'] * scale
        ln['target_weight'] = w if w >= o['min_weight'] else 0.
        ln['illustrative_change'] = ln['target_weight'] - ln['current_weight']
        ln['reading'] = ('no active weight: ' + ln['why'] if not ln['target_weight'] else
                         ('PROVISIONAL (draft research) ' if ln['status'] == 'provisional' else '') + f"active {ln['target_weight']:.1%}: " + ln['why'])
    active = sum(ln['target_weight'] for ln in lines)
    w_idx = 1 - active

    def port(s):
        vals = [(ln['target_weight'], (ln['irr'] or {}).get(s)) for ln in lines if ln['target_weight']]
        if any(v is None for _, v in vals) or not lines:
            return None
        idx = next((ln['index_expected_return'] for ln in lines if ln['index_expected_return'] is not None), None)
        return None if idx is None else sum(w * v for w, v in vals) + w_idx * idx
    idx_r = next((ln['index_expected_return'] for ln in lines if ln['index_expected_return'] is not None), None)
    drivers = {}
    for ln in lines:
        for d in ln['drivers']:
            drivers[d] = drivers.get(d, 0.) + ln['target_weight']
    review = [ln['symbol'] for ln in lines if ln['current_weight'] > 0 and ln['target_weight'] < ln['current_weight'] - o['min_weight']]
    return {'targets': sorted(lines, key=lambda x: -x['target_weight']), 'index_weight': w_idx, 'active_weight': active,
            'expected_return': {'index_only': idx_r, 'portfolio_bear': port('bear'), 'portfolio_base': port('base'), 'portfolio_bull': port('bull')},
            'driver_exposure': dict(sorted(drivers.items(), key=lambda x: -x[1])), 'holdings_to_review': review,
            'provisional': any(ln['status'] == 'provisional' and ln['target_weight'] for ln in lines), 'options': o,
            'rules': {'size': 'kelly_fraction x edge / max(bear loss, floor)^2 x confidence factor, capped per name',
                      'edge': '(base IRR - index expected return) x edge reliability - (valuation-error haircut + trading cost)',
                      'drivers': f"names sharing an economic driver scaled to at most {o['driver_cap']:.0%} together",
                      'index': 'everything not earned by an active edge stays in the index'},
            'meaning': ('illustrative target book that follows the research; not an order, not tax-aware (portfolio.compare is), '
                        'and provisional wherever the research is a draft')}


def render_he(plan, others=8):
    """Active positions and current holdings, then the next `others` names (the full list is in the plan)."""
    p = lambda x: 'n/a' if x is None else f'{x * 100:.1f}%'
    er = plan['expected_return']
    out = [f"מדד: {p(plan['index_weight'])} מהתיק; פוזיציות פעילות: {p(plan['active_weight'])}"
           + (' (זמני: המחקר עדיין טיוטה)' if plan['provisional'] else ''),
           f"תשואה שנתית צפויה: מדד בלבד {p(er['index_only'])}; התיק המוצע דובי/בסיס/שורי {p(er['portfolio_bear'])} / "
           f"{p(er['portfolio_base'])} / {p(er['portfolio_bull'])}", '']
    shown = [t for t in plan['targets'] if t['target_weight'] or t['current_weight']]
    rest = [t for t in plan['targets'] if t not in shown and t['status'] not in ('excluded', 'no_return_estimate')][:others]
    for t in shown + rest:
        out.append(f"- {t['symbol']}: יעד {p(t['target_weight'])} (היום {p(t['current_weight'])}) — {t['reading']}")
    if len(plan['targets']) > len(shown) + len(rest):
        out.append(f"- ועוד {len(plan['targets']) - len(shown) - len(rest)} חברות בלי משקל פעיל (הרשימה המלאה בקובץ התוכנית)")
    if plan['holdings_to_review']:
        out.append('')
        out.append('אחזקות שהמחקר לא תומך במשקל הנוכחי שלהן (לבדיקה, לא פקודה): ' + ', '.join(plan['holdings_to_review']))
    return '\n'.join(out)
