"""Investment decision: four separate questions, one closed verdict set and the index alternative.

Why: earlier routes folded quality, growth and price into one score or one price bucket, so 'excellent
business but expensive', 'interesting but not yet valuable' or 'the index is better' could not be said,
and a strong growth story could lift a weak price case. Here (1) how good the business is, (2) its state
and trajectory, (3) how attractive the stock is at today's price and (4) the risks and size of possible
error are assessed from their own evidence and are never averaged together. Business quality uses no
price input at all, so it cannot move when only the price moves. Growth is a trajectory question: a
great business growing moderately keeps its quality level.

Price attractiveness is the base-case internal rate of return at today's price
(valuation_tools.implied_cost_of_equity: the cost of equity at which the base value equals the price,
cash flows held fixed) against a hurdle = index expected return + a premium scaled by the assessed risk.
The owner's 14% aspiration is a separate comparison, never the hurdle and never a tuning target.

The verdict comes from one precedence list (VERDICT_RULES). Disqualifying gates (disqualifying risk,
impaired equity, blocked data, insufficient understanding, critical red-team failure, unfunded plan, no
valuation) are separated from attractiveness factors and from risks that can be priced. Analyst
overrides need a written justification plus evidence ids, are stored beside the untouched mechanical
verdict, and can never lift a gate. Thresholds are stated rules of thumb (adjusted by business economics
where noted); a missing input leaves a signal unknown, never zero or imputed.
"""
from copy import deepcopy
from datetime import date
import json
from math import isfinite, log, exp
from .facts import digest
from .finance import number
from .history import cagr
from .plain import MARGIN_BARS
from .scores import COST_OF_CAPITAL_REFERENCE
from .decision_summary import RED_FLAG_KEYS
from .valuation_tools import implied_cost_of_equity, _shifted_value

ASSESSMENTS = ('business_quality', 'trajectory', 'price_attractiveness', 'risk_and_error',
               'growth_quality', 'financial_strength', 'management_capital_allocation', 'data_uncertainty')
LEVELS = ('strong', 'adequate', 'weak', 'unknown')
POINTS = {'strong': 2, 'adequate': 1, 'weak': 0}
LEVEL_HE = {'strong': 'חזקה', 'adequate': 'סבירה', 'weak': 'חלשה', 'unknown': 'לא ידועה'}
VERDICTS = {
    'attractive_at_price': 'מעניין להשקעה במחיר הנוכחי',
    'quality_but_expensive': 'עסק איכותי, אבל יקר כרגע',
    'promising_funding_conditional': 'מבטיח, אבל תלוי במימון שעוד לא הובטח',
    'interesting_not_yet_valuable': 'מעניין, אבל עדיין אין בסיס אמין לשווי',
    'insufficient_evidence': 'אין מספיק ראיות להחלטה',
    'index_preferable': 'קרן מדד רחבה עדיפה כרגע',
    'avoid': 'להימנע',
}
VERDICT_RULES = [
    ('insufficient_evidence', 'business understanding insufficient (model card completeness insufficient / critical question '
                              'open): checked first, because an evidence gap is never a negative judgement'),
    ('avoid', 'a disqualifying risk in the risk register, or impaired equity (base value today is zero)'),
    ('insufficient_evidence', 'critical data blocked, review blocked, critical red-team failure, or (no valuation / quality '
                              'unknown) without positive business evidence'),
    ('interesting_not_yet_valuable', 'method not_reliably_valuable or valuation not computable, but business evidence '
                                     'positive; or quality not yet demonstrated while trajectory/growth is strong and the '
                                     'model price clears the hurdle'),
    ('promising_funding_conditional', 'plan needs external funding that is not secured, business evidence positive'),
    ('attractive_at_price', 'quality strong/adequate, base IRR >= hurdle, bear-case loss within the tolerance for that quality'),
    ('quality_but_expensive', 'quality strong/adequate, base IRR < hurdle (or bear-case loss beyond tolerance)'),
    ('index_preferable', 'no quality edge (weak or unproven) and/or base IRR not above index + risk premium'),
]
INDEX_DEFAULT = {
    'value': .07, 'low': .05, 'high': .09, 'provenance': 'model_assumption', 'source': 'engine default (no dated index input)',
    'basis': ('Long-run nominal return of a broad equity index ~= shareholder yield (dividends + net buybacks, ~2-3%) + nominal '
              'per-share earnings growth (~4-5%: real growth plus ~2-2.5% inflation). The 5-9% range reflects starting-'
              'valuation uncertainty. Not a forecast; supply inputs.index {value, basis, source} to replace it.')}
# Extra return demanded over a diversified index for single-stock concentration and model error, by the
# risk_and_error level (strong = limited downside, small error). Unknown risk is treated as above average.
RISK_PREMIUM = {'strong': .01, 'adequate': .02, 'weak': .04, 'unknown': .03}
OWNER_ASPIRATION = .14
# Largest acceptable loss from today's price to the bear value today, by business quality. A stronger
# business is more likely to recover from a bad-case markdown, so it tolerates a deeper one.
BEAR_LOSS_TOLERANCE = {'strong': .40, 'adequate': .30}
ROIC_STRONG = .15                                     # well above the ~8% cost-of-capital reference
MARGIN_PROFILE = {'subscription_software': 'high_margin', 'platform_marketplace': 'high_margin', 'semiconductor': 'general',
                  'hardware_manufacturing': 'general', 'industrial': 'general', 'conglomerate': 'general',
                  'capital_intensive_infrastructure': 'general', 'consumer_brand': 'general', 'commodity': 'thin_margin'}
NOT_MARGIN_GRADED = {'financials', 'reit', 'pre_revenue_biotech'}
COMPETITIVE_DIMS = ('moat', 'switching_costs', 'pricing_power', 'retention')
MANAGEMENT_DIMS = ('management', 'capital_allocation', 'governance')
OVERRIDE_EFFECT = {'business_quality': 'verdict quality test and bear-loss tolerance', 'trajectory': 'positive-business-evidence test',
                   'growth_quality': 'positive-business-evidence test', 'risk_and_error': 'hurdle risk premium',
                   'price_attractiveness': 'price test (only when no cash-flow IRR exists)', 'financial_strength': 'recorded; ranking only',
                   'management_capital_allocation': 'recorded; ranking only', 'data_uncertainty': 'confidence reasons'}
DEMONSTRATED = {'growth': ('revenue_cagr_3y', '3y revenue CAGR'), 'mature_margin': ('operating_margin', 'current operating margin')}
# risk.py marks every gate as class 'disqualifying'; only some are negative judgements (avoid). The others are
# evidence gaps (insufficient_evidence) or the funding condition, routed to their own precedence step.
RISK_GATES = {'gate.blocked_data': 'data_blocked', 'gate.thesis_without_basis': 'understanding',
              'gate.redteam_critical_failure': 'valuation_unverified', 'gate.unfunded_plan': 'funding', 'gate.equity_impaired': 'impaired'}
# A gated verdict may only be overridden toward an outcome that is at least as restrictive.
GATE_ALLOWED = {'disqualifying_risk': {'avoid'}, 'impaired_equity': {'avoid'},
                'data_blocked': {'avoid', 'insufficient_evidence'}, 'review_blocked': {'avoid', 'insufficient_evidence'},
                'valuation_unverified': {'avoid', 'insufficient_evidence'},
                'understanding_insufficient': {'avoid', 'insufficient_evidence'},
                'unfunded_plan': {'avoid', 'insufficient_evidence', 'promising_funding_conditional'},
                'no_valuation': {'avoid', 'insufficient_evidence', 'interesting_not_yet_valuable', 'index_preferable'}}
FIELD_HE = {'shares_outstanding': 'מספר המניות', 'cash_and_liquid_investments': 'המזומן וההשקעות הנזילות',
            'investments': 'ההשקעות', 'debt': 'החוב', 'convertibles': 'האג"ח להמרה', 'post_period_events': 'אירועים אחרי תאריך הדוח',
            'revenue_ttm': 'ההכנסות בשנה האחרונה', 'operating_income_ttm': 'הרווח התפעולי בשנה האחרונה',
            'cfo_ttm': 'תזרים המזומנים מפעילות', 'capex_ttm': 'ההשקעות ברכוש קבוע', 'sbc_ttm': 'התגמול מבוסס המניות'}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) else None


def _pct(v, d=1):
    return 'n/a' if v is None else f'{v * 100:.{d}f}%'


def _he_pct(v, near=None):
    """Whole percent unless a comparison within one point needs a decimal (no fake precision)."""
    if v is None:
        return 'לא ידוע'
    d = 1 if near is not None and abs(v - near) < .01 else 0
    return f'{v * 100:.{d}f}%'


def _metric(an, name):
    m = ((an or {}).get('metrics') or {}).get(name)
    return m if isinstance(m, dict) else None


def _val(an, name):
    m = _metric(an, name)
    return _num(m.get('value')) if m else None


def _ref(an, name):
    m = _metric(an, name)
    if not m:
        return {'metric': name, 'value': None, 'status': 'missing'}
    return {k: v for k, v in (('metric', name), ('value', _num(m.get('value'))), ('period', m.get('period')),
                              ('status', m.get('status')), ('formula', m.get('formula'))) if v is not None}


def _card(card, *path):
    node = card or {}
    for k in path:
        node = node.get(k) if isinstance(node, dict) else None
    return node


def _band(v, strong, adequate, higher=True):
    if v is None:
        return 'unknown'
    if higher:
        return 'strong' if v >= strong else 'adequate' if v >= adequate else 'weak'
    return 'strong' if v <= strong else 'adequate' if v <= adequate else 'weak'


def _signal(name, value, level, rule, core=False, evidence=None, note=None, shown=None):
    return {'signal': name, 'value': value, 'shown': shown if shown is not None else (_pct(value) if isinstance(value, float) else value),
            'level': level, 'rule': rule, 'core': core, 'evidence': evidence or [], 'note': note}


