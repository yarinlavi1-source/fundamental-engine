"""Investment decision: four separate questions, one closed verdict set and the index alternative.

Why: earlier routes folded quality, growth and price into one score or one price bucket, so 'excellent
business but expensive', 'interesting but not yet valuable' or 'the index is better' could not be said,
and a strong growth story could lift a weak price case. Here (1) how good the business is, (2) its state
and trajectory, (3) how attractive the stock is at today's price and (4) the risks and size of possible
error are assessed from their own evidence and are never averaged together. Business quality uses no
price input at all, so it cannot move when only the price moves; for a cyclical it is graded on cycle
averages, so it does not move with the cycle position either. Growth is a trajectory question: a great
business growing moderately keeps its quality level. A loss-making early/growth company is graded on gross
margin and EVIDENCED competitive position (a computed gross-margin proxy is not counted twice) and stays at
most 'adequate' until its unit economics are evidenced.

One required return everywhere. The index expected return is, by default, the market return of the
forecast's own cost-of-equity framework (risk-free + equity risk premium), unless the user supplies an
index assumption (never below the risk-free rate); which one was used is recorded. The required return
(hurdle) = that index return + a premium for the risk of PERMANENT loss in this business and balance sheet
(single-stock concentration, balance sheet, funding dependence, cyclicality, unproven economics of a
loss-making early/growth company, durability of quality, flags one step from a gate), shown component by
component, and never below the engine's own cost of equity for the company (which carries financial
leverage). Valuation error (scenario range, rate sensitivity, terminal share) is not a company risk: it
widens the marginal band around the hurdle and lowers confidence instead. Data and understanding gaps lower
confidence once. The owner's 14% aspiration is a separate comparison, never the hurdle and never a tuning target.

Price attractiveness is the base-case internal rate of return at today's price (cash flows held fixed)
against that hurdle; the bear-case margin of safety is measured at the same required return. A change in
the cost of equity moves the terminal WACC by the equity weight only (Harris-Pringle: WACC = (1 - w) Ke +
w Kd (1 - t)), for the IRR and for every value at a required return. The value the owner sees for the
decision is the price at which the base IRR equals the hurdle ("value at your required return"); the base
value at the model's cost of equity is secondary. A price inside the marginal band (hurdle +/- 1pp widened
by valuation error, the index range, and a bear loss within 5pp of its tolerance) keeps the verdict code
but marks it 'marginal' and says on which side it flips.

The verdict comes from one precedence list (VERDICT_RULES). Every route has a gate id whose allowed
analyst verdict overrides are listed in GATE_ALLOWED: disqualifying conditions (disqualifying risk,
impaired equity, blocked data, a failed valuation stage, insufficient understanding, critical red-team
failure, unfunded plan, no valuation) can never be lifted, attractive_at_price is reachable by override
only where the mechanical price test passes, and on routes shared by a negative and a positive outcome an
override is never more favourable than the mechanical verdict. An assessment upgrade can never produce a
verdict the mechanical route does not allow and never lowers the required return; only downgrades move
the premium. Upgrades need a written justification plus evidence that traces to a FactBook filing or
issuer document, or to a reported/guidance statement sourced from one (a statement dataset alone, market
feeds, vendor share counts, consensus, assumptions, analyst-written case sources and synthetic data are
rejected); conservative downgrades are never blocked. Releasing a forensic red flag needs an accepted
independent reviewer and filing evidence. Overrides are stored beside the untouched mechanical verdict and
the owner text says when a result rests on one. Thresholds are stated rules of thumb; a missing input stays
unknown, never imputed.

Funding is a gate only when the bear/base plan is blocked (an unfunded cash requirement) or rests on assumed financing whose
forecast material issue is unresolved. Financing the forecast prices (debt within capacity, equity at the issue-price rule,
dilution in per-share values) and a reviewer resolved is valued like any other path and the verdict is labelled conditional
on it; SBC settled in new shares is labelled the same way. Any unresolved forecast material issue (possible lender, unconfirmed
cyclical peak, computed moat proxy, leverage at the cap, convertibles not applied, ...) keeps the verdict a draft and is named
in the owner text. A computed moat proxy also widens the marginal band: the verdict is re-run without it.
"""
from copy import deepcopy
from datetime import date
import json
from math import isfinite, log, exp
from statistics import mean
from .facts import digest
from .finance import number
from .forecast import RF_DEFAULT, ERP_DEFAULT, STAGE
from .history import cagr
from .plain import MARGIN_BARS
from .scores import COST_OF_CAPITAL_REFERENCE
from .decision_summary import RED_FLAG_KEYS
from .valuation import value_company
from .valuation_tools import implied_cost_of_equity, shifted_value

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
    ('insufficient_evidence', 'critical data blocked, or the valuation/forecast stage failed to run (an engine failure is an '
                              'evidence gap, never a business judgement)'),
    ('interesting_not_yet_valuable', 'method not_reliably_valuable or valuation not computable, but business evidence '
                                     'positive (checked before the review gate: a missing valuation is the cause, not the review)'),
    ('insufficient_evidence', 'review blocked or critical red-team failure, or no valuation without positive business evidence'),
    ('promising_funding_conditional', 'bear/base plan blocked for funding, or resting on assumed financing whose material issue is '
                                      'unresolved, business evidence positive (a reviewed, priced financing path is valued and labelled '
                                      'conditional instead)'),
    ('attractive_at_price', 'quality strong/adequate, base IRR >= required return, bear-case loss (bear value at the same required '
                            'return) within the tolerance for that quality'),
    ('quality_but_expensive', 'quality strong/adequate, base IRR < required return (or bear-case loss beyond tolerance)'),
    ('interesting_not_yet_valuable', 'quality not yet demonstrated while trajectory/growth is strong and the model price clears the hurdle'),
    ('index_preferable', 'no quality edge (weak or unproven) and/or base IRR not above index + risk premium'),
]
INDEX_RANGE = .01            # +-1pp around the market return: equity-premium uncertainty (implied-ERP studies centre ~4-5%)
INDEX_DEFAULT = {
    'value': RF_DEFAULT + ERP_DEFAULT, 'low': RF_DEFAULT + ERP_DEFAULT - INDEX_RANGE, 'high': RF_DEFAULT + ERP_DEFAULT + INDEX_RANGE,
    'provenance': 'model_assumption', 'origin': 'engine_default',
    'source': 'engine default: forecast.RF_DEFAULT + forecast.ERP_DEFAULT (no forecast cost-of-equity components, no index input)',
    'components': {'risk_free': RF_DEFAULT, 'equity_risk_premium': ERP_DEFAULT},
    'basis': ('Market return of the same framework that sets the model cost of equity: risk-free + equity risk premium. '
              'The +-1pp range reflects equity-premium uncertainty. Not a forecast; supply inputs.index {value, basis, source, low?, high?} to replace it.')}
# Premium over the index for the risk of PERMANENT loss in this business and balance sheet (model assumptions, stated
# per component and capped). Valuation error is deliberately absent: it widens the marginal band and lowers confidence.
PREMIUM = {
    'single_stock': .01,                                                      # one stock instead of a diversified index
    'balance_sheet': {'strong': 0., 'adequate': .005, 'weak': .015, 'unknown': .005},
    'funding_dependence': {'self_funded': 0., 'needs_external': .015, 'unknown': .005},
    'cyclicality': .01,
    'unproven_economics': {s: STAGE[s]['ke'] for s in ('early', 'growth', 'turnaround')},   # loss-making: the forecast's stage add-on
    'quality_durability': {'strong': 0., 'adequate': .005, 'weak': .015, 'unknown': .01},
    'durability_flag': .005,                                                  # weak ROIC durability / competitive position under a strong-adequate grade
    'near_disqualifying_each': .01, 'near_disqualifying_max': .02,            # forensic or governance red flags one step from a gate
    'cap': .06}
OWNER_ASPIRATION = .14
# Marginal band: the hurdle is moved by BAND_STEP plus a widening by the size of possible valuation error, and the
# index low/high (+ premium) are tested too. A verdict that changes anywhere inside the band is 'marginal'.
BAND_STEP = .01
ERROR_WIDENING = {'low': 0., 'medium': .005, 'high': .01, 'unknown': .005}
BEAR_TOLERANCE_STEP = .05     # a bear loss within 5pp of its tolerance is a marginal margin-of-safety call, not a precise one
# Largest acceptable loss from today's price to the bear value today, by business quality. A stronger
# business is more likely to recover from a bad-case markdown, so it tolerates a deeper one.
BEAR_LOSS_TOLERANCE = {'strong': .40, 'adequate': .30}
ROIC_STRONG = .15                                     # well above the ~8% cost-of-capital reference
MARGIN_PROFILE = {'subscription_software': 'high_margin', 'platform_marketplace': 'high_margin', 'semiconductor': 'general',
                  'hardware_manufacturing': 'general', 'industrial': 'general', 'conglomerate': 'general',
                  'capital_intensive_infrastructure': 'general', 'consumer_brand': 'general', 'commodity': 'thin_margin'}
NOT_MARGIN_GRADED = {'financials', 'reit', 'pre_revenue_biotech'}
BALANCE_SHEET_LENDERS = {'financials'}               # banks/insurers: leverage is the business; net cash is no strength measure
COMPETITIVE_DIMS = ('moat', 'switching_costs', 'pricing_power', 'retention')
MANAGEMENT_DIMS = ('management', 'capital_allocation', 'governance')
OVERRIDE_EFFECT = {'business_quality': 'verdict quality test and bear-loss tolerance (within the mechanical route); a downgrade also '
                                       'raises the quality-durability premium component',
                   'trajectory': 'positive-business-evidence test', 'growth_quality': 'positive-business-evidence test',
                   'risk_and_error': 'recorded; does not move the hurdle (the premium comes from business and balance-sheet risk)',
                   'price_attractiveness': 'price test (only when no cash-flow IRR exists)',
                   'financial_strength': 'a downgrade raises the balance-sheet premium component; an upgrade never lowers the required return',
                   'management_capital_allocation': 'recorded; ranking only',
                   'data_uncertainty': 'confidence reasons (an evidence-backed upgrade removes the data reason; a downgrade never does)'}
DEMONSTRATED = {'growth': ('revenue_cagr_3y', '3y revenue CAGR'), 'mature_margin': ('operating_margin', 'current operating margin')}
# risk.py marks every gate as class 'disqualifying'; only some are negative judgements (avoid). The others are
# evidence gaps (insufficient_evidence) or the funding condition, routed to their own precedence step.
# Evidence gaps (rk['evidence_gaps'], class pricing) are routed the same way: understanding -> insufficient_evidence.
# Routing is by id, not category (gate.unfunded_plan has category 'execution').
RISK_GATES = {'gate.blocked_data': 'data_blocked', 'gap.understanding_insufficient': 'understanding',
              'gate.redteam_critical_failure': 'valuation_unverified', 'gate.unfunded_plan': 'funding', 'gate.equity_impaired': 'impaired'}
# Every verdict route has a gate id. An analyst verdict override must be one of the allowed outcomes for the route the
# mechanical (or assessment-overridden) verdict took. attractive_at_price is allowed only where the price test passes.
_NOT_PRICE = {'avoid', 'insufficient_evidence'}
GATE_ALLOWED = {'disqualifying_risk': {'avoid'}, 'impaired_equity': {'avoid'},
                'data_blocked': set(_NOT_PRICE), 'valuation_failed': set(_NOT_PRICE),
                'review_blocked': set(_NOT_PRICE), 'valuation_unverified': set(_NOT_PRICE),
                'understanding_insufficient': set(_NOT_PRICE), 'evidence_gap': set(_NOT_PRICE),
                'unfunded_plan': _NOT_PRICE | {'promising_funding_conditional'},
                'no_valuation': _NOT_PRICE | {'interesting_not_yet_valuable', 'index_preferable'},
                'price_undetermined': _NOT_PRICE | {'interesting_not_yet_valuable', 'index_preferable'},
                'quality_unproven': _NOT_PRICE | {'interesting_not_yet_valuable', 'index_preferable'},
                'quality_unknown': _NOT_PRICE | {'interesting_not_yet_valuable', 'index_preferable'},
                'weak_quality': _NOT_PRICE | {'index_preferable'},
                'no_quality_edge': _NOT_PRICE | {'index_preferable'},
                'below_hurdle': _NOT_PRICE | {'index_preferable', 'quality_but_expensive'},
                # the bear case is changed through the forecast inputs, never by declaring it too harsh
                'bear_loss_beyond_tolerance': _NOT_PRICE | {'index_preferable', 'quality_but_expensive'},
                'price_test_passed': set(VERDICTS)}
# How favourable a verdict is to buying the stock. Conservative outcomes (avoid / insufficient evidence) are always a
# downgrade. On routes shared by a negative and a positive outcome an override is never more favourable than the
# mechanical verdict (an 'avoid' on an unfunded plan cannot become 'promising').
FAVOUR = {'avoid': 0, 'insufficient_evidence': 1, 'index_preferable': 1, 'quality_but_expensive': 3,
          'interesting_not_yet_valuable': 3, 'promising_funding_conditional': 3, 'attractive_at_price': 4}
CONSERVATIVE = set(_NOT_PRICE)
SHARED_GATES = {'unfunded_plan', 'no_valuation'}
VERDICT_ASSESSMENTS = ('business_quality', 'trajectory', 'growth_quality', 'price_attractiveness')   # targets that can move the route
PRICE_ROUTES = ('price_test_passed', 'bear_loss_beyond_tolerance', 'below_hurdle', 'quality_unproven', 'quality_unknown',
                'weak_quality', 'no_quality_edge')
# Upgrade evidence must trace to the FactBook: a filing or issuer document, or a reported/guidance statement whose sources
# include a filing; a whole statement dataset may accompany but never carries an upgrade alone (the mechanical grade already
# read it). A price feed, a vendor share count, consensus, an assumption, a question, a risk entry, an analyst-written case
# source, an unsourced or secondary-sourced statement or synthetic data is not evidence for an upgrade. Downgrades (more
# conservative calls) need a justification and only known ids.
DOC_KINDS = {'issuer_document', 'document', 'primary_document'}
DATASET_KINDS = {'filing_derived'}
EVIDENCE_ACCEPTED = {'filing_document', 'filing_dataset', 'qualitative'}
EVIDENCE_CARRIES = {'filing_document', 'qualitative'}     # at least one of these per upgrade
EVIDENCE_BY_TARGET = {'data_uncertainty': ({'filing_document', 'filing_dataset'}, {'filing_document'}),   # the document itself
                      'red_flags': ({'filing_document', 'filing_dataset'}, {'filing_document'})}
FIELD_HE = {'shares_outstanding': 'מספר המניות', 'cash_and_liquid_investments': 'המזומן וההשקעות הנזילות',
            'investments': 'ההשקעות', 'debt': 'החוב', 'convertibles': 'האג"ח להמרה', 'post_period_events': 'אירועים אחרי תאריך הדוח',
            'revenue_ttm': 'ההכנסות בשנה האחרונה', 'operating_income_ttm': 'הרווח התפעולי בשנה האחרונה',
            'cfo_ttm': 'תזרים המזומנים מפעילות', 'capex_ttm': 'ההשקעות ברכוש קבוע', 'sbc_ttm': 'התגמול מבוסס המניות'}
DIM_HE = {'competition': 'תחרות', 'substitutes': 'תחליפים', 'customer_concentration': 'ריכוז לקוחות', 'technology_ai': 'השפעת בינה מלאכותית',
          'regulation': 'רגולציה', 'geopolitics': 'גאופוליטיקה', 'governance': 'ממשל תאגידי', 'management': 'הנהלה', 'moat': 'היתרון התחרותי',
          'retention': 'שימור לקוחות'}
PRIORITY_DIMS = ('technology_ai', 'competition', 'substitutes', 'moat', 'retention')
# Forecast material issues (forecast.build -> case.underwriting.material_issues). Any unresolved one keeps the verdict a
# draft and is named in the owner text; 'hold' issues mean the executed number is not a valuation of this business (an
# FCFF model on a lender's balance sheet, a value set by the leverage cap), so no value is shown even as a draft.
MATERIAL_HE = {'possible_lender': 'מאזן של גוף מלווה (פיקדונות ומימון הם חלק מהפעילות) שהוערך במודל של עסק תפעולי',
               'cyclical_peak_unconfirmed': 'הרווחיות הנוכחית נראית כשיא מחזורי, ועוד לא נקבע בראיות אם היא קבועה או זמנית',
               'moat_proxy': 'הרווחיות בתחזית נשענת על אומדן מחושב ליתרון תחרותי, לא על הערכה מבוססת',
               'leverage_at_cap': 'החוב גבוה ביחס לשווי ההון, ולכן השווי נקבע מתקרת המינוף של המודל ולא מהערכה',
               'convertibles_unapplied': 'אג"ח להמרה שהדילול שלהן עוד לא נכלל בשווי',
               'opening_dates_differ': 'נתוני המניות והמאזן הם מתאריכים שונים ועוד לא גושרו',
               'data_blocked': 'נתונים קריטיים חסרים או סותרים',
               'assumed_financing': 'התוכנית נשענת על מימון חיצוני מונח שעוד לא נבדק'}
