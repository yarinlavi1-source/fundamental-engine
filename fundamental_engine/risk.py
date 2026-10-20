"""Risks, failure scenarios, downside and size of error (pipeline stage 'risk').

Why a separate stage: a list of alarming ratios rejects every company, while a single bear number
hides how a thesis fails. The register keeps the owner's three classes apart:
- disqualifying: conditions under which no responsible price conclusion exists. ONLY blocked data,
  an unfunded base plan or impaired equity, a critical red-team failure, a strong manipulation
  signal (Beneish likely manipulator AND weak cash conversion) and distress (Altman distress AND
  negative FCF AND a near-term refinancing need).
- evidence gaps are not judgements on the company: a business not yet understood (model card
  completeness insufficient) is a high-severity pricing entry listed in `evidence_gaps`; review then
  withholds the price conclusion ('understanding_insufficient') so the decision is insufficient_evidence.
- pricing: real risks that lower attractiveness or confidence and belong in the required margin
  of safety, not in a veto.
- accepted_priced: risks the executed bear scenario already models (in_bear_case: the bear rows
  are materially worse than base on exactly the levers the risk acts on, measured from execution,
  not from labels) or whose explicit cost-of-equity add-on (priced_via) moves base value materially:
  removing it raises base value today by at least max(half the base-to-bear markdown, 10%) (15% when
  the markdown is unknown). A smaller or unquantified add-on leaves the entry in class pricing.
- withhold_price: evidenced going-concern doubt, material weakness or restatement (a 'reported'
  FactBook qualitative item, or a weak governance/management assessment with evidence ids). A
  pricing entry, not a veto; review withholds the price conclusion until an independent reviewer
  documents an override.
Every entry carries pricing_status (the rule of decision.risk_pricing): priced (in the bear path or a
material add-on), partially_priced (an add-on below the bar or not quantifiable; not in the bear path),
not_priced, open_question (an unassessed dimension) or gate (disqualifying); pricing_basis says why and
priced_via_effect holds the add-on, the base value without it, the value effect and the bar.
val.wide_range and val.terminal_share repeat error_size triggers: they are flagged valuation_error
(the size of possible valuation error, not a company risk).
Unknown business dimensions (including governance, management, moat and retention) become
'unassessed' entries whose severity is their relevance to the business type: an unknown is an open
research question, never an invented risk.

Failure scenarios re-run drivers.build_case + valuation.value_company on documented edits of the
bear driver row (type-specific permanent-loss mechanisms); every edit is at least as severe as the bear
on the lever it changes (a shrinking bear keeps shrinking), and a variant whose value is still not below
the bear is flagged not_more_severe_than_bear. When that path runs out of cash the gap is
filled with equity at a depressed price, so the loss shows up as dilution instead of a withheld
number. Severity thresholds are descriptive screens, never automatic rejections.

error_size measures only the possible VALUATION error (scenario range, discount-rate sensitivity,
terminal dependence, unverifiable red-team checks). Data limits, business understanding and unassessed
dimensions are process gaps listed separately (error_size.process_gaps): they lower confidence, and
must not also widen the valuation error or the required return.
"""
from copy import deepcopy
from datetime import date, timedelta
import re
from . import facts as F
from . import valuation_tools as vt
from .capital import accreted_value, summarize
from .drivers import build_case, RATIO_KEYS
from .review import num, metric, scen, resolved, valuation_parts, annual_revenue, redteam_check
from .scores import forecast_base_rate
from .valuation import value_company, years

CRITICAL_FIELDS = {'shares_outstanding', 'cash_and_liquid_investments', 'investments', 'debt', 'convertibles', 'post_period_events',
                   'revenue_ttm', 'operating_income_ttm', 'cfo_ttm', 'capex_ttm', 'sbc_ttm'}
DIMENSIONS = ('competition', 'substitutes', 'customer_concentration', 'technology_ai', 'regulation', 'geopolitics',
              'governance', 'management', 'moat', 'retention')
# relevance of each dimension (order above) by business model: H/M/L sets the severity of an UNASSESSED dimension
RELEVANCE = {'subscription_software': 'HMLHLLMMHH', 'platform_marketplace': 'HMLHHMMMHM', 'semiconductor': 'HMHHMHMMML',
             'hardware_manufacturing': 'HMHMLHMMML', 'capital_intensive_infrastructure': 'MMHMHMMMMM', 'consumer_brand': 'HHMLMMMMHM',
             'industrial': 'MMMMMMMMMM', 'financials': 'MMLMHMMMMM', 'pre_revenue_biotech': 'HHLMHLMHML', 'conglomerate': 'MMMMMMMMMM',
             'reit': 'MLMLMLMMLM', 'commodity': 'MMMLHHMMLL', 'healthcare_products': 'HHMMHMMMHL',
             'healthcare_services': 'MMMMHLMMML', 'it_services': 'HHMHLMMMMH', 'distribution': 'HMMLLMMMML'}
DEFAULT_RELEVANCE = 'M' * len(DIMENSIONS)   # an unclassified business (blank label, publishing, hospitals...): medium on every dimension
assert all(len(v) == len(DIMENSIONS) and set(v) <= set('HML') for v in RELEVANCE.values()), 'RELEVANCE needs one H/M/L per dimension'
LEVEL = {'H': 'high', 'M': 'medium', 'L': 'low'}
DIM_LEVERS = {'competition': ('growth', 'margin'), 'substitutes': ('growth', 'margin'), 'customer_concentration': ('growth',),
              'technology_ai': ('growth', 'margin'), 'regulation': ('margin',), 'geopolitics': ('growth',),
              'governance': (), 'management': ('growth', 'margin'), 'moat': ('margin',), 'retention': ('growth',)}
# a business risk counts as in the bear case only when the bear names it (thesis, or an assumption whose bear value differs)
DIM_LINK = {'competition': r'competit|share loss|price war|pricing pressure', 'substitutes': r'substitut',
            'customer_concentration': r'customer|concentrat|churn', 'technology_ai': r'\bai\b|artificial intelligence|technolog|disrupt|automation',
            'regulation': r'regulat|antitrust|licen[cs]', 'geopolitics': r'geopolit|tariff|export control|sanction|china|taiwan',
            'governance': r'governance', 'management': r'management|\bceo\b|\bcfo\b|execution|capital allocation',
            'moat': r'moat|competitive advantage|pricing power|commoditi', 'retention': r'retention|churn|renewal'}
DIM_RATE_WORDS = {'competition': (), 'substitutes': (), 'customer_concentration': ('concentrat',), 'technology_ai': ('technolog', 'disrupt'),
                  'regulation': ('regulat',), 'geopolitics': ('country', 'geopolit', 'sovereign'), 'governance': ('governance',),
                  'management': ('management', 'key_person'), 'moat': (), 'retention': ()}
# evidenced red flags that withhold a price conclusion until an independent reviewer documents an override
RED_FLAGS = {'going_concern': (r'substantial doubt|ability to continue as a going concern|going[- ]concern (?:doubt|uncertaint|qualif|warning|opinion|paragraph|emphasis)',
                               'going-concern doubt', 'ספק מהותי ביכולת החברה להמשיך לפעול'),
             'material_weakness': (r'material weakness', 'material weakness in internal control', 'חולשה מהותית בבקרה הפנימית על הדיווח הכספי'),
             'restatement': (r'restatement|restated (?:[\w-]+ ){0,4}financial statements|non[- ]reliance on previously issued',
                             'restatement of financial statements', 'הצגה מחדש של דוחות כספיים')}
NEGATED = re.compile(r'\b(?:no|without|absence of|free of|did not identify(?: any)?|not identified any|there (?:were|was|are|is) no)\s+(?:\w+\s+){0,2}$')
LOSS = {'subscription_software': 'growth_stall', 'platform_marketplace': 'growth_stall', 'consumer_brand': 'growth_stall',
        'conglomerate': 'growth_stall', 'financials': 'growth_stall', 'industrial': 'cyclical_collapse',
        'hardware_manufacturing': 'cyclical_collapse', 'semiconductor': 'cyclical_collapse', 'commodity': 'cyclical_collapse',
        'capital_intensive_infrastructure': 'utilization_collapse', 'reit': 'utilization_collapse', 'pre_revenue_biotech': 'funding_failure',
        'healthcare_products': 'growth_stall', 'healthcare_services': 'growth_stall', 'it_services': 'growth_stall',
        'distribution': 'growth_stall'}