def _assessment(signals, how='mean', min_known=2, semantics=None, extra=()):
    """Combine one question's signals. 'mean': strong=2/adequate=1/weak=0 averaged over KNOWN signals;
    strong >= 1.5 with no weak core signal, weak < 0.75 or two weak core signals, else adequate.
    'worst': the weakest known signal (a large risk is not offset by small ones). Unknown when fewer than
    min_known signals are known or no core signal is known: missing evidence is never imputed."""
    known = [s for s in signals if s['level'] in POINTS]
    core = [s for s in known if s['core']]
    if len(known) < min_known or not known or (any(s['core'] for s in signals) and not core):
        level = 'unknown'
    elif how == 'worst':
        level = min(known, key=lambda s: POINTS[s['level']])['level']
    else:
        mean = sum(POINTS[s['level']] for s in known) / len(known)
        weak_core = sum(s['level'] == 'weak' for s in core)
        level = 'strong' if mean >= 1.5 and not weak_core else 'weak' if mean < .75 or weak_core >= 2 else 'adequate'
    reasons = [f"{s['signal']}: {s['level']} ({s['shown'] if s['shown'] is not None else 'missing'}; {s['rule']})"
               + (f" - {s['note']}" if s.get('note') else '') for s in signals] + list(extra)
    evidence = [e for s in signals for e in s['evidence']]
    out = {'level': level, 'reasons': reasons, 'evidence': evidence, 'signals': signals,
           'rule': _assessment.__doc__.split('\n')[0] + (' Combination: weakest known signal.' if how == 'worst' else ' Combination: mean of known signals.')}
    if semantics:
        out['level_semantics'] = semantics
    return out


# ---------------------------------------------------------------- the eight assessments

def _competitive(card):
    dims = _card(card, 'dimensions') or {}
    used, ignored = [], []
    for k in COMPETITIVE_DIMS:
        d = dims.get(k) or {}
        if d.get('assessment') in POINTS:
            # An analyst label without evidence ids is an assertion, not evidence.
            (ignored if d.get('source') == 'analyst' and not d.get('evidence_ids') else used).append((k, d))
    if not used:
        return 'unknown', used, ignored
    n = len(used)
    strong = sum(d['assessment'] == 'strong' for _, d in used)
    weak = sum(d['assessment'] == 'weak' for _, d in used)
    return ('strong' if strong * 2 > n and not weak else 'weak' if weak * 2 > n else 'adequate'), used, ignored


def financial_strength(an, card):
    sig = []
    nc, nd = _val(an, 'net_cash'), _val(an, 'net_debt_to_ebitda')
    if nc is not None and nc >= 0:
        lev = _signal('leverage', nc, 'strong', 'net cash >= 0 is strong; else net debt/EBITDA <= 1 strong, <= 3 adequate',
                      evidence=[_ref(an, 'net_cash')], shown=f'net cash {nc / 1e6:,.0f}m')
    elif nd is not None:
        lev = _signal('leverage', nd, _band(nd, 1, 3, False) if nd >= 0 else 'weak',
                      'net debt/EBITDA <= 1 strong, <= 3 adequate, else weak (negative EBITDA with net debt is weak)',
                      evidence=[_ref(an, 'net_debt_to_ebitda'), _ref(an, 'net_cash')], shown=f'{nd:.1f}x')
    else:
        lev = _signal('leverage', None, 'unknown', 'net cash or net debt/EBITDA required', evidence=[_ref(an, 'net_cash')])
    sig.append(lev)
    cov = _val(an, 'interest_coverage')
    sig.append(_signal('interest_coverage', cov, _band(cov, 10, 4), 'EBIT/interest >= 10x strong, >= 4x adequate',
                       evidence=[_ref(an, 'interest_coverage')], shown=None if cov is None else f'{cov:.1f}x'))
    z = (((an or {}).get('forensics') or {}).get('altman') or {})
    if z.get('status') == 'computed':
        sig.append(_signal('altman', _num(z.get('z')), {'safe': 'strong', 'grey': 'adequate', 'distress': 'weak'}.get(z.get('zone'), 'unknown'),
                           f"Altman {z.get('model')}: safe / grey / distress zones", shown=f"z={_num(z.get('z')) or 0:.2f} ({z.get('zone')})",
                           evidence=[{'metric': 'forensics.altman', 'value': _num(z.get('z')), 'status': z.get('zone')}]))
    fund = _card(card, 'funding', 'value')
    ttm = (an or {}).get('ttm') or {}
    fcf = _num(ttm.get('fcf'))
    liquid = sum(x for x in (_num(ttm.get('cash')), _num(ttm.get('investments'))) if x is not None) if ttm.get('cash') is not None else None
    if fcf is not None and fcf < 0 and liquid is not None:
        runway = liquid / -fcf
        sig.append(_signal('cash_runway', runway, _band(runway, 3, 1.5), 'years of cash at current FCF burn: >= 3 strong, >= 1.5 adequate',
                           shown=f'{runway:.1f} years', evidence=[{'metric': 'ttm.fcf', 'value': fcf}, {'metric': 'ttm.cash+investments', 'value': liquid}]))
    sig.append(_signal('funding', fund, {'self_funded': 'strong', 'needs_external': 'weak'}.get(fund, 'unknown'),
                       'model card funding: self_funded strong, needs_external weak',
                       evidence=[{'card': 'funding', 'value': fund, 'reasons': _card(card, 'funding', 'reasons')}]))
    return _assessment(sig, min_known=1)


def business_quality(an, card, fs_level):
    """How good is the business (price-independent). Core: ROIC, operating margin, FCF conversion;
    for an early/growth company that runs an operating loss while investing, current profits do not
    measure unit economics, so gross margin and evidenced competitive position become core instead."""
    bm, stage = _card(card, 'business_model', 'primary'), _card(card, 'stage', 'value')
    opm, gm = _val(an, 'operating_margin'), _val(an, 'gross_margin')
    grower = stage in ('early', 'growth') and opm is not None and opm < 0
    graded = bm not in NOT_MARGIN_GRADED
    profile = MARGIN_PROFILE.get(bm, 'general')
    bars = MARGIN_BARS[profile]
    nm = 'not_meaningful'
    why_nm = (f'margins/ROIC are not graded for {bm}; use its dedicated model' if not graded else
              f'operating loss while investing for growth (stage {stage}); current profit does not measure unit economics')
    sig = []
    roic = _val(an, 'roic')
    roic_rule = f'strong >= {ROIC_STRONG:.0%}, adequate >= {COST_OF_CAPITAL_REFERENCE:.0%} (cost-of-capital reference)'
    if not graded or grower:
        sig.append(_signal('roic', roic, nm, roic_rule, note=why_nm, evidence=[_ref(an, 'roic')]))
    else:
        sig.append(_signal('roic', roic, _band(roic, ROIC_STRONG, COST_OF_CAPITAL_REFERENCE), roic_rule, core=True,
                           evidence=[_ref(an, 'roic'), _ref(an, 'roic_ex_goodwill')]))
        series = [_num(s.get('roic')) for s in ((((an or {}).get('forensics') or {}).get('roic') or {}).get('series') or [])]
        series = [x for x in series if x is not None]
        if len(series) >= 3:
            sig.append(_signal('roic_durability', min(series), _band(min(series), ROIC_STRONG, COST_OF_CAPITAL_REFERENCE),
                               f'lowest ROIC of the last {len(series)} years; ' + roic_rule,
                               evidence=[{'metric': 'forensics.roic.series', 'value': series}]))
    op_rule = f"{profile} bars: strong >= {bars['operating'][1]:.0%}, adequate >= {bars['operating'][2]:.0%}"
    if not graded or grower:
        sig.append(_signal('operating_margin', opm, nm, op_rule, note=why_nm, evidence=[_ref(an, 'operating_margin')]))
    else:
        sig.append(_signal('operating_margin', opm, _band(opm, bars['operating'][1], bars['operating'][2]), op_rule, core=True,
                           evidence=[_ref(an, 'operating_margin'), _ref(an, 'operating_margin_best_3y')]))
    gm_rule = f"{profile} bars: strong >= {bars['gross'][1]:.0%}, adequate >= {bars['gross'][2]:.0%}"
    sig.append(_signal('gross_margin', gm, _band(gm, bars['gross'][1], bars['gross'][2]) if graded else nm, gm_rule,
                       core=grower, evidence=[_ref(an, 'gross_margin')], note=None if graded else why_nm))
    fm, conv = _val(an, 'fcf_margin'), _val(an, 'cfo_to_net_income')
    conv_rule = 'FCF margin <= 0 weak; else CFO/net income >= 1.0 strong, >= 0.8 adequate (fallback FCF margin >= 15% / >= 5%)'
    conv_ev = [_ref(an, 'fcf_margin'), _ref(an, 'cfo_to_net_income'), _ref(an, 'fcf_after_sbc_margin')]
    if grower or not graded:
        sig.append(_signal('fcf_conversion', fm, nm, conv_rule, note=why_nm, evidence=conv_ev))
    elif fm is not None and fm <= 0:
        sig.append(_signal('fcf_conversion', fm, 'weak', conv_rule, core=True, evidence=conv_ev, shown=f'FCF margin {_pct(fm)}'))
    elif conv is not None and conv > 0:
        sig.append(_signal('fcf_conversion', conv, _band(conv, 1.0, .8), conv_rule, core=True, evidence=conv_ev, shown=f'CFO/NI {conv:.2f}x'))
    else:
        sig.append(_signal('fcf_conversion', fm, _band(fm, .15, .05), conv_rule, core=True, evidence=conv_ev,
                           shown=None if fm is None else f'FCF margin {_pct(fm)}'))
    lvl, used, ignored = _competitive(card)
    sig.append(_signal('competitive_position', [k for k, _ in used], lvl,
                       'evidenced model-card dimensions (moat, switching costs, pricing power, retention): majority strong '
                       'and none weak -> strong; majority weak -> weak', core=grower,
                       shown=', '.join(f"{k}={d['assessment']}" for k, d in used) or None,
                       evidence=[{'dimension': k, 'assessment': d['assessment'], 'evidence_ids': d.get('evidence_ids') or [],
                                  'source': d.get('source')} for k, d in used],
                       note=('ignored analyst labels without evidence ids: ' + ', '.join(k for k, _ in ignored)) if ignored else None))
    sig.append(_signal('balance_sheet', fs_level, fs_level if fs_level in POINTS else 'unknown',
                       'financial_strength assessment (supporting)'))
    return _assessment(sig)