MATERIAL_HOLD = {'possible_lender', 'leverage_at_cap'}
FUNDING_SCENARIOS = ('bear', 'base')               # the plan the verdict rests on (base IRR, bear margin of safety)
# risk register pricing_status (when risk.py supplies one): the decision keeps the more conservative of the register's
# claim and its own check (in the bear path, or a quantified material adjustment); synonyms map to one vocabulary
PRICING_RANK = {'not_priced': 0, 'partially_priced': 1, 'priced': 2}
PRICING_ALIASES = {'accepted_priced': 'priced', 'unpriced': 'not_priced', 'partial': 'partially_priced', 'unassessed': 'open_question',
                   'disqualifying': 'gate'}


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


def _g3(v):
    """A plain number with at most three significant figures (no unformatted floats in reasons)."""
    x = _num(v)
    if x is None:
        return v
    return f'{x:,.0f}' if abs(x) >= 1000 else f'{x:.3g}'


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
    """Evidenced competitive position. An analyst label without evidence ids is an assertion, not evidence; a 'computed'
    dimension is a proxy restating reported numbers (pricing_power = stable gross margin), which business quality already
    grades through gross margin, so it is shown but never counted a second time."""
    dims = _card(card, 'dimensions') or {}
    used, ignored = [], []
    for k in COMPETITIVE_DIMS:
        d = dims.get(k) or {}
        if d.get('assessment') in POINTS:
            proxy = d.get('source') == 'computed'
            (ignored if proxy or (d.get('source') == 'analyst' and not d.get('evidence_ids')) else used).append((k, d))
    if not used:
        return 'unknown', used, ignored
    n = len(used)
    strong = sum(d['assessment'] == 'strong' for _, d in used)
    weak = sum(d['assessment'] == 'weak' for _, d in used)
    return ('strong' if strong * 2 > n and not weak else 'weak' if weak * 2 > n else 'adequate'), used, ignored


def _roe(an):
    """Return on equity for a balance-sheet lender: analysis metric 'roe', else latest net income / average equity."""
    v = _val(an, 'roe')
    if v is not None:
        return v, [_ref(an, 'roe')], 'analysis.metrics.roe'
    ann = (an or {}).get('annual') or {}
    ends = sorted(ann)
    if not ends:
        return None, [], None
    last = ann[ends[-1]] or {}
    ni, eq = _num(last.get('net_income')), _num(last.get('equity'))
    prev = _num((ann[ends[-2]] or {}).get('equity')) if len(ends) > 1 else None
    base = (eq + prev) / 2 if eq is not None and prev is not None else eq
    if ni is None or base is None or base <= 0:
        return None, [], None
    return ni / base, [{'metric': 'net_income', 'value': ni, 'period': ends[-1]}, {'metric': 'equity', 'value': eq, 'period': ends[-1]}], \
        f'net income {ends[-1]} / ' + ('average equity' if prev is not None else 'year-end equity')


def _lender_strength(an, fc):
    """Banks and insurers: leverage IS the business, so net cash and interest coverage say nothing. Graded on ROE versus
    the cost of equity (and capital ratios when the analysis supplies them); otherwise not assessed."""
    ke = _num((((fc or {}).get('cost_of_equity') or {}).get('base')))
    coe, coe_src = (ke, 'forecast base cost of equity') if ke is not None else (COST_OF_CAPITAL_REFERENCE, 'cost-of-capital reference')
    sig = []
    roe, ev, how = _roe(an)
    rule = f'ROE >= cost of equity + 3pp strong, >= cost of equity adequate, else weak ({coe_src} {_pct(coe)})'
    if roe is None:
        sig.append(_signal('roe_vs_cost_of_equity', None, 'unknown', rule, core=True,
                           note='not assessed: ROE unavailable; net cash is not a strength measure for a bank or insurer'))
    else:
        sig.append(_signal('roe_vs_cost_of_equity', roe, _band(roe, coe + .03, coe), rule, core=True, evidence=ev,
                           shown=f'ROE {_pct(roe)} vs {_pct(coe)} ({how})'))
    cet1 = _val(an, 'cet1_ratio')
    sig.append(_signal('capital_ratio', cet1, _band(cet1, .12, .10), 'CET1 >= 12% strong, >= 10% adequate', evidence=[_ref(an, 'cet1_ratio')],
                       note=None if cet1 is not None else 'not assessed: capital ratios are not in the analysis'))
    a = _assessment(sig, min_known=1)
    a['reasons'].append('leverage, net cash and interest coverage are not graded for a balance-sheet lender')
    return a


def financial_strength(an, card, fc=None):
    if _card(card, 'business_model', 'primary') in BALANCE_SHEET_LENDERS:
        return _lender_strength(an, fc)
    sig = []
    nc, nd = _val(an, 'net_cash'), _val(an, 'net_debt_to_ebitda')
    if nc is not None and nc >= 0:
        lev = _signal('leverage', nc, 'strong', 'net cash >= 0 is strong; else net debt/EBITDA <= 1 strong, <= 3 adequate',
                      evidence=[_ref(an, 'net_cash')], shown=f'net cash {nc / 1e6:,.0f}m')
    elif nd is not None:
        lev = _signal('leverage', nd, _band(nd, 1, 3, False) if nd >= 0 else 'weak',
                      'net debt/EBITDA <= 1 strong, <= 3 adequate, else weak (negative EBITDA with net debt is weak)',
                      evidence=[_ref(an, 'net_debt_to_ebitda'), _ref(an, 'net_cash')], shown=f'{nd:.1f}x')
    elif nc is not None and (lambda e: e is not None and e <= 0)(_num((_metric(an, 'net_debt_to_ebitda') or {}).get('ebitda'))):
        # net debt with EBITDA at or below zero: the rule above calls it weak; reading it as unknown let the remaining
        # signals rate a leveraged loss-maker strong (Harrow 2026-10: net debt 208m, TTM EBITDA below zero)
        lev = _signal('leverage', None, 'weak', 'net debt with EBITDA at or below zero is weak',
                      evidence=[_ref(an, 'net_debt_to_ebitda'), _ref(an, 'net_cash')], shown=f'net debt {-nc / 1e6:,.0f}m, EBITDA <= 0')
    else:
        lev = _signal('leverage', None, 'unknown', 'net cash or net debt/EBITDA required', evidence=[_ref(an, 'net_cash')])
    sig.append(lev)
    cov = _val(an, 'interest_coverage')
    ic = _metric(an, 'interest_coverage') or {}
    if cov is None and (_num(ic.get('interest_expense')) or 0) > 0:
        sig.append(_signal('interest_coverage', None, 'weak', 'operating income at or below zero while paying interest is weak',
                           evidence=[_ref(an, 'interest_coverage')], shown='not covered'))
    else:
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
    measure unit economics, so gross margin and evidenced competitive position become core instead, and the
    grade stays at most 'adequate' until competitive position / unit economics are evidenced (gross margin
    alone is one fact). For a cyclical (model card) ROIC and operating margin are graded on cycle averages
    and the worst-year ROIC is core, so the grade does not follow the cycle position."""
    bm, stage = _card(card, 'business_model', 'primary'), _card(card, 'stage', 'value')
    opm, gm = _val(an, 'operating_margin'), _val(an, 'gross_margin')
    cyc = _card(card, 'cyclical') or {}
    cyclical = cyc.get('value') is True or cyc.get('normalize') is True
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
    series = [_num(s.get('roic')) for s in ((((an or {}).get('forensics') or {}).get('roic') or {}).get('series') or [])]
    series = [x for x in series if x is not None]
    if not graded or grower:
        sig.append(_signal('roic', roic, nm, roic_rule, note=why_nm, evidence=[_ref(an, 'roic')]))
    else:
        cyc_roic = mean(series) if cyclical and len(series) >= 3 else None
        r = cyc_roic if cyc_roic is not None else roic
        sig.append(_signal('roic', r, _band(r, ROIC_STRONG, COST_OF_CAPITAL_REFERENCE),
                           roic_rule + (f'; cyclical: mean of the last {len(series)} years' if cyc_roic is not None else ''), core=True,
                           evidence=[_ref(an, 'roic'), _ref(an, 'roic_ex_goodwill')]
                           + ([{'metric': 'forensics.roic.series', 'value': series}] if cyc_roic is not None else []),
                           shown=(f'cycle average {_pct(cyc_roic)} (latest {_pct(roic)})' if cyc_roic is not None else None),
                           note=('graded on the cycle average: the latest year is a cycle position' if cyc_roic is not None else
                                 'cyclical, but fewer than 3 years of ROIC: latest year graded' if cyclical else None)))
        if len(series) >= 3:
            sig.append(_signal('roic_durability', min(series), _band(min(series), ROIC_STRONG, COST_OF_CAPITAL_REFERENCE),
                               f'lowest ROIC of the last {len(series)} years; ' + roic_rule + ('; core for a cyclical' if cyclical else ''),
                               core=cyclical, evidence=[{'metric': 'forensics.roic.series', 'value': series}]))
    op_rule = f"{profile} bars: strong >= {bars['operating'][1]:.0%}, adequate >= {bars['operating'][2]:.0%}"
    if not graded or grower:
        sig.append(_signal('operating_margin', opm, nm, op_rule, note=why_nm, evidence=[_ref(an, 'operating_margin')]))
    else:
        norm = _num((cyc.get('normalized_operating_margin') or {}).get('value')) if cyclical else None
        m = norm if norm is not None else opm
        sig.append(_signal('operating_margin', m, _band(m, bars['operating'][1], bars['operating'][2]),
                           op_rule + ('; cyclical: model-card mid-cycle (mean) operating margin' if norm is not None else ''), core=True,
                           evidence=[_ref(an, 'operating_margin'), _ref(an, 'operating_margin_best_3y')]
                           + ([{'card': 'cyclical.normalized_operating_margin', 'value': norm}] if norm is not None else []),
                           shown=(f'cycle average {_pct(norm)} (latest {_pct(opm)})' if norm is not None else None),
                           note=('graded on the cycle average: the latest year is a cycle position' if norm is not None else
                                 'cyclical, but no normalized margin on the model card: latest year graded' if cyclical else None)))
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
    proxies = [k for k, d in ignored if d.get('source') == 'computed']
    bare = [k for k, d in ignored if d.get('source') != 'computed']
    sig.append(_signal('competitive_position', [k for k, _ in used], lvl,
                       'evidenced model-card dimensions (moat, switching costs, pricing power, retention): majority strong '
                       'and none weak -> strong; majority weak -> weak', core=grower,
                       shown=', '.join(f"{k}={d['assessment']}" for k, d in used) or None,
                       evidence=[{'dimension': k, 'assessment': d['assessment'], 'evidence_ids': d.get('evidence_ids') or [],
                                  'source': d.get('source')} for k, d in used],
                       note='; '.join(x for x in (('ignored analyst labels without evidence ids: ' + ', '.join(bare)) if bare else '',
                                                  ('computed proxies not counted (they restate reported margins already graded): '
                                                   + ', '.join(f"{k}={d['assessment']}" for k, d in ignored if k in proxies)) if proxies else '')
                                      if x) or None))
    sig.append(_signal('balance_sheet', fs_level, fs_level if fs_level in POINTS else 'unknown',
                       'financial_strength assessment (supporting)'))
    a = _assessment(sig)
    if grower and lvl == 'unknown' and a['level'] == 'strong':
        a['level'] = 'adequate'
        a['reasons'].append('capped at adequate: a loss-making early/growth company needs evidenced competitive position or unit '
                            'economics for a strong grade; gross margin alone is one reported fact' + (' (the pricing-power proxy restates it)' if proxies else ''))
        a['capped'] = 'unproven_unit_economics'
    return a


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


def risk_and_error(rk, an, view, pricing=None):
    sem = 'strong = limited downside and small possible error; weak = deep downside, large error or disqualifying risk'
    rk = rk or {}
    sig = []
    loss = view.get('loss_to_bear')
    sig.append(_signal('downside_to_bear', loss, _band(loss, .20, .40, False), 'loss from price to bear value today <= 20% strong, <= 40% adequate',
                       evidence=[{'metric': 'bear_value_today', 'value': view.get('bear'), 'provenance': 'calculation'}]))
    err = (rk.get('error_size') or {})
    sig.append(_signal('error_size', err.get('level'), {'low': 'strong', 'medium': 'adequate', 'high': 'weak'}.get(err.get('level'), 'unknown'),
                       'risk.error_size level low / medium / high (valuation error only; widens the marginal band, not the hurdle)',
                       evidence=[{'risk': 'error_size', 'reasons': err.get('reasons') or []}]))
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
    part = [p for p in pricing or [] if p['status'] == 'partially_priced']
    if part:
        extra.append('partially priced (not in the bear path; any explicit adjustment is small): '
                     + '; '.join(f"{p['id']} ({p['basis']})" for p in part[:4]))
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
    """Is the stock attractive at today's price: base IRR against the required return (hurdle); margin of safety
    against the value at the required return, base and bear values; what the price requires versus what is
    demonstrated (valuation what_must_be_true)."""
    irr, hurdle = er.get('base_irr'), er.get('hurdle')
    sig = []
    rule = 'base IRR >= required return + 2pp strong, >= required return adequate, below weak'
    if irr is not None and hurdle is not None:
        lvl = 'strong' if irr >= hurdle + .02 else 'adequate' if irr >= hurdle else 'weak'
        sig.append(_signal('base_irr_vs_hurdle', irr, lvl, rule, core=True, shown=f'{_pct(irr, 2)} vs required return {_pct(hurdle, 2)}',
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
    pfh = (er.get('price_for_hurdle') or {}).get('value')
    if pfh is not None and view.get('price'):
        extra.append(f"value at your required return ({_pct(hurdle, 2)}): {_g3(pfh)}; price {_g3(view['price'])} is "
                     f"{_pct(abs(1 - view['price'] / pfh), 0)} {'below' if view['price'] <= pfh else 'above'} it")
    if view.get('discount_to_base') is not None:
        extra.append(f"discount to base value at the model cost of equity (secondary): {_pct(view['discount_to_base'], 0)} "
                     '(1 - price/base; negative = premium)')
    ar = er.get('at_required_return') or {}
    if ar.get('loss_to_bear') is not None:
        extra.append(f"loss to bear value today at your required return ({_pct(hurdle, 2)}): {_pct(ar['loss_to_bear'], 0)} "
                     f"(bear {_g3(ar.get('bear'))}); at the model cost of equity (secondary): {_pct(view.get('loss_to_bear'), 0)}")
    elif view.get('loss_to_bear') is not None:
        extra.append(f"loss to bear value today (model cost of equity): {_pct(view['loss_to_bear'], 0)}")
    hard = []
    for w in wmbt or []:
        if not isinstance(w, dict):
            continue
        f = (lambda x: _pct(_num(x)) if _num(x) is not None else x) if w.get('unit') == 'ratio' else _g3
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


FIVE_YEAR_WINDOW_DAYS = 183


def _anniversary(start, years):
    try:
        return start.replace(year=start.year + years)
    except ValueError:                       # 29 February
        return start.replace(year=start.year + years, day=28)


def five_year_row(annual_values, as_of):
    """Valuation row for the fifth anniversary of as_of: the row dated exactly on the anniversary when the valuation
    reports it; otherwise the nearest row within +-183 days, with its real horizon in years stated (e.g. 5.1 or 4.8).
    A row outside that window is never relabelled as five years: the result then says why (status 'outside_window').
    Returns None only when there are no rows beyond today."""
    start = date.fromisoformat(as_of)
    target = _anniversary(start, 5)
    rows = [(date.fromisoformat(r['date']), r) for r in (annual_values or [])[1:] if isinstance(r, dict) and r.get('date')]
    if not rows:
        return None
    d, row = min(rows, key=lambda x: (abs((x[0] - target).days), -x[0].toordinal()))
    off = (d - target).days
    yrs = (d - start).days / 365.25
    if abs(off) > FIVE_YEAR_WINDOW_DAYS:
        return {'status': 'outside_window', 'date': None, 'row': None, 'target': target.isoformat(), 'nearest': d.isoformat(),
                'nearest_years': yrs, 'reason': f'no valuation row within {FIVE_YEAR_WINDOW_DAYS} days of the fifth anniversary '
                                                f'{target.isoformat()}; the nearest row {d.isoformat()} is {yrs:.1f} years out'}
    return {'status': 'exact' if off == 0 else 'nearest', 'date': d.isoformat(), 'years': yrs, 'row': row,
            'target': target.isoformat(), 'offset_days': off}


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
            view['notes'].append(f"valuation quote {_g3(q)} differs from the FactBook price {_g3(view['price'])}; returns use the FactBook price")
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
        view['model_cost_of_equity'] = _num((next((s for s in case['scenarios'] if s['name'] == 'base'), {}) or {}).get('cost_of_equity'))
        five = five_year_row(res['annual_values'], case['as_of'])
        if five and five.get('row') is None:
            view['five_year_unavailable'] = five['reason']
        elif five and _num(five['row'].get('base')) is not None and p:
            v5 = five['row']['base']
            tl = next((r for r in base_sc.get('timeline', []) if r['date'] == five['date']), {})
            dist = _num(tl.get('cumulative_dividend_per_share')) or 0.
            view['five_year'] = {'date': five['date'], 'years': five['years'], 'status': five['status'], 'target': five['target'],
                                 'base_value': v5, 'bear_value': _num(five['row'].get('bear')), 'bull_value': _num(five['row'].get('bull')),
                                 'cumulative_distributions_base': dist, 'cumulative_dividends_base': dist,
                                 'price_change_excluding_dividends': v5 / p - 1,
                                 'price_change_including_distributions': (v5 + dist) / p - 1,
                                 'annualized_price_change_excluding_dividends': (v5 / p) ** (1 / five['years']) - 1 if v5 > 0 else None,
                                 'rate': 'model cost of equity',
                                 'semantics': 'conditional base-case value at that date at the model cost of equity, not a predicted market price; '
                                              'distributions are base-case buybacks/dividends paid by then'}
        elif five is None:
            view['five_year_unavailable'] = 'the valuation reports no date after today'
    return view


def _quote_case(case, price):
    if case is None or price is None or abs(case['quote']['price'] - price) < 1e-12:
        return case
    c = deepcopy(case)
    c['quote']['price'] = price
    return c


_IRR_MEMO = {}
SCENARIOS = ('bear', 'base', 'bull')


def _debt_weight(case, fc, base_value):
    """Debt weight w of the forecast's own relevering (Harris-Pringle: Ke = Ku + (Ku - Kd) D/E, WACC = Ku - w Kd t, so a
    change in the cost of equity moves the WACC by (1 - w) times that change). Fallback for a case without forecast
    leverage: opening debt less cash and marketable investments against the base value of equity."""
    lev = (((((fc or {}).get('cost_of_equity') or {}).get('components')) or {}).get('leverage')) or {}
    w = _num(lev.get('debt_weight'))
    if w is not None:
        return min(max(w, 0.), .95), 'forecast.cost_of_equity.components.leverage.debt_weight'
    op = (case or {}).get('opening') or {}
    nd = (_num(op.get('debt')) or 0.) - (_num(op.get('unrestricted_cash')) or 0.) - (_num(op.get('marketable_investments')) or 0.)
    sh = _num(op.get('shares'))
    if nd <= 0 or not sh or not base_value or base_value <= 0:
        return 0., 'no net debt in the opening balance'
    return min(nd / (nd + base_value * sh), .95), 'opening net debt / (net debt + base value x opening shares)'


def _at_rate(case, rate, w, names=None):
    """Copy of the case with each named scenario's cost of equity set to `rate`; an operating case's terminal WACC moves by
    the equity weight (1 - w) times the change (Harris-Pringle), never 1:1, so a levered terminal does not explode."""
    c = deepcopy(case)
    for s in c['scenarios']:
        if names is None or s['name'] in names:
            shift = rate - s['cost_of_equity']
            s['cost_of_equity'] = rate
            if c['method'] == 'operating':
                s['terminal']['wacc'] += (1 - w) * shift
    return c


def _irr(case, scenario, diag=None, w=0.):
    """Annual return that equates the scenario value with today's price (cash flows fixed).
    The solve is a pure function of (case, scenario, w), so it is memoised by the case digest. Without net debt (w = 0)
    the convention equals valuation_tools.implied_cost_of_equity and its diagnostic is reused."""
    if w <= 1e-12:
        d = (diag or {}).get(scenario) or {}
        if d.get('status') == 'solved' and _num(d.get('implied_cost_of_equity')) is not None:
            return {'value': d['implied_cost_of_equity'], 'status': 'solved', 'bound': None}
    key = (digest(case), scenario, round(w, 9))
    if key not in _IRR_MEMO:
        if len(_IRR_MEMO) > 512:
            _IRR_MEMO.clear()
        _IRR_MEMO[key] = _solve_irr(case, scenario) if w <= 1e-12 else _solve_irr_levered(case, scenario, w)
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


def _solve_irr_levered(case, scenario, w):
    """Bisection on the cost of equity with the terminal WACC moved by the equity weight (same search range as
    implied_cost_of_equity: -8pp .. +30pp around the model rate, terminal WACC kept above terminal growth)."""
    s = next((x for x in case['scenarios'] if x['name'] == scenario), None)
    if s is None:
        return {'value': None, 'status': 'not_computable', 'bound': None, 'reason': 'unknown scenario'}
    ke, q = s['cost_of_equity'], case['quote']['price']
    low, high = max(-.08, -ke + .005), .30
    if case['method'] == 'operating':
        low = max(low, (s['terminal']['growth'] - s['terminal']['wacc'] + .0025) / (1 - w))

    def v(shift):
        try:
            return _num(value_company(_at_rate(case, ke + shift, w, (scenario,)))['annual_values'][0][scenario])
        except (ValueError, KeyError, ZeroDivisionError):
            return None
    lo_v, hi_v = v(low), v(high)
    if lo_v is None or hi_v is None:
        return {'value': None, 'status': 'invalid_search_domain', 'bound': None, 'reason': 'the search range makes the case invalid or unfunded'}
    if q > lo_v:
        return {'value': None, 'status': 'below_search_range', 'bound': {'type': 'below', 'rate': ke + low}}
    if q < hi_v:
        return {'value': None, 'status': 'above_search_range', 'bound': {'type': 'above', 'rate': ke + high}}
    for _ in range(50):
        mid = (low + high) / 2
        m = v(mid)
        if m is None:
            return {'value': None, 'status': 'invalid_search_domain', 'bound': None, 'reason': 'the case is invalid inside the search range'}
        if m > q:
            low = mid
        else:
            high = mid
    return {'value': ke + (low + high) / 2, 'status': 'solved', 'bound': None}


class _Rates:
    """Scenario annual values with every scenario's cost of equity set to a given required return (terminal WACC moved by the
    equity weight, the same convention as the IRR); cached per rate. The base value today at rate h is the price at which the
    base IRR equals h ('value at your required return'); the bear value at h is the margin-of-safety value at that return."""

    def __init__(self, case, price, w=0.):
        self.case, self.price, self.w, self.memo = case, price, w, {}
        self.base = next((s for s in (case or {}).get('scenarios') or [] if s.get('name') == 'base'), None)
        self.ok = case is not None and case.get('method') in ('operating', 'residual_income') and self.base is not None

    def values(self, rate):
        if not self.ok or _num(rate) is None:
            return None
        k = round(rate, 10)
        if k not in self.memo:
            self.memo[k] = self._value(rate)
        return self.memo[k]

    def _value(self, rate):
        try:
            return value_company(_at_rate(self.case, rate, self.w))['annual_values']
        except (ValueError, KeyError, ZeroDivisionError):
            pass
        # one scenario's terminal can be invalid at this rate while the others are valid: value them one at a time
        cols = {}
        for s in self.case['scenarios']:
            try:
                cols[s['name']] = value_company(_at_rate(self.case, rate, self.w, (s['name'],)))['annual_values']
            except (ValueError, KeyError, ZeroDivisionError):
                cols[s['name']] = None
        tmpl = next((av for av in cols.values() if av), None)
        if tmpl is None:
            return None
        return [{'date': r['date'], **{n: (cols[n][i].get(n) if cols.get(n) else None) for n in SCENARIOS}} for i, r in enumerate(tmpl)]

    def at(self, rate, scenario='base'):
        av = self.values(rate)
        return _num(av[0].get(scenario)) if av else None

    def pfh(self, rate):
        if not self.ok or _num(rate) is None:
            return None
        v = self.at(rate)
        if v is None:
            return {'value': None, 'status': 'invalid', 'hurdle': rate,
                    'reason': 'this required return makes the terminal invalid (WACC <= growth) or the case unfunded'}
        out = {'value': v, 'status': 'solved', 'hurdle': rate, 'label': 'value at your required return', 'debt_weight': self.w,
               'formula': 'base value with cost of equity = required return (terminal WACC moved by the equity weight 1 - w, '
                          'Harris-Pringle); the base IRR at this price equals the required return'}
        if self.price:
            out['change_from_price'] = v / self.price - 1
        return out


class _Scaled:
    """_Rates with each scenario's values scaled by a fixed ratio (the band test without the computed moat proxy)."""

    def __init__(self, rates, ratio):
        self.r, self.k, self.price, self.ok = rates, ratio, rates.price, rates.ok

    def values(self, rate):
        av = self.r.values(rate)
        return None if av is None else [{**row, **{s: row[s] * self.k[s] for s in SCENARIOS if _num(row.get(s)) is not None}} for row in av]

    def at(self, rate, scenario='base'):
        v = self.r.at(rate, scenario)
        return None if v is None else v * self.k[scenario]

    def pfh(self, rate):
        out = self.r.pfh(rate)
        if out and _num(out.get('value')) is not None:
            out = {**out, 'value': out['value'] * self.k['base'], 'label': 'value at your required return without the computed moat proxy'}
            if self.price:
                out['change_from_price'] = out['value'] / self.price - 1
        return out