DROP = {'semiconductor': -.30, 'commodity': -.30, 'hardware_manufacturing': -.20, 'industrial': -.20}
ISSUE_DISCOUNT, ISSUE_FEE = .5, .05      # distressed raise: half the current quote, 5% fees (stated stress, not a forecast)
MECHANISM = {
    'growth_stall': 'Demand saturates or is taken by competitors/substitutes: growth stalls near zero, operating leverage never '
                    'arrives and margins compress; the market re-rates the business as ex-growth.',
    'cyclical_collapse': 'A cyclical or competitive downturn cuts volume and price while fixed costs, committed capex and debt '
                         'stay; margins collapse and do not fully recover.',
    'utilization_collapse': 'Utilization or pricing per unit collapses on long-lived, debt-financed capacity: revenue falls, '
                            'fixed costs and interest remain, refinancing becomes expensive.',
    'funding_failure': 'Growth falls short, uncommitted financing is not available, and the cash gap is closed with equity '
                       'issued at a depressed price: current holders are diluted permanently.'}
SIGNS = {
    'growth_stall': ['Reported revenue growth falling toward 0% for two consecutive quarters (base assumes {g:.0%} next year)',
                     'Billings, deferred revenue or RPO growing slower than revenue (renewal pipeline weakening)',
                     'Net revenue retention, customer counts or seats declining in filings or calls',
                     'Operating margin after SBC below {m:.0%} without a cost response',
                     'SBC as a share of revenue rising while growth slows'],
    'cyclical_collapse': ['Revenue down {d:.0%} year over year while capex commitments are unchanged',
                          'Book-to-bill below 1, inventory or receivables rising faster than revenue',
                          'Gross margin falling for two quarters (price cuts, under-absorption)',
                          'Interest coverage below 2x, covenant amendments or new secured debt',
                          'Impairments of PP&E, inventory write-downs'],
    'utilization_collapse': ['Utilization, occupancy or price per unit falling in quarterly disclosures',
                             'Revenue down {d:.0%} while operating costs and interest are flat',
                             'Customer contract non-renewals or renegotiations at lower prices',
                             'Refinancing at higher spreads, covenant waivers or secured debt',
                             'Asset impairments or delayed capacity projects'],
    'funding_failure': ['Cash runway below 12 months at the latest quarter (cash / quarterly burn)',
                        'New shelf registration, ATM program or equity raise below the prior quarter price',
                        'Going-concern language in the auditor report or 10-Q',
                        'Uncommitted facilities not drawn or terms tightened (higher coupons, attached warrants)',
                        'Capex or R&D programs deferred to conserve cash']}
WMBT_UNREACHABLE_HE = {'growth': 'אף קצב צמיחה סביר לבדו לא מצדיק את המחיר בתרחיש הבסיס',
                       'mature_margin': 'אף רמת רווחיות בוגרת סבירה לבדה לא מצדיקה את המחיר בתרחיש הבסיס',
                       'capital_efficiency': 'אף שיפור סביר ביעילות ההון לבדו לא מצדיק את המחיר בתרחיש הבסיס',
                       None: 'אף שינוי סביר בהנחה אחת לבדה לא מצדיק את המחיר בתרחיש הבסיס'}
ERROR_SCOPE = ('Counted in error_size: it lowers confidence and widens the marginal band; it is not a business risk and not part of '
               'the required return.')
CLASS_ORDER = {'disqualifying': 0, 'pricing': 1, 'accepted_priced': 2}
SEV_ORDER = {'high': 0, 'medium': 1, 'low': 2}


def _latest(an, key):
    t = (an or {}).get('ttm') or {}
    if num(t.get(key)) is not None:
        return num(t[key]), 'analysis.ttm.' + key
    annual = (an or {}).get('annual') or {}
    for end in sorted(annual, reverse=True):
        if num((annual[end] or {}).get(key)) is not None:
            return num(annual[end][key]), f'analysis.annual.{end}.{key}'
    return None, None


def _loss(v, price):
    return None if v is None or not price else max(0., 1 - v / price)


def _gap(v, price):
    return None if v is None or not price else v / price - 1


def bear_levers(case, result):
    """Levers on which the executed bear is materially worse than base, from rows and inputs (not labels)."""
    if not case or not result or case.get('method') != 'operating':
        return {}
    rb, rs, cb, cs = scen(result, 'bear'), scen(result, 'base'), scen(case, 'bear'), scen(case, 'base')
    if not all((rb, rs, cb, cs)):
        return {}

    def margin(rows):
        rev = sum(r['revenue'] for r in rows)
        return sum(r['ebit'] for r in rows) / rev if rev > 0 else 0.

    def share(c, rows, *keys):
        rev = sum(r['revenue'] for r in rows)
        return sum(sum(num(p.get(k)) or 0 for k in keys) for p in c['periods']) / rev if rev > 0 else 0.

    def mean(c, k):
        xs = [num(p.get(k)) or 0 for p in c['periods']]
        return sum(xs) / len(xs)

    def issue(c):
        xs = [num(p.get('equity_issue_price')) for p in c['periods'] if (num(p.get('equity_proceeds')) or 0) > 0]
        return min(xs) if xs else None

    def lever(modeled, bear, base, measure):
        return {'modeled': bool(modeled), 'bear': bear, 'base': base, 'measure': measure}
    gb, gs = annual_revenue(rb['forecast'][-1]) or 0, annual_revenue(rs['forecast'][-1]) or 0
    mb, ms = margin(rb['forecast']), margin(rs['forecast'])
    tb, ts = cb['terminal']['operating_margin'], cs['terminal']['operating_margin']
    shb, shs = rb['forecast'][-1]['shares'], rs['forecast'][-1]['shares']
    sbb, sbs = share(cb, rb['forecast'], 'sbc_expense'), share(cs, rs['forecast'], 'sbc_expense')
    irb, irs = mean(cb, 'interest_rate'), mean(cs, 'interest_rate')
    db, ds = rb['forecast'][-1]['debt'], rs['forecast'][-1]['debt']
    ib, is_ = issue(cb), issue(cs)
    kb, ks = (cb['cost_of_equity'], cb['terminal']['wacc']), (cs['cost_of_equity'], cs['terminal']['wacc'])
    cpb, cps = share(cb, rb['forecast'], 'maintenance_capex', 'growth_capex'), share(cs, rs['forecast'], 'maintenance_capex', 'growth_capex')
    return {
        'growth': lever(gb < gs * .95, gb, gs, 'final explicit annualized revenue; modeled when bear < 95% of base'),
        'margin': lever(mb < ms - .01 or tb < ts - .01, [mb, tb], [ms, ts], 'average explicit EBIT margin or terminal margin at least 1pp lower'),
        'dilution': lever(shb > shs * 1.01 or sbb > sbs + .005, [shb, sbb], [shs, sbs], 'final shares +1% or SBC/revenue +0.5pp'),
        'financing': lever(irb > irs + .0025 or db > ds * 1.05 + 1 or (ib is not None and (is_ is None or ib < is_)), [irb, db, ib], [irs, ds, is_],
                           'interest rate +25bp, final debt +5%, or equity issued at a lower price'),
        'discount': lever(kb[0] > ks[0] + .0025 or kb[1] > ks[1] + .0025, kb, ks, 'cost of equity or terminal WACC +25bp'),
        'capex': lever(cpb > cps + .005, cpb, cps, 'capex/revenue +0.5pp')}


def liquidity(fb, qa, an):
    """Liquid resources, near-term maturities, refinancing need and runway (inputs and formula recorded)."""
    cash, sti = resolved(qa, 'cash'), resolved(qa, 'short_term_investments')
    basis = 'dataqa.resolved cash + short_term_investments'
    if cash is None:
        cash, _ = _latest(an, 'cash')
        sti, _ = _latest(an, 'investments')
        basis = 'analysis latest cash + investments'
    if cash is not None and min(cash, sti or 0) < 0:      # a data error (dataqa rejects it), never a liquidity figure
        cash, sti, basis = None, None, basis + ': negative cash or investments reported (a data error): liquidity unknown'
    liquid = None if cash is None else cash + (sti or 0)
    fcf, fsrc = _latest(an, 'fcf')
    near, nbasis, horizon = None, 'no maturity data', 1
    cs, as_of = (fb or {}).get('capital_structure'), (fb or {}).get('as_of')
    if cs and as_of:
        horizon, limit = 2, date.fromisoformat(as_of) + timedelta(days=730)
        rows = [x for x in cs.get('debt', []) if x.get('kind') != 'lease' and x.get('maturity') and date.fromisoformat(x['maturity']) <= limit]
        near = sum(accreted_value(x, cs['as_of']) for x in rows)
        nbasis = f"capital_structure instruments maturing by {limit.isoformat()}: {[x['id'] for x in rows]}"
    elif (fb or {}).get('facts'):
        ref = F.latest_instant(fb, 'total_assets') or F.latest_instant(fb, 'cash')
        if ref:
            near = sum(F.value(fb, m, period_end=ref['period_end'], default=0) for m in ('current_portion_ltd', 'short_term_borrowings'))
            nbasis = f"current debt tagged at the latest balance sheet {ref['period_end']} (current_portion_ltd + short_term_borrowings; an older tag is not carried forward)"
    gap = near - liquid - max(fcf or 0, 0) * horizon if near is not None and liquid is not None else None
    return {'liquid': liquid, 'liquid_basis': basis, 'fcf': fcf, 'fcf_source': fsrc, 'near_term_maturities': near, 'maturity_basis': nbasis,
            'horizon_years': horizon, 'refinancing_gap': gap, 'refinancing_need': bool(near and gap is not None and gap > 0),
            'runway_years': liquid / -fcf if fcf is not None and fcf < 0 and liquid is not None else None,
            'formula': 'refinancing_gap = maturities within horizon - liquid - max(FCF, 0) x horizon; runway = liquid / -FCF when FCF < 0'}