def trajectory(an, card, forecast_view):
    """State and direction of the business: growth, acceleration and margin trend from reported history.
    The forecast is shown for comparison but is a model assumption, not evidence of trajectory."""
    g3, g5, last = _val(an, 'revenue_cagr_3y'), _val(an, 'revenue_cagr_5y'), _val(an, 'revenue_growth_last')
    g, gname = next(((v, n) for v, n in ((g3, 'revenue_cagr_3y'), (g5, 'revenue_cagr_5y'), (last, 'revenue_growth_last'))
                     if v is not None), (None, 'revenue_cagr_3y'))
    sig = [_signal('revenue_growth', g, _band(g, .10, .03),
                   'strong >= 10%/yr, adequate >= 3%/yr (about keeping pace with the nominal economy)', core=True,
                   evidence=[_ref(an, gname)], note=None if gname == 'revenue_cagr_3y' else f'fallback {gname}')]
    if last is not None and g3 is not None:
        d = last - g3
        sig.append(_signal('acceleration', d, 'strong' if d >= .03 else 'weak' if d <= -.05 else 'adequate',
                           'last-year growth minus 3y CAGR: >= +3pp accelerating, <= -5pp decelerating',
                           evidence=[_ref(an, 'revenue_growth_last'), _ref(an, 'revenue_cagr_3y')], shown=f'{d * 100:+.1f}pp'))
    mt = _val(an, 'operating_margin_trend_3y')
    sig.append(_signal('operating_margin_trend', mt, _band(mt, .02, -.02),
                       '3-year change in operating margin: >= +2pp expanding, >= -2pp stable, else contracting', core=True,
                       evidence=[_ref(an, 'operating_margin_trend_3y')], shown=None if mt is None else f'{mt * 100:+.1f}pp'))
    stage = _card(card, 'stage', 'value')
    sig.append(_signal('stage', stage, 'weak' if stage == 'decline' else 'context', 'model card stage (scored only when decline)',
                       evidence=[{'card': 'stage', 'value': stage}]))
    if forecast_view and forecast_view.get('base_revenue_cagr') is not None:
        f = forecast_view
        note = 'model assumption, not evidence'
        if g is not None and f['base_revenue_cagr'] > g + .10:
            note += '; base forecast is more than 10pp above its own history and needs a demonstrated cause'
        sig.append(_signal('forecast_direction', f['base_revenue_cagr'], 'context', f"base revenue CAGR to {f['through']}", note=note,
                           evidence=[{'metric': 'valuation.base.revenue_cagr', 'value': f['base_revenue_cagr'], 'provenance': 'model_assumption'}]))
    return _assessment(sig, min_known=1)


def growth_quality(an):
    acq, inc = _val(an, 'acquisition_spend_pct_revenue_3y'), _val(an, 'incremental_roic_3y')
    dil, vol, sbc = _val(an, 'share_count_cagr_3y'), _val(an, 'growth_volatility'), _val(an, 'sbc_pct_revenue')
    sig = [_signal('organic', acq, _band(acq, .05, .20, False), 'acquisition spend <= 5% of revenue strong, <= 20% adequate',
                   evidence=[_ref(an, 'acquisition_spend_pct_revenue_3y')]),
           _signal('incremental_returns', inc, _band(inc, ROIC_STRONG, COST_OF_CAPITAL_REFERENCE),
                   f'incremental ROIC (3y) >= {ROIC_STRONG:.0%} strong, >= {COST_OF_CAPITAL_REFERENCE:.0%} adequate',
                   evidence=[_ref(an, 'incremental_roic_3y'), _ref(an, 'sales_to_capital_incremental_3y')]),
           _signal('per_share', dil, _band(dil, 0, .02, False), 'share count CAGR (3y) <= 0 strong, <= 2%/yr adequate',
                   evidence=[_ref(an, 'share_count_cagr_3y')]),
           _signal('steadiness', vol, _band(vol, .05, .15, False), 'volatility of annual growth <= 5pp strong, <= 15pp adequate',
                   evidence=[_ref(an, 'growth_volatility')]),
           _signal('sbc_cost', sbc, _band(sbc, .05, .12, False), 'SBC <= 5% of revenue strong, <= 12% adequate',
                   evidence=[_ref(an, 'sbc_pct_revenue')])]
    return _assessment(sig)


def management_capital_allocation(an, card):
    dims = _card(card, 'dimensions') or {}
    sig = []
    for k in MANAGEMENT_DIMS:
        d = dims.get(k) or {}
        ok = d.get('assessment') in POINTS and (d.get('source') != 'analyst' or d.get('evidence_ids'))
        sig.append(_signal(k, d.get('assessment'), d['assessment'] if ok else 'unknown',
                           'model card dimension (analyst labels need evidence ids)', core=False,
                           evidence=[{'dimension': k, 'assessment': d.get('assessment'), 'evidence_ids': d.get('evidence_ids') or [],
                                      'source': d.get('source')}] if d else []))
    dil = _val(an, 'share_count_cagr_3y')
    sig.append(_signal('share_count', dil, _band(dil, -.01, .02, False), 'share count CAGR (3y) <= -1% strong (net shrink), <= 2% adequate',
                       evidence=[_ref(an, 'share_count_cagr_3y'), _ref(an, 'buybacks_pct_fcf_3y'), _ref(an, 'sbc_offset_by_buybacks')]))
    acq, inc = _val(an, 'acquisition_spend_pct_revenue_3y'), _val(an, 'incremental_roic_3y')
    if acq is not None and acq > .20:
        sig.append(_signal('acquisitions', inc, _band(inc, ROIC_STRONG, COST_OF_CAPITAL_REFERENCE),
                           'acquisitive (> 20% of revenue over 3y): judged by incremental ROIC',
                           evidence=[_ref(an, 'acquisition_spend_pct_revenue_3y'), _ref(an, 'incremental_roic_3y')]))
    return _assessment(sig)


def data_uncertainty(qa, val, fb):
    sem = 'strong = low data uncertainty (verified, primary-read); weak = blocked or synthetic'
    sig = []
    st = (qa or {}).get('status')
    sig.append(_signal('dataqa', st, {'ok': 'strong', 'limited': 'adequate', 'blocked': 'weak'}.get(st, 'unknown'),
                       'dataqa status ok / limited / blocked',
                       evidence=[{'dataqa': st, 'blocking': (qa or {}).get('blocking', []), 'limited': (qa or {}).get('limited', [])}],
                       note=('blocking: ' + ', '.join((qa or {}).get('blocking') or [])) if (qa or {}).get('blocking') else
                       ('limited: ' + ', '.join((qa or {}).get('limited') or [])) if (qa or {}).get('limited') else None))
    res = (val or {}).get('result') or {}
    access = ((res.get('underwriting_audit') or {}).get('source_access') or {}).get('status')
    if access:
        sig.append(_signal('source_access', access, {'primary_read': 'strong', 'secondary_access': 'adequate',
                                                     'no_primary_source': 'weak', 'demo': 'weak'}.get(access, 'unknown'),
                           'opening balance read from the primary filing (valuation_audit.source_access)'))
    facts = (fb or {}).get('facts') or []
    if facts:
        est = sum(bool(f.get('available_at_estimated')) for f in facts) / len(facts)
        sig.append(_signal('filing_dates', est, 'context', 'share of facts whose public date is estimated from deadlines',
                           note='point-in-time dating limitation' if est > .5 else None))
    return _assessment(sig, how='worst', min_known=1, semantics=sem)


def risk_and_error(rk, an, view):
    sem = 'strong = limited downside and small possible error; weak = deep downside, large error or disqualifying risk'
    rk = rk or {}
    sig = []
    loss = view.get('loss_to_bear')
    sig.append(_signal('downside_to_bear', loss, _band(loss, .20, .40, False), 'loss from price to bear value today <= 20% strong, <= 40% adequate',
                       evidence=[{'metric': 'bear_value_today', 'value': view.get('bear'), 'provenance': 'calculation'}]))
    err = (rk.get('error_size') or {})
    sig.append(_signal('error_size', err.get('level'), {'low': 'strong', 'medium': 'adequate', 'high': 'weak'}.get(err.get('level'), 'unknown'),
                       'risk.error_size level low / medium / high', evidence=[{'risk': 'error_size', 'reasons': err.get('reasons') or []}]))
    flags = [f for f in (an or {}).get('flags') or [] if f.get('severity') == 'high']
    raised = _forensic_flags(an)
    n = len(flags) + len(raised)
    if an is not None:
        sig.append(_signal('red_flags', n, 'strong' if n == 0 else 'adequate' if n == 1 else 'weak',
                           'high-severity analysis flags + forensic red flags (Beneish likely manipulator, Altman distress): 0 / 1 / 2+',
                           evidence=[{'flag': f.get('code'), 'message': f.get('message')} for f in flags] + [{'forensic': k} for k in raised]))
    perm = (rk.get('downside') or {}).get('loss_to_permanent')
    extra = []
    if _num(perm) is not None:
        extra.append(f'permanent-loss scenario: {_pct(_num(perm), 0)} from price (context)')
    ts = view.get('terminal_share')
    if ts is not None and ts > .8:
        extra.append(f'terminal value is {_pct(ts, 0)} of base value today: most value sits beyond the explicit forecast')
    a = _assessment(sig, how='worst', min_known=1, semantics=sem, extra=extra)
    if not [s for s in sig[:2] if s['level'] in POINTS]:
        a['level'] = 'unknown'
    dq = list(rk.get('disqualifying') or [])
    if dq:
        a['level'] = 'weak'
        a['reasons'].insert(0, 'disqualifying risks: ' + ', '.join(dq))
        a['evidence'] += [{'risk_id': i} for i in dq]
    return a