def _moat_test(fc, qcase, view, w, rates):
    """The computed moat proxy (forecast.moat_proxy: base and bull blended toward strong-moat rules from the computed record,
    not an analyst assessment) as a marginal-band test. Without it each scenario's values are the executed values times the
    forecast's own without/with ratio (today, model cost of equity) at every rate, and the base IRR is re-solved at the
    equivalent price (price / base ratio). None when the proxy is not applied."""
    mp = (fc or {}).get('moat_proxy')
    if not isinstance(mp, dict) or rates is None or not view.get('price'):
        return None
    wi, wo = mp.get('value_today_with_proxy') or {}, mp.get('value_today_without_proxy') or {}
    out = {'weights': mp.get('weights'), 'basis': mp.get('basis'), 'value_today_with_proxy': wi, 'value_today_without_proxy': wo or None}
    k = {s: wo[s] / wi[s] if _num(wo.get(s)) is not None and _num(wi.get(s)) and wi[s] > 0 else None for s in SCENARIOS}
    if k['base'] is None or k['base'] <= 0:
        return {**out, 'status': 'unavailable', 'reason': 'the forecast reports no positive base value without the computed moat proxy'}
    k = {s: r if r is not None and r > 0 else 1. for s, r in k.items()}
    irr = _irr(_quote_case(qcase, view['price'] / k['base']), 'base', None, w)
    if irr['value'] is None and not irr['bound']:
        return {**out, 'status': 'unavailable', 'ratios': k, 'reason': 'base IRR without the computed moat proxy not solvable: ' + str(irr.get('reason'))}
    return {**out, 'status': 'computed', 'ratios': k, 'base_irr': irr['value'], 'base_irr_bound': irr['bound'], 'rates': _Scaled(rates, k)}


def _moat_public(moat):
    return None if moat is None else {k: v for k, v in moat.items() if k != 'rates'}


def _decomposition(case, res, view, an):
    """Re-rating scenario at the model's cost of equity, NOT the return at your required return and not the IRR.
    Base 5-year return split into business growth x multiple change x cash yield (log-additive, exact):
    (V_T + D_T) / P_0 = (F_T / F_0) * ((V_T / F_T) / (P_0 / F_0)) * ((V_T + D_T) / V_T), where F is per-share
    FCF after SBC (fallback: NOPAT per share), V_T the base value at the five-year date at the model cost of
    equity, D_T cumulative base distributions (buybacks or dividends), P_0 today's price. Each factor is
    annualised as factor^(1/T) - 1, so (1+g)(1+m)(1+c) = 1 + total. It assumes the stock trades at that base
    value at T (a re-rating to the model's value); base_irr instead holds the cash flows through the terminal value."""
    five = view.get('five_year')
    if not five or not case or case.get('method') != 'operating':
        return {'status': 'unavailable', 'reason': view.get('five_year_unavailable') or 'needs an operating cash-flow valuation with a five-year base value'}
    T, V, D, P = five['years'], five['base_value'], five['cumulative_distributions_base'], view['price']
    rows = next((s['forecast'] for s in res['scenarios'] if s['name'] == 'base'), [])

    def dur(r):
        return (date.fromisoformat(r['end']) - date.fromisoformat(r['start'])).days / 365.25

    fd = date.fromisoformat(five['date'])
    first = next((r for r in rows if dur(r) > .9), None)
    # shares at T: the model row that ends on (or contains) the five-year date
    at_t = next((r for r in rows if date.fromisoformat(r['start']) < fd <= date.fromisoformat(r['end'])), None)
    if at_t is None or first is None:
        return {'status': 'unavailable', 'reason': 'no model row ending on or containing the five-year date'}

    def trailing(flow):
        # the twelve months ending at T, pro rata over the model rows they overlap (the run-rate at T, whatever the boundaries)
        lo_t = date.fromordinal(fd.toordinal() - 365)
        total, covered = 0., 0
        for r in rows:
            s0, e0 = date.fromisoformat(r['start']), date.fromisoformat(r['end'])
            a, b = max(s0, lo_t), min(e0, fd)
            if b > a and e0 > s0:
                total += flow(r) * (b - a).days / (e0 - s0).days
                covered += (b - a).days
        return total / at_t['shares'] if covered >= 360 and at_t['shares'] else None
    fcf = lambda r: r['cfo'] - r['maintenance_capex'] - r['growth_capex']
    nopat = lambda r: r['ebit'] - r['cash_taxes']
    shares0 = case['opening']['shares']
    ttm = (an or {}).get('ttm') or {}
    candidates = []
    fa = _num(ttm.get('fcf_after_sbc'))
    if fa is not None:
        candidates.append(('fcf_after_sbc_per_share', fa / shares0, f"reported TTM FCF after SBC ({ttm.get('period_end')}) / opening shares",
                           'calculation from reported', fcf, ttm.get('period_end')))
    candidates.append(('fcf_after_sbc_per_share', fcf(first) / first['shares'] / dur(first), f"first full model year to {first['end']}",
                       'model_assumption', fcf, first['end']))
    candidates.append(('nopat_per_share', nopat(first) / first['shares'] / dur(first),
                       f"first full model year to {first['end']} (EBIT - cash taxes)", 'model_assumption', nopat, first['end']))
    for metric, f0, basis, prov, flow, anchor_end in candidates:
        fT = trailing(flow)
        if fT is None:
            return {'status': 'unavailable', 'reason': 'the model rows do not cover the twelve months before the five-year date'}
        if f0 > 0 and fT > 0 and V > 0 and P:
            G, M, C = fT / f0, (V / fT) / (P / f0), (V + D) / V
            ann = lambda x: exp(log(x) / T) - 1
            try:
                span = (fd - date.fromisoformat(str(anchor_end)[:10])).days / 365.25
            except ValueError:
                span = None
            # the anchor flow ends at anchor_end, not at as_of: business growth per year over the real anchor-to-end span
            g_span = exp(log(G) / span) - 1 if span and span > .5 else None
            return {'status': 'computed', 'metric': metric, 'date': five['date'], 'years': T,
                    'scenario': 're-rating to the base value at the model cost of equity',
                    'model_cost_of_equity': view.get('model_cost_of_equity'),
                    'anchor_end': anchor_end, 'anchor_span_years': span, 'business_growth_over_anchor_span': g_span,
                    'anchor': {'value': f0, 'basis': basis, 'provenance': prov},
                    'end': {'value': fT, 'basis': f"base model, twelve months to {five['date']}", 'provenance': 'model_assumption'},
                    'current_multiple': P / f0, 'exit_multiple': V / fT,
                    'business_growth': ann(G), 'multiple_change': ann(M), 'cash_yield': ann(C),
                    'total_annualized': ann((V + D) / P),
                    'semantics': ' '.join(x.strip() for x in _decomposition.__doc__.split('\n')),
                    'formula': '(V_T+D_T)/P_0 = (F_T/F_0) * ((V_T/F_T)/(P_0/F_0)) * ((V_T+D_T)/V_T); each factor annualised as x^(1/T)-1.'}
    return {'status': 'unavailable', 'reason': 'per-share FCF and NOPAT are not positive at both ends; a multiple is not meaningful'}