def bear_text(case, fc):
    """Text in which the bear explicitly names its mechanisms: bear thesis plus assumptions whose bear value differs from base."""
    parts = [(scen(case, 'bear') or {}).get('thesis') or '', (scen((fc or {}).get('spec'), 'bear') or {}).get('thesis') or '']
    for a in (fc or {}).get('assumptions') or []:
        vals = a.get('values') or {}
        if 'bear' in vals and vals.get('bear') != vals.get('base'):
            parts += [str(a.get(k) or '') for k in ('id', 'topic', 'rationale')]
    ids = {r for a in (fc or {}).get('assumptions') or [] for r in a.get('risk_ids') or []}
    return ' '.join(parts).lower(), ids


def rate_effect(case, today):
    """(effect, material): effect(add) = how much a cost-of-equity add-on lowers base value today (1 - base / base without
    it), and the materiality bar max(half the base-to-bear markdown, 10%), 15% when the markdown is unknown or not
    positive. The rule of decision.risk_pricing; None when the add-on cannot be quantified on an executed cash-flow case."""
    base, bear = num((today or {}).get('base')), num((today or {}).get('bear'))
    gap = 1 - bear / base if base and base > 0 and bear is not None else None
    material = max(.5 * gap, .10) if gap is not None and gap > 0 else .15
    memo = {}

    def effect(add):
        if add is None or add <= 0 or not base or base <= 0 or (case or {}).get('method') not in ('operating', 'residual_income'):
            return None
        if add not in memo:
            try:
                v0 = num(vt.shifted_value(case, 'base', -add))
            except (ValueError, KeyError, ZeroDivisionError):
                v0 = None
            memo[add] = {'add_on': add, 'base_value': base, 'base_value_without_add_on': v0,
                         'value_effect': 1 - base / v0 if v0 and v0 > 0 else None, 'material_threshold': material}
        return memo[add]
    return effect, material


def _pricing(cls, modeled, via, eff, material, unassessed):
    """(pricing_status, basis): the decision.risk_pricing rule applied in the register."""
    if cls == 'disqualifying':
        return 'gate', 'disqualifying condition (not priced; a gate)'
    if modeled:
        return 'priced', 'the executed bear path models it'
    if via:
        e, add = (eff or {}).get('value_effect'), (eff or {}).get('add_on')
        if e is not None and e >= material:
            return 'priced', f'cost-of-equity add-on {add:.2%} lowers base value {e:.0%} (material: >= {material:.0%})'
        return 'partially_priced', (f'cost-of-equity add-on {add:.2%} lowers base value only {e:.0%} (material: >= {material:.0%}); '
                                    'not in the bear path' if e is not None else
                                    'claimed priced via the cost of equity, effect not quantified; not in the bear path')
    if unassessed:
        return 'open_question', 'not assessed: an open research question'
    return 'not_priced', 'not in the bear path and no quantified adjustment'


def _register(levers, components, text='', linked_ids=(), effect=None, material=.15):
    reg = []

    def add(rid, category, description, severity, cls=None, evidence=(), lever=(), rate_words=(), link=None, **extra):
        modeled = bool(lever) and all(levers.get(x, {}).get('modeled') for x in lever)
        if modeled and link and rid not in linked_ids and not re.search(link, text):
            modeled, extra['bear_link'] = False, 'bear is worse on these levers but does not name this risk'
        key = next((k for k, v in components.items() if (rate_words and any(w in k.lower() for w in rate_words) and (num(v) or 0) > 0)
                    or (isinstance(v, dict) and rid in (v.get('covers') or []))), None)
        via = f'forecast.cost_of_equity.components.{key}' if key else None
        eff = effect(num(components[key])) if key and effect and cls != 'disqualifying' and not modeled else None
        status, basis = _pricing(cls, modeled, via, eff, material, extra.get('status') == 'unassessed' or extra.get('assessed') is False)
        row = {'id': rid, 'category': category, 'description': description, 'evidence_ids': sorted(set(map(str, evidence))),
               'class': cls or ('accepted_priced' if status == 'priced' else 'pricing'), 'severity': severity,
               'in_bear_case': modeled and cls != 'disqualifying', 'levers': list(lever), 'pricing_status': status, 'pricing_basis': basis}
        if via and cls != 'disqualifying':
            row['priced_via'] = via
            if eff:
                row['priced_via_effect'] = eff
        row.update(extra)
        reg.append(row)
        return row
    return reg, add


def _data(add, qa, rt):
    fields = (qa or {}).get('fields') or {}
    for f in (qa or {}).get('limited') or []:
        fd = fields.get(f) or {}
        add(f'data.limited.{f}', 'data', f"{f}: {fd.get('status', 'limited')}" + (f" ({fd['note']})" if fd.get('note') else '')
            + '; figures that depend on it carry lower confidence.', 'medium' if f in CRITICAL_FIELDS and fd.get('status') in ('conflict', 'missing') else 'low',
            evidence=['dataqa.fields.' + f] + list(fd.get('sources') or []))
    if rt['critical_unverifiable']:
        add('data.redteam_unverifiable', 'data', 'Critical red-team checks cannot be verified from available data: '
            + ', '.join(rt['critical_unverifiable']) + '. The price conclusion waits for verification.', 'high',
            evidence=['review.redteam.' + c for c in rt['critical_unverifiable']])


def _financial(add, an, liq):
    nde, nc = metric(an, 'net_debt_to_ebitda'), metric(an, 'net_cash')
    if nde is not None and nde > 2.5:
        add('fin.leverage', 'financial', f'Net debt / EBITDA {nde:.1f}x.', 'high' if nde > 4 else 'medium',
            evidence=['analysis.metrics.net_debt_to_ebitda'], lever=('financing',), rate_words=('leverage', 'credit'), values={'net_debt_to_ebitda': nde})
    elif nde is not None and nde < 0 and nc is not None and nc < 0:
        add('fin.leverage', 'financial', 'Net debt with negative EBITDA: debt cannot be serviced from operations.', 'high',
            evidence=['analysis.metrics.net_debt_to_ebitda', 'analysis.metrics.net_cash'], lever=('financing',), values={'net_debt_to_ebitda': nde, 'net_cash': nc})
    cov = metric(an, 'interest_coverage')
    if cov is not None and cov < 5:
        add('fin.coverage', 'financial', f'Interest coverage {cov:.1f}x (operating income / interest expense).', 'high' if cov < 2 else 'medium',
            evidence=['analysis.metrics.interest_coverage'], lever=('financing',), values={'interest_coverage': cov})
    near, liquid = liq['near_term_maturities'], liq['liquid']
    if liq['refinancing_need']:
        add('fin.refinancing', 'financial', f"Debt maturing within {liq['horizon_years']}y ({near:,.0f}) exceeds liquid resources ({liquid:,.0f}) "
            f"plus {liq['horizon_years']}y of positive FCF: refinancing is required.", 'high', evidence=['risk.liquidity'], lever=('financing',), values=liq)
    elif near and liquid and near > .5 * liquid:
        add('fin.refinancing', 'financial', f'Near-term maturities ({near:,.0f}) exceed half of liquid resources ({liquid:,.0f}).', 'medium',
            evidence=['risk.liquidity'], lever=('financing',), values=liq)
    if liq['runway_years'] is not None and liq['runway_years'] < 3:
        add('fin.runway', 'financial', f"Negative free cash flow: liquidity covers {liq['runway_years']:.1f} years of burn.",
            'high' if liq['runway_years'] < 1.5 else 'medium', evidence=['risk.liquidity', liq['fcf_source']], lever=('financing',), values=liq)
    elif liq['fcf'] is not None and liq['fcf'] < 0 and liquid is None:
        add('fin.runway', 'financial', 'Negative free cash flow with unknown liquidity.', 'medium', evidence=[liq['fcf_source']], lever=('financing',))