def _forensic_flags(an):
    fz = (an or {}).get('forensics') or {}
    out = []
    if (fz.get('beneish') or {}).get('zone') == 'likely_manipulator':
        out.append('accounting_risk')
    if (fz.get('altman') or {}).get('zone') == 'distress':
        out.append('distress')
    return out


def price_attractiveness(er, view, wmbt, an=None):
    """Is the stock attractive at today's price: base IRR against the hurdle; margin of safety against base
    and bear values; what the price requires versus what is demonstrated (valuation what_must_be_true)."""
    irr, hurdle = er.get('base_irr'), er.get('hurdle')
    sig = []
    rule = 'base IRR >= hurdle + 2pp strong, >= hurdle adequate, below weak'
    if irr is not None and hurdle is not None:
        lvl = 'strong' if irr >= hurdle + .02 else 'adequate' if irr >= hurdle else 'weak'
        sig.append(_signal('base_irr_vs_hurdle', irr, lvl, rule, core=True, shown=f'{_pct(irr)} vs hurdle {_pct(hurdle)}',
                           evidence=[{'metric': 'base_irr', 'value': irr, 'provenance': 'calculation'},
                                     {'metric': 'hurdle', 'value': hurdle, 'provenance': 'model_assumption'}]))
    elif er.get('base_irr_bound') and hurdle is not None:
        b = er['base_irr_bound']
        lvl = 'weak' if b['type'] == 'below' and b['rate'] <= hurdle else 'strong' if b['type'] == 'above' and b['rate'] >= hurdle + .02 else 'unknown'
        sig.append(_signal('base_irr_vs_hurdle', b['rate'], lvl, rule, core=True, shown=f"IRR {b['type']} {_pct(b['rate'])}"))
    elif view.get('base') is not None:
        d = view.get('discount_to_base')
        sig.append(_signal('discount_to_base', d, _band(d, .25, .10),
                           'no cash-flow IRR for this method: discount to base value >= 25% strong, >= 10% adequate', core=True))
    else:
        sig.append(_signal('base_irr_vs_hurdle', None, 'unknown', rule, core=True, note='no executed valuation'))
    extra = []
    if view.get('discount_to_base') is not None:
        extra.append(f"discount to base value today: {_pct(view['discount_to_base'], 0)} (1 - price/base; negative = premium)")
    if view.get('loss_to_bear') is not None:
        extra.append(f"loss to bear value today: {_pct(view['loss_to_bear'], 0)}")
    hard = []
    for w in wmbt or []:
        if not isinstance(w, dict):
            continue
        f = (lambda x: _pct(_num(x)) if _num(x) is not None else x) if w.get('unit') == 'ratio' else (lambda x: x)
        shown = DEMONSTRATED.get(w.get('lever'))
        hist = f" vs demonstrated {_pct(_val(an, shown[0]))} ({shown[1]})" if shown and _val(an, shown[0]) is not None else ''
        extra.append(f"what must be true - {w.get('label') or w.get('lever')}: price requires {f(w.get('required'))} vs base case "
                     f"{f(w.get('anchor'))}{hist} ({w.get('status')})")
        if w.get('status') == 'not_reachable':
            hard.append(w.get('label') or w.get('lever'))
    if hard:
        extra.append('no reachable value of these levers alone justifies the price: ' + ', '.join(map(str, hard)))
    a = _assessment(sig, min_known=1, extra=extra)
    a['evidence'] += [{'what_must_be_true': w} for w in (wmbt or []) if isinstance(w, dict)]
    return a


# ---------------------------------------------------------------- valuation view and expected return

def _case_and_result(val):
    val = val or {}
    return val.get('case'), val.get('result')


def five_year_row(annual_values, as_of):
    """Fifth anniversary or the first model boundary up to 100 days later (plain_language protocol);
    never an earlier row relabelled as five years, never an extrapolation."""
    start = date.fromisoformat(as_of)
    try:
        target = start.replace(year=start.year + 5)
    except ValueError:
        target = start.replace(year=start.year + 5, day=28)
    rows = [(date.fromisoformat(r['date']), r) for r in (annual_values or [])[1:]
            if 0 <= (date.fromisoformat(r['date']) - target).days <= 100]
    if not rows:
        return None
    d, row = min(rows, key=lambda x: x[0])
    return {'date': d.isoformat(), 'years': (d - start).days / 365.25, 'row': row}


def _view(fb, val):
    case, res = _case_and_result(val)
    price = ((fb or {}).get('market') or {}).get('price')
    view = {'price': None, 'price_source': None, 'bear': None, 'base': None, 'bull': None, 'status': (val or {}).get('status'),
            'method': (case or {}).get('method'), 'is_demo': bool((res or {}).get('is_demo')), 'notes': []}
    if price:
        view.update(price=_num(price.get('value')), currency=price.get('currency'), price_as_of=price.get('as_of'),
                    price_source=price.get('source_id'), price_kind=price.get('kind'))
    if case and res:
        q = case['quote']['price']
        if view['price'] is None:
            view.update(price=q, currency=case['currency'], price_as_of=case['quote']['as_of'], price_source=(case['quote']['source_ids'] or [None])[0])
        elif abs(q / view['price'] - 1) > .005:
            view['notes'].append(f"valuation quote {q:g} differs from the FactBook price {view['price']:g}; returns use the FactBook price")
        today = res['annual_values'][0]
        view.update(bear=_num(today.get('bear')), base=_num(today.get('base')), bull=_num(today.get('bull')))
        p = view['price']
        if view['base'] is not None and p:
            view['discount_to_base'] = 1 - p / view['base'] if view['base'] > 0 else None
            view['upside_to_base'] = view['base'] / p - 1
        if view['bear'] is not None and p:
            view['loss_to_bear'] = max(0., 1 - view['bear'] / p)
        base_sc = next((s for s in res.get('scenarios', []) if s['name'] == 'base'), {})
        view['terminal_share'] = _num(base_sc.get('terminal_share_of_present_value'))
        five = five_year_row(res['annual_values'], case['as_of'])
        if five and _num(five['row'].get('base')) is not None and p:
            v5 = five['row']['base']
            tl = next((r for r in base_sc.get('timeline', []) if r['date'] == five['date']), {})
            view['five_year'] = {'date': five['date'], 'years': five['years'], 'base_value': v5,
                                 'bear_value': _num(five['row'].get('bear')), 'bull_value': _num(five['row'].get('bull')),
                                 'cumulative_dividends_base': _num(tl.get('cumulative_dividend_per_share')) or 0.,
                                 'price_change_excluding_dividends': v5 / p - 1,
                                 'annualized_price_change_excluding_dividends': (v5 / p) ** (1 / five['years']) - 1 if v5 > 0 else None,
                                 'semantics': 'conditional base-case value at that date, not a predicted market price'}
    return view


def _quote_case(case, price):
    if case is None or price is None or abs(case['quote']['price'] - price) < 1e-12:
        return case
    c = deepcopy(case)
    c['quote']['price'] = price
    return c


_IRR_MEMO = {}


def _irr(case, scenario, diag=None):
    """Annual return that equates the scenario value with today's price (cash flows fixed).
    The solve is a pure function of (case, scenario), so it is memoised by the case digest."""
    d = (diag or {}).get(scenario) or {}
    if d.get('status') == 'solved' and _num(d.get('implied_cost_of_equity')) is not None:
        return {'value': d['implied_cost_of_equity'], 'status': 'solved', 'bound': None}
    key = (digest(case), scenario)
    if key not in _IRR_MEMO:
        if len(_IRR_MEMO) > 512:
            _IRR_MEMO.clear()
        _IRR_MEMO[key] = _solve_irr(case, scenario)
    return deepcopy(_IRR_MEMO[key])


def _solve_irr(case, scenario):
    try:
        r = implied_cost_of_equity(case, scenario, high=.30)
    except (ValueError, KeyError, ZeroDivisionError) as e:
        return {'value': None, 'status': 'not_computable', 'bound': None, 'reason': str(e)}
    if r['status'] == 'solved':
        return {'value': r['implied_cost_of_equity'], 'status': 'solved', 'bound': None}
    s = next(x for x in case['scenarios'] if x['name'] == scenario)
    hi_v, lo_v = (r.get('range_values') or [None, None])
    q = case['quote']['price']
    if r['status'] == 'not_bracketed' and lo_v is not None and q > lo_v:
        low = max(-.08, -s['cost_of_equity'] + .005, (s['terminal']['growth'] - s['terminal']['wacc'] + .0025) if case['method'] == 'operating' else -1)
        return {'value': None, 'status': 'below_search_range', 'bound': {'type': 'below', 'rate': s['cost_of_equity'] + low}}
    if r['status'] == 'not_bracketed' and hi_v is not None and q < hi_v:
        return {'value': None, 'status': 'above_search_range', 'bound': {'type': 'above', 'rate': s['cost_of_equity'] + .30}}
    return {'value': None, 'status': r['status'], 'bound': None, 'reason': r.get('limitation')}