def _index(inputs, fc=None):
    """Index expected return: the user's assumption when supplied, else the market return of the forecast's own
    cost-of-equity components (risk-free + equity risk premium), else the same framework's engine defaults. A user index
    is never below the framework's risk-free rate (an index return is risk-free + an equity premium), gets the +-1pp
    range when it supplies none, and is labelled a user assumption."""
    idx = (inputs or {}).get('index')
    comp = (((fc or {}).get('cost_of_equity') or {}).get('components')) or {}
    rf, erp = _num(comp.get('risk_free')), _num(comp.get('equity_risk_premium'))
    if idx is None:
        if rf is None or erp is None:
            return deepcopy(INDEX_DEFAULT)
        v = rf + erp
        return {'value': v, 'low': v - INDEX_RANGE, 'high': v + INDEX_RANGE, 'provenance': 'model_assumption',
                'origin': 'forecast_cost_of_equity', 'components': {'risk_free': rf, 'equity_risk_premium': erp},
                'source': 'forecast.cost_of_equity.components: risk-free + equity risk premium',
                'basis': (f'Market return of the framework that sets the model cost of equity: risk-free {_pct(rf, 2)} + equity risk '
                          f'premium {_pct(erp, 2)}. The +-1pp range reflects equity-premium uncertainty. Supply inputs.index to replace it.')}
    if not isinstance(idx, dict):
        raise ValueError('inputs.index must be {value, basis, source}')
    v = number(idx.get('value'), 'index.value', -.05, .25)
    for k in ('basis', 'source'):
        if not isinstance(idx.get(k), str) or not idx[k].strip():
            raise ValueError(f'inputs.index.{k}: nonempty text required')
    floor, floor_src = (rf, 'forecast.cost_of_equity.components.risk_free') if rf is not None else (RF_DEFAULT, 'forecast.RF_DEFAULT')
    if v < floor:
        raise ValueError(f'inputs.index.value {_pct(v, 2)} is below the risk-free rate {_pct(floor, 2)} ({floor_src}): an index expected '
                         'return is risk-free + an equity premium, so a lower value is not a market return')
    lo, hi = _num(idx.get('low')), _num(idx.get('high'))
    if (lo is not None and lo > v) or (hi is not None and hi < v):
        raise ValueError('inputs.index: low <= value <= high required')
    rng = 'user_input'
    if lo is None or hi is None:
        lo, hi, rng = (v - INDEX_RANGE if lo is None else lo), (v + INDEX_RANGE if hi is None else hi), 'engine default +-1pp where not supplied'
    return {'value': v, 'low': lo, 'high': hi, 'range_origin': rng, 'basis': idx['basis'], 'source': idx['source'], 'origin': 'user_input',
            'provenance': idx.get('provenance', 'user_assumption'), 'risk_free_floor': floor}


def _model_ke(view, fc):
    """The engine's own cost of equity for the company (base scenario of the executed case), with its leverage part."""
    ke = _num(view.get('model_cost_of_equity'))
    if ke is None:
        return None
    comp = (((fc or {}).get('cost_of_equity') or {}).get('components')) or {}
    lev = comp.get('leverage') or {}
    ku = _num(comp.get('unlevered'))
    return {'value': ke, 'unlevered': ku, 'leverage': (_num(lev.get('ke')) - ku) if _num(lev.get('ke')) is not None and ku is not None else None,
            'd_to_e': _num(lev.get('d_to_e'))}


def _required_return(A, index, card, g, rk, an=None, ke=None):
    """Required return = max(index expected return + premium for the risk of permanent loss in this business and balance
    sheet (component by component, PREMIUM, capped), the engine's own cost of equity for the company). Valuation error is not
    part of it (it widens the marginal band). The floor means the value at the required return is never above the model's
    own base value: a levered equity is never discounted below the rate the engine itself requires of it."""
    comps = []

    def add(name, value, basis):
        comps.append({'component': name, 'value': round(value, 6), 'basis': basis})
    add('single_stock', PREMIUM['single_stock'], 'one stock instead of a diversified index: company-specific outcomes do not average out')
    fsA = A['financial_strength']
    if fsA.get('override'):
        bs, why = fsA['level'], 'financial_strength (analyst downgrade with evidence)'
    else:
        sig = [s for s in fsA.get('signals') or [] if s['signal'] != 'funding']
        bs = _assessment(sig, min_known=1)['level'] if sig else 'unknown'
        why = ('leverage, interest coverage, Altman zone and cash runway (financial_strength without the funding signal)'
               if any(s['signal'] == 'leverage' for s in sig) else 'ROE versus cost of equity and capital ratios (balance-sheet lender)')
    add('balance_sheet', PREMIUM['balance_sheet'][bs], f'balance sheet {bs}: {why}' + ('; unknown is treated as above average' if bs == 'unknown' else ''))
    fund = _card(card, 'funding', 'value')
    ext = any(c['kind'] == 'external' for c in g.get('funding_conditional') or [])
    fk = 'needs_external' if fund == 'needs_external' or g['funding'] or ext else 'self_funded' if fund == 'self_funded' else 'unknown'
    add('funding_dependence', PREMIUM['funding_dependence'][fk],
        {'needs_external': 'the plan depends on outside capital: current holders can be diluted or delayed'
                           + (' (priced financing path in the valuation)' if ext and not g['funding'] else ''),
         'self_funded': 'self-funded (model card)', 'unknown': 'funding status not established; treated as above average'}[fk])
    cyc = _card(card, 'cyclical', 'value')
    add('cyclicality', PREMIUM['cyclicality'] if cyc is True else 0.,
        'cyclical business: a downturn can force losses at the bottom of the cycle' if cyc is True else
        'not cyclical (model card)' if cyc is False else 'cyclicality not established (no premium added)')
    stage = _card(card, 'stage', 'value')
    opm, fm = _val(an, 'operating_margin'), _val(an, 'fcf_margin')
    losing = (opm is not None and opm < 0) or (fm is not None and fm < 0)
    if stage in PREMIUM['unproven_economics'] and losing:
        add('unproven_economics', PREMIUM['unproven_economics'][stage],
            f'{stage}-stage business still loss-making (operating margin {_pct(opm)}, FCF margin {_pct(fm)}): unit economics not yet proven '
            'at scale (the forecast\'s own stage add-on)')
    bq = A['business_quality']
    q = bq['level']
    add('quality_durability', PREMIUM['quality_durability'][q], f'business quality {q}' + (' (not demonstrated: treated as above average)' if q == 'unknown' else ''))
    weak = [s['signal'] for s in bq.get('signals') or [] if s['signal'] in ('roic_durability', 'competitive_position') and s['level'] == 'weak']
    if weak and q in ('strong', 'adequate'):
        add('durability_flags', PREMIUM['durability_flag'], 'weak ' + ', '.join(weak) + f' under a {q} grade: returns may not persist')
    near = list(g['red_flags']) + list((rk or {}).get('withhold_price') or [])
    if near:
        add('near_disqualifying', min(PREMIUM['near_disqualifying_each'] * len(near), PREMIUM['near_disqualifying_max']),
            'flags one step from a gate: ' + ', '.join(near))
    total = sum(c['value'] for c in comps)
    prem = min(total, PREMIUM['cap'])
    h = index['value'] + prem
    floor = {'value': (ke or {}).get('value'), 'binding': False, 'basis': "the engine's own cost of equity for this company (base scenario)"}
    if floor['value'] is not None and floor['value'] > h + 1e-12:
        lev = (ke or {}).get('leverage')
        parts = [f"model cost of equity {_pct(floor['value'], 2)}"]
        if ke.get('unlevered') is not None:
            parts.append(f"unlevered {_pct(ke['unlevered'], 2)}")
        if lev:
            parts.append(f"financial leverage +{lev * 100:.2f}pp" + (f" (D/E {ke['d_to_e']:.0%})" if ke.get('d_to_e') is not None else ''))
        add('model_cost_of_equity_floor', floor['value'] - h,
            ' '.join(['index + permanent-loss premium is below the engine\'s own cost of equity for this company (' + ', '.join(parts) + '):',
                      'the required return is never below it, so the value at the required return never exceeds the model base value']))
        floor['binding'] = True
        h = floor['value']
    return {'premium': round(h - index['value'], 10), 'permanent_loss_premium': round(prem, 10), 'hurdle': round(h, 10), 'components': comps,
            'capped': total > PREMIUM['cap'], 'index': index['value'], 'floor': floor,
            'rule': 'required return = max(index expected return + sum of permanent-loss premium components (cap '
                    f"{PREMIUM['cap']:.0%}), the model's own cost of equity); valuation error widens the marginal band instead"}


def price_for_hurdle(case, hurdle, w=0.):
    """Price at which the base IRR equals the hurdle = the base value with cost of equity moved to the hurdle (terminal WACC
    moved by the equity weight 1 - w; with no net debt this is the convention of implied_cost_of_equity). Exact, no search."""
    if case is None or case.get('method') not in ('operating', 'residual_income') or hurdle is None:
        return None
    base = next(s for s in case['scenarios'] if s['name'] == 'base')
    if w <= 1e-12:
        v = shifted_value(case, 'base', hurdle - base['cost_of_equity'])
    else:
        try:
            v = _num(value_company(_at_rate(case, hurdle, w, ('base',)))['annual_values'][0]['base'])
        except (ValueError, KeyError, ZeroDivisionError):
            v = None
    if v is None:
        return {'value': None, 'status': 'invalid', 'reason': 'hurdle makes the terminal invalid (WACC <= growth) or the case unfunded'}
    return {'value': v, 'status': 'solved', 'hurdle': hurdle,
            'formula': 'base value at cost of equity = hurdle (terminal WACC moved by the equity weight); base IRR at this price equals the hurdle'}


def _hurdle_band(req, index, err_level, rates, moat=None):
    """The marginal band: required return -/+ (1pp + widening by valuation error size), and the index low/high plus the same
    permanent-loss premium (never below the model cost of equity, like the hurdle itself). The verdict is re-run at the
    lowest and highest required return of the band. When the forecast applied a computed moat proxy, the value range also
    spans the value at the required return without it, and the verdict is re-run without it."""
    h, prem = req['hurdle'], req.get('permanent_loss_premium', req['premium'])
    fl = (req.get('floor') or {}).get('value')
    widen = ERROR_WIDENING.get(err_level if err_level in ERROR_WIDENING else 'unknown')
    step = BAND_STEP + widen
    tests = [(f'required return - {step * 100:.1f}pp', h - step), (f'required return + {step * 100:.1f}pp', h + step)]
    if _num(index.get('low')) is not None and _num(index.get('high')) is not None:
        tests += [(lab, max(x + prem, fl) if fl is not None else x + prem) for lab, x in
                  (('index low + premium', index['low']), ('index high + premium', index['high']))]
    lo, hi = min(x for _, x in tests), max(x for _, x in tests)
    rows = [{'label': lab, 'hurdle': round(x, 10), 'value_at_required_return': ((rates.pfh(x) if rates else None) or {}).get('value')} for lab, x in tests]
    pl, ph = ((rates.pfh(hi) if rates else None) or {}).get('value'), ((rates.pfh(lo) if rates else None) or {}).get('value')
    mp = None
    if (moat or {}).get('status') == 'computed' and rates:
        v = (moat['rates'].pfh(h) or {}).get('value')
        rows.append({'label': 'required return, without the computed moat proxy', 'hurdle': round(h, 10), 'value_at_required_return': v})
        if v is not None:
            pl, ph = (min(pl, v) if pl is not None else v), (max(ph, v) if ph is not None else v)
        mp = {'value_at_required_return_without_proxy': v, 'base_irr_without_proxy': moat['base_irr'], 'ratios': moat['ratios']}
    elif moat:
        mp = {'status': moat.get('status'), 'reason': moat.get('reason')}
    return {'hurdle_low': round(lo, 10), 'hurdle_high': round(hi, 10), 'step': step, 'error_level': err_level or 'unknown',
            'error_widening': widen, 'price_low': pl, 'price_high': ph, 'rows': rows,
            'bear_tolerance_step': BEAR_TOLERANCE_STEP, **({'moat_proxy': {**_moat_public(moat), **mp}} if moat else {}),
            'rule': f'required return -/+ ({BAND_STEP * 100:.0f}pp + valuation-error widening {json.dumps(ERROR_WIDENING)}), the index '
                    f'range + premium, the bear-loss tolerance -/+ {BEAR_TOLERANCE_STEP * 100:.0f}pp and, when the forecast applied a '
                    'computed moat proxy, the values without it; a verdict that changes inside the band is marginal'}


def _five_at_rate(view, rates, rate):
    five = view.get('five_year')
    if not five or rates is None or not view.get('price'):
        return None
    av = rates.values(rate)
    row = next((r for r in av or [] if r.get('date') == five['date']), None)
    v = _num((row or {}).get('base'))
    if v is None:
        return None
    D, P = five['cumulative_distributions_base'], view['price']
    tot = (v + D) / P
    return {'date': five['date'], 'years': five['years'], 'status': five['status'], 'rate': rate, 'base_value': v,
            'cumulative_distributions_base': D, 'total_value_with_distributions': v + D,
            'change_excluding_distributions': v / P - 1, 'change_including_distributions': tot - 1,
            'annualized_including_distributions': tot ** (1 / five['years']) - 1 if tot > 0 and five['years'] > 0 else None,
            'assumes': 're-rating: the price converges to the value at your required return by that date',
            'semantics': 'base-case value at that date discounted at your required return, plus the base-case distributions '
                         '(buybacks/dividends) paid by then; the change from today assumes the price converges to that value '
                         '(a re-rating), so its annual rate can differ from the base IRR; a conditional estimate, not a predicted price'}


def _forecast_view(an, res, view):
    five = view.get('five_year')
    if not res or not five:
        return None
    rows = next((s['forecast'] for s in res['scenarios'] if s['name'] == 'base'), [])
    fd = date.fromisoformat(five['date'])
    end = next((r for r in rows if 'revenue' in r and date.fromisoformat(r['start']) < fd <= date.fromisoformat(r['end'])
                and (date.fromisoformat(r['end']) - date.fromisoformat(r['start'])).days > 300), None)
    ttm = (an or {}).get('ttm') or {}
    rev0, end0 = _num(ttm.get('revenue')), ttm.get('period_end')
    if end is None or rev0 is None or not end0:
        return None
    d = (date.fromisoformat(end['end']) - date.fromisoformat(end['start'])).days / 365.25
    yrs = (date.fromisoformat(end['end']) - date.fromisoformat(end0)).days / 365.25
    return {'base_revenue_cagr': cagr(rev0, end['revenue'] / d, yrs), 'through': end['end'], 'from': end0}


def _at_required(view, rates, h):
    """Scenario values today at the required return and the loss from today's price to the bear value at that return
    (the margin-of-safety test uses the same rate as the price test)."""
    if rates is None or view.get('base') is None:
        return None
    vals = {s: rates.at(h, s) for s in SCENARIOS}
    p, bear = view.get('price'), vals['bear']
    return {'rate': h, **vals, 'loss_to_bear': max(0., 1 - bear / p) if bear is not None and p else None,
            'semantics': 'scenario values today with every cost of equity at your required return (terminal WACC moved by the equity weight)'}