def _dilution(add, an, fb):
    sbc = metric(an, 'sbc_pct_revenue')
    if sbc is not None and sbc >= .05:
        off = metric(an, 'sbc_offset_by_buybacks')
        add('dil.sbc', 'dilution', f'Stock-based compensation {sbc:.1%} of revenue: a real cost to holders whether expensed or diluting'
            + ('; currently offset by buybacks, which consume cash.' if off and off >= 1 else '.'), 'high' if sbc >= .12 else 'medium',
            evidence=['analysis.metrics.sbc_pct_revenue'], lever=('dilution',), values={'sbc_pct_revenue': sbc, 'sbc_offset_by_buybacks': off})
    g = metric(an, 'share_count_cagr_3y')
    if g is not None and g > .015:
        add('dil.share_growth', 'dilution', f'Share count growing {g:.1%}/yr over three years.', 'high' if g > .04 else 'medium',
            evidence=['analysis.metrics.share_count_cagr_3y'], lever=('dilution',), values={'share_count_cagr_3y': g})
    cs = (fb or {}).get('capital_structure')
    if cs:
        try:
            s = summarize(cs)
        except (ValueError, KeyError) as e:
            add('data.capital_structure', 'data', f'Capital structure invalid: {e}', 'medium', evidence=['factbook.capital_structure'])
            return
        conv, basic = sum(r['conversion_shares'] for r in s['debt']), s['basic_economic_shares']
        if basic and conv / basic > .03:
            ids = [x['source_id'] for x in cs.get('debt', []) if x.get('kind') == 'convertible']
            add('dil.convertibles', 'dilution', f'Convertibles can add {conv:,.0f} shares ({conv / basic:.1%} of basic economic shares).',
                'high' if conv / basic > .10 else 'medium', evidence=ids, lever=('dilution',), values={'conversion_shares': conv, 'basic_shares': basic})


def _accounting(add, an):
    fz = (an or {}).get('forensics') or {}
    ben, alt, pio = fz.get('beneish') or {}, fz.get('altman') or {}, fz.get('piotroski') or {}
    ni, _ = _latest(an, 'net_income')
    cfo_ni, acc = metric(an, 'cfo_to_net_income'), metric(an, 'accruals_ratio')
    weak = (cfo_ni is not None and (ni or 0) > 0 and cfo_ni < .8) or (acc is not None and acc > .10)
    if weak:
        add('acc.cash_conversion', 'accounting', f'Weak cash conversion: CFO / net income {cfo_ni}, accruals ratio {acc} (screen: CFO/NI < 0.8 or accruals > 10%).',
            'high' if cfo_ni is not None and cfo_ni < .5 else 'medium', evidence=['analysis.metrics.cfo_to_net_income', 'analysis.metrics.accruals_ratio'],
            values={'cfo_to_net_income': cfo_ni, 'accruals_ratio': acc})
    comp = ben.get('components') or {}
    if ben.get('zone') in ('likely_manipulator', 'watch'):
        add('acc.beneish', 'accounting', f"Beneish M-score {num(ben.get('m')) if num(ben.get('m')) is not None else '?'} in zone {ben['zone']} (screen, not an accusation); "
            f"SGI {num(comp.get('SGI'))} - fast growth raises the score by construction. Check receivables, capitalised costs, accruals.",
            'high' if ben['zone'] == 'likely_manipulator' else 'medium', evidence=['analysis.forensics.beneish'])
    if alt.get('zone') in ('distress', 'grey'):
        add('acc.altman', 'accounting', f"Altman {alt.get('model', 'Z')} = {num(alt.get('z'))} in zone {alt['zone']} (weak for buyback-heavy or young firms).",
            'high' if alt['zone'] == 'distress' else 'low', evidence=['analysis.forensics.altman'])
    if pio.get('zone') == 'weak':
        add('acc.piotroski', 'accounting', f"Piotroski F-score {pio.get('score')}/{pio.get('out_of')} weak: deteriorating fundamentals on most signals.",
            'medium', evidence=['analysis.forensics.piotroski'])
    return weak


def _business(add, card):
    dims = (card or {}).get('dimensions') or {}
    primary = ((card or {}).get('business_model') or {}).get('primary')
    rel = RELEVANCE.get(primary, DEFAULT_RELEVANCE)
    early = ((card or {}).get('stage') or {}).get('value') == 'early'
    for i, d in enumerate(DIMENSIONS):
        level = 'high' if d == 'customer_concentration' and early else LEVEL[rel[i]]
        x = dims.get(d) or {}
        a, ev = x.get('assessment', 'unknown'), list(x.get('evidence_ids') or [])
        label = d.replace('_', ' ')
        if a == 'weak':
            add(f'biz.{d}', 'business', f'{label}: assessed weak' + (f" - {x['note']}" if x.get('note') else '') + ('' if ev else ' (asserted without evidence ids)'),
                'high' if ev else 'medium', evidence=ev + [f'model_card.dimensions.{d}'], lever=DIM_LEVERS[d], rate_words=DIM_RATE_WORDS[d], link=DIM_LINK[d],
                assessed=True, assessment=a, relevance=level, source=x.get('source'))
        elif a == 'adequate':
            add(f'biz.{d}', 'business', f'{label}: assessed adequate (exposed but not weak)' + (f" - {x['note']}" if x.get('note') else ''), 'low',
                evidence=ev + [f'model_card.dimensions.{d}'], lever=DIM_LEVERS[d], rate_words=DIM_RATE_WORDS[d], link=DIM_LINK[d], assessed=True, assessment=a, relevance=level, source=x.get('source'))
        elif a != 'strong':
            add(f'biz.{d}', 'business', f"{label}: not assessed; relevance to {primary or 'this business'} is {level}. An open research question, not an evidenced risk.",
                level, evidence=[f'model_card.dimensions.{d}'], assessed=False, status='unassessed', relevance=level)


def _red_flags(add, fb, card):
    """Going-concern doubt, material weakness, restatement: evidenced only by a 'reported' FactBook qualitative item or a
    weak governance/management assessment that carries evidence ids. Explicit negations ('no material weakness') do not count."""
    texts = [(f"{q.get('topic', '')}: {q.get('statement', '')}", [q['id']] + list(q.get('source_ids') or []))
             for q in (fb or {}).get('qualitative') or [] if q.get('kind') == 'reported' and q.get('id')]
    for d in ('governance', 'management'):
        x = ((card or {}).get('dimensions') or {}).get(d) or {}
        if x.get('assessment') == 'weak' and x.get('evidence_ids') and x.get('note'):
            texts.append((str(x['note']), list(x['evidence_ids']) + [f'model_card.dimensions.{d}']))
    for key, (pattern, label, label_he) in RED_FLAGS.items():
        hits = [(t, ev) for t, ev in texts for mt in re.finditer(pattern, t, re.I) if not NEGATED.search(t[max(0, mt.start() - 60):mt.start()].lower())]
        if hits:
            add(f'gov.{key}', 'governance', f'Evidenced {label}: ' + ' | '.join(dict.fromkeys(t.strip() for t, _ in hits)) + '. The price conclusion is withheld '
                'until an independent reviewer documents an override (review risk_overrides).', 'high', cls='pricing',
                evidence=[i for _, ev in hits for i in ev], withhold_price=True, description_he=label_he)