def _decomposition(case, res, view, an):
    """Base 5-year return split into business growth x multiple change x cash yield (log-additive, exact):
    (V_T + D_T) / P_0 = (F_T / F_0) * ((V_T / F_T) / (P_0 / F_0)) * ((V_T + D_T) / V_T), where F is per-share
    FCF after SBC (fallback: NOPAT per share), V_T the base value at the five-year boundary, D_T cumulative
    base distributions (dividends or equivalent buybacks), P_0 today's price. Each factor is annualised as
    factor^(1/T) - 1, so (1+g)(1+m)(1+c) = 1 + total. The 5-year return assumes the stock trades at the
    base value at T; base_irr instead holds the cash flows through the terminal value."""
    five = view.get('five_year')
    if not five or not case or case.get('method') != 'operating':
        return {'status': 'unavailable', 'reason': 'needs an operating cash-flow valuation with a five-year base value'}
    T, V, D, P = five['years'], five['base_value'], five['cumulative_dividends_base'], view['price']
    rows = next((s['forecast'] for s in res['scenarios'] if s['name'] == 'base'), [])

    def dur(r):
        return (date.fromisoformat(r['end']) - date.fromisoformat(r['start'])).days / 365.25

    end = next((r for r in rows if r['end'] == five['date'] and dur(r) > .9), None)
    first = next((r for r in rows if dur(r) > .9), None)
    if end is None or first is None:
        return {'status': 'unavailable', 'reason': 'no full model year ending at the five-year boundary'}
    shares0 = case['opening']['shares']
    ttm = (an or {}).get('ttm') or {}
    candidates = []
    fa = _num(ttm.get('fcf_after_sbc'))
    if fa is not None:
        candidates.append(('fcf_after_sbc_per_share', fa / shares0, f"reported TTM FCF after SBC ({ttm.get('period_end')}) / opening shares",
                           'calculation from reported', lambda r: (r['cfo'] - r['maintenance_capex'] - r['growth_capex']) / r['shares'] / dur(r)))
    candidates.append(('fcf_after_sbc_per_share', (first['cfo'] - first['maintenance_capex'] - first['growth_capex']) / first['shares'] / dur(first),
                       f"first full model year to {first['end']}", 'model_assumption',
                       lambda r: (r['cfo'] - r['maintenance_capex'] - r['growth_capex']) / r['shares'] / dur(r)))
    candidates.append(('nopat_per_share', (first['ebit'] - first['cash_taxes']) / first['shares'] / dur(first),
                       f"first full model year to {first['end']} (EBIT - cash taxes)", 'model_assumption',
                       lambda r: (r['ebit'] - r['cash_taxes']) / r['shares'] / dur(r)))
    for metric, f0, basis, prov, fn in candidates:
        fT = fn(end)
        if f0 > 0 and fT > 0 and V > 0 and P:
            G, M, C = fT / f0, (V / fT) / (P / f0), (V + D) / V
            ann = lambda x: exp(log(x) / T) - 1
            return {'status': 'computed', 'metric': metric, 'date': five['date'], 'years': T,
                    'anchor': {'value': f0, 'basis': basis, 'provenance': prov},
                    'end': {'value': fT, 'basis': f"base model year to {end['end']}", 'provenance': 'model_assumption'},
                    'current_multiple': P / f0, 'exit_multiple': V / fT,
                    'business_growth': ann(G), 'multiple_change': ann(M), 'cash_yield': ann(C),
                    'total_annualized': ann((V + D) / P), 'formula': _decomposition.__doc__.split('\n')[0] + ' ' +
                    '(V_T+D_T)/P_0 = (F_T/F_0) * ((V_T/F_T)/(P_0/F_0)) * ((V_T+D_T)/V_T); each factor annualised as x^(1/T)-1.'}
    return {'status': 'unavailable', 'reason': 'per-share FCF and NOPAT are not positive at both ends; a multiple is not meaningful'}


def _index(inputs):
    idx = (inputs or {}).get('index')
    if idx is None:
        return deepcopy(INDEX_DEFAULT)
    if not isinstance(idx, dict):
        raise ValueError('inputs.index must be {value, basis, source}')
    v = number(idx.get('value'), 'index.value', -.05, .25)
    for k in ('basis', 'source'):
        if not isinstance(idx.get(k), str) or not idx[k].strip():
            raise ValueError(f'inputs.index.{k}: nonempty text required')
    return {'value': v, 'low': _num(idx.get('low')), 'high': _num(idx.get('high')), 'basis': idx['basis'],
            'source': idx['source'], 'provenance': idx.get('provenance', 'vendor_estimate')}


def _hurdle(index, risk_level):
    prem = RISK_PREMIUM[risk_level if risk_level in RISK_PREMIUM else 'unknown']
    return round(index['value'] + prem, 10), prem


def price_for_hurdle(case, hurdle):
    """Price at which the base IRR equals the hurdle = the base value with cost of equity (and terminal
    WACC, same convention as implied_cost_of_equity) moved to the hurdle. Exact, no search needed."""
    if case is None or case.get('method') not in ('operating', 'residual_income') or hurdle is None:
        return None
    base = next(s for s in case['scenarios'] if s['name'] == 'base')
    v = _shifted_value(case, 'base', hurdle - base['cost_of_equity'])
    if v is None:
        return {'value': None, 'status': 'invalid', 'reason': 'hurdle makes the terminal invalid (WACC <= growth) or the case unfunded'}
    return {'value': v, 'status': 'solved', 'hurdle': hurdle,
            'formula': 'base value at cost of equity = hurdle (terminal WACC shifted equally); base IRR at this price equals the hurdle'}


def _forecast_view(an, res, view):
    five = view.get('five_year')
    if not res or not five:
        return None
    rows = next((s['forecast'] for s in res['scenarios'] if s['name'] == 'base'), [])
    end = next((r for r in rows if r['end'] == five['date'] and 'revenue' in r), None)
    ttm = (an or {}).get('ttm') or {}
    rev0, end0 = _num(ttm.get('revenue')), ttm.get('period_end')
    if end is None or rev0 is None or not end0:
        return None
    d = (date.fromisoformat(end['end']) - date.fromisoformat(end['start'])).days / 365.25
    yrs = (date.fromisoformat(end['end']) - date.fromisoformat(end0)).days / 365.25
    return {'base_revenue_cagr': cagr(rev0, end['revenue'] / d, yrs), 'through': end['end'], 'from': end0}


# ---------------------------------------------------------------- gates and verdict

def _gates(parts, view, an):
    qa, card, val, rk, rv = (parts.get(k) for k in ('qa', 'card', 'val', 'rk', 'rv'))
    g = {'disqualifying': [], 'impaired': False, 'data_blocked': [], 'review_blocked': [],
         'valuation_unverified': [], 'understanding': [], 'funding': [], 'no_valuation': [], 'draft': [], 'red_flags': _forensic_flags(an)}
    reg = {r.get('id'): r for r in (rk or {}).get('register') or [] if isinstance(r, dict)}
    for i in (rk or {}).get('disqualifying') or []:
        r = reg.get(i) or {}
        item = {'id': i, 'description': r.get('description'), 'description_he': r.get('description_he'), 'category': r.get('category')}
        gate = RISK_GATES.get(i) or ('funding' if r.get('category') == 'funding' else None)
        if gate == 'impaired':
            g['impaired'] = True
            g['disqualifying'].append(item)
        elif gate:
            g[gate].append('risk register ' + i + ': ' + str(r.get('description') or i))
        else:
            g['disqualifying'].append(item)
    if view.get('base') is not None and view['base'] <= 0:
        g['impaired'] = True
    if qa is None:
        g['data_blocked'].append('data validation did not run')
    elif qa.get('status') == 'blocked':
        g['data_blocked'] += [f"critical field {f}: {((qa.get('fields') or {}).get(f) or {}).get('status', 'blocked')}" for f in qa.get('blocking') or []] or ['dataqa blocked']
    if rv is not None:
        if rv.get('status') == 'blocked' or (rv.get('status') == 'reviewed' and rv.get('price_conclusion_allowed') is False):
            g['review_blocked'] += [str(r) for r in rv.get('reasons') or []] or ['review blocked the price conclusion']
        crit = (((rv.get('mechanical') or {}).get('redteam') or {}).get('critical_failures')) or []
        if crit:
            g['valuation_unverified'] += ['critical red-team failure: ' + ', '.join(map(str, crit))]
        if rv.get('status') not in ('blocked', 'reviewed'):
            g['draft'] += ['review status ' + str(rv.get('status')) + ': ' + '; '.join(map(str, rv.get('reasons') or []))]
    else:
        g['draft'].append('independent review did not run')
    if view['is_demo']:
        g['draft'].append('demo / synthetic valuation inputs')
    if card is None:
        g['understanding'].append('model card (business understanding) missing')
    else:
        comp = (card.get('completeness') or {}).get('status')
        open_crit = [q for q in card.get('understanding') or [] if q.get('critical') and q.get('status') == 'open']
        if comp == 'insufficient' or (comp is None and open_crit):
            g['understanding'] += ['open critical question: ' + str(q.get('question') or q.get('id')) for q in open_crit] or ['model card completeness insufficient']
    method = _card(card, 'valuation', 'method')
    status = (val or {}).get('status')
    if method == 'not_reliably_valuable':
        g['no_valuation'].append('model card: not_reliably_valuable - ' + str(_card(card, 'valuation', 'why') or ''))
    elif not val or status == 'not_computed' or not (val or {}).get('result'):
        g['no_valuation'].append('valuation not computed: ' + str((val or {}).get('reason') or 'no valuation stage output'))
    elif view.get('base') is None and status != 'funding_blocked':
        g['no_valuation'].append('valuation produced no base value today')
    case, res = _case_and_result(val)
    if status == 'funding_blocked':
        g['funding'].append('valuation: unfunded cash requirement (funding_blocked)')
    if case:
        assumed = sorted({p['end'][:4] for s in case['scenarios'] if s['name'] in ('bear', 'base') for p in s.get('periods', [])
                          if p.get('financing_status') == 'assumed' and (p.get('debt_draw') or p.get('equity_proceeds'))})
        if assumed:
            g['funding'].append('assumed (not committed) financing in ' + ', '.join(assumed))
    if _card(card, 'funding', 'value') == 'needs_external' and not g['funding']:
        committed = case and any(p.get('financing_status') == 'committed' and (p.get('debt_draw') or p.get('equity_proceeds'))
                                 for s in case['scenarios'] for p in s.get('periods', []))
        if not committed:
            g['funding'].append('model card: needs external funding; no committed financing in the valuation')
    return g