def _expected(irrs, view, index, req, rates, qcase, res, an, err_level, w=0., moat=None):
    base_irr = irrs['base']['value']
    pfh = rates.pfh(req['hurdle']) if rates is not None and view.get('base') is not None else None
    er = {'base_irr': base_irr, 'bear_irr': irrs['bear']['value'], 'bull_irr': irrs['bull']['value'],
          'base_irr_bound': irrs['base']['bound'], 'irr_status': {k: {kk: vv for kk, vv in v.items() if kk != 'value'} for k, v in irrs.items()},
          'price': view['price'],
          'decomposition': _decomposition(qcase, res, view, an) if qcase is not None else {'status': 'unavailable', 'reason': 'no executed valuation'},
          'index_expected_return': index, 'risk_premium': req['premium'], 'permanent_loss_premium': req['permanent_loss_premium'],
          'premium_components': req['components'], 'model_cost_of_equity_floor': req['floor'],
          'premium_capped': req['capped'], 'hurdle': req['hurdle'], 'required_return': req['hurdle'], 'hurdle_rule': req['rule'],
          'price_for_hurdle': pfh,
          'value_at_required_return': (pfh or {}).get('value'),
          'at_required_return': _at_required(view, rates, req['hurdle']),
          'debt_weight': w,
          'model_cost_of_equity': view.get('model_cost_of_equity'),
          'base_value_at_model_cost_of_equity': view.get('base'),
          'band': _hurdle_band(req, index, err_level, rates if view.get('base') is not None else None, moat),
          'five_year_at_required_return': _five_at_rate(view, rates, req['hurdle']) if view.get('base') is not None else None,
          'owner_aspiration': OWNER_ASPIRATION,
          'meets_owner_aspiration_14pct': None if base_irr is None else base_irr >= OWNER_ASPIRATION,
          'semantics': ('IRR = cost of equity at which the scenario value equals today\'s price, cash flows held fixed, the terminal '
                        'WACC moving by the equity weight (1 - debt weight; with no net debt this is valuation_tools.implied_cost_of_equity); '
                        'a conditional return, not a forecast. The value at your required return is the price at which the base IRR equals '
                        'the required return; the bear-case margin of safety is measured at the same return; the base value at the model '
                        'cost of equity is secondary. The 14% aspiration is a comparison only.')}
    return er


# ---------------------------------------------------------------- risk pricing (priced / partially priced / not priced)

def _risk_pricing(rk, fc, view, rates):
    """'Priced' requires the risk to be in the bear (stress) path, or an explicit quantified adjustment that moves value
    materially (at least half of the base-to-bear markdown, minimum 10%). A small cost-of-equity add-on is 'partially
    priced'; a register class accepted_priced without either is partially priced too. The register's own pricing_status
    (risk.py applies the same rule) is read and the more conservative of the two is kept; a valuation_error entry (size of
    possible valuation error, risk.error_size) is marked as such, not as a company risk."""
    comps = (((fc or {}).get('cost_of_equity') or {}).get('components')) or {}
    base, bear = view.get('base'), view.get('bear')
    gap = 1 - bear / base if base and bear is not None and base > 0 else None
    material = max(.5 * gap, .10) if gap is not None and gap > 0 else .15
    ke = view.get('model_cost_of_equity')
    out = []
    for r in (rk or {}).get('register') or []:
        if not isinstance(r, dict) or not r.get('id'):
            continue
        rid, cls = r['id'], r.get('class')
        if cls == 'disqualifying':
            status, basis, eff = 'gate', 'disqualifying condition (not priced; a gate)', None
        elif r.get('in_bear_case'):
            status, basis, eff = 'priced', 'the executed bear path models it', None
        elif r.get('priced_via'):
            key = str(r['priced_via']).rsplit('.', 1)[-1]
            add = _num(comps.get(key))
            v0 = (rates.values(ke - add) or [{}])[0].get('base') if rates is not None and ke is not None and add else None
            eff = 1 - base / v0 if _num(v0) and base else None
            if eff is not None and eff >= material:
                status, basis = 'priced', f'cost-of-equity add-on {_pct(add, 2)} lowers base value {_pct(eff, 0)} (material: >= {_pct(material, 0)})'
            else:
                status = 'partially_priced'
                basis = (f'cost-of-equity add-on {_pct(add, 2)} lowers base value only {_pct(eff, 0)}; not in the bear path' if eff is not None
                         else 'claimed priced via the cost of equity, effect not quantified; not in the bear path')
        elif cls in ('accepted_priced', 'priced'):
            status, basis, eff = 'partially_priced', 'classed priced but neither in the bear path nor quantified', None
        elif r.get('status') == 'unassessed' or r.get('assessed') is False:
            status, basis, eff = 'open_question', 'not assessed: an open research question', None
        else:
            status, basis, eff = 'not_priced', 'not in the bear path and no quantified adjustment', None
        claim = r.get('pricing_status')
        claim = PRICING_ALIASES.get(claim, claim)
        if status != 'gate' and (claim in ('gate', 'open_question') or (claim in PRICING_RANK and status in PRICING_RANK
                                                                         and PRICING_RANK[claim] < PRICING_RANK[status])):
            status, basis = claim, f"risk register pricing_status '{r['pricing_status']}' (more conservative than the decision check: {basis})"
        out.append({'id': rid, 'status': status, 'basis': basis, 'value_effect': eff, 'register_class': cls,
                    'register_pricing_status': r.get('pricing_status'), 'register_pricing_basis': r.get('pricing_basis'),
                    'valuation_error': bool(r.get('valuation_error'))})
    return out


# ---------------------------------------------------------------- gates and verdict

def _released_flags(rv, reg, idx):
    """Governance red flags (withhold_price) released by the reviewer, split into supported releases and releases whose
    evidence does not address the flag: a release needs a FactBook filing/issuer document other than the flag's own
    evidence, and nothing but filing evidence (a price feed, consensus or the flag's own evidence never releases it)."""
    ro = ((rv or {}).get('reviewer') or {}).get('risk_overrides') or {}
    ok, bad = [], []
    for rid in (rv or {}).get('risk_overrides_applied') or []:
        ev = list(((ro.get(rid) or {}) if isinstance(ro.get(rid), dict) else {}).get('evidence_ids') or [])
        own = set((reg.get(rid) or {}).get('evidence_ids') or [])
        cls = {x: _evidence_class(x, idx) for x in ev}
        fresh = [x for x in ev if x not in own]
        if fresh and all(cls[x] in ('filing_document', 'filing_dataset') for x in fresh) and any(cls[x] == 'filing_document' for x in fresh):
            ok.append(rid)                                    # the flag's own evidence may be cited too, but never counts
        else:
            bad.append({'id': rid, 'evidence': {x: cls[x] for x in ev}, 'own_evidence_cited': sorted(set(ev) & own)})
    return ok, bad


def _issues(case, fc):
    """Forecast material issues of the executed case (fallback: the forecast spec)."""
    mi = ((case or {}).get('underwriting') or {}).get('material_issues')
    if mi is None:
        mi = ((((fc or {}).get('spec') or {}).get('underwriting')) or {}).get('material_issues')
    return [m for m in mi or [] if isinstance(m, dict)]


def _open_issues(issues):
    out = []
    for m in issues:
        if m.get('status') == 'resolved':
            continue
        i = str(m.get('id') or 'unnamed')
        key = 'assumed_financing' if i.startswith('assumed_financing_') else i
        out.append({'id': i, 'description': str(m.get('description') or i), 'assumption_ids': list(m.get('assumption_ids') or []),
                    'description_he': MATERIAL_HE.get(key) or f'שאלה מהותית בתחזית ({i})', 'hold': key in MATERIAL_HOLD})
    return out


def _funding(g, case, fc, issues):
    """Funding gate only on a blocked plan or an unresolved assumed-financing material issue in the bear/base plan. Financing
    the forecast prices (equity at the issue-price rule, debt within capacity, dilution in per-share values) whose material
    issue a named reviewer resolved is a valued path, labelled conditional; SBC settled in new shares is labelled too."""
    unresolved = {m.get('id') for m in issues if m.get('status') != 'resolved'}
    resolved = {m.get('id') for m in issues if m.get('status') == 'resolved'}
    info = (fc or {}).get('funding') or {}
    for s in (case or {}).get('scenarios') or []:
        nm = s.get('name')
        if nm not in FUNDING_SCENARIOS:
            continue
        mid = f'assumed_financing_{nm}'
        yrs = sorted({p['end'][:4] for p in s.get('periods', []) if p.get('financing_status') == 'assumed'
                      and (p.get('debt_draw') or p.get('equity_proceeds'))})
        f = info.get(nm) or {}
        if mid in unresolved or (yrs and mid not in resolved):
            g['funding'].append(f'{nm}: assumed (not committed) financing' + (f" in {', '.join(yrs)}" if yrs else '')
                                + f' not reviewed (material issue {mid} unresolved)')
        elif yrs:
            debt, eq = _num(f.get('debt_assumed')), _num(f.get('equity_raised'))
            ip = [x for x in f.get('issue_prices') or [] if x.get('period_end') in {e.get('period_end') for e in f.get('equity') or []}]
            how = ([f'debt {debt:,.0f}'] if debt else []) + ([f'equity {eq:,.0f}'] if eq else []) \
                + ([f"new shares at {', '.join(str(_g3(x.get('price'))) for x in ip)}"] if ip else []) + [f'material issue {mid} resolved by review']
            g['funding_conditional'].append({
                'scenario': nm, 'kind': 'external', 'years': yrs, 'debt': debt, 'equity': eq, 'new_shares': _num(f.get('new_financing_shares')),
                'issue_prices': ip, 'price_dependent': bool(f.get('price_dependent')), 'material_issue': mid,
                'text': f"{nm}: assumed financing in {', '.join(yrs)} priced in the valuation ({'; '.join(how)})"})
        if f.get('status') == 'share_settled' and _num(f.get('sbc_settlement_shares')):
            g['funding_conditional'].append({
                'scenario': nm, 'kind': 'share_settlement', 'new_shares': f['sbc_settlement_shares'], 'price_dependent': bool(f.get('price_dependent')),
                'issue_price_rule': f.get('issue_price_rule'),
                'text': f"{nm}: part of SBC settled in {f['sbc_settlement_shares']:,.0f} new shares at the issue-price rule "
                        f"({f.get('issue_price_rule') or 'n/a'})" + ('; per-share value depends on the quote' if f.get('price_dependent') else '')})


def _gates(parts, view, an, idx=None):
    qa, card, fc, val, rk, rv = (parts.get(k) for k in ('qa', 'card', 'fc', 'val', 'rk', 'rv'))
    g = {'disqualifying': [], 'evidence_gaps': [], 'impaired': False, 'data_blocked': [], 'review_blocked': [],
         'valuation_unverified': [], 'understanding': [], 'funding': [], 'funding_conditional': [], 'no_valuation': [], 'valuation_failed': [],
         'draft': [], 'material_issues': [], 'red_flags': _forensic_flags(an), 'red_flags_released': False, 'draft_codes': [],
         'withhold_price': [], 'withhold_price_released': [], 'reviewer_present': False}
    reg = {r.get('id'): r for r in (rk or {}).get('register') or [] if isinstance(r, dict)}
    released, unsupported = _released_flags(rv, reg, idx or {})
    for i in (rk or {}).get('withhold_price') or []:
        r = reg.get(i) or {}
        item = {'id': i, 'description': r.get('description'), 'description_he': r.get('description_he')}
        g['withhold_price_released' if i in released else 'withhold_price'].append(item)
    for u in unsupported:
        g['draft'].append(f"governance red flag {u['id']} released by the reviewer without filing evidence that addresses it "
                          f"({', '.join(f'{k} ({c})' for k, c in u['evidence'].items()) or 'no evidence ids'}"
                          + (f"; cites the flag's own evidence {', '.join(u['own_evidence_cited'])}" if u['own_evidence_cited'] else '')
                          + '): the price call stays withheld')
    for i in (rk or {}).get('evidence_gaps') or []:
        g['understanding' if RISK_GATES.get(i) == 'understanding' else 'evidence_gaps'].append(
            'risk register ' + i + ': ' + str((reg.get(i) or {}).get('description') or i))
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
        rc = rv.get('reviewer_check') or {}
        g['reviewer_present'] = bool(rv.get('reviewer')) and bool(rc.get('accepted', rc.get('valid')))
        g['draft_codes'] = [str(c) for c in rv.get('reason_codes') or []]
        if rv.get('status') == 'blocked' or (rv.get('status') == 'reviewed' and rv.get('price_conclusion_allowed') is False):
            g['review_blocked'] += [str(r) for r in rv.get('reasons') or []] or ['review blocked the price conclusion']
        crit = (((rv.get('mechanical') or {}).get('redteam') or {}).get('critical_failures')) or []
        if crit:
            g['valuation_unverified'] += ['critical red-team failure: ' + ', '.join(map(str, crit))]
        if rv.get('status') not in ('blocked', 'reviewed'):
            g['draft'] += ['review status ' + str(rv.get('status')) + ': ' + '; '.join(map(str, rv.get('reasons') or []))]
    else:
        g['draft'].append('review did not run (no mechanical check, no independent reviewer)')
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
    why = str((val or {}).get('reason') or '')
    if method == 'not_reliably_valuable':
        g['no_valuation'].append('model card: not_reliably_valuable - ' + str(_card(card, 'valuation', 'why') or ''))
    elif not val or why.startswith('valuation failed:') or why == 'forecast unavailable':
        # an engine/stage failure (the stage raised, or never ran) is an evidence gap, never a business judgement
        g['valuation_failed'].append('the valuation stage failed: ' + (why or 'no valuation stage output'))
        g['draft'].append('valuation stage failed (engine error): no price analysis was executed')
    elif status == 'not_computed' or not (val or {}).get('result'):
        g['no_valuation'].append('valuation not computed: ' + (why or 'no valuation stage output'))
    elif view.get('base') is None and status != 'funding_blocked':
        g['no_valuation'].append('valuation produced no base value today')
    case, res = _case_and_result(val)
    if status == 'funding_blocked':
        g['funding'].append('valuation: unfunded cash requirement (funding_blocked)')
    issues = _issues(case, fc)
    _funding(g, case, fc, issues)
    g['material_issues'] = _open_issues(issues)
    return g


def _positive(A):
    q = A['business_quality']['level']
    return q in ('strong', 'adequate') or (q == 'unknown' and 'strong' in (A['trajectory']['level'], A['growth_quality']['level']))


def _allowed(gate, code):
    """Verdict overrides allowed on a route. On a route shared by a negative and a positive outcome an override is never
    more favourable than the mechanical verdict (conservative outcomes are always allowed where the gate lists them)."""
    allowed = set(GATE_ALLOWED.get(gate, set()))
    if gate in SHARED_GATES and code in FAVOUR:
        allowed = {c for c in allowed if c in CONSERVATIVE or FAVOUR[c] <= FAVOUR[code]}
    return allowed


def _he_req(h, idx, prem, near=None, floor=False, levered=False):
    """Hebrew required return with components that visibly add up (one precision for all three numbers)."""
    d0 = 1 if near is not None and abs(h - near) < .01 else 0
    if floor:
        return f'התשואה הנדרשת, כ־{h * 100:.{d0}f}% (שיעור ההיוון שהמודל עצמו דורש מהחברה' + (', כולל סיכון המינוף)' if levered else ')')
    d = next((k for k in range(d0, 3) if round(round(idx * 100, k) + round(prem * 100, k), k) == round(h * 100, k)), 2)
    return f'התשואה הנדרשת, כ־{h * 100:.{d}f}% (מדד רחב כ־{idx * 100:.{d}f}% ועוד פרמיית סיכון של כ־{prem * 100:.{d}f}%)'


def _conditional(g):
    """Label for a price verdict that rests on a priced financing path (reviewed assumed financing) or on SBC settled in new
    shares: valued, never gated, but conditional on that issuance happening at the modelled prices."""
    items = g.get('funding_conditional') or []
    if not items:
        return None
    ext = [c for c in items if c['kind'] == 'external']
    dep = any(c.get('price_dependent') for c in items)
    he = ('בהנחה שהמימון החיצוני שהונח במודל (חוב והנפקת מניות במחירים שבתחזית, כולל הדילול) אכן יגויס' if ext else
          'בהנחה שחלק מתגמול העובדים ישולם במניות חדשות במחירי ההנפקה שבתחזית (הדילול כלול בשווי)')
    return {'kind': 'funding' if ext else 'share_settlement', 'items': items, 'price_dependent': dep,
            'text': 'conditional on ' + ('the priced financing path' if ext else 'SBC settled in new shares') + ': '
                    + '; '.join(c['text'] for c in items),
            'text_he': 'השווי מותנה: ' + he + (', והשווי למניה תלוי במחיר המניה בעת ההנפקה' if dep else '')}