def _execution(add, an, card, result, case, val):
    f = (card or {}).get('funding') or {}
    if f.get('value') == 'needs_external':
        add('exe.external_funding', 'execution', 'The business needs external capital to execute its plan'
            + (f": {'; '.join(map(str, f.get('reasons') or []))}" if f.get('reasons') else '.'), 'high', evidence=['model_card.funding'], lever=('financing',))
    if result and case and case.get('method') == 'operating':
        sb, rb = scen(case, 'base'), scen(result, 'base')
        ext = [(p['end'], (num(p.get('debt_draw')) or 0) + (num(p.get('equity_proceeds')) or 0), p.get('financing_status')) for p in sb['periods']]
        total, assumed = sum(x[1] for x in ext), [x[0] for x in ext if x[1] > 0 and x[2] == 'assumed']
        if total > 0:
            add('exe.financing_plan', 'execution', f'Base plan relies on {total:,.0f} of external financing'
                + (f'; uncommitted (assumed) in periods ending {assumed}.' if assumed else ', all committed.'), 'high' if assumed else 'medium',
                evidence=['valuation.case.scenarios.base.periods'], lever=('financing',), values={'external_financing': total, 'uncommitted_periods': assumed})
        rev = sum(r['revenue'] for r in rb['forecast'])
        plan = sum(r['maintenance_capex'] + r['growth_capex'] for r in rb['forecast']) / rev if rev > 0 else None
        hist = metric(an, 'capex_pct_revenue')
        if plan is not None and hist is not None:
            if plan > max(hist * 1.5, hist + .05):
                add('exe.capex_plan', 'execution', f'Planned capex {plan:.1%} of revenue versus {hist:.1%} historically: delivery, overrun and funding risk.',
                    'medium', evidence=['analysis.metrics.capex_pct_revenue', 'valuation.result.scenarios.base.forecast'], lever=('capex',), values={'plan': plan, 'history': hist})
            elif hist > .10 and plan < hist * .5:
                add('val.capex_below_history', 'valuation', f'Planned capex {plan:.1%} of revenue is under half of history {hist:.1%}: free cash flow may be flattered by under-investment.',
                    'medium', evidence=['analysis.metrics.capex_pct_revenue', 'valuation.result.scenarios.base.forecast'], values={'plan': plan, 'history': hist})
    stress = (((val or {}).get('diagnostics') or {}).get('stress') or {}).get('stresses') or []
    shocks = [s['shock'] for s in stress if s.get('status') == 'funding_blocked' and (s.get('first_funding_gaps') or {}).get('base')]
    if shocks:
        add('exe.stress_funding_gap', 'execution', f'Fixed stresses {shocks} open a funding gap in the base plan.', 'high' if len(shocks) >= 2 else 'medium',
            evidence=['valuation.diagnostics.stress'], lever=('financing',))


def _valuation(add, an, result, today, price, val):
    if not result:
        return
    base = scen(result, 'base') or {}
    ts = num(base.get('terminal_share_of_present_value'))
    if ts is not None and ts > .8:
        add('val.terminal_share', 'valuation', f'Size of possible valuation error, not a company risk: {ts:.0%} of present value comes from terminal '
            'equity, so the value rests mostly on maturity assumptions (margin, ROIC and growth after the explicit years). ' + ERROR_SCOPE,
            'high' if ts > .9 else 'medium', evidence=['valuation.result.scenarios.base.terminal_share_of_present_value', 'risk.error_size'],
            values={'terminal_share': ts}, valuation_error=True)
    v = {k: num(today.get(k)) for k in ('bear', 'base', 'bull')}
    rel = (v['bull'] - v['bear']) / v['base'] if None not in v.values() and v['base'] > 0 else None
    if rel is not None and rel > 1.0:
        add('val.wide_range', 'valuation', f'Size of possible valuation error, not a company risk: the bear-to-bull range is {rel:.0%} of base value, '
            'so the value estimate is highly uncertain. ' + ERROR_SCOPE, 'high' if rel > 2 else 'medium',
            evidence=['valuation.result.annual_values', 'risk.error_size'], values={'value_range_rel': rel, **v}, valuation_error=True)
    rows = [{'period_end': e, 'revenue': num((r or {}).get('revenue'))} for e, r in sorted(((an or {}).get('annual') or {}).items()) if num((r or {}).get('revenue'))]
    br = forecast_base_rate(rows, result) if rows else None
    for f in (br or {}).get('flags', []):
        g, h = br['implied']['implied_revenue_cagr'], br['historical_cagr']
        rare = f['key'] == 'rare_sustained_growth'
        text = (f'Base case implies {g:.0%}/yr revenue growth for about five years; sustained growth above 20% is rare (base rates).'
                if rare else f'Base case growth {g:.0%}/yr is more than 10pp above the company history {h:.0%}/yr.')
        he = (f'התחזית מניחה צמיחה של כ־{g:.0%} בשנה במשך כחמש שנים, קצב שמעט חברות מחזיקות לאורך זמן' if rare else
              f'התחזית מניחה צמיחה של כ־{g:.0%} בשנה, הרבה מעל כ־{h:.0%} בשנה שהחברה השיגה בעבר' if h is not None else
              'התחזית מניחה צמיחה מהירה בהרבה מזו שהחברה השיגה בעבר')
        add(f"val.base_rate.{f['key']}", 'valuation', text, 'high' if rare else 'medium',
            evidence=['scores.forecast_base_rate'], values={'implied': br['implied'], 'historical_cagr': h}, description_he=he)
    diag = (val or {}).get('diagnostics') or {}
    rev = diag.get('reverse') or diag.get('reverse_price') or {}
    hist_g = metric(an, 'revenue_cagr_3y') if metric(an, 'revenue_cagr_3y') is not None else metric(an, 'revenue_cagr_5y')
    if rev.get('status') == 'solved':
        path = rev.get('implied_growth_path')
        if path and hist_g is not None and sum(path) / len(path) > hist_g + .05:
            avg = sum(path) / len(path)
            add('val.price_implies_growth', 'valuation', f'The price implies average growth {avg:.1%}/yr versus {hist_g:.1%}/yr demonstrated '
                '(uniform-shift sensitivity of this spec, not market consensus).', 'high' if avg > hist_g + .10 else 'medium',
                evidence=['valuation.diagnostics.reverse', 'analysis.metrics.revenue_cagr_3y'], values={'implied_average_growth': avg, 'historical_cagr': hist_g})
        m = num(rev.get('multiplier'))
        if m is not None and m > 1.25:
            add('val.price_requires_revenue', 'valuation', f'The price needs base revenue per unit {m:.2f}x the forecast with costs, capex and terminal fixed.',
                'high' if m > 1.6 else 'medium', evidence=['valuation.diagnostics.reverse_price'], values={'multiplier': m})
    above = price is not None and v['base'] is not None and price > v['base']
    best = metric(an, 'operating_margin_best_3y')
    for w in (val or {}).get('what_must_be_true') or []:
        lever, label = str(w.get('lever', '')), str(w.get('label') or w.get('lever', ''))
        req, anc = num(w.get('required')), num(w.get('anchor'))
        if lever == 'cost_of_equity':
            continue                     # the return implied by the price is expected return (decision), not a business requirement
        if w.get('status') == 'not_reachable':
            if above:
                add(f'val.wmbt.{lever}', 'valuation', f'No value of {label} within the search bounds reconciles the base case with the price.', 'high',
                    evidence=['valuation.what_must_be_true'], values=w, description_he=WMBT_UNREACHABLE_HE.get(lever, WMBT_UNREACHABLE_HE[None]))
            continue
        if req is None or anc is None:
            continue
        sev, why, he = None, '', None
        if lever == 'growth' or ('growth' in label.lower() and lever != 'capital_efficiency'):
            stretch = req - anc
            sev = 'high' if stretch > .10 or (stretch > 0 and req > .20) else ('medium' if stretch > .05 else None)
            why = f' ({req - anc:+.1%} versus base; sustained growth above 20% is rare)' if req > .20 else f' ({req - anc:+.1%} versus base)'
            he = f'כדי להצדיק את המחיר, הצמיחה הממוצעת בהכנסות צריכה להגיע לכ־{req:.0%} בשנה, מול כ־{anc:.0%} בתרחיש הבסיס'
        elif 'margin' in lever or 'margin' in label.lower():
            sev = 'high' if req - anc > .10 or (best is not None and req > best + .10) else ('medium' if req - anc > .05 else None)
            why = f' (base {anc:.1%}' + (f', best history {best:.1%})' if best is not None else ')')
            he = f'כדי להצדיק את המחיר, הרווחיות התפעולית הבוגרת צריכה להגיע לכ־{req:.0%}, מול כ־{anc:.0%} בתרחיש הבסיס'
        elif lever == 'capital_efficiency' and req > 0:
            sev = 'high' if req < .5 else ('medium' if req < 2 / 3 else None)
            why = f' (growth must need only {req:.2f}x the capital assumed in base)'
            he = f'כדי להצדיק את המחיר, הצמיחה צריכה לדרוש רק כ־{req:.0%} מההשקעה שתרחיש הבסיס מניח'
        if sev:
            add(f'val.wmbt.{lever}', 'valuation', f'To justify the price, {label} must reach {req:.4g} versus {anc:.4g}{why}.', sev,
                evidence=['valuation.what_must_be_true'], values=w, description_he=he)