def _positive(A):
    q = A['business_quality']['level']
    return q in ('strong', 'adequate') or (q == 'unknown' and 'strong' in (A['trajectory']['level'], A['growth_quality']['level']))


def _verdict(A, g, er, view, index, pfh_fn):
    """One pass over VERDICT_RULES in order; returns the first that applies with its reason and gate."""
    q, risk = A['business_quality']['level'], A['risk_and_error']['level']
    hurdle, prem = _hurdle(index, risk)
    irr, bound = er.get('base_irr'), er.get('base_irr_bound')
    pos = _positive(A)

    def out(code, gate, reason, reason_he, change, change_he):
        return {'code': code, 'label_he': VERDICTS[code], 'gate': gate, 'reason': reason, 'reason_he': reason_he,
                'what_would_change_it': change, 'what_would_change_it_he': change_he, 'hurdle_used': hurdle, 'risk_premium_used': prem}

    if g['understanding']:
        # We cannot conclude without understanding the business: an evidence gap, never a negative judgement
        # ('avoid'). Disqualifying risks found meanwhile stay listed in the reason and in the risk assessment.
        dq = [str(x['description'] or x['id']) for x in g['disqualifying']] + (['impaired equity'] if g['impaired'] else [])
        return out('insufficient_evidence', 'understanding_insufficient', 'Insufficient evidence: ' + '; '.join(g['understanding'])
                   + (f"; disqualifying risks also recorded: {'; '.join(dq)}" if dq else ''),
                   'שאלות מהותיות על איך העסק מרוויח עדיין פתוחות', ['resolve: ' + x for x in g['understanding']],
                   'מענה מבוסס ראיות לשאלות ההבנה הפתוחות והרצה מחדש')
    if g['disqualifying'] or g['impaired']:
        items = [str(x['description'] or x['id']) for x in g['disqualifying']]
        he = '; '.join(x['description_he'] for x in g['disqualifying'] if x.get('description_he')) or 'ברשימת הסיכונים יש סיכון שפוסל את ההשקעה'
        if any(x['id'] == 'gate.manipulation_signal' for x in g['disqualifying']):
            he = 'סימן לניפוח רווחים יחד עם המרה חלשה של רווח למזומן, ולכן הרווחים המדווחים אינם בסיס אמין'
        elif any(x['id'] == 'gate.distress' for x in g['disqualifying']):
            he = 'מצוקה פיננסית: תזרים שלילי וצורך קרוב בהון חדש'
        other = [x for x in g['disqualifying'] if x['id'] != 'gate.equity_impaired']
        if g['impaired']:
            if not any(x['id'] == 'gate.equity_impaired' for x in g['disqualifying']):
                items.append('impaired equity: base value today is zero')
            he = 'בתרחיש הבסיס ההתחייבויות עולות על שווי העסק, ולבעלי המניות נשאר ערך אופציה בלבד' if not other else he
        return out('avoid', 'impaired_equity' if g['impaired'] and not other else 'disqualifying_risk',
                   'Disqualifying: ' + '; '.join(items), he,
                   ['documented evidence that removes each disqualifying risk (re-run risk.assess)'], 'ראיות מתועדות שמבטלות את הסיכון הפוסל')
    for key, gate, he in (('data_blocked', 'data_blocked', None), ('review_blocked', 'review_blocked', 'הבדיקה הבלתי תלויה חסמה מסקנת מחיר'),
                          ('valuation_unverified', 'valuation_unverified', 'בדיקת האדום מצאה כשל קריטי בחישוב השווי')):
        if g[key]:
            if key == 'data_blocked':
                fields = [FIELD_HE.get(f.split(':')[0].replace('critical field ', ''), '') for f in g[key]]
                he = 'נתונים קריטיים חסרים או סותרים' + (f" ({', '.join(x for x in fields if x)})" if any(fields) else '')
            return out('insufficient_evidence', gate, 'Insufficient evidence: ' + '; '.join(g[key]), he,
                       ['resolve: ' + x for x in g[key]], 'השלמת הנתונים או ההבנה החסרים והרצה מחדש')
    if g['no_valuation']:
        if pos:
            return out('interesting_not_yet_valuable', 'no_valuation',
                       f"Business evidence is positive (quality {q}, trajectory {A['trajectory']['level']}) but "
                       + '; '.join(g['no_valuation']) + (' (funding also unresolved)' if g['funding'] else ''),
                       'עדיין אין נתונים שמאפשרים לבסס תחזית תזרים אמינה לשיטת השווי המתאימה לחברה',
                       ['a reliable valuation basis: sustained positive unit economics / cash flow that a cash-flow model can underwrite'],
                       'אבן דרך עסקית שתאפשר הערכת שווי אמינה, למשל תזרים מזומנים חיובי ויציב')
        if q == 'weak':
            return out('index_preferable', 'no_valuation', 'No valuation and weak business quality: no demonstrated edge over an index',
                       'איכות העסק חלשה ואין הערכת שווי שמראה יתרון על פני מדד', ['evidence of durable improvement in returns and a valuation basis'],
                       'שיפור מתמשך ומוכח בתשואה על ההון')
        return out('insufficient_evidence', 'no_valuation', 'No valuation and business quality not established: ' + '; '.join(g['no_valuation']),
                   'איכות העסק עוד לא הוכחה ואין הערכת שווי אמינה', ['evidence of business quality and an executable valuation'],
                   'ראיות לאיכות העסק והערכת שווי שאפשר להריץ')
    if g['funding']:
        if pos:
            return out('promising_funding_conditional', 'unfunded_plan', 'Business evidence positive but the plan needs external funding that is not secured: '
                       + '; '.join(g['funding']), 'התוכנית העסקית דורשת מימון חיצוני שעוד לא הובטח, ולכן בעלי המניות של היום חשופים לדילול או לעיכוב',
                       ['committed financing at terms that preserve per-share value, then re-run the valuation'],
                       'מימון מחייב בתנאים שלא מוחקים את הערך למניה')
        if q == 'weak':
            return out('avoid', 'unfunded_plan', 'Weak business evidence and an unfunded plan: ' + '; '.join(g['funding']),
                       'העסק חלש והתוכנית תלויה במימון שעוד לא הובטח', ['secured funding and evidence of improving economics'],
                       'מימון מובטח וראיות לשיפור בכלכלת העסק')
        return out('insufficient_evidence', 'unfunded_plan', 'Business quality not established and plan unfunded: ' + '; '.join(g['funding']),
                   'איכות העסק לא הוכחה והתוכנית תלויה במימון שלא הובטח', ['secured funding and evidence of business quality'],
                   'מימון מובטח וראיות לאיכות העסק')
    if irr is not None:
        meets, irr_txt = irr >= hurdle, _pct(irr)
    elif bound:
        meets = True if bound['type'] == 'above' and bound['rate'] >= hurdle else False if bound['type'] == 'below' and bound['rate'] <= hurdle else None
        irr_txt = f"{bound['type']} {_pct(bound['rate'])}"
    else:
        meets = A['price_attractiveness']['level'] in ('strong', 'adequate') if A['price_attractiveness']['level'] in POINTS else None
        irr_txt = 'n/a (no cash-flow IRR; discount to base value used)'
    pfh_v = (pfh_fn(hurdle) or {}).get('value')
    price_change = ([f'price at or below {pfh_v:,.2f} (base IRR = hurdle {_pct(hurdle)})'] if pfh_v else []) + \
                   ['new evidence that raises the base case (see what_must_be_true)']
    price_change_he = (f'מחיר של כ־{_fmt_price(pfh_v)} ומטה (שבו התשואה בתרחיש הבסיס שווה לסף)' if pfh_v else 'מחיר נמוך יותר או ראיות לתרחיש בסיס טוב יותר')
    idx, irr_he, hur_he = index['value'], _he_pct(irr, hurdle) if irr is not None else irr_txt, _he_pct(hurdle, irr)
    if meets is None:
        return out('insufficient_evidence', None, 'Price return could not be determined (IRR not solvable and no usable base value)',
                   'אי אפשר היה לחשב את התשואה הצפויה במחיר הנוכחי', ['an executable base case with a solvable IRR'], 'תרחיש בסיס שאפשר לחשב')
    if q in ('strong', 'adequate'):
        loss, tol = view.get('loss_to_bear'), BEAR_LOSS_TOLERANCE[q]
        if meets and (loss is None or loss <= tol):
            return out('attractive_at_price', None,
                       f'{q} business; base IRR {irr_txt} >= hurdle {_pct(hurdle)} (index {_pct(idx)} + {prem * 100:.0f}pp risk premium); '
                       f"bear-case loss {_pct(loss, 0)} within the {tol:.0%} tolerance for {q} quality",
                       f'עסק באיכות {LEVEL_HE[q]} שהתשואה השנתית המשתמעת ממנו בתרחיש הבסיס, כ־{irr_he}, עוברת את הסף הנדרש של כ־{hur_he} (מדד רחב כ־{_he_pct(idx)} ועוד פרמיית סיכון)',
                       [f'price above {pfh_v:,.2f} (base IRR would fall below the hurdle)' if pfh_v else 'a higher price',
                        'evidence that falsifies a base-case assumption (forecast falsifiers)', f'bear-case loss beyond {tol:.0%}'],
                       f'מחיר גבוה מכ־{_fmt_price(pfh_v)} או ראיה שמפריכה את הנחות תרחיש הבסיס' if pfh_v else 'ראיה שמפריכה את הנחות תרחיש הבסיס')
        if meets:
            safe = view['bear'] / (1 - tol) if view.get('bear') else None
            return out('quality_but_expensive', None,
                       f'{q} business and base IRR {irr_txt} clears the hurdle {_pct(hurdle)}, but the bear-case loss {_pct(loss, 0)} '
                       f'exceeds the {tol:.0%} tolerance for {q} quality: not enough margin of safety',
                       f'העסק באיכות {LEVEL_HE[q]} והתשואה בתרחיש הבסיס עוברת את הסף, אבל בתרחיש השמרני השווי נמוך בכ־{_he_pct(loss)} מהמחיר, יותר ממרווח הביטחון המתאים',
                       ([f'price at or below {safe:,.2f} (bear loss within tolerance)'] if safe else []) + ['a better-supported bear case'],
                       f'מחיר של כ־{_fmt_price(safe)} ומטה' if safe else 'תרחיש שמרני טוב יותר')
        below_index = irr is not None and irr < idx
        return out('quality_but_expensive', None,
                   f'{q} business, but base IRR {irr_txt} < hurdle {_pct(hurdle)} (index {_pct(idx)} + {prem * 100:.0f}pp)'
                   + (' and below the index assumption itself' if below_index else ''),
                   f'העסק באיכות {LEVEL_HE[q]}, אבל במחיר הנוכחי התשואה השנתית המשתמעת בתרחיש הבסיס היא כ־{irr_he}, מתחת לסף של כ־{hur_he}'
                   + (f' ואף מתחת לתשואה המשוערת של מדד רחב (כ־{_he_pct(idx)})' if below_index else ''),
                   price_change, price_change_he)
    if q == 'unknown' and meets:
        if 'strong' in (A['trajectory']['level'], A['growth_quality']['level']):
            return out('interesting_not_yet_valuable', None,
                       f'Model base IRR {irr_txt} clears the hurdle, but business quality is not yet demonstrated; the price edge rests on unproven economics',
                       'המודל מראה תשואה שעוברת את הסף, אבל איכות העסק עוד לא הוכחה ולכן השווי עדיין לא אמין',
                       ['evidence of business quality (unit economics, returns on capital)'], 'ראיות לאיכות העסק, כמו כלכלת יחידה ותשואה על ההון')
        return out('insufficient_evidence', None, f'Model base IRR {irr_txt} clears the hurdle but business quality is unknown',
                   'המודל מראה תשואה סבירה, אבל אין מספיק ראיות לאיכות העסק', ['evidence of business quality'], 'ראיות לאיכות העסק')
    if q == 'weak' and meets:
        return out('index_preferable', None, f'Base IRR {irr_txt} clears the hurdle on the model, but business quality is weak: a low price alone is not an opportunity',
                   'המחיר נראה נמוך לפי המודל, אבל איכות העסק חלשה, ומחיר נמוך לבדו אינו הזדמנות',
                   ['evidence that the weakness is temporary (returns on capital recovering above the ~8% cost of capital)'],
                   'ראיות שהחולשה זמנית, למשל התאוששות בתשואה על ההון')
    return out('index_preferable', None, f'No demonstrated quality edge ({q}) and base IRR {irr_txt} is not above index + risk premium ({_pct(hurdle)})',
               f'אין יתרון איכות מוכח, והתשואה המשתמעת (כ־{irr_he}) אינה עוברת את תשואת המדד בתוספת פרמיית סיכון (כ־{hur_he})',
               price_change + ['evidence of business quality'], price_change_he)