def _verdict(A, g, er, view, index, rates, req, tol_shift=0.):
    """One pass over VERDICT_RULES in order; returns the first that applies with its reason and gate (route id).
    tol_shift moves the bear-loss tolerance (marginal-band test only)."""
    q = A['business_quality']['level']
    hurdle, prem = req['hurdle'], req['premium']
    irr, bound = er.get('base_irr'), er.get('base_irr_bound')
    pos = _positive(A)

    def out(code, gate, reason, reason_he, change, change_he, **extra):
        cond = _conditional(g) if gate in PRICE_ROUTES else None
        if cond:
            reason += '; ' + cond['text']
            change = list(change) + ['the financing or share issuance the valuation assumes does not happen at the modelled prices']
        return {'code': code, 'label_he': VERDICTS[code], 'gate': gate, 'reason': reason, 'reason_he': reason_he,
                'what_would_change_it': change, 'what_would_change_it_he': change_he, 'hurdle_used': hurdle, 'risk_premium_used': prem,
                'allowed_overrides': sorted(_allowed(gate, code)), **({'conditional_on': cond} if cond else {}), **extra}

    if g['understanding'] or g['evidence_gaps']:
        # We cannot conclude without understanding the business: an evidence gap, never a negative judgement
        # ('avoid'). Disqualifying risks found meanwhile stay listed in the reason and in the risk assessment.
        dq = [str(x['description'] or x['id']) for x in g['disqualifying']] + (['impaired equity'] if g['impaired'] else [])
        gaps = g['understanding'] + g['evidence_gaps']
        return out('insufficient_evidence', 'understanding_insufficient' if g['understanding'] else 'evidence_gap',
                   'Insufficient evidence: ' + '; '.join(gaps)
                   + (f"; disqualifying risks also recorded: {'; '.join(dq)}" if dq else ''),
                   'שאלות מהותיות על איך העסק מרוויח עדיין פתוחות' if g['understanding'] else 'חסרות ראיות מהותיות להחלטה',
                   ['resolve: ' + x for x in gaps],
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
    if g['data_blocked']:
        fields = [FIELD_HE.get(f.split(':')[0].replace('critical field ', ''), '') for f in g['data_blocked']]
        he = 'נתונים קריטיים חסרים או סותרים' + (f" ({', '.join(x for x in fields if x)})" if any(fields) else '')
        return out('insufficient_evidence', 'data_blocked', 'Insufficient evidence: ' + '; '.join(g['data_blocked']), he,
                   ['resolve: ' + x for x in g['data_blocked']], 'השלמת הנתונים החסרים או הסותרים והרצה מחדש')
    if g['valuation_failed']:
        return out('insufficient_evidence', 'valuation_failed', 'Insufficient evidence: ' + '; '.join(g['valuation_failed'])
                   + ' (an engine failure, not a judgement on the business)',
                   'שלב הערכת השווי נכשל בהרצה (תקלה במנוע, לא שיפוט על העסק), ולכן עדיין אין הערכת שווי',
                   ['fix the valuation/forecast stage error and re-run: ' + '; '.join(g['valuation_failed'])],
                   'תיקון התקלה בשלב הערכת השווי והרצה מחדש')
    if g['no_valuation']:
        # Checked before the review gate: without a valuation the review cannot pass, and the missing valuation is the cause.
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
    if g['review_blocked']:
        he = 'הבודק הבלתי תלוי חסם מסקנת מחיר' if g['reviewer_present'] else 'הבדיקה המכנית חסמה מסקנת מחיר'
        return out('insufficient_evidence', 'review_blocked', 'Insufficient evidence: ' + '; '.join(g['review_blocked']), he,
                   ['resolve: ' + x for x in g['review_blocked']], 'תיקון מה שהבדיקה מצאה והרצה מחדש')
    if g['valuation_unverified']:
        return out('insufficient_evidence', 'valuation_unverified', 'Insufficient evidence: ' + '; '.join(g['valuation_unverified']),
                   'הבדיקה המכנית של חישוב השווי מצאה כשל קריטי, ולכן השווי אינו מאומת',
                   ['resolve: ' + x for x in g['valuation_unverified']], 'תיקון הכשל בחישוב השווי והרצה מחדש')
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
        meets, irr_txt = irr >= hurdle, _pct(irr, 2)
        irr_he = 'כ־' + _he_pct(irr, hurdle)
    elif bound:
        meets = True if bound['type'] == 'above' and bound['rate'] >= hurdle else False if bound['type'] == 'below' and bound['rate'] <= hurdle else None
        irr_txt = f"{bound['type']} {_pct(bound['rate'])}"
        irr_he = (f"מעל {_he_pct(bound['rate'])}" if bound['type'] == 'above' else f"מתחת ל־{_he_pct(bound['rate'])}")
    else:
        meets = A['price_attractiveness']['level'] in ('strong', 'adequate') if A['price_attractiveness']['level'] in POINTS else None
        irr_txt = 'n/a (no cash-flow IRR; discount to base value used)'
        irr_he = 'לא ניתנת לחישוב (נבחנה ההנחה מול השווי המרכזי)'
    pfh_v = ((rates.pfh(hurdle) if rates is not None else None) or {}).get('value')
    price_change = ([f'price at or below {_g3(pfh_v)} (value at your required return {_pct(hurdle, 2)}; base IRR = required return)'] if pfh_v else []) + \
                   ['new evidence that raises the base case (see what_must_be_true)']
    price_change_he = (f'מחיר של כ־{_fmt_price(pfh_v)} ומטה (השווי בתשואה הנדרשת)' if pfh_v else 'מחיר נמוך יותר או ראיות לתרחיש בסיס טוב יותר')
    idx = index['value']
    fl = bool((req.get('floor') or {}).get('binding'))
    req_txt = (f'required return {_pct(hurdle, 2)} (index {_pct(idx, 2)} + {prem * 100:.2f}pp premium'
               + (', floored at the model cost of equity' if fl else '') + ')')
    req_he = _he_req(hurdle, idx, prem, irr, fl, any(c['component'] == 'model_cost_of_equity_floor' and 'financial leverage' in c['basis']
                                                     for c in req.get('components') or []))
    if meets is None:
        return out('insufficient_evidence', 'price_undetermined', 'Price return could not be determined (IRR not solvable and no usable base value)',
                   'אי אפשר היה לחשב את התשואה הצפויה במחיר הנוכחי', ['an executable base case with a solvable IRR'], 'תרחיש בסיס שאפשר לחשב')
    if q in ('strong', 'adequate'):
        # margin of safety at the SAME required return as the price test (model-rate bear loss is secondary context)
        price = view.get('price')
        bear_h = rates.at(hurdle, 'bear') if rates is not None else None
        loss = max(0., 1 - bear_h / price) if bear_h is not None and price else view.get('loss_to_bear')
        basis = 'at your required return' if bear_h is not None else 'at the model cost of equity'
        tol = BEAR_LOSS_TOLERANCE[q] + tol_shift
        mos = {'loss_to_bear': loss, 'bear_value': bear_h if bear_h is not None else view.get('bear'), 'basis': basis, 'tolerance': tol}
        if meets and (loss is None or loss <= tol):
            return out('attractive_at_price', 'price_test_passed',
                       f"{q} business; base IRR {irr_txt} >= {req_txt}; bear-case loss {_pct(loss, 0)} ({basis}) within the {tol:.0%} tolerance for {q} quality",
                       f'עסק באיכות {LEVEL_HE[q]} שהתשואה השנתית המשתמעת ממנו בתרחיש הבסיס, {irr_he}, עוברת את {req_he}',
                       [f'price above {_g3(pfh_v)} (base IRR would fall below the required return)' if pfh_v else 'a higher price',
                        'evidence that falsifies a base-case assumption (forecast falsifiers)', f'bear-case loss beyond {tol:.0%}'],
                       f'מחיר גבוה מכ־{_fmt_price(pfh_v)} או ראיה שמפריכה את הנחות תרחיש הבסיס' if pfh_v else 'ראיה שמפריכה את הנחות תרחיש הבסיס',
                       margin_of_safety=mos)
        if meets:
            safe = mos['bear_value'] / (1 - tol) if mos['bear_value'] else None
            return out('quality_but_expensive', 'bear_loss_beyond_tolerance',
                       f'{q} business and base IRR {irr_txt} clears the {req_txt}, but the bear-case loss {_pct(loss, 0)} ({basis}) '
                       f'exceeds the {tol:.0%} tolerance for {q} quality: not enough margin of safety',
                       f'העסק באיכות {LEVEL_HE[q]} והתשואה בתרחיש הבסיס עוברת את התשואה הנדרשת, אבל בתרחיש השמרני השווי '
                       f'{"בתשואה הנדרשת " if bear_h is not None else ""}נמוך בכ־{_he_pct(loss)} מהמחיר, יותר ממרווח הביטחון המתאים',
                       ([f'price at or below {_g3(safe)} (bear loss {basis} within tolerance)'] if safe else []) + ['a better-supported bear case (forecast inputs)'],
                       f'מחיר של כ־{_fmt_price(safe)} ומטה' if safe else 'תרחיש שמרני טוב יותר', margin_of_safety=mos)
        below_index = irr is not None and irr < idx
        return out('quality_but_expensive', 'below_hurdle',
                   f'{q} business, but base IRR {irr_txt} < {req_txt}' + (' and below the index assumption itself' if below_index else ''),
                   f'העסק באיכות {LEVEL_HE[q]}, אבל במחיר הנוכחי התשואה השנתית המשתמעת בתרחיש הבסיס היא {irr_he}, מתחת ל{req_he[1:]}'
                   + (' ואף מתחת למדד עצמו' if below_index else ''),
                   price_change, price_change_he, margin_of_safety=mos)
    if q == 'unknown' and meets:
        if 'strong' in (A['trajectory']['level'], A['growth_quality']['level']):
            return out('interesting_not_yet_valuable', 'quality_unproven',
                       f'Model base IRR {irr_txt} clears the {req_txt}, but business quality is not yet demonstrated; the price edge rests on unproven economics',
                       'המודל מראה תשואה שעוברת את התשואה הנדרשת, אבל איכות העסק עוד לא הוכחה ולכן השווי עדיין לא אמין',
                       ['evidence of business quality (unit economics, returns on capital) in the reported record'],
                       'ראיות לאיכות העסק, כמו כלכלת יחידה ותשואה על ההון')
        return out('insufficient_evidence', 'quality_unknown', f'Model base IRR {irr_txt} clears the {req_txt} but business quality is unknown',
                   'המודל מראה תשואה סבירה, אבל אין מספיק ראיות לאיכות העסק', ['evidence of business quality'], 'ראיות לאיכות העסק')
    if q == 'weak' and meets:
        return out('index_preferable', 'weak_quality', f'Base IRR {irr_txt} clears the {req_txt} on the model, but business quality is weak: a low price alone is not an opportunity',
                   'המחיר נראה נמוך לפי המודל, אבל איכות העסק חלשה, ומחיר נמוך לבדו אינו הזדמנות',
                   ['evidence that the weakness is temporary (returns on capital recovering above the ~8% cost of capital)'],
                   'ראיות שהחולשה זמנית, למשל התאוששות בתשואה על ההון')
    return out('index_preferable', 'no_quality_edge', f'No demonstrated quality edge ({q}) and base IRR {irr_txt} is not above the {req_txt}',
               f'אין יתרון איכות מוכח, והתשואה המשתמעת ({irr_he}) אינה עוברת את {req_he}',
               price_change + ['evidence of business quality'], price_change_he)


def _fmt_price(v):
    if v is None:
        return 'לא ידוע'
    return f'{v:,.0f}' if abs(v) >= 100 else f'{v:,.1f}' if abs(v) >= 10 else f'{v:,.2f}'


def _margin(A, g, er, view, index, rates, req, verdict, moat=None):
    """Re-run the verdict at the lowest and highest required return of the marginal band, with the bear-loss tolerance
    moved -/+ BEAR_TOLERANCE_STEP and, when the forecast applied a computed moat proxy, without it. 'marginal' when the code
    changes anywhere (the code itself is kept), with the side and the required return at which it flips; 'clear' when it
    does not; None for routes without a price test."""
    if verdict.get('gate') not in PRICE_ROUTES:
        return None
    band = er.get('band') or {}
    lo, hi = band.get('hurdle_low'), band.get('hurdle_high')
    if lo is None or hi is None:
        return None
    h0, code = req['hurdle'], verdict['code']
    run = lambda h, t=0.: _verdict(A, g, er, view, index, rates, {**req, 'hurdle': h}, t)['code']
    flips = []
    for side, h in (('lower', lo), ('higher', hi)):
        c = run(h)
        if c != code:
            a, b = h0, h                                    # bisection for the required return where the code changes
            for _ in range(10):
                m = (a + b) / 2
                a, b = (m, b) if run(m) == code else (a, m)
            flips.append({'test': 'required_return', 'side': side, 'hurdle': h, 'code': c, 'threshold': round(b, 6)})
    for side, t in (('stricter', -BEAR_TOLERANCE_STEP), ('looser', BEAR_TOLERANCE_STEP)):
        c = run(h0, t)
        if c != code:
            flips.append({'test': 'bear_loss_tolerance', 'side': side, 'tolerance_shift': t, 'code': c,
                          'loss_to_bear': (verdict.get('margin_of_safety') or {}).get('loss_to_bear'),
                          'tolerance': (verdict.get('margin_of_safety') or {}).get('tolerance')})
    if (moat or {}).get('status') == 'computed':
        c = _verdict(A, g, {**er, 'base_irr': moat['base_irr'], 'base_irr_bound': moat['base_irr_bound']}, view, index, moat['rates'], req)['code']
        if c != code:
            flips.append({'test': 'moat_proxy', 'side': 'without_proxy', 'code': c, 'base_irr_without_proxy': moat['base_irr'],
                          'value_today_with_proxy': _num((moat.get('value_today_with_proxy') or {}).get('base')),
                          'value_today_without_proxy': _num((moat.get('value_today_without_proxy') or {}).get('base'))})
    price = view.get('price')
    pl, ph = band.get('price_low'), band.get('price_high')
    out = {'status': 'marginal' if flips else 'clear', 'flips': flips, 'verdict': code, 'hurdle_range': [lo, hi], 'value_range': [pl, ph],
           'price_inside_value_range': (pl <= price <= ph) if None not in (pl, ph, price) else None,
           'rule': band.get('rule')}
    out['text'], out['text_he'] = _margin_text(out)
    return out


def _margin_text(m):
    """(English, Hebrew) description of where a marginal call flips: the side and the required return, or the bear-loss tolerance."""
    en, he = [], []
    for f in (m or {}).get('flips') or []:
        if f['test'] == 'required_return':
            th = f.get('threshold') or f['hurdle']
            en.append(f"a required return {'below' if f['side'] == 'lower' else 'above'} about {_pct(th)} changes the verdict to {f['code']}")
            he.append(f"בתשואה נדרשת {'נמוכה' if f['side'] == 'lower' else 'גבוהה'} מכ־{th * 100:.1f}% המסקנה משתנה")
        elif f['test'] == 'bear_loss_tolerance' and not any(x.startswith('the bear-case loss') for x in en):
            loss, tol = f.get('loss_to_bear'), f.get('tolerance')
            en.append(f"the bear-case loss {_pct(loss, 0)} is within {BEAR_TOLERANCE_STEP * 100:.0f}pp of the {_pct(tol, 0)} margin-of-safety tolerance")
            he.append(f'ההפסד בתרחיש השמרני (כ־{_he_pct(loss)}) קרוב לסף מרווח הביטחון ({_he_pct(tol)})')
        elif f['test'] == 'moat_proxy':
            en.append(f"without the computed moat proxy (base value today {_g3(f.get('value_today_with_proxy'))} -> "
                      f"{_g3(f.get('value_today_without_proxy'))} at the model cost of equity) the verdict changes to {f['code']}")
            he.append('בלי האומדן המחושב ליתרון תחרותי (שלא נבדק בידי אנליסט) המסקנה משתנה')
    return '; '.join(en), ' ו'.join(he) if len(he) == 2 else '; '.join(he)


# ---------------------------------------------------------------- confidence

def _names(xs, he=None, n=3):
    xs = [(he or {}).get(x, x) for x in xs]
    if len(xs) <= n + 1:
        return ', '.join(xs[:-1]) + (' ו' if len(xs) > 1 else '') + xs[-1] if xs else ''
    return ', '.join(xs[:n]) + f' ועוד {len(xs) - n}'


def _data_reason(qa, du):
    """One data reason naming the actual gaps (missing / single-source / stale fields, opening balance source)."""
    fields = (qa or {}).get('fields') or {}
    groups = {}
    for f in (qa or {}).get('limited') or []:
        groups.setdefault((fields.get(f) or {}).get('status') or 'limited', []).append(f)
    access = (du.get('source_access') or {}).get('value')
    en, he = [], []
    for st, label in (('missing', 'missing'), ('conflict', 'conflicting'), ('single_source', 'single-source'), ('stale', 'stale'),
                      ('limited', 'limited')):
        if groups.get(st):
            en.append(f"{label} {', '.join(groups[st])}")
    if groups.get('missing'):
        he.append('חסרים ' + _names(groups['missing'], FIELD_HE, 2))
    if groups.get('conflict'):
        he.append('יש סתירה בנתונים על ' + _names(groups['conflict'], FIELD_HE, 2))
    rest = sum(len(groups.get(k) or []) for k in ('single_source', 'stale', 'limited'))
    if rest:
        only = (groups.get('single_source') or []) + (groups.get('stale') or []) + (groups.get('limited') or [])
        he.append((_names(only, FIELD_HE, 2) if rest <= 2 else f'{rest} נתונים') + ' ישנים או ממקור יחיד')
    if access in ('secondary_access', 'no_primary_source'):
        en.append('opening balance not read from the primary filing')
        he.append('המאזן לא נקרא מהדוח עצמו')
    if not en:
        return None
    return 'data gaps: ' + '; '.join(en), 'פערי נתונים: ' + '; '.join(he)


def _understanding_reason(card, rk):
    """One understanding reason: open questions on the model card and high-relevance dimensions not yet assessed."""
    open_q = [q for q in (card or {}).get('understanding') or [] if q.get('status') == 'open']
    gaps = ((rk or {}).get('error_size') or {}).get('process_gaps') or []
    dims = [i for gp in gaps if gp.get('code') == 'dimensions_unassessed' for i in gp.get('ids') or []]
    dims = sorted({str(i).split('.', 1)[-1] for i in dims}, key=lambda d: (PRIORITY_DIMS.index(d) if d in PRIORITY_DIMS else 9, d))
    partial = _card(card, 'completeness', 'status') == 'partial'
    if not (partial or dims):
        return None
    en = (['business understanding partial: ' + (f'{len(open_q)} open questions' if open_q else 'part asserted, not evidenced')] if partial else []) \
        + (['high-relevance dimensions unassessed: ' + ', '.join(dims)] if dims else [])
    he = (['חלק מההבנה העסקית עוד פתוח' + (f' ({len(open_q)} שאלות)' if open_q else '')] if partial else []) \
        + (['עוד לא נבדקו לעומק: ' + _names(dims, DIM_HE)] if dims else [])
    return '; '.join(en), '; '.join(he)


def _confidence(g, A, view, rk, card, val, qa, margin=None, overrides=None):
    """Each cause counts once: data gaps once (dataqa + opening-balance source), understanding once (model card + unassessed
    dimensions from risk.process_gaps), valuation error once (risk.error_size; terminal share only when it is absent).
    A result that rests on an accepted analyst override says so."""
    hard, soft = [], []
    if g['draft'] and view['is_demo']:
        hard.append(('demo valuation inputs (synthetic)', 'זו דוגמה מומצאת, לא חברה אמיתית'))
    if g['data_blocked']:
        hard.append(('critical data blocked: ' + '; '.join(g['data_blocked']), 'נתונים קריטיים חסרים או סותרים'))
    if g['valuation_failed']:
        hard.append(('valuation stage failed: ' + '; '.join(g['valuation_failed']), 'שלב הערכת השווי נכשל בהרצה'))
    if g['understanding'] or g['evidence_gaps']:
        hard.append(('business understanding insufficient' if g['understanding'] else 'material evidence gap',
                     'שאלות מהותיות על העסק עדיין פתוחות' if g['understanding'] else 'חסרות ראיות מהותיות'))
    if g['valuation_unverified']:
        hard.append(('valuation unverified by the mechanical red-team check', 'הבדיקה המכנית לא אישרה את חישוב השווי'))
    if (margin or {}).get('status') == 'marginal':
        en, he = _margin_text(margin)
        own = margin.get('verdict')
        soft.append((f'marginal call{" (mechanical verdict " + own + ")" if overrides and own else ""}: {en}',
                     f'ההכרעה גבולית: {he}'))
    if overrides:                                  # counted once, however many overrides
        soft.append(('rests on accepted analyst overrides: ' + '; '.join(
            f"{o['target']} -> {o['value']} ({o.get('direction', 'n/a')}; evidence {', '.join(o.get('evidence_ids') or []) or 'none'})" for o in overrides),
            'התוצאה נשענת גם על הכרעת אנליסט מתועדת ולא רק על הבדיקה המכנית'))
    if g['no_valuation']:
        soft.append(('no reliable valuation', 'אין עדיין הערכת שווי אמינה'))
    mat = g.get('material_issues') or []
    if mat:
        soft.append(('unresolved forecast material issues: ' + '; '.join(f"{m['id']} ({m['description']})" for m in mat),
                     'בתחזית נותרו שאלות מהותיות פתוחות: ' + '; '.join(list(dict.fromkeys(m['description_he'] for m in mat))[:2])))
    if any(c['kind'] == 'external' for c in g.get('funding_conditional') or []) and not g['funding']:
        soft.append(('value conditional on the priced (assumed) financing path', 'השווי מותנה במימון החיצוני שהונח במודל'))
    if [d for d in g['draft'] if 'demo' not in d]:
        soft.append(('not independently reviewed (draft)', 'הניתוח עוד לא עבר בדיקה בלתי תלויה'))
    if g['red_flags'] and g['red_flags_released']:
        soft.append(('forensic red flag released by a documented override with filing evidence and an accepted independent reviewer: '
                     + ', '.join(g['red_flags']), 'דגל אדום בבדיקות הדוחות הוסבר בראיות מהדוחות ואושר בבדיקה בלתי תלויה'))
    elif g['red_flags']:
        soft.append(('forensic red flag: ' + ', '.join(g['red_flags']), 'בדיקות הדוחות הדליקו דגל אדום שעוד לא הוסבר'))
    if g['withhold_price']:
        soft.append(('governance red flag: ' + ', '.join(x['id'] for x in g['withhold_price']),
                     'יש ראיה ל' + (next((x['description_he'] for x in g['withhold_price'] if x.get('description_he')), None) or 'דגל אדום בממשל התאגידי')))
    elif g.get('withhold_price_released'):
        soft.append(('governance red flag released by an accepted independent reviewer with filing evidence: '
                     + ', '.join(x['id'] for x in g['withhold_price_released']), 'דגל אדום בממשל התאגידי הוסבר בראיות מהדוחות ואושר בבדיקה בלתי תלויה'))
    du = {x['signal']: x for x in A['data_uncertainty']['signals']}
    dov = A['data_uncertainty'].get('override') or {}
    data = _data_reason(qa, du) if (du.get('dataqa') or {}).get('value') == 'limited' or \
        (du.get('source_access') or {}).get('value') in ('secondary_access', 'no_primary_source') else None
    if data and dov.get('direction') != 'upgrade' and not g['data_blocked']:   # blocked data is already a hard reason; only an
        soft.append(data)                                                        # evidence-backed upgrade removes it, never a downgrade
    err = (rk or {}).get('error_size') or {}
    if err.get('level') == 'high':
        soft.append(('large possible valuation error: ' + '; '.join(map(str, err.get('reasons') or [])), 'טווח הטעות האפשרי בהערכת השווי גדול'))
    elif not err and (view.get('terminal_share') or 0) > .8:
        soft.append(('more than 80% of base value from the terminal', 'רוב השווי מגיע משנים רחוקות'))
    if any('forecast is more than 10pp above' in (s.get('note') or '') for s in A['trajectory']['signals']):
        soft.append(('base forecast growth far above own history', 'התחזית מניחה צמיחה מהירה בהרבה מההיסטוריה'))
    if any('7 calendar days' in w for w in (((val or {}).get('result') or {}).get('warnings') or [])):
        soft.append(('valuation quote older than a week', 'המחיר שהושווה ישן מיותר משבוע'))
    und = None if g['understanding'] or g['evidence_gaps'] else _understanding_reason(card, rk)   # insufficient understanding is already hard
    if und:
        soft.append(und)
    reasons = hard + soft
    level = 'low' if hard or len(soft) >= 3 else 'medium' if soft else 'high'
    return {'level': level, 'level_he': {'low': 'נמוכה', 'medium': 'בינונית', 'high': 'גבוהה'}[level],
            'reasons': [r[0] for r in reasons], 'reasons_he': [r[1] for r in reasons],
            'rule': 'low if any hard reason (demo, blocked data, insufficient understanding, unverified valuation) or 3+ other reasons; '
                    'medium with 1-2; high with none. Each cause counts once.'}


# ---------------------------------------------------------------- overrides

def _evidence_index(parts):
    """Every id an override may cite, with the kind of thing it is. FactBook sources and qualitative items first."""
    fb, card, fc, val, rk = (parts.get(k) for k in ('fb', 'card', 'fc', 'val', 'rk'))
    idx = {}
    for sid, s in ((fb or {}).get('sources') or {}).items():
        idx[sid] = {'type': 'source', 'kind': (s or {}).get('kind'), 'provider': (s or {}).get('provider')}
    for q in (fb or {}).get('qualitative') or []:
        if q.get('id'):
            idx[q['id']] = {'type': 'qualitative', 'kind': q.get('kind'), 'source_ids': list(q.get('source_ids') or [])}
    case = (val or {}).get('case') or {}
    for s in case.get('sources') or []:
        if s.get('id'):
            idx.setdefault(s['id'], {'type': 'case_source', 'kind': s.get('kind')})
    for q in (card or {}).get('understanding') or []:
        if q.get('id'):
            idx.setdefault(q['id'], {'type': 'question'})
        for e in q.get('evidence_ids') or []:
            idx.setdefault(e, {'type': 'unresolved'})
    for d in ((card or {}).get('dimensions') or {}).values():
        for e in (d or {}).get('evidence_ids') or []:
            idx.setdefault(e, {'type': 'unresolved'})
    for a in list((fc or {}).get('assumptions') or []) + list(case.get('assumptions') or []):
        if a.get('id'):
            idx.setdefault(a['id'], {'type': 'assumption', 'kind': a.get('kind')})
        for e in a.get('source_ids') or []:
            idx.setdefault(e, {'type': 'unresolved'})
    for r in (rk or {}).get('register') or []:
        if r.get('id'):
            idx.setdefault(r['id'], {'type': 'risk'})
        for e in r.get('evidence_ids') or []:
            idx.setdefault(e, {'type': 'unresolved'})
    return {k: v for k, v in idx.items() if isinstance(k, str)}


def _evidence_class(eid, idx, _seen=()):
    """What an id is, as evidence for an override. FactBook sources by kind (a document is 'filing_document', a statement
    dataset 'filing_dataset'); a qualitative item counts as 'qualitative' only when it is reported/guidance AND one of its
    sources is a filing (otherwise it takes its sources' class, or 'unsourced'); a case source that is not a FactBook
    source was written by the analyst into the case ('analyst_input'), whatever kind it claims."""
    e = idx.get(eid) or {}
    t, kind = e.get('type'), e.get('kind')
    if t == 'source':
        if kind in DOC_KINDS:
            return 'filing_document'
        if kind in DATASET_KINDS or (e.get('provider') in ('sec', 'issuer') and kind not in ('market_feed', 'consensus', 'synthetic', 'secondary',
                                                                                             'vendor_normalized')):
            return 'filing_dataset'
        return {'market_feed': 'market_feed', 'consensus': 'consensus', 'vendor_normalized': 'vendor', 'synthetic': 'synthetic',
                'secondary': 'secondary'}.get(kind, kind or 'other')
    if t == 'case_source':
        return 'analyst_input'
    if t == 'qualitative':
        if kind not in ('reported', 'guidance'):
            return kind or 'other'                        # opinion / assumption is not evidence for an override
        src = [_evidence_class(x, idx, _seen + (eid,)) for x in e.get('source_ids') or [] if x not in _seen + (eid,)]
        if not src:
            return 'unsourced'
        if any(c in ('filing_document', 'filing_dataset') for c in src):
            return 'qualitative'
        return src[0]                                     # e.g. synthetic, secondary, vendor: the statement is no better than its source
    return t or 'unknown'


def _check_shape(o, seen):
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
    if not isinstance(why, str) or not why.strip():
        return 'documented justification required'
    if ev is not None and (not isinstance(ev, list) or not all(isinstance(x, str) and x for x in ev)):
        return 'evidence_ids must be a list of ids'
    return None


def _check_evidence(o, idx, direction):
    """Downgrades (more conservative) need a justification and only known ids (a risk entry, an opinion, news or none).
    Upgrades need a justification of at least 5 words and evidence of accepted classes, at least one of which carries the
    change (a filing/issuer document or a filing-sourced reported/guidance statement; a statement dataset alone does not)."""
    t, why, ev = o['target'], o['justification'], list(o.get('evidence_ids') or [])
    unknown = [x for x in ev if x not in idx]
    if unknown:
        return 'unknown evidence ids (not in FactBook sources/qualitative, model card, forecast, risk register or valuation case): ' + ', '.join(unknown)
    if direction == 'downgrade':
        return None
    if len(why.split()) < 5:
        return 'documented justification required for an upgrade (at least 5 words)'
    if not ev:
        return 'evidence_ids required for an upgrade (nonempty list of ids)'
    ok, carry = EVIDENCE_BY_TARGET.get(t, (EVIDENCE_ACCEPTED, EVIDENCE_CARRIES))
    cls = {x: _evidence_class(x, idx) for x in ev}
    bad = [(x, c) for x, c in cls.items() if c not in ok]
    need = ('filing or issuer documents' if carry == {'filing_document'} else
            'a filing or issuer document, or a reported/guidance statement sourced from a filing')
    if bad:
        return (f'evidence for {t} must be {need}; not accepted: ' + ', '.join(f'{x} ({c})' for x, c in bad)
                + ' (a price feed, vendor share count, consensus, opinion, assumption, question, risk entry, analyst-written case source, '
                  'statement without a filing source or synthetic data is not evidence here)')
    if not any(c in carry for c in cls.values()):
        return (f'evidence for {t} must include {need}; a whole statement dataset ({", ".join(ev)}) alone does not carry an upgrade '
                '(the mechanical assessment already read it)')
    return None


def _direction(old, new):
    r = {'strong': 3, 'adequate': 2, 'unknown': 1, 'weak': 0}
    return 'upgrade' if r[new] > r[old] else 'downgrade' if r[new] < r[old] else 'same'


def _verdict_direction(old, new):
    if new == old:
        return 'same'
    return 'downgrade' if new in CONSERVATIVE or FAVOUR[new] < FAVOUR[old] else 'upgrade'


# ---------------------------------------------------------------- entry point

def decide(parts, inputs=None):
    """parts = {'fb','qa','an','card','fc','val','rk','rv'} (any may be None when a stage failed).
    inputs = {'index': {value, basis, source, low?, high?}?, 'overrides': [{target, value, justification, evidence_ids}]?}."""
    inputs = inputs or {}
    if not isinstance(inputs, dict) or set(inputs) - {'index', 'overrides'}:
        raise ValueError('decision inputs accept index and overrides')
    overrides = inputs.get('overrides') or []
    if not isinstance(overrides, list):
        raise ValueError('inputs.overrides must be a list')
    parts = parts or {}
    fb, qa, an, card, fc, val, rk = (parts.get(k) for k in ('fb', 'qa', 'an', 'card', 'fc', 'val', 'rk'))
    index = _index(inputs, fc)
    view = _view(fb, val)
    case, res = _case_and_result(val)
    qcase = _quote_case(case, view['price']) if res else None
    valued = qcase is not None and view.get('base') is not None
    w, w_src = _debt_weight(qcase, fc, view.get('base')) if valued else (0., None)
    diag = ((val or {}).get('diagnostics') or {}).get('implied_cost_of_equity') if qcase is case else None
    irrs = {s: (_irr(qcase, s, diag, w) if valued else
                {'value': None, 'status': 'not_computable', 'bound': None, 'reason': 'no executed base value'})
            for s in SCENARIOS}
    rates = _Rates(qcase, view['price'], w) if valued else None
    ke = _model_ke(view, fc)
    fview = _forecast_view(an, res, view)
    fs = financial_strength(an, card, fc)
    pricing = _risk_pricing(rk, fc, view, rates)
    A = {'business_quality': business_quality(an, card, fs['level']), 'trajectory': trajectory(an, card, fview),
         'growth_quality': growth_quality(an), 'financial_strength': fs,
         'management_capital_allocation': management_capital_allocation(an, card),
         'data_uncertainty': data_uncertainty(qa, val, fb), 'risk_and_error': risk_and_error(rk, an, view, pricing)}
    idx = _evidence_index(parts)
    g = _gates(parts, view, an, idx)
    err_level = ((rk or {}).get('error_size') or {}).get('level')
    wmbt = (val or {}).get('what_must_be_true')
    req = _required_return(A, index, card, g, rk, an, ke)
    moat = _moat_test(fc, qcase, view, w, rates) if valued else None
    er = _expected(irrs, view, index, req, rates, qcase, res, an, err_level, w, moat)
    er['debt_weight_source'] = w_src
    A['price_attractiveness'] = price_attractiveness(er, view, wmbt, an)
    A = {k: A[k] for k in ASSESSMENTS}
    mech = _verdict(A, g, er, view, index, rates, req)
    mech['margin_detail'] = _margin(A, g, er, view, index, rates, req, mech, moat)
    # Overrides: assessments first (re-evaluated through the same gates), then at most one verdict override.
    seen, records, pending, red_release = set(), [], None, None
    eff_A = deepcopy(A)
    for o in overrides:
        err = _check_shape(o, seen)
        rec = {k: o.get(k) for k in ('target', 'value', 'justification', 'evidence_ids')} if isinstance(o, dict) else {'target': None}
        if err:
            records.append({**rec, 'status': 'rejected', 'reason': err})
            continue
        t = o['target']
        seen.add(t)
        if t == 'verdict':
            pending = (o, rec)
            continue
        if t == 'price_attractiveness' and er['base_irr'] is not None:
            records.append({**rec, 'status': 'rejected', 'reason': 'price attractiveness is arithmetic on the executed valuation; '
                            'change the forecast inputs (forecast.build overrides) instead'})
            continue
        if t == 'red_flags':
            err = _check_evidence(o, idx, 'upgrade')            # releasing a manipulation/distress screen is never conservative
            if not err and not g['red_flags']:
                err = 'no forensic flag raised'
            elif not err and not set(g['red_flags']) <= set(o['value']):
                err = 'must name every raised forensic flag: ' + ', '.join(g['red_flags'])
            elif not err and not g['reviewer_present']:
                err = ('releasing a forensic red flag (Beneish likely manipulator / Altman distress) needs an accepted independent reviewer '
                       'bound to this valuation (review inputs), not only the analyst\'s own override; the price call stays withheld')
            if err:
                records.append({**rec, 'status': 'rejected', 'reason': err})
            else:
                red_release = o
                records.append({**rec, 'status': 'accepted', 'direction': 'upgrade',
                                'reason': 'documented filing evidence and an accepted independent reviewer cover every raised forensic flag; '
                                          'the flags stay on record'})
            continue
        direction = _direction(A[t]['level'], o['value'])
        err = _check_evidence(o, idx, direction)
        if err:
            records.append({**rec, 'status': 'rejected', 'direction': direction, 'reason': err})
            continue
        eff_A[t].update(mechanical_level=A[t]['level'], level=o['value'],
                        override={'value': o['value'], 'justification': o['justification'], 'evidence_ids': list(o.get('evidence_ids') or []),
                                  'direction': direction})
        records.append({**rec, 'status': 'accepted', 'direction': direction, 'reason': 'assessment level replaced; mechanical level kept',
                        'effect': OVERRIDE_EFFECT[t]})

    def effective(eA):
        # only downgrades move the premium: an analyst upgrade never lowers the required return
        prem_A = {k: (eA[k] if (eA[k].get('override') or {}).get('direction') == 'downgrade' else A[k]) for k in ASSESSMENTS}
        r = _required_return(prem_A, index, card, g, rk, an, ke)
        e = deepcopy(er) if r['hurdle'] == req['hurdle'] and r['components'] == req['components'] else \
            _expected(irrs, view, index, r, rates, qcase, res, an, err_level, w, moat)
        e['debt_weight_source'] = w_src
        if not eA['price_attractiveness'].get('override'):
            eA['price_attractiveness'] = price_attractiveness(e, view, wmbt, an)
        v = _verdict(eA, g, e, view, index, rates, r)
        v['margin_detail'] = _margin(eA, g, e, view, index, rates, r, v, moat)
        return r, e, v

    eff_req, eff_er, eff = req, er, deepcopy(mech)
    if any(r['status'] == 'accepted' and r['target'] in ASSESSMENTS for r in records):
        eff_req, eff_er, eff = effective(eff_A)
        # an assessment upgrade can never produce a verdict the mechanical route does not allow (no gate bypass)
        ups = [r for r in records if r['status'] == 'accepted' and r['target'] in VERDICT_ASSESSMENTS and r.get('direction') == 'upgrade']
        if ups and eff['code'] not in _allowed(mech['gate'], mech['code']) | {mech['code']} \
                and _verdict_direction(mech['code'], eff['code']) == 'upgrade':
            for r in ups:
                r.update(status='rejected', reason=(f"this upgrade would turn the mechanical '{mech['code']}' (route '{mech['gate']}') into "
                                                    f"'{eff['code']}', which that route does not allow (allowed: "
                                                    f"{', '.join(sorted(_allowed(mech['gate'], mech['code'])))}); an assessment upgrade cannot bypass a gate"))
                r.pop('effect', None)
                eff_A[r['target']] = deepcopy(A[r['target']])
            if any(r['status'] == 'accepted' and r['target'] in ASSESSMENTS for r in records):
                eff_req, eff_er, eff = effective(eff_A)
            else:
                eff_A['price_attractiveness'] = deepcopy(A['price_attractiveness'])
                eff_req, eff_er, eff = req, er, deepcopy(mech)
    if eff_req['hurdle'] != req['hurdle']:
        eff_er['hurdle_basis'] = 'effective: recomputed after accepted assessment downgrades (the mechanical hurdle is kept under mechanical)'
        eff_er['mechanical'] = {'hurdle': req['hurdle'], 'risk_premium': req['premium'], 'premium_components': req['components'],
                                'price_for_hurdle': er['price_for_hurdle']}
    else:
        eff_er['hurdle_basis'] = 'mechanical (no accepted override changed it; analyst upgrades never lower it)'
    if pending:
        o, rec = pending
        direction = _verdict_direction(eff['code'], o['value'])
        allowed = _allowed(eff['gate'], eff['code'])
        if _verdict_direction(mech['code'], o['value']) == 'upgrade':
            allowed &= _allowed(mech['gate'], mech['code']) | {mech['code']}
        err = ('no-op: the override equals the effective verdict; nothing is replaced and the mechanical margin and reasons stay'
               if direction == 'same' else _check_evidence(o, idx, direction))
        if not err and o['value'] not in allowed:
            err = (f"cannot lift the '{eff['gate']}' gate behind '{eff['code']}' to '{o['value']}' (allowed: {', '.join(sorted(allowed))}); "
                   "resolve the gate's cause or change the evidence the route rests on instead")
        if err:
            records.append({**rec, 'status': 'rejected', 'direction': direction, 'reason': err})
        else:
            records.append({**rec, 'status': 'accepted', 'direction': direction, 'reason': 'analyst verdict replaces the mechanical verdict; both are stored'})
            md = eff.get('margin_detail')
            eff = {**eff, 'code': o['value'], 'label_he': VERDICTS[o['value']], 'overridden_from': eff['code'],
                   'margin_detail': ({**md, 'applies_to': f"the result before the analyst verdict override ({md.get('verdict')})"} if md else None),
                   'reason': f"Analyst override ({o['justification']}); mechanical: {eff['reason']}",
                   'reason_he': 'הכרעת אנליסט מתועדת, שונה מהתוצאה המכנית',
                   'what_would_change_it_he': 'ראיה שסותרת את נימוק האנליסט המתועד',
                   'what_would_change_it': (['the evidence behind the override is contradicted: ' + ', '.join(o.get('evidence_ids') or [])]
                                            if o.get('evidence_ids') else ['the analyst\'s documented reason no longer holds']) + eff['what_would_change_it']}
    g['red_flags_released'] = bool(red_release)
    route_moved = (eff['code'], eff['gate']) != (mech['code'], mech['gate']) or eff.get('overridden_from') is not None
    for r in records:
        if r['status'] != 'accepted':
            continue
        t = r['target']
        # material = the result the owner reads depends on it; otherwise it is recorded only (never presented as a basis)
        r['material'] = (t in ('verdict', 'red_flags', 'business_quality', 'trajectory')
                         or (t == 'data_uncertainty' and r.get('direction') == 'upgrade')
                         or (eff_req['hurdle'] != req['hurdle'] and r.get('direction') == 'downgrade' and t == 'financial_strength')
                         or (route_moved and t in VERDICT_ASSESSMENTS))
        if not r['material']:
            r['effect'] = 'recorded only: no effect on the verdict, the required return or the confidence'
    accepted = [r for r in records if r['status'] == 'accepted' and r.get('material')]
    conf = _confidence(g, eff_A, view, rk, card, val, qa, eff.get('margin_detail'), accepted)
    withheld = list(g['draft']) + [f"unresolved forecast material issue {m['id']}: {m['description']}" for m in g['material_issues']]
    if g['red_flags'] and not red_release:
        withheld.append('forensic red flag raised (' + ', '.join(g['red_flags']) + '); price call withheld until explained with filing '
                        'evidence and an accepted independent reviewer')
    for v, e in ((mech, er), (eff, eff_er)):
        v['confidence'] = conf['level']
        v['status'] = 'draft' if withheld else 'final'
        v['price_claim_withheld'] = bool(withheld)
        v['withheld_reasons'] = withheld
        v['margin'] = (v.get('margin_detail') or {}).get('status')
        v['price_for_hurdle'] = e['price_for_hurdle'] if v['code'] in ('attractive_at_price', 'quality_but_expensive', 'index_preferable') else None
        v['value_at_required_return'] = e['value_at_required_return']
    for k, a in eff_A.items():
        a['level_he'] = LEVEL_HE[a['level']]
    company = (fb or {}).get('company') or ({'ticker': case['ticker'], 'currency': case['currency']} if case else {})
    out = {'decision_version': 3, 'as_of': (fb or {}).get('as_of') or (case or {}).get('as_of'),
           'company': {k: company.get(k) for k in ('ticker', 'name', 'currency')},
           'price': {k: view.get(k) for k in ('price', 'currency', 'price_as_of', 'price_source', 'price_kind')},
           'assessments': eff_A, 'mechanical_assessments': {k: a['level'] for k, a in A.items()},
           'expected_return': eff_er, 'verdict': eff, 'mechanical_verdict': mech, 'overrides': records, 'confidence': conf,
           'valuation_view': {k: v for k, v in view.items() if k not in ('price_source',)},
           'risk_pricing': pricing, 'judgement': _judgement(g, eff_A, eff_er, view, pricing, rk),
           'gates': g, 'rules': {'verdict_precedence': VERDICT_RULES, 'premium': PREMIUM, 'marginal_band': {'step': BAND_STEP, 'error_widening': ERROR_WIDENING,
                                                                                                          'bear_tolerance_step': BEAR_TOLERANCE_STEP},
                                 'bear_loss_tolerance': BEAR_LOSS_TOLERANCE, 'gate_allowed_overrides': {k: sorted(v) for k, v in GATE_ALLOWED.items()},
                                 'shared_gates_no_more_favourable': sorted(SHARED_GATES), 'verdict_favour': FAVOUR,
                                 'override_evidence': {'accepted': sorted(EVIDENCE_ACCEPTED), 'must_include_one_of': sorted(EVIDENCE_CARRIES),
                                                       'by_target': {k: {'accepted': sorted(a), 'must_include_one_of': sorted(b)}
                                                                     for k, (a, b) in EVIDENCE_BY_TARGET.items()},
                                                       'downgrades': 'justification and known ids only (never blocked by evidence kind)'}},
           'business_reason_he': _business_he(eff_A),
           'semantics': ('Four questions assessed separately and never averaged. Values are conditional estimates, not predicted prices; '
                         'no win rate is claimed. A research opinion, not an order or suitability assessment.')}
    json.dumps(out, allow_nan=False)
    return out


JUDGEMENT_FACTORS = ('business_quality', 'growth_quality', 'trajectory', 'financial_strength', 'management_capital_allocation',
                     'price_attractiveness')


def _judgement(g, A, er, view, pricing, rk):
    """The owner's three buckets (2026-10-20), kept apart: material disqualifiers (unreliable data or valuation, a risk that
    cannot be justified, a thesis without a basis yet), factors that make the investment more or less attractive, and risks
    accepted and priced versus risks still to price. A derived view of the same gates, assessments and risk register: it
    adds no threshold and changes no verdict. A thesis-basis gap is a research task, never a judgement on the business."""
    reg = {r.get('id'): r for r in (rk or {}).get('register') or [] if isinstance(r, dict)}
    dq = [{'kind': 'unacceptable_risk', 'what': it.get('description') or it.get('id')} for it in g['disqualifying']]
    if g['impaired'] and not any('impair' in str(d['what']).lower() for d in dq):
        dq.append({'kind': 'unacceptable_risk', 'what': 'equity impaired: base value today at or below zero'})
    dq += [{'kind': 'reliability', 'what': x} for x in g['data_blocked'] + g['valuation_unverified'] + g['valuation_failed']]
    dq += [{'kind': 'thesis_basis', 'what': x, 'resolves_by': 'evidence for the open business questions (a research task, not a '
            'negative judgement)'} for x in g['understanding'] + g['evidence_gaps']]
    fac = {'for': [], 'neutral': [], 'against': [], 'unknown': []}
    for k in JUDGEMENT_FACTORS:
        a = A.get(k) or {}
        fac[{'strong': 'for', 'adequate': 'neutral', 'weak': 'against'}.get(a.get('level'), 'unknown')].append(
            {'factor': k, 'level': a.get('level'), 'why': (a.get('reasons') or [])[:2]})
    idx = er.get('index_expected_return')
    idx = idx.get('value') if isinstance(idx, dict) else idx
    base, hurdle = er.get('base_irr'), er.get('hurdle')
    ret = {'bear_irr': er.get('bear_irr'), 'base_irr': base, 'bull_irr': er.get('bull_irr'), 'index_expected_return': idx,
           'required_return': hurdle, 'excess_over_index': base - idx if base is not None and idx is not None else None,
           'excess_over_required': base - hurdle if base is not None and hurdle is not None else None,
           'loss_to_bear': view.get('loss_to_bear'), 'meets_owner_aspiration_14pct': er.get('meets_owner_aspiration_14pct'),
           'note': 'returns are conditional on each scenario; the 14% aspiration is reported, never a threshold'}
    risks = {'priced': [], 'to_price': [], 'open_questions': []}
    for p in pricing or []:
        r = reg.get(p.get('id')) or {}
        item = {'id': p.get('id'), 'what': r.get('description') or p.get('id'), 'severity': r.get('severity')}
        st = p.get('status')
        (risks['priced'] if st in ('priced', 'partially_priced') else risks['open_questions'] if st == 'open_question'
         else risks['to_price']).append(item)
    material = [d for d in dq if d['kind'] != 'thesis_basis']
    if material:
        reading = 'excluded until resolved: ' + '; '.join(str(d['what']) for d in material[:2])
    elif dq:
        reading = ('no material disqualifier; the thesis still needs evidence before a decision'
                   + (f"; economics so far: base return {_pct(base)} vs index {_pct(idx)}" if base is not None and idx is not None else ''))
    else:
        reading = 'no disqualifier: the decision rests on attractiveness against the index and the priced risks'
    return {'disqualifiers': dq, 'material_disqualifier': bool(material), 'attractiveness': fac, 'return_vs_alternatives': ret,
            'risks': risks, 'reading': reading,
            'meaning': 'three separate questions: is anything disqualifying, how attractive is it against the index, which risks '
                       'are accepted and priced; not a checklist and not a score'}


SIGNAL_HE = {'roic': 'תשואה על ההון המושקע', 'operating_margin': 'שולי רווח תפעולי', 'gross_margin': 'שולי רווח גולמי',
             'fcf_conversion': 'המרת רווח למזומן', 'competitive_position': 'עדויות ליתרון תחרותי',
             'roe_vs_cost_of_equity': 'תשואה על ההון העצמי'}


def _business_he(A):
    """Quality sentence without any price figure (used when a price claim is withheld). An analyst-overridden level is
    labelled as such next to the mechanical level, so the words never contradict the figures they cite."""
    bq = A['business_quality']
    parts = []
    for s in bq['signals']:
        if s['signal'] in SIGNAL_HE and s['level'] in POINTS and s['core']:
            v = s['value']
            parts.append(SIGNAL_HE[s['signal']] + (f' של כ־{v * 100:.0f}%' if isinstance(v, float) and s['signal'] != 'fcf_conversion' else ''))
    tail = (' (' + ', '.join(parts[:2]) + ')') if parts else ''
    if bq.get('override'):
        q = (f"איכות העסק {LEVEL_HE[bq['level']]} לפי הכרעת אנליסט מתועדת (במדדים המכניים: {LEVEL_HE[bq.get('mechanical_level', 'unknown')]}"
             + (', ' + ', '.join(parts[:2]) if parts else '') + ')')
    else:
        q = f"איכות העסק {LEVEL_HE[bq['level']]}{tail}"
    tr = A['trajectory']
    t = LEVEL_HE[tr['level']] + (' לפי הכרעת אנליסט' if tr.get('override') else '')
    return f"{q}, והמגמה העסקית {t}"