def _gates(add, fb, qa, an, card, result, val, rt, liq, weak_cash, scenarios):
    if (qa or {}).get('status') == 'blocked':
        add('gate.blocked_data', 'data', f"Critical data blocked ({', '.join((qa or {}).get('blocking') or []) or 'see dataqa'}): no price conclusion on unreliable inputs.",
            'high', cls='disqualifying', evidence=['dataqa.fields.' + f for f in (qa or {}).get('blocking') or []])
    comp = (card or {}).get('completeness') or {}
    if comp.get('status') == 'insufficient':
        add('gap.understanding_insufficient', 'business', 'Business not yet understood (model card completeness insufficient); open: '
            + (', '.join(map(str, comp.get('missing') or [])) or 'unspecified') + '. An evidence gap, not a judgement on the company.',
            'high', evidence=['model_card.completeness'], evidence_gap=True)
    if result:
        recovered = {s['name'] for s in ((val or {}).get('recovery') or {}).get('scenarios', []) if s.get('status') == 'conditional'}
        dil = next((x for x in scenarios if x['name'] == 'permanent_loss:funding_failure' and x.get('status') == 'computed'), None)
        for s in result['scenarios']:
            if s['status'] != 'funding_blocked':
                continue
            first = next((r['end'] for r in s.get('forecast', []) if r.get('funding_gap', 0) > 1e-7), None)
            gap = sum(r.get('funding_gap', 0) for r in s.get('forecast', []))
            ev = [f"valuation.result.scenarios.{s['name']}.forecast"]
            if s['name'] in recovered:
                add(f"fin.funding_gap.{s['name']}", 'financial', f"The {s['name']} scenario needs financing from {first}; a funded recovery case was supplied.",
                    'high', evidence=ev + ['valuation.recovery'], lever=('financing',), values={'first_gap': first, 'total_gap': gap})
            elif s['name'] == 'base':
                add('gate.unfunded_plan', 'execution', f'The base plan has an unfunded cash requirement from {first} (gaps total {gap:,.0f}) and no financing '
                    'or recovery case: the thesis is not executable as modeled.', 'high', cls='disqualifying', evidence=ev)
            else:
                add(f"fin.funding_gap.{s['name']}", 'financial', f"The {s['name']} scenario runs out of cash from {first} (gaps total {gap:,.0f}): holders would face a "
                    'recapitalization' + (f" (quantified by {dil['name']}: value {dil['value_per_share']:.4g} per share)." if dil else '; model it explicitly.'),
                    'high', evidence=ev + (['risk.failure_scenarios.' + dil['name']] if dil else []), values={'first_gap': first, 'total_gap': gap})
        base = scen(result, 'base') or {}
        raw = num((base.get('terminal') or {}).get('raw_equity_value'))
        if base.get('status') != 'funding_blocked' and raw is not None and raw <= 0:
            add('gate.equity_impaired', 'financial', 'Base-case claims exceed enterprise value at the horizon: equity is impaired (only option value remains).',
                'high', cls='disqualifying', evidence=['valuation.result.scenarios.base.terminal.raw_equity_value'], values={'raw_equity_value': raw})
    if rt['critical_failed']:
        add('gate.redteam_critical_failure', 'valuation', 'Critical red-team failure on inputs derived from execution: ' + ', '.join(rt['critical_failed']) + '.',
            'high', cls='disqualifying', evidence=['review.redteam.' + c for c in rt['critical_failed']])
    fz = (an or {}).get('forensics') or {}
    if (fz.get('beneish') or {}).get('zone') == 'likely_manipulator' and weak_cash:
        add('gate.manipulation_signal', 'accounting', 'Beneish likely-manipulator zone AND weak cash conversion: reported earnings are not a reliable valuation base until explained.',
            'high', cls='disqualifying', evidence=['analysis.forensics.beneish', 'analysis.metrics.cfo_to_net_income', 'analysis.metrics.accruals_ratio'])
    if (fz.get('altman') or {}).get('zone') == 'distress' and liq['fcf'] is not None and liq['fcf'] < 0 and liq['refinancing_need']:
        add('gate.distress', 'financial', 'Altman distress zone AND negative free cash flow AND a near-term refinancing need: survival depends on new capital.',
            'high', cls='disqualifying', evidence=['analysis.forensics.altman', liq['fcf_source'], 'risk.liquidity'], values=liq)


# ---------------------------------------------------------------- failure scenarios

def _expand(row, key, count, default=0.):
    v = row.get(key, default)
    return list(v) if isinstance(v, list) else [v] * count


EXTERNAL_ROWS = ('equity_proceeds', 'debt_draw', 'sbc_share_settlement')


def _strip_funding(s2, bear, count):
    """Remove the bear's executed funding plan: assumed debt and equity and SBC settled in shares were sized for the bear's
    OWN cash flows (an SBC settlement above the variant's smaller SBC charge is invalid, and financing_status 'none' with
    cash financing is rejected). Investment liquidations stay (an internal source, never above the balance). The variant's
    gaps are re-closed by fund_with_equity: paused distributions, remaining investments, then equity at a depressed price."""
    had = [k for k in EXTERNAL_ROWS if any(x for x in _expand(bear, k, count))]
    if not had and bear.get('financing_status', 'none') == 'none':
        return []
    if any(x for x in _expand(bear, 'debt_draw', count)) and 'interest_rate' in bear:
        # the forecast blends the existing rate with assumed new money; without the new debt the existing rate applies
        firsts = [_expand(x, 'interest_rate', count)[0] for x in s2['scenarios'] if not _expand(x, 'debt_draw', count)[0]]
        r0 = min(firsts) if firsts else _expand(bear, 'interest_rate', count)[0]
        bear['interest_rate'] = [r0] * count
    for k in EXTERNAL_ROWS:
        bear[k] = [0.] * count
    bear['financing_status'] = 'none'
    return [f"the bear's own funding plan removed ({', '.join(had) or 'financing status'}): it was sized for the bear's cash flows; "
            'gaps are re-closed by pausing distributions, remaining investments, then equity at a depressed price']


def variant_spec(spec, kind, drop=-.25):
    """Driver spec whose bear row is replaced by a permanent-loss mechanism; returns (spec, edits).
    Each edit is at least as severe as the bear on its lever: growth never above the bear path (a shrinking bear keeps
    shrinking), cash costs never below it. The bear's executed funding plan (assumed debt and equity, SBC settled in shares)
    is removed first: it was sized for different cash flows and would make value_company reject the variant."""
    s2 = deepcopy(spec)
    count = len(s2['boundaries'])
    stub = 1 if s2.get('stub_annual_revenue_rate') is not None else 0
    gcount = count - stub
    bear = deepcopy(scen(s2, 'bear'))
    pre = _strip_funding(s2, bear, count)
    ccr, sbc, dep = _expand(bear, 'cash_cost_ratio', count), _expand(bear, 'sbc_pct', count), _expand(bear, 'depreciation_pct', count)
    g = _expand(bear, 'growth', gcount)
    t, tm, edits = bear['terminal'], None, []
    cap = lambda k: RATIO_KEYS[k][2]
    floor = lambda c0: [min(max(x, c0), cap('cash_cost_ratio')) for x in ccr]       # never cheaper than the bear path
    if kind == 'growth_stall':
        c0, s = min(ccr[0] + .03, cap('cash_cost_ratio')), max(sbc)
        cc = floor(c0)
        bear.update(growth=[min(x, 0.) for x in g], cash_cost_ratio=cc, sbc_pct=s)
        tm = max(.01, min(t['operating_margin'] - .03, 1 - cc[-1] - s - dep[-1]))
        edits = ['revenue growth min(bear, 0%) in every forecast year (growth stops; a shrinking bear keeps shrinking)',
                 f'cash cost ratio at least {c0:.3f} (bear first year +3pp; no operating leverage) and never below the bear path',
                 f'SBC held at {s:.1%} of revenue (bear peak)']
    elif kind in ('cyclical_collapse', 'utilization_collapse'):
        c0, scale = min(ccr[0] + (.08 if kind == 'cyclical_collapse' else .10), cap('cash_cost_ratio')), 1 / (1 + drop)
        cc = floor(c0)
        maint, gcap, ir = _expand(bear, 'maintenance_capex_pct', count), _expand(bear, 'growth_capex_pct', count), _expand(bear, 'interest_rate', count)
        bear.update(growth=[min(drop, g[0])] + [min(x, 0.) for x in g[1:]], cash_cost_ratio=cc,
                    depreciation_pct=[min(x * scale, cap('depreciation_pct')) for x in dep],
                    maintenance_capex_pct=[min(x * scale, cap('maintenance_capex_pct')) for x in maint],
                    growth_capex_pct=[min(x * scale, cap('growth_capex_pct')) if i < stub + 2 else x for i, x in enumerate(gcap)],
                    interest_rate=[min(x + .02, cap('interest_rate')) for x in ir])
        tm = max(.01, min(t['operating_margin'] * .6, 1 - cc[-1] - sbc[-1] - min(dep[-1] * scale, cap('depreciation_pct'))))
        edits = [f'revenue min(bear, {drop:+.0%}) in the first full year, then min(bear, 0%)',
                 f'cash cost ratio at least {c0:.3f} (fixed costs do not flex) and never below the bear path',
                 'depreciation and maintenance capex kept in absolute terms (asset base unchanged); committed growth capex kept for two periods',
                 'interest rate +2pp (stressed refinancing)']
    elif kind == 'funding_failure':
        bear['growth'] = [x - .5 * abs(x) for x in g]
        edits = ['revenue growth g - 0.5|g| on the bear path (half the growth; a decline 1.5x deeper)',
                 'no uncommitted (assumed) debt or equity at the quote: financing only at a depressed price']
    else:
        raise ValueError(f'unknown failure mechanism {kind}')
    if tm is not None:
        t.update(operating_margin=tm, growth=min(t['growth'], .01))
        edits += [f'terminal operating margin {tm:.1%} (no recovery above the explicit margin)', f"terminal growth {t['growth']:.1%}"]
    bear['thesis'] = MECHANISM[kind]
    s2['scenarios'] = [bear] + [s for s in s2['scenarios'] if s['name'] in ('base', 'bull')]
    return s2, pre + edits