def _fmt_price(v):
    if v is None:
        return 'לא ידוע'
    return f'{v:,.0f}' if abs(v) >= 100 else f'{v:,.1f}' if abs(v) >= 10 else f'{v:,.2f}'


# ---------------------------------------------------------------- confidence

def _confidence(g, A, view, rk, card, val, qa):
    hard, soft = [], []
    if g['draft'] and view['is_demo']:
        hard.append(('demo valuation inputs (synthetic)', 'זו דוגמה מומצאת, לא חברה אמיתית'))
    if g['data_blocked']:
        hard.append(('critical data blocked: ' + '; '.join(g['data_blocked']), 'נתונים קריטיים חסרים או סותרים'))
    if g['understanding']:
        hard.append(('business understanding insufficient', 'שאלות מהותיות על העסק עדיין פתוחות'))
    if g['valuation_unverified']:
        hard.append(('valuation unverified by the red team', 'בדיקת האדום לא אישרה את חישוב השווי'))
    if g['no_valuation']:
        soft.append(('no reliable valuation', 'אין עדיין הערכת שווי אמינה'))
    if [d for d in g['draft'] if 'demo' not in d]:
        soft.append(('not independently reviewed (draft)', 'הניתוח עוד לא עבר בדיקה בלתי תלויה'))
    if g['red_flags']:
        soft.append(('forensic red flag: ' + ', '.join(g['red_flags']), 'בדיקות הדוחות הדליקו דגל אדום שעוד לא הוסבר'))
    du = {x['signal']: x for x in A['data_uncertainty']['signals']}
    if (du.get('dataqa') or {}).get('value') == 'limited':
        soft.append(('critical fields single-source or stale: ' + ', '.join((qa or {}).get('limited') or []),
                     'חלק מהנתונים הקריטיים מגיעים ממקור יחיד או שהם ישנים'))
    if (du.get('source_access') or {}).get('value') in ('secondary_access', 'no_primary_source'):
        soft.append(('opening balance not read from the primary filing', 'נתוני המאזן לא נקראו ישירות מהדוח עצמו'))
    if A['data_uncertainty'].get('override'):
        soft = [x for x in soft if 'single-source' not in x[0] and 'primary filing' not in x[0]]
    if ((rk or {}).get('error_size') or {}).get('level') == 'high':
        soft.append(('large possible valuation error', 'טווח הטעות האפשרי בהערכה גדול'))
    if (view.get('terminal_share') or 0) > .8:
        soft.append(('more than 80% of base value from the terminal', 'רוב השווי מגיע משנים רחוקות'))
    band = ((val or {}).get('diagnostics') or {}).get('discount_rate_band') or {}
    if band.get('quote_inside_band'):
        soft.append(('one point of required return flips cheap/expensive', 'שינוי של נקודת אחוז אחת בתשואה הנדרשת הופך את המסקנה'))
    if any('forecast is more than 10pp above' in (s.get('note') or '') for s in A['trajectory']['signals']):
        soft.append(('base forecast growth far above own history', 'התחזית מניחה צמיחה מהירה בהרבה מההיסטוריה'))
    if any('7 calendar days' in w for w in (((val or {}).get('result') or {}).get('warnings') or [])):
        soft.append(('valuation quote older than a week', 'המחיר שהושווה ישן מיותר משבוע'))
    if (_card(card, 'completeness', 'status') == 'partial'):
        soft.append(('part of the business understanding is asserted, not evidenced', 'חלק מההבנה העסקית נשען על טענות ולא על ראיות'))
    reasons = hard + soft
    level = 'low' if hard or len(soft) >= 3 else 'medium' if soft else 'high'
    return {'level': level, 'level_he': {'low': 'נמוכה', 'medium': 'בינונית', 'high': 'גבוהה'}[level],
            'reasons': [r[0] for r in reasons], 'reasons_he': [r[1] for r in reasons],
            'rule': 'low if any hard reason (demo, blocked data, insufficient understanding, unverified valuation) or 3+ other reasons; medium with 1-2; high with none'}


# ---------------------------------------------------------------- overrides

def _known_ids(parts):
    fb, card, fc, val, rk = (parts.get(k) for k in ('fb', 'card', 'fc', 'val', 'rk'))
    ids = set((fb or {}).get('sources') or {}) | {q.get('id') for q in (fb or {}).get('qualitative') or []}
    for q in (card or {}).get('understanding') or []:
        ids |= set(q.get('evidence_ids') or []) | {q.get('id')}
    for d in ((card or {}).get('dimensions') or {}).values():
        ids |= set((d or {}).get('evidence_ids') or [])
    for a in (fc or {}).get('assumptions') or []:
        ids |= {a.get('id')} | set(a.get('source_ids') or [])
    for r in (rk or {}).get('register') or []:
        ids |= {r.get('id')} | set(r.get('evidence_ids') or [])
    case = (val or {}).get('case') or {}
    ids |= {s.get('id') for s in case.get('sources') or []} | {a.get('id') for a in case.get('assumptions') or []}
    return {i for i in ids if isinstance(i, str)}


def _check_override(o, known, seen):
    if not isinstance(o, dict):
        return 'override must be an object'
    t, v, why, ev = o.get('target'), o.get('value'), o.get('justification'), o.get('evidence_ids')
    if t not in ASSESSMENTS and t not in ('verdict', 'red_flags'):
        return f'unknown target {t!r}'
    if t in seen:
        return f'only one override per target ({t})'
    if t == 'verdict' and v not in VERDICTS:
        return f'verdict must be one of {sorted(VERDICTS)}'
    if t in ASSESSMENTS and v not in LEVELS:
        return 'assessment level must be strong, adequate, weak or unknown'
    if t == 'red_flags' and (not isinstance(v, list) or not v or set(v) - set(RED_FLAG_KEYS)):
        return 'red_flags value must list ' + ' and/or '.join(RED_FLAG_KEYS)
    if not isinstance(why, str) or len(why.split()) < 5:
        return 'documented justification required (at least 5 words)'
    if not isinstance(ev, list) or not ev or not all(isinstance(x, str) and x for x in ev):
        return 'evidence_ids required (nonempty list of ids)'
    unknown = [x for x in ev if x not in known]
    if unknown:
        return 'unknown evidence ids (not in FactBook sources/qualitative, model card, forecast, risk register or valuation case): ' + ', '.join(unknown)
    return None