def fund_with_equity(case, name, price, detail=None):
    """Close each funding gap of one scenario, cheapest source first: pause the latest earlier distribution, liquidate the
    remaining marketable investments (their income falls with the balance), then issue equity at a depressed price.
    Returns (result, raised, issue_price) or (None, ...); detail (a dict) receives the liquidations and paused payouts."""
    issue, raised = price * (1 - ISSUE_DISCOUNT), 0.
    sc = scen(case, name)
    inv = num(case['opening'].get('marketable_investments')) or 0.
    detail = {} if detail is None else detail
    detail.update(investments_liquidated=0., payouts_paused=[])
    for _ in range(4 * len(sc['periods']) + 5):
        r = value_company(case)
        res = scen(r, name)
        if res['status'] != 'funding_blocked':
            return r, raised, issue
        i = next(i for i, row in enumerate(res['forecast']) if row['funding_gap'] > 1e-7)
        gap = res['forecast'][i]['funding_gap']
        paid = [k for k in range(i) if (num(sc['periods'][k].get('payout_fraction')) or 0) > 0]
        if paid:
            sc['periods'][paid[-1]]['payout_fraction'] = 0.
            detail['payouts_paused'].append(sc['periods'][paid[-1]]['end'])
            continue
        p = sc['periods'][i]
        left = inv - sum(num(q.get('investment_liquidation')) or 0 for q in sc['periods'])
        if left > 1:
            x = min(left, gap * 1.001 + 1)
            p['investment_liquidation'] = (num(p.get('investment_liquidation')) or 0) + x
            for q in sc['periods'][i:]:
                q['investment_income_after_tax'] = (num(q.get('investment_income_after_tax')) or 0) * (left - x) / left
            detail['investments_liquidated'] += x
            continue
        amount = gap / (1 - ISSUE_FEE) * 1.001 + 1
        p.update(equity_proceeds=(num(p.get('equity_proceeds')) or 0) + amount, equity_issue_price=issue, equity_fee_rate=ISSUE_FEE, financing_status='assumed')
        raised += amount
    return None, raised, issue


def _bear(result, case, today, price):
    rb, rs, cb = scen(result, 'bear'), scen(result, 'base'), scen(case, 'bear')
    v = num(today.get('bear'))
    signs = []
    if case.get('method') == 'operating' and rb and rs:
        fb_, fs = [r for r in rb['forecast'] if years(r['start'], r['end']) > .9], [r for r in rs['forecast'] if years(r['start'], r['end']) > .9]
        if fb_ and fs and fb_[0]['revenue'] > 0 and fs[0]['revenue'] > 0:
            r0, s0 = fb_[0], fs[0]
            signs += [f"Revenue for the year ending {r0['end']} at or below {r0['revenue']:,.0f} (bear) versus {s0['revenue']:,.0f} (base)",
                      f"Operating margin after SBC at or below {r0['ebit'] / r0['revenue']:.1%} (bear) versus {s0['ebit'] / s0['revenue']:.1%} (base) that year"]
        first = next((r['end'] for r in rb['forecast'] if r.get('funding_gap', 0) > 1e-7), None)
        if first:
            signs.append(f'Cash below the minimum balance by {first} without new financing')
    if cb:
        signs.append('Thesis-specific: ' + cb['thesis'])
    return {'name': 'bear', 'kind': 'bear', 'status': 'computed' if v is not None else 'funding_blocked', 'mechanism': (cb or {}).get('thesis'),
            'observable_signs': signs, 'value_per_share': v, 'loss_from_price': _loss(v, price), 'gap_vs_price': _gap(v, price),
            'source': 'executed valuation, bear scenario'}


def _permanent(spec, kind, drop, price, bear_value=None):
    g = (_expand(scen(spec, 'base') or {}, 'growth', 1, 0.)[0]) if spec else 0.
    out = {'name': 'permanent_loss:' + kind, 'kind': 'permanent_loss', 'mechanism': MECHANISM[kind]}
    if not spec:
        out['observable_signs'] = [s.format(g=g, d=drop) for s in SIGNS[kind] if '{m' not in s]
        return {**out, 'status': 'not_computed', 'value_per_share': None,
                'reason': 'no driver spec (forecast.spec) to re-run; only driver-spec operating forecasts are re-executed'}
    try:
        s2, edits = variant_spec(spec, kind, drop)
        out['observable_signs'] = [s.format(g=g, m=scen(s2, 'bear')['terminal']['operating_margin'], d=drop) for s in SIGNS[kind]]
        c2 = build_case(s2)
        r2, dilution = value_company(c2), None
        if scen(r2, 'bear')['status'] == 'funding_blocked':
            det = {}
            r3, raised, issue = fund_with_equity(c2, 'bear', price, det)
            if r3 is None:
                return {**out, 'status': 'not_computed', 'value_per_share': None, 'edits': edits,
                        'reason': 'funding gap not closed with equity at the depressed price within the search: loss could approach 100%'}
            rows = scen(r3, 'bear')['forecast']
            dilution = {'equity_raised': raised, 'issue_price': issue, 'issue_discount_to_quote': ISSUE_DISCOUNT, 'fee_rate': ISSUE_FEE,
                        'new_shares': sum(r['new_financing_shares'] for r in rows), 'original_ownership_retained': rows[-1]['original_ownership'],
                        'investments_liquidated': det['investments_liquidated'], 'payouts_paused': det['payouts_paused']}
            if det['payouts_paused'] or det['investments_liquidated']:
                edits.append(f"before any raise: distributions paused ({len(det['payouts_paused'])} period(s)) and "
                             f"{det['investments_liquidated']:,.0f} of remaining investments liquidated")
            if raised:
                edits.append(f'funding gaps closed with {raised:,.0f} of equity at {issue:.4g} per share ({ISSUE_DISCOUNT:.0%} below the quote, {ISSUE_FEE:.0%} fees)')
            r2 = r3
        v = num(r2['annual_values'][0]['bear'])
        milder = None if v is None or bear_value is None else v >= bear_value
        out = {**out, 'status': 'computed', 'value_per_share': v, 'loss_from_price': _loss(v, price), 'gap_vs_price': _gap(v, price),
               'edits': edits, 'dilution': dilution, 'input_sha256': r2['input_sha256'], 'not_more_severe_than_bear': milder,
               'calculation': 'drivers.build_case + valuation.value_company with the bear driver row replaced by the listed edits'}
        if milder:
            out['severity_note'] = (f'value {v:.4g} is not below the bear {bear_value:.4g}: this mechanism is not a worse case than the executed bear '
                                    '(e.g. shrinking a loss-making business faster reduces losses); the bear is the more severe stress')
        return out
    except (ValueError, KeyError, ZeroDivisionError, StopIteration) as e:
        out.setdefault('observable_signs', [s.format(g=g, d=drop) for s in SIGNS[kind] if '{m' not in s])
        return {**out, 'status': 'not_computed', 'value_per_share': None, 'reason': f'{type(e).__name__}: {e}'}