# ---------------------------------------------------------------- entry point

def decide(parts, inputs=None):
    """parts = {'fb','qa','an','card','fc','val','rk','rv'} (any may be None when a stage failed).
    inputs = {'index': {value, basis, source}?, 'overrides': [{target, value, justification, evidence_ids}]?}."""
    inputs = inputs or {}
    if not isinstance(inputs, dict) or set(inputs) - {'index', 'overrides'}:
        raise ValueError('decision inputs accept index and overrides')
    overrides = inputs.get('overrides') or []
    if not isinstance(overrides, list):
        raise ValueError('inputs.overrides must be a list')
    parts = parts or {}
    fb, qa, an, card, val, rk = (parts.get(k) for k in ('fb', 'qa', 'an', 'card', 'val', 'rk'))
    index = _index(inputs)
    view = _view(fb, val)
    case, res = _case_and_result(val)
    qcase = _quote_case(case, view['price']) if res else None
    diag = ((val or {}).get('diagnostics') or {}).get('implied_cost_of_equity') if qcase is case else None
    irrs = {s: (_irr(qcase, s, diag) if qcase is not None and view.get('base') is not None else
                {'value': None, 'status': 'not_computable', 'bound': None, 'reason': 'no executed base value'})
            for s in ('bear', 'base', 'bull')}
    fview = _forecast_view(an, res, view)
    fs = financial_strength(an, card)
    A = {'business_quality': business_quality(an, card, fs['level']), 'trajectory': trajectory(an, card, fview),
         'growth_quality': growth_quality(an), 'financial_strength': fs,
         'management_capital_allocation': management_capital_allocation(an, card),
         'data_uncertainty': data_uncertainty(qa, val, fb), 'risk_and_error': risk_and_error(rk, an, view)}
    hurdle, prem = _hurdle(index, A['risk_and_error']['level'])
    base_irr = irrs['base']['value']
    er = {'base_irr': base_irr, 'bear_irr': irrs['bear']['value'], 'bull_irr': irrs['bull']['value'],
          'base_irr_bound': irrs['base']['bound'], 'irr_status': {k: {kk: vv for kk, vv in v.items() if kk != 'value'} for k, v in irrs.items()},
          'price': view['price'],
          'decomposition': _decomposition(qcase, res, view, an) if qcase is not None else {'status': 'unavailable', 'reason': 'no executed valuation'},
          'index_expected_return': index, 'risk_premium': prem, 'hurdle': hurdle,
          'hurdle_rule': 'index expected return + risk premium by risk_and_error level ' + json.dumps(RISK_PREMIUM),
          'price_for_hurdle': price_for_hurdle(qcase, hurdle) if qcase is not None and view.get('base') is not None else None,
          'owner_aspiration': OWNER_ASPIRATION,
          'meets_owner_aspiration_14pct': None if base_irr is None else base_irr >= OWNER_ASPIRATION,
          'semantics': ('IRR = cost of equity at which the scenario value equals today\'s price, cash flows held fixed '
                        '(valuation_tools.implied_cost_of_equity); a conditional return, not a forecast. The 14% aspiration is a comparison only.')}
    pfh = (er['price_for_hurdle'] or {}).get('value')
    if pfh is not None and view['price']:
        er['price_for_hurdle']['change_from_price'] = pfh / view['price'] - 1
    A['price_attractiveness'] = price_attractiveness(er, view, (val or {}).get('what_must_be_true'), an)
    A = {k: A[k] for k in ASSESSMENTS}
    g = _gates(parts, view, an)
    cache = {round(hurdle, 12): er['price_for_hurdle']}

    def pfh_fn(h):
        if round(h, 12) not in cache:
            r = price_for_hurdle(qcase, h) if qcase is not None and view.get('base') is not None else None
            if r and r.get('value') is not None and view['price']:
                r['change_from_price'] = r['value'] / view['price'] - 1
            cache[round(h, 12)] = r
        return cache[round(h, 12)]

    mech = _verdict(A, g, er, view, index, pfh_fn)
    # Overrides: assessments first (re-evaluated through the same gates), then at most one verdict override.
    known, seen, records, pending = _known_ids(parts), set(), [], None
    eff_A, red_release = deepcopy(A), None
    for o in overrides:
        err = _check_override(o, known, seen)
        rec = {k: o.get(k) for k in ('target', 'value', 'justification', 'evidence_ids')} if isinstance(o, dict) else {'target': None}
        if err:
            records.append({**rec, 'status': 'rejected', 'reason': err})
            continue
        seen.add(o['target'])
        if o['target'] == 'price_attractiveness' and base_irr is not None:
            records.append({**rec, 'status': 'rejected', 'reason': 'price attractiveness is arithmetic on the executed valuation; '
                            'change the forecast inputs (forecast.build overrides) instead'})
            continue
        if o['target'] in ASSESSMENTS:
            a = eff_A[o['target']]
            a.update(mechanical_level=a['level'], level=o['value'],
                     override={'value': o['value'], 'justification': o['justification'], 'evidence_ids': o['evidence_ids']})
            records.append({**rec, 'status': 'accepted', 'reason': 'assessment level replaced; mechanical level kept', 'effect': OVERRIDE_EFFECT[o['target']]})
        elif o['target'] == 'red_flags':
            red_release = o if g['red_flags'] and set(g['red_flags']) <= set(o['value']) else None
            records.append({**rec, 'status': 'accepted' if red_release else 'rejected',
                            'reason': 'documented override covers every raised forensic flag; the flags stay on record' if red_release else
                            ('must name every raised forensic flag: ' + ', '.join(g['red_flags'])) if g['red_flags'] else 'no forensic flag raised'})
        else:
            pending = (o, rec)
    eff = _verdict(eff_A, g, er, view, index, pfh_fn) if any(r['status'] == 'accepted' and r['target'] in ASSESSMENTS for r in records) else deepcopy(mech)
    if pending:
        o, rec = pending
        gate = eff.get('gate')
        if gate and o['value'] not in GATE_ALLOWED.get(gate, set()):
            records.append({**rec, 'status': 'rejected', 'reason': f"cannot lift the '{gate}' gate behind '{eff['code']}' to '{o['value']}' "
                            f"(allowed: {', '.join(sorted(GATE_ALLOWED.get(gate, set())))}); resolve the gate's cause instead"})
        else:
            records.append({**rec, 'status': 'accepted', 'reason': 'analyst verdict replaces the mechanical verdict; both are stored'})
            eff = {**eff, 'code': o['value'], 'label_he': VERDICTS[o['value']], 'overridden_from': eff['code'],
                   'reason': f"Analyst override ({o['justification']}); mechanical: {eff['reason']}",
                   'what_would_change_it': ['the evidence behind the override is contradicted: ' + ', '.join(o['evidence_ids'])] + eff['what_would_change_it']}
    conf = _confidence(g, eff_A, view, rk, card, val, qa)
    withheld = list(g['draft'])
    if g['red_flags'] and not red_release:
        withheld.append('forensic red flag raised (' + ', '.join(g['red_flags']) + '); price call withheld until explained with evidence')
    for v in (mech, eff):
        v['confidence'] = conf['level']
        v['status'] = 'draft' if withheld else 'final'
        v['price_claim_withheld'] = bool(withheld)
        v['withheld_reasons'] = withheld
        v['price_for_hurdle'] = pfh_fn(v['hurdle_used']) if v['code'] in ('attractive_at_price', 'quality_but_expensive', 'index_preferable') else None
    for k, a in eff_A.items():
        a['level_he'] = LEVEL_HE[a['level']]
    company = (fb or {}).get('company') or ({'ticker': case['ticker'], 'currency': case['currency']} if case else {})
    out = {'decision_version': 1, 'as_of': (fb or {}).get('as_of') or (case or {}).get('as_of'),
           'company': {k: company.get(k) for k in ('ticker', 'name', 'currency')},
           'price': {k: view.get(k) for k in ('price', 'currency', 'price_as_of', 'price_source', 'price_kind')},
           'assessments': eff_A, 'mechanical_assessments': {k: a['level'] for k, a in A.items()},
           'expected_return': er, 'verdict': eff, 'mechanical_verdict': mech, 'overrides': records, 'confidence': conf,
           'valuation_view': {k: v for k, v in view.items() if k not in ('price_source',)},
           'gates': g, 'rules': {'verdict_precedence': VERDICT_RULES, 'risk_premium': RISK_PREMIUM,
                                 'bear_loss_tolerance': BEAR_LOSS_TOLERANCE, 'gate_allowed_overrides': {k: sorted(v) for k, v in GATE_ALLOWED.items()}},
           'business_reason_he': _business_he(eff_A),
           'semantics': ('Four questions assessed separately and never averaged. Values are conditional estimates, not predicted prices; '
                         'no win rate is claimed. A research opinion, not an order or suitability assessment.')}
    json.dumps(out, allow_nan=False)
    return out


SIGNAL_HE = {'roic': 'תשואה על ההון המושקע', 'operating_margin': 'שולי רווח תפעולי', 'gross_margin': 'שולי רווח גולמי',
             'fcf_conversion': 'המרת רווח למזומן', 'competitive_position': 'עדויות ליתרון תחרותי'}


def _business_he(A):
    """Quality sentence without any price figure (used when a price claim is withheld)."""
    bq = A['business_quality']
    parts = []
    for s in bq['signals']:
        if s['signal'] in SIGNAL_HE and s['level'] in POINTS and s['core']:
            v = s['value']
            parts.append(SIGNAL_HE[s['signal']] + (f' של כ־{v * 100:.0f}%' if isinstance(v, float) and s['signal'] != 'fcf_conversion' else ''))
    tail = (' (' + ', '.join(parts[:2]) + ')') if parts else ''
    return f"איכות העסק {LEVEL_HE[bq['level']]}{tail}, והמגמה העסקית {LEVEL_HE[A['trajectory']['level']]}"