def failure_scenarios(card, fc, result, case, today, price):
    primary = ((card or {}).get('business_model') or {}).get('primary')
    kinds = [LOSS.get(primary, 'growth_stall')]
    early = ((card or {}).get('stage') or {}).get('value') == 'early' or ((card or {}).get('funding') or {}).get('value') == 'needs_external'
    if result and any(s['status'] == 'funding_blocked' and s['name'] != 'base' for s in result['scenarios']):
        early = True
    if early and 'funding_failure' not in kinds:
        kinds.append('funding_failure')
    drop = DROP.get(primary, -.25)
    if not result:
        out = [{'name': 'bear', 'kind': 'bear', 'status': 'not_computed', 'mechanism': None, 'observable_signs': [], 'value_per_share': None,
                'reason': 'no executed valuation'}]
        return out + [{**_permanent(None, k, drop, None), 'reason': 'no executed valuation'} for k in kinds]
    spec = (fc or {}).get('spec') if case.get('method') == 'operating' else None
    bear = _bear(result, case, today, price)
    return [bear] + [_permanent(spec, k, drop, price, bear['value_per_share']) for k in kinds]


# ---------------------------------------------------------------- size of error

def error_size(result, case, today, val, qa, card, reg, rt):
    """Size of a possible VALUATION error from valuation triggers only (range, discount-rate sensitivity, terminal share,
    unverifiable red-team checks). Process gaps (data, understanding, unassessed dimensions) go to `process_gaps`: they
    are confidence matters and must not raise the error level (and through it the required return) a second time."""
    high, med = [], []
    v = {k: num(today.get(k)) for k in ('bear', 'base', 'bull')}
    rel = (v['bull'] - v['bear']) / v['base'] if None not in v.values() and v['base'] > 0 else None
    if result is None:
        high.append('no executed valuation: the size of error cannot be bounded')
    elif rel is None:
        high.append('a scenario value is withheld or base is not positive: the bear-bull range cannot be computed')
    elif rel > 1.5:
        high.append(f'bear-to-bull range {rel:.0%} of base value')
    elif rel > .6:
        med.append(f'bear-to-bull range {rel:.0%} of base value')
    band = ((val or {}).get('diagnostics') or {}).get('discount_rate_band')
    src = 'valuation.diagnostics.discount_rate_band'
    if band is None and case and case.get('method') in ('operating', 'residual_income'):
        src = 'valuation_tools.discount_rate_band (computed in risk)'
        try:
            band = vt.discount_rate_band(case)
        except (ValueError, KeyError, ZeroDivisionError) as e:
            band = {'status': 'not_computed', 'reason': str(e)}
    w = num((band or {}).get('relative_width'))
    if w is not None and w > .6:
        high.append(f'value moves {w:.0%} of base across +/-1pp of required return')
    elif (w is not None and w > .3) or (band or {}).get('quote_inside_band'):
        med.append('discount-rate sensitivity' + (f': value moves {w:.0%} across +/-1pp' if w is not None else '')
                   + ('; the quote lies inside the band, so a modest change in required return flips cheap/expensive' if (band or {}).get('quote_inside_band') else ''))
    ts = num((scen(result, 'base') or {}).get('terminal_share_of_present_value')) if result else None
    if ts is not None and ts > .8:
        med.append(f'{ts:.0%} of value from terminal equity')
    if rt['critical_unverifiable']:
        med.append('red-team checks unverifiable: ' + ', '.join(rt['critical_unverifiable']))
    gaps = []

    def gap(code, severity, detail, **extra):
        gaps.append({'code': code, 'severity': severity, 'detail': detail, **extra})
    qs = (qa or {}).get('status')
    if qs == 'blocked' or not qa:
        gap('data_blocked', 'high', 'critical data blocked or not validated', fields=list((qa or {}).get('blocking') or []))
    elif qs == 'limited' or (qa or {}).get('limited'):
        gap('data_limited', 'medium', f"data limited: {', '.join((qa or {}).get('limited') or []) or 'see dataqa'}", fields=list((qa or {}).get('limited') or []))
    cs = ((card or {}).get('completeness') or {}).get('status')
    if cs == 'insufficient':
        gap('understanding_insufficient', 'high', 'business understanding insufficient')
    elif cs == 'partial':
        gap('understanding_partial', 'medium', 'business understanding partial')
    open_high = [r['id'] for r in reg if r.get('status') == 'unassessed' and r['severity'] == 'high']
    if open_high:
        gap('dimensions_unassessed', 'medium', 'high-relevance business dimensions unassessed: ' + ', '.join(open_high), ids=open_high)
    level = 'high' if high or len(med) >= 3 else ('medium' if med else 'low')
    return {'label': 'size of possible valuation error (not a company risk)', 'level': level, 'value_range_rel': rel,
            'discount_rate_band': band, 'discount_rate_band_source': src, 'terminal_share': ts,
            'reasons': high + med or ['range, discount-rate sensitivity, terminal dependence and red-team verifiability within tolerance'],
            'process_gaps': gaps,
            'scope': 'level and reasons use valuation triggers only; process_gaps (data, understanding, unassessed dimensions) are '
                     'confidence inputs, not part of the valuation error or the required return',
            'rule': 'high if any high trigger or three medium triggers; medium if any medium trigger. Descriptive thresholds, not calibrated probabilities.',
            'formula': 'value_range_rel = (bull - bear) / base at today; band = base value at cost of equity and terminal WACC -/+ 1pp'}


def assess(fb, qa, an, card, fc, val):
    """Risk register, failure scenarios, downside and size of error for one casefile (docs/MODULE_CONTRACTS.md)."""
    fb, qa, an, card, fc = fb or {}, qa or {}, an or {}, card or {}, fc or {}
    result, case, today, price = valuation_parts(val)
    levers = bear_levers(case, result)
    comps = ((fc.get('cost_of_equity') or {}).get('components') or {})
    effect, material = rate_effect(case, today)
    reg, add = _register(levers, comps if isinstance(comps, dict) else {}, *bear_text(case, fc), effect=effect, material=material)
    liq = liquidity(fb, qa, an)
    rt = redteam_check(fb, qa, an, fc, val)
    _data(add, qa, rt)
    _financial(add, an, liq)
    _dilution(add, an, fb)
    weak_cash = _accounting(add, an)
    _business(add, card)
    _red_flags(add, fb, card)
    _execution(add, an, card, result, case, val)
    _valuation(add, an, result, today, price, val)
    scenarios = failure_scenarios(card, fc, result, case, today, price)
    _gates(add, fb, qa, an, card, result, val, rt, liq, weak_cash, scenarios)
    reg.sort(key=lambda r: (CLASS_ORDER[r['class']], SEV_ORDER[r['severity']], r['id']))
    bear = num(today.get('bear'))
    pl = [x for x in scenarios if x['kind'] == 'permanent_loss' and x.get('value_per_share') is not None]
    worst = min(pl, key=lambda x: x['value_per_share']) if pl else None
    downside = {'price': price, 'bear_value': bear, 'loss_to_bear': _loss(bear, price), 'bear_vs_price': _gap(bear, price),
                'permanent_loss_value': worst['value_per_share'] if worst else None,
                'loss_to_permanent': _loss(worst['value_per_share'], price) if worst else None,
                'permanent_loss_case': worst['name'] if worst else None,
                'not_more_severe_than_bear': [x['name'] for x in pl if x.get('not_more_severe_than_bear')],
                'not_computed': {x['name']: x.get('reason') for x in scenarios if x.get('value_per_share') is None},
                'meaning': 'loss = max(0, 1 - value / price) on today\'s conditional values; scenarios are not probabilities or price forecasts.'}
    count = lambda k, v: sum(1 for r in reg if r.get(k) == v)
    return {'register': reg, 'failure_scenarios': scenarios, 'downside': downside,
            'error_size': error_size(result, case, today, val, qa, card, reg, rt),
            'disqualifying': [r['id'] for r in reg if r['class'] == 'disqualifying'],
            'evidence_gaps': [r['id'] for r in reg if r.get('evidence_gap')],
            'withhold_price': [r['id'] for r in reg if r.get('withhold_price')],
            'summary': {'disqualifying': count('class', 'disqualifying'), 'pricing': count('class', 'pricing'),
                        'accepted_priced': count('class', 'accepted_priced'), 'partially_priced': count('pricing_status', 'partially_priced'),
                        'unassessed': count('status', 'unassessed'), 'valuation_error': count('valuation_error', True)},
            'bear_levers': levers, 'liquidity': liq, 'pricing_materiality': material,
            'redteam': {k: rt[k] for k in ('verdict', 'usable', 'critical_failed', 'critical_unverifiable', 'capital_structure_source', 'by_construction')},
            'meaning': 'Disqualifying entries block a price conclusion; pricing entries lower attractiveness or confidence; accepted_priced entries are '
                       'in the executed bear path or carry a cost-of-equity add-on that moves base value materially (pricing_status priced); '
                       'a small add-on is partially_priced. valuation_error entries describe the size of possible valuation error '
                       '(error_size), not a company risk. Severity thresholds are descriptive screens.'}
